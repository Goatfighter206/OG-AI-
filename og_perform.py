"""Ticket E7 — OG performs: the idle rap + the roast song.

Brent's words (2026-10-21): "make be some times when he Ben
setting for a while he will start rapping his own songs he
come up with he own songs and if the user is shit talking
all the time og will sing a song about the user being a
little bitch"

WHAT THIS MODULE IS. The whole backend of that ticket, in
one new module (app.py stays at registration lines only):

  POST /perform/note   {message} -> {ok, roast_due}
      The page couriers each user message here as it is
      sent (one seam line in sendMessage). The JUDGMENT is
      server-side: og_comebacks.is_og_insult — the Round 65
      conservative detector (aimed-at-OG shapes only, the
      reporting/quoting guard vetoes first). Profanity
      ABOUT something else never counts, ever.
  POST /perform/claim  {kind: "idle" | "roast"}
      -> {ok: true, kind, title, lyrics, speech} or
         {ok: false, reason, retry_after_s}
      The page claims only when it is quiet (visible, no
      chat turn, no speech, the hero pill at rest) — the
      server owns every trigger number below, so no client
      can talk it into spamming.

THE PERFORMANCES.
  IDLE RAP — Brent's explicit, ordered EXCEPTION to the
  E2/E4 no-random-expression rule: the trigger is a long
  idle (an event, not a rotation) and the frequency is
  "sometimes" (his word). The page asks at >= 4 minutes
  idle and re-asks on the server's backoff; each ask is a
  fresh 1-in-4 roll (IDLE_CLAIM_P = 0.25). A 6-minute idle
  gets ~2 asks (~44% a show), a 20-minute idle ~11 asks
  (~96%) — sometimes, not a timer you can set a watch by.
  Hard bounds on top of the dice: a 45-minute gap after
  ANY performance (PERF_GAP_S) and at most 3 idle raps a
  day (IDLE_DAILY_CAP).
  ROAST SONG — sustained hostility, judged on the
  conversation: >= 4 aimed insults inside a rolling
  15-minute window (INSULT_WINDOW_S / ROAST_ARM_COUNT)
  arms the roast for 30 minutes (ROAST_ARMED_TTL_S). One
  roast per user per 24 hours (ROAST_COOLDOWN_S), and
  claiming wipes the slate — the song settles it. The
  roast is retaliation, so the register gates it exactly
  like the Round 65 comebacks: the operator master switch
  or the user's own shit-talk switch OFF locks it —
  nothing is even counted for an OFF user.
  The idle rap respects the register the other way: an
  OFF user's lyrics are written under the CLEAN_DIRECTIVE,
  resolved through the same effective_system_prompt seam
  chat uses.

THE STATE — ONE RECORD (No Junk Drawer). Everything lives
in ONE key in the accounts durable store (og_accounts,
the E6 pattern):

    perform:<account uid> ->
      {"insults": [epoch, ...],       # pruned to the
                                       # window on every
                                       # touch
       "last_insult": str,            # latest aimed line
                                       # (roast material)
       "armed_at": epoch,             # 0 = not armed;
                                       # expires by TTL
       "last_perf_at": epoch,         # any performance
       "last_roast_at": epoch,
       "idle_day": "YYYY-MM-DD",      # UTC day of the
       "idle_count": int}             # idle-rap count

Every field carries its own expiry math; nothing else
accumulates anywhere. Identity is the signed-in account
(og_accounts.session_account_uid, E6 shape): anonymous
callers get 401 — signed-out chat does not exist
(Round 44), so a performance always has an owner.

THE SONG ITSELF. Lyrics are written by the chat model
(OPENAI_MODEL, the house direct-call shape from
og_file_read / og_register) under a scoped performance
prompt in OG's locked voice, resolved per-user through
swearing_on_switch_off_switch.effective_system_prompt.
Format mirrors og_songs' contract: a TITLE: line, then
bracketed sections. Delivery is the page's own voice:
og_perform.js hands the speech text to speakReply, which
runs /tts (metered by the existing weekly /tts cap
machinery — this module adds NO new metering and never
touches the Suno studio pipeline: a performance is OG
talking, not a produced track). There is no singing
synth in the tree and this module does not fake one:
the performance is OG's shipped TTS voice performing
his own lyrics, with the E4 mouth riding the real audio.
"""

