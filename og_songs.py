"""
OG songs (Round 30) — OG writes ORIGINAL lyrics in chat, the
visitor approves them, and a real studio API sings them into a
downloadable MP3: in-chat player, owner-only download, locker
save for paid tiers.

PROVIDER (the round's make-or-break first task, proven
2026-10-09 from the provider's own docs/terms as mirrored in
its official developer skill reference + its public API pricing
page): **ElevenLabs Music API** — POST
https://api.elevenlabs.io/v1/music with the `xi-api-key`
header, body {composition_plan | prompt, model_id}; returns
the finished track as a binary MP3 stream. The composition
plan is the lyric-exact path: chunks carry `[Section]` labels
+ OG's own lyric lines + style tags, so the words OG wrote in
chat are the words that get sung. Models: music_v1 / music_v2
/ music_v2_5 (this module defaults to music_v2_5, env
OG_SONG_MODEL). Price: $0.15 per generated minute on the
public API pricing page (pay-as-you-go; also drawable from
subscription credits at 900 credits/min) — a ~2.5-minute OG
song costs ~$0.38, in line with a Round 28 story video
(~$0.35-0.40). Terms: paid accounts may use output
commercially and the account retains its rights in the
output; free accounts are non-commercial and CANNOT use the
music API meaningfully (paid-plan-only feature), so the
activation account must be a paid one. Prompts/plans that
reference real artists, bands, or copyrighted lyrics are
rejected by the provider (bad_prompt / bad_composition_plan,
with a suggested rewrite) — OG's drafts are original-only by
instruction, and a rejection fails the job honestly, never
charges. Alternatives surveyed and rejected on evidence:
Stable Audio (official API, ~$0.20-0.26/generation, but
text-to-audio with NO lyric input — vocals are its
documented weakness; it cannot sing OG's words), Google
Lyria (instrumental only, watermarked), MiniMax Music
(hosted API in flux / verification-gated; open weights would
mean self-hosting a music model on Render). Suno has NO
official public API and unofficial wrappers are off the
table by Brent's standing rule — never integrated here.

The flow (Brent's prepare -> approve -> execute rule, the
Round 16/27/28 shape):

1. ASK. "make me a song about X" (optionally styled — "a
   country song", "write a rap about...") claims a song job.
   The reply makes the agent WRITE THE SONG in the thread
   (TITLE line + STYLE line + [Section] lyric blocks) and end
   with the approval ask. A per-visitor pending state is
   parked (30-min expiry). NO provider call happens before
   approval — the draft costs nothing but chat tokens.
2. APPROVE. Chat YES / NO, or the on-page card (GET
   /song/status carries the pending with the REAL title /
   style / sections / estimated minutes — captured from the
   visitor's own history, the "what was shown is what ships"
   pattern; POST /song/approve and /song/decline are the
   card's doors). First resolution wins; a decline scraps
   the pending and spends nothing. Lyric edits ("make the
   chorus about X") loop back to a fresh draft.
3. GENERATE. On approval a background job builds the
   composition plan from the approved lyrics and makes ONE
   provider call (synchronous MP3 stream, generous deadline,
   no silent retries — a retry could double-bill the studio
   account, and a failure charges the visitor nothing
   anyway). The returned bytes are verified as a real,
   playable MP3 before the job counts as done.
4. DELIVER. Owner-only GET /song/download/<id> (24 h
   retention), an inline <audio> player + download button on
   the page when the job completes, and "Save to my locker"
   (POST /song/save/<id>) into the Round 21 locker for tiers
   with quota.

Caps: og_tiers kind "song" (free 1 / standard 2 / pro 5 /
blue 15 / blackout 50 per day), charged ONLY on a completed,
verified generation — a failed take never eats the visitor's
song. The math at the provider's $0.15/min and the ~2-3 min
OG song length: worst case per visitor-day is ~$0.45 free /
$0.90 standard / $2.25 pro / $6.75 blue / $22.50 blackout of
studio cost at FULL cap usage every day — the same shape as
the Round 28 video ladder (which caps HIGHER at every paid
tier for a similar per-unit cost), so the spec's proposed
caps stand.

DARK GATE: the whole feature is inert unless BOTH
OG_SONG_ENABLED is truthy AND OG_ELEVENLABS_API_KEY is set
in the environment. Dark = claims never fire, /song/status
answers {"enabled": false}, the card doors 404. The key
lives in Render env only — set in-browser at activation,
never in chat, code, or reports.

Job records ride the Round 10/18/28 store pattern: Postgres
table og_song_jobs when OG_MEMORY_DB_URL is set (psycopg),
else a JSON file beside the renders. Pending approvals are
process-local (like the Round 14/16/28 drafts): a restart
mid-window drops the pending — nothing was generated, the
visitor just asks again. Tracks live on local disk (the
locker is the durable home, via Save).

Persona files are NEVER touched by this module. Lyrics come
from the existing persona, in the thread, like every other
round's drafts.
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

STORE_DIR = os.getenv("OG_SONG_STORE_DIR", "song_builds")
_PENDING_TTL = 30 * 60          # song approval window
_JOB_TTL = 24 * 60 * 60         # finished tracks kept 24 h
_STALE_S = 45 * 60              # working record older = cut off
_PROVIDER_TIMEOUT = 900         # one call, generous deadline
_MIN_WORDS = 40                 # a song needs at least this much lyric
_MAX_WORDS = 500                # capture trims anything past this
_MIN_SECTIONS = 2
_MAX_SECTIONS = 8
_MS_PER_WORD = 480              # sung pace (~125 wpm) for plan timing
_CHUNK_MIN_MS = 12000
_CHUNK_MAX_MS = 90000
_PLAN_MAX_MS = 300000           # ~5 min of music, hard ceiling

# Bound by app.py via bind_app (usage store, tier resolution,
# the visitor-history reader).
_deps: Dict = {}


def bind_app(deps):
    _deps.update(deps)


def _pro_url() -> str:
    base = os.getenv("OG_PUBLIC_URL", "https://og-ai-service.onrender.com")
    return base.rstrip("/") + "/pro"


# --- Provider gate / config -----------------------------------------------------


def _flag_on() -> bool:
    return os.getenv("OG_SONG_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on")


def _provider_key() -> str:
    return os.getenv("OG_ELEVENLABS_API_KEY", "").strip()


def _provider_url() -> str:
    return os.getenv(
        "OG_SONG_API_URL",
        "https://api.elevenlabs.io/v1/music").strip()


def _provider_model() -> str:
    return os.getenv("OG_SONG_MODEL", "music_v2_5").strip() \
        or "music_v2_5"


def _output_format() -> str:
    return os.getenv("OG_SONG_OUTPUT_FORMAT",
                     "mp3_44100_128").strip() or "mp3_44100_128"


def enabled() -> bool:
    """Songs are live only when the feature flag is on AND a
    studio key is configured. Either missing = dark, and dark
    means the module may as well not exist."""
    return _flag_on() and bool(_provider_key())


# --- Usage / caps (the bound app usage store, og_video pattern) -----------------


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _usage_get(key: str):
    with _deps["usage_lock"]:
        store = _deps["load_usage"]()
    entry = store.get(key)
    return dict(entry) if isinstance(entry, dict) else None


def _songs_used_today(uid: str) -> int:
    """Today's slice of the usage entry (substrate reader — the
    cap itself is weekly, Round 55)."""
    entry = _usage_get(f"song:{uid}")
    if not entry or entry.get("date") != _today():
        return 0
    return int(entry.get("count", 0))


def _songs_used_week(uid: str) -> int:
    """Sung tracks this visitor has generated in the trailing
    7 days (Round 55: the song cap is weekly-only)."""
    return _tiers.week_used(_usage_get(f"song:{uid}"))


def _song_week_left(uid: str, tier: str) -> int:
    cap_w = _tiers.weekly_cap(tier, "song")
    if cap_w is None:
        return 1
    return max(0, int(cap_w) - _songs_used_week(uid))


def _charge_song(uid: str) -> None:
    """Spend one unit of the weekly song cap. Called ONLY after
    a generation completes and verifies — failures never
    charge. (The per-day entry stays as the storage substrate;
    its per-day map is what the weekly sum reads.)"""
    key = f"song:{uid}"
    with _deps["usage_lock"]:
        store = _deps["load_usage"]()
        entry = store.get(key)
        if not isinstance(entry, dict) or entry.get("date") != _today():
            old, entry = entry, {"date": _today(), "count": 0}
            _tiers.carry_days(old, entry)
        entry["count"] = int(entry.get("count", 0)) + 1
        _tiers.note_day(entry, _today(), entry["count"])
        store[key] = entry
        _deps["save_usage"](store)


def _tier_now(uid: str) -> str:
    fn = _deps.get("get_tier")
    try:
        return str(fn() or "free") if fn else "free"
    except Exception:
        return "free"


# --- Pending approvals (process-local, the Round 14/16/28 shape) -----------------

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


# --- Job records (Postgres when OG_MEMORY_DB_URL, else JSON) --------------------

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — JSON backend is used
    psycopg = None

_jobs_lock = threading.Lock()
_jobs_mem: Dict[str, Dict] = {}
_gen_slots = threading.Semaphore(2)


def _key(uid: str) -> str:
    return hashlib.sha256(str(uid).encode()).hexdigest()[:32]


def _json_store_path() -> str:
    os.makedirs(STORE_DIR, exist_ok=True)
    return os.path.join(STORE_DIR, "song_jobs.json")


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_song_jobs ("
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
                        "INSERT INTO og_song_jobs (id, owner, updated,"
                        " data) VALUES (%s, %s, %s, %s) ON CONFLICT"
                        " (id) DO UPDATE SET owner = EXCLUDED.owner,"
                        " updated = EXCLUDED.updated,"
                        " data = EXCLUDED.data",
                        (rec["id"], rec.get("owner", ""), rec["updated"],
                         json.dumps(rec)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Song job DB write failed: {e}")
    try:
        path = _json_store_path()
        data = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        data[rec["id"]] = rec
        cutoff = time.time() - _JOB_TTL
        data = {k: v for k, v in data.items()
                if float(v.get("updated", 0)) > cutoff}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(f"Song job JSON write failed: {e}")


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
                        "SELECT data FROM og_song_jobs WHERE id = %s",
                        (job_id,))
                    row = cur.fetchone()
            if row:
                rec = json.loads(row[0])
                with _jobs_lock:
                    _jobs_mem[job_id] = rec
                return dict(rec)
        except Exception as e:
            logger.warning(f"Song job DB read failed: {e}")
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
        logger.warning(f"Song job JSON read failed: {e}")
    return None


def _store_latest_for_owner(owner: str) -> Optional[Dict]:
    best = None
    with _jobs_lock:
        cands = [dict(r) for r in _jobs_mem.values()
                 if r.get("owner") == owner]
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_song_jobs WHERE owner"
                        " = %s ORDER BY updated DESC LIMIT 1",
                        (owner,))
                    row = cur.fetchone()
            if row:
                cands.append(json.loads(row[0]))
        except Exception as e:
            logger.warning(f"Song job DB scan failed: {e}")
    # The JSON file is also consulted on a DB miss: _store_put
    # falls back to it whenever a DB write fails, so a record can
    # legitimately live there even with the DB configured.
    try:
        path = _json_store_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            cands.extend(v for v in data.values()
                         if v.get("owner") == owner)
    except Exception as e:
        logger.warning(f"Song job JSON scan failed: {e}")
    for rec in cands:
        if best is None or float(rec.get("updated", 0)) \
                > float(best.get("updated", 0)):
            best = rec
    return best


def list_jobs_for_owner(uid: str) -> List[Dict]:
    """All of the visitor's OWN song jobs, newest first
    (Library). Read-only: the same mem + DB + JSON scan
    _store_latest_for_owner does, but every record comes back
    (deduped by id) instead of only the latest. Retention is
    NOT re-decided here — the caller passes each record
    through _job_view, which already hides stale/gone jobs
    exactly the way /song/status does."""
    owner = _key(uid)
    found: Dict[str, Dict] = {}
    with _jobs_lock:
        for r in _jobs_mem.values():
            if r.get("owner") == owner:
                found[r["id"]] = dict(r)
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_song_jobs WHERE owner"
                        " = %s", (owner,))
                    rows = cur.fetchall()
            for row in rows:
                rec = json.loads(row[0])
                found[rec["id"]] = rec
        except Exception as e:
            logger.warning(f"Song job DB list failed: {e}")
    try:
        path = _json_store_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            for v in data.values():
                if v.get("owner") == owner:
                    found[v["id"]] = v
    except Exception as e:
        logger.warning(f"Song job JSON list failed: {e}")
    return sorted(found.values(),
                  key=lambda r: float(r.get("created", 0)),
                  reverse=True)


def purge_owner(uid: str):
    """Account deletion (Round 36, og_store_ready): every
    song job this visitor owns — the in-memory cache, the
    DB rows, the JSON fallback and the build dirs — plus
    any pending draft. Other owners' jobs never touched."""
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
                        "DELETE FROM og_song_jobs WHERE owner = %s",
                        (owner,))
                conn.commit()
        except Exception as e:
            logger.warning(f"Song job DB purge failed: {e}")
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
        logger.warning(f"Song job JSON purge failed: {e}")
    import shutil
    for jid in ids:
        try:
            shutil.rmtree(os.path.join(STORE_DIR, jid),
                          ignore_errors=True)
        except Exception:
            pass


