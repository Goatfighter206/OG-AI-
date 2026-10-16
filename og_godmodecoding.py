"""Round 278: GOD MODE AI CODING (Brent's words,
2026-10-13 13:32 PDT: "God mode Ai coding
helper" — read back as the top coding seat above
the master coders and APPROVED 13:34; specced in
round-278/SPEC.md.) The crew's escalation door
for code — the seat OG calls when a coding job
is too big or too tangled for the regular coding
seats.

WHAT ALREADY EXISTED (ridden, never forked):

  - THE CODING SEAT OWNS CODING (Round 119,
    og_coding). Ordinary coding answers there:
    write-it (drafted server-side + run through
    the Round 32 checker), pasted-error debugging
    (the ONE best fix first), plain-words
    explanations, language/tool picks, small-
    project scaffolds, and test-it / fix-it on the
    caller's bench. The master coders of the
    roster (Master Coder / Master Code Generation)
    ride that same seat — their plan-and-generate
    shapes are og_coding's own. This seat checks
    og_coding._claim_exact EXACTLY in its
    deferral chain — checked, never executed —
    and steps aside for every shape it claims:
    WHERE THE CHAINS OVERLAP, CODING ANSWERS
    FIRST. God mode takes only what the coding
    seat does not claim.
  - REPO-SHAPED ASKS ROUTE TO ROUND 60 EXACTLY
    (og_github — its pendings, its clarifying
    questions, its read/write/checks asks, the
    PR-only door). This seat checks og_github
    FIRST in its deferral and holds NO GitHub
    machinery of its own: no token use, no PR
    calls, no repo writes, nothing.
  - THE CODEBASE MAP IS ROUND 60'S TOO.
    og_github.build_local_repo_map (og_codemore's
    map, re-exported through the one og_github
    seam) + map_facts_block are the read this
    seat's rescue reads ride — searched first,
    like OG's self-build, never a second reader.
  - THE APPROVAL DOOR IS THE FIX QUEUE'S
    (Round 53 / Round 101). og_fixqueue.
    record_problem is the intake every proposal
    in OG already rides: fingerprint dedupe, one
    live proposal, the Approvals panel, approve /
    decline, the 11pm self-maintenance law. This
    seat FILES through that door and holds no
    execution machinery of its own.
  - THE CREW CODER STAYS THE CREW'S (og_crew's
    dispatched jobs and their caps); the Unity
    seat owns Unity asks; Code Safety owns the
    safety read on code — this seat defers to
    every earlier seat exactly, in house order,
    and og_browser's own claim is checked LAST.

WHAT IT OWNS — the escalation shapes nobody
claimed:

  - WHOLE-BUILD PLANS. "God mode: plan the whole
    og_watch rebuild", "plan a multi-file change
    for X": the FULL plan first — what changes,
    in what order, in which files, what each
    step risks, how each step gets verified —
    composed deterministically off the ask (the
    read first, one bounded step per named file,
    the sandbox checks, the door last) and
    PARKED on the caller's bench. NO PLAN,
    NO CODE: this seat writes no code at all —
    a plan is its product, and the code lanes
    (the coding seat, the GitHub door) do any
    writing under their own fences.
  - HARDEST-PROBLEM CODING, AT THE PLAN LEVEL.
    The jobs the other seats couldn't crack come
    here as a plan built from the ACTUAL
    codebase (the rescue read below feeds it),
    never from a guess: the seat searches first,
    then plans.
  - RESCUE READS. "Rescue read on og_watch",
    "nobody can figure out why the login flow is
    broken": the whole picture read off the repo
    map — the target's own definitions, what it
    imports, every file that imports IT, the
    shared surface, and the drift the map
    supports (names its callers use that live in
    OTHER files, never in the target) — and the
    read names where the fault hunt starts and
    WHY, in plain words. The loudest symptom is
    reported as a symptom; the read is the map's
    picture, labeled as a read — a read is not a
    fix, and the fix, when wanted, goes in a
    plan and through the door.
  - COORDINATION. "Split this job: …, who
    should take which piece": the job breaks
    into seat-sized pieces on the sentence
    boundaries the user gave, and each piece is
    assigned by lane — tests to the test-running
    machinery, repo/PR work to the GitHub door,
    safety reads to Code Safety, Unity work to
    the Unity seat, writing/fixing to the Coding
    seat — and the split parks with the plan so
    it can be filed the same way. The pieces
    still LAND through the same doors as always;
    an assignment here moves nothing itself.
  - THE BENCH. Kept plans re-serve with no new
    work: "file it" / "file the plan" sends the
    caller's last DRAFT plan through the
    approval door as an owner_steps proposal
    carrying the plan's own steps (filed once —
    a repeat ask reports the filing, it never
    files twice); "god mode status" reads the
    bench back. GET /godmodecoding/status serves
    the same picture.

THE FENCES — binding:

  - GOD MODE IS A SEAT, NOT A BYPASS. Every
    change this seat produces goes through the
    existing doors — the fix-approval pipeline /
    PR gate / the 11pm self-maintenance law —
    exactly like every other coding seat.
  - IT NEVER PUSHES DIRECT and never lands its
    own code: the module holds no GitHub write
    machinery, no PR machinery, no shell, no
    process-spawn (source-pinned). Its only write
    into the world is a PROPOSAL through
    og_fixqueue.record_problem — which lands
    nothing until an approval says so.
  - PLANS FIRST, like the master coders: no
    plan, no code — and this seat's code count
    is ZERO by construction; drafting belongs
    to the coding seat and the GitHub door.
  - CODE SAFETY'S STOP STANDS OVER THIS SEAT:
    a change Code Safety has stopped does not
    proceed because god mode planned it; a plan
    that touches a stopped area says so in its
    steps and files as a proposal like any
    other — the STOP is named, never routed
    around.
  - IT NEVER RUNS PASTED CODE ITSELF. Any check
    named in a plan runs in OG's server sandbox
    through the machinery that already owns
    checks (og_github.run_python_checks) when
    the plan is executed through its door —
    never here, never on the user's machines.
  - IT NEVER STAMPS CODE "100% SAFE" — a plan
    names its risks per step, a read names what
    the map does and does not show, and that is
    the whole honesty of the seat.
  - NOTHING OUTSIDE CODING: pricing, money,
    connections, and account actions stay
    behind their own fences — a "god mode" ask
    aimed at them is not this seat's chair
    (guarded by name), and the other god-mode
    seats' chairs (Tester, Runset, Money,
    Organizer, Push, Validation, Fixer, Design,
    Writer, Unity, Boss, Roster, Builder) are
    guarded the same way until those seats land.
  - Per-account isolation like every seat: one
    account's bench, plans, and reads are never
    shown to another. Persona file never
    touched; app.py NOT touched — module +
    og_library/og_fixqueue seams only. No new
    loop, no new timer (R72). Switch
    OG_GODMODECODING_ENABLED (unset = ON; off =
    the seat doesn't exist).
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
from typing import Dict, List, Optional

import og_garble

from fastapi import Request

logger = logging.getLogger(__name__)

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, \
    StoreError as _StoreError, apply_bounds as _apply_bounds

try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # pragma: no cover - local fallback
    psycopg = None
    _Jsonb = None

_TAG = "OG GOD MODE CODING"

GODMODECODING_STORE_FILE = os.getenv(
    "OG_GODMODECODING_STORE", "godmodecoding_store.json")

_gmc_lock = threading.RLock()


# --- The seat switch ---------------------------------------------------------


def godmodecoding_enabled() -> bool:
    """OG_GODMODECODING_ENABLED is the seat's kill
    switch: an explicit off (0/false/no/off) = the
    seat doesn't exist. Unset = ON (the crew
    pattern)."""
    v = os.getenv(
        "OG_GODMODECODING_ENABLED", "").strip().lower()
    return v not in ("0", "false", "no", "off")


# --- The bench store (Round 18 pattern: Postgres og_godmodecoding_data when
# --- OG_MEMORY_DB_URL is set, the JSON file otherwise) -----------------------


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_godmodecoding_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(GODMODECODING_STORE_FILE):
        try:
            with open(GODMODECODING_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"GodModeCoding store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(GODMODECODING_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"GodModeCoding store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_godmodecoding_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"GodModeCoding DB load failed: {e}")
            raise _StoreError(
                "godmodecoding store unavailable (Round 99)") from e
    with _gmc_lock:
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
                        "INSERT INTO og_godmodecoding_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"GodModeCoding DB save failed: {e}")
            raise _StoreError(
                "godmodecoding store unavailable (Round 99)") from e
    with _gmc_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


def _plans(uid: str) -> List[Dict]:
    got = _blob(uid, "plans", [])
    return got if isinstance(got, list) else []


def _save_plans(uid: str, plans: List[Dict]):
    _put_blob(uid, "plans", plans[-20:])


def _park_plan(uid: str, plan: Dict) -> Dict:
    plans = _plans(uid)
    plans.append(plan)
    _save_plans(uid, plans)
    return plan


def _draft_plan(uid: str) -> Optional[Dict]:
    for plan in reversed(_plans(uid)):
        if str(plan.get("state") or "") == "draft":
            return plan
    return None


def _reads(uid: str) -> List[Dict]:
    got = _blob(uid, "reads", [])
    return got if isinstance(got, list) else []


def _park_read(uid: str, read: Dict):
    reads = _reads(uid)
    reads.append(read)
    _put_blob(uid, "reads", reads[-20:])


# --- Result + text helpers (the house result shape) ----------------------------


def _result(title: str, body: str, href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{_TAG}] " + body,
             "href": href}]


def _asof() -> str:
    return time.strftime("%I:%M %p").lstrip("0") + " today"


def _clean(text, cap: int = 220) -> str:
    t = " ".join(str(text or "").split())
    if len(t) > cap:
        t = t[:cap] + "…"
    return t


# --- The plan composer (deterministic — the plan is built off the ask) --------


_FILE_RE = re.compile(r"\b(og_[a-z0-9_]+)(?:\.py)?\b")


def _named_targets(text: str) -> List[str]:
    seen: List[str] = []
    for m in _FILE_RE.finditer(str(text or "").lower()):
        stem = m.group(1)
        if stem not in seen:
            seen.append(stem)
    return seen[:6]


def _goal_of(raw: str) -> str:
    """The ask with its trigger wording stripped —
    what's left is the job the plan is for."""
    t = " ".join(str(raw or "").split()).strip()
    t = re.sub(r"^\W*(?:hey\s+)?god\s*mode\b\s*[:\-—]?\s*",
               "", t, flags=re.I)
    t = re.sub(r"\bgod\s*mode\b\s*[:\-—]?\s*", "", t,
               flags=re.I)
    t = re.sub(r"^(?:please\s+)?plan\s+(?:the\s+|a\s+)?"
               r"(?:whole|entire|full)\s+", "plan the ",
               t, flags=re.I)
    t = re.sub(r"^(?:please\s+)?plan\s+a\s+(?:multi[-\s]?file|"
               r"whole[-\s]?system|cross[-\s]?file)\s+",
               "plan a ", t, flags=re.I)
    t = t.strip(" .")
    return _clean(t, 240) or "the job as stated"


