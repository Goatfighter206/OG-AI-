"""
OG books (Generator 7c) — OG drafts a BOOK OUTLINE in chat,
the visitor approves it, and OG writes the book chapter by
chapter into the Library: structured long-form with a cover,
a reader, and a .md download.

The flow (Brent's prepare -> approve -> execute rule, the
7a podcast / Round 28 video shape):

1. ASK. "write a book about X" (also "write me a book…",
   "start a book…") claims a book job. The reply makes the
   agent WRITE THE OUTLINE in the thread — a TITLE line, a
   PREMISE line, and one "CHAPTER n: <title> — <beat>" line
   per chapter (default 8 chapters unless the visitor says
   otherwise) — and end with the approval ask. A per-visitor
   pending is parked (30-min expiry). NO chapter call happens
   before approval — zero chapter spend pre-YES; the outline
   draft itself is the one chat call of the ask turn, charged
   by /chat exactly once (this module never double-charges).
2. APPROVE. Chat YES / NO, or the on-page card (GET
   /books/status carries the pending with the REAL title,
   premise, chapter list and per-chapter word target —
   captured from the visitor's own history, the "what was
   shown is what ships" pattern; POST /books/approve and
   /books/decline are the card's doors). First resolution
   wins; a decline scraps the pending and spends nothing.
   Outline edits ("make chapter 3 darker") loop back to a
   fresh outline draft.
3. GENERATE. On approval a background job writes the book
   chapter by chapter. Each chapter is ONE chat-model call
   (chat/completions, OPENAI_MODEL) carrying the full
   outline, a running summary the job maintains, and the
   previous chapter's tail — target ~800–1,200 words per
   chapter unless the visitor asked otherwise. EVERY
   chapter is saved into the durable job record the moment
   it completes, so a redeploy mid-book loses nothing: the
   job RESUMES from the next unwritten chapter ("continue
   my book" / "finish the book", or POST
   /books/<id>/continue). "Rewrite chapter 3" is a fresh
   charged generation replacing that chapter, only on the
   explicit ask, and runs synchronously (one chapter).
4. COVER. When the LAST chapter lands — never before — the
   cover is generated once through the designer seam
   (og_designer.make_design with a poster spec: topic =
   premise, title = book title), whose machinery composites
   the title verbatim and charges the `image` kind exactly
   once, success-only. A capped/failed cover never sinks
   the book: it completes with cover_error stated honestly,
   and the persisted cover_url / cover_failed flags
   guarantee at most one cover attempt per book.
5. DELIVER. A Library item of kind "book" (action "Read",
   action_url /books/<id>/read); GET /books (the caller's
   books with progress), GET /books/<id> ({title, premise,
   cover_url, chapters: [{n, title, words}], status}),
   GET /books/<id>/read (full text, owner-only),
   GET /books/<id>/download (a .md file: title page +
   chapters), a book_done notification (target {"view":
   "library", "kind": "book", "id": ...}), and the
   og_books.js card showing "Chapter N of M done" progress
   lines as chapters land plus the reader overlay.

CAPS — chapters charge `chat`, through /chat's own path:
- Each chapter's tokens (the provider's usage.total_tokens
  when returned, else a chars/4 estimate) are recorded via
  app.py's _record_chat_usage — the SAME record path /chat
  uses, against the SAME weekly chat pool (Round 55).
- THE FENCE: before EVERY chapter the job re-reads the
  weekly pool. Pool dry => the job PAUSES honestly (state
  "paused_caps", progress intact, plain-words detail: it
  resumes as the visitor's old days roll off the week, or
  via token packs). The job never burns past the fence
  silently, and never auto-burns token packs: pack funding
  starts only on the visitor's explicit "continue my book"
  against a paused book with a balance > 0, and then each
  chapter debits its actual tokens at the chat rate (1:1,
  og_tokenpacks.settle_chat's shape), re-pausing if the
  balance runs dry.

DURABILITY DIVERGENCE (deliberate): podcast/video job
stores prune after 24 h because their deliverable is a
file with a locker home. A book's deliverable IS its job
record (the chapter text lives in it), so book records
are NEVER TTL-pruned.

CONTINUITY SUMMARY: extractive and local — after each
chapter the job appends "Ch n '<title>': <opening lines>
… <closing line>" to a capped running summary. No extra
model call, no extra charge. A rewrite rebuilds it whole.

DARK GATE: books are live only when an OpenAI key is
configured (chapters ride chat/completions). Dark = claims
never fire, /books/status answers {"enabled": false}, the
approve/decline/continue doors 404.

Persona files are NEVER touched by this module. Outlines
come from the existing persona, in the thread, like every
other generator's drafts.
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

import og_tiers as _tiers

logger = logging.getLogger(__name__)

STORE_DIR = os.getenv("OG_BOOK_STORE_DIR", "book_builds")
_PENDING_TTL = 30 * 60          # outline approval window
_STALE_S = 45 * 60              # working record older = cut off
_DEFAULT_CHAPTERS = 8
_MIN_CHAPTERS = 3
_MAX_CHAPTERS = 20
_DEFAULT_CH_WORDS = 1000        # mid of the 800-1,200 band
_MIN_CH_WORDS = 200
_MAX_CH_WORDS = 3000
_TAIL_CHARS = 600               # previous chapter's tail
_SUMMARY_CAP = 6000             # running summary ceiling
_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Bound by app.py via bind_app when a host binds them (tests);
# unbound (production) each dep resolves lazily from the
# fully-loaded app module — the og_podcast precedent.
_deps: Dict = {}

_APP_ATTRS = {
    "load_usage": "_load_usage_store",
    "save_usage": "_save_usage_store",
    "usage_lock": "_usage_lock",
    "load_history": "_load_visitor_history_locked",
    "record_chat_usage": "_record_chat_usage",
    "estimate_tokens": "_estimate_call_tokens",
    "get_agent": "get_agent",
}


def bind_app(deps):
    _deps.update(deps)


def _dep(name):
    if name in _deps:
        return _deps[name]
    if name == "get_api_key":
        return lambda: os.getenv("OPENAI_API_KEY", "")
    if name == "get_tier":
        def _get_tier():
            try:
                import app as _appmod
                return str(_appmod._current_tier.get("tier", "free")
                           or "free")
            except Exception:
                return "free"
        return _get_tier
    if name == "tier_of":
        def _tier_of(uid, req):
            try:
                import app as _appmod
                return _appmod._tier_of(req.cookies, uid)
            except Exception:
                return "free"
        return _tier_of
    attr = _APP_ATTRS.get(name)
    if attr is None:
        return None
    try:
        import app as _appmod
        return getattr(_appmod, attr, None)
    except Exception as e:
        logger.warning(f"og_books: app dep {name} unavailable: {e}")
        return None


def _pro_url() -> str:
    return _tiers.public_pro_url()


# --- Gate ------------------------------------------------------------------------


def _api_key() -> str:
    fn = _dep("get_api_key")
    try:
        return str(fn() or "") if fn else ""
    except Exception:
        return ""


def enabled() -> bool:
    """Books ride chat/completions, so the gate is the key."""
    return bool(_api_key())


# --- Usage / caps (the usage store, app.py's own shapes) --------------------------


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _usage_get(key: str):
    load, lock = _dep("load_usage"), _dep("usage_lock")
    if load is None or lock is None:
        return None
    with lock:
        store = load()
    entry = store.get(key)
    return dict(entry) if isinstance(entry, dict) else None


_local_chat_tokens: Dict[str, int] = {}


def _chat_used_week(uid: str) -> int:
    entry = _usage_get(uid)
    if entry is None:
        return _local_chat_tokens.get(uid, 0)
    return _tiers.week_used(entry, "token_days", "tokens")


def _chat_pool(uid: str, tier: str) -> int:
    return int(_tiers.weekly_chat_tokens(tier) or 0)


def _chat_left(uid: str, tier: str) -> int:
    return max(0, _chat_pool(uid, tier) - _chat_used_week(uid))


def _record_chapter_charge(uid: str, tokens: int) -> int:
    """Record ONE chapter's tokens through the SAME path
    /chat uses (app.py's _record_chat_usage, bound or lazily
    resolved). Returns the caller's new weekly chat total.
    When the path is unbound (unit seams), a process-local
    counter keeps the accounting observable."""
    tokens = max(0, int(tokens or 0))
    fn = _dep("record_chat_usage")
    if fn is None:
        _local_chat_tokens[uid] = _local_chat_tokens.get(uid, 0) + tokens
        return _local_chat_tokens[uid]
    fn(uid, tokens)
    return _chat_used_week(uid)


def _token_balance(uid: str) -> int:
    try:
        import og_tokenpacks as _tp
        return _tp.balance(uid)
    except Exception:
        return 0


def _token_debit(uid: str, tokens: int):
    """Debit a token-funded chapter's actual tokens at the
    chat rate (1:1 — og_tokenpacks.settle_chat's shape)."""
    try:
        import og_tokenpacks as _tp
        _tp.debit(uid, int(tokens), "chat")
    except Exception as e:
        logger.warning(f"Book token debit failed: {e}")


def _tier_now(uid: str) -> str:
    fn = _dep("get_tier")
    try:
        return str(fn() or "free") if fn else "free"
    except Exception:
        return "free"


def _pause_words(tier: str) -> str:
    pool = int(_tiers.weekly_chat_tokens(tier) or 0)
    return (f"Paused — this week's chat pool ({pool:,} tokens "
            f"on your plan) is used up, so the book stops here "
            "instead of burning past it. Every finished "
            "chapter is saved. It picks up right where it left "
            "off when the pool refills (a little every day as "
            "your old days roll off the week), or sooner with "
            "token packs — say \"continue my book\" and, if "
            "you have a token balance, the next chapters burn "
            "tokens instead.")


# --- Pending approvals (process-local, the 7a shape) ------------------------------


_pending: Dict[str, Dict] = {}
_pending_lock = threading.Lock()


def _set_pending(uid: str, state: Dict):
    state = dict(state)
    state.setdefault("created", time.time())
    with _pending_lock:
        _pending[uid] = state


def _get_pending(uid: str) -> Optional[Dict]:
    if not uid:
        return None
    with _pending_lock:
        state = _pending.get(uid)
        if state and time.time() - float(state.get("created", 0)) \
                > _PENDING_TTL:
            del _pending[uid]
            return None
        return dict(state) if state else None


def _clear_pending(uid: str):
    with _pending_lock:
        _pending.pop(uid, None)


# --- Job records (Postgres when OG_MEMORY_DB_URL, else JSON — NEVER pruned) ---------

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo, pooled_connect as _pooled_connect
try:
    import psycopg
except Exception:  # psycopg missing — JSON backend is used
    psycopg = None

_jobs_lock = threading.Lock()
_jobs_mem: Dict[str, Dict] = {}
_gen_slots = threading.Semaphore(2)


def _key(uid: str) -> str:
    return hashlib.sha256(str(uid).encode()).hexdigest()[:32]


def _json_store_path() -> str:
    os.makedirs(STORE_DIR, exist_ok=True)
    return os.path.join(STORE_DIR, "book_jobs.json")


def _db_connect():
    conn = _pooled_connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_book_jobs ("
            "id TEXT PRIMARY KEY, owner TEXT, updated DOUBLE PRECISION,"
            " data TEXT)")
    conn.commit()
    return conn


def _store_put(rec: Dict) -> None:
    rec = dict(rec)
    rec["updated"] = time.time()
    with _jobs_lock:
        _jobs_mem[rec["id"]] = rec
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_book_jobs (id, owner, updated,"
                        " data) VALUES (%s, %s, %s, %s) ON CONFLICT"
                        " (id) DO UPDATE SET owner = EXCLUDED.owner,"
                        " updated = EXCLUDED.updated,"
                        " data = EXCLUDED.data",
                        (rec["id"], rec.get("owner", ""), rec["updated"],
                         json.dumps(rec)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Book job DB write failed: {e}")
    try:
        path = _json_store_path()
        data = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        data[rec["id"]] = rec     # no TTL prune — see docstring
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(f"Book job JSON write failed: {e}")


def _store_get(job_id: str) -> Optional[Dict]:
    with _jobs_lock:
        rec = _jobs_mem.get(job_id)
    if rec:
        return dict(rec)
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_book_jobs WHERE id = %s",
                        (job_id,))
                    row = cur.fetchone()
            if row:
                rec = json.loads(row[0])
                with _jobs_lock:
                    _jobs_mem[job_id] = rec
                return dict(rec)
        except Exception as e:
            logger.warning(f"Book job DB read failed: {e}")
    try:
        path = _json_store_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            rec = data.get(job_id)
            if rec:
                with _jobs_lock:
                    _jobs_mem[job_id] = rec
                return dict(rec)
    except Exception as e:
        logger.warning(f"Book job JSON read failed: {e}")
    return None


def _all_records() -> List[Dict]:
    found: Dict[str, Dict] = {}
    with _jobs_lock:
        for r in _jobs_mem.values():
            found[r["id"]] = dict(r)
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT data FROM og_book_jobs")
                    rows = cur.fetchall()
            for row in rows:
                rec = json.loads(row[0])
                found[rec["id"]] = rec
        except Exception as e:
            logger.warning(f"Book job DB scan failed: {e}")
    try:
        path = _json_store_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            for v in data.values():
                found[v["id"]] = v
    except Exception as e:
        logger.warning(f"Book job JSON scan failed: {e}")
    return list(found.values())


def _store_latest_for_owner(owner: str) -> Optional[Dict]:
    best = None
    for rec in _all_records():
        if rec.get("owner") != owner:
            continue
        if best is None or float(rec.get("updated", 0)) \
                > float(best.get("updated", 0)):
            best = rec
    return best


def list_jobs_for_owner(uid: str) -> List[Dict]:
    """All of the visitor's OWN books, newest first (Library
    + /books). Read-only."""
    owner = _key(uid)
    got = [r for r in _all_records() if r.get("owner") == owner]
    return sorted(got, key=lambda r: float(r.get("created", 0)),
                  reverse=True)


def purge_owner(uid: str):
    """Account deletion (Round 36): every book this visitor
    owns — memory, DB rows, JSON fallback — plus any pending
    outline. Other owners' books are never touched. (Cover
    files belong to the designer job store and are purged
    by og_designer.purge_owner.)"""
    if not uid:
        return
    _clear_pending(uid)
    owner = _key(uid)
    ids = [r["id"] for r in list_jobs_for_owner(uid)]
    if ids:
        with _jobs_lock:
            for jid in ids:
                _jobs_mem.pop(jid, None)
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM og_book_jobs WHERE owner = %s",
                        (owner,))
                conn.commit()
        except Exception as e:
            logger.warning(f"Book job DB purge failed: {e}")
    try:
        path = _json_store_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            kept = {k: v for k, v in data.items()
                    if v.get("owner") != owner}
            if len(kept) != len(data):
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(kept, f)
    except Exception as e:
        logger.warning(f"Book job JSON purge failed: {e}")


def _active_job(uid: str) -> Optional[Dict]:
    rec = _store_latest_for_owner(_key(uid))
    if rec and rec.get("state") in ("queued", "generating"):
        if time.time() - float(rec.get("updated", 0)) > _STALE_S:
            return None     # stale working record = cut off
        return rec
    return None


def _latest_book(uid: str) -> Optional[Dict]:
    return _store_latest_for_owner(_key(uid))


# --- Outline parsing ------------------------------------------------------------------


_TITLE_RE = re.compile(
    r"^\s*(?:#{1,3}\s*)?(?:\*\*)?TITLE\s*[:\-—]\s*(.+?)(?:\*\*)?\s*$",
    re.IGNORECASE)
_PREMISE_RE = re.compile(
    r"^\s*(?:#{1,3}\s*)?(?:\*\*)?PREMISE\s*[:\-—]\s*(.+?)\s*$",
    re.IGNORECASE)
_CHAPTER_RE = re.compile(
    r"^\s*(?:#{1,4}\s*)?(?:\*\*|__)?\s*CHAPTER\s+(\d{1,2})\s*"
    r"[:\-—.)]\s*(.+?)\s*$", re.IGNORECASE)


def _clean_text(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text or "")
    text = re.sub(r"(?m)^#{1,4}\s*", "", text)
    return text.replace("`", "").strip()


def _word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", text or "") if w])


def parse_book_outline(text: str) -> Optional[Dict]:
    """Parse a book outline out of an assistant message: a
    TITLE line, a PREMISE line, and CHAPTER n lines carrying
    "<chapter title> — <one-line beat>" (the beat splits on
    the first em dash / ' - ' after the title; a chapter line
    without a dash is title-only). Returns {title, premise,
    chapters: [{n, title, beat}]} or None when the text isn't
    an outline (no title, fewer than 3 chapters)."""
    if not text:
        return None
    title = ""
    premise = ""
    chapters: Dict[int, Dict] = {}
    for line in str(text).splitlines():
        m = _TITLE_RE.match(line)
        if m and not title:
            title = _clean_text(m.group(1)).strip('"“”').strip()
            continue
        m = _PREMISE_RE.match(line)
        if m and not premise:
            premise = _clean_text(m.group(1))
            continue
        m = _CHAPTER_RE.match(line)
        if m:
            n = int(m.group(1))
            rest = _clean_text(m.group(2))
            beat = ""
            for sep in (" — ", " – ", " - "):
                if sep in rest:
                    rest, beat = rest.split(sep, 1)
                    break
            chapters[n] = {"n": n, "title": rest.strip()[:120],
                           "beat": beat.strip()[:300]}
    if not title or len(chapters) < _MIN_CHAPTERS:
        return None
    ordered = [chapters[k] for k in sorted(chapters)]
    for i, ch in enumerate(ordered, start=1):
        ch["n"] = i
    return {"title": title[:120],
            "premise": premise[:400], "chapters": ordered}


# --- History capture (what was shown is what ships) -------------------------------------


def _history(uid: str) -> List[Dict]:
    fn = _dep("load_history")
    if fn is None:
        return []
    try:
        return fn(uid) or []
    except Exception as e:
        logger.warning(f"Book history read failed: {e}")
        return []


def _capture_from_history(uid: str, pending: Dict) -> Optional[Dict]:
    hist = _history(uid)
    start = int(pending.get("hist_len", 0))
    for entry in reversed(hist[start:]):
        if not isinstance(entry, dict) \
                or entry.get("role") != "assistant":
            continue
        parsed = parse_book_outline(str(entry.get("content", "")))
        if parsed:
            return parsed
    if pending.get("stage") == "await_approval":
        for entry in reversed(hist):
            if not isinstance(entry, dict) \
                    or entry.get("role") != "assistant":
                continue
            parsed = parse_book_outline(str(entry.get("content", "")))
            if parsed:
                return parsed
    return None


def _ensure_captured(uid: str, pending: Dict) -> Optional[Dict]:
    if pending.get("chapters"):
        return pending
    parsed = _capture_from_history(uid, pending)
    if not parsed:
        return None
    pending = dict(pending)
    pending.update({
        "stage": "await_approval",
        "title": parsed["title"], "premise": parsed["premise"],
        "chapters": parsed["chapters"]})
    _set_pending(uid, pending)
    return pending


# --- Chapter generation seam (THE spend seam — stubbed in tests) ------------------------


class _BookError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def build_chapter_prompt(rec: Dict, spec: Dict, context: Dict) -> List[Dict]:
    """The messages for ONE chapter call. Continuity shape:
    system carries the assignment; user carries the book's
    title + premise, the FULL outline (every chapter title +
    beat), the target chapter's own beat, the running summary
    of everything written so far, and the previous chapter's
    tail — in that order."""
    outline_lines = "\n".join(
        f"CHAPTER {c['n']}: {c['title']}"
        + (f" — {c['beat']}" if c.get("beat") else "")
        for c in rec.get("outline", []))
    target = int(context.get("target_words") or _DEFAULT_CH_WORDS)
    user = (
        f"BOOK: {rec.get('title', '')}\n"
        f"PREMISE: {rec.get('premise', '')}\n\n"
        f"FULL OUTLINE:\n{outline_lines}\n\n"
        f"NOW WRITE CHAPTER {spec['n']}: {spec['title']}"
        + (f"\nThis chapter's beat: {spec['beat']}"
           if spec.get("beat") else "")
        + f"\nTarget length: about {target} words "
          f"({_MIN_CH_WORDS}-{_MAX_CH_WORDS} is the allowed band; "
          f"aim for {target}). Write ONLY the chapter's prose "
          f"— no title line, no chapter heading, no notes.")
    summary = str(context.get("summary") or "").strip()
    if summary:
        user += f"\n\nTHE STORY SO FAR (running summary):\n{summary}"
    tail = str(context.get("prev_tail") or "").strip()
    if tail:
        user += (f"\n\nTHE PREVIOUS CHAPTER ENDED LIKE THIS "
                 f"(continue seamlessly from it):\n…{tail}")
    return [
        {"role": "system",
         "content": "You are writing one chapter of a book, "
                    "following the outline and continuity you "
                    "are given exactly. Plain prose, no "
                    "headings, no commentary about the task."},
        {"role": "user", "content": user},
    ]


def _chat_completion(messages: List[Dict]):
    """ONE chat-model call: POST chat/completions; return
    (text, tokens_actually_consumed). Raises _BookError on
    any failure. No retries — a retry is a second billed
    call the visitor never approved."""
    import httpx
    key = _api_key()
    if not key:
        raise _BookError("auth", "no chat key configured")
    try:
        with httpx.Client(timeout=240) as client:
            r = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": _MODEL, "messages": messages,
                      "temperature": 0.85, "max_tokens": 2600})
    except Exception as e:
        raise _BookError("network",
                         f"chat service unreachable: {e}")
    if r.status_code != 200:
        kind = "auth" if r.status_code in (401, 403) else \
            "rate" if r.status_code == 429 else "http"
        raise _BookError(
            kind, f"chat service answered {r.status_code}: "
                  f"{(r.text or '')[:160]}")
    try:
        data = r.json()
        text = ((data.get("choices") or [{}])[0].get("message")
                or {}).get("content", "") or ""
        usage = data.get("usage") or {}
        tokens = int(usage.get("total_tokens") or 0)
    except Exception as e:
        raise _BookError("bad_reply", f"unreadable reply: {e}")
    text = text.strip()
    if _word_count(text) < 40:
        raise _BookError("bad_reply",
                         "the chapter came back nearly empty")
    if tokens <= 0:
        est = _dep("estimate_tokens")
        tokens = 0
        if est is not None:
            try:
                tokens = int(est(None, text) or 0)
            except Exception:
                tokens = 0
        if tokens <= 0:
            joined = " ".join(m.get("content", "")
                              for m in messages)
            tokens = max(1, (len(joined) + len(text)) // 4)
    return text, tokens


def _generate_chapter(uid: str, rec: Dict, spec: Dict,
                      context: Dict):
    """Generate ONE chapter: build the continuity prompt and
    make the one chat call. Returns (text, tokens)."""
    messages = build_chapter_prompt(rec, spec, context)
    return _chat_completion(messages)


# --- Running summary (extractive, local, free) -------------------------------------------


def _chapter_digest(n: int, title: str, text: str) -> str:
    flat = " ".join(str(text or "").split())
    head = flat[:260]
    tail = flat[-160:] if len(flat) > 420 else ""
    dig = f"Ch {n} \"{title}\": {head}"
    if tail:
        dig += f" … ends: {tail}"
    return dig


def _append_summary(summary: str, n: int, title: str,
                    text: str) -> str:
    out = (str(summary or "").rstrip() + "\n"
           + _chapter_digest(n, title, text)).strip()
    if len(out) > _SUMMARY_CAP:
        out = out[-_SUMMARY_CAP:]
        cut = out.find("\n")
        if cut > 0:
            out = out[cut + 1:]
    return out


def _rebuild_summary(rec: Dict) -> str:
    summary = ""
    for ch in rec.get("chapters", []):
        summary = _append_summary(summary, ch["n"], ch["title"],
                                  ch.get("text", ""))
    return summary


# --- Cover seam (og_designer.make_design, poster spec) -------------------------------------


def _generate_cover(uid: str, tier: str, rec: Dict) -> Dict:
    """ONE cover through the designer seam: a poster design
    whose topic is the premise and whose composited title is
    the book's title, verbatim. og_designer charges the
    `image` kind itself, success-only, exactly once per call
    — and this seam is called at most once per book (the
    persisted cover flags in _finish_book). Returns the
    designer's outcome dict."""
    import og_designer as _designer
    spec = {"design_kind": "poster",
            "title": rec.get("title", ""),
            "subtitle": "",
            "topic": rec.get("premise", "") or rec.get("title", ""),
            "idea": "", "top": "", "bottom": ""}
    return _designer.make_design(uid, tier, spec)


def _finish_book(uid: str, rec: Dict) -> Dict:
    """Last chapter is in: try the cover ONCE, mark done,
    notify. A failed/capped cover never sinks the book."""
    if not rec.get("cover_url") and not rec.get("cover_failed"):
        try:
            res = _generate_cover(uid, _tier_now(uid), rec)
        except Exception as e:
            logger.warning(f"Book cover failed: {e}")
            res = {"ok": False, "error": str(e)[:200]}
        if res.get("ok"):
            rec["cover_url"] = res.get("design_url", "")
            rec["cover_file"] = res.get("file", "")
            rec["cover_job_id"] = res.get("id", "")
            rec["cover_error"] = ""
        else:
            rec["cover_failed"] = True
            rec["cover_error"] = str(
                res.get("error", "the cover didn't come out"))[:300]
    total_words = sum(int(c.get("words", 0))
                      for c in rec.get("chapters", []))
    detail = (f"\"{rec.get('title', '')}\" is DONE — "
              f"{len(rec.get('chapters', []))} chapters, "
              f"{total_words:,} words. It's on your Library "
              f"shelf under Books.")
    if rec.get("cover_failed"):
        detail += (" The cover didn't come out ("
                   + rec.get("cover_error", "") + ") — the "
                   "book itself is complete.")
    rec = _update(rec, state="done", detail=detail)
    try:
        import og_notify as _notify
        _notify.record(
            uid, "book_done",
            f"Your book is ready: \"{rec.get('title', '')}\"",
            detail,
            target={"view": "library", "kind": "book",
                    "id": rec["id"]})
    except Exception:
        pass
    return rec


# --- Narration (grounded result blocks the persona voices) ----------------------------------


def _result(tag: str, title: str, body: str, href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body, "href": href}]


