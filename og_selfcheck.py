"""System self-check for OG (Round 50; Brent's order, 2026-10-10):
"Have OG auto check system two times a day, make sure everything
run good, he will check all of his system."

WHAT THIS IS. Twice a day (a background loop wakes hourly and runs
the check when the durable store says >=12h since the last one),
OG examines his own systems and files ONE report:

1. BUTTONS. A curated census maps the controls a visitor can see
   to the route behind each one, and every route is probed
   (loopback, signed out, read-only). A failure is reported by
   the BUTTON's plain name — "the Upload button" — never just a
   path. POST routes are probed only while the sign-in gate is on:
   the gate answers 401 before any handler runs, so a probe can
   neither spend money nor mutate anything. Gate off, POST probes
   are reported skipped, never sent.

2. UPDATES. The repo's pinned requirements are read and each
   package's latest release is fetched from PyPI (fail-soft per
   package). Newer-than-pinned is REPORTED only — nothing here
   ever upgrades anything on its own.

3. OVERLOAD. Only what is honestly measurable from inside the
   process is measured: locker bytes (total + the fullest
   visitor against their tier quota), the durable store's size
   and growth since the previous check, process memory against
   the 2 GB instance, Steel session counts (read-only list; the
   credit balance has no read OG can make and is reported NOT
   measurable rather than invented), and the operator's daily
   burn (free-token meter + browser minutes vs caps). >=85% of a
   real limit reads "near limit"; >=100% reads "overloaded —
   needs upgrade" and carries an upgrade offer.

THE UPGRADE OFFERS. Each needs-upgrade finding carries a stable
offer id and its steps, split: (a) steps OG can execute
server-side, (b) the owner's taps (dashboard / payment), written
as plain numbered instructions. In chat, "approve the upgrade
for <name>" — from the OPERATOR's account only — runs part (a)
through this module's executor registry and returns part (b) as
numbered steps. Without that approval nothing executes. This
round's registry is deliberately small: plan and billing
upgrades have NO server-side steps (they are dashboard
decisions), and the module says so per finding instead of
pretending otherwise; the one real executor is housekeeping for
OG's own report store (prune to the newest 14).

THE REPORT. One record per run in this module's durable store
(same pattern as og_notify: Postgres table og_selfcheck_data
when OG_MEMORY_DB_URL is set, else a JSON file), newest 14 kept.
One notification (kind "system_check", alerts-gated like the
Round 38 kinds) goes to the operator account resolved from
OG_OWNER_EMAIL; unset or unresolvable means store + owner route
only — a recipient is never guessed. GET /system/check?key=
<OG_STATS_TOKEN> returns the latest report (401 without the
key, the /stats posture); POST with the key runs a check now.

COST POSTURE. The check makes NO paid calls, creates NO Steel
session, and calls NO chat model. Probes are loopback HTTP; the
only outbound calls are the free PyPI JSON reads.

GATE. OG_SELFCHECK_ENABLED: unset/anything-but-off = ON (the
owner ordered this on); 0/false/no/off disables the scheduler
(the routes and the chat seam stay live).
"""

import asyncio
import hmac
import json
import logging
import os
import platform
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

STORE_FILE = os.getenv("OG_SELFCHECK_STORE", "selfcheck_store.json")
RUN_EVERY_SECONDS = 12 * 60 * 60
POLL_SECONDS = int(os.getenv("OG_SELFCHECK_POLL_SECONDS", "3600") or "3600")
MAX_REPORTS = 14
INSTANCE_MEMORY_MB = 2048.0
NEAR_PCT = 85.0

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_store_lock = threading.Lock()
_deps: Dict = {}
_loop_started = {"on": False}


def bind_app(deps):
    _deps.update(deps)


def enabled() -> bool:
    v = os.getenv("OG_SELFCHECK_ENABLED", "").strip().lower()
    return v not in ("0", "false", "no", "off")


# --- Durable store (og_notify pattern: one key -> JSON blob) -------------------


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_selfcheck_data ("
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
            logger.warning(f"Selfcheck store load failed: {e}")
    return {}


def _save_file_store(store: dict):
    try:
        with open(STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Selfcheck store save failed: {e}")


def _get(key: str):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_selfcheck_data WHERE key=%s",
                        (key,))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning(f"Selfcheck DB load failed, using file: {e}")
    with _store_lock:
        store = _load_file_store()
    return store.get(key)


def _put(key: str, value):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_selfcheck_data (key, data) "
                        "VALUES (%s, %s) ON CONFLICT (key) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (key, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Selfcheck DB save failed, using file: {e}")
    with _store_lock:
        store = _load_file_store()
        store[key] = value
        _save_file_store(store)