def _build_steps(goal: str, targets: List[str]) -> List[Dict]:
    steps: List[Dict] = []
    steps.append({
        "target": "the codebase map",
        "change": "Read the whole picture first — the repo "
                  "map plus every file that touches the "
                  "target(s) — before any step below is "
                  "trusted (search first, like the "
                  "self-build).",
        "risk": "none — a read changes nothing",
        "verify": "the read is named in the plan's first "
                  "line when it files",
    })
    named = targets or []
    if named:
        for stem in named:
            steps.append({
                "target": f"{stem}.py",
                "change": f"Draft the {stem} change as ONE "
                          f"bounded piece — plan first, "
                          f"code second, through the coding "
                          f"lanes that own drafting.",
                "risk": "the seam risk: claim order and the "
                        "seats around this file — a step "
                        "that ignores them breaks the chain",
                "verify": "the file's own round suite, by "
                          "name, in the server sandbox",
            })
    else:
        steps.append({
            "target": "the named area (no file was named)",
            "change": "Scope the change to the smallest "
                      "set of files the read supports, one "
                      "bounded piece per file, drafted "
                      "through the coding lanes.",
            "risk": "scope risk: an unnamed target means "
                    "the read decides the file set — the "
                    "filing names them before approval",
            "verify": "each touched file's own round "
                      "suite, by name, in the server sandbox",
        })
    steps.append({
        "target": "the checks",
        "change": "Run the named checks on the drafted "
                  "pieces — the Round 32 checker plus the "
                  "round suites the steps touch — in OG's "
                  "server sandbox only, never on your "
                  "machines.",
        "risk": "battery time: a timeout is reported BY "
                "NAME, never counted as a pass",
        "verify": "a check verdict is reported by name, "
                  "never dressed — and never stamped "
                  "\"100% safe\"",
    })
    steps.append({
        "target": "the approval door",
        "change": "File this plan through the fixqueue "
                  "door as a proposal — nothing lands "
                  "without an approval, and a decline "
                  "discards it whole.",
        "risk": "none until approved — the door is the "
                "gate, and Code Safety's STOP stands over "
                "every step above",
        "verify": "the proposal's own record in the "
                  "Approvals panel",
    })
    return steps


