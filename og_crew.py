"""Round 81: OG'S CREW (Brent's order, 2026-10-11: "Can you
also build OG helpers like you?" — "But he needs a crew.")

WHAT THIS IS. OG gains a named crew he can dispatch from
chat, the way a lead dispatches hands: OG stays in the
conversation; a helper works in the background on its own
thread; when it finishes, its report lands at the TOP of
the visitor's next chat exchange (the Round 18/29 delivery
seam) plus a notification, and the Progress seat shows the
job while it works.

THE ROSTER (one surface — crew_status() below, the chat
answer and GET /crew/status are the same payload):
  Researcher — NEW here: a deep web-lookup job.
  Runner     — NEW here: a read-only browser errand.
  Coder      — the existing fix-drafter (og_fixqueue);
               folded in by name, machinery untouched.
  Watcher    — the existing watches (og_watch/og_monitor);
               folded in by name, machinery untouched.
  Maker      — the existing generator jobs (video / song /
               podcast / book / motion / models / sfx /
               designer); folded in by name, untouched.
Only the Researcher and the Runner needed new machinery;
the rest are read into the roster from their own stores
through og_progress.collect_progress — the board's single
source. Nothing here duplicates a store or a state.

HARD RULES (Brent's standing doctrine, enforced in code):
- PREPARE + REPORT ONLY. No crew code path spends money,
  approves anything, or commits an action. The Runner's
  drive vocabulary is navigate + read — there is no click
  / type / send call in this module at all. Anything
  committal still parks as the usual in-chat approval.
- ON DEMAND ONLY (Round 72 diet): no scheduler, no idle
  loop. Threads start at dispatch or when a finishing
  thread promotes the oldest queued job.
- EXISTING CAPS ONLY. Researcher searches are PRE-CHARGED
  at dispatch, inside the chat turn (where the app's uid +
  tier context are the dispatcher's own), through the
  app's own _consume_lookup — the one lookup budget. The
  thread spends only its granted bundle and never touches
  app globals (see the round NOTES for why). Runner
  minutes ride og_watch's own watch_min ledger (its own
  _minutes_used/_add_minutes against og_tiers' cap) — the
  background-session pool, shared with watches, disclosed
  at dispatch. No new uncapped path exists.
- THREAD DISCIPLINE (Round 80): the box is 2 uvicorn
  workers on 1 vCPU; heavy work on the event loop caused
  the 502s. Every crew job runs on its own daemon thread,
  never on the loop, never behind the chat memory lock.
  Working cap: 3 globally / 2 per user, counted from the
  shared job store; over-cap dispatches QUEUE and the
  confirmation says so plainly.
- KILL SWITCH: OG_CREW_ENABLED=false stops dispatch and
  promotion cold; the roster ask then answers with a
  plain switched-off note. Working jobs finish (short,
  already paid for).

STATE. Own durable store, the Round 18 pattern: Postgres
table og_crew_data when OG_MEMORY_DB_URL is set, else a
JSON file. One blob per visitor: 'jobs'. Persona files
are never touched; app.py is never touched (install rides
the og_fixqueue chain, routes ride og_library).
"""

import json
import logging
import os
import re
import threading
import time
import urllib.parse
import uuid
from html.parser import HTMLParser
from typing import Dict, List, Optional

import og_garble
import og_tiers as _tiers

from fastapi import Request

logger = logging.getLogger(__name__)

# --- Config -------------------------------------------------------------------

GLOBAL_WORKING_CAP = 3       # R80 evidence: see round NOTES
USER_WORKING_CAP = 2
USER_QUEUED_CAP = 3
GLOBAL_QUEUED_CAP = 10
RESEARCH_BUNDLE = 4          # searches pre-charged per dispatch
RESEARCH_RESULTS = 6         # results kept per search
RESEARCH_FETCHES = 3         # pages read per research job
RUNNER_SESSION_MIN = 3       # the watch check's session budget
STALE_S = 900.0              # working job older = thread died
JOB_KEEP = 30                # finished jobs kept per visitor
REPORT_CAP = 4500            # chars of findings per job

CREW_STORE_FILE = "crew_store.json"
# RLock, not Lock: job updates hold the lock across a
# read-modify-write whose save path re-enters it through
# _put_blob's file branch.
_crew_lock = threading.RLock()

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, StoreError, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo, pooled_connect as _pooled_connect
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

# Lazily-resolved app deps (the og_podcast/og_respond
# precedent: never at import time, so no import cycle;
# bind_app overrides for tests). consume_lookup is the
# app's own budget function — the ONLY lookup ledger.
_deps: Dict = {}
_APP_ATTRS = {"consume_lookup": "_consume_lookup"}


def bind_app(deps):
    _deps.update(deps)


