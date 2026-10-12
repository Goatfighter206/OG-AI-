"""Round 70 (addition): OG-generated ideas for the Ideas seat.

Brent (2026-10-11): Ideas stops being only fix proposals —
OG also generates ideas FOR the user, from the user's own
signals. V1 is RULES ONLY (no model call): every candidate
below is derived from records the server already holds for
THIS caller —

  watch_hit     an unread watch-match notification is
                waiting (og_notify records)
  watch_login   one of the caller's watches is stuck at
                waiting_login (og_watch store)
  book_continue a started book is unfinished and not
                currently generating (og_books via the
                library collector)
  generator_try a generator the caller has never used
                (library counts == 0; first match only)

Brent's rule (2026-10-11, same day): "OG should not be
finding things to upgrade — he should be focusing on
user tasks." Ideas draws ONLY from the user's world —
their asks, tasks, watches, creations. NOTHING here may
come from OG's own system needs: no upgrade pitches, no
capacity nags, no maintain-me cards, ever. (A storage
signal shipped in the first cut and was removed the
same day under this rule.)

Chat topics are deliberately NOT a v1 signal — there is no
honest rules-only read of them, and inventing one would be
fake intelligence. Copy claims nothing the rules don't do.

Semantics:
- At most MAX_LIVE ideas are live at once, in the priority
  order above.
- Ideas NEVER execute anything: a card offers Ask (the ask
  is seeded into chat by the client) and Dismiss.
- Generation refreshes on read (GET /ideas): candidates are
  re-derived from current records every time. (Round 95
  amended what happens when a signal goes away — see the
  Round 95 note below; a shown idea no longer expires on
  the next read.) A first-seen timestamp is kept while
  the signal persists.
- Dismissal suppresses the idea's key for 30 days.
- Fix proposals keep their own section and approval
  semantics (og_fixqueue) — untouched by this module.

Round 95 (Brent, 2026-10-12: "in ideas you can see some
of those notifications pop up and then go away"): the
vanish was this module's own semantics — an idea was a
live view of its signal, so the moment the signal
flipped (the watch-match notification got marked read
on ANY surface, a watch left waiting_login), the next
read dropped the card silently, mid-look if a refresh
landed while the seat was open. Now a SHOWN idea stays:
live signals take the seats first, and a shown idea
whose signal went away is retained until the user
dismisses it or the signal has been gone for a full
SIGNAL_GRACE_SECONDS (24h) — it expires server-side,
on a named constant, never mid-look. The routes also
resolve the caller through og_accounts.session_uid_or
(Round 95's one identity), like every other surface.

State rides og_notify's key-value store under
"ideas:<uid>" (the house pattern for small per-user
state): {"ideas": [...], "dismissed": {key: ts}}.
"""

import logging
import time
from typing import Dict, List, Optional

from fastapi import Request

logger = logging.getLogger(__name__)

UID_COOKIE = "ogai_uid"
MAX_LIVE = 3
DISMISS_DAYS = 30
# Round 95: how long a SHOWN idea outlives its signal.
# The card never vanishes mid-look; it retires on a
# later read once its signal has been gone this long.
SIGNAL_GRACE_SECONDS = 24 * 60 * 60

_TRY_IDEAS = (
    ("song", "Try a song",
     "OG can write and sing an original song for you — "
     "you haven't made one yet.",
     "Make me a song"),
    ("video", "Try a story video",
     "OG can turn a story into a narrated video — "
     "you haven't made one yet.",
     "Make me a story video"),
    ("podcast", "Try a podcast",
     "OG can cut a podcast episode in two voices — "
     "you haven't made one yet.",
     "Make me a podcast episode"),
    ("book", "Try a book",
     "OG can write you a book, chapter by chapter — "
     "you haven't started one yet.",
     "Write me a book"),
)
_COUNT_KEYS = {"song": "songs", "video": "videos",
               "podcast": "podcasts", "book": "books"}


def _state(uid: str) -> Dict:
    import og_notify
    st = og_notify._get("ideas:" + uid)
    if not isinstance(st, dict):
        st = {}
    if not isinstance(st.get("dismissed"), dict):
        st["dismissed"] = {}
    if not isinstance(st.get("ideas"), list):
        st["ideas"] = []
    return st


def _save(uid: str, st: Dict) -> None:
    import og_notify
    og_notify._put("ideas:" + uid, st)


