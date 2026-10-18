"""Round 280: GOD MODE AI RUNSET (Brent's words,
2026-10-13 13:39 PDT: "God mode Ai runset
helper" — read back as the top runset seat of
the God Mode tier and APPROVED 13:41 as part of
the nine-seat God Mode batch; specced in
round-280/SPEC.md.) The God Mode tier's runset
seat, ABOVE Master AI Runset (R277): the final
read on the battery picture — the top verdict
on whether a run counts as clean, and the seat
that settles disputes about what a failure is.

WHAT ALREADY EXISTED (ridden, never forked):

  - THE PICTURE IS THE MASTER RUNSET SEAT'S
    (R277, og_masterrunset — unbuilt when this
    seat was prepped): the standing baseline
    and what it is / when it last moved, the
    run-to-run deltas (NEW / DISAPPEARED /
    UNCHANGED, by name), the regression flags
    with first appearances, the completeness
    record, the run history. This seat DEFERS
    to it defensively wherever the module
    answers (a getattr-probed check, inert
    until it lands) and its own guard never
    eats a baseline / delta / history ask —
    that chair is the Master seat's when it
    lands. This seat RULES on the picture; it
    does not keep the picture, and it never
    moves a baseline: a baseline change is a
    landing event recorded in a round's NOTES,
    never any seat's chat call.
  - VERDICTS BY NAME ARE THE GOD MODE
    TESTER'S (R279, og_godmodetester): every
    named failure classified — real break,
    known family, timing-shaped, not enough
    proof — plus test strategy and escalation
    reads. This seat defers to it defensively
    the same way, and never re-classifies a
    name the Tester has read when the ask is
    the Tester's shape. What this seat adds is
    the RUN-LEVEL ruling the Tester never
    gives (the Tester reads names; this seat
    rules on the run) and the dispute ruling
    when the reads disagree.
  - RUNNING IS THE MACHINERY'S, NOT THIS
    SEAT'S. OG's batteries and drills run
    through the machinery that already owns
    them. This seat holds NO runner of its
    own: no battery handle, no check handle,
    no execution surface at all (source-
    pinned). "Run the tests" is NOT claimed
    here — it falls through to whoever owns
    it. The stopped test-running landing is
    another seat's chair, full stop: this
    seat builds nothing on it, calls nothing
    in it, and does not reference it.
  - THE REPO DOOR IS ROUND 60'S (og_github —
    pendings, clarifying questions, checks
    asks, the PR-only door). Checked FIRST in
    the deferral; this seat holds no repo
    machinery of its own.
  - THE GOD MODE CODING SEAT (R278) OWNS
    WHOLE-BUILD PLANS AND CODE ESCALATIONS.
    Deferred to exactly in the chain; its
    shapes are never eaten here.

WHAT IT OWNS — the ruling nobody claimed:

  - THE TOP VERDICT. Given the battery
    picture — the failing names, the baseline
    names, the completeness, as the caller
    brings them (and Master Runset's parked
    picture read defensively when that seat
    exists) — this seat gives the crew's
    FINAL read on whether the run COUNTS AS
    CLEAN, and says why in plain words.
    COMPLETENESS IS STATED FIRST, before any
    ruling: a partial run NEVER gets called
    a full clean run at this level. The
    ruling is deterministic off the evidence:
    a genuinely new name (failed, on no
    baseline named, matching no known family)
    means the run does NOT count as clean,
    and the names are named; baseline names
    and known-family names are CARRIED —
    named, never hidden, never dressed as
    new; an unproven timing-shaped name
    blocks a clean ruling until a re-run
    settles it. When the picture brought is
    not enough to rule on, the seat says
    CANNOT RULE and names what to bring —
    it never rules from nothing. The ruling
    PARKS on the caller's bench.
  - DISPUTES SETTLED. When seats disagree
    about whether a failure is new or a
    known family — "the tester says it's a
    regression, the picture says it's a
    family; settle it" — this seat rules,
    and its read is the one that stands. The
    evidence hierarchy is fixed and shown:
    a name standing on the baseline named in
    the ask is NOT NEW, whatever either side
    says; a name matching a known family
    (the r29/r38 Steel live-profile race,
    the r61 and r83 timing families) is the
    FAMILY it matches; a name on no baseline
    and in no family, with the run asserting
    failure, is GENUINELY NEW and the
    regression read stands; a name the
    evidence cannot place gets NO RULING —
    the seat names what would settle it, and
    neither side's word stands as fact until
    then. Reasoning shown, names named,
    every time. The ruling parks.
  - THE BENCH. "God mode runset status"
    reads the bench back: top verdicts given
    (by outcome), disputes settled, the last
    of each — re-served with no new work.
    GET /godmoderunset/status serves the
    same picture.

THE FENCES — binding:

  - GOD MODE IS A TIER, NOT A BYPASS.
    READ-ONLY AT THE TOP: this seat edits
    nothing, re-runs nothing, files nothing,
    and NEVER MOVES A BASELINE — baselines
    move only through a landing's NOTES;
    this seat reads and rules on the picture.
  - IT NEVER STAMPS A RUN "100% safe" —
    not in a ruling, not anywhere. A clean
    ruling is "this run counts as clean on
    the picture brought, the carried names
    named" — never a safety stamp. A stamp
    ask chaired to this seat is claimed and
    REFUSED, by name.
  - COMPLETENESS IS STATED FIRST: no ruling
    from this seat ever leads with the
    outcome; the battery's completeness (as
    stated in the picture brought, or NOT
    STATED, said plainly) comes first, and
    a partial battery's green is ruled a
    partial green, never a full clean run.
  - IT NEVER RULES FROM NOTHING: no names,
    no picture, no ruling — the seat names
    what to bring instead of guessing.
  - NOTHING OUTSIDE THE RUNSET PICTURE:
    pricing, money, connections, and account
    actions stay behind their own fences,
    and the other god-mode seats' chairs
    (Coding, Tester, Money, Organizer, Push,
    Validation, Fix Code, Fixer, Boss,
    Design, Writer, Unity, Roster, Builder)
    are guarded by name until those seats
    land. The Master Runset seat's own chair
    (baseline / deltas / history) is guarded
    the same way — the ruling is this seat's,
    the picture is the Master seat's.
  - Per-account isolation like every seat:
    one account's bench, rulings, and
    disputes are never shown to another.
    Persona file never touched; app.py NOT
    touched — module + og_library /
    og_fixqueue seams only. No new loop, no
    new timer (R72). Switch
    OG_GODMODERUNSET_ENABLED (unset = ON;
    off = the seat doesn't exist).
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
    StoreError as _StoreError, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo, pooled_connect as _pooled_connect

try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # pragma: no cover - local fallback
    psycopg = None
    _Jsonb = None

_TAG = "OG GOD MODE RUNSET"

GODMODERUNSET_STORE_FILE = os.getenv(
    "OG_GODMODERUNSET_STORE", "godmoderunset_store.json")

_gmr_lock = threading.RLock()


# --- The seat switch ---------------------------------------------------------


def godmoderunset_enabled() -> bool:
    """OG_GODMODERUNSET_ENABLED is the seat's kill
    switch: an explicit off (0/false/no/off) = the
    seat doesn't exist. Unset = ON (the crew
    pattern)."""
    v = os.getenv(
        "OG_GODMODERUNSET_ENABLED", "").strip().lower()
    return v not in ("0", "false", "no", "off")


# --- The bench store (Round 18 pattern: Postgres og_godmoderunset_data when
# --- OG_MEMORY_DB_URL is set, the JSON file otherwise) -----------------------


def _db_connect():
    conn = _pooled_connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_godmoderunset_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(GODMODERUNSET_STORE_FILE):
        try:
            with open(GODMODERUNSET_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"GodModeRunset store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(GODMODERUNSET_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"GodModeRunset store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_godmoderunset_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"GodModeRunset DB load failed: {e}")
            raise _StoreError(
                "godmoderunset store unavailable (Round 99)") from e
    with _gmr_lock:
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
                        "INSERT INTO og_godmoderunset_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"GodModeRunset DB save failed: {e}")
            raise _StoreError(
                "godmoderunset store unavailable (Round 99)") from e
    with _gmr_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


def _rulings(uid: str) -> List[Dict]:
    got = _blob(uid, "rulings", [])
    return got if isinstance(got, list) else []


def _park_ruling(uid: str, rec: Dict) -> Dict:
    rows = _rulings(uid)
    rows.append(rec)
    _put_blob(uid, "rulings", rows[-20:])
    return rec


def _disputes(uid: str) -> List[Dict]:
    got = _blob(uid, "disputes", [])
    return got if isinstance(got, list) else []


def _park_dispute(uid: str, rec: Dict) -> Dict:
    rows = _disputes(uid)
    rows.append(rec)
    _put_blob(uid, "disputes", rows[-20:])
    return rec


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


# --- The picture parser (deterministic — the ruling reads only what is
# --- brought, plus the Master seat's parked picture when it exists) ---------


_NAME_RE = re.compile(
    r"\b(?:test|check|suite|r\d+)[A-Za-z0-9_.\-]*\b")

_STOPWORDS = {
    "tests", "test", "the", "these", "those", "my",
    "failures", "failure", "failed", "failing",
    "results", "result", "verdict", "verdicts",
    "run", "suite", "suites", "check", "checks",
    "read", "names", "name", "all", "and", "for",
    "keep", "every", "clean", "count", "counts",
    "settle", "settled", "dispute", "right",
    "tester", "testing",
}


def _names_in(text: str) -> List[str]:
    """The check names actually named in a stretch
    of text — tokens shaped like check names
    (test_… / check_… / rNN…), deduplicated, glue
    words dropped. A name is evidence; prose is
    not. Shared shape with the Tester seat's own
    extractor: the crew reads names one way."""
    seen: List[str] = []
    for m in _NAME_RE.finditer(str(text or "").lower()):
        tok = m.group(0).strip("._-")
        if not tok or tok in _STOPWORDS:
            continue
        if tok not in seen:
            seen.append(tok)
    return seen[:12]


_FAIL_ASSERT_RE = re.compile(
    r"\b(?:fail|failed|failing|fails|red|error|"
    r"errors|broke|broken)\b", re.I)

_BASELINE_CUE_RE = re.compile(
    r"\bbaseline\b[^:.;]*[:\-—]?\s*", re.I)

_PARTIAL_RE = re.compile(
    r"\b(?:partial|only\s+ran|just\s+ran|just\s+the|"
    r"skipped|didn'?t\s+run|did\s+not\s+run|"
    r"cut\s+short|one\s+suite|single\s+suite|"
    r"not\s+the\s+full)\b", re.I)

_FULL_RE = re.compile(
    r"\b(?:full\s+battery|whole\s+battery|"
    r"entire\s+battery|all\s+suites|full\s+runset|"
    r"full\s+run|everything\s+ran)\b", re.I)

_ALLPASS_RE = re.compile(
    r"\b(?:all|everything|every)\b[^.?!]*"
    r"\bpass(?:ed|es)?\b|\bno\s+failures\b|"
    r"\b0\s+failed\b|\bgreen\s+run\b|"
    r"\ball\s+green\b", re.I)


def _baseline_names(raw: str) -> List[str]:
    """The names the ask itself places on the
    baseline: the names in the stretch that
    follows the word "baseline" (the caller's
    own statement of the accepted picture).
    Nothing here reaches into any record —
    the standing baseline itself is Master
    Runset's record; this seat rules on the
    picture as brought."""
    out: List[str] = []
    for m in _BASELINE_CUE_RE.finditer(str(raw or "")):
        seg = str(raw)[m.end():m.end() + 400]
        seg = seg.split(". ")[0].split(" — ")[0]
        for n in _names_in(seg):
            if n not in out:
                out.append(n)
    return out[:12]


def _failing_names(raw: str, baseline: List[str]) -> List[str]:
    names = _names_in(raw)
    return [n for n in names if n not in baseline][:12]


def _completeness(raw: str) -> str:
    if _PARTIAL_RE.search(str(raw or "")):
        return "PARTIAL"
    if _FULL_RE.search(str(raw or "")):
        return "FULL"
    return "NOT STATED"


_FAMILY_RULES = [
    (re.compile(r"(?:^|[_\-. ])r(?:29|38)(?:[_\-. ]|$)|"
                r"steel|live[_\- ]profile", re.I),
     "KNOWN FAMILY — the r29/r38 Steel live-profile race"),
    (re.compile(r"(?:^|[_\-. ])r61(?:[_\-. ]|$)", re.I),
     "KNOWN FAMILY — the r61 timing family"),
    (re.compile(r"(?:^|[_\-. ])r83(?:[_\-. ]|$)", re.I),
     "KNOWN FAMILY — the r83 timing family"),
    (re.compile(r"timeout|timed[_\- ]out|flak|race|"
                r"intermittent|order[_\- ]dependent",
                re.I),
     "TIMING-FAMILY SHAPED — suspected, not proven"),
]


def _family_of(name: str) -> Optional[str]:
    for rx, label in _FAMILY_RULES:
        if rx.search(name):
            return label
    return None


def _master_picture(uid: str) -> Optional[Dict]:
    """Master Runset's parked picture for this
    account, read DEFENSIVELY when that seat
    exists (getattr-probed; inert until R277
    lands). This seat reads the picture to rule
    on it — it never writes to the Master
    seat's store, and a picture it cannot read
    is simply not in evidence."""
    try:
        import og_masterrunset
        fn = getattr(og_masterrunset,
                     "masterrunset_status_for", None)
        if fn is not None:
            view = fn(uid)
            if isinstance(view, dict):
                return view
    except Exception:
        pass
    return None


# --- The top verdict (the run-level ruling) ------------------------------------


_FENCE_LINE = (
    "The line that never moves: this seat reads "
    "and rules on the picture — it edits nothing, "
    "re-runs nothing, moves no baseline, and no "
    "run, from this seat, ever gets stamped "
    "\"100% safe\".")


def _do_topverdict(uid: str, claim: Dict) -> List[Dict]:
    raw = str(claim.get("raw") or "")
    if claim.get("stamp"):
        return _result(
            "God Mode Runset — the stamp, refused",
            "No. This seat never stamps a run "
            "\"100% safe\" — not a clean one, not "
            "anyone's. What it gives instead is the "
            "top verdict WITH the picture: the "
            "battery's completeness stated first, "
            "the failing names named, the carried "
            "names carried in the open — and the "
            "ruling that follows from them. Bring "
            "the run's picture and you get the real "
            "ruling; the stamp doesn't exist here.")
    comp = _completeness(raw)
    baseline = _baseline_names(raw)
    asserted = bool(_FAIL_ASSERT_RE.search(raw))
    allpass = bool(_ALLPASS_RE.search(raw))
    failing = _failing_names(raw, baseline) \
        if asserted else []
    if not failing and allpass:
        failing = []
    picture = _master_picture(uid)
    comp_line = {
        "FULL": "Completeness, stated first: the "
                "FULL battery, as the picture "
                "brought describes it.",
        "PARTIAL": "Completeness, stated first: "
                   "a PARTIAL battery — only part "
                   "of the runset ran, as the "
                   "picture brought describes it.",
        "NOT STATED": "Completeness, stated "
                      "first: NOT STATED in the "
                      "picture brought — this "
                      "ruling covers the names "
                      "brought; the run's own "
                      "completeness record is "
                      "Master Runset's to keep.",
    }[comp]
    if picture:
        comp_line += (" Master Runset's parked "
                      "picture for your account was "
                      "read alongside (its record, "
                      "not this seat's).")
    if not failing and not allpass and not baseline:
        return _result(
            "God Mode Runset — the top verdict",
            comp_line + "\n"
            "CANNOT RULE — and this seat never "
            "rules from nothing. A top verdict "
            "needs the run's picture: which checks "
            "failed BY NAME, which of them stand "
            "on the baseline, and whether the full "
            "battery ran or only part of it. Bring "
            "the names and the completeness, and "
            "the ruling follows from them — named, "
            "with the reasoning shown.")
    new_names: List[str] = []
    carried: List[str] = []
    unproven: List[str] = []
    for n in failing:
        fam = _family_of(n)
        if fam and fam.startswith("KNOWN FAMILY"):
            carried.append(f"{n} — {fam}")
        elif fam:
            unproven.append(f"{n} — {fam}")
        else:
            new_names.append(n)
    for n in baseline:
        carried.append(
            f"{n} — on the baseline named in the "
            f"picture (carried, not new)")
    if comp == "PARTIAL":
        outcome = "DOES NOT COUNT AS A FULL CLEAN RUN"
        why = ("a partial battery's green is a "
               "partial green: it covers the suites "
               "that ran, nothing beyond them — at "
               "this level a partial run never gets "
               "called a full clean run.")
    elif new_names:
        outcome = "DOES NOT COUNT AS CLEAN"
        why = ("genuinely new failures stand in "
               "the picture — failed, on no "
               "baseline named, in no known "
               "family. A run carrying new names "
               "does not count as clean until "
               "those names are fixed or "
               "classified through the doors that "
               "own them.")
    elif unproven:
        outcome = "DOES NOT COUNT AS CLEAN — YET"
        why = ("names in the picture are "
               "timing-shaped but UNPROVEN: a "
               "suspicion is not a ruling either "
               "way, and this seat does not call "
               "a run clean on a suspicion. The "
               "same names green on a clean "
               "re-run, with nothing changed, is "
               "what settles them.")
    else:
        outcome = "COUNTS AS CLEAN"
        why = ("no genuinely new failures stand "
               "in the picture: every failing name "
               "brought is carried in the open "
               "below, and a run is clean at this "
               "level when nothing new stands "
               "against the baseline — clean "
               "means exactly that, never "
               "\"100% safe\".")
    rec = {"id": hashlib.sha1(
        f"{uid}|ruling|{time.time()}".encode(
            "utf-8")).hexdigest()[:8],
           "outcome": outcome, "completeness": comp,
           "new": new_names, "carried": len(carried),
           "at": time.time()}
    _park_ruling(uid, rec)
    lines = [f"THE TOP VERDICT — {outcome} "
             f"(ruling #{rec['id']}, {_asof()})",
             comp_line,
             f"Why, in plain words: {why}"]
    if new_names:
        lines.append("Genuinely new, named: "
                     + ", ".join(new_names))
    if unproven:
        lines.append("Unproven, named: "
                     + "; ".join(unproven))
    if carried:
        lines.append("Carried, in the open: "
                     + "; ".join(carried))
    if not failing and allpass:
        lines.append(
            "No failures were named in the "
            "picture brought — the ruling reads "
            "the run as described, and this seat "
            "repeats it as the picture brought, "
            "not as a run it watched.")
    lines.append(_FENCE_LINE)
    return _result("God Mode Runset — the top verdict",
                   "\n".join(lines))


# --- Disputes settled ------------------------------------------------------------


def _do_dispute(uid: str, claim: Dict) -> List[Dict]:
    raw = str(claim.get("raw") or "")
    baseline = _baseline_names(raw)
    names = _names_in(raw)
    disputed = [n for n in names if n not in baseline]
    targets = disputed or names
    if not targets:
        return _result(
            "God Mode Runset — dispute settled",
            "A dispute ruling needs the NAME. Tell "
            "me which check the seats disagree on "
            "and what each side calls it — new "
            "regression or known family — and the "
            "ruling follows from the evidence, "
            "with the reasoning shown. No name, no "
            "ruling: this seat never settles a "
            "dispute it cannot name.")
    lines = ["THE DISPUTE RULING — this seat's "
             "read is the one that stands:"]
    ruled_rows = []
    for n in targets[:4]:
        if n in baseline:
            ruled = "NOT NEW — it stands on the baseline"
            why = ("the picture brought places "
                   "this name on the baseline: an "
                   "accepted failure, carried in "
                   "the open. The side calling it "
                   "new is wrong on the evidence, "
                   "whatever the run printed.")
        else:
            fam = _family_of(n)
            if fam and fam.startswith("KNOWN FAMILY"):
                ruled = fam
                why = ("the name matches the "
                       "family's own shape: it is "
                       "labeled as the family it "
                       "is — never dressed as new, "
                       "never hidden either. The "
                       "family side stands.")
            elif fam:
                ruled = "NO RULING — unproven either way"
                why = ("the name is timing-SHAPED "
                       "but shape is suspicion, not "
                       "proof: neither side's word "
                       "stands as fact. What "
                       "settles it is the same "
                       "name green on a clean "
                       "re-run with nothing "
                       "changed — bring that, and "
                       "the ruling lands.")
            elif _FAIL_ASSERT_RE.search(raw):
                ruled = "GENUINELY NEW — the regression read stands"
                why = ("failed, on no baseline "
                       "named in the picture, "
                       "matching no known family: "
                       "on the evidence brought, "
                       "this one is new, and the "
                       "side calling it a "
                       "regression is right.")
            else:
                ruled = "NO RULING — no result in evidence"
                why = ("no side brought the run's "
                       "own words for this name "
                       "(failed / passed, and the "
                       "line it printed): this "
                       "seat rules on evidence, "
                       "never on volume.")
        ruled_rows.append({"name": n, "ruled": ruled})
        lines.append(f"• {n} — {ruled}: {why}")
    rec = {"id": hashlib.sha1(
        f"{uid}|dispute|{time.time()}".encode(
            "utf-8")).hexdigest()[:8],
           "names": ruled_rows, "at": time.time()}
    _park_dispute(uid, rec)
    lines.append(
        f"Parked on your bench at {_asof()} "
        f"(ruling #{rec['id']}). The reasoning "
        f"above is the ruling's whole basis — "
        f"bring new evidence (a re-run, the "
        f"baseline record) and the seat re-reads "
        f"it in the open.")
    lines.append(_FENCE_LINE)
    return _result("God Mode Runset — dispute settled",
                   "\n".join(lines))


# --- Status ------------------------------------------------------------------------


def godmoderunset_status_for(uid: str) -> Dict:
    rulings = _rulings(uid)
    disputes = _disputes(uid)
    by_outcome: Dict[str, int] = {}
    for rec in rulings:
        k = str(rec.get("outcome") or "")
        if k:
            by_outcome[k] = by_outcome.get(k, 0) + 1
    last_r = rulings[-1] if rulings else None
    last_d = disputes[-1] if disputes else None
    return {
        "rulings": len(rulings),
        "disputes": len(disputes),
        "by_outcome": by_outcome,
        "last_ruling": ({
            "id": last_r.get("id"),
            "outcome": last_r.get("outcome"),
            "completeness": last_r.get("completeness"),
        } if last_r else None),
        "last_dispute": ({
            "id": last_d.get("id"),
            "names": [r.get("name")
                      for r in (last_d.get("names")
                                or [])],
        } if last_d else None),
    }


def _do_status(uid: str, claim: Dict) -> List[Dict]:
    view = godmoderunset_status_for(uid)
    if not view["rulings"] and not view["disputes"]:
        return _result(
            "God Mode Runset — bench status",
            "Your God Mode Runset bench is EMPTY: "
            "no top verdicts given, no disputes "
            "settled. Bring a run's picture — the "
            "failed names, the baseline names, "
            "whether the full battery ran — and "
            "ask if it counts as clean; or bring "
            "a disagreement over a name, and it "
            "gets settled here.")
    lines = [
        f"YOUR GOD MODE RUNSET BENCH (as of "
        f"{_asof()}):",
        f"• top verdicts given: {view['rulings']}",
        f"• disputes settled: {view['disputes']}",
    ]
    if view["by_outcome"]:
        tally = ", ".join(
            f"{k.lower()}: {v}"
            for k, v in sorted(view["by_outcome"].items())
            if v)
        if tally:
            lines.append(f"• verdicts by outcome: {tally}")
    last_r = view.get("last_ruling")
    if last_r:
        lines.append(
            f"• last top verdict: #{last_r.get('id')} — "
            f"{last_r.get('outcome')} "
            f"(completeness: "
            f"{last_r.get('completeness')})")
    last_d = view.get("last_dispute")
    if last_d:
        names = ", ".join(
            n for n in (last_d.get("names") or []) if n)
        lines.append(
            f"• last dispute: #{last_d.get('id')} — "
            f"{names}")
    lines.append(
        "The bench re-serves from here with no new "
        "work — and nothing on it was ever stamped "
        "\"100% safe\", and no baseline on it was "
        "ever moved by this seat.")
    return _result("God Mode Runset — bench status",
                   "\n".join(lines))


def godmoderunset_results(claim: Dict,
                          uid: str) -> List[Dict]:
    op = (claim or {}).get("op")
    if op == "gmr_verdict":
        return _do_topverdict(uid, claim)
    if op == "gmr_dispute":
        return _do_dispute(uid, claim)
    if op == "gmr_status":
        return _do_status(uid, claim)
    return _result(
        "God Mode Runset",
        "That god-mode runset ask didn't land — "
        "say it again and the top runset seat "
        "takes it.")


# --- Claims -------------------------------------------------------------------------
#
# The claims order, pinned (SPEC, Round 280): this seat installs
# AFTER the landed helper chain (its seam block sits after the
# outermost landed helper block in og_fixqueue and before the
# Round 275 Activity block) — its detect wraps outermost among
# the helper seats — and its claims sit AFTER R277 Master
# Runset's claims and after R128 Testing's where they overlap.
# TWO of its shapes are checked BEFORE the deferral chain, by
# design and documented here: the dispute shape and the
# run-level top-verdict shape. They are this seat's exclusive
# claims (no landed or specced seat gives a run-level ruling or
# settles a new-vs-family dispute), their patterns require
# runset context (run / battery / failure-name vocabulary), and
# the probe before this build verified no landed seat claims
# them. Everything else DEFERS EXACTLY FIRST: og_github's own
# claim, then the inner owners in house order, with the
# runset-adjacent chairs checked exactly among them: og_coding,
# the God Mode Coding seat (R278 — checked exactly), the God
# Mode Tester seat and the Master Runset seat (both defensive
# getattr-probed checks wherever those modules answer), and
# LAST og_browser. The stopped test-running seat is NOT in
# this chain and NOT referenced: its run-the-suite shapes were
# never this seat's to claim, and running asks fall through
# untouched. Where the chains overlap, the owners below answer
# first; god mode runset claims only the ruling: the top
# verdict, the dispute settlement, the stamp refusal chaired
# to it, and its own bench.


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
    (R278's whole-build plans, rescue reads, and
    splits stay its wherever it answers), Phone,
    CarPlay, Tracker, Marketplace, Facebook,
    Messenger, the R54 marketplace chain, og_watch,
    og_monitor, the ordering pack, the R127 Website +
    Browser seat, GOD MODE TESTER (defensive —
    R279's strategy / verdicts-by-name /
    escalation shapes stay its wherever it
    answers), MASTER RUNSET (defensive — R277's
    baselines / deltas / history stay its
    wherever it answers), and LAST og_browser —
    checked exactly, never executed from here."""
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
        import og_godmodetester
        c = None
        for _fn in ("_claim_exact", "_claim_godmodetester"):
            _f = getattr(og_godmodetester, _fn, None)
            if _f is not None:
                try:
                    c = _f(message, uid)
                except Exception:
                    c = None
                # The garble wrapper answers with a
                # (claim, info) TUPLE: normalize to
                # the claim itself, and count only
                # a real claim dict — a bare tuple
                # is truthy even when its claim is
                # None, and counting it would defer
                # every ask this seat exists for.
                if isinstance(c, tuple):
                    c = c[0] if c else None
                if isinstance(c, dict) and c:
                    break
                c = None
        if c:
            return "godmodetester", c
    except Exception:
        pass
    try:
        import og_masterrunset
        c = None
        for _fn in ("_claim_exact", "_claim_masterrunset"):
            _f = getattr(og_masterrunset, _fn, None)
            if _f is not None:
                c = _f(message, uid)
                if isinstance(c, tuple):
                    c = c[0] if c else None
                if isinstance(c, dict) and c:
                    break
                c = None
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
# the Master Runset seat's picture-chair words,
# or a fenced domain) is never eaten here — the
# chair named right after the invocation, plus
# the phrase chairs. It falls through to whoever
# owns it, or to nobody until that seat lands.
# RUNSET is this seat's own chair and is NOT in
# this list; TESTER is — verdicts by name are
# the Tester seat's, and this seat's guard
# keeps its hands off them.
_OTHER_CHAIR_RE = re.compile(
    r"\bgod\s*mode\s+(?:ai\s+)?(?:coding|code|"
    r"tester|test|money|maker|organizer|organize|"
    r"push|validation|validate|fix\s*code|fixer|"
    r"boss|design|designer|writer|writing|unity|"
    r"roster|eye|memory|data|algorithm|research|"
    r"teacher|safety|security|blueprint|"
    r"mechanic|shopping|selling|social|movie|"
    r"trending|health|voice|phone|communication|"
    r"banker|budget|trading|builder)\b", re.I)