def _active_job(uid: str) -> Optional[Dict]:
    rec = _store_latest_for_owner(_key(uid))
    if rec and rec.get("state") in ("queued", "generating"):
        if time.time() - float(rec.get("updated", 0)) > _STALE_S:
            return None     # stale working record = a cut-off take
        return rec
    return None


# --- Song parsing -----------------------------------------------------------------

_TITLE_RE = re.compile(
    r"^\s*(?:#{1,3}\s*)?(?:\*\*)?TITLE\s*[:\-—]\s*(.+?)(?:\*\*)?\s*$",
    re.IGNORECASE)
_STYLE_RE = re.compile(
    r"^\s*(?:#{1,3}\s*)?(?:\*\*)?STYLE\s*[:\-—]\s*(.+?)(?:\*\*)?\s*$",
    re.IGNORECASE)
_SECTION_RE = re.compile(
    r"^\s*(?:\*\*|__)?\s*\[\s*([A-Za-z][A-Za-z0-9 '\-]{0,30})\s*\]"
    r"\s*(?:\*\*|__)?\s*$")


def _clean_text(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text or "")
    text = re.sub(r"(?m)^#{1,4}\s*", "", text)
    text = text.replace("`", "")
    return text.strip()


def _word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", text or "") if w])


def parse_song(text: str) -> Optional[Dict]:
    """Parse a song draft out of an assistant message: a TITLE
    line, a STYLE line, and [Section] blocks of lyric lines.
    Returns {title, style, sections, words} or None when the
    text isn't a song draft (too few sung sections / too
    little lyric)."""
    if not text:
        return None
    lines = str(text).splitlines()
    title = ""
    style = ""
    body_start = 0
    for i, line in enumerate(lines[:8]):
        m = _TITLE_RE.match(line)
        if m and not title:
            title = m.group(1).strip().strip("*").strip()
            body_start = max(body_start, i + 1)
            continue
        # The served text may carry an interjection before the
        # TITLE marker (the register pass works the whole
        # reply); take whatever follows the marker on that line.
        j = line.upper().rfind("TITLE:")
        if j >= 0 and not title and len(line) - (j + 6) >= 2:
            title = line[j + 6:].strip().strip("*").strip()
            body_start = max(body_start, i + 1)
            continue
        m = _STYLE_RE.match(line)
        if m and not style:
            style = m.group(1).strip().strip("*").strip()
            body_start = max(body_start, i + 1)
    title = title.strip().strip('"“”').strip()
    body_lines = lines[body_start:]
    sections: List[Dict] = []
    cur: Optional[Dict] = None
    for line in body_lines:
        m = _SECTION_RE.match(line)
        if m:
            name = " ".join(m.group(1).split()).strip()
            cur = {"name": name[:40], "lines": []}
            sections.append(cur)
            continue
        clean = _clean_text(line)
        if cur is not None and clean:
            # Skip the composer's own trailing chatter lines
            # (the approval ask rides after the last section in
            # prose, never inside brackets; a line that starts
            # the ask is long prose — lyric lines are short).
            cur["lines"].append(clean)
    # Trim trailing non-lyric lines off the LAST section: the
    # approval ask paragraph the persona appends after the song.
    # Lyric lines are short; the ask is one long prose block.
    if sections:
        last = sections[-1]
        while last["lines"] and len(last["lines"][-1]) > 160:
            last["lines"].pop()
    sections = [s for s in sections
                if sum(1 for ln in s["lines"]
                       if _word_count(ln) >= 2) >= 2]
    if len(sections) < _MIN_SECTIONS:
        return None
    sections = sections[:_MAX_SECTIONS]
    words = sum(_word_count(ln) for s in sections
                for ln in s["lines"])
    if words < _MIN_WORDS:
        return None
    # Length cap: drop whole sections from the end.
    while len(sections) > _MIN_SECTIONS and words > _MAX_WORDS:
        dropped = sections.pop()
        words -= sum(_word_count(ln) for ln in dropped["lines"])
    if not title:
        title = "OG's Song"
    if not style:
        style = "original song, full band, sung vocals"
    return {"title": title[:90], "style": style[:200],
            "sections": sections, "words": words}