def _down_body() -> List[Dict]:
    return _result(
        "BOOK: DOWN", "📖 Book — press dark",
        "The book press is dark right now (not switched on). "
        "Tell the visitor plainly, in persona — no fake "
        "promises: he can still plan the book with them right "
        "here in chat, outline and all; the written chapters "
        "wait until the press lights up.")


# --- Claim table ------------------------------------------------------------------------------


_YES_WORDS = {
    "yes", "yeah", "yep", "yup", "ya", "ok", "okay", "sure",
    "approve", "approved", "go", "do it", "write it",
    "lets go", "let's go", "yes please", "yes write it",
    "yes do it", "👍",
}
_NO_WORDS = {
    "no", "nope", "nah", "decline", "declined", "scrap it",
    "never mind", "nevermind", "forget it", "cancel", "stop",
    "don't", "dont", "no thanks",
}
_BOOK_VERB = re.compile(
    r"\b(write|start|make|create|author|pen)\b", re.I)
_BOOK_NOUN = re.compile(r"\b(books?|novel)\b", re.I)
_BOOK_ABOUT = re.compile(r"\bbook\s+(?:about|on)\s+(.+)$", re.I)
_ABOUT = re.compile(r"\b(?:about|on)\s+(.+)$", re.I)
_CONTINUE = re.compile(
    r"\b(continue|finish|resume|complete)\b[^.?!]{0,30}"
    r"\b(book|it)\b|\bcontinue my book\b", re.I)
