"""OG's lessons book (Round 51): a durable, operator-keyed record
of what OG has learned from his own mistakes — so a failure, once
paid for, is never bought twice.

WHAT GETS WRITTEN (three sources, all fail-safe — a lessons
failure can never break the fix queue, a report, or a draft):
(a) fix_failed — an APPROVED fix executed and its verification
    still showed the problem (written from og_fixqueue's outcome
    path, with the fingerprint + the approach_key of exactly
    what was tried);
(b) report — a visitor reported one of OG's replies through
    POST /report (Round 36). The lesson holds the report's
    reason plus a sha256 of the reported text and a short
    excerpt — never more user data than the report itself
    already holds in the reports store;
(c) recurrence — a problem that was marked fixed (or resolved)
    is seen again (written from og_fixqueue.record_problem's
    reopen path).

WHAT READS IT: OG's code drafters — the Round 32 verify loop
(og_github) and the fix-pipeline drafter (og_fixqueue Part 2).
Relevant lessons (same fingerprint first, then same file) are
loaded into the drafting context; a draft shaped by a lesson
CITES it ("Lesson applied: <title>"). HARD RULE: an approach
recorded as failed for a fingerprint (same fingerprint + same
approach_key, source fix_failed) may not be re-proposed for
that fingerprint — enforced in og_fixqueue._prepare.

STORAGE: the app's usage store, bound by app.py via bind_app
(load_usage / save_usage / usage_lock — the Round 56 tokenpacks
pattern), under the key "lessons:<owner>". When unbound (tests,
tooling), the module falls back to its own durable store in the
og_fixqueue pattern (Postgres table og_lessons_data when
OG_MEMORY_DB_URL is set, else lessons_store.json;
OG_LESSONS_STORE overrides the file path). Owner = the operator
uid, resolved exactly as Round 50 resolves it (OG_OWNER_EMAIL
through og_accounts); unresolved operators key to the sentinel
"__operator__" — a recipient is never guessed, and the records
adopt the same keying the fix queue uses.

VISIBILITY: GET /system/lessons?key=<OG_STATS_TOKEN> returns the
newest lessons (the /system/check posture — the handler 401s
itself without the key). No notifications are sent for lessons;
the fix_needed notification already covers proposals.
"""

import hashlib
import hmac
import json
import logging
import os
import threading
import time
import uuid
from typing import Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

SENTINEL_OWNER = "__operator__"
MAX_LESSONS = 200
_TITLE_CAP = 200
_LESSON_CAP = 800

STORE_FILE = os.getenv("OG_LESSONS_STORE", "lessons_store.json")
MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_store_lock = threading.Lock()

# Bound by app.py via bind_app (the app's usage store accessors).
_deps: Dict = {}


def bind_app(deps):
    _deps.update(deps)


# --- Owner (exactly Round 50's resolution) ---------------------------------------


def _operator_uid() -> Optional[str]:
    try:
        import og_selfcheck as _sc
        return _sc._operator_uid()
    except Exception:
        return None


def _owner_key() -> str:
    return _operator_uid() or SENTINEL_OWNER


# --- Store backends -----------------------------------------------------------------


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_lessons_data ("
            "key TEXT PRIMARY KEY, data JSONB)")
    conn.commit()
    return conn


def _own_get(key: str):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_lessons_data WHERE key=%s",
                        (key,))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning(f"Lessons DB load failed, using file: {e}")
    with _store_lock:
        store = _load_file_store()
    return store.get(key)


def _own_put(key: str, value):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_lessons_data (key, data) "
                        "VALUES (%s, %s) ON CONFLICT (key) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (key, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Lessons DB save failed, using file: {e}")
    with _store_lock:
        store = _load_file_store()
        store[key] = value
        _save_file_store(store)