def _history(uid: str) -> List[Dict]:
    fn = _deps.get("load_history")
    if fn is None:
        return []
    try:
        return fn(uid) or []
    except Exception as e:
        logger.warning(f"Song history read failed: {e}")
        return []


def _capture_from_history(uid: str, pending: Dict) -> Optional[Dict]:
    """Lift the song the visitor approved out of their own
    thread: the newest assistant message at/after the pending's
    history watermark that parses as a song. For thread-bound
    pendings the watermark is 0 — the song is already there."""
    hist = _history(uid)
    start = int(pending.get("hist_len", 0))
    for entry in reversed(hist[start:]):
        if not isinstance(entry, dict) \
                or entry.get("role") != "assistant":
            continue
        parsed = parse_song(str(entry.get("content", "")))
        if parsed:
            return parsed
    if pending.get("stage") == "await_approval":
        for entry in reversed(hist):
            if not isinstance(entry, dict) \
                    or entry.get("role") != "assistant":
                continue
            parsed = parse_song(str(entry.get("content", "")))
            if parsed:
                return parsed
    return None


def _ensure_captured(uid: str, pending: Dict) -> Optional[Dict]:
    """Lazy capture: fill a pending with the song's real title /
    style / sections the first time anyone looks after the
    draft lands. Returns the updated pending or None."""
    if pending.get("sections"):
        return pending
    parsed = _capture_from_history(uid, pending)
    if not parsed:
        return None
    pending = dict(pending)
    pending.update({
        "stage": "await_approval",
        "title": parsed["title"], "style": parsed["style"],
        "sections": parsed["sections"], "words": parsed["words"]})
    _set_pending(uid, pending)
    return pending


def _style_tags(style: str) -> List[str]:
    tags = [t.strip() for t in re.split(r"[,;]", style or "")
            if t.strip()]
    if not tags and style:
        tags = [style.strip()]
    return tags[:5] or ["original song", "sung vocals"]


def build_plan(song: Dict) -> Dict:
    """The ElevenLabs composition plan for an approved song:
    one chunk per section, OG's exact lyric lines as the chunk
    text, per-section durations from the sung pace, the song's
    style tags on every chunk. Deterministic — the same draft
    always builds the same plan."""
    tags = _style_tags(song.get("style", ""))
    chunks = []
    total = 0
    for sec in song.get("sections", []):
        words = sum(_word_count(ln) for ln in sec["lines"])
        ms = int(max(_CHUNK_MIN_MS,
                     min(_CHUNK_MAX_MS, words * _MS_PER_WORD)))
        pos = list(tags)
        name_low = sec["name"].lower()
        if "chorus" in name_low or "hook" in name_low:
            pos.append("anthemic, catchy hook")
        elif "bridge" in name_low:
            pos.append("stripped back, emotional build")
        text = f"[{sec['name']}]\n" + "\n".join(sec["lines"])
        chunks.append({
            "text": text[:2000],
            "duration_ms": ms,
            "positive_styles": pos[:6],
            "negative_styles": ["spoken word", "instrumental only"],
            "context_adherence": "high"})
        total += ms
    if total > _PLAN_MAX_MS and total > 0:
        scale = _PLAN_MAX_MS / float(total)
        for ch in chunks:
            ch["duration_ms"] = max(
                3000, int(ch["duration_ms"] * scale))
    return {"chunks": chunks}


