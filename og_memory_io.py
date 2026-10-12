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

try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg not installed — DB backend unavailable
    psycopg = None


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
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
