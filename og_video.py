"""
OG story videos (Round 28) — OG writes a short story in chat, the
visitor approves it, and OG turns it into a real downloadable MP4:
one AI picture per scene, OG's own voice (OpenAI TTS "onyx", the
/tts voice) narrating scene by scene, stitched server-side with
ffmpeg (the static binary from the imageio-ffmpeg pip package —
Render's runtime has no guaranteed system ffmpeg).

The flow (Brent's prepare -> approve -> execute rule, the Round
16/27 shape):

1. ASK. "make me a story video about X" claims a video job. The
   reply makes the agent WRITE THE STORY in the thread (title line
   + SCENE headings, 5-8 scenes, 300-550 words) and end with the
   approval ask. A per-visitor pending state is parked (30-min
   expiry). "make that a video" binds to the newest story already
   in the visitor's thread instead. NO image or TTS call happens
   before approval — the story costs nothing but chat tokens.
2. APPROVE. Chat YES / NO, or the on-page card (GET /video/status
   carries the pending with the REAL title / scene count / est.
   minutes — captured from the visitor's own history, the Round
   14/16 "what was shown is what ships" pattern; POST
   /video/approve and /video/decline are the card's doors). First
   resolution wins; a decline scraps the pending and spends
   nothing. Change requests ("make it scarier") loop back to a
   fresh story draft.
3. PRODUCE. On approval a background job runs: per scene, one
   image via og_image_gen.openai_generate_image (gpt-image-1
   path) + TTS of that scene's text (sentence-chunked), then
   ffmpeg assembly — 1920x1080, the scene picture centered over
   its own blurred, darkened stretch-fill, a slow Ken Burns zoom
   (zoompan on an upscaled still — proven clean locally), scene
   duration = its narration duration exactly (no padding). The
   title is burned onto the first seconds ONLY when the ffmpeg
   build has drawtext AND a font file exists; the imageio static
   binary has NO drawtext (verified 2026-10-09), so on Render the
   title lives in the chat, the filename, and the MP4 metadata.
4. DELIVER. Owner-only GET /video/download/<id> (24 h retention),
   an inline player + download button on the page when the job
   completes, and "Save to my locker" (POST /video/save/<id>)
   into the Round 21 locker for tiers with quota.

Caps: og_tiers kind "video" (free 1 / standard 3 / pro 10 /
blue 25 / blackout 100 per day), charged ONLY on a completed,
verified render — a failed job never eats the visitor's video.
Story cap ~550 words / ~4 min for every tier in v1 (render time
is the real cost): capture trims anything longer.

Job records ride the Round 10/18 store pattern: Postgres table
og_video_jobs when OG_MEMORY_DB_URL is set (psycopg), else a
JSON file beside the renders. Pending approvals are process-
local (like the Round 14/16 drafts): a restart mid-window drops
the pending — nothing was produced, the visitor just asks
again. A restart mid-render leaves the record in a working
state; /video/status reports it as cut off, honestly, and never
fakes a file. Renders themselves live on local disk (the
locker is the durable home, via Save).

Persona files are NEVER touched by this module.
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

import og_tiers as _tiers

logger = logging.getLogger(__name__)

STORE_DIR = os.getenv("OG_VIDEO_STORE_DIR", "video_builds")
_PENDING_TTL = 30 * 60          # story approval window
_JOB_TTL = 24 * 60 * 60         # finished renders kept 24 h
_MAX_WORDS = 550                # v1 story cap (~4 min narration)
_TRIM_WORDS = 640               # capture trims anything past this
_MAX_SCENES = 8
_MIN_SCENES = 5
_WPM = 150.0                    # narration pace for estimates
_TTS_MODEL = "tts-1"
_TTS_VOICE = "onyx"             # the same voice as /tts
_FPS = 24
_SEG_FPS = 12   # segment render rate (28.2 — see _render_segment)

# Bound by app.py via bind_app (usage store, tier resolution,
# the OpenAI key, and the visitor-history reader).
_deps: Dict = {}


def bind_app(deps):
    _deps.update(deps)


def _pro_url() -> str:
    base = os.getenv("OG_PUBLIC_URL", "https://og-ai-service.onrender.com")
    return base.rstrip("/") + "/pro"


def _api_key() -> str:
    fn = _deps.get("get_api_key")
    try:
        return str(fn() or "") if fn else ""
    except Exception:
        return ""


def enabled() -> bool:
    """Story videos need the OpenAI key (story-adjacent calls,
    images, TTS) and an ffmpeg binary (imageio-ffmpeg's static
    build, or a system ffmpeg). Missing either = dark."""
    return bool(_api_key()) and bool(_ffmpeg_exe())


# --- ffmpeg -------------------------------------------------------------------


_ffmpeg_cache: Dict = {}


def _ffmpeg_exe() -> str:
    if "exe" not in _ffmpeg_cache:
        exe = ""
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe() or ""
        except Exception:
            exe = ""
        if not exe or not os.path.exists(exe):
            exe = shutil.which("ffmpeg") or ""
        _ffmpeg_cache["exe"] = exe
    return _ffmpeg_cache["exe"]


def _ffmpeg_has_drawtext() -> bool:
    if "drawtext" not in _ffmpeg_cache:
        ok = False
        exe = _ffmpeg_exe()
        if exe:
            try:
                out = subprocess.run(
                    [exe, "-hide_banner", "-h", "filter=drawtext"],
                    capture_output=True, text=True, timeout=20)
                ok = "drawtext" in (out.stdout or "") \
                    and "Unknown filter" not in (out.stdout or "")
            except Exception:
                ok = False
        _ffmpeg_cache["drawtext"] = ok
    return _ffmpeg_cache["drawtext"]


_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)


def _find_font() -> str:
    env = os.getenv("OG_VIDEO_FONT", "").strip()
    if env and os.path.exists(env):
        return env
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return ""


def _run_ffmpeg(args: List[str], timeout: int = 1200) -> None:
    exe = _ffmpeg_exe()
    if not exe:
        raise RuntimeError("ffmpeg unavailable")
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y"] + args
    # Renders are background work on a small shared instance:
    # deprioritize them so the site itself stays responsive
    # while a video cooks (observed live: brief 503s mid-stitch).
    nice = shutil.which("nice")
    if nice:
        cmd = [nice, "-n", "10"] + cmd
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed: {(proc.stderr or '')[-400:]}")


def _media_duration(path: str) -> float:
    """Duration in seconds via ffprobe when present, else by
    parsing ffmpeg -i output. 0.0 when unreadable."""
    probe = shutil.which("ffprobe")
    if probe:
        try:
            out = subprocess.run(
                [probe, "-v", "error", "-show_entries",
                 "format=duration", "-of", "csv=p=0", path],
                capture_output=True, text=True, timeout=30)
            return float((out.stdout or "0").strip() or 0)
        except Exception:
            pass
    exe = _ffmpeg_exe()
    if exe:
        try:
            out = subprocess.run([exe, "-hide_banner", "-i", path],
                                 capture_output=True, text=True, timeout=30)
            m = re.search(r"Duration: (\d+):(\d+):([\d.]+)",
                          out.stderr or "")
            if m:
                return (int(m.group(1)) * 3600 + int(m.group(2)) * 60
                        + float(m.group(3)))
        except Exception:
            pass
    return 0.0


def _wav_duration(path: str) -> float:
    import wave
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


# --- Usage / caps (the bound app usage store, og_browser pattern) -------------


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _usage_get(key: str):
    with _deps["usage_lock"]:
        store = _deps["load_usage"]()
    entry = store.get(key)
    return dict(entry) if isinstance(entry, dict) else None


def _videos_used_today(uid: str) -> int:
    entry = _usage_get(f"video:{uid}")
    if not entry or entry.get("date") != _today():
        return 0
    return int(entry.get("count", 0))


def _video_left(uid: str, tier: str) -> int:
    return max(0, int(_tiers.cap(tier, "video"))
               - _videos_used_today(uid))


def _charge_video(uid: str) -> None:
    """Spend one unit of the daily video cap. Called ONLY after a
    render completes and verifies — failures never charge."""
    key = f"video:{uid}"
    with _deps["usage_lock"]:
        store = _deps["load_usage"]()
        entry = store.get(key)
        if not isinstance(entry, dict) or entry.get("date") != _today():
            entry = {"date": _today(), "count": 0}
        entry["count"] = int(entry.get("count", 0)) + 1
        store[key] = entry
        _deps["save_usage"](store)


def _tier_now(uid: str) -> str:
    fn = _deps.get("get_tier")
    try:
        return str(fn() or "free") if fn else "free"
    except Exception:
        return "free"


# --- Pending approvals (process-local, the Round 14/16 shape) -----------------

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


# --- Job records (Postgres when OG_MEMORY_DB_URL, else JSON) ------------------

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — JSON backend is used
    psycopg = None

_jobs_lock = threading.Lock()
_jobs_mem: Dict[str, Dict] = {}
_render_slots = threading.Semaphore(2)


def _key(uid: str) -> str:
    return hashlib.sha256(str(uid).encode()).hexdigest()[:32]


def _json_store_path() -> str:
    os.makedirs(STORE_DIR, exist_ok=True)
    return os.path.join(STORE_DIR, "video_jobs.json")


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_video_jobs ("
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
                        "INSERT INTO og_video_jobs (id, owner, updated,"
                        " data) VALUES (%s, %s, %s, %s) ON CONFLICT"
                        " (id) DO UPDATE SET owner = EXCLUDED.owner,"
                        " updated = EXCLUDED.updated,"
                        " data = EXCLUDED.data",
                        (rec["id"], rec.get("owner", ""), rec["updated"],
                         json.dumps(rec)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Video job DB write failed: {e}")
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
        logger.warning(f"Video job JSON write failed: {e}")


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
                        "SELECT data FROM og_video_jobs WHERE id = %s",
                        (job_id,))
                    row = cur.fetchone()
            if row:
                rec = json.loads(row[0])
                with _jobs_lock:
                    _jobs_mem[job_id] = rec
                return dict(rec)
        except Exception as e:
            logger.warning(f"Video job DB read failed: {e}")
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
        logger.warning(f"Video job JSON read failed: {e}")
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
                        "SELECT data FROM og_video_jobs WHERE owner"
                        " = %s ORDER BY updated DESC LIMIT 1",
                        (owner,))
                    row = cur.fetchone()
            if row:
                cands.append(json.loads(row[0]))
        except Exception as e:
            logger.warning(f"Video job DB scan failed: {e}")
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
        logger.warning(f"Video job JSON scan failed: {e}")
    for rec in cands:
        if best is None or float(rec.get("updated", 0)) \
                > float(best.get("updated", 0)):
            best = rec
    return best


def list_jobs_for_owner(uid: str) -> List[Dict]:
    """All of the visitor's OWN video jobs, newest first
    (Library). Read-only: the same mem + DB + JSON scan
    _store_latest_for_owner does, but every record comes back
    (deduped by id) instead of only the latest. Retention is
    NOT re-decided here — the caller passes each record
    through _job_view, which already hides stale/gone jobs
    exactly the way /video/status does."""
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
                        "SELECT data FROM og_video_jobs WHERE owner"
                        " = %s", (owner,))
                    rows = cur.fetchall()
            for row in rows:
                rec = json.loads(row[0])
                found[rec["id"]] = rec
        except Exception as e:
            logger.warning(f"Video job DB list failed: {e}")
    try:
        path = _json_store_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            for v in data.values():
                if v.get("owner") == owner:
                    found[v["id"]] = v
    except Exception as e:
        logger.warning(f"Video job JSON list failed: {e}")
    return sorted(found.values(),
                  key=lambda r: float(r.get("created", 0)),
                  reverse=True)


def purge_owner(uid: str):
    """Account deletion (Round 36, og_store_ready): every
    video job this visitor owns — the in-memory cache, the
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
                        "DELETE FROM og_video_jobs WHERE owner = %s",
                        (owner,))
                conn.commit()
        except Exception as e:
            logger.warning(f"Video job DB purge failed: {e}")
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
        logger.warning(f"Video job JSON purge failed: {e}")
    for jid in ids:
        try:
            shutil.rmtree(os.path.join(STORE_DIR, jid),
                          ignore_errors=True)
        except Exception:
            pass


