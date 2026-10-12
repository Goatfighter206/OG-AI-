"""
OG motion video (Generator 7d) — TRUE text-to-video: the
visitor describes real motion, approves the plan and its
estimated cost, and a text-to-video provider renders an
actual moving MP4: in-chat player, owner-only download,
locker save, Library kind "motion".

PROVIDER (researched 2026-10-11 from official docs only;
full comparison in round-gen-motion/NOTES.md): **Runway
Dev API** — POST https://api.dev.runwayml.com/v1/text_to_video
{model, promptText, ratio, duration} returns a task id;
GET /v1/tasks/{id} polls PENDING/RUNNING to SUCCEEDED
(output[0] is the MP4 URL, ephemeral) or FAILED. Auth is a
plain Bearer secret + X-Runway-Version header. Public
pricing: credits cost $0.01 each; gen4_turbo is 5
credits/sec ($0.25 for the default 5 s clip) and gen4.5 is
12 credits/sec ($0.60 for 5 s) — the estimate shown in the
approval ask comes straight from that table. Fallback
(named, not built): Google Veo 3.1 Lite via the Gemini API.
Kling was rejected for package-priced expiring units and
signed auth; Luma for its mid-migration Agents API surface.

The flow (Brent's prepare -> approve -> execute rule):
1. ASK. Explicit motion language only ("make a motion
   video of X", "animate X", "text to video: X", "a video
   of X running/flying/...") parks a pending with the
   prompt, duration (5 s default; "10 seconds" wins, 2-10)
   and ratio (portrait when asked). The reply shows the
   plan + the estimated provider cost. ZERO provider calls
   happen before approval. "Make a video about X" is a
   STORY video (og_video) and is never claimed here.
2. APPROVE. Chat YES / NO or the card (GET /motion/status,
   POST /motion/approve|decline). First resolution wins.
3. GENERATE. One async job: submit -> poll with a deadline
   -> download -> verify (MP4 header + a real duration via
   og_video's ffmpeg reader, falling back to the requested
   duration when no probe exists) -> charge the weekly
   `video` kind ONCE (the og_video pool — motion and story
   videos share it; token packs fund past the wall at the
   og_tokenpacks video rate) -> motion_done notification.
   Any provider failure or timeout fails the job honestly
   and charges nothing.
4. DELIVER. Owner-only GET /motion/download/<id> (24 h),
   POST /motion/save/<id> locker save (kind "video" record,
   the og_video hand-rolled path), Library kind "motion".

DARK GATE: live only when OG_MOTION_ENABLED is truthy AND
OG_MOTION_API_KEY is set. Dark = GET /motion/status answers
{"enabled": false, "provider": "runway"}, the card doors
404, and no claim fires EXCEPT an explicit motion ask,
which gets the honest line that motion video isn't
switched on yet (the og_songs _down_body pattern). The key
lives in Render env only. Provider base URL + model also
come from env (OG_MOTION_API_URL / OG_MOTION_MODEL), so
activation is pure configuration.

All provider interaction sits behind ONE seam,
_run_provider(), so tests mock exactly that. HTTP is
stdlib urllib (the og_browser pattern) — no new deps.

Persona files are NEVER touched by this module.
"""

import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

import og_tiers as _tiers

logger = logging.getLogger(__name__)

STORE_DIR = os.getenv("OG_MOTION_STORE_DIR", "motion_builds")
_PENDING_TTL = 30 * 60
_JOB_TTL = 24 * 60 * 60
_STALE_S = 45 * 60
_DEFAULT_DURATION = 5
_CREDIT_USD = 0.01
# Public Runway per-second credit rates (official pricing
# page, 2026-10-11). Unknown models estimate at gen4.5.
_MODEL_CREDITS_PER_S = {"gen4_turbo": 5, "gen4.5": 12,
                        "gen4": 12}
_FALLBACK_CREDITS_PER_S = 12

_deps: Dict = {}


def bind_app(deps):
    _deps.update(deps)


def _pro_url() -> str:
    base = os.getenv("OG_PUBLIC_URL", "https://og-ai-service.onrender.com")
    return base.rstrip("/") + "/pro"


# --- Provider gate / config ------------------------------------

PROVIDER_NAME = "runway"


def _flag_on() -> bool:
    return os.getenv("OG_MOTION_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on")


def _provider_key() -> str:
    return os.getenv("OG_MOTION_API_KEY", "").strip()


def _provider_base() -> str:
    return os.getenv(
        "OG_MOTION_API_URL",
        "https://api.dev.runwayml.com/v1").strip().rstrip("/")


def _provider_model() -> str:
    return os.getenv("OG_MOTION_MODEL", "gen4_turbo").strip() \
        or "gen4_turbo"


