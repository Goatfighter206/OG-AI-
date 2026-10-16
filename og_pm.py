"""Round 109: THE PROJECT MANAGER (Brent's order,
2026-10-12 11:04 PDT: "Project manager helper").

WHAT THIS IS. OG's crew (Round 81: Researcher, Runner, +
Coder/Watcher/Maker folded in by name through og_progress)
gains a foreman. The user hands OG a whole project in chat
("plan my kitchen remodel research", "get the truck ready
to sell") and the PM:

  1. writes a PLAN — a short ordered step list, stored as a
     project row in this module's own per-account store and
     surfaced on the Progress seat through og_progress
     (which reads this module's seam, duplicating no state —
     the og_crew/og_progress pattern);
  2. DISPATCHES the existing crew for the steps inside
     their lanes — Researcher for lookups, Runner for
     read-only browser errands — through og_crew's own
     dispatch (crew_results), inside the SAME caps
     (3 global / 2 per user) and the SAME switch family
     (OG_PM_ENABLED unset = ON, under the crew master
     OG_CREW_ENABLED);
  3. TRACKS + REPORTS — when a step lands the project
     advances; when the project finishes (or parks on a
     fence) the summary lands at the TOP of the visitor's
     next chat exchange plus an og_notify notice (the R81
     delivery pattern): done / doing / waiting-on-you, per
     step.

THE FENCES (unchanged, and the PM never widens them):
- The PM prepares and coordinates ONLY — it holds NO new
  powers. A step that reaches a fence (buy / order / send /
  pay / post / delete / book) is NEVER dispatched: it parks
  as a normal approval ask to the user (waiting-on-you),
  exactly the R81/R53 pattern. Watcher/Maker steps (their
  machinery lives behind their own chat claims) likewise
  park waiting-on-you with the exact ask the user can say.
- Re-planning: a dispatched step that FAILS SOFT (its crew
  job fails) is re-planned ONCE (re-dispatched); a second
  failure parks the step waiting-on-you with the reason —
  no silent loops, no infinite retries.
- NO new always-on loop (Round 72 doctrine): the PM
  advances on user exchanges (its seam), on its status
  route, and on Progress reads — never on a timer.
- Per-account isolation: a project and its steps belong to
  the account that started them; every read seam is keyed
  by the caller's own uid.
- Persona untouched: the PM reports in OG's own voice —
  OG wearing a foreman's hat, not a new character.

STATE. Own durable store, the Round 18 pattern (mirroring
og_crew): Postgres table og_pm_data when OG_MEMORY_DB_URL
is set, else a JSON file. One blob per visitor per kind:
'projects' + 'reports'. Persona files are never touched;
app.py is never touched (install rides the og_fixqueue
chain, routes ride og_library).
"""

import json
import logging
import os
import re
import threading
import time
import urllib.parse
import uuid
from typing import Dict, List, Optional

import og_garble

from fastapi import Request

logger = logging.getLogger(__name__)

# --- Config -------------------------------------------------------------------

MAX_STEPS = 6            # plan steps incl. the closing report step
PROJECT_KEEP = 20        # finished/cancelled projects kept per visitor
REPORT_CAP = 2600        # chars of a project summary delivery
STEP_SNIPPET = 700       # chars of a crew report kept per step
LINGER_S = 2 * 3600.0    # done projects linger on the board

PM_STORE_FILE = "pm_store.json"
# RLock, not Lock: store updates hold the lock across a
# read-modify-write whose save path re-enters it through
# _put_blob's file branch (the og_crew shape).
_pm_lock = threading.RLock()

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, DB_OPTIONS as _DB_OPTS, StoreError, apply_bounds as _apply_bounds
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None


# --- Switches -------------------------------------------------------------------

def pm_enabled() -> bool:
    """OG_PM_ENABLED=false is the PM's own kill switch (any
    other value, or unset, leaves the PM on) — under the crew
    master: with the crew off, the PM can still take a
    project but no step can be dispatched."""
    return os.environ.get(
        "OG_PM_ENABLED", "true").strip().lower() not in (
            "0", "false", "no", "off")


# --- Durable store (Round 18 pattern, mirroring og_crew) ------------------------

def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=_DB_CT, options=_DB_OPTS)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_pm_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(PM_STORE_FILE):
        try:
            with open(PM_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"PM store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(PM_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"PM store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_pm_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"PM DB load failed: {e}")
            raise StoreError("pm store unavailable (Round 99)") from e
    with _pm_lock:
        store = _load_file_store()
    entry = (store.get(uid) or {}).get(kind)
    return entry if entry is not None else default


