"""Round 119: THE CODING + PROGRAMMING SPECIALIST
HELPER (Brent's order, 2026-10-12 11:25 PDT: "Coding
specialist helper programming specialist helper" —
named in one breath: ONE helper covering both,
specced in round-119/SPEC.md.)

WHAT THIS IS. The crew's programmer — the specialist
face of the coding OG can already do. Audit-first,
NO duplicates: it EXTENDS the shipped machinery and
forks none of it.

  - REPO-SHAPED ASKS ROUTE TO ROUND 60 EXACTLY AS IT
    STANDS. og_github already owns the visitor's own
    repos: repo map, <=3-file edits, the full test
    battery, post-merge live verification, PR-only +
    approval-gated. This seat checks og_github's own
    claim on every message FIRST (its pendings, its
    clarifying questions, its read/write/checks asks)
    and steps aside — the proven path, never
    re-implemented. This module calls NO GitHub write
    and opens NO pull request, ever.
  - THE CHECKS ARE ROUND 32'S. When code needs
    proving, it runs through og_github.
    run_python_checks — the code-that-works sandbox
    (py_compile + the code's own pytest when test
    files ride along) — and, when a draft fails a
    revisable check, og_github._fix_loop (bounded,
    3 attempts) revises it. No second sandbox, no
    second loop. The checks run on OG's server,
    NEVER on the user's machines.
  - THE CREW CODER STAYS THE CREW'S. og_crew's Coder
    (the fix-drafter folded in via og_progress rows)
    keeps its dispatch phrases and its seat; this
    specialist defers to the crew's claims exactly.

WHAT IT DOES, in chat (the bench):
  - WRITE-IT. "Write me a script that renames these
    files" (no repo named — the GitHub door never
    claimed those): when OG's drafting completion is
    available and the ask is Python, the seat drafts
    the file server-side, runs it through the checker
    BEFORE the user sees it (fix loop included), and
    presents the code with the check verdict BY NAME
    plus a plain-words explanation. When drafting
    isn't available, the seat parks the ask and the
    reply is drafted in chat under the seat's
    instruction; the user says "test it" and the
    code is lifted from the thread and checked for
    real then.
  - DEBUG. A pasted error or traceback (with or
    without the code): the facts are parsed
    deterministically — the exception, the message,
    the file and line, the offending line — and the
    answer leads with THE ONE BEST FIX, then the
    why. With the code pasted and drafting
    available, the fix itself is drafted, compiled,
    and shown with its own verdict.
  - EXPLAIN. Pasted code read back in plain words:
    what it does, piece by piece, anything
    surprising named. No rewrite unless asked.
  - PICK. "What language for X" gets ONE best
    answer with the reason (the R89 law) — never a
    menu.
  - SCAFFOLD. A small project's shape: the files,
    what each one does, and the first working
    version named — then the write lane drafts it.
  - TEST-IT / FIX-IT. The bench remembers the
    caller's last code (per account, own store).
    "Test it" runs the checker on the bench code
    (or on code pasted with the ask) and reports
    the verdict BY NAME. "Fix it" after a failed
    check runs the bounded loop on the bench code.

THE FENCES — binding, unchanged from the machinery
this seat rides:
- PR-ONLY + APPROVAL. Nothing merges, nothing lands
  on any main, nothing is written to any repo by
  this seat — repo writes live behind og_github's
  approval doors exactly as before. This seat's own
  outputs are chat answers and sandbox check runs.
- NOTHING EXECUTES ON THE USER'S MACHINES, EVER.
  The only code that runs anywhere runs in OG's
  server sandbox (the R32 caps), on pastes and
  drafts the user asked to have checked.
- THE VERDICT IS THE VERDICT. A check is reported
  by name: compiled OK, tests passed/failed with
  the failing names, compile errors quoted. A
  compile-only run is NEVER dressed as "it works" —
  when no tests ride along, the answer says the
  logic itself was not run. OG never says "that
  should work" in place of a result.

CLAIMS. This seam installs AFTER Selling (outermost
in the og_fixqueue chain) and defers EXACTLY first
to og_github's claim, then to the PM, the Filer,
the crew, the Builder, the Wood Pro, MaintEng,
the Mechanic, the Blueprint Specialist, Shopping,
Buying, Selling, the R54 marketplace claims, and
the ordering pack's claims — checked exactly,
never executed from here. Per-account isolation:
the bench is the caller's own, namespaced by uid
at every read (drilled). NO new loop (R72).
Persona untouched; app.py never touched. Switch:
OG_CODING_ENABLED (unset = ON), the SPEC's name.

STATE. Own durable store, the Round 18 pattern:
Postgres table og_coding_data when OG_MEMORY_DB_URL
is set, else a JSON file; blob kind 'bench' (the
caller's last coding exchange: the ask, the code
when the seat holds it, its language, and the last
check summary BY NAME). The LIVE pending (a write
ask awaiting its in-chat draft / check) is
process-local: a restart drops the pending, never
the bench record.
"""

import json
import logging
import os
import re
import threading
import time
from typing import Dict, List, Optional

import og_garble
import og_github

from fastapi import Request

logger = logging.getLogger(__name__)

_TAG = "OG CODING"

CODING_STORE_FILE = "coding_store.json"
_coding_lock = threading.RLock()

# The live pendings, process-local by design (the
# house pattern). uid -> {mode, ask, ts}.
_pendings: Dict[str, Dict] = {}

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, DB_OPTIONS as _DB_OPTS, StoreError, apply_bounds as _apply_bounds
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None


