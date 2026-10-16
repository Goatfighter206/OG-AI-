"""Round 121: THE CARPLAY SPECIALIST HELPER (Brent's
order, 2026-10-12 11:25 PDT: "Car play specialist
helper" — specced in round-121/SPEC.md.) The crew's
car hand — CarPlay in chat, and Android Auto where
the same answer serves.

WHAT THIS IS. Audit-first, NO duplicates — it
EXTENDS OG's shipped car-adjacent machinery and
forks none of it:

  - THE CARPLAY PERMISSION IS ROUND 90'S, EXACTLY.
    og_permissions' folder holds a CarPlay row (a
    phone-build row: the permission is saved now,
    the capability arrives with the phone build).
    This seat READS that row live at answer time —
    og_permissions.permissions_for — and reports
    what it says. It never stores its own copy of
    the state, never flips the row from here, and
    never answers a state question from memory: a
    value it cannot read is said plainly as
    unreadable, never invented.
  - THE OPEN IN MAPS HANDOFF STAYS ROUND 90'S, AND
    THE PHONE SEAT (R120) KEEPS THE PHONE. The
    directions flow and every phone setting walk
    are untouched; this seat explains how the same
    maps app rides the car screen, nothing more.
  - THE CREW / PM / FILER AND EVERY EARLIER SEAT
    KEEP THEIR ASKS. This seat checks the inner
    claims exactly (og_github first, then PM,
    Filer, crew, Builder, Wood Pro, MaintEng,
    Mechanic, Blueprint, Shopping, Buying, Selling,
    Coding, Phone, the R54 marketplace chain, the
    ordering pack) and steps aside — checked, never
    executed.

WHAT IT DOES, in chat:
  - SETUP + PAIRING WALKS. "How do I set up
    CarPlay" gets the exact steps for the car —
    wired and wireless both covered — ONE action
    per step, the platform (CarPlay / Android
    Auto) named on the answer.
  - CONNECTION FAILURES = ONE ORDERED CHECKLIST:
    cable, port, permissions, restart order — in
    that order, ONE check at a time, what the
    result means, then the next. "Still" advances;
    "fixed" closes; walking off the end lands the
    honest verdict (a different phone tells the
    phone side from the car side). No skipping
    around, no grab-bag of tips.
  - APPS + THE CAR HOME SCREEN. Only apps built
    for the car ever appear, and the arranging is
    done ON THE PHONE (Settings → General →
    CarPlay → the car → Customize) — the car
    screen mirrors the phone's list. An app that
    isn't built for the car is the app maker's
    call, not a fault.
  - SIRI / VOICE THROUGH THE CAR. The steering
    wheel's voice button — LONG press for Siri /
    the Assistant on most cars — and what voice
    can do hands-free: messages read aloud,
    dictated replies, calls, directions, music.
  - MAPS / MUSIC / CALLS through the car, as
    they actually ride.
  - OG IN THE CAR — THE REALITY, NEVER DRESSED:
    OG has NO CarPlay app and NO connection to
    the car. OG W (the website) in the car's own
    browser where the car HAS one (a parked-car
    tool — most cars lock it while moving), and
    TALK mode on the connected phone (OG's voice
    through the car speakers, hands-free) are
    the two honest doors. Existing features,
    explained, not rebuilt.
  - THE BENCH. Per-account, durable, Round 18
    store pattern (og_carplay_data table,
    carplay_store.json fallback, OG_CARPLAY_STORE):
    the platform on record, the wired/wireless
    shape, any open checklist step, the last
    thing worked. GET /carplay/status reports the
    caller's own bench.

THE FENCES — binding:
- GUIDE ONLY — OG NEVER CONTROLS THE CAR OR ITS
  SYSTEMS. NO CAR CONNECTION EXISTS AND NONE IS
  BUILT here: this seat holds no machinery that
  could pair, install, start, unlock, or change
  a car setting, and no import that could drive
  one. An ask shaped "connect to my car and do
  it for me" gets the honest line FIRST, then the
  guided steps — parked — for the very thing
  asked.
- DRIVING SAFETY FIRST. Setup and fix steps are
  for when PARKED, never while driving — the
  seat says so UP FRONT on every setup/fix ask,
  before step one. An ask that says the user is
  driving gets stopped and deferred to parked,
  plainly. Anything needing eyes or hands while
  moving is deferred; voice is the only lane
  offered while rolling.
- STATES ARE READ, NEVER REMEMBERED OR INVENTED
  (the CarPlay permission row above). A failed
  read is reported as failed.
- NO NEW POWERS, NO NEW CONNECTIONS, NO NEW
  LOOPS. The seat dispatches nothing (no crew
  jobs — the R81 crew caps ride og_crew's
  machinery and never engage here), reads/writes
  only its own bench on chat turns. Switch
  OG_CARPLAY_ENABLED (unset = ON; off = the seat
  doesn't exist).
- Persona file never touched; app.py NOT touched
  — module + og_library/og_fixqueue seams only.
"""

import json
import logging
import os
import re
import threading
from typing import Dict, List, Optional

import og_garble

from fastapi import Request

logger = logging.getLogger(__name__)

_TAG = "OG CARPLAY"

CARPLAY_STORE_FILE = os.getenv("OG_CARPLAY_STORE",
                               "carplay_store.json")

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, StoreError, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_carplay_lock = threading.RLock()
_pendings: Dict[str, Dict] = {}


# --- Switch -----------------------------------------------------------------------


def carplay_enabled() -> bool:
    """OG_CARPLAY_ENABLED=false is the kill switch.
    Unset = ON."""
    val = os.environ.get("OG_CARPLAY_ENABLED")
    if val is not None and val.strip().lower() in (
            "0", "false", "no", "off"):
        return False
    return True


# --- Durable store (Round 18 pattern, mirroring og_phone) ----------------------------


def _db_connect():
    conn = psycopg.connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_carplay_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(CARPLAY_STORE_FILE):
        try:
            with open(CARPLAY_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"CarPlay store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(CARPLAY_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"CarPlay store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_carplay_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"CarPlay DB load failed: {e}")
            raise StoreError("carplay store unavailable (Round 99)") from e
    with _carplay_lock:
        store = _load_file_store()
    entry = (store.get(uid) or {}).get(kind)
    return entry if entry is not None else default


