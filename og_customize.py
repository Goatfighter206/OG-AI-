"""
OG AI tap-to-customize (Brent, 2026-10-09): "Make it so you can
click on the avatar to customize like name and avatar."

Clicking OG's avatar (the pinned bar at the top of the text screen,
or the empty-state showcase) opens a small sheet where THIS visitor
can change two things, for themselves only:

  1. His DISPLAY NAME — the label under the pinned avatar. Default
     "OG", 1-24 visible characters. It is a LABEL, nothing more:
     the persona, the voice and how OG talks never change (the
     persona files are not touched by this module, and the name is
     never fed to the model).
  2. His AVATAR PICTURE — the visitor uploads one picture (PNG /
     JPG / WEBP, validated by magic bytes and the same per-tier
     size ceiling + daily `uploads` accounting as Round 6 file
     reading). The bytes live in the Round 21 S3 object store
     under a reserved system prefix (custom-avatars/<sha256(uid)>)
     — never in the visitor's locker, never listed there. Served
     back ONLY to the same visitor at GET /customize/avatar (the
     path carries no id at all; the caller's own cookie is the
     only key, so a stranger can never reach it). An uploaded
     picture is a STILL: it replaces the avatar image in the
     pinned bar and the showcase spots while the moving clips
     step aside; resetting to the default brings the clips back.
     The sheet says exactly that — no fake promises.

PER-VISITOR, ALWAYS: preferences are keyed by the ogai_uid cookie
and stored in the Round 18/29 durable pattern (Postgres table
og_customize_data when OG_MEMORY_DB_URL is set, else
customize_store.json). Visitor B never sees visitor A's name or
picture — there is no shared/global preference anywhere.

THE NAME IS HOSTILE TEXT: it is validated (trimmed, control
characters stripped, whitespace collapsed, 1-24 chars), stored as
plain data, returned as JSON, and the page renders it ONLY through
textContent / input .value — never innerHTML — so a name cannot
inject markup into the visitor's own page, let alone anyone
else's.

PICTURES RIDE THE LOCKER'S SWITCH: when object storage is off
(OG_STORAGE_ENABLED + keys unset), name customization still works
and the picture endpoints answer honestly that pictures are
unavailable. Reset restores the defaults (name "OG", default
avatar) and deletes the stored picture object best-effort.
Persona files are never touched.
"""

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

import og_file_read as _og_files
import og_storage as _og_storage

logger = logging.getLogger(__name__)

DEFAULT_NAME = "OG"
MAX_NAME_CHARS = 24
_AVATAR_PREFIX = "custom-avatars"

CUSTOMIZE_STORE_FILE = os.getenv(
    "OG_CUSTOMIZE_STORE_FILE", "customize_store.json")
_store_lock = threading.Lock()

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_deps: Dict = {}


def bind_app(deps):
    _deps.update(deps)