# --- Switch -----------------------------------------------------------------------


def coding_enabled() -> bool:
    """OG_CODING_ENABLED=false is the kill switch.
    Unset = ON."""
    val = os.environ.get("OG_CODING_ENABLED")
    if val is not None and val.strip().lower() in (
            "0", "false", "no", "off"):
        return False
    return True


# --- Durable store (Round 18 pattern, mirroring og_selling) -------------------------


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=_DB_CT, options=_DB_OPTS)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_coding_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(CODING_STORE_FILE):
        try:
            with open(CODING_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Coding store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(CODING_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Coding store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_coding_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"Coding DB load failed: {e}")
            raise StoreError("coding store unavailable (Round 99)") from e
    with _coding_lock:
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
                        "INSERT INTO og_coding_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Coding DB save failed: {e}")
            raise StoreError("coding store unavailable (Round 99)") from e
    with _coding_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


# --- The bench (the caller's last coding exchange) -----------------------------------


def _bench(uid: str) -> Dict:
    got = _blob(uid, "bench", {})
    return got if isinstance(got, dict) else {}


def _save_bench(uid: str, rec: Dict):
    _put_blob(uid, "bench", rec)


def _park_pending(uid: str, pending: Dict):
    with _coding_lock:
        _pendings[uid] = pending


def _live_pending(uid: str) -> Optional[Dict]:
    with _coding_lock:
        got = _pendings.get(uid)
    return got if isinstance(got, dict) and got.get("ask") else None


def _drop_pending(uid: str):
    with _coding_lock:
        _pendings.pop(uid, None)


def _result(tag: str, title: str, body: str,
            href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body,
             "href": href}]


# --- Facts: what the message itself carries ------------------------------------------

_FENCE_RE = re.compile(r"```(\w*)\s*\n(.*?)```", re.S)
_EXC_RE = re.compile(
    r"^([A-Za-z_][\w.]*(?:Error|Exception|Warning|"
    r"Interrupt|Exit))\s*:\s*(.+)$", re.M)
_FILELINE_RE = re.compile(r'File "([^"]+)", line (\d+)')
_TRACEBACK_MARK = "traceback (most recent call last)"

_LANG_WORDS = (
    ("javascript", ("javascript", " js ", "node", "node.js")),
    ("typescript", ("typescript", " ts ")),
    ("java", (" java ",)),
    ("c++", ("c++", "cpp")),
    ("c#", ("c#", "csharp", "unity script")),
    ("go", (" golang ", " go ")),
    ("rust", (" rust ",)),
    ("php", (" php ",)),
    ("ruby", (" ruby ",)),
    ("swift", (" swift ",)),
    ("kotlin", (" kotlin ",)),
    ("python", (" python ", "python3", " py ")),
)


def _fenced_blocks(message: str) -> List[Dict]:
    """Every fenced block the user pasted, with its
    fence language: [{lang, code}]."""
    out = []
    for m in _FENCE_RE.finditer(str(message or "")):
        code = m.group(2)
        if code and code.strip():
            out.append({"lang": (m.group(1) or "").lower(),
                        "code": code.strip("\n")})
    return out


def _is_test_block(block: Dict, all_blocks: List[Dict]) -> bool:
    code = block.get("code") or ""
    if re.search(r"\bdef test_", code) or "import pytest" in code \
            or "unittest" in code:
        return True
    return False


def _error_facts(message: str) -> Optional[Dict]:
    """The deterministic facts in a pasted failure:
    the exception + its message, the file/line frames,
    and whether a traceback rides along. None when the
    message carries no error shape at all."""
    raw = str(message or "")
    low = raw.lower()
    exc = None
    m = _EXC_RE.search(raw)
    if m:
        exc = {"type": m.group(1), "message": m.group(2).strip()}
    frames = _FILELINE_RE.findall(raw)
    has_tb = _TRACEBACK_MARK in low
    if not exc and not has_tb:
        return None
    facts = {"exception": exc, "traceback": has_tb,
             "frames": [{"file": f, "line": int(n)}
                        for f, n in frames[:4]]}
    if frames:
        facts["last_file"] = frames[-1][0]
        facts["last_line"] = int(frames[-1][1])
    return facts


def _facts_line(facts: Dict) -> str:
    bits = []
    exc = facts.get("exception") or {}
    if exc.get("type"):
        bits.append(f"{exc['type']}: {exc.get('message', '')}")
    if facts.get("last_file"):
        bits.append(f"at {facts['last_file']} line "
                    f"{facts.get('last_line')}")
    if facts.get("traceback"):
        bits.append("(full traceback pasted)")
    return " · ".join(bits) if bits else "an unnamed failure"


def _named_language(text: str) -> Optional[str]:
    low = " " + str(text or "").lower() + " "
    for lang, words in _LANG_WORDS:
        for w in words:
            if w in low:
                return lang
    return None


# --- The checker (Round 32's, through og_github — never a second sandbox) ------------


def _run_checks(code: str, test_code: str = "") -> Dict:
    """run_python_checks over the snippet (+ its test
    file when one rides along). The record is
    og_github's own, untouched."""
    files = {"snippet.py": code}
    if test_code:
        files["test_snippet.py"] = test_code
    return og_github.run_python_checks(files, ["snippet.py"])