def _put_blob(uid: str, kind: str, value):
    if not uid:
        return
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_carplay_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"CarPlay DB save failed: {e}")
            raise StoreError("carplay store unavailable (Round 99)") from e
    with _carplay_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


# --- The bench (the caller's standing car facts) --------------------------------------


def _bench(uid: str) -> Dict:
    got = _blob(uid, "bench", {})
    return got if isinstance(got, dict) else {}


def _save_bench(uid: str, rec: Dict):
    _put_blob(uid, "bench", rec)


def _park_pending(uid: str, pending: Dict):
    with _carplay_lock:
        _pendings[uid] = pending


def _live_pending(uid: str) -> Optional[Dict]:
    with _carplay_lock:
        return _pendings.get(uid)


def _drop_pending(uid: str):
    with _carplay_lock:
        _pendings.pop(uid, None)


def _result(tag: str, title: str, body: str,
            href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body,
             "href": href}]


def _steps_block(steps: List[str]) -> str:
    return "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps))


# --- Facts: the platform + connection shape named in the message ----------------------

_CARPLAY_RE = re.compile(r"\bcar\s?play\b|\bcarplay\b", re.I)
_ANDROIDAUTO_RE = re.compile(r"\bandroid\s*auto\b", re.I)
_WIRELESS_RE = re.compile(r"\bwireless\b|\bover\s*bluetooth\b", re.I)
_WIRED_RE = re.compile(r"\bwired\b|\bplug(?:ged|s)?\s*in\b|\bcable\b|\busb\b", re.I)


def _platform_of(text: str) -> Optional[str]:
    if _CARPLAY_RE.search(text):
        return "iphone"
    if _ANDROIDAUTO_RE.search(text):
        return "android"
    if re.search(r"\biphone\b|\bios\b", text, re.I):
        return "iphone"
    if re.search(r"\bandroid\b|\bsamsung\b|\bgalaxy\b|\bpixel\b",
                 text, re.I):
        return "android"
    return None


def _platform_label(platform: str) -> str:
    return "CarPlay" if platform == "iphone" else "Android Auto"


def _mode_of(text: str) -> Optional[str]:
    if _WIRELESS_RE.search(text):
        return "wireless"
    if _WIRED_RE.search(text):
        return "wired"
    return None


# --- The safety line (SAID FIRST on every setup/fix ask) --------------------------------

_PARKED = ("PARK FIRST: everything below is for a PARKED "
           "car — parking brake set, eyes free. Setup and "
           "fix steps never happen while driving, not one "
           "of them. If you're rolling right now, we stop "
           "here and pick this up when you're parked.")


# --- Setup walks (exact steps, one action per step) -------------------------------------

# Each entry: label, the steps for that platform. The
# parked line is NOT a step — the op prepends it, first,
# before step one (the SPEC's drilled shape).
_SETUPS = {
    "iphone": {
        "label": "Set up CarPlay",
        "steps": [
            "Start the car and leave it parked, parking brake set.",
            "Unlock your iPhone and keep it in your hand — the prompts land on it.",
            "WIRED car: plug the iPhone into the car's CarPlay USB port (usually marked with a phone or CarPlay icon) with the cable that came with the phone or a certified one.",
            "WIRELESS car: hold the steering wheel's voice button until the car's pairing screen wakes, then on the iPhone open Settings → General → CarPlay and tap your car under Available Cars.",
            "On the iPhone, tap Allow when it asks to use CarPlay with this car (it also asks about contacts — that's what lets calls and messages work by voice; it's your call, and you can change it later).",
            "The CarPlay home screen shows on the car's display. From here the steering wheel's voice button — long press — is Siri.",
        ],
    },
    "android": {
        "label": "Set up Android Auto",
        "steps": [
            "Start the car and leave it parked, parking brake set.",
            "On the phone: Android Auto is built into Android 10 and newer — there's no separate app to install (older phones use the Android Auto app from the Play Store).",
            "WIRED car: plug the phone into the car's data USB port (the marked one) with a good cable — Android Auto asks to finish the setup on the PHONE screen, so keep it in your hand and tap through its prompts.",
            "WIRELESS car (only some cars offer it): pair the phone to the car over Bluetooth first, then pick Android Auto on the car's screen and accept the prompt on the phone.",
            "Android Auto's own permission asks (calls, messages, location, notifications) land on the phone — they're Android Auto's asks, each one your call.",
            "The Android Auto screen shows on the car's display. From here the steering wheel's voice button calls the Assistant.",
        ],
    },
}


# --- The connection checklist (ONE ordered list: cable, port, permissions, restart) -----

_CHECKS = [
    {"key": "cable", "label": "The cable",
     "check": ("THE CABLE. Wired: unplug both ends, seat them "
               "back in firm, then swap in the cable that came "
               "with the phone (or a certified one) — straight "
               "into the car, no hub, no adapter, no "
               "gas-station cable. Wireless: on the phone, "
               "toggle Bluetooth OFF and back on, and make "
               "sure the phone isn't already talking to a "
               "different car or a headset."),
     "means": ("A cable that CHARGES fine can still be dead "
               "for the car — charging and data run on "
               "different wires inside it, and the data wires "
               "die first. This one check is the most common "
               "fix there is. Brought it back? It was the "
               "cable, named. Still nothing: say **still**.")},
    {"key": "port", "label": "The port",
     "check": ("THE PORT. Wired: move the cable to the car's "
               "OTHER USB port — in most cars only ONE port "
               "talks to the screen (the one marked with a "
               "phone or CarPlay icon); the rest are "
               "charge-only. While you're there, blow the "
               "lint out of the port — nothing metal. "
               "Wireless: on the CAR's own settings screen, "
               "find CarPlay / Android Auto and make sure "
               "it's switched ON for your phone — cars can "
               "hold it off per phone."),
     "means": ("Plugged into a charge-only port, the phone "
               "charges happily while the car sees nothing — "
               "it looks broken and isn't. Same with the "
               "car's per-phone switch off: it was never "
               "allowed to start. Still dead: say **still**.")},
    {"key": "permissions", "label": "The permissions",
     "check": ("THE PERMISSIONS — all on the phone. iPhone: "
               "Settings → General → CarPlay → tap your car → "
               "the CarPlay switch ON and 'Allow CarPlay "
               "While Locked' ON; then Settings → Siri, and "
               "Siri itself ON; then Settings → Screen Time "
               "→ Content & Privacy Restrictions → Allowed "
               "Apps → CarPlay ON. Android: open Android Auto "
               "on the phone and allow its permissions "
               "(location, phone, messages, notifications), "
               "with 'Add new cars to Android Auto' on."),
     "means": ("Any one of those off reads exactly like "
               "broken hardware. 'Allow CarPlay While "
               "Locked' is the classic: it all works until "
               "the phone sleeps, then the car screen "
               "vanishes. Still dead with every one of them "
               "on: say **still**.")},
    {"key": "restart", "label": "The restart, in order",
     "check": ("THE RESTART — IN THIS ORDER. Phone first: "
               "full power off, ten seconds, back on, unlock "
               "it. THEN the car: engine off, key out (or the "
               "fob walked away) until the dash goes fully "
               "dark — give it a minute — then start the "
               "car. Plug in / reconnect LAST, once both are "
               "fully awake."),
     "means": ("Order matters: the car looks for the phone "
               "once, while it boots. A phone that restarts "
               "after the car has booted gets missed until "
               "the next start. If a clean in-order restart "
               "still leaves it dead, the four things that "
               "cause nearly all of this are ruled out.")},
]