def _api_version() -> str:
    return os.getenv("OG_MOTION_API_VERSION", "2024-11-06").strip() \
        or "2024-11-06"


def enabled() -> bool:
    return _flag_on() and bool(_provider_key())


def provider_name() -> str:
    return PROVIDER_NAME


def est_cost_usd(duration: int, model: str = "") -> float:
    model = model or _provider_model()
    rate = _MODEL_CREDITS_PER_S.get(model, _FALLBACK_CREDITS_PER_S)
    return round(int(duration) * rate * _CREDIT_USD, 2)


def _poll_interval() -> float:
    try:
        return max(0.0, float(os.getenv("OG_MOTION_POLL_S", "5")))
    except ValueError:
        return 5.0


def _poll_deadline() -> float:
    try:
        return max(1.0, float(
            os.getenv("OG_MOTION_DEADLINE_S", "600")))
    except ValueError:
        return 600.0


# --- Usage / caps (the og_video `video` pool, shared) ----------


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _usage_get(key: str):
    with _deps["usage_lock"]:
        store = _deps["load_usage"]()
    entry = store.get(key)
    return dict(entry) if isinstance(entry, dict) else None


def _videos_used_week(uid: str) -> int:
    return _tiers.week_used(_usage_get(f"video:{uid}"))


def _video_week_left(uid: str, tier: str) -> int:
    cap_w = _tiers.weekly_cap(tier, "video")
    if cap_w is None:
        return 1
    return max(0, int(cap_w) - _videos_used_week(uid))


def _charge_video(uid: str) -> None:
    key = f"video:{uid}"
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


def _token_balance(uid: str) -> int:
    try:
        import og_tokenpacks as _tp
        return _tp.balance(uid)
    except Exception:
        return 0


def _token_rate() -> int:
    try:
        import og_tokenpacks as _tp
        return int(_tp.RATES["video"])
    except Exception:
        return 0


def _motion_blocked(uid: str, tier: str) -> bool:
    if _video_week_left(uid, tier) > 0:
        return False
    rate = _token_rate()
    return rate <= 0 or _token_balance(uid) < rate


def _settle_motion(uid: str, rec: Dict) -> None:
    """Charge a completed, verified motion clip exactly once:
    weekly `video` pool, or tokens for a token-funded job."""
    if rec.get("fund") == "tokens":
        try:
            import og_tokenpacks as _tp
            _tp.debit(uid, int(_tp.RATES["video"]), "video")
        except Exception:
            pass
        return
    _charge_video(uid)


def _tier_now(uid: str) -> str:
    fn = _deps.get("get_tier")
    try:
        return str(fn() or "free") if fn else "free"
    except Exception:
        return "free"


# --- Pending approvals ------------------------------------------

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


# --- Job records (Postgres when OG_MEMORY_DB_URL, else JSON) ---

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, DB_OPTIONS as _DB_OPTS
try:
    import psycopg
except Exception:
    psycopg = None

_jobs_lock = threading.Lock()
_jobs_mem: Dict[str, Dict] = {}
_gen_slots = threading.Semaphore(2)


def _key(uid: str) -> str:
    return hashlib.sha256(str(uid).encode()).hexdigest()[:32]


def _json_store_path() -> str:
    os.makedirs(STORE_DIR, exist_ok=True)
    return os.path.join(STORE_DIR, "motion_jobs.json")


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=_DB_CT, options=_DB_OPTS)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_motion_jobs ("
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
                        "INSERT INTO og_motion_jobs (id, owner, updated,"
                        " data) VALUES (%s, %s, %s, %s) ON CONFLICT"
                        " (id) DO UPDATE SET owner = EXCLUDED.owner,"
                        " updated = EXCLUDED.updated,"
                        " data = EXCLUDED.data",
                        (rec["id"], rec.get("owner", ""), rec["updated"],
                         json.dumps(rec)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Motion job DB write failed: {e}")
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
        logger.warning(f"Motion job JSON write failed: {e}")


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
                        "SELECT data FROM og_motion_jobs WHERE id = %s",
                        (job_id,))
                    row = cur.fetchone()
            if row:
                rec = json.loads(row[0])
                with _jobs_lock:
                    _jobs_mem[job_id] = rec
                return dict(rec)
        except Exception as e:
            logger.warning(f"Motion job DB read failed: {e}")
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
        logger.warning(f"Motion job JSON read failed: {e}")
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
                        "SELECT data FROM og_motion_jobs WHERE owner"
                        " = %s ORDER BY updated DESC LIMIT 1",
                        (owner,))
                    row = cur.fetchone()
            if row:
                cands.append(json.loads(row[0]))
        except Exception as e:
            logger.warning(f"Motion job DB scan failed: {e}")
    try:
        path = _json_store_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            cands.extend(v for v in data.values()
                         if v.get("owner") == owner)
    except Exception as e:
        logger.warning(f"Motion job JSON scan failed: {e}")
    for rec in cands:
        if best is None or float(rec.get("updated", 0)) \
                > float(best.get("updated", 0)):
            best = rec
    return best


