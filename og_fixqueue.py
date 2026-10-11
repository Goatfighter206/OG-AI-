"""Fix-approval pipeline for OG (Round 53; Brent's rule,
2026-10-10): "if he's running something and runs into a problem
he will start problem solving and ask for your approval to fix
it — anything OG wants to fix needs approval."

WHAT THIS IS. The general pipeline behind that rule, grown out
of Round 50's approve-to-upgrade hook:

1. PROBLEMS. A durable, owner-keyed record of everything that
   goes wrong: {id, fingerprint (dedupe), source, title in plain
   words, diagnosis (what OG found and why he thinks it
   happened), first_seen/last_seen, status}. System problems key
   to the OPERATOR, resolved exactly as Round 50 resolves it
   (OG_OWNER_EMAIL -> account uid); unset means the problems
   store under a sentinel key and surface the moment an operator
   resolves — a recipient is never guessed. Sources today:
   (a) every non-ok Round 50 self-check finding, ingested
   automatically when a check runs; (b) a fail-safe
   record_problem() hook other modules call — wired into the
   watch-check failure paths and the video/song job failure
   paths (one line each, never breaks the caller).

2. PROPOSALS. For each problem OG prepares at most one LIVE
   proposal: what he wants to do, in plain words, with the
   payload prepared and a verify plan. Preparation NEVER
   mutates anything. Kinds: "server" (runs through this
   module's executor registry on approval), "owner_steps"
   (numbered steps for the human's hands — dashboards, plan
   pages, re-asks), and "code" (reserved in the schema).
   CODE SEAM, honestly: Round 32's code flow drafts from a
   human's message against their connected repo and parks its
   own pending through two human YESes — a diagnosed system
   problem cannot honestly pre-draft a sandbox-checked diff
   without a human scoping the change, so code-class problems
   ship as owner_steps proposals whose steps carry the
   diagnosis and the exact sentence that starts the Round 32
   verified flow.

3. EXECUTORS. The registry is deliberately the honest set that
   existing machinery grounds: prune_reports (Round 50's own
   housekeeping executor) and prune_problem_history (this
   store's). A stuck video/song job needs no clearer — those
   modules already read stale working records as cut off at
   every seam, so clearing one would be theatre. Anything with
   no safe server fix is an owner_steps proposal, never an
   invented executor. HARD RULES: a fix NEVER charges money or
   changes a plan (that stays Round 50's upgrade path), and no
   executor ever touches another uid's data.

4. APPROVAL. One notification per new proposal (kind
   "fix_needed", Round 38 alerts-gated family) to the owning
   uid. In chat: "approve the fix for <name>" / "decline the
   fix for <name>" — the parser REQUIRES the word "fix", so a
   bare YES is never eaten (the Round 50 posture). Routes:
   GET /approvals/proposals (the caller's own only, behind the
   sign-in gate like every caller route) feeds the Round 52
   Approvals panel, whose per-proposal buttons POST
   /approvals/proposals/act. Approvals execute ONLY for the
   owning uid — somebody else's id simply isn't in your list.

5. EXECUTION. On approve: the proposal must be open and
   unexpired (7 days), and the problem is re-verified first —
   already gone means the proposal is superseded and nothing
   runs. The proposal is stamped executed BEFORE the executor
   runs (exactly once), the executor runs exactly the prepared
   payload, then the verify step re-checks the condition and
   the outcome is recorded on the problem (fixed | fix_failed)
   and reported either way. NO silent retry; a second attempt
   needs a new proposal and a new approval. Decline discards
   the proposal; the problem stays open and may re-propose at
   most once per 24h per fingerprint. Approving an owner_steps
   proposal hands the steps over (numbered) and marks the
   problem acknowledged; the self-check sweep closes it when
   the condition actually clears.
"""

import hashlib
import json
import logging
import os
import platform
import re
import threading
import time
import uuid
from typing import Dict, List, Optional, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

STORE_FILE = os.getenv("OG_FIXQUEUE_STORE", "fixqueue_store.json")
PROPOSAL_TTL_SECONDS = 7 * 24 * 60 * 60
REPROPOSE_COOLDOWN_SECONDS = 24 * 60 * 60
MAX_HISTORY = 24
REPORT_STORE_LIMIT = 1024 * 1024
PROBLEM_STORE_LIMIT = 200
PROBLEM_HISTORY_KEEP = 100
SENTINEL_OWNER = "__operator__"

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_store_lock = threading.Lock()
_pending: Dict = {"job": None}


# --- Durable store (og_notify pattern: one key -> JSON blob) -------------------


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_fixqueue_data ("
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
            logger.warning(f"Fixqueue store load failed: {e}")
    return {}


def _save_file_store(store: dict):
    try:
        with open(STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Fixqueue store save failed: {e}")


def _get(key: str):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_fixqueue_data WHERE key=%s",
                        (key,))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning(f"Fixqueue DB load failed, using file: {e}")
    with _store_lock:
        store = _load_file_store()
    return store.get(key)


def _put(key: str, value):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_fixqueue_data (key, data) "
                        "VALUES (%s, %s) ON CONFLICT (key) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (key, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Fixqueue DB save failed, using file: {e}")
    with _store_lock:
        store = _load_file_store()
        store[key] = value
        _save_file_store(store)


def _problems(owner: str) -> Dict[str, dict]:
    data = _get("problems:" + owner)
    return data if isinstance(data, dict) else {}


def _save_problems(owner: str, problems: Dict[str, dict]):
    _put("problems:" + owner, problems)