def _put_blob(uid: str, kind: str, value):
    if not uid:
        return
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_pm_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"PM DB save failed: {e}")
            raise StoreError("pm store unavailable (Round 99)") from e
    with _pm_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


# --- Projects -------------------------------------------------------------------

_STEP_STATES_DONE = ("done",)


def _projects(uid: str) -> List[Dict]:
    got = _blob(uid, "projects", [])
    return got if isinstance(got, list) else []


def _save_projects(uid: str, projects: List[Dict]):
    dead = [p for p in projects
            if p.get("state") in ("done", "cancelled")]
    live = [p for p in projects
            if p.get("state") not in ("done", "cancelled")]
    dead.sort(key=lambda p: float(p.get("created", 0) or 0),
              reverse=True)
    _put_blob(uid, "projects", live + dead[:PROJECT_KEEP])


def _get_project(uid: str, project_id: str) -> Optional[Dict]:
    for p in _projects(uid):
        if isinstance(p, dict) and p.get("id") == project_id:
            return p
    return None


def _update_project(uid: str, project_id: str, **fields) -> Optional[Dict]:
    with _pm_lock:
        projects = _projects(uid)
        hit = None
        for p in projects:
            if isinstance(p, dict) and p.get("id") == project_id:
                p.update(fields)
                p["updated"] = time.time()
                hit = p
                break
        if hit is not None:
            _save_projects(uid, projects)
        return hit


def _front_step(proj: Dict) -> Optional[Dict]:
    """The first step not done — the step the project is on."""
    for s in proj.get("steps") or []:
        if isinstance(s, dict) and s.get("state") not in _STEP_STATES_DONE:
            return s
    return None


def _steps_done(proj: Dict) -> int:
    return sum(1 for s in proj.get("steps") or []
               if isinstance(s, dict) and s.get("state") == "done")


def projects_for(uid: str) -> List[Dict]:
    """The board's read seam (og_progress + /pm/status): the
    visitor's own project views, newest first."""
    out = []
    for p in _projects(uid):
        if not isinstance(p, dict):
            continue
        steps = []
        for s in p.get("steps") or []:
            if not isinstance(s, dict):
                continue
            steps.append({
                "n": s.get("n", 0), "title": s.get("title", ""),
                "lane": s.get("lane", ""), "state": s.get("state", ""),
                "note": s.get("note", ""),
            })
        out.append({
            "id": p.get("id", ""), "title": p.get("title", ""),
            "goal": p.get("goal", ""), "state": p.get("state", ""),
            "created": float(p.get("created", 0) or 0),
            "updated": float(p.get("updated", 0) or 0),
            "steps": steps,
            "done_count": _steps_done(p),
            "total": len(steps),
        })
    out.sort(key=lambda x: -x["created"])
    return out


# --- Reports (the R81 delivery pattern: notify + prepend next exchange) ---------

def _reports(uid: str) -> List[Dict]:
    got = _blob(uid, "reports", [])
    return got if isinstance(got, list) else []


def _save_reports(uid: str, reports: List[Dict]):
    reports.sort(key=lambda r: float(r.get("ts", 0) or 0))
    _put_blob(uid, "reports", reports[-20:])


def _add_report(uid: str, title: str, body: str):
    """Queue a project report for the next exchange AND drop
    the og_notify notice (the crew _finish pattern)."""
    rep = {"id": uuid.uuid4().hex[:12], "title": title,
           "body": body[:REPORT_CAP], "ts": time.time(),
           "delivered": False}
    with _pm_lock:
        reps = _reports(uid)
        reps.append(rep)
        _save_reports(uid, reps)
    try:
        import og_notify
        og_notify.record(uid, "notice", title[:160],
                         body[:600], None)
    except Exception as e:
        logger.warning(f"PM notify failed: {e}")
    return rep


def _pending_reports(uid: str) -> List[Dict]:
    out = []
    stale = []
    now = time.time()
    for r in _reports(uid):
        if not isinstance(r, dict):
            continue
        if not r.get("delivered"):
            if now - float(r.get("ts", 0) or 0) > 24 * 3600:
                stale.append(r.get("id", ""))
            else:
                out.append(r)
    if stale:
        with _pm_lock:
            reps = _reports(uid)
            for r in reps:
                if r.get("id") in stale:
                    r["delivered"] = True
            _save_reports(uid, reps)
    out.sort(key=lambda r: float(r.get("ts", 0) or 0))
    return out[:2]


