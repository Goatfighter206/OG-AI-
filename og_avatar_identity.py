"""Ticket E6 — avatar identity: the selfie avatar + OG's name.

Brent's words (2026-10-21): "I want to make it to were you can
take a picture of your self and it will create an avatar for
og that look like the user and still moves and act like og.
Nothing changes but the avatar they can also change the name
to by clicking the avatar and then clicking the pen icon
right next to the avatar"

WHAT THIS MODULE IS. The whole backend of that ticket, in
one new module (app.py is at its size ceiling; registration
is the standard bind_app + register block there):

  GET  /avatar-identity/me        the caller's identity
                                  record: {name, has_avatar,
                                  art_url, mouth}
  POST /avatar-identity/rename    {name} -> the name OG
                                  answers to for this user
  POST /avatar-identity/generate  multipart selfie -> an
                                  OG-style avatar of the
                                  user, generated + stored
  POST /avatar-identity/revert    drop the personalized
                                  art (the default OG face
                                  comes back; the name stays)
  GET  /avatar-identity/art       the caller's own avatar
                                  art (account data — NOT
                                  public media like the
                                  brand poster)

IDENTITY. Every route requires a live session
(og_accounts.session_account_uid on the ogai_session
cookie) — E6 is a signed-in feature ("the signed-in user
takes a selfie"). The record rides the accounts durable
store (og_accounts._get/_put, the Round 18 key -> JSON
pattern) under ONE key per account:

    avatarid:<account uid> ->
      {"name": str | None,            # None = default "OG"
       "art_b64": str | None,         # the normalized art
       "art_fmt": "webp" | "png",
       "art_rev": str,                # sha256[:16] of the art
       "mouth": {"mx": 0.49, "my": 0.612},
       "updated": iso8601}

One record, one door (No Junk Drawer): the selfie itself
is NEVER stored — it goes to the image lab once and is
gone; only the drawing is kept. Revert deletes the art
from the same record; nothing else accumulates.

GENERATION. Rides the live image machinery's account: the
OPENAI_API_KEY the /image endpoint uses, the same weekly
image cap (og_tiers.weekly_cap(tier, "images") over the
same usage-store key "image:<uid>"), and the same
token-pack fallback /image offers when the pool is dry
(og_tokenpacks, RATES["image"]). The call itself is the
one piece /image does not have: an IDENTITY edit —
POST /v1/images/edits with the selfie as the input image
and a style+composition prompt (below), model
og_image_gen.IMAGE_MODEL (gpt-image-1).

NO FALLBACK MODEL, ON PURPOSE. og_image_gen's chain falls
back to dall-e-3 for plain text-to-image; dall-e-3 cannot
take an input image at all, so a "fallback" here would
draw a stranger and call it the user. If the edit call
fails, the route answers with the honest down line and
saves nothing.

THE COMPOSITION CONTRACT (why the mouth still lands).
The default poster is a SQUARE bust (256x256 webp): face
centred at x ~= 0.49, mouth line at y ~= 0.612 of the
artwork — the fractions og_mouth.js's per-face geometry
remaps. The generation prompt pins that same composition
(square frame, bust filling it, chin about three-fifths
down) and _normalize_art enforces the square server-side
(centre-crop, NEVER a stretch; downscale to 512; WEBP).
A personalized face therefore enters the presentation
under the same framing the E4/E5 geometry was calibrated
on, and og_mouth.js is untouched. The record still
carries a per-user "mouth" seam (the canonical fractions)
so a future calibration has a home without a migration.

METERING PARITY NOTE. /image meters under the visitor
uid; this module meters under the resolved account uid
(session_uid_or semantics: the entitlement follows the
account). Same pool key shape, same weekly cap.
"""

import asyncio
import base64
import hashlib
import io
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DEFAULT_NAME = "OG"
NAME_MAX = 24
SELFIE_MAX_BYTES = 8 * 1024 * 1024      # the house upload cap
ART_SIZE = 512                          # stored art edge (square)
# The canonical artwork fractions og_mouth.js remaps per
# face (its header: mouth line centres at (0.49, 0.612)
# of the artwork). Stored per user as the calibration seam.
CANONICAL_MOUTH = {"mx": 0.49, "my": 0.612}

