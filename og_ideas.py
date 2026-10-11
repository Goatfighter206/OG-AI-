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
  storage       the caller's locker is >= 90% full
                (og_storage via the library collector)
  generator_try a generator the caller has never used
                (library counts == 0; first match only)

Chat topics are deliberately NOT a v1 signal — there is no
honest rules-only read of them, and inventing one would be
fake intelligence. Copy claims nothing the rules don't do.

Semantics:
- At most MAX_LIVE ideas are live at once, in the priority
  order above.
- Ideas NEVER execute anything: a card offers Ask (the ask
  is seeded into chat by the client) and Dismiss.
- Generation refreshes on read (GET /ideas): candidates are
  re-derived from current records every time, so an idea
  whose signal went away expires on the next read. A
  first-seen timestamp is kept while the signal persists.
- Dismissal suppresses the idea's key for 30 days.
- Fix proposals keep their own section and approval
  semantics (og_fixqueue) — untouched by this module.

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

    # 4. The locker is nearly full (the standing
    #    housekeeping rule: warn + propose, never delete
    #    silently — this idea only proposes the look).
    try:
        import og_library
        _li, line = og_library._locker(uid, request)
        if isinstance(line, dict) and line.get("quota"):
            used = float(line.get("used") or 0)
            quota = float(line.get("quota") or 0)
            if quota > 0 and used / quota >= 0.9:
                out.append({
                    "id": "storage",
                    "kind": "storage",
                    "title": "Your locker is almost full",
                    "body": (f"{line.get('used_human', '')} of "
                             f"{line.get('quota_human', '')} "
                             "used. OG never deletes anything "
                             "without your say-so."),
                    "ask": "Show me what's in my storage "
                           "locker",
                })
    except Exception as e:
        logger.warning(f"Ideas storage signal failed: {e}")

    # 5. A generator the caller has never used (one idea).
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
    seen = {i.get("id"): i.get("created")
            for i in st["ideas"] if isinstance(i, dict)}
    live: List[Dict] = []
    for cand in _candidates(uid, request):
        if cand["id"] in dismissed:
            continue
        cand = dict(cand)
        cand["created"] = float(seen.get(cand["id"]) or now)
        live.append(cand)
        if len(live) >= MAX_LIVE:
            break
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


def register_idea_routes(app):
    @app.get("/ideas")
    async def ideas_list(request: Request):
        uid = request.cookies.get(UID_COOKIE) or ""
        return {"ideas": ideas_for(uid, request)}

    @app.post("/ideas/dismiss")
    async def ideas_dismiss(request: Request):
        uid = request.cookies.get(UID_COOKIE) or ""
        try:
            body = await request.json()
        except Exception:
            body = {}
        ok = dismiss(uid, str((body or {}).get("id") or ""))
        return {"ok": ok}