def _mark_delivered(uid: str, report_id: str):
    with _pm_lock:
        reps = _reports(uid)
        for r in reps:
            if r.get("id") == report_id:
                r["delivered"] = True
        _save_reports(uid, reps)


def _delivery_results(reports: List[Dict]) -> List[Dict]:
    out = []
    for r in reports:
        out.append({
            "title": f"📋 Project Manager — {r.get('title', '')}",
            "body": ("FIRST — before answering anything else — "
                     "deliver this project report, plainly and "
                     "in persona, with the facts exactly as "
                     "written, THEN answer their message:\n"
                     + str(r.get("body") or "")),
            "href": ""})
    return out


def _result(tag: str, title: str, body: str,
            href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body,
             "href": href}]


# --- The planner (deterministic decomposition — the model narrates, the PM
# --- stores structure; a plan the tests can't predict is a plan that lies) -----

_LANE_LABELS = {"researcher": "Researcher", "runner": "Runner",
                "watcher": "Watcher", "maker": "Maker",
                "fence": "Needs your approval", "report": "Wrap-up"}

_FENCE_RE = re.compile(
    r"\b(buy|order|purchase|pay for|pay|send|email|post|delete|"
    r"book|reserve|call|text|message|sign up for|subscribe)\b",
    re.I)
_WATCH_RE = re.compile(
    r"\b(watch|track|monitor|alert me|keep an eye)\b", re.I)
_MAKER_RE = re.compile(
    r"\b(make|create|build|design|generate)\b[^.?!]*"
    r"\b(video|song|podcast|book|image|picture|design|poster|"
    r"3d model|model|story|clip)\b", re.I)
_RESEARCH_RE = re.compile(
    r"\b(research|look into|find out|investigate|compare|"
    r"prices?|costs?|reviews?|options?|best)\b", re.I)


def _clean_topic(goal: str) -> str:
    t = re.sub(r"\s+", " ", str(goal or "")).strip(" .!?")
    return t[:200]


def _plan_steps(goal: str) -> List[Dict]:
    """Decompose the goal into ordered steps. Dispatchable
    lanes (researcher / runner) run through the crew; the
    fence, watcher and maker lanes are born waiting-on-you —
    the PM coordinates them, it never performs them."""
    steps: List[Dict] = []
    low = " " + goal.lower() + " "

    def add(lane, title, **extra):
        step = {"n": len(steps) + 1, "lane": lane,
                "title": title[:110], "state": "queued",
                "job_id": "", "note": "", "attempts": 0,
                "snippet": ""}
        step.update(extra)
        steps.append(step)

    # Fenced clauses: split the goal on step separators and
    # pull out any clause carrying a fence verb — it becomes
    # its own approval-ask step, never a dispatch.
    clauses = re.split(r"\s*(?:;|\bthen\b|\band then\b)\s*",
                         goal)
    fence_clauses = [c.strip() for c in clauses
                     if c.strip() and _FENCE_RE.search(c)]
    rest = " ".join(c for c in clauses
                    if c.strip() and not _FENCE_RE.search(c))
    rest = rest.strip() or goal

    url, host = "", ""
    try:
        import og_browser_claim as _claim
        url = _claim._extract_url(goal) \
            or _claim._resolve_site_url(goal.lower())
        if url:
            host = (urllib.parse.urlparse(url).hostname
                    or "").lower()
    except Exception:
        url, host = "", ""

    if _RESEARCH_RE.search(rest) or not (url or fence_clauses):
        topic_src = rest
        if url:
            parts = [c for c in re.split(
                r"\s*(?:;|,|\bthen\b|\band then\b)\s*", rest)
                if c.strip() and url not in c
                and (not host or host not in c)]
            topic_src = " ".join(parts).strip() or rest
        topic = _clean_topic(topic_src).strip(" ,")
        topic = re.sub(
            r"^(?:research|look into|investigate)\s+", "",
            topic, flags=re.I)
        if topic:
            add("researcher", f"Research {topic}",
                topic=topic)
    if url:
        add("runner", f"Read {host or url}",
            url=url, focus="")
    for c in clauses:
        if c.strip() and _WATCH_RE.search(c) \
                and not _FENCE_RE.search(c):
            add("watcher", c.strip().capitalize(),
                note="Watches are set in chat with their own "
                     "ask — say the watch you want (e.g. "
                     "\"watch <what> for <change>\") and this "
                     "step is covered.")
            break
    m = _MAKER_RE.search(goal)
    if m and not any(s["lane"] == "maker" for s in steps):
        add("maker", f"Make the {m.group(2)}",
            note="Creations start from chat in their own "
                 "lane — ask OG to make it and this step is "
                 "covered.")
    for c in fence_clauses:
        add("fence", c.strip().capitalize(),
            note="This step touches a fence (money, a send, "
                 "a post, a booking, or a deletion). The PM "
                 "never crosses a fence itself — it parks "
                 "here as your approval ask.")
    for s in steps:
        if s["lane"] in ("watcher", "maker", "fence"):
            s["state"] = "waiting"
    add("report", "Wrap-up: the project report")
    if len(steps) > MAX_STEPS:
        steps = steps[:MAX_STEPS - 1] + steps[-1:]
    for i, s in enumerate(steps):
        s["n"] = i + 1
    return steps