def _plan_lines(plan: Dict) -> List[str]:
    lines = [f"THE PLAN — {plan.get('goal')}",
             f"Parked on your bench at {_asof()} "
             f"(plan #{plan.get('id')}). Nothing is "
             f"written anywhere yet — plans first."]
    for i, step in enumerate(plan.get("steps") or [], 1):
        lines.append(
            f"{i}. [{step.get('target')}] "
            f"{step.get('change')}")
        lines.append(
            f"   risk: {step.get('risk')} · "
            f"verify: {step.get('verify')}")
    lines.append(
        "Say \"file it\" and this plan goes through "
        "the approval door as a proposal — it lands "
        "only on an approval (the Approvals panel / "
        "the 11pm law), and God Mode never pushes "
        "direct.")
    return lines


def _new_plan(uid: str, kind: str, goal: str,
              steps: List[Dict],
              split: Optional[List[Dict]] = None) -> Dict:
    plan = {
        "id": hashlib.sha1(
            f"{uid}|{goal}|{time.time()}".encode(
                "utf-8")).hexdigest()[:8],
        "kind": kind,
        "goal": goal,
        "steps": steps,
        "split": split or [],
        "state": "draft",
        "filed_id": None,
        "at": time.time(),
    }
    return _park_plan(uid, plan)


# --- Ops ------------------------------------------------------------------------


def _do_plan(uid: str, claim: Dict) -> List[Dict]:
    raw = str(claim.get("raw") or "")
    goal = _goal_of(raw)
    targets = _named_targets(raw)
    plan = _new_plan(uid, "plan", goal,
                     _build_steps(goal, targets))
    body = "\n".join(_plan_lines(plan))
    return _result("God Mode Coding — the whole-build plan",
                   body)


def _repo_map():
    """The ONE codebase read (Round 60's map through
    the og_github seam). Fail-safe None — a dark map
    is an honest answer, never a guessed one."""
    try:
        import og_github
        fn = getattr(og_github, "build_local_repo_map",
                     None)
        if fn is None:
            return None
        m = fn()
        return m if isinstance(m, dict) else None
    except Exception:
        return None


def _target_path(repo_map: Dict, raw: str) -> str:
    """The map path the ask names: an og_ stem first;
    else the longest word in the ask that matches a
    map stem. '' when the map holds nothing named."""
    files = (repo_map or {}).get("files") or {}
    stems = {}
    for path in files:
        base = str(path).rsplit("/", 1)[-1]
        stem = base[:-3] if base.endswith(".py") else base
        stems[stem] = path
    named = _named_targets(raw)
    for stem in named:
        if stem in stems:
            return stems[stem]
    words = [w for w in re.findall(r"[a-z0-9]+",
                                   str(raw or "").lower())
             if len(w) >= 5]
    for word in sorted(words, key=len, reverse=True):
        hits = [s for s in stems if word in s]
        if len(hits) == 1:
            return stems[hits[0]]
    return ""


