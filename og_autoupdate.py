"""Nightly auto-update for OG's OWN system (Round 69; Brent's
rule, 2026-10-11): "this is for his system — that's the only
one he can do like that; any other type he has to ask — the
user or me."

WHAT THIS IS. Once a night, in the 23:00 owner-time window,
OG looks at the fixes he has already prepared and fully
tested for his OWN machinery (the Round 53 fix queue's code
proposals, drafted green through the Round 51/60 machinery)
and — for that one class only — applies them without waking
anybody: the prepared fix ships through the exact Round 53
execution path (re-verified, stamped, opened as a PR), the
PR is merged, and after the deploy lands OG re-tests
everything and compares against the state from just before
the update. Anything newly broken -> he rolls the change
back himself through the same PR machinery (an inverse PR),
records the approach as failed in the lessons book so it can
never be auto-retried, and tells the owner what rolled back
and why, by plain name. Two rolled-back nights in a row and
auto-apply switches ITSELF off until the owner turns it back
on. A quiet night (nothing eligible) is a silent no-op.

THE RULE, encoded in classify_paths() below — NEVER
auto-applied, always left in the queue for a human yes, even
when a prepared fix touches them:
- the persona (ai_agent_enhanced.py / ai_agent.py) — how OG
  talks is Brent's, never the night shift's;
- pricing, tiers, caps (og_tiers.py, og_tokenpacks.py);
- user money (og_wallet.py, og_trading.py, og_ordering*.py);
- user accounts + sign-in machinery (og_accounts.py,
  og_2fa.py);
- account deletion / store readiness (og_store_ready.py);
- connected services and anything that holds tokens
  (GitHub, Google, Spotify, Discord, Twitch, Reddit,
  YouTube, Plaid, the Steel browser + watch vault, R2
  storage, the notification fanout with its push tokens);
- environment / keys / secrets (by filename, any tree);
- any non-code change (a touched path that is not a .py
  file), and the test surface itself (the battery stays an
  honest judge — auto-apply never edits the tests);
- this module (the night shift never rewrites the night
  shift).
Anything else in the fix pipeline that is not a prepared,
green CODE proposal (owner_steps, server executors) is not
an auto-apply candidate either — it simply isn't this
feature's kind. An approach the lessons book already
records as failed for its fingerprint is skipped, like
everywhere else in the pipeline.

STAGING. Applying a code fix pushes to main, Render
redeploys, and THIS process is replaced mid-run — so the
run is staged and every stage's state is durable in this
module's store before anything irreversible happens:
pending_verify (baselines + applied items, each file's
before/after text) is persisted BEFORE the first merge, and
the post-deploy process picks the verification up from
there. Exactly-once per item rides the fix queue's own
stamp-before-running semantics; nothing is ever retried
silently — an item a merge cut short stays open and is
picked up on a later night.

GATE. OG_AUTO_UPDATE_ENABLED must be truthy for a new batch
to start (default off in code; the owner flips it in
Render). Verification/rollback stages for an already-applied
batch run regardless — finishing a safety check is not a
new apply. Separately, the disabled-after-failures state
blocks new batches until the owner re-enables via POST
/system/autoupdate/reenable (stats-key gated, like
/system/check).

TIME. The 23:00 window is read in America/Los_Angeles
(OG_AUTOUPDATE_TZ renames it) via stdlib zoneinfo — the
og_google_hands precedent. If zoneinfo/tzdata is
unavailable, the fallback is a fixed offset
(OG_AUTOUPDATE_TZ_OFFSET_MINUTES, default -480): with that
fallback the wall clock is honest about NOT tracking DST.
"""

import asyncio
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

STORE_FILE = os.getenv("OG_AUTOUPDATE_STORE", "autoupdate_store.json")
GATE_ENV = "OG_AUTO_UPDATE_ENABLED"
POLL_SECONDS = int(os.getenv("OG_AUTOUPDATE_POLL_SECONDS", "60") or "60")
DEPLOY_WAIT_SECONDS = int(
    os.getenv("OG_AUTOUPDATE_DEPLOY_WAIT_SECONDS", "300") or "300")
MAX_HISTORY = 40
DISABLE_AFTER_ROLLBACKS = 2

RULE_TEXT = (
    "Nightly auto-update (Brent's rule, 2026-10-11): OG may "
    "apply prepared, fully-tested fixes to his OWN system "
    "without asking. Anything else — the persona, prices, "
    "tiers or caps, user money, accounts, data deletion, "
    "connected services, keys or environment, non-code "
    "changes — he has to ask about first: the user it "
    "affects, or the owner.")

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, DB_OPTIONS as _DB_OPTS
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_store_lock = threading.Lock()
_loop_started = {"on": False}


