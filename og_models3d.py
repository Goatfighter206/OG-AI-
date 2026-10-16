"""
OG 3D models (Generator 7e) — text-to-3D: the visitor
describes an object, approves the plan and its estimated
credit cost, and a text-to-3D provider builds an actual
GLB: owner-only download, locker save, Library kind
"model3d".

PROVIDER (researched 2026-10-11 from official docs only;
full comparison in round-gen-models/NOTES.md): **Tripo**
— POST {base}/task {type: "text_to_model", prompt,
texture, pbr} returns a task id; GET {base}/task/{id}
polls queued/running to success (the output carries the
model URL) or a failed/banned/expired terminal state.
Auth is a plain Bearer key. Public pricing:
pay-as-you-go, 1 credit = $0.01; text-to-3D is 20 credits
with standard texture — $0.20 per model, the estimate
shown in the approval ask. Fallback (named, not built):
Meshy (explicit target_formats GLB guarantee, but a
two-task preview->refine chain and a paid plan in front
of API access).

The flow (Brent's prepare -> approve -> execute rule):
1. ASK. Explicit 3D-model language only ("make a 3D model
   of X", "generate a 3d model of X", "text to 3d: X")
   parks a pending with the prompt. The reply shows the
   plan + the estimated provider cost (20 credits /
   $0.20). ZERO provider calls happen before approval.
   Unity asks, image/designer asks, OG-avatar and
   war-game asks are NEVER claimed here.
2. APPROVE. Chat YES / NO or the card (GET
   /models3d/status, POST /models3d/approve|decline).
   First resolution wins.
3. GENERATE. One async job: submit -> poll with a
   deadline -> download the GLB -> verify (file exists,
   non-trivial size, GLB magic bytes "glTF") -> charge
   the weekly `images` kind ONCE (a finished model is a
   one-shot static asset, the closest existing fence —
   the og_designer mapping: usage entry "image:<uid>",
   token packs fund past the wall at the image rate) ->
   model_done notification. Any provider failure or
   timeout fails the job honestly and charges nothing.
4. DELIVER. Owner-only GET /models3d/download/<id>
   (24 h), POST /models3d/save/<id> locker save, Library
   kind "model3d". The GLB download IS the Unity-usable
   asset — a standard glTF binary imports into Unity;
   this module writes NO Unity project (og_unity owns
   project builds).

DARK GATE: live only when OG_MODELS_ENABLED is truthy
AND OG_MODELS_API_KEY is set. Dark = GET
/models3d/status answers {"enabled": false,
"provider": "tripo"}, the card doors 404, and no claim
fires EXCEPT an explicit 3D-model ask, which gets the
honest line that 3D models aren't switched on yet (the
og_songs _down_body pattern). The key lives in Render
env only. Provider base URL + model version also come
from env (OG_MODELS_API_URL / OG_MODELS_MODEL_VERSION),
so activation is pure configuration.

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

STORE_DIR = os.getenv("OG_MODELS_STORE_DIR", "model3d_builds")
_PENDING_TTL = 30 * 60
_JOB_TTL = 24 * 60 * 60
_STALE_S = 45 * 60
_CREDIT_USD = 0.01
# Public Tripo price (official pricing page, 2026-10-11):
# text-to-3D with standard texture = 20 credits at $0.01.
_TEXT_CREDITS = 20
_MIN_GLB_BYTES = 100

_deps: Dict = {}


def bind_app(deps):
    _deps.update(deps)


def _pro_url() -> str:
    base = os.getenv("OG_PUBLIC_URL", "https://og-ai-service.onrender.com")
    return base.rstrip("/") + "/pro"


# --- Provider gate / config ------------------------------------

PROVIDER_NAME = "tripo"


def _flag_on() -> bool:
    return os.getenv("OG_MODELS_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on")


def _provider_key() -> str:
    return os.getenv("OG_MODELS_API_KEY", "").strip()


def _provider_base() -> str:
    return os.getenv(
        "OG_MODELS_API_URL",
        "https://api.tripo3d.ai/v2/openapi").strip().rstrip("/")


def _provider_model() -> str:
    return os.getenv("OG_MODELS_MODEL_VERSION", "").strip()


def enabled() -> bool:
    return _flag_on() and bool(_provider_key())


def provider_name() -> str:
    return PROVIDER_NAME


def est_credits() -> int:
    return _TEXT_CREDITS


def est_cost_usd() -> float:
    return round(_TEXT_CREDITS * _CREDIT_USD, 2)


def _poll_interval() -> float:
    try:
        return max(0.0, float(os.getenv("OG_MODELS_POLL_S", "5")))
    except ValueError:
        return 5.0


def _poll_deadline() -> float:
    try:
        return max(1.0, float(
            os.getenv("OG_MODELS_DEADLINE_S", "900")))
    except ValueError:
        return 900.0


# --- Usage / caps (the weekly `images` pool — see header) ------


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _usage_get(key: str):
    with _deps["usage_lock"]:
        store = _deps["load_usage"]()
    entry = store.get(key)
    return dict(entry) if isinstance(entry, dict) else None


def _images_used_week(uid: str) -> int:
    return _tiers.week_used(_usage_get(f"image:{uid}"))


def _image_week_left(uid: str, tier: str) -> int:
    cap_w = _tiers.weekly_cap(tier, "images")
    if cap_w is None:
        return 0
    return max(0, int(cap_w) - _images_used_week(uid))


def _charge_image(uid: str) -> None:
    key = f"image:{uid}"
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
        return int(_tp.RATES["image"])
    except Exception:
        return 0


def _model_blocked(uid: str, tier: str) -> bool:
    if _image_week_left(uid, tier) > 0:
        return False
    rate = _token_rate()
    return rate <= 0 or _token_balance(uid) < rate


def _settle_model(uid: str, rec: Dict) -> None:
    """Charge a completed, verified model exactly once:
    weekly `images` pool, or tokens for a token-funded job."""
    if rec.get("fund") == "tokens":
        try:
            import og_tokenpacks as _tp
            _tp.debit(uid, int(_tp.RATES["image"]), "image")
        except Exception:
            pass
        return
    _charge_image(uid)


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
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo
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
    return os.path.join(STORE_DIR, "model3d_jobs.json")


def _db_connect():
    conn = psycopg.connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_model3d_jobs ("
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
                        "INSERT INTO og_model3d_jobs (id, owner, updated,"
                        " data) VALUES (%s, %s, %s, %s) ON CONFLICT"
                        " (id) DO UPDATE SET owner = EXCLUDED.owner,"
                        " updated = EXCLUDED.updated,"
                        " data = EXCLUDED.data",
                        (rec["id"], rec.get("owner", ""), rec["updated"],
                         json.dumps(rec)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Model3D job DB write failed: {e}")
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
        logger.warning(f"Model3D job JSON write failed: {e}")


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
                        "SELECT data FROM og_model3d_jobs WHERE id = %s",
                        (job_id,))
                    row = cur.fetchone()
            if row:
                rec = json.loads(row[0])
                with _jobs_lock:
                    _jobs_mem[job_id] = rec
                return dict(rec)
        except Exception as e:
            logger.warning(f"Model3D job DB read failed: {e}")
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
        logger.warning(f"Model3D job JSON read failed: {e}")
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
                        "SELECT data FROM og_model3d_jobs WHERE owner"
                        " = %s ORDER BY updated DESC LIMIT 1",
                        (owner,))
                    row = cur.fetchone()
            if row:
                cands.append(json.loads(row[0]))
        except Exception as e:
            logger.warning(f"Model3D job DB scan failed: {e}")
    try:
        path = _json_store_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            cands.extend(v for v in data.values()
                         if v.get("owner") == owner)
    except Exception as e:
        logger.warning(f"Model3D job JSON scan failed: {e}")
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
                        "SELECT data FROM og_model3d_jobs WHERE owner"
                        " = %s", (owner,))
                    rows = cur.fetchall()
            for row in rows:
                rec = json.loads(row[0])
                found[rec["id"]] = rec
        except Exception as e:
            logger.warning(f"Model3D job DB list failed: {e}")
    try:
        path = _json_store_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            for v in data.values():
                if v.get("owner") == owner:
                    found[v["id"]] = v
    except Exception as e:
        logger.warning(f"Model3D job JSON list failed: {e}")
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
                        "DELETE FROM og_model3d_jobs WHERE owner = %s",
                        (owner,))
                conn.commit()
        except Exception as e:
            logger.warning(f"Model3D job DB purge failed: {e}")
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
        logger.warning(f"Model3D job JSON purge failed: {e}")
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
    headers = {"Authorization": f"Bearer {_provider_key()}"}
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


class TripoClient:
    """Small submit -> poll -> download client for the Tripo
    text-to-3D API. All HTTP goes through the two helpers
    above; _run_provider is the single seam tests replace."""

    def submit(self, prompt: str) -> str:
        payload = {"type": "text_to_model",
                   "prompt": prompt[:1000],
                   "texture": True, "pbr": True}
        model = _provider_model()
        if model:
            payload["model_version"] = model
        status, body = _http_json(
            "POST", _provider_base() + "/task", payload)
        data = body.get("data") if isinstance(body, dict) else None
        task_id = ""
        if isinstance(data, dict):
            task_id = str(data.get("task_id") or data.get("id") or "")
        if status not in (200, 201) or not task_id:
            kind = "auth" if status in (401, 403) else \
                "rate" if status == 429 else "http"
            raise _ProviderError(
                kind, f"provider answered {status}: "
                      f"{str(body)[:200]}")
        return task_id

    def poll_once(self, task_id: str) -> Dict:
        status, body = _http_json(
            "GET", f"{_provider_base()}/task/{task_id}")
        if status != 200:
            kind = "auth" if status in (401, 403) else "http"
            raise _ProviderError(
                kind, f"provider answered {status} on poll")
        data = body.get("data") if isinstance(body, dict) else None
        return data if isinstance(data, dict) else body

    def download(self, url: str) -> bytes:
        return _http_bytes(url)


_client = TripoClient()


def _run_provider(prompt: str) -> bytes:
    """Submit, poll until success (deadline-bounded), and
    download the GLB bytes. Raises _ProviderError on
    provider failure, timeout, or a bad download. ONE call
    site in the job runner; tests replace this wholesale."""
    task_id = _client.submit(prompt)
    deadline = time.time() + _poll_deadline()
    while True:
        body = _client.poll_once(task_id)
        state = str(body.get("status") or "").lower()
        if state == "success":
            out = body.get("output") or {}
            url = ""
            if isinstance(out, dict):
                url = str(out.get("pbr_model") or out.get("model")
                          or out.get("model_url")
                          or out.get("base_model") or "")
            if not url:
                raise _ProviderError(
                    "bad_model", "provider finished with no model URL")
            data = _client.download(url)
            if not data:
                raise _ProviderError(
                    "bad_model", "provider sent an empty file")
            return data
        if state in ("failed", "banned", "expired",
                     "cancelled", "canceled"):
            raise _ProviderError(
                "provider_failed",
                f"provider task {state}: "
                f"{str(body.get('message') or body)[:200]}")
        if time.time() >= deadline:
            raise _ProviderError(
                "timeout",
                f"provider did not finish within "
                f"{int(_poll_deadline())}s")
        time.sleep(_poll_interval())


def _looks_like_glb(data: bytes) -> bool:
    """GLB verification: non-trivial size + the glTF magic
    bytes every binary glTF file starts with."""
    return bool(data) and len(data) >= _MIN_GLB_BYTES \
        and data[:4] == b"glTF"


_FAIL_DETAILS = {
    "auth": "The 3D studio key got rejected — 3D models "
            "need a fix on OG's side. Nothing was charged.",
    "rate": "The 3D studio is slammed right now — nothing "
            "was charged. Give it a minute and ask again.",
    "network": "The 3D studio didn't answer — nothing was "
               "charged. Ask again and I'll build it.",
    "timeout": "The 3D studio took too long and the build "
               "timed out — nothing was charged. Ask again "
               "and I'll build it.",
    "provider_failed": "The 3D studio failed that build "
                       "— nothing was charged. A different "
                       "prompt angle usually clears it.",
    "bad_model": "The 3D studio sent back something that "
                 "isn't a real GLB model — nothing was charged.",
    "http": "That build didn't make it — nothing was charged. "
            "Ask again and I'll build it.",
}


# --- Narration ---------------------------------------------------


def _result(tag: str, title: str, body: str, href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body, "href": href}]


def _down_body() -> List[Dict]:
    return _result(
        "MODEL3D: DOWN", "🧊 3D models — not switched on",
        "3D model generation isn't switched on yet. Tell "
        "the visitor plainly, in persona — no fake promises: "
        "OG can still draw them a picture or build them a "
        "Unity project right here; real downloadable 3D "
        "models (GLB) wait until the 3D studio is switched on.")


def _cap_body(tier: str, uid: str) -> List[Dict]:
    cap_w = int(_tiers.weekly_cap(tier, "images") or 0)
    refill = _tiers.week_refill_text(_usage_get(f"image:{uid}"))
    body = (f"The visitor is at THIS WEEK's image cap for "
            f"their plan ({cap_w} per week, shared with "
            f"pictures — a finished 3D model counts as one) "
            f"— there is no daily reset. {refill}. Their "
            f"token balance ({_token_balance(uid):,} tokens) "
            f"can't cover a model either — one model burns "
            f"{_token_rate():,} tokens past the weekly wall. "
            f"Tell them plainly, in persona. Higher plans "
            f"make more a week: {_pro_url()}")
    return _result("MODEL3D: AT-CAP", "🧊 3D models — capped", body)


# --- Claim table ---------------------------------------------------

_YES_WORDS = {
    "yes", "yeah", "yep", "yup", "ya", "ok", "okay", "sure",
    "approve", "approved", "go", "do it", "make it", "lets go",
    "let's go", "yes please", "yes make it", "yes do it",
    "build it", "make the model", "build the model", "👍",
}
_NO_WORDS = {
    "no", "nope", "nah", "decline", "declined", "scrap it",
    "never mind", "nevermind", "forget it", "cancel", "stop",
    "don't", "dont", "no thanks",
}

_EXPLICIT = re.compile(
    r"\b3[-\s]?d\s+(model|models|object|mesh|asset|figure|"
    r"statue|prop|character)\b|"
    r"\btext[-\s]to[-\s]3d\b|"
    r"\bmodel\s+in\s+3[-\s]?d\b", re.I)


def _norm(message: str) -> str:
    return " ".join(str(message or "").lower().split()).strip(" .!")


def parse_model3d(message: str) -> Optional[Dict]:
    """Explicit 3D-model parse. Returns {prompt} or None.
    Pure — no gate, no pending — so the routing boundary
    is testable in both states."""
    raw = str(message or "").strip()
    low = raw.lower()
    if not raw:
        return None
    # Guards: other owners keep their asks. Unity projects,
    # the OG avatar and the war game are NOT 3D-model jobs.
    if "unity" in low:
        return None
    if "avatar" in low or "war game" in low or "wargame" in low:
        return None
    if re.search(r"\b(song|podcast|book|poster|thumbnail|meme|"
                 r"motion\s+video|story\s+video)\b", low):
        return None
    if re.match(r"(how|what|why|when|where|which|can you "
                r"explain|tell me about)\b", low):
        return None  # a question ABOUT 3D models, not an ask
    if not _EXPLICIT.search(low):
        return None
    prompt = raw
    prompt = re.sub(
        r"^(please\s+)?(make|create|generate|build|render|"
        r"produce|give me|i want|i need)\s+(me\s+)?"
        r"(a\s+|an\s+|the\s+)?", "", prompt, flags=re.I).strip()
    prompt = re.sub(r"^text[-\s]to[-\s]3d\s*[:\-]?\s*", "",
                    prompt, flags=re.I).strip()
    prompt = re.sub(
        r"^3[-\s]?d\s+(model|object|mesh|asset|figure|statue|"
        r"prop|character)\s+(of\s+|for\s+)?", "",
        prompt, flags=re.I).strip()
    prompt = re.sub(r"^model\s+in\s+3[-\s]?d\s+(of\s+)?", "",
                    prompt, flags=re.I).strip()
    prompt = re.sub(r"\s+", " ", prompt).strip(" ,.-")
    return {"prompt": prompt[:1000]}


def _browser_has_pending(uid: str) -> bool:
    try:
        import og_browser as _browser
        pend = _browser._get_pending(uid)
        return bool(pend and pend.get("kind") == "action")
    except Exception:
        return False


def _other_pending_newer(uid: str, mine: Dict) -> bool:
    created = float(mine.get("created", 0))
    for mod_name in ("og_video", "og_songs", "og_podcast",
                     "og_books", "og_motion"):
        try:
            import importlib
            mod = importlib.import_module(mod_name)
            pend = mod._get_pending(uid)
            if pend and float(pend.get("created", 0)) > created:
                return True
        except Exception:
            continue
    return False


def _claim_model(message: str, uid: str) -> Optional[Dict]:
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
                return {"kind": "new", "prompt": raw.strip()[:1000]}
            return None
        if parse_model3d(raw) and _EXPLICIT.search(raw):
            parsed = parse_model3d(raw)
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
    parsed = parse_model3d(raw)
    if parsed is None:
        return None
    if not enabled():
        # Dark: an explicit 3D-model ask gets the honest
        # line — and nothing else fires. Zero provider calls.
        return {"kind": "down"}
    if not parsed["prompt"]:
        return {"kind": "need_prompt"}
    return {"kind": "new", **parsed}


# --- Flow results --------------------------------------------------


def _plan_line(p: Dict) -> str:
    return (f"a textured 3D model (GLB download) of "
            f"\"{p['prompt']}\" — estimated provider cost "
            f"{est_credits()} credits "
            f"(~${est_cost_usd():.2f})")


def _approve(uid: str, tier: str):
    pending = _get_pending(uid)
    if pending is None:
        return False, "Nothing is waiting on approval — it " \
            "expired or was already answered.", None
    if _active_job(uid):
        _clear_pending(uid)
        return False, "A 3D model is already building for " \
            "you — let that one finish first.", None
    if _model_blocked(uid, tier):
        _clear_pending(uid)
        return False, "You're at this week's image cap for " \
            "your plan, and your token balance can't cover a " \
            "model either, so this one can't build.", None
    rec = _new_job(uid, pending)
    if _image_week_left(uid, tier) <= 0:
        rec["fund"] = "tokens"
    _clear_pending(uid)
    _store_put(rec)
    thread = threading.Thread(target=_run_job,
                              args=(rec["id"], uid), daemon=True)
    thread.start()
    return True, f"Locked in — building \"{rec['title']}\" now.", rec


def _decline(uid: str):
    pending = _get_pending(uid)
    if pending is None:
        return False, "Nothing was waiting — no 3D model " \
            "is pending."
    _clear_pending(uid)
    return True, "Scrapped — nothing built, nothing charged."


def model_results(job: Dict, message: str, uid: str) -> List[Dict]:
    kind = job.get("kind", "")
    tier = _tier_now(uid)
    if kind == "down":
        return _down_body()
    if kind == "need_prompt":
        _set_pending(uid, {"stage": "await_prompt"})
        return _result(
            "MODEL3D: NEED-PROMPT", "🧊 3D models — needs an object",
            "The visitor wants a 3D model but didn't say of "
            "what. Ask them, in persona, in one line: what "
            "object should the model be? Whatever they "
            "answer next becomes the prompt.")
    if kind in ("new", "replace"):
        if kind == "replace":
            _clear_pending(uid)
        if _active_job(uid):
            return _result(
                "MODEL3D: BUSY", "🧊 3D models — one building",
                "A 3D model is ALREADY building for this "
                "visitor. Tell them, in persona: one at a "
                "time — it lands right here when it's done.")
        if _model_blocked(uid, tier):
            return _cap_body(tier, uid)
        pending = {"stage": "await_approval",
                   "prompt": job.get("prompt", "")}
        _set_pending(uid, pending)
        fund = ""
        if _image_week_left(uid, tier) <= 0:
            fund = (f" Their weekly images are used up, so "
                    f"this one burns {_token_rate():,} tokens "
                    f"from their balance ({_token_balance(uid):,} "
                    f"on hand) when it finishes instead.")
        body = (f"The visitor wants a 3D MODEL. Present this "
                f"plan + approval ask, in persona, with these "
                f"REAL numbers (do not invent others): "
                f"{_plan_line(pending)} on Tripo. Replying YES "
                f"builds it for real — a GLB file with a "
                f"download right here (it drops straight into "
                f"Unity, too) — and it counts as one of their "
                f"{int(_tiers.weekly_cap(tier, 'images') or 0)} "
                f"weekly images (a finished model is a one-shot "
                f"asset, charged on that fence) ONLY when it "
                f"finishes and verifies as a real GLB; a failed "
                f"build charges nothing. NO scraps it. Nothing "
                f"is built before their YES.{fund}")
        return _result("MODEL3D: APPROVE?", "🧊 3D models — approval",
                       body)
    if kind == "approve":
        ok, msg, rec = _approve(uid, tier)
        if not ok:
            return _result("MODEL3D: APPROVE-FAILED",
                           "🧊 3D models — not started",
                           msg + " Tell the visitor exactly that, "
                           "in persona — plainly.")
        return _result(
            "MODEL3D: BUILDING", "🧊 3D models — building",
            f"APPROVED — the 3D studio is BUILDING "
            f"\"{rec['title']}\" now. Tell the visitor, in "
            f"persona: locked in, it's building for real — "
            f"the GLB lands RIGHT HERE with a download when "
            f"it's done (usually a minute or a few). It only "
            f"counts against their weekly images when it "
            f"FINISHES and verifies.")
    if kind == "decline":
        ok, msg = _decline(uid)
        return _result("MODEL3D: DECLINED", "🧊 3D models — scrapped",
                       msg + " Tell the visitor that, in persona, "
                       "briefly.")
    return _down_body()


# --- Production ------------------------------------------------------


def _new_job(uid: str, pending: Dict) -> Dict:
    job_id = uuid.uuid4().hex[:16]
    prompt = pending.get("prompt", "")
    title = (prompt[:60].strip() or "OG's 3D Model")
    return {
        "id": job_id, "owner": _key(uid),
        "title": title, "prompt": prompt,
        "est_credits": est_credits(),
        "est_cost": est_cost_usd(),
        "provider": PROVIDER_NAME,
        "state": "queued", "detail": "In line — studio starting.",
        "created": time.time(), "updated": time.time(),
        "file": "", "size": 0,
        "saved": False, "charged": False, "error": "",
    }


def _update(rec: Dict, **kw) -> Dict:
    rec.update(kw)
    rec["updated"] = time.time()
    _store_put(rec)
    return rec


def _slug(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(title or "model")).strip("-")
    return (slug[:60] or "model").lower()


def _run_job(job_id: str, uid: str) -> None:
    rec = _store_get(job_id)
    if rec is None:
        return
    job_dir = os.path.join(STORE_DIR, job_id)
    try:
        with _gen_slots:
            os.makedirs(job_dir, exist_ok=True)
            _update(rec, state="generating",
                    detail="In the studio — building your 3D model…")
            data = _run_provider(rec.get("prompt", ""))
            if not _looks_like_glb(data):
                raise _ProviderError(
                    "bad_model", "downloaded file failed the GLB check")
            final = os.path.join(job_dir, "final.glb")
            with open(final, "wb") as f:
                f.write(data)
            if not os.path.exists(final) \
                    or os.path.getsize(final) < _MIN_GLB_BYTES:
                raise _ProviderError(
                    "bad_model", "saved GLB failed verification")
            _settle_model(uid, rec)
            _update(rec, state="done",
                    detail=f"\"{rec.get('title', '')}\" — a real "
                           f"3D model (GLB), ready to download.",
                    file=final, size=len(data), charged=True)
            try:
                import og_notify as _notify
                _notify.record(
                    uid, "model_done",
                    f"Your 3D model is ready: "
                    f"\"{rec.get('title', '')}\"",
                    f"\"{rec.get('title', '')}\" — a real "
                    f"text-to-3D model (GLB), ready to download.",
                    target={"view": "library", "kind": "model3d",
                            "id": job_id})
            except Exception:
                pass
    except _ProviderError as e:
        logger.warning(f"Model3D job {job_id} failed ({e.kind}): {e}")
        _fail_job(rec, uid, e.kind, str(e))
    except Exception as e:
        logger.warning(f"Model3D job {job_id} failed: {e}")
        _fail_job(rec, uid, "http", f"{type(e).__name__}: {e}")


def _fail_job(rec: Dict, uid: str, kind: str, msg: str) -> None:
    try:
        import og_fixqueue as _fq
        _fq.record_problem(
            uid, "models3d", f"model3d:{rec.get('id', '')}",
            "model3d-generate", "A 3D model build failed",
            f"The build of \"{rec.get('title', '')}\" failed "
            f"({kind}): {msg[:200]}",
            fix={"kind": "owner_steps",
                 "summary": "The 3D model build didn't finish. "
                            "Nothing was charged.",
                 "steps": [
                     "Nothing was charged — the build died "
                     "before it finished.",
                     "Ask OG for the 3D model again; a "
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
            f"That model is {_storage.fmt_size(len(data))} but "
            f"you only got {_storage.fmt_size(quota - used)} "
            f"left in your locker ({_storage.fmt_size(used)} "
            f"of {_storage.fmt_size(quota)} used). Delete "
            f"something first — say \"my storage\".")
    owner = _storage.owner_of(uid)
    fid = uuid.uuid4().hex
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_",
                  os.path.basename(str(name or "model.glb")))[:80]
    if not safe:
        safe = "model.glb"
    key = f"{owner[:32]}/{fid}-{safe}"
    _storage.get_backend().put(key, data, "model/gltf-binary")
    rec = {"id": fid, "owner": owner, "name": name,
           "size": len(data), "content_type": "model/gltf-binary",
           "kind": "file", "uploaded_at": time.time(),
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
                "detail": "That build got cut off (the server "
                          "restarted mid-build) — nothing was "
                          "charged. Ask again and I'll rebuild it."}
    if state == "done" and (not fresh or not rec.get("file")
                            or not os.path.exists(rec.get("file", ""))):
        return None
    out = {"id": rec["id"], "title": rec.get("title", ""),
           "prompt": rec.get("prompt", ""),
           "state": state, "detail": rec.get("detail", ""),
           "saved": bool(rec.get("saved", False))}
    if state == "done":
        out["model_url"] = f"/models3d/download/{rec['id']}"
        out["download_url"] = out["model_url"]
        out["size"] = int(rec.get("size", 0))
    if state == "failed":
        out["detail"] = rec.get("detail") or \
            "That build didn't make it — nothing was charged."
    return out


def register_models3d_routes(app):
    @app.get("/models3d/status")
    def models3d_status(request: Request):
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
                "est_credits": est_credits(),
                "est_cost": est_cost_usd(),
                "expires_in_s": max(0, expires)}
        locker_ok = False
        try:
            import og_storage as _storage
            locker_ok = bool(_storage.storage_enabled()
                             and _storage.quota_bytes(tier) > 0)
        except Exception:
            locker_ok = False
        return {"enabled": True, "provider": PROVIDER_NAME,
                "tier": tier,
                "cap": int(_tiers.weekly_cap(tier, "images") or 0),
                "used": _images_used_week(uid),
                "left": _image_week_left(uid, tier),
                "token_balance": _token_balance(uid),
                "locker_ok": locker_ok,
                "pending": pending_view,
                "job": _job_view(_store_latest_for_owner(_key(uid)))}

    @app.post("/models3d/approve")
    def models3d_approve(request: Request):
        if not enabled():
            return JSONResponse({"ok": False,
                                 "error": "3d models are off"},
                                status_code=404)
        uid = _route_uid(request)
        if not uid:
            return {"ok": False, "error": "no visitor identity"}
        ok, msg, rec = _approve(uid, _route_tier(uid, request))
        return {"ok": ok,
                "title": rec.get("title", "") if rec else "",
                "error": "" if ok else msg}

    @app.post("/models3d/decline")
    def models3d_decline(request: Request):
        if not enabled():
            return JSONResponse({"ok": False,
                                 "error": "3d models are off"},
                                status_code=404)
        uid = _route_uid(request)
        if not uid:
            return {"ok": False, "error": "no visitor identity"}
        ok, msg = _decline(uid)
        return {"ok": ok, "error": "" if ok else msg}

    @app.get("/models3d/download/{job_id}")
    def models3d_download(job_id: str, request: Request):
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
            rec["file"], media_type="model/gltf-binary",
            filename=_slug(rec.get("title", "model")) + ".glb")

    @app.post("/models3d/save/{job_id}")
    def models3d_save(job_id: str, request: Request):
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
                _slug(rec.get("title", "model")) + ".glb", data)
        except _LockerError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(f"Model3D locker save failed: {e}")
            return {"ok": False,
                    "error": "The locker wouldn't take it just "
                             "now — the download still works."}
        rec["saved"] = True
        _store_put(rec)
        return {"ok": True, "name": saved.get("name", "")}


# --- The seam ------------------------------------------------------------

_slot = {"job": None, "message": ""}


def install_models3d_tools(agent_instance, get_uid):
    if getattr(agent_instance, "_og_models3d_installed", False):
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
            job = _claim_model(str(message), uid) if uid else None
            if job:
                _slot["job"] = job
                _slot["message"] = str(message)
                if isinstance(intent, dict):
                    intent["needs_code_generation"] = False
                    if not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
        except Exception as e:
            logger.warning(f"Model3D trigger check failed: {e}")
            _slot["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _slot.get("job")
        message = _slot.get("message", "")
        _slot["job"] = None
        _slot["message"] = ""
        if job:
            try:
                results = model_results(job, message, get_uid())
            except Exception as e:
                logger.warning(f"Model3D job failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_models3d_installed = True