def _active_job(uid: str) -> Optional[Dict]:
    rec = _store_latest_for_owner(_key(uid))
    if rec and rec.get("state") in (
            "queued", "drawing", "voicing", "stitching"):
        if time.time() - float(rec.get("updated", 0)) > 45 * 60:
            return None     # stale working record = a cut-off render
        return rec
    return None


# --- Story parsing ------------------------------------------------------------

_TITLE_RE = re.compile(
    r"^\s*(?:#{1,3}\s*)?(?:\*\*)?TITLE\s*[:\-—]\s*(.+?)(?:\*\*)?\s*$",
    re.IGNORECASE)
_SCENE_RE = re.compile(
    r"^\s*(?:#{1,4}\s*)?(?:\*\*|__)?\s*SCENE\s+(\d+)\b[^\n]*"
    r"(?:\*\*|__)?\s*$", re.IGNORECASE)


def _clean_text(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text or "")
    text = re.sub(r"(?m)^#{1,4}\s*", "", text)
    text = text.replace("`", "")
    return text.strip()


def _word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", text or "") if w])


def split_scenes_by_size(text: str, words: int) -> List[str]:
    """Paragraph/sentence grouping into 5-8 scenes when the
    story carries no SCENE headings (the module-side split)."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    units = paras if len(paras) >= 4 else \
        [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not units:
        return []
    target = max(_MIN_SCENES, min(_MAX_SCENES,
                                  int(round(words / 78.0)) or 1))
    per = max(1, int(round(len(units) / float(target))))
    scenes = []
    for i in range(0, len(units), per):
        chunk = " ".join(units[i:i + per]).strip()
        if chunk:
            scenes.append(chunk)
    while len(scenes) > _MAX_SCENES:
        scenes[-2] = scenes[-2] + " " + scenes[-1]
        scenes.pop()
    merged: List[str] = []
    for sc in scenes:
        if merged and _word_count(sc) < 12:
            merged[-1] = merged[-1] + " " + sc
        else:
            merged.append(sc)
    return merged


def parse_story(text: str) -> Optional[Dict]:
    """Parse a story out of an assistant message: optional TITLE
    line + SCENE headings, with the module-side split as the
    fallback. Returns {title, scenes, words} or None when the
    text isn't a story (too short / no prose)."""
    if not text:
        return None
    lines = str(text).splitlines()
    title = ""
    body_start = 0
    for i, line in enumerate(lines[:6]):
        m = _TITLE_RE.match(line)
        if m:
            title = m.group(1).strip().strip("*").strip()
            body_start = i + 1
            break
        # The served text may carry an interjection before the
        # TITLE marker (the register pass works the whole reply);
        # take whatever follows the marker on that line.
        j = line.upper().rfind("TITLE:")
        if j >= 0 and len(line) - (j + 6) >= 2:
            title = line[j + 6:].strip().strip("*").strip()
            body_start = i + 1
            break
    title = title.strip().strip('"“”').strip()
    body = "\n".join(lines[body_start:])
    heads = [m for m in
             (_SCENE_RE.match(l) for l in body.splitlines()) if m]
    scenes: List[str] = []
    if len(heads) >= 3:
        parts = re.split(
            r"(?im)^\s*(?:#{1,4}\s*)?(?:\*\*|__)?\s*SCENE\s+\d+\b"
            r"[^\n]*(?:\*\*|__)?\s*$", body)
        scenes = [_clean_text(p) for p in parts if _clean_text(p)]
    if len(scenes) < 3:
        cleaned = _clean_text(body)
        words = _word_count(cleaned)
        if words < 120:
            return None
        scenes = split_scenes_by_size(cleaned, words)
    scenes = [s for s in scenes if _word_count(s) >= 8]
    if len(scenes) < 3:
        return None
    # v1 length cap: trim whole scenes from the end.
    while len(scenes) > 1 and \
            sum(_word_count(s) for s in scenes) > _TRIM_WORDS:
        scenes.pop()
    words = sum(_word_count(s) for s in scenes)
    if words < 120:
        return None
    if not title:
        for line in lines[:4]:
            cand = _clean_text(line).strip('"“”').strip()
            if cand and len(cand) <= 80 and \
                    not _SCENE_RE.match(line):
                title = cand
                break
    return {"title": (title or "OG's Story")[:90],
            "scenes": scenes[:_MAX_SCENES], "words": words}