def _do_rescue(uid: str, claim: Dict) -> List[Dict]:
    raw = str(claim.get("raw") or "")
    repo_map = _repo_map()
    if repo_map is None:
        return _result(
            "God Mode Coding — rescue read",
            "The rescue read is DARK: the codebase map "
            "(Round 60's, through the GitHub seam) did "
            "not build on this box, so I have no whole "
            "picture to read — and I won't guess at a "
            "fault without one. The map is the first "
            "thing to fix; the read rides it or it "
            "doesn't ride at all.")
    path = _target_path(repo_map, raw)
    files = repo_map.get("files") or {}
    if not path or path not in files:
        named = _named_targets(raw)
        what = (f"{named[0]}.py" if named else "that target")
        return _result(
            "God Mode Coding — rescue read",
            f"The map has NO file for {what} — it is "
            f"not in this codebase's picture "
            f"({len(files)} files mapped), so there is "
            f"nothing honest to read. Name a file the "
            f"map holds and the read runs on it.")
    base = path.rsplit("/", 1)[-1]
    stem = base[:-3] if base.endswith(".py") else base
    entry = files.get(path) or {}
    defined = []
    for s in entry.get("symbols") or []:
        name = str(s.get("name") or "").split(".")[-1]
        if name and name not in defined:
            defined.append(name)
    imports = [str(i) for i in (entry.get("imports")
                                or [])]
    referencers = []
    drift = []
    defined_set = set(defined)
    other_defined = set()
    for other_path, other in files.items():
        if other_path == path:
            continue
        for s in (other or {}).get("symbols") or []:
            nm = str(s.get("name") or "").split(".")[-1]
            if nm:
                other_defined.add(nm)
    for other_path, other in files.items():
        if other_path == path:
            continue
        if stem in [str(i) for i in
                    (other or {}).get("imports") or []]:
            referencers.append(other_path)
            for used in (other or {}).get("uses") or []:
                u = str(used)
                if (u in other_defined
                        and u not in defined_set
                        and u not in drift
                        and len(u) >= 4):
                    drift.append(u)
    facts = ""
    try:
        import og_github
        fn = getattr(og_github, "map_facts_block", None)
        if fn is not None:
            facts = str(fn(repo_map, path) or "")
    except Exception:
        facts = ""
    _park_read(uid, {"target": path, "at": time.time(),
                     "drift": drift[:3],
                     "referencers": len(referencers)})
    lines = [
        f"THE RESCUE READ — {base}",
        f"The whole picture off the codebase map "
        f"({len(files)} files), read at {_asof()}. "
        f"This is a READ, not a fix.",
        f"• {base} defines {len(defined)} names"
        + (f" — {', '.join(defined[:8])}"
           if defined else "")
        + (f" (+{len(defined) - 8} more)"
           if len(defined) > 8 else "") + ".",
        f"• It imports: {', '.join(imports[:10])}"
        if imports else
        "• It imports no og_ modules the map shows.",
        f"• {len(referencers)} file(s) import it"
        + (f" — {', '.join(p.rsplit('/', 1)[-1] for p in referencers[:6])}"
           if referencers else "") + ".",
    ]
    if drift:
        lines.append(
            f"• THE DRIFT THE MAP SUPPORTS: its "
            f"callers use {', '.join(drift[:3])} — "
            f"names that live in OTHER files, never "
            f"in {base}. A moved or renamed piece is "
            f"the first suspect, and the hunt starts "
            f"there, not at the loudest symptom.")
    else:
        lines.append(
            "• NO STRUCTURAL DRIFT: every name its "
            "callers use that the map can see is "
            "defined where they expect it. The fault "
            "is not visible in the map's structure — "
            "it sits in behavior, and the hunt starts "
            "at the failing check, by name.")
    if facts:
        lines.append("• The map's own facts for the "
                     "target ride the drafting lane "
                     "when a plan files from this read.")
    lines.append(
        "Want it fixed? Say \"plan the fix\" and the "
        "fix becomes a whole-build plan — through "
        "the approval door, never direct.")
    return _result("God Mode Coding — rescue read",
                   "\n".join(lines))


_LANE_RES = [
    (re.compile(r"\b(?:safety|dangerous|unsafe|"
                r"code safety)\b", re.I),
     "Code Safety — its STOP stands over the piece"),
    (re.compile(r"\b(?:test|tests|testing|check|"
                r"checks|verify|battery|suite)\b",
                re.I),
     "the test-running machinery (suites by name)"),
    (re.compile(r"\b(?:unity|3d|avatar|app build|"
                r"phone app)\b", re.I),
     "the Unity seat"),
    (re.compile(r"\b(?:repo|github|pull request|"
                r"\bpr\b|push|merge|branch)\b", re.I),
     "the GitHub door (Round 60, PR-only)"),
    (re.compile(r"\b(?:fix|debug|broken|error|"
                r"crash)\b", re.I),
     "the Coding seat (fix lane)"),
    (re.compile(r"\b(?:write|draft|build|add|"
                r"create|generate|code)\b", re.I),
     "the Coding seat (write lane)"),
]


def _seat_for(piece: str) -> str:
    for rx, seat in _LANE_RES:
        if rx.search(piece):
            return seat
    return "God Mode Coding holds this piece"


def _pieces_of(raw: str) -> List[str]:
    t = " ".join(str(raw or "").split()).strip()
    t = re.sub(r"^\W*(?:hey\s+)?god\s*mode\b\s*"
               r"[:\-—]?\s*", "", t, flags=re.I)
    t = re.sub(r"\bgod\s*mode\b\s*[:\-—]?\s*", "", t,
               flags=re.I)
    t = re.sub(r"^(?:please\s+)?(?:split|break)\s+"
               r"(?:this|the)\s+(?:coding\s+)?job"
               r"(?:\s+into\s+pieces)?\s*[:\-—]?\s*",
               "", t, flags=re.I)
    t = re.sub(r"\s+who\s+should\s+take\s+which\s+"
               r"piece\s*[.!?]*$", "", t, flags=re.I)
    parts = re.split(r";|\band then\b|\band also\b",
                     t)
    pieces: List[str] = []
    for part in parts:
        part = part.strip(" ,.")
        part = re.sub(r"^\d+[.)]\s*", "", part)
        if len(part) >= 8:
            pieces.append(_clean(part, 160))
    if len(pieces) < 2 and "," in t:
        pieces = [_clean(p, 160)
                  for p in t.split(",")
                  if len(p.strip()) >= 8][:6]
    return pieces[:6]


