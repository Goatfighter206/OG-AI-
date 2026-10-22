"""Library for OG (Brent's order, 2026-10-09): "Build OG
an artifacts library" — one place holding everything OG
made for a user.

WHAT THIS IS. OG's creations lived scattered: story
videos in og_video's job store, songs in og_songs', Unity
packages on disk next to og_unity, durable files in the
Round 21 locker (og_storage). The page had no shelf. This
module is that shelf — a single READ-ONLY aggregate:

    GET /library -> {"items": [...], "storage": {...}|null,
                     "counts": {videos, songs, images,
                                files, projects}}

Every item is normalized: {kind, id, title, created_at
(epoch seconds), status, action, action_url, meta}. The
endpoint writes to NO store and duplicates NO store logic:
each section reads through the owning module's own seam
(list_jobs_for_owner in og_video / og_songs,
list_packages_for_owner in og_unity, list_records /
usage_bytes / quota_bytes in og_storage), and every video
/ song job passes through its owner's own _job_view — so
retention, stale-job and gone-file semantics are EXACTLY
what /video/status and /song/status already show. Expired
items simply do not appear; nothing is resurrected. A
producer that raises contributes no items; the endpoint
still answers 200 with the other sections intact. Every
action_url is one of the producers' existing owner-only
routes (/video/download/<id>, /song/download/<id>,
/storage/download/<id>, /unity/download/<id>) — this
module serves no bytes itself.

IMAGES — THE HONEST FINDING. Round 5 generated images are
NOT stored server-side per visitor: POST /image returns a
data URI in the chat response, and app.py notes only a
text line in the visitor's thread ("too big to store").
There is no generated-image store to aggregate, and this
round invents none. The Images tab is backed by what IS
durable: locker records whose kind is "image" — photos
the visitor saved into their locker (Round 21). A
generated picture the visitor never saved does not
appear. That is the truth of the current storage, stated
here, in the tab's empty copy, and in the round report.

TRUST POSTURE. Keyed by the caller's own ogai_uid
cookie — the same posture as /history and
/notifications. A uid only ever sees its own items, and
the producers' download routes re-check ownership on
every fetch anyway.
"""

import logging
import sys
import threading
from datetime import datetime
from http.cookies import SimpleCookie

from fastapi import Request

logger = logging.getLogger(__name__)

UID_COOKIE = "ogai_uid"
ITEM_CAP = 100


def _iso_to_epoch(text) -> float:
    try:
        return datetime.fromisoformat(str(text)).timestamp()
    except Exception:
        return 0.0


def _video_items(uid: str) -> list:
    import og_video
    out = []
    for rec in og_video.list_jobs_for_owner(uid):
        view = og_video._job_view(rec)
        if not view:
            continue
        done = view.get("state") == "done"
        out.append({
            "kind": "video", "id": view.get("id", ""),
            "title": view.get("title") or "OG's Story",
            "created_at": float(rec.get("created", 0) or 0),
            "status": view.get("state", ""),
            "action": "Play" if done else "",
            "action_url": view.get("video_url", "") if done else "",
            "meta": {"detail": view.get("detail", ""),
                     "size": int(view.get("size", 0) or 0),
                     "duration": float(view.get("duration", 0) or 0)},
        })
    return out


def _song_items(uid: str) -> list:
    import og_songs
    out = []
    for rec in og_songs.list_jobs_for_owner(uid):
        view = og_songs._job_view(rec)
        if not view:
            continue
        done = view.get("state") == "done"
        out.append({
            "kind": "song", "id": view.get("id", ""),
            "title": view.get("title") or "OG's Song",
            "created_at": float(rec.get("created", 0) or 0),
            "status": view.get("state", ""),
            "action": "Play" if done else "",
            "action_url": view.get("song_url", "") if done else "",
            "meta": {"style": view.get("style", ""),
                     "detail": view.get("detail", ""),
                     "size": int(view.get("size", 0) or 0),
                     "duration": float(view.get("duration", 0) or 0)},
        })
    return out


def _podcast_items(uid: str) -> list:
    import og_podcast
    out = []
    for rec in og_podcast.list_jobs_for_owner(uid):
        view = og_podcast._job_view(rec)
        if not view:
            continue
        done = view.get("state") == "done"
        out.append({
            "kind": "podcast", "id": view.get("id", ""),
            "title": view.get("title") or "OG's Podcast",
            "created_at": float(rec.get("created", 0) or 0),
            "status": view.get("state", ""),
            "action": "Play" if done else "",
            "action_url": view.get("podcast_url", "") if done else "",
            "meta": {"detail": view.get("detail", ""),
                     "size": int(view.get("size", 0) or 0),
                     "duration": float(view.get("duration", 0) or 0),
                     "voices": view.get("voices", []),
                     "tts_calls": int(view.get("tts_calls", 0) or 0)},
        })
    return out