def _state() -> dict:
    st = _get("state")
    return st if isinstance(st, dict) else {}


def _reports() -> list:
    reps = _get("reports")
    return reps if isinstance(reps, list) else []


def latest_report() -> Optional[dict]:
    reps = _reports()
    return reps[-1] if reps else None


# --- Operator -------------------------------------------------------------------


def _operator_uid() -> Optional[str]:
    """The operator account's uid, resolved from OG_OWNER_EMAIL
    through og_accounts' email -> account record. None when the
    env is unset or the email has no account — never guessed."""
    email = os.getenv("OG_OWNER_EMAIL", "").strip().lower()
    if not email:
        return None
    try:
        import og_accounts as _acct
        acct = _acct._get("acct:" + email)
        if isinstance(acct, dict) and acct.get("uid"):
            return str(acct["uid"])
    except Exception as e:
        logger.warning(f"Selfcheck operator resolve failed: {e}")
    return None


# --- Buttons job ------------------------------------------------------------------

# (button plain name, method, path, probe kind)
#   public200  — a public GET; healthy is 200.
#   gated      — a signed-in GET; healthy is the gate's 401 while
#                the gate is on, or 200 while it is off.
#   post_gated — a POST behind the gate; probed ONLY while the
#                gate is on (healthy = the gate's 401; no handler
#                ever runs). Gate off -> skipped, never sent.
BUTTON_CENSUS = [
    ("Send button", "POST", "/chat", "post_gated"),
    ("Voice reply button", "POST", "/tts", "post_gated"),
    ("Microphone button", "POST", "/transcribe", "post_gated"),
    ("Image button", "POST", "/image", "post_gated"),
    ("Upload button", "POST", "/upload", "post_gated"),
    ("Report button", "POST", "/report", "post_gated"),
    ("Browser panel", "GET", "/browser/status", "gated"),
    ("Browser approve button", "POST", "/browser/approve", "post_gated"),
    ("Google connect", "GET", "/auth/google/status", "gated"),
    ("GitHub connect", "GET", "/auth/github/status", "gated"),
    ("Spotify connect", "GET", "/auth/spotify/status", "gated"),
    ("YouTube connect", "GET", "/auth/youtube/status", "gated"),
    ("Discord connect", "GET", "/auth/discord/status", "gated"),
    ("Twitch connect", "GET", "/auth/twitch/status", "gated"),
    ("Reddit connect", "GET", "/auth/reddit/status", "gated"),
    ("Watch button", "GET", "/watch/status", "gated"),
    ("Notifications bell", "GET", "/notifications", "gated"),
    ("Library button", "GET", "/library", "gated"),
    ("Storage locker", "GET", "/storage/status", "gated"),
    ("Song status", "GET", "/song/status", "gated"),
    ("Video status", "GET", "/video/status", "gated"),
    ("Upgrade page", "GET", "/pro", "public200"),
    ("Sign-in", "GET", "/auth/me", "public200"),
    ("Health check", "GET", "/health", "public200"),
]


def _probe_base() -> str:
    base = os.getenv("OG_SELFCHECK_PROBE_BASE", "").strip()
    if base:
        return base.rstrip("/")
    return f"http://127.0.0.1:{os.getenv('PORT', '10000')}"


def _probe_http(method: str, path: str):
    """One signed-out loopback probe -> (status, body text).
    The suite stubs this seam; production probes never carry a
    cookie, so the gate's answer IS the posture being measured."""
    url = _probe_base() + path
    data = b"{}" if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception:
            return e.code, ""
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def _gate_on() -> bool:
    try:
        import og_accounts as _acct
        return bool(_acct.gate_enabled())
    except Exception:
        return False