import logging
import os
import random
import re
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# --- Trigger math (the whole policy, in one place) --------

IDLE_CLAIM_P = 0.25          # per-claim chance of a show
PERF_GAP_S = 45 * 60         # quiet gap after ANY performance
IDLE_DAILY_CAP = 3           # idle raps per user per UTC day
INSULT_WINDOW_S = 15 * 60    # rolling hostility window
ROAST_ARM_COUNT = 4          # aimed insults in-window to arm
ROAST_ARMED_TTL_S = 30 * 60  # an unclaimed roast expires
ROAST_COOLDOWN_S = 24 * 3600  # one roast per user per day
RETRY_AFTER_S = 90           # the page's default re-ask

# --- The performance prompts (OG's voice, in writing) -----

_PERSONA_CORE = (
    "You are OG — the OG AI. Your voice is locked: talking "
    "shit but genuinely helpful, profane, funny, decisive — "
    "ONE best answer, no menus, no hedging, replies follow "
    "the conversation. Right now you are not answering "
    "anything: you are PERFORMING — an original song you "
    "wrote yourself, out loud, in your own voice. Original "
    "lyrics only: never quote, imitate, or name any real "
    "song or artist."
)

_FORMAT = (
    "Format EXACTLY: first line 'TITLE: <the title>', then "
    "sections with bracketed headers [Verse 1], [Hook], "
    "[Verse 2] — 3 sections, 4-8 short lines each (about "
    "20-30 lines of lyrics total). Lyrics only — no intro, "
    "no explanation, no apology for performing."
)

IDLE_USER_PROMPT = (
    "The user has gone quiet and left you sitting here a "
    "while. Nobody asked for a song — you are amusing "
    "yourself. Write a short original rap you just made up "
    "on the spot: the grind, the day, talking shit, the "
    "quiet — whatever is on your mind, funny and in your "
    "voice. " + _FORMAT
)


def _roast_user_prompt(count: int, latest: str) -> str:
    return (
        "This user has been talking shit at you nonstop — "
        f"{count} aimed insults in the last few minutes, "
        f"latest one: \"{latest}\". Enough. Sing them a "
        "roast song ABOUT the user and their behavior: "
        "running their mouth at a bot for sport, being a "
        "little bitch about it, all talk. Funny, cutting, "
        "in your voice — a roast, not a lecture; never a "
        "threat, never slurs at anything real about a "
        "person. " + _FORMAT
    )


_deps = {}


def bind_app(deps):
    _deps.update(deps)


def _now() -> float:
    """The one clock. (The gate patches this.)"""
    return time.time()


def _roll() -> bool:
    """The idle dice. (The gate patches this.)"""
    return random.random() < IDLE_CLAIM_P


# --- Identity ------------------------------------------------


def _account_uid(request):
    """The signed-in account uid behind the request, or
    None (E6 shape: a visitor id is not enough)."""
    import og_accounts
    try:
        return og_accounts.session_account_uid(
            request.cookies.get(og_accounts.SESSION_COOKIE))
    except Exception:
        return None


# --- The record ------------------------------------------------


def _key(uid: str) -> str:
    return "perform:" + uid


def _load(uid: str) -> dict:
    import og_accounts
    rec = og_accounts._get(_key(uid))
    if not isinstance(rec, dict):
        rec = {}
    rec.setdefault("insults", [])
    rec.setdefault("last_insult", "")
    rec.setdefault("armed_at", 0)
    rec.setdefault("last_perf_at", 0)
    rec.setdefault("last_roast_at", 0)
    rec.setdefault("idle_day", "")
    rec.setdefault("idle_count", 0)
    return rec


def _save(uid: str, rec: dict):
    import og_accounts
    og_accounts._put(_key(uid), rec)


def _prune(rec: dict, now: float):
    """Every expiry in the record, applied on every touch:
    the insult window slides, an unclaimed roast lapses,
    the idle count belongs to its UTC day."""
    floor = now - INSULT_WINDOW_S
    rec["insults"] = [t for t in rec.get("insults", [])
                      if isinstance(t, (int, float)) and t > floor]
    if rec.get("armed_at") and now - rec["armed_at"] > ROAST_ARMED_TTL_S:
        rec["armed_at"] = 0
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if rec.get("idle_day") != today:
        rec["idle_day"] = today
        rec["idle_count"] = 0