def _plan_text(proj: Dict) -> str:
    lines = [f"Project started — \"{proj.get('title', '')}\"",
             "The plan, in order:"]
    for s in proj.get("steps") or []:
        label = _LANE_LABELS.get(s.get("lane"), "Step")
        lines.append(f"{s.get('n')}. [{label}] {s.get('title')}")
    return "\n".join(lines)


def _steps_status_text(proj: Dict) -> str:
    marks = {"done": "done", "working": "doing",
             "queued": "queued", "waiting": "waiting on you"}
    lines = []
    for s in proj.get("steps") or []:
        st = marks.get(s.get("state"), s.get("state", ""))
        line = f"{s.get('n')}. {s.get('title')} — {st}"
        if s.get("state") == "waiting" and s.get("note"):
            line += f" ({s['note'][:120]})"
        lines.append(line)
    return "\n".join(lines)


# --- Dispatch (through og_crew's own dispatch — same store, same caps) ---------

def _bound_job_ids(uid: str) -> set:
    ids = set()
    for p in _projects(uid):
        for s in p.get("steps") or []:
            if isinstance(s, dict) and s.get("job_id"):
                ids.add(s["job_id"])
    return ids


def _dispatch_step(uid: str, proj: Dict, step: Dict) -> str:
    """Dispatch one dispatchable step through the crew.
    Returns the step's new state. The crew job is bound by
    matching the newest unbound job this dispatch created
    (role + payload + creation time) in og_crew's own store —
    the PM reads the crew's seam, never re-implements it."""
    import og_crew
    if not og_crew.crew_enabled():
        step["note"] = ("The crew is switched off right now "
                        "(OG_CREW_ENABLED), so this step can't "
                        "go out yet.")
        return step["state"]
    lane = step.get("lane")
    claim = None
    if lane == "researcher":
        claim = {"op": "research", "topic": step.get("topic", "")}
    elif lane == "runner":
        claim = {"op": "runner", "url": step.get("url", ""),
                 "focus": step.get("focus", "")}
    if claim is None:
        return step["state"]
    t0 = time.time()
    bound = _bound_job_ids(uid)
    results = []
    try:
        results = og_crew.crew_results(claim, uid)
    except Exception as e:
        logger.warning(f"PM dispatch failed: {e}")
        step["note"] = ("The dispatch glitched before anything "
                        "was charged or started.")
        step["state"] = "waiting"
        return step["state"]
    found = None
    try:
        for j in og_crew._jobs(uid):
            if not isinstance(j, dict):
                continue
            if j.get("id") in bound:
                continue
            if float(j.get("created", 0) or 0) < t0 - 2.0:
                continue
            if lane == "researcher" and j.get("role") == "researcher" \
                    and j.get("detail") == step.get("topic", ""):
                found = j
                break
            if lane == "runner" and j.get("role") == "runner" \
                    and j.get("detail") == step.get("url", ""):
                found = j
                break
    except Exception as e:
        logger.warning(f"PM job bind failed: {e}")
    if found is None:
        # The crew refused (cap, budget, gate) — its own
        # words are the reason; the step parks on the user.
        body = ""
        if results:
            body = str(results[0].get("body") or "")
            body = re.sub(r"^\[OG CREW\]\s*", "", body)
        step["note"] = (body[:220] or
                        "The crew couldn't take this step.")
        step["state"] = "waiting"
        return step["state"]
    step["job_id"] = found.get("id", "")
    js = found.get("state") or "queued"
    # A job that already landed (done/failed/cancelled)
    # before the bind is NOT mirrored raw — the step binds
    # as working and _advance processes the terminal state
    # through the normal paths (complete / re-plan / park).
    step["state"] = js if js in ("queued", "working") \
        else "working"
    return step["state"]


# --- Advance (the state machine; runs on exchanges + reads, never a timer) -----