def _checks_summary(test: Dict) -> Dict:
    """The bench's durable shape of a check record:
    the verdict pieces, BY NAME, nothing dressed."""
    comp = (test or {}).get("compile") or {}
    tests = (test or {}).get("tests") or {}
    return {
        "green": bool(og_github.checks_green(test)),
        "compile_ok": None if test.get("compile") is None
        else not bool(comp.get("errors")),
        "compile_errors": [str(e)[:240]
                           for e in (comp.get("errors") or [])][:3],
        "tests_status": str(tests.get("status") or ""),
        "passed": int(tests.get("passed") or 0),
        "failed": int(tests.get("failed") or 0),
        "failing": [str(f)[:160]
                    for f in (tests.get("failing") or [])][:5],
        "reason": str(tests.get("reason") or "")[:200],
    }


def _verdict_lines(summary: Dict) -> str:
    """The honest verdict, in words, from a summary.
    Compile-only is stated as compile-only — the
    logic was NOT run and the line says so."""
    if summary.get("compile_errors"):
        errs = summary["compile_errors"]
        lines = ["❌ It does NOT compile:"]
        lines += [f"  • {e}" for e in errs]
        lines.append("That's the checker's own word — "
                     "nothing about this code is proven "
                     "until it compiles.")
        return "\n".join(lines)
    status = summary.get("tests_status") or ""
    if status == "passed":
        if summary.get("passed"):
            return (f"✅ Compiled OK · tests: "
                    f"{summary['passed']} passed, 0 failed. "
                    "The checker ran it — that's a result, "
                    "not a guess.")
        return ("✅ Compiled OK · the test run came back "
                "clean (0 failures).")
    if status == "failed":
        names = ", ".join(summary.get("failing") or []) \
            or "names in the run output"
        return (f"⚠️ It compiles, but the tests FAIL: "
                f"{summary.get('passed', 0)} passed, "
                f"{summary.get('failed', 0)} failed — "
                f"failing: {names}. OG does not call that "
                "working.")
    if status == "none":
        return ("✅ It compiles clean. No tests came with "
                "it, so the checker proved it compiles — "
                "the logic itself was NOT run, and OG "
                "won't pretend otherwise.")
    if status == "unavailable":
        return ("✅ It compiles clean. The test runner "
                "itself is unavailable on OG's server, so "
                "no test verdict exists — compile is all "
                "that's proven.")
    if status == "timeout":
        return (f"⚠️ It compiles clean, but the test run "
                f"timed out ({summary.get('reason', '')}) — "
                "a timeout is unknown, never a pass.")
    if status == "error":
        return (f"⚠️ It compiles clean, but the test run "
                f"itself errored ({summary.get('reason', '')}) "
                "— that's the run, not necessarily the code.")
    if status == "skipped":
        return (f"✅ It compiles clean · the test suite "
                f"was not run ({summary.get('reason', '')}).")
    return "The checker returned no verdict."


def _record_checks(uid: str, rec: Dict, test: Dict) -> Dict:
    rec["checks"] = _checks_summary(test)
    rec["ts"] = time.time()
    _save_bench(uid, rec)
    return rec


# --- Drafting (og_github's completion — the R60 drafter, reused) ----------------------


def _draft_snippet(ask: str) -> Optional[str]:
    """One server-side draft of a write-it ask: a
    complete Python file in ONE fenced block, or None
    when drafting is unavailable / unusable."""
    raw = og_github._draft_completion([
        {"role": "system", "content":
         "You are OG's code drafter. Write the code the "
         "visitor asked for: a COMPLETE, runnable Python "
         "file, standard library only unless the ask "
         "names a library. Reply with ONLY the file in "
         "ONE fenced code block — no explanation, no "
         "extra text."},
        {"role": "user", "content":
         f"The ask: {ask}\n\nReply with the complete "
         "Python file in one fenced code block."},
    ])
    code = og_github._extract_code_block(raw or "")
    if code and len(code) <= 64000:
        return code
    return None


def _explain_tail() -> str:
    return ("Present the code EXACTLY as it stands — do "
            "not rewrite it, shorten it, or soften the "
            "check verdict — then explain it in plain "
            "words, in persona: what it does, how to run "
            "it, and one thing to watch for.")


# --- Operations -------------------------------------------------------------------------


def _do_write(uid: str, claim: Dict) -> List[Dict]:
    ask = str(claim.get("ask") or claim.get("raw") or "")
    lang = claim.get("lang") or _named_language(ask) or "python"
    rec = {"ask": ask, "code": None, "lang": lang,
           "path": "snippet.py", "checks": None,
           "mode": "write", "ts": time.time()}
    if lang == "python" and og_github._draft_available():
        code = _draft_snippet(ask)
        if code:
            def run_checks(c):
                return og_github.run_python_checks(
                    {"snippet.py": c}, ["snippet.py"])
            content, test, attempts, _cited = og_github._fix_loop(
                run_checks, "snippet.py", "", code, ask, [])
            rec["code"] = content
            _record_checks(uid, rec, test)
            summary = rec["checks"]
            body = (
                f"Drafted it and ran it through OG's "
                f"checker BEFORE showing you — the "
                f"verdict is the checker's own:\n\n"
                f"```python\n{content}\n```\n\n"
                f"{_verdict_lines(summary)}")
            attempts_line = og_github._attempts_line(
                attempts, test)
            if attempts_line:
                body += f"\n{attempts_line}"
            body += f"\n\n{_explain_tail()}"
            _drop_pending(uid)
            return _result(_TAG, "Coding — drafted + checked",
                           body)
    # No server draft (not Python, or drafting is
    # unavailable / returned nothing usable): park the
    # ask; the reply is drafted in chat under this
    # instruction, and the checker gets its turn the
    # moment the user says "test it".
    _save_bench(uid, rec)
    _park_pending(uid, {"mode": "write", "ask": ask,
                        "ts": time.time()})
    body = (
        f"The visitor asked for code: {ask!r} "
        f"(language: {lang}). Write it for them in "
        f"persona: ONE complete, runnable fenced code "
        f"block first — the whole thing, not a sketch — "
        f"then a plain-words explanation of what it "
        f"does and how to run it. One best version, no "
        f"menu of alternatives, no hedging. Do NOT "
        f"claim it was tested — it wasn't yet. Close by "
        f"telling them, in your own words, that saying "
        f"\"test it\" gets it run through OG's checker "
        f"for real before they trust it.")
    return _result(_TAG, "Coding — write it", body)