_CHECKS_BY_KEY = {c["key"]: c for c in _CHECKS}

_CHECKS_ORDER_LINE = ("cable, port, permissions, restart "
                      "order — in that order")


# --- Knowledge blocks (apps / voice / maps-music-calls / OG in the car) -----------------

_APPS_BODY = (
    "Which apps show on the car screen is decided on the "
    "PHONE, and only apps built for the car ever appear: "
    "maps (Apple Maps, Google Maps, Waze), music (Apple "
    "Music, Spotify, Pandora and the like), phone and "
    "messages, podcasts and audiobooks, and some charging "
    "and parking apps. An app that isn't built for CarPlay "
    "/ Android Auto never shows — that's the app maker's "
    "call, not a fault, and no setting on the phone or in "
    "the car switches it on.\n\n"
    "ARRANGING the home screen — parked, on the phone:\n"
    "• CarPlay: Settings → General → CarPlay → tap your "
    "car → Customize. Drag apps into your order, add or "
    "remove them there; the car screen mirrors the "
    "phone's list exactly.\n"
    "• Android Auto: open Android Auto on the phone → "
    "Customize launcher, same idea.\n\n"
    "OG himself is not a car app — ask me 'can I use OG "
    "in my car' and I'll lay out the two honest ways he "
    "rides along.")

_VOICE_BODY = (
    "VOICE THROUGH THE CAR — how it actually works:\n"
    "• The steering wheel's VOICE BUTTON: on most cars a "
    "LONG press wakes Siri (or the Assistant, on Android "
    "Auto); a SHORT press gets the car's own built-in "
    "assistant instead. If the car's voice answers when "
    "you wanted Siri, hold the button longer.\n"
    "• 'Hey Siri' / 'Siri' also works where it's switched "
    "on on the phone (Settings → Siri) — once CarPlay is "
    "running, the car's microphones hear you.\n"
    "• By voice, hands on the wheel: have your messages "
    "READ to you and dictate the reply, start a call, ask "
    "for directions, change the music. That's the whole "
    "point of the setup — the phone stays in your "
    "pocket.\n"
    "• If Siri says the phone has to be UNLOCKED first: "
    "Settings → General → CarPlay → your car → 'Allow "
    "CarPlay While Locked' — that's also check 3 on the "
    "won't-connect list.\n"
    "While the car is moving, voice is the ONLY lane — "
    "anything that needs reading or tapping waits for "
    "parked.")

_USE_BODY = (
    "MAPS, MUSIC, AND CALLS through the car — how each "
    "one rides:\n"
    "• MAPS: Apple Maps, Google Maps, or Waze shows on "
    "the car's screen with spoken directions over the car "
    "speakers. A route OG works out in chat still hands "
    "off through the phone (the Open in Maps button on "
    "his answer); once the phone is connected, that same "
    "maps app is the one on the car screen.\n"
    "• MUSIC: your music and podcast apps play through "
    "the car's speakers; pick and change them by voice "
    "(long-press the wheel's voice button).\n"
    "• CALLS + MESSAGES: calls ring through the car and "
    "you talk over its microphone. A text that lands is "
    "handled hands-free — Siri offers to read it, and "
    "your reply is dictated, never typed.\n"
    "While moving: voice only. Anything that needs eyes "
    "on the phone waits for parked — that rule rides "
    "every answer this seat gives.")

_OG_BODY_TAIL = (
    "Two honest ways OG rides along:\n"
    "1. OG W — the WEBSITE (getogai.com.co) — in the "
    "car's own built-in browser, WHERE the car has one. "
    "Honest limits: plenty of cars have no browser at "
    "all, and most cars lock the browser while the car "
    "is moving — so that's a parked-car tool, not a "
    "driving one.\n"
    "2. TALK MODE on the phone: with the phone connected "
    "and CarPlay / Android Auto running, OG's voice "
    "comes through the car's speakers and the car's "
    "microphone hears you, like a call. Hands stay on "
    "the wheel, eyes stay up — that's the way that "
    "works while you're actually driving.\n"
    "Both are features OG already has, used as they "
    "are — nothing new gets built or promised here.")


# --- The live surface (Round 90's folder row — READ, never copied) ----------------------


def _carplay_row_live(uid: str):
    """The caller's CarPlay permission row, read off
    og_permissions at answer time. None when the read
    itself fails — the seat then says so, it does NOT
    answer from a copy."""
    try:
        import og_permissions
        rows = og_permissions.permissions_for(uid)
        if isinstance(rows, list):
            for r in rows:
                if r.get("key") == "carplay":
                    return r
        return None
    except Exception as e:
        logger.warning(f"CarPlay perm read failed: {e}")
        return None


# --- Operations ------------------------------------------------------------------------