def _book_items(uid: str) -> list:
    import og_books
    out = []
    for rec in og_books.list_jobs_for_owner(uid):
        view = og_books._book_view(rec)
        if not view:
            continue
        done = view.get("status") == "done"
        started = int(view.get("chapters_done", 0) or 0) > 0
        out.append({
            "kind": "book", "id": view.get("id", ""),
            "title": view.get("title") or "OG's Book",
            "created_at": float(rec.get("created", 0) or 0),
            "status": view.get("status", ""),
            "action": "Read" if (done or started) else "",
            "action_url": (f"/books/{view.get('id', '')}/read"
                           if (done or started) else ""),
            "meta": {"detail": view.get("detail", ""),
                     "chapters_done": int(
                         view.get("chapters_done", 0) or 0),
                     "chapters_total": int(
                         view.get("chapters_total", 0) or 0),
                     "words": int(view.get("words", 0) or 0),
                     "cover_url": view.get("cover_url", "")},
        })
    return out


def _motion_items(uid: str) -> list:
    import og_motion
    out = []
    for rec in og_motion.list_jobs_for_owner(uid):
        view = og_motion._job_view(rec)
        if not view:
            continue
        done = view.get("state") == "done"
        out.append({
            "kind": "motion", "id": view.get("id", ""),
            "title": view.get("title") or "OG's Motion Clip",
            "created_at": float(rec.get("created", 0) or 0),
            "status": view.get("state", ""),
            "action": "Play" if done else "",
            "action_url": view.get("motion_url", "") if done else "",
            "meta": {"detail": view.get("detail", ""),
                     "size": int(view.get("size", 0) or 0),
                     "duration": float(view.get("duration", 0) or 0),
                     "prompt": view.get("prompt", ""),
                     "provider": "runway"},
        })
    return out


def _model3d_items(uid: str) -> list:
    import og_models3d
    out = []
    for rec in og_models3d.list_jobs_for_owner(uid):
        view = og_models3d._job_view(rec)
        if not view:
            continue
        done = view.get("state") == "done"
        out.append({
            "kind": "model3d", "id": view.get("id", ""),
            "title": view.get("title") or "OG's 3D Model",
            "created_at": float(rec.get("created", 0) or 0),
            "status": view.get("state", ""),
            "action": "Download" if done else "",
            "action_url": view.get("model_url", "") if done else "",
            "meta": {"detail": view.get("detail", ""),
                     "size": int(view.get("size", 0) or 0),
                     "prompt": view.get("prompt", ""),
                     "provider": "tripo"},
        })
    return out


def _sfx_items(uid: str) -> list:
    import og_voice_lab
    out = []
    for rec in og_voice_lab.list_jobs_for_owner(uid):
        view = og_voice_lab._job_view(rec)
        if not view:
            continue
        done = view.get("state") == "done"
        out.append({
            "kind": "sfx", "id": view.get("id", ""),
            "title": view.get("title") or "OG's Sound Effect",
            "created_at": float(rec.get("created", 0) or 0),
            "status": view.get("state", ""),
            "action": "Play" if done else "",
            "action_url": view.get("sfx_url", "") if done else "",
            "meta": {"detail": view.get("detail", ""),
                     "size": int(view.get("size", 0) or 0),
                     "duration": float(view.get("duration", 0) or 0),
                     "description": view.get("description", "")},
        })
    return out


def _project_items(uid: str) -> list:
    import og_unity
    out = []
    for pkg in og_unity.list_packages_for_owner(uid):
        out.append({
            "kind": "project", "id": pkg.get("id", ""),
            "title": pkg.get("name") or "Unity project",
            "created_at": _iso_to_epoch(pkg.get("created")),
            "status": "ready",
            "action": "Download",
            "action_url": f"/unity/download/{pkg.get('id', '')}",
            "meta": {"filename": pkg.get("filename", ""),
                     "size": int(pkg.get("size", 0) or 0),
                     "files": len(pkg.get("files") or [])},
        })
    return out


def _locker(uid: str, request: Request):
    """(items, storage_line) from og_storage in ONE pass, so
    the file list and the usage line can never disagree. The
    locker section exists only while the locker itself is
    enabled — its download routes 404 while dark, so listing
    records then would hand out dead links."""
    import og_storage
    import og_tiers
    if not og_storage.storage_enabled():
        return [], None
    items = []
    for rec in og_storage.list_records(uid):
        is_image = rec.get("kind") == "image"
        items.append({
            "kind": "image" if is_image else "file",
            "id": rec.get("id", ""),
            "title": rec.get("name") or "file",
            "created_at": float(rec.get("uploaded_at", 0) or 0),
            "status": "ready",
            "action": "Open" if is_image else "Download",
            "action_url": f"/storage/download/{rec.get('id', '')}",
            "meta": {"size": int(rec.get("size", 0) or 0),
                     "size_human": og_storage.fmt_size(
                         rec.get("size", 0)),
                     "content_type": rec.get("content_type", ""),
                     "locker_kind": rec.get("kind", "")},
        })
    tier = og_tiers.tier_of(request.cookies, uid)
    quota = og_storage.quota_bytes(tier)
    used = og_storage.usage_bytes(uid)
    line = {"enabled": True, "tier": tier, "used": used,
            "quota": quota,
            "used_human": og_storage.fmt_size(used),
            "quota_human": og_storage.fmt_size(quota)}
    return items, line