def _history(uid: str) -> List[Dict]:
    fn = _deps.get("load_history")
    if fn is None:
        return []
    try:
        return fn(uid) or []
    except Exception as e:
        logger.warning(f"Video history read failed: {e}")
        return []


def _capture_from_history(uid: str, pending: Dict) -> Optional[Dict]:
    """Lift the story the visitor approved out of their own
    thread: the newest assistant message at/after the pending's
    history watermark that parses as a story. For thread-bound
    ("make that a video") pendings the watermark is 0 — the
    story is already there."""
    hist = _history(uid)
    start = int(pending.get("hist_len", 0))
    for entry in reversed(hist[start:]):
        if not isinstance(entry, dict) \
                or entry.get("role") != "assistant":
            continue
        parsed = parse_story(str(entry.get("content", "")))
        if parsed:
            return parsed
    if pending.get("stage") == "await_approval":
        for entry in reversed(hist):
            if not isinstance(entry, dict) \
                    or entry.get("role") != "assistant":
                continue
            parsed = parse_story(str(entry.get("content", "")))
            if parsed:
                return parsed
    return None


def _ensure_captured(uid: str, pending: Dict) -> Optional[Dict]:
    """Lazy capture: fill a pending with the story's real title /
    scenes / estimate the first time anyone looks after the
    story lands. Returns the updated pending or None."""
    if pending.get("scenes"):
        return pending
    parsed = _capture_from_history(uid, pending)
    if not parsed:
        return None
    pending = dict(pending)
    pending.update({
        "stage": "await_approval",
        "title": parsed["title"], "scenes": parsed["scenes"],
        "words": parsed["words"]})
    _set_pending(uid, pending)
    return pending


def _est_min(words: int) -> float:
    return round(words / _WPM, 1)


def split_tts_chunks(text: str, limit: int = 380) -> List[str]:
    """Sentence-boundary chunks under the TTS input limit."""
    sentences = [s.strip() for s in
                 re.split(r"(?<=[.!?])\s+", _clean_text(text))
                 if s.strip()]
    chunks: List[str] = []
    cur = ""
    for s in sentences:
        if len(s) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            for i in range(0, len(s), limit):
                chunks.append(s[i:i + limit])
            continue
        if cur and len(cur) + 1 + len(s) > limit:
            chunks.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        chunks.append(cur)
    return chunks


# --- Narration (grounded result blocks the persona voices) --------------------


def _result(tag: str, title: str, body: str, href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body, "href": href}]


def _cap_body(tier: str, note: str = "") -> List[Dict]:
    body = (f"The visitor is at today's story-video cap for their "
            f"plan ({int(_tiers.cap(tier, 'video'))} per day) — "
            f"story videos reset tomorrow. {note} Tell them plainly, "
            f"in persona: the story itself they can still read right "
            f"here in chat any time; the VIDEO is what's capped. "
            f"Higher plans make more videos a day: {_pro_url()}")
    return _result("STORY-VIDEO: AT-CAP", "🎬 Story videos — capped",
                   body)


def _down_body() -> List[Dict]:
    return _result(
        "STORY-VIDEO: DOWN", "🎬 Story video — lab down",
        "The story-video lab is down right now (no render "
        "pipeline). Tell the visitor plainly, in persona — the "
        "story itself still works: offer to write it in chat so "
        "they at least get the story.")


