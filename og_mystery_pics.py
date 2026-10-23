"""MM-PICS — pictures for the Murder Mystery game
(og-games-wave-ticket-2026-10-23.md, Brent approved
2026-10-23 12:51).

WHAT THIS MODULE IS: the picture seam for og_mystery.
At game start it makes ONE crime-scene picture and
ONE portrait per suspect; after that, ONE photo for
each dealt clue at the moment that clue is served —
never before. Every picture rides OG's EXISTING image
machinery, exactly as a normal chat image does:

- generation: og_image_gen.openai_generate_image
  (the gpt-image-1 / dall-e-3 chain);
- Library save: og_image_gen.autosave_generated_image
  — the Ticket P2 path — so a saved picture lands in
  the owner's Library under Images, owner-checked
  like everything else in the locker;
- caps/metering: NOTHING here invents a cap. The
  /image endpoint in app.py is where image caps live
  (audit, cand-mm-pics/REPORT.md): the tier's WEEKLY
  image cap via og_tiers.weekly_cap(tier, "images")
  minus og_tiers.week_used over the app usage store's
  "image:<uid>" entry, the og_tokenpacks fallback
  (can_afford / debit at RATES["image"]) once the
  weekly pool is dry, and _consume_image on success
  only. app.py binds those exact functions into this
  module via bind_app() — the og_tokenpacks /
  og_files house pattern — so a mystery picture and
  a chat picture draw from the same pool, the same
  ledger, the same rates. Unbound, the meter seam
  refuses (no parallel cap is ever guessed at).

HARD RULE — PROMPTS ARE PUBLIC-STATE ONLY: every
prompt is built by the pure builders below from
PUBLIC game state and nothing else: the setting,
the victim's name and public profile, the tone, a
suspect's public name / role (the only way suspects
are ever introduced — the record carries no public
appearance field), and a clue's text AFTER that clue
has been served. The stored truth is never read
here: no killer identity or marker, no method, no
motive, no suspect secret, no finder lie, no
planted/real flag, no unserved clue content. Every
portrait uses ONE neutral template — the same words
for the killer and the innocents alike; only the
public name / role slots change.

FAILURE DISCIPLINE: a generation failure, a missing
API key, or a cap hit never blocks the game. The
first failure flips the game record to text-only
(pics_off), records ONE plain notice as pending on
the record, and og_mystery surfaces that notice in
that turn's directive exactly once
(pics_notice_sent). No further image is attempted
for that game — so a cap hit means zero further
image calls, and the notice can never repeat.

ONE WRITTEN RECORD: every picture made is appended
to the game record's "pics" list — kind, label,
prompt, library item ref + autosave status, model,
when — and the owner door (GET /games) lists them.
Pictures die with the game record (purge_owner
already owns the record's life).

MAIN THREAD RULE: a picture is store/API work, the
same weight a normal /image ask already carries;
generation runs through _run_coro (the og_designer
pattern) so the sync chat seam can call the async
generator even inside a running event loop. No
scheduler, no background task, nothing else heavy.

KILL SWITCHES: OG_MYSTERY_PICS_ENABLED darkens
pictures alone (the game stays fully playable);
OG_MYSTERY_ENABLED darkens the whole mode in
og_mystery, so pictures go with it. House pattern:
enabled unless the env value is an explicit off.
"""

import asyncio
import logging
import os
import threading
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# The ONE plain notice a game ever gets about its
# pictures. og_mystery appends it to a directive once
# (pics_notice_sent on the record is the receipt).
PIC_NOTICE = (
    "Pictures are off for this game — we're going "
    "text-only from here.")


# --- Kill switch ---------------------------------------------------------------


def pics_enabled() -> bool:
    """OG_MYSTERY_PICS_ENABLED: enabled unless the
    env value is an explicit off."""
    return os.environ.get(
        "OG_MYSTERY_PICS_ENABLED", "true").strip().lower() not in (
            "0", "false", "no", "off")


# --- Bound app hooks (the /image cap/meter path) ---------------------------------
# app.py binds the SAME functions its /image endpoint
# uses. See the module docstring: no parallel cap.

_hooks: Dict = {}


def bind_app(hooks: Dict):
    _hooks.update(hooks or {})


def seam_get_tier(uid: str) -> str:
    fn = _hooks.get("get_tier")
    if fn is None:
        return "free"
    try:
        return str(fn(uid) or "free")
    except Exception as e:
        logger.warning(f"Mystery pics tier hook failed: {e}")
        return "free"