_REWRITE = re.compile(
    r"\b(rewrite|redo|regenerate|re-write)\b[^.?!]{0,20}"
    r"\bchapter\s+(\d{1,2})\b", re.I)
_N_CHAPTERS = re.compile(r"\b(\d{1,2})\s*(?:-\s*)?chapters?\b", re.I)
_CH_WORDS = re.compile(
    r"\b(\d{3,4})\s*(?:-\s*)?words?\s*(?:per|a|each)\s+chapter\b",
    re.I)


def _norm(message: str) -> str:
    return " ".join(str(message or "").lower().split()).strip(" .!")


def _chapter_count_hint(raw: str) -> int:
    m = _N_CHAPTERS.search(raw or "")
    if m:
        return max(_MIN_CHAPTERS,
                   min(_MAX_CHAPTERS, int(m.group(1))))
    return _DEFAULT_CHAPTERS


def _chapter_words_hint(raw: str) -> int:
    m = _CH_WORDS.search(raw or "")
    if m:
        return max(_MIN_CH_WORDS,
                   min(_MAX_CH_WORDS, int(m.group(1))))
    low = str(raw or "").lower()
    if re.search(r"\bshort chapters?\b|\bshorter chapters?\b", low):
        return 800
    if re.search(r"\blong chapters?\b|\blonger chapters?\b", low):
        return 1200
    return _DEFAULT_CH_WORDS