# --- Claim table ----------------------------------------------------------------

_YES_WORDS = {
    "yes", "yeah", "yep", "yup", "ya", "ok", "okay", "sure",
    "approve", "approved", "go", "do it", "make it", "lets go",
    "let's go", "yes please", "yes make it", "yes do it",
    "make the video", "do the video", "👍",
}
_NO_WORDS = {
    "no", "nope", "nah", "decline", "declined", "scrap it",
    "never mind", "nevermind", "forget it", "cancel", "stop",
    "don't", "dont", "no thanks",
}
_VIDEO_VERB = re.compile(
    r"\b(make|create|turn|render|generate|produce|build)\b", re.I)
_THREAD_BIND = re.compile(
    r"\b(make|turn|render|convert)\b[^.?!]{0,40}\b(that|this|it)\b"
    r"[^.?!]{0,20}\bvideo\b|\bmake that a video\b|\bvideo of that\b|"
    r"\bturn that story\b", re.I)
_ABOUT = re.compile(r"\babout\s+(.+)$", re.I)
_STORY_VIDEO = re.compile(
    r"\b(story\s+video|video\s+story|video\s+of\s+(?:the|that|this)"
    r"\s+story)\b", re.I)
_MAKE_VIDEO = re.compile(
    r"\b(make|create|render|generate|produce)\b[^.?!]{0,30}"
    r"\bvideo\b", re.I)
# Round 40: "I want a video about …" — the want-form carries an
# article (or none), never "that/this" (those point at an existing
# video, not a new one).
_WANT_VIDEO = re.compile(
    r"\bi want\s+(?:a\s+|an\s+|the\s+|some\s+)?(?:story\s+)?video\b",
    re.I)


def _norm(message: str) -> str:
    return " ".join(str(message or "").lower().split()).strip(" .!")


def _browser_has_pending(uid: str) -> bool:
    """A bare YES/NO owed to a parked browser action is never
    stolen (the Round 21/27 rule)."""
    try:
        import og_browser as _browser
        pend = _browser._get_pending(uid)
        return bool(pend and pend.get("kind") == "action")
    except Exception:
        return False


def _claim_job(message: str, uid: str) -> Optional[Dict]:
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
            if len(norm) >= 2:
                return {"kind": "topic",
                        "topic": raw.strip()[:200]}
            return None
        # A fresh ask with its own topic replaces the parked
        # story instead of approving it by accident.
        if _MAKE_VIDEO.search(low) and _ABOUT.search(raw):
            m = _ABOUT.search(raw.strip())
            return {"kind": "replace",
                    "topic": m.group(1).strip().strip(".!")[:200]}
        # A live story is waiting on approval.
        if norm in _YES_WORDS:
            if _browser_has_pending(uid):
                return None
            return {"kind": "approve"}
        if norm in _NO_WORDS:
            if _browser_has_pending(uid):
                return None
            return {"kind": "decline"}
        if re.search(r"\b(make|turn)\b[^.?!]{0,30}\bvideo\b", low) \
                or norm in ("video", "make it a video"):
            return {"kind": "approve"}
        if re.search(r"\b(change|rewrite|redo|make it|instead|"
                     r"add|remove|scarier|funnier|darker|shorter|"
                     r"longer)\b", low) and len(low) < 300:
            return {"kind": "revise", "note": raw.strip()[:300]}
        return None
    # No pending — declines/approvals alone claim nothing.
    if "game" in low and "video" in low:
        return None     # "video game" belongs to Unity / chat
    if _THREAD_BIND.search(low):
        return {"kind": "from_thread"}
    if _STORY_VIDEO.search(low) or _MAKE_VIDEO.search(low) \
            or _WANT_VIDEO.search(low):
        topic = ""
        m = _ABOUT.search(raw.strip())
        if m:
            topic = m.group(1).strip().strip(".!")[:200]
        topic = re.sub(
            r"^(a|an|the)\s+(story\s+video|video)\s+(about\s+)?", "",
            topic, flags=re.I).strip()
        return {"kind": "new", "topic": topic}
    return None


# --- Flow results -----------------------------------------------------------------


def _story_instruction(topic: str) -> str:
    return (
        f"The visitor wants a STORY VIDEO about: {topic}. Do this "
        f"in your reply, in persona, right now — STORY FIRST, the "
        f"video comes after they approve: (1) Write the story "
        f"itself: aim for {_MIN_SCENES}-{_MAX_SCENES} scenes, "
        f"300-{_MAX_WORDS} words of story total. Format it EXACTLY "
        f"like this: a first line 'TITLE: <the title>', then for "
        f"each scene a line 'SCENE 1', 'SCENE 2', ... with that "
        f"scene's prose under it. Address the story to an adult "
        f"audience in your own voice. (2) After the story, close "
        f"with the approval ask, in persona: name the title, the "
        f"scene count, and the rough video length (the story's "
        f"word count divided by 150 is the minutes), and tell them "
        f"replying YES gets it cooked into a real video — a "
        f"picture per scene, your voice narrating, a download "
        f"link right here — and NO scraps it. Do NOT say the "
        f"video exists yet; nothing renders before their YES.")


def _start_story(uid: str, topic: str) -> List[Dict]:
    hist_len = len(_history(uid))
    _set_pending(uid, {"stage": "await_capture", "topic": topic,
                       "hist_len": hist_len})
    return _result("STORY-VIDEO: WRITE",
                   "🎬 Story video — story first",
                   _story_instruction(topic))


def _approve(uid: str, tier: str):
    """Shared resolution for the chat YES and the card door.
    Returns (ok, message, rec)."""
    pending = _get_pending(uid)
    if pending is None:
        return False, "Nothing is waiting on approval — it " \
            "expired or was already answered.", None
    pending = _ensure_captured(uid, pending) or pending
    if not pending.get("scenes"):
        _clear_pending(uid)
        return False, "I couldn't find that story in the thread " \
            "anymore, so there's nothing to render. Ask me for " \
            "the story again and we'll redo it.", None
    if _active_job(uid):
        _clear_pending(uid)
        return False, "A video is already cooking for you — " \
            "let that one finish first.", None
    if _video_left(uid, tier) <= 0:
        _clear_pending(uid)
        return False, "You're at today's story-video cap for " \
            "your plan, so this one can't render. The story " \
            "itself stays right here in chat.", None
    rec = _new_job(uid, pending)
    _clear_pending(uid)
    _store_put(rec)
    thread = threading.Thread(target=_run_job,
                              args=(rec["id"], uid), daemon=True)
    thread.start()
    return True, f"Locked in — cooking \"{rec['title']}\" now.", rec