def seam_images_left(uid: str, tier: str) -> Optional[int]:
    """Images left in the tier's weekly pool, via the
    bound /image computation. None = the meter is not
    bound — the seam then refuses rather than guessing
    a cap of its own."""
    fn = _hooks.get("images_left")
    if fn is None:
        return None
    return int(fn(uid, tier))


def seam_can_afford_tokens(uid: str) -> bool:
    fn = _hooks.get("can_afford_tokens")
    if fn is None:
        return False
    try:
        return bool(fn(uid))
    except Exception as e:
        logger.warning(f"Mystery pics token check failed: {e}")
        return False


def seam_consume_image(uid: str):
    fn = _hooks.get("consume_image")
    if fn is not None:
        fn(uid)


def seam_debit_tokens(uid: str):
    fn = _hooks.get("debit_tokens")
    if fn is not None:
        fn(uid)


def seam_get_api_key() -> str:
    fn = _hooks.get("get_api_key")
    if fn is not None:
        try:
            return str(fn() or "")
        except Exception:
            return ""
    return os.getenv("OPENAI_API_KEY", "") or ""


# --- The generation / autosave seam ----------------------------------------------
# Module-level functions on purpose: the gate stubs
# THESE (recording every prompt, returning fake
# bytes), and production runs the real machinery.


