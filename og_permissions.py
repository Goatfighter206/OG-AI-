"""Permissions folder (Round 90, Brent's orders
2026-10-12): "there should be a folder called permission
to run anything on their phone."

ONE per-account store for everything OG may run or use
ON the user's phone — the single source both faces and
OG's seams read:

    GET  /permissions -> {"ok": true, "caps": [
        {"key", "name", "on", "note"}, ...]}
    POST /permissions {"cap": key, "on": bool}
                      -> {"ok": true, "cap": key, "on": b}
    no uid -> 401 sign_in_required; unknown cap / non-bool
    "on" -> 400, writes nothing (the R73/R84 contract
    shape; routes ride og_library's registration — NOT in
    PUBLIC_PATHS, so the Round 44 gate covers them).

STORAGE. The codebase's established durable pattern —
one key -> JSON blob — in this module's own store,
exactly the og_gps / og_shittalk form (Postgres table
og_permissions_data when OG_MEMORY_DB_URL is set, else
a JSON file: OG_PERMISSIONS_STORE or
permissions_store.json). Records are keyed "perm:"+uid
-> {"caps": {cap: bool}} and hold ONLY the values the
user explicitly set; the effective value is the stored
one when present, else the capability's default. No
migration: an account with no record already reads
exactly right.

THE ROWS. Brent's nine, verbatim, in his order —
carplay, maps, calculator, calendar, health,
apple_watch, spotify, pandora, gps — plus the audit's
proven additions from what OG actually touches on a
phone (round-90/NOTES.md has the proof): microphone
(SpeechRecognition dictation/Talk/voice approvals),
camera_photos (the Add sheet's Camera/Picture tiles),
notifications (og_notify's prefs/subscriptions).

GPS IS NOT A VALUE IN THIS STORE. The gps row CARRIES
Round 73's account location switch itself (og_gps,
"gpsloc:"+uid, GET/POST /gps/location): its payload
value is read live from og_gps.location_on_for and its
POST delegates to og_gps.set_location_on, so the
Permissions folder, the chat flips (R73) and the
one-time ask (R78) can never disagree — one switch,
one source, a new home. Its default is the switch's
own: OFF.

DEFAULTS (pinned in the r90 suite; reasoning in
round-90/NOTES.md): maps / microphone / camera_photos /
calculator / calendar / spotify / pandora default ON —
they govern features that already run behind the
browser's or the account connection's own gates, and
an OFF default would silently break them for every
existing user. health / apple_watch / carplay default
OFF (sensitive data; the car) and notifications
defaults OFF, matching og_notify's master prefs.
Phone-build-only rows (carplay, apple_watch, health)
carry their honesty note on the payload itself: the
permission is held now, the capability arrives with
the phone build, and no row claims a live integration
that does not exist.

WHO READS THE STORE TODAY: the maps handoff — the web
chat's "Open in Maps" affordance renders only while
the maps permission is on (og_r90.js consults
GET /permissions) — and every Permissions folder paint
on both faces. Other seams read it as they are built;
the store is the one source they will read.
"""

import json
import logging
import os
import threading

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

UID_COOKIE = "ogai_uid"
STORE_FILE = os.getenv("OG_PERMISSIONS_STORE",
                       "permissions_store.json")

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, StoreError, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_store_lock = threading.RLock()

# ===========================================================================
# The capability table (display order = Brent's list, then the audit's)
# ===========================================================================

_PHONE_NOTE = ("Arrives with the phone build — your "
               "permission is saved now.")

CAPS = [
    {"key": "carplay", "name": "CarPlay", "default": False,
     "note": "OG in your car through CarPlay. " + _PHONE_NOTE},
    {"key": "maps", "name": "Maps", "default": True,
     "note": "When OG works out a route in chat, the answer "
             "carries an Open in Maps button that hands the "
             "drive to your phone's maps app."},
    {"key": "calculator", "name": "Calculator", "default": True,
     "note": "Permission for OG to use a calculator with "
             "you. Today he does the math right in chat."},
    {"key": "calendar", "name": "Calendar", "default": True,
     "note": "Permission for OG to use your calendar. On "
             "the web his calendar work runs through the "
             "Google account you connected (see "
             "Connections); the phone build will use the "
             "calendar on your phone."},
    {"key": "health", "name": "Health", "default": False,
     "note": "Your health data — steps, workouts, heart. "
             + _PHONE_NOTE},
    {"key": "apple_watch", "name": "Apple Watch",
     "default": False,
     "note": "OG can work with your Apple Watch. "
             + _PHONE_NOTE},
    {"key": "spotify", "name": "Spotify", "default": True,
     "note": "Permission for OG to work with Spotify for "
             "you — connect the account in Connections; "
             "this is your standing yes or no."},
    {"key": "pandora", "name": "Pandora", "default": True,
     "note": "Permission for OG to hand music off to "
             "Pandora on your phone. There is no direct "
             "Pandora connection yet — this saves your "
             "standing permission."},
    {"key": "gps", "name": "GPS", "default": False,
     "note": "OG's location switch — the same one chat "
             "flips. It stays how you leave it, on every "
             "device signed into your account, until you "
             "tap it again or ask OG to change it. Your "
             "browser still has its own location "
             "permission underneath it."},
    {"key": "microphone", "name": "Microphone",
     "default": True,
     "note": "Talk and dictation. Your browser asks for "
             "the mic itself — this is OG's standing "
             "permission to use it with you."},
    {"key": "camera_photos", "name": "Camera & Photos",
     "default": True,
     "note": "The Camera and Picture tiles in the Add "
             "sheet. Your browser runs the picker — this "
             "is OG's standing permission."},
    {"key": "notifications", "name": "Notifications",
     "default": False,
     "note": "OG can send you notifications. The email "
             "and push switches themselves live in "
             "Notifications; this is the standing "
             "permission for your phone."},
]

