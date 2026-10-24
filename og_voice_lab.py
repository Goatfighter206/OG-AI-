"""
OG voice lab (Generator 7f) — VOICE CLONING + SOUND EFFECTS,
built dark on the ElevenLabs rails OG Songs already uses.

Two tools, one lab:

1. VOICE CLONING (rights-gated). The visitor uploads 1-3
   audio samples, names the voice, and — in the approval
   step — confirms the rights attestation (below). Only
   then does OG create the voice at ElevenLabs
   (POST /v1/voices/add, multipart samples -> voice_id).
   The raw samples are NEVER retained after creation: the
   staged files are deleted at every terminal resolution
   (creation confirmed, failure, decline, expiry). Only
   {voice_id, name, created} is stored, per-uid. The cloned
   voices are selectable as `voice:<voice_id>` options:
   GET /voice-lab/voices lists them, POST /voice-lab/tts
   speaks in one (owner-checked), and og_podcast's synth
   seam delegates `voice:` host voices here (its runner
   checks ownership first). POST /voice-lab/delete removes
   a voice at the provider and wipes the local record.

2. SOUND EFFECTS. "make a sound effect: X" / "sfx: X" /
   "sound of X" parks a pending with the description and a
   duration estimate; on approval ONE ElevenLabs call
   (POST /v1/sound-generation {text, duration_seconds})
   renders an MP3 -> verified -> delivered with an in-chat
   player, owner-only download, locker save, Library kind
   "sfx" and an sfx_done notification.

RIGHTS GATE (hard rule): a user may clone ONLY a voice
they have rights to. The approval card carries the exact
attestation line in ATTESTATION_LINE. No attestation -> no
clone, BY CONSTRUCTION: the provider client is reachable
only past the attestation check in _approve_clone, and the
suite pins that an unattested approval makes zero calls.

DARK GATE (independent by design): live only when
OG_VOICE_ENABLED is truthy AND OG_ELEVENLABS_API_KEY is set
— the SAME key OG Songs rides, but its OWN flag, so Brent
can light songs and voice independently. Dark =
GET /voice-lab/status answers {"enabled": false}, every
other door 404s, claims never fire EXCEPT an explicit
clone/sfx ask, which gets the honest "isn't switched on
yet" line, and zero provider calls happen. The key lives
in Render env only.

CHARGE MAPPING (documented, suite-pinned): a sound effect
is a SHORT AUDIO generation, so it charges the `tts` kind
ONCE per completed, verified generation (usage entry
"tts:<uid>", the Round 55 weekly tts fence, token-pack
fallback at RATES["tts"] = 175 past the wall) — the same
fence podcasts meter short audio into. It does NOT charge
`song`: a sound effect is not a sung track, the song fence
is far scarcer (free 0/week), and an effect must never eat
a song slot. Failures charge nothing. Cloning itself
charges nothing (setup, like connecting an account);
speaking with a cloned voice via /voice-lab/tts charges
the `tts` kind once per call, at call time, like /tts.

SAMPLES / UPLOAD AUDIT: the existing /upload machinery
(og_file_read) accepts only PDF/TXT/PNG/JPG/WEBP and keeps
ONE active file per visitor — audio samples cannot ride
it. POST /voice-lab/samples therefore reuses its limits
and posture: per-tier size ceiling og_tiers.cap(tier,
"upload_mb") (free/standard 8 MB, pro+ 25 MB per sample),
audio sniffed by magic bytes via og_voice._sniff_audio
(webm/ogg/m4a/mp3/wav), owner keyed by the ogai_uid hash.

All provider interaction sits behind ONE client seam
(ElevenLabsVoiceClient via _run_clone / _run_delete /
_run_sfx / _run_cloned_tts) — tests replace those wrappers
wholesale and exercise the client against mocked HTTP.
HTTP is stdlib urllib (multipart built by hand); no new
deps. Persona files are NEVER touched by this module.
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
from fastapi.responses import FileResponse, JSONResponse, Response

import og_tiers as _tiers

logger = logging.getLogger(__name__)

STORE_DIR = os.getenv("OG_VOICE_STORE_DIR", "voice_builds")
_PENDING_TTL = 30 * 60
_JOB_TTL = 24 * 60 * 60
_STALE_S = 45 * 60
_MAX_SAMPLES = 3
_DEFAULT_SFX_S = 5
_MAX_SFX_S = 30

ATTESTATION_LINE = \
    "This is my voice or I have permission to clone it"
PROVIDER_NAME = "elevenlabs"
# The /tts voice table (app.py TTS_VOICES) — listed by
# /voice-lab/voices so a picker can show every option.
BUILTIN_VOICES = ("onyx", "fable", "echo", "nova",
                  "shimmer", "alloy")

# Bound by bind_app when a host binds them (tests); unbound
# (production) each dep resolves lazily from the fully-loaded
# app module — the og_podcast/og_books precedent.
_deps: Dict = {}

_APP_ATTRS = {
    "load_usage": "_load_usage_store",
    "save_usage": "_save_usage_store",
    "usage_lock": "_usage_lock",
    "load_history": "_load_visitor_history_locked",
}


def bind_app(deps):
    _deps.update(deps)


def _dep(name):
    if name in _deps:
        return _deps[name]
    if name == "get_tier":
        def _get_tier():
            try:
                import app as _appmod
                return str(_appmod._current_tier.get(
                    "tier", "free") or "free")
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
        logger.warning(f"og_voice_lab: app dep {name} "
                       f"unavailable: {e}")
        return None


def _pro_url() -> str:
    base = os.getenv("OG_PUBLIC_URL",
                     "https://og-ai-service.onrender.com")
    return base.rstrip("/") + "/pro"


# --- Provider gate / config ------------------------------------

def _flag_on() -> bool:
    return os.getenv("OG_VOICE_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on")


def _provider_key() -> str:
    # THE SAME key OG Songs rides (og_songs._provider_key).
    return os.getenv("OG_ELEVENLABS_API_KEY", "").strip()


def _provider_base() -> str:
    return os.getenv(
        "OG_VOICE_API_URL",
        "https://api.elevenlabs.io/v1").strip().rstrip("/")


def _tts_model() -> str:
    return os.getenv("OG_VOICE_TTS_MODEL",
                     "eleven_turbo_v2_5").strip() \
        or "eleven_turbo_v2_5"


def _output_format() -> str:
    return os.getenv("OG_VOICE_OUTPUT_FORMAT",
                     "mp3_44100_128").strip() or "mp3_44100_128"


def enabled() -> bool:
    """Voice lab is live only when its OWN flag is on AND
    the shared ElevenLabs key is configured — independent
    of OG_SONG_ENABLED, so songs and voice light separately."""
    return _flag_on() and bool(_provider_key())


def provider_name() -> str:
    return PROVIDER_NAME


# --- Usage / caps (the weekly `tts` fence — see header) ---------


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _usage_get(key: str):
    lock = _dep("usage_lock")
    load = _dep("load_usage")
    if lock is None or load is None:
        return None
    with lock:
        store = load()
    entry = store.get(key)
    return dict(entry) if isinstance(entry, dict) else None


def _tts_used_week(uid: str) -> int:
    return _tiers.week_used(_usage_get(f"tts:{uid}"))


def _tts_week_left(uid: str, tier: str) -> int:
    cap_w = _tiers.weekly_cap(tier, "tts")
    if cap_w is None:
        return 1
    return max(0, int(cap_w) - _tts_used_week(uid))


def _charge_tts(uid: str) -> None:
    """Spend one unit of the weekly `tts` fence. For SFX
    this is called ONLY after a generation completes and
    verifies; for /voice-lab/tts it is called at call time,
    exactly the /tts posture."""
    lock = _dep("usage_lock")
    load = _dep("load_usage")
    save = _dep("save_usage")
    if lock is None or load is None or save is None:
        return
    key = f"tts:{uid}"
    with lock:
        store = load()
        entry = store.get(key)
        if not isinstance(entry, dict) \
                or entry.get("date") != _today():
            old, entry = entry, {"date": _today(), "count": 0}
            _tiers.carry_days(old, entry)
        entry["count"] = int(entry.get("count", 0)) + 1
        _tiers.note_day(entry, _today(), entry["count"])
        store[key] = entry
        save(store)


def _token_balance(uid: str) -> int:
    try:
        import og_tokenpacks as _tp
        return _tp.balance(uid)
    except Exception:
        return 0


def _token_rate() -> int:
    try:
        import og_tokenpacks as _tp
        return int(_tp.RATES["tts"])
    except Exception:
        return 0


def _tts_blocked(uid: str, tier: str) -> bool:
    if _tts_week_left(uid, tier) > 0:
        return False
    rate = _token_rate()
    return rate <= 0 or _token_balance(uid) < rate


def _settle_tts(uid: str, rec: Dict) -> None:
    """Charge a completed, verified SFX generation exactly
    once in the `tts` fence: the weekly pool, or tokens for
    a token-funded job (marked at approval time)."""
    if rec.get("fund") == "tokens":
        try:
            import og_tokenpacks as _tp
            _tp.debit(uid, int(_tp.RATES["tts"]), "tts")
        except Exception:
            pass
        return
    _charge_tts(uid)


def _tier_now(uid: str) -> str:
    fn = _dep("get_tier")
    try:
        return str(fn() or "free") if fn else "free"
    except Exception:
        return "free"


# --- Owner key / pending approvals -------------------------------

def _key(uid: str) -> str:
    return hashlib.sha256(str(uid).encode()).hexdigest()[:32]


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
            expired = dict(state)
        else:
            expired = None
    if expired is not None:
        # Expiry is a terminal resolution for a clone: the
        # staged samples are wiped with the pending.
        if expired.get("flow") == "clone":
            _wipe_samples(uid)
        return None
    return dict(state) if state else None


def _clear_pending(uid: str, wipe_samples: bool = True):
    with _pending_lock:
        state = _pending.pop(uid, None)
    if wipe_samples and state and state.get("flow") == "clone":
        _wipe_samples(uid)


# --- Sample staging (temp only — wiped at every resolution) -----


def _staging_dir(uid: str) -> str:
    return os.path.join(STORE_DIR, "staging", _key(uid))


def _wipe_samples(uid: str) -> None:
    import shutil
    try:
        shutil.rmtree(_staging_dir(uid), ignore_errors=True)
    except Exception:
        pass


def _sniff(filename: str, content_type: str, data: bytes):
    """(ext, mime) for an audio sample — og_voice's Round 8
    magic-byte sniff, reused."""
    try:
        import og_voice as _ov
        return _ov._sniff_audio(filename, content_type, data)
    except Exception:
        return None


def _sample_max_bytes(tier: str) -> int:
    # The /upload per-tier ceiling (og_file_read._max_bytes
    # rides og_tiers.cap(tier, "upload_mb")).
    try:
        return int(_tiers.cap(tier, "upload_mb")) * 1024 * 1024
    except Exception:
        return 8 * 1024 * 1024


def stage_sample(uid: str, filename: str, content_type: str,
                 data: bytes, tier: str) -> Dict:
    """Validate and stage ONE clone sample for this visitor.
    Returns a meta dict; raises ValueError with the honest
    line for bad type / oversize / too many samples. Files
    live only in the staging dir and only until the clone
    resolves — they are never copied anywhere else."""
    sniffed = _sniff(filename, content_type, data)
    if not sniffed:
        raise ValueError(
            "That sample isn't audio I can use — I take "
            "webm, ogg, m4a, mp3 or wav clips.")
    ext, mime = sniffed
    if not data:
        raise ValueError("That sample came through empty.")
    limit = _sample_max_bytes(tier)
    if len(data) > limit:
        raise ValueError(
            f"That sample is too heavy — I max out at "
            f"{max(1, limit // (1024 * 1024))} MB per sample "
            f"on your plan.")
    pending = _get_pending(uid)
    samples = list((pending or {}).get("samples") or [])
    if len(samples) >= _MAX_SAMPLES:
        raise ValueError(
            f"That's the {_MAX_SAMPLES}-sample limit for one "
            f"clone — approve or scrap this one first.")
    os.makedirs(_staging_dir(uid), exist_ok=True)
    fname = f"sample{len(samples) + 1}.{ext}"
    with open(os.path.join(_staging_dir(uid), fname), "wb") as f:
        f.write(data)
    meta = {"file": fname, "name": os.path.basename(
        str(filename or fname))[:80], "size": len(data),
        "mime": mime, "ext": ext}
    samples.append(meta)
    if pending is None or pending.get("flow") != "clone":
        pending = {"flow": "clone", "stage": "await_samples",
                   "name": "", "attested": False}
    pending = dict(pending)
    pending["samples"] = samples
    if len(samples) >= 1 and pending.get("stage") == "await_samples":
        pending["stage"] = "await_approval"
    _set_pending(uid, pending)
    return meta


def _read_samples(uid: str, metas: List[Dict]):
    out = []
    for m in metas or []:
        path = os.path.join(_staging_dir(uid), m.get("file", ""))
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception:
            continue
        out.append((m.get("name") or "sample", data,
                    m.get("mime") or "audio/mpeg"))
    return out


# --- Voices store (per-uid {voice_id, name, created} only) ------

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo, pooled_connect as _pooled_connect
try:
    import psycopg
except Exception:
    psycopg = None

_vstore_lock = threading.Lock()
_vstore_mem: Dict[str, Dict] = {}


def _vstore_path() -> str:
    os.makedirs(STORE_DIR, exist_ok=True)
    return os.path.join(STORE_DIR, "voices.json")


def _vstore_load(uid: str) -> Dict:
    owner = _key(uid)
    with _vstore_lock:
        rec = _vstore_mem.get(owner)
    if rec is not None:
        return {"voices": list(rec.get("voices") or []),
                "clone_state": dict(rec.get("clone_state") or {})}
    if MEMORY_DB_URL and psycopg is not None:
        try:
            conn = _pooled_connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
            _apply_bounds(conn)
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "CREATE TABLE IF NOT EXISTS og_voice_voices "
                        "(owner TEXT PRIMARY KEY, data TEXT)")
                    cur.execute(
                        "SELECT data FROM og_voice_voices "
                        "WHERE owner = %s", (owner,))
                    row = cur.fetchone()
                conn.commit()
            if row:
                rec = json.loads(row[0])
                with _vstore_lock:
                    _vstore_mem[owner] = rec
                return {"voices": list(rec.get("voices") or []),
                        "clone_state": dict(
                            rec.get("clone_state") or {})}
        except Exception as e:
            logger.warning(f"Voice store DB read failed: {e}")
    try:
        path = _vstore_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            rec = data.get(owner)
            if isinstance(rec, dict):
                with _vstore_lock:
                    _vstore_mem[owner] = rec
                return {"voices": list(rec.get("voices") or []),
                        "clone_state": dict(
                            rec.get("clone_state") or {})}
    except Exception as e:
        logger.warning(f"Voice store JSON read failed: {e}")
    return {"voices": [], "clone_state": {}}


def _vstore_save(uid: str, data: Dict) -> None:
    owner = _key(uid)
    rec = {"voices": list(data.get("voices") or []),
           "clone_state": dict(data.get("clone_state") or {})}
    with _vstore_lock:
        _vstore_mem[owner] = rec
    if MEMORY_DB_URL and psycopg is not None:
        try:
            conn = _pooled_connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
            _apply_bounds(conn)
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "CREATE TABLE IF NOT EXISTS og_voice_voices "
                        "(owner TEXT PRIMARY KEY, data TEXT)")
                    cur.execute(
                        "INSERT INTO og_voice_voices (owner, data) "
                        "VALUES (%s, %s) ON CONFLICT (owner) DO "
                        "UPDATE SET data = EXCLUDED.data",
                        (owner, json.dumps(rec)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Voice store DB write failed: {e}")
    try:
        path = _vstore_path()
        all_data = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                all_data = json.load(f) or {}
        all_data[owner] = rec
        with open(path, "w", encoding="utf-8") as f:
            json.dump(all_data, f)
    except Exception as e:
        logger.warning(f"Voice store JSON write failed: {e}")


def list_voices(uid: str) -> List[Dict]:
    """This visitor's OWN cloned voices, oldest first —
    {voice_id, name, created, option} where option is the
    `voice:<voice_id>` selector other surfaces consume."""
    out = []
    for v in _vstore_load(uid)["voices"]:
        vid = str(v.get("voice_id", ""))
        if not vid:
            continue
        out.append({"voice_id": vid,
                    "name": v.get("name", ""),
                    "created": float(v.get("created", 0) or 0),
                    "option": f"voice:{vid}",
                    "provider": PROVIDER_NAME})
    return out


def get_voice(uid: str, voice_id: str) -> Optional[Dict]:
    """The visitor's OWN voice record for voice_id, or None —
    the ownership check every cloned-voice use goes through."""
    for v in list_voices(uid):
        if v["voice_id"] == str(voice_id):
            return v
    return None


def _add_voice(uid: str, voice_id: str, name: str) -> Dict:
    data = _vstore_load(uid)
    data["voices"] = [v for v in data["voices"]
                      if v.get("voice_id") != voice_id]
    rec = {"voice_id": voice_id, "name": name,
           "created": time.time()}
    data["voices"].append(rec)
    _vstore_save(uid, data)
    return rec


def _set_clone_state(uid: str, state: Dict) -> None:
    data = _vstore_load(uid)
    data["clone_state"] = dict(state or {})
    _vstore_save(uid, data)


# --- SFX job records (Postgres when configured, else JSON) ------

_jobs_lock = threading.Lock()
_jobs_mem: Dict[str, Dict] = {}
_gen_slots = threading.Semaphore(2)


def _json_store_path() -> str:
    os.makedirs(STORE_DIR, exist_ok=True)
    return os.path.join(STORE_DIR, "sfx_jobs.json")


def _db_connect():
    conn = _pooled_connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_voice_jobs ("
            "id TEXT PRIMARY KEY, owner TEXT, "
            "updated DOUBLE PRECISION, data TEXT)")
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
                        "INSERT INTO og_voice_jobs (id, owner, "
                        "updated, data) VALUES (%s, %s, %s, %s) "
                        "ON CONFLICT (id) DO UPDATE SET "
                        "owner = EXCLUDED.owner, "
                        "updated = EXCLUDED.updated, "
                        "data = EXCLUDED.data",
                        (rec["id"], rec.get("owner", ""),
                         rec["updated"], json.dumps(rec)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"SFX job DB write failed: {e}")
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
        logger.warning(f"SFX job JSON write failed: {e}")


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
                        "SELECT data FROM og_voice_jobs "
                        "WHERE id = %s", (job_id,))
                    row = cur.fetchone()
            if row:
                rec = json.loads(row[0])
                with _jobs_lock:
                    _jobs_mem[job_id] = rec
                return dict(rec)
        except Exception as e:
            logger.warning(f"SFX job DB read failed: {e}")
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
        logger.warning(f"SFX job JSON read failed: {e}")
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
                        "SELECT data FROM og_voice_jobs WHERE "
                        "owner = %s ORDER BY updated DESC "
                        "LIMIT 1", (owner,))
                    row = cur.fetchone()
            if row:
                cands.append(json.loads(row[0]))
        except Exception as e:
            logger.warning(f"SFX job DB scan failed: {e}")
    try:
        path = _json_store_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            cands.extend(v for v in data.values()
                         if v.get("owner") == owner)
    except Exception as e:
        logger.warning(f"SFX job JSON scan failed: {e}")
    for rec in cands:
        if best is None or float(rec.get("updated", 0)) \
                > float(best.get("updated", 0)):
            best = rec
    return best


def list_jobs_for_owner(uid: str) -> List[Dict]:
    """All of the visitor's OWN sfx jobs, newest first
    (Library). Retention is the caller's _job_view call."""
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
                        "SELECT data FROM og_voice_jobs "
                        "WHERE owner = %s", (owner,))
                    rows = cur.fetchall()
            for row in rows:
                rec = json.loads(row[0])
                found[rec["id"]] = rec
        except Exception as e:
            logger.warning(f"SFX job DB list failed: {e}")
    try:
        path = _json_store_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            for v in data.values():
                if v.get("owner") == owner:
                    found[v["id"]] = v
    except Exception as e:
        logger.warning(f"SFX job JSON list failed: {e}")
    return sorted(found.values(),
                  key=lambda r: float(r.get("created", 0)),
                  reverse=True)