def _dep(name):
    if name in _deps:
        return _deps[name]
    if name == "get_tier":
        def _get_tier():
            try:
                import app as _appmod
                return str(_appmod._current_tier.get(
                    "tier", "free") or "free")
            except Exception:
                return "free"
        return _get_tier
    attr = _APP_ATTRS.get(name)
    if attr is None:
        return None
    try:
        import app as _appmod
        return getattr(_appmod, attr, None)
    except Exception as e:
        logger.warning(f"og_crew: app dep {name} unavailable: {e}")
        return None


# --- Switch ---------------------------------------------------------------------

def crew_enabled() -> bool:
    """OG_CREW_ENABLED=false is the kill switch (any other
    value, or unset, leaves the crew on) — the watch
    switch's shape."""
    return os.environ.get(
        "OG_CREW_ENABLED", "true").strip().lower() not in (
            "0", "false", "no", "off")


# --- Durable store (Round 18 pattern, mirroring og_watch) -----------------------

def _db_connect():
    conn = _pooled_connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_crew_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(CREW_STORE_FILE):
        try:
            with open(CREW_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Crew store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(CREW_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Crew store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_crew_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"Crew DB load failed: {e}")
            raise StoreError("crew store unavailable (Round 99)") from e
    with _crew_lock:
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
                        "INSERT INTO og_crew_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Crew DB save failed: {e}")
            raise StoreError("crew store unavailable (Round 99)") from e
    with _crew_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


def _all_uids() -> List[str]:
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT DISTINCT uid FROM og_crew_data")
                    return [r[0] for r in cur.fetchall() if r[0]]
        except Exception as e:
            logger.warning(f"Crew uid scan failed: {e}")
            raise StoreError("crew store unavailable (Round 99)") from e
    with _crew_lock:
        return list(_load_file_store().keys())


# --- Jobs -----------------------------------------------------------------------

def _jobs(uid: str) -> List[Dict]:
    got = _blob(uid, "jobs", [])
    return got if isinstance(got, list) else []


def jobs_for(uid: str) -> List[Dict]:
    """The board's read seam (og_progress): the visitor's own
    job records, newest first, trimmed to what a row needs."""
    out = []
    for j in _jobs(uid):
        if not isinstance(j, dict):
            continue
        out.append({
            "id": j.get("id", ""), "role": j.get("role", ""),
            "title": j.get("title", ""),
            "detail": j.get("detail", ""),
            "host": j.get("host", ""),
            "state": j.get("state", ""),
            "progress": j.get("progress"),
            "created": float(j.get("created", 0) or 0),
            "updated": float(j.get("updated", 0) or 0),
        })
    out.sort(key=lambda x: -x["created"])
    return out


def _save_jobs(uid: str, jobs: List[Dict]):
    done = [j for j in jobs
            if j.get("state") in ("done", "failed", "cancelled")]
    live = [j for j in jobs
            if j.get("state") not in ("done", "failed", "cancelled")]
    done.sort(key=lambda j: float(j.get("created", 0) or 0),
              reverse=True)
    _put_blob(uid, "jobs", live + done[:JOB_KEEP])


def _update_job(uid: str, job_id: str, **fields) -> Optional[Dict]:
    with _crew_lock:
        jobs = _jobs(uid)
        hit = None
        for j in jobs:
            if isinstance(j, dict) and j.get("id") == job_id:
                j.update(fields)
                j["updated"] = time.time()
                hit = dict(j)
                break
        if hit is not None:
            _save_jobs(uid, jobs)
        return hit


def _get_job(uid: str, job_id: str) -> Optional[Dict]:
    for j in _jobs(uid):
        if isinstance(j, dict) and j.get("id") == job_id:
            return dict(j)
    return None


def _counts() -> Dict:
    """Working/queued counts across ALL visitors, from the
    shared store — both app workers see the same numbers."""
    working = 0
    queued = 0
    per_user_working: Dict[str, int] = {}
    per_user_queued: Dict[str, int] = {}
    for uid in _all_uids():
        for j in _jobs(uid):
            if not isinstance(j, dict):
                continue
            st = j.get("state")
            if st == "working":
                working += 1
                per_user_working[uid] = \
                    per_user_working.get(uid, 0) + 1
            elif st == "queued":
                queued += 1
                per_user_queued[uid] = per_user_queued.get(uid, 0) + 1
    return {"working": working, "queued": queued,
            "per_user_working": per_user_working,
            "per_user_queued": per_user_queued}


def _result(tag: str, title: str, body: str,
            href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body,
             "href": href}]


# --- Chat claims ------------------------------------------------------------------
#
# Narrow imperative frames only (house doctrine): a crew ask
# names the crew, a researcher/runner, or starts with a
# research verb. Plain lookups ("search for…", "what's…")
# and plain browser asks ("open facebook") are NOT claimed
# here — those seams keep their asks.