def _clean_topic(topic: str) -> str:
    topic = str(topic or "").strip()
    # A trailing chapter-count clause ("… with 12 chapters")
    # is structure, not premise.
    topic = re.sub(r"\s+(?:with|in)\s+\d{1,2}\s+chapters?\b.*$",
                   "", topic, flags=re.I)
    topic = re.sub(r"[\s,]+\d{1,2}\s+chapters?\s*$", "",
                   topic, flags=re.I)
    return topic.strip().strip(".!")[:200]


def _browser_has_pending(uid: str) -> bool:
    try:
        import og_browser as _browser
        pend = _browser._get_pending(uid)
        return bool(pend and pend.get("kind") == "action")
    except Exception:
        return False


def _other_pending_newer(uid: str, book_pending: Dict) -> bool:
    """When a video / song / podcast approval is ALSO parked,
    the bare YES/NO belongs to whichever ask the visitor saw
    LAST (the newest pending) — the 7a cross-module rule."""
    try:
        created = float(book_pending.get("created", 0))
        import og_video as _video
        vpend = _video._get_pending(uid)
        if vpend and float(vpend.get("created", 0)) > created:
            return True
        import og_songs as _songs
        spend = _songs._get_pending(uid)
        if spend and float(spend.get("created", 0)) > created:
            return True
        import og_podcast as _podcast
        ppend = _podcast._get_pending(uid)
        if ppend and float(ppend.get("created", 0)) > created:
            return True
    except Exception:
        pass
    return False


