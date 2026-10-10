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
              "files": 0, "projects": 0}
    for key, fn in (("videos", _video_items),
                    ("songs", _song_items),
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

    @app.get("/library")
    async def library_list(request: Request):
        uid = request.cookies.get(UID_COOKIE) or ""
        if not uid:
            return {"items": [], "storage": None,
                    "counts": {"videos": 0, "songs": 0,
                               "images": 0, "files": 0,
                               "projects": 0}}
        items, storage_line, counts = collect_library(
            uid, request)
        return {"items": items, "storage": storage_line,
                "counts": counts}