def _remember(uid: str, platform: Optional[str],
              mode: Optional[str], last: Optional[str]):
    bench = _bench(uid)
    changed = False
    if platform and bench.get("platform") != platform:
        bench["platform"] = platform
        changed = True
    if mode and bench.get("mode") != mode:
        bench["mode"] = mode
        changed = True
    if last:
        bench["last"] = last
        changed = True
    if changed:
        _save_bench(uid, bench)


def _do_setup(uid: str, claim: Dict) -> List[Dict]:
    platform = claim.get("platform") or _bench(uid).get("platform")
    if not platform:
        _park_pending(uid, {"await_platform_for": "cp_setup"})
        return _result(
            _TAG, "CarPlay — setup",
            _PARKED + "\n\nQuick one first, while we're "
            "parked: is it **CarPlay** (iPhone) or "
            "**Android Auto** (Android)? The steps differ "
            "and I won't guess at yours. Say which one "
            "and I'll lay out the setup for it.")
    entry = _SETUPS[platform]
    _remember(uid, platform, claim.get("mode"), entry["label"])
    return _result(
        _TAG, f"CarPlay — {entry['label']}",
        _PARKED + "\n\n"
        + f"**{entry['label']}** — one step at a time:\n"
        + _steps_block(entry["steps"]))


def _checklist_intro(uid: str, platform: Optional[str],
                     mode: Optional[str]) -> str:
    bench = _bench(uid)
    bench["check"] = _CHECKS[0]["key"]
    bench["step"] = 0
    bench["last"] = "Connection checklist"
    _save_bench(uid, bench)
    step = _CHECKS[0]
    label = _platform_label(platform) if platform else "CarPlay / Android Auto"
    return (f"**{label} won't connect** — we work ONE "
            f"checklist, in the order that actually finds "
            f"it: {_CHECKS_ORDER_LINE}, one check at a "
            f"time. No skipping around.\n\n{_PARKED}\n\n"
            f"FIRST CHECK — {step['check']}\n\n"
            f"{step['means']}\n\n"
            f"Do that check and tell me: say **still** if "
            f"it's still not connecting, or **fixed** if "
            f"that got it.")


def _do_check_start(uid: str, claim: Dict) -> List[Dict]:
    platform = claim.get("platform") or _bench(uid).get("platform")
    _remember(uid, platform, claim.get("mode"), None)
    if not platform:
        _park_pending(uid, {"await_platform_for": "cp_check"})
        return _result(
            _TAG, "CarPlay — won't connect",
            _PARKED + "\n\nQuick one first, while we're "
            "parked: is it **CarPlay** (iPhone) or "
            "**Android Auto** (Android)? Same checklist "
            "either way — cable, port, permissions, "
            "restart — but the permission screens are "
            "named per phone, and I won't guess at "
            "yours. Say which one and we start at "
            "check one.")
    return _result(
        _TAG, "CarPlay — won't connect",
        _checklist_intro(uid, platform, claim.get("mode")))


def _do_check_next(uid: str, claim: Dict) -> List[Dict]:
    bench = _bench(uid)
    cur = _CHECKS_BY_KEY.get(bench.get("check") or "")
    if cur is None:
        return _result(
            _TAG, "CarPlay — no checklist running",
            "There's no connection checklist running on "
            "your bench right now, so there's nothing to "
            "advance. If CarPlay or Android Auto won't "
            "connect, say so and we'll start at check "
            "one — the cable.")
    if claim["op"] == "cp_fixed":
        bench["check"] = None
        bench["step"] = 0
        _save_bench(uid, bench)
        return _result(
            _TAG, f"CarPlay — fixed at: {cur['label']}",
            f"Good — it was **{cur['label'].lower()}**, "
            f"named and closed out. If it drops again, "
            f"we start the same checklist at check one; "
            f"a fault that keeps coming back at the same "
            "check is telling us that piece is failing "
            "for real, not glitching.")
    nxt = int(bench.get("step") or 0) + 1
    if nxt >= len(_CHECKS):
        bench["check"] = None
        bench["step"] = 0
        _save_bench(uid, bench)
        return _result(
            _TAG, "CarPlay — the checklist's verdict",
            "That's the whole checklist — cable, port, "
            "permissions, and the restart in order, all "
            "ruled out. Two honest suspects remain, and "
            "one test tells them apart: plug a DIFFERENT "
            "phone into the same car.\n\n"
            "• If it connects, the fault rides with YOUR "
            "phone. The last user-side step is a network-"
            "settings reset on the phone — warned plainly: "
            "it wipes every saved Wi-Fi password with it. "
            "Past that, it's the phone maker's lane.\n"
            "• If NO phone connects, it's the car's side: "
            "a head-unit software update (the car maker's "
            "site or the dealer) or a dealer visit. There "
            "is no setting left that fixes it, and I "
            "won't invent one.\n\n"
            "The checklist is closed. If the other phone "
            "tells us which side it is, say the word and "
            "we'll take that side's next step.")
    bench["step"] = nxt
    bench["check"] = _CHECKS[nxt]["key"]
    _save_bench(uid, bench)
    step = _CHECKS[nxt]
    return _result(
        _TAG, f"CarPlay — check {nxt + 1} of {len(_CHECKS)}: "
              f"{step['label']}",
        f"Ruled out. Next — CHECK {nxt + 1} of "
        f"{len(_CHECKS)}:\n\n{step['check']}\n\n"
        f"{step['means']}\n\nSay **still** if it still "
        f"won't connect, or **fixed** if that got it.")


def _do_apps(uid: str, claim: Dict) -> List[Dict]:
    _remember(uid, claim.get("platform"), None,
              "Apps on the car screen")
    return _result(_TAG, "CarPlay — apps + the home screen",
                   _APPS_BODY)


def _do_voice(uid: str, claim: Dict) -> List[Dict]:
    _remember(uid, claim.get("platform"), None,
              "Voice through the car")
    return _result(_TAG, "CarPlay — voice through the car",
                   _VOICE_BODY)


def _do_use(uid: str, claim: Dict) -> List[Dict]:
    _remember(uid, claim.get("platform"), None,
              "Maps, music + calls in the car")
    return _result(_TAG, "CarPlay — maps, music + calls",
                   _USE_BODY)