def _decline(uid: str):
    pending = _get_pending(uid)
    if pending is None:
        return False, "Nothing was waiting — no story video " \
            "is pending."
    _clear_pending(uid)
    return True, "Scrapped — nothing renders, nothing charged."


def video_results(job: Dict, message: str, uid: str) -> List[Dict]:
    kind = job.get("kind", "")
    tier = _tier_now(uid)
    if kind == "new":
        if _active_job(uid):
            return _result(
                "STORY-VIDEO: BUSY", "🎬 Story video — one cooking",
                "A story video is ALREADY rendering for this "
                "visitor. Tell them, in persona: one at a time — "
                "the cooking one lands right here in the thread "
                "when it's done, then they can line up the next.")
        if _video_left(uid, tier) <= 0:
            return _cap_body(tier)
        topic = job.get("topic", "")
        if not topic:
            _set_pending(uid, {"stage": "await_topic",
                               "hist_len": len(_history(uid))})
            return _result(
                "STORY-VIDEO: NEED-TOPIC",
                "🎬 Story video — needs a topic",
                "The visitor wants a story video but didn't say "
                "what about. Ask them, in persona, in one line: "
                "what's the story about? Whatever they answer "
                "next becomes the topic — no need for them to "
                "repeat 'video'.")
        return _start_story(uid, topic)
    if kind == "topic":
        topic = job.get("topic", "")
        _clear_pending(uid)
        if _video_left(uid, tier) <= 0:
            return _cap_body(tier)
        return _start_story(uid, topic)
    if kind == "replace":
        _clear_pending(uid)
        if _active_job(uid):
            return _result(
                "STORY-VIDEO: BUSY", "🎬 Story video — one cooking",
                "A story video is ALREADY rendering for this "
                "visitor. Tell them, in persona: one at a time — "
                "wait for the cooking one to land in the thread.")
        if _video_left(uid, tier) <= 0:
            return _cap_body(tier)
        return _start_story(uid, job.get("topic", ""))
    if kind == "from_thread":
        if _active_job(uid):
            return _result(
                "STORY-VIDEO: BUSY", "🎬 Story video — one cooking",
                "A story video is ALREADY rendering for this "
                "visitor. Tell them, in persona: one at a time — "
                "wait for the cooking one to land in the thread.")
        if _video_left(uid, tier) <= 0:
            return _cap_body(tier)
        pending = {"stage": "await_approval", "hist_len": 0,
                   "topic": ""}
        _set_pending(uid, pending)
        captured = _ensure_captured(uid, _get_pending(uid) or pending)
        if not captured:
            _clear_pending(uid)
            _set_pending(uid, {"stage": "await_topic",
                               "hist_len": len(_history(uid))})
            return _result(
                "STORY-VIDEO: NO-STORY",
                "🎬 Story video — no story yet",
                "The visitor asked to make a video of a story, "
                "but there's no story in this thread yet. Tell "
                "them, in persona: give me a topic and I'll write "
                "the story first, show it, and only cook the "
                "video after your YES. Whatever topic they answer "
                "with becomes the story.")
        scenes = captured["scenes"]
        body = (
            f"The visitor wants THAT story turned into a video. "
            f"Present this approval ask, in persona, with these "
            f"REAL numbers (do not invent different ones): the "
            f"story is \"{captured['title']}\", "
            f"{len(scenes)} scenes, about "
            f"{_est_min(captured['words'])} minutes of video, "
            f"a picture per scene, your voice narrating. Replying "
            f"YES cooks it (one of their "
            f"{int(_tiers.cap(tier, 'video'))} daily story "
            f"videos); NO scraps it. Nothing renders before "
            f"their YES. Videos left today: "
            f"{_video_left(uid, tier)}.")
        return _result("STORY-VIDEO: APPROVE?",
                       "🎬 Story video — approval", body)
    if kind == "approve":
        ok, msg, rec = _approve(uid, tier)
        if not ok:
            return _result("STORY-VIDEO: APPROVE-FAILED",
                           "🎬 Story video — not started", msg +
                           " Tell the visitor exactly that, in "
                           "persona — plainly, no dressing it up.")
        body = (
            f"APPROVED — production has STARTED for "
            f"\"{rec['title']}\": {rec['scene_count']} scenes. "
            f"Tell the visitor, in persona: locked in, it's "
            f"cooking — drawing a picture per scene, voicing the "
            f"whole thing, stitching it into a real video file. "
            f"It lands RIGHT HERE in this thread with a player "
            f"and a download button when it's done (a few "
            f"minutes for a story this size). They don't need to "
            f"do anything — and it only counts against their "
            f"daily videos when it FINISHES, so a failed render "
            f"costs them nothing.")
        return _result("STORY-VIDEO: COOKING",
                       "🎬 Story video — cooking", body)
    if kind == "decline":
        ok, msg = _decline(uid)
        return _result(
            "STORY-VIDEO: DECLINED", "🎬 Story video — scrapped",
            msg + " Tell the visitor that, in persona, briefly — "
            "the story itself stays in the thread if they want "
            "to read it, and a fresh ask starts a fresh story.")
    if kind == "revise":
        pending = _get_pending(uid) or {}
        topic = pending.get("topic", "") or "the same story"
        _set_pending(uid, {"stage": "await_capture", "topic": topic,
                           "hist_len": len(_history(uid))})
        body = (
            f"The visitor wants the pending story video's story "
            f"CHANGED before they approve: \"{job.get('note', '')}\""
            f". Rewrite the story NOW in your reply, in persona, "
            f"with the change applied, in the EXACT format: first "
            f"line 'TITLE: <title>', then 'SCENE 1', 'SCENE 2'... "
            f"with prose under each ({_MIN_SCENES}-{_MAX_SCENES} "
            f"scenes, 300-{_MAX_WORDS} words). Close with the same "
            f"approval ask (title, scenes, rough minutes from "
            f"words/150, YES cooks it / NO scraps it).")
        return _result("STORY-VIDEO: REWRITE",
                       "🎬 Story video — rewrite", body)
    return _down_body()


# --- Production -------------------------------------------------------------------


def _new_job(uid: str, pending: Dict) -> Dict:
    job_id = uuid.uuid4().hex[:16]
    return {
        "id": job_id, "owner": _key(uid),
        "title": pending.get("title", "OG's Story"),
        "topic": pending.get("topic", ""),
        "scenes": pending.get("scenes", []),
        "scene_count": len(pending.get("scenes", [])),
        "words": pending.get("words", 0),
        "state": "queued", "detail": "In line — render starting.",
        "scenes_done": 0, "created": time.time(), "updated": time.time(),
        "file": "", "size": 0, "duration": 0.0,
        "saved": False, "charged": False, "error": "",
    }


