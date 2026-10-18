"""Voice folder (Round 98, Brent's orders 2026-10-12):
"we should have a couple different voice styles to
use" + "it should go in a folder, called voice that
you can click on to see all the voices to choose
from."

ONE per-account voice-style store — the single source
both faces read for how OG SOUNDS:

    GET  /voice?avatar=og|kane|sable
        -> {"ok": true, "voice": <effective voice id>,
            "picked": <stored pick or null>,
            "avatar": <avatar>, "default_voice": <id>,
            "styles": [{"voice", "name", "note",
                        "current", "is_default"}, ...]}
    POST /voice {"voice": <id>, "avatar": <optional>}
        -> the same payload, after the pick is stored
    no uid -> 401 sign_in_required; an unknown voice
    id -> 400, writes nothing (the R73/R84 contract
    shape; routes ride og_library's registration — NOT
    in PUBLIC_PATHS, so the Round 44 gate covers them).

WHAT A "STYLE" HONESTLY IS. The audit (round-98/
NOTES.md): POST /tts calls OpenAI's speech endpoint
with model "tts-1" — a voice-selection model. It
takes a voice id and the text; it has NO delivery-
instructions channel (that is the newer
gpt-4o-mini-tts model's feature, and /tts does not
call it). So a style here is exactly a VOICE: the six
Round 37 voices, each of which measurably changes
the /tts request (the suite asserts the request body
carries the pick). There are no fake styles and no
delivery styles this round — saying otherwise would
need an app.py model change, and app.py is frozen.

DEFAULTS. No record = the AVATAR's own voice — the
Round 37 map, kept as this module's one table and
pinned by the suite against app.py's TTS_VOICES /
TTS_DEFAULT_VOICE and the Unity client's VoiceId
table so the faces cannot drift: OG = onyx,
KANE = echo, SABLE = nova. The pick is per-ACCOUNT
(it follows the user across devices); the default
is per-avatar (each face asks with the avatar on
its own stage: the web's stage is OG, the app sends
its selected figure). Picking a style changes the
SOUND only — never the words (persona law).

WHO READS THE STORE: the two speaking clients. The
web page speaks through speakReply -> fetchTTSPromise
-> POST /tts; static/og_r98.js carries this store's
effective voice into that request (the page's own
default path is untouched when no pick exists).
The app speaks through OGApiClient.Speak; fix82
caches this store's effective voice the fix75 way
and Speak sends it. Generator narration does NOT
read this store: the podcast is two-voice by design
(its own resolve_voices) and story videos pin onyx
in og_video — both named out of scope in the spec.

STORAGE. The codebase's established durable pattern
— one key -> JSON blob — in this module's own store,
exactly the og_permissions form (Postgres table
og_voice_data when OG_MEMORY_DB_URL is set, else a
JSON file: OG_VOICE_STORE or voice_store.json).
Records are keyed "voice:"+uid -> {"voice": <id>}
and hold ONLY an explicit pick; no record already
reads exactly right for every existing account.
Round 99's law: with a database configured, a store
error raises og_db.StoreError and the caller fails
honestly (the app-wide 503) — never a shadow answer.
"""

import asyncio
import json
import logging
import os
import threading

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

UID_COOKIE = "ogai_uid"
STORE_FILE = os.getenv("OG_VOICE_STORE", "voice_store.json")

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, StoreError, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo, pooled_connect as _pooled_connect
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_store_lock = threading.RLock()

# ===========================================================================
# The style table (display order: OG's own voice first, then the
# other two stage voices, then the rest of the Round 37 set)
# ===========================================================================

STYLES = [
    {"voice": "onyx", "name": "Onyx",
     "note": "OG's own voice — deep and steady, the one "
             "he ships with."},
    {"voice": "echo", "name": "Echo",
     "note": "Kane's voice — a broader, heavier man's "
             "voice."},
    {"voice": "nova", "name": "Nova",
     "note": "Sable's voice — a clear woman's voice."},
    {"voice": "fable", "name": "Fable",
     "note": "A lively storyteller's voice with a "
             "British edge."},
    {"voice": "alloy", "name": "Alloy",
     "note": "Even and balanced — sits right in the "
             "middle."},
    {"voice": "shimmer", "name": "Shimmer",
     "note": "A softer, clearer woman's voice."},
]

VOICE_IDS = [s["voice"] for s in STYLES]
_BY_ID = {s["voice"]: s for s in STYLES}

# The Round 37 avatar -> voice map: the DEFAULT each
# face falls back to with no pick stored. One table,
# suite-pinned against app.py + the Unity client.
AVATAR_VOICES = {"og": "onyx", "kane": "echo", "sable": "nova"}
DEFAULT_AVATAR = "og"


def _avatar(avatar) -> str:
    """Normalize a client-supplied avatar id; anything
    unknown is OG (a typo'd avatar must never block a
    pick — the pick is what gets stored)."""
    a = str(avatar or "").strip().lower()
    return a if a in AVATAR_VOICES else DEFAULT_AVATAR