def collect_library(uid: str, request: Request):
    """(items newest-first capped, storage_line, counts).
    Each producer is guarded on its own: a broken section
    contributes nothing and never sinks the shelf."""
    items: list = []
    counts = {"videos": 0, "songs": 0, "images": 0,
              "files": 0, "projects": 0, "podcasts": 0,
              "books": 0, "motions": 0, "models3d": 0, "sfx": 0}
    for key, fn in (("videos", _video_items),
                    ("songs", _song_items),
                    ("podcasts", _podcast_items),
                    ("books", _book_items),
                    ("motions", _motion_items),
                    ("models3d", _model3d_items),
                    ("sfx", _sfx_items),
                    ("projects", _project_items)):
        try:
            got = fn(uid)
        except Exception as e:
            logger.warning(f"Library section {key} failed: {e}")
            got = []
        counts[key] = len(got)
        items.extend(got)
    storage_line = None
    try:
        locker_items, storage_line = _locker(uid, request)
    except Exception as e:
        logger.warning(f"Library locker section failed: {e}")
        locker_items, storage_line = [], None
    counts["files"] = sum(1 for i in locker_items
                          if i["kind"] == "file")
    counts["images"] = sum(1 for i in locker_items
                           if i["kind"] == "image")
    items.extend(locker_items)
    # Round 110: filed items carry their project name
    # (og_filing's seam — a link annotation only; the item
    # itself never moves). Locker items key by record id,
    # creations by kind + job id.
    try:
        import og_filing
        by_item = og_filing.projects_by_item(uid)
        if by_item:
            for it in items:
                if (it.get("action_url") or "").startswith(
                        "/storage/download/"):
                    key = f"locker||{it.get('id', '')}"
                else:
                    key = (f"library|{it.get('kind', '')}"
                           f"|{it.get('id', '')}")
                if key in by_item:
                    it["project"] = by_item[key]
    except Exception as e:
        logger.warning(f"Library filing annotation failed: {e}")
    items.sort(key=lambda i: float(i.get("created_at", 0) or 0),
               reverse=True)
    return items[:ITEM_CAP], storage_line, counts


# ===========================================================================
# Round 70 (addition): the Feed ordering — ONE function.
#
# Brent (2026-10-11): the Feed is ordered by a per-user
# algorithm instead of plain newest-first, and the filter
# chips are gone. This function is the whole algorithm:
# it scores ONLY the caller's own records (their library
# items + their notifications) and returns the rows in
# final order — the client renders payload order verbatim.
# No learned model, no cross-user data, no fake
# intelligence: the score is recency plus named,
# time-equivalent boosts —
#   unread notification ......... +3 days
#   alert kinds (watch hits, price alerts, storage
#     warnings, approvals, sign-in needs) ......... +2 days
#   a creation still being made ... +1 day
#   any of the user's own creations ............... +6 hours
# Sort: score desc, then ts desc. Every row carries its
# score so the order is auditable.
# ===========================================================================

FEED_BOOST_UNREAD = 3 * 86400.0
FEED_BOOST_ALERT = 2 * 86400.0
FEED_BOOST_WORKING = 1 * 86400.0
FEED_BOOST_CREATION = 6 * 3600.0
FEED_ALERT_KINDS = {"watch_match", "mp_match", "price_alert",
                    "storage_warning", "approval_needed",
                    "signin_needed"}
FEED_KIND_LABELS = {"video": "Video", "song": "Song",
                    "image": "Image", "file": "File",
                    "project": "Project", "podcast": "Podcast",
                    "book": "Book", "motion": "Motion",
                    "sfx": "Sound FX", "model3d": "3D Model"}


def feed_score(src: str, kind: str, ts: float, read: bool,
               status: str) -> float:
    score = float(ts or 0)
    if src == "ntf":
        if not read:
            score += FEED_BOOST_UNREAD
        if kind in FEED_ALERT_KINDS:
            score += FEED_BOOST_ALERT
    else:
        score += FEED_BOOST_CREATION
        if status == "working":
            score += FEED_BOOST_WORKING
    return score


def feed_rows(uid: str, request: Request):
    """(rows in feed order, storage_line_text, unread,
    degraded). Rows mirror the shape the shell has always
    rendered: {src, id, kind, title, sub, ts, read, target,
    action, action_url, status, score}.

    Round 321 (FIX-PLAN Addendum V4): PER-LEG FAIL-SOFT.
    The Feed seat 500'd whole when ONE leg raised — the
    og_notify leg had no guard at all, so a stalled DB
    (og_notify._items raising StoreError under the R99
    law) killed the seat while the fail-soft Ideas and
    Progress seats rendered. Now the library aggregate
    and the notify leg each degrade to absent on failure
    and the payload carries degraded=True, so the seat
    can say "some of the Feed couldn't load" instead of
    failing whole. Mirrors og_activity.background_for's
    per-leg pattern."""
    import og_notify
    degraded = False
    try:
        items, storage_line, _counts = collect_library(
            uid, request)
    except Exception as e:
        logger.warning(f"Feed library leg failed: {e}")
        items, storage_line = [], None
        degraded = True
    rows: list = []
    for it in items:
        ts = float(it.get("created_at", 0) or 0)
        status = it.get("status", "")
        rows.append({
            "src": "lib", "id": it.get("id", ""),
            "kind": it.get("kind", ""),
            "title": it.get("title", ""),
            "sub": (FEED_KIND_LABELS.get(it.get("kind", ""), "Item")
                    + (" · making…" if status == "working" else "")),
            "ts": ts, "read": True, "target": None,
            "action": it.get("action", ""),
            "action_url": it.get("action_url", ""),
            "status": status,
            "score": feed_score("lib", it.get("kind", ""), ts,
                                True, status),
        })
    unread = 0
    try:
        notify_items = og_notify._items(uid)
    except Exception as e:
        # Round 321 (V4): the notify leg degrades to
        # absent — never sinks the whole seat again.
        logger.warning(f"Feed notify leg failed: {e}")
        notify_items = []
        degraded = True
    for it in notify_items:
        if not isinstance(it, dict):
            continue
        ts = float(it.get("ts", 0) or 0)
        read = bool(it.get("read"))
        if not read:
            unread += 1
        rows.append({
            "src": "ntf", "id": it.get("id", ""),
            "kind": it.get("kind", ""),
            "title": it.get("title", ""),
            "sub": it.get("body", ""),
            "ts": ts, "read": read,
            "target": it.get("target"),
            "action": "", "action_url": "", "status": "",
            "score": feed_score("ntf", it.get("kind", ""), ts,
                                read, ""),
        })
    rows.sort(key=lambda r: (-(r["score"]), -(r["ts"])))
    line = None
    if isinstance(storage_line, dict):
        line = (f"Locker: {storage_line.get('used_human', '0 B')} "
                f"of {storage_line.get('quota_human', '0 B')} used")
    return rows[:ITEM_CAP], line, unread, degraded


