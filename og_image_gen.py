"""
OG image generation helpers (Round 5).

The self-contained half of the /image feature: turning a chat message
into a picture prompt, and the OpenAI Images API call itself
(gpt-image-1 first, dall-e-3 fallback; OG_IMAGE_MODEL overrides the
first choice). The endpoint, the per-visitor daily caps, and the
memory note live in app.py with the other identity/money layers.
"""

import logging
import os
import re

logger = logging.getLogger(__name__)

IMAGE_MODEL = os.getenv("OG_IMAGE_MODEL", "gpt-image-1")
IMAGE_MODEL_FALLBACK = "dall-e-3"
IMAGE_PROMPT_MAX_CHARS = 1000
IMAGE_SIZE = "1024x1024"
# The first model that actually worked this process — tried first next
# time, so a key without gpt-image-1 access pays one failed call total,
# not one per image.
_working_model = {"model": ""}

IMAGE_CAPTIONS = (
    "Say less. Cooked it up fresh out the OG lab. 🎨",
    "There it go — straight off the drawing board. 🎨",
    "Done and done. Frame that one, it's a masterpiece. 🎨",
)
IMAGE_DOWN_LINE = (
    "Yo, my drawing hand's outta commission right now — the image lab "
    "is down. Ask me anything else though, I got you."
)
_TRIGGER_LEADINS = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"^\s*(please\s+)?(draw|paint|sketch|illustrate|render|generate|create|make|design|show|give)\s+(me\s+)?(an?\s+|the\s+)?(image|picture|pic|photo|drawing|painting|illustration|logo|artwork|poster|wallpaper|sticker|meme)?\s*(of\s+|about\s+|for\s+)?",
    r"^\s*(draw|paint|sketch|illustrate)\s+(me\s+)?",
    r"^\s*(an?\s+)?(image|picture|drawing|painting|illustration|logo)\s+of\s+",
))


def clean_image_prompt(raw: str) -> str:
    """Turn a chat message ('draw me a dragon') into a picture prompt
    ('a dragon') by stripping the request lead-in; falls back to the raw
    message when stripping would leave almost nothing."""
    text = " ".join(str(raw or "").split())[:IMAGE_PROMPT_MAX_CHARS]
    cleaned = text
    for pattern in _TRIGGER_LEADINS:
        cleaned = pattern.sub("", cleaned, count=1)
    cleaned = re.sub(r"\s+(for me|please)\s*[.!]*\s*$", "", cleaned,
                     flags=re.IGNORECASE).strip(" .,!")
    if len(cleaned) < 3:
        return text
    return cleaned


def _model_chain():
    chain = []
    for model in (_working_model["model"], IMAGE_MODEL,
                  IMAGE_MODEL_FALLBACK):
        if model and model not in chain:
            chain.append(model)
    return chain


async def openai_generate_image(api_key: str, prompt: str,
                                size: str = None):
    """Generate one image; returns (image_src, model_used).

    image_src is a data URI when the API hands back base64 (the normal
    case for both models), else the temporary hosted URL converted to a
    data URI when it can be downloaded. Raises on total failure so the
    endpoint can answer with the graceful down line instead.

    `size` (Generator 7b): an explicit API size such as "1536x1024"
    (landscape) or "1024x1536" (portrait); None keeps the historical
    IMAGE_SIZE default, so every existing caller is unchanged.
    """
    import httpx
    last_error = None
    for model in _model_chain():
        body = {"model": model, "prompt": prompt,
                "size": size or IMAGE_SIZE, "n": 1}
        if model == IMAGE_MODEL_FALLBACK:
            body["response_format"] = "b64_json"
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(
                    "https://api.openai.com/v1/images/generations",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=body,
                )
        except httpx.TimeoutException as e:
            # A slow generation is not a dead model — don't pay a second
            # full wait on the fallback; surface the failure instead.
            raise RuntimeError(f"image generation timed out ({model})") from e
        except Exception as e:
            last_error = e
            logger.warning(f"Image call failed ({model}): {e}")
            continue
        if r.status_code != 200:
            last_error = RuntimeError(f"{model} answered {r.status_code}")
            logger.warning(
                f"Image API status ({model}): {r.status_code} {r.text[:200]}")
            continue
        try:
            item = (r.json().get("data") or [])[0]
        except Exception as e:
            last_error = e
            continue
        b64 = item.get("b64_json")
        if b64:
            _working_model["model"] = model
            return f"data:image/png;base64,{b64}", model
        url = item.get("url")
        if url:
            _working_model["model"] = model
            # Convert to a data URI so the picture survives the hosted
            # URL's short expiry; fall back to the URL if that fails.
            try:
                import base64
                async with httpx.AsyncClient(timeout=60) as client:
                    ir = await client.get(url)
                if ir.status_code == 200 and ir.content:
                    mime = ir.headers.get(
                        "content-type", "image/png").split(";")[0]
                    return (f"data:{mime};base64,"
                            + base64.b64encode(ir.content).decode()), model
            except Exception as e:
                logger.warning(f"Image download failed: {e}")
            return url, model
        last_error = RuntimeError(f"{model} returned no image data")
    raise RuntimeError(f"image generation failed: {last_error}")


