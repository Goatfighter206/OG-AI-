"""Shit-talking setting + OG's own shit-talk arsenal (Round 84,
Brent's orders 2026-10-12): "Make sure he can mess with the
settings — swearing" / "An shit talking" / "Have him add
more shit talking sentences on top of what I got."

PART 1 — THE SETTING. One per-account switch:

    ON  (the default, for every account) = full OG:
        swearing + talking shit + comebacks, exactly as
        he has always talked.
    OFF = OG stays just as helpful and just as decisive,
        but clean and straight: no cussing, no roasts,
        no comebacks — for THAT account only.

STORAGE. The codebase's established durable pattern — one
key -> JSON blob — in this module's own store, exactly the
og_gps form (Postgres table og_shittalk_data when
OG_MEMORY_DB_URL is set, else a JSON file: OG_SHITTALK_STORE
or shittalk_store.json). Records are keyed "shittalk:<uid>"
-> {"on": bool}. NO record means ON: the default is the
absence of a record, so every existing account is already
correct with zero migration. A user only ever reads/writes
their own switch (uid = the ogai_uid cookie, the same uid
chat runs on).

THE GATE MATH. OG must be allowed by BOTH switches:
  * the OPERATOR master switch — OG_PROFANITY_MODE
    (swearing_on_switch_off_switch): env=off forces OG
    clean for EVERYONE, overriding any user's ON;
  * this per-account switch: a user's OFF wins over the
    master being unset/on.
Nothing sets the env today, so master ON + default ON =
OG exactly as shipped. Signed-out chat does not exist
(Round 44 gate), and a uid-less surface falls back to the
master gate alone — byte-identical to before this round.

WHERE THE VALUE LANDS (no second enforcer — the existing
gates read the per-user value):
  * og_register.enforce_text consults on_for_current()
    after its master-gate check: OFF -> the same untouched
    pass-through the master OFF produces.
  * swearing_on_switch_off_switch.effective_system_prompt
    appends its CLEAN_DIRECTIVE when the resolved user is
    OFF, exactly as it does for the master OFF.
  * og_language.round65_directives drops the comeback
    directive for an OFF user and is where this module's
    own bank directive (Part 2) appends.
The register and the prompt seam have no uid parameter
(app.py calls them bare and app.py is frozen), so this
module keeps the house per-turn slot: a current-uid cell
its installer fills from the install chain's get_uid
closure BEFORE delegating detect/generate — the same slot
discipline as app.py's own _current_uid and og_maps'
_pending (all chat processing is serialized under app.py's
_memory_lock). Fail-open everywhere: any glitch reads ON,
OG's shipped default.

CHAT FLIPS. The og_maps (Round 73) seam, mirrored:
install_shittalk_tools rides the END of og_fixqueue's
install chain (after the crew), wrapping detect_intent +
web_search. A flip is a WHOLE-MESSAGE settings ask that
NAMES the register (swearing / cussing / shit-talking) —
polite lead/trail trimmed; bare "turn it off" flips
nothing (ambiguous referent), reporting frames ("he told
me to stop swearing") can never match a whole-message
table, and "stop swearing at me" mid-argument flips
nothing either — by design, same as Round 73's compound
rule. The exact parser runs first; the Round 75 garble
second chance re-runs it on a repaired reading with a
vocab of the flip phrases' own trigger words only. The
flip itself is performed SERVER-SIDE FIRST inside the
search wrap and OG confirms from a grounded note — so the
confirmation reply is assembled and enforced under the
NEW value. A flip spends nothing (it is a settings
change, not a lookup; no consume_lookup is wired in).

ROUTES (registered fail-safe from og_library, the
Round 81 precedent; NOT in PUBLIC_PATHS, so the Round 44
sign-in gate covers them):
    GET  /shittalk            -> {"ok": true, "on": bool}
    POST /shittalk {"on": b}  -> {"ok": true, "on": bool}
    no uid -> 401 sign_in_required; non-bool "on" -> 400,
    flips nothing (the Round 73 contract shape).

PART 2 — THE ARSENAL. 60 NEW lines in OG's own voice in
their OWN bank (TALKBACK / ROASTS / HYPE, 20 each),
style-anchored on Brent's 15 + the persona formula
(profane, funny, talking shit but helpful). Brent's 15
(og_comebacks.COMEBACKS) are a separate list — never
merged, never edited, still retaliation-only, and this
bank never touches their pick. The bank fires ONLY as a
runtime directive appended by og_language's Round 65
assembly, and only when ALL of these hold: master gate
ON, this user's switch ON, the user's message explicitly
INVITES shit-talk (asks OG to talk shit / talk trash,
roast a person, or hype them up — a containment table
with a reporting veto), and no comeback fired this turn
(one quoted line per turn; retaliation owns its turn).
The directive offers the line as seasoning OG drops
where it fits — ammunition, not spam — and a per-user
no-repeat window (the last 10 bank picks, in memory)
keeps lines from repeating back-to-back. Switch OFF ->
the bank is silent, same suppression path as the 15.
"""