def _check_buttons() -> dict:
    gate = _gate_on()
    ok, failed, skipped = 0, [], []
    for name, method, path, kind in BUTTON_CENSUS:
        route = f"{method} {path}"
        try:
            if kind == "post_gated" and not gate:
                skipped.append({"button": name, "route": route,
                                "why": "sign-in gate off — POST not "
                                       "probed (would run the handler)"})
                continue
            status, body = _probe_http(method, path)
            if kind == "public200":
                good = status == 200
            elif gate:
                good = status == 401 and "sign_in_required" in body
            else:
                good = status == 200
            if good:
                ok += 1
            else:
                failed.append({
                    "button": name, "route": route,
                    "observed": f"HTTP {status}" if status else
                                f"no answer ({body[:80]})"})
        except Exception as e:  # a probe exception = that probe failed
            failed.append({"button": name, "route": route,
                           "observed": f"probe error: {type(e).__name__}: {e}"})
    return {"checked": ok + len(failed), "ok": ok,
            "failed": failed, "skipped": skipped}


# --- Updates job ------------------------------------------------------------------


def _ver_key(v: str):
    parts = []
    for piece in str(v).split("."):
        m = re.match(r"\d+", piece)
        parts.append(int(m.group(0)) if m else 0)
    return tuple(parts)


def _pinned_requirements() -> list:
    """[(package, pinned floor)] from the requirements.txt that
    sits beside this module. Unpinned lines are skipped."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "requirements.txt")
    out = []
    try:
        with open(path) as f:
            lines = f.readlines()
    except Exception as e:
        logger.warning(f"Selfcheck requirements read failed: {e}")
        return out
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?\s*(.*)$", line)
        if not m:
            continue
        name, _, spec = m.groups()
        vm = re.search(r"(\d+(?:\.\d+)*)", spec or "")
        if vm:
            out.append((name, vm.group(1)))
    return out


def _fetch_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "og-selfcheck"})
    with urllib.request.urlopen(req, timeout=4) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _pypi_latest(pkg: str) -> Optional[str]:
    try:
        data = _fetch_json(f"https://pypi.org/pypi/{pkg}/json")
        v = ((data or {}).get("info") or {}).get("version")
        return str(v) if v else None
    except Exception:
        return None


def _runtime_pin() -> Optional[str]:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "runtime.txt")
    try:
        with open(path) as f:
            text = f.read()
        m = re.search(r"(\d+\.\d+(?:\.\d+)?)", text)
        return m.group(1) if m else None
    except Exception:
        return None


def _check_updates() -> dict:
    updates, unknown = [], 0
    for pkg, pinned in _pinned_requirements():
        latest = _pypi_latest(pkg)
        if latest is None:
            unknown += 1
            continue
        if _ver_key(latest) > _ver_key(pinned):
            updates.append({"package": pkg, "pinned": pinned,
                            "latest": latest})
    return {"updates": updates, "unreachable": unknown,
            "python": {"running": platform.python_version(),
                       "pin": _runtime_pin()}}


# --- Overload job -------------------------------------------------------------------


def _level(pct: Optional[float]) -> str:
    if pct is None:
        return "info"
    if pct >= 100.0:
        return "over"
    if pct >= NEAR_PCT:
        return "near"
    return "ok"


def _process_memory_mb() -> Optional[float]:
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:
        return None


def _db_size_bytes() -> Optional[int]:
    if not (MEMORY_DB_URL and psycopg is not None):
        return None
    try:
        with _db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_database_size(current_database())")
                row = cur.fetchone()
        return int(row[0]) if row else None
    except Exception:
        return None


def _usage_store_bytes() -> Optional[int]:
    try:
        path = os.getenv("OG_USAGE_STORE_FILE", "usage_store.json")
        if os.path.exists(path):
            return os.path.getsize(path)
    except Exception:
        pass
    return None


def _locker_stats() -> dict:
    """Aggregate locker bytes + the fullest visitor vs quota,
    through og_storage's own records. Fail-soft to {}."""
    try:
        import og_storage as _storage
        import og_tiers as _tiers
        records = _storage._all_records()
        total = 0
        per_uid: Dict[str, int] = {}
        for rec in records:
            size = int(rec.get("size") or 0)
            total += size
            uid = str(rec.get("uid") or "")
            if uid:
                per_uid[uid] = per_uid.get(uid, 0) + size
        fullest = None
        for uid, used in per_uid.items():
            tier = _tiers.entitlement_tier_for(uid) or "free"
            quota = _tiers.storage_bytes(tier)
            pct = (used / quota * 100.0) if quota else (100.0 if used else 0.0)
            if fullest is None or pct > fullest["pct"]:
                fullest = {"used": used, "quota": quota, "pct": pct}
        return {"total_bytes": total, "visitors": len(per_uid),
                "fullest": fullest}
    except Exception as e:
        logger.warning(f"Selfcheck locker stats failed: {e}")
        return {}