_STATUS_RE = re.compile(
    r"\bwhat(?:'s| is) (?:the|your|my) crew doing\b"
    r"|\bcrew status\b"
    r"|\bwho(?:'s| is) on (?:your|the|my) crew\b"
    r"|\bwhat are your helpers doing\b"
    r"|\bhow(?:'s| is) (?:the|your) crew\b", re.I)
_CANCEL_RE = re.compile(
    r"\b(?:cancel|stop|call off)\b[^.?!]*"
    r"\b(?:crew|research|researcher|runner|that job|the job)\b",
    re.I)
_RESEARCH_LEAD_RE = re.compile(
    r"^\W*(?:please\s+)?(?:research|investigate)\s+(?:on\s+|into\s+)?"
    r"(.+?)\s*[.!?]*$", re.I)
_DEEPDIVE_RE = re.compile(
    r"\bdeep\s*dive\s+(?:on|into|about)\s+(.+?)\s*[.!?]*$", re.I)
_CREW_RESEARCH_RE = re.compile(
    r"\b(?:have|send|put|get)\s+(?:your\s+|the\s+|my\s+)?"
    r"(?:researcher|crew)\b[^.?!]*?"
    r"(?:look\s+into|research|investigate|dig\s+into|check\s+out|on)\s+"
    r"(.+?)\s*[.!?]*$", re.I)
_CREW_RESEARCH_BARE_RE = re.compile(
    r"\bresearcher\b\s*[:,-]?\s*(?:look\s+into\s+|research\s+|on\s+)?"
    r"(.+?)\s*[.!?]*$", re.I)
_RUNNER_RE = re.compile(
    r"\b(?:send|have|put|get)\s+(?:your\s+|the\s+|my\s+)?runner\b"
    r"|\brunner\b\s*[:,-]", re.I)

_GARBLE_VOCAB = ("research", "researcher", "runner", "crew",
                 "investigate")


def _claim_exact(message: str) -> Optional[Dict]:
    raw = str(message or "").strip()
    if not raw:
        return None
    if _STATUS_RE.search(raw):
        return {"op": "crew_status"}
    if _CANCEL_RE.search(raw):
        return {"op": "crew_cancel"}
    m = _RESEARCH_LEAD_RE.match(raw) or _DEEPDIVE_RE.search(raw) \
        or _CREW_RESEARCH_RE.search(raw)
    topic = ""
    if m:
        topic = (m.group(1) or "").strip()
    elif _CREW_RESEARCH_BARE_RE.search(raw):
        topic = (_CREW_RESEARCH_BARE_RE.search(raw).group(1)
                 or "").strip()
    if topic and len(topic) >= 3:
        return {"op": "research", "topic": topic[:240]}
    if _RUNNER_RE.search(raw):
        try:
            import og_browser_claim as _claim
            url = _claim._extract_url(raw) \
                or _claim._resolve_site_url(raw.lower())
        except Exception:
            url = ""
        if url:
            focus = _runner_focus(raw, url)
            return {"op": "runner", "url": url, "focus": focus}
    return None


def _runner_focus(raw: str, url: str) -> str:
    """The errand's errand-words: what's left of the ask after
    the runner frame and the site itself are removed. Used
    ONLY to pick which page lines lead the report — the
    words are the visitor's own, verbatim."""
    text = " " + raw.lower() + " "
    text = re.sub(r"\b(?:send|have|put|get|your|the|my|runner|"
                  r"please|check|to|on|go|and|see|find|look|at|"
                  r"for|of|what|is|it|out)\b", " ", text)
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    for bit in (host, host.replace("www.", ""),
                host.split(".")[0]):
        if bit:
            text = text.replace(bit, " ")
    return " ".join(text.split())[:80]


def _claim_job(message: str):
    """(job, garble_info): the exact claim first; on a miss,
    the Round 75 second chance — og_garble repairs the
    TRIGGER words only (the vocab is crew words); the
    payload (topic, site, errand words) passes verbatim
    from the repaired string exactly as og_maps does it.
    One seam-local guard on top of og_garble's NEVER list:
    a repair whose heard word is "search" never claims —
    "search" is a real word and the lookup seam's own verb,
    said verbatim; garbling it into "research" would
    manufacture a dispatch out of a plain lookup ask."""
    job = _claim_exact(message)
    if job:
        return job, None
    try:
        repaired, info = og_garble.repair_info(
            str(message or ""), _GARBLE_VOCAB)
        if info:
            if any(str(f[0]).lower() == "search"
                   for f in info.get("fixes") or []):
                return None, None
            job = _claim_exact(repaired)
            if job:
                return job, info
    except Exception as e:
        logger.warning(f"Crew garble second chance failed: {e}")
    return None, None


# --- Researcher backend -----------------------------------------------------------

_FETCH_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; "
             "Win64; x64) AppleWebKit/537.36 (KHTML, like "
             "Gecko) Chrome/124.0 Safari/537.36"}