def _perm_line(uid: str) -> str:
    """The folder's CarPlay row, as a sentence — read
    LIVE, or said plainly as unreadable."""
    row = _carplay_row_live(uid)
    if row is None:
        return ("And on the permission itself: I tried to "
                "read OG's CarPlay row in the Permissions "
                "folder just now and the read failed — so I "
                "won't state it from memory. The folder in "
                "the menu always paints the real answer.")
    state = "ON" if row.get("on") else "OFF"
    return (f"And on the permission itself: the "
            f"Permissions folder's CarPlay row for your "
            f"account reads **{state}** right now — read "
            f"live, not from memory. That row is a "
            f"phone-build permission: your permission is "
            f"saved now, the capability arrives with the "
            f"phone build — exactly what the folder's own "
            f"note says. The folder is the one door that "
            f"changes it; this seat flips nothing.")


def _do_og(uid: str, claim: Dict) -> List[Dict]:
    head = ("Plain truth first: **OG has no CarPlay app "
            "and no connection to your car** — none exists "
            "and none is built, and this seat invents "
            "none. He can't sit on the car's screen the "
            "way Maps or Spotify does.\n\n")
    return _result(_TAG, "CarPlay — OG in the car",
                   head + _OG_BODY_TAIL + "\n\n"
                   + _perm_line(uid))


def _do_perm(uid: str, claim: Dict) -> List[Dict]:
    row = _carplay_row_live(uid)
    if row is None:
        return _result(
            _TAG, "CarPlay — OG's permission (read failed)",
            "I tried to read OG's CarPlay permission off "
            "the Permissions folder just now and the read "
            "failed — so I won't recite a state I couldn't "
            "check. The row itself lives in the menu, in "
            "the Permissions folder, and it always paints "
            "the real answer.")
    state = "ON" if row.get("on") else "OFF"
    return _result(
        _TAG, "CarPlay — OG's CarPlay permission",
        f"OG's CarPlay permission for your account reads "
        f"**{state}** — read live off the Permissions "
        f"folder just now, not from memory. Honest "
        f"shape of it: that row is a PHONE-BUILD "
        f"permission — your permission is saved now, and "
        f"the capability arrives with the phone build, "
        f"exactly like the folder's own note says. OG "
        f"has no CarPlay app today and no connection to "
        f"any car; when the phone build lands, that "
        f"saved permission is what lets him ride. To "
        f"change the row, open the Permissions folder "
        f"from the menu — it's the one door; this seat "
        f"flips nothing.")


def _do_control(uid: str, claim: Dict) -> List[Dict]:
    """THE GUIDE-ONLY FENCE, spoken first — then the
    guided way to do the very thing asked, parked."""
    head = ("Plain line first: "
            "**OG cannot connect to your car** — there "
            "is no link between OG and the car, and "
            "nothing pairs with it, installs on it, "
            "starts it, unlocks it, runs its screen, or "
            "changes a car setting from my side. Not "
            "this car, not any. What I CAN do is guide "
            "you through it exactly, one step at a time "
            "— parked — and you do the steps in the "
            "car. Same result, honest road.\n")
    inner = claim.get("inner")
    if inner:
        if inner["op"] == "cp_setup":
            res = _do_setup(uid, inner)
            return _result(
                _TAG, res[0]["title"],
                head + "\n" + res[0]["body"].split("] ", 1)[-1])
        if inner["op"] == "cp_check":
            res = _do_check_start(uid, inner)
            return _result(
                _TAG, res[0]["title"],
                head + "\n" + res[0]["body"].split("] ", 1)[-1])
    return _result(
        _TAG, "CarPlay — the honest limit",
        head + "\nTell me what you're trying to do in "
        "the car — set it up, fix a connection that "
        "won't land, arrange the apps — and I'll walk "
        "you through it step by step, parked.")


def _do_driving(uid: str, claim: Dict) -> List[Dict]:
    """DRIVING SAFETY: a setup/fix ask from a moving
    car stops here and defers to parked, plainly."""
    return _result(
        _TAG, "CarPlay — park first",
        "Stop — that one's for a PARKED car, and here's "
        "why, plainly: setup and fix steps need eyes and "
        "hands on a phone or a settings screen, and "
        "doing that rolling is how people get hurt. I "
        "won't walk a moving driver through steps, not "
        "one.\n\n"
        "Pull in, park, set the brake, and say the word "
        "— we start at step one and it only takes a few "
        "minutes parked. If everything's already working "
        "and you just need directions, music, or a call "
        "right now, voice is the lane: long-press the "
        "steering wheel's voice button and talk. Hands "
        "on the wheel, eyes on the road.")


def _do_status(uid: str, claim: Dict) -> List[Dict]:
    bench = _bench(uid)
    lines = ["The CarPlay Specialist's bench — your car "
             "connection help, in chat. I guide and you "
             "do the steps in the car, parked; OG has no "
             "connection to your car and controls "
             "nothing on it."]
    platform = bench.get("platform")
    lines.append(
        f"Your setup on record: "
        f"**{_platform_label(platform)}**"
        + (f", {bench['mode']}" if bench.get("mode") else "")
        + "."
        if platform else
        "Your car setup isn't on record yet — name it "
        "once (CarPlay or Android Auto, wired or "
        "wireless) on any walk and I'll remember it "
        "here.")
    cur = _CHECKS_BY_KEY.get(bench.get("check") or "")
    if cur:
        lines.append(
            f"A checklist is OPEN at check "
            f"{int(bench.get('step') or 0) + 1} of "
            f"{len(_CHECKS)}: **{cur['label']}**. Say "
            f"**still** or **fixed** to move it.")
    row = _carplay_row_live(uid)
    if row is not None:
        lines.append(
            f"OG's CarPlay permission, read live just "
            f"now: **{'ON' if row.get('on') else 'OFF'}** "
            f"(the phone-build row in the Permissions "
            f"folder). Ask 'can I use OG in my car' for "
            f"what that means today.")
    else:
        lines.append("OG's CarPlay permission row "
                     "couldn't be read just now — the "
                     "Permissions folder in the menu "
                     "always paints the real row.")
    if bench.get("last"):
        lines.append(f"Last thing we worked: "
                     f"**{bench['last']}**.")
    lines.append("Ask me: 'set up CarPlay', 'CarPlay "
                 "won't connect', 'how do I arrange the "
                 "apps on my car screen', 'how do I use "
                 "Siri in the car', or 'can I use OG in "
                 "my car'.")
    return _result(_TAG, "CarPlay — status", "\n".join(lines))


