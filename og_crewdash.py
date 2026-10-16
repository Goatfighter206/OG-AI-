"""Crew Dashboard (Round 267 build, OG W dashboards wave,
2026-10-16) — the Manager Dashboard's server routes.

Brent's words: 2026-10-13 "click on og and it will take
you to that stuff" + the closed cut pass (no header
banner, NO fake-task button, slim Refresh/Export/
auto-refresh control row, compact Helper Status listing
ALL seats, canonical 42x42 X top-right); 2026-10-16:
avatar tap = this dashboard AND a button at the bottom
of the Progress panel.

WHAT THIS IS. Three read-only routes over OG's REAL
stores — nothing here holds a second copy of anything,
dispatches nothing, approves nothing, and flips no
switch:

  GET /dashboards/state -> {manager, crypto, fin}
      The shared scaffold's one gate read. manager is
      this module's own switch family (below); crypto /
      fin are read through Worker B's modules
      (og_cryptohub.cryptohub_enabled /
      og_finagent.finagent_enabled) by lazy import — a
      sibling that cannot be imported reports False,
      never an error.
  GET /crewdash/data -> the six R267 sections, computed
      at read time from the caller's OWN records:
      System Overview, Performance, Helper Status
      (compact, every seat), Recent Tasks, Communication
      Hub (the real delivery record), Task Distribution.
  GET /crewdash/export -> the caller's own crew records
      (jobs / projects / deliveries) as a JSON download,
      exactly as the stores hold them.

HONESTY FENCES (R267 SPEC, binding, encoded below):
- Every figure is computed at read time from a real
  store read, with its basis beside it (counts beside
  rates, N beside the average) — or replaced by its
  honest unavailable/empty line. No third state.
- Success rate only over finished jobs (done + failed);
  zero finished jobs -> the rate is absent, its line
  shows instead. Response time only from real
  started/finished stamp pairs; untimed jobs are
  excluded and the exclusion is counted in the payload.
- Performance is finished-per-day over the trailing 14
  days from real finish stamps; chartable only when at
  least 3 of those days carry finishes, else the honest
  empty line (the client draws no curve).
- Task Distribution shows true zeros when the crew
  store reads empty, and its unavailable line when the
  store read FAILS — the two never look alike.
- Each section is independently fail-soft: one store's
  error yields that section's unavailable state, never
  a 500 for the page.
- Capability counts: no seat module reports a shipped-
  lane count through any read seam, so every seat's
  capability figure is null (the client renders the
  SPEC's dash) — never an invented number.

SWITCH. OG_CREWDASH_ENABLED (unset = ON, the og_crew
family shape) UNDER the crew master: OG_CREW_ENABLED
off = the dashboard does not exist. While either is
off, /crewdash/data and /crewdash/export answer 404.
(/dashboards/state stays up so the scaffold can learn
the surfaces are off and leave every entry behaving
exactly as before — the R267 drill (h) posture.)

READ SEAMS. Crew jobs are read through og_crew's own
store read (og_crew._jobs — the same seam og_pm binds
its steps through; jobs_for trims the started/finished/
report fields the Overview, Performance, Recent and
Communication sections honestly need). Projects ride
og_pm.projects_for, deliveries og_notify._items,
prepared fixes og_fixqueue.list_proposals, the Progress
aggregate og_progress.collect_progress, the seat census
og_activity.SEAT_NAMES. No store is forked.

Handlers are plain def (the Round 321 main-thread rule):
the reads are sync store work, so FastAPI runs them in
its threadpool and a stalled store can never freeze the
event loop. No new loop, no new state. Persona untouched.

PROFILE SHEET (2026-10-16). GET /crewdash/sheet is OG's
own profile surface — its door is the avatar, NOT the
Manager Dashboard (the Manager keeps its Progress-panel
button and its own switch family). The sheet payload is
additive: a live status line derived only from real
state (activity claim, browser session, working crew
job, background work — first match wins, else "Idle"),
plus five tabs — Today, Approvals, Browser Tasks,
Scheduled, Identity (view-only) — each read once from
the owning store through guarded seams and each
independently fail-soft, exactly like the sections
above. The sheet has its OWN switch,
OG_PROFILESHEET_ENABLED (profile_sheet_enabled,
reported as "profile" in /dashboards/state): off = the
sheet route 404s, regardless of the crewdash/crew
switches, and the Manager is unaffected by it. The
six-section /crewdash/data payload is unchanged.
"""

import logging
import math
import os
import time
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

UID_COOKIE = "ogai_uid"
RECENT_CAP = 20
COMMS_CAP = 20
PERF_DAYS = 14
PERF_MIN_DAYS = 3

_ROLE_LABELS = {"researcher": "Researcher", "runner": "Runner",
                "coder": "Coder", "watcher": "Watcher",
                "maker": "Maker"}
_ROLE_ORDER = ("researcher", "runner", "coder", "watcher",
               "maker")

_STATE_LABELS = {"queued": "Queued", "working": "Working",
                 "done": "Done", "failed": "Failed",
                 "cancelled": "Cancelled"}