def _active_job(uid: str) -> Optional[Dict]:
    rec = _store_latest_for_owner(_key(uid))
    if rec and rec.get("state") in ("queued", "generating"):
        if time.time() - float(rec.get("updated", 0)) > _STALE_S:
            return None
        return rec
    return None


def purge_owner(uid: str):
    """Account deletion (og_store_ready): provider-delete
    every cloned voice (fail-safe), wipe the voices store,
    the sfx jobs (mem + DB + JSON + build dirs), staged
    samples and pendings. Other owners never touched."""
    if not uid:
        return
    for v in list_voices(uid):
        try:
            _run_delete(v["voice_id"])
        except Exception:
            pass
    owner = _key(uid)
    with _vstore_lock:
        _vstore_mem.pop(owner, None)
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM og_voice_voices "
                        "WHERE owner = %s", (owner,))
                    cur.execute(
                        "DELETE FROM og_voice_jobs "
                        "WHERE owner = %s", (owner,))
                conn.commit()
        except Exception as e:
            logger.warning(f"Voice lab DB purge failed: {e}")
        try:
            conn = _pooled_connect(_normalize_conninfo(MEMORY_DB_URL),
                                   connect_timeout=_DB_CT)
            _apply_bounds(conn)
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "CREATE TABLE IF NOT EXISTS "
                        "og_voice_voices (owner TEXT PRIMARY "
                        "KEY, data TEXT)")
                    cur.execute(
                        "DELETE FROM og_voice_voices "
                        "WHERE owner = %s", (owner,))
                conn.commit()
        except Exception:
            pass
    try:
        path = _vstore_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            if owner in data:
                del data[owner]
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f)
    except Exception as e:
        logger.warning(f"Voice store JSON purge failed: {e}")
    ids = [r["id"] for r in list_jobs_for_owner(uid)]
    if ids:
        with _jobs_lock:
            for jid in ids:
                _jobs_mem.pop(jid, None)
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
        logger.warning(f"SFX job JSON purge failed: {e}")
    import shutil
    for jid in ids:
        try:
            shutil.rmtree(os.path.join(STORE_DIR, jid),
                          ignore_errors=True)
        except Exception:
            pass
    _clear_pending(uid, wipe_samples=True)