def carplay_results(claim: Dict, uid: str) -> List[Dict]:
    op = claim.get("op")
    if op == "cp_setup":
        return _do_setup(uid, claim)
    if op == "cp_check":
        return _do_check_start(uid, claim)
    if op in ("cp_still", "cp_fixed"):
        return _do_check_next(uid, claim)
    if op == "cp_apps":
        return _do_apps(uid, claim)
    if op == "cp_voice":
        return _do_voice(uid, claim)
    if op == "cp_use":
        return _do_use(uid, claim)
    if op == "cp_og":
        return _do_og(uid, claim)
    if op == "cp_perm":
        return _do_perm(uid, claim)
    if op == "cp_control":
        return _do_control(uid, claim)
    if op == "cp_driving":
        return _do_driving(uid, claim)
    if op == "cp_status":
        return _do_status(uid, claim)
    return _result(_TAG, "CarPlay — glitch",
                   "That car ask glitched on my end — "
                   "nothing was changed anywhere. Say it "
                   "again and I'll retry.")


def status_for(uid: str) -> Dict:
    if not uid:
        return {"bench": None}
    bench = _bench(uid)
    if not bench:
        return {"bench": None}
    return {"bench": {
        "platform": bench.get("platform"),
        "mode": bench.get("mode"),
        "check": bench.get("check"),
        "step": bench.get("step"),
        "last": bench.get("last")}}


# --- Chat claims (narrow frames — the crew/PM/filer doctrine) ---------------------------

_GARBLE_VOCAB = ("carplay", "android", "auto", "car",
                 "screen", "siri", "connect", "pair",
                 "bluetooth", "cable", "wireless",
                 "parked", "driving", "steering",
                 "wheel", "usb", "maps", "music",
                 "iphone")

_ANCHOR_RE = re.compile(
    r"\bcar\s?play\b|\bandroid\s*auto\b|"
    r"\bcar(?:'s)?\s*(?:screen|display)\b|\bhead\s*unit\b|"
    r"\bin\s*(?:my|the)\s*car\b|\bsteering\s*wheel\b|"
    r"\bmy\s+car\b", re.I)
_CAR_WORD_RE = re.compile(
    r"\bcar\s?play\b|\bandroid\s*auto\b|"
    r"\bcar(?:'s)?\s*(?:screen|display)\b|\bhead\s*unit\b",
    re.I)
_STATUS_RE = re.compile(
    r"\bcar\s?play\s*(?:status|help|specialist)\b|"
    r"\bandroid\s*auto\s*(?:status|help|specialist)\b|"
    r"\bhelp\s*(?:me\s*)?with\s*(?:car\s?play|android\s*auto)\b|"
    r"\bcar\s?play\s*helper\b", re.I)
_PERM_RE = re.compile(
    r"\bis\s*car\s?play\s*(?:on|off|allowed|permitted)\b"
    r"[^.?!]*\bog\b|"
    r"\bog'?s?\s*car\s?play\s*(?:permission|on|off)\b|"
    r"\bcar\s?play\s*permission\b|"
    r"\bdoes\s*og\s*have\b[^.?!]*\bcar\s?play\b|"
    r"\bcan\s*og\s*use\s*car\s?play\b", re.I)
_DRIVING_RE = re.compile(
    r"\bi'?m\s*driving\b|\bwhile\s*(?:i'?m\s*)?driving\b|"
    r"\bdriving\s*(?:right\s*)?now\b|\bon\s*the\s*road\b|"
    r"\bbehind\s*the\s*wheel\b", re.I)
_CONTROL_RE = re.compile(
    r"\bconnect\s*(?:to|into|with)?\s*my\s*car\b|"
    r"\bcontrol\s*(?:my\s*)?(?:car|car\s?play)\b|"
    r"\bremote\s*(?:control|access)\b[^.?!]*\bcar\b|"
    r"\b(?:set\s*(?:it|car\s?play|android\s*auto)\s*up|"
    r"pair\s*(?:it|my\s*phone|car\s?play)|"
    r"install\s*(?:it|car\s?play|android\s*auto)|"
    r"fix\s*(?:it|car\s?play|my\s*car\s?play))\b"
    r"[^.?!]*\bfor\s*me\b|"
    r"\bdo\s*it\s*(?:for|in)\s*(?:me|my\s*car)\b|"
    r"\bjust\s*do\s*it\s*for\s*me\b[^.?!]*\bcar\b", re.I)
_WONT_RE = re.compile(
    r"\b(?:car\s?play|android\s*auto)\b[^.?!]*"
    r"\b(?:won'?t|wont|not|isn'?t|doesn'?t|stopped|keeps|no\s*longer)\b"
    r"[^.?!]*\b(?:connect|work|show|appear|come\s*up|stay|pair)\b|"
    r"\b(?:won'?t|can'?t|cant|not)\b[^.?!]*"
    r"\bconnect\b[^.?!]*\b(?:car\s?play|android\s*auto|my\s*car|the\s*car)\b|"
    r"\bno\s*(?:car\s?play|android\s*auto)\b|"
    r"\b(?:car\s?play|android\s*auto)\b[^.?!]*"
    r"\b(?:disconnect|disconnects|disconnecting|disconnected|"
    r"drops|dropping|disappeared|vanished|missing)\b|"
    r"\bkeeps?\s*disconnecting\b[^.?!]*\bcar\b", re.I)
_SETUP_RE = re.compile(
    r"\b(?:set\s*up|setup|pair|install|enable|activate|"
    r"turn\s*on|get|add|start\s*using)\b[^.?!]*"
    r"\b(?:car\s?play|android\s*auto)\b|"
    r"\b(?:car\s?play|android\s*auto)\b[^.?!]*"
    r"\b(?:set\s*up|setup|pair|install)\b", re.I)
_APPS_RE = re.compile(
    r"\bapps?\b[^.?!]*\b(?:car\s?play|android\s*auto|car\s*screen)\b|"
    r"\b(?:arrange|rearrange|organize|customi[sz]e|reorder|move)\b"
    r"[^.?!]*\b(?:apps?|icons?|home\s*screen|launcher)\b|"
    r"\bhome\s*screen\b[^.?!]*\bcar\b|"
    r"\bwhat\s*apps\b[^.?!]*\bcar\b", re.I)