def list_jobs_for_owner(uid: str) -> List[Dict]:
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
                        "SELECT data FROM og_motion_jobs WHERE owner"
                        " = %s", (owner,))
                    rows = cur.fetchall()
            for row in rows:
                rec = json.loads(row[0])
                found[rec["id"]] = rec
        except Exception as e:
            logger.warning(f"Motion job DB list failed: {e}")
    try:
        path = _json_store_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            for v in data.values():
                if v.get("owner") == owner:
                    found[v["id"]] = v
    except Exception as e:
        logger.warning(f"Motion job JSON list failed: {e}")
    return sorted(found.values(),
                  key=lambda r: float(r.get("created", 0)),
                  reverse=True)


def purge_owner(uid: str):
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
                        "DELETE FROM og_motion_jobs WHERE owner = %s",
                        (owner,))
                conn.commit()
        except Exception as e:
            logger.warning(f"Motion job DB purge failed: {e}")
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
        logger.warning(f"Motion job JSON purge failed: {e}")
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
            return None
        return rec
    return None


# --- Provider client (THE spend seam — mocked in tests) ---------


class _ProviderError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _http_json(method: str, url: str, payload=None, timeout=60):
    data = None
    headers = {"Authorization": f"Bearer {_provider_key()}",
               "X-Runway-Version": _api_version()}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(
                resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        body = {}
        try:
            body = json.loads(e.read().decode("utf-8", "replace") or "{}")
        except Exception:
            body = {}
        return e.code, body
    except Exception as e:
        raise _ProviderError("network", f"provider unreachable: {e}")


def _http_bytes(url: str, timeout=300) -> bytes:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        raise _ProviderError("network", f"download failed: {e}")


class RunwayClient:
    """Small submit -> poll -> download client for the Runway
    text-to-video API. All HTTP goes through the two helpers
    above; _run_provider is the single seam tests replace."""

    def submit(self, prompt: str, duration: int, ratio: str) -> str:
        status, body = _http_json(
            "POST", _provider_base() + "/text_to_video",
            {"model": _provider_model(), "promptText": prompt[:1000],
             "ratio": ratio, "duration": int(duration)})
        if status not in (200, 201) or not body.get("id"):
            kind = "auth" if status in (401, 403) else \
                "rate" if status == 429 else "http"
            raise _ProviderError(
                kind, f"provider answered {status}: "
                      f"{str(body)[:200]}")
        return str(body["id"])

    def poll_once(self, task_id: str) -> Dict:
        status, body = _http_json(
            "GET", f"{_provider_base()}/tasks/{task_id}")
        if status != 200:
            kind = "auth" if status in (401, 403) else "http"
            raise _ProviderError(
                kind, f"provider answered {status} on poll")
        return body

    def download(self, url: str) -> bytes:
        return _http_bytes(url)


_client = RunwayClient()


def _run_provider(prompt: str, duration: int, ratio: str) -> bytes:
    """Submit, poll until SUCCEEDED (deadline-bounded), and
    download the MP4 bytes. Raises _ProviderError on provider
    failure, timeout, or a bad download. ONE call site in the
    job runner; tests replace this function wholesale."""
    task_id = _client.submit(prompt, duration, ratio)
    deadline = time.time() + _poll_deadline()
    while True:
        body = _client.poll_once(task_id)
        state = str(body.get("status") or "").upper()
        if state == "SUCCEEDED":
            out = body.get("output") or []
            if not out:
                raise _ProviderError(
                    "bad_video", "provider finished with no output")
            data = _client.download(str(out[0]))
            if not data:
                raise _ProviderError(
                    "bad_video", "provider sent an empty file")
            return data
        if state in ("FAILED", "CANCELLED", "CANCELED"):
            raise _ProviderError(
                "provider_failed",
                f"provider task {state.lower()}: "
                f"{str(body.get('failure') or body)[:200]}")
        if time.time() >= deadline:
            raise _ProviderError(
                "timeout",
                f"provider did not finish within "
                f"{int(_poll_deadline())}s")
        time.sleep(_poll_interval())


def _looks_like_mp4(data: bytes) -> bool:
    return bool(data) and len(data) > 64 and data[4:8] == b"ftyp"


def _verify_duration(path: str, fallback_s: float) -> float:
    try:
        import og_video as _video
        dur = _video._media_duration(path)
        if dur and dur > 0:
            return round(float(dur), 1)
    except Exception:
        pass
    return round(float(fallback_s), 1)


_FAIL_DETAILS = {
    "auth": "The motion studio key got rejected — motion "
            "needs a fix on OG's side. Nothing was charged.",
    "rate": "The motion studio is slammed right now — nothing "
            "was charged. Give it a minute and ask again.",
    "network": "The motion studio didn't answer — nothing was "
               "charged. Ask again and I'll re-render it.",
    "timeout": "The motion studio took too long and the render "
               "timed out — nothing was charged. Ask again "
               "and I'll re-render it.",
    "provider_failed": "The motion studio failed that render "
                       "— nothing was charged. A different "
                       "prompt angle usually clears it.",
    "bad_video": "The motion studio sent back something that "
                 "isn't a playable MP4 — nothing was charged.",
    "http": "That render didn't make it — nothing was charged. "
            "Ask again and I'll re-render it.",
}


# --- Narration ---------------------------------------------------


def _result(tag: str, title: str, body: str, href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body, "href": href}]