def _claim_book(message: str, uid: str) -> Optional[Dict]:
    if not uid or not enabled():
        return None
    raw = str(message or "")
    low = raw.lower()
    norm = _norm(raw)
    m = _REWRITE.search(low)
    if m:
        return {"kind": "rewrite", "chapter": int(m.group(2))}
    if _CONTINUE.search(low) and _BOOK_NOUN.search(low):
        return {"kind": "continue"}
    pending = _get_pending(uid)
    if pending is not None:
        stage = pending.get("stage", "")
        if stage == "await_topic":
            if norm in _NO_WORDS:
                return {"kind": "decline"}
            m2 = _ABOUT.search(raw.strip())
            if _BOOK_NOUN.search(low) and m2:
                return {"kind": "topic",
                        "topic": _clean_topic(m2.group(1)),
                        "chapters_wanted": _chapter_count_hint(raw),
                        "chapter_words": _chapter_words_hint(raw)}
            if len(norm) >= 2:
                return {"kind": "topic",
                        "topic": _clean_topic(raw),
                        "chapters_wanted": _chapter_count_hint(raw),
                        "chapter_words": _chapter_words_hint(raw)}
            return None
        # A fresh ask with its own topic replaces the parked
        # outline instead of approving it by accident.
        if _BOOK_NOUN.search(low) and _ABOUT.search(raw):
            m2 = _ABOUT.search(raw.strip())
            return {"kind": "replace",
                    "topic": _clean_topic(m2.group(1)),
                    "chapters_wanted": _chapter_count_hint(raw),
                    "chapter_words": _chapter_words_hint(raw)}
        if norm in _YES_WORDS:
            if _browser_has_pending(uid):
                return None
            if _other_pending_newer(uid, pending):
                return None
            return {"kind": "approve"}
        if norm in _NO_WORDS:
            if _browser_has_pending(uid):
                return None
            if _other_pending_newer(uid, pending):
                return None
            return {"kind": "decline"}
        if re.search(r"\b(change|rewrite|redo|make it|instead|"
                     r"add|remove|darker|funnier|longer|shorter|"
                     r"chapter|title|premise)\b", low) \
                and len(low) < 300:
            return {"kind": "revise", "note": raw.strip()[:300]}
        return None
    # No pending — bare approvals/declines claim nothing.
    m = _BOOK_ABOUT.search(raw.strip())
    if m and _BOOK_VERB.search(low[:m.start() + 5]):
        return {"kind": "new", "topic": _clean_topic(m.group(1)),
                "chapters_wanted": _chapter_count_hint(raw),
                "chapter_words": _chapter_words_hint(raw)}
    verb = _BOOK_VERB.search(low)
    noun = _BOOK_NOUN.search(low)
    want = re.search(r"\bi want\s+(?:a\s+|an\s+)?(?:book|novel)\b",
                     low)
    vpos = verb.start() if verb else (want.start() if want else None)
    if noun and vpos is not None and noun.start() > vpos \
            and noun.start() - vpos < 60:
        topic = ""
        m2 = _ABOUT.search(raw.strip())
        if m2:
            topic = _clean_topic(m2.group(1))
        return {"kind": "new", "topic": topic,
                "chapters_wanted": _chapter_count_hint(raw),
                "chapter_words": _chapter_words_hint(raw)}
    return None


# --- Flow results ---------------------------------------------------------------------------------


def _outline_instruction(topic: str, chapters_wanted: int,
                         chapter_words: int) -> str:
    return (
        f"The visitor wants a BOOK about: {topic}. Do this in "
        f"your reply, in persona, right now — OUTLINE FIRST, "
        f"the chapters come after they approve: (1) Draft the "
        f"book's outline: a title, a one-line premise, and "
        f"exactly {chapters_wanted} chapters, each with a "
        f"chapter title and a one-line beat (what happens in "
        f"it). Format it EXACTLY like this: a first line "
        f"'TITLE: <the book title>', a second line 'PREMISE: "
        f"<one line>', then one line per chapter as 'CHAPTER "
        f"1: <chapter title> — <one-line beat>', 'CHAPTER 2: "
        f"...', and so on. Each chapter will be written at "
        f"about {chapter_words} words when they approve. "
        f"(2) After the outline, close with the approval ask, "
        f"in persona: name the title, the chapter count and "
        f"the rough total ({chapters_wanted} chapters x "
        f"~{chapter_words} words), and tell them replying YES "
        f"gets it WRITTEN for real, chapter by chapter into "
        f"their Library — and NO scraps it. Outline edits are "
        f"welcome ('make chapter 3 darker'). Do NOT say any "
        f"chapter exists yet; nothing is written before "
        f"their YES.")


def _start_book(uid: str, topic: str, chapters_wanted: int,
                chapter_words: int) -> List[Dict]:
    hist_len = len(_history(uid))
    _set_pending(uid, {"stage": "await_capture", "topic": topic,
                       "chapters_wanted": int(chapters_wanted),
                       "chapter_words": int(chapter_words),
                       "hist_len": hist_len})
    return _result("BOOK: WRITE", "📖 Book — outline first",
                   _outline_instruction(topic, chapters_wanted,
                                         chapter_words))


def _new_job(uid: str, pending: Dict) -> Dict:
    job_id = uuid.uuid4().hex[:16]
    return {
        "id": job_id, "owner": _key(uid),
        "title": pending.get("title", "OG's Book"),
        "premise": pending.get("premise", ""),
        "topic": pending.get("topic", ""),
        "outline": pending.get("chapters", []),
        "chapters": [],
        "summary": "",
        "chapter_words": int(pending.get("chapter_words")
                             or _DEFAULT_CH_WORDS),
        "tokens_used": 0,
        "fund_mode": "",
        "state": "queued",
        "detail": "In line — the press is warming up.",
        "created": time.time(), "updated": time.time(),
        "cover_url": "", "cover_file": "", "cover_job_id": "",
        "cover_failed": False, "cover_error": "",
        "error": "",
    }


def _update(rec: Dict, **kw) -> Dict:
    rec.update(kw)
    rec["updated"] = time.time()
    _store_put(rec)
    return rec


def _slug(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(title or "book")).strip("-")
    return (slug[:60] or "book").lower()


def _record_problem(uid: str, rec: Dict, kind: str, err: str):
    try:
        import og_fixqueue as _fq
        _fq.record_problem(
            uid, "book", f"book:{rec.get('id', '')}",
            "book-chapter", "A book chapter failed",
            f"Chapter writing for \"{rec.get('title', '')}\" "
            f"failed ({kind}): {err[:200]}",
            fix={"kind": "owner_steps",
                 "summary": "A chapter didn't finish. Every "
                            "finished chapter is saved.",
                 "steps": [
                     "Every finished chapter is saved — "
                     "nothing already written was lost.",
                     "Say \"continue my book\" and OG picks "
                     "up at the next unwritten chapter.",
                 ]})
    except Exception:
        pass


_FAIL_DETAILS = {
    "auth": "The writing key got rejected — the press needs "
            "a fix on OG's side. Every finished chapter is "
            "saved.",
    "rate": "The writing service is slammed right now. Every "
            "finished chapter is saved — say \"continue my "
            "book\" in a bit and it picks up where it stopped.",
    "network": "The writing service didn't answer in time. "
               "Every finished chapter is saved.",
    "bad_reply": "A chapter came back unusable, so the press "
                 "stopped instead of shipping it. Every "
                 "finished chapter is saved.",
    "http": "A chapter didn't make it. Every finished "
            "chapter is saved.",
}


