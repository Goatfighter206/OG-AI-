"""The wall-clock claim budget (Round 321c).

The P0 (round-321 NOTES): a plain chat turn paid the FULL
claim-chain deferral cascade — seat._claim_exact ->
_defer_to_others -> _other_claim -> the next seat's
_claim_exact, recursively, each miss also paying
og_garble's difflib repair — 1,113 s for one plain turn,
so the stream died and the parked browser claim captured
a turn it should never have seen. The cure is a budget,
not a redesign of 200 seats' claim semantics: while a
turn's claim phase runs, a thread-local deadline stands
(og_fixqueue's detect wrapper and app.py's chat workers
open and close it); once it passes, the claim FUNNELS
answer "not mine" at once — _defer_to_others reports a
claim elsewhere (stepping aside is always legal), the
other-claim probes raise a private exception every real
call site already swallows, og_garble stops repairing —
the cascade unwinds in seconds and the turn falls
through to plain generation. The claim ENTRIES
(_claim_exact / _claim_job / _claim_<seat>) are cut
too: once the budget blows they answer None at once —
profiling the first cut showed the ~200 seats' local
parses still SUMMING to 83 s of detect (the exact
length of production's one successful chat that
hour), because each seat loads its store and parses
before deferring. A claim found BEFORE the deadline
is never cut, and the forget's chain walks watch ->
browser in the first seconds of the pass, which is
how the deep owner commands survive the budget. While the
budget is OPEN (no deadline, or not yet passed) every
wrapped function is an exact pass-through — fast turns
and non-chat callers (the suites call claims directly,
with no budget begun) see the old behavior.

The value: 8 s. Grounds (round-321 NOTES, corrected
production log verdict 2026-10-15): production runs
gunicorn --timeout 12 — a worker whose loop starves
past 12 s is SIGKILLed, and the claim cascade's CPU
starved it exactly like that (worker timeouts at
18:43:33/18:48:04 UTC, the hour's one successful chat
took 83.3 s, requests dying at the edge). The claim
phase must therefore finish or fall through WELL
inside 12 s: 8 s of claims + the seconds-fast
post-blow unwind leaves heartbeat margin. A bare
detect pass measured 409 s locally for a plain
message (forget's claim at 99 s), so without the
budget every parked-state turn was a worker-killer.
OG_CLAIM_BUDGET_SECONDS overrides.
"""

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

CLAIM_BUDGET_SECONDS = float(
    os.getenv("OG_CLAIM_BUDGET_SECONDS", "8") or 8)
_claim_budget_local = threading.local()


class _ClaimBudgetBlown(Exception):
    """Private: the claim budget closed mid-cascade."""


def begin_claim_budget(seconds: float = None) -> bool:
    """Open a claim budget on this thread; True when THIS
    call set the deadline (it owns the matching end call).
    Nested begins keep the earliest deadline, return False."""
    depth = getattr(_claim_budget_local, "depth", 0)
    _claim_budget_local.depth = depth + 1
    if depth > 0:
        return False
    span = CLAIM_BUDGET_SECONDS if seconds is None else seconds
    _claim_budget_local.deadline = time.monotonic() + max(0.0, span)
    _claim_budget_local.blew = False
    return True


def end_claim_budget(started: bool):
    """Close a begin_claim_budget: only the deadline's
    owner clears it, once the nesting has drained."""
    depth = getattr(_claim_budget_local, "depth", 0) - 1
    _claim_budget_local.depth = max(0, depth)
    if started and depth <= 0:
        _claim_budget_local.deadline = None


def claim_budget_open() -> bool:
    """True while claims may proceed: no budget begun on
    this thread, or its deadline has not passed yet."""
    deadline = getattr(_claim_budget_local, "deadline", None)
    if deadline is None or time.monotonic() < deadline:
        return True
    if not getattr(_claim_budget_local, "blew", False):
        _claim_budget_local.blew = True
        logger.warning(
            "Claim budget blown: claims step aside, the "
            "turn falls through to plain generation.")
    return False


def _budget_guard(fn, kind):
    if kind == "claim":
        def _g(*a, **k):
            if not claim_budget_open():
                return None
            return fn(*a, **k)
    elif kind == "defer":
        def _g(message, uid):
            return True if not claim_budget_open() \
                else fn(message, uid)
    elif kind == "blown":
        def _g(message, uid):
            if not claim_budget_open():
                raise _ClaimBudgetBlown()
            return fn(message, uid)
    else:  # garble text/info: the no-repair answer
        def _g(text, vocab, homophones=None):
            if not claim_budget_open():
                return str(text or ""), \
                    [] if kind == "text" else None
            return fn(text, vocab, homophones)
    _g._og_budget_wrapped = True
    return _g


def _wrap_claim_funnels() -> int:
    """Install-time census (run LAST by og_fixqueue's
    install, when every seat module is imported): wrap
    each loaded og_* module's claim funnels so they
    answer at once once the budget closes. Fail-soft
    per module, idempotent; returns the wrap count."""
    wrapped = 0
    import sys as _sys
    for modname, mod in list(_sys.modules.items()):
        if mod is None or not modname.startswith("og_"):
            continue
        if modname == __name__ or modname.endswith("_testing"):
            continue
        try:
            targets = (("repair", "text"), ("repair_info", "info")) \
                if modname == "og_garble" else (
                    ("_defer_to_others", "defer"),
                    ("_other_claim", "blown"),
                    ("_seat_other_claim", "blown"),
                    ("_probe_claim", "blown"))
            for attr, kind in targets:
                fn = getattr(mod, attr, None)
                if fn is None or not callable(fn):
                    continue
                if getattr(fn, "_og_budget_wrapped", False):
                    continue
                if getattr(fn, "__module__", None) != modname:
                    continue
                _g = _budget_guard(fn, kind)
                _g.__module__ = modname
                setattr(mod, attr, _g)
                wrapped += 1
            if modname != "og_garble":
                # The seats' claim ENTRIES (_claim_exact,
                # _claim_job, _claim_<seat>): once the
                # budget blows they answer "not mine"
                # (None) at once — the local parses of
                # ~200 seats otherwise SUM to minutes
                # even with the deferral cascade cut.
                for attr in dir(mod):
                    if not attr.startswith("_claim_"):
                        continue
                    fn = getattr(mod, attr, None)
                    if fn is None or not callable(fn):
                        continue
                    if getattr(fn, "_og_budget_wrapped", False):
                        continue
                    if getattr(fn, "__module__", None) != modname:
                        continue
                    _g = _budget_guard(fn, "claim")
                    _g.__module__ = modname
                    setattr(mod, attr, _g)
                    wrapped += 1
        except Exception as e:
            logger.warning(f"Claim-budget wrap skipped: {e}")
    if wrapped:
        logger.info(f"Claim budget: {wrapped} funnels wrapped.")
    return wrapped