_OTHER_CHAIR_PHRASE_RE = re.compile(
    r"\bgod\s*mode\b[^.?!]*\b(?:baseline|deltas?|"
    r"overseer|history|boss\s+of|builds\s+all|"
    r"whole[-\s]?build\s+plan|rescue\s+read|"
    r"pricing|subscription|stripe|password)\b",
    re.I)

_RUNSET_CONTEXT_RE = re.compile(
    r"\b(?:test|tests|testing|run|runs|runset|"
    r"battery|suite|suites|check|checks|fail|"
    r"fails|failed|failing|failure|failures|"
    r"baseline|regression|family|verdict)\b",
    re.I)

_TOPVERDICT_RES = [
    re.compile(r"\bdoes\s+(?:this|the|that)\s+"
               r"(?:run|battery|runset)\s+count\b",
               re.I),
    re.compile(r"\bcount(?:s|ed)?\s+as\s+(?:a\s+)?"
               r"clean\b", re.I),
    re.compile(r"\bis\s+(?:this|the|that)\s+"
               r"(?:run|battery|runset)\s+clean\b",
               re.I),
    re.compile(r"\btop\s+verdict\b", re.I),
    re.compile(r"\bfinal\s+verdict\b[^.?!]*"
               r"\b(?:run|battery|runset)\b", re.I),
    re.compile(r"\bverdict\s+on\s+(?:the|this|"
               r"that)\s+(?:run|battery|runset)\b",
               re.I),
    re.compile(r"\bfinal\s+(?:say|ruling|read)\s+"
               r"on\s+(?:the|this|that)\s+"
               r"(?:run|battery)\b", re.I),
    re.compile(r"\brule\s+on\s+(?:the|this|that)\s+"
               r"(?:run|battery|runset)\b", re.I),
]

