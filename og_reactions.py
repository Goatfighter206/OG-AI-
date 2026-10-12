"""Message reactions for OG (Round 62, Brent's approval
2026-10-10): tap-and-hold a message, drop an emoji on it.

WHAT THIS IS. Chat messages had no reactions at all. This
module is the durable per-user store behind them:

    GET  /reactions -> {"reactions": {message_key: [emoji, ...]}}
    POST /reactions {"message_key": str, "emoji": str}
         toggles the CALLER's reaction of that emoji on that
         message and answers the updated entry:
         {"ok": True, "message_key": k,
          "reactions": {k: [emoji, ...]}, "entry": [emoji, ...]}

message_key is an opaque client-derived key (the web client
derives it from the message's position + a hash of its text);
the server never parses it. A user only ever sees and toggles
their OWN map — records are keyed "reactions:<ogai_uid>" and a
request resolves exactly one uid: its own cookie.

STORAGE. The codebase's established durable pattern — one
key -> JSON blob — in this module's own store, exactly the
og_notify form (Postgres table og_reactions_data when
OG_MEMORY_DB_URL is set, else a JSON file: OG_REACTIONS_STORE
or reactions_store.json). (og_lessons binds the app's usage
store via bind_app instead; app.py sits at its push ceiling
with no room for a new bind line, so this module carries the
self-contained form of the same pattern — see round-62
NOTES.md.) The map is capped at MAX_KEYS messages per user;
the oldest keys fall off first.

VALIDATION. message_key must be a string of 1..128 chars;
emoji a string of 1..8 chars. Anything else is a 400 and
writes nothing.

GATE. These routes are NOT in og_accounts.PUBLIC_PATHS, so
the Round 44 sign-in gate covers them by construction: a
signed-out caller gets the gate's 401 before any handler
runs. (In a bare local process the gate is off, like every
other feature route; a request with no uid cookie simply
reads/writes nothing and gets an empty map.)
"""

import json
import logging
import os
import threading

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

UID_COOKIE = "ogai_uid"
STORE_FILE = os.getenv("OG_REACTIONS_STORE", "reactions_store.json")
MAX_KEYS = 500
KEY_CAP = 128
EMOJI_CAP = 8

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, DB_OPTIONS as _DB_OPTS, StoreError
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_store_lock = threading.RLock()


# --- Durable store (og_notify pattern: one key -> JSON blob) -------------------


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=_DB_CT, options=_DB_OPTS)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_reactions_data ("
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
            logger.warning(f"Reactions store load failed: {e}")
    return {}


def _save_file_store(store: dict):
    try:
        with open(STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Reactions store save failed: {e}")


def _get(key: str):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_reactions_data "
                        "WHERE key=%s", (key,))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning(f"Reactions DB load failed: {e}")
            raise StoreError("reactions store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
    return store.get(key)


def _put(key: str, value):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_reactions_data (key, data) "
                        "VALUES (%s, %s) ON CONFLICT (key) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (key, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Reactions DB save failed: {e}")
            raise StoreError("reactions store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
        store[key] = value
        _save_file_store(store)


def purge_uid(uid: str):
    """Account deletion helper (same posture as og_notify's):
    drop this uid's whole reaction map, nobody else's."""
    if uid:
        _put("reactions:" + uid, {})


# --- The map ---------------------------------------------------------------------


def _map_for(uid: str) -> dict:
    data = _get("reactions:" + uid)
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, list):
            emojis = [e for e in v if isinstance(e, str) and e]
            if emojis:
                out[k] = emojis
    return out


def toggle(uid: str, message_key: str, emoji: str) -> list:
    """Toggle the caller's one reaction of `emoji` on
    `message_key`; returns the message's updated emoji list.
    (One entry per user per emoji: reacting again removes it.
    A caller can still stack DIFFERENT emojis on one message.)
    """
    with _store_lock:
        rmap = _map_for(uid)
        entry = list(rmap.get(message_key) or [])
        if emoji in entry:
            entry.remove(emoji)
        else:
            entry.append(emoji)
        if entry:
            rmap[message_key] = entry
        else:
            rmap.pop(message_key, None)
        while len(rmap) > MAX_KEYS:
            rmap.pop(next(iter(rmap)), None)
        _put("reactions:" + uid, rmap)
    return entry


# --- Routes ------------------------------------------------------------------------


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


def _uid_of(request: Request) -> str:
    return request.cookies.get(UID_COOKIE) or ""


def register_reaction_routes(app):

    @app.get("/reactions")
    async def reactions_list(request: Request):
        uid = _uid_of(request)
        if not uid:
            return {"reactions": {}}
        return {"reactions": _map_for(uid)}

    @app.post("/reactions")
    async def reactions_toggle(request: Request):
        uid = _uid_of(request)
        body = await _json_body(request)
        message_key = body.get("message_key")
        emoji = body.get("emoji")
        if (not isinstance(message_key, str)
                or not 1 <= len(message_key) <= KEY_CAP
                or not isinstance(emoji, str)
                or not 1 <= len(emoji) <= EMOJI_CAP):
            return JSONResponse(
                {"ok": False, "detail": "bad reaction"},
                status_code=400)
        if not uid:
            return JSONResponse(
                {"ok": False, "detail": "no visitor id"},
                status_code=400)
        entry = toggle(uid, message_key, emoji)
        return {"ok": True, "message_key": message_key,
                "reactions": {message_key: entry}, "entry": entry}
