"""
OG voice-note transcription (Round 8).

The self-contained half of the voice-note feature: POST /transcribe
takes one recorded audio clip (webm / ogg / m4a / mp3 / wav, at most
10 MB), turns it into text with the OpenAI audio API on the EXISTING
OPENAI_API_KEY, and hands the transcript back to the page — which then
sends it through the normal /chat path itself, so a voice note is
metered, remembered and answered exactly like typed text. This module
never touches the chat meter and never double-charges.

Model: gpt-4o-mini-transcribe is preferred when the key can use it,
whisper-1 is the fallback. The first model that answers is probed once
and cached for the life of the process; OG_TRANSCRIBE_MODEL pins one
model explicitly (no fallback in that case).

Caps: per visitor per UTC day, by tier, from the Round 7 table —
og_tiers.cap(tier, "transcribe"): free 3, standard 15, pro 50,
blue 150, blackout 500; env OG_CAP_<TIER>_TRANSCRIBE overrides any of
them. Only successful transcriptions consume quota.

Privacy: audio is processed in memory and discarded — never written
to disk, never logged. Transcript contents are never logged either;
only byte counts and the model that answered.
"""

import logging
import os
import uuid
from datetime import datetime, timezone

import og_tiers as _og_tiers

logger = logging.getLogger(__name__)


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


MAX_AUDIO_BYTES = int(os.getenv("OG_TRANSCRIBE_MAX_BYTES",
                                str(10 * 1024 * 1024)))
_READ_CAP = MAX_AUDIO_BYTES + 1

BAD_TYPE_LINE = (
    "Yo, that audio ain't a format my ears take — I hear webm, ogg, "
    "m4a, mp3 or wav. Run it back in one of those."
)
TOO_BIG_LINE = (
    "Yo, that voice note's too heavy — I max out at 10 MB. "
    "Chop it down and slide it again."
)
EMPTY_LINE = (
    "Yo, I listened twice and heard nothin' but air. "
    "Say that again, closer to the mic."
)
DOWN_LINE = (
    "Yo, my ears are down right now — the listening lab's trippin'. "
    "Type it out for me, or try the note again in a bit."
)

# ---------------------------------------------------------------------------
# Model choice (probe once, cache)
# ---------------------------------------------------------------------------

_ENV_MODEL = os.getenv("OG_TRANSCRIBE_MODEL", "").strip()
_MODEL_PREFERENCE = ([_ENV_MODEL] if _ENV_MODEL
                     else ["gpt-4o-mini-transcribe", "whisper-1"])
_model_state = {"model": None}


class _ModelRejected(Exception):
    """The transcription model name itself was rejected (unknown /
    not enabled on this key) — the next preference may still work."""


async def _call_transcribe(api_key, data, ext, mime, model):
    """One transcription attempt. Returns the transcript ('' when the
    audio held no speech) or None when the service is down/errored.
    Raises _ModelRejected when the model name is the problem."""
    import httpx
    files = {"file": (f"voice-note.{ext}", data, mime)}
    form = {"model": model, "response_format": "json"}
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files=files, data=form)
    if r.status_code == 200:
        try:
            return (r.json().get("text") or "").strip()
        except Exception:
            return ""
    if r.status_code in (400, 404):
        try:
            err = str(r.json().get("error", {})).lower()
        except Exception:
            err = ""
        if "model" in err:
            raise _ModelRejected(model)
    logger.warning(f"Transcription upstream status: {r.status_code}")
    return None


async def transcribe(api_key, data, ext, mime):
    """Transcript text ('' = no speech heard) or None when every
    route is down. Tries the cached model first, then the preference
    order; the first model that answers is cached."""
    order = []
    if _model_state["model"]:
        order.append(_model_state["model"])
    for m in _MODEL_PREFERENCE:
        if m not in order:
            order.append(m)
    for model in order:
        try:
            text = await _call_transcribe(api_key, data, ext, mime, model)
        except _ModelRejected:
            if _model_state["model"] == model:
                _model_state["model"] = None
            continue
        if text is None:
            return None
        _model_state["model"] = model
        return text
    return None


# ---------------------------------------------------------------------------
# Audio sniffing
# ---------------------------------------------------------------------------

def _sniff_audio(filename, content_type, data):
    """(ext, mime) from magic bytes first (a renamed clip can't lie),
    then the declared content type, then the filename extension.
    None when nothing knows it."""
    if data[:4] == b"\x1a\x45\xdf\xa3":            # EBML — webm
        return "webm", "audio/webm"
    if data[:4] == b"OggS":
        return "ogg", "audio/ogg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav", "audio/wav"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3",
                                          b"\xff\xf2"):
        return "mp3", "audio/mpeg"
    if data[4:8] == b"ftyp":                       # mp4 family — m4a/mp4
        return "m4a", "audio/mp4"
    ct = (content_type or "").split(";")[0].strip().lower()
    by_type = {
        "audio/webm": ("webm", "audio/webm"),
        "audio/ogg": ("ogg", "audio/ogg"),
        "audio/wav": ("wav", "audio/wav"),
        "audio/x-wav": ("wav", "audio/wav"),
        "audio/mpeg": ("mp3", "audio/mpeg"),
        "audio/mp3": ("mp3", "audio/mpeg"),
        "audio/mp4": ("m4a", "audio/mp4"),
        "audio/m4a": ("m4a", "audio/mp4"),
        "audio/x-m4a": ("m4a", "audio/mp4"),
        "video/mp4": ("m4a", "audio/mp4"),
    }
    if ct in by_type:
        return by_type[ct]
    ext = os.path.splitext(filename or "")[1].lower().lstrip(".")
    by_ext = {
        "webm": ("webm", "audio/webm"), "ogg": ("ogg", "audio/ogg"),
        "opus": ("ogg", "audio/ogg"), "wav": ("wav", "audio/wav"),
        "mp3": ("mp3", "audio/mpeg"), "m4a": ("m4a", "audio/mp4"),
        "mp4": ("m4a", "audio/mp4"),
    }
    return by_ext.get(ext)