def _run_coro(factory):
    """Run an async factory to completion from sync
    code, even inside a running event loop (via a
    helper thread) — the og_designer pattern."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    box: Dict = {}

    def _t():
        try:
            box["r"] = asyncio.run(factory())
        except Exception as e:  # noqa: BLE001
            box["e"] = e

    th = threading.Thread(target=_t, daemon=True)
    th.start()
    th.join(timeout=240)
    if "e" in box:
        raise box["e"]
    return box.get("r")


def seam_generate_image(api_key: str, prompt: str):
    """ONE image through og_image_gen.openai_generate_image.
    Returns (image_src, model_used); raises on failure."""
    import og_image_gen
    return _run_coro(
        lambda: og_image_gen.openai_generate_image(api_key, prompt))


def seam_autosave_image(uid: str, tier: str, image_src: str,
                        prompt: str) -> Dict:
    """The Ticket P2 save: og_image_gen.autosave_generated_image.
    Never raises by its own contract; wrapped anyway."""
    import og_image_gen
    return og_image_gen.autosave_generated_image(
        uid, tier, image_src, prompt)


# --- Prompt builders (PURE, public state only) -------------------------------------
# These take plain public values — never the record,
# never the truth. og_mystery extracts the public
# fields and passes them in, so truth fields have no
# path into a prompt even by accident.


def crime_scene_prompt(setting: str, victim: str,
                       victim_profile: str, tone: str) -> str:
    p = (f"Crime scene illustration for a {tone} murder "
         f"mystery. Setting: {setting}. The room just after "
         f"{victim} was found dead — an empty, still scene, "
         "no people, no body, painterly style.")
    if victim_profile:
        p += f" The victim was known as: {victim_profile}."
    return p


def portrait_prompt(name: str, role: str, tone: str) -> str:
    """ONE neutral template for every suspect — killer
    and innocents get the identical words; only the
    public name / role slots change."""
    return (f"Portrait illustration of {name}, the {role}, "
            f"for a {tone} murder mystery. Head and "
            "shoulders, neutral expression, plain "
            "background, painterly style.")


def clue_prompt(clue_text: str, setting: str, tone: str) -> str:
    """Built ONLY from a clue's text, and only ever
    called after that clue has been served."""
    return (f"Evidence photo for a {tone} murder mystery "
            f"set in {setting}: {clue_text} Close-up, no "
            "people, painterly style.")


# --- The driver ---------------------------------------------------------------------


def _pics_off(rec: Dict):
    """First failure of any kind: the game goes
    text-only, the ONE notice is queued on the record,
    and no image is ever attempted again for it."""
    rec["pics_off"] = True
    if not rec.get("pics_notice_sent") and \
            not rec.get("pics_pending_notice"):
        rec["pics_pending_notice"] = PIC_NOTICE


def _already(rec: Dict, kind: str, key: str) -> bool:
    for p in rec.get("pics") or []:
        if p.get("kind") == kind and p.get("key") == key:
            return True
    return False


def make_picture(rec: Dict, kind: str, key: str,
                 label: str, prompt: str) -> Optional[Dict]:
    """Make ONE picture for a game, riding the /image
    cap/meter path. Returns the record entry on
    success, None when pictures are dark, already
    made, capped, or failed — the caller never
    branches on why; the record carries the state.
    Mutates rec (the caller saves it)."""
    if not pics_enabled() or rec.get("pics_off"):
        return None
    if _already(rec, kind, key):
        return None
    uid = str(rec.get("owner") or "")
    if not uid:
        _pics_off(rec)
        return None
    tier = seam_get_tier(uid)
    # The /image order, verbatim: weekly pool first,
    # token-pack fallback once the pool is dry, refuse
    # only when neither can pay.
    try:
        left = seam_images_left(uid, tier)
    except Exception as e:
        logger.warning(f"Mystery pics meter failed: {e}")
        left = None
    if left is None:
        _pics_off(rec)
        return None
    token_funded = False
    if left <= 0:
        token_funded = seam_can_afford_tokens(uid)
        if not token_funded:
            _pics_off(rec)   # cap hit — text-only, once
            return None
    api_key = seam_get_api_key()
    if not api_key:
        _pics_off(rec)
        return None
    try:
        image_src, model_used = seam_generate_image(api_key, prompt)
    except Exception as e:
        logger.warning(f"Mystery picture failed ({kind}): {e}")
        _pics_off(rec)       # generation failure — same discipline
        return None
    # Metering is success-only, exactly as /image does
    # it: debit the token pack, else consume one from
    # the weekly pool.
    try:
        if token_funded:
            seam_debit_tokens(uid)
        else:
            seam_consume_image(uid)
    except Exception as e:
        logger.warning(f"Mystery pics metering failed: {e}")
    save: Dict = {"status": "missed", "record": None}
    try:
        save = seam_autosave_image(uid, tier, image_src, prompt) \
            or save
    except Exception as e:
        logger.warning(f"Mystery pics autosave failed: {e}")
    lib = save.get("record") or {}
    entry = {
        "kind": kind,
        "key": key,
        "label": label,
        "prompt": prompt,
        "model": model_used,
        "library_id": lib.get("id", "") if isinstance(lib, dict) else "",
        "save_status": save.get("status", "missed"),
        "token_funded": bool(token_funded),
        "made_at": time.time(),
    }
    rec.setdefault("pics", []).append(entry)
    return entry


def opening_pictures(rec: Dict) -> List[Dict]:
    """Game start: ONE crime scene + ONE portrait per
    suspect. Called by og_mystery AFTER the truth is
    committed and the opening is built — the prompts
    below read only the setup / roster fields the
    opening itself serves publicly."""
    if not pics_enabled() or rec.get("pics_off"):
        return []
    setup = rec.get("setup") or {}
    tone = setup.get("tone") or "gritty noir"
    made = []
    entry = make_picture(
        rec, "crime_scene", "crime_scene", "Crime scene",
        crime_scene_prompt(
            setup.get("setting") or "",
            setup.get("victim") or "",
            setup.get("victim_profile") or "",
            tone))
    if entry:
        made.append(entry)
    for s in rec.get("suspects") or []:
        if rec.get("pics_off"):
            break
        entry = make_picture(
            rec, "portrait", str(s.get("id") or ""),
            f"Portrait: {s.get('name', '')}",
            portrait_prompt(
                s.get("name") or "", s.get("role") or "", tone))
        if entry:
            made.append(entry)
    return made


def clue_picture(rec: Dict, clue: Dict) -> Optional[Dict]:
    """ONE photo for a clue, at the moment it is
    served. og_mystery calls this only with a clue
    whose served flag has just been set — the prompt
    is built from that clue's (now public) text."""
    if not pics_enabled() or rec.get("pics_off"):
        return None
    if not clue or not clue.get("served"):
        return None
    setup = rec.get("setup") or {}
    text = str(clue.get("text") or "")
    idx = -1
    for i, c in enumerate(rec.get("clues") or []):
        if c is clue or c.get("text") == text:
            idx = i
            break
    return make_picture(
        rec, "clue", f"clue:{idx}", f"Clue photo {idx + 1}",
        clue_prompt(text, setup.get("setting") or "",
                    setup.get("tone") or "gritty noir"))