# ===========================================================================
# Durable store (og_permissions pattern: one key -> JSON blob)
# ===========================================================================


def _db_connect():
    conn = _pooled_connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_voice_data ("
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
            logger.warning(f"Voice store load failed: {e}")
    return {}


def _save_file_store(store: dict):
    try:
        with open(STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Voice store save failed: {e}")


def _get(key: str):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_voice_data "
                        "WHERE key=%s", (key,))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning(f"Voice DB load failed: {e}")
            raise StoreError("voice store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
    return store.get(key)


def _put(key: str, value):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_voice_data (key, data) "
                        "VALUES (%s, %s) ON CONFLICT (key) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (key, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Voice DB save failed: {e}")
            raise StoreError("voice store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
        store[key] = value
        _save_file_store(store)


# ===========================================================================
# Reads and writes (the single source every speaking face reads)
# ===========================================================================


def stored_voice(uid: str):
    """The account's explicit pick, or None. A stored
    value that is not a known voice id reads as no
    pick (the store only ever writes valid ids; this
    guards a hand-edited or legacy record). No uid ->
    None (there is no account to read)."""
    if not uid:
        return None
    rec = _get("voice:" + uid)
    if isinstance(rec, dict):
        v = rec.get("voice")
        if v in _BY_ID:
            return v
    return None


def voice_for(uid: str, avatar=None) -> str:
    """The effective voice for one speaking face: the
    account's pick when one is stored, else the
    avatar's own Round 37 voice."""
    pick = stored_voice(uid)
    if pick:
        return pick
    return AVATAR_VOICES[_avatar(avatar)]


def set_voice(uid: str, voice) -> str:
    """Store the account's pick; returns the pick now
    in force (what actually reads back — the R73
    discipline). Unknown id -> writes nothing, returns
    the current effective voice for OG. No uid ->
    nothing written."""
    if voice not in _BY_ID:
        return voice_for(uid)
    if not uid:
        return voice
    _put("voice:" + uid, {"voice": voice})
    return stored_voice(uid) or voice


# ===========================================================================
# Round 107 (B1): the reads moved OFF the event loop.
#
# /voice used to run its sync store read (psycopg through
# og_db's bounds — up to ~15 s on a slow database) INLINE
# on the loop, and the page polls it about every 6 s: any
# database slowdown froze the whole worker for every
# visitor (the diagnosis's #1 stall cause). The handlers
# below now reach the store only through asyncio.to_thread.
# No payload cache: Round 99's law (pinned by the r98
# suite) says a store error answers the honest 503, never
# a shadow answer — a cache would serve exactly that
# during an outage. The thread hop is the cure; the polls
# cost one bounded store read each, like every sibling
# settings route.
# ===========================================================================


def _set_voice_payload(uid: str, voice, avatar) -> dict:
    """POST's one thread hop: store the pick, then build
    the payload from what actually reads back."""
    set_voice(uid, voice)
    return voice_payload(uid, avatar)


def voice_payload(uid: str, avatar=None) -> dict:
    """The folder's payload: the effective voice, the
    asking face's avatar + its default, and every
    style row with its current/default marks — all
    read from the one store."""
    av = _avatar(avatar)
    default = AVATAR_VOICES[av]
    pick = stored_voice(uid)
    effective = pick or default
    return {
        "ok": True,
        "voice": effective,
        "picked": pick,
        "avatar": av,
        "default_voice": default,
        "styles": [
            {"voice": s["voice"], "name": s["name"],
             "note": s["note"],
             "current": s["voice"] == effective,
             "is_default": s["voice"] == default}
            for s in STYLES
        ],
    }


def purge_uid(uid: str):
    """Account deletion helper: drop this uid's pick
    (tombstone, the og_gps purge form) — a re-created
    account starts at its avatar's default voice."""
    if uid:
        _put("voice:" + uid, None)


# ===========================================================================
# Routes — GET/POST /voice (the R73/R84 contract shape)
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


def register_voice_routes(app):

    @app.get("/voice")
    async def voice_get(request: Request):
        uid = request.cookies.get(UID_COOKIE) or ""
        if not uid:
            # Signed-in only (the pinned contract). In
            # production the Round 44 gate answers first
            # with this same body; this covers any mount
            # without the gate.
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        # Round 107 (B1): the sync store read runs OFF
        # the event loop (see the note above voice_payload).
        return await asyncio.to_thread(
            voice_payload, uid, request.query_params.get("avatar"))

    @app.post("/voice")
    async def voice_set(request: Request):
        uid = request.cookies.get(UID_COOKIE) or ""
        if not uid:
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        body = await _json_body(request)
        voice = body.get("voice")
        if voice not in _BY_ID:
            return _bad("unknown voice")
        # Round 107 (B1): the write + rebuild run OFF the
        # loop in one hop; the answer is built from what
        # actually reads back (the R73 discipline).
        return await asyncio.to_thread(
            _set_voice_payload, uid, voice, body.get("avatar"))