def _all_owners() -> List[str]:
    """Owner keys that hold problems. File backend: the store's
    keys; DB backend: a keys listing. Fail-soft to []."""
    try:
        if MEMORY_DB_URL and psycopg is not None:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT key FROM og_fixqueue_data "
                        "WHERE key LIKE 'problems:%'")
                    rows = cur.fetchall()
            return [str(r[0]).split(":", 1)[1] for r in rows]
        with _store_lock:
            store = _load_file_store()
        return [k.split(":", 1)[1] for k in store
                if k.startswith("problems:")]
    except Exception:
        return []


def _total_problems() -> int:
    return sum(len(_problems(o)) for o in _all_owners())


# --- Operator (exactly Round 50's resolution) -------------------------------------


def _operator_uid() -> Optional[str]:
    try:
        import og_selfcheck as _sc
        return _sc._operator_uid()
    except Exception:
        return None


def _adopt_sentinel(uid: str):
    """Operator problems recorded while OG_OWNER_EMAIL was unset
    sit under the sentinel key; the moment the caller IS the
    resolved operator, they become his."""
    if not uid or uid == SENTINEL_OWNER:
        return
    if _operator_uid() != uid:
        return
    stray = _problems(SENTINEL_OWNER)
    if not stray:
        return
    mine = _problems(uid)
    for fp, prob in stray.items():
        prob["owner"] = uid
        cur = mine.get(fp)
        if cur is None or float(prob.get("last_seen") or 0) >= \
                float(cur.get("last_seen") or 0):
            mine[fp] = prob
    _save_problems(uid, mine)
    _save_problems(SENTINEL_OWNER, {})


# --- Helpers ------------------------------------------------------------------------


def _slugify(text: str, fallback: str = "problem") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return slug or fallback


def _note(problem: dict, event: str, note: str = ""):
    hist = problem.setdefault("history", [])
    hist.append({"ts": time.time(), "event": event, "note": note})
    problem["history"] = hist[-MAX_HISTORY:]


# --- Proposal preparation (NEVER mutates anything) -----------------------------------


def _default_fix(problem: dict) -> dict:
    return {
        "kind": "owner_steps",
        "summary": f"OG found a problem: {problem['title']}.",
        "steps": [
            "The diagnosis above is what OG found and why he "
            "thinks it happened.",
            "If it clears on its own, the next system check "
            "closes this automatically.",
        ],
    }


# --- Round 51: OG drafts real code fixes (Part 2) + lessons (Part 3) --------------
#
# The Round 53 code seam said a diagnosed system problem could
# not honestly pre-draft a sandbox-checked diff. Now it can:
# og_github.draft_system_fix drafts against OG's own codebase
# through the bounded competence loop. A GREEN draft becomes a
# "code" proposal whose payload is the tested diff; anything
# else (no drafting key, loop exhausted, drafting blew up)
# falls back to exactly the owner_steps behavior Round 53
# shipped — with the attempt history attached when the loop
# honestly ran and failed. An approach the lessons book records
# as failed for this fingerprint is NEVER re-proposed as code.


def _approach_key(prop: dict) -> Optional[str]:
    """The stable identity of WHAT a proposal would do — the
    lessons book keys failed approaches by it."""
    payload = prop.get("payload") or {}
    if prop.get("fix_kind") == "code":
        return payload.get("approach_key")
    if prop.get("fix_kind") == "server":
        executor = str(payload.get("executor") or "")
        digest = hashlib.sha256(json.dumps(
            payload.get("args") or {},
            sort_keys=True).encode("utf-8")).hexdigest()[:12]
        return f"server:{executor}:{digest}"
    return None


def _lesson_fix_failed(problem: dict, prop: dict, why: str):
    """Part 3, source (a): an executed fix failed its
    verification — write the lesson. Fail-safe."""
    try:
        import og_lessons as _lessons
        key = _approach_key(prop)
        _lessons.record_lesson(
            "fix_failed",
            f"Fix failed: {problem.get('title', 'a fix')}",
            f"OG's approved fix for "
            f"'{problem.get('title')}' was executed and the "
            f"verification still showed the problem ({why}). "
            f"Approach: {key or 'manual steps'}. Do not "
            f"re-propose the same approach for this problem — "
            f"diagnose differently next time.",
            fingerprint=problem.get("fingerprint"),
            approach_key=key)
    except Exception as e:
        logger.warning(f"Fix-failed lesson failed: {e}")


def _is_operator_problem(problem: dict) -> bool:
    owner = str(problem.get("owner") or "")
    if owner == SENTINEL_OWNER:
        return True
    op = _operator_uid()
    return bool(op) and owner == op


def _attempts_text(attempts) -> str:
    parts = []
    for a in attempts or []:
        if a.get("green"):
            parts.append(f"attempt {a.get('n')}: passed")
        else:
            fail = ", ".join(a.get("failing") or []) \
                or a.get("status") or "checks failed"
            parts.append(f"attempt {a.get('n')}: failed — {fail}")
    return "; ".join(parts)