def _do_split(uid: str, claim: Dict) -> List[Dict]:
    raw = str(claim.get("raw") or "")
    pieces = _pieces_of(raw)
    if len(pieces) < 2:
        return _result(
            "God Mode Coding — split the job",
            "A split needs the pieces named — lay the "
            "job out with its parts separated (\"split "
            "this job: fix the seat; add its suite; "
            "push it through the door\") and each piece "
            "gets its seat. With one piece there's "
            "nothing to split — that's a plan, not a "
            "split.")
    split = [{"piece": p, "seat": _seat_for(p)}
             for p in pieces]
    steps = [{
        "target": s["seat"],
        "change": f"Take the piece: {s['piece']}",
        "risk": "the handoff risk: a piece lands only "
                "through its own seat's doors — an "
                "assignment here moves nothing itself",
        "verify": "the owning seat's own checks, by "
                  "name",
    } for s in split]
    goal = "split job: " + "; ".join(pieces)[:180]
    plan = _new_plan(uid, "split", goal, steps,
                     split=split)
    lines = [f"THE SPLIT — {len(split)} pieces, "
             f"parked with plan #{plan.get('id')}:"]
    for i, s in enumerate(split, 1):
        lines.append(f"{i}. {s['piece']} → {s['seat']}")
    lines.append(
        "Every piece still lands through its own "
        "seat's doors. Say \"file it\" and the whole "
        "split files as one proposal.")
    return _result("God Mode Coding — split the job",
                   "\n".join(lines))


def _do_file(uid: str, claim: Dict) -> List[Dict]:
    plan = _draft_plan(uid)
    if plan is None:
        filed = [p for p in _plans(uid)
                 if str(p.get("state") or "") == "filed"]
        if filed:
            last = filed[-1]
            return _result(
                "God Mode Coding — file it",
                f"Your last plan (#{last.get('id')}) is "
                f"already filed through the approval "
                f"door as proposal "
                f"{last.get('filed_id') or '—'} — it "
                f"lands only on an approval, and there "
                f"is no draft left to file. Ask for a "
                f"new plan and I'll build one.")
        return _result(
            "God Mode Coding — file it",
            "There is NO draft plan on your bench to "
            "file. Ask me to plan the whole build "
            "first — no plan, no filing.")
    record = None
    try:
        import og_fixqueue
        record = getattr(og_fixqueue, "record_problem",
                         None)
    except Exception:
        record = None
    if record is None:
        return _result(
            "God Mode Coding — file it",
            "The approval door (the fix queue) is DARK "
            "on this box right now, so the plan stays "
            "a draft on your bench — nothing was "
            "filed, nothing was lost. It files the "
            "moment the door is back.")
    steps_txt = []
    for i, step in enumerate(plan.get("steps") or [], 1):
        steps_txt.append(
            f"{i}. [{step.get('target')}] "
            f"{step.get('change')} "
            f"(risk: {step.get('risk')})")
    fp = "godmodecoding:" + hashlib.sha1(
        str(plan.get("goal") or "").encode(
            "utf-8")).hexdigest()[:12]
    pid = record(
        uid, "godmodecoding", fp,
        "god-mode-plan-" + str(plan.get("id") or ""),
        "God Mode plan: "
        + _clean(plan.get("goal"), 120),
        "Whole-build plan from the God Mode Coding "
        "seat (plan #" + str(plan.get("id") or "")
        + "). Plans first — no code was written by "
        "the seat; the plan's steps are the proposal.",
        verify={"kind": "none"},
        fix={"kind": "owner_steps",
             "summary": "God Mode Coding plan: "
                        + _clean(plan.get("goal"), 160),
             "steps": steps_txt or [
                 "Read the plan in the Approvals "
                 "panel and approve or decline it."]})
    if not pid:
        return _result(
            "God Mode Coding — file it",
            "The approval door took nothing just now "
            "(no proposal id came back) — the plan "
            "stays a draft on your bench and nothing "
            "was filed. Say \"file it\" again to "
            "retry.")
    plans = _plans(uid)
    for p in plans:
        if p.get("id") == plan.get("id"):
            p["state"] = "filed"
            p["filed_id"] = pid
    _save_plans(uid, plans)
    return _result(
        "God Mode Coding — filed through the door",
        f"FILED. Plan #{plan.get('id')} ("
        f"{_clean(plan.get('goal'), 140)}) went "
        f"through the approval door as proposal "
        f"{pid}. It lands ONLY on an approval — the "
        f"Approvals panel or the 11pm law — and a "
        f"decline discards it whole. God Mode filed "
        f"a proposal; it pushed nothing.")


def godmodecoding_status_for(uid: str) -> Dict:
    plans = _plans(uid)
    drafts = [p for p in plans
              if str(p.get("state") or "") == "draft"]
    filed = [p for p in plans
             if str(p.get("state") or "") == "filed"]
    last = plans[-1] if plans else None
    return {
        "plans": len(plans),
        "drafts": len(drafts),
        "filed": len(filed),
        "reads": len(_reads(uid)),
        "last": ({
            "id": last.get("id"),
            "kind": last.get("kind"),
            "goal": last.get("goal"),
            "state": last.get("state"),
        } if last else None),
    }


