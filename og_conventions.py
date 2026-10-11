"""OG's conventions notebook (Round 60, item 4): a durable,
per-repo record of the coding patterns OG has OBSERVED in a
repo — naming, where new code goes, error-handling style, test
style — so drafts are shaped by the repo's own habits instead of
generic ones.

This module is a sibling of og_lessons.py and mirrors its shape
on purpose: the same store pattern (the app's usage store when
bound via bind_app, else its own durable store — Postgres table
og_conventions_data when OG_MEMORY_DB_URL is set, else a JSON
file; OG_CONVENTIONS_STORE overrides the file path), the same
never-raise discipline, and the same operator-key posture on its
route. Keys are "conventions:<owner>:<repo_full>" — the notebook
belongs to the operator, one per repo.

WHO WRITES IT (all fail-safe — a notebook failure can never
break drafting, the fix loop, or a lesson):
- seed_from_map: when og_github builds a fresh repo map, the
  observable facts (test location + style, layout, naming,
  conftest presence) become seed entries;
- note_correction: when the competence loop has to revise a
  draft for a file, the failure pattern is noted so the next
  draft for that file starts smarter;
- note_failed_approach: when an executed code fix fails its
  verification (a fix_failed lesson lands in og_fixqueue), the
  failed approach is noted against the repo.

WHO READS IT: OG's drafters (og_github's chat instruction, the
fix-pipeline drafter, the revise step). Entries consulted for a
draft are CITED in the verified preview / proposal summary —
"📐 Convention applied: <text>" — the same pattern as the
Round 51 lesson citations. The owner reads the whole book at
GET /system/conventions?key=<OG_STATS_TOKEN> (the /system/lessons
posture — the handler 401s itself without the key).

Bounds: at most MAX_ENTRIES entries per repo (upserted by key,
so a refreshed observation replaces the stale one), entry text
capped at 300 chars, at most 2 conventions cited per draft.
"""

import hmac
import json
import logging
import os
import threading
import time
from typing import Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

SENTINEL_OWNER = "__operator__"
MAX_ENTRIES = 40
_TEXT_CAP = 300
_KEY_CAP = 120

STORE_FILE = os.getenv("OG_CONVENTIONS_STORE",
                       "conventions_store.json")
MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_store_lock = threading.Lock()

# Bound by app.py via bind_app when available (the app's usage
# store accessors — the og_lessons pattern). Unbound is fine:
# the module's own durable store below carries it.
_deps: Dict = {}


def bind_app(deps):
    _deps.update(deps)


# --- Owner (exactly Round 50/51's resolution) --------------------------------------


def _operator_uid() -> Optional[str]:
    try:
        import og_selfcheck as _sc
        return _sc._operator_uid()
    except Exception:
        return None


def _owner_key() -> str:
    return _operator_uid() or SENTINEL_OWNER


# --- Store backends (the og_lessons pattern) -----------------------------------------


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_conventions_data ("
            "key TEXT PRIMARY KEY, data JSONB)")
    conn.commit()
    return conn


def _own_get(key: str):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_conventions_data "
                        "WHERE key=%s", (key,))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning(
                f"Conventions DB load failed, using file: {e}")
    with _store_lock:
        store = _load_file_store()
    return store.get(key)