def _ddgs_search(query: str, num_results: int) -> List[Dict]:
    """The agent's own built-in search backend, called exactly
    as ai_agent_enhanced.web_search calls it (same library,
    same signature). Returns [{title, body, href}]."""
    try:
        from duckduckgo_search import DDGS
    except Exception:
        return []
    try:
        with DDGS() as ddgs:
            rows = list(ddgs.text(query, max_results=num_results))
    except Exception as e:
        logger.warning(f"Crew search failed: {e}")
        return []
    out = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        out.append({"title": str(r.get("title") or "")[:200],
                    "body": str(r.get("body") or "")[:600],
                    "href": str(r.get("href") or "")})
    return out


class _TextGrab(HTMLParser):
    """Visible-text extraction for a fetched page (stdlib
    only): skips script/style, keeps the words."""

    def __init__(self):
        super().__init__()
        self.parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _fetch_page_text(url: str) -> str:
    """Read one result page for depth. Only ever called on
    URLs the search itself returned (never a raw user URL),
    http/https only, tight timeout, capped text."""
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return ""
        import httpx
        with httpx.Client(timeout=10.0, follow_redirects=True) as c:
            r = c.get(url, headers=_FETCH_UA)
        if r.status_code != 200:
            return ""
        ctype = (r.headers.get("content-type") or "").lower()
        if "html" not in ctype and "text" not in ctype:
            return ""
        grab = _TextGrab()
        grab.feed(r.text[:400_000])
        return " ".join(" ".join(grab.parts).split())[:5000]
    except Exception as e:
        logger.warning(f"Crew page read failed ({url[:60]}): {e}")
        return ""


def _refine_phrase(topic: str, results: List[Dict]) -> str:
    """The most-repeated capitalized bigram across the first
    round's result titles that isn't already in the topic —
    the grounded second-query refinement. '' when none."""
    counts: Dict[str, int] = {}
    low_topic = topic.lower()
    for r in results:
        for run in re.findall(
                r"[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*",
                r.get("title") or ""):
            words = run.split()
            for a, b in zip(words, words[1:]):
                phrase = f"{a} {b}"
                if phrase.lower() not in low_topic:
                    counts[phrase] = counts.get(phrase, 0) + 1
    if not counts:
        return ""
    best = max(counts.items(), key=lambda kv: kv[1])
    return best[0] if best[1] >= 2 else ""


def _cancelled(uid: str, job_id: str) -> bool:
    j = _get_job(uid, job_id)
    return bool(j and j.get("state") == "cancelled")


def _run_researcher(job: Dict) -> None:
    uid, job_id = job["uid"], job["id"]
    topic = job.get("detail") or job.get("title") or ""
    bundle = max(1, int(job.get("lookups") or 1))
    spent = 0
    sources: List[Dict] = []
    seen_urls = set()
    queries = [topic]
    while spent < bundle and queries:
        if _cancelled(uid, job_id):
            return
        query = queries.pop(0)
        got = _ddgs_search(query, RESEARCH_RESULTS)
        spent += 1
        _update_job(uid, job_id,
                    progress={"done": spent, "total": bundle})
        for r in got:
            href = r.get("href") or ""
            if href and href not in seen_urls:
                seen_urls.add(href)
                sources.append(r)
        if spent == 1 and spent < bundle:
            phrase = _refine_phrase(topic, got)
            if phrase:
                queries.append(f"{topic} {phrase}")
    reads = []
    for r in sources[:RESEARCH_FETCHES]:
        if _cancelled(uid, job_id):
            return
        text = _fetch_page_text(r.get("href") or "")
        if text:
            host = (urllib.parse.urlparse(
                r.get("href") or "").hostname or "")
            reads.append((host, text))
    lines = [f"Research report — {topic}",
             f"Searches run: {spent} of {bundle} granted."]
    if not sources:
        lines.append("The searches came back empty — no sources "
                     "found for this one.")
    for r in sources[:8]:
        line = f"• {r.get('title') or 'Untitled'}"
        body = " ".join(str(r.get("body") or "").split())[:280]
        if body:
            line += f" — {body}"
        if r.get("href"):
            line += f" ({r['href']})"
        lines.append(line)
    for host, text in reads:
        lines.append(f"From {host}: {text[:700]}")
    _finish(job, "done", "\n".join(lines)[:REPORT_CAP])


# --- Runner backend -----------------------------------------------------------------