def _candidates(uid: str, request: Request) -> List[Dict]:
    """The caller's live idea candidates, in priority
    order. Every source is guarded on its own — a broken
    signal contributes nothing and never sinks the rest."""
    out: List[Dict] = []

    # 1. An unread watch hit is waiting.
    try:
        import og_notify
        for it in og_notify._items(uid):
            if (isinstance(it, dict) and not it.get("read")
                    and it.get("kind") in ("watch_match",
                                           "mp_match")):
                out.append({
                    "id": f"watch_hit:{it.get('id', '')}",
                    "kind": "watch_hit",
                    "title": "A watch found something",
                    "body": (it.get("title") or "A match is "
                             "waiting for you in your Feed."),
                    "ask": "Show me the match you found for me",
                })
                break
    except Exception as e:
        logger.warning(f"Ideas watch-hit signal failed: {e}")

    # 2. A watch is stuck waiting on the user's sign-in.
    try:
        import og_watch
        for w in og_watch._watches(uid):
            if isinstance(w, dict) \
                    and w.get("status") == "waiting_login":
                item = w.get("item") or "your watch"
                out.append({
                    "id": f"watch_login:{item}",
                    "kind": "watch_login",
                    "title": "A watch is waiting on your sign-in",
                    "body": (f"OG can't check “{item}” until "
                             "you sign in on the site it "
                             "watches."),
                    "ask": (f"Open the site so I can sign in "
                            f"for my watch on {item}"),
                })
                break
    except Exception as e:
        logger.warning(f"Ideas watch-login signal failed: {e}")

    # Library-derived signals share one collection pass.
    counts: Dict = {}
    try:
        import og_library
        _items, _line, counts = og_library.collect_library(
            uid, request)
    except Exception as e:
        logger.warning(f"Ideas library pass failed: {e}")

    # 3. A started book is unfinished and idle.
    try:
        import og_library
        for it in og_library._book_items(uid):
            meta = it.get("meta") or {}
            done = int(meta.get("chapters_done", 0) or 0)
            total = int(meta.get("chapters_total", 0) or 0)
            status = it.get("status", "")
            if done > 0 and total > done and status not in (
                    "done", "failed", "generating", "queued",
                    "await_approval"):
                title = it.get("title") or "your book"
                out.append({
                    "id": f"book:{it.get('id', '')}",
                    "kind": "book_continue",
                    "title": f"Keep writing “{title}”",
                    "body": (f"{done} of {total} chapters "
                             "are written — the next one is "
                             "ready when you are."),
                    "ask": f"Continue my book {title}",
                })
                break
    except Exception as e:
        logger.warning(f"Ideas book signal failed: {e}")

    # (Removed in the same 2026-10-11 rule pass: a
    #    "locker nearly full" idea. It was the caller's
    #    own storage, but in form it is a capacity nag,
    #    and Brent's rule is that Ideas carries NOTHING
    #    from OG's system-needs side — no upgrade pitches,
    #    no capacity nags, no maintain-me cards. The
    #    pre-existing storage_warning notification in the
    #    Feed is a different, older mechanism and is not
    #    part of Ideas.)

    # 4. A generator the caller has never used (one idea).
    try:
        for kind, title, body, ask in _TRY_IDEAS:
            if int(counts.get(_COUNT_KEYS[kind], 0) or 0) == 0:
                out.append({
                    "id": f"try:{kind}",
                    "kind": "generator_try",
                    "title": title, "body": body, "ask": ask,
                })
                break
    except Exception as e:
        logger.warning(f"Ideas try signal failed: {e}")

    return out


def ideas_for(uid: str, request: Request) -> List[Dict]:
    """Refresh-and-read: re-derive candidates, drop
    dismissed (30 days), cap at MAX_LIVE, keep first-seen
    timestamps for persisting signals, persist, return."""
    if not uid:
        return []
    st = _state(uid)
    now = time.time()
    dismissed = {k: v for k, v in st["dismissed"].items()
                 if now - float(v or 0) < DISMISS_DAYS * 86400}
    prev = {i.get("id"): i for i in st["ideas"]
            if isinstance(i, dict) and i.get("id")}
    live: List[Dict] = []
    live_ids = set()
    for cand in _candidates(uid, request):
        if cand["id"] in dismissed:
            continue
        cand = dict(cand)
        old = prev.get(cand["id"]) or {}
        cand["created"] = float(old.get("created") or now)
        cand["last_live"] = now
        live.append(cand)
        live_ids.add(cand["id"])
        if len(live) >= MAX_LIVE:
            break
    # Round 95: a shown idea whose signal went away does
    # NOT wink out — it is retained behind the live
    # signals until the user dismisses it or the signal
    # has been gone a full SIGNAL_GRACE_SECONDS. Its
    # last_live stays at the moment the signal last
    # held, so the grace clock is honest.
    for old in st["ideas"]:
        if len(live) >= MAX_LIVE:
            break
        if not isinstance(old, dict):
            continue
        oid = old.get("id")
        if not oid or oid in live_ids or oid in dismissed:
            continue
        anchor = float(old.get("last_live")
                       or old.get("created") or 0)
        if anchor and now - anchor > SIGNAL_GRACE_SECONDS:
            continue  # truly expired server-side
        kept = dict(old)
        kept["created"] = float(old.get("created") or now)
        kept["last_live"] = anchor or now
        live.append(kept)
        live_ids.add(oid)
    st["dismissed"] = dismissed
    st["ideas"] = live
    try:
        _save(uid, st)
    except Exception as e:
        logger.warning(f"Ideas state save failed: {e}")
    return live


def dismiss(uid: str, idea_id: str) -> bool:
    if not uid or not idea_id:
        return False
    st = _state(uid)
    st["dismissed"][idea_id] = time.time()
    st["ideas"] = [i for i in st["ideas"]
                   if not isinstance(i, dict)
                   or i.get("id") != idea_id]
    try:
        _save(uid, st)
    except Exception as e:
        logger.warning(f"Idea dismiss save failed: {e}")
        return False
    return True


def _request_uid(request: Request) -> str:
    """Round 95: the caller's ONE identity — the live
    session's account uid when signed in, else the
    presented cookie (og_accounts.session_uid_or)."""
    cookie_uid = request.cookies.get(UID_COOKIE) or ""
    try:
        import og_accounts as _acct
        return _acct.session_uid_or(request.cookies,
                                    cookie_uid)
    except Exception:
        return cookie_uid


def register_idea_routes(app):
    @app.get("/ideas")
    async def ideas_list(request: Request):
        uid = _request_uid(request)
        return {"ideas": ideas_for(uid, request)}

    @app.post("/ideas/dismiss")
    async def ideas_dismiss(request: Request):
        uid = _request_uid(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        ok = dismiss(uid, str((body or {}).get("id") or ""))
        return {"ok": ok}