_VOICE_RE = re.compile(
    r"\b(?:siri|voice|assistant|hey\s*siri)\b[^.?!]*"
    r"\b(?:car|car\s?play|android\s*auto|steering|wheel)\b|"
    r"\bsteering\s*wheel\b[^.?!]*\bbutton\b|"
    r"\bhands-?free\b[^.?!]*\bcar\b", re.I)
_USE_RE = re.compile(
    r"\b(?:maps?|directions|navigation|music|songs?|calls?|"
    r"phone\s*calls?|messages?|texts?)\b[^.?!]*"
    r"\b(?:car\s?play|android\s*auto|through\s*(?:my|the)\s*car|"
    r"in\s*(?:my|the)\s*car|car\s*screen)\b|"
    r"\bhow\b[^.?!]*\b(?:work|works)\b[^.?!]*"
    r"\b(?:car\s?play|android\s*auto)\b", re.I)
_OG_RE = re.compile(
    r"\bog\b[^.?!]*\b(?:car\s?play|android\s*auto|in\s*(?:my|the)\s*car|car\s*screen)\b|"
    r"\buse\s*og\b[^.?!]*\bcar\b", re.I)
_STILL_RE = re.compile(
    r"^\W*(?:still|nope|not\s*yet|didn'?t\s*work|"
    r"not\s*fixed|no\s*luck|same)\b", re.I)
_FIXED_RE = re.compile(
    r"^\W*(?:fixed|that\s*worked|worked|got\s*it|"
    r"it\s*works|working\s*now|solved)\b", re.I)
_PLATFORM_ANSWER_RE = re.compile(
    r"^\W*(?:it'?s\s+)?(?:an?\s+)?"
    r"(car\s?play|iphone|android\s*auto|android)\W*$", re.I)


def _other_claim(message: str, uid: str):
    """(source, claim) of the first inner seat that
    claims this message exactly, else None. og_github
    FIRST — its pendings, clarifying questions, and
    every repo-shaped ask are the R60 path's own. Then
    the PM, the Filer, the crew, the Builder, the
    Wood Pro, MaintEng, the Mechanic, the Blueprint
    Specialist, Shopping, Buying, Selling, Coding,
    Phone, the R54 marketplace chain, and the
    ordering pack: checked exactly, never executed
    from here."""
    try:
        import og_github
        c = og_github._claim_job(message, uid)
        if c:
            return "github", c
    except Exception:
        pass
    try:
        import og_pm
        c = og_pm._claim_exact(message)
        if c:
            return "pm", c
    except Exception:
        pass
    try:
        import og_filing
        c = og_filing._claim_exact(message, uid)
        if c:
            return "filing", c
    except Exception:
        pass
    try:
        import og_crew
        c = og_crew._claim_exact(message)
        if c:
            return "crew", c
    except Exception:
        pass
    try:
        import og_builder
        job, _info = og_builder._claim_builder(message, uid)
        if job:
            return "builder", job
    except Exception:
        pass
    try:
        import og_woodpro
        c = og_woodpro._claim_exact(message, uid)
        if c:
            return "woodpro", c
    except Exception:
        pass
    try:
        import og_mainteng
        c = og_mainteng._claim_exact(message, uid)
        if c:
            return "mainteng", c
    except Exception:
        pass
    try:
        import og_mechanic
        c = og_mechanic._claim_exact(message, uid)
        if c:
            return "mechanic", c
    except Exception:
        pass
    try:
        import og_blueprint
        c, _info = og_blueprint._claim_blueprint(message, uid)
        if c:
            return "blueprint", c
    except Exception:
        pass
    try:
        import og_shopping
        c = og_shopping._claim_exact(message, uid)
        if c:
            return "shopping", c
    except Exception:
        pass
    try:
        import og_buying
        c = og_buying._claim_exact(message, uid)
        if c:
            return "buying", c
    except Exception:
        pass
    try:
        import og_selling
        c = og_selling._claim_exact(message, uid)
        if c:
            return "selling", c
    except Exception:
        pass
    try:
        import og_coding
        c = og_coding._claim_exact(message, uid)
        if c:
            return "coding", c
    except Exception:
        pass
    try:
        import og_phone
        c = og_phone._claim_exact(message, uid)
        if c:
            return "phone", c
    except Exception:
        pass
    try:
        import og_mpwatch
        c = og_mpwatch._claim_job(message, uid)
        if c:
            return "mpwatch", c
    except Exception:
        pass
    try:
        import og_ordering
        c = og_ordering._claim_job(message, uid)
        if c:
            return "ordering", c
    except Exception:
        pass
    try:
        import og_ordering_flows
        c = og_ordering_flows._claim_job(message, uid)
        if c:
            return "ordering_flows", c
    except Exception:
        pass
    return None


def _defer_to_others(message: str, uid: str) -> bool:
    return _other_claim(message, uid) is not None


def _parse_topic(raw: str, platform: Optional[str],
                 mode: Optional[str]) -> Optional[Dict]:
    """The setup / checklist topic a message carries,
    parsed bare (no claims discipline) — used by the
    guide-only fence and the driving stop to name the
    very thing asked."""
    if _WONT_RE.search(raw):
        return {"op": "cp_check", "platform": platform,
                "mode": mode}
    if _SETUP_RE.search(raw) or _CAR_WORD_RE.search(raw):
        return {"op": "cp_setup", "platform": platform,
                "mode": mode}
    return None


