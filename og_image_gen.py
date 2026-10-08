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


async def openai_generate_image(api_key: str, prompt: str):
    """Generate one image; returns (image_src, model_used).

    image_src is a data URI when the API hands back base64 (the normal
    case for both models), else the temporary hosted URL converted to a
    data URI when it can be downloaded. Raises on total failure so the
    endpoint can answer with the graceful down line instead.
    """
    import httpx
    last_error = None
    for model in _model_chain():
        body = {"model": model, "prompt": prompt, "size": IMAGE_SIZE, "n": 1}
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