def _plan_ms(song: Dict) -> int:
    plan = build_plan(song)
    return sum(int(c["duration_ms"]) for c in plan["chunks"])


def _est_min(song: Dict) -> float:
    return round(_plan_ms(song) / 60000.0, 1)


# --- Provider call (THE spend seam — stubbed in tests) ----------------------------


class _ProviderError(Exception):
    def __init__(self, kind: str, message: str,
                 suggestion: str = ""):
        super().__init__(message)
        self.kind = kind
        self.suggestion = suggestion


def _looks_like_mp3(data: bytes) -> bool:
    if not data or len(data) < 1024:
        return False
    if data[:3] == b"ID3":
        return True
    # MPEG audio frame sync
    return len(data) > 2 and data[0] == 0xFF \
        and (data[1] & 0xE0) == 0xE0


def _provider_error_from(status: int, body: bytes) -> _ProviderError:
    text = ""
    suggestion = ""
    kind = "http"
    try:
        text = body.decode("utf-8", "replace")[:2000]
        data = json.loads(text)
        blob = json.dumps(data).lower()
        if "bad_composition_plan" in blob:
            kind = "bad_plan"
        elif "bad_prompt" in blob:
            kind = "bad_plan"

        def _hunt(obj, keys):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in keys and isinstance(v, str) and v:
                        return v
                    got = _hunt(v, keys)
                    if got:
                        return got
            elif isinstance(obj, list):
                for v in obj:
                    got = _hunt(v, keys)
                    if got:
                        return got
            return ""
        suggestion = _hunt(data, {"composition_plan_suggestion",
                                  "prompt_suggestion", "suggestion"})
    except Exception:
        text = ""
    if status == 401 or status == 403:
        kind = "auth"
    elif status == 429:
        kind = "rate"
    return _ProviderError(
        kind, f"studio answered {status}: {text[:200]}", suggestion)


def _generate_track(song: Dict) -> bytes:
    """ONE synchronous studio call: POST the composition plan,
    return the finished track's MP3 bytes. Raises
    _ProviderError on any failure. No retries — a second call
    could double-bill the studio account, and a failed take
    charges the visitor nothing either way."""
    import httpx
    key = _provider_key()
    if not key:
        raise _ProviderError("auth", "no studio key configured")
    plan = build_plan(song)
    url = _provider_url()
    sep = "&" if "?" in url else "?"
    url = f"{url}{sep}output_format={_output_format()}"
    try:
        with httpx.Client(timeout=_PROVIDER_TIMEOUT) as client:
            r = client.post(
                url,
                headers={"xi-api-key": key,
                         "Content-Type": "application/json"},
                json={"composition_plan": plan,
                      "model_id": _provider_model()})
    except Exception as e:
        raise _ProviderError("network", f"studio unreachable: {e}")
    if r.status_code != 200:
        raise _provider_error_from(r.status_code, r.content or b"")
    data = r.content or b""
    if not _looks_like_mp3(data):
        raise _ProviderError(
            "bad_audio",
            f"studio sent {len(data)} bytes that are not an MP3")
    return data


_FAIL_DETAILS = {
    "auth": "The studio key got rejected — the song lab needs "
            "a fix on OG's side. Nothing was charged.",
    "bad_plan": "The studio bounced that draft — it flags "
                "anything that smells like a real artist's "
                "song. Nothing was charged. Ask for a rewrite "
                "with different words and we'll re-track it.",
    "rate": "The studio is slammed right now — nothing was "
            "charged. Give it a minute and ask again.",
    "network": "The studio didn't answer in time — nothing "
               "was charged. Ask again and I'll re-track it.",
    "bad_audio": "The studio sent back something that isn't "
                 "a playable track — nothing was charged.",
    "http": "That take didn't make it — nothing was charged. "
            "Ask again and I'll re-track it.",
}


# --- Narration (grounded result blocks the persona voices) -----------------------


def _result(tag: str, title: str, body: str, href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body, "href": href}]


def _cap_body(tier: str, uid: str, note: str = "") -> List[Dict]:
    cap_w = int(_tiers.weekly_cap(tier, "song") or 0)
    refill = _tiers.week_refill_text(_usage_get(f"song:{uid}"))
    body = (f"The visitor is at THIS WEEK's song cap for their "
            f"plan ({cap_w} per week) — there is no daily reset. "
            f"The weekly pool has no fixed reset day: {refill}. "
            f"{note} Tell them plainly, in persona: the lyrics "
            f"themselves they can still get right here in chat "
            f"any time; the SUNG track is what's capped. Higher "
            f"plans sing more songs a week: {_pro_url()}")
    return _result("SONG: AT-CAP", "🎵 Songs — capped", body)


def _down_body() -> List[Dict]:
    return _result(
        "SONG: DOWN", "🎵 Song — studio dark",
        "The song studio is dark right now (not switched on). "
        "Tell the visitor plainly, in persona — no fake "
        "promises: he can still WRITE the song with them right "
        "here in chat, lyrics and all; the sung track waits "
        "until the studio lights up.")


# --- Claim table -------------------------------------------------------------------

_YES_WORDS = {
    "yes", "yeah", "yep", "yup", "ya", "ok", "okay", "sure",
    "approve", "approved", "go", "do it", "make it", "lets go",
    "let's go", "yes please", "yes make it", "yes do it",
    "sing it", "make the song", "do the song", "👍",
}
_NO_WORDS = {
    "no", "nope", "nah", "decline", "declined", "scrap it",
    "never mind", "nevermind", "forget it", "cancel", "stop",
    "don't", "dont", "no thanks",
}
_SONG_VERB = re.compile(
    r"\b(make|write|create|compose|generate|sing|record|produce|"
    r"cut)\b", re.I)
_SONG_NOUN = re.compile(
    r"\b(song|songs|track|anthem|jingle|rap|ballad)\b", re.I)
_ABOUT = re.compile(r"\babout\s+(.+)$", re.I)
# Round 40: "I want a song about …" — want-form with an article,
# like the build verbs; "I want that song" (an existing song)
# must not claim a fresh draft.
_WANT_SONG = re.compile(
    r"\bi want\s+(?:a\s+|an\s+|some\s+|one\s+)?"
    r"(song|songs|track|anthem|jingle|rap|ballad)\b", re.I)
