"""Keyed writes for the chat memory store (Round 80).

Why this module exists: app.py's chat-history save path used
to load the ENTIRE og_visitor_memory table and re-upsert
EVERY visitor's row on every single chat message (three
fresh connections and two full-table reads per message,
growing with the user base) — a standing tax on the same
event loop / worker threads that answer /health. This
module is the keyed counterpart for the write side: one
connection, one single-row upsert, one count, and a prune
only when the visitor cap is actually exceeded.

The read side still uses app.py's whole-store load (the
app.py push ceiling leaves no room for its delegation this
round — see round-80/NOTES.md); account deletion also
keeps using the whole-store functions, untouched.

Pattern mirrors og_accounts (Round 18): each module owns
its connection helper for the shared key->blob table
family; this one owns the og_visitor_memory table app.py
created (uid TEXT PRIMARY KEY, updated TEXT, history
JSONB). Every function fails soft: False means "backend
unavailable — caller falls back to the legacy path",
exactly the posture the legacy store functions take.
"""

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, DB_OPTIONS as _DB_OPTS, apply_bounds as _apply_bounds

try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg not installed — DB backend unavailable
    psycopg = None


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=_DB_CT, options=_DB_OPTS)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_visitor_memory ("
            "uid TEXT PRIMARY KEY, updated TEXT, history JSONB)"
        )
    conn.commit()
    return conn


def save_history(uid: str, history: list,
                 max_visitors: int) -> bool:
    """Persist ONE visitor's thread (already trimmed by the
    caller; the `updated` stamp is taken here) and prune the
    stalest threads beyond the cap — the same bound the
    whole-store save enforced, enforced here with a count +
    a single bounded DELETE instead of a full-table rewrite.
    True on success; False on any failure or when no DB
    backend is configured."""
    if not (MEMORY_DB_URL and psycopg is not None):
        return False
    updated = datetime.now(timezone.utc).isoformat()
    try:
        with _db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO og_visitor_memory (uid, updated, "
                    "history) VALUES (%s, %s, %s) ON CONFLICT (uid) "
                    "DO UPDATE SET updated = EXCLUDED.updated, "
                    "history = EXCLUDED.history",
                    (uid, updated, _Jsonb(list(history))),
                )
                cur.execute("SELECT COUNT(*) FROM og_visitor_memory")
                total = int(cur.fetchone()[0])
                if total > max_visitors:
                    cur.execute(
                        "DELETE FROM og_visitor_memory WHERE uid IN ("
                        "SELECT uid FROM og_visitor_memory "
                        "ORDER BY updated ASC LIMIT %s)",
                        (total - max_visitors,))
            conn.commit()
        return True
    except Exception as e:
        logger.warning(f"Keyed memory save failed, using legacy: {e}")
        return False


def load_history_entry(uid: str):
    """One visitor's stored memory entry, read KEYED by uid:
    {"updated": ..., "history": [...]} — or {} when the
    visitor has no row. The read twin of save_history (R80
    shipped the keyed WRITE and deferred this load; Round
    135 lands it for the chat history path — the load the
    browser-claim turn rides — which used to pull the WHOLE
    table to read one visitor's thread).

    Fail-soft, this module's posture since R80: returns
    None when no DB backend is configured or the read
    fails, so the caller takes its legacy path."""
    if not (MEMORY_DB_URL and psycopg is not None):
        return None
    try:
        with _db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT updated, history FROM og_visitor_memory "
                    "WHERE uid = %s", (uid,))
                row = cur.fetchone()
        if row is None:
            return {}
        return {"updated": row[0], "history": row[1]}
    except Exception as e:
        logger.warning(f"Keyed memory load failed, using legacy: {e}")
        return None


def delete_history(uid: str) -> bool:
    """Delete ONE visitor's stored thread, keyed — the
    delete twin of save_history (Round 321, the
    /chat/leave seam: leaving the site wipes the caller's
    visible chat thread; account memory is a different
    store and is never touched here). True when the DB
    handled it — the row is gone, or was never there
    (idempotent). False on any failure or when no DB
    backend is configured, so the caller takes its
    legacy (JSON file) path, exactly this module's
    posture since Round 80."""
    if not uid or not (MEMORY_DB_URL and psycopg is not None):
        return False
    try:
        with _db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM og_visitor_memory "
                    "WHERE uid = %s", (uid,))
            conn.commit()
        return True
    except Exception as e:
        logger.warning(f"Keyed memory delete failed, "
                       f"using legacy: {e}")
        return False