# --- Provider client (THE spend seam — mocked in tests) ---------


class _ProviderError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _looks_like_mp3(data: bytes) -> bool:
    if not data or len(data) < 1024:
        return False
    if data[:3] == b"ID3":
        return True
    return len(data) > 2 and data[0] == 0xFF \
        and (data[1] & 0xE0) == 0xE0


def _http_raw(method: str, url: str, body: bytes = None,
              headers: Dict = None, timeout: int = 300):
    """(status, bytes) for one ElevenLabs call. Every client
    call goes through here; tests mock this helper."""
    hdrs = {"xi-api-key": _provider_key()}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read()
        except Exception:
            return e.code, b""
    except Exception as e:
        raise _ProviderError("network",
                             f"studio unreachable: {e}")


def _multipart(fields: Dict, files) -> (bytes, str):
    """Hand-built multipart/form-data body (stdlib only).
    files: list of (field, filename, data, mime)."""
    boundary = "----ogvoicelab" + uuid.uuid4().hex
    out = bytearray()
    for name, value in (fields or {}).items():
        out += f"--{boundary}\r\n".encode()
        out += (f'Content-Disposition: form-data; '
                f'name="{name}"\r\n\r\n').encode()
        out += str(value).encode("utf-8") + b"\r\n"
    for field, filename, data, mime in files or []:
        safe = re.sub(r'["\r\n]', "_", str(filename or "sample"))
        out += f"--{boundary}\r\n".encode()
        out += (f'Content-Disposition: form-data; '
                f'name="{field}"; filename="{safe}"\r\n'
                f'Content-Type: {mime or "audio/mpeg"}'
                f'\r\n\r\n').encode()
        out += bytes(data) + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def _err_kind(status: int) -> str:
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate"
    return "http"