def _run_runner(job: Dict) -> None:
    """One read-only errand, the og_watch check shape: own
    throwaway session on the visitor's vault profile,
    navigate + read ONLY, released in full, real elapsed
    minutes charged to og_watch's watch_min ledger."""
    import og_browser as B
    import og_watch as W

    uid, job_id = job["uid"], job["id"]
    url = job.get("detail") or ""
    if _cancelled(uid, job_id):
        return
    ok, reason = B._url_gate(url)
    if not ok:
        _finish(job, "failed",
                f"Runner report — {url}\nThe errand didn't run: "
                f"{reason}.")
        return
    tier = job.get("tier") or "free"
    cap = int(_tiers.cap(tier, "watch_min"))
    if cap <= 0 or W._minutes_used(uid) >= cap:
        _finish(job, "failed",
                f"Runner report — {url}\nThe errand didn't run: "
                "your background browser minutes (the pool your "
                "watches use) are spent for today.")
        return
    pid = None
    try:
        pid = (W._vault(uid) or {}).get("profile_id")
    except Exception:
        pid = None
    sess = None
    try:
        sess = B._steel_create_session(
            RUNNER_SESSION_MIN, profile_id=pid, persist=True)
    except B._SteelError as e:
        if pid:
            logger.warning(f"Runner: stored profile refused: {e}")
            try:
                W._hook_profile_gone(uid)
            except Exception:
                pass
            try:
                sess = B._steel_create_session(
                    RUNNER_SESSION_MIN, persist=True)
            except B._SteelError as e2:
                logger.warning(f"Runner: fresh retry failed: {e2}")
        else:
            logger.warning(f"Runner: session create failed: {e}")
    if sess is None:
        _finish(job, "failed",
                f"Runner report — {url}\nThe errand didn't run: "
                "the browser service handed back no session.")
        return
    new_pid = str(sess.get("profileId") or "")
    if new_pid:
        try:
            W._hook_note_profile(uid, new_pid)
        except Exception:
            pass
    rec = {"steel_id": sess["id"], "ws": sess["websocketUrl"]}
    t0 = time.time()
    try:
        snap = B._drive(rec, {"do": "navigate", "url": url})
        if not (snap.get("text") or "").strip() \
                and not W._is_checkpoint(snap) \
                and not B._login_wall(snap):
            time.sleep(2.5)  # heavy SPA: one re-read (watch shape)
            snap = B._drive(rec, {"do": "read"})
    finally:
        try:
            B._steel_release(sess["id"])
        except Exception:
            pass
    W._add_minutes(uid, max(0.1, round((time.time() - t0) / 60.0, 2)))
    host = job.get("host") or ""
    if W._is_checkpoint(snap) or B._login_wall(snap):
        _finish(job, "done",
                f"Runner report — {url}\n{host} showed a sign-in "
                "wall, so the runner couldn't read the page. It "
                "never signs in for you: open the site in OG's "
                "browser, Take control, sign in yourself, hand "
                "back, then send the runner again.")
        return
    title = snap.get("title") or "(untitled page)"
    landed = snap.get("url") or url
    text = " ".join(str(snap.get("text") or "").split())
    lines = [f"Runner report — {landed}",
             f"Page: {title}"]
    focus = (job.get("focus") or "").strip()
    if focus and text:
        words = [w for w in focus.split() if len(w) >= 3]
        hits = [seg.strip() for seg in re.split(r"(?<=[.!?])\s+",
                                                 text)
                if any(w in seg.lower() for w in words)]
        if hits:
            lines.append("Lines matching the errand: "
                         + " · ".join(hits[:6])[:1200])
    if text:
        lines.append(f"What's on the page: {text[:1200]}")
    else:
        lines.append("The page came back with no readable text.")
    _finish(job, "done", "\n".join(lines)[:REPORT_CAP])


# --- Finish / promote ---------------------------------------------------------------

def _finish(job: Dict, state: str, report: str) -> None:
    uid, job_id = job["uid"], job["id"]
    cur = _get_job(uid, job_id)
    if cur and cur.get("state") == "cancelled":
        _promote()
        return
    _update_job(uid, job_id, state=state, report=report,
                finished=time.time(), delivered=False)
    try:
        import og_notify
        role = "Researcher" if job.get("role") == "researcher" \
            else "Runner"
        og_notify.record(
            uid, "notice",
            f"Crew — {role} {'done' if state == 'done' else 'report'}: "
            f"{job.get('title', '')}"[:160],
            report[:600], None)
    except Exception as e:
        logger.warning(f"Crew notify failed: {e}")
    _promote()


def _run_job(job: Dict) -> None:
    try:
        if job.get("role") == "researcher":
            _run_researcher(job)
        else:
            _run_runner(job)
    except Exception as e:
        logger.warning(f"Crew job {job.get('id')} failed: {e}")
        try:
            _finish(job, "failed",
                    f"Crew report — {job.get('title', '')}\n"
                    f"The job hit a glitch ({type(e).__name__}) and "
                    "stopped. Nothing was changed or sent.")
        except Exception:
            pass