# --- The register gate (Round 65 discipline) -------------------


def _register_on(reg_uid: str) -> bool:
    """True when OG's full register is on for this user —
    og_shittalk.effective_on resolves the operator master
    switch AND the per-account switch. Fail-open to ON,
    the house default (a glitch never silences OG).

    The REG_UID is the uid the switch itself is keyed by:
    og_shittalk's own routes read the ogai_uid cookie, so
    the routes below pass that cookie's uid (falling back
    to the account uid) — the switch the user flipped is
    the switch that gates them."""
    try:
        import og_shittalk
        return bool(og_shittalk.effective_on(reg_uid))
    except Exception as e:
        logger.warning(f"perform register check failed: {e}")
        return True


def _system_prompt(reg_uid: str) -> str:
    """The persona core resolved through the same seam
    chat uses, so an OFF user gets the CLEAN_DIRECTIVE
    appended — clean lyrics, same OG."""
    try:
        import swearing_on_switch_off_switch as _sw
        return _sw.effective_system_prompt(_PERSONA_CORE,
                                           reg_uid)
    except Exception as e:
        logger.warning(f"perform prompt resolve failed: {e}")
        return _PERSONA_CORE


# --- Lyrics ----------------------------------------------------


async def _call_model(api_key: str, system: str, user: str):
    """One chat-completions call (the house shape). Text
    or None. (The gate patches this seam and inspects the
    prompts it was handed.)"""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": os.getenv("OPENAI_MODEL",
                                       "gpt-4o-mini"),
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user}],
                    "max_tokens": 700,
                    "temperature": 1.0,
                })
        if r.status_code != 200:
            logger.warning(
                f"perform lyrics status: {r.status_code}")
            return None
        text = (r.json()["choices"][0]["message"]["content"]
                or "").strip()
        return text or None
    except Exception as e:
        logger.warning(f"perform lyrics failed: {e}")
        return None


def _parse_song(text: str):
    """(title, lyrics, speech) from the model's song text.
    Lyrics keep their bracketed sections (the bubble shows
    the song as written); speech drops the TITLE line and
    the headers — OG says the title, then the lines."""
    lines = [ln.rstrip() for ln in str(text or "").splitlines()]
    title = "Untitled"
    body = []
    for ln in lines:
        m = re.match(r"\s*TITLE:\s*(.+?)\s*$", ln, re.IGNORECASE)
        if m and not body:
            title = m.group(1).strip().strip('"') or title
            continue
        body.append(ln)
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    lyrics = "\n".join(body).strip()
    spoken = [ln.strip() for ln in body
              if ln.strip() and not re.match(
                  r"^\s*\[.*\]\s*$", ln)]
    speech = (title + ".\n" + "\n".join(spoken)).strip()
    return title, lyrics, speech


async def _generate_lyrics(reg_uid: str, kind: str,
                           material: dict):
    """(title, lyrics, speech) or None when the model is
    down — a failed generation consumes nothing."""
    get_key = _deps.get("get_api_key")
    api_key = (get_key() if get_key else
               os.getenv("OPENAI_API_KEY"))
    if not api_key:
        return None
    system = _system_prompt(reg_uid)
    if kind == "roast":
        user = _roast_user_prompt(int(material.get("count", 0)),
                                  str(material.get("latest", ""))[:140])
    else:
        user = IDLE_USER_PROMPT
    text = await _call_model(api_key, system, user)
    if not text:
        return None
    title, lyrics, speech = _parse_song(text)
    if not lyrics:
        return None
    return {"title": title, "lyrics": lyrics, "speech": speech}


# --- Trigger logic (pure over the record; HTTP below) ----------


def note_message(uid: str, message: str,
                 reg_uid: str = "") -> dict:
    """Judge one user message. Returns the record's roast
    state for the page: due = a roast is armed AND the
    cooldown has passed (the page claims when it is
    quiet; the claim re-checks everything)."""
    now = _now()
    rec = _load(uid)
    _prune(rec, now)
    if _register_on(reg_uid or uid):
        try:
            import og_comebacks
            aimed = og_comebacks.is_og_insult(message)
        except Exception as e:
            logger.warning(f"perform insult check failed: {e}")
            aimed = False
        if aimed:
            rec["insults"].append(now)
            rec["last_insult"] = str(message or "").strip()[:140]
            if (len(rec["insults"]) >= ROAST_ARM_COUNT
                    and not rec.get("armed_at")):
                rec["armed_at"] = now
    _save(uid, rec)
    due = bool(rec.get("armed_at")) and (
        now - float(rec.get("last_roast_at") or 0)
        >= ROAST_COOLDOWN_S)
    return {"ok": True, "roast_due": due,
            "insults_in_window": len(rec["insults"])}