def _history_code(uid: str) -> Optional[str]:
    """The last fenced block in the caller's own
    thread (the in-chat draft the write lane parked
    for), read through og_github's bound history
    seam — the same reader the PR preview extraction
    trusts. None when no history seam is bound or no
    block stands in the thread."""
    load_history = og_github._deps.get("load_history")
    if load_history is None or not uid:
        return None
    try:
        history = load_history(uid)
    except Exception as e:
        logger.warning(f"Coding history read failed: {e}")
        return None
    for entry in reversed(history or []):
        if not isinstance(entry, dict):
            continue
        if entry.get("role") != "assistant":
            continue
        code = og_github._extract_code_block(
            str(entry.get("content", "")))
        if code:
            return code
    return None


def _do_test(uid: str, claim: Dict) -> List[Dict]:
    blocks = claim.get("blocks") or []
    code, test_code = "", ""
    for b in blocks:
        if _is_test_block(b, blocks) and not test_code:
            test_code = b.get("code") or ""
        elif not code:
            code = b.get("code") or ""
    source = "pasted with the ask"
    if not code:
        bench = _bench(uid)
        if bench.get("code"):
            code = bench["code"]
            source = "the code on your bench"
    if not code:
        code = _history_code(uid) or ""
        source = "the draft in this thread"
    if not code:
        return _result(
            _TAG, "Coding — nothing to test",
            "There's no code in front of me to run — "
            "not pasted with the ask, not on your "
            "bench, not in this thread. Paste the code "
            "and say \"test it\" again and the checker "
            "runs it for real.")
    if blocks and blocks[0].get("lang") not in ("", "python", "py"):
        return _result(
            _TAG, "Coding — checker is Python-only",
            f"That code is {blocks[0]['lang']} — OG's "
            "checker only runs Python here, so there's "
            "no honest verdict to give it. It was NOT "
            "run, and OG won't dress an eyeball up as a "
            "test.")
    test = _run_checks(code, test_code)
    bench = _bench(uid)
    rec = {"ask": bench.get("ask") or "pasted code",
           "code": code, "lang": "python",
           "path": "snippet.py", "checks": None,
           "mode": "test", "ts": time.time()}
    _record_checks(uid, rec, test)
    _drop_pending(uid)
    summary = rec["checks"]
    body = (f"Ran the checker on {source}.\n\n"
            f"{_verdict_lines(summary)}")
    if not summary.get("green"):
        body += ("\nSay \"fix it\" and OG takes a run "
                 "at fixing it himself — revised, "
                 "re-checked, and the new verdict shown "
                 "either way.")
    return _result(_TAG, "Coding — check verdict", body)


def _do_fix(uid: str, claim: Dict) -> List[Dict]:
    bench = _bench(uid)
    code = bench.get("code") or ""
    if not code:
        return _result(
            _TAG, "Coding — nothing to fix",
            "There's no code on your bench to fix. "
            "Paste the broken code (with the error, if "
            "you've got it) and I'll take it from there.")
    ask = bench.get("ask") or "fix the code"
    if not og_github._draft_available():
        summary = bench.get("checks") or {}
        failing = summary.get("compile_errors") or \
            summary.get("failing") or []
        named = "; ".join(failing) or \
            summary.get("tests_status") or "the last check"
        body = (
            f"The visitor's code on the bench failed "
            f"its check ({named}) and they said \"fix "
            f"it\". OG's drafting bench is unavailable "
            f"right now, so give the fix in persona: "
            f"the ONE best correction first — the "
            f"corrected code block, complete — then one "
            f"short why, aimed at the named failure. "
            f"Tell them to say \"test it\" after and "
            f"the checker delivers the real verdict.")
        return _result(_TAG, "Coding — fix it", body)

    def run_checks(c):
        return og_github.run_python_checks(
            {"snippet.py": c}, ["snippet.py"])
    content, test, attempts, _cited = og_github._fix_loop(
        run_checks, "snippet.py", "", code, ask, [])
    rec = dict(bench)
    rec["code"] = content
    rec["mode"] = "fix"
    _record_checks(uid, rec, test)
    summary = rec["checks"]
    body = (f"Took a run at it myself — drafted, "
            f"revised against the checker, and this is "
            f"where it landed:\n\n"
            f"```python\n{content}\n```\n\n"
            f"{_verdict_lines(summary)}")
    attempts_line = og_github._attempts_line(attempts, test)
    if attempts_line:
        body += f"\n{attempts_line}"
    body += f"\n\n{_explain_tail()}"
    return _result(_TAG, "Coding — fixed + re-checked", body)


