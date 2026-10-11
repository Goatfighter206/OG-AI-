"""OG coding machinery, second half (Round 60 split).

og_github.py hit the same push-size ceiling as app.py, so the
self-contained machinery Round 60 added — plus the Round 32
GitHub Actions checks subsystem, which is equally separable —
lives here:

- the repo map (per repo+ref cache, ast symbols, references);
- the full test battery (pytest + r*-style suite scripts);
- the multi-file machinery (draft/extract/verify/execute for
  changes of up to _MAX_CHANGE_FILES files) and the multi-file
  system drafter;
- the checks subsystem (watch store, poller, answers);
- post-merge live verification (merge watch + the one verdict
  pass).

Everything here reaches og_github's internals (the contents
API, the sandbox, the pending store) through ONE lazy seam,
_gh() — og_github binds itself via bind_app at import, and the
fallback import keeps this module usable standalone. Nothing
here imports og_github at module level, so og_github can
re-export these names at its own bottom without a cycle.
Discipline is unchanged: preparation never mutates, approvals
are the caller's, every failure degrades to the pre-Round-60
behavior.
"""

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from typing import Dict, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

# Durable backend (the og_lessons pattern): Postgres when
# OG_MEMORY_DB_URL is set, else JSON files beside the stores.
MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg not installed — file backends
    psycopg = None
    _Jsonb = None

# Round 60 bounds owned by this module's subsystems.
_MAP_TTL = 6 * 60 * 60  # repo-map cache lifetime, seconds
_MAP_MAX_FILES = 40  # .py files symbol-mapped per repo build
_MAP_MAX_REPOS = 8  # repo maps kept in the cache store
_MAP_STORE_FILE = "github_maps.json"
_BATTERY_SUITE_CAP = 120  # per-suite battery cap, seconds
_BATTERY_TOTAL_CAP = 600  # whole-battery hard cap, seconds
_CHECKS_STORE_FILE = "github_checks.json"
_CHECKS_KEEP = 5  # tracked PRs remembered per visitor

# The one seam back to og_github (bound by og_github itself).
_G = None


def bind_app(deps):
    global _G
    if isinstance(deps, dict) and deps.get("gh") is not None:
        _G = deps["gh"]


def _gh():
    global _G
    if _G is None:
        import og_github
        _G = og_github
    return _G


# --- GitHub Actions check tracking (Brent's rule: test it with GitHub) ---------
# After a PR opens, OG watches its check runs: a bounded background
# poll (~20 min) plus a lazy refresh when the visitor asks. The
# terminal outcome is recorded through og_notify exactly once per
# PR. Watches are per visitor and durable (Postgres when configured,
# else a JSON file next to the token store).

_checks_lock = threading.Lock()
_CHECK_TERMINAL = ("success", "failure", "none", "stopped")


def _checks_db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_github_checks ("
            "uid TEXT PRIMARY KEY, data JSONB)")
    conn.commit()
    return conn


