"""
OG avatar motion assets (Composer+Panel patch, spec section 4).

OG's avatar works like Maya's: ONE character (the Round 24 OG —
hoodie, chain, scars; the anime set was rejected) with short
looping video variants per state, swapped by what OG is doing:

    idle      — breathing / blinking / talking-idle loop
    working   — at the computer, composing / running tools
    hood      — under a car hood (making_something variant)
    drinking  — actively drinking out of the cup (milestone beat)

The raw generated clips are 3.4–4.4 MB at 960px — far too big to
ship. They were compressed to ~450px, ~5–6s, 15fps, no audio
(42–80 KB each) and ride as base64 in og_avatar_data_<state>.py
modules, because the repo push path stores text content only
(proven 2026-10-09: a binary probe landed as its base64 text).
This module decodes them once and serves the bytes at
GET /avatar/<state>.mp4 with long cache headers + an ETag.
The page falls back to the still OG_AVATAR image whenever a
video can't play (or the visitor prefers reduced motion).
"""

import base64
import hashlib
import logging

logger = logging.getLogger(__name__)

_STATES = ("idle", "working", "hood", "drinking")
_cache = {}


def _load(state: str) -> bytes:
    if state in _cache:
        return _cache[state]
    import importlib
    mod = importlib.import_module(f"og_avatar_data_{state}")
    raw = base64.b64decode(mod.B64)
    _cache[state] = raw
    return raw


def avatar_bytes(state: str):
    """Decoded MP4 bytes for a state, or None for an unknown one."""
    if state not in _STATES:
        return None
    try:
        return _load(state)
    except Exception:
        logger.warning("avatar asset load failed: %s", state,
                       exc_info=True)
        return None


def poster_bytes():
    """The still OG webp (the page's poster/fallback image)."""
    try:
        import og_avatar_data_poster as _poster
        return base64.b64decode(_poster.B64)
    except Exception:
        logger.warning("avatar poster load failed", exc_info=True)
        return None


def register_avatar_routes(app):
    from fastapi import Request
    from fastapi.responses import JSONResponse, Response

    @app.get("/avatar/status")
    async def avatar_status():
        ok = [s for s in _STATES if avatar_bytes(s)]
        return JSONResponse(content={"states": ok,
                                     "poster": bool(poster_bytes())})

    @app.get("/avatar/poster.webp")
    async def avatar_poster(request: Request):
        raw = poster_bytes()
        if raw is None:
            return JSONResponse(content={"detail": "no poster"},
                                status_code=404)
        etag = '"' + hashlib.sha256(raw).hexdigest()[:24] + '"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(
            content=raw, media_type="image/webp",
            headers={"Cache-Control": "public, max-age=604800",
                     "ETag": etag})

    @app.get("/avatar/{state}.mp4")
    async def avatar_clip(state: str, request: Request):
        raw = avatar_bytes(state)
        if raw is None:
            return JSONResponse(content={"detail": "no such avatar"},
                                status_code=404)
        etag = '"' + hashlib.sha256(raw).hexdigest()[:24] + '"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        return Response(
            content=raw, media_type="video/mp4",
            headers={"Cache-Control": "public, max-age=604800",
                     "ETag": etag})