# --- Durable store (og_fixqueue pattern: one key -> JSON blob) -----------------


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=_DB_CT, options=_DB_OPTS)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_autoupdate_data ("
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
            logger.warning(f"Autoupdate store load failed: {e}")
    return {}


def _save_file_store(store: dict):
    try:
        with open(STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Autoupdate store save failed: {e}")


def _get(key: str):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_autoupdate_data "
                        "WHERE key=%s", (key,))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning(f"Autoupdate DB load failed, using file: {e}")
    with _store_lock:
        store = _load_file_store()
    return store.get(key)


def _put(key: str, value):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_autoupdate_data (key, data) "
                        "VALUES (%s, %s) ON CONFLICT (key) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (key, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Autoupdate DB save failed, using file: {e}")
    with _store_lock:
        store = _load_file_store()
        store[key] = value
        _save_file_store(store)


def _state() -> dict:
    data = _get("state")
    return data if isinstance(data, dict) else {}


def _save_state(state: dict):
    _put("state", state)


def _history(state: dict, kind: str, detail: str):
    hist = state.setdefault("history", [])
    hist.append({"ts": time.time(), "kind": kind, "detail": detail})
    state["history"] = hist[-MAX_HISTORY:]


# --- Gate + clock ---------------------------------------------------------------


def enabled() -> bool:
    """The env gate: Brent ordered this feature ON, and the
    switch itself lives in Render's env (OG_AUTOUPDATE_ENABLED).
    Code defaults OFF so a deploy without the var never
    surprises anybody."""
    return os.getenv(GATE_ENV, "").strip().lower() in (
        "1", "true", "yes", "on")


def _owner_tz():
    """The owner's timezone for the 23:00 window. zoneinfo per
    the og_google_hands precedent; fixed-offset fallback that
    does NOT track DST (named honestly wherever it shows)."""
    name = os.getenv("OG_AUTOUPDATE_TZ", "").strip() \
        or "America/Los_Angeles"
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name), name
    except Exception:
        try:
            minutes = int(os.getenv(
                "OG_AUTOUPDATE_TZ_OFFSET_MINUTES", "-480"))
        except Exception:
            minutes = -480
        return (timezone(timedelta(minutes=minutes)),
                f"fixed offset {minutes} min (no DST tracking)")


def _owner_now(ts: Optional[float] = None) -> datetime:
    tz, _ = _owner_tz()
    return datetime.fromtimestamp(
        time.time() if ts is None else ts, tz)


def _night_for(local_dt: datetime) -> Optional[str]:
    """The night (a local date, YYYY-MM-DD) whose 23:00 window
    a moment belongs to: from 23:00 through 03:59 the run for
    that night is due (the short after-midnight tail is the
    catch-up for a process that was down at 23:00 sharp).
    Anything later waits for the next 23:00."""
    if local_dt.hour >= 23:
        return local_dt.date().isoformat()
    if local_dt.hour < 4:
        return (local_dt.date() - timedelta(days=1)).isoformat()
    return None


# --- The exclusion classifier (THE RULE) -----------------------------------------

_EXCLUDED_FILES: Dict[str, set] = {
    "the persona (how OG talks)": {
        "ai_agent_enhanced.py", "ai_agent.py"},
    "pricing, tiers or caps": {
        "og_tiers.py", "og_tokenpacks.py"},
    "user money": {
        "og_wallet.py", "og_trading.py", "og_ordering.py",
        "og_ordering_flows.py", "og_tokenpacks.py"},
    "user accounts": {
        "og_accounts.py", "og_2fa.py"},
    "account deletion": {
        "og_store_ready.py"},
    "a connected service or tokens": {
        "og_github.py", "og_codemore.py", "og_google_drive.py",
        "og_google_hands.py", "og_spotify.py", "og_discord.py",
        "og_twitch.py", "og_reddit.py", "og_youtube.py",
        "og_plaid.py", "og_monitor.py", "og_browser.py",
        "og_browser_claim.py", "og_browser_pending.py",
        "og_browser_starting.py", "og_watch.py", "og_mpwatch.py",
        "og_storage.py", "og_notify.py"},
    "the auto-updater itself": {
        "og_autoupdate.py"},
}

_ENV_KEY_RE = re.compile(
    r"(secret|credential|api[-_]?key|access[-_]?key|\.env)", re.I)