def _steel_sessions() -> dict:
    """Read-only Steel session list count — NO session creates.
    Credits have no read in the API surface OG uses: reported
    not-measurable by the caller, never invented."""
    try:
        import og_browser as _browser
        if not _browser.enabled():
            return {"note": "browser dark"}
        data = _browser._steel_api("GET", "/sessions")
        if isinstance(data, dict):
            items = data.get("sessions") or data.get("data") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []
        return {"sessions_listed": len(items)}
    except Exception as e:
        return {"note": f"unreachable: {type(e).__name__}"}


def _owner_burn(operator_uid: Optional[str]) -> dict:
    """The operator's day so far: the free-token meter (global,
    the /stats counter) + the operator's browser minutes vs cap.
    Read through the bound usage store; degrades to {}."""
    out: Dict = {}
    load = _deps.get("load_usage")
    lock = _deps.get("usage_lock")
    if load is None:
        return out
    try:
        import og_tiers as _tiers
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if lock is not None:
            with lock:
                store = load()
        else:
            store = load()
        stats = store.get("__stats__") or {}
        day = ((stats.get("days") or {}).get(today) or {})
        out["free_tokens_today"] = int(day.get("tokens", 0) or 0)
        out["free_token_cap"] = int(os.getenv(
            "OG_FREE_DAILY_TOKENS",
            os.getenv("OG_FREE_DAILY_LIMIT", "25000")))
        if operator_uid:
            tier = _tiers.entitlement_tier_for(operator_uid) or "free"
            out["tier"] = tier
            entry = store.get(f"browser:{operator_uid}") or {}
            if isinstance(entry, dict) and entry.get("date") == today:
                used = int(entry.get("minutes", 0) or 0)
            else:
                used = 0
            out["browser_minutes_today"] = used
            out["browser_minutes_cap"] = int(_tiers.cap(tier, "browser_min"))
    except Exception as e:
        logger.warning(f"Selfcheck owner burn failed: {e}")
    return out


def _upgrade(name: str, what: str, owner_steps: List[str],
             server_steps: Optional[List[dict]] = None) -> dict:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return {"id": f"upgrade:{slug}", "name": name, "what": what,
            "server_steps": server_steps or [],
            "owner_steps": owner_steps}