# --- Durable store (Round 18/29 pattern) ---------------------------------------


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_customize_data ("
            "uid TEXT, kind TEXT, data JSONB, PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(CUSTOMIZE_STORE_FILE):
        try:
            with open(CUSTOMIZE_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Customize store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(CUSTOMIZE_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Customize store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_customize_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"Customize DB load failed, using file: {e}")
    with _store_lock:
        store = _load_file_store()
    entry = (store.get(uid) or {}).get(kind)
    return entry if entry is not None else default


def _put_blob(uid: str, kind: str, value):
    if not uid:
        return
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_customize_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Customize DB save failed, using file: {e}")
    with _store_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


# --- Preferences -----------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_prefs() -> Dict:
    return {"name": DEFAULT_NAME, "avatar": "default", "avatar_mime": ""}


def get_prefs(uid: str) -> Dict:
    """This visitor's prefs, defaults-filled. Never another visitor's:
    the store is keyed by this uid alone."""
    p = _blob(uid, "prefs", None) if uid else None
    if not isinstance(p, dict):
        return default_prefs()
    out = default_prefs()
    name = p.get("name")
    if isinstance(name, str) and name:
        out["name"] = name
    if p.get("avatar") == "custom":
        out["avatar"] = "custom"
        mime = p.get("avatar_mime")
        out["avatar_mime"] = mime if isinstance(mime, str) else ""
    return out


def _save_prefs(uid: str, prefs: Dict) -> Dict:
    prefs = dict(prefs)
    prefs["updated"] = _now_iso()
    _put_blob(uid, "prefs", prefs)
    return get_prefs(uid)


def clean_name(raw) -> Optional[str]:
    """Validate a display name: must be a string; control/format
    characters are stripped (str.isprintable() is False for them),
    whitespace runs collapse to single spaces, ends trimmed; the
    result must be 1..MAX_NAME_CHARS visible characters. Returns
    the cleaned name, or None when there is nothing valid to keep.
    The 24-char cap is measured AFTER cleaning, on the stored
    value itself."""
    if not isinstance(raw, str):
        return None
    kept = "".join(ch for ch in raw if ch.isprintable())
    cleaned = " ".join(kept.split())
    if not cleaned or len(cleaned) > MAX_NAME_CHARS:
        return None
    return cleaned


# --- Custom avatar picture (Round 21 S3 layer, reserved prefix) -----------------


def pictures_available() -> bool:
    """Pictures ride the storage locker's switch (Round 21 S3)."""
    return _og_storage.storage_enabled()


def _avatar_key(uid: str) -> str:
    return f"{_AVATAR_PREFIX}/{_og_storage.owner_of(uid)}"


def _sniff_image_mime(data: bytes) -> str:
    """Image type from magic bytes only (a renamed file can't lie):
    PNG / JPG / WEBP — the Round 6 image set, minus PDF/TXT."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _delete_picture(uid: str) -> None:
    """Best-effort removal of the stored picture object."""
    if not pictures_available():
        return
    try:
        backend = _og_storage.get_backend()
        if backend is not None:
            backend.delete(_avatar_key(uid))
    except Exception as e:
        logger.warning(f"Customize picture delete failed: {e}")


# --- Routes ------------------------------------------------------------------------


def register_customize_routes(app):
    from fastapi import Request as _Req  # noqa: F401  (annotation only)
    from fastapi.responses import JSONResponse, Response
    d = _deps
    max_age = d.get("cookie_max_age", 365 * 24 * 60 * 60)

    def _uid_of(raw_request):
        """(uid, had_cookie): mints a fresh uid for a first-time
        visitor (same pattern as POST /upload), so a preference
        saved before the first chat still sticks."""
        cookie_uid = raw_request.cookies.get("ogai_uid")
        return (cookie_uid or uuid.uuid4().hex), bool(cookie_uid)

    def _finish(payload, uid, had_cookie, status_code=200):
        resp = JSONResponse(content=payload, status_code=status_code)
        if not had_cookie:
            resp.set_cookie(
                "ogai_uid", uid, max_age=max_age,
                httponly=True, samesite="lax", path="/")
        return resp

    def _public(prefs: Dict) -> Dict:
        return {"ok": True, "name": prefs["name"],
                "avatar": prefs["avatar"],
                "pictures": pictures_available()}

    @app.get("/customize")
    async def customize_get(raw_request: _Req):
        """This visitor's customize prefs (defaults when new)."""
        uid = raw_request.cookies.get("ogai_uid")
        prefs = get_prefs(uid) if uid else default_prefs()
        return JSONResponse(content=_public(prefs))

    @app.post("/customize/name")
    async def customize_name(raw_request: _Req):
        """Set this visitor's display name for OG (label only)."""
        try:
            body = await raw_request.json()
        except Exception:
            body = {}
        cleaned = clean_name((body or {}).get("name"))
        if cleaned is None:
            return JSONResponse(content={
                "ok": False,
                "response": ("That name won't fly — give me 1 to "
                             f"{MAX_NAME_CHARS} plain characters, "
                             "no funny business.")}, status_code=400)
        uid, had_cookie = _uid_of(raw_request)
        prefs = get_prefs(uid)
        prefs["name"] = cleaned
        saved = _save_prefs(uid, prefs)
        return _finish(_public(saved), uid, had_cookie)

    @app.post("/customize/avatar")
    async def customize_avatar(raw_request: _Req):
        """Set this visitor's custom avatar picture (PNG/JPG/WEBP,
        Round 6 validation + the `uploads` daily accounting)."""
        if not pictures_available():
            return JSONResponse(content={
                "ok": False, "pictures": False,
                "response": ("Yo — avatar pictures ain't switched on "
                             "right now (the picture storage is "
                             "off). Your name change still works "
                             "fine.")}, status_code=503)
        cookie_uid = raw_request.cookies.get("ogai_uid")
        uid = cookie_uid or uuid.uuid4().hex

        def _reply(payload, status_code=200):
            resp = JSONResponse(content=payload, status_code=status_code)
            if not cookie_uid:
                resp.set_cookie(
                    "ogai_uid", uid, max_age=max_age,
                    httponly=True, samesite="lax", path="/")
            return resp

        tier = _og_files._visitor_tier(uid, raw_request)
        if _og_files.uploads_left(uid, tier) <= 0:
            cap = _og_files._upload_cap(tier)
            return _reply({
                "ok": False, "capped": True, "uploads_left": 0,
                "response": (f"Yo, you already burned through your "
                             f"{cap} uploads for today — new "
                             "pictures slide back tomorrow.")})
        try:
            form = await raw_request.form()
        except Exception:
            form = {}
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return _reply({"ok": False, "response": (
                "Slide me a picture file — PNG, JPG or WEBP.")},
                status_code=400)
        raw = await upload.read(_og_files._READ_CAP)
        if not raw:
            return _reply({"ok": False, "response": (
                "That picture came through empty — try another "
                "one.")}, status_code=400)
        mime = _sniff_image_mime(raw)
        if not mime:
            return _reply({"ok": False, "response": (
                "Yo, I can't use that for a picture — I take PNG, "
                "JPG or WEBP only.")}, status_code=400)
        limit = _og_files._max_bytes(tier)
        if len(raw) > limit:
            return _reply({"ok": False, "response": (
                f"Yo, that picture's too heavy — I max out at "
                f"{limit // (1024 * 1024)} MB. Shrink it down and "
                "slide it again.")}, status_code=400)
        try:
            backend = _og_storage.get_backend()
            backend.put(_avatar_key(uid), raw, mime)
        except Exception:
            logger.warning("customize picture put failed", exc_info=True)
            return _reply({"ok": False, "response": (
                "Yo, the picture locker glitched — try that again "
                "in a sec.")}, status_code=502)
        _og_files._consume_upload(uid)
        prefs = get_prefs(uid)
        prefs["avatar"] = "custom"
        prefs["avatar_mime"] = mime
        saved = _save_prefs(uid, prefs)
        return _reply(_public(saved))

    @app.get("/customize/avatar")
    async def customize_avatar_get(raw_request: _Req):
        """The CALLER's own custom avatar picture — no id in the
        path, the cookie is the only key. 404 for anyone else, for
        no-cookie callers, and when no custom picture is set."""
        uid = raw_request.cookies.get("ogai_uid")
        if not uid or not pictures_available():
            return JSONResponse(content={"detail": "no custom avatar"},
                                status_code=404)
        prefs = get_prefs(uid)
        if prefs.get("avatar") != "custom":
            return JSONResponse(content={"detail": "no custom avatar"},
                                status_code=404)
        try:
            backend = _og_storage.get_backend()
            data = backend.get(_avatar_key(uid)) if backend else None
        except Exception:
            logger.warning("customize picture get failed", exc_info=True)
            data = None
        if not data:
            return JSONResponse(content={"detail": "no custom avatar"},
                                status_code=404)
        mime = prefs.get("avatar_mime") or _sniff_image_mime(data) \
            or "application/octet-stream"
        return Response(content=data, media_type=mime,
                        headers={"Cache-Control": "private, no-store"})

    @app.post("/customize/avatar/default")
    async def customize_avatar_default(raw_request: _Req):
        """Back to the default OG picture (name untouched); the
        moving clips come back with it."""
        uid, had_cookie = _uid_of(raw_request)
        prefs = get_prefs(uid)
        had = prefs.get("avatar") == "custom"
        prefs["avatar"] = "default"
        prefs["avatar_mime"] = ""
        saved = _save_prefs(uid, prefs)
        if had:
            _delete_picture(uid)
        return _finish(_public(saved), uid, had_cookie)

    @app.post("/customize/reset")
    async def customize_reset(raw_request: _Req):
        """Full reset: default name AND default picture."""
        uid, had_cookie = _uid_of(raw_request)
        had = get_prefs(uid).get("avatar") == "custom"
        saved = _save_prefs(uid, default_prefs())
        if had:
            _delete_picture(uid)
        return _finish(_public(saved), uid, had_cookie)