_DISPUTE_RES = [
    re.compile(r"\bsettle\s+(?:it|this|the\s+"
               r"dispute)\b", re.I),
    re.compile(r"\bwho'?s\s+right\b", re.I),
    re.compile(r"\bdispute\s+(?:over|about)\b",
               re.I),
    re.compile(r"\bsays\s+it'?s\s+(?:a\s+)?"
               r"(?:new|regression|real\s+break)\b"
               r"[^.?!]*\bsays\s+it'?s\b", re.I),
    re.compile(r"\bdisagree(?:s|d|ment)?\b[^.?!]*"
               r"\b(?:test|run|battery|baseline|"
               r"family|regression)\b", re.I),
]

_STATUS_RES = [
    re.compile(r"\bgod\s*mode\s+(?:ai\s+)?runset\b"
               r"[^.?!]*\b(?:status|bench)\b", re.I),
    re.compile(r"\bmy\s+runset\s+(?:bench|"
               r"rulings|verdicts)\b", re.I),
]

_STAMP_RES = [
    re.compile(r"\b100\s*%\s*safe\b", re.I),
    re.compile(r"\bstamp(?:ed|ing)?\b[^.?!]*"
               r"\bsafe\b", re.I),
    re.compile(r"\bguarantee(?:d|s)?\b[^.?!]*"
               r"\bsafe\b", re.I),
]