def _check_overload(prev_sizes: dict, operator_uid) -> dict:
    measures, not_measurable, upgrades = [], [], []

    mem = _process_memory_mb()
    if mem is None:
        not_measurable.append("Process memory (rusage unavailable)")
    else:
        pct = mem / INSTANCE_MEMORY_MB * 100.0
        lvl = _level(pct)
        measures.append({
            "name": "Process memory", "detail":
                f"{mem:.0f} MB of the {INSTANCE_MEMORY_MB:.0f} MB "
                f"instance ({pct:.0f}%)", "pct": round(pct, 1),
            "level": lvl})
        if lvl == "over":
            upgrades.append(_upgrade(
                "Process memory",
                "The server process is at/over the instance's "
                "2 GB of memory.",
                ["Open the Render dashboard and go to the "
                 "og-ai-service service.",
                 "Open Settings and find the instance type.",
                 "Pick the next size up (more memory) and save — "
                 "Render redeploys on the new size."]))

    locker = _locker_stats()
    if not locker:
        not_measurable.append("Locker storage (storage module unreadable)")
    else:
        measures.append({
            "name": "Locker storage", "detail":
                f"{locker.get('total_bytes', 0)} bytes across "
                f"{locker.get('visitors', 0)} visitor(s)",
            "pct": None, "level": "info"})
        fullest = locker.get("fullest")
        if fullest:
            pct = float(fullest["pct"])
            lvl = _level(pct)
            measures.append({
                "name": "Fullest locker", "detail":
                    f"one visitor holds {fullest['used']} of their "
                    f"{fullest['quota']} byte quota ({pct:.0f}%)",
                "pct": round(pct, 1), "level": lvl})
            if lvl == "over":
                upgrades.append(_upgrade(
                    "Locker storage",
                    "A visitor's locker is at/over its tier quota.",
                    ["Decide the new quota for that tier.",
                     "In the Render dashboard, open og-ai-service "
                      "→ Environment.",
                     "Set OG_STORAGE_GB_<TIER> to the new size "
                      "and save — the service restarts with it."]))

    sizes: Dict[str, int] = {}
    db_size = _db_size_bytes()
    if db_size is not None:
        sizes["database_bytes"] = db_size
    file_size = _usage_store_bytes()
    if file_size is not None:
        sizes["usage_file_bytes"] = file_size
    rep_blob = 0
    try:
        rep_blob = len(json.dumps(_reports()))
    except Exception:
        pass
    sizes["report_store_bytes"] = rep_blob
    for key, label in (("database_bytes", "Database"),
                       ("usage_file_bytes", "Usage store file")):
        if key in sizes:
            now_b = sizes[key]
            prev_b = prev_sizes.get(key)
            detail = f"{now_b} bytes"
            if isinstance(prev_b, int) and prev_b > 0:
                growth = (now_b - prev_b) / prev_b * 100.0
                detail += f" ({growth:+.0f}% since the last check)"
            measures.append({"name": label, "detail": detail,
                             "pct": None, "level": "info"})
    if rep_blob > 1024 * 1024:
        measures.append({
            "name": "Self-check report store", "detail":
                f"{rep_blob} bytes held in reports",
            "pct": None, "level": "near"})
        upgrades.append(_upgrade(
            "Self-check report store",
            "OG's own check reports are taking over 1 MB.",
            [],
            server_steps=[{"label": "Prune stored reports to "
                                     "the newest 14",
                           "executor": "prune_reports"}]))
    not_measurable.append(
        "Steel credit balance (no read-only balance call in the "
        "Steel API surface OG uses)")
    steel = _steel_sessions()
    if "sessions_listed" in steel:
        measures.append({
            "name": "Steel browser sessions", "detail":
                f"{steel['sessions_listed']} session(s) on the "
                f"account right now", "pct": None, "level": "info"})
    elif steel.get("note"):
        measures.append({"name": "Steel browser sessions",
                         "detail": steel["note"], "pct": None,
                         "level": "info"})

    return {"measures": measures, "not_measurable": not_measurable,
            "upgrades": upgrades, "sizes": sizes,
            "owner_burn": _owner_burn(operator_uid)}


# --- Upgrade-assist executors -------------------------------------------------------

def _exec_prune_reports() -> str:
    reps = _reports()[-MAX_REPORTS:]
    _put("reports", reps)
    return f"Stored reports pruned to the newest {len(reps)}."


_EXECUTORS = {"prune_reports": _exec_prune_reports}


def find_upgrade(report: Optional[dict], name: str) -> Optional[dict]:
    if not report:
        return None
    want = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    for up in report.get("upgrades") or []:
        slug = str(up.get("id", "")).split(":", 1)[-1]
        if want and (want == slug or want in slug or slug in want
                     or want in str(up.get("name", "")).lower()):
            return up
    return None


def approve_upgrade(uid: str, name: str) -> List[Dict]:
    """The assist hook. Operator-only; without a matching offer
    in the latest report, nothing executes. Server steps run
    fail-safe through the registry; owner steps come back as
    plain numbered instructions."""
    operator = _operator_uid()
    if operator is None:
        return _result("SYSTEM CHECK", "Upgrade approvals are off",
                       "No operator account is configured "
                       "(OG_OWNER_EMAIL), so upgrade approvals "
                       "are not switched on. Nothing was run.")
    if uid != operator:
        return _result("SYSTEM CHECK", "That one's the owner's call",
                       "Upgrades are approved by the operator "
                       "account only. Nothing was run.")
    up = find_upgrade(latest_report(), name)
    if up is None:
        return _result("SYSTEM CHECK", "No upgrade waiting by that name",
                       f"No needs-upgrade finding named like "
                       f"'{name}' is waiting in the latest system "
                       f"check. Nothing was run.")
    lines = [f"Upgrade approved: {up['name']}. {up['what']}"]
    ran = []
    for step in up.get("server_steps") or []:
        fn = _EXECUTORS.get(step.get("executor", ""))
        if fn is None:
            continue
        try:
            ran.append(f"{step.get('label', 'step')}: {fn()}")
        except Exception as e:
            ran.append(f"{step.get('label', 'step')}: FAILED "
                       f"({type(e).__name__}) — nothing else was "
                       f"changed by it.")
    if ran:
        lines.append("Done on the server: " + " ".join(ran))
    elif not (up.get("server_steps") or []):
        lines.append("There are no server-side steps for this "
                     "one — it is a dashboard/plan change, so "
                     "nothing was run here.")
    steps = up.get("owner_steps") or []
    if steps:
        lines.append("Your steps:")
        lines.extend(f"{i}. {s}" for i, s in enumerate(steps, 1))
    return _result("SYSTEM CHECK", f"Upgrade: {up['name']}",
                   "\n".join(lines))