def _try_draft_fix(problem: dict, fix: dict) -> Optional[dict]:
    """Draft a code fix for an operator problem. Returns one of:
    {"mode": "code", "payload", "summary"} — a green, tested
    draft; {"mode": "note", "note"} — drafting honestly ran (or
    was vetoed by the lessons book) but produced no code
    proposal, so the caller falls back to owner_steps with the
    note attached; None — drafting unavailable, plain fallback.
    NEVER raises; preparation NEVER mutates the repo."""
    try:
        import og_github as _gh
        import og_lessons as _lessons
        spec = fix.get("draft") or {}
        lessons = _lessons.relevant_lessons(
            fingerprint=problem.get("fingerprint"),
            path=spec.get("target_path") or None, limit=5)
        draft, attempts = _gh.draft_system_fix(
            str(problem.get("title") or ""),
            str(problem.get("diagnosis") or ""), spec, lessons)
        if draft is None:
            if attempts:
                return {"mode": "note", "note":
                        "OG tried to draft this fix himself "
                        "first: " + _attempts_text(attempts)
                        + ". His checks never passed, so no code "
                          "fix is proposed — the steps below "
                          "are the manual route."}
            return None
        failed = _lessons.failed_lesson_for(
            problem.get("fingerprint"), draft.get("approach_key"))
        if failed is not None:
            return {"mode": "note", "note":
                    "OG drafted a fix for this himself, but his "
                    "lessons book says that exact approach was "
                    "tried before and failed its verification "
                    f"(\"{failed.get('title')}\") — he won't "
                    "propose it again. The steps below are the "
                    "manual route."}
        try:
            checks = _gh._checks_line(draft["test"], draft["path"])
        except Exception:
            checks = "sandbox checks passed"
        summary = (f"OG drafted a fix for {draft['path']} "
                   f"himself and verified it in his sandbox "
                   f"before proposing it. Checks: {checks}")
        if len(draft.get("attempts") or []) > 1:
            summary += (f" It took "
                        f"{draft['attempts'][-1].get('n')} "
                        f"attempts — the earlier drafts failed "
                        f"his checks and he revised them himself.")
        for title_ in draft.get("lessons_cited") or []:
            summary += f" Lesson applied: {title_}."
        payload = {
            "path": draft["path"],
            "base_text": draft["base_text"],
            "content": draft["content"],
            "diff": draft["diff"],
            "test": draft["test"],
            "attempts": draft["attempts"],
            "approach_key": draft["approach_key"],
            "lessons_cited": draft["lessons_cited"],
            "repo": os.getenv("OG_SELF_REPO", "").strip(),
        }
        return {"mode": "code", "payload": payload,
                "summary": summary}
    except Exception as e:
        logger.warning(f"Fix draft attempt failed: {e}")
        return None


def _prepare(problem: dict) -> Optional[dict]:
    """Build the one live proposal for a problem, honouring the
    decline cooldown and any already-live proposal. Returns the
    proposal or None."""
    now = time.time()
    prop = problem.get("proposal")
    if isinstance(prop, dict) and prop.get("status") == "open":
        if now <= float(prop.get("expires") or 0):
            return None  # a live one is already waiting
        prop["status"] = "expired"
        _note(problem, "proposal_expired")
    declined_at = problem.get("declined_at")
    if isinstance(declined_at, (int, float)) and \
            now - float(declined_at) < REPROPOSE_COOLDOWN_SECONDS:
        return None
    fix = problem.get("fix") or _default_fix(problem)
    # Round 51: an operator problem carrying a draft spec gets a
    # real drafted code proposal when the drafter reaches green.
    draft_note = ""
    if isinstance(fix, dict) and fix.get("draft") \
            and _is_operator_problem(problem):
        outcome = _try_draft_fix(problem, fix)
        if outcome and outcome.get("mode") == "code":
            proposal = {
                "id": uuid.uuid4().hex[:12],
                "problem_id": problem["id"],
                "fix_kind": "code",
                "summary": outcome["summary"],
                "payload": outcome["payload"],
                "verify": problem.get("verify") or {"kind": "none"},
                "created": now,
                "expires": now + PROPOSAL_TTL_SECONDS,
                "status": "open",
                "outcome": None,
            }
            problem["proposal"] = proposal
            _note(problem, "proposal_prepared", "code")
            return proposal
        if outcome and outcome.get("note"):
            draft_note = " " + outcome["note"]
    kind = str(fix.get("kind") or "owner_steps")
    if kind == "server":
        payload = {"executor": str(fix.get("executor") or ""),
                   "args": fix.get("args") or {}}
        if payload["executor"] not in _EXECUTORS:
            kind = "owner_steps"  # never promise an executor we lack
            payload = {"steps": fix.get("steps") or
                       _default_fix(problem)["steps"]}
    else:
        kind = "owner_steps"
        payload = {"steps": fix.get("steps") or
                   _default_fix(problem)["steps"]}
    proposal = {
        "id": uuid.uuid4().hex[:12],
        "problem_id": problem["id"],
        "fix_kind": kind,
        "summary": str(fix.get("summary") or
                       f"Fix for: {problem['title']}") + draft_note,
        "payload": payload,
        "verify": problem.get("verify") or {"kind": "none"},
        "created": now,
        "expires": now + PROPOSAL_TTL_SECONDS,
        "status": "open",
        "outcome": None,
    }
    problem["proposal"] = proposal
    _note(problem, "proposal_prepared", proposal["fix_kind"])
    return proposal


def _notify_proposal(owner: str, problem: dict, proposal: dict):
    if not owner or owner == SENTINEL_OWNER:
        return  # never guess a recipient
    try:
        import og_notify as _notify
        body = (proposal["summary"] + " Diagnosis: " +
                problem["diagnosis"])[:590]
        _tgt = {"view": "approvals"}
        if problem.get("id"):
            _tgt["id"] = problem["id"]
        _notify.record(owner, "fix_needed",
                       f"Fix waiting: {problem['title']}", body,
                       target=_tgt)
    except Exception as e:
        logger.warning(f"Fixqueue notify failed: {e}")


# --- Problem intake -------------------------------------------------------------------