def _down_body() -> List[Dict]:
    return _result(
        "MOTION: DOWN", "🎞️ Motion video — not switched on",
        "Motion video (real text-to-video) isn't switched on "
        "yet. Tell the visitor plainly, in persona — no fake "
        "promises: OG can still write them a story video or a "
        "picture right here; the true motion clips wait until "
        "the motion studio is switched on.")


def _cap_body(tier: str, uid: str) -> List[Dict]:
    cap_w = int(_tiers.weekly_cap(tier, "video") or 0)
    refill = _tiers.week_refill_text(_usage_get(f"video:{uid}"))
    body = (f"The visitor is at THIS WEEK's video cap for their "
            f"plan ({cap_w} per week, shared with story videos) "
            f"— there is no daily reset. {refill}. Their token "
            f"balance ({_token_balance(uid):,} tokens) can't "
            f"cover a clip either — one video burns "
            f"{_token_rate():,} tokens past the weekly wall. "
            f"Tell them plainly, in persona. Higher plans make "
            f"more videos a week: {_pro_url()}")
    return _result("MOTION: AT-CAP", "🎞️ Motion — capped", body)


# --- Claim table ---------------------------------------------------

_YES_WORDS = {
    "yes", "yeah", "yep", "yup", "ya", "ok", "okay", "sure",
    "approve", "approved", "go", "do it", "make it", "lets go",
    "let's go", "yes please", "yes make it", "yes do it",
    "render it", "make the clip", "do the clip", "👍",
}
_NO_WORDS = {
    "no", "nope", "nah", "decline", "declined", "scrap it",
    "never mind", "nevermind", "forget it", "cancel", "stop",
    "don't", "dont", "no thanks",
}

_MOTION_VERBS_ING = (
    "running", "moving", "flying", "walking", "swimming",
    "dancing", "driving", "racing", "jumping", "falling",
    "flowing", "spinning", "rolling", "galloping", "surfing",
    "skiing", "riding", "chasing", "exploding", "burning",
    "raining", "snowing", "sailing", "climbing", "diving",
    "sliding", "bouncing", "floating", "crashing", "waving",
)
_EXPLICIT = re.compile(
    r"\bmotion\s+video\b|\btext[-\s]to[-\s]video\b|"
    r"\banimate\b|\banimated\s+(video|clip|short|movie)\b|"
    r"\bmoving\s+(video|clip|picture|photo|image)\b", re.I)
_VIDEO_OF = re.compile(r"\b(video|clip)\s+of\b(.+)$", re.I | re.S)
_MOTION_ING = re.compile(
    r"\b(" + "|".join(_MOTION_VERBS_ING) + r")\b", re.I)
_DURATION = re.compile(
    r"\b(\d{1,2})\s*(?:s|sec|secs|second|seconds)\b", re.I)


def _norm(message: str) -> str:
    return " ".join(str(message or "").lower().split()).strip(" .!")


def _duration_hint(raw: str) -> int:
    m = _DURATION.search(raw or "")
    if m:
        try:
            return max(2, min(10, int(m.group(1))))
        except ValueError:
            pass
    return _DEFAULT_DURATION


def _ratio_hint(raw: str) -> str:
    low = str(raw or "").lower()
    if any(w in low for w in ("portrait", "vertical", "9:16",
                              "phone", "reel", "shorts")):
        return "720:1280"
    return "1280:720"