_THREAD_BIND = re.compile(
    r"\b(make|turn|set)\b[^.?!]{0,40}\b(that|this|it)\b"
    r"[^.?!]{0,20}\b(song|track)\b|\bmake that a song\b|"
    r"\bsong of that\b|\bturn that into a song\b", re.I)
_STYLE_WORDS = (
    "heavy metal", "hip hop", "hip-hop", "outlaw country",
    "lo-fi", "lofi", "r&b", "rap", "country", "rock", "metal",
    "punk", "pop", "ballad", "blues", "jazz", "folk", "soul",
    "funk", "reggae", "edm", "techno", "house", "trap", "drill",
    "grime", "indie", "acoustic", "gospel", "opera", "bluegrass",
    "ska", "emo", "synthwave", "americana", "honky tonk",
)


def _norm(message: str) -> str:
    return " ".join(str(message or "").lower().split()).strip(" .!")


def _style_hint(raw: str) -> str:
    low = " " + str(raw or "").lower() + " "
    for word in _STYLE_WORDS:
        if f" {word} " in low or f" {word}\n" in low:
            return word
    return ""


def _browser_has_pending(uid: str) -> bool:
    """A bare YES/NO owed to a parked browser action is never
    stolen (the Round 21/27 rule)."""
    try:
        import og_browser as _browser
        pend = _browser._get_pending(uid)
        return bool(pend and pend.get("kind") == "action")
    except Exception:
        return False


def _video_pending_newer(uid: str, song_pending: Dict) -> bool:
    """When BOTH a story-video approval and a song approval are
    parked, the bare YES/NO belongs to whichever ask the
    visitor saw LAST (the newest pending) — the Round 28 card
    rule, applied across modules."""
    try:
        import og_video as _video
        vpend = _video._get_pending(uid)
        if not vpend:
            return False
        return float(vpend.get("created", 0)) \
            > float(song_pending.get("created", 0))
    except Exception:
        return False


def _claim_song(message: str, uid: str) -> Optional[Dict]:
    if not uid or not enabled():
        return None
    raw = str(message or "")
    low = raw.lower()
    norm = _norm(raw)
    pending = _get_pending(uid)
    if pending is not None:
        stage = pending.get("stage", "")
        if stage == "await_topic":
            if norm in _NO_WORDS:
                return {"kind": "decline"}
            if _SONG_VERB.search(low) and _ABOUT.search(raw):
                m = _ABOUT.search(raw.strip())
                return {"kind": "topic",
                        "topic": m.group(1).strip().strip(".!")[:200]}
            if len(norm) >= 2:
                return {"kind": "topic", "topic": raw.strip()[:200]}
            return None
        # A fresh ask with its own topic replaces the parked
        # draft instead of approving it by accident.
        if _SONG_VERB.search(low) and _SONG_NOUN.search(low) \
                and _ABOUT.search(raw):
            m = _ABOUT.search(raw.strip())
            return {"kind": "replace",
                    "topic": m.group(1).strip().strip(".!")[:200],
                    "style": _style_hint(raw)}
        # A live draft is waiting on approval.
        if norm in _YES_WORDS:
            if _browser_has_pending(uid):
                return None
            if _video_pending_newer(uid, pending):
                return None
            return {"kind": "approve"}
        if norm in _NO_WORDS:
            if _browser_has_pending(uid):
                return None
            if _video_pending_newer(uid, pending):
                return None
            return {"kind": "decline"}
        if norm in ("song", "sing it", "track it"):
            return {"kind": "approve"}
        if re.search(r"\b(change|rewrite|redo|make it|instead|"
                     r"add|remove|faster|slower|happier|sadder|"
                     r"darker|lighter|chorus|verse|bridge|hook|"
                     r"longer|shorter)\b", low) and len(low) < 300:
            return {"kind": "revise", "note": raw.strip()[:300]}
        return None
    # No pending — bare approvals/declines claim nothing.
    if "video" in low:
        return None     # story videos / song videos: Round 28
    if _THREAD_BIND.search(low) and not _ABOUT.search(raw):
        return {"kind": "from_thread"}
    verb = _SONG_VERB.search(low)
    noun = _SONG_NOUN.search(low)
    want = _WANT_SONG.search(low)
    vpos = verb.start() if verb else (want.start() if want else None)
    if noun and vpos is not None and noun.start() > vpos \
            and noun.start() - vpos < 60:
        topic = ""
        m = _ABOUT.search(raw.strip())
        if m:
            topic = m.group(1).strip().strip(".!")[:200]
        topic = re.sub(
            r"^(a|an|the)\s+(\S+\s+){0,2}(song|track|anthem|"
            r"jingle|rap|ballad)\s+(about\s+)?", "", topic,
            flags=re.I).strip()
        return {"kind": "new", "topic": topic,
                "style": _style_hint(raw)}
    if _THREAD_BIND.search(low):
        return {"kind": "from_thread"}
    return None


# --- Flow results --------------------------------------------------------------------


def _song_instruction(topic: str, style: str = "") -> str:
    style_bit = (
        f" They asked for it in this style: {style} — build the "
        f"STYLE line around that." if style else "")
    return (
        f"The visitor wants a SONG about: {topic}.{style_bit} "
        f"Do this in your reply, in persona, right now — SONG "
        f"FIRST, the studio comes after they approve: (1) Write "
        f"the song itself: ORIGINAL lyrics only, yours, never "
        f"quoting or imitating any real song or artist (the "
        f"studio rejects those and so do we). Aim for 3-6 "
        f"sections, 4-8 short lyric lines each, roughly 110-260 "
        f"words of lyrics total (about 2-3 minutes sung). "
        f"Format it EXACTLY like this: a first line 'TITLE: "
        f"<the title>', a second line 'STYLE: <genre, mood, "
        f"vocal type, key instruments — one line, no artist "
        f"names>', then each section as a bracketed header "
        f"line like [Verse 1], [Chorus], [Verse 2], [Bridge], "
        f"[Outro] with that section's lyric lines under it. "
        f"(2) After the song, close with the approval ask, in "
        f"persona: name the title, the style, and the rough "
        f"length, and tell them replying YES gets it SUNG for "
        f"real in the studio — a finished track, a player and "
        f"a download right here — and NO scraps it. Lyric "
        f"edits are welcome ('make the chorus about X'). Do "
        f"NOT say the track exists yet; nothing is generated "
        f"before their YES.")


def _start_song(uid: str, topic: str, style: str = "") -> List[Dict]:
    hist_len = len(_history(uid))
    _set_pending(uid, {"stage": "await_capture", "topic": topic,
                       "style_hint": style, "hist_len": hist_len})
    return _result("SONG: WRITE", "🎵 Song — lyrics first",
                   _song_instruction(topic, style))