_RUNSET_SIGNAL_RE = re.compile(
    r"\b(?:runset|run|runs|battery|clean|count|"
    r"counts|verdict|rule|ruling|settle|dispute|"
    r"fail|fails|failed|failure|baseline)\b",
    re.I)


def _claim_exact(message: str, uid: str) -> Optional[Dict]:
    raw = str(message or "").strip()
    if not raw or not uid:
        return None
    low = " ".join(raw.lower().split())
    # THIS SEAT'S TWO EXCLUSIVE SHAPES FIRST (see
    # the claims-order note above): the dispute
    # settlement and the run-level top verdict.
    # Both require runset context in the ask; a
    # shape without it is not this seat's.
    if _RUNSET_CONTEXT_RE.search(low):
        for rx in _DISPUTE_RES:
            if rx.search(low):
                return {"op": "gmr_dispute",
                        "raw": raw}
        for rx in _TOPVERDICT_RES:
            if rx.search(low):
                return {"op": "gmr_verdict",
                        "raw": raw}
    if _defer_to_others(raw, uid):
        return None
    for rx in _STATUS_RES:
        if rx.search(low):
            return {"op": "gmr_status", "raw": raw}
    god = bool(_GODMODE_RE.search(low))
    if god and (_OTHER_CHAIR_RE.search(low)
                or _OTHER_CHAIR_PHRASE_RE.search(low)):
        return None
    # The stamp ask chaired to THIS seat is
    # claimed to be REFUSED — the fence is this
    # seat's to enforce on its own chair. (A
    # stamp ask with no runset chair was already
    # the Tester seat's, in the deferral above.)
    if god and re.search(r"\brunset\b", low):
        for rx in _STAMP_RES:
            if rx.search(low):
                return {"op": "gmr_verdict",
                        "raw": raw, "stamp": True}
    if god and re.search(
            r"^\W*god\s*mode\s*(?:ai\s*)?runset"
            r"\s*[.!?]*$", low):
        return {"op": "gmr_status", "raw": raw}
    if god and not _RUNSET_SIGNAL_RE.search(low):
        return None
    if god and re.search(r"\brunset\b", low) and \
            re.search(r"\b(?:clean|count|ruling|"
                      r"verdict)\b", low):
        return {"op": "gmr_verdict", "raw": raw}
    return None