def _do_status(uid: str, claim: Dict) -> List[Dict]:
    view = godmodecoding_status_for(uid)
    if not view["plans"] and not view["reads"]:
        return _result(
            "God Mode Coding — bench status",
            "Your God Mode bench is EMPTY: no plans "
            "parked, no rescue reads held. Ask for a "
            "whole-build plan or a rescue read and "
            "the bench starts.")
    lines = [
        f"YOUR GOD MODE BENCH (as of {_asof()}):",
        f"• plans parked: {view['plans']} "
        f"({view['drafts']} draft, "
        f"{view['filed']} filed through the door)",
        f"• rescue reads held: {view['reads']}",
    ]
    last = view.get("last")
    if last:
        lines.append(
            f"• last plan: #{last.get('id')} — "
            f"{_clean(last.get('goal'), 140)} "
            f"({last.get('state')})")
    lines.append(
        "Kept plans re-serve from here with no new "
        "work — say \"file it\" to send the draft "
        "through the door.")
    return _result("God Mode Coding — bench status",
                   "\n".join(lines))


def godmodecoding_results(claim: Dict,
                          uid: str) -> List[Dict]:
    op = (claim or {}).get("op")
    if op == "gmc_plan":
        return _do_plan(uid, claim)
    if op == "gmc_rescue":
        return _do_rescue(uid, claim)
    if op == "gmc_split":
        return _do_split(uid, claim)
    if op == "gmc_file":
        return _do_file(uid, claim)
    if op == "gmc_status":
        return _do_status(uid, claim)
    return _result(
        "God Mode Coding",
        "That god-mode coding ask didn't land — say "
        "it again and the top seat takes it.")


# --- Claims ---------------------------------------------------------------------
#
# The claims order, pinned (SPEC, Round 278): this seat installs
# AFTER the landed helper chain (its seam block sits after the
# outermost landed helper block in og_fixqueue and before the
# Round 275 Activity block) — its detect wraps outermost among
# the helper seats — and its _claim_exact DEFERS EXACTLY FIRST:
# og_github's own claim, then the inner owners in house order,
# with og_coding (the Coding seat — the roster's Master Coder /
# Master Code Generation ride that same seat) checked exactly
# among them. Where the chains overlap, ordinary and master
# coding answer first; god mode claims only the escalation
# shapes the coding seat has no claim on: whole-build plans,
# rescue reads, splits, and its own bench's follow-ups. When
# the Master AI line (R139/R140) lands as separate seats later,
# they slot between og_coding and this seat by the same rule —
# this seat's deferral keeps that ordering honest without a
# change here.


def _other_claim(message: str, uid: str):
    """(source, claim) of the first inner owner that
    claims this message exactly, else None. og_github
    FIRST — its pendings, clarifying questions, and
    every repo-shaped ask are the R60 path's own.
    Then the YouTube CONNECT'S OWN PARSE, the R126
    YOUTUBE SEAT exactly, PM, Filer, crew, Builder,
    WoodPro, MaintEng, Mechanic, Blueprint, Shopping,
    Buying, Selling, CODING (the master coders' seat —
    every write / debug / explain / pick / scaffold /
    test / fix shape stays its), Phone, CarPlay,
    Tracker, Marketplace, Facebook, Messenger, the
    R54 marketplace chain, og_watch, og_monitor, the
    ordering pack, the R127 Website + Browser seat,
    and LAST og_browser — checked exactly, never
    executed from here."""
    try:
        import og_github
        c = og_github._claim_job(message, uid)
        if c:
            return "github", c
    except Exception:
        pass
    try:
        import og_youtube
        c = og_youtube.parse_youtube_intent(message)
        if c:
            return "youtube_connect", c
    except Exception:
        pass
    try:
        import og_youtube
        c = og_youtube._seat_claim_exact(message, uid)
        if c:
            return "youtube_seat", c
    except Exception:
        pass
    try:
        import og_pm
        c = og_pm._claim_exact(message)
        if c:
            return "pm", c
    except Exception:
        pass
    try:
        import og_filing
        c = og_filing._claim_exact(message, uid)
        if c:
            return "filing", c
    except Exception:
        pass
    try:
        import og_crew
        c = og_crew._claim_exact(message)
        if c:
            return "crew", c
    except Exception:
        pass
    try:
        import og_builder
        job, _info = og_builder._claim_builder(message, uid)
        if job:
            return "builder", job
    except Exception:
        pass
    try:
        import og_woodpro
        c = og_woodpro._claim_exact(message, uid)
        if c:
            return "woodpro", c
    except Exception:
        pass
    try:
        import og_mainteng
        c = og_mainteng._claim_exact(message, uid)
        if c:
            return "mainteng", c
    except Exception:
        pass
    try:
        import og_mechanic
        c = og_mechanic._claim_exact(message, uid)
        if c:
            return "mechanic", c
    except Exception:
        pass
    try:
        import og_blueprint
        c, _info = og_blueprint._claim_blueprint(message, uid)
        if c:
            return "blueprint", c
    except Exception:
        pass
    try:
        import og_shopping
        c = og_shopping._claim_exact(message, uid)
        if c:
            return "shopping", c
    except Exception:
        pass
    try:
        import og_buying
        c = og_buying._claim_exact(message, uid)
        if c:
            return "buying", c
    except Exception:
        pass
    try:
        import og_selling
        c = og_selling._claim_exact(message, uid)
        if c:
            return "selling", c
    except Exception:
        pass
    try:
        import og_coding
        c = og_coding._claim_exact(message, uid)
        if c:
            return "coding", c
    except Exception:
        pass
    try:
        import og_phone
        c = og_phone._claim_exact(message, uid)
        if c:
            return "phone", c
    except Exception:
        pass
    try:
        import og_carplay
        c = og_carplay._claim_exact(message, uid)
        if c:
            return "carplay", c
    except Exception:
        pass
    try:
        import og_tracker
        c = og_tracker._claim_exact(message, uid)
        if c:
            return "tracker", c
    except Exception:
        pass
    try:
        import og_marketplace
        c = og_marketplace._claim_exact(message, uid)
        if c:
            return "marketplace", c
    except Exception:
        pass
    try:
        import og_facebook
        c = og_facebook._claim_exact(message, uid)
        if c:
            return "facebook", c
    except Exception:
        pass
    try:
        import og_messenger
        c = og_messenger._claim_exact(message, uid)
        if c:
            return "messenger", c
    except Exception:
        pass
    try:
        import og_mpwatch
        c = og_mpwatch._claim_job(message, uid)
        if c:
            return "mpwatch", c
    except Exception:
        pass
    try:
        import og_watch
        c = og_watch._claim_job(message, uid)
        if c:
            return "watch", c
    except Exception:
        pass
    try:
        import og_monitor
        c = og_monitor.parse_monitor_intent(message)
        if c:
            return "monitor", c
    except Exception:
        pass
    try:
        import og_ordering
        c = og_ordering._claim_job(message, uid)
        if c:
            return "ordering", c
    except Exception:
        pass
    try:
        import og_ordering_flows
        c = og_ordering_flows._claim_job(message, uid)
        if c:
            return "ordering_flows", c
    except Exception:
        pass
    try:
        import og_webbrowser
        c = og_webbrowser._claim_exact(message, uid)
        if c:
            return "webbrowser", c
    except Exception:
        pass
    try:
        import og_browser
        c = og_browser._claim_job(message, uid)
        if c:
            return "browser", c
    except Exception:
        pass
    return None