def _update(rec: Dict, **kw) -> Dict:
    rec.update(kw)
    rec["updated"] = time.time()
    _store_put(rec)
    return rec


def _slug(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(title or "story")).strip("-")
    return (slug[:60] or "story").lower()


_PROFANITY = re.compile(
    r"\b(fuck\w*|shit\w*|bitch\w*|damn|nigga\w*|whore\w*|slut\w*|"
    r"pussy|dick|cock|asshole|bastard)\b", re.I)


def _image_prompt(title: str, scene: str, retry: bool = False) -> str:
    excerpt = _clean_text(scene)
    if retry:
        excerpt = _PROFANITY.sub("", excerpt)
    excerpt = " ".join(excerpt.split())[:300]
    return (f"Cinematic storybook illustration for a scene from "
            f"\"{title}\": {excerpt} — rich detail, dramatic "
            f"lighting, painterly, widescreen mood. No text, no "
            f"words, no letters, no watermark.")


# --- Per-scene upstream deadlines (28.3) -------------------------------------
#
# httpx timeouts are per-operation: a response that keeps
# trickling bytes (a "slow drip") resets the read timer
# forever, so ONE hung image or TTS call could freeze a whole
# render until the 45-minute stale rule noticed (28.0 live
# attempt 3 hung at "voicing scene 1"; the first post-upgrade
# verification cook sat on scene 4's picture call for the
# full 45 minutes). Every
# upstream call in the cook path now ALSO runs under a TOTAL
# deadline: the call runs in a daemon worker thread and the
# job thread waits at most OG_VIDEO_SCENE_DEADLINE_S for it.
# A call that blows the deadline is retried ONCE; a second
# blow raises _SceneStall and the job fails honestly at that
# scene — nothing is charged (the completion-only charge
# rule is untouched). The leaked worker is a daemon doing
# one HTTP call; it dies with its response or the process.
# (The ffmpeg steps already carry subprocess timeouts.)

_SCENE_DEADLINE_DEFAULT = 120.0


def _scene_deadline_s() -> float:
    try:
        v = float(os.environ.get("OG_VIDEO_SCENE_DEADLINE_S", "")
                  or _SCENE_DEADLINE_DEFAULT)
    except (TypeError, ValueError):
        return _SCENE_DEADLINE_DEFAULT
    return v if v > 0 else _SCENE_DEADLINE_DEFAULT


class _SceneStall(Exception):
    """One scene's upstream call hung past the total deadline
    twice. `what` is "picture" or "narration"."""

    def __init__(self, what: str):
        super().__init__(f"{what} call hung past the "
                         f"deadline twice")
        self.what = what


def _with_deadline(fn, what: str):
    """Run fn() under the per-scene total deadline; retry
    ONCE on a timeout; raise _SceneStall on a second timeout.
    Any other exception passes straight through — the wrapped
    calls carry their own retry logic for ordinary failures,
    and only a hang earns the extra attempt."""
    deadline = _scene_deadline_s()
    for _attempt in (1, 2):
        box: Dict = {}

        def _target():
            try:
                box["value"] = fn()
            except BaseException as e:  # re-raised in caller
                box["error"] = e

        worker = threading.Thread(target=_target, daemon=True)
        worker.start()
        worker.join(deadline)
        if worker.is_alive():
            continue  # timed out — one retry, then _SceneStall
        if "error" in box:
            raise box["error"]
        return box.get("value")
    raise _SceneStall(what)


def _fetch_image_bytes(api_key: str, title: str,
                       scene: str) -> bytes:
    """One scene picture via the existing og_image_gen path.
    Retries once with a sanitized prompt (the persona's prose
    can trip the image filter); raises on total failure."""
    import httpx
    last_error: Optional[Exception] = None
    for retry in (False, True):
        try:
            src, _model = asyncio.run(
                _img_openai(api_key, _image_prompt(title, scene, retry)))
        except Exception as e:
            last_error = e
            continue
        try:
            if src.startswith("data:"):
                return base64.b64decode(src.split(",", 1)[1])
            if src.startswith("http"):
                with httpx.Client(timeout=60) as client:
                    r = client.get(src)
                if r.status_code == 200 and r.content:
                    return r.content
            raise RuntimeError("image came back in a shape "
                               "we can't use")
        except Exception as e:
            last_error = e
    raise RuntimeError(f"image generation failed: {last_error}")


async def _img_openai(api_key: str, prompt: str):
    import og_image_gen as _img_gen
    return await _img_gen.openai_generate_image(api_key, prompt)


def _tts_scene(api_key: str, scene: str, out_mp3: str) -> None:
    """Narrate one scene (sentence-chunked) into a single MP3."""
    import httpx
    chunks = split_tts_chunks(scene)
    if not chunks:
        raise RuntimeError("scene has no narratable text")
    parts = []
    with httpx.Client(timeout=90) as client:
        for i, chunk in enumerate(chunks):
            def _post(chunk=chunk):
                r = client.post(
                    "https://api.openai.com/v1/audio/speech",
                    headers={"Authorization":
                             f"Bearer {api_key}"},
                    json={"model": _TTS_MODEL,
                          "voice": _TTS_VOICE,
                          "input": chunk,
                          "response_format": "mp3"})
                if r.status_code != 200:
                    raise RuntimeError(
                        f"TTS answered {r.status_code}: "
                        f"{r.text[:160]}")
                return r.content
            # 28.3: total deadline per chunk call — the
            # client's 90 s read timeout alone does not bound
            # a slow-drip response.
            content = _with_deadline(_post, "narration")
            part = f"{out_mp3}.part{i}"
            with open(part, "wb") as f:
                f.write(content)
            parts.append(part)
    if len(parts) == 1:
        os.replace(parts[0], out_mp3)
        return
    list_path = out_mp3 + ".list"
    with open(list_path, "w", encoding="utf-8") as f:
        for part in parts:
            f.write(f"file '{os.path.abspath(part)}'\n")
    _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", list_path,
                 "-c:a", "libmp3lame", "-q:a", "3", out_mp3],
                timeout=300)
    for part in parts:
        try:
            os.remove(part)
        except OSError:
            pass


