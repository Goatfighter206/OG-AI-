"""Ticket D — the smarter door (design settled with Brent
2026-10-20, og-drill-fixes-2026-10-18.md).

Why: a plain chat turn's claim phase never finished — it
burned the whole 3 s claim budget every time. Measured on
the production loop (2026-10-23, entry-timing probe): one
plain message ("hey, how's it going") drove ~89,000 claim
funnel calls before the budget cut it, two-thirds of the
phase inside og_garble's difflib repairs re-run by the
recursive deferral probes; "catch me up" drove ~290,000.
Every seat's wrapper ran its full claim check, each check
probed ~23 other seats, each probe recursed — the cascade
is combinatorial, so the common case could never be fast,
only capped.

The door, cheapest test first (Brent's design):
  1. THE GLANCE — an in-memory topic scan of the message
     (word -> seat), harvested from the fleet itself: each
     wrapped seat's own _GARBLE_VOCAB trigger words plus
     its module name, read off the og_claimbudget census
     registry. No database, no store reads.
  2. THE REGULARS — seats that claimed this visitor's
     recent turns are checked first (conversations stay on
     topic). Kept in-process per uid, newest first, capped,
     and expired after 30 idle minutes (the No Junk Drawer
     rule: this state revalidates itself — a stale regular
     simply runs its real claim check and either claims or
     doesn't; the sweep below backstops any miss, and a
     wrong guess costs one parse, never a wrong answer).
  3. THE SHORTLIST — only the glanced/regular seats' claim
     entries run their full check in pass 1 (their
     deferral probes run live, but a probe that touches a
     non-shortlisted seat answers with the fleet's own
     "claimed elsewhere" signal, so a shortlisted seat
     YIELDS rather than stealing an ask it would normally
     defer — precedence is unchanged). If a shortlisted
     seat claims, the turn is done: no sweep.
  4. THE FALLBACK — if nobody on the shortlist claimed,
     the EXISTING full sweep runs (pass 2), so no turn
     ever falls through: every ask gets exactly the
     routing it gets today. The sweep itself is cheaper
     than it was, because the census guard memoizes claim
     entries within each phase — the cascade's repeated
     subtrees run once per phase (the memo is cleared at
     the flip: a pass-1 result computed with probes
     refused is never reused as a sweep result).

The two approval doors (og_fixqueue / og_selfcheck
_claim_job — pure-regex decision parses, precedence zero)
are always on the shortlist: an "approve the fix" must
never wait for a sweep.

Coordination lives here and is driven from the outermost
detect wrapper (og_activity's): it arms the pass in
og_claimbudget (whose guards do the refusing/memoizing),
runs the chain, and flips to the sweep only when pass 1
claimed nothing through a wrapped entry and at least one
entry was actually refused. Maker/browser entries are
claim-budget-exempt and unwrapped, so the door never
gates them — they run in both passes exactly as before.
Any error in the door falls back to the plain chain: the
old behavior is the failure mode, by design.

Kill switch: OG_DOOR_ENABLED=0 — the coordinator becomes
a pure pass-through and no pass is ever armed.
Persona untouched: this is plumbing; OG talks as he did.
"""

import logging
import os
import re
import sys
import threading
import time

logger = logging.getLogger(__name__)

DOOR_ENABLED = os.getenv("OG_DOOR_ENABLED", "1") != "0"

_ALWAYS = frozenset({"og_fixqueue", "og_selfcheck"})
_MAX_GLANCE_SEATS = 6
_REGULARS_TTL = 1800.0
_REGULARS_KEEP = 5
_REGULARS_MAX_UIDS = 512

_lock = threading.Lock()
_vocab_map = None          # word/phrase -> set(modname)
_regulars = {}             # uid -> [(modname, ts), ...]


# --- the glance -------------------------------------------------