# --- The check -----------------------------------------------------------------------


def _summarize(report: dict) -> str:
    when = datetime.fromtimestamp(
        report["ts"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"System check — {when}."]
    b = report["buttons"]
    if b["failed"]:
        names = "; ".join(
            f"{f['button']} ({f['route']} answered {f['observed']})"
            for f in b["failed"])
        lines.append(f"Buttons: {b['ok']} of {b['checked']} working. "
                     f"FAILED: {names}.")
    else:
        lines.append(f"Buttons: all {b['ok']} checked are working.")
    ups = report["updates"]["updates"]
    if ups:
        lines.append("Updates waiting: " + "; ".join(
            f"{u['package']} {u['pinned']} -> {u['latest']}"
            for u in ups) + ".")
    else:
        lines.append("Updates: nothing newer than the pinned "
                     "versions.")
    py = report["updates"]["python"]
    if py.get("pin") and py["pin"] != py["running"]:
        lines.append(f"Python: running {py['running']}, runtime "
                     f"pin says {py['pin']}.")
    over = [m for m in report["overload"]["measures"]
            if m["level"] == "over"]
    near = [m for m in report["overload"]["measures"]
            if m["level"] == "near"]
    if over:
        lines.append("OVERLOADED — needs upgrade: " + "; ".join(
            f"{m['name']} ({m['detail']})" for m in over) + ".")
    if near:
        lines.append("Near limit: " + "; ".join(
            f"{m['name']} ({m['detail']})" for m in near) + ".")
    if not over and not near:
        lines.append("Load: everything measured is within limits.")
    if report["overload"]["not_measurable"]:
        lines.append("Not measurable from inside: " + "; ".join(
            report["overload"]["not_measurable"]) + ".")
    return "\n".join(lines)


def run_check(trigger: str = "scheduled") -> dict:
    """Run all three jobs (each fail-safe), store the report,
    notify the operator once. Returns the report."""
    started = time.time()
    state = _state()
    prev_sizes = state.get("sizes") or {}
    # Stamp BEFORE running: a crash mid-check or a restart can
    # neither hot-loop the scheduler nor double-run a due check.
    state["last_run"] = started
    state["last_trigger"] = trigger
    _put("state", state)

    try:
        buttons = _check_buttons()
    except Exception as e:
        buttons = {"checked": 0, "ok": 0, "skipped": [],
                   "failed": [{"button": "Button census",
                               "route": "(census)",
                               "observed": f"{type(e).__name__}: {e}"}]}
    try:
        updates = _check_updates()
    except Exception as e:
        updates = {"updates": [], "unreachable": -1,
                   "python": {"running": platform.python_version(),
                              "pin": None},
                   "error": f"{type(e).__name__}: {e}"}
    operator = _operator_uid()
    try:
        overload = _check_overload(prev_sizes, operator)
    except Exception as e:
        overload = {"measures": [], "not_measurable": [],
                    "upgrades": [], "sizes": {},
                    "owner_burn": {},
                    "error": f"{type(e).__name__}: {e}"}
    state = _state()
    if overload.get("sizes"):
        state["sizes"] = overload["sizes"]
    _put("state", state)

    report = {
        "ts": started,
        "date": datetime.fromtimestamp(
            started, timezone.utc).strftime("%Y-%m-%d"),
        "trigger": trigger,
        "buttons": buttons,
        "updates": updates,
        "overload": {k: overload.get(k) for k in
                     ("measures", "not_measurable", "upgrades",
                      "owner_burn")},
        "upgrades": overload.get("upgrades") or [],
    }
    report["summary"] = _summarize(report)

    reps = _reports()
    reps.append(report)
    _put("reports", reps[-MAX_REPORTS:])

    if operator:
        try:
            import og_notify as _notify
            bad = len(buttons["failed"])
            over_n = len(report["upgrades"])
            if bad or over_n:
                title = (f"System check: {bad} button(s) failing, "
                         f"{over_n} upgrade(s) needed")
            else:
                title = "System check: everything measured is OK"
            _notify.record(operator, "system_check", title,
                           report["summary"])
        except Exception as e:
            logger.warning(f"Selfcheck notify failed: {e}")
    return report


def due(now: Optional[float] = None) -> bool:
    if not enabled():
        return False
    now = time.time() if now is None else now
    last = _state().get("last_run")
    if not isinstance(last, (int, float)):
        return True
    return (now - float(last)) >= RUN_EVERY_SECONDS


# --- Chat seam (mirror of the house pattern; installed last) -------------------------

_pending: Dict = {"job": None}

_UPGRADE_APPROVE_RE = re.compile(
    r"^\W*(approve|approved|yes|yeah|yep|ok|okay|confirm|"
    r"go ahead)\b[^.]*?\bupgrade\b\s*(?:for\s+)?(.+?)\s*$",
    re.I | re.S)


def _claim_job(message: str) -> Optional[Dict]:
    m = _UPGRADE_APPROVE_RE.match(str(message or ""))
    if not m:
        return None
    name = (m.group(2) or "").strip().strip(".! ")
    if name.lower().startswith("the "):
        name = name[4:]
    if not name:
        return None
    return {"op": "approve_upgrade", "name": name}


def _result(tag: str, title: str, body: str, href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body, "href": href}]