def record_problem(owner, source, fingerprint, slug, title,
                   diagnosis, verify=None, fix=None) -> Optional[str]:
    """Fail-safe intake — the hook other modules call. NEVER
    raises. Dedupes by fingerprint under the owner; a recurrence
    of a fixed/resolved problem reopens it; prepares the one
    live proposal and notifies once per new proposal. Returns
    the problem id or None."""
    try:
        owner_key = str(owner or "").strip() or SENTINEL_OWNER
        fp = str(fingerprint or "").strip()
        if not fp:
            return None
        now = time.time()
        problems = _problems(owner_key)
        problem = problems.get(fp)
        if problem is None:
            problem = {
                "id": uuid.uuid4().hex[:12],
                "owner": owner_key,
                "fingerprint": fp,
                "source": str(source or "job"),
                "slug": _slugify(slug or title),
                "title": str(title or "Something went wrong"),
                "diagnosis": str(diagnosis or "")[:600],
                "first_seen": now,
                "last_seen": now,
                "status": "open",
                "verify": verify or {"kind": "none"},
                "fix": fix,
                "proposal": None,
                "declined_at": None,
                "history": [],
            }
            _note(problem, "recorded", str(source or ""))
            problems[fp] = problem
        else:
            problem["last_seen"] = now
            if diagnosis:
                problem["diagnosis"] = str(diagnosis)[:600]
            if verify:
                problem["verify"] = verify
            if fix:
                problem["fix"] = fix
            if problem.get("status") in ("fixed", "resolved",
                                         "fix_failed"):
                # A recurrence: the problem is back (or still
                # here after a failed fix), so it reopens and a
                # FRESH proposal may be prepared — a second
                # attempt still needs that new proposal + a new
                # approval; nothing retries on its own.
                old_status = problem.get("status")
                problem["status"] = "open"
                problem["declined_at"] = None
                _note(problem, "recurred")
                if old_status in ("fixed", "resolved"):
                    # Part 3, source (c): a fingerprint came
                    # back AFTER a fix was verified working —
                    # the fix wasn't durable; record the lesson.
                    try:
                        import og_lessons as _lessons
                        last_prop = problem.get("proposal") or {}
                        _lessons.record_lesson(
                            "recurrence",
                            f"Recurrence: {problem.get('title')}",
                            f"'{problem.get('title')}' was "
                            f"marked fixed, then came back. The "
                            f"fix that closed it did not hold — "
                            f"the next approach must address the "
                            f"root cause, not just the symptom "
                            f"that was verified.",
                            fingerprint=fp,
                            approach_key=_approach_key(last_prop))
                    except Exception as e:
                        logger.warning(
                            f"Recurrence lesson failed: {e}")
        proposal = _prepare(problem)
        _save_problems(owner_key, problems)
        if proposal is not None:
            _notify_proposal(owner_key, problem, proposal)
        return str(problem["id"])
    except Exception as e:
        logger.warning(f"Fixqueue record_problem failed: {e}")
        return None


# --- Self-check ingest (Round 50 findings become problems) -----------------------------


def _code_flow_step(what: str) -> str:
    return (f"To have OG fix it in code, say in chat: \"fix "
            f"{what} in the connected repo\" — OG drafts the "
            f"change, sandbox-checks it, and shows you the diff; "
            f"nothing ships without your yes (the Round 32 flow).")