def _do_debug(uid: str, claim: Dict) -> List[Dict]:
    facts = claim.get("facts") or {}
    blocks = claim.get("blocks") or []
    code = blocks[0].get("code") if blocks else ""
    fl = _facts_line(facts) if facts else "a failure with no error text pasted"
    if code and og_github._draft_available():
        test = _run_checks(code)
        summary = _checks_summary(test)
        raw = og_github._draft_completion([
            {"role": "system", "content":
             "You are OG's code drafter. The visitor's "
             "code is failing. Diagnose from the error "
             "facts and produce the ONE best fix: the "
             "complete corrected file. Reply with ONLY "
             "the fixed file in ONE fenced code block — "
             "no explanation."},
            {"role": "user", "content":
             f"The failure: {fl}\n\nThe code:\n```\n"
             f"{code}\n```\n\nReply with the complete "
             "fixed file in one fenced code block."},
        ])
        fixed = og_github._extract_code_block(raw or "")
        if fixed and fixed != code:
            ftest = _run_checks(fixed)
            fsummary = _checks_summary(ftest)
            rec = {"ask": claim.get("raw") or "debug",
                   "code": fixed, "lang": "python",
                   "path": "snippet.py", "checks": None,
                   "mode": "debug", "ts": time.time()}
            _record_checks(uid, rec, ftest)
            body = (
                f"THE FIX — one best fix, first:\n\n"
                f"```python\n{fixed}\n```\n\n"
                f"WHY IT BROKE: the run itself says "
                f"{fl}. The fix is aimed at exactly "
                f"that — not a spray of guesses.\n\n"
                f"THE FIX, CHECKED: "
                f"{_verdict_lines(fsummary)}")
            return _result(_TAG, "Coding — the fix", body)
    # Instruction lane: the facts are real (parsed from
    # the paste); the fix prose is the model's, aimed by
    # them. ONE best fix first, then the why — the SPEC
    # drill, and the R89 law.
    bench_note = ""
    if code:
        bench_note = (" The code is pasted in this "
                      "same message — work from it.")
    body = (
        f"The visitor's code is failing. The facts, "
        f"parsed off their own paste: {fl}.{bench_note} "
        f"Answer in persona with THE ONE BEST FIX "
        f"FIRST — the corrected code (or the exact "
        f"corrected lines) before anything else — "
        f"then ONE short why, tied to the named "
        f"failure. No menu of possible causes, no "
        f"hedging. If the one best fix truly needs "
        f"code you can't see, give the fix for what's "
        f"named and name the exact piece to paste "
        f"next — never invent their code. Close by "
        f"offering the real check: once the fixed "
        f"code is in front of you, \"test it\" runs "
        f"it through OG's checker.")
    if code:
        rec = {"ask": claim.get("raw") or "debug",
               "code": code, "lang": "python",
               "path": "snippet.py", "checks": None,
               "mode": "debug", "ts": time.time()}
        _save_bench(uid, rec)
    return _result(_TAG, "Coding — debug", body)


def _do_explain(uid: str, claim: Dict) -> List[Dict]:
    blocks = claim.get("blocks") or []
    code = blocks[0].get("code") if blocks else ""
    if code:
        rec = {"ask": claim.get("raw") or "explain",
               "code": code,
               "lang": blocks[0].get("lang") or "python",
               "path": "snippet.py",
               "checks": (_bench(uid).get("checks")
                          if _bench(uid).get("code") == code
                          else None),
               "mode": "explain", "ts": time.time()}
        _save_bench(uid, rec)
    body = (
        "The visitor pasted code in this message and "
        "wants it explained. In persona, in plain "
        "words: first what the code does overall, in "
        "one or two lines; then walk it piece by "
        "piece in the order it runs, saying what each "
        "part is FOR; name anything surprising, "
        "risky, or easy to break, plainly. No jargon "
        "left unexplained. Do NOT rewrite the code "
        "and do NOT fix anything — they asked what "
        "it does, not for a new version. If a part "
        "genuinely can't be read from what's pasted, "
        "say which part and why — never invent what "
        "it does.")
    return _result(_TAG, "Coding — explained plain", body)


# The pick table: job words -> (pick, why, the other
# wins only if). ONE best answer (R89) — the runner-up
# is named once, as the condition, never as a menu.
_PICKS = (
    (("scrape", "scraping", "crawl", "pull data off",
      "grab data from"), "Python",
     "its scraping and text libraries are the deepest "
     "and it reads like the job itself",
     "the site needs a real browser clicking around — "
     "then the browser lane rides Python anyway"),
    (("csv", "excel", "spreadsheet", "data cleaning",
      "clean data", "analyze data", "numbers"),
     "Python",
     "rows, columns, and files are its home turf",
     "the whole job lives inside Excel itself — then "
     "it's a macro, not a program"),
    (("web page", "webpage", "website", "front end",
      "frontend", "in the browser"), "JavaScript",
     "it is the browser's own language — nothing else "
     "runs there natively",
     "you only need a static page — then plain HTML "
     "with no script at all"),
    (("android",), "Kotlin",
     "it's Android's own first language now — the "
     "tools and docs all aim at it",
     "the same app must also ship on iPhone — then a "
     "cross-platform kit earns its keep"),
    (("iphone", "ios", "ipad"), "Swift",
     "it's the iPhone's own language; Apple's tools "
     "are built around it",
     "the same app must also ship on Android — then "
     "a cross-platform kit earns its keep"),
    (("unity", "game"), "C#",
     "Unity is built on it — your game code, your "
     "scenes, one language",
     "the game targets a different engine entirely"),
    (("rename files", "batch", "automation", "automate",
      "folders", "organize files"), "Python",
     "file work is a few readable lines and it runs "
     "everywhere",
     "it must run on a bare Windows box with nothing "
     "installed — then PowerShell is already there"),
    (("api", "server", "backend", "back end", "web app"),
     "Python",
     "a working server is a dozen lines and the "
     "error messages read plain",
     "your whole team already writes JavaScript — "
     "then Node keeps one language across the stack"),
    (("windows app", "desktop app", "windows program"),
     "C#",
     "it's Windows' own stack — native windows, "
     "native feel, one toolkit",
     "it must also run on Mac or Linux unchanged"),
)