# tag -> (module, enabled function): each seat's OWN kill
# switch, read live. OG FIX has no switch function of its
# own (the fix queue is always readable); it is handled
# specially in _seat_state.
_SEAT_SWITCHES = {
    "OG SELLING": ("og_selling", "selling_enabled"),
    "OG CODING": ("og_coding", "coding_enabled"),
    "OG MESSENGER": ("og_messenger", "messenger_enabled"),
    "OG FACEBOOK": ("og_facebook", "facebook_enabled"),
    "OG MARKETPLACE": ("og_marketplace", "marketplace_enabled"),
    "OG SHOPPING": ("og_shopping", "shopping_enabled"),
    "OG BUYING": ("og_buying", "buying_enabled"),
    "OG PHONE": ("og_phone", "phone_enabled"),
    "OG CARPLAY": ("og_carplay", "carplay_enabled"),
    "OG TRACKER": ("og_tracker", "tracker_enabled"),
    "OG CAR MECHANIC": ("og_mechanic", "mechanic_enabled"),
    "OG MAINTENANCE + ENGINEER": ("og_mainteng", "mainteng_enabled"),
    "OG BLUEPRINT SPECIALIST": ("og_blueprint", "blueprint_enabled"),
    "OG PROJECT MANAGER": ("og_pm", "pm_enabled"),
    "OG PROJECT FILING": ("og_filing", "filing_enabled"),
    "OG PROJECT BUILDER": ("og_builder", "builder_enabled"),
    "OG WOOD PROJECT BUILDER PRO": ("og_woodpro", "woodpro_enabled"),
    "OG CREW": ("og_crew", "crew_enabled"),
    "OG WATCH": ("og_watch", "watch_enabled"),
    "OG YOUTUBE": ("og_youtube", "youtube_seat_enabled"),
    "OG BROWSER SPECIALIST": ("og_webbrowser", "webbrowser_enabled"),
    "OG GOD MODE CODING": ("og_godmodecoding", "godmodecoding_enabled"),
    "OG GOD MODE TESTER": ("og_godmodetester", "godmodetester_enabled"),
    "OG GOD MODE RUNSET": ("og_godmoderunset", "godmoderunset_enabled"),
    "OG GOD MODE MAKE MONEY FAST": (
        "og_godmodemoneyfast", "godmodemoneyfast_enabled"),
}

# og_notify kind -> the seat a delivery is credited to on
# the Communication Hub. Kinds not listed credit "OG".
_COMMS_SEATS = {
    "watch_match": "Watcher", "mp_match": "Marketplace",
    "price_alert": "Tracker",
    "video_done": "Maker", "song_done": "Maker",
    "podcast_done": "Maker", "book_done": "Maker",
    "motion_done": "Maker", "model_done": "Maker",
    "sfx_done": "Maker", "voice_done": "Maker",
    "fix_needed": "Fixes", "storage_warning": "OG",
    "approval_needed": "OG", "signin_needed": "OG",
    "system_check": "OG", "auto_update": "OG",
    "notice": "OG",
}


# --- Switches -------------------------------------------------------------------


def crewdash_enabled() -> bool:
    """OG_CREWDASH_ENABLED=false is the kill switch (any
    other value, or unset, leaves the dashboard on) —
    the og_crew.crew_enabled shape, mirrored exactly."""
    return os.environ.get(
        "OG_CREWDASH_ENABLED", "true").strip().lower() not in (
            "0", "false", "no", "off")


def _crew_on() -> bool:
    try:
        import og_crew
        return bool(og_crew.crew_enabled())
    except Exception:
        return False


def manager_enabled() -> bool:
    """The dashboard exists only while its own switch AND
    the crew master are both on (SPEC: crew master off =
    the dashboard doesn't exist)."""
    return crewdash_enabled() and _crew_on()


def profile_sheet_enabled() -> bool:
    """OG_PROFILESHEET_ENABLED=false is the profile
    sheet's OWN kill switch (unset = ON, the house
    shape). Independent of the crewdash/crew switches:
    the sheet is its own surface (avatar door), not the
    Manager, so neither door gates the other."""
    return os.environ.get(
        "OG_PROFILESHEET_ENABLED", "true").strip().lower() not in (
            "0", "false", "no", "off")


def _sibling_enabled(module_name: str, fn_name: str) -> bool:
    """A sibling dashboard's own switch, by lazy import
    (the Worker B contract). Any failure — module not
    built yet, import error, switch error — reports
    False: the scaffold refuses that surface."""
    try:
        import importlib
        mod = importlib.import_module(module_name)
        return bool(getattr(mod, fn_name)())
    except Exception:
        return False


def dashboards_state() -> Dict:
    return {
        "manager": manager_enabled(),
        "crypto": _sibling_enabled("og_cryptohub",
                                   "cryptohub_enabled"),
        "fin": _sibling_enabled("og_finagent",
                                "finagent_enabled"),
        "profile": profile_sheet_enabled(),
    }


# --- Store reads (each guarded by its caller; nothing forked) ---------------------


def _crew_jobs(uid: str) -> List[Dict]:
    """The caller's raw crew job records from og_crew's
    own store read. Raises on a store failure — each
    section's guard turns that into its unavailable
    state. (jobs_for trims started/finished/report, which
    four sections honestly need; og_pm reads this same
    seam to bind its steps.)"""
    import og_crew
    raw = None
    if hasattr(og_crew, "_jobs"):
        raw = og_crew._jobs(uid)
    if not isinstance(raw, list):
        raw = og_crew.jobs_for(uid)
    return [j for j in raw if isinstance(j, dict)]


def _progress_rows(uid: str, request) -> List[Dict]:
    import og_progress
    data = og_progress.collect_progress(uid, request)
    rows = (data or {}).get("rows")
    return rows if isinstance(rows, list) else []


def _notify_items(uid: str) -> List[Dict]:
    import og_notify
    items = og_notify._items(uid)
    return [it for it in items if isinstance(it, dict)] \
        if isinstance(items, list) else []


def _fix_proposals(uid: str) -> List[Dict]:
    import og_fixqueue
    data = og_fixqueue.list_proposals(uid)
    props = (data or {}).get("proposals")
    return props if isinstance(props, list) else []


def _current_claim_tag(uid: str) -> Optional[str]:
    """The tag of the seat og_activity currently records
    as working for this account (inside its own CURRENT
    window), or None."""
    import og_activity
    snap = og_activity.activity_snapshot(uid)
    cur = (snap or {}).get("current")
    if isinstance(cur, dict) and cur.get("tag"):
        return str(cur["tag"])
    return None