# ===========================================================================
# Round 321 (D2): POST /chat/leave — leaving wipes the thread.
#
# Brent's rule (2026-10-15, D2 approved as proposed): if a user
# backs out of the website, the chat resets. ONLY the visible
# thread — account/long-term memory is a different store and
# survives untouched, and this is deliberately NOT POST /reset's
# shape (that also clears the shared agent's in-memory history
# wholesale). The durable thread store is og_visitor_memory,
# which /chat reads keyed-first since Round 135, so the clear
# rides og_memory_io's keyed delete. When no DB is configured,
# the JSON file store is cleared with app.py's own
# _clear_visitor_history shape, mirrored here (app.py is not
# editable from a seam). One known limit, shared with every
# cross-tab race in the app: a chat turn already in flight on
# another tab can save its thread back after the clear.
# ===========================================================================

_thread_clear_lock = threading.Lock()


def _clear_thread(uid: str) -> bool:
    """Remove ONLY uid's stored chat thread. True when no
    thread remains for uid afterwards (the row is deleted,
    or there never was one and the delete was handled);
    False on a store failure — the answer never claims a
    clear that did not happen."""
    import og_memory_io as _memio
    if _memio.MEMORY_DB_URL:
        return bool(_memio.delete_history(uid))
    import json
    import os
    path = "memory_store.json"
    try:
        with _thread_clear_lock:
            store = {}
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    store = data
            if uid not in store:
                return True
            del store[uid]
            with open(path, "w") as f:
                json.dump(store, f)
            return True
    except Exception as e:
        logger.warning(f"Chat-leave thread clear failed: {e}")
        return False


def _request_uid(request: Request) -> str:
    """Round 95: the caller's ONE identity — the live
    session's account uid when signed in, else the
    presented cookie (og_accounts.session_uid_or, the
    tierfix resolver). The Feed and the notification
    surfaces resolve identity the same way, so they can
    never read different piles for the same person."""
    cookie_uid = request.cookies.get(UID_COOKIE) or ""
    try:
        import og_accounts as _acct
        return _acct.session_uid_or(request.cookies,
                                    cookie_uid)
    except Exception:
        return cookie_uid