class ElevenLabsVoiceClient:
    """The official ElevenLabs shapes this lab uses:
    POST /voices/add (multipart -> {voice_id}),
    DELETE /voices/{voice_id},
    POST /sound-generation {text, duration_seconds} -> MP3,
    POST /text-to-speech/{voice_id} {text, model_id} -> MP3.
    """

    def create_voice(self, name: str, samples) -> str:
        files = [("files", fn, data, mime)
                 for fn, data, mime in samples]
        body, ctype = _multipart({"name": name}, files)
        status, raw = _http_raw(
            "POST", _provider_base() + "/voices/add", body,
            {"Content-Type": ctype}, timeout=300)
        if status not in (200, 201):
            raise _ProviderError(
                _err_kind(status),
                f"studio answered {status}: "
                f"{raw[:200]!r}")
        try:
            voice_id = json.loads(
                raw.decode("utf-8", "replace"))["voice_id"]
        except Exception:
            raise _ProviderError(
                "http", "studio sent no voice_id back")
        if not voice_id:
            raise _ProviderError(
                "http", "studio sent an empty voice_id")
        return str(voice_id)

    def delete_voice(self, voice_id: str) -> None:
        status, raw = _http_raw(
            "DELETE",
            f"{_provider_base()}/voices/{voice_id}",
            timeout=60)
        if status not in (200, 204):
            raise _ProviderError(
                _err_kind(status),
                f"studio answered {status} on delete")

    def generate_sfx(self, text: str, duration: int) -> bytes:
        url = (f"{_provider_base()}/sound-generation"
               f"?output_format={_output_format()}")
        body = json.dumps({
            "text": str(text)[:1000],
            "duration_seconds": float(duration),
            "prompt_influence": 0.3}).encode("utf-8")
        status, raw = _http_raw(
            "POST", url, body,
            {"Content-Type": "application/json"}, timeout=300)
        if status != 200:
            raise _ProviderError(
                _err_kind(status),
                f"studio answered {status}: {raw[:200]!r}")
        if not _looks_like_mp3(raw):
            raise _ProviderError(
                "bad_audio",
                f"studio sent {len(raw)} bytes that are not "
                f"an MP3")
        return raw

    def synth(self, voice_id: str, text: str) -> bytes:
        url = (f"{_provider_base()}/text-to-speech/{voice_id}"
               f"?output_format={_output_format()}")
        body = json.dumps({
            "text": str(text)[:2000],
            "model_id": _tts_model()}).encode("utf-8")
        status, raw = _http_raw(
            "POST", url, body,
            {"Content-Type": "application/json"}, timeout=120)
        if status != 200:
            raise _ProviderError(
                _err_kind(status),
                f"studio answered {status}: {raw[:200]!r}")
        if not raw or len(raw) < 100:
            raise _ProviderError(
                "bad_audio",
                f"studio sent {len(raw)} bytes of audio")
        return raw


_client = ElevenLabsVoiceClient()


def _run_clone(name: str, samples) -> str:
    """ONE seam: create a cloned voice, return its voice_id.
    samples: [(filename, bytes, mime)]. Tests replace this."""
    if not _provider_key():
        raise _ProviderError("auth", "no studio key configured")
    return _client.create_voice(name, samples)


def _run_delete(voice_id: str) -> None:
    """ONE seam: delete a voice at the provider."""
    if not _provider_key():
        raise _ProviderError("auth", "no studio key configured")
    _client.delete_voice(voice_id)


def _run_sfx(text: str, duration: int) -> bytes:
    """ONE seam: generate one sound effect, return MP3 bytes."""
    if not _provider_key():
        raise _ProviderError("auth", "no studio key configured")
    return _client.generate_sfx(text, duration)


def _run_cloned_tts(voice_id: str, text: str) -> bytes:
    """ONE seam: speak text in a cloned voice (ElevenLabs
    TTS with that voice_id), return MP3 bytes. Ownership is
    the CALLER's check (routes / the podcast runner)."""
    if not _provider_key():
        raise _ProviderError("auth", "no studio key configured")
    return _client.synth(voice_id, text)


def pack_enabled() -> bool:
    """Ticket R real-voice rebuild: the pack-only gate.
    True exactly when the meaner pack's OWN switch is on
    (OG_VOICE_PACK_EL_ENABLED truthy) AND the provider
    key is present. DELIBERATELY narrower than
    enabled(): the lab switch (OG_VOICE_ENABLED) also
    lights cloning and SFX, which Brent has not approved
    shipping — the pack never rides it. Read at request
    time by the /tts pack branch and the Voice folder
    payload, so flipping the Render env var takes
    effect with no redeploy."""
    flag = os.getenv("OG_VOICE_PACK_EL_ENABLED", "") \
        .strip().lower() in ("1", "true", "yes", "on")
    return flag and bool(_provider_key())


def _run_library_tts(voice_id: str, text: str) -> bytes:
    """ONE seam (Ticket R real-voice rebuild): speak text
    in a LIBRARY voice (ElevenLabs TTS with that
    voice_id) for the meaner pack's /tts branch, return
    MP3 bytes. The pack's voices ARE library voices, so
    this shares the cloned path's client, base URL,
    model and output format — it clones, lists and
    deletes nothing. The client is blocking urllib:
    call through asyncio.to_thread (the Main Thread
    rule). Raises _ProviderError on every failure; the
    /tts route turns that into an honest 502 and never
    falls back to another engine."""
    if not _provider_key():
        raise _ProviderError("auth", "no studio key configured")
    return _client.synth(voice_id, text)


_FAIL_DETAILS = {
    "auth": "The studio key got rejected — the voice lab "
            "needs a fix on OG's side. Nothing was charged.",
    "rate": "The studio is slammed right now — nothing was "
            "charged. Give it a minute and ask again.",
    "network": "The studio didn't answer in time — nothing "
               "was charged. Ask again and I'll redo it.",
    "bad_audio": "The studio sent back something that isn't "
                 "playable audio — nothing was charged.",
    "http": "That take didn't make it — nothing was "
            "charged. Ask again and I'll redo it.",
}


# --- Narration (grounded result blocks the persona voices) ------


def _result(tag: str, title: str, body: str,
            href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body,
             "href": href}]


def _down_body() -> List[Dict]:
    return _result(
        "VOICE LAB: DOWN", "🎙️ Voice lab — not switched on",
        "The voice lab (voice cloning + sound effects) isn't "
        "switched on yet. Tell the visitor plainly, in "
        "persona — no fake promises: OG can still chat, sing "
        "and make pictures right here; cloning a voice and "
        "making sound effects wait until the voice lab is "
        "switched on.")


def _cap_body(tier: str, uid: str) -> List[Dict]:
    cap_w = int(_tiers.weekly_cap(tier, "tts") or 0)
    refill = _tiers.week_refill_text(_usage_get(f"tts:{uid}"))
    body = (f"The visitor is at THIS WEEK's voice cap for "
            f"their plan ({cap_w} per week — sound effects "
            f"share the voice pool with voice replies). "
            f"{refill}. Their token balance "
            f"({_token_balance(uid):,} tokens) can't cover "
            f"one either — one effect burns "
            f"{_token_rate():,} tokens past the weekly wall. "
            f"Tell them plainly, in persona. Higher plans "
            f"get more voice a week: {_pro_url()}")
    return _result("VOICE LAB: AT-CAP",
                   "🎙️ Voice lab — capped", body)