def _do_pick(uid: str, claim: Dict) -> List[Dict]:
    raw = str(claim.get("raw") or "")
    low = " " + raw.lower() + " "
    for words, pick, why, other in _PICKS:
        if any(w in low for w in words):
            body = (f"**{pick}.** That's the one for "
                    f"this job: {why}. The other pick "
                    f"only wins if {other}. One answer, "
                    f"on purpose — say the word and the "
                    f"bench starts writing it in {pick}.")
            return _result(_TAG, f"Coding — pick: {pick}",
                           body)
    body = (
        f"The visitor asked which language or tool "
        f"fits their job: {raw!r}. Answer in persona "
        f"with ONE best pick — decisive, no menu, no "
        f"hedging (the one-best-answer law). Name the "
        f"pick, the one reason it's right for THIS "
        f"job, and the single condition under which "
        f"a different pick would win. Then offer to "
        f"start writing it.")
    return _result(_TAG, "Coding — the pick", body)


_SCAFFOLDS = (
    (("api", "server", "backend", "back end"),
     "API server",
     (("main.py", "the server itself — routes and "
       "startup; the whole first version can live here"),
      ("models.py", "the data shapes, once main.py "
       "outgrows holding them"),
      ("requirements.txt", "the libraries it needs, "
       "pinned"),
      ("README.md", "how to run it, in plain words"),
      ("tests/test_api.py", "the checks the bench can "
       "run for real"))),
    (("bot",),
     "bot",
     (("main.py", "the bot — connects, listens, "
       "answers; the first version lives here"),
      ("handlers.py", "what it does per command, "
       "once main.py gets crowded"),
      ("config.py", "its settings and keys — never "
       "hard-coded in main.py"),
      ("requirements.txt", "the libraries it needs, "
       "pinned"),
      ("README.md", "how to run it, in plain words"))),
    (("game",),
     "game (Unity shape)",
     (("Assets/Scripts/GameManager.cs", "the game's "
       "brain — state, turns, winning and losing"),
      ("Assets/Scripts/Player.cs", "the player — "
       "movement and input"),
      ("Assets/Scenes/Main.unity", "the one scene "
       "the first version needs"),
      ("README.md", "how to open and run it, in "
       "plain words"))),
    (("site", "web page", "webpage", "page"),
     "web page",
     (("index.html", "the page itself — the whole "
       "first version can live here"),
      ("style.css", "how it looks, once the page "
       "outgrows inline styles"),
      ("app.js", "what it does when clicked, once "
       "it does something"),
      ("README.md", "how to open it, in plain words"))),
)

_SCAFFOLD_DEFAULT = (
    "python tool",
    (("main.py", "the tool itself — the whole first "
      "working version lives here"),
     ("README.md", "what it does and how to run it, "
      "in plain words"),
     ("tests/test_main.py", "the checks the bench "
      "can run for real")))


def _do_scaffold(uid: str, claim: Dict) -> List[Dict]:
    raw = str(claim.get("raw") or "")
    low = " " + raw.lower() + " "
    kind, files = None, None
    for words, k, f in _SCAFFOLDS:
        if any(w in low for w in words):
            kind, files = k, f
            break
    if kind is None:
        kind, files = _SCAFFOLD_DEFAULT
    lines = [f"THE SHAPE — a small {kind}, kept "
             "small on purpose:"]
    for name, job in files:
        lines.append(f"  • {name} — {job}")
    first = files[0][0]
    lines.append(
        f"\nFIRST WORKING VERSION: just {first}. "
        "Everything else earns its place after that "
        "runs. Say \"write " + first.split('/')[-1] +
        "\" (or name any file) and the bench drafts "
        "it — checked before you trust it, if it's "
        "Python.")
    prev = _bench(uid)
    rec = {"ask": raw,
           "code": prev.get("code"),
           "lang": prev.get("lang") or "python",
           "path": prev.get("path") or "snippet.py",
           "checks": prev.get("checks"),
           "mode": "scaffold", "ts": time.time()}
    _save_bench(uid, rec)
    _park_pending(uid, {"mode": "scaffold", "ask": raw,
                        "ts": time.time()})
    return _result(_TAG, "Coding — the shape",
                   "\n".join(lines))