class _Reads:
    """One data build's store reads, each attempted ONCE
    and each carrying its own failure — sections consume
    the reads, they never re-read or scrape each other."""

    def __init__(self, uid: str, request):
        self.uid = uid
        self.jobs: Optional[List[Dict]] = None
        self.jobs_err: Optional[Exception] = None
        try:
            self.jobs = _crew_jobs(uid)
        except Exception as e:
            self.jobs_err = e
        self.prows: Optional[List[Dict]] = None
        self.prows_err: Optional[Exception] = None
        try:
            self.prows = _progress_rows(uid, request)
        except Exception as e:
            self.prows_err = e
        self.items: Optional[List[Dict]] = None
        self.items_err: Optional[Exception] = None
        try:
            self.items = _notify_items(uid)
        except Exception as e:
            self.items_err = e
        self.props: Optional[List[Dict]] = None
        self.props_err: Optional[Exception] = None
        try:
            self.props = _fix_proposals(uid)
        except Exception as e:
            self.props_err = e
        self.claim_tag: Optional[str] = None
        try:
            self.claim_tag = _current_claim_tag(uid)
        except Exception:
            self.claim_tag = None


def _f(job: Dict, key: str) -> float:
    try:
        return float(job.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


# --- Section 1: System Overview ---------------------------------------------------


def _section_overview(rd: _Reads) -> Dict:
    if rd.jobs_err is not None:
        return {"unavailable": True,
                "line": "Crew status unavailable",
                "crew_on": _crew_on(), "crew_status": None,
                "active_helpers": None, "active_seats": [],
                "total_tasks": None, "done": None,
                "failed": None, "finished": None,
                "success_rate": None,
                "success_line": "Crew status unavailable",
                "avg_response_s": None, "avg_timed": 0,
                "avg_untimed": 0,
                "avg_line": "Crew status unavailable"}
    jobs = rd.jobs or []
    crew_on = _crew_on()
    done = sum(1 for j in jobs if j.get("state") == "done")
    failed = sum(1 for j in jobs
                 if j.get("state") == "failed")
    finished = done + failed
    if finished:
        success_rate = round(100.0 * done / finished, 1)
        success_line = None
    else:
        success_rate = None
        success_line = "No finished jobs yet"
    durations = []
    untimed = 0
    for j in jobs:
        if j.get("state") not in ("done", "failed"):
            continue
        started, fin = _f(j, "started"), _f(j, "finished")
        if started > 0 and fin >= started and fin > 0:
            durations.append(fin - started)
        else:
            untimed += 1
    if durations:
        avg = round(sum(durations) / len(durations), 2)
        avg_line = None
    else:
        avg = None
        avg_line = "No timed jobs yet"
    # Active helpers: seats holding a running job RIGHT
    # NOW — crew roles on a working job, a Project Manager
    # project with a step being worked, the seat
    # og_activity currently records as claiming work.
    active = set()
    for j in jobs:
        if j.get("state") == "working":
            label = _ROLE_LABELS.get(str(j.get("role") or ""))
            if label:
                active.add(label)
    prows = rd.prows or []
    if any(p.get("section") == "making" and p.get("working")
           for p in prows):
        active.add("Maker")
    if any(p.get("section") == "watching" for p in prows):
        active.add("Watcher")
    if any(p.get("section") == "projects" and p.get("working")
           for p in prows):
        active.add("Project Manager")
    if rd.props and any(p.get("status") == "working"
                        for p in rd.props):
        active.add("Coder")
    if rd.claim_tag:
        try:
            import og_activity
            name = og_activity.SEAT_NAMES.get(rd.claim_tag)
            if name:
                active.add(name)
        except Exception:
            pass
    return {"unavailable": False, "line": None,
            "crew_on": crew_on,
            "crew_status": "Running" if crew_on else "Off",
            "active_helpers": len(active),
            "active_seats": sorted(active),
            "total_tasks": len(jobs),
            "done": done, "failed": failed,
            "finished": finished,
            "success_rate": success_rate,
            "success_line": success_line,
            "avg_response_s": avg,
            "avg_timed": len(durations),
            "avg_untimed": untimed,
            "avg_line": avg_line}


# --- Section 2: Performance ---------------------------------------------------------


def _section_performance(rd: _Reads) -> Dict:
    empty = ("Not enough finished work yet to chart — your "
             "crew's history will draw itself here.")
    if rd.jobs_err is not None:
        return {"unavailable": True,
                "line": "Performance unavailable", "days": [],
                "chartable": False, "empty_line": empty,
                "total_completed": 0, "days_with_finishes": 0}
    today = datetime.now().date()
    days = [today - timedelta(days=i)
            for i in range(PERF_DAYS - 1, -1, -1)]
    counts = {d.isoformat(): 0 for d in days}
    for j in rd.jobs or []:
        if j.get("state") != "done":
            continue
        fin = _f(j, "finished")
        if fin <= 0:
            continue
        key = datetime.fromtimestamp(fin).date().isoformat()
        if key in counts:
            counts[key] += 1
    out_days = [{"date": d.isoformat(),
                 "label": d.strftime("%b %d"),
                 "count": counts[d.isoformat()]} for d in days]
    with_fin = sum(1 for d in out_days if d["count"] > 0)
    return {"unavailable": False, "line": None,
            "days": out_days,
            "chartable": with_fin >= PERF_MIN_DAYS,
            "empty_line": empty,
            "total_completed": sum(counts.values()),
            "days_with_finishes": with_fin}


# --- Section 3: Helper Status (COMPACT, every seat) ---------------------------------


def _crew_role_state(role: str, rd: _Reads) -> Dict:
    """{state, load_working} for one crew role."""
    if not _crew_on():
        return {"state": "OFF", "load_working": None}
    jobs = rd.jobs
    if role in ("researcher", "runner"):
        if jobs is None:
            return {"state": "UNKNOWN", "load_working": None}
        working = sum(1 for j in jobs
                      if j.get("role") == role
                      and j.get("state") == "working")
        return {"state": "WORKING" if working else "READY",
                "load_working": working}
    prows = rd.prows
    if role == "coder":
        if rd.props is not None and any(
                p.get("status") == "working" for p in rd.props):
            return {"state": "WORKING", "load_working": None}
        if prows is None and rd.props is None:
            return {"state": "UNKNOWN", "load_working": None}
        return {"state": "READY", "load_working": None}
    if role == "watcher":
        if prows is None:
            return {"state": "UNKNOWN", "load_working": None}
        working = any(p.get("section") == "watching"
                      for p in prows)
        return {"state": "WORKING" if working else "READY",
                "load_working": None}
    if role == "maker":
        if prows is None:
            return {"state": "UNKNOWN", "load_working": None}
        working = any(p.get("section") == "making"
                      and p.get("working") for p in prows)
        return {"state": "WORKING" if working else "READY",
                "load_working": None}
    return {"state": "READY", "load_working": None}


def _seat_state(tag: str, rd: _Reads) -> str:
    if tag == "OG FIX":
        if rd.props is None:
            return "UNKNOWN"
        return "WORKING" if any(
            p.get("status") == "working" for p in rd.props) \
            else "READY"
    switch = _SEAT_SWITCHES.get(tag)
    if switch is None:
        state = "READY"
    else:
        try:
            import importlib
            mod = importlib.import_module(switch[0])
            on = bool(getattr(mod, switch[1])())
        except Exception:
            return "UNKNOWN"
        if not on:
            return "OFF"
        state = "READY"
    if rd.claim_tag == tag:
        return "WORKING"
    if tag == "OG CREW" and rd.jobs and any(
            j.get("state") == "working" for j in rd.jobs):
        return "WORKING"
    if tag == "OG WATCH" and rd.prows and any(
            p.get("section") == "watching" for p in rd.prows):
        return "WORKING"
    if tag == "OG PROJECT MANAGER" and rd.prows and any(
            p.get("section") == "projects" and p.get("working")
            for p in rd.prows):
        return "WORKING"
    return state


def _section_helpers(rd: _Reads) -> Dict:
    rows: List[Dict] = []
    user_cap = 2
    try:
        import og_crew
        user_cap = int(getattr(og_crew, "USER_WORKING_CAP", 2)
                       or 2)
    except Exception:
        user_cap = 2
    for role in _ROLE_ORDER:
        st = _crew_role_state(role, rd)
        load = None
        if st["load_working"] is not None:
            load = f"{st['load_working']}/{user_cap}"
        rows.append({
            "key": f"crew:{role}",
            "name": _ROLE_LABELS[role],
            "state": st["state"],
            "load": load,
            "load_working": st["load_working"],
            "load_cap": user_cap
            if st["load_working"] is not None else None,
            "capabilities": None})
    try:
        import og_activity
        seat_names = dict(og_activity.SEAT_NAMES)
    except Exception:
        seat_names = {}
    if not seat_names:
        return {"unavailable": True,
                "line": "Helper list unavailable",
                "rows": rows, "count": len(rows)}
    for tag, name in seat_names.items():
        rows.append({
            "key": tag, "name": name,
            "state": _seat_state(tag, rd),
            "load": None, "load_working": None,
            "load_cap": None, "capabilities": None})
    return {"unavailable": False, "line": None,
            "rows": rows, "count": len(rows)}


# --- Section 4: Recent Tasks ----------------------------------------------------------


def _section_recent(rd: _Reads, now: float) -> Dict:
    if rd.jobs_err is not None:
        return {"unavailable": True,
                "line": "Recent tasks unavailable",
                "rows": [], "total": 0, "shown": 0}
    jobs = sorted(rd.jobs or [],
                  key=lambda j: -_f(j, "created"))
    rows = []
    for j in jobs[:RECENT_CAP]:
        started, fin = _f(j, "started"), _f(j, "finished")
        elapsed: Optional[float] = None
        if started > 0 and fin > 0 and fin >= started:
            elapsed = round(fin - started, 1)
        elif started > 0 and j.get("state") == "working":
            elapsed = round(now - started, 1)
        role = str(j.get("role") or "")
        state = str(j.get("state") or "")
        rows.append({
            "id": str(j.get("id") or ""),
            "title": str(j.get("title")
                         or j.get("detail") or "Crew job"),
            "description": str(j.get("detail")
                               or j.get("title") or ""),
            "role": role,
            "role_label": _ROLE_LABELS.get(role, role),
            "state": state,
            "state_label": _STATE_LABELS.get(state, state),
            "created": _f(j, "created"),
            "started": started, "finished": fin,
            "elapsed_s": elapsed})
    return {"unavailable": False, "line": None, "rows": rows,
            "total": len(jobs), "shown": len(rows)}


# --- Section 5: Communication Hub (the real delivery record) -------------------------


def _first_line(text) -> str:
    for line in str(text or "").splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return ""


def _section_communication(rd: _Reads) -> Dict:
    if rd.jobs_err is not None and rd.items_err is not None:
        return {"unavailable": True,
                "line": "Communication record unavailable",
                "rows": [],
                "empty_line": "No deliveries yet — when a "
                              "crew result lands for you, it "
                              "shows up here."}
    rows: List[Dict] = []
    crew_titles = set()
    for j in rd.jobs or []:
        if j.get("state") not in ("done", "failed"):
            continue
        report = str(j.get("report") or "")
        if not report.strip():
            continue
        title = str(j.get("title") or "Crew job")
        crew_titles.add(title)
        role = str(j.get("role") or "")
        rows.append({
            "ts": _f(j, "finished") or _f(j, "updated"),
            "seat": _ROLE_LABELS.get(role, "Crew"),
            "kind": "crew_report",
            "title": title,
            "text": _first_line(report)})
    for it in rd.items or []:
        title = str(it.get("title") or "")
        # A crew finish also records an og_notify notice
        # ("Crew — <Role> done: <job title>") — the crew
        # entry above IS that delivery; never show it twice.
        if title.startswith("Crew — ") and any(
                t and t in title for t in crew_titles):
            continue
        kind = str(it.get("kind") or "notice")
        rows.append({
            "ts": _f(it, "ts"),
            "seat": _COMMS_SEATS.get(kind, "OG"),
            "kind": kind,
            "title": title,
            "text": _first_line(it.get("body"))})
    rows.sort(key=lambda r: -r["ts"])
    return {"unavailable": False, "line": None,
            "rows": rows[:COMMS_CAP],
            "empty_line": "No deliveries yet — when a crew "
                          "result lands for you, it shows "
                          "up here."}


# --- Section 6: Task Distribution ------------------------------------------------------


def _section_distribution(rd: _Reads) -> Dict:
    if rd.jobs_err is not None:
        return {"unavailable": True,
                "line": "Task distribution unavailable — "
                        "the crew store could not be read.",
                "rows": [], "total_completed": None}
    rows = []
    total_completed = 0
    for role in _ROLE_ORDER:
        mine = [j for j in (rd.jobs or [])
                if j.get("role") == role]
        counts = {"working": 0, "queued": 0, "done": 0,
                  "failed": 0, "cancelled": 0}
        for j in mine:
            st = str(j.get("state") or "")
            if st in counts:
                counts[st] += 1
        total_completed += counts["done"]
        rows.append({"role": role,
                     "label": _ROLE_LABELS[role],
                     "running": counts["working"],
                     "queued": counts["queued"],
                     "done": counts["done"],
                     "failed": counts["failed"],
                     "cancelled": counts["cancelled"],
                     "total": len(mine)})
    return {"unavailable": False, "line": None, "rows": rows,
            "total_completed": total_completed}


# --- The payload ------------------------------------------------------------------------


def build_data(uid: str, request=None) -> Dict:
    """The six sections for ONE account, each independently
    fail-soft. request rides og_progress.collect_progress
    (its owner-only block reads the caller's cookies)."""
    rd = _Reads(uid, request)
    now = time.time()
    return {
        "ok": True,
        "generated_at": now,
        "overview": _section_overview(rd),
        "performance": _section_performance(rd),
        "helpers": _section_helpers(rd),
        "recent": _section_recent(rd, now),
        "communication": _section_communication(rd),
        "distribution": _section_distribution(rd),
    }


def build_export(uid: str) -> Dict:
    """The caller's own records, exactly as the stores
    hold them — jobs (og_crew's raw records), projects
    (og_pm.projects_for), deliveries (og_notify._items).
    A store that cannot be read names itself in errors
    and contributes an empty list; nothing synthesized."""
    out: Dict = {"exported_at": time.time(), "uid": uid,
                 "jobs": [], "projects": [], "deliveries": [],
                 "errors": []}
    try:
        import og_crew
        raw = og_crew._jobs(uid) \
            if hasattr(og_crew, "_jobs") else []
        out["jobs"] = [j for j in raw if isinstance(j, dict)]
    except Exception:
        out["errors"].append("jobs")
    try:
        import og_pm
        out["projects"] = og_pm.projects_for(uid)
    except Exception:
        out["errors"].append("projects")
    try:
        import og_notify
        items = og_notify._items(uid)
        out["deliveries"] = [it for it in items
                             if isinstance(it, dict)] \
            if isinstance(items, list) else []
    except Exception:
        out["errors"].append("deliveries")
    return out


# --- Profile sheet reads (each guarded, each attempted once) ----------------------


def _activity_snapshot(uid: str) -> Dict:
    import og_activity
    snap = og_activity.activity_snapshot(uid)
    return snap if isinstance(snap, dict) else {}


def _browser_session(uid: str):
    import og_browser
    rec = og_browser._get_session(uid)
    return rec if isinstance(rec, dict) else None


def _browser_pending(uid: str):
    import og_browser
    rec = og_browser._get_pending(uid)
    return rec if isinstance(rec, dict) else None


def _watch_records(uid: str) -> List[Dict]:
    import og_watch
    raw = og_watch._watches(uid)
    return [w for w in raw if isinstance(w, dict)] \
        if isinstance(raw, list) else []


def _memory_entry(uid: str):
    import og_memory_io
    return og_memory_io.load_history_entry(uid)


def _account_for(uid: str):
    """The account record for this uid through
    og_accounts' own keyed seam — the uid->email index
    read og_notify already uses ("uidacct:" + uid),
    then the account record by email ("acct:" + email,
    shaped {"email", "uid", "created", ...}). Returns
    {"email", "created"} or None when the uid owns no
    account. The accounts store is never forked."""
    import og_accounts
    email = og_accounts._get("uidacct:" + uid)
    if not isinstance(email, str) or not email:
        return None
    created = None
    acct = og_accounts._get("acct:" + email)
    if isinstance(acct, dict):
        if isinstance(acct.get("email"), str) \
                and acct.get("email"):
            email = str(acct["email"])
        created = acct.get("created")
        if not isinstance(created, str):
            created = None
    return {"email": email, "created": created}


class _SheetReads:
    """One sheet build's store reads, each attempted
    ONCE and each carrying its own failure — the sheet
    sections consume the reads, they never re-read.
    Mirrors _Reads; the progress aggregate is not a
    sheet source and is not read here."""

    def __init__(self, uid: str):
        self.uid = uid
        self.jobs: Optional[List[Dict]] = None
        self.jobs_err: Optional[Exception] = None
        try:
            self.jobs = _crew_jobs(uid)
        except Exception as e:
            self.jobs_err = e
        self.items: Optional[List[Dict]] = None
        self.items_err: Optional[Exception] = None
        try:
            self.items = _notify_items(uid)
        except Exception as e:
            self.items_err = e
        self.props: Optional[List[Dict]] = None
        self.props_err: Optional[Exception] = None
        try:
            self.props = _fix_proposals(uid)
        except Exception as e:
            self.props_err = e
        self.snap: Optional[Dict] = None
        self.snap_err: Optional[Exception] = None
        try:
            self.snap = _activity_snapshot(uid)
        except Exception as e:
            self.snap_err = e
        self.bsess = None
        self.bsess_err: Optional[Exception] = None
        try:
            self.bsess = _browser_session(uid)
        except Exception as e:
            self.bsess_err = e
        self.bpend = None
        self.bpend_err: Optional[Exception] = None
        try:
            self.bpend = _browser_pending(uid)
        except Exception as e:
            self.bpend_err = e
        self.watches: Optional[List[Dict]] = None
        self.watches_err: Optional[Exception] = None
        try:
            self.watches = _watch_records(uid)
        except Exception as e:
            self.watches_err = e
        # load_history_entry is fail-soft by its own
        # contract: None = no DB backend / read failure,
        # {} = no row, a dict = the stored entry. A raise
        # (never expected) reads the same as None.
        try:
            self.mem_entry = _memory_entry(uid)
        except Exception:
            self.mem_entry = None
        try:
            self.account = _account_for(uid)
        except Exception:
            self.account = None


def _site_of(url) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        host = urllib.parse.urlparse(text).hostname
        if not host and "://" not in text:
            host = urllib.parse.urlparse("//" + text).hostname
        return str(host or "")
    except Exception:
        return ""


def _trim80(text: str) -> str:
    text = str(text or "").strip()
    if len(text) <= 80:
        return text
    return text[:77].rstrip() + "..."


def _pending_url(pend: Dict) -> str:
    url = str(pend.get("url") or pend.get("page_url") or "")
    if url:
        return url
    sig = pend.get("sig")
    if isinstance(sig, dict):
        return str(sig.get("url") or "")
    return ""


def _pending_goal(pend: Dict) -> str:
    return str(pend.get("goal") or pend.get("desc") or "")


# --- Profile sheet: status line ------------------------------------------------------


def _sheet_status(rd: _SheetReads) -> Dict:
    snap = rd.snap or {}
    current = snap.get("current")
    if isinstance(current, dict):
        line = str(current.get("line")
                   or snap.get("text") or "")
        if line:
            tag = current.get("tag")
            return {"text": line, "kind": "claim",
                    "detail": str(tag) if tag else None}
    if isinstance(rd.bsess, dict):
        sess = rd.bsess
        site = _site_of(sess.get("last_url")
                        or sess.get("nav_target"))
        control = str(sess.get("control") or "")
        if control in ("user", "visitor"):
            text = "You're in control of the browser"
        else:
            goal = _first_line(sess.get("goal"))
            if goal:
                text = ("Working in the browser — "
                        + _trim80(goal))
            elif site:
                text = f"Browsing {site}"
            else:
                text = "Browsing"
        return {"text": text, "kind": "browser",
                "detail": site or None}
    for j in rd.jobs or []:
        if j.get("state") == "working":
            title = str(j.get("title")
                        or j.get("detail") or "a task")
            return {"text": f"Working on {title}",
                    "kind": "crew", "detail": None}
    background = snap.get("background")
    if isinstance(background, list) and background:
        first = background[0]
        text = ""
        if isinstance(first, dict):
            text = str(first.get("text") or "")
        elif isinstance(first, str):
            text = first
        if text:
            return {"text": text, "kind": "background",
                    "detail": None}
    return {"text": "Idle", "kind": "idle", "detail": None}


# --- Profile sheet: Today ---------------------------------------------------------------


def _sheet_today(rd: _SheetReads, day_start: float) -> Dict:
    rows: List[Dict] = []
    crew_titles = set()
    for j in rd.jobs or []:
        if j.get("state") in ("done", "failed"):
            title = str(j.get("title") or "")
            if title:
                crew_titles.add(title)
    if rd.jobs_err is None:
        for j in rd.jobs or []:
            title = str(j.get("title")
                        or j.get("detail") or "Crew job")
            created = _f(j, "created")
            if created >= day_start and created > 0:
                rows.append({"ts": created, "kind": "crew",
                             "text": f"Task queued — {title}"})
            state = str(j.get("state") or "")
            if state in ("done", "failed"):
                fin = _f(j, "finished")
                if fin >= day_start and fin > 0:
                    word = "done" if state == "done" \
                        else "failed"
                    rows.append({
                        "ts": fin, "kind": "crew",
                        "text": f"Task {word} — {title}"})
    if rd.props_err is None:
        for p in rd.props or []:
            created = _f(p, "created")
            if created >= day_start and created > 0:
                title = str(p.get("title") or "Fix")
                rows.append({"ts": created, "kind": "fix",
                             "text": f"Fix proposed — {title}"})
    if rd.items_err is None:
        for it in rd.items or []:
            title = str(it.get("title") or "")
            # A crew finish also records an og_notify
            # notice ("Crew — <Role> done: <job title>") —
            # the crew row above IS that event; never
            # show it twice (the _section_communication
            # dedupe, mirrored).
            if title.startswith("Crew — ") and any(
                    t and t in title for t in crew_titles):
                continue
            ts = _f(it, "ts")
            if ts < day_start or ts <= 0:
                continue
            text = title or _first_line(it.get("body"))
            if text:
                rows.append({"ts": ts, "kind": "notify",
                             "text": text})
    if rd.bsess_err is None and isinstance(rd.bsess, dict):
        started = _f(rd.bsess, "started")
        if started >= day_start and started > 0:
            site = _site_of(rd.bsess.get("last_url")
                            or rd.bsess.get("nav_target"))
            text = f"Browser session — {site}" if site \
                else "Browser session"
            rows.append({"ts": started, "kind": "browser",
                         "text": text})
    rows.sort(key=lambda r: r["ts"])
    unavailable = (rd.jobs_err is not None
                   and rd.props_err is not None
                   and rd.items_err is not None
                   and rd.bsess_err is not None)
    return {"unavailable": unavailable, "rows": rows[:60],
            "empty_line": "Nothing yet today — what OG "
                          "does for you today shows up "
                          "here."}


# --- Profile sheet: Approvals --------------------------------------------------------------


_STATUS_LABELS_SHEET = {"working": "Working",
                        "executed": "Done",
                        "handed_off": "Handed off",
                        "declined": "Declined",
                        "superseded": "Superseded",
                        "expired": "Expired"}


def _sheet_approvals(rd: _SheetReads,
                     generated_at: float) -> Dict:
    pending: List[Dict] = []
    history: List[Dict] = []
    open_titles: List[str] = []
    if rd.props_err is None:
        for p in rd.props or []:
            title = str(p.get("title") or "Fix")
            status = str(p.get("status") or "")
            created = _f(p, "created")
            if status == "open":
                open_titles.append(title)
                expires = p.get("expires")
                pending.append({
                    "kind": "fix", "title": title,
                    "line": _first_line(p.get("summary")),
                    "ts": created,
                    "expires": float(expires)
                    if isinstance(expires, (int, float))
                    else None})
            else:
                line = str(p.get("outcome")
                           or p.get("status_note")
                           or _STATUS_LABELS_SHEET.get(
                               status, status))
                history.append({"kind": "fix",
                                "title": title, "line": line,
                                "ts": created})
    if rd.bpend_err is None and isinstance(rd.bpend, dict):
        pend = rd.bpend
        url = _pending_url(pend)
        site = _site_of(url)
        goal = _pending_goal(pend)
        stamp = _f(pend, "created")
        pending.append({
            "kind": "browser",
            "title": "Browser action waiting",
            "line": goal or site,
            "ts": stamp if stamp > 0 else generated_at,
            "expires": None})
    if rd.items_err is None:
        for it in rd.items or []:
            kind = str(it.get("kind") or "")
            title = str(it.get("title") or "")
            line = _first_line(it.get("body"))
            ts = _f(it, "ts")
            if it.get("read") is False and kind in (
                    "approval_needed", "signin_needed"):
                if any(t and t in title
                       for t in open_titles):
                    continue
                pending.append({"kind": "notify",
                                "title": title, "line": line,
                                "ts": ts, "expires": None})
            elif it.get("read") is True and kind in (
                    "approval_needed", "signin_needed",
                    "fix_needed"):
                history.append({"kind": "notify",
                                "title": title, "line": line,
                                "ts": ts})
    pending.sort(key=lambda r: r["ts"])
    history.sort(key=lambda r: -r["ts"])
    unavailable = (rd.props_err is not None
                   and rd.items_err is not None)
    return {"unavailable": unavailable, "pending": pending,
            "history": history[:20],
            "empty_line": "Nothing waiting for your "
                          "approval right now."}


# --- Profile sheet: Browser Tasks -------------------------------------------------------------


def _sheet_browser(rd: _SheetReads) -> Dict:
    session = None
    if isinstance(rd.bsess, dict):
        sess = rd.bsess
        url = str(sess.get("last_url")
                  or sess.get("nav_target") or "")
        session = {
            "site": _site_of(url), "url": url,
            "title": str(sess.get("last_title") or ""),
            "goal": str(sess.get("goal") or ""),
            "control": str(sess.get("control") or ""),
            "started": _f(sess, "started"),
            "last_action": _f(sess, "last_action")}
    pending = None
    if isinstance(rd.bpend, dict):
        url = _pending_url(rd.bpend)
        pending = {"site": _site_of(url), "url": url,
                   "goal": _pending_goal(rd.bpend)}
    return {"unavailable": rd.bsess_err is not None,
            "session": session, "pending": pending,
            "empty_line": "No browser session right now "
                          "— ask OG to open a site and "
                          "it shows up here."}


# --- Profile sheet: Scheduled ---------------------------------------------------------------------


def _watch_label(w: Dict) -> str:
    try:
        import og_watch
        fn = getattr(og_watch, "_criteria_text", None)
        if callable(fn):
            text = str(fn(w) or "").strip()
            if text:
                return text
    except Exception:
        pass
    text = str(w.get("item") or w.get("query") or "").strip()
    return text or "Watch"


def _sheet_scheduled(rd: _SheetReads) -> Dict:
    rows: List[Dict] = []
    if rd.watches_err is None:
        for w in rd.watches or []:
            url = str(w.get("url") or "")
            if "facebook.com" in url and "marketplace" in url:
                detail = "Facebook Marketplace"
            else:
                detail = _site_of(url)
            status = str(w.get("status") or "")
            cadence = w.get("cadence_min")
            cadence_min = int(cadence) if isinstance(
                cadence, (int, float)) else None
            if status == "active":
                status_label = (
                    f"Checking about every {cadence_min} min"
                    if cadence_min is not None
                    else "Checking")
            elif status == "waiting_login":
                status_label = ("Waiting for your "
                                "Facebook log-in")
            elif status == "paused_checkpoint":
                status_label = "Paused — checkpoint"
            else:
                status_label = status
            rows.append({
                "label": _watch_label(w), "detail": detail,
                "status": status,
                "status_label": status_label,
                "cadence_min": cadence_min,
                "created": str(w.get("created") or "")})
    rows.sort(key=lambda r: (
        0 if r["status"] == "active" else 1, r["created"]))
    return {"unavailable": rd.watches_err is not None,
            "rows": rows,
            "empty_line": "Nothing scheduled — watches "
                          "you set show up here."}


# --- Profile sheet: Identity (view-only) -------------------------------------------------------------


def _sheet_identity(rd: _SheetReads) -> Dict:
    entry = rd.mem_entry
    if entry is None:
        memory = {"available": False, "entries": None,
                  "updated": None,
                  "line": "Memory store unavailable "
                          "right now."}
    elif isinstance(entry, dict) and not entry:
        memory = {"available": True, "entries": 0,
                  "updated": None,
                  "line": "Nothing saved yet — OG's "
                          "memory for you starts with "
                          "your first chats."}
    elif isinstance(entry, dict):
        history = entry.get("history")
        updated = entry.get("updated")
        if not isinstance(updated, (str, int, float)) \
                or isinstance(updated, bool):
            updated = None
        memory = {"available": True,
                  "entries": len(history)
                  if isinstance(history, list) else 0,
                  "updated": updated, "line": None}
    else:
        memory = {"available": False, "entries": None,
                  "updated": None,
                  "line": "Memory store unavailable "
                          "right now."}
    return {
        "persona": {
            "name": "OG", "locked": True,
            "line": "OG's persona is set by the owner "
                    "and locked — this card is view "
                    "only."},
        "memory": memory,
        "account": rd.account,
    }


# --- Profile sheet payload --------------------------------------------------------------------------------


def build_sheet(uid: str, request=None,
                tzoff_minutes=0) -> Dict:
    """The profile sheet payload for ONE account: a
    live status line plus the five tabs, every section
    independently fail-soft — a dead store yields that
    section's unavailable/empty state, never an
    exception out of here. tzoff_minutes is the
    viewer's UTC offset in minutes, clamped to
    [-840, 840] (non-numeric reads as 0); the Today tab
    filters on the viewer's own midnight."""
    try:
        tzoff = int(float(tzoff_minutes))
    except (TypeError, ValueError):
        tzoff = 0
    tzoff = max(-840, min(840, tzoff))
    now = time.time()
    day_start = (math.floor((now + tzoff * 60) / 86400)
                 * 86400 - tzoff * 60)
    rd = _SheetReads(uid)
    try:
        status = _sheet_status(rd)
    except Exception:
        status = {"text": "Idle", "kind": "idle",
                  "detail": None}
    try:
        today = _sheet_today(rd, float(day_start))
    except Exception:
        today = {"unavailable": True, "rows": [],
                 "empty_line": "Nothing yet today — what "
                               "OG does for you today "
                               "shows up here."}
    try:
        approvals = _sheet_approvals(rd, now)
    except Exception:
        approvals = {"unavailable": True, "pending": [],
                     "history": [],
                     "empty_line": "Nothing waiting for "
                                   "your approval right "
                                   "now."}
    try:
        browser = _sheet_browser(rd)
    except Exception:
        browser = {"unavailable": True, "session": None,
                   "pending": None,
                   "empty_line": "No browser session "
                                 "right now — ask OG to "
                                 "open a site and it "
                                 "shows up here."}
    try:
        scheduled = _sheet_scheduled(rd)
    except Exception:
        scheduled = {"unavailable": True, "rows": [],
                     "empty_line": "Nothing scheduled — "
                                   "watches you set show "
                                   "up here."}
    try:
        identity = _sheet_identity(rd)
    except Exception:
        identity = {
            "persona": {
                "name": "OG", "locked": True,
                "line": "OG's persona is set by the "
                        "owner and locked — this card "
                        "is view only."},
            "memory": {"available": False,
                       "entries": None, "updated": None,
                       "line": "Memory store unavailable "
                               "right now."},
            "account": None}
    return {"ok": True, "generated_at": now,
            "status": status, "today": today,
            "approvals": approvals, "browser": browser,
            "scheduled": scheduled, "identity": identity}


# --- Routes ------------------------------------------------------------------------------
# Plain def handlers (Round 321 main-thread rule): the bodies are sync
# store reads; FastAPI runs def handlers in its threadpool.


def register_crewdash_routes(app):

    @app.get("/dashboards/state")
    def dashboards_state_route(request: Request):
        return dashboards_state()

    @app.get("/crewdash/data")
    def crewdash_data_route(request: Request):
        if not manager_enabled():
            return JSONResponse({"error": "not found"},
                                status_code=404)
        uid = request.cookies.get(UID_COOKIE) or ""
        return build_data(uid, request)

    @app.get("/crewdash/sheet")
    def crewdash_sheet_route(request: Request):
        if not profile_sheet_enabled():
            return JSONResponse({"error": "not found"},
                                status_code=404)
        uid = request.cookies.get(UID_COOKIE) or ""
        tzoff = request.query_params.get("tzoff")
        return build_sheet(uid, request,
                             tzoff if tzoff is not None else 0)

    @app.get("/crewdash/export")
    def crewdash_export_route(request: Request):
        if not manager_enabled():
            return JSONResponse({"error": "not found"},
                                status_code=404)
        uid = request.cookies.get(UID_COOKIE) or ""
        return JSONResponse(
            build_export(uid),
            headers={"Content-Disposition":
                     'attachment; filename="og-crewdash-export.json"'})