def _defer_to_others(message: str, uid: str) -> bool:
    return _other_claim(message, uid) is not None


_GODMODE_RE = re.compile(r"\bgod\s*mode\b", re.I)

# The other chairs: a "god mode" ask aimed at a
# seat that is NOT this one (the rest of the God
# Mode tier, or a fenced-off domain: money,
# pricing, connections, accounts) is never eaten
# here — it falls through to whoever owns it, or
# to nobody until that seat lands.
_OTHER_CHAIR_RE = re.compile(
    r"\bgod\s*mode\b[^.?!]*\b(?:tester|test\s+running|"
    r"runset|make\s+money|money|banker|budget|trading|"
    r"organizer|organize|push|validation|validate|"
    r"fix\s*code|fixer|boss|design|designer|writer|"
    r"writing|unity|roster|builder|builds\s+all|"
    r"memory|data|algorithm|research|teacher|safety|"
    r"security|project\s+manager|blueprint|mechanic|"
    r"shopping|selling|social|youtube|movie|"
    r"trending|health|voice|phone|communication|"
    r"pricing|price|subscription|stripe|account|"
    r"password)\b", re.I)

_CODING_SIGNAL_RE = re.compile(
    r"\b(?:code|coding|build|rebuild|refactor|repo|"
    r"codebase|module|function|file|files|system|"
    r"app|feature|script|program|software|seat|"
    r"broken|fix|debug|error|crash|plan|split|"
    r"rescue)\b|og_[a-z0-9_]+", re.I)

_PLAN_RES = [
    re.compile(r"\bplan\s+(?:the\s+)?(?:whole|entire|"
               r"full)\s+(?:build|system|app|rebuild|"
               r"rewrite|overhaul|change|job|thing|"
               r"codebase)\b", re.I),
    re.compile(r"\bplan\s+the\s+(?:whole|entire|full)"
               r"\s+\S+\s+(?:rebuild|rewrite|"
               r"overhaul)\b", re.I),
    re.compile(r"\bplan\s+a\s+(?:multi[-\s]?file|"
               r"whole[-\s]?system|cross[-\s]?file|"
               r"whole[-\s]?build)\b", re.I),
    re.compile(r"\bwhole[-\s]?build\s+plan\b", re.I),
    re.compile(r"\bplan\s+the\s+fix\b", re.I),
]

_RESCUE_RES = [
    re.compile(r"\brescue\s+read\b", re.I),
    re.compile(r"\bread\s+the\s+whole\s+picture\s+on\b",
               re.I),
    re.compile(r"\bnobody\s+can\s+figure\s+out\b",
               re.I),
    re.compile(r"\bno\s+one\s+can\s+(?:figure\s+out|"
               r"see)\s+why\b", re.I),
    re.compile(r"\bcan'?t\s+figure\s+out\s+why\b",
               re.I),
    re.compile(r"\bcannot\s+figure\s+out\s+why\b",
               re.I),
]

_SPLIT_RES = [
    re.compile(r"\bsplit\s+(?:this|the)\s+"
               r"(?:coding\s+)?job\b", re.I),
    re.compile(r"\bbreak\s+this\s+(?:coding\s+)?job"
               r"\s+into\s+pieces\b", re.I),
    re.compile(r"\bwho\s+should\s+take\s+which\s+"
               r"piece\b", re.I),
    re.compile(r"\bsplit\s+it\s+between\s+the\s+"
               r"seats\b", re.I),
]

_FILE_RES = [
    re.compile(r"^\W*(?:please\s+)?(?:file|send|"
               r"submit)\s+(?:it|that|the\s+plan)"
               r"(?:\s+for\s+approval)?\s*[.!?]*$",
               re.I),
    re.compile(r"\bfile\s+the\s+(?:god\s*mode\s+)?"
               r"plan\b", re.I),
]

_STATUS_RE = re.compile(
    r"\bgod\s*mode\s+(?:coding\s+)?(?:status|plans|"
    r"bench)\b|\bmy\s+god\s*mode\s+plans\b", re.I)