def _do_status(uid: str, claim: Dict) -> List[Dict]:
    view = status_for(uid)
    bench = view.get("bench")
    if not bench:
        return _result(
            _TAG, "Coding — bench empty",
            "Your bench is empty — no code has been "
            "written, pasted, or checked this round. "
            "Ask for a script, paste some code, or "
            "paste an error and it starts filling.")
    bits = [f"Last ask: {bench.get('ask') or '—'}"]
    bits.append(f"Language: {bench.get('lang') or '—'}")
    bits.append("Code on the bench: "
                + ("yes" if bench.get("has_code") else "no"))
    checks = bench.get("checks")
    if checks:
        bits.append("Last check: " + _verdict_lines(checks))
    else:
        bits.append("Last check: none run yet — say "
                    "\"test it\" once there's code.")
    return _result(_TAG, "Coding — bench status",
                   "\n".join(bits))


def coding_results(claim: Dict, uid: str) -> List[Dict]:
    op = (claim or {}).get("op")
    if op == "cd_write":
        return _do_write(uid, claim)
    if op == "cd_test":
        return _do_test(uid, claim)
    if op == "cd_fix":
        return _do_fix(uid, claim)
    if op == "cd_debug":
        return _do_debug(uid, claim)
    if op == "cd_explain":
        return _do_explain(uid, claim)
    if op == "cd_pick":
        return _do_pick(uid, claim)
    if op == "cd_scaffold":
        return _do_scaffold(uid, claim)
    if op == "cd_status":
        return _do_status(uid, claim)
    return _result(_TAG, "Coding",
                   "That coding ask didn't land — say "
                   "it again and the bench takes it.")


def status_for(uid: str) -> Dict:
    bench = _bench(uid)
    if not bench:
        return {"bench": None}
    return {"bench": {
        "ask": str(bench.get("ask") or "")[:200],
        "lang": bench.get("lang"),
        "has_code": bool(bench.get("code")),
        "checks": bench.get("checks"),
        "mode": bench.get("mode"),
    }}


# --- Chat claims (narrow frames — the crew/PM/filer doctrine) --------------------------

_GARBLE_VOCAB = ("coding", "code", "script", "function",
                 "python", "debug", "error", "explain",
                 "test", "program", "programming",
                 "scaffold", "fix", "checker")

_STATUS_RE = re.compile(
    r"\bcoding\s+status\b|\bmy\s+bench\b|"
    r"\bwhat'?s\s+on\s+my\s+bench\b", re.I)
_TEST_RE = re.compile(
    r"\b(?:test|check|run)\s+(?:it|this|that|the\s+code|"
    r"my\s+code|the\s+script)\b|"
    r"\brun\s+the\s+checks?\b|"
    r"\btest\s+the\s+code\b", re.I)
_FIX_RE = re.compile(
    r"^\W*(?:fix|repair)\s+(?:it|this|that)\b", re.I)
_DEBUG_PASTE_RE = re.compile(
    r"\b(?:debug|fix|what'?s\s+wrong\s+with|"
    r"why\s+(?:does|is|did))\b", re.I)
_DEBUG_SHAPE_RE = re.compile(
    r"\bmy\s+(?:code|script|program|function)\b[^.?!]*"
    r"\b(?:fail|fails|failing|error|errors|broken|"
    r"crash|crashes|throw|throws|bug|bugs|won'?t\s+run|"
    r"not\s+working)\b|"
    r"\b(?:getting|got|shows|throws)\s+an?\s+"
    r"(?:error|exception)\b", re.I)
_EXPLAIN_RE = re.compile(
    r"\bexplain\b[^.?!]*\b(?:code|script|function|this|it)\b|"
    r"\bwhat\s+does\s+this\s+(?:code|script|function)\s+do\b|"
    r"\bwalk\s+me\s+through\s+this\s+(?:code|script)\b", re.I)
_PICK_RE = re.compile(
    r"\bwhat\s+(?:language|tool|stack|framework)\b|"
    r"\bwhich\s+(?:language|tool|framework)\b|"
    r"\bshould\s+i\s+(?:use|learn|pick|write)\b|"
    r"\bbest\s+(?:language|tool)\s+for\b", re.I)
_PICK_OR_RE = re.compile(
    r"\b(?:python|javascript|java|kotlin|swift|rust|go|"
    r"php|ruby|c\+\+|c#)\s+or\s+"
    r"(?:python|javascript|java|kotlin|swift|rust|go|"
    r"php|ruby|c\+\+|c#)\b", re.I)
_SCAFFOLD_RE = re.compile(
    r"\b(?:scaffold|start|set\s*up|bootstrap|structure)\b"
    r"[^.?!]*\b(?:project|app|api|site|bot|game|tool|"
    r"server|program)\b|"
    r"\bstart\s+a\s+(?:new\s+)?(?:project|app)\b", re.I)
_WRITE_RE = re.compile(
    r"\b(?:write|make|create|build|generate|code)\b"
    r"[^.?!]*\b(?:script|function|program|code|class|"
    r"parser|scraper|tool|utility|snippet)\b|"
    r"\bcode\s+(?:that|to|for)\b|"
    r"\bscript\s+that\b|"
    r"\bwrite\s+me\s+a\b[^.?!]*\b(?:script|function|"
    r"program|tool)\b", re.I)


def _other_claim(message: str, uid: str):
    """(source, claim) of the first inner seat that
    claims this message exactly, else None. og_github
    FIRST — its pendings (a drafted PR awaiting its
    YES), its clarifying questions, and every
    repo-shaped ask are the R60 path's own. Then the
    PM, the Filer, the crew, the Builder, the Wood
    Pro, MaintEng, the Mechanic, the Blueprint
    Specialist, Shopping, Buying, Selling, the R54
    marketplace chain, and the ordering pack: checked
    exactly, never executed from here."""
    try:
        c = og_github._claim_job(message, uid)
        if c:
            return "github", c
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
        import og_mpwatch
        c = og_mpwatch._claim_job(message, uid)
        if c:
            return "mpwatch", c
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
    return None