def _park(uid: str, proj: Dict, step: Dict, reason: str):
    """Park the project on the user: state waiting + ONE
    report per park per step (the approval ask)."""
    pid = proj.get("id", "")
    if proj.get("parked_for") == step.get("n") \
            and proj.get("state") == "waiting":
        return
    _update_project(uid, pid, state="waiting",
                    parked_for=step.get("n"))
    proj["state"] = "waiting"
    proj["parked_for"] = step.get("n")
    body = (f"Your project \"{proj.get('title', '')}\" is "
            f"parked on step {step.get('n')} of "
            f"{len(proj.get('steps') or [])} — it needs you.\n"
            + _steps_status_text(proj)
            + f"\nWhy it's parked: {reason}")
    _add_report(uid, f"Project — {proj.get('title', '')}",
                body)


def _complete(uid: str, proj: Dict):
    pid = proj.get("id", "")
    _update_project(uid, pid, state="done",
                    finished=time.time(), parked_for=None)
    proj["state"] = "done"
    lines = [f"Project done — \"{proj.get('title', '')}\"",
             "How it landed, step by step:"]
    for s in proj.get("steps") or []:
        if s.get("lane") == "report":
            continue
        line = f"{s.get('n')}. {s.get('title')} — done"
        if s.get("note") and "handled" in s.get("note", ""):
            line += f" ({s['note'][:100]})"
        lines.append(line)
        if s.get("snippet"):
            lines.append("   " + s["snippet"][:STEP_SNIPPET])
    _add_report(uid, f"Project — {proj.get('title', '')}",
                "\n".join(lines))


def _step_failed(uid: str, proj: Dict, step: Dict,
                 reason: str):
    """A dispatched step failed soft. Re-plan ONCE
    (re-dispatch); a second failure parks waiting-on-you
    with the reason — never a silent loop."""
    step["attempts"] = int(step.get("attempts", 0) or 0) + 1
    if step["attempts"] <= 1 and step.get("lane") in (
            "researcher", "runner"):
        step["job_id"] = ""
        step["note"] = ("First try failed soft — the PM "
                        "re-planned this step once and sent "
                        "it out again.")
        _dispatch_step(uid, proj, step)
        if step["state"] in ("working", "queued"):
            return
        # The re-dispatch itself was refused — park with
        # the refusal as the reason (note already set).
        _park(uid, proj, step,
              step.get("note") or reason)
        return
    step["state"] = "waiting"
    step["note"] = (f"This step failed twice — the last "
                    f"failure: {reason[:160]}")
    _park(uid, proj, step, step["note"])


def _advance(uid: str):
    """Move every live project one honest notch. Idempotent;
    called from the chat seam, the status route and the
    Progress read — the crew's ticks are the user's turns."""
    if not uid:
        return
    try:
        import og_crew
    except Exception:
        og_crew = None
    for proj in _projects(uid):
        if not isinstance(proj, dict):
            continue
        if proj.get("state") not in ("active", "waiting"):
            continue
        try:
            _advance_project(uid, proj, og_crew)
        except Exception as e:
            logger.warning(f"PM advance failed: {e}")


def _advance_project(uid: str, proj: Dict, og_crew):
    pid = proj.get("id", "")
    steps = proj.get("steps") or []
    dirty = False
    for step in steps:
        st = step.get("state")
        if st == "done":
            continue
        lane = step.get("lane")
        if lane == "report":
            _complete(uid, proj)
            dirty = True
            break
        if st == "waiting":
            # Born-waiting lane (fence/watcher/maker) or a
            # parked step: the project waits on the user.
            if proj.get("state") != "waiting" \
                    or proj.get("parked_for") != step.get("n"):
                _park(uid, proj, step,
                      step.get("note") or
                      "This step needs you before the "
                      "project can move.")
            break
        if st in ("queued", "working"):
            if step.get("job_id"):
                # Dispatched: mirror the crew job's state.
                if og_crew is None:
                    break
                job = None
                try:
                    job = og_crew._get_job(uid, step["job_id"])
                except Exception:
                    job = None
                if job is None:
                    _step_failed(uid, proj, step,
                                 "the crew job's record is gone")
                    dirty = True
                    break
                js = job.get("state")
                if js == "done":
                    step["state"] = "done"
                    step["snippet"] = str(
                        job.get("report") or "")[:STEP_SNIPPET]
                    dirty = True
                    continue  # next step may go out this pass
                if js == "failed":
                    rep = str(job.get("report") or "")
                    first = rep.strip().split("\n")[0][:160] \
                        if rep.strip() else "the job failed"
                    _step_failed(uid, proj, step, first)
                    dirty = True
                    break
                if js == "cancelled":
                    step["state"] = "waiting"
                    step["note"] = ("The crew job for this "
                                    "step was cancelled.")
                    dirty = True
                    _park(uid, proj, step, step["note"])
                    break
                if js in ("queued", "working") and js != st:
                    step["state"] = js
                    dirty = True
                break
            if st == "queued" and lane in ("researcher",
                                           "runner"):
                _dispatch_step(uid, proj, step)
                dirty = True
                if step.get("state") == "waiting":
                    _park(uid, proj, step,
                          step.get("note") or
                          "The crew couldn't take this step.")
                break
            if st == "queued":
                step["state"] = "waiting"
                dirty = True
                _park(uid, proj, step,
                      step.get("note") or "This step needs you.")
                break
            break
    if dirty:
        _update_project(uid, pid, steps=steps)


