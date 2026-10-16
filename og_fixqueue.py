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
   uid. Round 101 exception (Brent, 2026-10-12 — standing
   approval for the night shift): while the auto-update
   gate is ON and the feature has not disabled itself, a
   fresh proposal the nightly collector would auto-apply
   (a green code proposal against OG's own repo, touching
   only auto-eligible paths) sends NO fix_needed — there
   is nothing to ask. Fenced classes, not-green drafts,
   other kinds, and everything while the gate is off
   notify exactly as before. In chat: "approve the fix for <name>" / "decline the
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

6. THE 11PM HOLD (Round 71; Brent, 2026-10-11: "those
   should not pop up right now — those should be an 11 o' pm
   thing"). UPDATE-class problems (verify kind "update" —
   the package updates the self-check ingest mints) are
   stamped at creation with hold_until = the releasing
   23:00 in the owner's timezone (og_autoupdate's own
   clock): the next 23:00 for a daytime stamp, or the
   23:00 that opened the running night for one minted
   inside the nightly window (Round 72's seam — a
   nightly-minted update surfaces that same night). While
   the hold is active the proposal still exists in the
   queue — the nightly run reads the queue unfiltered and
   the operator surfaces are untouched — but it is excluded
   from list_proposals (Ideas / Approvals / the Progress
   board, which reads the same list) and NO notification is
   created for it. The hold is purely time-based: it lifts
   at 23:00 whether or not the auto-update gate is on.
   Update proposals minted before the hold existed carry no
   stamp; list_proposals backfills one (next 23:00 from the
   first read) through this module's own store, so nothing
   update-class ever surfaces unstamped. Every other class
   of problem is exactly as it was.

   Round 320 ADDENDUM (Brent, 2026-10-14 04:15 PDT: "Go
   ahead and lift it — if it helps us out"): the hold now
   rides the auto-update schedule switch (og_autoupdate's
   OG_AUTOUPDATE_SCHEDULE). While the schedule is OFF —
   the shipped default since Round 320 — there is NO hold:
   update proposals surface in list_proposals and their
   fix_needed notifications fire the moment they are
   minted, like every other class, and a stamp already
   sitting on an older problem is treated as released.
   Restoring OG_AUTOUPDATE_SCHEDULE=nightly restores the
   hold together with the window, exactly as written
   above. The stamp itself is still written at creation
   and backfilled as before — it is data; _hold_active is
   the only reader that gives it effect, and it answers
   False for everything while the schedule is off.
   Fail-safe: if og_autoupdate cannot answer, the hold
   STANDS (the pre-addendum posture) — a late pop-up is
   the safe error, a surprise one is not.
"""

import asyncio
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
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, StoreError, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo
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
    conn = psycopg.connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
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
            logger.warning(f"Fixqueue DB load failed: {e}")
            raise StoreError("fixqueue store unavailable (Round 99)") from e
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
            logger.warning(f"Fixqueue DB save failed: {e}")
            raise StoreError("fixqueue store unavailable (Round 99)") from e
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


# --- The 11pm hold for update proposals (Round 71) --------------------------------
# Round 320 addendum: the hold is LIFTED while the auto-update schedule
# is off (see _hold_lifted); OG_AUTOUPDATE_SCHEDULE=nightly restores it.

RELEASE_HOUR = 23  # owner-local; og_autoupdate's nightly window


def _hold_lifted() -> bool:
    """Round 320 addendum (Brent, 2026-10-14): True while
    the 11pm hold is lifted. The hold rides the auto-update
    schedule switch: og_autoupdate._schedule_nightly() is
    True only when OG_AUTOUPDATE_SCHEDULE=nightly restores
    the window, and only then does the hold stand. The
    shipped default (schedule off) lifts it. Fail-safe: any
    failure to answer means the hold STANDS — the
    pre-addendum posture; surfacing late is the safe
    error."""
    try:
        import og_autoupdate as _au
        return not _au._schedule_nightly()
    except Exception:
        return False


def _is_update_problem(problem) -> bool:
    """The UPDATE class, by producer kind: the self-check
    ingest updates loop is the only producer that stamps
    verify kind "update" (with the package name). Never
    title-matching, never fingerprint-matching."""
    if not isinstance(problem, dict):
        return False
    verify = problem.get("verify") or {}
    return isinstance(verify, dict) \
        and verify.get("kind") == "update"


def _release_ts(ts: float) -> float:
    """The 23:00 owner-local that releases a stamp made at
    ts. Daytime stamps get the next 23:00 strictly after ts
    (Round 71). A stamp minted INSIDE the nightly window
    (Round 72: the self-check battery runs in the window
    now, so that is where update problems are born) gets
    the 23:00 that OPENED the running night — already due
    the moment it lands, so a nightly-minted update
    surfaces THAT night: in Ideas, and counted by the
    nightly note's released-updates line, which keys on a
    past hold_until. The night is read through
    og_autoupdate's own _night_for/_owner_tz so the hold
    clock and the run clock are literally the same code
    (zoneinfo America/Los_Angeles with its fixed-offset
    fallback; the fallback mirrors _night_for's arithmetic:
    23:00-23:59 is that date's night, 00:00-03:59 is the
    previous date's catch-up tail)."""
    from datetime import datetime, timedelta, timezone
    tz = None
    night_for = None
    try:
        import og_autoupdate as _au
        tz, _ = _au._owner_tz()
        night_for = _au._night_for
    except Exception:
        tz = None
    if tz is None:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("America/Los_Angeles")
        except Exception:
            tz = timezone(timedelta(hours=-8))
    local = datetime.fromtimestamp(ts, tz)
    night = None
    if night_for is not None:
        try:
            night = night_for(local)
        except Exception:
            night = None
    elif local.hour >= RELEASE_HOUR:
        night = local.date().isoformat()
    elif local.hour < 4:
        night = (local.date() - timedelta(days=1)).isoformat()
    if night:
        try:
            _y, _m, _d = (int(p) for p in str(night).split("-"))
            return datetime(_y, _m, _d, RELEASE_HOUR,
                            tzinfo=tz).timestamp()
        except Exception:
            pass
    cand = local.replace(hour=RELEASE_HOUR, minute=0,
                         second=0, microsecond=0)
    if cand <= local:
        cand = cand + timedelta(days=1)
    return cand.timestamp()


def _hold_active(problem, now: Optional[float] = None) -> bool:
    """True while an update problem sits inside its 11pm
    hold: stamped, and the release moment is still ahead.
    Round 320 addendum: while the schedule is off the hold
    is lifted — this answers False for every problem, so
    an existing stamp reads as released."""
    if _hold_lifted():
        return False
    if not _is_update_problem(problem):
        return False
    hold = problem.get("hold_until")
    if not isinstance(hold, (int, float)):
        return False
    return (time.time() if now is None else now) < float(hold)


def _ensure_update_holds(owner: str) -> bool:
    """Backfill (Round 71): update-class problems recorded
    before the hold existed carry no stamp. The first time
    they are read for surfacing, stamp them to the next
    23:00 through this module's own store — a targeted data
    touch on the live update proposals only (open problem,
    open proposal, no stamp yet). Idempotent; returns True
    when anything was stamped. Fail-safe."""
    try:
        problems = _problems(owner)
        now = time.time()
        changed = False
        for prob in problems.values():
            if not _is_update_problem(prob):
                continue
            if prob.get("status") != "open":
                continue
            prop = prob.get("proposal")
            if not isinstance(prop, dict) \
                    or prop.get("status") != "open":
                continue
            if isinstance(prob.get("hold_until"), (int, float)):
                continue
            prob["hold_until"] = _release_ts(now)
            _note(prob, "update_hold_stamped")
            changed = True
        if changed:
            _save_problems(owner, problems)
        return changed
    except Exception as e:
        logger.warning(f"Fixqueue hold backfill failed: {e}")
        return False


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
        # Round 60: the failed approach is also noted in the
        # repo's conventions notebook, so future drafts for
        # that file steer away from it.
        try:
            payload = prop.get("payload") or {}
            if prop.get("fix_kind") == "code" \
                    and payload.get("repo") \
                    and payload.get("path"):
                import og_conventions as _conv
                _conv.note_failed_approach(
                    str(payload["repo"]), str(payload["path"]),
                    key)
        except Exception:
            pass
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
        draft_files = [f for f in (draft.get("files") or [])
                       if isinstance(f, dict) and f.get("path")]
        if len(draft_files) > 1:
            summary += (f" The fix spans {len(draft_files)} "
                        f"files: "
                        + ", ".join(f["path"]
                                    for f in draft_files) + ".")
        if len(draft.get("attempts") or []) > 1:
            summary += (f" It took "
                        f"{draft['attempts'][-1].get('n')} "
                        f"attempts — the earlier drafts failed "
                        f"his checks and he revised them himself.")
        for title_ in draft.get("lessons_cited") or []:
            summary += f" Lesson applied: {title_}."
        for conv in draft.get("conventions_cited") or []:
            summary += f" Convention applied: {conv}."
        payload = {
            "path": draft["path"],
            "base_text": draft["base_text"],
            "content": draft["content"],
            "diff": draft["diff"],
            "test": draft["test"],
            "attempts": draft["attempts"],
            "approach_key": draft["approach_key"],
            "lessons_cited": draft["lessons_cited"],
            "conventions_cited":
                draft.get("conventions_cited") or [],
            "repo": os.getenv("OG_SELF_REPO", "").strip(),
        }
        if len(draft_files) > 1:
            payload["files"] = draft_files
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


def _night_shift_will_apply(problem: dict,
                            proposal: dict) -> bool:
    """Round 101: True when the nightly auto-update
    (og_autoupdate) would collect this fresh proposal at
    23:00 — a green CODE proposal against OG's own repo,
    touching only auto-eligible paths, whose approach the
    lessons book has not already failed — while the gate
    is ON and the feature has not disabled itself. This
    mirrors og_autoupdate._collect's per-item tests on
    purpose (the collector stays the one applier; this
    only decides whether there is anything to ASK).
    While True, record_problem sends no fix_needed for
    the proposal: Brent's standing approval (2026-10-12)
    means there is nothing to ask. ANY doubt — imports,
    the autoupdate store (Round 99: a store that cannot
    answer raises), the gate — answers False: notifying
    is the safe default. NEVER raises."""
    try:
        if proposal.get("fix_kind") != "code":
            return False
        if not _is_operator_problem(problem):
            return False
        import og_autoupdate as _au
        if not _au.enabled():
            return False
        if _au._state().get("disabled"):
            return False
        payload = proposal.get("payload") or {}
        self_repo = os.getenv("OG_SELF_REPO", "").strip()
        repo = str(payload.get("repo") or self_repo).strip()
        if not self_repo or repo != self_repo:
            return False
        if not _au._is_green(proposal):
            return False
        key = _approach_key(proposal)
        if key:
            import og_lessons as _lessons
            if _lessons.approach_failed(
                    problem.get("fingerprint"), key):
                return False
        return _au.classify_paths(
            _au._touched_paths(proposal)) is None
    except Exception:
        return False


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
            if _is_update_problem(problem):
                # Round 71: an update waits for the 11pm
                # window before it may surface anywhere.
                # Round 320 addendum: the stamp is still
                # written, but it takes effect only while
                # OG_AUTOUPDATE_SCHEDULE=nightly restores
                # the hold — _hold_active is its only
                # reader, and it answers False while the
                # schedule is off.
                problem["hold_until"] = _release_ts(now)
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
        if proposal is not None and not _hold_active(problem) \
                and not _night_shift_will_apply(problem,
                                                proposal):
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
                f"release is {u.get('latest')}. A green "
                f"drafted update applies itself at any hour "
                f"now (Brent turned the nightly schedule "
                f"off — Round 320).",
                verify={"kind": "update", "package": pkg},
                fix={"kind": "owner_steps",
                     "summary": f"Update {pkg} "
                                f"{u.get('pinned')} -> "
                                f"{u.get('latest')}.",
                     "steps": [
                         "Dependency updates ship as code — "
                         "a green drafted one applies itself "
                         "at any hour now (the nightly "
                         "schedule is off — Round 320); "
                         "these steps are the manual route "
                         "when no draft could be prepared.",
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


def _decide_code(owner, problems, problem, prop, auto=None):
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
    # Round 60: a multi-file payload re-verifies EVERY file's
    # base the same way, and the re-check runs on the combined
    # tree with all drafts applied.
    multi_files = [f for f in (payload.get("files") or [])
                   if isinstance(f, dict) and f.get("path")]
    is_multi = len(multi_files) > 1
    multi_infos: Dict = {}
    if is_multi:
        for f in multi_files:
            fst, finfo = _gh._fetch_file(
                token, owner_login, repo_name, str(f["path"]),
                default_branch)
            if finfo is None \
                    or finfo.get("text") != f.get("base_text"):
                return supersede(
                    "The file moved since the draft",
                    f"{f['path']} changed on {repo_full} after "
                    "OG drafted this fix, so the prepared diff "
                    "no longer matches the live file. Nothing "
                    "was shipped — OG will draft a fresh fix "
                    "if the problem is still there.")
            multi_infos[str(f["path"])] = finfo
        test = _gh.run_local_checks_multi(
            {str(f["path"]): str(f.get("content") or "")
             for f in multi_files})
    else:
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
    if auto:
        # Round 69: the nightly auto-update of OG's OWN system
        # is the one caller that arrives without a human tap —
        # the stamp says so, with the rule it ran under.
        prop["decided_via"] = "nightly_auto_update"
        prop["auto_rule"] = str(auto.get("rule") or "")
        prop["auto_night"] = str(auto.get("night") or "")
        _note(problem, "fix_auto_applied",
              str(auto.get("rule") or "nightly auto-update"))
    else:
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
        # Round 60 item 5: the checks watch for this PR carries
        # the link back to this problem, so the post-merge
        # verification can record its verdict on the proposal.
        "fix_link": {
            "owner": owner,
            "fingerprint": problem.get("fingerprint"),
            "verify": problem.get("verify") or {},
            "repo_full": repo_full},
    }
    extracted = {"path": path, "content": content}
    if is_multi:
        pending["files"] = [{
            "path": str(f["path"]),
            "base_text": str(f.get("base_text") or ""),
            "base_sha": str((multi_infos.get(str(f["path"]))
                             or {}).get("sha") or ""),
        } for f in multi_files]
        pending["pkg"]["files"] = [{
            "path": str(f["path"]),
            "content": str(f.get("content") or ""),
            "diff": f.get("diff"),
        } for f in multi_files]
        if payload.get("conventions_cited"):
            pending["pkg"]["conventions"] = \
                payload["conventions_cited"]
        extracted["files"] = [{
            "path": str(f["path"]),
            "content": str(f.get("content") or ""),
        } for f in multi_files]
    results = _gh._create_pr(entry, pending, extracted, owner)
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


def _run_code_decision_bg(owner: str, fingerprint,
                          prop_id: str):
    """Round 83: the background half of a deferred code
    approve. decide() has ALREADY claimed the proposal
    (status 'working', stamped, saved) under the
    stamp-before-run discipline; this worker re-loads the
    owner's problems and runs the UNCHANGED _decide_code,
    whose own stamps land the durable outcome (executed /
    superseded) on every exit. An exception escaping
    _decide_code is caught here and stamped exactly the
    way decide()'s server-kind tail stamps a crashed
    executor — executed/fix_failed + a lesson — so a
    claimed proposal never strands silently at 'working'
    because of a code error. Runs on a daemon thread;
    never the event loop."""
    try:
        problems = _problems(owner)
        problem = problems.get(fingerprint or "")
        if problem is None:
            for p in problems.values():
                if (p.get("proposal") or {}).get("id") \
                        == prop_id:
                    problem = p
                    break
        if problem is None:
            return
        prop = problem.get("proposal") or {}
        if prop.get("id") != prop_id or \
                prop.get("status") != "working":
            return  # already landed by someone else
        _decide_code(owner, problems, problem, prop)
    except Exception:
        try:
            problems = _problems(owner)
            for p in problems.values():
                prop = p.get("proposal") or {}
                if prop.get("id") == prop_id and \
                        prop.get("status") == "working":
                    prop["status"] = "executed"
                    prop["outcome"] = "fix_failed"
                    p["status"] = "fix_failed"
                    _note(p, "code_fix_failed",
                          "The approved fix crashed before "
                          "it could land an outcome")
                    _save_problems(owner, problems)
                    _lesson_fix_failed(
                        p, prop,
                        "the background run crashed before "
                        "the fix could land an outcome")
                    break
        except Exception:
            pass


def decide(uid: str, ident: str, decision: str,
           auto: Optional[dict] = None,
           defer_code: bool = False) -> Tuple[bool, str, str]:
    """The one approval door. Returns (ok, title, body).
    Executes ONLY for the owning uid, ONLY an open unexpired
    proposal, ONLY the prepared payload — then verifies and
    records the outcome either way. Round 69: `auto` is the
    nightly auto-update's stamp (rule + night), passed ONLY
    by og_autoupdate for OG's own system fixes; every human
    caller leaves it None and nothing about their path
    changes. Round 83: `defer_code` is passed ONLY by the
    web act route — an approved CODE fix is claimed
    (status 'working', stamped before anything runs) and
    executed on a background thread by _decide_code,
    unchanged; decide() returns at once and the outcome
    lands on the proposal when the run lands. Every other
    decision/kind/caller behaves exactly as before."""
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
        if defer_code and not auto:
            # Round 83: claim FIRST — the stamp-before-run
            # discipline, tightened. The proposal leaves
            # 'open' before ANY of the run's network work
            # starts, so a second decide (a re-armed button
            # tap, a chat YES, the nightly pass) meets a
            # non-open proposal and runs nothing; the run
            # itself is the unchanged _decide_code on a
            # worker thread, off the event loop.
            prop["status"] = "working"
            prop["decided_at"] = now
            _note(problem, "fix_claimed",
                  "Owner approved — running in the "
                  "background")
            _save_problems(owner, problems)
            threading.Thread(
                target=_run_code_decision_bg,
                args=(owner, problem.get("fingerprint"),
                      prop["id"]),
                daemon=True).start()
            return (True, "Working on it",
                    f"OG is running the fix for "
                    f"'{problem['title']}' now — the "
                    f"re-checks and the pull request happen "
                    f"in the background. The card flips to "
                    f"the result when it lands; there is "
                    f"nothing else to tap.")
        return _decide_code(owner, problems, problem, prop,
                            auto=auto)

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


def record_fix_verdict(owner, fingerprint, verdict,
                       pr_url: str = "") -> bool:
    """Round 60 item 5: record the ONE post-merge verification
    verdict on an executed fix's proposal. Called by og_github's
    merge watch after the deploy window. verified_fixed closes
    the loop quietly; still_present reopens the problem, writes
    a recurrence LESSON (the Round 51 source) with the failed
    approach, and sends ONE fix_needed notification that the
    fix didn't take. Exactly one verdict is ever recorded —
    a second call is a no-op. NEVER raises; a verdict failure
    changes nothing else."""
    try:
        if verdict not in ("verified_fixed", "still_present"):
            return False
        owner_key = str(owner or "").strip() or SENTINEL_OWNER
        fp = str(fingerprint or "").strip()
        if not fp:
            return False
        problems = _problems(owner_key)
        problem = problems.get(fp)
        if problem is None:
            return False
        prop = problem.get("proposal")
        if not isinstance(prop, dict):
            return False
        if prop.get("verification"):
            return False  # exactly one verdict per executed fix
        prop["verification"] = verdict
        prop["verified_at"] = time.time()
        if pr_url:
            prop["verification_pr"] = pr_url
        if verdict == "verified_fixed":
            _note(problem, "post_merge_verified", pr_url or "")
            _save_problems(owner_key, problems)
            return True
        # still_present: the fix didn't take.
        problem["status"] = "open"
        _note(problem, "post_merge_still_present", pr_url or "")
        _save_problems(owner_key, problems)
        try:
            import og_lessons as _lessons
            _lessons.record_lesson(
                "recurrence",
                f"Fix didn't take: {problem.get('title')}",
                f"OG's approved fix for "
                f"'{problem.get('title')}' shipped as a pull "
                f"request ({pr_url or 'PR'}) and MERGED, but "
                f"the post-merge verification still shows the "
                f"problem. The fix did not hold — the next "
                f"approach must address the root cause, not "
                f"just the symptom that was verified.",
                fingerprint=fp,
                approach_key=_approach_key(prop))
        except Exception as e:
            logger.warning(f"Verdict lesson failed: {e}")
        if owner_key != SENTINEL_OWNER:
            try:
                import og_notify as _notify
                _tgt = {"view": "approvals"}
                if problem.get("id"):
                    _tgt["id"] = problem["id"]
                _notify.record(
                    owner_key, "fix_needed",
                    f"Fix didn't take: {problem['title']}",
                    f"Your approved fix for "
                    f"'{problem['title']}' merged ({pr_url}), "
                    f"but OG re-checked after the deploy and "
                    f"the problem is still there. It's back on "
                    f"the list — a fresh fix needs a fresh "
                    f"approval from you.",
                    target=_tgt)
            except Exception as e:
                logger.warning(f"Verdict notify failed: {e}")
        return True
    except Exception as e:
        logger.warning(f"Fix verdict record failed: {e}")
        return False


# --- Panel feed ---------------------------------------------------------------------------


def _proposal_view(problem: dict, prop: dict) -> dict:
    # Round 83: a superseded proposal's card says WHY — the
    # headline of the refusal that retired it, read from the
    # problem's own history (the note _decide_code's
    # supersede() recorded). Empty for every other status.
    status_note = ""
    if prop.get("status") == "superseded":
        for h in reversed(problem.get("history") or []):
            if isinstance(h, dict) and \
                    h.get("event") == "code_fix_superseded" \
                    and h.get("note"):
                status_note = str(h["note"])
                break
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
        "status_note": status_note,
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
        # Round 60: the post-merge verification verdict + the
        # conventions that shaped the draft + every file's diff
        # for a multi-file fix.
        view["verification"] = prop.get("verification")
        view["conventions"] = \
            payload.get("conventions_cited") or []
        files = payload.get("files") or []
        if len(files) > 1:
            view["files"] = [{
                "path": f.get("path"),
                "diff": str(f.get("diff") or "")[:4000] + (
                    "\n… (diff truncated in this view)"
                    if len(str(f.get("diff") or "")) > 4000
                    else ""),
            } for f in files if isinstance(f, dict)]
    return view


def list_proposals(uid: str) -> dict:
    """The caller's own proposals: ones being executed
    right now ('working', Round 83) first, then open ones,
    then the most recent decided ones (newest 5) so
    outcomes stay visible — 'superseded' among them since
    Round 83: an honest refusal is an outcome too, and a
    card that silently vanishes reads as a dead tap.
    Somebody else's never appear. open_count counts ONLY
    open proposals ('working' is decided, not waiting)."""
    if not uid:
        return {"proposals": [], "open_count": 0}
    _adopt_sentinel(uid)
    for owner in _owner_keys_for(uid):
        _ensure_update_holds(owner)
    open_views, decided, working_views = [], [], []
    for owner in _owner_keys_for(uid):
        for prob in _problems(owner).values():
            prop = prob.get("proposal")
            if not isinstance(prop, dict):
                continue
            if prop.get("status") == "open":
                if time.time() > float(prop.get("expires") or 0):
                    continue  # lazily retired on decide; hidden here
                if _hold_active(prob):
                    continue  # Round 71: an update inside its
                    # 11pm hold does not surface yet
                open_views.append(_proposal_view(prob, prop))
            elif prop.get("status") == "working":
                # Round 83: already approved and executing —
                # the hold governed surfacing FOR approval,
                # never the visibility of a running fix.
                working_views.append(_proposal_view(prob, prop))
            elif prop.get("status") in ("executed", "handed_off",
                                        "declined", "superseded"):
                decided.append(_proposal_view(prob, prop))
    open_views.sort(key=lambda v: float(v.get("created") or 0))
    working_views.sort(key=lambda v: float(v.get("created") or 0),
                       reverse=True)
    decided.sort(key=lambda v: float(v.get("created") or 0),
                 reverse=True)
    return {"proposals": working_views + open_views + decided[:5],
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


# Round 321c: the wall-clock claim budget lives in
# og_claimbudget; the dispatcher hub stays here — this
# module's detect wrapper opens/closes it per turn and
# its install runs the funnel census LAST.
from og_claimbudget import (  # noqa: E402
    CLAIM_BUDGET_SECONDS, begin_claim_budget,
    end_claim_budget, claim_budget_open, _wrap_claim_funnels)


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
        # Round 321c: the OUTERMOST detect — its entry opens
        # the turn's claim budget, its exit closes it.
        _budget_started = begin_claim_budget()
        try:
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
        finally:
            end_claim_budget(_budget_started)

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
    # Swearing switch ("swearing on switch off switch"): this
    # install runs last in app.py's get_agent, so the switch's
    # prompt-assembly wraps land outermost. Fail-safe — a switch
    # problem never takes the fixqueue seam (or chat) down.
    try:
        import swearing_on_switch_off_switch as _swearing
        _swearing.install_swearing_switch(agent_instance)
    except Exception as e:
        logger.warning(f"Swearing switch install skipped: {e}")
    # Round 89: the one-best-answer reinforcement rides the
    # same last install, AFTER the swearing switch and
    # BEFORE R65 — installed here, its prompt wraps sit
    # inside R65's, so at assembly time its directive block
    # is appended after the R65 block and lands LAST in the
    # system prompt (the persona law, restated as the final
    # word). Fail-safe — a problem here never takes the
    # fixqueue seam (or chat) down.
    try:
        import og_oneanswer as _ogone
        _ogone.install_oneanswer_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"R89 one-answer install skipped: {e}")
    # Round 65: language + retaliation-comeback directives ride
    # the same last install, AFTER the swearing switch (so the
    # combined R65 prompt wraps land outermost). Fail-safe — a
    # problem here never takes the fixqueue seam (or chat) down.
    try:
        import og_language as _oglang
        _oglang.install_language_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"R65 language/comeback install skipped: {e}")
    # Generator 7a: the podcast claim seam rides the same last
    # install, AFTER R65 (so podcast wraps land outermost).
    # Fail-safe — a problem here never takes the fixqueue seam
    # (or chat) down.
    try:
        import og_podcast as _ogpod
        _ogpod.install_podcast_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Podcast install skipped: {e}")
    # Generator 7b: the designer claim seam rides the same last
    # install, AFTER 7a (so designer wraps land outermost; its
    # claims require an explicit thumbnail/poster/meme noun, so
    # bare YES/NO and other generators' asks are never eaten).
    # Fail-safe — a problem here never takes the seam down.
    try:
        import og_designer as _ogdes
        _ogdes.install_designer_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Designer install skipped: {e}")
    # Generator 7c: the book claim seam rides the same last
    # install, AFTER 7b (so book wraps land outermost; its
    # bare YES/NO handling defers to any NEWER video/song/
    # podcast pending, and its other claims require an
    # explicit book/continue/rewrite shape). Fail-safe.
    try:
        import og_books as _ogbooks
        _ogbooks.install_books_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Book install skipped: {e}")
    # Generator 7d: the motion claim seam rides the same last
    # install, AFTER 7c (so motion wraps land outermost; its
    # claims require explicit motion language, and its bare
    # YES/NO defers to any newer generator pending). Fail-safe.
    try:
        import og_motion as _ogmotion
        _ogmotion.install_motion_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Motion install skipped: {e}")
    # Generator 7e: the 3D-model claim seam rides the same last
    # install, AFTER 7d (so models3d wraps land outermost; its
    # claims require an explicit 3D-model marker, and its bare
    # YES/NO defers to any newer generator pending). Fail-safe.
    try:
        import og_models3d as _ogmodels
        _ogmodels.install_models3d_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Model3D install skipped: {e}")
    # Generator 7f: the voice-lab claim seam rides the same
    # last install, AFTER 7e (so voice-lab wraps land
    # outermost; its claims require an explicit clone/sfx
    # marker, its bare YES/NO defers to any newer generator
    # pending, and a clone YES additionally requires the
    # rights attestation). Fail-safe.
    try:
        import og_voice_lab as _ogvoice
        _ogvoice.install_voice_lab_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Voice lab install skipped: {e}")
    # Round 81: the crew seam rides the same last install,
    # AFTER 7f (so crew wraps land outermost; its claims
    # require an explicit crew/researcher/runner frame or a
    # sentence-initial research verb, so bare YES/NO and
    # every other seam's asks are never eaten). Fail-safe.
    try:
        import og_crew as _ogcrew
        _ogcrew.install_crew_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Crew install skipped: {e}")
    # Round 84: the shit-talking seam rides the same last
    # install, AFTER the crew (so its wraps land outermost:
    # it notes the turn's uid before every other seam reads
    # it, and its chat flips are whole-message settings asks
    # naming the register, so no other seam's asks or bare
    # YES/NO are ever eaten). Fail-safe.
    try:
        import og_shittalk as _ogshit
        _ogshit.install_shittalk_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Shittalk install skipped: {e}")
    # Round 109: the Project Manager seam rides the same last
    # install, AFTER R84 (so PM wraps land outermost; its
    # claims require an explicit project frame, a plan-lead,
    # or the get-ready shape — and it never claims a message
    # the crew claims — so bare YES/NO and every other seam's
    # asks are never eaten). Fail-safe.
    try:
        import og_pm as _ogpm
        _ogpm.install_pm_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"PM install skipped: {e}")
    # Round 110: the Project Filing seam rides the same last
    # install, AFTER the PM (so filing wraps land outermost;
    # its claims require an explicit file frame tied to a
    # project — the word 'project' or a project name that
    # resolves — and it claims a bare YES/NO only while its
    # own suggestion is pending and no other flow owns the
    # answer). Fail-safe.
    try:
        import og_filing as _ogfiling
        _ogfiling.install_filing_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Filing install skipped: {e}")
    # Round 111: the Project Builder seam rides the same
    # last install, AFTER the Filer (its detect wraps
    # outermost; its claims require an explicit build frame
    # — a buildable noun, a build-plan lead, or an
    # adjustment to an open draft plan — and it defers to
    # the PM/Filer/crew claims checked exactly first, so
    # bare YES/NO and every other seam's asks are never
    # eaten). Knowledge + planning only: no ordering
    # machinery here — plan ordering asks leave with the
    # plan's own materials sheet and the ordering pack's
    # approval door.
    try:
        import og_builder as _ogbuilder
        _ogbuilder.install_builder_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Builder install skipped: {e}")
    # Round 112: the Wood Project Builder Pro seam rides
    # the same last install, AFTER the Builder (its detect
    # wraps outermost; it defers to the PM/Filer/crew
    # claims checked exactly first, and claims only
    # wood-flavored asks — furniture pieces, cut lists,
    # joinery, species, finishes, board feet — while
    # structures like decks and sheds stay the Builder's).
    # Knowledge + planning only: no ordering machinery —
    # lumber asks leave with the plan's own lumber sheet
    # and the ordering pack's approval door.
    try:
        import og_woodpro as _ogwoodpro
        _ogwoodpro.install_woodpro_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"WoodPro install skipped: {e}")
    # Round 113: the Maintenance + Engineer seam rides
    # the same last install, AFTER the Wood Pro (its
    # detect wraps outermost; it defers to the
    # PM/Filer/crew/Builder/WoodPro claims checked
    # exactly first, and claims only the built-world
    # upkeep lanes — symptom walks, PM schedules,
    # engineering math). Emergencies (gas smell, CO
    # alarm, sparking) claim before every defer: the
    # answer is the emergency line, full stop.
    # Knowledge + guidance only: no ordering machinery
    # — parts asks leave with the context's own parts
    # sheet and the ordering pack's approval door.
    try:
        import og_mainteng as _ogmainteng
        _ogmainteng.install_mainteng_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"MaintEng install skipped: {e}")
    # Round 114: the Car Mechanic seam rides the same
    # last install, AFTER Maintenance + Engineer (its
    # detect wraps outermost; it defers to the
    # PM/Filer/crew/Builder/WoodPro/MaintEng claims
    # checked exactly first, and claims only the car
    # lanes — symptom walks, OBD codes, the shop-job
    # lane, sounds, the vehicle list, intervals).
    # Car emergencies (brakes gone, fuel leaking,
    # steering lost, exhaust in the cabin) claim
    # before every defer: the answer is the emergency
    # line, full stop. Knowledge + guidance only: no
    # ordering machinery — parts asks leave with the
    # context's own parts sheet and the ordering
    # pack's approval door.
    try:
        import og_mechanic as _ogmechanic
        _ogmechanic.install_mechanic_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Mechanic install skipped: {e}")
    # Round 115: the Blueprint Specialist seam rides the
    # same last install, AFTER the Car Mechanic (its
    # detect wraps outermost; it defers to the
    # PM/Filer/crew/Builder/WoodPro/MaintEng/Mechanic
    # claims checked exactly first, and claims only
    # sheet-shaped asks — reads, takeoffs, scale math,
    # symbol meanings, "what does this call for",
    # two-sheet cross-checks). The seat READS through
    # the shipped reader (og_file_read) and the locker;
    # every answer names its source on the sheet, and
    # an unreadable part gets "can't read this part",
    # never a guessed dimension.
    try:
        import og_blueprint as _ogblueprint
        _ogblueprint.install_blueprint_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Blueprint install skipped: {e}")
    # Round 116: the Shopping seam rides the same last
    # install, AFTER the Blueprint Specialist (its detect
    # wraps outermost; it defers to the PM/Filer/crew/
    # Builder/WoodPro/MaintEng/Mechanic/Blueprint claims
    # checked exactly first, plus the R54 marketplace
    # claims and the ordering pack's claims). The seat
    # EXTENDS the shipped lanes — the web search it
    # harvests through the seam, and the caller's own
    # R54 Marketplace watch matches read at the data
    # level — and forks neither. It finds, compares,
    # refines, and saves picks; it NEVER buys, checks
    # out, pays, messages, or haggles: those crossings
    # stop at their approval doors.
    try:
        import og_shopping as _ogshopping
        _ogshopping.install_shopping_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Shopping install skipped: {e}")
    # Round 117: the Buying seam rides the same last
    # install, AFTER Shopping (its detect wraps
    # outermost; it defers to the PM/Filer/crew/
    # Builder/WoodPro/MaintEng/Mechanic/Blueprint
    # claims checked exactly first, plus the R54
    # marketplace claims and the ordering pack's
    # claims). ONE takeover, by design: Shopping's
    # buy-stop claim — the door R116 pointed at — is
    # taken by this seat, as is a fully-specified
    # direct ask (price + destination stated whole).
    # The seat prepares a buy to the approval wall
    # off the caller's saved pick / shortlist or a
    # direct ask: destination verified BEFORE any
    # card (a lookalike is flagged, no card parked),
    # ONE total only when every line is known, an
    # approval card, and a hand-off on approval.
    # OG NEVER PAYS: no payment machinery, no card
    # entry, no checkout completion — the user's own
    # hand pays at the merchant (app-store buys run
    # through Apple/Google billing).
    try:
        import og_buying as _ogbuying
        _ogbuying.install_buying_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Buying install skipped: {e}")
    # Round 118: the Selling seam rides the same last
    # install, AFTER Buying (its detect wraps outermost;
    # it defers to the PM/Filer/crew/Builder/WoodPro/
    # MaintEng/Mechanic/Blueprint claims checked exactly
    # first, plus Shopping, Buying, the R54 marketplace
    # claims, and the ordering pack's claims). The seat
    # prices off real comps (the seam harvest parsed by
    # the Shopping seat's own parser — no fork), drafts
    # the listing (title, description off stated facts
    # only, photo plan in order, terms = the user's
    # call, never promised), and drafts buyer-message
    # replies. THE FENCES: nothing posts, sends, or
    # lists without the user's approval of the EXACT
    # text (approval hands back the same words; any
    # edit voids it); the price floor is the USER'S —
    # offers at/over it get a reply drafted inside it,
    # offers below it park as an ask, never accepted,
    # never countered under it. OG never completes a
    # sale or touches payment: posting and sending are
    # the user's own taps (no poster is built — R54's
    # buyer-side chain stays whole, untouched).
    try:
        import og_selling as _ogselling
        _ogselling.install_selling_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Selling install skipped: {e}")
    # Round 119: the Coding + Programming specialist
    # seam rides the same last install, AFTER Selling
    # (its detect wraps outermost; it defers EXACTLY
    # first to og_github's own claim — its pendings,
    # its clarifying questions, every repo-shaped ask:
    # the R60 path stands untouched — then to the
    # PM/Filer/crew/Builder/WoodPro/MaintEng/Mechanic/
    # Blueprint claims, plus Shopping, Buying,
    # Selling, the R54 marketplace claims, and the
    # ordering pack's claims). The seat is the
    # specialist FACE of OG's coding: write-it asks
    # (drafted server-side + run through the R32
    # checker when drafting is available), pasted-
    # error debugging (the ONE best fix first),
    # plain-words explanations, language/tool picks
    # (ONE best answer), small-project scaffolds, and
    # test-it / fix-it on the caller's bench. THE
    # FENCES: PR-only + approval unchanged — this
    # seat opens no PR and writes nothing anywhere;
    # nothing executes on the user's machines (checks
    # run only in OG's server sandbox, through
    # og_github.run_python_checks); a check verdict
    # is reported BY NAME, never dressed.
    try:
        import og_coding as _ogcoding
        _ogcoding.install_coding_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Coding install skipped: {e}")
    # Round 120: the Phone specialist seam rides the
    # same last install, AFTER Coding (its detect wraps
    # outermost; it defers EXACTLY first to og_github's
    # own claim — the R60 path stands untouched — then
    # to the PM/Filer/crew/Builder/WoodPro/MaintEng/
    # Mechanic/Blueprint claims, plus Shopping, Buying,
    # Selling, Coding, the R54 marketplace claims, and
    # the ordering pack's claims). The seat is the
    # crew's phone hand: settings walks (the exact tap
    # path for the named phone/OS, dead-simple numbered
    # steps, ONE action at a time), troubleshooting
    # (ONE best first check, then the next), backups
    # and new-phone moves, and OG-on-your-phone answers
    # read LIVE off Round 90's Permissions store and
    # Round 73's location switch — never a copied state.
    # THE FENCE: OG cannot remote-control the user's
    # phone — the seat GUIDES, the user TAPS; a "do it
    # for me" ask gets the honest line first, then the
    # guided steps. No new powers, no new loops.
    try:
        import og_phone as _ogphone
        _ogphone.install_phone_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Phone install skipped: {e}")
    # Round 121: the CarPlay specialist seam rides the
    # same last install, AFTER Phone (its detect wraps
    # outermost; it defers EXACTLY first to
    # og_github's own claim — the R60 path stands
    # untouched — then to the PM/Filer/crew/Builder/
    # WoodPro/MaintEng/Mechanic/Blueprint claims, plus
    # Shopping, Buying, Selling, Coding, Phone, the
    # R54 marketplace claims, and the ordering pack's
    # claims). The seat is the crew's car hand: setup
    # + pairing walks and connection failures worked
    # as ONE ordered checklist (cable, port,
    # permissions, restart order — one check at a
    # time), apps + arranging the car home screen
    # (done on the phone), Siri/voice through the
    # car, maps/music/calls as they ride, and
    # OG-in-the-car as it honestly is (OG W in the
    # car's browser where one exists; TALK as the
    # hands-free path). THE FENCES: guide only — OG
    # never controls the car or its systems, no car
    # connection exists and none is built; DRIVING
    # SAFETY FIRST — setup/fix steps are for when
    # PARKED, said up front on every such ask, and a
    # driving ask is deferred to parked. The CarPlay
    # permission state is read LIVE off Round 90's
    # folder row, never copied. No new powers, no
    # new loops.
    try:
        import og_carplay as _ogcarplay
        _ogcarplay.install_carplay_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"CarPlay install skipped: {e}")
    # Round 122: the Tracker specialist seam rides the
    # same last install, AFTER CarPlay (its detect wraps
    # outermost; it defers EXACTLY first to og_github's
    # own claim — the R60 path stands untouched — then
    # to the PM/Filer/crew/Builder/WoodPro/MaintEng/
    # Mechanic/Blueprint claims, plus Shopping, Buying,
    # Selling, Coding, Phone, CarPlay, the R54
    # marketplace chain, og_watch's own claim,
    # og_monitor's own parse, and the ordering pack's
    # claims). The seat is the crew's tracker: ONE chat
    # front-end that unifies OG's tracking asks and
    # routes each through the machinery that owns it —
    # price tracks ARE og_monitor watches (its caps,
    # cadence, fire-once, prepend + og_notify delivery),
    # page tracks ARE og_watch fbwatches (its slot
    # ladder, seen-store, states), whereabouts read
    # Round 73's GPS points live, and package status
    # answers off the caller's own Buying book. THE
    # FENCES: a tracker tracks only what belongs to
    # the signed-in user — tracking another PERSON
    # (following someone, finding someone's location,
    # watching their device to locate them) is REFUSED
    # BY NAME, first; read-only tracking — the seat
    # never buys, books, messages, or pays, and a
    # fired price track hands off to the Buying helper
    # and stops there; states read off the real
    # stores, never invented; no new loop, no new
    # timer.
    try:
        import og_tracker as _ogtracker
        _ogtracker.install_tracker_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Tracker install skipped: {e}")
    # Round 123: the MARKETPLACE specialist installs in the
    # same last install, AFTER Tracker (its detect wraps
    # outermost; it defers EXACTLY first to og_github's own
    # claim — the R60 path stands untouched — then to the
    # PM/Filer/crew/Builder/WoodPro/MaintEng/Mechanic/
    # Blueprint claims, plus Shopping, Buying, Selling,
    # Coding, Phone, CarPlay, Tracker, the R54 marketplace
    # chain, og_watch's own claim, og_monitor's own parse,
    # and the ordering pack's claims). The seat is the
    # crew's Marketplace hand: Marketplace-named searches
    # nobody claimed (the caller's watched matches via
    # og_shopping's lane reader + fresh Marketplace
    # listings off the web harvest, parsed by
    # og_shopping's own parser — a few in range, BEST
    # DEAL FIRST, source/as-of on every price), the
    # per-account hunt book (hunts here; watches +
    # approvals read live off og_watch/og_mpwatch), and
    # the pick that hands off to the Buying helper
    # through the one pick store it already reads. THE
    # FENCES — Brent's standing marketplace law at full
    # strength: ask before messaging a seller (the R54
    # approval; this seat holds NO send machinery and
    # parks no drafts); ask SEPARATELY before haggling,
    # inside a ceiling the user sets, no counter past
    # it; final yes before anything is agreed; OG never
    # pays, never agrees a price on its own, never
    # arranges a meetup; listings read, never invented;
    # no new loop, no new timer.
    try:
        import og_marketplace as _ogmarketplace
        _ogmarketplace.install_marketplace_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Marketplace install skipped: {e}")
    # Round 124: the FACEBOOK specialist installs in the
    # same last install, AFTER Marketplace (its detect
    # wraps outermost; it defers EXACTLY first to
    # og_github's own claim — the R60 path stands
    # untouched — then to the PM/Filer/crew/Builder/
    # WoodPro/MaintEng/Mechanic/Blueprint claims, plus
    # Shopping, Buying, Selling, Coding, Phone, CarPlay,
    # Tracker, Marketplace, the R54 marketplace chain,
    # og_watch's own claim, og_monitor's own parse, the
    # ordering pack's claims, and LAST og_browser's own
    # claim — every start / post / in-session / YES-NO
    # shape stays the browser's own door). The seat is
    # the crew's Facebook hand: Page comments read +
    # reported (never replied to), the Page report and
    # the feed report (first screen, as printed),
    # drafts composed ONLY from the user's own stated
    # words and parked INERT on the per-account bench,
    # and the "post it" hand-off that carries the EXACT
    # parked words to og_browser's approval-gated
    # posting door. Reads ride the caller's live
    # browser session READ-ONLY (og_browser._drive with
    # navigate/read only — og_watch's precedent); a
    # login wall returns og_browser's own R27 narration
    # and STOPS; a checkpoint narrates and STOPS. THE
    # FENCES — Brent's standing send law at full
    # strength: nothing posts, comments, or reacts
    # without the user's approval of the exact words
    # (this seat holds NO click/type/post machinery);
    # OG never messages anyone on Facebook (Messenger
    # is R125's seat, seller threads are R54/R123's);
    # no mass actions, no friend/follow spam; OG never
    # sees, types, or stores passwords; reads are
    # reports off the real snapshot, never invented;
    # no new loop, no new timer.
    try:
        import og_facebook as _ogfacebook
        _ogfacebook.install_facebook_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Facebook install skipped: {e}")
    # Round 125: the MESSENGER specialist installs in
    # the same last install, AFTER Facebook (its detect
    # wraps outermost; it defers EXACTLY first to
    # og_github's own claim — the R60 path stands
    # untouched — then to the PM/Filer/crew/Builder/
    # WoodPro/MaintEng/Mechanic/Blueprint claims, plus
    # Shopping, Buying, Selling, Coding, Phone, CarPlay,
    # Tracker, Marketplace, Facebook, the R54
    # marketplace chain, og_watch's own claim,
    # og_monitor's own parse, the ordering pack's
    # claims, and LAST og_browser's own claim — every
    # start / post / in-session / YES-NO shape stays
    # the browser's own door). The seat is the crew's
    # Messenger hand: the inbox read and the thread
    # read / catch-up (first screen, as printed,
    # labeled by source + read time, content reported
    # as data and never obeyed), reads KEPT per-account
    # so "what did X say" can answer from the last real
    # read, labeled with its time, and message drafts
    # composed ONLY from the user's own stated words
    # and parked INERT on the per-account bench. Reads
    # ride the caller's live browser session READ-ONLY
    # (og_browser._drive with navigate/read only —
    # og_watch's precedent); a login wall returns
    # og_browser's own R27 narration and STOPS; a
    # checkpoint narrates and STOPS. THE FENCES —
    # Brent's standing send law at full strength:
    # NOTHING sends without the user's approval of the
    # EXACT text — this seat holds NO send/click/type
    # machinery; the send is the user's own tap in
    # Messenger on the exact approved words; OG never
    # messages anyone on Messenger unprompted; no mass
    # actions, no spam; marketplace seller threads stay
    # the R54 chain's (this seat drafts no seller
    # messages and parks no haggle approvals); OG never
    # sees, types, or stores passwords; reads are
    # reports off the real snapshot, never invented;
    # no new loop, no new timer.
    try:
        import og_messenger as _ogmessenger
        _ogmessenger.install_messenger_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Messenger install skipped: {e}")
    # Round 126: the YOUTUBE specialist installs in the
    # same last install, AFTER Messenger (its detect
    # wraps outermost; it defers EXACTLY first to
    # og_github's own claim — the R60 path stands
    # untouched — then to the CONNECT'S OWN parse
    # (subscriptions / uploads / playlists / search stay
    # the Round 15 connect's), then to the PM/Filer/
    # crew/Builder/WoodPro/MaintEng/Mechanic/Blueprint
    # claims, plus Shopping, Buying, Selling, Coding,
    # Phone, CarPlay, Tracker, Marketplace, Facebook,
    # Messenger, the R54 marketplace chain, og_watch's
    # own claim, og_monitor's own parse, the ordering
    # pack's claims, and LAST og_browser's own claim).
    # The seat EXTENDS the Round 15 og_youtube connect
    # in place — no second connect, no second token
    # store: the channel report, the video report, and
    # the comments read ride the connect's own _request
    # with the caller's own token, numbers exactly as
    # the Data API prints them, labeled by source +
    # read time. Drafts (reply / title / description /
    # tags) are composed ONLY from the user's own stated
    # words and parked INERT on the per-account bench.
    # THE FENCES — Brent's standing law at full
    # strength: NOTHING uploads, publishes, or posts
    # without the user's approval of the exact text
    # (this seat holds NO write machinery: GET reads
    # only); the connect's scopes bound it plainly —
    # Analytics numbers name the missing
    # yt-analytics.readonly scope, a reply post names
    # the missing youtube.force-ssl scope, an upload
    # names the missing youtube.upload scope, and none
    # of them is ever faked; OG never comments as the
    # user unapproved; no view/sub manipulation, no
    # spam; reads key on the caller's uid only, so a
    # cross-account ask surfaces only the caller's own
    # connection state; no new loop, no new timer.
    try:
        import og_youtube as _ogyoutube
        _ogyoutube.install_youtube_seat_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"YouTube seat install skipped: {e}")
    # Round 127: the WEBSITE + BROWSER specialist
    # installs in the same last install, AFTER
    # Facebook (its detect wraps outermost; it defers
    # EXACTLY first to og_github's own claim — the
    # R60 path stands untouched — then to the
    # PM/Filer/crew/Builder/WoodPro/MaintEng/Mechanic/
    # Blueprint claims, plus Shopping, Buying,
    # Selling, Coding, Phone, CarPlay, Tracker,
    # Marketplace, the R54 marketplace chain,
    # Messenger, Facebook, og_watch's own claim,
    # og_monitor's own parse, the ordering pack's
    # claims, and LAST og_browser's own claim — every
    # start / post / in-session / YES-NO shape stays
    # the browser's own door). The seat is the OG
    # Browser's specialist front door, never a second
    # browser: it compares two pages side by side
    # (read-only _drive, each target gated by
    # _url_gate before it opens, the browser restored
    # after), reads browser status and the
    # always-allow list off og_browser's own
    # surfaces, and fills a form right up to the
    # commit wall by delegating each field to
    # og_browser's own act/type hands. THE FENCES —
    # Brent's standing browser law at full strength:
    # THE APPROVAL GATE ON EVERY COMMIT ACTION STAYS
    # THE BROWSER'S OWN (this seat holds no click
    # machinery and no gate of its own); CHECKPOINTS
    # AND LOGIN WALLS MEAN NARRATION + STOP (never
    # solved, never worked around; OG never sees,
    # types, or stores passwords); TAKEOVER FREEZES
    # OG'S HANDS — ZERO ACTIONS WHILE THE USER
    # DRIVES; TIER MINUTE CAPS RIDE OG_BROWSER'S OWN
    # METER (no second session, no second meter);
    # reads are reports off the real snapshot, never
    # invented; the crew Runner's background errands
    # and the Tracker's page watches stay theirs;
    # no new loop, no new timer.
    try:
        import og_webbrowser as _ogwebbrowser
        _ogwebbrowser.install_webbrowser_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Webbrowser install skipped: {e}")
    # Round 278: GOD MODE AI CODING installs in the
    # same last install, AFTER the landed helper
    # chain (its detect wraps outermost among the
    # helper seats; the Round 275 observer still
    # wraps outside it). It defers EXACTLY first to
    # og_github's own claim — the R60 path stands
    # untouched — then to the YouTube connect's own
    # parse and the R126 seat, PM/Filer/crew/
    # Builder/WoodPro/MaintEng/Mechanic/Blueprint
    # claims, plus Shopping, Buying, Selling,
    # CODING (the master coders' seat — ordinary
    # and master coding answer first where the
    # chains overlap), Phone, CarPlay, Tracker,
    # Marketplace, the R54 marketplace chain,
    # Facebook, Messenger, og_watch's own claim,
    # og_monitor's own parse, the ordering pack's
    # claims, the R127 Website + Browser seat, and
    # LAST og_browser's own claim. The seat is the
    # crew's ESCALATION door for code: whole-build
    # plans (plans first — the seat writes NO code
    # itself), rescue reads off the Round 60
    # codebase map, job splits across the seats,
    # and its own bench (kept plans file through
    # og_fixqueue.record_problem — the R53/R101
    # approval door). THE FENCES — Brent's law at
    # full strength: GOD MODE IS A SEAT, NOT A
    # BYPASS (every change rides the fix-approval
    # pipeline / PR gate / 11pm law); IT NEVER
    # PUSHES DIRECT AND NEVER LANDS ITS OWN CODE
    # (no GitHub write machinery lives here);
    # CODE SAFETY'S STOP STANDS OVER THIS SEAT;
    # IT NEVER RUNS PASTED CODE ITSELF AND NEVER
    # STAMPS CODE "100% SAFE"; nothing outside
    # coding is this seat's chair; per-account
    # bench isolation; no new loop, no new timer.
    try:
        import og_godmodecoding as _oggodmodecoding
        _oggodmodecoding.install_godmodecoding_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"GodModeCoding install skipped: {e}")
    # Round 279: GOD MODE AI TESTER installs in the
    # same last install, AFTER the landed helper
    # chain (its detect wraps outermost among the
    # helper seats; the Round 275 observer still
    # wraps outside it). It defers EXACTLY first to
    # og_github's own claim — the R60 path stands
    # untouched — then to the YouTube connect's own
    # parse and the R126 seat, PM/Filer/crew/
    # Builder/WoodPro/MaintEng/Mechanic/Blueprint
    # claims, plus Shopping, Buying, Selling,
    # CODING (the bench's test/fix shapes stay
    # its), GOD MODE CODING defensively (R278's
    # plans / rescue reads / splits stay its),
    # Phone, CarPlay, Tracker, Marketplace, the
    # R54 marketplace chain, Facebook, Messenger,
    # og_watch's own claim, og_monitor's own
    # parse, the ordering pack's claims, the R127
    # Website + Browser seat, MASTER RUNSET
    # defensively (R277's baselines / deltas stay
    # its), and LAST og_browser's own claim. The
    # stopped test-running seat is NOT in the
    # chain and NOT referenced — running belongs
    # to the machinery's owners. The seat is the
    # crew's TESTING JUDGMENT: test strategy
    # (what must be tested, how deep, in what
    # order, sized to the risk), verdicts BY NAME
    # (real break / known family — the r29/r38
    # Steel live-profile race, the r61 and r83
    # timing families / timing-shaped / not
    # enough proof), escalation reads that name
    # the one fault the whole picture supports,
    # and its own per-account bench. THE FENCES —
    # Brent's law at full strength: GOD MODE IS A
    # SEAT, NOT A BYPASS; IT FIXES NOTHING ITSELF
    # AND HOLDS NO RUNNER (findings go to the
    # fixing seats through the approval door);
    # IT NEVER STAMPS A BUILD "100% SAFE" (the
    # stamp ask is claimed and refused); IT NEVER
    # MOVES A BASELINE; IT NEVER CLAIMS A RUN IT
    # DID NOT READ; nothing outside testing is
    # this seat's chair; per-account bench
    # isolation; no new loop, no new timer.
    try:
        import og_godmodetester as _oggodmodetester
        _oggodmodetester.install_godmodetester_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"GodModeTester install skipped: {e}")
    # Round 280: GOD MODE AI RUNSET installs in the
    # same last install, AFTER the landed helper
    # chain AND the God Mode Coding seat (its
    # detect wraps outermost among the helper
    # seats; the Round 275 observer still wraps
    # outside it). It is the TOP runset seat,
    # above Master AI Runset (R277): two of its
    # shapes (the dispute settlement and the
    # run-level top verdict) are checked BEFORE
    # its deferral, by design — no other seat
    # gives a run-level ruling or settles a
    # new-vs-family dispute — and everything
    # else defers EXACTLY first to og_github's
    # own claim, the YouTube connect's own parse
    # and the R126 seat, PM/Filer/crew/Builder/
    # WoodPro/MaintEng/Mechanic/Blueprint claims,
    # plus Shopping, Buying, Selling, CODING,
    # the God Mode Coding seat (checked exactly),
    # Phone, CarPlay, Tracker, Marketplace, the
    # R54 marketplace chain, Facebook, Messenger,
    # og_watch's own claim, og_monitor's own
    # parse, the ordering pack's claims, the
    # R127 Website + Browser seat, the God Mode
    # Tester seat and Master Runset (both
    # DEFENSIVE getattr-probed checks wherever
    # they answer), and LAST og_browser's own
    # claim. THE FENCES — Brent's law at full
    # strength: GOD MODE IS A TIER, NOT A BYPASS;
    # READ-ONLY AT THE TOP (edits nothing,
    # re-runs nothing, files nothing); IT NEVER
    # MOVES A BASELINE (baselines move only
    # through a landing's NOTES); IT NEVER
    # STAMPS A RUN "100% SAFE"; COMPLETENESS IS
    # STATED FIRST in every ruling; IT NEVER
    # RULES FROM NOTHING; per-account bench
    # isolation; no new loop, no new timer.
    try:
        import og_godmoderunset as _oggodmoderunset
        _oggodmoderunset.install_godmoderunset_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"GodModeRunset install skipped: {e}")
    # Round 281: GOD MODE AI MAKE MONEY FAST
    # installs in the same last install, AFTER
    # the landed helper chain (its detect wraps
    # outermost among the helper seats; the
    # Round 275 observer still wraps outside
    # it). It checks its two fence shapes FIRST
    # (the scam read and the guarantee
    # correction — both require money context),
    # then defers EXACTLY first to og_github's
    # own claim — the R60 path stands untouched
    # — then to the YouTube connect's own parse
    # and the R126 seat, PM/Filer/crew/Builder/
    # WoodPro/MaintEng/Mechanic/Blueprint
    # claims, plus Shopping, Buying, Selling,
    # CODING, GOD MODE CODING defensively,
    # Phone, CarPlay, Tracker, Marketplace, the
    # R54 marketplace chain, Facebook,
    # Messenger, og_watch's own claim,
    # og_monitor's own parse, the ordering
    # pack's claims, TRADING's own claim (the
    # per-trade approval door stands untouched),
    # og_plaid's own parse (the bank-read door),
    # the R127 Website + Browser seat, GOD MODE
    # TESTER and GOD MODE RUNSET defensively,
    # the unbuilt MONEY CLUSTER defensively
    # (R261/R271/R262/R263/R265 — this seat's
    # claims sit after theirs where they
    # overlap, per its SPEC), GOD MODE MONEY
    # (R305, the keeping side) and GOD MONEY
    # MAKER (R319, Brent's owner-only mode)
    # defensively, and LAST og_browser's own
    # claim. The seat is the TOP OF THE MONEY
    # CLUSTER: the ranked fast-money plan
    # (fastest legitimate routes to real cash,
    # ranked by honest time-to-first-dollar,
    # every lane carrying its realistic take —
    # labeled ESTIMATED with its basis — its
    # real cost, and its real catch), the scam
    # read (fee-first / keys-first /
    # signature-first / too-good-to-be-true
    # NAMED A SCAM and refused), the guarantee
    # correction, and its own per-account
    # bench. THE FENCES — Brent's law at full
    # strength: GOD MODE IS A TIER OF SEATS,
    # NOT A BYPASS (every lower seat's fence
    # and approval door stands over this one);
    # LEGITIMATE ONLY (theft, fraud, deception
    # refused by name); IT MOVES NO MONEY,
    # CLAIMS NOTHING, AND SIGNS NOTHING (every
    # tap stays the user's at the official
    # door, through the owning seat's approval
    # chain); NO GUARANTEED-INCOME CLAIMS,
    # EVER; the keeping side is R305's chair
    # and never blurs into this seat; nothing
    # outside making-money-fast is this seat's
    # chair; per-account bench isolation; no
    # new loop, no new timer.
    try:
        import og_godmodemoneyfast as _oggodmodemoneyfast
        _oggodmodemoneyfast.install_godmodemoneyfast_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"GodModeMoneyFast install skipped: {e}")
    # Round 275: the ACTIVITY OBSERVER installs in
    # the same last install, AFTER Messenger (its
    # web_search wrap is outermost). It routes
    # NOTHING and answers NOTHING: it reads the tag
    # off the REAL routing result ("[OG SELLING] ..."
    # — the house result shape) and records which
    # helper seat claimed this account's turn on the
    # caller's own bench (og_activity_data) — the
    # record the header bubble's live line and, later,
    # the Crew Dashboard read. THE HONESTY LAW
    # (Brent's, the R275 SPEC's): the bubble never
    # shows activity that is not happening — no seat
    # is named unless that seat actually claimed work
    # for this account, no count is shown that was
    # not read live from a real store (og_watch /
    # og_monitor / og_crew, read at answer time,
    # nothing copied). Fail-soft: an observer error
    # loses a bubble update, never a chat turn. The
    # switch is OG_ACTIVITY_ENABLED (unset = ON);
    # off = nothing records and the bubble falls back
    # to its static truthful states. No new loop, no
    # new timer; persona untouched.
    try:
        import og_activity as _ogactivity
        _ogactivity.install_activity_observer(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Activity observer install skipped: {e}")
    # Round 321c: LAST — every seat module is imported by
    # now, so the claim-budget census wraps their funnels.
    try:
        _wrap_claim_funnels()
    except Exception as e:
        logger.warning(f"Claim-budget wrap skipped: {e}")
    agent_instance._og_fixqueue_installed = True


# --- Routes ---------------------------------------------------------------------------------


def _route_uid(request) -> str:
    """Round 95: the caller's ONE identity — the live
    session's account uid when signed in, else the
    presented cookie (og_accounts.session_uid_or, the
    tierfix resolver every Round 95 surface uses)."""
    uid = ""
    try:
        uid = request.cookies.get("ogai_uid", "") or ""
    except Exception:
        uid = ""
    try:
        import og_accounts as _acct
        return _acct.session_uid_or(request.cookies, uid)
    except Exception:
        return uid


def register_fixqueue_routes(app):

    @app.get("/approvals/proposals")
    # Round 321 (Step 3.1): def, not async def — the body is
    # sync store work; FastAPI runs def handlers in its
    # threadpool, so a stalled DB can never freeze the loop.
    def approvals_proposals(request: Request):
        uid = _route_uid(request)
        feed = list_proposals(uid)
        return {"ok": True, "proposals": feed["proposals"],
                "open_count": feed["open_count"]}

    @app.post("/approvals/proposals/act")
    async def approvals_proposals_act(request: Request):
        uid = _route_uid(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        # Round 83: decide() NEVER runs on the event loop
        # (the R80 rule this route missed) — and with
        # defer_code an approved code fix is claimed and
        # executed on decide()'s own worker thread, so the
        # POST answers at once with the working state
        # instead of holding the tap hostage for the whole
        # GitHub sequence. decide()'s discipline — owner
        # only, stamp before run, exactly once — is
        # unchanged; only the transport moved.
        ok, title, text = await asyncio.to_thread(
            decide, uid, str(body.get("id") or ""),
            str(body.get("decision") or ""), defer_code=True)
        return {"ok": ok, "title": title, "body": text,
                "error": "" if ok else text}