def _promote() -> None:
    """Start queued jobs while working slots are free. Called
    at dispatch, at finish, and on status reads — never on a
    timer (Round 72 diet). Working jobs older than STALE_S
    are marked failed first (their thread died — a deploy),
    so a corpse can never hold a slot forever."""
    if not crew_enabled():
        return
    try:
        now = time.time()
        for uid in _all_uids():
            for j in _jobs(uid):
                if isinstance(j, dict) \
                        and j.get("state") == "working" \
                        and now - float(j.get("updated", 0) or 0) \
                        > STALE_S:
                    _update_job(uid, j.get("id", ""), state="failed",
                                report="The job's worker went away "
                                       "(the server restarted) before "
                                       "it could report.",
                                finished=now, delivered=False)
        counts = _counts()
        if counts["working"] >= GLOBAL_WORKING_CAP:
            return
        queued = []
        for uid in _all_uids():
            for j in _jobs(uid):
                if isinstance(j, dict) and j.get("state") == "queued":
                    queued.append((uid, j))
        queued.sort(key=lambda x: float(x[1].get("created", 0) or 0))
        for uid, j in queued:
            if counts["working"] >= GLOBAL_WORKING_CAP:
                break
            if counts["per_user_working"].get(uid, 0) \
                    >= USER_WORKING_CAP:
                continue
            fresh = _get_job(uid, j.get("id", ""))
            if not fresh or fresh.get("state") != "queued":
                continue
            _update_job(uid, fresh["id"], state="working",
                        started=time.time())
            counts["working"] += 1
            counts["per_user_working"][uid] = \
                counts["per_user_working"].get(uid, 0) + 1
            fresh["state"] = "working"
            threading.Thread(target=_run_job, args=(fresh,),
                             daemon=True).start()
    except Exception as e:
        logger.warning(f"Crew promote failed: {e}")


# --- Dispatch (runs inside the chat turn) --------------------------------------------

def _dispatch(uid: str, claim: Dict) -> List[Dict]:
    tier = "free"
    try:
        tier = str(_dep("get_tier")() or "free")
    except Exception:
        pass
    counts = _counts()
    if counts["per_user_queued"].get(uid, 0) >= USER_QUEUED_CAP \
            or counts["queued"] >= GLOBAL_QUEUED_CAP:
        return _result(
            "OG CREW", "Crew — queue full",
            "Your crew's queue is full — too many jobs already "
            "waiting. Let some finish (or cancel one) and ask "
            "again.")
    now = time.time()
    job = {"id": uuid.uuid4().hex[:12], "uid": uid,
           "role": "researcher" if claim["op"] == "research"
                   else "runner",
           "state": "queued",
           "created": now, "updated": now, "started": 0.0,
           "finished": 0.0, "progress": None, "report": "",
           "delivered": True, "tier": tier, "lookups": 0,
           "focus": "", "host": "", "title": "", "detail": ""}
    if claim["op"] == "research":
        topic = claim["topic"]
        consume = _dep("consume_lookup")
        if consume is None:
            return _result(
                "OG CREW", "Crew — glitch",
                "The researcher couldn't be sent — the lookup "
                "budget check glitched. Nothing was charged or "
                "started. Say it again and I'll retry.")
        granted = 0
        try:
            while granted < RESEARCH_BUNDLE and consume(uid):
                granted += 1
        except Exception as e:
            logger.warning(f"Crew pre-charge failed: {e}")
            granted = 0
        if granted <= 0:
            return _result(
                "OG CREW", "Crew — out of lookups",
                "Your researcher can't go out — you're at "
                "today's web-lookup cap for your plan, and "
                "research rides that same budget. It refills "
                "tomorrow (UTC).")
        job.update({"title": topic[:90], "detail": topic,
                    "lookups": granted,
                    "progress": {"done": 0, "total": granted}})
        with _crew_lock:
            jobs = _jobs(uid)
            jobs.append(job)
            _save_jobs(uid, jobs)
        _promote()
        fresh = _get_job(uid, job["id"]) or job
        if fresh.get("state") in ("working", "done"):
            body = (f"Your researcher is ON IT — digging into "
                    f"\"{topic}\" with {granted} searches. It "
                    "reports back right here in the chat when "
                    "it's done, and the Progress seat shows it "
                    "working. (Tell the user exactly that — do "
                    "not invent findings before the report "
                    "lands.)")
        else:
            body = (f"Your researcher is QUEUED for "
                    f"\"{topic}\" ({granted} searches granted) — "
                    "the crew's hands are full right now, so it "
                    "goes out as soon as a slot frees up and "
                    "reports back here. (Tell the user exactly "
                    "that.)")
        return _result("OG CREW", "Crew — researcher dispatched",
                       body)
    # Runner
    import og_browser as B
    import og_watch as W
    url = claim["url"]
    if not B.enabled():
        return B._dark_text()
    ok, reason = B._url_gate(url)
    if not ok:
        return _result(
            "OG CREW", "Crew — runner refused",
            f"The runner won't go there: {reason}. Pick a "
            "site OG's browser is allowed to read and send it "
            "again.")
    cap = int(_tiers.cap(tier, "watch_min"))
    if cap <= 0 or W._minutes_used(uid) >= cap:
        return _result(
            "OG CREW", "Crew — runner can't go out",
            "Your runner can't go out — the background "
            "browser minutes it rides (the same pool your "
            "watches use) are spent for today on your plan.")
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    job.update({"title": f"Check {host}", "detail": url,
                "host": host, "focus": claim.get("focus", "")})
    with _crew_lock:
        jobs = _jobs(uid)
        jobs.append(job)
        _save_jobs(uid, jobs)
    _promote()
    fresh = _get_job(uid, job["id"]) or job
    if fresh.get("state") == "done":
        return _result(
            "OG CREW", "Crew — runner dispatched",
            f"Your runner already went to {url} and is back "
            "— its report lands at the top of your next "
            "message. It only reads: it never clicks, types, "
            "signs in, or sends anything.")
    where = "ON IT" if fresh.get("state") == "working" \
        else "QUEUED"
    return _result(
        "OG CREW", "Crew — runner dispatched",
        f"Your runner is {where} — heading to {url} to read "
        "the page and report back here in the chat. It only "
        "reads: it never clicks, types, signs in, or sends "
        "anything. The errand rides your background browser "
        "minutes (the pool your watches use). (Tell the user "
        "exactly that.)")