class ChatDisconnectLogMiddleware:
    """Round 107 (item 8): ONE info line when a /chat client
    disconnects mid-stream. The diagnosis: a dropped stream
    used to vanish without a trace — the stream worker's
    sink swallows the dead loop silently BY DESIGN (the
    worker still finishes and saves the turn), so the
    server log held the access line and nothing about the
    drop. This pure-ASGI wrapper (the og_accounts
    SignInGateMiddleware precedent) counts the SSE bytes
    streamed for a POST /chat and, if the transport reports
    a disconnect before the response completes, writes
    exactly one INFO line with the visitor's uid + the
    byte count. Every other path passes through unwrapped,
    and the messages themselves are never altered."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope.get("type") != "http"
                or scope.get("path") != "/chat"
                or scope.get("method") != "POST"):
            await self.app(scope, receive, send)
            return
        uid = ""
        for name, value in scope.get("headers") or []:
            if name.lower() == b"cookie":
                try:
                    jar = SimpleCookie()
                    jar.load(value.decode("latin-1"))
                    if UID_COOKIE in jar:
                        uid = jar[UID_COOKIE].value
                except Exception:
                    uid = ""
                break
        state = {"bytes": 0, "done": False, "logged": False}

        async def send_wrap(message):
            if message["type"] == "http.response.body":
                state["bytes"] += len(message.get("body") or b"")
                if not message.get("more_body"):
                    state["done"] = True
            await send(message)

        async def receive_wrap():
            message = await receive()
            if (message["type"] == "http.disconnect"
                    and not state["done"] and not state["logged"]):
                state["logged"] = True
                logger.info(
                    "chat stream client disconnected mid-turn: "
                    f"uid={uid or '?'} "
                    f"bytes_streamed={state['bytes']}")
            return message

        await self.app(scope, receive_wrap, send_wrap)


_warm_lock = threading.Lock()
_warm_started = False


def _warm_agent_once(app) -> None:
    """Round 107 (B5): warm the shared agent at worker boot.

    /health and the first chat used to pay the cold
    get_agent() build (the agent + its ~24 installers)
    inside a request — on the 2026-10-12 loop the health
    check itself timed out behind it. The build now runs
    on a daemon thread spawned from this registration,
    once per worker, so by the time traffic arrives the
    call is a cache hit. Gated to the REAL app (the app
    module's own app object): a test mount of these routes
    warms nothing. The lazy `import app` inside the thread
    waits out the import lock when registration happens
    mid-import, and any failure is one warning — warming
    must never take a boot down with it."""
    global _warm_started
    appmod = sys.modules.get("app")
    if appmod is None or getattr(appmod, "app", None) is not app:
        return
    with _warm_lock:
        if _warm_started:
            return
        _warm_started = True

    def _warm():
        try:
            import app as _appmod
            _appmod.get_agent()
        except Exception as e:
            logger.warning(f"Agent warm-up failed: {e}")

    threading.Thread(target=_warm, daemon=True,
                     name="og-agent-warm").start()


def register_library_routes(app):

    # Round 62: message reactions + OG respond ride this
    # registration (app.py already calls register_library_routes;
    # app.py is at its push ceiling, so the sibling modules
    # register from here — the Round 60 conventions precedent).
    # Fail-safe: an import problem in either can never take the
    # library route down with it.
    try:
        import og_reactions as _ogr
        _ogr.register_reaction_routes(app)
    except Exception as e:
        logger.warning(f"Reaction routes skipped: {e}")
    try:
        import og_respond as _ogresp
        _ogresp.register_respond_routes(app)
    except Exception as e:
        logger.warning(f"Respond routes skipped: {e}")
    # Round 63: 2-step verification routes ride the same
    # registration (app.py ceiling — see og_reactions above).
    try:
        import og_2fa as _og2fa
        _og2fa.register_2fa_routes(app)
    except Exception as e:
        logger.warning(f"2FA routes skipped: {e}")
    # Round 54: Marketplace watch approval routes ride the same
    # registration (app.py ceiling — see above).
    try:
        import og_mpwatch as _ogmp
        _ogmp.register_mp_routes(app)
    except Exception as e:
        logger.warning(f"MPWatch routes skipped: {e}")
    # Round 65: language preference routes ride the same
    # registration (app.py ceiling — see above).
    try:
        import og_language as _oglang
        _oglang.register_language_routes(app)
    except Exception as e:
        logger.warning(f"Language routes skipped: {e}")
    # Round 66: GPS routes (points / convert / between /
    # stakeout / route) ride the same registration.
    try:
        import og_gps as _oggps
        _oggps.register_gps_routes(app)
    except Exception as e:
        logger.warning(f"GPS routes skipped: {e}")
    # Generator 7a: podcast routes (/podcast/status, approve,
    # decline, download, save) ride the same registration.
    try:
        import og_podcast as _ogpod
        _ogpod.register_podcast_routes(app)
    except Exception as e:
        logger.warning(f"Podcast routes skipped: {e}")
    # Generator 7b: designer routes (/designer/status, generate,
    # download, save) ride the same registration. Designs reach
    # the Images tab through the locker (og_storage records),
    # so no new aggregation section is needed here.
    try:
        import og_designer as _ogdes
        _ogdes.register_designer_routes(app)
    except Exception as e:
        logger.warning(f"Designer routes skipped: {e}")
    # Generator 7c: book routes (/books, /books/status,
    # approve/decline, /books/<id>/read|download|continue)
    # ride the same registration.
    try:
        import og_books as _ogbooks
        _ogbooks.register_book_routes(app)
    except Exception as e:
        logger.warning(f"Book routes skipped: {e}")
    # Generator 7d: motion routes (/motion/status, approve,
    # decline, download, save) ride the same registration.
    try:
        import og_motion as _ogmotion
        _ogmotion.register_motion_routes(app)
    except Exception as e:
        logger.warning(f"Motion routes skipped: {e}")
    # Generator 7e: 3D-model routes (/models3d/status, approve,
    # decline, download, save) ride the same registration.
    try:
        import og_models3d as _ogmodels
        _ogmodels.register_models3d_routes(app)
    except Exception as e:
        logger.warning(f"Model3D routes skipped: {e}")
    # Generator 7f: voice-lab routes (/voice-lab/status,
    # voices, samples, approve, decline, delete, tts,
    # download, save) ride the same registration.
    try:
        import og_voice_lab as _ogvoice
        _ogvoice.register_voice_lab_routes(app)
    except Exception as e:
        logger.warning(f"Voice lab routes skipped: {e}")
    # Wallet Phase A: wallet routes (/wallet/status, setup,
    # complete, history, remove) ride the same registration.
    # Phase A is tokenize-only — no Library section is added
    # (a wallet is a payment method, not an artifact).
    try:
        import og_wallet as _ogwallet
        _ogwallet.register_wallet_routes(app)
    except Exception as e:
        logger.warning(f"Wallet routes skipped: {e}")
    # Round 69: the nightly auto-update's operator surface +
    # scheduler loop ride the same registration (app.py stays
    # untouched — it is at its push ceiling).
    try:
        import og_autoupdate as _ogau
        _ogau.register_autoupdate_routes(app)
    except Exception as e:
        logger.warning(f"Autoupdate routes skipped: {e}")

    @app.get("/library")
    def library_list(request: Request):
        uid = _request_uid(request)
        if not uid:
            return {"items": [], "storage": None,
                    "counts": {"videos": 0, "songs": 0,
                               "images": 0, "files": 0,
                               "projects": 0, "podcasts": 0,
                               "books": 0, "motions": 0,
                               "models3d": 0, "sfx": 0}}
        items, storage_line, counts = collect_library(
            uid, request)
        return {"items": items, "storage": storage_line,
                "counts": counts}

    @app.get("/feed")
    def feed_list(request: Request):
        """Round 70 (addition): the Feed seat's one payload —
        the caller's rows in feed_rows() order (the ordering
        lives server-side; the client renders it verbatim).
        Behind the same sign-in gate as /library."""
        uid = _request_uid(request)
        if not uid:
            return {"rows": [], "storage_line": None,
                    "unread": 0, "degraded": False}
        rows, line, unread, degraded = feed_rows(uid, request)
        return {"rows": rows, "storage_line": line,
                "unread": unread, "degraded": degraded}

    @app.post("/chat/leave")
    async def chat_leave(request: Request):
        """Round 321 (D2): the page's pagehide beacon lands
        here — the caller's visible chat THREAD is wiped
        (account memory untouched; see _clear_thread).
        Idempotent, own-thread only (the uid is the
        session-resolved identity every route here uses);
        an anonymous beacon is a clean no-op."""
        import asyncio
        uid = _request_uid(request)
        if not uid:
            return {"ok": True, "cleared": False}
        cleared = await asyncio.to_thread(_clear_thread, uid)
        return {"ok": True, "cleared": cleared}

    @app.post("/browser/leave")
    async def browser_leave(request: Request):
        """Round 321 (Piece 1, Req 2): the pagehide beacon's
        other landing — the caller's browser session ends
        NOW through og_browser's one funnel (_end_session:
        Steel released, minutes metered, record + parked
        approval cleared) instead of lingering to the idle
        kill. Mounted here, not in og_browser, because that
        module sits at the push ceiling; the handler is
        three lines of og_browser calls. Cookie-scoped:
        a caller ends only their OWN session; leaving with
        no live session is a clean no-op."""
        import asyncio

        import og_browser as _ogb
        from fastapi.responses import JSONResponse
        if not _ogb.enabled():
            return JSONResponse({"error": "not found"},
                                status_code=404)
        uid = _ogb._route_uid(request)
        if not uid:
            return {"ok": True, "ended": False}
        rec = await asyncio.to_thread(_ogb._end_session, uid,
                                      "page left")
        return {"ok": True, "ended": rec is not None}

    # Round 70 (addition): OG-generated ideas (og_ideas.py)
    # ride the same registration.
    try:
        import og_ideas as _ogideas
        _ogideas.register_idea_routes(app)
    except Exception as e:
        logger.warning(f"Idea routes skipped: {e}")
    # Round 70 (addition): the Progress aggregate
    # (og_progress.py) rides the same registration.
    try:
        import og_progress as _ogprog
        _ogprog.register_progress_routes(app)
    except Exception as e:
        logger.warning(f"Progress routes skipped: {e}")
    # Round 321 dashboards batch: the Manager Dashboard
    # (Crew Dashboard, R267 slot — GET /dashboards/state,
    # /crewdash/data, /crewdash/export in og_crewdash.py)
    # rides the same registration.
    try:
        import og_crewdash as _ogcdash
        _ogcdash.register_crewdash_routes(app)
    except Exception as e:
        logger.warning(f"Crewdash routes skipped: {e}")
    # Round 321 dashboards batch: the Crypto Hub
    # (GET /cryptohub/data, /cryptohub/watchlist in
    # og_cryptohub.py) rides the same registration.
    try:
        import og_cryptohub as _ogchub
        _ogchub.register_cryptohub_routes(app)
    except Exception as e:
        logger.warning(f"Cryptohub routes skipped: {e}")
    # Round 321 dashboards batch: the Financial Smart
    # Agent (GET /finagent/quote, /finagent/sentiment,
    # /finagent/portfolio in og_finagent.py) rides the
    # same registration.
    try:
        import og_finagent as _ogfin
        _ogfin.register_finagent_routes(app)
    except Exception as e:
        logger.warning(f"Finagent routes skipped: {e}")
    # Ticket L: the Financial Smart Agent's bank picture
    # (GET /finagent/bank in og_findash.py) rides the
    # same registration.
    try:
        import og_findash as _ogfindash
        _ogfindash.register_findash_routes(app)
    except Exception as e:
        logger.warning(f"Findash routes skipped: {e}")
    # Round 81: the crew's one route (GET /crew/status — the
    # roster payload the chat answer also uses) rides the
    # same registration.
    try:
        import og_crew as _ogcrew
        _ogcrew.register_crew_routes(app)
    except Exception as e:
        logger.warning(f"Crew routes skipped: {e}")
    # Round 84: the shit-talking setting's routes
    # (GET/POST /shittalk — the per-account register switch)
    # ride the same registration.
    try:
        import og_shittalk as _ogshit
        _ogshit.register_shittalk_routes(app)
    except Exception as e:
        logger.warning(f"Shittalk routes skipped: {e}")
    # Round 90: the Permissions folder's routes
    # (GET/POST /permissions — the one per-account store
    # for everything OG runs on the user's phone) ride
    # the same registration.
    try:
        import og_permissions as _ogperm
        _ogperm.register_permissions_routes(app)
    except Exception as e:
        logger.warning(f"Permissions routes skipped: {e}")
    # Round 98: the Voice folder's routes (GET/POST
    # /voice — the one per-account store for OG's
    # speaking voice) ride the same registration.
    try:
        import og_voice_style as _ogvoicestyle
        _ogvoicestyle.register_voice_routes(app)
    except Exception as e:
        logger.warning(f"Voice routes skipped: {e}")
    # Round 108: the Reboot routes (GET /reboot/status,
    # POST /reboot — the per-account soft reboot;
    # POST /reboot/server — the owner-only server
    # restart) ride the same registration.
    try:
        import og_reboot as _ogreboot
        _ogreboot.register_reboot_routes(app)
    except Exception as e:
        logger.warning(f"Reboot routes skipped: {e}")
    # Round 109: the Project Manager's one route
    # (GET /pm/status — the projects payload the chat answer
    # also uses) rides the same registration.
    try:
        import og_pm as _ogpm
        _ogpm.register_pm_routes(app)
    except Exception as e:
        logger.warning(f"PM routes skipped: {e}")
    # Round 110: the Project Filing routes (GET /filing —
    # the caller's projects with file counts + links;
    # GET /filing/project/<id> — one project's file list,
    # the Progress row's tap-through target) ride the same
    # registration.
    try:
        import og_filing as _ogfiling
        _ogfiling.register_filing_routes(app)
    except Exception as e:
        logger.warning(f"Filing routes skipped: {e}")
    # Round 111: the Project Builder's one route
    # (GET /builder/status — the caller's own build plans)
    # rides the same registration.
    try:
        import og_builder as _ogbuilder
        _ogbuilder.register_builder_routes(app)
    except Exception as e:
        logger.warning(f"Builder routes skipped: {e}")
    # Round 112: the Wood Project Builder Pro's one route
    # (GET /woodpro/status — the caller's own wood plans)
    # rides the same registration.
    try:
        import og_woodpro as _ogwoodpro
        _ogwoodpro.register_woodpro_routes(app)
    except Exception as e:
        logger.warning(f"WoodPro routes skipped: {e}")
    # Round 113: the Maintenance + Engineer helper's one
    # route (GET /mainteng/status — the caller's own walk
    # + schedules) rides the same registration.
    try:
        import og_mainteng as _ogmainteng
        _ogmainteng.register_mainteng_routes(app)
    except Exception as e:
        logger.warning(f"MaintEng routes skipped: {e}")
    # Round 114: the Car Mechanic helper's one route
    # (GET /mechanic/status — the caller's own walk +
    # their own vehicles) rides the same registration.
    try:
        import og_mechanic as _ogmechanic
        _ogmechanic.register_mechanic_routes(app)
    except Exception as e:
        logger.warning(f"Mechanic routes skipped: {e}")
    # Round 115: the Blueprint Specialist helper's one
    # route (GET /blueprint/status — the caller's own
    # sheets + the one on the table) rides the same
    # registration.
    try:
        import og_blueprint as _ogblueprint
        _ogblueprint.register_blueprint_routes(app)
    except Exception as e:
        logger.warning(f"Blueprint routes skipped: {e}")
    # Round 116: the Shopping helper's one route
    # (GET /shopping/status — the caller's own current
    # shortlist summary + saved pick) rides the same
    # registration.
    try:
        import og_shopping as _ogshopping
        _ogshopping.register_shopping_routes(app)
    except Exception as e:
        logger.warning(f"Shopping routes skipped: {e}")
    # Round 117: the Buying helper's one route
    # (GET /buying/status — the caller's own last
    # prepared buy + whether its card is live) rides
    # the same registration.
    try:
        import og_buying as _ogbuying
        _ogbuying.register_buying_routes(app)
    except Exception as e:
        logger.warning(f"Buying routes skipped: {e}")
    # Round 118: the Selling helper's one route
    # (GET /selling/status — the caller's own working
    # listing + book summary + pending state) rides the
    # same registration.
    try:
        import og_selling as _ogselling
        _ogselling.register_selling_routes(app)
    except Exception as e:
        logger.warning(f"Selling routes skipped: {e}")
    # Round 119: the Coding + Programming specialist's
    # one route (GET /coding/status — the caller's own
    # bench: last ask, code standing, last check
    # summary by name) rides the same registration.
    try:
        import og_coding as _ogcoding
        _ogcoding.register_coding_routes(app)
    except Exception as e:
        logger.warning(f"Coding routes skipped: {e}")
    # Round 120: the Phone specialist's one route
    # (GET /phone/status — the caller's own bench: the
    # phone on record, any open check, the last thing
    # worked) rides the same registration.
    try:
        import og_phone as _ogphone
        _ogphone.register_phone_routes(app)
    except Exception as e:
        logger.warning(f"Phone routes skipped: {e}")
    # Round 121: the CarPlay specialist's one route
    # (GET /carplay/status — the caller's own bench:
    # the platform on record, the wired/wireless
    # shape, any open checklist step, the last thing
    # worked) rides the same registration.
    try:
        import og_carplay as _ogcarplay
        _ogcarplay.register_carplay_routes(app)
    except Exception as e:
        logger.warning(f"CarPlay routes skipped: {e}")
    # Round 122: the Tracker specialist's one route
    # (GET /tracker/status — the caller's own tracking
    # counts, read live off the watch + monitor stores,
    # + the bench's last action) rides the same
    # registration.
    try:
        import og_tracker as _ogtracker
        _ogtracker.register_tracker_routes(app)
    except Exception as e:
        logger.warning(f"Tracker routes skipped: {e}")
    # Round 123: the Marketplace specialist's one route
    # (GET /marketplace/status — the caller's own hunt
    # summary + book count, with watch and open-approval
    # counts read live off the og_watch / og_mpwatch
    # stores) rides the same registration.
    try:
        import og_marketplace as _ogmarketplace
        _ogmarketplace.register_marketplace_routes(app)
    except Exception as e:
        logger.warning(f"Marketplace routes skipped: {e}")
    # Round 124: the Facebook specialist's one route
    # (GET /facebook/status — the caller's own bench:
    # the Page on record, any parked draft, and the
    # live browser session's host read live) rides
    # the same registration.
    try:
        import og_facebook as _ogfacebook
        _ogfacebook.register_facebook_routes(app)
    except Exception as e:
        logger.warning(f"Facebook routes skipped: {e}")
    # Round 125: the Messenger specialist's one route
    # (GET /messenger/status — the caller's own bench:
    # the parked message draft, the last kept thread
    # read with its time, and the live browser
    # session's host read live) rides the same
    # registration.
    try:
        import og_messenger as _ogmessenger
        _ogmessenger.register_messenger_routes(app)
    except Exception as e:
        logger.warning(f"Messenger routes skipped: {e}")
    # Round 126: the YouTube specialist seat's one route
    # (GET /youtube/status — the caller's own bench:
    # the channel + niche on record, any parked draft,
    # and the connect's state read live) rides the same
    # registration. The seat extends the Round 15
    # og_youtube connect module in place; the connect's
    # own /auth/youtube* routes stay app.py's, untouched.
    try:
        import og_youtube as _ogyoutube
        _ogyoutube.register_youtube_seat_routes(app)
    except Exception as e:
        logger.warning(f"YouTube seat routes skipped: {e}")
    # Round 127: the Website + Browser specialist's
    # one route (GET /webbrowser/status — the
    # caller's own browser picture: session, plan
    # minutes, parked approval, always-allow hosts,
    # parked compare, read live off og_browser's own
    # surfaces) rides the same registration.
    try:
        import og_webbrowser as _ogwebbrowser
        _ogwebbrowser.register_webbrowser_routes(app)
    except Exception as e:
        logger.warning(f"Webbrowser routes skipped: {e}")
    # Round 278: the God Mode Coding seat's one
    # route (GET /godmodecoding/status — the
    # caller's own god-mode bench: plans parked,
    # drafts vs filed through the approval door,
    # rescue reads held) rides the same
    # registration.
    try:
        import og_godmodecoding as _oggodmodecoding
        _oggodmodecoding.register_godmodecoding_routes(app)
    except Exception as e:
        logger.warning(f"GodModeCoding routes skipped: {e}")
    # Round 279: the God Mode Tester seat's one
    # route (GET /godmodetester/status — the
    # caller's own god-mode tester bench:
    # strategies parked, verdicts given by
    # class, escalation reads held) rides the
    # same registration.
    try:
        import og_godmodetester as _oggodmodetester
        _oggodmodetester.register_godmodetester_routes(app)
    except Exception as e:
        logger.warning(f"GodModeTester routes skipped: {e}")
    # Round 280: the God Mode Runset seat's one
    # route (GET /godmoderunset/status — the
    # caller's own god-mode runset bench: top
    # verdicts given by outcome, disputes
    # settled, the last of each) rides the
    # same registration.
    try:
        import og_godmoderunset as _oggodmoderunset
        _oggodmoderunset.register_godmoderunset_routes(app)
    except Exception as e:
        logger.warning(f"GodModeRunset routes skipped: {e}")
    # Round 275: the activity source's one route
    # (GET /activity/status — the caller's own claim
    # record + live background work + the composed
    # header-bubble line; the same snapshot the Crew
    # Dashboard will read) rides the same registration.
    try:
        import og_activity as _ogactivity
        _ogactivity.register_activity_routes(app)
    except Exception as e:
        logger.warning(f"Activity routes skipped: {e}")
    # Round 107 (item 8): the chat-disconnect log — a
    # pure-ASGI middleware in this module (see the class
    # above); registered here because app.py is at its
    # push ceiling.
    try:
        app.add_middleware(ChatDisconnectLogMiddleware)
    except Exception as e:
        logger.warning(f"Chat disconnect log skipped: {e}")
    # Round 107 (B5): warm the agent at boot (real app
    # only — the gate lives in _warm_agent_once).
    try:
        _warm_agent_once(app)
    except Exception as e:
        logger.warning(f"Agent warm-up skipped: {e}")