def _run_job(job_id: str, uid: str) -> None:
    """Write the remaining chapters of one book, one durable
    save per chapter. Resumable by construction: everything
    the loop needs is re-read from the store record, and the
    loop starts at the first unwritten chapter."""
    rec = _store_get(job_id)
    if rec is None:
        return
    tier = _tier_now(uid)
    try:
        with _gen_slots:
            outline = rec.get("outline", [])
            total = len(outline)
            while len(rec.get("chapters", [])) < total:
                done_n = len(rec["chapters"])
                spec = outline[done_n]
                # THE FENCE — re-read before EVERY chapter.
                funded = rec.get("fund_mode") == "tokens"
                if not funded and _chat_left(uid, tier) <= 0:
                    _update(rec, state="paused_caps",
                            detail=_pause_words(tier))
                    return
                if funded and _token_balance(uid) <= 0:
                    _update(rec, state="paused_caps",
                            fund_mode="",
                            detail=_pause_words(tier))
                    return
                _update(rec, state="generating",
                        detail=f"Writing chapter "
                               f"{spec['n']} of {total} — "
                               f"\"{spec['title']}\"…")
                prev = rec["chapters"][-1] if rec["chapters"] \
                    else None
                context = {
                    "summary": rec.get("summary", ""),
                    "prev_tail": (prev.get("text", "")
                                  [-_TAIL_CHARS:] if prev else ""),
                    "target_words": rec.get("chapter_words")
                    or _DEFAULT_CH_WORDS,
                }
                text, tokens = _generate_chapter(uid, rec, spec,
                                                 context)
                # Charge through /chat's own record path —
                # the tokens this call actually consumed.
                _record_chapter_charge(uid, tokens)
                if funded:
                    _token_debit(uid, tokens)
                chapter = {"n": spec["n"], "title": spec["title"],
                           "beat": spec.get("beat", ""),
                           "text": text,
                           "words": _word_count(text),
                           "tokens": int(tokens)}
                rec["chapters"] = rec.get("chapters", []) + [chapter]
                rec["summary"] = _append_summary(
                    rec.get("summary", ""), spec["n"],
                    spec["title"], text)
                rec["tokens_used"] = int(rec.get("tokens_used", 0)) \
                    + int(tokens)
                # DURABLE SAVE the moment the chapter completes.
                _update(rec, state="generating",
                        detail=f"Chapter {spec['n']} of {total} "
                               f"done — \"{spec['title']}\" "
                               f"({chapter['words']:,} words).")
                rec = _store_get(job_id) or rec
            _finish_book(uid, rec)
    except _BookError as e:
        logger.warning(f"Book job {job_id} failed ({e.kind}): {e}")
        _record_problem(uid, rec, e.kind, str(e))
        try:
            _update(rec, state="failed", error=str(e)[:300],
                    detail=_FAIL_DETAILS.get(
                        e.kind, _FAIL_DETAILS["http"]))
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"Book job {job_id} failed: {e}")
        _record_problem(uid, rec, type(e).__name__, str(e))
        try:
            _update(rec, state="failed", error=str(e)[:300],
                    detail=_FAIL_DETAILS["http"])
        except Exception:
            pass


def _start_job_thread(rec: Dict, uid: str):
    thread = threading.Thread(target=_run_job,
                              args=(rec["id"], uid), daemon=True)
    thread.start()


def _approve(uid: str, tier: str):
    """Shared resolution for the chat YES and the card door.
    Returns (ok, message, rec)."""
    pending = _get_pending(uid)
    if pending is None:
        return False, "Nothing is waiting on approval — it " \
            "expired or was already answered.", None
    pending = _ensure_captured(uid, pending) or pending
    if not pending.get("chapters"):
        _clear_pending(uid)
        return False, "I couldn't find that outline in the " \
            "thread anymore, so there's nothing to write. Ask " \
            "me for the book again and we'll redo it.", None
    if _active_job(uid):
        _clear_pending(uid)
        return False, "A book is already on the press for " \
            "you — let that one finish first.", None
    if _chat_left(uid, tier) <= 0 and _token_balance(uid) <= 0:
        _clear_pending(uid)
        return False, "You're at this week's chat cap for " \
            "your plan and your token balance is empty, so " \
            "the press can't start. The pool refills as your " \
            "old days roll off the week — the outline itself " \
            "stays right here in chat.", None
    rec = _new_job(uid, pending)
    if _chat_left(uid, tier) <= 0:
        # Pool dry but the balance covers it: chapters burn
        # tokens instead (named up front, never silent).
        rec["fund_mode"] = "tokens"
    _clear_pending(uid)
    _store_put(rec)
    _start_job_thread(rec, uid)
    return True, f"Locked in — writing \"{rec['title']}\" now.", rec


def _decline(uid: str):
    pending = _get_pending(uid)
    if pending is None:
        return False, "Nothing was waiting — no book is pending."
    _clear_pending(uid)
    return True, "Scrapped — nothing written, nothing charged."


def _resume(uid: str, tier: str, rec: Dict):
    """Resume one incomplete book. Returns (ok, message, rec):
    pool room => normal resume; pool dry + token balance =>
    token-funded resume (the visitor's explicit continue is
    what authorizes burning packs); otherwise the book stays
    paused_caps and the message says so plainly."""
    if rec.get("state") == "done":
        return True, f"\"{rec.get('title', '')}\" is already " \
            "finished — it's on your Library shelf.", rec
    if rec.get("state") in ("queued", "generating") \
            and time.time() - float(rec.get("updated", 0)) \
            <= _STALE_S:
        return False, "That book is on the press right now — " \
            "it's still writing.", rec
    if _chat_left(uid, tier) > 0:
        rec = _update(rec, fund_mode="", state="queued",
                      detail="Back on the press — picking up "
                             "at the next unwritten chapter.")
    elif _token_balance(uid) > 0:
        rec = _update(rec, fund_mode="tokens", state="queued",
                      detail="Back on the press — burning "
                             "token packs for the remaining "
                             "chapters (your weekly pool is "
                             "still dry).")
    else:
        rec = _update(rec, state="paused_caps",
                      detail=_pause_words(tier))
        return False, "Still paused: " + _pause_words(tier), rec
    _start_job_thread(rec, uid)
    return True, f"Back on the press — \"{rec.get('title', '')}\" " \
        "picks up at the next unwritten chapter.", rec


def _rewrite_chapter(uid: str, tier: str, n: int) -> List[Dict]:
    rec = _latest_book(uid)
    if rec is None:
        return _result(
            "BOOK: NO-BOOK", "📖 Book — nothing to rewrite",
            "The visitor asked to rewrite a chapter but has "
            "no book yet. Tell them, in persona: there's no "
            "book on the shelf — ask for one first ('write a "
            "book about …'), approve the outline, and the "
            "chapters land in the Library.")
    chapters = rec.get("chapters", [])
    target = next((c for c in chapters if int(c.get("n", 0)) == n),
                  None)
    if target is None:
        return _result(
            "BOOK: NO-CHAPTER", "📖 Book — no such chapter",
            f"The visitor asked to rewrite chapter {n} of "
            f"\"{rec.get('title', '')}\", but only "
            f"{len(chapters)} chapter(s) are written so far. "
            "Tell them exactly that, in persona — no chapter "
            "was generated and nothing was charged.")
    if rec.get("state") in ("queued", "generating") \
            and time.time() - float(rec.get("updated", 0)) \
            <= _STALE_S:
        return _result(
            "BOOK: BUSY", "📖 Book — on the press",
            "The book is ON THE PRESS right now. Tell the "
            "visitor, in persona: let the current writing "
            "finish (or pause) before rewriting a chapter — "
            "nothing was generated.")
    funded = _chat_left(uid, tier) <= 0
    if funded and _token_balance(uid) <= 0:
        return _result(
            "BOOK: CAPPED", "📖 Book — weekly wall",
            "The visitor is at this week's chat cap and has "
            "no token balance, so the rewrite can't run. Tell "
            "them plainly, in persona: " + _pause_words(tier)
            + " The chapter on the shelf is unchanged.")
    outline = rec.get("outline", [])
    spec = next((c for c in outline if int(c.get("n", 0)) == n),
                {"n": n, "title": target.get("title", ""),
                 "beat": target.get("beat", "")})
    idx = chapters.index(target)
    prev = chapters[idx - 1] if idx > 0 else None
    summary = ""
    for ch in chapters[:idx]:
        summary = _append_summary(summary, ch["n"], ch["title"],
                                  ch.get("text", ""))
    context = {"summary": summary,
               "prev_tail": (prev.get("text", "")[-_TAIL_CHARS:]
                             if prev else ""),
               "target_words": rec.get("chapter_words")
               or _DEFAULT_CH_WORDS}
    try:
        text, tokens = _generate_chapter(uid, rec, spec, context)
    except _BookError as e:
        _record_problem(uid, rec, e.kind, str(e))
        return _result(
            "BOOK: REWRITE-FAILED", "📖 Book — rewrite failed",
            "The rewrite FAILED honestly: "
            + _FAIL_DETAILS.get(e.kind, _FAIL_DETAILS["http"])
            + " Tell the visitor exactly that, in persona — "
            "the chapter on the shelf is unchanged and "
            "nothing was charged for the failed take.")
    _record_chapter_charge(uid, tokens)
    if funded:
        _token_debit(uid, tokens)
    chapters[idx] = {"n": n, "title": spec["title"],
                     "beat": spec.get("beat", ""),
                     "text": text, "words": _word_count(text),
                     "tokens": int(tokens)}
    rec["chapters"] = chapters
    rec["summary"] = _rebuild_summary(rec)
    rec["tokens_used"] = int(rec.get("tokens_used", 0)) + int(tokens)
    _store_put(rec)
    return _result(
        "BOOK: CHAPTER-REWRITTEN", "📖 Book — chapter rewritten",
        f"Chapter {n} of \"{rec.get('title', '')}\" was "
        f"REWRITTEN for real ({_word_count(text):,} words, "
        f"{int(tokens):,} chat tokens charged"
        + (", token-funded past the weekly wall" if funded else "")
        + "). The new chapter replaced the old one on the "
        "shelf — same book, same outline, the Library reader "
        "and the .md download now serve the new text. Tell "
        "the visitor, in persona: chapter "
        f"{n} is redone and back on the shelf.")