def _claim_exact(message: str, uid: str) -> Optional[Dict]:
    raw = str(message or "").strip()
    if not raw or not uid:
        return None
    if _defer_to_others(raw, uid):
        return None
    low = " ".join(raw.lower().split())
    low_c = low.rstrip(" .!?")
    # The bench's own doors first — "file it" is a
    # state change on THIS seat's bench and binds
    # only with a draft (or a filed record) behind
    # it; with an empty bench the words fall
    # through to whoever else owns them.
    for rx in _FILE_RES:
        if rx.search(low_c):
            if _plans(uid):
                return {"op": "gmc_file", "raw": raw}
            return None
    if _STATUS_RE.search(low):
        return {"op": "gmc_status", "raw": raw}
    if _OTHER_CHAIR_RE.search(low):
        return None
    god = bool(_GODMODE_RE.search(low))
    if god and not _CODING_SIGNAL_RE.search(low):
        return None
    # Split before plan: a split ask carries plan
    # words but is the coordination door.
    for rx in _SPLIT_RES:
        if rx.search(low):
            if god or "job" in low:
                return {"op": "gmc_split", "raw": raw}
    # Rescue before plan: a rescue ask about a
    # whole system is a read, not a plan.
    for rx in _RESCUE_RES:
        if rx.search(low):
            return {"op": "gmc_rescue", "raw": raw}
    for rx in _PLAN_RES:
        if rx.search(low):
            return {"op": "gmc_plan", "raw": raw}
    if god and re.search(r"\bplan\b", low):
        # "God mode: plan X" — the explicit
        # invocation of the top seat's plan door,
        # with a coding signal already proven above
        # and every inner claim already deferred.
        return {"op": "gmc_plan", "raw": raw}
    if god and re.search(
            r"^\W*god\s*mode\s*(?:coding)?\s*[.!?]*$",
            low):
        return {"op": "gmc_status", "raw": raw}
    return None


_GARBLE_VOCAB = [
    "god", "mode", "coding", "plan", "whole", "build",
    "rescue", "read", "split", "status", "plans",
    "bench",
]

_GARBLE_OPS = ("gmc_plan", "gmc_rescue", "gmc_status")


def _claim_godmodecoding(message: str, uid: str):
    """(claim, garble_info): the exact claim first;
    on a miss, the Round 75 second chance — og_garble
    repairs the TRIGGER words only. File / split ops
    are REFUSED on the garble path: filing is a state
    change into the approval pipeline and a split
    parks the coordination record a filing acts on —
    verbatim or nothing."""
    claim = _claim_exact(message, uid)
    if claim:
        return claim, None
    try:
        repaired, info = og_garble.repair_info(
            str(message or ""), _GARBLE_VOCAB)
        if info:
            claim = _claim_exact(repaired, uid)
            if claim and claim.get("op") in _GARBLE_OPS:
                return claim, info
    except Exception:
        pass
    return None, None


# --- The seam ------------------------------------------------------------------

_pending = {"claim": None, "garble": None}


def install_godmodecoding_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search OUTSIDE the
    landed helper chain (installed last, from the
    og_fixqueue chain — after the outermost landed
    helper seam, before the Round 275 Activity
    observer) so god-mode coding escalation asks
    claim here and everything else delegates back
    untouched. Persona files never touched."""
    if getattr(agent_instance,
               "_og_godmodecoding_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent",
                          None)
    prev_search = getattr(agent_instance, "web_search",
                          None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["claim"] = None
        _pending["garble"] = None
        try:
            uid = get_uid()
            if uid and godmodecoding_enabled():
                claim, info = _claim_godmodecoding(
                    str(message), uid)
                if claim:
                    _pending["claim"] = claim
                    _pending["garble"] = info
                    if isinstance(intent, dict):
                        intent[
                            "needs_code_generation"] = False
                        if not intent.get("needs_web_search"):
                            intent["needs_web_search"] = True
                            intent["search_query"] = \
                                str(message).strip()
        except Exception as e:
            logger.warning(
                f"GodModeCoding trigger check failed: {e}")
            _pending["claim"] = None
            _pending["garble"] = None
        return intent

    def search_wrapped(query, num_results=5):
        claim = _pending.get("claim")
        garble = _pending.get("garble")
        _pending["claim"] = None
        _pending["garble"] = None
        uid = ""
        try:
            uid = get_uid()
        except Exception:
            uid = ""
        results = None
        if claim and uid:
            try:
                results = godmodecoding_results(claim, uid)
            except Exception as e:
                logger.warning(f"GodModeCoding op failed: {e}")
                results = _result(
                    "God Mode Coding — glitch",
                    "That god-mode ask glitched on my "
                    "end — nothing was planned, filed, "
                    "or changed. Say it again and I'll "
                    "retry.")
        if results is None:
            results = prev_search(query, num_results)
        if garble and results:
            results = og_garble.annotate(
                results, garble, prepend=True)
        return results

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_godmodecoding_installed = True


# --- Routes ----------------------------------------------------------------------


def _route_uid(request) -> str:
    """Round 95: the caller's ONE identity — the live
    session's account uid when signed in, else the
    presented cookie (og_accounts.session_uid_or)."""
    uid = ""
    try:
        uid = request.cookies.get("ogai_uid", "")
    except Exception:
        uid = ""
    try:
        import og_accounts as _acct
        return _acct.session_uid_or(request.cookies, uid)
    except Exception:
        return uid


def register_godmodecoding_routes(app):
    """GET /godmodecoding/status — the caller's own
    god-mode bench: plans parked (drafts vs filed),
    rescue reads held, and the last plan's summary —
    never another account's."""

    @app.get("/godmodecoding/status")
    async def godmodecoding_status_route(request: Request):
        uid = _route_uid(request)
        view = godmodecoding_status_for(uid) if uid else {
            "plans": 0, "drafts": 0, "filed": 0,
            "reads": 0, "last": None}
        return {"ok": True,
                "enabled": godmodecoding_enabled(),
                **view}