def _approve(uid: str, tier: str):
    """Shared resolution for the chat YES and the card door.
    Returns (ok, message, rec)."""
    pending = _get_pending(uid)
    if pending is None:
        return False, "Nothing is waiting on approval — it " \
            "expired or was already answered.", None
    pending = _ensure_captured(uid, pending) or pending
    if not pending.get("sections"):
        _clear_pending(uid)
        return False, "I couldn't find that song in the thread " \
            "anymore, so there's nothing to sing. Ask me for " \
            "the song again and we'll redo it.", None
    if _active_job(uid):
        _clear_pending(uid)
        return False, "A track is already in the studio for " \
            "you — let that one finish first.", None
    if _song_week_left(uid, tier) <= 0:
        _clear_pending(uid)
        return False, "You're at this week's song cap for " \
            "your plan, so this one can't be tracked. The " \
            "weekly pool refills as your old days roll off " \
            "it — the lyrics themselves stay right here in " \
            "chat.", None
    rec = _new_job(uid, pending)
    _clear_pending(uid)
    _store_put(rec)
    thread = threading.Thread(target=_run_job,
                              args=(rec["id"], uid), daemon=True)
    thread.start()
    return True, f"Locked in — tracking \"{rec['title']}\" now.", rec


def _decline(uid: str):
    pending = _get_pending(uid)
    if pending is None:
        return False, "Nothing was waiting — no song is pending."
    _clear_pending(uid)
    return True, "Scrapped — nothing generated, nothing charged."


def song_results(job: Dict, message: str, uid: str) -> List[Dict]:
    kind = job.get("kind", "")
    tier = _tier_now(uid)
    if kind == "new":
        if _active_job(uid):
            return _result(
                "SONG: BUSY", "🎵 Song — one in the studio",
                "A track is ALREADY being generated for this "
                "visitor. Tell them, in persona: one at a time — "
                "the tracking one lands right here in the thread "
                "when it's done, then they can line up the next.")
        if _song_week_left(uid, tier) <= 0:
            return _cap_body(tier, uid)
        topic = job.get("topic", "")
        if not topic:
            _set_pending(uid, {"stage": "await_topic",
                               "hist_len": len(_history(uid))})
            return _result(
                "SONG: NEED-TOPIC", "🎵 Song — needs a topic",
                "The visitor wants a song but didn't say what "
                "about. Ask them, in persona, in one line: "
                "what's the song about? Whatever they answer "
                "next becomes the topic — no need for them to "
                "repeat 'song'.")
        return _start_song(uid, topic, job.get("style", ""))
    if kind == "topic":
        topic = job.get("topic", "")
        _clear_pending(uid)
        if _song_week_left(uid, tier) <= 0:
            return _cap_body(tier, uid)
        return _start_song(uid, topic)
    if kind == "replace":
        _clear_pending(uid)
        if _active_job(uid):
            return _result(
                "SONG: BUSY", "🎵 Song — one in the studio",
                "A track is ALREADY being generated for this "
                "visitor. Tell them, in persona: one at a time — "
                "wait for the tracking one to land in the thread.")
        if _song_week_left(uid, tier) <= 0:
            return _cap_body(tier, uid)
        return _start_song(uid, job.get("topic", ""),
                           job.get("style", ""))
    if kind == "from_thread":
        if _active_job(uid):
            return _result(
                "SONG: BUSY", "🎵 Song — one in the studio",
                "A track is ALREADY being generated for this "
                "visitor. Tell them, in persona: one at a time — "
                "wait for the tracking one to land in the thread.")
        if _song_week_left(uid, tier) <= 0:
            return _cap_body(tier, uid)
        pending = {"stage": "await_approval", "hist_len": 0,
                   "topic": ""}
        _set_pending(uid, pending)
        captured = _ensure_captured(uid, _get_pending(uid) or pending)
        if not captured:
            _clear_pending(uid)
            _set_pending(uid, {"stage": "await_topic",
                               "hist_len": len(_history(uid))})
            return _result(
                "SONG: NO-SONG", "🎵 Song — no song yet",
                "The visitor asked to make a song of something, "
                "but there's no song written in this thread "
                "yet. Tell them, in persona: give me a topic "
                "and I'll write the song first, show it, and "
                "only track it after your YES. Whatever topic "
                "they answer with becomes the song.")
        sections = captured["sections"]
        body = (
            f"The visitor wants THAT song tracked. Present "
            f"this approval ask, in persona, with these REAL "
            f"numbers (do not invent different ones): the song "
            f"is \"{captured['title']}\", style: "
            f"{captured['style']}; {len(sections)} sections, "
            f"about {_est_min(captured)} minutes of sung "
            f"track. Replying YES tracks it (one of their "
            f"{int(_tiers.weekly_cap(tier, 'song') or 0)} "
            f"weekly songs); NO scraps it. Nothing is "
            f"generated before their YES. Songs left this "
            f"week: {_song_week_left(uid, tier)}.")
        return _result("SONG: APPROVE?", "🎵 Song — approval",
                       body)
    if kind == "approve":
        ok, msg, rec = _approve(uid, tier)
        if not ok:
            return _result("SONG: APPROVE-FAILED",
                           "🎵 Song — not started", msg +
                           " Tell the visitor exactly that, in "
                           "persona — plainly, no dressing it up.")
        body = (
            f"APPROVED — the studio is TRACKING "
            f"\"{rec['title']}\" now ({rec['style']}). Tell "
            f"the visitor, in persona: locked in, it's in the "
            f"studio — the singer's cutting it for real. It "
            f"lands RIGHT HERE in this thread with a player "
            f"and a download button when it's done (studio "
            f"takes a minute or a few). They don't need to do "
            f"anything — and it only counts against their "
            f"weekly songs when it FINISHES, so a failed take "
            f"costs them nothing.")
        return _result("SONG: TRACKING", "🎵 Song — in the studio",
                       body)
    if kind == "decline":
        ok, msg = _decline(uid)
        return _result(
            "SONG: DECLINED", "🎵 Song — scrapped",
            msg + " Tell the visitor that, in persona, briefly — "
            "the lyrics themselves stay in the thread if they "
            "want to read them, and a fresh ask starts a fresh "
            "song.")
    if kind == "revise":
        pending = _get_pending(uid) or {}
        topic = pending.get("topic", "") or "the same song"
        _set_pending(uid, {"stage": "await_capture", "topic": topic,
                           "hist_len": len(_history(uid))})
        body = (
            f"The visitor wants the pending song CHANGED before "
            f"they approve: \"{job.get('note', '')}\". Rewrite "
            f"the song NOW in your reply, in persona, with the "
            f"change applied, in the EXACT format: first line "
            f"'TITLE: <title>', second line 'STYLE: <style>', "
            f"then [Section] headers with lyric lines under "
            f"each (3-6 sections, ~110-260 words of lyrics, "
            f"ORIGINAL lyrics only). Close with the same "
            f"approval ask (title, style, rough minutes, YES "
            f"tracks it / NO scraps it).")
        return _result("SONG: REWRITE", "🎵 Song — rewrite", body)
    return _down_body()