def selfcheck_results(job: Dict, uid: str) -> List[Dict]:
    if job.get("op") == "approve_upgrade":
        return approve_upgrade(uid, job.get("name", ""))
    return _result("SYSTEM CHECK", "System check",
                   "Nothing to run for that one.")


def install_selfcheck_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search LAST (after songs): an
    'approve the upgrade for X' must win over the browser doors'
    bare YES/NO claims, exactly the Round 49 precedence."""
    if getattr(agent_instance, "_og_selfcheck_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["job"] = None
        try:
            uid = get_uid()
            job = _claim_job(str(message)) if uid else None
            if job:
                _pending["job"] = job
                if isinstance(intent, dict):
                    intent["needs_code_generation"] = False
                    if not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
        except Exception as e:
            logger.warning(f"Selfcheck trigger check failed: {e}")
            _pending["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _pending.get("job")
        _pending["job"] = None
        if job:
            try:
                return selfcheck_results(job, get_uid())
            except Exception as e:
                logger.warning(f"Selfcheck results failed: {e}")
                return _result(
                    "SYSTEM CHECK", "System check — glitch",
                    "The upgrade approval glitched — nothing was "
                    "run. Say it again and I'll retry.")
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_selfcheck_installed = True


# --- Routes + scheduler ---------------------------------------------------------------


def _key_ok(key: str) -> bool:
    token = os.getenv("OG_STATS_TOKEN", "").strip()
    return bool(token) and hmac.compare_digest(str(key or ""), token)


def register_selfcheck_routes(app):

    @app.get("/system/check")
    async def system_check_latest(key: str = ""):
        if not _key_ok(key):
            return JSONResponse({"detail": "Owner key required"},
                                status_code=401)
        rep = latest_report()
        return {"ok": True, "latest": rep,
                "summary": (rep or {}).get("summary"),
                "enabled": enabled(),
                "last_run": _state().get("last_run")}

    @app.post("/system/check")
    async def system_check_now(key: str = ""):
        """The operator's manual trigger (key-gated, /stats
        posture): run one check now and return its report."""
        if not _key_ok(key):
            return JSONResponse({"detail": "Owner key required"},
                                status_code=401)
        rep = await asyncio.to_thread(run_check, "manual")
        return {"ok": True, "latest": rep, "summary": rep["summary"]}

    @app.on_event("startup")
    async def _start_selfcheck_loop():
        if _loop_started["on"]:
            return
        _loop_started["on"] = True

        async def _loop():
            while True:
                await asyncio.sleep(max(30, POLL_SECONDS))
                try:
                    if due():
                        await asyncio.to_thread(run_check, "scheduled")
                except Exception as e:
                    logger.warning(f"Selfcheck cycle failed: {e}")

        asyncio.create_task(_loop())