def parse_motion(message: str) -> Optional[Dict]:
    """Explicit-motion parse. Returns {prompt, duration,
    ratio} or None. Pure — no gate, no pending — so the
    routing boundary is testable in both states."""
    raw = str(message or "").strip()
    low = raw.lower()
    if not raw:
        return None
    # Guards: story videos and other generators keep theirs.
    if "story video" in low:
        return None
    if re.search(r"\b(song|podcast|book|poster|thumbnail|meme)\b",
                 low) and not _EXPLICIT.search(low):
        return None
    explicit = bool(_EXPLICIT.search(low))
    video_of = _VIDEO_OF.search(raw)
    motion_ing = bool(_MOTION_ING.search(low))
    if not explicit:
        # The only implicit door: "video/clip of X <moving>".
        if not (video_of and motion_ing):
            return None
        if re.search(r"\bvideo\s+about\b", low):
            return None
    prompt = raw
    # Strip the trigger scaffolding; what remains is the scene.
    prompt = re.sub(
        r"^(please\s+)?(make|create|generate|render|produce|"
        r"animate|give me|i want|i need)\s+(me\s+)?"
        r"(a\s+|an\s+|the\s+)?", "", prompt, flags=re.I).strip()
    prompt = re.sub(r"^motion\s+video\s+(of\s+|about\s+)?", "",
                    prompt, flags=re.I).strip()
    prompt = re.sub(r"^text[-\s]to[-\s]video\s*[:\-]?\s*", "",
                    prompt, flags=re.I).strip()
    prompt = re.sub(r"^(animated\s+)?(video|clip|short|movie)\s+"
                    r"of\s+", "", prompt, flags=re.I).strip()
    prompt = re.sub(r"^animate\s+", "", prompt, flags=re.I).strip()
    prompt = _DURATION.sub("", prompt).strip(" ,.-")
    prompt = re.sub(r"\s+", " ", prompt).strip()
    if prompt.lower().startswith("a ") or prompt.lower().startswith("an "):
        pass  # a leading article reads fine in a scene prompt
    return {"prompt": prompt[:1000],
            "duration": _duration_hint(raw),
            "ratio": _ratio_hint(raw)}


def _browser_has_pending(uid: str) -> bool:
    try:
        import og_browser as _browser
        pend = _browser._get_pending(uid)
        return bool(pend and pend.get("kind") == "action")
    except Exception:
        return False


def _other_pending_newer(uid: str, mine: Dict) -> bool:
    created = float(mine.get("created", 0))
    for mod_name in ("og_video", "og_songs", "og_podcast", "og_books"):
        try:
            import importlib
            mod = importlib.import_module(mod_name)
            pend = mod._get_pending(uid)
            if pend and float(pend.get("created", 0)) > created:
                return True
        except Exception:
            continue
    return False


def _claim_motion(message: str, uid: str) -> Optional[Dict]:
    if not uid:
        return None
    raw = str(message or "")
    norm = _norm(raw)
    pending = _get_pending(uid) if enabled() else None
    if pending is not None:
        if pending.get("stage") == "await_prompt":
            if norm in _NO_WORDS:
                return {"kind": "decline"}
            if len(norm) >= 2:
                return {"kind": "new", "prompt": raw.strip()[:1000],
                        "duration": _duration_hint(raw),
                        "ratio": _ratio_hint(raw)}
            return None
        if parse_motion(raw) and _EXPLICIT.search(raw):
            parsed = parse_motion(raw)
            return {"kind": "replace", **parsed}
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
        return None
    parsed = parse_motion(raw)
    if parsed is None:
        return None
    if not enabled():
        # Dark: an explicit motion ask gets the honest line —
        # and nothing else fires. Zero provider calls.
        return {"kind": "down"}
    if not parsed["prompt"]:
        return {"kind": "need_prompt"}
    return {"kind": "new", **parsed}


# --- Flow results --------------------------------------------------


def _plan_line(p: Dict) -> str:
    return (f"a {p['duration']}-second motion clip "
            f"({p['ratio']}), prompt: \"{p['prompt']}\" — "
            f"estimated provider cost "
            f"${est_cost_usd(p['duration']):.2f}")


def _approve(uid: str, tier: str):
    pending = _get_pending(uid)
    if pending is None:
        return False, "Nothing is waiting on approval — it " \
            "expired or was already answered.", None
    if _active_job(uid):
        _clear_pending(uid)
        return False, "A motion clip is already rendering for " \
            "you — let that one finish first.", None
    if _motion_blocked(uid, tier):
        _clear_pending(uid)
        return False, "You're at this week's video cap for " \
            "your plan, and your token balance can't cover a " \
            "clip either, so this one can't render.", None
    rec = _new_job(uid, pending)
    if _video_week_left(uid, tier) <= 0:
        rec["fund"] = "tokens"
    _clear_pending(uid)
    _store_put(rec)
    thread = threading.Thread(target=_run_job,
                              args=(rec["id"], uid), daemon=True)
    thread.start()
    return True, f"Locked in — rendering \"{rec['title']}\" now.", rec