# --- Ticket P2: generated images save themselves ---------------------------
# A picture OG generates in chat used to live only in the chat
# bubble (and a text note in the thread): it reached the Library
# only if the user saved it to the locker by hand. Now the
# /image endpoint hands every successful generation here, and
# the bytes go into the visitor's locker via
# og_storage.save_bytes — kind "image" (sniffed from the magic
# bytes), counted against the tier quota — so the Library's
# existing locker section surfaces it as an Images item, owner
# -checked on every open/download like everything else OG makes.
#
# Truthfulness rules (the house patterns, same as Ticket P):
#   * the quota check binds the COMBINED figure the Library
#     line shows — locker bytes + artifact bytes — never a
#     smaller private number;
#   * a save that did not happen is never claimed: every
#     non-save outcome carries a plain line the endpoint
#     appends to the caption, and the picture still ships in
#     chat regardless (no silent loss, no fake save);
#   * the saver NEVER raises — a locker glitch must not cost
#     the visitor the picture they already paid a generation
#     for; failures land in the log and in the outcome dict.
#
# Kill switch: OG_IMAGE_AUTOSAVE_ENABLED (the og_artifacts
# house pattern) — enabled unless the env value is an explicit
# off; while off, the endpoint behaves exactly as pre-P2.

_SAVE_NOTE_SAVED = (
    "🗄️ Saved to your locker too — it's in your Library "
    "under Images.")
_SAVE_NOTE_FULL = (
    "⚠️ Couldn't save that one to your locker — it's full "
    "({used} of {quota} used). The picture's right here in "
    "chat; say \"my storage\" and I'll help clear space.")
_SAVE_NOTE_FREE = (
    "Heads up: the free plan ain't got a locker, so this "
    "one lives in chat only — storage starts at Standard.")
_SAVE_NOTE_DARK = (
    "Heads up: file storage isn't switched on yet, so this "
    "one lives in chat only for now.")
_SAVE_NOTE_MISSED = (
    "Couldn't park a copy of that one in your locker — the "
    "picture's right here in chat, and the download link "
    "under it grabs the file.")

_MIME_EXT = {"image/png": ".png", "image/jpeg": ".jpg",
             "image/webp": ".webp"}


def autosave_enabled() -> bool:
    """OG_IMAGE_AUTOSAVE_ENABLED is the kill switch: enabled
    unless the env value is an explicit off."""
    return os.environ.get(
        "OG_IMAGE_AUTOSAVE_ENABLED", "true").strip().lower() \
        not in ("0", "false", "no", "off")


def _save_name(prompt: str, mime: str) -> str:
    """The locker display name: a slug of the picture prompt
    (what the Library title shows) + the format extension."""
    words = re.findall(r"[a-z0-9]+", str(prompt or "").lower())
    slug = "-".join(words[:6])[:48].strip("-") or "og-image"
    return slug + _MIME_EXT.get(mime, ".png")


def _bytes_from_src(image_src: str):
    """(bytes, mime) from a generated image src, or
    (None, "") when no bytes can be had. The normal case is
    a data URI (both models' base64 path, already converted
    by openai_generate_image). The hosted-URL fallback gets
    ONE download attempt — the generator already tried once,
    so a failure here means the copy truly can't be parked."""
    src = str(image_src or "")
    if src.startswith("data:"):
        head, _, payload = src.partition(",")
        mime = head[5:].split(";")[0].strip() or "image/png"
        try:
            import base64
            return base64.b64decode(payload), mime
        except Exception as e:
            logger.warning(f"Autosave decode failed: {e}")
            return None, ""
    if src.startswith(("http://", "https://")):
        import urllib.request
        try:
            req = urllib.request.Request(
                src, headers={"User-Agent": "OG-AI/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
                mime = (r.headers.get_content_type()
                        or "image/png")
            if data:
                return data, mime
        except Exception as e:
            logger.warning(f"Autosave URL fetch failed: {e}")
    return None, ""


def autosave_generated_image(uid: str, tier: str,
                             image_src: str, prompt: str) -> dict:
    """Park a generated image in the visitor's locker.

    Returns {"status", "note", "record"}: status is one of
    saved / full / free / dark / missed / off; note is the
    plain line the endpoint appends to the caption (the
    _SAVE_NOTE_* line for the outcome; "" only while the
    kill switch is off); record is the og_storage record
    when saved, else None. NEVER raises.
    """
    outcome = {"status": "missed", "note": _SAVE_NOTE_MISSED,
               "record": None}
    try:
        if not autosave_enabled():
            return {"status": "off", "note": "", "record": None}
        if not uid:
            return outcome
        import og_storage
        if not og_storage.storage_enabled():
            outcome.update(status="dark", note=_SAVE_NOTE_DARK)
            return outcome
        quota = og_storage.quota_bytes(tier)
        if quota <= 0:
            outcome.update(status="free", note=_SAVE_NOTE_FREE)
            return outcome
        data, mime = _bytes_from_src(image_src)
        if not data:
            return outcome
        used = og_storage.usage_bytes(uid)
        try:
            import og_artifacts
            used += og_artifacts.usage_bytes(uid)
        except Exception:
            pass
        if used + len(data) > quota:
            outcome.update(
                status="full",
                note=_SAVE_NOTE_FULL.format(
                    used=og_storage.fmt_size(used),
                    quota=og_storage.fmt_size(quota)))
            return outcome
        try:
            rec = og_storage.save_bytes(
                uid, tier, _save_name(prompt, mime), data, mime)
        except og_storage.QuotaExceeded:
            outcome.update(
                status="full",
                note=_SAVE_NOTE_FULL.format(
                    used=og_storage.fmt_size(
                        og_storage.usage_bytes(uid)),
                    quota=og_storage.fmt_size(quota)))
            return outcome
        outcome.update(status="saved", note=_SAVE_NOTE_SAVED,
                       record=rec)
        logger.info(
            f"Generated image auto-saved to locker "
            f"(id={rec.get('id')}, bytes={len(data)})")
        return outcome
    except Exception as e:
        logger.warning(f"Image autosave failed: {e}")
        return outcome