# --- Claim table --------------------------------------------------

_YES_WORDS = {
    "yes", "yeah", "yep", "yup", "ya", "ok", "okay", "sure",
    "approve", "approved", "go", "do it", "make it", "lets go",
    "let's go", "yes please", "yes make it", "yes do it", "👍",
}
_NO_WORDS = {
    "no", "nope", "nah", "decline", "declined", "scrap it",
    "never mind", "nevermind", "forget it", "cancel", "stop",
    "don't", "dont", "no thanks",
}

_CLONE_RE = re.compile(
    r"\bclone\b[^.?!]{0,60}\bvoice\b|\bvoice\s+clone\b|"
    r"\bcloning\b[^.?!]{0,30}\bvoice\b", re.I)
_CLONE_NAME_RE = re.compile(
    r"\b(?:named|called|name it|call it|as)\s+"
    r"[\"']?([A-Za-z0-9][A-Za-z0-9 _\-]{0,39})", re.I)
_SFX_PREFIX = re.compile(r"^\s*sfx\s*[:\-]\s*(.+)$", re.I | re.S)
_SFX_MAKE = re.compile(
    r"\b(?:make|generate|create|produce)\b[^.?!]{0,30}?"
    r"\b(?:sound effect|sfx)\b\s*(?:of\s+|:)?\s*(.*)$", re.I | re.S)
_SFX_SOUND_OF = re.compile(
    r"\b(?:make|generate|create|produce)\b[^.?!]{0,20}?"
    r"\bsound\s+of\s+(.+)$", re.I | re.S)
_SFX_BARE = re.compile(
    r"^\s*(?:sound effect|sound)\s+of\s+(.+)$", re.I | re.S)
_DURATION = re.compile(
    r"\b(\d{1,2})\s*(?:s|sec|secs|second|seconds)\b", re.I)
_GUARD_NOUN = re.compile(
    r"\b(song|podcast|book|poster|thumbnail|meme|"
    r"3d model|motion video)\b", re.I)


def _norm(message: str) -> str:
    return " ".join(str(message or "").lower().split()).strip(" .!")


def _attested_in(message: str) -> bool:
    low = str(message or "").lower()
    return "this is my voice" in low or \
        "i have permission to clone" in low or \
        ("permission" in low and "clone" in low
         and "my voice" in low)


def parse_clone(message: str) -> Optional[Dict]:
    """Explicit clone ask -> {name} or None. Pure."""
    raw = str(message or "")
    if not _CLONE_RE.search(raw):
        return None
    name = ""
    m = _CLONE_NAME_RE.search(raw)
    if m:
        name = m.group(1).strip().strip(".!")[:40]
        # "as me/my voice" style tails are not names.
        if name.lower() in ("me", "my voice", "mine",
                            "my own voice"):
            name = ""
    return {"name": name}


def parse_sfx(message: str) -> Optional[Dict]:
    """Explicit sound-effect ask -> {description, duration}
    or None. Pure — no gate, no pending."""
    raw = str(message or "").strip()
    if not raw:
        return None
    low = raw.lower()
    if re.match(r"^\s*(what|how|why|when|who)\b", low) \
            and not _SFX_PREFIX.match(raw):
        return None
    desc = ""
    matched = False
    m = _SFX_PREFIX.match(raw)
    if m:
        matched = True
        desc = m.group(1)
    else:
        m = _SFX_MAKE.search(raw)
        if m:
            matched = True
            desc = m.group(1)
        else:
            m = _SFX_SOUND_OF.search(raw)
            if m:
                matched = True
                desc = m.group(1)
            else:
                m = _SFX_BARE.match(raw)
                if m:
                    matched = True
                    desc = m.group(1)
    if not matched:
        return None
    desc = " ".join(str(desc or "").split()).strip(" .!")
    if not desc:
        return {"description": "", "duration": _DEFAULT_SFX_S}
    if _GUARD_NOUN.search(low) and not _SFX_PREFIX.match(raw) \
            and "sound effect" not in low and "sfx" not in low:
        return None
    duration = _DEFAULT_SFX_S
    dm = _DURATION.search(raw)
    if dm:
        try:
            duration = max(1, min(_MAX_SFX_S, int(dm.group(1))))
        except ValueError:
            pass
        desc = _DURATION.sub("", desc).strip(" ,.-")
        desc = " ".join(desc.split())
        desc = re.sub(r"\s+(for|of|in)$", "", desc).strip()
    if not desc:
        return {"description": "", "duration": duration}
    return {"description": desc[:300], "duration": duration}


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
                     "og_books", "og_motion", "og_models3d"):
        try:
            import importlib
            mod = importlib.import_module(mod_name)
            pend = mod._get_pending(uid)
            if pend and float(pend.get("created", 0)) > created:
                return True
        except Exception:
            continue
    return False



def _claim_voice(message: str, uid: str) -> Optional[Dict]:
    if not uid:
        return None
    raw = str(message or "")
    norm = _norm(raw)
    pending = _get_pending(uid) if enabled() else None
    if pending is not None:
        if pending.get("stage") == "await_desc":
            if norm in _NO_WORDS:
                return {"kind": "decline"}
            if len(norm) >= 2:
                dur = _DEFAULT_SFX_S
                dm = _DURATION.search(raw)
                if dm:
                    try:
                        dur = max(1, min(_MAX_SFX_S,
                                         int(dm.group(1))))
                    except ValueError:
                        pass
                return {"kind": "sfx_new",
                        "description": raw.strip()[:300],
                        "duration": dur}
            return None
        # A fresh explicit ask replaces the parked one.
        clone = parse_clone(raw)
        if clone is not None and pending.get("flow") != "clone":
            return {"kind": "clone_new", **clone}
        sfx = parse_sfx(raw)
        if sfx is not None and sfx.get("description") \
                and pending.get("flow") != "sfx":
            return {"kind": "sfx_new", **sfx}
        if pending.get("flow") == "clone":
            if _attested_in(raw):
                if _browser_has_pending(uid) \
                        or _other_pending_newer(uid, pending):
                    return None
                return {"kind": "clone_approve", "attested": True}
            m = _CLONE_NAME_RE.search(raw)
            if m and pending.get("stage") in (
                    "await_samples", "await_approval") \
                    and norm not in _YES_WORDS \
                    and norm not in _NO_WORDS:
                name = m.group(1).strip().strip(".!")[:40]
                if name and name.lower() not in (
                        "me", "my voice", "mine"):
                    return {"kind": "clone_name", "name": name}
        if norm in _YES_WORDS:
            if _browser_has_pending(uid):
                return None
            if _other_pending_newer(uid, pending):
                return None
            if pending.get("flow") == "clone":
                return {"kind": "clone_approve",
                        "attested": bool(pending.get("attested"))}
            return {"kind": "sfx_approve"}
        if norm in _NO_WORDS:
            if _browser_has_pending(uid):
                return None
            if _other_pending_newer(uid, pending):
                return None
            return {"kind": "decline"}
        return None
    # No pending parked.
    clone = parse_clone(raw)
    sfx = parse_sfx(raw)
    if clone is None and sfx is None:
        return None
    if not enabled():
        # Dark: an explicit ask gets the honest line only.
        return {"kind": "down"}
    if clone is not None:
        return {"kind": "clone_new", **clone}
    if sfx is not None:
        if not sfx.get("description"):
            return {"kind": "sfx_need_desc"}
        return {"kind": "sfx_new", **sfx}
    return None


# --- Flow results -------------------------------------------------