def _render_segment(img: str, wav: str, dur: float, out: str,
                    title: str = "") -> None:
    """One scene segment: the picture centered (slow Ken Burns
    zoom) over its own blurred, darkened stretch-fill; the
    scene's narration as the audio; duration = narration
    duration exactly. Encoder is ultrafast/crf22 on purpose:
    the live instance throttles hard under sustained CPU (a
    veryfast render of a ~3-min video stitched for over an
    hour in the first live proof) — for a narrated slideshow
    the speed matters more than the last compression gains.
    Segments render at 12 fps (28.2): a slow zoom moves a
    fiftieth of a percent per frame at 24 fps, so halving the
    frame rate is invisible in the product and halves the
    per-frame filter + encode work — the zoompan source is
    also sized 1152 (just over the 1080 window) instead of
    1296. The delivered file stays 1920x1080."""
    job_dir = os.path.dirname(out)
    bg = os.path.join(job_dir, "bg-" + os.path.basename(out) + ".png")
    _run_ffmpeg(["-i", img, "-vf",
                 "scale=1920:1080:force_original_aspect_ratio="
                 "increase,crop=1920:1080,gblur=sigma=26,"
                 "eq=brightness=-0.18",
                 "-frames:v", "1", bg], timeout=300)
    frames = max(2, int(round(dur * _SEG_FPS)))
    graph = (
        f"[1:v]scale=1152:1152,"
        f"zoompan=z='1+0.08*on/{frames}':x='iw/2-(iw/zoom/2)':"
        f"y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1080:fps={_SEG_FPS}[fg];"
        f"[0:v][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]")
    maps = ["[v]"]
    if title and _ffmpeg_has_drawtext() and _find_font():
        safe = title.replace("'", "").replace(":", " ").replace(
            "\\", "")
        graph += (f";[v]drawtext=fontfile={_find_font()}:"
                  f"text='{safe}':fontsize=58:fontcolor=white:"
                  f"borderw=3:bordercolor=black:x=(w-text_w)/2:"
                  f"y=64:enable='lt(t,3)'[vt]")
        maps = ["[vt]"]
    _run_ffmpeg(["-loop", "1", "-i", bg, "-loop", "1", "-i", img,
                 "-i", wav, "-filter_complex", graph,
                 "-map", maps[0], "-map", "2:a",
                 "-t", f"{dur:.3f}", "-r", str(_SEG_FPS),
                 "-c:v", "libx264", "-preset", "ultrafast",
                 "-crf", "22", "-c:a", "aac", "-b:a", "128k",
                 "-ar", "44100", "-ac", "2", out],
                timeout=1800)
    try:
        os.remove(bg)
    except OSError:
        pass


def _verify_final(path: str, expect_dur: float) -> float:
    dur = _media_duration(path)
    if dur <= 0:
        raise RuntimeError("the stitched file can't be read back")
    if abs(dur - expect_dur) > 2.0:
        raise RuntimeError(
            f"the stitch came out wrong ({dur:.1f}s vs the "
            f"{expect_dur:.1f}s of narration)")
    probe = shutil.which("ffprobe")
    if probe:
        out = subprocess.run(
            [probe, "-v", "error", "-show_entries",
             "stream=codec_type", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30)
        kinds = (out.stdout or "")
        if "video" not in kinds or "audio" not in kinds:
            raise RuntimeError("the stitched file is missing a "
                               "video or audio track")
    return dur


def _run_job(job_id: str, uid: str) -> None:
    rec = _store_get(job_id)
    if rec is None:
        return
    api_key = _api_key()
    title = rec.get("title", "OG's Story")
    scenes = rec.get("scenes", [])
    job_dir = os.path.join(STORE_DIR, job_id)
    stall_scene = 0
    try:
        with _render_slots:
            os.makedirs(job_dir, exist_ok=True)
            durations = []
            prev_img: Optional[str] = None
            reused = 0
            for i, scene in enumerate(scenes):
                stall_scene = i
                _update(rec, state="drawing",
                        detail=f"Drawing scene {i + 1} of "
                               f"{len(scenes)}…",
                        scenes_done=i)
                img_path = os.path.join(job_dir, f"scene{i}.png")
                try:
                    # 28.3: total deadline on the picture call
                    # (see _with_deadline). A stall fails the
                    # job honestly at this scene — it does NOT
                    # fall into the reuse-previous-picture path
                    # below, which is for ordinary failures.
                    data = _with_deadline(
                        lambda: _fetch_image_bytes(
                            api_key, title, scene),
                        "picture")
                    with open(img_path, "wb") as f:
                        f.write(data)
                    prev_img = img_path
                except _SceneStall:
                    raise
                except Exception:
                    if prev_img is None:
                        raise
                    shutil.copyfile(prev_img, img_path)
                    reused += 1
                _update(rec, state="voicing",
                        detail=f"Voicing scene {i + 1} of "
                               f"{len(scenes)}…")
                mp3_path = os.path.join(job_dir, f"scene{i}.mp3")
                wav_path = os.path.join(job_dir, f"scene{i}.wav")
                _tts_scene(api_key, scene, mp3_path)
                _run_ffmpeg(["-i", mp3_path, "-ar", "24000",
                             "-ac", "1", wav_path], timeout=300)
                durations.append(_wav_duration(wav_path))
            _update(rec, state="stitching",
                    detail="Stitching the scenes together…",
                    scenes_done=len(scenes))
            segments = []
            for i, scene in enumerate(scenes):
                seg = os.path.join(job_dir, f"seg{i}.mp4")
                _render_segment(
                    os.path.join(job_dir, f"scene{i}.png"),
                    os.path.join(job_dir, f"scene{i}.wav"),
                    durations[i], seg,
                    title=title if i == 0 else "")
                segments.append(seg)
            list_path = os.path.join(job_dir, "segments.list")
            with open(list_path, "w", encoding="utf-8") as f:
                for seg in segments:
                    f.write(f"file '{os.path.abspath(seg)}'\n")
            final = os.path.join(job_dir, "final.mp4")
            _run_ffmpeg(["-f", "concat", "-safe", "0", "-i",
                         list_path, "-c", "copy",
                         "-movflags", "+faststart",
                         "-metadata", f"title={title}",
                         final], timeout=900)
            total = _verify_final(final, sum(durations))
            _charge_video(uid)
            detail = (f"\"{title}\" — {len(scenes)} scenes, "
                      f"narrated by OG.")
            if reused:
                detail += (f" ({reused} picture(s) didn't come "
                           f"out, so the last good one covers "
                           f"those scenes.)")
            _update(rec, state="done", detail=detail,
                    file=final, size=os.path.getsize(final),
                    duration=round(total, 1), charged=True,
                    scenes_done=len(scenes))
            # Notifications (optional layer): the finished
            # video also lands in the visitor's notification
            # center (+ opt-in channels). Fail-safe.
            try:
                import og_notify as _notify
                _notify.record(
                    uid, "video_done",
                    f"Your video is ready: \"{title}\"", detail)
            except Exception:
                pass
            for name in os.listdir(job_dir):
                if name != "final.mp4":
                    try:
                        os.remove(os.path.join(job_dir, name))
                    except OSError:
                        pass
    except Exception as e:
        logger.warning(f"Story video job {job_id} failed: {e}")
        try:
            if isinstance(e, _SceneStall):
                detail = (f"Scene {stall_scene + 1} stalled — "
                          f"its {e.what} call hung twice, so I "
                          f"cut the render off. Nothing was "
                          f"charged. Ask again and I'll "
                          f"recook it.")
            else:
                detail = ("That render didn't make it — nothing "
                          "was charged. Ask again and I'll "
                          "recook it.")
            _update(rec, state="failed",
                    error=str(e)[:300],
                    detail=detail)
        except Exception:
            pass


# --- Locker save (Round 21 storage, reused) -------------------------------------


class _LockerError(Exception):
    pass


def _locker_save(uid: str, tier: str, name: str, data: bytes) -> Dict:
    """Save the finished MP4 into the visitor's Round 21 locker.
    Mirrors og_storage.save_bytes' ladder + record shape, minus
    its file-kind sniff (which knows PDF/text/images only — an
    MP4 is refused there), reusing og_storage's backend, quota,
    and record store so the file lists and counts exactly like
    any other locker file."""
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
            f"That video is {_storage.fmt_size(len(data))} but "
            f"you only got {_storage.fmt_size(quota - used)} left "
            f"in your locker ({_storage.fmt_size(used)} of "
            f"{_storage.fmt_size(quota)} used). Delete something "
            f"first — say \"my storage\".")
    owner = _storage.owner_of(uid)
    fid = uuid.uuid4().hex
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_",
                  os.path.basename(str(name or "story.mp4")))[:80]
    if not safe:
        safe = "story.mp4"
    key = f"{owner[:32]}/{fid}-{safe}"
    _storage.get_backend().put(key, data, "video/mp4")
    rec = {"id": fid, "owner": owner, "name": name, "size": len(data),
           "content_type": "video/mp4", "kind": "video",
           "uploaded_at": time.time(), "key": key}
    _storage._insert_record(rec)
    return rec