def _decline(uid: str):
    pending = _get_pending(uid)
    if pending is None:
        return False, "Nothing was waiting — no motion clip " \
            "is pending."
    _clear_pending(uid)
    return True, "Scrapped — nothing rendered, nothing charged."


def motion_results(job: Dict, message: str, uid: str) -> List[Dict]:
    kind = job.get("kind", "")
    tier = _tier_now(uid)
    if kind == "down":
        return _down_body()
    if kind == "need_prompt":
        _set_pending(uid, {"stage": "await_prompt"})
        return _result(
            "MOTION: NEED-PROMPT", "🎞️ Motion — needs a scene",
            "The visitor wants a motion video but didn't say "
            "what should move. Ask them, in persona, in one "
            "line: what should the clip show moving? Whatever "
            "they answer next becomes the prompt.")
    if kind in ("new", "replace"):
        if kind == "replace":
            _clear_pending(uid)
        if _active_job(uid):
            return _result(
                "MOTION: BUSY", "🎞️ Motion — one rendering",
                "A motion clip is ALREADY rendering for this "
                "visitor. Tell them, in persona: one at a time "
                "— it lands right here when it's done.")
        if _motion_blocked(uid, tier):
            return _cap_body(tier, uid)
        pending = {"stage": "await_approval",
                   "prompt": job.get("prompt", ""),
                   "duration": int(job.get("duration", _DEFAULT_DURATION)),
                   "ratio": job.get("ratio", "1280:720")}
        _set_pending(uid, pending)
        fund = ""
        if _video_week_left(uid, tier) <= 0:
            fund = (f" Their weekly videos are used up, so this "
                    f"one burns {_token_rate():,} tokens from "
                    f"their balance ({_token_balance(uid):,} on "
                    f"hand) when it finishes instead.")
        body = (f"The visitor wants a MOTION VIDEO. Present "
                f"this plan + approval ask, in persona, with "
                f"these REAL numbers (do not invent others): "
                f"{_plan_line(pending)} on Runway "
                f"({_provider_model()}). Replying YES renders "
                f"it for real — a moving MP4 with a player and "
                f"a download right here — and it counts as one "
                f"of their {int(_tiers.weekly_cap(tier, 'video') or 0)} "
                f"weekly videos (shared with story videos) ONLY "
                f"when it finishes and verifies; a failed render "
                f"charges nothing. NO scraps it. Nothing is "
                f"rendered before their YES.{fund}")
        return _result("MOTION: APPROVE?", "🎞️ Motion — approval", body)
    if kind == "approve":
        ok, msg, rec = _approve(uid, tier)
        if not ok:
            return _result("MOTION: APPROVE-FAILED",
                           "🎞️ Motion — not started",
                           msg + " Tell the visitor exactly that, "
                           "in persona — plainly.")
        return _result(
            "MOTION: RENDERING", "🎞️ Motion — rendering",
            f"APPROVED — the motion studio is RENDERING "
            f"\"{rec['title']}\" now. Tell the visitor, in "
            f"persona: locked in, it's rendering for real — it "
            f"lands RIGHT HERE with a player and a download "
            f"when it's done (usually a minute or a few). It "
            f"only counts against their weekly videos when it "
            f"FINISHES and verifies.")
    if kind == "decline":
        ok, msg = _decline(uid)
        return _result("MOTION: DECLINED", "🎞️ Motion — scrapped",
                       msg + " Tell the visitor that, in persona, "
                       "briefly.")
    return _down_body()


# --- Production ------------------------------------------------------


def _new_job(uid: str, pending: Dict) -> Dict:
    job_id = uuid.uuid4().hex[:16]
    prompt = pending.get("prompt", "")
    title = (prompt[:60].strip() or "OG's Motion Clip")
    return {
        "id": job_id, "owner": _key(uid),
        "title": title, "prompt": prompt,
        "duration": int(pending.get("duration", _DEFAULT_DURATION)),
        "ratio": pending.get("ratio", "1280:720"),
        "model": _provider_model(),
        "est_cost": est_cost_usd(int(pending.get(
            "duration", _DEFAULT_DURATION))),
        "provider": PROVIDER_NAME,
        "state": "queued", "detail": "In line — studio starting.",
        "created": time.time(), "updated": time.time(),
        "file": "", "size": 0, "duration_actual": 0.0,
        "saved": False, "charged": False, "error": "",
    }


def _update(rec: Dict, **kw) -> Dict:
    rec.update(kw)
    rec["updated"] = time.time()
    _store_put(rec)
    return rec


def _slug(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(title or "motion")).strip("-")
    return (slug[:60] or "motion").lower()


