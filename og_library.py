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
from datetime import datetime

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
    items.sort(key=lambda i: float(i.get("created_at", 0) or 0),
               reverse=True)
    return items[:ITEM_CAP], storage_line, counts


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

    @app.get("/library")
    async def library_list(request: Request):
        uid = request.cookies.get(UID_COOKIE) or ""
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