def ingest_report(report: dict) -> int:
    """Convert a Round 50 report's non-ok findings into operator
    problems + proposals, and sweep self-check problems the new
    report no longer shows. Fail-safe; returns the number of
    findings ingested."""
    try:
        if not isinstance(report, dict):
            return 0
        operator = _operator_uid()
        owner = operator or SENTINEL_OWNER
        if operator:
            _adopt_sentinel(operator)
        seen = set()
        count = 0

        upgrades = {}
        for up in report.get("upgrades") or []:
            upgrades[str(up.get("id", "")).split(":", 1)[-1]] = up

        for f in (report.get("buttons") or {}).get("failed") or []:
            name = str(f.get("button") or "A button")
            route = str(f.get("route") or "")
            fp = f"button:{name}"
            seen.add(fp)
            count += 1
            record_problem(
                owner, "selfcheck", fp, _slugify(name),
                f"The {name} isn't working",
                f"{route} answered {f.get('observed', 'nothing')} "
                f"when the system check probed it.",
                verify={"kind": "button", "button": name},
                fix={"kind": "owner_steps",
                     "summary": f"The {name} failed its system-"
                                f"check probe ({route}).",
                     "steps": [
                         "Open the Render dashboard and go to "
                         "og-ai-service → Logs.",
                         f"Search around the last system check "
                         f"for errors on {route}.",
                         _code_flow_step(f"the {name.lower()}"),
                     ],
                     # Round 51: draft the code fix first; the
                     # steps stay as the honest fallback.
                     "draft": {
                         "target_path": None,
                         "what": f"the {name} ({route}) on the "
                                 f"OG page answered "
                                 f"{f.get('observed', 'nothing')} "
                                 f"when probed instead of "
                                 f"working — find the cause in "
                                 f"its handler and fix it",
                     }})

        for m in (report.get("overload") or {}).get("measures") or []:
            if m.get("level") not in ("near", "over"):
                continue
            name = str(m.get("name") or "A measure")
            slug = _slugify(name)
            fp = f"measure:{slug}"
            seen.add(fp)
            count += 1
            over = m.get("level") == "over"
            title = (f"{name} is over its limit" if over
                     else f"{name} is near its limit")
            verify = {"kind": "measure", "name": name}
            fix = None
            up = upgrades.get(slug)
            if up is not None:
                server = [s for s in up.get("server_steps") or []
                          if s.get("executor") in _EXECUTORS]
                if server:
                    if slug == "self-check-report-store":
                        verify = {"kind": "report_store"}
                    fix = {"kind": "server",
                           "executor": server[0]["executor"],
                           "args": {},
                           "summary": str(up.get("what") or title)}
                elif up.get("owner_steps"):
                    fix = {"kind": "owner_steps",
                           "summary": str(up.get("what") or title),
                           "steps": list(up["owner_steps"])}
            if fix is None:
                fix = {"kind": "owner_steps",
                       "summary": f"{title}: {m.get('detail', '')}",
                       "steps": [
                           "This is a capacity number, not a "
                           "crash — nothing is broken.",
                           "If it keeps climbing, the upgrade "
                           "for it is a dashboard/plan decision "
                           "(Round 50's upgrade approvals cover "
                           "those).",
                       ]}
            record_problem(owner, "selfcheck", fp, slug, title,
                           str(m.get("detail") or ""),
                           verify=verify, fix=fix)

        updates = report.get("updates") or {}
        for u in updates.get("updates") or []:
            pkg = str(u.get("package") or "")
            if not pkg:
                continue
            fp = f"update:{pkg.lower()}"
            seen.add(fp)
            count += 1
            record_problem(
                owner, "selfcheck", fp, _slugify(pkg + " update"),
                f"{pkg} has an update waiting",
                f"Pinned at {u.get('pinned')}; the latest "
                f"release is {u.get('latest')}. Nothing upgrades "
                f"on its own.",
                verify={"kind": "update", "package": pkg},
                fix={"kind": "owner_steps",
                     "summary": f"Update {pkg} "
                                f"{u.get('pinned')} -> "
                                f"{u.get('latest')}.",
                     "steps": [
                         "Dependency updates ship as code, "
                         "never automatically.",
                         _code_flow_step(
                             f"the {pkg} update to "
                             f"{u.get('latest')}"),
                     ],
                     # Round 51: draft the pin bump first;
                     # falls back to the steps above.
                     "draft": {
                         "target_path": "requirements.txt",
                         "what": f"bump the {pkg} pin from "
                                 f"{u.get('pinned')} to "
                                 f"{u.get('latest')} in "
                                 f"requirements.txt",
                     }})
        # NOTE: the updates job's Python pin-vs-running drift is
        # deliberately NOT ingested as a problem. Round 50
        # already surfaces it in every report summary (and the
        # system_check notification); it is a config note, not a
        # failure OG ran into, and promoting it would double-
        # report the same line through two channels forever (the
        # drift is permanent until a deploy decision, so the
        # "problem" could never clear). Package updates above DO
        # become problems — those are actionable findings.

        # Sweep: self-check problems this report no longer shows
        # have cleared on their own.
        problems = _problems(owner)
        changed = False
        for fp, prob in problems.items():
            if prob.get("source") != "selfcheck":
                continue
            if prob.get("status") not in ("open", "acknowledged"):
                continue
            if fp in seen:
                continue
            prob["status"] = "resolved"
            prop = prob.get("proposal")
            if isinstance(prop, dict) and prop.get("status") == "open":
                prop["status"] = "superseded"
            _note(prob, "resolved_by_check")
            changed = True
        if changed:
            _save_problems(owner, problems)

        # Own-store housekeeping problem (the prune_reports
        # pattern, one level up).
        total = _total_problems()
        if total > PROBLEM_STORE_LIMIT:
            record_problem(
                owner, "fixqueue", "fixqueue:store",
                "fix-queue-store",
                "OG's fix queue is holding a lot of old problems",
                f"{total} problem records are stored; the "
                f"closed ones can be pruned to the newest "
                f"{PROBLEM_HISTORY_KEEP}. Open problems are "
                f"never pruned.",
                verify={"kind": "fixqueue_store",
                        "max": PROBLEM_STORE_LIMIT},
                fix={"kind": "server",
                     "executor": "prune_problem_history",
                     "args": {"keep": PROBLEM_HISTORY_KEEP},
                     "summary": "Prune closed problem records "
                                "to the newest "
                                f"{PROBLEM_HISTORY_KEEP}."})
        return count
    except Exception as e:
        logger.warning(f"Fixqueue ingest failed: {e}")
        return 0


# --- Verify (is the condition actually gone?) ------------------------------------------


def _condition_gone(problem: dict) -> bool:
    """The verify step. Read-only re-checks through the owning
    modules' own seams; any doubt reads NOT gone."""
    spec = problem.get("verify") or {}
    kind = spec.get("kind")
    try:
        import og_selfcheck as _sc
        if kind == "button":
            entry = None
            for name, method, path, pkind in _sc.BUTTON_CENSUS:
                if name == spec.get("button"):
                    entry = (method, path, pkind)
                    break
            if entry is None:
                return False
            method, path, pkind = entry
            gate = _sc._gate_on()
            if pkind == "post_gated" and not gate:
                return False  # cannot confirm without probing live
            status, body = _sc._probe_http(method, path)
            if pkind == "public200":
                return status == 200
            if gate:
                return status == 401 and "sign_in_required" in body
            return status == 200
        if kind == "measure":
            overload = _sc._check_overload({}, _sc._operator_uid())
            for m in overload.get("measures") or []:
                if m.get("name") == spec.get("name"):
                    return m.get("level") in ("ok", "info")
            return True  # no longer measured = no longer flagged
        if kind == "report_store":
            return len(json.dumps(_sc._reports())) <= REPORT_STORE_LIMIT
        if kind == "update":
            pinned = dict(_sc._pinned_requirements())
            cur = pinned.get(str(spec.get("package") or ""))
            latest = _sc._pypi_latest(str(spec.get("package") or ""))
            if cur is None or latest is None:
                return False
            return _sc._ver_key(latest) <= _sc._ver_key(cur)
        if kind == "python":
            pin = _sc._runtime_pin()
            return bool(pin) and pin == platform.python_version()
        if kind == "fixqueue_store":
            return _total_problems() <= int(spec.get("max") or
                                            PROBLEM_STORE_LIMIT)
    except Exception as e:
        logger.warning(f"Fixqueue verify failed: {e}")
    return False