CAP_KEYS = [c["key"] for c in CAPS]
_BY_KEY = {c["key"]: c for c in CAPS}
GPS_KEY = "gps"

# ===========================================================================
# Durable store (og_gps / og_shittalk pattern: one key -> JSON blob)
# ===========================================================================


def _db_connect():
    conn = psycopg.connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_permissions_data ("
            "key TEXT PRIMARY KEY, data JSONB)")
    conn.commit()
    return conn


def _load_file_store() -> dict:
    if os.path.exists(STORE_FILE):
        try:
            with open(STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Permissions store load failed: {e}")
    return {}


def _save_file_store(store: dict):
    try:
        with open(STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Permissions store save failed: {e}")


def _get(key: str):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_permissions_data "
                        "WHERE key=%s", (key,))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning(
                f"Permissions DB load failed: {e}")
            raise StoreError("permissions store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
    return store.get(key)


def _put(key: str, value):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_permissions_data "
                        "(key, data) VALUES (%s, %s) "
                        "ON CONFLICT (key) DO UPDATE "
                        "SET data=EXCLUDED.data",
                        (key, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(
                f"Permissions DB save failed: {e}")
            raise StoreError("permissions store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
        store[key] = value
        _save_file_store(store)


# ===========================================================================
# Reads and writes (the single source every surface reads)
# ===========================================================================


def _stored_caps(uid: str) -> dict:
    rec = _get("perm:" + uid) if uid else None
    if isinstance(rec, dict) and isinstance(rec.get("caps"), dict):
        return rec["caps"]
    return {}


def allowed(uid: str, cap: str) -> bool:
    """The effective permission for one capability. The
    gps cap reads Round 73's location switch live — it
    is never a value in this store. Unknown cap -> False.
    No uid -> the capability's default (there is no
    account to read; the caller's own gates still apply),
    except gps, whose switch reads OFF without a uid."""
    spec = _BY_KEY.get(cap)
    if spec is None:
        return False
    if cap == GPS_KEY:
        import og_gps
        return og_gps.location_on_for(uid)
    if not uid:
        return bool(spec["default"])
    stored = _stored_caps(uid)
    if cap in stored:
        return stored[cap] is True
    return bool(spec["default"])


def set_allowed(uid: str, cap: str, on) -> bool:
    """Store one capability's permission; returns the
    value now in force (what actually reads back — the
    R73 discipline). The gps cap delegates to the
    location switch itself (og_gps.set_location_on), so
    a flip here, a chat flip and the one-time ask all
    write the one switch. Unknown cap -> False, writes
    nothing. No uid -> nothing written, returns the
    effective default."""
    spec = _BY_KEY.get(cap)
    if spec is None:
        return False
    if cap == GPS_KEY:
        import og_gps
        return og_gps.set_location_on(uid, on)
    if not uid:
        return allowed(uid, cap)
    caps = dict(_stored_caps(uid))
    caps[cap] = bool(on)
    _put("perm:" + uid, {"caps": caps})
    return allowed(uid, cap)


def permissions_for(uid: str) -> list:
    """The folder's payload rows, in display order:
    {key, name, on, note} for every capability, each
    value read from the one source."""
    return [{"key": c["key"], "name": c["name"],
             "on": allowed(uid, c["key"]), "note": c["note"]}
            for c in CAPS]


def purge_uid(uid: str):
    """Account deletion helper: drop this uid's stored
    permissions (tombstone, the og_gps purge form) — a
    re-created account starts at the defaults. The
    location switch itself is purged by og_gps' own
    step in the same chain."""
    if uid:
        _put("perm:" + uid, None)


# ===========================================================================
# Routes — GET/POST /permissions (the R73/R84 contract shape)
# ===========================================================================


def _bad(detail):
    return JSONResponse({"ok": False, "detail": detail},
                        status_code=400)


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def register_permissions_routes(app):

    @app.get("/permissions")
    def permissions_get(request: Request):
        uid = request.cookies.get(UID_COOKIE) or ""
        if not uid:
            # Signed-in only (the pinned contract). In
            # production the Round 44 gate answers first
            # with this same body; this covers any mount
            # without the gate.
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        return {"ok": True, "caps": permissions_for(uid)}

    @app.post("/permissions")
    async def permissions_set(request: Request):
        uid = request.cookies.get(UID_COOKIE) or ""
        if not uid:
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        body = await _json_body(request)
        cap = body.get("cap")
        if cap not in _BY_KEY:
            return _bad("unknown cap")
        if not isinstance(body.get("on"), bool):
            return _bad("on must be true or false")
        return {"ok": True, "cap": cap,
                "on": set_allowed(uid, cap, body["on"])}