_GARBLE_VOCAB = [
    "god", "mode", "runset", "verdict", "clean",
    "count", "counts", "settle", "dispute",
    "ruling", "status", "bench", "battery",
    "safe", "baseline", "failed", "failures",
]

_GARBLE_OPS = ("gmr_verdict", "gmr_dispute",
               "gmr_status")


def _claim_godmoderunset(message: str, uid: str):
    """(claim, garble_info): the exact claim first;
    on a miss, the Round 75 second chance — og_garble
    repairs the TRIGGER words only. Every op here is
    a composition onto this seat's own bench (the
    R278 plan-parking precedent), so all three ride
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


# --- The seam ---------------------------------------------------------------------

_pending = {"claim": None, "garble": None}


def install_godmoderunset_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search OUTSIDE the
    landed helper chain (installed last, from the
    og_fixqueue chain — after the outermost landed
    helper seam, before the Round 275 Activity
    observer) so god-mode runset asks claim here
    and everything else delegates back untouched.
    Persona files never touched."""
    if getattr(agent_instance,
               "_og_godmoderunset_installed", False):
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
            if uid and godmoderunset_enabled():
                claim, info = _claim_godmoderunset(
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
                f"GodModeRunset trigger check failed: {e}")
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
                results = godmoderunset_results(claim, uid)
            except Exception as e:
                logger.warning(f"GodModeRunset op failed: {e}")
                results = _result(
                    "God Mode Runset — glitch",
                    "That god-mode runset ask glitched "
                    "on my end — nothing was ruled, "
                    "settled, or changed. Say it again "
                    "and I'll retry.")
        if results is None:
            results = prev_search(query, num_results)
        if garble and results:
            results = og_garble.annotate(
                results, garble, prepend=True)
        return results

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_godmoderunset_installed = True


# --- Routes --------------------------------------------------------------------------


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


def register_godmoderunset_routes(app):
    """GET /godmoderunset/status — the caller's own
    god-mode runset bench: top verdicts given by
    outcome, disputes settled, the last of each —
    never another account's."""

    @app.get("/godmoderunset/status")
    def godmoderunset_status_route(request: Request):
        uid = _route_uid(request)
        view = godmoderunset_status_for(uid) if uid else {
            "rulings": 0, "disputes": 0, "by_outcome": {},
            "last_ruling": None, "last_dispute": None}
        return {"ok": True,
                "enabled": godmoderunset_enabled(),
                **view}