def _defer_to_others(message: str, uid: str) -> bool:
    return _other_claim(message, uid) is not None


def _claim_exact(message: str, uid: str) -> Optional[Dict]:
    raw = str(message or "").strip()
    if not raw or not uid:
        return None
    blocks = _fenced_blocks(raw)
    facts = _error_facts(raw)
    bench = _bench(uid)
    has_bench_code = bool(bench.get("code"))
    # The bench's own follow-ups bind FIRST — an
    # explicit "test it" / "fix it" over code this
    # seat already holds — but never across another
    # flow's claim on the same words.
    if _TEST_RE.search(raw) and (
            blocks or has_bench_code
            or _live_pending(uid) is not None):
        if not _defer_to_others(raw, uid):
            return {"op": "cd_test", "blocks": blocks,
                    "raw": raw}
    if _FIX_RE.search(raw) and has_bench_code:
        checks = bench.get("checks") or {}
        if bench.get("checks") is None or \
                not checks.get("green", True):
            if not _defer_to_others(raw, uid):
                return {"op": "cd_fix", "raw": raw}
    if _defer_to_others(raw, uid):
        return None
    if _STATUS_RE.search(raw):
        return {"op": "cd_status", "raw": raw}
    if blocks:
        if facts or _DEBUG_PASTE_RE.search(raw) \
                and re.search(r"\b(?:wrong|fail|error|bug|fix|debug)\b",
                              raw, re.I):
            return {"op": "cd_debug", "blocks": blocks,
                    "facts": facts, "raw": raw}
        if _EXPLAIN_RE.search(raw):
            return {"op": "cd_explain", "blocks": blocks,
                    "raw": raw}
        if _TEST_RE.search(raw):
            return {"op": "cd_test", "blocks": blocks,
                    "raw": raw}
        return None
    if facts and _DEBUG_SHAPE_RE.search(raw) \
            or facts and "code" in raw.lower():
        return {"op": "cd_debug", "blocks": blocks,
                "facts": facts, "raw": raw}
    if _DEBUG_SHAPE_RE.search(raw):
        return {"op": "cd_debug", "blocks": blocks,
                "facts": facts, "raw": raw}
    if _PICK_RE.search(raw) or _PICK_OR_RE.search(raw):
        return {"op": "cd_pick", "raw": raw}
    if _SCAFFOLD_RE.search(raw):
        return {"op": "cd_scaffold", "raw": raw}
    if _WRITE_RE.search(raw):
        return {"op": "cd_write", "raw": raw,
                "ask": raw}
    return None


def _claim_coding(message: str, uid: str):
    """(claim, garble_info): the exact claim first; on
    a miss, the Round 75 second chance — og_garble
    repairs the TRIGGER words only; the payload (the
    pasted code, the error text) passes verbatim —
    the fenced blocks are re-read off the ORIGINAL
    message, never the repaired one, so a repair can
    never rewrite the user's code."""
    claim = _claim_exact(message, uid)
    if claim:
        return claim, None
    try:
        repaired, info = og_garble.repair_info(
            str(message or ""), _GARBLE_VOCAB)
        if info:
            claim = _claim_exact(repaired, uid)
            if claim:
                if claim.get("blocks"):
                    claim["blocks"] = _fenced_blocks(
                        str(message or ""))
                return claim, info
    except Exception:
        pass
    return None, None


# --- The seam ----------------------------------------------------------------------------

_pending = {"claim": None, "garble": None}


def install_coding_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search OUTSIDE the
    Selling seam (installed last, from the og_fixqueue
    chain) so coding-bench asks claim here and
    everything else delegates back untouched. Persona
    files never touched."""
    if getattr(agent_instance, "_og_coding_installed",
               False):
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
            if uid and coding_enabled():
                claim, info = _claim_coding(
                    str(message), uid)
                if claim:
                    _pending["claim"] = claim
                    _pending["garble"] = info
                    if isinstance(intent, dict):
                        if not intent.get("needs_web_search"):
                            intent["needs_web_search"] = True
                            intent["search_query"] = \
                                str(message).strip()
        except Exception as e:
            logger.warning(f"Coding trigger check failed: {e}")
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
                results = coding_results(claim, uid)
            except Exception as e:
                logger.warning(f"Coding op failed: {e}")
                results = _result(
                    _TAG, "Coding — glitch",
                    "That ask glitched on the bench — "
                    "nothing was written, run anywhere "
                    "but the checker, or sent anywhere. "
                    "Say it again and I'll retry.")
        if results is None:
            results = prev_search(query, num_results)
        if garble and results:
            results = og_garble.annotate(results, garble,
                                         prepend=True)
        return results

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_coding_installed = True


# --- Routes --------------------------------------------------------------------------------


def _route_uid(request) -> str:
    """Round 95: the caller's ONE identity — the live
    session's account uid when signed in, else the
    presented cookie (og_accounts.session_uid_or)."""
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


def register_coding_routes(app):
    """GET /coding/status — the caller's own bench:
    the last ask, whether code stands on it, and the
    last check summary BY NAME."""

    @app.get("/coding/status")
    async def coding_status_route(request: Request):
        uid = _route_uid(request)
        view = status_for(uid) if uid else {"bench": None}
        return {"ok": True, "enabled": coding_enabled(),
                **view}