# --- Executor registry (the honest set only) ---------------------------------------------


def _exec_prune_reports(args: dict) -> str:
    import og_selfcheck as _sc
    fn = _sc._EXECUTORS["prune_reports"]
    return fn()


def _exec_prune_problem_history(args: dict) -> str:
    keep = int(args.get("keep") or PROBLEM_HISTORY_KEEP)
    dropped = 0
    for owner in _all_owners():
        problems = _problems(owner)
        closed = [p for p in problems.values()
                  if p.get("status") != "open"]
        closed.sort(key=lambda p: float(p.get("last_seen") or 0),
                    reverse=True)
        for prob in closed[keep:]:
            problems.pop(prob["fingerprint"], None)
            dropped += 1
        _save_problems(owner, problems)
    return (f"Pruned {dropped} closed problem record(s); open "
            f"problems were not touched.")


_EXECUTORS = {
    "prune_reports": _exec_prune_reports,
    "prune_problem_history": _exec_prune_problem_history,
}


# --- Decide (shared by chat + routes) -----------------------------------------------------


def _owner_keys_for(uid: str) -> List[str]:
    keys = [uid]
    if uid and _operator_uid() == uid:
        keys.append(SENTINEL_OWNER)
    return keys


def _find(uid: str, ident: str):
    """(owner, problems, problem, proposal) for a proposal id or
    a problem slug/name, searching ONLY the caller's own keys."""
    ident = str(ident or "").strip()
    if not ident or not uid:
        return None
    _adopt_sentinel(uid)
    want = _slugify(ident)
    slug_hit = None
    for owner in _owner_keys_for(uid):
        problems = _problems(owner)
        for prob in problems.values():
            prop = prob.get("proposal")
            if not isinstance(prop, dict):
                continue
            if prop.get("id") == ident:
                return owner, problems, prob, prop
            if slug_hit is None and want and (
                    want == prob.get("slug")
                    or want in str(prob.get("slug") or "")
                    or str(prob.get("slug") or "") in want
                    or want in str(prob.get("title") or "").lower()):
                slug_hit = (owner, problems, prob, prop)
    return slug_hit


def _decide_code(owner, problems, problem, prop):
    """Execute an approved CODE proposal under exactly the
    Round 32 discipline: the target file must not have moved
    since the draft, the drafted content must re-pass its
    sandbox checks, the proposal is stamped executed BEFORE
    anything runs (exactly once), and the fix ships ONLY as a
    pull request through og_github's verified flow — never a
    direct push, never a merge. Any staleness refuses honestly
    and runs nothing."""
    payload = prop.get("payload") or {}
    path = str(payload.get("path") or "")
    content = str(payload.get("content") or "")
    base_text = str(payload.get("base_text") or "")
    repo_full = str(payload.get("repo")
                    or os.getenv("OG_SELF_REPO", "")).strip()

    def supersede(title, body):
        prop["status"] = "superseded"
        _note(problem, "code_fix_superseded", title)
        _save_problems(owner, problems)
        return (False, title, body)

    import og_github as _gh
    entry = _gh._github_connection(owner)
    if not repo_full or entry is None or "/" not in repo_full:
        return supersede(
            "Connect GitHub to ship this fix",
            f"The drafted fix for '{problem['title']}' ships as "
            "a pull request, which needs your GitHub connected "
            "and OG_SELF_REPO set to OG's own repo. Nothing was "
            "run and nothing was written.")
    owner_login, repo_name = repo_full.split("/", 1)
    token = str(entry.get("access_token") or "")
    status, data = _gh._gh_request(
        "GET", f"/repos/{owner_login}/{repo_name}", token)
    if status != 200 or not isinstance(data, dict):
        return supersede(
            "Couldn't re-check the repo",
            f"OG couldn't reach {repo_full} to re-verify the "
            "draft against it, so the fix was retired instead "
            "of shipped blind. Nothing was written.")
    default_branch = str(data.get("default_branch") or "main")
    st, info = _gh._fetch_file(token, owner_login, repo_name,
                                path, default_branch)
    if info is None or info.get("text") != base_text:
        return supersede(
            "The file moved since the draft",
            f"{path} changed on {repo_full} after OG drafted "
            "this fix, so the prepared diff no longer matches "
            "the live file. Nothing was shipped — OG will "
            "draft a fresh fix if the problem is still there.")
    test = _gh.run_local_checks(path, content)
    if not _gh.checks_green(test):
        return supersede(
            "The drafted fix no longer passes checks",
            f"OG re-ran the drafted fix for '{problem['title']}' "
            "in his sandbox just now and the checks did not "
            "pass, so nothing was shipped. He will draft a "
            "fresh fix if the problem is still there.")
    # Stamp executed BEFORE running: exactly once, even on a crash.
    prop["status"] = "executed"
    prop["decided_at"] = time.time()
    _note(problem, "fix_approved")
    _save_problems(owner, problems)
    branch = "og/fix-" + _slugify(str(problem.get("slug")
                                      or path))
    pending = {
        "repo_full": repo_full, "owner": owner_login,
        "repo": repo_name, "branch": branch, "path_hint": "",
        "thing": str(problem.get("title") or "fix"),
        "mode": "edit", "path": path,
        "base_sha": str(info.get("sha") or ""),
        "base_text": base_text,
        "default_branch": default_branch, "stage": "verified",
        "pkg": {"path": path, "content": content,
                "diff": payload.get("diff"), "test": test,
                "attempts": payload.get("attempts") or [],
                "lessons": payload.get("lessons_cited") or []},
        "py_paths": [], "tree_paths": [],
    }
    results = _gh._create_pr(
        entry, pending, {"path": path, "content": content}, owner)
    pr_url = ""
    for r in results or []:
        if "PR opened" in str(r.get("title", "")):
            m = re.search(r"https://github\.com/\S+/pull/\d+",
                          str(r.get("body", "")))
            pr_url = m.group(0) if m else ""
            break
    problems = _problems(owner)
    problem = problems.get(problem["fingerprint"], problem)
    prop = problem.get("proposal") or prop
    if pr_url:
        problem["status"] = "fixed"
        prop["outcome"] = "fixed"
        prop["pr_url"] = pr_url
        _note(problem, "fix_shipped_pr", pr_url)
        _save_problems(owner, problems)
        return (True, f"Fix shipped as a PR: {problem['title']}",
                f"Approved and done — OG opened a pull request "
                f"with exactly the drafted, sandbox-verified "
                f"fix: {pr_url} It merges only when you merge "
                f"it; OG never merges his own fixes.")
    problem["status"] = "fix_failed"
    prop["outcome"] = "fix_failed"
    _note(problem, "fix_failed", "PR flow stopped")
    _save_problems(owner, problems)
    _lesson_fix_failed(problem, prop,
                       "the pull-request flow stopped before a "
                       "PR opened")
    return (True, f"Fix didn't ship: {problem['title']}",
            "The pull-request flow stopped before a PR opened, "
            "so nothing was written to the repo. Nothing was "
            "retried — a second attempt needs a fresh proposal "
            "and a fresh approval from you.")