def _run_job(job_id: str, uid: str) -> None:
    rec = _store_get(job_id)
    if rec is None:
        return
    job_dir = os.path.join(STORE_DIR, job_id)
    try:
        with _gen_slots:
            os.makedirs(job_dir, exist_ok=True)
            _update(rec, state="generating",
                    detail="In the studio — rendering real motion…")
            data = _run_provider(rec.get("prompt", ""),
                                 int(rec.get("duration", _DEFAULT_DURATION)),
                                 rec.get("ratio", "1280:720"))
            if not _looks_like_mp4(data):
                raise _ProviderError(
                    "bad_video", "downloaded file failed the MP4 check")
            final = os.path.join(job_dir, "final.mp4")
            with open(final, "wb") as f:
                f.write(data)
            dur = _verify_duration(final, float(rec.get("duration", 5)))
            _settle_motion(uid, rec)
            _update(rec, state="done",
                    detail=f"\"{rec.get('title', '')}\" — a real "
                           f"motion clip, ready to play.",
                    file=final, size=len(data),
                    duration_actual=dur, charged=True)
            try:
                import og_notify as _notify
                _notify.record(
                    uid, "motion_done",
                    f"Your motion clip is ready: "
                    f"\"{rec.get('title', '')}\"",
                    f"\"{rec.get('title', '')}\" — a real "
                    f"text-to-video clip, ready to play.",
                    target={"view": "library", "kind": "motion",
                            "id": job_id})
            except Exception:
                pass
    except _ProviderError as e:
        logger.warning(f"Motion job {job_id} failed ({e.kind}): {e}")
        _fail_job(rec, uid, e.kind, str(e))
    except Exception as e:
        logger.warning(f"Motion job {job_id} failed: {e}")
        _fail_job(rec, uid, "http", f"{type(e).__name__}: {e}")


def _fail_job(rec: Dict, uid: str, kind: str, msg: str) -> None:
    try:
        import og_fixqueue as _fq
        _fq.record_problem(
            uid, "motion", f"motion:{rec.get('id', '')}",
            "motion-render", "A motion render failed",
            f"The render of \"{rec.get('title', '')}\" failed "
            f"({kind}): {msg[:200]}",
            fix={"kind": "owner_steps",
                 "summary": "The motion render didn't finish. "
                            "Nothing was charged.",
                 "steps": [
                     "Nothing was charged — the render died "
                     "before it finished.",
                     "Ask OG for the motion clip again; a "
                     "one-off studio failure usually clears on "
                     "a fresh try."]})
    except Exception:
        pass
    try:
        _update(rec, state="failed", error=msg[:300],
                detail=_FAIL_DETAILS.get(kind, _FAIL_DETAILS["http"]))
    except Exception:
        pass


# --- Locker save -------------------------------------------------------


class _LockerError(Exception):
    pass


def _locker_save(uid: str, tier: str, name: str, data: bytes) -> Dict:
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
            f"That clip is {_storage.fmt_size(len(data))} but "
            f"you only got {_storage.fmt_size(quota - used)} "
            f"left in your locker ({_storage.fmt_size(used)} "
            f"of {_storage.fmt_size(quota)} used). Delete "
            f"something first — say \"my storage\".")
    owner = _storage.owner_of(uid)
    fid = uuid.uuid4().hex
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_",
                  os.path.basename(str(name or "motion.mp4")))[:80]
    if not safe:
        safe = "motion.mp4"
    key = f"{owner[:32]}/{fid}-{safe}"
    _storage.get_backend().put(key, data, "video/mp4")
    rec = {"id": fid, "owner": owner, "name": name,
           "size": len(data), "content_type": "video/mp4",
           "kind": "video", "uploaded_at": time.time(),
           "key": key}
    _storage._insert_record(rec)
    return rec


# --- Routes ------------------------------------------------------------


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
                "detail": "That render got cut off (the server "
                          "restarted mid-render) — nothing was "
                          "charged. Ask again and I'll re-render it."}
    if state == "done" and (not fresh or not rec.get("file")
                            or not os.path.exists(rec.get("file", ""))):
        return None
    out = {"id": rec["id"], "title": rec.get("title", ""),
           "prompt": rec.get("prompt", ""),
           "state": state, "detail": rec.get("detail", ""),
           "saved": bool(rec.get("saved", False))}
    if state == "done":
        out["motion_url"] = f"/motion/download/{rec['id']}"
        out["video_url"] = out["motion_url"]
        out["size"] = int(rec.get("size", 0))
        out["duration"] = float(rec.get("duration_actual", 0))
    if state == "failed":
        out["detail"] = rec.get("detail") or \
            "That render didn't make it — nothing was charged."
    return out