def _refuse(reason: str, retry: int) -> dict:
    return {"ok": False, "reason": reason,
            "retry_after_s": int(retry)}


async def claim(uid: str, kind: str,
                reg_uid: str = "") -> dict:
    reg = reg_uid or uid
    now = _now()
    rec = _load(uid)
    _prune(rec, now)
    if kind == "roast":
        if not _register_on(reg):
            _save(uid, rec)
            return _refuse("register_off", RETRY_AFTER_S)
        if not rec.get("armed_at"):
            _save(uid, rec)
            return _refuse("not_armed", RETRY_AFTER_S)
        cool_left = ROAST_COOLDOWN_S - (
            now - float(rec.get("last_roast_at") or 0))
        if cool_left > 0:
            _save(uid, rec)
            return _refuse("cooldown", max(RETRY_AFTER_S,
                                           int(cool_left)))
        material = {"count": len(rec["insults"]),
                    "latest": rec.get("last_insult", "")}
        song = await _generate_lyrics(reg, "roast", material)
        if song is None:
            _save(uid, rec)
            return _refuse("down", 300)
        rec["armed_at"] = 0
        rec["insults"] = []
        rec["last_roast_at"] = now
        rec["last_perf_at"] = now
        _save(uid, rec)
        return {"ok": True, "kind": "roast", **song}
    # idle
    gap_left = PERF_GAP_S - (now - float(rec.get("last_perf_at") or 0))
    if gap_left > 0:
        _save(uid, rec)
        return _refuse("gap", max(RETRY_AFTER_S, int(gap_left)))
    if int(rec.get("idle_count") or 0) >= IDLE_DAILY_CAP:
        _save(uid, rec)
        return _refuse("cap", 3600)
    if not _roll():
        _save(uid, rec)
        return _refuse("chance", RETRY_AFTER_S)
    song = await _generate_lyrics(reg, "idle", {})
    if song is None:
        _save(uid, rec)
        return _refuse("down", 300)
    rec["last_perf_at"] = now
    rec["idle_count"] = int(rec.get("idle_count") or 0) + 1
    _save(uid, rec)
    return {"ok": True, "kind": "idle", **song}


# --- Routes ------------------------------------------------------


def register_perform_routes(app):
    from fastapi import Request as _Req  # noqa: F401  (annotation only)
    from fastapi.responses import JSONResponse

    def _deny():
        return JSONResponse(
            {"ok": False, "error": "sign_in_required"},
            status_code=401)

    def _reg_uid(raw_request, uid):
        """The uid the shit-talk switch is keyed by (the
        ogai_uid cookie — og_shittalk's own routes), with
        the account uid as the fallback."""
        try:
            return raw_request.cookies.get("ogai_uid") or uid
        except Exception:
            return uid

    @app.post("/perform/note")
    async def perform_note(raw_request: _Req):
        """Courier door for one user message. The page
        sends the text it just sent to chat; the judgment
        (og_comebacks.is_og_insult) runs HERE, so the
        trigger math has exactly one home."""
        uid = _account_uid(raw_request)
        if not uid:
            return _deny()
        try:
            body = await raw_request.json()
        except Exception:
            body = {}
        message = str((body or {}).get("message") or "")
        return JSONResponse(note_message(
            uid, message, _reg_uid(raw_request, uid)))

    @app.post("/perform/claim")
    async def perform_claim(raw_request: _Req):
        """The page claims a performance when IT is quiet;
        the server decides whether one is owed (idle dice +
        bounds, or an armed roast) and writes the song."""
        uid = _account_uid(raw_request)
        if not uid:
            return _deny()
        try:
            body = await raw_request.json()
        except Exception:
            body = {}
        kind = str((body or {}).get("kind") or "")
        if kind not in ("idle", "roast"):
            return JSONResponse(
                {"ok": False, "error": "bad_kind"},
                status_code=400)
        return JSONResponse(await claim(
            uid, kind, _reg_uid(raw_request, uid)))