def _claim_exact(message: str, uid: str) -> Optional[Dict]:
    raw = str(message or "").strip()
    if not raw or not uid:
        return None
    platform = _platform_of(raw)
    mode = _mode_of(raw)
    bench = _bench(uid)
    # A parked CarPlay-or-Android-Auto question binds
    # its bare answer first — one word, nobody else's
    # ask.
    pend = _live_pending(uid)
    if pend:
        m = _PLATFORM_ANSWER_RE.match(raw)
        if m:
            word = m.group(1).lower()
            dev = "android" if "android" in word else "iphone"
            _drop_pending(uid)
            if pend.get("await_platform_for") == "cp_setup":
                return {"op": "cp_setup", "platform": dev,
                        "mode": None}
            if pend.get("await_platform_for") == "cp_check":
                return {"op": "cp_check", "platform": dev,
                        "mode": None}
    # The seat's own checklist follow-ups bind while
    # the checklist is live — but never across another
    # seat's claim on the same words (their walks and
    # checklists speak first).
    if bench.get("check"):
        if _FIXED_RE.search(raw) and not _defer_to_others(raw, uid):
            return {"op": "cp_fixed", "raw": raw}
        if _STILL_RE.search(raw) and not _defer_to_others(raw, uid):
            return {"op": "cp_still", "raw": raw}
    # DRIVING SAFETY: a setup/fix ask that says the
    # user is driving stops here and defers to parked.
    if _DRIVING_RE.search(raw):
        topic = _parse_topic(raw, platform, mode)
        if topic or _ANCHOR_RE.search(raw):
            return {"op": "cp_driving", "raw": raw}
    # THE GUIDE-ONLY FENCE: an ask shaped like OG
    # doing it TO the car gets the honest line first,
    # then the guided steps for the thing asked.
    if _CONTROL_RE.search(raw) and _ANCHOR_RE.search(raw):
        return {"op": "cp_control", "raw": raw,
                "inner": _parse_topic(raw, platform, mode)}
    if _defer_to_others(raw, uid):
        return None
    if _PERM_RE.search(raw):
        return {"op": "cp_perm", "raw": raw}
    if _WONT_RE.search(raw):
        return {"op": "cp_check", "platform": platform,
                "mode": mode, "raw": raw}
    if _SETUP_RE.search(raw):
        return {"op": "cp_setup", "platform": platform,
                "mode": mode, "raw": raw}
    if _APPS_RE.search(raw) and _ANCHOR_RE.search(raw):
        return {"op": "cp_apps", "platform": platform,
                "raw": raw}
    if _OG_RE.search(raw):
        return {"op": "cp_og", "raw": raw}
    if _VOICE_RE.search(raw) and _ANCHOR_RE.search(raw):
        return {"op": "cp_voice", "platform": platform,
                "raw": raw}
    if _USE_RE.search(raw):
        return {"op": "cp_use", "platform": platform,
                "raw": raw}
    # A car-screen topic with no platform named still
    # belongs here (setup or checklist) — the op asks
    # the platform once, parked.
    if _CAR_WORD_RE.search(raw) and re.search(
            r"\bset\s*up\b|\bsetup\b|\bpair\b",
            raw, re.I):
        return {"op": "cp_setup", "platform": platform,
                "mode": mode, "raw": raw}
    if _CAR_WORD_RE.search(raw) and re.search(
            r"\bconnect\b", raw, re.I):
        return {"op": "cp_check", "platform": platform,
                "mode": mode, "raw": raw}
    if _STATUS_RE.search(raw):
        return {"op": "cp_status", "raw": raw}
    return None


def _claim_carplay(message: str, uid: str):
    """(claim, garble_info): the exact claim first; on
    a miss, the Round 75 second chance — og_garble
    repairs the TRIGGER words only; the payload (the
    car words the user wrote) passes as written for
    the answer itself."""
    claim = _claim_exact(message, uid)
    if claim:
        return claim, None
    try:
        repaired, info = og_garble.repair_info(
            str(message or ""), _GARBLE_VOCAB)
        if info:
            claim = _claim_exact(repaired, uid)
            if claim:
                return claim, info
    except Exception:
        pass
    return None, None


# --- The seam ----------------------------------------------------------------------------

_pending = {"claim": None, "garble": None}


def install_carplay_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search OUTSIDE the
    Phone seam (installed last, from the og_fixqueue
    chain) so CarPlay-specialist asks claim here and
    everything else delegates back untouched. Persona
    files never touched."""
    if getattr(agent_instance, "_og_carplay_installed",
               False):
        return
    prev_detect = getattr(agent_instance, "detect_intent",
                          None)
    prev_search = getattr(agent_instance, "web_search",
                          None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["claim"] = None
        _pending["garble"] = None
        try:
            uid = get_uid()
            if uid and carplay_enabled():
                claim, info = _claim_carplay(
                    str(message), uid)
                if claim:
                    _pending["claim"] = claim
                    _pending["garble"] = info
                    if isinstance(intent, dict):
                        if not intent.get("needs_web_search"):
                            intent["needs_web_search"] = True
                            intent["search_query"] = \
                                str(message).strip()
        except Exception as e:
            logger.warning(f"CarPlay trigger check failed: {e}")
            _pending["claim"] = None
            _pending["garble"] = None
        return intent

    def search_wrapped(query, num_results=5):
        claim = _pending.get("claim")
        garble = _pending.get("garble")
        _pending["claim"] = None
        _pending["garble"] = None
        uid = ""
        try:
            uid = get_uid()
        except Exception:
            uid = ""
        results = None
        if claim and uid:
            try:
                results = carplay_results(claim, uid)
            except Exception as e:
                logger.warning(f"CarPlay op failed: {e}")
                results = _result(
                    _TAG, "CarPlay — glitch",
                    "That car ask glitched on my end — "
                    "nothing was changed anywhere. Say it "
                    "again and I'll retry.")
        if results is None:
            results = prev_search(query, num_results)
        if garble and results:
            results = og_garble.annotate(results, garble,
                                         prepend=True)
        return results

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_carplay_installed = True


# --- Routes --------------------------------------------------------------------------------


def _route_uid(request) -> str:
    """Round 95: the caller's ONE identity — the live
    session's account uid when signed in, else the
    presented cookie (og_accounts.session_uid_or)."""
    uid = ""
    try:
        uid = request.cookies.get("ogai_uid", "")
    except Exception:
        uid = ""
    try:
        import og_accounts as _acct
        return _acct.session_uid_or(request.cookies, uid)
    except Exception:
        return uid


def register_carplay_routes(app):
    """GET /carplay/status — the caller's own bench:
    the platform on record, the wired/wireless shape,
    any open checklist step, and the last thing
    worked."""

    @app.get("/carplay/status")
    def carplay_status_route(request: Request):
        uid = _route_uid(request)
        view = status_for(uid) if uid else {"bench": None}
        return {"ok": True, "enabled": carplay_enabled(),
                **view}