def _build_vocab():
    """Harvest word -> seat from the census registry: each
    wrapped seat's own _GARBLE_VOCAB (its trigger words,
    the fleet's own declaration of its topic) plus its
    module name. Built lazily on first use (the census
    runs at agent install, before any chat turn)."""
    global _vocab_map
    if _vocab_map is not None:
        return _vocab_map
    mapping = {}
    try:
        import og_claimbudget as _cb
        registry = _cb.door_registry()
    except Exception:
        registry = []
    for modname, _attr in registry:
        words = set()
        mod = sys.modules.get(modname)
        vocab = getattr(mod, "_GARBLE_VOCAB", None) if mod \
            else None
        if isinstance(vocab, (list, tuple)):
            for w in vocab:
                if isinstance(w, str) and w.strip():
                    words.add(" ".join(w.lower().split()))
        base = modname[3:] if modname.startswith("og_") \
            else modname
        if base:
            words.add(base)
        for w in words:
            if len(w) >= 3:
                mapping.setdefault(w, set()).add(modname)
    _vocab_map = mapping
    return mapping


def _glance(message):
    """Seats whose declared topic words appear in the
    message, strongest (longest, phrase) matches first,
    capped. Empty for a message with no topic words —
    the regulars and the sweep carry those turns."""
    low = " " + " ".join(
        str(message or "").lower().split()) + " "
    scored = {}
    for word, mods in _build_vocab().items():
        if " " in word:
            hit = word in low
            weight = 3 * len(word)
        else:
            hit = re.search(
                r"\b" + re.escape(word) + r"\b", low) \
                is not None
            weight = len(word)
        if hit:
            for m in mods:
                scored[m] = scored.get(m, 0) + weight
    ranked = sorted(scored.items(), key=lambda kv: -kv[1])
    return [m for m, _s in ranked[:_MAX_GLANCE_SEATS]]


# --- the regulars -----------------------------------------------


def _regulars_for(uid):
    if not uid:
        return []
    now = time.time()
    with _lock:
        rows = [(m, t) for m, t in _regulars.get(uid, [])
                if now - t <= _REGULARS_TTL]
        if rows:
            _regulars[uid] = rows
        else:
            _regulars.pop(uid, None)
    return [m for m, _t in rows]


def _note_claims(uid, modnames):
    """Record seats that claimed for this visitor (newest
    first, deduped, capped, timestamped for the TTL)."""
    if not uid or not modnames:
        return
    now = time.time()
    with _lock:
        rows = [(m, t) for m, t in _regulars.get(uid, [])
                if m not in modnames
                and now - t <= _REGULARS_TTL]
        rows = [(m, now) for m in modnames] + rows
        _regulars[uid] = rows[:_REGULARS_KEEP]
        if len(_regulars) > _REGULARS_MAX_UIDS:
            for k in list(_regulars)[:len(_regulars)
                                     - _REGULARS_MAX_UIDS]:
                _regulars.pop(k, None)


# --- the coordinator --------------------------------------------


def coordinate(prev_detect, message, get_uid):
    """Run one detect pass through the door. prev_detect is
    the wrapped chain the outermost detect wrapper holds.
    Returns the chain's intent, with exactly the routing
    the plain chain would have produced: pass 1 (shortlist)
    first, the full sweep as fallback when it claims
    nothing through a wrapped entry. Fail-soft: any door
    error runs the plain chain, untouched."""
    if not DOOR_ENABLED:
        return prev_detect(message)
    try:
        import og_claimbudget as _cb
        uid = ""
        try:
            uid = get_uid() or ""
        except Exception:
            uid = ""
        allowed = set(_ALWAYS)
        allowed.update(_regulars_for(uid))
        allowed.update(_glance(message))
        st = _cb.door_begin(allowed, uid)
        if st is None:
            # A pass is already armed on this thread
            # (nested detect): ride the outer pass.
            return prev_detect(message)
        try:
            intent = prev_detect(message)
            if not st.claimed and st.skips > 0:
                _cb.door_to_sweep(st)
                intent = prev_detect(message)
            return intent
        finally:
            claimed = set(st.claimed)
            _cb.door_end(st)
            try:
                _note_claims(uid, claimed)
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Door coordination failed: {e}")
        return prev_detect(message)