def book_results(job: Dict, message: str, uid: str) -> List[Dict]:
    kind = job.get("kind", "")
    tier = _tier_now(uid)
    if kind in ("new", "topic", "replace"):
        if kind == "replace":
            _clear_pending(uid)
        if _active_job(uid):
            return _result(
                "BOOK: BUSY", "📖 Book — one on the press",
                "A book is ALREADY being written for this "
                "visitor. Tell them, in persona: one at a "
                "time — the one on the press lands in the "
                "Library when it's done, then they can line "
                "up the next.")
        topic = job.get("topic", "")
        if not topic:
            _set_pending(uid, {"stage": "await_topic",
                               "hist_len": len(_history(uid))})
            return _result(
                "BOOK: NEED-TOPIC", "📖 Book — needs a premise",
                "The visitor wants a book but didn't say what "
                "about. Ask them, in persona, in one line: "
                "what's the book about? Whatever they answer "
                "next becomes the premise — no need for them "
                "to repeat 'book'.")
        return _start_book(uid, topic,
                           job.get("chapters_wanted")
                           or _DEFAULT_CHAPTERS,
                           job.get("chapter_words")
                           or _DEFAULT_CH_WORDS)
    if kind == "approve":
        ok, msg, rec = _approve(uid, tier)
        if not ok:
            return _result("BOOK: APPROVE-FAILED",
                           "📖 Book — not started", msg +
                           " Tell the visitor exactly that, in "
                           "persona — plainly, no dressing it up.")
        total = len(rec.get("outline", []))
        fund_line = (
            " Heads-up to voice honestly, in persona: their "
            "weekly chat pool is dry, so the chapters burn "
            "their token-pack balance instead (named at "
            "approval, per chapter, actual tokens only)."
            if rec.get("fund_mode") == "tokens" else "")
        body = (
            f"APPROVED — the press is WRITING "
            f"\"{rec['title']}\" now: {total} chapters at "
            f"about {rec.get('chapter_words', _DEFAULT_CH_WORDS)} "
            f"words each, one chapter at a time, every chapter "
            f"saved the moment it lands. Tell the visitor, in "
            f"persona: locked in, it's on the press — progress "
            f"shows on the book card right here ('Chapter N of "
            f"{total} done') and the finished book lands in "
            f"their Library under Books with a cover, a reader "
            f"and a download. They don't need to do anything."
            + fund_line)
        return _result("BOOK: WRITING", "📖 Book — on the press",
                       body)
    if kind == "decline":
        ok, msg = _decline(uid)
        return _result(
            "BOOK: DECLINED", "📖 Book — scrapped",
            msg + " Tell the visitor that, in persona, briefly — "
            "the outline itself stays in the thread if they "
            "want to read it, and a fresh ask starts a fresh "
            "book.")
    if kind == "revise":
        pending = _get_pending(uid) or {}
        topic = pending.get("topic", "") or "the same book"
        cw = int(pending.get("chapters_wanted")
                 or _DEFAULT_CHAPTERS)
        cwords = int(pending.get("chapter_words")
                     or _DEFAULT_CH_WORDS)
        _set_pending(uid, {"stage": "await_capture", "topic": topic,
                           "chapters_wanted": cw,
                           "chapter_words": cwords,
                           "hist_len": len(_history(uid))})
        body = (
            f"The visitor wants the pending book outline "
            f"CHANGED before they approve: "
            f"\"{job.get('note', '')}\". Redraft the outline "
            f"NOW in your reply, in persona, with the change "
            f"applied, in the EXACT format: first line 'TITLE: "
            f"<title>', second line 'PREMISE: <one line>', "
            f"then 'CHAPTER 1: <title> — <beat>' lines "
            f"({cw} chapters). Close with the same approval "
            f"ask (title, chapter count, rough total words, "
            f"YES writes it / NO scraps it).")
        return _result("BOOK: REWRITE-OUTLINE",
                       "📖 Book — outline rewrite", body)
    if kind == "continue":
        rec = _latest_book(uid)
        if rec is None:
            return _result(
                "BOOK: NO-BOOK", "📖 Book — nothing to continue",
                "The visitor asked to continue a book but has "
                "none on the shelf. Tell them, in persona: no "
                "book exists yet — 'write a book about …' "
                "starts one, outline first, chapters after "
                "their YES.")
        if rec.get("state") == "done":
            return _result(
                "BOOK: DONE", "📖 Book — already finished",
                f"\"{rec.get('title', '')}\" is already "
                "finished — it's on the Library shelf under "
                "Books, with the reader and the .md download. "
                "Tell the visitor exactly that, in persona.")
        ok, msg, rec = _resume(uid, tier, rec)
        tag = "BOOK: WRITING" if ok else "BOOK: PAUSED"
        title = "📖 Book — back on the press" if ok \
            else "📖 Book — still paused"
        return _result(
            tag, title,
            msg + " Tell the visitor exactly that, in persona. "
            "Progress shows on the book card; the finished "
            "book lands in the Library under Books.")
    if kind == "rewrite":
        return _rewrite_chapter(uid, tier,
                                int(job.get("chapter", 0)))
    return _down_body()


# --- Views + markdown ---------------------------------------------------------------------