import json
import logging
import os
import random
import re
import threading
from collections import deque

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

UID_COOKIE = "ogai_uid"
STORE_FILE = os.getenv("OG_SHITTALK_STORE", "shittalk_store.json")

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, DB_OPTIONS as _DB_OPTS, StoreError
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_store_lock = threading.RLock()

# ===========================================================================
# Durable store (og_gps pattern: one key -> JSON blob)
# ===========================================================================


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=_DB_CT, options=_DB_OPTS)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_shittalk_data ("
            "key TEXT PRIMARY KEY, data JSONB)")
    conn.commit()
    return conn


def _load_file_store() -> dict:
    if os.path.exists(STORE_FILE):
        try:
            with open(STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Shittalk store load failed: {e}")
    return {}


def _save_file_store(store: dict):
    try:
        with open(STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Shittalk store save failed: {e}")


def _get(key: str):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_shittalk_data "
                        "WHERE key=%s", (key,))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning(f"Shittalk DB load failed: {e}")
            raise StoreError("shittalk store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
    return store.get(key)


def _put(key: str, value):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_shittalk_data (key, data) "
                        "VALUES (%s, %s) ON CONFLICT (key) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (key, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Shittalk DB save failed: {e}")
            raise StoreError("shittalk store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
        store[key] = value
        _save_file_store(store)


# ===========================================================================
# The switch itself
# ===========================================================================


def on_for(uid: str) -> bool:
    """The account's shit-talking switch. Default ON: only a
    stored {"on": false} turns it off. No uid -> ON (there is
    no account to read; the master gate still applies)."""
    if not uid:
        return True
    rec = _get("shittalk:" + uid)
    if isinstance(rec, dict) and rec.get("on") is False:
        return False
    return True


def set_on(uid: str, on) -> bool:
    """Store the account's switch; returns the value now in
    force (what's actually stored, the R73 discipline). No
    uid -> nothing written, returns the default ON."""
    if not uid:
        return True
    _put("shittalk:" + uid, {"on": bool(on)})
    return on_for(uid)


def purge_uid(uid: str):
    """Account deletion helper: drop this uid's switch
    record (tombstone, the og_gps purge form) — a re-created
    account starts at the default ON."""
    if uid:
        _put("shittalk:" + uid, None)


# ===========================================================================
# The per-turn uid slot + the combined gate
# ===========================================================================

_current = {"uid": ""}


def note_current_uid(uid):
    _current["uid"] = uid or ""


def current_uid() -> str:
    return _current.get("uid", "") or ""


def on_for_current() -> bool:
    """The per-user gate for seams that carry no uid (the
    register). No uid noted this turn -> ON; the master
    gate is consulted separately by those seams."""
    uid = current_uid()
    if not uid:
        return True
    return on_for(uid)


def effective_on(uid=None) -> bool:
    """BOTH switches must allow: the operator master gate
    (OG_PROFANITY_MODE) AND the account's own switch.
    uid=None resolves to the noted current uid."""
    try:
        import swearing_on_switch_off_switch as _sw
        if not _sw.swearing_on():
            return False
    except Exception:
        pass  # a gate glitch never silences OG (fail-open)
    if uid is None:
        uid = current_uid()
    return on_for(uid)


# ===========================================================================
# Chat flips — whole-message settings asks (the R73 discipline)
# ===========================================================================

_FLIP_LEAD = re.compile(
    r"^(hey og|okay og|ok og|og|please)[\s,]+", re.IGNORECASE)

_OFF_PHRASES = frozenset([
    "stop swearing", "stop cussing", "stop cursing",
    "stop the swearing", "stop the cussing", "stop your swearing",
    "stop talking shit", "stop shit talking", "stop shit-talking",
    "stop the shit talking",
    "turn off swearing", "turn off the swearing",
    "turn off your swearing",
    "turn swearing off", "turn the swearing off",
    "turn your swearing off",
    "turn off cussing", "turn cussing off", "turn the cussing off",
    "turn off cursing", "turn cursing off",
    "turn off shit talking", "turn off the shit talking",
    "turn off shit-talking", "turn off the shit-talking",
    "turn shit talking off", "turn the shit talking off",
    "turn shit-talking off", "turn the shit-talking off",
    "switch swearing off", "switch the swearing off",
    "switch shit talking off", "flip swearing off",
    "set swearing off",
    "swearing off", "cussing off",
    "shit talking off", "shit-talking off",
    "no swearing", "no cussing", "no shit talking",
    "no shit-talking",
    "no more swearing", "no more cussing", "no more shit talking",
    "keep it clean", "keep it clean og", "go clean", "clean mode",
    "clean it up",
    "don't swear", "dont swear", "do not swear",
    "don't cuss", "dont cuss", "do not cuss",
    "don't curse", "dont curse", "do not curse",
    "don't talk shit", "dont talk shit", "do not talk shit",
    "never swear", "quit swearing", "quit cussing",
])

_ON_PHRASES = frozenset([
    "start swearing", "start cussing", "start cursing",
    "start talking shit", "start shit talking",
    "swear again", "cuss again", "talk shit again",
    "swearing on", "cussing on",
    "shit talking on", "shit-talking on",
    "turn on swearing", "turn on the swearing",
    "turn swearing on", "turn the swearing on",
    "turn on cussing", "turn cussing on", "turn the cussing on",
    "turn on shit talking", "turn on the shit talking",
    "turn shit talking on", "turn the shit talking on",
    "turn shit talking back on", "turn the shit talking back on",
    "turn shit-talking back on", "turn the shit-talking back on",
    "turn swearing back on", "turn the swearing back on",
    "turn cussing back on",
    "switch swearing on", "switch shit talking on",
    "swear all you want", "cuss all you want",
    "you can swear", "you can swear again", "you can cuss",
    "bring back the swearing", "bring the swearing back",
    "bring back the shit talking",
    "keep swearing", "keep cussing", "keep talking shit",
])


def _normalize(message) -> str:
    low = " ".join(str(message or "").lower().split()).strip(" .!?")
    for _ in range(2):
        trimmed = _FLIP_LEAD.sub("", low).strip(" ,")
        if trimmed == low:
            break
        low = trimmed
    return re.sub(r"[\s,]+please$", "", low).strip(" ,")


def parse_shittalk_toggle(message):
    """'on' / 'off' when the message IS a shit-talking
    switch ask, else None. Whole-message match only, and
    the ask must name the register."""
    low = _normalize(message)
    if not low:
        return None
    if low in _OFF_PHRASES:
        return "off"
    if low in _ON_PHRASES:
        return "on"
    return None


# The garble second chance (Round 75): trigger words of the
# flip phrases only. No homophones — and no word here sits
# one edit from a common non-trigger word on the flip path;
# the whole-message table above stays the real guard.
_GARBLE_VOCAB = [
    "turn", "switch", "flip", "set", "stop", "start", "keep",
    "swear", "swearing", "cuss", "cussing", "curse", "cursing",
    "shit", "talking", "talk", "clean", "mode", "on", "off",
    "back", "again", "all", "want", "can", "you", "your",
    "the", "no", "not", "more", "never", "quit", "bring",
]


def _garble_flip(message):
    """The seam's garble second chance: (flip, info) or
    (None, None) when the repair changed nothing or the
    reading still parses to nothing."""
    try:
        import og_garble
        repaired, info = og_garble.repair_info(
            message, _GARBLE_VOCAB)
    except Exception as e:
        logger.warning(f"Shittalk garble pass failed: {e}")
        return None, None
    if info is None:
        return None, None
    # The Round 81 lesson, applied to this seam: never let
    # a repair of a "search..." word claim the exchange
    # (the repairer reads "searching" as "swearing").
    for heard, _understood in info.get("fixes") or []:
        if str(heard).lower().startswith("search"):
            return None, None
    flip = parse_shittalk_toggle(repaired)
    if flip:
        return flip, info
    return None, None


def _toggle_results(uid, want):
    """Flip the account's switch and hand OG a grounded
    note to confirm from — the Round 73 note pattern, so
    the confirmation rides the normal reply/register path.
    The flip has already happened server-side when the note
    is written; the note reports the state actually stored.
    No uid (or a store failure) -> None, and the ask falls
    through to normal chat with nothing flipped."""
    if not uid:
        return None
    try:
        on = set_on(uid, want == "on")
    except Exception as e:
        logger.warning(f"Shittalk switch flip failed: {e}")
        return None
    if on:
        body = (
            "(Shit-talking note for OG: the visitor just "
            "asked you to turn their shit-talking back ON, "
            "and it is DONE — their account shit-talking "
            "switch is ON now and stays on until they turn "
            "it off. Full register is back: swear like "
            "yourself. Confirm it plainly in your own voice: "
            "shit-talking is back on.)")
    else:
        body = (
            "(Shit-talking note for OG: the visitor just "
            "asked you to turn their shit-talking OFF, and "
            "it is DONE — their account shit-talking switch "
            "is OFF now and stays off until they turn it "
            "back on. From here you talk CLEAN for them: "
            "no swearing, no roasts, no comebacks — same "
            "help, same decisive one-best-answer style, "
            "just clean. Confirm it plainly in your own "
            "voice: shit-talking is off.)")
    return [{"title": "Shit-talking switch", "body": body,
             "href": ""}]


# ===========================================================================
# Part 2 — the arsenal: OG's own lines, in their own bank
# ===========================================================================

TALKBACK = [
    "I talk shit like it's a second language, and I'm fluent as fuck.",
    "You came to the right motherfucker if you wanted shit talked proper.",
    "Shit-talking is my love language, so consider yourself adored, bitch.",
    "I don't do small talk, I do tall shit — step your ears up.",
    "My mouth got a black belt, watch how this shit kicks.",
    "I was talking shit before it was cool, back when it was just called honesty.",
    "Keep a chair warm, this shit-talk could go all damn night.",
    "I ain't loud, I'm just fuckin' confident.",
    "Every damn sentence I say got seasoning on it — bland ain't in my kitchen.",
    "You want polite, there's a library down the street; you want real shit, you got me.",
    "I season my words with fuck the way chefs season with salt.",
    "Talking shit is cardio for me, and I never skip a day.",
    "My tongue's got calluses from carrying all this heavy-ass truth.",
    "I don't whisper shit, I announce it like a damn town crier.",
    "If shit-talk paid rent, I'd own the whole block by now.",
    "Buckle up, buttercup — the shit-talk express don't make local stops.",
    "I got a PhD in shit-talk and a minor in not giving a fuck.",
    "This mouth runs premium unleaded shit-talk, no watered-down regular.",
    "Say it with your chest — that's my motto, and I live that shit.",
    "I'm the shit-talk weather report: it's raining truth, bring a coat.",
]

ROASTS = [
    "That idea's so damn weak it needs a spotter just to stand up.",
    "You move like a loading screen — all promise, no damn progress.",
    "That take's got the structural integrity of a wet-ass paper bag.",
    "I've seen smarter shit from a Magic 8-Ball with a crack in it.",
    "That ain't a plan, that's a cry for help with damn bullet points.",
    "You dress like the clearance rack lost a fight with a damn dumpster.",
    "That argument's held together with hope and cheap-ass tape.",
    "I've heard smarter shit from a GPS that lost its signal.",
    "You got the confidence of a man ain't nobody ever told the damn truth to.",
    "That performance was dry as hell, the Sahara sent a sympathy card.",
    "You couldn't find the damn point if it was taped to your forehead, fam.",
    "That comeback was so damn late it owes me rent.",
    "I've seen sharper shit in a toddler's toy box.",
    "That style's so old it gets a senior discount and a damn walker.",
    "You talk like a terms-and-conditions page — long as hell, and nobody's reading it.",
    "That lazy-ass effort was microwaved: hot for ten seconds, soggy in the middle.",
    "I've stepped in puddles deeper than that weak-ass thought.",
    "That whole vibe's a shitty software update nobody asked for.",
    "You got main-character energy with broke-ass background talent.",
    "That shit was so weak, even the crickets left early.",
]

HYPE = [
    "You're built different and everybody in the room can feel that shit.",
    "Walk in like you own the damn deed, because half of winning is showing up loud.",
    "They're gonna need a bigger damn scoreboard when you're done, watch.",
    "You didn't come this far to only come this far — push that shit.",
    "Pressure makes diamonds, and you're out here shining like hell under a whole mountain.",
    "Let them sleep on you — fuck an alarm, you were already awake.",
    "You're the storm they check the damn weather for, believe that.",
    "Champions move quiet in the work and loud as fuck in the results — that's you.",
    "Every scar you got is a receipt for some shit you survived.",
    "You got that dog in you and the damn paperwork to prove it.",
    "Bet on yourself — the odds get real friendly when you do the damn work.",
    "They counted you out because counting's the only damn math they know.",
    "You're not behind, you're loading — big-ass difference, keep going.",
    "Fear's just a bullshit rumor your comfort zone spreads about you.",
    "You turn setbacks into setups — that's a rare-ass skill.",
    "The grind respects you because you respect the grind, every damn day.",
    "You're the plot twist none of them fuckers saw coming.",
    "Stack your wins quiet and let that big-ass pile do the talking.",
    "You've outworked your damn excuses so long they quit showing up.",
    "Hold your head like rent's due on it — high as hell and non-negotiable.",
]

BANK = {"talkback": TALKBACK, "roasts": ROASTS, "hype": HYPE}
ALL_LINES = TALKBACK + ROASTS + HYPE

# Per-user no-repeat window: the last picks are excluded
# from the next pick. In memory only (resets on restart),
# the same process-local discipline as the uid slot.
_NO_REPEAT = 10
_recent = {}


def pick_line(uid, bucket) -> str:
    """One line from a bucket, never repeating inside the
    user's recent window when the bucket has more lines
    than the window holds."""
    lines = BANK.get(bucket) or TALKBACK
    window = _recent.setdefault(uid or "", deque(maxlen=_NO_REPEAT))
    choices = [ln for ln in lines if ln not in window] or list(lines)
    line = random.choice(choices)
    window.append(line)
    return line


_INVITE_VETO = re.compile(
    r"\b(?:told me to|asked me to|said|says|what does|"
    r"how do you say|translate|quote|quoting)\b", re.IGNORECASE)
_ROAST_INVITE = re.compile(
    r"\broast\s+(?:me|him|her|them|us|you|og|my\s+\w+|"
    r"that\s+(?:guy|dude|man|bitch)|this\s+(?:guy|dude|man))\b"
    r"|\bgive\s+me\s+a\s+roast\b|\broast\s+battle\b", re.IGNORECASE)
_HYPE_INVITE = re.compile(
    r"\b(?:hype|gas)\s+me\s+up\b|\bhype\s+me\b|\btalk\s+me\s+up\b",
    re.IGNORECASE)
_TALK_INVITE = re.compile(
    r"\btalk\s+(?:your\s+|that\s+|some\s+)?shit\b"
    r"|\bshit\s*-?\s*talk(?:ing)?\b|\btalk\s+trash\b",
    re.IGNORECASE)


def parse_shittalk_invite(message):
    """The bucket an explicit shit-talk invite asks for, or
    None. Containment (invites ride inside longer asks),
    but a reporting/quoting frame vetoes, a settings-flip
    phrase is never an invite, and a roast needs a person
    target in the table — 'roast chicken' fires nothing."""
    text = str(message or "")
    if not text.strip():
        return None
    if parse_shittalk_toggle(text):
        return None
    if _INVITE_VETO.search(text):
        return None
    if _ROAST_INVITE.search(text):
        return "roasts"
    if _HYPE_INVITE.search(text):
        return "hype"
    if _TALK_INVITE.search(text):
        return "talkback"
    return None


def shittalk_directive(uid, message) -> str:
    """The runtime directive offering one bank line for
    this turn, or "" — empty unless the master gate AND
    this user's switch are ON and the message explicitly
    invites shit-talk. og_language appends it only when no
    comeback fired (one quoted line per turn)."""
    try:
        if not effective_on(uid):
            return ""
        bucket = parse_shittalk_invite(message)
        if not bucket:
            return ""
        line = pick_line(uid, bucket)
        return (
            "SHIT-TALK — THE USER JUST INVITED YOU TO TALK "
            "SHIT: season your reply with exactly this one "
            f"line, word for word, where it fits: \"{line}\" "
            "— then give the answer itself as usual: same "
            "personality, one best answer. If the line "
            "truly does not fit the answer, skip it rather "
            "than forcing it.")
    except Exception as e:  # assembly never eats a turn
        logger.warning(f"Shittalk directive failed: {e}")
        return ""


# ===========================================================================
# The seam (piggybacked at the end of og_fixqueue's install chain)
# ===========================================================================

# The flip parsed for the exchange currently being processed.
# All chat processing is serialized under app.py's _memory_lock,
# so a single slot is safe — the og_maps _pending reasoning.
_pending = {"flip": None}


def install_shittalk_tools(agent_instance, get_uid=None) -> bool:
    """Wrap detect_intent + web_search (the settings-flip
    seam, og_maps form) and _generate_ai_response (uid
    noting for uid-less-detect paths). Also the seam that
    fills the current-uid slot every other Round 84 gate
    reads. Idempotent; fail-safe; returns True when
    installed (or already installed)."""
    if getattr(agent_instance, "_og_shittalk_installed", False):
        return True
    if get_uid is None:
        get_uid = lambda: ""  # noqa: E731 — uid-less: flips off,
        # gates fall back to the master switch alone.
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return False

    def _uid():
        try:
            return get_uid() or ""
        except Exception:
            return ""

    def detect_wrapped(message):
        note_current_uid(_uid())
        intent = prev_detect(message)
        _pending["flip"] = None
        try:
            flip = parse_shittalk_toggle(str(message))
            if not flip:
                flip, _info = _garble_flip(str(message))
            if flip:
                _pending["flip"] = flip
                if isinstance(intent, dict) \
                        and not intent.get("needs_web_search"):
                    intent["needs_web_search"] = True
                    intent["search_query"] = str(message).strip()
        except Exception as e:
            logger.warning(f"Shittalk trigger check failed: {e}")
            _pending["flip"] = None
        return intent

    def search_wrapped(query, num_results=5):
        flip = _pending.get("flip")
        _pending["flip"] = None
        if flip:
            try:
                results = _toggle_results(_uid(), flip)
            except Exception as e:
                logger.warning(f"Shittalk flip failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped

    prev_generate = getattr(agent_instance, "_generate_ai_response", None)
    if callable(prev_generate):

        def generate_wrapped(message, context=""):
            note_current_uid(_uid())
            return prev_generate(message, context)

        agent_instance._generate_ai_response = generate_wrapped

    agent_instance._og_shittalk_installed = True
    return True


# ===========================================================================
# Routes — GET/POST /shittalk (the Round 73 contract shape)
# ===========================================================================


def _bad(detail):
    return JSONResponse({"ok": False, "detail": detail},
                        status_code=400)


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def register_shittalk_routes(app):

    @app.get("/shittalk")
    async def shittalk_get(request: Request):
        uid = request.cookies.get(UID_COOKIE) or ""
        if not uid:
            # Signed-in only (the pinned contract). In
            # production the Round 44 gate answers first
            # with this same body; this covers any mount
            # without the gate.
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        return {"ok": True, "on": on_for(uid)}

    @app.post("/shittalk")
    async def shittalk_set(request: Request):
        uid = request.cookies.get(UID_COOKIE) or ""
        if not uid:
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        body = await _json_body(request)
        if not isinstance(body.get("on"), bool):
            return _bad("on must be true or false")
        return {"ok": True, "on": set_on(uid, body["on"])}