def _approve_clone(uid: str, attested: bool):
    """Start a clone creation. RIGHTS GATE: without the
    attestation this returns BEFORE any provider seam is
    touched — no attestation -> no clone, by construction.
    Returns (ok, message, state)."""
    pending = _get_pending(uid)
    if pending is None or pending.get("flow") != "clone":
        return False, "Nothing is waiting on approval — it " \
            "expired or was already answered.", None
    if not attested and not pending.get("attested"):
        # THE gate. The pending stays parked; zero calls.
        return False, "ATTESTATION REQUIRED", None
    samples = list(pending.get("samples") or [])
    if not samples:
        return False, "No samples are staged yet — upload " \
            "1-3 audio clips of the voice first.", None
    name = (pending.get("name") or "").strip() or "My Voice"
    state = {"state": "generating", "name": name,
             "samples": samples, "detail": "In the studio — "
             "building your cloned voice…",
             "created": time.time()}
    _set_clone_state(uid, state)
    _clear_pending(uid, wipe_samples=False)
    thread = threading.Thread(target=_run_clone_job,
                              args=(uid, name, samples),
                              daemon=True)
    thread.start()
    return True, f"Locked in — cloning \"{name}\" now.", state


def _run_clone_job(uid: str, name: str, sample_metas) -> None:
    try:
        samples = _read_samples(uid, sample_metas)
        if not samples:
            raise _ProviderError(
                "http", "staged samples could not be read")
        voice_id = _run_clone(name, samples)
        _add_voice(uid, voice_id, name)
        _set_clone_state(uid, {
            "state": "done", "name": name,
            "voice_id": voice_id,
            "detail": f"\"{name}\" is cloned and ready — "
                      f"pick it as voice:{voice_id}.",
            "created": time.time()})
        try:
            import og_notify as _notify
            _notify.record(
                uid, "voice_done",
                f"Your cloned voice is ready: \"{name}\"",
                f"\"{name}\" was cloned from your samples "
                f"(the samples themselves were deleted). "
                f"Use it as voice:{voice_id}.",
                target={"view": "system", "kind": "voice",
                        "id": voice_id})
        except Exception:
            pass
    except _ProviderError as e:
        logger.warning(f"Clone job failed ({e.kind}): {e}")
        _record_problem(uid, "clone", name, e.kind, str(e))
        _set_clone_state(uid, {
            "state": "failed", "name": name,
            "detail": _FAIL_DETAILS.get(
                e.kind, _FAIL_DETAILS["http"]),
            "created": time.time()})
    except Exception as e:
        logger.warning(f"Clone job failed: {e}")
        _record_problem(uid, "clone", name, "http",
                        f"{type(e).__name__}: {e}")
        _set_clone_state(uid, {
            "state": "failed", "name": name,
            "detail": _FAIL_DETAILS["http"],
            "created": time.time()})
    finally:
        # The samples die with the job, whatever happened —
        # they are never stored after creation resolves.
        _wipe_samples(uid)


def _record_problem(uid: str, flow: str, title: str,
                    kind: str, msg: str) -> None:
    try:
        import og_fixqueue as _fq
        _fq.record_problem(
            uid, "voice_lab", f"voice:{flow}:{title}",
            f"{flow}-take", f"A {flow} job failed",
            f"The {flow} job \"{title}\" failed ({kind}): "
            f"{msg[:200]}",
            fix={"kind": "owner_steps",
                 "summary": "The job didn't finish. Nothing "
                            "was charged.",
                 "steps": [
                     "Nothing was charged — the job died "
                     "before it finished.",
                     "Ask OG to redo it; a one-off studio "
                     "failure usually clears on a fresh try."]})
    except Exception:
        pass