def decide(uid: str, ident: str, decision: str) -> Tuple[bool, str, str]:
    """The one approval door. Returns (ok, title, body).
    Executes ONLY for the owning uid, ONLY an open unexpired
    proposal, ONLY the prepared payload — then verifies and
    records the outcome either way."""
    found = _find(uid, ident)
    if found is None:
        return (False, "No fix waiting by that name",
                "No open fix by that name is waiting on YOUR "
                "account, so nothing was run. Fixes are approved "
                "by the account they belong to.")
    owner, problems, problem, prop = found
    now = time.time()
    if prop.get("status") != "open":
        return (False, "That fix was already answered",
                f"The fix for '{problem['title']}' is not open "
                f"anymore (status: {prop.get('status')}). Nothing "
                f"was run.")
    if now > float(prop.get("expires") or 0):
        prop["status"] = "expired"
        _note(problem, "proposal_expired")
        _save_problems(owner, problems)
        return (False, "That fix offer expired",
                f"The prepared fix for '{problem['title']}' is "
                f"over 7 days old, so it was retired instead of "
                f"run. OG will prepare a fresh one if the problem "
                f"is still there.")
    if decision == "decline":
        prop["status"] = "declined"
        prop["decided_at"] = now
        problem["declined_at"] = now
        _note(problem, "proposal_declined")
        _save_problems(owner, problems)
        return (True, "Fix declined",
                f"Left '{problem['title']}' open — nothing was "
                f"changed. OG can offer a fresh fix for it after "
                f"24 hours.")
    if decision != "approve":
        return (False, "Say approve or decline",
                "That answer wasn't an approval or a decline, "
                "so nothing was run.")

    if prop.get("fix_kind") == "owner_steps":
        prop["status"] = "handed_off"
        prop["decided_at"] = now
        problem["status"] = "acknowledged"
        _note(problem, "steps_handed_off")
        _save_problems(owner, problems)
        steps = (prop.get("payload") or {}).get("steps") or []
        lines = [f"Approved: {problem['title']}.",
                 prop.get("summary", ""),
                 "This one is in your hands — OG prepared the "
                 "steps, and nothing was run on the server:"]
        lines.extend(f"{i}. {s}" for i, s in enumerate(steps, 1))
        lines.append("When the condition clears, the next "
                     "system check closes this problem on its "
                     "own.")
        return (True, f"Fix steps: {problem['title']}",
                "\n".join(lines))

    if prop.get("fix_kind") == "code":
        return _decide_code(owner, problems, problem, prop)

    # server kind: re-verify the problem is still there first.
    if _condition_gone(problem):
        problem["status"] = "resolved"
        prop["status"] = "superseded"
        _note(problem, "already_gone_at_approve")
        _save_problems(owner, problems)
        return (False, "No fix needed anymore",
                f"'{problem['title']}' checked out clean just "
                f"now — the problem cleared on its own, so the "
                f"prepared fix was retired and nothing was run.")
    # Stamp executed BEFORE running: exactly once, even on a crash.
    prop["status"] = "executed"
    prop["decided_at"] = now
    _note(problem, "fix_approved")
    _save_problems(owner, problems)

    payload = prop.get("payload") or {}
    fn = _EXECUTORS.get(str(payload.get("executor") or ""))
    exec_note, exec_error = "", None
    if fn is None:
        exec_error = "the prepared executor is not in the registry"
    else:
        try:
            exec_note = fn(payload.get("args") or {})
        except Exception as e:
            exec_error = f"{type(e).__name__}: {e}"

    problems = _problems(owner)
    problem = problems.get(problem["fingerprint"], problem)
    prop = problem.get("proposal") or prop
    if exec_error is None and _condition_gone(problem):
        problem["status"] = "fixed"
        prop["outcome"] = "fixed"
        _note(problem, "fix_verified_fixed", exec_note)
        _save_problems(owner, problems)
        return (True, f"Fixed: {problem['title']}",
                f"Approved and done. {exec_note} OG re-checked "
                f"afterwards and the problem is gone.")
    problem["status"] = "fix_failed"
    prop["outcome"] = "fix_failed"
    _note(problem, "fix_failed", exec_error or exec_note)
    _save_problems(owner, problems)
    _lesson_fix_failed(problem, prop,
                       exec_error or "the re-check still showed "
                                     "the problem after the fix ran")
    why = (f"The fix itself failed ({exec_error})." if exec_error
           else f"The fix ran ({exec_note}) but the re-check "
                f"still shows the problem.")
    return (True, f"Fix didn't take: {problem['title']}",
            f"{why} Nothing was retried — a second attempt "
            f"needs a fresh proposal and a fresh approval from "
            f"you.")