def _own_put(key: str, value):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_conventions_data "
                        "(key, data) VALUES (%s, %s) "
                        "ON CONFLICT (key) DO UPDATE "
                        "SET data=EXCLUDED.data",
                        (key, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(
                f"Conventions DB save failed, using file: {e}")
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
            logger.warning(f"Conventions store load failed: {e}")
    return {}


def _save_file_store(store: dict):
    try:
        with open(STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Conventions store save failed: {e}")


def _usage_bound() -> bool:
    return callable(_deps.get("load_usage")) \
        and callable(_deps.get("save_usage"))


def _store_key(owner: str, repo_full: str) -> str:
    return f"conventions:{owner}:{repo_full}"


def _entries(owner: str, repo_full: str) -> List[dict]:
    """One repo's notebook entries, oldest first. Fail-safe []."""
    key = _store_key(owner, repo_full)
    try:
        if _usage_bound():
            lock = _deps.get("usage_lock")
            if lock is not None:
                with lock:
                    store = _deps["load_usage"]()
                    items = store.get(key) \
                        if isinstance(store, dict) else None
            else:
                store = _deps["load_usage"]()
                items = store.get(key) \
                    if isinstance(store, dict) else None
        else:
            items = _own_get(key)
        if isinstance(items, list):
            return [i for i in items if isinstance(i, dict)]
    except Exception as e:
        logger.warning(f"Conventions load failed: {e}")
    return []


def _save_entries(owner: str, repo_full: str, items: List[dict]):
    key = _store_key(owner, repo_full)
    items = items[-MAX_ENTRIES:]
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


def _all_notebooks(owner: str) -> Dict[str, List[dict]]:
    """{repo_full: entries} for every notebook the owner has —
    the GET /system/conventions read. Fail-safe to {}."""
    prefix = f"conventions:{owner}:"
    out: Dict[str, List[dict]] = {}
    try:
        if _usage_bound():
            lock = _deps.get("usage_lock")
            if lock is not None:
                with lock:
                    store = _deps["load_usage"]()
            else:
                store = _deps["load_usage"]()
            if isinstance(store, dict):
                for k, v in store.items():
                    if str(k).startswith(prefix) \
                            and isinstance(v, list):
                        out[str(k)[len(prefix):]] = [
                            i for i in v if isinstance(i, dict)]
            return out
        if MEMORY_DB_URL and psycopg is not None:
            try:
                with _db_connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT key, data FROM "
                            "og_conventions_data")
                        rows = cur.fetchall()
                for k, v in rows:
                    if str(k).startswith(prefix) \
                            and isinstance(v, list):
                        out[str(k)[len(prefix):]] = [
                            i for i in v if isinstance(i, dict)]
                return out
            except Exception as e:
                logger.warning(
                    f"Conventions DB scan failed, using file: {e}")
        with _store_lock:
            store = _load_file_store()
        for k, v in store.items():
            if str(k).startswith(prefix) and isinstance(v, list):
                out[str(k)[len(prefix):]] = [
                    i for i in v if isinstance(i, dict)]
    except Exception as e:
        logger.warning(f"Conventions scan failed: {e}")
    return out


# --- Writers (NEVER raise) ------------------------------------------------------------


def record_convention(repo_full, key, text, source="observed") \
        -> Optional[str]:
    """Upsert one notebook entry for a repo. Returns the entry
    key or None. Fail-safe by construction: the notebook is an
    aid to drafting, never a dependency of it."""
    try:
        repo_full = str(repo_full or "").strip()
        key = str(key or "").strip()[:_KEY_CAP]
        text = str(text or "").strip()[:_TEXT_CAP]
        if not repo_full or not key or not text:
            return None
        owner = _owner_key()
        items = _entries(owner, repo_full)
        now = time.time()
        for entry in items:
            if entry.get("key") == key:
                entry["text"] = text
                entry["source"] = str(source or "")[:40]
                entry["ts"] = now
                _save_entries(owner, repo_full, items)
                return key
        items.append({"key": key, "text": text,
                      "source": str(source or "")[:40],
                      "ts": now})
        _save_entries(owner, repo_full, items)
        return key
    except Exception as e:
        logger.warning(f"Convention record failed: {e}")
        return None


def seed_from_map(repo_full, facts: dict) -> int:
    """Seed (or refresh) a notebook from repo-map facts. `facts`
    is the plain dict og_github's map builder derives: test
    location/style, layout, naming, conftest presence. Only
    facts actually present become entries — the notebook never
    invents a convention. Returns how many entries were
    written. NEVER raises."""
    written = 0
    try:
        if not isinstance(facts, dict) or not repo_full:
            return 0
        seeds = []
        test_dir = facts.get("test_dir")
        if test_dir:
            seeds.append((
                "test-location",
                f"Tests live in {test_dir}/ — new tests go "
                f"there, in the repo's existing test style."))
        elif facts.get("tests_beside_code"):
            seeds.append((
                "test-location",
                "Tests sit beside the code they cover (no "
                "separate tests/ directory) — keep new tests "
                "beside their module."))
        if facts.get("has_conftest"):
            seeds.append((
                "test-style",
                "The repo carries a conftest.py — tests are "
                "pytest-style; follow the fixtures already "
                "there instead of inventing new ones."))
        layout = facts.get("layout")
        if layout:
            seeds.append(("layout", str(layout)))
        if facts.get("naming"):
            seeds.append(("naming", str(facts["naming"])))
        if facts.get("dominant_prefix"):
            seeds.append((
                "module-prefix",
                f"Modules share the prefix "
                f"'{facts['dominant_prefix']}' — new modules "
                f"follow it."))
        for key, text in seeds:
            if record_convention(repo_full, key, text,
                                 source="repo_map"):
                written += 1
    except Exception as e:
        logger.warning(f"Conventions seed failed: {e}")
    return written


def note_correction(repo_full, path, failure: str) -> None:
    """A draft for `path` had to be revised during the
    competence loop: note the pattern so the next draft for
    that file starts smarter. NEVER raises."""
    try:
        if not repo_full or not path:
            return
        fail = str(failure or "checks failed")[:120]
        record_convention(
            repo_full, f"loop:{path}",
            f"Drafts for {path} have needed revision after "
            f"failing checks ({fail}) — check that pattern "
            f"before drafting this file again.",
            source="loop_correction")
    except Exception as e:
        logger.warning(f"Convention correction note failed: {e}")


def note_failed_approach(repo_full, path, approach_key) -> None:
    """An executed code fix failed its verification: note the
    failed approach against the repo so future drafts steer
    away from it. NEVER raises."""
    try:
        if not repo_full or not path:
            return
        record_convention(
            repo_full, f"failed:{path}",
            f"An approved fix for {path} "
            f"({str(approach_key or '')[:60]}) was executed "
            f"and did NOT take — diagnose differently before "
            f"drafting this file again.",
            source="fix_failed")
    except Exception as e:
        logger.warning(f"Convention failed-note failed: {e}")


# --- Readers (fail-safe) -----------------------------------------------------------------


def conventions_for(repo_full, limit: int = 5) -> List[dict]:
    """The entries a drafter should see for this repo, newest
    last (stable reading order). Any store failure reads as an
    empty notebook — drafting never blocks on it."""
    try:
        if not repo_full:
            return []
        owner = _owner_key()
        items = _entries(owner, str(repo_full))
        if owner != SENTINEL_OWNER and not items:
            items = _entries(SENTINEL_OWNER, str(repo_full))
        return items[-max(0, int(limit or 0)):] \
            if limit else items
    except Exception as e:
        logger.warning(f"Conventions read failed: {e}")
        return []


def notebook(repo_full) -> List[dict]:
    """The full notebook for one repo (owner read)."""
    return conventions_for(repo_full, limit=MAX_ENTRIES)


def all_notebooks() -> Dict[str, List[dict]]:
    """Every notebook the operator has (owner read)."""
    try:
        owner = _owner_key()
        out = _all_notebooks(owner)
        if owner != SENTINEL_OWNER:
            for repo, items in _all_notebooks(
                    SENTINEL_OWNER).items():
                out.setdefault(repo, items)
        return out
    except Exception as e:
        logger.warning(f"Conventions all failed: {e}")
        return {}


# --- Route (the /system/lessons posture: the handler 401s itself) -----------------------


def _key_ok(key: str) -> bool:
    token = os.getenv("OG_STATS_TOKEN", "").strip()
    return bool(token) and hmac.compare_digest(
        str(key or ""), token)


def register_conventions_routes(app):

    @app.get("/system/conventions")
    async def system_conventions(request: Request, key: str = "",
                                 repo: str = ""):
        if not _key_ok(key):
            return JSONResponse(
                {"detail": "Owner key required"}, status_code=401)
        if repo:
            return {"ok": True, "repo": repo,
                    "conventions": notebook(repo)}
        books = all_notebooks()
        return {"ok": True, "notebooks": books,
                "count": sum(len(v) for v in books.values())}