# --- Routes -------------------------------------------------------------------------


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
    if state in ("queued", "drawing", "voicing", "stitching") \
            and time.time() - float(rec.get("updated", 0)) > 45 * 60:
        return {"id": rec["id"], "title": rec.get("title", ""),
                "state": "failed",
                "detail": "That render got cut off (the server "
                          "restarted mid-cook) — nothing was "
                          "charged. Ask again and I'll recook it."}
    if state == "done" and (not fresh or not rec.get("file")
                            or not os.path.exists(rec.get("file", ""))):
        return None
    out = {"id": rec["id"], "title": rec.get("title", ""),
           "state": state, "detail": rec.get("detail", ""),
           "scenes_done": int(rec.get("scenes_done", 0)),
           "scenes_total": int(rec.get("scene_count", 0)),
           "saved": bool(rec.get("saved", False))}
    if state == "done":
        out["video_url"] = f"/video/download/{rec['id']}"
        out["size"] = int(rec.get("size", 0))
        out["duration"] = float(rec.get("duration", 0))
    if state == "failed":
        out["detail"] = rec.get("detail") or \
            "That render didn't make it — nothing was charged."
    return out


def register_video_routes(app):
    @app.get("/video/status")
    async def video_status(request: Request):
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
            if pending.get("scenes"):
                pending_view = {
                    "stage": "await_approval",
                    "title": pending.get("title", ""),
                    "scenes": len(pending.get("scenes", [])),
                    "words": int(pending.get("words", 0)),
                    "est_min": _est_min(int(pending.get("words", 0))),
                    "expires_in_s": max(0, expires)}
            else:
                pending_view = {
                    "stage": pending.get("stage", "await_capture"),
                    "title": pending.get("topic", ""),
                    "scenes": 0, "words": 0, "est_min": 0,
                    "expires_in_s": max(0, expires)}
        locker_ok = False
        try:
            import og_storage as _storage
            locker_ok = bool(_storage.storage_enabled()
                             and _storage.quota_bytes(tier) > 0)
        except Exception:
            locker_ok = False
        return {"enabled": True, "tier": tier,
                "cap": int(_tiers.cap(tier, "video")),
                "used": _videos_used_today(uid),
                "left": _video_left(uid, tier),
                "locker_ok": locker_ok,
                "pending": pending_view,
                "job": _job_view(
                    _store_latest_for_owner(_key(uid)))}

    @app.post("/video/approve")
    async def video_approve(request: Request):
        if not enabled():
            return JSONResponse({"ok": False,
                                 "error": "story videos are off"},
                                status_code=404)
        uid = _route_uid(request)
        if not uid:
            return {"ok": False, "error": "no visitor identity"}
        ok, msg, rec = _approve(uid, _route_tier(uid, request))
        return {"ok": ok,
                "title": rec.get("title", "") if rec else "",
                "error": "" if ok else msg}

    @app.post("/video/decline")
    async def video_decline(request: Request):
        if not enabled():
            return JSONResponse({"ok": False,
                                 "error": "story videos are off"},
                                status_code=404)
        uid = _route_uid(request)
        if not uid:
            return {"ok": False, "error": "no visitor identity"}
        ok, msg = _decline(uid)
        return {"ok": ok, "error": "" if ok else msg}

    @app.get("/video/download/{job_id}")
    async def video_download(job_id: str, request: Request):
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
            rec["file"], media_type="video/mp4",
            filename=_slug(rec.get("title", "story")) + ".mp4")

    @app.post("/video/save/{job_id}")
    async def video_save(job_id: str, request: Request):
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
                _slug(rec.get("title", "story")) + ".mp4", data)
        except _LockerError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(f"Video locker save failed: {e}")
            return {"ok": False,
                    "error": "The locker wouldn't take it just "
                             "now — the download still works."}
        rec["saved"] = True
        _store_put(rec)
        return {"ok": True, "name": saved.get("name", "")}


# --- The seam (app.py installs this after og_browser) ------------------------------

_slot = {"job": None, "message": ""}


def install_video_tools(agent_instance, get_uid):
    """Wrap the agent's detect_intent + web_search hooks so
    story-video asks, the story-first step, and the approval
    flow ride the established seam. Persona files never
    touched."""
    if getattr(agent_instance, "_og_video_installed", False):
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
            job = _claim_job(str(message), uid) if uid else None
            if job:
                _slot["job"] = job
                _slot["message"] = str(message)
                if isinstance(intent, dict):
                    intent["needs_code_generation"] = False
                    if not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
        except Exception as e:
            logger.warning(f"Video trigger check failed: {e}")
            _slot["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _slot.get("job")
        message = _slot.get("message", "")
        _slot["job"] = None
        _slot["message"] = ""
        if job:
            try:
                results = video_results(job, message, get_uid())
            except Exception as e:
                logger.warning(f"Video job failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_video_installed = True