# --- Production ------------------------------------------------------------------------


def _new_job(uid: str, pending: Dict) -> Dict:
    job_id = uuid.uuid4().hex[:16]
    song = {"title": pending.get("title", "OG's Song"),
            "style": pending.get("style", ""),
            "sections": pending.get("sections", []),
            "words": pending.get("words", 0)}
    return {
        "id": job_id, "owner": _key(uid),
        "title": song["title"], "style": song["style"],
        "topic": pending.get("topic", ""),
        "song": song,
        "section_count": len(song["sections"]),
        "words": song["words"],
        "plan_ms": _plan_ms(song),
        "state": "queued", "detail": "In line — studio starting.",
        "created": time.time(), "updated": time.time(),
        "file": "", "size": 0, "duration": 0.0,
        "saved": False, "charged": False, "error": "",
    }


def _update(rec: Dict, **kw) -> Dict:
    rec.update(kw)
    rec["updated"] = time.time()
    _store_put(rec)
    return rec


def _slug(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(title or "song")).strip("-")
    return (slug[:60] or "song").lower()


def _track_duration(path: str, fallback_s: float) -> float:
    """Real duration of the finished MP3 when a probe exists
    (og_video's reader rides the imageio-ffmpeg binary that
    ships for Round 28); the plan's duration otherwise."""
    try:
        import og_video as _video
        dur = _video._media_duration(path)
        if dur and dur > 0:
            return round(float(dur), 1)
    except Exception:
        pass
    return round(float(fallback_s), 1)


def _run_job(job_id: str, uid: str) -> None:
    rec = _store_get(job_id)
    if rec is None:
        return
    song = rec.get("song") or {}
    job_dir = os.path.join(STORE_DIR, job_id)
    try:
        with _gen_slots:
            os.makedirs(job_dir, exist_ok=True)
            _update(rec, state="generating",
                    detail="In the studio — the singer's "
                           "tracking it now…")
            data = _generate_track(song)
            final = os.path.join(job_dir, "final.mp3")
            with open(final, "wb") as f:
                f.write(data)
            if not _looks_like_mp3(data):
                raise _ProviderError(
                    "bad_audio", "written file failed re-check")
            dur = _track_duration(
                final, float(rec.get("plan_ms", 0)) / 1000.0)
            _charge_song(uid)
            _update(rec, state="done",
                    detail=f"\"{rec.get('title', '')}\" — "
                           f"{rec.get('style', '')}; a real "
                           f"sung track, ready to play.",
                    file=final, size=len(data), duration=dur,
                    charged=True)
            # Notifications (optional layer): the finished
            # song also lands in the visitor's notification
            # center (+ opt-in channels). Fail-safe.
            try:
                import og_notify as _notify
                _notify.record(
                    uid, "song_done",
                    f"Your song is ready: "
                    f"\"{rec.get('title', '')}\"",
                    f"\"{rec.get('title', '')}\" — "
                    f"{rec.get('style', '')}; a real sung "
                    f"track, ready to play.")
            except Exception:
                pass
    except _ProviderError as e:
        logger.warning(f"Song job {job_id} failed ({e.kind}): {e}")
        try:
            import og_fixqueue as _fq
            _fq.record_problem(
                uid, "song", f"song:{job_id}", "song-take",
                "A song take failed",
                f"The take of \"{rec.get('title', '')}\" failed "
                f"at the studio ({e.kind}): {str(e)[:200]}",
                fix={"kind": "owner_steps",
                     "summary": "The song take didn't finish. "
                                "Nothing was charged.",
                     "steps": [
                         "Nothing was charged — the take died "
                         "before it finished.",
                         "Ask OG to track the song again; a "
                         "one-off studio failure usually clears "
                         "on a fresh try.",
                     ]})
        except Exception:
            pass
        detail = _FAIL_DETAILS.get(e.kind, _FAIL_DETAILS["http"])
        if e.suggestion:
            detail += (" The studio suggested this angle "
                       "instead: " + e.suggestion[:200])
        try:
            _update(rec, state="failed", error=str(e)[:300],
                    detail=detail)
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"Song job {job_id} failed: {e}")
        try:
            import og_fixqueue as _fq
            _fq.record_problem(
                uid, "song", f"song:{job_id}", "song-take",
                "A song take failed",
                f"The take of \"{rec.get('title', '')}\" failed: "
                f"{type(e).__name__}: {str(e)[:200]}",
                fix={"kind": "owner_steps",
                     "summary": "The song take didn't finish. "
                                "Nothing was charged.",
                     "steps": [
                         "Nothing was charged — the take died "
                         "before it finished.",
                         "Ask OG to track the song again; a "
                         "one-off failure usually clears on a "
                         "fresh try.",
                     ]})
        except Exception:
            pass
        try:
            _update(rec, state="failed", error=str(e)[:300],
                    detail=_FAIL_DETAILS["http"])
        except Exception:
            pass


# --- Locker save (Round 21 storage, reused) --------------------------------------------


class _LockerError(Exception):
    pass


def _locker_save(uid: str, tier: str, name: str, data: bytes) -> Dict:
    """Save the finished MP3 into the visitor's Round 21
    locker. Mirrors og_video's locker path (og_storage's own
    save flow refuses kinds it doesn't sniff), reusing
    og_storage's backend, quota, and record store so the file
    lists and counts exactly like any other locker file."""
    import og_storage as _storage
    if not _storage.storage_enabled():
        raise _LockerError("The locker is switched off right now.")
    quota = _storage.quota_bytes(tier)
    if quota <= 0:
        raise _LockerError(
            "Your plan doesn't carry a locker — step up a plan "
            f"and it's yours to keep: {_pro_url()}")
    used = _storage.usage_bytes(uid)
    if used + len(data) > quota:
        raise _LockerError(
            f"That track is {_storage.fmt_size(len(data))} but "
            f"you only got {_storage.fmt_size(quota - used)} "
            f"left in your locker ({_storage.fmt_size(used)} "
            f"of {_storage.fmt_size(quota)} used). Delete "
            f"something first — say \"my storage\".")
    owner = _storage.owner_of(uid)
    fid = uuid.uuid4().hex
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_",
                  os.path.basename(str(name or "song.mp3")))[:80]
    if not safe:
        safe = "song.mp3"
    key = f"{owner[:32]}/{fid}-{safe}"
    _storage.get_backend().put(key, data, "audio/mpeg")
    rec = {"id": fid, "owner": owner, "name": name,
           "size": len(data), "content_type": "audio/mpeg",
           "kind": "audio", "uploaded_at": time.time(),
           "key": key}
    _storage._insert_record(rec)
    return rec