def _new_sfx_job(uid: str, pending: Dict) -> Dict:
    job_id = uuid.uuid4().hex[:16]
    desc = pending.get("description", "")
    return {
        "id": job_id, "owner": _key(uid),
        "title": (desc[:60].strip() or "OG's Sound Effect"),
        "description": desc,
        "duration": int(pending.get("duration", _DEFAULT_SFX_S)),
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


def _approve_sfx(uid: str, tier: str):
    pending = _get_pending(uid)
    if pending is None or pending.get("flow") != "sfx":
        return False, "Nothing is waiting on approval — it " \
            "expired or was already answered.", None
    if _active_job(uid):
        _clear_pending(uid)
        return False, "A sound effect is already rendering " \
            "for you — let that one finish first.", None
    if _tts_blocked(uid, tier):
        _clear_pending(uid)
        return False, "You're at this week's voice cap for " \
            "your plan, and your token balance can't cover " \
            "an effect either, so this one can't render.", None
    rec = _new_sfx_job(uid, pending)
    if _tts_week_left(uid, tier) <= 0:
        rec["fund"] = "tokens"
    _clear_pending(uid)
    _store_put(rec)
    thread = threading.Thread(target=_run_sfx_job,
                              args=(rec["id"], uid), daemon=True)
    thread.start()
    return True, f"Locked in — making \"{rec['title']}\" now.", rec


def _approve(uid: str, tier: str, attested: bool = False):
    """Unified approval door (chat YES + the card). For a
    clone, `attested` is the card's checkbox / the chat
    attestation phrase — the rights gate lives in
    _approve_clone."""
    pending = _get_pending(uid)
    if pending is None:
        return False, "Nothing is waiting on approval.", None
    if pending.get("flow") == "clone":
        if attested:
            pending = dict(pending)
            pending["attested"] = True
            _set_pending(uid, pending)
        return _approve_clone(uid, attested)
    return _approve_sfx(uid, tier)


def _decline(uid: str):
    pending = _get_pending(uid)
    if pending is None:
        return False, "Nothing was waiting — nothing is pending."
    _clear_pending(uid, wipe_samples=True)
    return True, "Scrapped — nothing generated, nothing charged."


def _run_sfx_job(job_id: str, uid: str) -> None:
    rec = _store_get(job_id)
    if rec is None:
        return
    job_dir = os.path.join(STORE_DIR, job_id)
    try:
        with _gen_slots:
            os.makedirs(job_dir, exist_ok=True)
            _update(rec, state="generating",
                    detail="In the studio — rendering the "
                           "sound effect…")
            data = _run_sfx(rec.get("description", ""),
                            int(rec.get("duration",
                                        _DEFAULT_SFX_S)))
            if not _looks_like_mp3(data):
                raise _ProviderError(
                    "bad_audio",
                    "downloaded file failed the MP3 check")
            final = os.path.join(job_dir, "final.mp3")
            with open(final, "wb") as f:
                f.write(data)
            dur = float(rec.get("duration", _DEFAULT_SFX_S))
            try:
                import og_songs as _songs
                dur = _songs._track_duration(final, dur)
            except Exception:
                pass
            _settle_tts(uid, rec)
            _update(rec, state="done",
                    detail=f"\"{rec.get('title', '')}\" — a "
                           f"real sound effect, ready to play.",
                    file=final, size=len(data),
                    duration_actual=dur, charged=True)
            try:
                import og_notify as _notify
                _notify.record(
                    uid, "sfx_done",
                    f"Your sound effect is ready: "
                    f"\"{rec.get('title', '')}\"",
                    f"\"{rec.get('title', '')}\" — a real "
                    f"sound effect, ready to play.",
                    target={"view": "library", "kind": "sfx",
                            "id": job_id})
            except Exception:
                pass
    except _ProviderError as e:
        logger.warning(f"SFX job {job_id} failed ({e.kind}): {e}")
        _fail_sfx(rec, uid, e.kind, str(e))
    except Exception as e:
        logger.warning(f"SFX job {job_id} failed: {e}")
        _fail_sfx(rec, uid, "http", f"{type(e).__name__}: {e}")


def _fail_sfx(rec: Dict, uid: str, kind: str, msg: str) -> None:
    _record_problem(uid, "sfx", rec.get("title", ""), kind, msg)
    try:
        _update(rec, state="failed", error=msg[:300],
                detail=_FAIL_DETAILS.get(
                    kind, _FAIL_DETAILS["http"]))
    except Exception:
        pass


def delete_voice(uid: str, voice_id: str):
    """Delete one of the visitor's OWN cloned voices:
    provider delete first, then the local record wipe. A
    provider failure keeps the local record (retryable)
    and reports honestly. Returns (ok, message)."""
    rec = get_voice(uid, voice_id)
    if rec is None:
        return False, "That voice isn't one of yours."
    try:
        _run_delete(str(voice_id))
    except Exception as e:
        logger.warning(f"Voice delete failed: {e}")
        return False, "The studio wouldn't delete that voice " \
            "just now — it's still on your list; try again."
    data = _vstore_load(uid)
    data["voices"] = [v for v in data["voices"]
                      if v.get("voice_id") != str(voice_id)]
    _vstore_save(uid, data)
    return True, f"Deleted — \"{rec.get('name', '')}\" is gone."


def voice_results(job: Dict, message: str, uid: str) -> List[Dict]:
    kind = job.get("kind", "")
    tier = _tier_now(uid)
    if kind == "down":
        return _down_body()
    if kind == "clone_new":
        _clear_pending(uid, wipe_samples=True)
        _set_pending(uid, {
            "flow": "clone", "stage": "await_samples",
            "name": job.get("name", ""), "attested": False,
            "samples": []})
        name_bit = (f" named \"{job['name']}\""
                    if job.get("name") else "")
        body = (
            f"The visitor wants a VOICE CLONE{name_bit}. "
            f"Walk them through it, in persona, plainly: "
            f"(1) They upload 1-3 short audio samples of the "
            f"voice in the Voice Lab card (clear speech, no "
            f"music behind it works best). Samples are used "
            f"ONLY to build the clone and are DELETED the "
            f"moment the clone is made — OG never keeps the "
            f"raw samples. (2) Before anything is created "
            f"they must confirm this exact line: "
            f"\"{ATTESTATION_LINE}\" — no confirmation, no "
            f"clone, no exceptions, and they may only clone "
            f"a voice they have the rights to. (3) On their "
            f"YES with the attestation, the clone is created "
            f"for real and lands here with its voice option. "
            f"Cloning itself is free — speaking with the "
            f"voice later counts as ordinary voice use. Do "
            f"NOT say the clone exists yet.")
        return _result("VOICE LAB: CLONE START",
                       "🎙️ Voice clone — samples first", body)
    if kind == "clone_name":
        pending = _get_pending(uid) or {}
        pending = dict(pending)
        pending["name"] = job.get("name", "")
        _set_pending(uid, pending)
        return _result(
            "VOICE LAB: CLONE NAMED", "🎙️ Voice clone — named",
            f"The clone will be called \"{job.get('name', '')}\". "
            f"Tell the visitor that, in persona, briefly, and "
            f"remind them what's still needed (samples in the "
            f"Voice Lab card + the attestation) if anything is.")
    if kind == "clone_approve":
        ok, msg, state = _approve_clone(uid, bool(
            job.get("attested")))
        if not ok and msg == "ATTESTATION REQUIRED":
            return _result(
                "VOICE LAB: ATTESTATION", "🎙️ Voice clone — "
                "rights check",
                f"STOP — the clone can NOT be approved yet: "
                f"the visitor has not confirmed the rights "
                f"attestation. Ask them, in persona, to "
                f"confirm this exact line before anything is "
                f"created: \"{ATTESTATION_LINE}\". If they "
                f"reply with that confirmation, the clone "
                f"goes ahead; a bare yes is NOT enough for a "
                f"voice clone. Nothing has been created and "
                f"nothing was charged.")
        if not ok:
            return _result(
                "VOICE LAB: APPROVE-FAILED",
                "🎙️ Voice clone — not started",
                msg + " Tell the visitor exactly that, in "
                "persona — plainly.")
        return _result(
            "VOICE LAB: CLONING", "🎙️ Voice clone — in the studio",
            f"APPROVED with the rights attestation confirmed "
            f"— the studio is CLONING \"{state['name']}\" "
            f"now. Tell the visitor, in persona: locked in, "
            f"their samples are being built into a voice "
            f"right now and the raw samples get deleted the "
            f"moment it's done. The finished voice lands "
            f"RIGHT HERE with its voice:<id> option — usable "
            f"for voice replies and podcast hosting.")
    if kind == "sfx_need_desc":
        _set_pending(uid, {"flow": "sfx", "stage": "await_desc"})
        return _result(
            "SFX: NEED-DESC", "🔊 Sound effect — needs a sound",
            "The visitor wants a sound effect but didn't say "
            "what it should sound like. Ask them, in persona, "
            "in one line: what sound should it be? Whatever "
            "they answer next becomes the description.")
    if kind == "sfx_new":
        _clear_pending(uid, wipe_samples=True)
        if _active_job(uid):
            return _result(
                "SFX: BUSY", "🔊 Sound effect — one rendering",
                "A sound effect is ALREADY rendering for this "
                "visitor. Tell them, in persona: one at a time "
                "— it lands right here when it's done.")
        if _tts_blocked(uid, tier):
            return _cap_body(tier, uid)
        pending = {"flow": "sfx", "stage": "await_approval",
                   "description": job.get("description", ""),
                   "duration": int(job.get(
                       "duration", _DEFAULT_SFX_S))}
        _set_pending(uid, pending)
        fund = ""
        if _tts_week_left(uid, tier) <= 0:
            fund = (f" Their weekly voice pool is used up, so "
                    f"this one burns {_token_rate():,} tokens "
                    f"from their balance ({_token_balance(uid):,} "
                    f"on hand) when it finishes instead.")
        body = (f"The visitor wants a SOUND EFFECT. Present "
                f"this approval ask, in persona, with these "
                f"REAL numbers (do not invent others): "
                f"\"{pending['description']}\", about "
                f"{pending['duration']} seconds of generated "
                f"audio. Replying YES renders it for real — "
                f"an MP3 with a player and a download right "
                f"here — and it counts as ONE voice use "
                f"(their weekly voice pool, shared with "
                f"voice replies: {int(_tiers.weekly_cap(tier, 'tts') or 0)} "
                f"a week) ONLY when it finishes and verifies; "
                f"a failed render charges nothing. NO scraps "
                f"it. Nothing is rendered before their YES."
                f"{fund}")
        return _result("SFX: APPROVE?", "🔊 Sound effect — approval",
                       body)
    if kind == "sfx_approve":
        ok, msg, rec = _approve_sfx(uid, tier)
        if not ok:
            return _result("SFX: APPROVE-FAILED",
                           "🔊 Sound effect — not started",
                           msg + " Tell the visitor exactly "
                           "that, in persona — plainly.")
        return _result(
            "SFX: RENDERING", "🔊 Sound effect — rendering",
            f"APPROVED — the studio is RENDERING "
            f"\"{rec['title']}\" now. Tell the visitor, in "
            f"persona: locked in, it lands RIGHT HERE with "
            f"a player and a download when it's done. It "
            f"only counts against their weekly voice pool "
            f"when it FINISHES and verifies.")
    if kind == "decline":
        pending = _get_pending(uid) or {}
        flow = pending.get("flow", "")
        ok, msg = _decline(uid)
        label = "Voice clone" if flow == "clone" \
            else "Sound effect"
        icon = "🎙️" if flow == "clone" else "🔊"
        return _result(
            "VOICE LAB: DECLINED", f"{icon} {label} — scrapped",
            msg + " Tell the visitor that, in persona, "
            "briefly." + (" Any staged samples were deleted."
                          if flow == "clone" else ""))
    return _down_body()


# --- Locker save (Round 21 storage, og_songs' path) --------------


class _LockerError(Exception):
    pass


def _locker_save(uid: str, tier: str, name: str,
                 data: bytes) -> Dict:
    import og_storage as _storage
    if not _storage.storage_enabled():
        raise _LockerError("The locker is switched off right now.")
    quota = _storage.quota_bytes(tier)
    if quota <= 0:
        raise _LockerError(
            "Your plan doesn't carry a locker — step up a "
            f"plan and it's yours to keep: {_pro_url()}")
    used = _storage.usage_bytes(uid)
    if used + len(data) > quota:
        raise _LockerError(
            f"That effect is {_storage.fmt_size(len(data))} "
            f"but you only got "
            f"{_storage.fmt_size(quota - used)} left in your "
            f"locker ({_storage.fmt_size(used)} of "
            f"{_storage.fmt_size(quota)} used). Delete "
            f"something first — say \"my storage\".")
    owner = _storage.owner_of(uid)
    fid = uuid.uuid4().hex
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_",
                  os.path.basename(str(name or "sfx.mp3")))[:80]
    if not safe:
        safe = "sfx.mp3"
    key = f"{owner[:32]}/{fid}-{safe}"
    _storage.get_backend().put(key, data, "audio/mpeg")
    rec = {"id": fid, "owner": owner, "name": name,
           "size": len(data), "content_type": "audio/mpeg",
           "kind": "audio", "uploaded_at": time.time(),
           "key": key}
    _storage._insert_record(rec)
    return rec