def _load_checks_store() -> Dict:
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _checks_db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT uid, data FROM og_github_checks")
                    return {u: d for u, d in cur.fetchall()}
        except Exception as e:
            logger.warning(f"GitHub checks DB load failed, "
                           f"using file: {e}")
    if os.path.exists(_CHECKS_STORE_FILE):
        try:
            with open(_CHECKS_STORE_FILE, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Could not load checks store: {e}")
    return {}


def _save_checks_store(store: Dict):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _checks_db_connect() as conn:
                with conn.cursor() as cur:
                    for uid, data in store.items():
                        cur.execute(
                            "INSERT INTO og_github_checks (uid, data) "
                            "VALUES (%s, %s) ON CONFLICT (uid) DO "
                            "UPDATE SET data = EXCLUDED.data",
                            (uid, _Jsonb(data)))
                    cur.execute("SELECT uid FROM og_github_checks")
                    existing = {row[0] for row in cur.fetchall()}
                    for stale in existing - set(store.keys()):
                        cur.execute(
                            "DELETE FROM og_github_checks WHERE "
                            "uid = %s", (stale,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"GitHub checks DB save failed, "
                           f"using file: {e}")
    try:
        with open(_CHECKS_STORE_FILE, 'w') as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Could not save checks store: {e}")


def _get_watches(uid: str) -> list:
    if not uid:
        return []
    with _checks_lock:
        store = _load_checks_store()
    watches = store.get(uid)
    return list(watches) if isinstance(watches, list) else []


def _register_checks_watch(uid: str, watch: Dict):
    with _checks_lock:
        store = _load_checks_store()
        watches = store.get(uid)
        if not isinstance(watches, list):
            watches = []
        watches.append(watch)
        store[uid] = watches[-_CHECKS_KEEP:]
        _save_checks_store(store)


def _update_watch(uid: str, pr_url: str, **fields):
    with _checks_lock:
        store = _load_checks_store()
        watches = store.get(uid)
        if not isinstance(watches, list):
            return
        for watch in watches:
            if isinstance(watch, dict) \
                    and watch.get("pr_url") == pr_url:
                watch.update(fields)
                watch["updated"] = time.time()
        _save_checks_store(store)


def _get_watch(uid: str, pr_url: str) -> Optional[Dict]:
    for watch in _get_watches(uid):
        if isinstance(watch, dict) \
                and watch.get("pr_url") == pr_url:
            return watch
    return None


def _aggregate_check_runs(data) -> tuple:
    """(state, lines) from a check-runs API payload. state is
    'none' (the repo runs no Actions checks), 'pending',
    'success' or 'failure'; lines are (name, status, conclusion)
    per run."""
    if not isinstance(data, dict):
        return "pending", []
    runs = [r for r in (data.get("check_runs") or [])
            if isinstance(r, dict)]
    total = data.get("total_count", len(runs))
    if not total or not runs:
        return "none", []
    lines = [(r.get("name") or "check", r.get("status") or "",
              r.get("conclusion") or "") for r in runs]
    state = "success"
    for _name, st, concl in lines:
        if st != "completed":
            state = "pending"
        elif concl in ("failure", "cancelled", "timed_out",
                       "action_required", "startup_failure"):
            if state != "pending":
                state = "failure"
    return state, lines


def _notify_checks(uid: str, watch: Dict, state: str):
    """Record a PR's terminal checks outcome exactly once, via
    the notification seam (kind 'notice' — og_notify's tidy
    general kind). Fail-safe: a notification can never break
    tracking."""
    verdict = {
        "success": ("✅ GitHub checks passed",
                    "All GitHub Actions checks passed."),
        "failure": ("❌ GitHub checks FAILED",
                    "At least one GitHub Actions check failed."),
        "none": ("🐙 No GitHub Actions checks",
                 "That repo has no GitHub Actions checks, so "
                 "there was nothing to run."),
    }.get(state)
    if verdict is None:
        return
    title, line = verdict
    try:
        import og_notify
        og_notify.record(
            uid, "notice",
            f"{title} — {watch.get('repo_full')}"
            f"#{watch.get('pr_number')}",
            f"{line} PR: {watch.get('pr_url')}")
    except Exception as e:
        logger.warning(f"Checks notify failed: {e}")


def _refresh_watch(entry: Dict, uid: str, watch: Dict) -> Optional[str]:
    """One live check-runs read for a tracked PR; updates the
    stored state and notifies (once) at a terminal state. Returns
    the state, or None when the read itself failed."""
    token = entry.get("access_token", "")
    status, data = _gh()._gh_request(
        "GET", f"/repos/{watch['owner']}/{watch['repo']}/commits/"
        + quote(str(watch.get("head_sha", "")), safe="")
        + "/check-runs", token)
    if status == 401:
        _update_watch(uid, watch["pr_url"], status="stopped")
        return "stopped"
    if status != 200:
        return None
    state, lines = _aggregate_check_runs(data)
    fields = {"status": state,
              "checks": [f"{n}: {c or s}" for n, s, c in lines]}
    fresh = _get_watch(uid, watch["pr_url"]) or watch
    if state in _CHECK_TERMINAL and not fresh.get("notified"):
        fields["notified"] = True
        _update_watch(uid, watch["pr_url"], **fields)
        _notify_checks(uid, fresh, state)
    else:
        _update_watch(uid, watch["pr_url"], **fields)
    return state

# --- The repo map (Round 60, item 1) --------------------------------------------
# Before drafting, OG consults a searchable map of the target repo:
# the file tree plus, per Python file, its symbols (defs/classes via
# ast), its imports, and the identifiers it uses — enough to answer
# "what does the target define, who references it, what sits beside
# it" without fetching the whole repo into every draft. The map is
# cached per repo+ref in a store mirroring the checks store, with a
# _MAP_TTL lifetime; building one is bounded (_MAP_MAX_FILES files,
# each already size-capped by the contents API read) and EVERY
# failure degrades to no map — drafting then behaves exactly as it
# did before Round 60.

_map_lock = threading.Lock()


def _maps_db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_github_maps ("
            "key TEXT PRIMARY KEY, data JSONB)")
    conn.commit()
    return conn


def _load_map_store() -> Dict:
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _maps_db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT key, data FROM og_github_maps")
                    return {k: d for k, d in cur.fetchall()}
        except Exception as e:
            logger.warning(f"Repo-map DB load failed, "
                           f"using file: {e}")
    if os.path.exists(_MAP_STORE_FILE):
        try:
            with open(_MAP_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Could not load repo-map store: {e}")
    return {}


def _save_map_store(store: Dict):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _maps_db_connect() as conn:
                with conn.cursor() as cur:
                    for key, data in store.items():
                        cur.execute(
                            "INSERT INTO og_github_maps (key, data) "
                            "VALUES (%s, %s) ON CONFLICT (key) DO "
                            "UPDATE SET data = EXCLUDED.data",
                            (key, _Jsonb(data)))
                    cur.execute("SELECT key FROM og_github_maps")
                    existing = {row[0] for row in cur.fetchall()}
                    for stale in existing - set(store.keys()):
                        cur.execute(
                            "DELETE FROM og_github_maps WHERE "
                            "key = %s", (stale,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Repo-map DB save failed, "
                           f"using file: {e}")
    try:
        with open(_MAP_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Could not save repo-map store: {e}")


def _symbols_of(text: str) -> Dict:
    """One Python file's map entry: top-level + method symbols,
    imported module stems, and the identifiers the file uses.
    Unparseable files map to an empty entry — the map is an aid,
    never a gate. Nothing here raises."""
    entry = {"symbols": [], "imports": [], "uses": []}
    try:
        import ast
        tree = ast.parse(text)
    except Exception:
        return entry
    imports = set()
    uses = set()
    symbols = []

    def _record(node, prefix=""):
        for child in getattr(node, "body", []) or []:
            if isinstance(child, (ast.FunctionDef,
                                  ast.AsyncFunctionDef)):
                symbols.append({
                    "name": prefix + child.name,
                    "kind": "method" if prefix else "function",
                    "line": getattr(child, "lineno", 0)})
            elif isinstance(child, ast.ClassDef):
                symbols.append({
                    "name": prefix + child.name,
                    "kind": "class",
                    "line": getattr(child, "lineno", 0)})
                _record(child, prefix + child.name + ".")
    _record(tree)
    try:
        import ast
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add((alias.name or "").split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split(".")[0])
                for alias in node.names:
                    uses.add(alias.name)
            elif isinstance(node, ast.Name):
                uses.add(node.id)
            elif isinstance(node, ast.Attribute):
                uses.add(node.attr)
    except Exception:
        pass
    entry["symbols"] = symbols[:80]
    entry["imports"] = sorted(i for i in imports if i)[:40]
    entry["uses"] = sorted(u for u in uses if u)[:200]
    return entry


def build_repo_map(token: str, owner: str, repo: str, ref: str,
                   tree_paths) -> Optional[Dict]:
    """Build a fresh repo map from the contents API. Bounded:
    at most _MAP_MAX_FILES Python files are fetched and parsed;
    every other path is recorded by name only. None on any
    failure."""
    try:
        paths = [p for p in (tree_paths or [])
                 if isinstance(p, str)]
        if not paths:
            return None
        files: Dict = {}
        py_paths = sorted(p for p in paths
                          if p.endswith(".py"))[:_MAP_MAX_FILES]
        for p in py_paths:
            st, info = _gh()._fetch_file(token, owner, repo, p, ref)
            if info is None:
                continue
            files[p] = _symbols_of(info["text"])
        return {"repo_full": f"{owner}/{repo}", "ref": ref,
                "built": time.time(), "paths": paths,
                "files": files}
    except Exception as e:
        logger.warning(f"Repo map build failed: {e}")
        return None


def get_repo_map(token: str, owner: str, repo: str, ref: str,
                 tree_paths) -> Optional[Dict]:
    """The cached map for repo+ref, rebuilt when stale. A fresh
    build also seeds the conventions notebook (item 4) from the
    map's observable facts. Fail-safe: None means 'no map' and
    every caller falls back to the pre-Round-60 behavior."""
    try:
        key = f"{owner}/{repo}@{ref}"
        with _map_lock:
            store = _load_map_store()
        cached = store.get(key)
        if isinstance(cached, dict) \
                and time.time() - float(cached.get("built", 0)) \
                < _MAP_TTL:
            return cached
        built = build_repo_map(token, owner, repo, ref,
                               tree_paths)
        if built is None:
            return cached if isinstance(cached, dict) else None
        with _map_lock:
            store = _load_map_store()
            store[key] = built
            if len(store) > _MAP_MAX_REPOS:
                oldest = sorted(
                    store, key=lambda k: float(
                        (store.get(k) or {}).get("built", 0)))
                for stale in oldest[:len(store) - _MAP_MAX_REPOS]:
                    store.pop(stale, None)
            _save_map_store(store)
        try:
            import og_conventions
            og_conventions.seed_from_map(
                built["repo_full"], _map_seed_facts(built))
        except Exception as e:
            logger.warning(f"Conventions seed failed: {e}")
        return built
    except Exception as e:
        logger.warning(f"Repo map consult failed: {e}")
        return None


def _map_seed_facts(repo_map: Dict) -> Dict:
    """The notebook-seed facts a map supports — only what the
    tree actually shows (test location/style, layout, naming)."""
    paths = repo_map.get("paths") or []
    facts: Dict = {}
    test_paths = [p for p in paths if _gh()._is_test_path(p)]
    test_dir = ""
    for p in test_paths:
        parts = p.split("/")
        if len(parts) > 1 and parts[0] in ("tests", "test"):
            test_dir = parts[0]
            break
    if test_dir:
        facts["test_dir"] = test_dir
    elif test_paths:
        facts["tests_beside_code"] = True
    if any(p == "conftest.py" or p.endswith("/conftest.py")
           for p in paths):
        facts["has_conftest"] = True
    if any(p.startswith("src/") for p in paths):
        facts["layout"] = ("Code lives under src/ — new modules "
                           "go there, beside the existing ones.")
    elif any("/" not in p and p.endswith(".py") for p in paths):
        facts["layout"] = ("Flat layout: Python modules live at "
                           "the repo root.")
    py_names = [p.rsplit("/", 1)[-1] for p in paths
                if p.endswith(".py")]
    if py_names:
        snake = sum(1 for n in py_names
                    if n == n.lower() and " " not in n)
        if snake >= 0.8 * len(py_names):
            facts["naming"] = ("Files and functions use "
                               "snake_case naming.")
        prefixes: Dict = {}
        for n in py_names:
            if "_" in n:
                pre = n.split("_", 1)[0]
                prefixes[pre] = prefixes.get(pre, 0) + 1
        for pre, count in prefixes.items():
            if count >= 3 and count >= len(py_names) // 2:
                facts["dominant_prefix"] = pre
                break
    return facts


def map_facts_block(repo_map, target_path: str) -> str:
    """The 'map facts' a drafter sees for one target file: its
    own definitions, the files that reference it (imports of its
    module or uses of its names), and its siblings with their
    headline symbols. Empty when the map has nothing for the
    target. Bounded so it can ride a drafting context."""
    try:
        if not isinstance(repo_map, dict) or not target_path:
            return ""
        files = repo_map.get("files") or {}
        entry = files.get(target_path)
        lines = []
        defined = set()
        if entry:
            syms = entry.get("symbols") or []
            defined = {str(s.get("name", "")).split(".")[-1]
                       for s in syms}
            if syms:
                shown = ", ".join(
                    f"{s.get('name')} ({s.get('kind')}, "
                    f"line {s.get('line')})" for s in syms[:12])
                lines.append(
                    f"Map facts for {target_path} — it defines: "
                    f"{shown}.")
        stem = target_path.rsplit("/", 1)[-1]
        if stem.endswith(".py"):
            stem = stem[:-3]
        callers = []
        for p, e in files.items():
            if p == target_path or not isinstance(e, dict):
                continue
            if stem in (e.get("imports") or []) \
                    or defined & set(e.get("uses") or []):
                callers.append(p)
        if callers:
            lines.append("Referenced by: "
                         + ", ".join(sorted(callers)[:8]) + ".")
        folder = target_path.rsplit("/", 1)[0] \
            if "/" in target_path else ""
        siblings = []
        for p in sorted(files):
            if p == target_path:
                continue
            pfolder = p.rsplit("/", 1)[0] if "/" in p else ""
            if pfolder != folder:
                continue
            syms = (files.get(p) or {}).get("symbols") or []
            head = ", ".join(str(s.get("name"))
                             for s in syms[:3])
            siblings.append(f"{p} ({head})" if head else p)
            if len(siblings) >= 6:
                break
        if siblings:
            lines.append("Siblings in the same folder: "
                         + "; ".join(siblings) + ".")
        return "\n".join(lines)
    except Exception as e:
        logger.warning(f"Map facts failed: {e}")
        return ""


_local_map_cache: Dict = {"ts": 0.0, "map": None}


def build_local_repo_map() -> Optional[Dict]:
    """The repo map for OG's OWN codebase (the local source
    dir), built from local reads — the fix-pipeline drafter's
    map. In-process cache, 5-minute TTL. Fail-safe None."""
    try:
        now = time.time()
        if _local_map_cache.get("map") is not None \
                and now - float(_local_map_cache.get("ts", 0)) \
                < 300:
            return _local_map_cache["map"]
        paths = _gh()._local_paths()
        files: Dict = {}
        for p in paths:
            if not p.endswith(".py"):
                continue
            text = _gh()._local_read(p)
            if text is None:
                continue
            files[p] = _symbols_of(text)
        repo_map = {"repo_full": _self_repo_label(),
                    "ref": "local", "built": now,
                    "paths": paths, "files": files}
        _local_map_cache["ts"] = now
        _local_map_cache["map"] = repo_map
        return repo_map
    except Exception as e:
        logger.warning(f"Local repo map failed: {e}")
        return None


def _self_repo_label() -> str:
    return os.getenv("OG_SELF_REPO", "").strip() or "og-self"

# --- The full test battery (Round 60, item 3) -------------------------------------
# A wider net than _gh().run_python_checks' single pytest run: pytest
# over the assembled tree PLUS every r*-style suite script the
# repo carries (r<digits>tests/test_*.py, run as a script from
# the tree root — the house suite format prints ^PASS/^FAIL
# lines). Each suite runs under _BATTERY_SUITE_CAP; the whole
# battery under _BATTERY_TOTAL_CAP (a suite that would start
# past the budget is reported skipped, never silently dropped).
# Per-suite results are reported exactly as they ran — a timeout
# is a timeout, never a pass. Preparation only: the same
# throwaway dir, scrubbed env and process-group kill as the
# sandbox above, and nothing here ever writes outside it.

_R_SUITE_RE = re.compile(r"^r\d+tests/test_[^/]+\.py$")


def _repo_has_r_suites(tree_paths) -> bool:
    return any(_R_SUITE_RE.match(str(p))
               for p in (tree_paths or []))


def _suite_result(name: str, status: str, passed: int = 0,
                  failed: int = 0, failing=None, reason: str = "",
                  seconds: float = 0.0) -> Dict:
    return {"name": name, "status": status, "passed": passed,
            "failed": failed, "failing": (failing or [])[:10],
            "reason": reason, "seconds": round(seconds, 1)}


def run_test_battery(files: Dict, changed_paths=(),
                     per_suite: int = _BATTERY_SUITE_CAP,
                     total_cap: int = _BATTERY_TOTAL_CAP) -> Dict:
    """Run the repo's whole test surface over the assembled
    tree. Returns {"suites": [per-suite results], "seconds"}.
    Nothing here ever raises."""
    suites: list = []
    out = {"suites": suites, "seconds": 0.0}
    tmp = tempfile.mkdtemp(prefix="og_battery_")
    started = time.time()
    try:
        for path, blob in files.items():
            if not _gh()._safe_rel(path):
                continue
            if isinstance(blob, str):
                blob = blob.encode("utf-8", "replace")
            dest = os.path.join(tmp, path)
            parent = os.path.dirname(dest)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(dest, "wb") as f:
                f.write(blob)
        env = _gh()._sandbox_env(tmp)
        plan = []
        if any(_gh()._is_test_path(p) for p in files):
            plan.append(("pytest", None))
        for p in sorted(files):
            if _R_SUITE_RE.match(p):
                plan.append((p, p))
        for name, script in plan:
            elapsed = time.time() - started
            remaining = total_cap - elapsed
            if remaining <= 0:
                suites.append(_suite_result(
                    name, "skipped",
                    reason="battery budget exhausted",
                    seconds=0.0))
                continue
            cap = max(1, int(min(per_suite, remaining)))
            t0 = time.time()
            if script is None:
                if not _gh()._pytest_available():
                    suites.append(_suite_result(
                        name, "unavailable",
                        reason="pytest is not installed on "
                               "OG's server",
                        seconds=time.time() - t0))
                    continue
                argv = [sys.executable, "-m", "pytest", "-q",
                        "-rf", "--tb=line",
                        "-p", "no:cacheprovider"]
            else:
                argv = [sys.executable, script]
            rc, stdout, stderr, timed = _gh()._run_capped(
                argv, tmp, env, cap)
            blob = ((stdout or "") + "\n" + (stderr or "")).strip()
            secs = time.time() - t0
            if timed:
                suites.append(_suite_result(
                    name, "timeout",
                    reason=f"timed out after {cap}s",
                    seconds=secs))
                continue
            if rc is None:
                suites.append(_suite_result(
                    name, "error",
                    reason=(stderr or "could not start")[:200],
                    seconds=secs))
                continue
            if script is None:
                mp = re.search(r"(\d+) passed", blob)
                mf = re.search(r"(\d+) failed", blob)
                passed = int(mp.group(1)) if mp else 0
                failed = int(mf.group(1)) if mf else 0
                failing = re.findall(r"^FAILED (\S+)", blob,
                                     re.M)[:10]
                if rc == 0:
                    status = "passed"
                elif rc == 1:
                    status = "failed"
                elif rc == 5:
                    status = "none"
                else:
                    status = "error"
                    failing = failing or re.findall(
                        r"^ERROR (\S+)", blob, re.M)[:10]
                suites.append(_suite_result(
                    name, status, passed, failed, failing,
                    seconds=secs))
            else:
                npass = len(re.findall(r"^PASS ", blob, re.M))
                nfail = len(re.findall(r"^FAIL ", blob, re.M))
                failing = [ln[5:].strip()[:120] for ln
                           in blob.splitlines()
                           if ln.startswith("FAIL ")][:10]
                if npass or nfail:
                    suites.append(_suite_result(
                        name, "failed" if nfail else "passed",
                        npass, nfail, failing, seconds=secs))
                elif rc == 0:
                    suites.append(_suite_result(
                        name, "passed", seconds=secs))
                else:
                    first = next((ln for ln in blob.splitlines()
                                  if ln.strip()), "")
                    suites.append(_suite_result(
                        name, "error", reason=first[:200],
                        seconds=secs))
        out["seconds"] = round(time.time() - started, 1)
        return out
    except Exception as e:
        logger.warning(f"Test battery failed: {e}")
        out["seconds"] = round(time.time() - started, 1)
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def battery_green(battery) -> bool:
    """A battery is green when it ran at least one suite and no
    suite failed, errored or timed out — the same honesty rule
    as _gh().checks_green: a timeout is never a pass."""
    if not isinstance(battery, dict):
        return False
    suites = battery.get("suites") or []
    if not suites:
        return False
    return all(s.get("status") in ("passed", "none")
               for s in suites)


def _battery_lines(battery) -> list:
    """The verified preview's per-suite battery lines."""
    lines = []
    for s in (battery or {}).get("suites") or []:
        name = s.get("name", "suite")
        status = s.get("status", "")
        if status == "passed":
            detail = (f"{s.get('passed', 0)} passed"
                      if s.get("passed") else "passed")
            lines.append(f"🔋 Battery · {name}: ✅ {detail}.")
        elif status == "failed":
            names = ", ".join((s.get("failing") or [])[:5])
            lines.append(
                f"🔋 Battery · {name}: ❌ {s.get('passed', 0)} "
                f"passed, {s.get('failed', 0)} failed"
                + (f" ({names})" if names else "") + ".")
        elif status == "timeout":
            lines.append(
                f"🔋 Battery · {name}: ⏱️ TIMED OUT "
                f"({s.get('reason', '')}) — reported as a "
                f"timeout, not a pass.")
        elif status == "none":
            lines.append(f"🔋 Battery · {name}: no tests "
                         f"collected.")
        elif status == "skipped":
            lines.append(
                f"🔋 Battery · {name}: not run "
                f"({s.get('reason', '')}).")
        else:
            lines.append(
                f"🔋 Battery · {name}: ⚠️ {status}"
                + (f" ({s.get('reason', '')})"
                   if s.get("reason") else "") + ".")
    return lines

def _failing_files(test, paths) -> list:
    """Which of the changed files a failed combined run points
    at: a changed path named in the failure brief (compile
    errors carry their path), or a changed module whose test
    file is among the failing tests. Empty when the run doesn't
    identify one — callers fall back to the first changed file,
    so a revision never fans out wider than the evidence."""
    brief = _gh()._failure_brief(test)
    hits = []
    for p in paths:
        stem = p.rsplit("/", 1)[-1]
        if stem.endswith(".py"):
            stem = stem[:-3]
        if p in brief or (stem and (f"test_{stem}" in brief
                                    or f"{stem}.py" in brief)):
            hits.append(p)
    return hits


def _fix_loop_multi(run_checks, state, thing: str, lessons,
                    extra_for=None) -> tuple:
    """The competence loop for a MULTI-file change (Round 60):
    the sandbox checks the COMBINED tree; on a revisable
    failure only the failing file(s) are revised — the files
    the run's own output names, or the first changed file when
    it names none — then the combined tree is re-checked. Same
    bound as the single-file loop: _gh()._MAX_FIX_ATTEMPTS check runs
    total. state is a list of {"path", "base_text", "content"}
    dicts, mutated in place. Returns (test, attempts,
    cited_lesson_titles, revised_paths) — revised_paths names
    exactly the files the loop rewrote, so callers can note the
    correction against the right files."""
    attempts = []
    cited: list = []
    revised_paths: list = []
    test = run_checks(state)
    attempts.append(_gh()._attempt_record(1, test))
    while len(attempts) < _gh()._MAX_FIX_ATTEMPTS \
            and not _gh().checks_green(test) and _gh()._revisable(test):
        paths = [s["path"] for s in state]
        failing = _failing_files(test, paths) or paths[:1]
        changed_any = False
        for s in state:
            if s["path"] not in failing:
                continue
            extra = ""
            if extra_for is not None:
                try:
                    extra = extra_for(s["path"]) or ""
                except Exception:
                    extra = ""
            revised, used = _gh()._revise_content(
                s["path"], s["base_text"], s["content"], test,
                thing, lessons, extra_context=extra)
            if revised is not None:
                s["content"] = revised
                cited = used or cited
                changed_any = True
                if s["path"] not in revised_paths:
                    revised_paths.append(s["path"])
        if not changed_any:
            break
        test = run_checks(state)
        attempts.append(_gh()._attempt_record(len(attempts) + 1, test))
    return test, attempts, cited, revised_paths

def _local_sandbox_files_multi(changes: Dict) -> Dict:
    """Assemble this codebase for a sandbox run with SEVERAL
    drafted changes applied (Round 60 multi-file) — the same
    exclusions as _local_sandbox_files (test files out by
    design, pytest config not copied), every change in."""
    files: Dict = {}
    total = 0
    for p in _gh()._local_paths():
        if not p.endswith(".py") or p in changes:
            continue
        if _gh()._is_test_path(p):
            continue
        text = _gh()._local_read(p)
        if text is None:
            continue
        blob = text.encode("utf-8")
        if len(files) >= _gh()._SANDBOX_MAX_FILES \
                or total + len(blob) > _gh()._SANDBOX_MAX_BYTES:
            continue
        files[p] = blob
        total += len(blob)
    for p, content in changes.items():
        files[p] = str(content).encode("utf-8")
    for cfg in ("pytest.ini", "setup.cfg", "tox.ini"):
        if cfg not in files:
            text = _gh()._local_read(cfg)
            if text is not None and len(text) < 64 * 1024:
                files[cfg] = text.encode("utf-8")
    return files


def run_local_checks_multi(changes: Dict) -> Dict:
    """run_local_checks for a multi-file draft (Round 60): the
    combined tree is assembled with ALL changes applied and
    checked once — py_compile on every changed .py, the same
    exit-5 reading as the single-file check. A change set with
    no Python in it checks as 'real, non-empty changes'."""
    py_changes = {p: c for p, c in (changes or {}).items()
                  if p.endswith(".py")}
    if not py_changes:
        ok = bool(changes) and all(
            str(c or "").strip() for c in changes.values())
        return {"compile": {"ok": ok,
                            "errors": [] if ok else
                            ["a draft file is empty"]},
                "tests": {"status": "none", "passed": 0,
                          "failed": 0, "failing": [], "tail": ""},
                "timeout": False}
    result = _gh().run_python_checks(
        _local_sandbox_files_multi(changes), list(py_changes))
    tests = result.get("tests") or {}
    if tests.get("status") == "error" \
            and "exit 5" in str(tests.get("reason") or ""):
        tests["status"] = "none"
        tests["reason"] = ("no pytest suite ships with the "
                            "server modules — py_compile is "
                            "the check")
    return result

def _draft_system_fix_multi(title: str, diagnosis: str,
                            spec: Dict, lessons, targets,
                            conv_entries, repo_map) -> tuple:
    """Round 60 multi-file system draft: a diagnosed problem
    whose fix spans up to _gh()._MAX_CHANGE_FILES named files. Each
    file is drafted with its own map facts + the notebook in
    context; the loop checks the COMBINED tree and revises only
    the failing file(s). Green or nothing — the same discipline
    as the single-file drafter. Preparation only, always."""
    attempts: list = []
    try:
        paths = _gh()._local_paths()
        for t in targets:
            if t not in paths or not t.endswith(".py") \
                    or not _gh()._valid_path(t):
                return None, attempts
        base_texts: Dict = {}
        for t in targets:
            text = _gh()._local_read(t)
            if text is None or len(text) > _gh()._MAX_EDIT_CHARS:
                return None, attempts
            base_texts[t] = text
        conv_block = _gh()._conventions_block(conv_entries)

        def _extra_for(p):
            return "\n\n".join(
                x for x in [map_facts_block(repo_map, p),
                            conv_block] if x)

        state = []
        for t in targets:
            content = _gh()._draft_fix_content(
                title, diagnosis, spec, t, base_texts[t],
                lessons, extra_context=_extra_for(t))
            if not _gh()._usable_revision(content, base_texts[t]):
                return None, attempts
            state.append({"path": t, "base_text": base_texts[t],
                          "content": content})

        def _run(st):
            return run_local_checks_multi(
                {s["path"]: s["content"] for s in st})

        test, attempts, cited, revised_paths = _fix_loop_multi(
            _run, state, str(spec.get("what") or title), lessons,
            extra_for=_extra_for)
        for p in revised_paths:
            _gh()._note_loop_correction(_self_repo_label(), p,
                                  attempts)
        if not _gh().checks_green(test):
            return None, attempts
        state = [s for s in state
                 if s["content"] != s["base_text"]]
        if not state:
            return None, attempts
        files_out = [{
            "path": s["path"], "base_text": s["base_text"],
            "content": s["content"],
            "diff": _gh()._make_diff(s["base_text"], s["content"],
                               s["path"]),
        } for s in state]
        digest = hashlib.sha256(
            "".join(s["content"] for s in state)
            .encode("utf-8")).hexdigest()[:16]
        return {
            "path": files_out[0]["path"],
            "base_text": files_out[0]["base_text"],
            "content": files_out[0]["content"],
            "diff": files_out[0]["diff"],
            "files": files_out,
            "test": test,
            "attempts": attempts,
            "approach_key": "code:" + "+".join(
                f["path"] for f in files_out) + f":{digest}",
            "lessons_cited": cited,
            "conventions_cited": [
                str(e.get("text", "")) for e in (conv_entries
                                                or [])
                if e.get("text")][:2],
        }, attempts
    except Exception as e:
        logger.warning(f"System multi-fix draft failed: {e}")
        return None, attempts

def _draft_answer_multi(entry: Dict, uid: str, ask_text: str,
                        thing: str, who: str, full_name: str,
                        owner: str, repo: str,
                        default_branch: str, repo_data: Dict,
                        named_paths, tree_paths) -> Optional[list]:
    """Round 60 multi-file draft step: the ask named 2-3
    EXISTING files. Every file is read, parked on the pending
    change, and shown to the drafter with its current content —
    the preview carries ONE fenced block per file, in order.
    Nothing is written to GitHub here (same law as the
    single-file draft step)."""
    token = entry.get("access_token", "")
    files = []
    for p in named_paths:
        fstatus, info = _gh()._fetch_file(token, owner, repo, p,
                                    default_branch)
        if fstatus == 401:
            return _gh()._revoked(uid, who)
        if info is None:
            body = (f"The visitor ({who}) asked OG to change "
                    f"several files in {full_name}, but OG could "
                    f"not read {p} as text (it is missing or "
                    "binary), so OG will NOT draft a multi-file "
                    "change it cannot see whole. Tell them "
                    "plainly, in persona — no draft, no pull "
                    "request promised.")
            return [{"title": "🐙 GitHub — file unreadable",
                     "body": body,
                     "href": repo_data.get("html_url", "")}]
        if len(info["text"]) > _gh()._MAX_EDIT_CHARS:
            body = (f"The visitor ({who}) asked OG to change "
                    f"several files in {full_name}, but {p} is "
                    f"{len(info['text'])} characters — over OG's "
                    f"{_gh()._MAX_EDIT_CHARS}-character edit limit, "
                    "because OG only edits a file it can show "
                    "whole. Tell them plainly, in persona — no "
                    "draft, no pull request promised.")
            return [{"title": "🐙 GitHub — file too large to edit",
                     "body": body,
                     "href": repo_data.get("html_url", "")}]
        files.append({"path": p, "base_sha": info["sha"],
                      "base_text": info["text"]})
    slug_src = thing or files[0]["path"].rsplit("/", 1)[-1]
    branch = "og/" + _gh()._slugify(slug_src)
    repo_map = get_repo_map(token, owner, repo, default_branch,
                            tree_paths)
    facts = []
    if repo_map:
        for f in files:
            block = map_facts_block(repo_map, f["path"])
            if block:
                facts.append(block)
    conv_entries = _gh()._consult_conventions(full_name)
    conv_block = _gh()._conventions_block(conv_entries)
    conv_texts = [str(e.get("text", "")) for e in conv_entries
                  if e.get("text")][:2]
    _gh()._set_pending(uid, {
        "repo_full": full_name, "owner": owner, "repo": repo,
        "branch": branch, "path_hint": named_paths[0],
        "thing": thing, "mode": "edit", "path": files[0]["path"],
        "base_sha": files[0]["base_sha"],
        "base_text": files[0]["base_text"],
        "default_branch": default_branch, "stage": "draft",
        "verify_first": True, "multi": True,
        "files": files,
        "py_paths": [p for p in (tree_paths or [])
                     if p.endswith(".py")],
        "tree_paths": tree_paths or [],
        "conventions": conv_texts,
    })
    closing = ("Reply YES — OG will run the checks and show you "
               "the final verified preview before anything is "
               "written — or NO to scrap it.")
    shape = (
        "Present the draft in EXACTLY this shape, in this order, "
        "with nothing before the first line:\n"
        f"Line 1: 🧾 {_gh()._PREVIEW_MARKER} — nothing is on GitHub yet\n"
        f"Line 2: Repo: {full_name}\n"
        f"Line 3: Branch: {branch}\n"
        f"Line 4: Files: {', '.join(named_paths)}\n"
        "Line 5: Mode: edit\n"
        "Then, for EACH file in the order listed, a line "
        "'File: <path>' followed by the COMPLETE new content of "
        "that file in ONE fenced code block.\n"
        "Then ONE short plain-words line saying what changed.\n"
        f"Then this closing line, word for word: {closing}\n\n"
        f"Rules: this change spans exactly these "
        f"{len(named_paths)} files (OG's multi-file bound is "
        f"{_gh()._MAX_CHANGE_FILES}); each file's new content under "
        "64 KB. Do NOT claim the change or the PR exists yet — "
        "this is a preview awaiting their YES.")
    currents = "\n\n".join(
        f"Current content of {f['path']} on {default_branch} — "
        f"draft the new version FROM this, keeping everything "
        f"that should not change:\n{f['base_text']}"
        for f in files)
    body = (
        f"The visitor ({who}) asked you to change code in their "
        f"GitHub repo {full_name}: \"{ask_text}\". The repo is "
        f"real and connected. The change spans these EXISTING "
        f"files: {', '.join(named_paths)} — their current "
        "contents are at the end of this instruction. DRAFT the "
        "complete new content of EACH file NOW — whole finished "
        "files with the change applied, not snippets and not "
        "patches. NOTHING has been written to GitHub yet: OG "
        "only ever writes as a pull request the owner "
        "approves.\n\n"
        "Repo files (context for the draft):\n"
        + _gh()._tree_excerpt(tree_paths, files[0]["path"]) + "\n\n"
        + ("\n\n".join(facts) + "\n\n" if facts else "")
        + (conv_block + "\n" if conv_block else "")
        + shape + "\n\n" + currents)
    return [{"title": f"🐙 PR draft for {full_name}",
             "body": body,
             "href": repo_data.get("html_url", "")}]

def _extract_preview_multi(uid: str, pending: Dict) \
        -> Optional[Dict]:
    """Round 60: lift an approved MULTI-file preview out of the
    visitor's own thread. The Files line must name exactly the
    pending change's files, in order, and each file's section
    ('File: <path>' + its fenced block) must be present — the
    same what-you-saw-is-what-ships discipline as the
    single-file extractor, per file. Returns {"path", "content",
    "files": [{path, content}]} with the first file's values in
    the legacy slots."""
    load_history = _gh()._deps.get("load_history")
    if load_history is None:
        return None
    try:
        history = load_history(uid)
    except Exception as e:
        logger.warning(f"GitHub preview history read failed: {e}")
        return None
    text = ""
    for entry in reversed(history or []):
        if isinstance(entry, dict) and entry.get("role") == "assistant" \
                and _gh()._PREVIEW_MARKER in str(entry.get("content", "")) \
                and "(verified)" not in str(entry.get("content", "")):
            text = str(entry["content"])
            break
    if not text:
        return None
    m_repo = re.search(r"^Repo:\s*(\S+)\s*$", text, re.M)
    m_branch = re.search(r"^Branch:\s*(\S+)\s*$", text, re.M)
    m_mode = re.search(r"^Mode:\s*(\S+)\s*$", text, re.M)
    m_files = re.search(r"^Files:\s*(.+)$", text, re.M)
    if not m_repo or m_repo.group(1) != pending.get("repo_full"):
        return None
    if not m_branch or m_branch.group(1) != pending.get("branch"):
        return None
    if m_mode and m_mode.group(1) != "edit":
        return None
    expected = [f["path"] for f in (pending.get("files") or [])]
    if not expected or not m_files:
        return None
    listed = [p.strip() for p in m_files.group(1).split(",")
              if p.strip()]
    if listed != expected:
        return None
    out_files = []
    pos = m_files.end()
    for p in expected:
        m_file = re.search(r"^File:\s*" + re.escape(p) + r"\s*$",
                           text[pos:], re.M)
        if not m_file:
            return None
        start = pos + m_file.end()
        m_block = re.search(r"```[^\n]*\n(.*?)```", text[start:],
                            re.S)
        if not m_block:
            return None
        content = m_block.group(1)
        if not content.strip():
            return None
        if len(content.encode("utf-8")) > _gh()._MAX_FILE_BYTES:
            return {"error": "too_big", "path": p}
        out_files.append({"path": p, "content": content})
        pos = start + m_block.end()
    return {"path": out_files[0]["path"],
            "content": out_files[0]["content"],
            "files": out_files}

def _assemble_test_files_multi(entry: Dict, pending: Dict,
                               changes: Dict):
    """_assemble_test_files for a multi-file change (Round 60):
    the repo's Python files with ALL pending changes applied.
    Same (files, hard_reason, soft_note) contract and the same
    assembly limits."""
    token = entry.get("access_token", "")
    owner, repo = pending["owner"], pending["repo"]
    ref = pending.get("default_branch", "")
    files = {p: str(c).encode("utf-8")
             for p, c in changes.items()}
    py_paths = [p for p in (pending.get("py_paths") or [])
                if p not in changes]
    if len(py_paths) + len(changes) > _gh()._SANDBOX_MAX_FILES + 1:
        return files, (f"the repo has "
                       f"{len(py_paths) + len(changes)} Python "
                       f"files — over OG's "
                       f"{_gh()._SANDBOX_MAX_FILES}-file assembly limit"), ""
    total = sum(len(b) for b in files.values())
    fetched = 0
    for p in py_paths:
        st, info = _gh()._fetch_file(token, owner, repo, p, ref)
        if info is None:
            continue
        blob = info["text"].encode("utf-8")
        total += len(blob)
        if total > _gh()._SANDBOX_MAX_BYTES:
            return files, ("the repo's Python exceeds OG's "
                           f"{_gh()._SANDBOX_MAX_BYTES // (1024 * 1024)} MB "
                           "assembly limit"), ""
        files[p] = blob
        fetched += 1
    for cfg in ("pytest.ini", "pyproject.toml", "setup.cfg",
                "tox.ini"):
        if cfg in (pending.get("tree_paths") or []) \
                and cfg not in files:
            st, info = _gh()._fetch_file(token, owner, repo, cfg, ref)
            if info is not None and len(info["text"]) < 64 * 1024:
                files[cfg] = info["text"].encode("utf-8")
    soft = ""
    if fetched < len(py_paths):
        soft = (f"assembled {fetched} of {len(py_paths)} Python "
                "files — the rest could not be read")
    return files, "", soft

def _verify_pending_multi(entry: Dict, pending: Dict,
                          extracted: Dict, uid: str) -> list:
    """Round 60: the first YES on a MULTI-file change. Every
    file's base is re-checked on GitHub (any moved = abort,
    honestly), the sandbox assembles ALL files and checks the
    combined tree, the competence loop revises only the failing
    file(s), and the verified preview shows EVERY file's
    module-computed diff. The second YES executes. Any failure
    here writes NOTHING — the single-file discipline, per file."""
    token = entry.get("access_token", "")
    who = entry.get("login") or entry.get("name") or "the visitor"
    owner, repo, full = (pending["owner"], pending["repo"],
                         pending["repo_full"])
    ref = pending.get("default_branch", "")
    bases = {f["path"]: f for f in (pending.get("files") or [])
             if isinstance(f, dict)}

    def abort(title: str, body: str) -> list:
        _gh()._clear_pending(uid)
        return [{"title": title, "body": body,
                 "href": f"https://github.com/{full}"}]

    state = []
    for item in extracted.get("files") or []:
        p = item["path"]
        base = bases.get(p) or {}
        st, info = _gh()._fetch_file(token, owner, repo, p, ref)
        if st == 401:
            _gh()._clear_pending(uid)
            return _gh()._revoked(uid, who)
        if info is None or info.get("sha") != base.get("base_sha"):
            return abort(
                "🐙 GitHub PR — file changed",
                f"The visitor ({who}) approved a draft change "
                f"spanning several files in {full}, but {p} has "
                "CHANGED on GitHub since the preview was drafted "
                "— OG will not write over someone else's newer "
                "work. NOTHING was written. Tell them plainly, "
                "in persona, and invite them to ask again so OG "
                "drafts a fresh preview against the current "
                "files.")
        state.append({"path": p,
                      "base_text": base.get("base_text") or "",
                      "content": item["content"]})
    if not state:
        return abort(
            "🐙 GitHub PR — couldn't read draft",
            f"The visitor ({who}) approved a multi-file draft "
            f"for {full}, but OG could not read the files back "
            "cleanly. NOTHING was written. Tell them plainly, "
            "in persona.")

    test = None
    attempts: list = []
    cited: list = []
    if any(s["path"].endswith(".py") for s in state):
        def _run(st):
            files, hard, soft = _assemble_test_files_multi(
                entry, pending,
                {s["path"]: s["content"] for s in st})
            t = _gh().run_python_checks(
                files, [s["path"] for s in st],
                tests_skip_reason=hard)
            if soft:
                t["note"] = soft
            return t

        lessons = _gh()._consult_lessons(
            path=state[0]["path"])
        repo_map = None
        if pending.get("tree_paths"):
            repo_map = get_repo_map(token, owner, repo, ref,
                                    pending.get("tree_paths"))
        conv_block = _gh()._conventions_block(
            _gh()._consult_conventions(full))

        def _extra_for(p):
            return "\n\n".join(
                x for x in [map_facts_block(repo_map, p),
                            conv_block] if x)

        test, attempts, cited, revised_paths = _fix_loop_multi(
            _run, state, pending.get("thing") or "", lessons,
            extra_for=_extra_for)
        for p in revised_paths:
            _gh()._note_loop_correction(full, p, attempts)
    state = [s for s in state
             if s["content"] != s["base_text"]]
    if not state:
        return abort(
            "🐙 GitHub PR — no change",
            f"The visitor ({who}) approved a multi-file draft "
            f"for {full}, but after OG's own revisions every "
            "file is back to IDENTICAL with what's already "
            "there — there is no change to ship. NOTHING was "
            "written. Tell them plainly, in persona.")
    battery = None
    if any(s["path"].endswith(".py") for s in state) \
            and _repo_has_r_suites(pending.get("tree_paths")):
        bfiles, bhard, _bsoft = _assemble_test_files_multi(
            entry, pending,
            {s["path"]: s["content"] for s in state})
        if not bhard:
            battery = run_test_battery(
                bfiles, [s["path"] for s in state])
    files_out = [{
        "path": s["path"], "base_text": s["base_text"],
        "content": s["content"],
        "diff": _gh()._make_diff(s["base_text"], s["content"],
                           s["path"]),
    } for s in state]
    pkg = {"path": files_out[0]["path"],
           "content": files_out[0]["content"],
           "diff": files_out[0]["diff"], "files": files_out,
           "test": test, "attempts": attempts, "lessons": cited}
    if pending.get("conventions"):
        pkg["conventions"] = pending["conventions"]
    if battery is not None:
        pkg["battery"] = battery
    verified = dict(pending)
    verified["stage"] = "verified"
    verified["pkg"] = pkg
    verified["files"] = [{"path": f["path"],
                          "base_sha": (bases.get(f["path"])
                                       or {}).get("base_sha", ""),
                          "base_text": f["base_text"]}
                         for f in files_out]
    _gh()._set_pending(uid, verified)

    checks = _gh()._checks_line(test, files_out[0]["path"])
    if test is not None and test.get("note"):
        checks += f" (Partial run: {test['note']}.)"
    extra_lines = ""
    aline = _gh()._attempts_line(attempts, test)
    if aline:
        extra_lines += aline + "\n"
    for title_ in cited:
        extra_lines += f"📘 Lesson applied: {title_}\n"
    if battery is not None:
        for line in _battery_lines(battery):
            extra_lines += line + "\n"
    for conv in (pkg.get("conventions") or []):
        extra_lines += f"📐 Convention applied: {conv}\n"
    blocks = "\n".join(
        f"File: {f['path']}\n```diff\n{f['diff']}```"
        for f in files_out)
    names = ", ".join(f["path"] for f in files_out)
    preview = (
        f"🧾 {_gh()._PREVIEW_MARKER} (verified) — nothing is on GitHub "
        f"yet\nRepo: {full}\nBranch: {pending['branch']}\n"
        f"Files: {names}\nMode: edit\n"
        f"{blocks}\nChecks: {checks}\n"
        f"{extra_lines}"
        "Reply YES to open the PR — or NO to scrap it.")
    body = (
        f"The visitor ({who}) said YES to the multi-file draft "
        f"for {full}. OG has now VERIFIED the change: every "
        "file's base on GitHub is unchanged, each diff below "
        "was computed by OG itself (they are exact), and the "
        "Python checks ran on the COMBINED tree — their outcome "
        "is on the Checks line. NOTHING has been written to "
        "GitHub yet. Present the following preview EXACTLY, word "
        "for word — every line, every block and the Checks line "
        f"— and add nothing about the change yourself:\n\n"
        f"{preview}")
    return [{"title": "🐙 PR verified preview — "
                       f"{full}", "body": body,
             "href": f"https://github.com/{full}"}]

def _create_pr_multi(entry: Dict, pending: Dict, file_list,
                     uid: str) -> list:
    """Round 60: execute an APPROVED multi-file change — the
    existing PR mechanics, once per change: ONE branch from the
    default branch, one Contents-API commit per file, ONE pull
    request carrying all of them. Every file's base is
    re-verified at write time (the standing law); any refusal
    stops the write and reports the exact partial state."""
    token = entry.get("access_token", "")
    who = entry.get("login") or entry.get("name") or "the visitor"
    owner = pending["owner"]
    repo = pending["repo"]
    full = pending["repo_full"]
    bases = {f["path"]: f for f in (pending.get("files") or [])
             if isinstance(f, dict)}

    def failed(stage, note=""):
        body = (f"The visitor ({who}) approved a multi-file "
                f"pull request for {full}, but the GitHub write "
                f"stopped at the {stage} step{note}. No pull "
                "request was opened. Tell them plainly, in "
                "persona, exactly where it stopped — do NOT "
                "claim a PR exists.")
        return [{"title": "🐙 GitHub PR — stopped", "body": body,
                 "href": f"https://github.com/{full}"}]

    status, data = _gh()._gh_request("GET", f"/repos/{owner}/{repo}",
                               token)
    if status == 401:
        return _gh()._revoked(uid, who)
    if status != 200 or not isinstance(data, dict):
        return failed("repo check")
    default_branch = data.get("default_branch") or "main"

    # The standing law, per file, at the moment of writing.
    for item in file_list:
        p = item["path"]
        base_sha = (bases.get(p) or {}).get("base_sha", "")
        vstatus, vinfo = _gh()._fetch_file(token, owner, repo, p,
                                     default_branch)
        if vstatus == 401:
            return _gh()._revoked(uid, who)
        if base_sha:
            if vinfo is None or vinfo.get("sha") != base_sha:
                body = (f"The visitor ({who}) approved a "
                        f"multi-file pull request for {full}, "
                        f"but {p} CHANGED on GitHub after the "
                        "preview — OG will not write over newer "
                        "work. NOTHING was written and no branch "
                        "was created. Tell them plainly, in "
                        "persona, and invite them to ask again "
                        "for a fresh preview.")
                return [{"title": "🐙 GitHub PR — file changed",
                         "body": body,
                         "href": f"https://github.com/{full}"}]
        elif vstatus == 200:
            body = (f"The visitor ({who}) approved a multi-file "
                    f"pull request for {full}, but a file "
                    f"already exists at {p} on {default_branch} "
                    "— it appeared after the preview. NOTHING "
                    "was written and no branch was created. Tell "
                    "them plainly, in persona.")
            return [{"title": "🐙 GitHub PR — file exists",
                     "body": body,
                     "href": f"https://github.com/{full}"}]

    status, ref = _gh()._gh_request(
        "GET", f"/repos/{owner}/{repo}/git/ref/heads/"
        + quote(default_branch, safe="/"), token)
    if status == 401:
        return _gh()._revoked(uid, who)
    if status != 200 or not isinstance(ref, dict) \
            or not (ref.get("object") or {}).get("sha"):
        return failed("default-branch lookup")
    base_commit = ref["object"]["sha"]

    branch = pending["branch"]
    status = None
    for attempt in range(3):
        candidate = branch if attempt == 0 \
            else f"{branch}-{attempt + 1}"
        status, _ = _gh()._gh_request(
            "POST", f"/repos/{owner}/{repo}/git/refs", token,
            json_body={"ref": f"refs/heads/{candidate}",
                       "sha": base_commit})
        if status == 401:
            return _gh()._revoked(uid, who)
        if status == 201:
            branch = candidate
            break
    else:
        return failed("branch creation")

    committed = []
    for item in file_list:
        p = item["path"]
        base_sha = (bases.get(p) or {}).get("base_sha", "")
        encoded = base64.b64encode(
            str(item["content"]).encode("utf-8")).decode()
        put_body = {
            "message": (f"Update {p} (via OG AI)" if base_sha
                        else f"Add {p} (via OG AI)"),
            "content": encoded, "branch": branch}
        if base_sha:
            put_body["sha"] = base_sha
        status, _ = _gh()._gh_request(
            "PUT", f"/repos/{owner}/{repo}/contents/"
            + quote(p, safe="/"), token, json_body=put_body)
        if status == 401:
            return _gh()._revoked(uid, who)
        if status not in (200, 201):
            done = (f" — {len(committed)} of {len(file_list)} "
                    f"files WERE committed on branch {branch} "
                    f"({', '.join(committed)}), the rest were "
                    "not" if committed else "")
            return failed("file commit",
                          f" (the branch {branch} WAS created"
                          f"{done}; GitHub refused {p})")
        committed.append(p)

    thing = (pending.get("thing") or "").strip()
    fallback = f"Update {file_list[0]['path']} + " \
               f"{len(file_list) - 1} more"
    title = (thing[:60] if thing else fallback)
    title = title[0].upper() + title[1:] if title else fallback
    names = ", ".join(f"`{i['path']}`" for i in file_list)
    pr_body = (f"Drafted by OG in chat and approved by the repo "
               f"owner before anything was written.\n\n"
               f"Files changed: {names} on branch `{branch}`.")
    pkg = pending.get("pkg") or {}
    pkg_test = pkg.get("test")
    if pkg_test is not None:
        pr_body += ("\n\nChecks OG ran before opening: "
                    + _gh()._checks_line(pkg_test,
                                   file_list[0]["path"]))
    pkg_attempts = pkg.get("attempts") or []
    if len(pkg_attempts) > 1:
        pr_body += (f"\n\nOG's first draft failed those checks; "
                    f"he diagnosed it and revised the code "
                    f"himself — attempt "
                    f"{pkg_attempts[-1].get('n', len(pkg_attempts))} "
                    f"of {_gh()._MAX_FIX_ATTEMPTS} is what was approved.")
    for title_ in pkg.get("lessons") or []:
        pr_body += f"\nLesson applied: {title_}"
    for conv in pkg.get("conventions") or []:
        pr_body += f"\nConvention applied: {conv}"
    status, pr = _gh()._gh_request(
        "POST", f"/repos/{owner}/{repo}/pulls", token,
        json_body={"title": title, "head": branch,
                   "base": default_branch, "body": pr_body})
    if status == 401:
        return _gh()._revoked(uid, who)
    if status != 201 or not isinstance(pr, dict) \
            or not pr.get("html_url"):
        return failed("pull request opening",
                      f" (the files WERE committed on branch "
                      f"{branch})")
    pr_url = pr["html_url"]
    head_sha = (pr.get("head") or {}).get("sha", "")
    if head_sha:
        watch = {
            "repo_full": full, "owner": owner, "repo": repo,
            "pr_number": pr.get("number"), "pr_url": pr_url,
            "branch": branch, "head_sha": head_sha,
            "status": "pending", "checks": [], "notified": False,
            "created": time.time(), "updated": time.time()}
        if pending.get("fix_link"):
            watch["fix_link"] = pending["fix_link"]
        _register_checks_watch(uid, watch)
        _start_checks_watch(uid, pr_url)
    body = (f"DONE — the pull request the visitor ({who}) "
            f"approved is now OPEN on {full}: \"{title}\" — "
            f"files {', '.join(i['path'] for i in file_list)} "
            f"on branch {branch}. The PR URL is {pr_url}. "
            "Confirm it to them in persona with that URL — it "
            "is already open; do not say you will open it."
            + (" OG is also tracking the PR's GitHub Actions "
               "checks: the outcome lands in the visitor's "
               "notifications, and they can ask 'did the checks "
               "pass on my PR?' — mention that once, briefly."
               if head_sha else ""))
    return [{"title": "🐙 GitHub PR opened", "body": body,
             "href": pr_url}]

# --- Post-merge live verification (Round 60, item 5) ------------------------------
# When an APPROVED DRAFTED FIX's PR merges, OG checks once —
# after the deploy window — whether the fix actually took, and
# records the verdict on the proposal (verified_fixed |
# still_present) through og_fixqueue's seam. Exactly one pass
# per executed fix (the watch's verified_at / verify_failed
# stamps are the guard), and the pass is fail-safe end to end:
# it reads and records, it never writes to any repo, and a
# verification failure mutates nothing at all.


def _pr_merged(entry: Dict, watch: Dict):
    """The PR's merge state: True (merged), False (open),
    'closed' (closed unmerged), or None (the read failed)."""
    token = entry.get("access_token", "")
    number = watch.get("pr_number")
    if not number:
        return None
    status, data = _gh()._gh_request(
        "GET", f"/repos/{watch['owner']}/{watch['repo']}/pulls/"
        + quote(str(number), safe=""), token)
    if status != 200 or not isinstance(data, dict):
        return None
    if data.get("merged"):
        return True
    if str(data.get("state") or "") == "closed":
        return "closed"
    return False


def _merge_watch(uid: str, pr_url: str):
    """The bounded merge watch for a fix-linked watch: poll the
    PR every _gh()._MERGE_POLL_SECONDS for up to _gh()._MERGE_POLL_BUDGET.
    On merge: stamp it, wait out the deploy window, run the ONE
    verification pass. On close-without-merge: stop quietly —
    an unmerged fix needs no verdict."""
    deadline = time.time() + _gh()._MERGE_POLL_BUDGET
    while time.time() < deadline:
        watch = _get_watch(uid, pr_url)
        if watch is None or watch.get("verified_at") \
                or watch.get("verify_failed"):
            return
        entry = _gh()._github_connection(uid)
        if not entry:
            return
        try:
            merged = _pr_merged(entry, watch)
        except Exception as e:
            logger.warning(f"Merge poll failed: {e}")
            merged = None
        if merged == "closed":
            _update_watch(uid, pr_url, merge="closed")
            return
        if merged is True:
            _update_watch(uid, pr_url, merge="merged",
                          merged_at=time.time())
            time.sleep(_gh()._DEPLOY_WINDOW)
            watch = _get_watch(uid, pr_url) or watch
            if watch.get("verified_at") \
                    or watch.get("verify_failed"):
                return
            _run_fix_verification(entry, uid, watch)
            return
        time.sleep(_gh()._MERGE_POLL_SECONDS)


def _battery_on_remote_tree(entry: Dict, watch: Dict):
    """A fresh battery run over the repo's CURRENT (merged)
    default-branch tree, assembled under the sandbox caps.
    None when the tree can't be assembled — an honest 'could
    not verify', never a pass."""
    try:
        token = entry.get("access_token", "")
        owner, repo = watch["owner"], watch["repo"]
        status, data = _gh()._gh_request(
            "GET", f"/repos/{owner}/{repo}", token)
        if status != 200 or not isinstance(data, dict):
            return None
        ref = data.get("default_branch") or "main"
        tstatus, entries = _gh()._fetch_tree(token, owner, repo, ref)
        if entries is None:
            return None
        tree_paths = [e["path"] for e in entries
                      if e.get("type") == "blob"]
        files: Dict = {}
        total = 0
        py_paths = [p for p in tree_paths
                    if p.endswith(".py")][:_gh()._SANDBOX_MAX_FILES]
        extras = [p for p in tree_paths
                  if _R_SUITE_RE.match(p)
                  or p in ("pytest.ini", "pyproject.toml",
                           "setup.cfg", "tox.ini", "conftest.py")
                  or p.endswith("/conftest.py")]
        for p in py_paths + [e for e in extras
                             if e not in py_paths]:
            st, info = _gh()._fetch_file(token, owner, repo, p, ref)
            if info is None:
                continue
            blob = info["text"].encode("utf-8")
            total += len(blob)
            if total > _gh()._SANDBOX_MAX_BYTES:
                return None
            files[p] = blob
        if not files:
            return None
        return run_test_battery(files, [])
    except Exception as e:
        logger.warning(f"Merged-tree battery failed: {e}")
        return None


def _run_fix_verification(entry: Dict, uid: str, watch: Dict):
    """The ONE post-merge verification pass for an executed
    fix. OG's own repo: re-check the originating problem
    through the fix queue's verify seam (og_fixqueue
    ._condition_gone — the self-check seam; its button checks
    probe the LIVE service). User repos: the merged PR's checks
    state + a fresh battery run on the merged tree. The verdict
    is recorded on the proposal via og_fixqueue
    .record_fix_verdict. NEVER raises; a pass that cannot run
    records nothing and mutates nothing."""
    try:
        if watch.get("verified_at") or watch.get("verify_failed"):
            return
        link = watch.get("fix_link") or {}
        repo_full = watch.get("repo_full") \
            or link.get("repo_full") or ""
        verdict = None
        verify = link.get("verify") or {}
        self_repo = os.getenv("OG_SELF_REPO", "").strip()
        if repo_full and self_repo and repo_full == self_repo \
                and str(verify.get("kind") or "") in (
                        "button", "measure", "report_store",
                        "update", "python", "fixqueue_store"):
            try:
                import og_fixqueue as _fq
                gone = _fq._condition_gone(
                    {"verify": verify})
                verdict = "verified_fixed" if gone \
                    else "still_present"
            except Exception as e:
                logger.warning(
                    f"Self-repo verification failed: {e}")
                verdict = None
        if verdict is None:
            checks_ok = watch.get("status") in ("success",
                                                "none")
            battery = _battery_on_remote_tree(entry, watch)
            if battery is not None:
                verdict = "verified_fixed" if (
                    checks_ok and battery_green(battery)) \
                    else "still_present"
        if verdict is None:
            _update_watch(uid, watch["pr_url"],
                          verify_failed=True)
            return
        _update_watch(uid, watch["pr_url"],
                      verified_at=time.time(),
                      verification=verdict)
        try:
            import og_fixqueue as _fq
            _fq.record_fix_verdict(
                str(link.get("owner") or ""),
                str(link.get("fingerprint") or ""), verdict,
                str(watch.get("pr_url") or ""))
        except Exception as e:
            logger.warning(f"Verdict record failed: {e}")
    except Exception as e:
        logger.warning(f"Fix verification failed: {e}")

def _start_checks_watch(uid: str, pr_url: str):
    """The bounded background poller: first look after a short
    delay, then on the poll cadence, until a terminal state or
    the ~20-minute budget runs out (then the watch is marked
    'stopped' — a later ask can still refresh it lazily).
    Round 60: a watch carrying a fix_link (an approved drafted
    fix) then keeps a bounded MERGE watch — when the PR merges,
    one post-merge verification pass runs after the deploy
    window. Non-fix watches behave exactly as before."""
    def run():
        deadline = time.time() + _gh()._CHECKS_POLL_BUDGET
        delay = _gh()._CHECKS_POLL_FIRST
        keep_watching_merge = False
        while time.time() < deadline:
            time.sleep(delay)
            delay = _gh()._CHECKS_POLL_SECONDS
            watch = _get_watch(uid, pr_url)
            if watch is None:
                return
            if watch.get("status") in _CHECK_TERMINAL:
                keep_watching_merge = True
                break
            entry = _gh()._github_connection(uid)
            if not entry:
                _update_watch(uid, pr_url, status="stopped")
                return
            try:
                state = _refresh_watch(entry, uid, watch)
            except Exception as e:
                logger.warning(f"Checks poll failed: {e}")
                state = None
            if state in _CHECK_TERMINAL:
                keep_watching_merge = True
                break
        else:
            watch = _get_watch(uid, pr_url)
            if watch is not None \
                    and watch.get("status") not in _CHECK_TERMINAL:
                _update_watch(uid, pr_url, status="stopped")
                keep_watching_merge = True
        if keep_watching_merge:
            watch = _get_watch(uid, pr_url)
            if watch is not None and watch.get("fix_link") \
                    and not watch.get("verified_at"):
                _merge_watch(uid, pr_url)

    threading.Thread(target=run, daemon=True).start()


def _checks_answer(entry: Optional[Dict], uid: str,
                   consume_lookup) -> Optional[list]:
    """'Did the checks pass on my PR?' — answered from the stored
    watch state, with ONE live refresh while the latest is still
    pending and inside its tracking budget."""
    watches = [w for w in _get_watches(uid) if isinstance(w, dict)]
    who = (entry or {}).get("login") or (entry or {}).get("name") \
        or "the visitor"
    if not watches:
        if not entry:
            return None
        if not _gh()._spend(consume_lookup, uid):
            return None
        body = (f"The visitor ({who}) is asking about GitHub "
                "Actions checks, but OG is not tracking any pull "
                "request for them — no PR they approved through OG "
                "is being watched. Tell them plainly, in persona; "
                "do not invent check results.")
        return [{"title": "🐙 GitHub checks — none tracked",
                 "body": body, "href": "/auth/github"}]
    latest = watches[-1]
    if entry and latest.get("status") == "pending" \
            and time.time() - float(latest.get("created", 0)) \
            <= _gh()._CHECKS_POLL_BUDGET:
        try:
            _refresh_watch(entry, uid, latest)
        except Exception as e:
            logger.warning(f"Checks lazy refresh failed: {e}")
        latest = _get_watch(uid, latest["pr_url"]) or latest
    if not _gh()._spend(consume_lookup, uid):
        logger.info("GitHub checks answer skipped: visitor at "
                    "daily lookup cap")
        return None
    state = latest.get("status", "pending")
    state_line = {
        "success": "✅ ALL CHECKS PASSED",
        "failure": "❌ CHECKS FAILED",
        "pending": "⏳ checks are still running",
        "none": "this repo has NO GitHub Actions checks — "
                "nothing ran",
        "stopped": "OG stopped tracking after ~20 minutes — the "
                   "PR page shows the current state",
    }.get(state, state)
    facts = [
        f"Pull request: {latest.get('repo_full')}"
        f"#{latest.get('pr_number')} ({latest.get('pr_url')})",
        f"Branch: {latest.get('branch')}",
        f"Checks state: {state_line}",
    ]
    for line in latest.get("checks") or []:
        facts.append(f"- {line}")
    if len(watches) > 1:
        others = "; ".join(
            f"{w.get('repo_full')}#{w.get('pr_number')}: "
            f"{w.get('status')}" for w in watches[:-1])
        facts.append(f"Also tracked earlier: {others}")
    body = (f"The visitor ({who}) is asking about the GitHub "
            "Actions checks on their pull request. Answer ONLY "
            "from these facts — OG's tracked state, refreshed "
            "live just now when it was still pending:\n\n"
            + "\n".join(facts))
    return [{"title": "🐙 GitHub checks — "
                       f"{latest.get('repo_full')}",
             "body": body, "href": latest.get("pr_url", "")}]