def _book_view(rec: Optional[Dict]) -> Optional[Dict]:
    if not rec:
        return None
    state = rec.get("state", "")
    if state in ("queued", "generating") \
            and time.time() - float(rec.get("updated", 0)) > _STALE_S:
        state_view = state
        detail = ("That writing run got cut off (the server "
                  "restarted mid-book) — every finished chapter "
                  "is saved. Say \"continue my book\" and it "
                  "picks up at the next unwritten chapter.")
    else:
        state_view = state
        detail = rec.get("detail", "")
    chapters = [{"n": int(c.get("n", 0)),
                 "title": c.get("title", ""),
                 "words": int(c.get("words", 0) or 0)}
                for c in rec.get("chapters", [])]
    total = len(rec.get("outline", []))
    out = {"id": rec["id"], "title": rec.get("title", ""),
           "premise": rec.get("premise", ""),
           "status": state_view, "state": state_view,
           "detail": detail,
           "chapters": chapters,
           "chapters_done": len(chapters),
           "chapters_total": total,
           "words": sum(c["words"] for c in chapters),
           "tokens_used": int(rec.get("tokens_used", 0) or 0),
           "created": float(rec.get("created", 0) or 0)}
    if rec.get("cover_url"):
        out["cover_url"] = rec["cover_url"]
    if rec.get("cover_error"):
        out["cover_error"] = rec["cover_error"]
    if state == "done":
        out["read_url"] = f"/books/{rec['id']}/read"
        out["download_url"] = f"/books/{rec['id']}/download"
    return out


def _book_markdown(rec: Dict) -> str:
    parts = [f"# {rec.get('title') or 'OG Book'}", ""]
    if rec.get("premise"):
        parts += [f"*{rec.get('premise', '')}*", ""]
    parts += ["---", ""]
    by_n = {int(c.get("n", 0)): c for c in rec.get("chapters", [])}
    outline = {int(c.get("n", 0)): c for c in rec.get("outline", [])}
    for n in sorted(by_n):
        ch = by_n[n]
        title = ch.get("title") or outline.get(n, {}).get(
            "title", f"Chapter {n}")
        parts += [f"## Chapter {n}: {title}", "",
                  ch.get("text", ""), ""]
    return "\n".join(parts).rstrip() + "\n"


# --- Routes ---------------------------------------------------------------------------------


def _route_uid(request: Request) -> str:
    try:
        return request.cookies.get("ogai_uid", "") or ""
    except Exception:
        return ""


def _route_tier(uid: str, request: Request) -> str:
    fn = _dep("tier_of")
    try:
        return str(fn(uid, request) or "free") if fn else "free"
    except Exception:
        return "free"


def _owned(uid: str, book_id: str) -> Optional[Dict]:
    if not uid:
        return None
    rec = _store_get(book_id)
    if not rec or rec.get("owner") != _key(uid):
        return None
    return rec


def register_book_routes(app):
    @app.get("/books/status")
    def books_status(request: Request):
        if not enabled():
            return {"enabled": False}
        uid = _route_uid(request)
        if not uid:
            return {"enabled": True, "pending": None, "job": None}
        tier = _route_tier(uid, request)
        pending_view = None
        pending = _get_pending(uid)
        if pending is not None:
            pending = _ensure_captured(uid, pending) or pending
            expires = int(float(pending.get("created", time.time()))
                          + _PENDING_TTL - time.time())
            if pending.get("chapters"):
                pending_view = {
                    "stage": "await_approval",
                    "title": pending.get("title", ""),
                    "premise": pending.get("premise", ""),
                    "chapters": pending.get("chapters", []),
                    "chapter_words": int(
                        pending.get("chapter_words")
                        or _DEFAULT_CH_WORDS),
                    "expires_in_s": max(0, expires)}
            else:
                pending_view = {
                    "stage": pending.get("stage", "await_capture"),
                    "title": pending.get("topic", ""),
                    "premise": "",
                    "chapters": [],
                    "chapter_words": int(
                        pending.get("chapter_words")
                        or _DEFAULT_CH_WORDS),
                    "expires_in_s": max(0, expires)}
        return {"enabled": True, "tier": tier,
                "chat_cap": _chat_pool(uid, tier),
                "chat_used": _chat_used_week(uid),
                "chat_left": _chat_left(uid, tier),
                "pending": pending_view,
                "job": _book_view(_latest_book(uid))}

    @app.post("/books/approve")
    def books_approve(request: Request):
        if not enabled():
            return JSONResponse({"ok": False,
                                 "error": "books are off"},
                                status_code=404)
        uid = _route_uid(request)
        if not uid:
            return {"ok": False, "error": "no visitor identity"}
        ok, msg, rec = _approve(uid, _route_tier(uid, request))
        return {"ok": ok,
                "title": rec.get("title", "") if rec else "",
                "error": "" if ok else msg}

    @app.post("/books/decline")
    def books_decline(request: Request):
        if not enabled():
            return JSONResponse({"ok": False,
                                 "error": "books are off"},
                                status_code=404)
        uid = _route_uid(request)
        if not uid:
            return {"ok": False, "error": "no visitor identity"}
        ok, msg = _decline(uid)
        return {"ok": ok, "error": "" if ok else msg}

    @app.get("/books")
    def books_list(request: Request):
        uid = _route_uid(request)
        if not uid:
            return {"books": []}
        return {"books": [_book_view(r)
                           for r in list_jobs_for_owner(uid)]}

    @app.get("/books/{book_id}")
    def book_one(book_id: str, request: Request):
        uid = _route_uid(request)
        rec = _owned(uid, book_id)
        if not rec:
            raise HTTPException(status_code=404,
                                detail="Not found")
        return _book_view(rec)

    @app.get("/books/{book_id}/read")
    def book_read(book_id: str, request: Request):
        uid = _route_uid(request)
        rec = _owned(uid, book_id)
        if not rec:
            raise HTTPException(status_code=404,
                                detail="Not found")
        chapters = [{"n": int(c.get("n", 0)),
                     "title": c.get("title", ""),
                     "words": int(c.get("words", 0) or 0),
                     "text": c.get("text", "")}
                    for c in rec.get("chapters", [])]
        view = _book_view(rec)
        return {"id": rec["id"], "title": rec.get("title", ""),
                "premise": rec.get("premise", ""),
                "cover_url": rec.get("cover_url", ""),
                "status": view["status"] if view else "",
                "chapters": chapters,
                "text": _book_markdown(rec)}

    @app.get("/books/{book_id}/download")
    def book_download(book_id: str, request: Request):
        uid = _route_uid(request)
        rec = _owned(uid, book_id)
        if not rec or not rec.get("chapters"):
            raise HTTPException(status_code=404,
                                detail="Not found")
        os.makedirs(STORE_DIR, exist_ok=True)
        path = os.path.join(STORE_DIR, f"{book_id}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_book_markdown(rec))
        return FileResponse(
            path, media_type="text/markdown",
            filename=_slug(rec.get("title", "book")) + ".md")

    @app.post("/books/{book_id}/continue")
    def book_continue(book_id: str, request: Request):
        if not enabled():
            return JSONResponse({"ok": False,
                                 "error": "books are off"},
                                status_code=404)
        uid = _route_uid(request)
        rec = _owned(uid, book_id)
        if not rec:
            raise HTTPException(status_code=404,
                                detail="Not found")
        ok, msg, rec = _resume(uid, _route_tier(uid, request), rec)
        return {"ok": ok, "error": "" if ok else msg,
                "status": rec.get("state", "") if rec else ""}


# --- The seam (installed fail-safe from og_fixqueue, the last install) -------------------------


_slot = {"job": None, "message": ""}


def install_books_tools(agent_instance, get_uid):
    """Wrap the agent's detect_intent + web_search hooks so
    book asks, the outline-first step, and the approval flow
    ride the established seam. Persona files never touched."""
    if getattr(agent_instance, "_og_books_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _slot["job"] = None
        _slot["message"] = ""
        try:
            uid = get_uid()
            job = _claim_book(str(message), uid) if uid else None
            if job:
                _slot["job"] = job
                _slot["message"] = str(message)
                if isinstance(intent, dict):
                    intent["needs_code_generation"] = False
                    if not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
        except Exception as e:
            logger.warning(f"Book trigger check failed: {e}")
            _slot["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _slot.get("job")
        message = _slot.get("message", "")
        _slot["job"] = None
        _slot["message"] = ""
        if job:
            try:
                results = book_results(job, message, get_uid())
            except Exception as e:
                logger.warning(f"Book job failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_books_installed = True