def register_motion_routes(app):
    @app.get("/motion/status")
    async def motion_status(request: Request):
        if not enabled():
            return {"enabled": False, "provider": PROVIDER_NAME}
        uid = _route_uid(request)
        if not uid:
            return {"enabled": True, "provider": PROVIDER_NAME,
                    "pending": None, "job": None}
        tier = _route_tier(uid, request)
        pending_view = None
        pending = _get_pending(uid)
        if pending is not None:
            expires = int(float(pending.get("created", time.time()))
                          + _PENDING_TTL - time.time())
            pending_view = {
                "stage": pending.get("stage", "await_approval"),
                "prompt": pending.get("prompt", ""),
                "duration": int(pending.get("duration", _DEFAULT_DURATION)),
                "ratio": pending.get("ratio", "1280:720"),
                "est_cost": est_cost_usd(int(pending.get(
                    "duration", _DEFAULT_DURATION))),
                "expires_in_s": max(0, expires)}
        locker_ok = False
        try:
            import og_storage as _storage
            locker_ok = bool(_storage.storage_enabled()
                             and _storage.quota_bytes(tier) > 0)
        except Exception:
            locker_ok = False
        return {"enabled": True, "provider": PROVIDER_NAME,
                "model": _provider_model(), "tier": tier,
                "cap": int(_tiers.weekly_cap(tier, "video") or 0),
                "used": _videos_used_week(uid),
                "left": _video_week_left(uid, tier),
                "token_balance": _token_balance(uid),
                "locker_ok": locker_ok,
                "pending": pending_view,
                "job": _job_view(_store_latest_for_owner(_key(uid)))}

    @app.post("/motion/approve")
    async def motion_approve(request: Request):
        if not enabled():
            return JSONResponse({"ok": False,
                                 "error": "motion is off"},
                                status_code=404)
        uid = _route_uid(request)
        if not uid:
            return {"ok": False, "error": "no visitor identity"}
        ok, msg, rec = _approve(uid, _route_tier(uid, request))
        return {"ok": ok,
                "title": rec.get("title", "") if rec else "",
                "error": "" if ok else msg}

    @app.post("/motion/decline")
    async def motion_decline(request: Request):
        if not enabled():
            return JSONResponse({"ok": False,
                                 "error": "motion is off"},
                                status_code=404)
        uid = _route_uid(request)
        if not uid:
            return {"ok": False, "error": "no visitor identity"}
        ok, msg = _decline(uid)
        return {"ok": ok, "error": "" if ok else msg}

    @app.get("/motion/download/{job_id}")
    async def motion_download(job_id: str, request: Request):
        uid = _route_uid(request)
        rec = _store_get(job_id) if uid else None
        if not rec or rec.get("owner") != _key(uid) \
                or rec.get("state") != "done" \
                or not rec.get("file") \
                or not os.path.exists(rec.get("file", "")) \
                or time.time() - float(rec.get("updated", 0)) \
                > _JOB_TTL:
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(
            rec["file"], media_type="video/mp4",
            filename=_slug(rec.get("title", "motion")) + ".mp4")

    @app.post("/motion/save/{job_id}")
    async def motion_save(job_id: str, request: Request):
        uid = _route_uid(request)
        rec = _store_get(job_id) if uid else None
        if not rec or rec.get("owner") != _key(uid) \
                or rec.get("state") != "done" \
                or not rec.get("file") \
                or not os.path.exists(rec.get("file", "")):
            raise HTTPException(status_code=404, detail="Not found")
        if rec.get("saved"):
            return {"ok": True, "already": True}
        tier = _route_tier(uid, request)
        try:
            with open(rec["file"], "rb") as f:
                data = f.read()
            saved = _locker_save(
                uid, tier,
                _slug(rec.get("title", "motion")) + ".mp4", data)
        except _LockerError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(f"Motion locker save failed: {e}")
            return {"ok": False,
                    "error": "The locker wouldn't take it just "
                             "now — the download still works."}
        rec["saved"] = True
        _store_put(rec)
        return {"ok": True, "name": saved.get("name", "")}


# --- The seam ------------------------------------------------------------

_slot = {"job": None, "message": ""}


def install_motion_tools(agent_instance, get_uid):
    if getattr(agent_instance, "_og_motion_installed", False):
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
            job = _claim_motion(str(message), uid) if uid else None
            if job:
                _slot["job"] = job
                _slot["message"] = str(message)
                if isinstance(intent, dict):
                    intent["needs_code_generation"] = False
                    if not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
        except Exception as e:
            logger.warning(f"Motion trigger check failed: {e}")
            _slot["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _slot.get("job")
        message = _slot.get("message", "")
        _slot["job"] = None
        _slot["message"] = ""
        if job:
            try:
                results = motion_results(job, message, get_uid())
            except Exception as e:
                logger.warning(f"Motion job failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_motion_installed = True