def _load_file_store() -> dict:
    if os.path.exists(STORE_FILE):
        try:
            with open(STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Lessons store load failed: {e}")
    return {}


def _save_file_store(store: dict):
    try:
        with open(STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Lessons store save failed: {e}")


def _usage_bound() -> bool:
    return callable(_deps.get("load_usage")) \
        and callable(_deps.get("save_usage"))


def _lessons(owner: str) -> List[dict]:
    """The owner's lessons, oldest first. Fail-safe to []."""
    key = "lessons:" + owner
    try:
        if _usage_bound():
            lock = _deps.get("usage_lock")
            if lock is not None:
                with lock:
                    store = _deps["load_usage"]()
                    items = store.get(key) if isinstance(store, dict) else None
            else:
                store = _deps["load_usage"]()
                items = store.get(key) if isinstance(store, dict) else None
        else:
            items = _own_get(key)
        if isinstance(items, list):
            return [i for i in items if isinstance(i, dict)]
    except Exception as e:
        logger.warning(f"Lessons load failed: {e}")
    return []


def _save_lessons(owner: str, items: List[dict]):
    key = "lessons:" + owner
    items = items[-MAX_LESSONS:]
    if _usage_bound():
        lock = _deps.get("usage_lock")
        if lock is not None:
            with lock:
                store = _deps["load_usage"]()
                if isinstance(store, dict):
                    store[key] = items
                    _deps["save_usage"](store)
        else:
            store = _deps["load_usage"]()
            if isinstance(store, dict):
                store[key] = items
                _deps["save_usage"](store)
        return
    _own_put(key, items)


# --- Writers (NEVER raise) ------------------------------------------------------------


def record_lesson(source, title, lesson, fingerprint=None,
                  approach_key=None) -> Optional[str]:
    """Append one lesson for the operator. Returns the lesson id
    or None. Fail-safe by construction: this is a memory, and a
    memory must never break the thing it remembers."""
    try:
        owner = _owner_key()
        rec = {
            "id": uuid.uuid4().hex[:12],
            "ts": time.time(),
            "source": str(source or "")[:40],
            "fingerprint": (str(fingerprint)[:200]
                            if fingerprint else None),
            "title": str(title or "Lesson")[:_TITLE_CAP],
            "lesson": str(lesson or "")[:_LESSON_CAP],
            "approach_key": (str(approach_key)[:300]
                             if approach_key else None),
        }
        items = _lessons(owner)
        items.append(rec)
        _save_lessons(owner, items)
        return rec["id"]
    except Exception as e:
        logger.warning(f"Lesson record failed: {e}")
        return None


def record_from_report(rec: dict) -> Optional[str]:
    """Source (b): a reply was reported via POST /report. The
    lesson keeps the reason, a sha256 of the reported text, and
    a short excerpt — no more than the report itself holds."""
    try:
        if not isinstance(rec, dict):
            return None
        reason = str(rec.get("reason") or "").strip()
        text = str(rec.get("text") or "")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        excerpt = " ".join(text.split())[:120]
        title = "A reply was reported"
        if reason:
            title += f": {reason[:80]}"
        body = (f"A visitor reported one of OG's replies. Reason: "
                f"{reason or '(none given)'}. Reported text "
                f"(sha256 {digest}): \"{excerpt}\" — check the tone "
                f"and the facts before answering the same way "
                f"again.")
        return record_lesson("report", title, body)
    except Exception as e:
        logger.warning(f"Report lesson failed: {e}")
        return None


# --- Readers (fail-safe) -----------------------------------------------------------------


def _owner_items() -> List[dict]:
    """The operator's lessons plus any still keyed to the
    sentinel (recorded before the operator resolved)."""
    owner = _owner_key()
    items = _lessons(owner)
    if owner != SENTINEL_OWNER:
        items = _lessons(SENTINEL_OWNER) + items
    return items


def relevant_lessons(fingerprint=None, path=None,
                     limit: int = 5) -> List[dict]:
    """Lessons a drafter must see before drafting: same
    fingerprint first, then same file (approach_key prefix
    "code:<path>:"). Newest first inside each class. Any store
    failure reads as no lessons — drafting never blocks on the
    book."""
    try:
        items = _owner_items()
        fp_hits, path_hits = [], []
        prefix = f"code:{path}:" if path else None
        for rec in reversed(items):
            if fingerprint and rec.get("fingerprint") == fingerprint:
                fp_hits.append(rec)
            elif prefix and str(rec.get("approach_key") or "") \
                    .startswith(prefix):
                path_hits.append(rec)
        return (fp_hits + path_hits)[: max(0, int(limit or 0))]
    except Exception as e:
        logger.warning(f"Lessons read failed: {e}")
        return []


def approach_failed(fingerprint, approach_key) -> bool:
    """The HARD RULE's lookup: True when this exact approach was
    already executed for this fingerprint and failed its
    verification. Fail-safe to False (a broken book never blocks
    a fix — it just can't veto one)."""
    return failed_lesson_for(fingerprint, approach_key) is not None


def failed_lesson_for(fingerprint, approach_key):
    """The fix_failed lesson blocking an approach, or None."""
    try:
        if not fingerprint or not approach_key:
            return None
        for rec in _owner_items():
            if rec.get("source") == "fix_failed" \
                    and rec.get("fingerprint") == fingerprint \
                    and rec.get("approach_key") == approach_key:
                return rec
    except Exception as e:
        logger.warning(f"Lessons approach lookup failed: {e}")
    return None


def latest_lessons(limit: int = 20) -> List[dict]:
    """The owner's newest lessons, for GET /system/lessons."""
    try:
        items = _owner_items()
        return list(reversed(items))[: max(0, int(limit or 0))]
    except Exception as e:
        logger.warning(f"Lessons latest failed: {e}")
        return []


# --- Route (the /system/check posture: the handler 401s itself) --------------------------


def _key_ok(key: str) -> bool:
    token = os.getenv("OG_STATS_TOKEN", "").strip()
    return bool(token) and hmac.compare_digest(str(key or ""), token)


def register_lessons_routes(app):

    @app.get("/system/lessons")
    async def system_lessons(request: Request, key: str = ""):
        if not _key_ok(key):
            return JSONResponse({"detail": "Owner key required"},
                                status_code=401)
        items = latest_lessons(50)
        return {"ok": True, "lessons": items, "count": len(items)}

    # Round 60: the conventions notebook rides this registration
    # (app.py already calls register_lessons_routes; app.py is at
    # its push ceiling, so the sibling module registers from
    # here). Fail-safe: a conventions import problem can never
    # take the lessons route down with it.
    try:
        import og_conventions as _conv
        _conv.register_conventions_routes(app)
    except Exception as e:
        logger.warning(f"Conventions routes skipped: {e}")
