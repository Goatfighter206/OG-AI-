"""Round 279: GOD MODE AI TESTER (Brent's words,
2026-10-13 13:39 PDT: "God mode Ai tester
helper" — read back as the top testing seat of
the God Mode tier and APPROVED 13:41 as part of
the nine-seat God Mode batch; specced in
round-279/SPEC.md.) The seat that DECIDES the
testing: what must be tested, how deep, in what
order — and the seat that reads run results and
calls every failure what it is, by name.

WHAT ALREADY EXISTED (ridden, never forked):

  - RUNNING IS THE MACHINERY'S, NOT THIS
    SEAT'S. OG's batteries and drills run
    through the machinery that already owns
    them — the self-check census, the Round 60
    full test battery, the sandbox checker —
    driven by the seats that own running. This
    seat holds NO runner of its own: no battery
    handle, no check handle, no execution
    surface at all (source-pinned). It never
    fires a run, heavy or light, asked or
    unasked.
  - THE CODING SEAT OWNS CODE (Round 119,
    og_coding): write-it, pasted-error debug,
    explain, pick, scaffold, and test-it /
    fix-it on the caller's own pasted code.
    This seat checks og_coding._claim_exact
    EXACTLY in its deferral chain — checked,
    never executed — and steps aside for every
    shape it claims. A pasted snippet with
    "test it" on it is the Coding seat's bench,
    not this seat's verdict.
  - REPO-SHAPED ASKS ROUTE TO ROUND 60 EXACTLY
    (og_github — its pendings, its clarifying
    questions, its read / write / checks asks,
    the PR-only door, and the battery machinery
    itself). This seat checks og_github FIRST
    in its deferral and holds no repo machinery
    of its own.
  - THE RUNSET PICTURE IS THE RUNSET SEAT'S
    (Master AI Runset, R277 — unbuilt when this
    seat was prepped): the standing baseline,
    the run-to-run deltas, the history. This
    seat DEFERS to it defensively wherever the
    module answers, and its own guard never
    eats a baseline / delta / overseer ask —
    that chair is the Runset seat's when it
    lands. This seat's verdicts are about
    NAMED FAILURES and TEST STRATEGY, not
    about moving any baseline: a baseline
    change is a landing event recorded in a
    round's notes, never this seat's call.
  - THE GOD MODE CODING SEAT (R278) OWNS
    WHOLE-BUILD PLANS AND CODE ESCALATIONS.
    This seat defers to it defensively (its
    claim is checked wherever the module
    answers) and its chair guard fences plan /
    rescue / split shapes aimed there: a plan
    for the code is Coding's door; a plan for
    the TESTING is this seat's.
  - THE STOPPED TEST-RUNNING LANDING IS
    ANOTHER SEAT'S CHAIR, FULL STOP. It was
    stopped mid-flight by Brent's 2026-10-13
    stop order and foldered for later; as far
    as this seat is concerned it does not
    exist: this seat builds nothing on it,
    calls nothing in it, and its run-the-suite
    shapes are NOT claimed here — running asks
    fall through to whoever owns them, exactly
    as they did before it was specced. When
    and if Brent revives it, it slots BELOW
    this seat by the SPEC's own order (the
    SPEC pins this seat's claims after the
    test-running claims where they overlap),
    and nothing here needs to change for that.

WHAT IT OWNS — the testing judgment nobody
claimed:

  - TEST STRATEGY. "What should I test before
    I ship the login change", "test plan for
    the wallet seat", "what tests does the
    Stripe change need": the plan for the
    TESTING — what must be tested, how deep,
    in what order, sized to the risk the ask
    names. Composed deterministically off the
    ask: the named target's own round suite
    first, the seam neighbors second, the full
    battery against the standing baseline
    third — and, when the risk is money /
    sign-in / persona deep, the live probes
    and the pin checks last. Every step says
    what a pass looks like, and the order is
    law inside the plan: a later step never
    excuses an earlier red. The strategy PARKS
    on the caller's bench.
  - VERDICTS BY NAME. Given a run's results —
    the names, pasted or listed — every name
    gets its verdict: REAL BREAK (matches no
    known family and the run asserts it
    failed), KNOWN FAMILY (the r29/r38 Steel
    live-profile race, the r61 and r83 timing
    families — labeled as the families they
    are, never dressed as new and never
    hidden), TIMING-FAMILY SHAPED (suspected,
    and the answer says what would prove it),
    or NOT ENOUGH PROOF (a name with no result
    behind it gets NO verdict — this seat
    never classifies a name it has no evidence
    for). The verdict parks with its counts.
    An all-green read is reported as exactly
    that — no failures named — with the
    battery's completeness stated: a partial
    battery's green is a partial green.
  - ESCALATION READS. "My tests keep failing",
    "the same names fail every run": the whole
    picture — the names in the ask plus the
    caller's own parked verdict history — read
    together, and the ONE fault the picture
    honestly supports is named: one family
    wearing many names (an environment fault,
    not a pile of code breaks), one seam every
    break shares, or breaks that share nothing
    but a moment (the hunt redirected to the
    change that touched them all, named as a
    hunt, never as a verdict). When the
    picture supports nothing, the seat says
    so and names what to bring. The read
    parks.
  - THE BENCH. "God mode tester status" reads
    the bench back: strategies parked,
    verdicts given (by class), escalation
    reads held, the last of each — re-served
    with no new work. GET /godmodetester/
    status serves the same picture.

THE FENCES — binding:

  - GOD MODE IS A SEAT, NOT A BYPASS.
    IT FIXES NOTHING ITSELF: this seat
    reports and directs testing — no code
    repair, no suite edits, no fix machinery
    of any kind (source-pinned). A real break
    it names goes to the fixing seats through
    the approval door, exactly like every
    other seat's findings.
  - IT NEVER STAMPS A BUILD "100% SAFE" —
    not in a verdict, not in a strategy, not
    anywhere. Verdicts come with the names
    and the evidence, or they don't get
    given; an all-green run is "no failures
    named in the run described", never a
    safety stamp. The stamp ask itself is
    claimed and REFUSED, by name.
  - IT NEVER CLAIMS A RUN IT DID NOT READ.
    There is no runner here, so there is no
    "I ran it" — compile-only is stated as
    compile-only by the seats that run, and
    this seat repeats a result only as the
    result the caller brought, labeled with
    where it came from.
  - IT NEVER MOVES A BASELINE and never
    re-runs anything: baselines move through
    landing notes; re-runs belong to the
    machinery's owners.
  - NOTHING OUTSIDE TESTING: pricing, money,
    connections, and account actions stay
    behind their own fences, and the other
    god-mode seats' chairs (Coding, Runset,
    Money, Organizer, Push, Validation, Fix
    Code, Fixer, Design, Writer, Unity, Boss,
    Roster, Builder) are guarded by name
    until those seats land.
  - Per-account isolation like every seat:
    one account's bench, verdicts, and reads
    are never shown to another. Persona file
    never touched; app.py NOT touched —
    module + og_library/og_fixqueue seams
    only. No new loop, no new timer (R72).
    Switch OG_GODMODETESTER_ENABLED (unset =
    ON; off = the seat doesn't exist).
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
    DB_OPTIONS as _DB_OPTS, StoreError as _StoreError, apply_bounds as _apply_bounds

try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # pragma: no cover - local fallback
    psycopg = None
    _Jsonb = None

_TAG = "OG GOD MODE TESTER"

GODMODETESTER_STORE_FILE = os.getenv(
    "OG_GODMODETESTER_STORE", "godmodetester_store.json")

_gmt_lock = threading.RLock()


# --- The seat switch ---------------------------------------------------------


def godmodetester_enabled() -> bool:
    """OG_GODMODETESTER_ENABLED is the seat's kill
    switch: an explicit off (0/false/no/off) = the
    seat doesn't exist. Unset = ON (the crew
    pattern)."""
    v = os.getenv(
        "OG_GODMODETESTER_ENABLED", "").strip().lower()
    return v not in ("0", "false", "no", "off")


# --- The bench store (Round 18 pattern: Postgres og_godmodetester_data when
# --- OG_MEMORY_DB_URL is set, the JSON file otherwise) -----------------------


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=_DB_CT,
                           options=_DB_OPTS)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_godmodetester_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(GODMODETESTER_STORE_FILE):
        try:
            with open(GODMODETESTER_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"GodModeTester store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(GODMODETESTER_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"GodModeTester store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_godmodetester_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"GodModeTester DB load failed: {e}")
            raise _StoreError(
                "godmodetester store unavailable (Round 99)") from e
    with _gmt_lock:
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
                        "INSERT INTO og_godmodetester_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"GodModeTester DB save failed: {e}")
            raise _StoreError(
                "godmodetester store unavailable (Round 99)") from e
    with _gmt_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


def _strategies(uid: str) -> List[Dict]:
    got = _blob(uid, "strategies", [])
    return got if isinstance(got, list) else []


def _park_strategy(uid: str, rec: Dict) -> Dict:
    rows = _strategies(uid)
    rows.append(rec)
    _put_blob(uid, "strategies", rows[-20:])
    return rec


def _verdicts(uid: str) -> List[Dict]:
    got = _blob(uid, "verdicts", [])
    return got if isinstance(got, list) else []


def _park_verdict(uid: str, rec: Dict) -> Dict:
    rows = _verdicts(uid)
    rows.append(rec)
    _put_blob(uid, "verdicts", rows[-20:])
    return rec


def _reads(uid: str) -> List[Dict]:
    got = _blob(uid, "reads", [])
    return got if isinstance(got, list) else []


def _park_read(uid: str, rec: Dict):
    rows = _reads(uid)
    rows.append(rec)
    _put_blob(uid, "reads", rows[-20:])


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


# --- The strategy composer (deterministic — the plan for the TESTING) ---------


_FILE_RE = re.compile(r"\b(og_[a-z0-9_]+)(?:\.py)?\b")

_DEEP_RE = re.compile(
    r"\b(?:money|payment|stripe|wallet|crypto|bank|"
    r"password|auth|login|log[-\s]?in|sign[-\s]?in|"
    r"persona|voice|pricing|price|tier|account|"
    r"delete|billing|checkout|trade|trading)\b", re.I)

_LIGHT_RE = re.compile(
    r"\b(?:copy|wording|label|colou?r|typo|comment|"
    r"text|spacing|icon)\b", re.I)


def _named_targets(text: str) -> List[str]:
    seen: List[str] = []
    for m in _FILE_RE.finditer(str(text or "").lower()):
        stem = m.group(1)
        if stem not in seen:
            seen.append(stem)
    return seen[:4]


def _target_phrase(raw: str) -> str:
    """The thing being tested, as the user named it:
    the og_* files first; else the words after the
    strategy trigger, cleaned of trigger wording."""
    files = _named_targets(raw)
    if files:
        return ", ".join(f"{f}.py" for f in files)
    t = " ".join(str(raw or "").split()).strip()
    t = re.sub(r"^\W*(?:hey\s+)?god\s*mode\b\s*"
               r"(?:tester\s*)?[:\-—]?\s*", "", t, flags=re.I)
    t = re.sub(r"^(?:please\s+)?(?:what\s+should\s+i\s+"
               r"test|what\s+tests?\s+(?:do\s+i\s+need|"
               r"does\s+it\s+need|should)|what\s+needs\s+"
               r"testing|how\s+do\s+i\s+test|test\s+"
               r"(?:plan|strategy)\s+for|test)\b\s*",
               "", t, flags=re.I)
    t = re.sub(r"^(?:for|on|before|of)\s+", "", t,
               flags=re.I)
    t = t.strip(" .?!")
    return _clean(t, 160) or "the change as stated"


def _depth_of(raw: str) -> str:
    if _DEEP_RE.search(str(raw or "")):
        return "DEEP"
    if _LIGHT_RE.search(str(raw or "")):
        return "LIGHT"
    return "STANDARD"


def _strategy_steps(target: str, depth: str) -> List[Dict]:
    steps: List[Dict] = [{
        "name": "the target's own round suite",
        "what": f"Run {target}'s own round suite first, "
                f"every check BY NAME — a change is "
                f"untested until its own suite is green "
                f"on the names that cover it",
        "pass": "the suite's own verdict: every named "
                "check green, or the reds named and "
                "owned",
    }, {
        "name": "the seam neighbors",
        "what": "The seats and files that share the "
                "target's seams — the chain on both "
                "sides of it — get their suites read "
                "next; seam breaks hide in the "
                "neighbors, not in the target",
        "pass": "no neighbor red that the target's "
                "change can explain",
    }]
    if depth == "LIGHT":
        steps.append({
            "name": "the battery, if the surface is "
                    "shared",
            "what": "A wording/label surface that "
                    "nothing else renders can stop "
                    "here; if ANY shared file moved, "
                    "the full battery against the "
                    "standing baseline runs — names "
                    "compared, deltas by name",
            "pass": "zero genuinely new names, or "
                    "every new name classified before "
                    "it counts",
        })
    else:
        steps.append({
            "name": "the full battery against the "
                    "standing baseline",
            "what": "The whole runset, names compared "
                    "against the standing baseline — "
                    "what is NEW, what DISAPPEARED, "
                    "what is UNCHANGED, by name; a "
                    "genuinely new failure is a "
                    "regression until classified",
            "pass": "zero genuinely new names, or "
                    "every new name carrying its "
                    "verdict (real break / known "
                    "family) before anything ships",
        })
    if depth == "DEEP":
        steps.append({
            "name": "live probes + the pins",
            "what": "Money / sign-in / persona depth "
                    "does not ship on local green: "
                    "probe BOTH live hosts, walk the "
                    "signed-in path the change touches, "
                    "and check the persona pin byte for "
                    "byte — production is the last test "
                    "and the persona is a pin, never a "
                    "variable",
            "pass": "both hosts answering clean and "
                    "the pin exact; anything less is "
                    "a named red, not a ship",
        })
    return steps


def _do_strategy(uid: str, claim: Dict) -> List[Dict]:
    raw = str(claim.get("raw") or "")
    target = _target_phrase(raw)
    depth = _depth_of(raw)
    steps = _strategy_steps(target, depth)
    rec = {
        "id": hashlib.sha1(
            f"{uid}|strategy|{target}|{time.time()}".encode(
                "utf-8")).hexdigest()[:8],
        "target": target,
        "depth": depth,
        "steps": len(steps),
        "at": time.time(),
    }
    _park_strategy(uid, rec)
    lines = [f"THE TEST STRATEGY — {target}",
             f"Depth: {depth} (sized to the risk the "
             f"ask names). Parked on your bench at "
             f"{_asof()} (strategy #{rec['id']}). In "
             f"this order — a later step never "
             f"excuses an earlier red:"]
    for i, step in enumerate(steps, 1):
        lines.append(f"{i}. [{step['name']}] "
                     f"{step['what']}.")
        lines.append(f"   a pass looks like: "
                     f"{step['pass']}")
    lines.append(
        "Running the steps belongs to the test "
        "machinery's owners — this seat decides the "
        "testing and reads the results by name; it "
        "fires nothing itself and fixes nothing "
        "itself.")
    return _result("God Mode Tester — the test strategy",
                   "\n".join(lines))


# --- The verdict classifier (deterministic — every name gets its evidence) ----


_FAMILY_RULES = [
    (re.compile(r"(?:^|[_\-. ])r(?:29|38)(?:[_\-. ]|$)|"
                r"steel|live[_\- ]profile", re.I),
     "KNOWN FAMILY — the r29/r38 Steel live-profile race",
     "a timing race on the live profile: it passes "
     "and fails on the clock and the load, not on "
     "the code — the proof is a re-run under the "
     "machinery's owner, never a code fix aimed at "
     "the name"),
    (re.compile(r"(?:^|[_\-. ])r61(?:[_\-. ]|$)", re.I),
     "KNOWN FAMILY — the r61 timing family",
     "one of the standing timing families on the "
     "baseline record: labeled as the family it is, "
     "never dressed as new, never hidden"),
    (re.compile(r"(?:^|[_\-. ])r83(?:[_\-. ]|$)", re.I),
     "KNOWN FAMILY — the r83 timing family",
     "one of the standing timing families on the "
     "baseline record: labeled as the family it is, "
     "never dressed as new, never hidden"),
    (re.compile(r"timeout|timed[_\- ]out|flak|race|"
                r"intermittent|order[_\- ]dependent",
                re.I),
     "TIMING-FAMILY SHAPED — suspected, not proven",
     "the name smells like the clock, not the code; "
     "what proves it is the same name going green on "
     "a clean re-run with nothing changed — until "
     "then it is a suspicion with a label, not a "
     "verdict of innocence"),
]

_NAME_RE = re.compile(
    r"\b(?:test|check|suite|r\d+)[A-Za-z0-9_.\-]*\b")

_STOPWORDS = {
    "tests", "test", "the", "these", "those", "my",
    "failures", "failure", "failed", "failing",
    "results", "result", "verdict", "verdicts",
    "run", "suite", "suites", "check", "checks",
    "read", "names", "name", "all", "and", "for",
    "keep", "every",
}


def _failure_names(raw: str) -> List[str]:
    """The check names the caller actually named —
    tokens shaped like check names (test_… /
    check_… / rNN…), deduplicated, glue words
    dropped. A name is evidence; prose is not."""
    seen: List[str] = []
    for m in _NAME_RE.finditer(str(raw or "").lower()):
        tok = m.group(0).strip("._-")
        if not tok or tok in _STOPWORDS:
            continue
        if tok not in seen:
            seen.append(tok)
    return seen[:12]


_FAIL_ASSERT_RE = re.compile(
    r"\b(?:fail|failed|failing|fails|red|error|"
    r"errors|broke|broken)\b", re.I)


def _classify(name: str, asserted: bool):
    for rx, label, why in _FAMILY_RULES:
        if rx.search(name):
            return label, why
    if asserted:
        return ("REAL BREAK",
                "matches no known family and the run "
                "asserts it failed — this one is real "
                "until a re-run or a read proves "
                "otherwise, and it goes to the fixing "
                "seats through the approval door")
    return ("NOT ENOUGH PROOF",
            "a name with no result behind it gets no "
            "verdict — bring the run's own words for "
            "this name (failed / passed, and the "
            "line it printed) and it gets classified")


def _verdict_lines(names: List[str], asserted: bool):
    lines, counts = [], {"REAL BREAK": 0,
                         "KNOWN FAMILY": 0,
                         "TIMING-FAMILY SHAPED": 0,
                         "NOT ENOUGH PROOF": 0}
    rows = []
    for name in names:
        label, why = _classify(name, asserted)
        key = ("KNOWN FAMILY" if label.startswith(
            "KNOWN FAMILY") else
               "TIMING-FAMILY SHAPED" if label.startswith(
                   "TIMING") else label)
        counts[key] = counts.get(key, 0) + 1
        rows.append({"name": name, "class": key,
                     "label": label})
        lines.append(f"• {name} — {label}: {why}")
    return lines, counts, rows


_FENCE_LINE = (
    "The line that never moves: verdicts carry "
    "names and evidence or they don't get given — "
    "and no run, from this seat, ever gets stamped "
    "\"100% safe\". A green run is \"no failures "
    "named in the run described\", nothing more.")


def _do_verdict(uid: str, claim: Dict) -> List[Dict]:
    raw = str(claim.get("raw") or "")
    if claim.get("stamp"):
        return _result(
            "God Mode Tester — the stamp, refused",
            "No. This seat never stamps a build "
            "\"100% safe\" — not yours, not anyone's. "
            "What it gives instead is a verdict WITH "
            "the names and the evidence: which checks "
            "ran, which failed, and what each failure "
            "is (real break, known family, or not "
            "enough proof). Bring the run's names and "
            "you get the real verdict; the stamp "
            "doesn't exist here.")
    names = _failure_names(raw)
    asserted = bool(_FAIL_ASSERT_RE.search(raw))
    allpass = bool(re.search(
        r"\b(?:all|everything|every)\b[^.?!]*"
        r"\bpass(?:ed|es)?\b|\bno\s+failures\b|"
        r"\b0\s+failed\b|\bgreen\s+run\b", raw, re.I))
    if allpass and not (asserted and names):
        partial = bool(re.search(
            r"\b(?:just|only|partial|one\s+suite|"
            r"single\s+suite)\b", raw, re.I))
        rec = {"id": hashlib.sha1(
            f"{uid}|verdict|allpass|{time.time()}".encode(
                "utf-8")).hexdigest()[:8],
               "names": [], "counts": {},
               "kind": "all-pass", "at": time.time()}
        _park_verdict(uid, rec)
        comp = ("PARTIAL battery — a partial green is "
                "a partial green: it covers the suites "
                "named, nothing beyond them."
                if partial else
                "As described, that is the run you "
                "brought — this seat repeats it as YOUR "
                "report, labeled as such; it ran "
                "nothing itself.")
        return _result(
            "God Mode Tester — verdicts by name",
            "NO FAILURES NAMED in the run you "
            "describe. That is a statement about the "
            "names in front of me, not a stamp: it "
            "proves the checks that ran were green "
            "on that run. " + comp + "\n" + _FENCE_LINE)
    if not names:
        return _result(
            "God Mode Tester — verdicts by name",
            "A verdict needs the NAMES. Tell me which "
            "checks failed — paste the run's failure "
            "lines or list the names — and every one "
            "gets its verdict: real break, known "
            "family, timing-shaped, or not enough "
            "proof. A bare count (\"3 failed\") "
            "classifies nothing, and this seat never "
            "guesses at names it wasn't given.")
    lines, counts, rows = _verdict_lines(names, asserted)
    rec = {"id": hashlib.sha1(
        f"{uid}|verdict|{time.time()}".encode(
            "utf-8")).hexdigest()[:8],
           "names": rows, "counts": counts,
           "kind": "names", "at": time.time()}
    _park_verdict(uid, rec)
    head = (f"THE VERDICTS — BY NAME (as of {_asof()}, "
            f"verdict #{rec['id']}):")
    tally = (f"Counts: {counts.get('REAL BREAK', 0)} "
             f"real break(s), "
             f"{counts.get('KNOWN FAMILY', 0)} known "
             f"family, "
             f"{counts.get('TIMING-FAMILY SHAPED', 0)} "
             f"timing-shaped, "
             f"{counts.get('NOT ENOUGH PROOF', 0)} not "
             f"enough proof.")
    return _result("God Mode Tester — verdicts by name",
                   "\n".join([head] + lines
                             + [tally, _FENCE_LINE]))


# --- The escalation read (the whole picture, one named fault) -----------------


def _stem_of(name: str) -> str:
    parts = [p for p in re.split(r"[_\-.]", name) if p]
    if len(parts) >= 2:
        return "_".join(parts[:2])
    return parts[0] if parts else name


def _do_escalate(uid: str, claim: Dict) -> List[Dict]:
    raw = str(claim.get("raw") or "")
    names = _failure_names(raw)
    asserted = True  # an escalation ask asserts failure
    hist = _verdicts(uid)
    hist_names: List[str] = []
    for rec in hist:
        for row in rec.get("names") or []:
            n = str(row.get("name") or "")
            if n and n not in hist_names:
                hist_names.append(n)
    pool = names or hist_names
    fault = ""
    detail = ""
    if pool:
        _l, _c, rows = _verdict_lines(pool, asserted)
        fam = [r for r in rows if r["class"]
               in ("KNOWN FAMILY",
                   "TIMING-FAMILY SHAPED")]
        if len(pool) >= 2 and len(fam) == len(pool):
            fault = ("ONE FAMILY WEARING MANY NAMES")
            detail = (
                "every named failure sits in the "
                "timing/family classes — that is an "
                "ENVIRONMENT fault (the clock, the "
                "load, the live profile), not a pile "
                "of separate code breaks. The fix lane "
                "is the family's owner and a clean "
                "re-run, and aiming code fixes at the "
                "names would fix nothing.")
        elif len(pool) >= 2:
            stems: Dict[str, int] = {}
            for r in rows:
                s = _stem_of(r["name"])
                stems[s] = stems.get(s, 0) + 1
            top = max(stems.items(), key=lambda kv: kv[1])
            if top[1] >= 2:
                fault = (f"ONE SEAM — {top[0]}")
                detail = (
                    f"{top[1]} of the named breaks "
                    f"share the {top[0]} stem: the "
                    f"fault hunt starts at that seam — "
                    f"the shared surface those checks "
                    f"stand on — not at any one name. "
                    f"The loudest name is a symptom; "
                    f"the seam is the picture.")
            else:
                fault = "BREAKS THAT SHARE A MOMENT"
                detail = (
                    "the named breaks share no family "
                    "and no seam — what they share is "
                    "WHEN they started. The hunt goes "
                    "to the last change that touched "
                    "all of their areas; that is a "
                    "HUNT, named as one, not a verdict "
                    "on the change.")
        else:
            fault = "ONE NAME, READ PLAIN"
            detail = (
                "a single recurring name is its own "
                "picture: read its class above the "
                "bench's verdicts — family means "
                "environment, real break means the "
                "fixing seats through the door.")
    if not fault:
        return _result(
            "God Mode Tester — the escalation read",
            "NOT ENOUGH PICTURE — and this seat "
            "doesn't fake one. An escalation read "
            "needs the names: the checks that keep "
            "failing, from the last run or two (paste "
            "the failure lines, or list the names). "
            "Your bench holds no verdict names to "
            "read from either. Bring the names and "
            "the whole picture gets read: one family, "
            "one seam, or one moment — named, with "
            "the evidence.")
    rec = {"id": hashlib.sha1(
        f"{uid}|read|{time.time()}".encode(
            "utf-8")).hexdigest()[:8],
           "fault": fault, "names": pool[:12],
           "at": time.time()}
    _park_read(uid, rec)
    src = ("the names in your ask" if names else
           "your bench's parked verdicts")
    lines = [f"THE ESCALATION READ — {fault}",
             f"Read off {src} "
             f"({len(pool)} name(s) in the picture), "
             f"parked at {_asof()} (read "
             f"#{rec['id']}).",
             detail,
             "This read names where the fault hunt "
             "starts and why — it fixes nothing "
             "itself; the fixing seats take it from "
             "here through the approval door."]
    return _result("God Mode Tester — the escalation read",
                   "\n".join(lines))


# --- Status --------------------------------------------------------------------


def godmodetester_status_for(uid: str) -> Dict:
    strategies = _strategies(uid)
    verdicts = _verdicts(uid)
    reads = _reads(uid)
    by_class: Dict[str, int] = {}
    for rec in verdicts:
        for k, v in (rec.get("counts") or {}).items():
            by_class[k] = by_class.get(k, 0) + int(v or 0)
    last_v = verdicts[-1] if verdicts else None
    last_s = strategies[-1] if strategies else None
    return {
        "strategies": len(strategies),
        "verdicts": len(verdicts),
        "reads": len(reads),
        "by_class": by_class,
        "last_strategy": ({
            "id": last_s.get("id"),
            "target": last_s.get("target"),
            "depth": last_s.get("depth"),
        } if last_s else None),
        "last_verdict": ({
            "id": last_v.get("id"),
            "kind": last_v.get("kind"),
            "counts": last_v.get("counts"),
        } if last_v else None),
    }


def _do_status(uid: str, claim: Dict) -> List[Dict]:
    view = godmodetester_status_for(uid)
    if not view["strategies"] and not view["verdicts"] \
            and not view["reads"]:
        return _result(
            "God Mode Tester — bench status",
            "Your God Mode Tester bench is EMPTY: no "
            "strategies parked, no verdicts given, no "
            "escalation reads held. Ask what to test, "
            "bring a run's failed names, or say the "
            "tests keep failing — and the bench "
            "starts.")
    lines = [
        f"YOUR GOD MODE TESTER BENCH (as of "
        f"{_asof()}):",
        f"• strategies parked: {view['strategies']}",
        f"• verdicts given: {view['verdicts']}",
        f"• escalation reads held: {view['reads']}",
    ]
    if view["by_class"]:
        tally = ", ".join(
            f"{k.lower()}: {v}"
            for k, v in sorted(view["by_class"].items())
            if v)
        if tally:
            lines.append(f"• verdicts by class: {tally}")
    last_s = view.get("last_strategy")
    if last_s:
        lines.append(
            f"• last strategy: #{last_s.get('id')} — "
            f"{_clean(last_s.get('target'), 120)} "
            f"({last_s.get('depth')})")
    last_v = view.get("last_verdict")
    if last_v:
        lines.append(
            f"• last verdict: #{last_v.get('id')} "
            f"({last_v.get('kind')})")
    lines.append(
        "The bench re-serves from here with no new "
        "work — and nothing on it was ever stamped "
        "\"100% safe\".")
    return _result("God Mode Tester — bench status",
                   "\n".join(lines))


def godmodetester_results(claim: Dict,
                          uid: str) -> List[Dict]:
    op = (claim or {}).get("op")
    if op == "gmt_strategy":
        return _do_strategy(uid, claim)
    if op == "gmt_verdict":
        return _do_verdict(uid, claim)
    if op == "gmt_escalate":
        return _do_escalate(uid, claim)
    if op == "gmt_status":
        return _do_status(uid, claim)
    return _result(
        "God Mode Tester",
        "That god-mode testing ask didn't land — "
        "say it again and the top testing seat "
        "takes it.")


# --- Claims ---------------------------------------------------------------------
#
# The claims order, pinned (SPEC, Round 279): this seat installs
# AFTER the landed helper chain (its seam block sits after the
# outermost landed helper block in og_fixqueue and before the
# Round 275 Activity block) — its detect wraps outermost among
# the helper seats — and its _claim_exact DEFERS EXACTLY FIRST:
# og_github's own claim, then the inner owners in house order,
# with the testing-adjacent chairs checked exactly among them:
# og_coding (past­ed-code test/fix shapes stay the Coding
# seat's) and, defensively, the God Mode Coding seat (R278 —
# whole-build plans and code escalations stay its) and the
# Master Runset seat (R277 — baselines and deltas stay its),
# wherever those modules answer. The stopped test-running
# seat is NOT in this chain and NOT referenced: its
# run-the-suite shapes were never this seat's to claim, and
# running asks fall through untouched. Where the chains
# overlap, the owners below answer first; god mode claims
# only the testing judgment: strategy, verdicts by name,
# escalation reads, the stamp refusal, and its own bench.


def _other_claim(message: str, uid: str):
    """(source, claim) of the first inner owner that
    claims this message exactly, else None. og_github
    FIRST — its pendings, clarifying questions, and
    every repo-shaped ask are the R60 path's own.
    Then the YouTube CONNECT'S OWN PARSE, the R126
    YOUTUBE SEAT exactly, PM, Filer, crew, Builder,
    WoodPro, MaintEng, Mechanic, Blueprint, Shopping,
    Buying, Selling, CODING (every write / debug /
    explain / pick / scaffold / bench test-it /
    fix-it shape stays its), GOD MODE CODING
    (defensive — R278's whole-build plans, rescue
    reads, and splits stay its wherever it answers),
    Phone, CarPlay, Tracker, Marketplace, Facebook,
    Messenger, the R54 marketplace chain, og_watch,
    og_monitor, the ordering pack, the R127 Website +
    Browser seat, MASTER RUNSET (defensive — R277's
    baselines / deltas / history stay its wherever
    it answers), and LAST og_browser — checked
    exactly, never executed from here."""
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
        import og_godmodecoding
        c = og_godmodecoding._claim_exact(message, uid)
        if c:
            return "godmodecoding", c
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
        import og_masterrunset
        c = None
        for _fn in ("_claim_exact", "_claim_masterrunset"):
            _f = getattr(og_masterrunset, _fn, None)
            if _f is not None:
                c = _f(message, uid)
                if c:
                    break
        if c:
            return "masterrunset", c
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

# The other chairs: a "god mode" ask that NAMES
# another chair (the rest of the God Mode tier,
# the Runset seat's baseline picture, or a fenced
# domain) is never eaten here — the chair named
# right after the invocation, plus the phrase
# chairs. It falls through to whoever owns it,
# or to nobody until that seat lands.
_OTHER_CHAIR_RE = re.compile(
    r"\bgod\s*mode\s+(?:ai\s+)?(?:coding|code|"
    r"runset|money|maker|organizer|organize|push|"
    r"validation|validate|fixer|boss|design|"
    r"designer|writer|writing|unity|roster|eye|"
    r"memory|data|algorithm|research|teacher|"
    r"safety|security|blueprint|mechanic|"
    r"shopping|selling|social|movie|trending|"
    r"health|voice|phone|communication|banker|"
    r"budget|trading|builder)\b", re.I)

_OTHER_CHAIR_PHRASE_RE = re.compile(
    r"\bgod\s*mode\b[^.?!]*\b(?:baseline|deltas?|"
    r"overseer|boss\s+of|builds\s+all|whole[-\s]?"
    r"build\s+plan|rescue\s+read|pricing|"
    r"subscription|stripe|password)\b", re.I)

_TEST_SIGNAL_RE = re.compile(
    r"\b(?:test|tests|testing|suite|suites|fail|"
    r"fails|failed|failing|failure|verdict|"
    r"verdicts|battery|flaky|safe|pass|passed)\b",
    re.I)

_STATUS_RES = [
    re.compile(r"\bgod\s*mode\s+(?:ai\s+)?tester\b"
               r"[^.?!]*\b(?:status|bench)\b", re.I),
    re.compile(r"\bmy\s+test(?:ing)?\s+(?:bench|"
               r"verdicts|strategies)\b", re.I),
]

_STAMP_RES = [
    re.compile(r"\b100\s*%\s*safe\b", re.I),
    re.compile(r"\bstamp(?:ed|ing)?\b[^.?!]*"
               r"\bsafe\b", re.I),
    re.compile(r"\bguarantee(?:d|s)?\b[^.?!]*"
               r"\bsafe\b", re.I),
]

_ESCALATE_RES = [
    re.compile(r"\bkeep(?:s|ing)?\s+(?:failing|"
               r"breaking)\b", re.I),
    re.compile(r"\bsame\s+(?:tests?|names?|"
               r"failures?|checks?)\b[^.?!]*"
               r"\bevery\s+(?:run|time)\b", re.I),
    re.compile(r"\bfail(?:s|ing|ures?)?\s+every\s+"
               r"(?:run|time)\b", re.I),
    re.compile(r"\bfailure\s+pattern\b", re.I),
    re.compile(r"\bwon'?t\s+stay\s+green\b", re.I),
    re.compile(r"\bflaky\s+tests?\b", re.I),
]

_VERDICT_RES = [
    re.compile(r"\bverdicts?\s+on\b", re.I),
    re.compile(r"\bread\s+my\s+test\s+results\b",
               re.I),
    re.compile(r"\bclassify\s+(?:these|the|my)\s+"
               r"(?:test\s+)?failures\b", re.I),
]

_STRATEGY_RES = [
    re.compile(r"\bwhat\s+should\s+i\s+test\b",
               re.I),
    re.compile(r"\bwhat\s+tests?\s+(?:do\s+i\s+"
               r"need|does\s+it\s+need|should)\b",
               re.I),
    re.compile(r"\bwhat\s+needs\s+testing\b",
               re.I),
    re.compile(r"\bhow\s+do\s+i\s+test\b", re.I),
    re.compile(r"\btest\s+(?:plan|strategy)\b",
               re.I),
    re.compile(r"\bplan\s+(?:the\s+|a\s+)?test"
               r"(?:ing|s)?\b", re.I),
]

_ALLPASS_RE = re.compile(
    r"\b(?:all|everything|every)\b[^.?!]*"
    r"\bpass(?:ed|es)?\b|\bno\s+failures\b|"
    r"\b0\s+failed\b|\bgreen\s+run\b", re.I)


def _claim_exact(message: str, uid: str) -> Optional[Dict]:
    raw = str(message or "").strip()
    if not raw or not uid:
        return None
    if _defer_to_others(raw, uid):
        return None
    low = " ".join(raw.lower().split())
    low_c = low.rstrip(" .!?")
    for rx in _STATUS_RES:
        if rx.search(low):
            return {"op": "gmt_status", "raw": raw}
    god = bool(_GODMODE_RE.search(low))
    if god and (_OTHER_CHAIR_RE.search(low)
                or _OTHER_CHAIR_PHRASE_RE.search(low)):
        return None
    # The stamp ask is claimed to be REFUSED —
    # before the signal gate, because "100% safe"
    # is this seat's fence to enforce.
    for rx in _STAMP_RES:
        if rx.search(low):
            return {"op": "gmt_verdict", "raw": raw,
                    "stamp": True}
    has_test = bool(re.search(
        r"\b(?:test|tests|testing|suite|suites|"
        r"battery|check|checks)\b", low))
    # Escalation before verdict: "keep failing"
    # asks carry failure names, but the READ is
    # the ask.
    if has_test:
        for rx in _ESCALATE_RES:
            if rx.search(low):
                return {"op": "gmt_escalate",
                        "raw": raw}
    names = _failure_names(low)
    if _FAIL_ASSERT_RE.search(low) and (names or has_test):
        return {"op": "gmt_verdict", "raw": raw}
    for rx in _VERDICT_RES:
        if rx.search(low):
            return {"op": "gmt_verdict", "raw": raw}
    if _ALLPASS_RE.search(low) and has_test:
        return {"op": "gmt_verdict", "raw": raw}
    for rx in _STRATEGY_RES:
        if rx.search(low):
            return {"op": "gmt_strategy", "raw": raw}
    if god and re.search(r"\btester\b", low):
        return {"op": "gmt_status", "raw": raw}
    if god and not _TEST_SIGNAL_RE.search(low):
        return None
    if god and has_test and re.search(
            r"\b(?:plan|strategy|what|how)\b", low):
        return {"op": "gmt_strategy", "raw": raw}
    if god and re.search(
            r"^\W*god\s*mode\s*(?:ai\s*)?(?:tester)?"
            r"\s*[.!?]*$", low):
        return {"op": "gmt_status", "raw": raw}
    return None


_GARBLE_VOCAB = [
    "god", "mode", "tester", "testing", "test",
    "tests", "strategy", "verdict", "verdicts",
    "failed", "failures", "status", "bench",
    "safe", "flaky",
]

_GARBLE_OPS = ("gmt_strategy", "gmt_verdict",
               "gmt_escalate", "gmt_status")


def _claim_godmodetester(message: str, uid: str):
    """(claim, garble_info): the exact claim first;
    on a miss, the Round 75 second chance — og_garble
    repairs the TRIGGER words only. Every op here is
    a composition onto this seat's own bench (the
    R278 plan-parking precedent), so all four ride
    the garble path; a repair that lands no claim
    changes nothing."""
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


def install_godmodetester_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search OUTSIDE the
    landed helper chain (installed last, from the
    og_fixqueue chain — after the outermost landed
    helper seam, before the Round 275 Activity
    observer) so god-mode testing asks claim here
    and everything else delegates back untouched.
    Persona files never touched."""
    if getattr(agent_instance,
               "_og_godmodetester_installed", False):
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
            if uid and godmodetester_enabled():
                claim, info = _claim_godmodetester(
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
                f"GodModeTester trigger check failed: {e}")
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
                results = godmodetester_results(claim, uid)
            except Exception as e:
                logger.warning(f"GodModeTester op failed: {e}")
                results = _result(
                    "God Mode Tester — glitch",
                    "That god-mode testing ask glitched "
                    "on my end — nothing was verdicted, "
                    "planned, or changed. Say it again "
                    "and I'll retry.")
        if results is None:
            results = prev_search(query, num_results)
        if garble and results:
            results = og_garble.annotate(
                results, garble, prepend=True)
        return results

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_godmodetester_installed = True


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


def register_godmodetester_routes(app):
    """GET /godmodetester/status — the caller's own
    god-mode tester bench: strategies parked,
    verdicts given by class, escalation reads
    held — never another account's."""

    @app.get("/godmodetester/status")
    async def godmodetester_status_route(request: Request):
        uid = _route_uid(request)
        view = godmodetester_status_for(uid) if uid else {
            "strategies": 0, "verdicts": 0, "reads": 0,
            "by_class": {}, "last_strategy": None,
            "last_verdict": None}
        return {"ok": True,
                "enabled": godmodetester_enabled(),
                **view}