# --- Chat claims ------------------------------------------------------------------
#
# Narrow imperative frames only (house doctrine): a PM ask
# names a project, leads with "plan my/a/the …", or carries
# the get-ready shape. Plain asks are NOT claimed — and a
# message the CREW claims is never claimed here (checked
# first, exactly): the PM dispatches the crew, it doesn't
# compete with it.

_STATUS_RE = re.compile(
    r"\bproject status\b"
    r"|\bhow(?:'s| is) (?:the|my) project\b"
    r"|\bwhat(?:'s| is) my project (?:doing|up to)\b"
    r"|\bmy projects\b"
    r"|\bproject progress\b", re.I)
_CANCEL_RE = re.compile(
    r"\b(?:cancel|stop|call off)\b[^.?!]*\bproject\b", re.I)
_CONTINUE_RE = re.compile(
    r"\bcontinue (?:the|my) project\b"
    r"|\bthat (?:step|part) is done\b"
    r"|\bi (?:did|handled|took care of) (?:that|the) "
    r"(?:step|part)\b", re.I)
_PROJECT_VERB_RE = re.compile(
    r"\b(?:plan|start|set up|setup|create|make|run|manage|"
    r"build|kick off)\b[^.?!]*\bproject\b"
    r"|\bproject\b\s*[:\-–]", re.I)
_PLAN_LEAD_RE = re.compile(
    r"^\W*(?:please\s+)?plan\s+(?:my\s+|a\s+|the\s+)?"
    r"(.+?)\s*[.!?]*$", re.I)
_GET_READY_RE = re.compile(
    r"^\W*(?:please\s+)?get\s+(.+?)\s+ready\s+(?:to|for)\s+"
    r"(.+?)\s*[.!?]*$", re.I)

_GARBLE_VOCAB = ("project", "projects", "plan", "manager")


def _goal_from(msg: str) -> str:
    """The project's goal in the user's own words: the frame
    words come off, the payload passes verbatim."""
    raw = str(msg or "").strip()
    m = _GET_READY_RE.match(raw)
    if m:
        return f"{m.group(1)} ready to {m.group(2)}"
    m = re.search(r"\bproject\b\s*[:\-–]\s*(.+)$", raw, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"\bproject\b\s+(?:to\s+|for\s+)?(.+)$",
                  raw, re.I)
    if m and len(m.group(1).strip()) >= 3:
        return m.group(1).strip()
    m = _PLAN_LEAD_RE.match(raw)
    if m:
        return m.group(1).strip()
    out = re.sub(r"\b(?:plan|start|set up|create|make|run|"
                 r"manage|build)\b", " ", raw, flags=re.I)
    out = re.sub(r"\b(?:a|the|my)?\s*project\b", " ", out,
                 flags=re.I)
    return " ".join(out.split()).strip(" .!?")


def _claim_exact(message: str) -> Optional[Dict]:
    raw = str(message or "").strip()
    if not raw:
        return None
    # The crew's claims are never the PM's (dispatch, not
    # competition).
    try:
        import og_crew
        if og_crew._claim_exact(raw):
            return None
    except Exception:
        pass
    if _STATUS_RE.search(raw):
        return {"op": "pm_status"}
    if _CANCEL_RE.search(raw):
        return {"op": "pm_cancel"}
    if _CONTINUE_RE.search(raw):
        return {"op": "pm_continue"}
    if _PROJECT_VERB_RE.search(raw):
        goal = _goal_from(raw)
        if len(goal) >= 3:
            return {"op": "pm_create", "goal": goal[:240]}
        return {"op": "pm_create",
                "goal": raw.strip(" .!?")[:240]}
    m = _PLAN_LEAD_RE.match(raw)
    if m:
        rest = m.group(1).strip()
        if len(rest.split()) >= 2:
            return {"op": "pm_create", "goal": rest[:240]}
    m = _GET_READY_RE.match(raw)
    if m:
        return {"op": "pm_create",
                "goal": f"{m.group(1)} ready to "
                         f"{m.group(2)}"[:240]}
    return None