# --- Panel feed ---------------------------------------------------------------------------


def _proposal_view(problem: dict, prop: dict) -> dict:
    view = {
        "id": prop.get("id"),
        "problem_id": problem.get("id"),
        "slug": problem.get("slug"),
        "title": problem.get("title"),
        "diagnosis": problem.get("diagnosis"),
        "summary": prop.get("summary"),
        "fix_kind": prop.get("fix_kind"),
        "status": prop.get("status"),
        "outcome": prop.get("outcome"),
        "created": prop.get("created"),
        "expires": prop.get("expires"),
    }
    if prop.get("fix_kind") == "owner_steps":
        view["steps"] = (prop.get("payload") or {}).get("steps") or []
    if prop.get("fix_kind") == "code":
        payload = prop.get("payload") or {}
        diff = str(payload.get("diff") or "")
        view["path"] = payload.get("path")
        view["diff"] = diff[:4000] + (
            "\n… (diff truncated in this view)"
            if len(diff) > 4000 else "")
        view["attempts"] = payload.get("attempts") or []
        view["lessons"] = payload.get("lessons_cited") or []
        view["pr_url"] = prop.get("pr_url")
    return view


def list_proposals(uid: str) -> dict:
    """The caller's own proposals: open ones first, then the
    most recent decided ones (newest 5) so outcomes stay
    visible. Somebody else's never appear."""
    if not uid:
        return {"proposals": [], "open_count": 0}
    _adopt_sentinel(uid)
    open_views, decided = [], []
    for owner in _owner_keys_for(uid):
        for prob in _problems(owner).values():
            prop = prob.get("proposal")
            if not isinstance(prop, dict):
                continue
            if prop.get("status") == "open":
                if time.time() > float(prop.get("expires") or 0):
                    continue  # lazily retired on decide; hidden here
                open_views.append(_proposal_view(prob, prop))
            elif prop.get("status") in ("executed", "handed_off",
                                        "declined"):
                decided.append(_proposal_view(prob, prop))
    open_views.sort(key=lambda v: float(v.get("created") or 0))
    decided.sort(key=lambda v: float(v.get("created") or 0),
                 reverse=True)
    return {"proposals": open_views + decided[:5],
            "open_count": len(open_views)}


# --- Chat seam (installed AFTER selfcheck: outermost claim) -------------------------------


def _result(tag: str, title: str, body: str, href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body, "href": href}]


_FIX_APPROVE_RE = re.compile(
    r"^\W*(approve|approved|yes|yeah|yep|ok|okay|confirm|"
    r"go ahead)\b[^.]*?\bfix\b\s*(?:for\s+)?(.+?)\s*$",
    re.I | re.S)
_FIX_DECLINE_RE = re.compile(
    r"^\W*(decline|declined|reject|rejected|deny|refuse|skip|"
    r"no)\b[^.]*?\bfix\b\s*(?:for\s+)?(.+?)\s*$",
    re.I | re.S)


def _claim_job(message: str) -> Optional[Dict]:
    text = str(message or "")
    for regex, decision in ((_FIX_APPROVE_RE, "approve"),
                            (_FIX_DECLINE_RE, "decline")):
        m = regex.match(text)
        if m:
            name = (m.group(2) or "").strip().strip(".! ")
            if name.lower().startswith("the "):
                name = name[4:]
            if name:
                return {"op": "fix_decision", "decision": decision,
                        "name": name}
    return None


def fixqueue_results(job: Dict, uid: str) -> List[Dict]:
    if job.get("op") == "fix_decision":
        ok, title, body = decide(uid, job.get("name", ""),
                                 job.get("decision", ""))
        return _result("OG FIX", title, body)
    return _result("OG FIX", "Fix queue", "Nothing to run for that one.")


def install_fixqueue_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search AFTER selfcheck (outermost):
    an 'approve the fix for X' must win over every other door,
    exactly the Round 49/50 precedence. The parser requires the
    word 'fix', so a bare YES can never be eaten."""
    if getattr(agent_instance, "_og_fixqueue_installed", False):
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
            logger.warning(f"Fixqueue trigger check failed: {e}")
            _pending["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _pending.get("job")
        _pending["job"] = None
        if job:
            try:
                return fixqueue_results(job, get_uid())
            except Exception as e:
                logger.warning(f"Fixqueue results failed: {e}")
                return _result(
                    "OG FIX", "Fix queue — glitch",
                    "The fix decision glitched — nothing was "
                    "run. Say it again and I'll retry.")
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_fixqueue_installed = True


# --- Routes ---------------------------------------------------------------------------------


def register_fixqueue_routes(app):

    @app.get("/approvals/proposals")
    async def approvals_proposals(request: Request):
        uid = ""
        try:
            uid = request.cookies.get("ogai_uid", "") or ""
        except Exception:
            uid = ""
        feed = list_proposals(uid)
        return {"ok": True, "proposals": feed["proposals"],
                "open_count": feed["open_count"]}

    @app.post("/approvals/proposals/act")
    async def approvals_proposals_act(request: Request):
        uid = ""
        try:
            uid = request.cookies.get("ogai_uid", "") or ""
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        ok, title, text = decide(uid, str(body.get("id") or ""),
                                 str(body.get("decision") or ""))
        return {"ok": ok, "title": title, "body": text,
                "error": "" if ok else text}