STYLE_PROMPT = (
    "Transform the person in this photo into a stylized "
    "digital portrait in the OG house style: a confident "
    "character bust portrait, black hoodie, thick gold "
    "chain, dark moody background with a warm golden glow, "
    "rich contrast, painterly-realistic finish. Keep the "
    "SAME person — same face, same features, same "
    "hairstyle — clearly recognizable as them. Composition: "
    "square frame filled by a head-and-torso bust, face "
    "centered horizontally, head large in the upper half, "
    "chin about three-fifths down the frame, hoodie torso "
    "running to the bottom edge, looking at the camera."
)

DOWN_LINE = (
    "Yo, the avatar lab's down right now — couldn't cook "
    "your new face. Try again in a bit, I got you."
)
NO_KEY_LINE = (
    "Yo, the drawing hand ain't wired up on this server — "
    "can't make your avatar till it is."
)

_deps = {}


def bind_app(deps):
    _deps.update(deps)


# --- The record -------------------------------------------------


def _key(uid: str) -> str:
    return "avatarid:" + uid


def _load(uid: str) -> dict:
    import og_accounts
    rec = og_accounts._get(_key(uid))
    if not isinstance(rec, dict):
        rec = {}
    rec.setdefault("name", None)
    rec.setdefault("art_b64", None)
    rec.setdefault("art_fmt", "webp")
    rec.setdefault("art_rev", "")
    mouth = rec.get("mouth")
    if not isinstance(mouth, dict):
        rec["mouth"] = dict(CANONICAL_MOUTH)
    return rec


def _save(uid: str, rec: dict):
    import og_accounts
    rec["updated"] = datetime.now(timezone.utc).isoformat()
    og_accounts._put(_key(uid), rec)


def _public(rec: dict) -> dict:
    has = bool(rec.get("art_b64"))
    return {
        "ok": True,
        "signed_in": True,
        "name": rec.get("name") or DEFAULT_NAME,
        "has_avatar": has,
        "art_url": ("/avatar-identity/art?v=" + rec["art_rev"]
                    if has and rec.get("art_rev") else
                    ("/avatar-identity/art" if has else None)),
        "mouth": rec.get("mouth") or dict(CANONICAL_MOUTH),
    }


# --- Identity ---------------------------------------------------


def _account_uid(request):
    """The signed-in account uid behind the request, or
    None. E6 is account-only: a visitor id is not enough."""
    import og_accounts
    try:
        return og_accounts.session_account_uid(
            request.cookies.get(og_accounts.SESSION_COOKIE))
    except Exception:
        return None


# --- Metering (mirrors app.py /image, R55 weekly pool) ----------


def _tier_of(request, uid):
    fn = _deps.get("tier_of")
    if fn is not None:
        try:
            return fn(uid, request)
        except Exception:
            pass
    import og_tiers
    return og_tiers.tier_of(request.cookies, uid or "")


def _week_images_used(uid: str) -> int:
    import og_tiers
    load = _deps.get("load_usage")
    if load is None:
        return 0
    store = load()
    return og_tiers.week_used(store.get(f"image:{uid}"))


def _consume_image(uid: str):
    """app.py _consume_image's twin, through the bound
    usage deps (same carry_days / note_day bookkeeping)."""
    import og_tiers
    load, save, lock = (_deps.get("load_usage"),
                        _deps.get("save_usage"),
                        _deps.get("usage_lock"))
    if load is None or save is None or lock is None:
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"image:{uid}"
    with lock:
        store = load()
        entry = store.get(key)
        if not isinstance(entry, dict) or entry.get("date") != today:
            old, entry = entry, {"date": today, "count": 0}
            og_tiers.carry_days(old, entry)
        entry["count"] = int(entry.get("count", 0)) + 1
        og_tiers.note_day(entry, today, entry["count"])
        store[key] = entry
        save(store)


# --- The image lab ------------------------------------------------