def _claim_pm(message: str):
    """(claim, garble_info): the exact claim first; on a
    miss, the Round 75 second chance — og_garble repairs the
    TRIGGER words only; the payload (the goal) passes
    verbatim from the repaired string, the og_crew pattern."""
    claim = _claim_exact(message)
    if claim:
        return claim, None
    try:
        repaired, info = og_garble.repair_info(
            str(message or ""), _GARBLE_VOCAB)
        if info:
            claim = _claim_exact(repaired)
            if claim:
                return claim, info
    except Exception:
        pass
    return None, None


# --- Operations -------------------------------------------------------------------

def _create_project(uid: str, goal: str) -> List[Dict]:
    goal = _clean_topic(goal) or "Untitled project"
    title = goal[:60]
    title = title[:1].upper() + title[1:]
    proj = {"id": uuid.uuid4().hex[:12], "uid": uid,
            "title": title, "goal": goal, "state": "active",
            "created": time.time(), "updated": time.time(),
            "finished": 0.0, "parked_for": None,
            "steps": _plan_steps(goal)}
    with _pm_lock:
        projects = _projects(uid)
        projects.append(proj)
        _save_projects(uid, projects)
    _advance(uid)
    fresh = _get_project(uid, proj["id"]) or proj
    body = (_plan_text(fresh) + "\n"
            + _steps_status_text(fresh)
            + "\nThe crew only goes out inside its usual caps, "
              "and anything fenced (buying, sending, posting, "
              "paying, deleting) parks as an approval ask to "
              "you — the PM never crosses a fence itself. "
              "Progress shows the project live. (Tell the user "
              "the plan and the step states exactly like that "
              "— do not invent step results before they land.)")
    return _result("OG PROJECT MANAGER",
                   "Project Manager — plan", body)


def _status_text(uid: str) -> str:
    _advance(uid)
    views = [p for p in projects_for(uid)
             if p.get("state") != "cancelled"]
    if not views:
        return ("No projects on the board right now. Hand me "
                "one — \"plan a project to …\" — and I'll run "
                "it step by step.")
    lines = ["Your projects:"]
    shown = 0
    for p in views:
        if p.get("state") == "done" and shown >= 3:
            continue
        if p.get("state") == "done" and \
                time.time() - float(p.get("updated", 0) or 0) \
                > LINGER_S and shown:
            continue
        shown += 1
        lines.append(f"\n\"{p.get('title', '')}\" — "
                     f"{p.get('state', '')} "
                     f"({p.get('done_count', 0)} of "
                     f"{p.get('total', 0)} steps done)")
        marks = {"done": "done", "working": "doing",
                 "queued": "queued", "waiting": "waiting on you"}
        for s in p.get("steps") or []:
            st = marks.get(s.get("state"), s.get("state", ""))
            line = (f"  {s.get('n')}. {s.get('title')} — {st}")
            if s.get("state") == "waiting" and s.get("note"):
                line += f" ({s['note'][:110]})"
            lines.append(line)
    return "\n".join(lines)


def _cancel_project(uid: str) -> List[Dict]:
    live = [p for p in _projects(uid)
            if isinstance(p, dict)
            and p.get("state") in ("active", "waiting")]
    if not live:
        return _result("OG PROJECT MANAGER",
                       "Project Manager — nothing to cancel",
                       "No project is running or parked right "
                       "now — nothing to cancel.")
    live.sort(key=lambda p: float(p.get("created", 0) or 0),
              reverse=True)
    p = live[0]
    _update_project(uid, p.get("id", ""), state="cancelled",
                    finished=time.time())
    return _result(
        "OG PROJECT MANAGER", "Project Manager — cancelled",
        f"Cancelled — the project \"{p.get('title', '')}\" is "
        "stopped. Any crew job already out on one of its steps "
        "keeps its own life and still reports to you here; "
        "cancel it on its own (\"cancel the research job\") if "
        "you want it stopped too.")