# ---------------------------------------------------------------------------
# App binding: caps + the /transcribe route ---------------------------------
# app.py binds its stores/helpers once via bind_app() and calls
# register_voice_routes(app) — the same pattern og_file_read.py uses,
# so app.py stays at binding lines only.

_deps = {}


def bind_app(deps):
    _deps.update(deps)


def _visitor_tier(uid, req):
    """Tier via the app's resolver (bound as deps["tier_of"]); falls
    back to the old entitled boolean when unbound."""
    fn = _deps.get("tier_of")
    if fn is not None:
        return fn(uid, req)
    return "standard" if _deps["is_entitled"](uid, req) else "free"


def _transcribe_cap(tier):
    """Daily transcription cap for a tier (Round 7 table in og_tiers;
    OG_CAP_<TIER>_TRANSCRIBE overrides)."""
    return _og_tiers.cap(tier, "transcribe")


def _voice_used_today(uid):
    d = _deps
    with d["usage_lock"]:
        store = d["load_usage"]()
        rec = store.get("voice:" + uid) or {}
        if rec.get("date") != _today():
            return 0
        return int(rec.get("count", 0))


def transcribe_left(uid, tier):
    return max(0, _transcribe_cap(tier) - _voice_used_today(uid))


def _consume_voice(uid):
    d = _deps
    with d["usage_lock"]:
        store = d["load_usage"]()
        key = "voice:" + uid
        rec = store.get(key) or {}
        if rec.get("date") != _today():
            rec = {"date": _today(), "count": 0}
        rec["count"] = int(rec.get("count", 0)) + 1
        store[key] = rec
        d["save_usage"](store)


def register_voice_routes(app):
    from fastapi import Request as _Req  # noqa: F401  (annotation only)
    from fastapi.responses import JSONResponse
    d = _deps

    @app.post("/transcribe")
    async def transcribe_note(raw_request: _Req):
        """Turn ONE voice note into text for THIS visitor —
        POST /transcribe (multipart form field "file"). The page
        sends the transcript on through /chat itself, so chat memory
        and metering behave exactly like typed text. Every expected
        failure answers 200 with an in-persona line — never a 500."""
        d["get_agent"]()
        cookie_uid = raw_request.cookies.get("ogai_uid")
        uid = cookie_uid or uuid.uuid4().hex

        def _respond(payload, status_code=200):
            resp = JSONResponse(content=payload, status_code=status_code)
            if not cookie_uid:
                resp.set_cookie(
                    "ogai_uid", uid, max_age=d["cookie_max_age"],
                    httponly=True, samesite="lax", path="/")
            return resp

        tier = _visitor_tier(uid, raw_request)
        left = transcribe_left(uid, tier)
        if left <= 0:
            cap = _transcribe_cap(tier)
            if tier != "free":
                line = (f"Yo, you burned through your {cap} voice notes "
                        f"for today — even the top shelf gotta pace it. "
                        f"Slide back tomorrow.")
                return _respond({"ok": False, "capped": True,
                                 "transcribe_left": 0, "response": line})
            line = (f"Yo, that's your {cap} free voice notes for today. "
                    f"Go premium for up to 500 a day: {d['pro_url']} — "
                    f"or slide back tomorrow.")
            return _respond({"ok": False, "capped": True,
                             "transcribe_left": 0,
                             "upgrade_url": d["pro_url"], "response": line})
        try:
            form = await raw_request.form()
        except Exception:
            form = {}
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return _respond({"ok": False, "response": BAD_TYPE_LINE})
        raw = await upload.read(_READ_CAP)
        if len(raw) > MAX_AUDIO_BYTES:
            return _respond({"ok": False, "response": TOO_BIG_LINE})
        if not raw:
            return _respond({"ok": False, "response": EMPTY_LINE})
        sniffed = _sniff_audio(getattr(upload, "filename", "") or "",
                               getattr(upload, "content_type", "") or "",
                               raw)
        if not sniffed:
            return _respond({"ok": False, "response": BAD_TYPE_LINE})
        ext, mime = sniffed
        api_key = d["get_api_key"]()
        if not api_key:
            return _respond({"ok": False, "response": DOWN_LINE,
                             "transcribe_left": left})
        try:
            text = await transcribe(api_key, raw, ext, mime)
        except Exception:
            logger.warning("transcription failed", exc_info=True)
            text = None
        # `raw` goes out of scope here — audio is never stored.
        if text is None:
            return _respond({"ok": False, "response": DOWN_LINE,
                             "transcribe_left": left})
        if not text:
            return _respond({"ok": False, "response": EMPTY_LINE,
                             "transcribe_left": left})
        _consume_voice(uid)
        logger.info(f"Voice note transcribed for visitor "
                    f"(bytes={len(raw)}, model={_model_state['model']})")
        return _respond({"ok": True, "transcript": text,
                         "transcribe_left": transcribe_left(uid, tier)})