def _slug(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-",
                  str(title or "sfx")).strip("-")
    return (slug[:60] or "sfx").lower()


# --- Routes ---------------------------------------------------------


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
                          "charged. Ask again and I'll redo it."}
    if state == "done" and (not fresh or not rec.get("file")
                            or not os.path.exists(
                                rec.get("file", ""))):
        return None
    out = {"id": rec["id"], "title": rec.get("title", ""),
           "description": rec.get("description", ""),
           "state": state, "detail": rec.get("detail", ""),
           "saved": bool(rec.get("saved", False))}
    if state == "done":
        out["sfx_url"] = f"/voice-lab/download/{rec['id']}"
        out["size"] = int(rec.get("size", 0))
        out["duration"] = float(rec.get("duration_actual", 0))
    if state == "failed":
        out["detail"] = rec.get("detail") or \
            "That render didn't make it — nothing was charged."
    return out


def _pending_view(uid: str) -> Optional[Dict]:
    pending = _get_pending(uid)
    if pending is None:
        return None
    expires = int(float(pending.get("created", time.time()))
                  + _PENDING_TTL - time.time())
    if pending.get("flow") == "clone":
        return {"flow": "clone",
                "stage": pending.get("stage", "await_samples"),
                "name": pending.get("name", ""),
                "samples": len(pending.get("samples") or []),
                "samples_max": _MAX_SAMPLES,
                "attestation": ATTESTATION_LINE,
                "attested": bool(pending.get("attested")),
                "expires_in_s": max(0, expires)}
    return {"flow": "sfx",
            "stage": pending.get("stage", "await_approval"),
            "description": pending.get("description", ""),
            "duration": int(pending.get("duration",
                                        _DEFAULT_SFX_S)),
            "expires_in_s": max(0, expires)}


def _off():
    return JSONResponse({"ok": False,
                         "error": "voice lab is off"},
                        status_code=404)


def register_voice_lab_routes(app):
    @app.get("/voice-lab/status")
    def voice_lab_status(request: Request):
        if not enabled():
            return {"enabled": False}
        uid = _route_uid(request)
        if not uid:
            return {"enabled": True, "provider": PROVIDER_NAME,
                    "pending": None, "job": None, "voices": []}
        tier = _route_tier(uid, request)
        state = _vstore_load(uid)["clone_state"]
        return {"enabled": True, "provider": PROVIDER_NAME,
                "tier": tier,
                "cap": int(_tiers.weekly_cap(tier, "tts") or 0),
                "used": _tts_used_week(uid),
                "left": _tts_week_left(uid, tier),
                "token_balance": _token_balance(uid),
                "attestation": ATTESTATION_LINE,
                "voices": list_voices(uid),
                "clone_state": state or None,
                "pending": _pending_view(uid),
                "job": _job_view(
                    _store_latest_for_owner(_key(uid)))}

    @app.post("/voice-lab/samples")
    async def voice_lab_samples(request: Request):
        if not enabled():
            return _off()
        uid = _route_uid(request)
        if not uid:
            return {"ok": False, "error": "no visitor identity"}
        tier = _route_tier(uid, request)
        try:
            form = await request.form()
        except Exception:
            form = {}
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return {"ok": False,
                    "error": "No audio sample arrived."}
        limit = _sample_max_bytes(tier)
        raw = await upload.read(limit + 1)
        name_field = str(form.get("name") or "").strip()[:40]
        try:
            meta = stage_sample(
                uid, getattr(upload, "filename", "") or "sample",
                getattr(upload, "content_type", "") or "",
                raw, tier)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        if name_field:
            pending = _get_pending(uid)
            if pending and pending.get("flow") == "clone" \
                    and not pending.get("name"):
                pending = dict(pending)
                pending["name"] = name_field
                _set_pending(uid, pending)
        return {"ok": True, "sample": meta,
                "pending": _pending_view(uid)}

    @app.post("/voice-lab/approve")
    async def voice_lab_approve(request: Request):
        if not enabled():
            return _off()
        uid = _route_uid(request)
        if not uid:
            return {"ok": False, "error": "no visitor identity"}
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        attested = body.get("attestation") is True
        ok, msg, rec = _approve(uid, _route_tier(uid, request),
                                attested=attested)
        title = ""
        if rec:
            title = rec.get("title") or rec.get("name", "")
        return {"ok": ok, "title": title,
                "error": "" if ok else msg}

    @app.post("/voice-lab/decline")
    def voice_lab_decline(request: Request):
        if not enabled():
            return _off()
        uid = _route_uid(request)
        if not uid:
            return {"ok": False, "error": "no visitor identity"}
        ok, msg = _decline(uid)
        return {"ok": ok, "error": "" if ok else msg}

    @app.post("/voice-lab/delete")
    async def voice_lab_delete(request: Request):
        if not enabled():
            return _off()
        uid = _route_uid(request)
        if not uid:
            return {"ok": False, "error": "no visitor identity"}
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        voice_id = str(body.get("voice_id") or
                       body.get("voice") or "").strip()
        if voice_id.startswith("voice:"):
            voice_id = voice_id[len("voice:"):]
        if not voice_id:
            return {"ok": False, "error": "no voice_id"}
        ok, msg = delete_voice(uid, voice_id)
        return {"ok": ok, "error": "" if ok else msg,
                "voices": list_voices(uid) if ok else None}

    @app.get("/voice-lab/download/{job_id}")
    def voice_lab_download(job_id: str, request: Request):
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
            filename=_slug(rec.get("title", "sfx")) + ".mp3")

    @app.post("/voice-lab/save/{job_id}")
    def voice_lab_save(job_id: str, request: Request):
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
                _slug(rec.get("title", "sfx")) + ".mp3", data)
        except _LockerError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(f"SFX locker save failed: {e}")
            return {"ok": False,
                    "error": "The locker wouldn't take it just "
                             "now — the download still works."}
        rec["saved"] = True
        _store_put(rec)
        return {"ok": True, "name": saved.get("name", "")}


# --- The seam ---------------------------------------------------------

_slot = {"job": None, "message": ""}


def install_voice_lab_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search so clone/sfx asks,
    the approval flow and the attestation step ride the
    established seam. Persona files never touched."""
    if getattr(agent_instance, "_og_voice_lab_installed", False):
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
            job = _claim_voice(str(message), uid) if uid else None
            if job:
                _slot["job"] = job
                _slot["message"] = str(message)
                if isinstance(intent, dict):
                    intent["needs_code_generation"] = False
                    if not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
        except Exception as e:
            logger.warning(f"Voice lab trigger check failed: {e}")
            _slot["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _slot.get("job")
        message = _slot.get("message", "")
        _slot["job"] = None
        _slot["message"] = ""
        if job:
            try:
                results = voice_results(job, message, get_uid())
            except Exception as e:
                logger.warning(f"Voice lab job failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_voice_lab_installed = True