def _continue_project(uid: str) -> List[Dict]:
    _advance(uid)
    parked = [p for p in _projects(uid)
              if isinstance(p, dict)
              and p.get("state") == "waiting"]
    if not parked:
        return _result("OG PROJECT MANAGER",
                       "Project Manager — nothing parked",
                       "No project is parked on you right now — "
                       "nothing to continue.")
    parked.sort(key=lambda p: float(p.get("updated", 0) or 0),
                reverse=True)
    proj = parked[0]
    steps = proj.get("steps") or []
    front = _front_step(proj)
    if front is None or front.get("state") != "waiting":
        return _result("OG PROJECT MANAGER",
                       "Project Manager — nothing parked",
                       "That project isn't waiting on a step "
                       "right now.")
    front["state"] = "done"
    front["note"] = "You handled this one yourself — marked done by you."
    _update_project(uid, proj.get("id", ""), steps=steps,
                    state="active", parked_for=None)
    _advance(uid)
    fresh = _get_project(uid, proj.get("id", "")) or proj
    body = (f"Got it — step {front.get('n')} of "
            f"\"{fresh.get('title', '')}\" is marked done and "
            "the project is moving again.\n"
            + _steps_status_text(fresh))
    return _result("OG PROJECT MANAGER",
                   "Project Manager — continued", body)


def pm_results(claim: Dict, uid: str) -> List[Dict]:
    op = claim.get("op")
    if op == "pm_status":
        return _result("OG PROJECT MANAGER", "Your projects",
                       _status_text(uid))
    if op == "pm_cancel":
        return _cancel_project(uid)
    if op == "pm_continue":
        return _continue_project(uid)
    if op == "pm_create":
        return _create_project(uid, claim.get("goal", ""))
    return _result("OG PROJECT MANAGER", "Project Manager",
                   _status_text(uid))


# --- The seam ---------------------------------------------------------------------------

_pending = {"claim": None, "garble": None, "reports": []}


def install_pm_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search OUTSIDE every earlier
    seam (installed last, from the og_fixqueue chain — the
    R81 precedent) so project asks claim here, stored project
    reports deliver at the top of the next exchange (the
    og_watch/crew alert pattern), and everything else
    delegates back untouched. Persona files never touched."""
    if getattr(agent_instance, "_og_pm_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["claim"] = None
        _pending["garble"] = None
        _pending["reports"] = []
        try:
            uid = get_uid()
            if uid:
                # Advance on the exchange (no timer — R72):
                # completions detected now deliver now.
                try:
                    _advance(uid)
                except Exception as e:
                    logger.warning(f"PM advance failed: {e}")
                _pending["reports"] = _pending_reports(uid)
                if pm_enabled():
                    claim, info = _claim_pm(str(message))
                    if claim:
                        _pending["claim"] = claim
                        _pending["garble"] = info
                if _pending["claim"] or _pending["reports"]:
                    if isinstance(intent, dict):
                        intent["needs_code_generation"] = False
                        if not intent.get("needs_web_search"):
                            intent["needs_web_search"] = True
                            intent["search_query"] = \
                                str(message).strip()
        except Exception as e:
            logger.warning(f"PM trigger check failed: {e}")
            _pending["claim"] = None
            _pending["reports"] = []
        return intent

    def search_wrapped(query, num_results=5):
        claim = _pending.get("claim")
        garble = _pending.get("garble")
        reports = _pending.get("reports") or []
        _pending["claim"] = None
        _pending["garble"] = None
        _pending["reports"] = []
        uid = ""
        try:
            uid = get_uid()
        except Exception:
            uid = ""
        results = None
        if claim and uid:
            try:
                results = pm_results(claim, uid)
            except Exception as e:
                logger.warning(f"PM op failed: {e}")
                results = _result(
                    "OG PROJECT MANAGER",
                    "Project Manager — glitch",
                    "The project ask glitched — nothing was "
                    "started or charged. Say it again and I'll "
                    "retry.")
        if results is None:
            results = prev_search(query, num_results)
        if reports and results is not None:
            delivered = _delivery_results(reports)
            results = delivered + list(results)
            try:
                for r in reports:
                    _mark_delivered(uid, r.get("id", ""))
            except Exception as e:
                logger.warning(f"PM delivery mark failed: {e}")
        if garble and results:
            results = og_garble.annotate(results, garble,
                                         prepend=True)
        return results

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_pm_installed = True


# --- Routes ------------------------------------------------------------------------------

def register_pm_routes(app):

    @app.get("/pm/status")
    async def pm_status_route(request: Request):
        uid = request.cookies.get("ogai_uid") or ""
        if uid:
            _advance(uid)
        return {"ok": True, "enabled": pm_enabled(),
                "projects": projects_for(uid) if uid else []}