# --- Routes ------------------------------------------------------------------------------


def _route_uid(request: Request) -> str:
    try:
        return request.cookies.get("ogai_uid", "") or ""
    except Exception:
        return ""


def _route_tier(uid: str, request: Request) -> str:
    fn = _deps.get("tier_of")
    try:
        return str(fn(uid, request) or "free") if fn else "free"
    except Exception:
        return "free"


def _job_view(rec: Optional[Dict]) -> Optional[Dict]:
    if not rec:
        return None
    state = rec.get("state", "")
    fresh = time.time() - float(rec.get("updated", 0)) <= _JOB_TTL
    if state in ("queued", "generating") \
            and time.time() - float(rec.get("updated", 0)) > _STALE_S:
        return {"id": rec["id"], "title": rec.get("title", ""),
                "state": "failed",
                "detail": "That take got cut off (the server "
                          "restarted mid-session) — nothing was "
                          "charged. Ask again and I'll re-track "
                          "it."}
    if state == "done" and (not fresh or not rec.get("file")
                            or not os.path.exists(rec.get("file", ""))):
        return None
    out = {"id": rec["id"], "title": rec.get("title", ""),
           "style": rec.get("style", ""),
           "state": state, "detail": rec.get("detail", ""),
           "saved": bool(rec.get("saved", False))}
    if state == "done":
        out["song_url"] = f"/song/download/{rec['id']}"
        out["size"] = int(rec.get("size", 0))
        out["duration"] = float(rec.get("duration", 0))
    if state == "failed":
        out["detail"] = rec.get("detail") or \
            "That take didn't make it — nothing was charged."
    return out


def register_song_routes(app):
    @app.get("/song/status")
    async def song_status(request: Request):
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
            if pending.get("sections"):
                captured = {"title": pending.get("title", ""),
                            "style": pending.get("style", ""),
                            "sections": pending.get("sections", []),
                            "words": pending.get("words", 0)}
                pending_view = {
                    "stage": "await_approval",
                    "title": captured["title"],
                    "style": captured["style"],
                    "sections": len(captured["sections"]),
                    "words": int(captured["words"]),
                    "est_min": _est_min(captured),
                    "lyrics_preview": "\n".join(
                        captured["sections"][0]["lines"][:4])
                    if captured["sections"] else "",
                    "expires_in_s": max(0, expires)}
            else:
                pending_view = {
                    "stage": pending.get("stage", "await_capture"),
                    "title": pending.get("topic", ""),
                    "style": pending.get("style_hint", ""),
                    "sections": 0, "words": 0, "est_min": 0,
                    "lyrics_preview": "",
                    "expires_in_s": max(0, expires)}
        locker_ok = False
        try:
            import og_storage as _storage
            locker_ok = bool(_storage.storage_enabled()
                             and _storage.quota_bytes(tier) > 0)
        except Exception:
            locker_ok = False
        return {"enabled": True, "tier": tier,
                "cap": int(_tiers.weekly_cap(tier, "song") or 0),
                "used": _songs_used_week(uid),
                "left": _song_week_left(uid, tier),
                "locker_ok": locker_ok,
                "pending": pending_view,
                "job": _job_view(
                    _store_latest_for_owner(_key(uid)))}

    @app.post("/song/approve")
    async def song_approve(request: Request):
        if not enabled():
            return JSONResponse({"ok": False,
                                 "error": "songs are off"},
                                status_code=404)
        uid = _route_uid(request)
        if not uid:
            return {"ok": False, "error": "no visitor identity"}
        ok, msg, rec = _approve(uid, _route_tier(uid, request))
        return {"ok": ok,
                "title": rec.get("title", "") if rec else "",
                "error": "" if ok else msg}

    @app.post("/song/decline")
    async def song_decline(request: Request):
        if not enabled():
            return JSONResponse({"ok": False,
                                 "error": "songs are off"},
                                status_code=404)
        uid = _route_uid(request)
        if not uid:
            return {"ok": False, "error": "no visitor identity"}
        ok, msg = _decline(uid)
        return {"ok": ok, "error": "" if ok else msg}

    @app.get("/song/download/{job_id}")
    async def song_download(job_id: str, request: Request):
        uid = _route_uid(request)
        rec = _store_get(job_id) if uid else None
        if not rec or rec.get("owner") != _key(uid) \
                or rec.get("state") != "done" \
                or not rec.get("file") \
                or not os.path.exists(rec.get("file", "")) \
                or time.time() - float(rec.get("updated", 0)) \
                > _JOB_TTL:
            raise HTTPException(status_code=404,
                                detail="Not found")
        return FileResponse(
            rec["file"], media_type="audio/mpeg",
            filename=_slug(rec.get("title", "song")) + ".mp3")

    @app.post("/song/save/{job_id}")
    async def song_save(job_id: str, request: Request):
        uid = _route_uid(request)
        rec = _store_get(job_id) if uid else None
        if not rec or rec.get("owner") != _key(uid) \
                or rec.get("state") != "done" \
                or not rec.get("file") \
                or not os.path.exists(rec.get("file", "")):
            raise HTTPException(status_code=404,
                                detail="Not found")
        if rec.get("saved"):
            return {"ok": True, "already": True}
        tier = _route_tier(uid, request)
        try:
            with open(rec["file"], "rb") as f:
                data = f.read()
            saved = _locker_save(
                uid, tier,
                _slug(rec.get("title", "song")) + ".mp3", data)
        except _LockerError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(f"Song locker save failed: {e}")
            return {"ok": False,
                    "error": "The locker wouldn't take it just "
                             "now — the download still works."}
        rec["saved"] = True
        _store_put(rec)
        return {"ok": True, "name": saved.get("name", "")}


# --- The seam (app.py installs this after og_watch) ---------------------------------------

_slot = {"job": None, "message": ""}


def install_song_tools(agent_instance, get_uid):
    """Wrap the agent's detect_intent + web_search hooks so
    song asks, the lyrics-first step, and the approval flow
    ride the established seam. Persona files never touched."""
    if getattr(agent_instance, "_og_songs_installed", False):
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
            job = _claim_song(str(message), uid) if uid else None
            if job:
                _slot["job"] = job
                _slot["message"] = str(message)
                if isinstance(intent, dict):
                    intent["needs_code_generation"] = False
                    if not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
        except Exception as e:
            logger.warning(f"Song trigger check failed: {e}")
            _slot["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _slot.get("job")
        message = _slot.get("message", "")
        _slot["job"] = None
        _slot["message"] = ""
        if job:
            try:
                results = song_results(job, message, get_uid())
            except Exception as e:
                logger.warning(f"Song job failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_songs_installed = True