def _cancel(uid: str) -> List[Dict]:
    jobs = [j for j in _jobs(uid)
            if isinstance(j, dict)
            and j.get("state") in ("queued", "working")]
    if not jobs:
        return _result("OG CREW", "Crew — nothing to cancel",
                       "No crew job is running or queued right "
                       "now — nothing to cancel.")
    jobs.sort(key=lambda j: float(j.get("created", 0) or 0),
              reverse=True)
    j = jobs[0]
    _update_job(uid, j.get("id", ""), state="cancelled",
                finished=time.time(), delivered=True)
    role = "researcher" if j.get("role") == "researcher" \
        else "runner"
    return _result(
        "OG CREW", "Crew — cancelled",
        f"Cancelled — your {role} job \"{j.get('title', '')}\" "
        "is stopped. If it was mid-run it drops its tools "
        "where it stands; nothing it was carrying gets sent "
        "anywhere.")


def crew_results(claim: Dict, uid: str) -> List[Dict]:
    op = claim.get("op")
    if op == "crew_status":
        return _result("OG CREW", "Your crew",
                       crew_status(uid)["text"])
    if op == "crew_cancel":
        return _cancel(uid)
    return _dispatch(uid, claim)


# --- Delivery (undelivered reports into the next exchange) ----------------------------

def _pending_reports(uid: str) -> List[Dict]:
    out = []
    stale = []
    now = time.time()
    for j in _jobs(uid):
        if not isinstance(j, dict):
            continue
        if j.get("state") in ("done", "failed") \
                and not j.get("delivered"):
            if now - float(j.get("finished", 0) or 0) > 24 * 3600:
                stale.append(j.get("id", ""))
            else:
                out.append(j)
    for job_id in stale:
        _update_job(uid, job_id, delivered=True)
    out.sort(key=lambda j: float(j.get("finished", 0) or 0))
    return out[:2]


def _delivery_results(reports: List[Dict]) -> List[Dict]:
    out = []
    for j in reports:
        role = "Researcher" if j.get("role") == "researcher" \
            else "Runner"
        out.append({
            "title": f"🧑‍🤝‍🧑 OG Crew — {role} report",
            "body": ("FIRST — before answering anything else — "
                     "deliver this crew report, plainly and in "
                     "persona, with the facts exactly as "
                     "written, THEN answer their message:\n"
                     + str(j.get("report") or "")),
            "href": ""})
    return out


# --- The roster (ONE surface: chat answer + GET /crew/status) -------------------------

_ROLE_ORDER = ("researcher", "runner", "coder", "watcher",
               "maker")
_ROLE_LABELS = {"researcher": "Researcher", "runner": "Runner",
                "coder": "Coder", "watcher": "Watcher",
                "maker": "Maker"}