def _prep_selfie(raw: bytes):
    """Validate + prep the selfie for the edit call:
    a real JPEG/PNG/WEBP, re-encoded as JPEG with the
    longest side at most 1024 (never upscaled). Returns
    (jpeg_bytes, error_line)."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        if img.format not in ("JPEG", "PNG", "WEBP"):
            return None, ("Yo, that's not a photo I can use — "
                          "send a JPG, PNG or WEBP selfie.")
        img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > 1024:
            scale = 1024 / max(w, h)
            img = img.resize(
                (max(1, round(w * scale)), max(1, round(h * scale))),
                Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "JPEG", quality=90)
        return out.getvalue(), None
    except Exception:
        return None, ("Yo, that picture came through garbled — "
                      "try a different photo.")


def _normalize_art(raw: bytes):
    """Enforce the composition contract on the drawing:
    square by centre-crop (never a stretch), 512 edge,
    WEBP. Returns (data, fmt). Falls back to the raw
    bytes (fmt from the payload) if Pillow can't run —
    the presentation's cover fit still frames it."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        w, h = img.size
        side = min(w, h)
        left, top = (w - side) // 2, (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
        if side != ART_SIZE:
            img = img.resize((ART_SIZE, ART_SIZE), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "WEBP", quality=82)
        return out.getvalue(), "webp"
    except Exception as e:
        logger.warning(f"Avatar art normalize failed: {e}")
        fmt = "png"
        try:
            from PIL import Image
            fmt = (Image.open(io.BytesIO(raw)).format or "png").lower()
        except Exception:
            pass
        return raw, fmt


async def _generate_art(api_key: str, selfie_jpeg: bytes) -> bytes:
    """ONE identity edit: the selfie in, OG-style art out
    (raw image bytes). gpt-image-1 only — no fallback
    model (see the module docstring). Raises on failure."""
    import httpx
    import og_image_gen
    model = og_image_gen.IMAGE_MODEL
    files = {"image": ("selfie.jpg", selfie_jpeg, "image/jpeg")}
    data = {"model": model, "prompt": STYLE_PROMPT,
            "size": "1024x1024", "n": "1"}
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(
            "https://api.openai.com/v1/images/edits",
            headers={"Authorization": f"Bearer {api_key}"},
            files=files, data=data)
    if r.status_code != 200:
        raise RuntimeError(
            f"avatar edit answered {r.status_code}: {r.text[:200]}")
    item = (r.json().get("data") or [])[0]
    b64 = item.get("b64_json")
    if b64:
        return base64.b64decode(b64)
    url = item.get("url")
    if url:
        async with httpx.AsyncClient(timeout=60) as client:
            ir = await client.get(url)
        if ir.status_code == 200 and ir.content:
            return ir.content
    raise RuntimeError("avatar edit returned no image data")


# --- Routes -------------------------------------------------------