def _basename(path: str) -> str:
    return str(path or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def classify_paths(paths: List[str]) -> Optional[str]:
    """The exclusion class a set of touched paths falls under,
    or None when every path is OG's own machinery and fair
    game for the night shift. ANY single excluded path
    excludes the whole item — it stays in the queue for a
    human yes, untouched."""
    for path in paths or []:
        base = _basename(path)
        low = base.lower()
        if not low.endswith(".py"):
            return "a non-code change"
        if low.startswith("test_") or low.endswith("_test.py") \
                or "tests" in str(path).replace("\\", "/").lower():
            return "the test surface"
        if _ENV_KEY_RE.search(base):
            return "environment or keys"
        for klass, names in _EXCLUDED_FILES.items():
            if base in names:
                return klass
    return None


def _touched_paths(prop: dict) -> List[str]:
    payload = prop.get("payload") or {}
    paths = []
    if payload.get("path"):
        paths.append(str(payload["path"]))
    for f in payload.get("files") or []:
        if isinstance(f, dict) and f.get("path"):
            paths.append(str(f["path"]))
    return paths


def _is_green(prop: dict) -> bool:
    """A prepared fix counts as green only on the drafting
    machinery's own evidence: the final draft attempt passed
    AND the recorded check run is green by og_github's rule
    (nothing failed; a timeout is never a pass)."""
    payload = prop.get("payload") or {}
    attempts = payload.get("attempts") or []
    if not attempts or not (attempts[-1] or {}).get("green"):
        return False
    try:
        import og_github as _gh
        return bool(_gh.checks_green(payload.get("test")))
    except Exception:
        return False


def _collect(operator: str) -> Tuple[List[dict], List[dict]]:
    """(eligible, skipped) over the operator's queue. Only the
    operator's own problems can carry code proposals at all;
    the sentinel key is adopted to the operator first."""
    import og_fixqueue as _fq
    import og_lessons as _lessons
    now = time.time()
    self_repo = os.getenv("OG_SELF_REPO", "").strip()
    eligible: List[dict] = []
    skipped: List[dict] = []
    for owner in _fq._owner_keys_for(operator):
        problems = _fq._problems(owner)
        for fp, prob in problems.items():
            if not isinstance(prob, dict):
                continue
            prop = prob.get("proposal")
            if not isinstance(prop, dict) \
                    or prop.get("status") != "open":
                continue
            title = str(prob.get("title") or "a fix")
            if prob.get("status") != "open":
                continue
            if now > float(prop.get("expires") or 0):
                continue  # the pipeline retires it on its own clock
            if prop.get("fix_kind") != "code":
                continue  # not this feature's kind — untouched
            payload = prop.get("payload") or {}
            repo = str(payload.get("repo") or self_repo).strip()
            if not self_repo or repo != self_repo:
                skipped.append({"title": title,
                                "why": "not OG's own repo"})
                continue
            if not _is_green(prop):
                skipped.append({"title": title,
                                "why": "draft is not green"})
                continue
            key = _fq._approach_key(prop)
            if key and _lessons.approach_failed(fp, key):
                skipped.append({
                    "title": title,
                    "why": "that approach already failed once "
                           "(lessons book)"})
                continue
            klass = classify_paths(_touched_paths(prop))
            if klass is not None:
                skipped.append({"title": title,
                                "why": f"touches {klass} — "
                                       f"needs a human yes"})
                continue
            eligible.append({
                "store_owner": owner, "fingerprint": fp,
                "proposal_id": prop.get("id"), "title": title,
                "created": float(prop.get("created") or 0),
                "paths": _touched_paths(prop)})
    eligible.sort(key=lambda c: c.get("created") or 0)
    return eligible, skipped


# --- Baselines + GitHub seams ----------------------------------------------------


def _failing_buttons(report: Optional[dict]) -> set:
    """The plain names of the buttons a self-check report
    shows failing (Round 50's census names)."""
    out = set()
    try:
        for f in ((report or {}).get("buttons") or {}).get("failed") or []:
            if isinstance(f, dict) and f.get("button"):
                out.add(str(f["button"]))
    except Exception:
        pass
    return out


def _failing_suites(battery: Optional[dict]) -> Optional[set]:
    """The names of the battery suites that did not pass, or
    None when the battery could not run at all — an unrun
    battery is never treated as a green one."""
    if not isinstance(battery, dict):
        return None
    suites = battery.get("suites")
    if not isinstance(suites, list) or not suites:
        return None
    return {str(s.get("name") or "?") for s in suites
            if isinstance(s, dict)
            and s.get("status") not in ("passed", "none")}


def _battery_failing(entry: dict, repo_full: str) -> Optional[set]:
    """One full battery over the repo's CURRENT default-branch
    tree (Round 60's merged-tree machinery, reused verbatim),
    as a failing-suite name set. None = could not run."""
    try:
        import og_codemore as _cm
        owner_login, repo_name = repo_full.split("/", 1)
        battery = _cm._battery_on_remote_tree(
            entry, {"owner": owner_login, "repo": repo_name,
                    "repo_full": repo_full})
        return _failing_suites(battery)
    except Exception as e:
        logger.warning(f"Autoupdate battery failed: {e}")
        return None


def _pr_number(pr_url: str) -> Optional[int]:
    m = re.search(r"/pull/(\d+)", str(pr_url or ""))
    return int(m.group(1)) if m else None


def _merge_pr(entry: dict, repo_full: str, number: int) -> bool:
    """The one step the human path leaves to a human: merging
    the PR the pipeline opened. True only on GitHub's own
    merged answer."""
    try:
        import og_github as _gh
        owner_login, repo_name = repo_full.split("/", 1)
        status, data = _gh._gh_request(
            "PUT",
            f"/repos/{owner_login}/{repo_name}/pulls/{number}/merge",
            str(entry.get("access_token") or ""),
            json_body={"merge_method": "merge"})
        return bool(status == 200 and isinstance(data, dict)
                    and data.get("merged"))
    except Exception as e:
        logger.warning(f"Autoupdate merge failed: {e}")
        return False


def _notify(operator: str, title: str, body: str):
    try:
        import og_notify as _notify
        _notify.record(operator, "auto_update", title, body,
                       target={"view": "system"})
    except Exception as e:
        logger.warning(f"Autoupdate notify failed: {e}")


def _released_updates_line(operator: str) -> str:
    """Round 71: the one plain line for tonight's owner
    notification — how many UPDATE proposals (the fix
    queue's 11pm hold, og_fixqueue) have been released and
    are still waiting on the owner's approval in Ideas.
    Each counted problem is stamped hold_announced in the
    queue, so a later notifying night never re-announces
    the same wait; a release on a night whose run sends no
    notification simply stays unannounced until a night
    that does. Returns "" when there is nothing to say.
    Never raises."""
    try:
        import og_fixqueue as _fq
        now = time.time()
        total = 0
        for owner in _fq._owner_keys_for(operator):
            problems = _fq._problems(owner)
            changed = False
            for prob in problems.values():
                if not isinstance(prob, dict):
                    continue
                if not _fq._is_update_problem(prob):
                    continue
                if prob.get("status") != "open":
                    continue
                prop = prob.get("proposal")
                if not isinstance(prop, dict) \
                        or prop.get("status") != "open":
                    continue
                if now > float(prop.get("expires") or 0):
                    continue
                hold = prob.get("hold_until")
                if not isinstance(hold, (int, float)) \
                        or now < float(hold):
                    continue  # not released yet
                if prob.get("hold_announced"):
                    continue  # an earlier night already said it
                prob["hold_announced"] = now
                changed = True
                total += 1
            if changed:
                _fq._save_problems(owner, problems)
        if total == 1:
            return ("1 update is waiting for your approval "
                    "in Ideas.")
        if total > 1:
            return (f"{total} updates are waiting for your "
                    f"approval in Ideas.")
    except Exception as e:
        logger.warning(f"Autoupdate updates line failed: {e}")
    return ""


# --- Stage 1: check + apply -------------------------------------------------------


def _apply_stage(night: str, stamp: bool = True) -> dict:
    """CHECK the queue, take baselines, then APPLY every
    eligible fix through the Round 53 door (decide with the
    nightly-auto stamp) and merge its PR. Durable
    pending_verify (baselines + what landed, with every
    file's before/after text) is persisted BEFORE the first
    merge — the deploy this triggers replaces this process,
    and the next one runs the tests from that record."""
    summary = {"night": night, "applied": [], "skipped": [],
               "blocked": ""}
    state = _state()
    if stamp:
        # Stamp BEFORE running: a crash or the deploy restart
        # can neither re-run this night nor double-apply.
        state["last_check_night"] = night
        _save_state(state)
    try:
        import og_fixqueue as _fq
        import og_selfcheck as _sc
        operator = _fq._operator_uid()
        if not operator:
            summary["blocked"] = "no operator resolved"
            _finish_apply(state, summary)
            return summary
        _fq._adopt_sentinel(operator)
        eligible, skipped = _collect(operator)
        summary["skipped"] = skipped
        if not eligible:
            _finish_apply(state, summary)
            return summary
        import og_github as _gh
        entry = _gh._github_connection(operator)
        if entry is None:
            summary["blocked"] = "GitHub is not connected"
            _finish_apply(state, summary)
            return summary
        repo_full = os.getenv("OG_SELF_REPO", "").strip()
        # Baselines BEFORE anything lands: parity is judged
        # against tonight, not against a standing assumption.
        base_report = _sc.latest_report()
        if base_report is None:
            try:
                base_report = _sc.run_check("auto_update_baseline")
            except Exception as e:
                logger.warning(f"Baseline check failed: {e}")
                base_report = None
        base_batt = _battery_failing(entry, repo_full)
        if base_batt is None:
            summary["blocked"] = (
                "the pre-update test battery could not run — "
                "nothing was applied (no baseline, no parity)")
            _finish_apply(state, summary)
            return summary
        pv = {"night": night, "operator": operator,
              "repo_full": repo_full, "started": time.time(),
              "merged_at": None,
              "baseline_buttons_failed": sorted(
                  _failing_buttons(base_report)),
              "baseline_battery_failed": sorted(base_batt),
              "items": []}
        state = _state()
        state["pending_verify"] = pv
        _save_state(state)
        auto = {"rule": RULE_TEXT, "night": night}
        for cand in eligible:
            try:
                ok, title, _body = _fq.decide(
                    operator, str(cand["proposal_id"]), "approve",
                    auto=auto)
            except Exception as e:
                summary["skipped"].append({
                    "title": cand["title"],
                    "why": f"the fix pipeline errored "
                           f"({type(e).__name__})"})
                continue
            problems = _fq._problems(cand["store_owner"])
            prob = problems.get(cand["fingerprint"]) or {}
            prop = prob.get("proposal") or {}
            pr_url = str(prop.get("pr_url") or "")
            if not pr_url:
                summary["skipped"].append({
                    "title": cand["title"],
                    "why": f"not shipped ({title})"})
                continue
            number = _pr_number(pr_url)
            if number is None or not _merge_pr(
                    entry, repo_full, number):
                summary["skipped"].append({
                    "title": cand["title"],
                    "why": "the PR opened but the merge did "
                           "not land — left for the owner",
                    "pr_url": pr_url})
                continue
            payload = prop.get("payload") or {}
            files = []
            multi = [f for f in (payload.get("files") or [])
                     if isinstance(f, dict) and f.get("path")]
            if len(multi) > 1:
                for f in multi:
                    files.append({
                        "path": str(f["path"]),
                        "base_text": str(f.get("base_text") or ""),
                        "content": str(f.get("content") or "")})
            else:
                files.append({
                    "path": str(payload.get("path") or ""),
                    "base_text": str(payload.get("base_text")
                                     or ""),
                    "content": str(payload.get("content")
                                   or "")})
            item = {"store_owner": cand["store_owner"],
                    "fingerprint": cand["fingerprint"],
                    "title": cand["title"], "pr_url": pr_url,
                    "pr_number": number, "files": files,
                    "approach_key": _fq._approach_key(prop)}
            pv["items"].append(item)
            pv["merged_at"] = time.time()
            state = _state()
            state["pending_verify"] = pv
            _save_state(state)
            summary["applied"].append(cand["title"])
        if not pv["items"]:
            state = _state()
            state["pending_verify"] = None
            _save_state(state)
    except Exception as e:
        logger.warning(f"Autoupdate apply stage failed: {e}")
        summary["blocked"] = summary["blocked"] or \
            f"apply stage errored ({type(e).__name__})"
    state = _state()
    _finish_apply(state, summary)
    return summary


def _finish_apply(state: dict, summary: dict):
    state["last_run"] = {"ts": time.time(), "stage": "apply",
                         "night": summary.get("night"),
                         "applied": summary.get("applied") or [],
                         "skipped": summary.get("skipped") or [],
                         "blocked": summary.get("blocked") or ""}
    detail = (f"night {summary.get('night')}: applied "
              f"{len(summary.get('applied') or [])} "
              f"({', '.join(summary.get('applied') or []) or 'none'}); "
              f"skipped {len(summary.get('skipped') or [])}")
    if summary.get("blocked"):
        detail += f"; blocked: {summary['blocked']}"
    _history(state, "apply", detail)
    _save_state(state)


# --- Stage 2: test what landed -----------------------------------------------------


def _verify_stage() -> dict:
    """TEST the applied batch against tonight's baselines:
    the home page answers, a fresh Round 50 self-check shows
    no NEW failing button, and the full test battery on the
    merged tree shows no NEW failing suite. A test that
    cannot run counts as a failure — unproven health takes
    the safe direction (rollback)."""
    state = _state()
    pv = state.get("pending_verify")
    if not isinstance(pv, dict) or not pv.get("items"):
        return {"ran": False}
    failures: List[str] = []
    try:
        import og_selfcheck as _sc
        status, _ = _sc._probe_http("GET", "/")
    except Exception:
        status = 0
    if status != 200:
        failures.append(f"Home page (answered {status})")
    report = None
    try:
        import og_selfcheck as _sc
        report = _sc.run_check("auto_update_verify")
    except Exception as e:
        failures.append(
            f"System check (could not run: {type(e).__name__})")
    if report is not None:
        base = set(pv.get("baseline_buttons_failed") or [])
        for name in sorted(_failing_buttons(report) - base):
            failures.append(f"{name} (button check)")
    entry = None
    try:
        import og_github as _gh
        entry = _gh._github_connection(str(pv.get("operator")
                                           or ""))
    except Exception:
        entry = None
    batt = _battery_failing(entry, str(pv.get("repo_full") or "")) \
        if entry is not None else None
    if batt is None:
        failures.append("Test battery (could not run)")
    else:
        base_b = set(pv.get("baseline_battery_failed") or [])
        for name in sorted(batt - base_b):
            failures.append(f"Test battery: {name}")
    if not failures:
        titles = [str(it.get("title") or "a fix")
                  for it in pv["items"]]
        lines = ["Nightly auto-update — everything is still "
                 "running.",
                 f"Applied to OG's own system "
                 f"({pv.get('night')}):"]
        for it in pv["items"]:
            lines.append(f"- {it.get('title')} "
                         f"({it.get('pr_url')})")
        lines.append("Post-update tests: home page OK, the "
                     "system check shows no new failing "
                     "buttons, and the full test battery "
                     "matches the pre-update run — no new "
                     "failures.")
        extra = _released_updates_line(
            str(pv.get("operator") or ""))
        if extra:
            lines.append(extra)
        state = _state()
        state["pending_verify"] = None
        state["consec_rollbacks"] = 0
        state["last_run"] = {
            "ts": time.time(), "stage": "verify",
            "night": pv.get("night"), "result": "passed",
            "applied": titles}
        _history(state, "verify_passed",
                 f"night {pv.get('night')}: "
                 f"{len(titles)} update(s) verified — "
                 + ", ".join(titles))
        _save_state(state)
        _notify(str(pv.get("operator") or ""),
                f"Auto-update: {len(titles)} fix(es) applied, "
                f"all tests passing",
                "\n".join(lines))
        return {"ran": True, "passed": True}
    return _rollback_stage(pv, failures)


# --- Stage 3: roll back what failed its tests --------------------------------------


def _revert_item(operator: str, repo_full: str, item: dict) \
        -> Tuple[bool, List[str]]:
    """Undo one applied fix through the same machinery that
    shipped it: an inverse PR (every file restored to its
    pre-update text) via og_github._create_pr, merged the
    same way. A file that has moved AGAIN since the update
    is never overwritten — it is skipped and named."""
    notes: List[str] = []
    try:
        import og_github as _gh
        entry = _gh._github_connection(operator)
        if entry is None:
            return False, ["GitHub is not connected — the "
                           "revert could not run"]
        token = str(entry.get("access_token") or "")
        owner_login, repo_name = repo_full.split("/", 1)
        status, data = _gh._gh_request(
            "GET", f"/repos/{owner_login}/{repo_name}", token)
        if status != 200 or not isinstance(data, dict):
            return False, ["the repo could not be re-checked "
                           "— the revert could not run"]
        default_branch = str(data.get("default_branch")
                             or "main")
        usable = []
        for f in item.get("files") or []:
            path = str(f.get("path") or "")
            st, info = _gh._fetch_file(
                token, owner_login, repo_name, path,
                default_branch)
            if info is None:
                notes.append(f"{path}: could not be read back "
                             f"— NOT reverted")
                continue
            if info.get("text") != f.get("content"):
                notes.append(f"{path}: changed again since "
                             f"the update — left untouched")
                continue
            usable.append({"path": path,
                           "current_text": info.get("text")
                           or "",
                           "current_sha": str(info.get("sha")
                                              or ""),
                           "restore": str(f.get("base_text")
                                          or "")})
        if not usable:
            return False, notes
        primary = usable[0]
        branch = "og/revert-" + re.sub(
            r"[^a-z0-9]+", "-",
            str(item.get("title") or "auto-update").lower()
        ).strip("-")[:40]
        pending = {
            "repo_full": repo_full, "owner": owner_login,
            "repo": repo_name, "branch": branch, "path_hint": "",
            "thing": f"Revert nightly auto-update: "
                     f"{item.get('title')}",
            "mode": "edit", "path": primary["path"],
            "base_sha": primary["current_sha"],
            "base_text": primary["current_text"],
            "default_branch": default_branch,
            "stage": "verified",
            "pkg": {"path": primary["path"],
                    "content": primary["restore"],
                    "diff": None, "test": None,
                    "attempts": [], "lessons": []},
            "py_paths": [], "tree_paths": [],
        }
        extracted = {"path": primary["path"],
                     "content": primary["restore"]}
        if len(usable) > 1:
            pending["files"] = [{
                "path": u["path"],
                "base_text": u["current_text"],
                "base_sha": u["current_sha"],
            } for u in usable]
            pending["pkg"]["files"] = [{
                "path": u["path"], "content": u["restore"],
                "diff": None,
            } for u in usable]
            extracted["files"] = [{
                "path": u["path"], "content": u["restore"],
            } for u in usable]
        results = _gh._create_pr(entry, pending, extracted,
                                 operator)
        pr_url = ""
        for r in results or []:
            if "PR opened" in str(r.get("title", "")):
                m = re.search(r"https://github\.com/\S+/pull/\d+",
                              str(r.get("body", "")))
                pr_url = m.group(0) if m else ""
                break
        if not pr_url:
            notes.append("the revert PR did not open")
            return False, notes
        number = _pr_number(pr_url)
        if number is None or not _merge_pr(entry, repo_full,
                                           number):
            notes.append(f"the revert PR opened ({pr_url}) "
                         f"but its merge did not land")
            return False, notes
        return True, notes
    except Exception as e:
        logger.warning(f"Autoupdate revert failed: {e}")
        return False, notes + [f"revert errored "
                                f"({type(e).__name__})"]


def _rollback_stage(pv: dict, failures: List[str]) -> dict:
    """Roll the batch back, write the lessons (the approach
    may never be auto-retried), count the bad night, maybe
    disable, and tell the owner — once, naming the failed
    checks plainly."""
    why = "; ".join(failures)
    operator = str(pv.get("operator") or "")
    repo_full = str(pv.get("repo_full") or "")
    reverted: List[str] = []
    notes: List[str] = []
    import og_fixqueue as _fq
    for item in reversed(pv.get("items") or []):
        ok, item_notes = _revert_item(operator, repo_full, item)
        notes.extend(f"{item.get('title')}: {n}"
                     for n in item_notes)
        if ok:
            reverted.append(str(item.get("title") or "a fix"))
        # The lesson lands regardless of how the revert went:
        # this approach failed its post-update verification.
        try:
            problems = _fq._problems(
                str(item.get("store_owner") or ""))
            prob = problems.get(str(item.get("fingerprint")
                                    or ""))
            if prob:
                _fq._lesson_fix_failed(
                    prob, prob.get("proposal") or {},
                    f"the nightly auto-update's post-update "
                    f"tests failed ({why}) and the change was "
                    f"rolled back")
        except Exception as e:
            logger.warning(f"Rollback lesson failed: {e}")
    state = _state()
    consec = int(state.get("consec_rollbacks") or 0) + 1
    state["consec_rollbacks"] = consec
    disabled_now = consec >= DISABLE_AFTER_ROLLBACKS
    if disabled_now:
        state["disabled"] = True
        state["disabled_reason"] = (
            f"{consec} nights in a row ended in a rollback — "
            f"auto-update is off until the owner turns it "
            f"back on")
        state["disabled_at"] = time.time()
    state["pending_verify"] = None
    state["pending_rollback_check"] = {
        "at": time.time(), "why": why,
        "titles": [str(it.get("title") or "a fix")
                   for it in pv.get("items") or []]}
    state["last_run"] = {
        "ts": time.time(), "stage": "rollback",
        "night": pv.get("night"), "result": "rolled_back",
        "failures": failures, "reverted": reverted}
    _history(state, "rollback",
             f"night {pv.get('night')}: rolled back "
             f"{len(reverted)} — {why}")
    _save_state(state)
    lines = ["Nightly auto-update — a fix failed its "
             "post-update tests and was rolled back.",
             f"What was rolled back: "
             f"{', '.join(reverted) or 'nothing merged'}."]
    lines.append(f"Why: the post-update tests failed — {why}.")
    for n in notes:
        lines.append(f"- {n}")
    lines.append("The approach is in OG's lessons book as "
                 "failed — he will not auto-apply it again.")
    if disabled_now:
        lines.append("That's two bad nights in a row, so "
                     "auto-update is now OFF until you turn "
                     "it back on (POST "
                     "/system/autoupdate/reenable).")
    extra = _released_updates_line(operator)
    if extra:
        lines.append(extra)
    _notify(operator,
            "Auto-update rolled back: "
            + (", ".join(reverted) or "update"),
            "\n".join(lines))
    return {"ran": True, "passed": False,
            "reverted": reverted, "failures": failures}


def _rollback_check_stage() -> dict:
    """After a rollback's own deploy lands: one light health
    look. Still unhealthy -> the owner hears that too;
    either way this check runs exactly once."""
    state = _state()
    prc = state.get("pending_rollback_check")
    if not isinstance(prc, dict):
        return {"ran": False}
    state["pending_rollback_check"] = None
    _save_state(state)
    failures: List[str] = []
    try:
        import og_selfcheck as _sc
        status, _ = _sc._probe_http("GET", "/")
        if status != 200:
            failures.append(f"Home page (answered {status})")
        report = _sc.run_check("auto_update_rollback_check")
        for name in sorted(_failing_buttons(report)):
            failures.append(f"{name} (button check)")
    except Exception as e:
        failures.append(f"the check could not run "
                        f"({type(e).__name__})")
    state = _state()
    if failures:
        _history(state, "rollback_check",
                 "still unhealthy after rollback: "
                 + "; ".join(failures))
        _save_state(state)
        # The operator uid rides the most recent run's record;
        # fall back to resolving it fresh.
        operator = ""
        try:
            import og_fixqueue as _fq
            operator = _fq._operator_uid() or ""
        except Exception:
            operator = ""
        if operator:
            _notify(operator,
                    "Auto-update: still not healthy after "
                    "the rollback",
                    "OG rolled last night's update back, and "
                    "the re-check after that deploy still "
                    "shows problems: " + "; ".join(failures)
                    + ". This one needs your eyes — nothing "
                      "else will be auto-applied until you "
                      "look.")
    else:
        _history(state, "rollback_check",
                 "healthy after rollback")
        _save_state(state)
    return {"ran": True, "healthy": not failures}


# --- Orchestrator + operator surface ----------------------------------------------


def tick() -> str:
    """One scheduler heartbeat. Pending stages first (they
    finish what an earlier process started — the deploy
    restart is part of the design), then the night window.
    Returns a short status word; never raises."""
    try:
        state = _state()
        now = time.time()
        prc = state.get("pending_rollback_check")
        if isinstance(prc, dict) and \
                now - float(prc.get("at") or 0) \
                >= DEPLOY_WAIT_SECONDS:
            _rollback_check_stage()
            return "rollback_checked"
        pv = state.get("pending_verify")
        if isinstance(pv, dict) and pv.get("items"):
            if pv.get("merged_at") and \
                    now - float(pv["merged_at"]) \
                    >= DEPLOY_WAIT_SECONDS:
                _verify_stage()
                return "verified"
            return "verifying"
        if isinstance(pv, dict) and not pv.get("items"):
            # An apply stage that died before its first merge
            # landed nothing; once it is older than the deploy
            # window it is closed out, never verified.
            if now - float(pv.get("started") or 0) \
                    >= DEPLOY_WAIT_SECONDS:
                state = _state()
                state["pending_verify"] = None
                _history(state, "apply",
                         "an interrupted apply stage landed "
                         "nothing and was closed out")
                _save_state(state)
            return "pending"
        if not enabled():
            return "gate_off"
        if state.get("disabled"):
            return "disabled"
        night = _night_for(_owner_now())
        if night is None:
            return "outside_window"
        if state.get("last_check_night") == night:
            return "already_checked"
        _apply_stage(night)
        return "applied_stage"
    except Exception as e:
        logger.warning(f"Autoupdate tick failed: {e}")
        return "error"


def state_view() -> dict:
    state = _state()
    _, tz_label = _owner_tz()
    pv = state.get("pending_verify")
    pv_view = None
    if isinstance(pv, dict):
        pv_view = {"night": pv.get("night"),
                   "items": [str(it.get("title") or "a fix")
                             for it in pv.get("items") or []],
                   "merged_at": pv.get("merged_at")}
    return {
        "ok": True,
        "enabled": enabled(),
        "gate_env": GATE_ENV,
        "disabled": bool(state.get("disabled")),
        "disabled_reason": state.get("disabled_reason") or "",
        "consec_rollbacks": int(state.get("consec_rollbacks")
                                 or 0),
        "disable_after_rollbacks": DISABLE_AFTER_ROLLBACKS,
        "last_check_night": state.get("last_check_night"),
        "last_run": state.get("last_run"),
        "pending_verify": pv_view,
        "window": "23:00 owner time, nightly",
        "timezone": tz_label,
        "rule": RULE_TEXT,
        "history": (state.get("history") or [])[-10:],
    }


def reenable() -> dict:
    state = _state()
    state["disabled"] = False
    state["disabled_reason"] = ""
    state["consec_rollbacks"] = 0
    _history(state, "reenable",
             "the owner turned auto-update back on")
    _save_state(state)
    return state_view()


def _nightly_check_hook() -> None:
    """Round 72 seam: wake the self-check battery's one
    nightly run (the activation diet moved it into this
    window). The entry is stamp-guarded (exactly once per
    night) and fail-safe; this wrapper only guards the
    import itself."""
    try:
        import og_selfcheck as _sc
        _sc.run_nightly_if_due()
    except Exception as e:
        logger.warning(f"Nightly self-check hook failed: {e}")


def register_autoupdate_routes(app):

    @app.get("/system/autoupdate")
    async def system_autoupdate(key: str = ""):
        import og_selfcheck as _sc
        if not _sc._key_ok(key):
            return JSONResponse({"detail": "Owner key required"},
                                status_code=401)
        return state_view()

    @app.post("/system/autoupdate/reenable")
    async def system_autoupdate_reenable(key: str = ""):
        import og_selfcheck as _sc
        if not _sc._key_ok(key):
            return JSONResponse({"detail": "Owner key required"},
                                status_code=401)
        return reenable()

    @app.post("/system/autoupdate/run")
    async def system_autoupdate_run(key: str = ""):
        """The owner's manual trigger (/system/check posture):
        run the stage machine NOW — pending stages first,
        then a fresh check+apply that ignores the clock but
        still respects the gate and the disabled state."""
        import og_selfcheck as _sc
        if not _sc._key_ok(key):
            return JSONResponse({"detail": "Owner key required"},
                                status_code=401)
        if not enabled():
            return {"ok": False,
                    "detail": f"{GATE_ENV} is off — the "
                              f"nightly auto-update is not "
                              f"enabled."}
        state = _state()
        if isinstance(state.get("pending_verify"), dict) \
                and state["pending_verify"].get("items"):
            return {"ok": True, "status": tick(),
                    **state_view()}
        if state.get("disabled"):
            return {"ok": False,
                    "detail": "Auto-update switched itself "
                              "off after repeated rollbacks — "
                              "POST /system/autoupdate/reenable "
                              "first."}
        label = "manual-" + _owner_now().strftime("%Y%m%d-%H%M")
        summary = await asyncio.to_thread(_apply_stage, label,
                                          False)
        return {"ok": True, "summary": summary, **state_view()}

    @app.on_event("startup")
    async def _start_autoupdate_loop():
        if _loop_started["on"]:
            return
        _loop_started["on"] = True

        async def _loop():
            while True:
                await asyncio.sleep(max(30, POLL_SECONDS))
                try:
                    # Round 72: the self-check battery runs
                    # in this window now — sequenced BEFORE
                    # the tick so tonight's findings feed
                    # tonight's collect.
                    await asyncio.to_thread(_nightly_check_hook)
                    await asyncio.to_thread(tick)
                except Exception as e:
                    logger.warning(f"Autoupdate cycle failed: {e}")

        asyncio.create_task(_loop())