def crew_status(uid: str) -> Dict:
    if uid:
        _promote()
    jobs = _jobs(uid) if uid else []
    mine = [j for j in jobs if isinstance(j, dict)]
    rows = []
    try:
        import og_progress
        rows = og_progress.collect_progress(uid, None)["rows"]
    except Exception as e:
        logger.warning(f"Crew status board read failed: {e}")
        rows = []

    def role_line(role: str) -> str:
        live = [j for j in mine if j.get("role") == role
                and j.get("state") in ("working", "queued")]
        if live:
            live.sort(key=lambda j: float(j.get("created", 0)
                                           or 0))
            j = live[0]
            verb = "digging into" if role == "researcher" \
                else "reading"
            what = j.get("detail") if role == "researcher" \
                else (j.get("host") or j.get("detail"))
            if j.get("state") == "working":
                return f"{verb} {what} right now"
            return f"queued — {what}"
        if role == "coder":
            n = sum(1 for r in rows if r.get("section") == "fixes")
            if n:
                return (f"{n} prepared fix{'es' if n != 1 else ''} "
                        "waiting on approval")
            return ("standing by — drafts and tests a fix when "
                    "OG spots a problem in his own system")
        if role == "watcher":
            n = sum(1 for r in rows
                    if r.get("section") == "watching")
            if n:
                return f"{n} watch{'es' if n != 1 else ''} on post"
            return "standing by — no watches set"
        if role == "maker":
            live_rows = [r for r in rows
                         if r.get("section") == "making"
                         and r.get("working")]
            if live_rows:
                return ("making: " + ", ".join(
                    str(r.get("title") or "a job")
                    for r in live_rows[:3]))
            return ("standing by — videos, songs, podcasts, "
                    "books and designs get made on ask")
        done = [j for j in mine if j.get("role") == role
                and j.get("state") == "done"]
        if done:
            done.sort(key=lambda j: float(j.get("finished", 0)
                                          or 0), reverse=True)
            return f"last job done: {done[0].get('title', '')}"
        return "standing by"

    roles = [{"role": r, "label": _ROLE_LABELS[r],
              "line": role_line(r)} for r in _ROLE_ORDER]
    text = "Your crew right now:\n" + "\n".join(
        f"- {_ROLE_LABELS[r]} — {role_line(r)}"
        for r in _ROLE_ORDER)
    if not crew_enabled():
        text = ("The crew is switched off right now "
                "(OG_CREW_ENABLED). Nobody is out on a job.")
        roles = [{"role": r, "label": _ROLE_LABELS[r],
                  "line": "switched off"} for r in _ROLE_ORDER]
    return {"roles": roles, "text": text,
            "jobs": jobs_for(uid) if uid else []}


# --- The seam ---------------------------------------------------------------------------

_pending = {"job": None, "garble": None, "reports": []}


def install_crew_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search OUTSIDE every earlier
    seam (installed last, from the og_fixqueue chain — the
    R65/7a–7f precedent) so crew asks claim here, stored crew
    reports deliver at the top of the next exchange (the
    og_watch alert pattern), and everything else delegates
    back untouched. Persona files never touched."""
    if getattr(agent_instance, "_og_crew_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["job"] = None
        _pending["garble"] = None
        _pending["reports"] = []
        try:
            uid = get_uid()
            if uid:
                _pending["reports"] = _pending_reports(uid)
                if crew_enabled():
                    job, info = _claim_job(str(message))
                    if job:
                        _pending["job"] = job
                        _pending["garble"] = info
                elif _STATUS_RE.search(str(message)):
                    _pending["job"] = {"op": "crew_status"}
                if _pending["job"] or _pending["reports"]:
                    if isinstance(intent, dict):
                        intent["needs_code_generation"] = False
                        if not intent.get("needs_web_search"):
                            intent["needs_web_search"] = True
                            intent["search_query"] = \
                                str(message).strip()
        except Exception as e:
            logger.warning(f"Crew trigger check failed: {e}")
            _pending["job"] = None
            _pending["reports"] = []
        return intent

    def search_wrapped(query, num_results=5):
        job = _pending.get("job")
        garble = _pending.get("garble")
        reports = _pending.get("reports") or []
        _pending["job"] = None
        _pending["garble"] = None
        _pending["reports"] = []
        uid = ""
        try:
            uid = get_uid()
        except Exception:
            uid = ""
        results = None
        if job and uid:
            try:
                results = crew_results(job, uid)
            except Exception as e:
                logger.warning(f"Crew job failed: {e}")
                results = _result(
                    "OG CREW", "Crew — glitch",
                    "The crew ask glitched — nothing was "
                    "started or charged. Say it again and I'll "
                    "retry.")
        if results is None:
            results = prev_search(query, num_results)
        if reports and results is not None:
            delivered = _delivery_results(reports)
            results = delivered + list(results)
            try:
                for j in reports:
                    _update_job(uid, j.get("id", ""),
                                delivered=True)
            except Exception as e:
                logger.warning(f"Crew delivery mark failed: {e}")
        if garble and results:
            results = og_garble.annotate(results, garble,
                                         prepend=True)
        return results

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_crew_installed = True


# --- Routes ------------------------------------------------------------------------------

def register_crew_routes(app):

    @app.get("/crew/status")
    def crew_status_route(request: Request):
        uid = request.cookies.get("ogai_uid") or ""
        data = crew_status(uid)
        return {"ok": True, "enabled": crew_enabled(),
                "roles": data["roles"], "jobs": data["jobs"]}