def register_avatar_identity_routes(app):
    from fastapi import Request
    from fastapi.responses import JSONResponse, Response

    def _deny():
        return JSONResponse(
            content={"ok": False, "signed_in": False,
                     "response": "Sign in first — your OG face "
                                 "lives on your account."},
            status_code=401)

    @app.get("/avatar-identity/me")
    # Step 3.1 house style: def, not async def — sync store
    # work rides FastAPI's threadpool, never the loop.
    def avatarid_me(request: Request):
        uid = _account_uid(request)
        if not uid:
            return _deny()
        return JSONResponse(content=_public(_load(uid)))

    @app.post("/avatar-identity/rename")
    async def avatarid_rename(request: Request):
        uid = _account_uid(request)
        if not uid:
            return _deny()
        try:
            body = await request.json()
        except Exception:
            body = {}
        raw = body.get("name") if isinstance(body, dict) else None
        name = " ".join(str(raw or "").split())
        if not name:
            return JSONResponse(
                content={"ok": False, "response":
                         "A name can't be blank, fam."},
                status_code=400)
        if len(name) > NAME_MAX:
            return JSONResponse(
                content={"ok": False, "response":
                         f"Keep it under {NAME_MAX + 1} characters "
                         "— that name's too long."},
                status_code=400)
        if any(ord(c) < 32 for c in name):
            return JSONResponse(
                content={"ok": False, "response":
                         "That name's got weird characters in "
                         "it — plain letters, fam."},
                status_code=400)

        def _work():
            rec = _load(uid)
            rec["name"] = None if name == DEFAULT_NAME else name
            _save(uid, rec)
            return rec
        rec = await asyncio.to_thread(_work)
        return JSONResponse(content={
            "ok": True, "name": rec.get("name") or DEFAULT_NAME,
            "response": f"Say less — he's {name} now."})

    @app.post("/avatar-identity/generate")
    async def avatarid_generate(request: Request):
        uid = _account_uid(request)
        if not uid:
            return _deny()
        try:
            form = await request.form()
        except Exception:
            form = {}
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return JSONResponse(
                content={"ok": False, "response":
                         "No photo came through — pick a "
                         "selfie and slide it again."},
                status_code=400)
        raw = await upload.read(SELFIE_MAX_BYTES + 1)
        if len(raw) > SELFIE_MAX_BYTES:
            return JSONResponse(
                content={"ok": False, "response":
                         "Yo, that photo's too heavy — I max "
                         "out at 8 MB. Shrink it down and "
                         "slide it again."},
                status_code=400)
        selfie, err = await asyncio.to_thread(_prep_selfie, raw)
        if selfie is None:
            return JSONResponse(
                content={"ok": False, "response": err},
                status_code=400)

        # The /image weekly pool + token-pack fallback,
        # metered under the account uid.
        import og_tiers
        tier = _tier_of(request, uid)
        cap_w = og_tiers.weekly_cap(tier, "images") or 0
        left = max(0, cap_w - _week_images_used(uid))
        token_funded = False
        if left <= 0:
            try:
                import og_tokenpacks
                token_funded = og_tokenpacks.can_afford(
                    uid, og_tokenpacks.RATES["image"])
            except Exception:
                token_funded = False
        if left <= 0 and not token_funded:
            return JSONResponse(content={
                "ok": False, "capped": True, "weekly_capped": True,
                "response": og_tiers.weekly_image_line(tier)})

        get_key = _deps.get("get_api_key")
        api_key = (get_key() if get_key else
                   os.getenv("OPENAI_API_KEY"))
        if not api_key:
            return JSONResponse(content={
                "ok": False, "response": NO_KEY_LINE})

        try:
            art_raw = await _generate_art(api_key, selfie)
        except Exception as e:
            logger.warning(f"Avatar generation failed: {e}")
            return JSONResponse(content={
                "ok": False, "response": DOWN_LINE})
        art, fmt = await asyncio.to_thread(_normalize_art, art_raw)

        def _work():
            rec = _load(uid)
            rec["art_b64"] = base64.b64encode(art).decode()
            rec["art_fmt"] = fmt
            rec["art_rev"] = hashlib.sha256(art).hexdigest()[:16]
            _save(uid, rec)
            return rec
        rec = await asyncio.to_thread(_work)

        if token_funded:
            try:
                import og_tokenpacks
                await asyncio.to_thread(
                    og_tokenpacks.debit, uid,
                    og_tokenpacks.RATES["image"], "avatar")
            except Exception as e:
                logger.warning(f"Avatar token debit failed: {e}")
        else:
            await asyncio.to_thread(_consume_image, uid)
        payload = _public(rec)
        payload["response"] = (
            "Done and done — that's YOU, OG style. New face, "
            "same me. 🔥")
        return JSONResponse(content=payload)

    @app.post("/avatar-identity/revert")
    def avatarid_revert(request: Request):
        uid = _account_uid(request)
        if not uid:
            return _deny()
        rec = _load(uid)
        rec["art_b64"] = None
        rec["art_rev"] = ""
        _save(uid, rec)
        payload = _public(rec)
        payload["response"] = "Back to the original face."
        return JSONResponse(content=payload)

    @app.get("/avatar-identity/art")
    def avatarid_art(request: Request):
        uid = _account_uid(request)
        if not uid:
            return _deny()
        rec = _load(uid)
        if not rec.get("art_b64"):
            return JSONResponse(
                content={"detail": "no personalized avatar"},
                status_code=404)
        try:
            raw = base64.b64decode(rec["art_b64"])
        except Exception:
            return JSONResponse(
                content={"detail": "avatar unreadable"},
                status_code=404)
        etag = '"' + (rec.get("art_rev") or
                      hashlib.sha256(raw).hexdigest()[:16]) + '"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag})
        media = "image/png" if rec.get("art_fmt") == "png" \
            else "image/webp"
        return Response(
            content=raw, media_type=media,
            headers={"Cache-Control": "private, max-age=86400",
                     "ETag": etag})
