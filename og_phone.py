"""Round 120: THE PHONE SPECIALIST HELPER (Brent's
order, 2026-10-12 11:25 PDT: "Phone specialist
helper" — specced in round-120/SPEC.md.)

WHAT THIS IS. The crew's phone hand — iPhone +
Android help in chat. Audit-first, NO duplicates:
it EXTENDS OG's shipped phone machinery and forks
none of it.

  - THE PERMISSIONS FOLDER IS ROUND 90'S, EXACTLY.
    og_permissions holds the ONE per-account list of
    what OG may use on the user's phone; the GPS row
    on it IS Round 73's location switch (og_gps).
    This seat READS those surfaces live at answer
    time — og_permissions.permissions_for,
    og_gps.location_on_for — and reports what they
    say. It never stores its own copy of a permission
    state, never flips a switch from here, and never
    answers a state question from memory: a value it
    cannot read is said plainly as unreadable, never
    invented.
  - THE OPEN IN MAPS HANDOFF STAYS ROUND 90'S. The
    directions flow (ask in chat, the gold button
    hands the drive to the phone's maps app) is
    untouched; this seat only explains it and walks
    the user to its permission when asked.
  - THE CREW / PM / FILER AND EVERY EARLIER SEAT
    KEEP THEIR ASKS. This seat checks the inner
    claims exactly (og_github first, then PM, Filer,
    crew, Builder, Wood Pro, MaintEng, Mechanic,
    Blueprint, Shopping, Buying, Selling, Coding,
    the R54 marketplace chain, the ordering pack)
    and steps aside — checked, never executed.

WHAT IT DOES, in chat:
  - SETTINGS WALKS. "How do I turn on the hotspot
    on my iPhone" gets the exact tap path for that
    phone/OS: dead-simple numbered steps, ONE action
    per step (Brent's own style rule), the OS named
    on the answer. If no phone is named, the seat
    asks ONCE — iPhone or Android — and the bare
    answer binds (and the phone is remembered on the
    caller's bench for the next walk).
  - TROUBLESHOOTING. A symptom (battery draining,
    storage full, no signal, apps crashing, won't
    charge, frozen, stuck update) gets ONE BEST
    FIRST CHECK, what the result means, then the
    next step — the R113/R114 one-check-at-a-time
    discipline. "Still" advances; "fixed" closes.
    Safety lines ride where they belong: a swollen
    or too-hot battery is a STOP line, first.
  - WHAT'S USING WHAT. Battery/storage/data hogs
    are read off the phone's OWN lists — the seat
    walks the user to the exact screen and the app
    at the top of the list is named the culprit.
    The facts come off the user's phone, not a
    guess.
  - OG ON YOUR PHONE. "What can OG use on my
    phone" / "is my location on for OG" answer off
    the LIVE permission surfaces above, with the
    folder named as the one door that changes them.
  - BACKUPS + NEW PHONE. Back-up-now walks per OS
    and the move-to-a-new-phone outline (Quick
    Start / Smart Switch), numbered, one tap at a
    time.

THE FENCES — binding:
- OG CANNOT REMOTE-CONTROL THE USER'S PHONE. Not
  a setting, not a fix, not anything: this seat
  GUIDES and THE USER TAPS. Any ask shaped "just
  do it on my phone for me" gets the honest line
  FIRST, then the guided steps for the thing
  asked. There is no remote-control machinery in
  this module — no imports that could drive a
  device, no shell, nothing.
- STATES ARE READ, NEVER REMEMBERED OR INVENTED.
  Permission and switch states come from the real
  surfaces at answer time (see above). A failed
  read is reported as failed.
- NO NEW POWERS, NO NEW CONNECTIONS, NO NEW
  LOOPS. The seat reads/writes only its own bench
  on chat turns; it deletes nothing on the user's
  phone (deletion steps in a walk are the user's
  own taps, always named as theirs).
- Persona file never touched; app.py NOT touched —
  module + og_library/og_fixqueue seams only.
"""

import json
import logging
import os
import re
import threading
import time
from typing import Dict, List, Optional

import og_garble

from fastapi import Request

logger = logging.getLogger(__name__)

_TAG = "OG PHONE"

PHONE_STORE_FILE = os.getenv("OG_PHONE_STORE",
                             "phone_store.json")

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, DB_OPTIONS as _DB_OPTS, StoreError, apply_bounds as _apply_bounds
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_phone_lock = threading.RLock()
_pendings: Dict[str, Dict] = {}


# --- Switch -----------------------------------------------------------------------


def phone_enabled() -> bool:
    """OG_PHONE_ENABLED=false is the kill switch.
    Unset = ON."""
    val = os.environ.get("OG_PHONE_ENABLED")
    if val is not None and val.strip().lower() in (
            "0", "false", "no", "off"):
        return False
    return True


# --- Durable store (Round 18 pattern, mirroring og_coding) -------------------------


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=_DB_CT, options=_DB_OPTS)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_phone_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(PHONE_STORE_FILE):
        try:
            with open(PHONE_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Phone store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(PHONE_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Phone store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_phone_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"Phone DB load failed: {e}")
            raise StoreError("phone store unavailable (Round 99)") from e
    with _phone_lock:
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
                        "INSERT INTO og_phone_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Phone DB save failed: {e}")
            raise StoreError("phone store unavailable (Round 99)") from e
    with _phone_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


# --- The bench (the caller's standing phone facts) -----------------------------------


def _bench(uid: str) -> Dict:
    got = _blob(uid, "bench", {})
    return got if isinstance(got, dict) else {}


def _save_bench(uid: str, rec: Dict):
    _put_blob(uid, "bench", rec)


def _park_pending(uid: str, pending: Dict):
    with _phone_lock:
        _pendings[uid] = pending


def _live_pending(uid: str) -> Optional[Dict]:
    with _phone_lock:
        return _pendings.get(uid)


def _drop_pending(uid: str):
    with _phone_lock:
        _pendings.pop(uid, None)


def _result(tag: str, title: str, body: str,
            href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body,
             "href": href}]


def _steps_block(steps: List[str]) -> str:
    return "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps))


# --- Facts: the phone named in the message -------------------------------------------

_IPHONE_RE = re.compile(r"\biphone\b|\bios\b|\bipad\b", re.I)
_ANDROID_RE = re.compile(
    r"\bandroid\b|\bsamsung\b|\bgalaxy\b|\bpixel\b", re.I)


def _device_of(text: str) -> Optional[str]:
    if _IPHONE_RE.search(text):
        return "iphone"
    if _ANDROID_RE.search(text):
        return "android"
    return None


def _device_label(device: str) -> str:
    return "iPhone" if device == "iphone" else "Android"


# --- Settings walks (exact tap paths, one action per step) ----------------------------

# Each entry: label, trigger regex, the iPhone steps,
# the Android steps, and an optional note for the OS
# whose path varies by brand.
_SETTINGS = [
    {"key": "hotspot", "label": "Personal Hotspot",
     "pat": re.compile(r"\bhotspot\b", re.I),
     "ios": ["Open Settings.",
             "Tap Personal Hotspot.",
             "Turn on Allow Others to Join.",
             "The network name and password on that screen are what the other device joins with."],
     "android": ["Open Settings.",
                 "Tap Connections.",
                 "Tap Mobile Hotspot and Tethering.",
                 "Tap Mobile Hotspot.",
                 "Turn it on at the top."],
     "android_note": "On a Pixel: Settings → Network & internet → Hotspot & tethering → Wi-Fi hotspot."},
    {"key": "wifi_calling", "label": "Wi-Fi Calling",
     "pat": re.compile(r"\bwifi\s*calling\b|\bwi-fi\s*calling\b", re.I),
     "ios": ["Open Settings.",
             "Tap Phone.",
             "Tap Wi-Fi Calling.",
             "Turn on Wi-Fi Calling on This iPhone."],
     "android": ["Open the Phone app.",
                 "Tap the three dots at the top.",
                 "Tap Settings.",
                 "Tap Calls.",
                 "Turn on Wi-Fi calling."],
     "android_note": "On a Samsung it's also at Settings → Connections → Wi-Fi Calling."},
    {"key": "dark_mode", "label": "Dark Mode",
     "pat": re.compile(r"\bdark\s*mode\b|\bdark\s*theme\b", re.I),
     "ios": ["Open Settings.",
             "Tap Display & Brightness.",
             "Tap Dark, under Appearance."],
     "android": ["Open Settings.",
                 "Tap Display.",
                 "Turn on Dark theme."]},
    {"key": "larger_text", "label": "Larger Text",
     "pat": re.compile(r"\bbigger\s*text\b|\blarger\s*text\b|\bfont\s*size\b|\btext\s*(?:too\s+)?small\b|\bmake\s+the\s+(?:words|text|letters)\s+bigger\b", re.I),
     "ios": ["Open Settings.",
             "Tap Accessibility.",
             "Tap Display & Text Size.",
             "Tap Larger Text.",
             "Drag the slider until it reads right."],
     "android": ["Open Settings.",
                 "Tap Accessibility.",
                 "Tap Text and display.",
                 "Tap Font size.",
                 "Drag the slider until it reads right."],
     "android_note": "Menu names vary a little by brand — you're hunting for Font size, under Accessibility or Display."},
    {"key": "captions", "label": "Captions",
     "pat": re.compile(r"\bcaptions?\b|\bsubtitles?\b", re.I),
     "ios": ["Open Settings.",
             "Tap Accessibility.",
             "Tap Subtitles & Captioning.",
             "Turn on Closed Captions + SDH."],
     "android": ["Open Settings.",
                 "Tap Accessibility.",
                 "Tap Caption preferences.",
                 "Turn on Show captions."]},
    {"key": "reduce_motion", "label": "Reduce Motion",
     "pat": re.compile(r"\breduce\s*motion\b|\bremove\s*animations?\b|\bscreen\s*(?:moving|movement)\b", re.I),
     "ios": ["Open Settings.",
             "Tap Accessibility.",
             "Tap Motion.",
             "Turn on Reduce Motion."],
     "android": ["Open Settings.",
                 "Tap Accessibility.",
                 "Tap Visibility enhancements.",
                 "Turn on Remove animations."],
     "android_note": "On a Pixel: Settings → Accessibility → Text and display → Remove animations."},
    {"key": "battery_health", "label": "Battery Health",
     "pat": re.compile(r"\bbattery\s*health\b|\bmaximum\s*capacity\b|\bhealth\s*of\s*(?:my\s*)?battery\b", re.I),
     "ios": ["Open Settings.",
             "Tap Battery.",
             "Tap Battery Health & Charging.",
             "Read Maximum Capacity — that's your battery's health as a percent. Under 80 means the battery is worn."],
     "android": ["Open Settings.",
                 "Tap Battery.",
                 "Tap Battery health, if your phone shows it."],
     "android_note": "Many Androids don't show a health percent at all — no number there isn't a fault, the number only exists on some models."},
    {"key": "battery_usage", "label": "What's Using the Battery",
     "pat": re.compile(r"\bwhat'?s\s*(?:using|eating|draining)\s*(?:my\s*)?(?:the\s*)?battery\b|\bbattery\s*usage\b|\bwhat\s*(?:is|apps?)\s*(?:using|eating)\s*(?:my\s*)?battery\b", re.I),
     "ios": ["Open Settings.",
             "Tap Battery.",
             "Scroll down — the list under the graph is every app and how much battery it used.",
             "The app at the top is the biggest drain. That's off your own phone, not a guess."],
     "android": ["Open Settings.",
                 "Tap Battery.",
                 "Tap Battery usage.",
                 "The app at the top of the list is the biggest drain. That's off your own phone, not a guess."]},
    {"key": "storage_check", "label": "What's Using the Storage",
     "pat": re.compile(r"\bwhat'?s\s*(?:using|eating|taking\s*up)\s*(?:my\s*)?(?:the\s*)?(?:storage|space)\b|\bstorage\s*(?:usage|screen|left)\b|\bhow\s*much\s*(?:storage|space)\s*(?:do\s*i\s*have|is\s*left|left)\b", re.I),
     "ios": ["Open Settings.",
             "Tap General.",
             "Tap iPhone Storage.",
             "The bar at the top shows what's eating the space, biggest first."],
     "android": ["Open Settings.",
                 "Tap Storage.",
                 "The list shows what's eating the space, biggest first."],
     "android_note": "On a Samsung: Settings → Battery and device care → Storage."},
    {"key": "data_usage", "label": "What's Using the Data",
     "pat": re.compile(r"\bwhat'?s\s*(?:using|eating)\s*(?:my\s*)?(?:the\s*)?data\b|\bdata\s*usage\b|\bcellular\s*usage\b", re.I),
     "ios": ["Open Settings.",
             "Tap Cellular (it may say Mobile Data).",
             "Scroll down — every app and its data use is listed, biggest first."],
     "android": ["Open Settings.",
                 "Tap Network & internet.",
                 "Tap SIMs.",
                 "Tap App data usage.",
                 "The app at the top used the most data."]},
    {"key": "software_update", "label": "Software Update",
     "pat": re.compile(r"\bsoftware\s*update\b|\bupdate\s*(?:my\s*)?(?:phone|iphone|android|the\s*(?:os|system))\b|\bsystem\s*update\b|\bupdate\s*(?:ios|android)\b", re.I),
     "ios": ["Open Settings.",
             "Tap General.",
             "Tap Software Update.",
             "If an update is listed, tap Download and Install — keep the phone plugged in while it runs."],
     "android": ["Open Settings.",
                 "Tap Software update.",
                 "Tap Download and install — keep the phone plugged in while it runs."],
     "android_note": "On a Pixel: Settings → System → System update."},
    {"key": "dnd", "label": "Do Not Disturb",
     "pat": re.compile(r"\bdo\s*not\s*disturb\b|\bfocus\s*mode\b", re.I),
     "ios": ["Open Settings.",
             "Tap Focus.",
             "Tap Do Not Disturb.",
             "Turn it on, or set the schedule on that screen."],
     "android": ["Open Settings.",
                 "Tap Notifications.",
                 "Tap Do Not Disturb.",
                 "Turn it on, or set the schedule on that screen."]},
    {"key": "location_services", "label": "Location Services",
     "pat": re.compile(r"\blocation\s*services\b|\blocation\s*settings\b|\bturn\s*(?:on|off)\s*(?:my\s*)?location\b|\bgps\s*settings\b", re.I),
     "ios": ["Open Settings.",
             "Tap Privacy & Security.",
             "Tap Location Services, at the top.",
             "The switch at the top is the whole phone's; each app's own setting is listed under it."],
     "android": ["Open Settings.",
                 "Tap Location.",
                 "The switch at the top is the whole phone's; app permissions sit under it."],
     "note": "This is the PHONE's own location switch — different from OG's location permission for directions, which lives in OG's Permissions folder."},
    {"key": "bluetooth", "label": "Bluetooth Pairing",
     "pat": re.compile(r"\bbluetooth\b|\bpair\b[^.?!]*\b(?:headphones|earbuds|speaker|watch|device)\b", re.I),
     "ios": ["Open Settings.",
             "Tap Bluetooth.",
             "Turn it on.",
             "Put the other device in pairing mode, then tap its name when it appears on this screen."],
     "android": ["Open Settings.",
                 "Tap Connections.",
                 "Tap Bluetooth.",
                 "Turn it on.",
                 "Put the other device in pairing mode, then tap its name when it appears on this screen."],
     "android_note": "On a Pixel: Settings → Connected devices → Pair new device."},
    {"key": "airplane", "label": "Airplane Mode",
     "pat": re.compile(r"\bairplane\s*mode\b|\bflight\s*mode\b", re.I),
     "ios": ["Swipe down from the very top of the screen.",
             "Tap the airplane icon — orange means it's ON."],
     "android": ["Swipe down twice from the very top of the screen.",
                 "Tap the airplane icon — lit means it's ON."]},
    {"key": "app_notifications", "label": "App Notifications",
     "pat": re.compile(r"\bturn\s*off\s+notifications?\b|\bstop\s+notifications?\b|\bapp\s*notifications?\b|\bnotifications?\s*for\s+an?\s+app\b", re.I),
     "ios": ["Open Settings.",
             "Tap Notifications.",
             "Tap the app's name.",
             "Turn off Allow Notifications."],
     "android": ["Open Settings.",
                 "Tap Notifications.",
                 "Tap App notifications.",
                 "Tap the app's name and turn it off."]},
    {"key": "screen_timeout", "label": "Screen Timeout",
     "pat": re.compile(r"\bscreen\s*(?:timeout|time\s*out|turns\s*off\s*too\s*(?:fast|quick|soon))\b|\bauto-?lock\b|\bscreen\s*goes\s*(?:dark|black|off)\s*too\b", re.I),
     "ios": ["Open Settings.",
             "Tap Display & Brightness.",
             "Tap Auto-Lock.",
             "Pick how long — 2 minutes is a sane pick."],
     "android": ["Open Settings.",
                 "Tap Display.",
                 "Tap Screen timeout.",
                 "Pick how long — 2 minutes is a sane pick."]},
    {"key": "find_my", "label": "Find My Phone",
     "pat": re.compile(r"\bfind\s*my\s*(?:iphone|phone|device)\b|\blocate\s*my\s*phone\b|\bfind-my\b", re.I),
     "ios": ["Open Settings.",
             "Tap your name, at the very top.",
             "Tap Find My.",
             "Tap Find My iPhone and turn it on."],
     "android": ["Open Settings.",
                 "Tap Security.",
                 "Tap Find My Device.",
                 "Turn it on."],
     "android_note": "On a Pixel: Settings → Security & privacy → Device finders → Find My Device."},
    {"key": "passcode", "label": "Passcode / Screen Lock",
     "pat": re.compile(r"\bchange\s*(?:my\s*)?(?:passcode|pin|screen\s*lock|lock\s*code)\b|\bpasscode\b|\bscreen\s*lock\b", re.I),
     "ios": ["Open Settings.",
             "Tap Face ID & Passcode.",
             "Type your current passcode.",
             "Tap Change Passcode and follow its steps."],
     "android": ["Open Settings.",
                 "Tap Security.",
                 "Tap Screen lock.",
                 "Type your current PIN, then pick the new one."],
     "android_note": "On a Samsung: Settings → Lock screen → Screen lock type."},
]

_SETTINGS_BY_KEY = {s["key"]: s for s in _SETTINGS}


# --- Troubleshooting walks (ONE best first check, then the next) ----------------------

_WALKS = [
    {"key": "battery_drain", "label": "Battery draining fast",
     "pat": re.compile(r"\bbattery\b[^.?!]*\b(?:drain|drains|draining|dies|dying|dead\s*fast|fast)\b|\bdrains?\s*(?:too\s*)?fast\b|\bbattery\s*(?:won'?t\s*last|doesn'?t\s*last)\b", re.I),
     "steps": [
         {"check": "Open the battery usage list — Settings → Battery — and read the app at the very top of the list.",
          "means": "That top app is your biggest drain, off your own phone's records. If one app sits way ahead of the rest, that's the culprit: close it, and check it for an update. If the list looks even, say **still** and we keep going."},
         {"check": "Restart the phone — full power off, wait ten seconds, power back on.",
          "means": "A process stuck running in the background dies with a restart and usually stays dead. Give it an hour of normal use; if it's draining at the same speed, say **still**."},
         {"check": "Read the battery health percent — iPhone: Settings → Battery → Battery Health & Charging. (Many Androids don't show one; skip ahead if yours doesn't.)",
          "means": "Under 80% means the battery itself is worn out — no setting fixes a worn battery, a replacement does. 80 or better with a still-fast drain: run the software update (Settings → General → Software Update / Settings → Software update), and if it STILL drains fast after that, it's a shop job — said plainly. A phone that drains fast AND gets hot goes to the shop, not another setting."}]},
    {"key": "storage_full", "label": "Storage full",
     "pat": re.compile(r"\bstorage\s*(?:is\s*)?full\b|\bout\s*of\s*(?:storage|space)\b|\bno\s*(?:more\s*)?(?:storage|space)\s*(?:left)?\b|\bphone\s*(?:says\s*)?(?:it'?s\s*)?full\b", re.I),
     "steps": [
         {"check": "Open the storage screen — iPhone: Settings → General → iPhone Storage. Android: Settings → Storage — and read the biggest item at the top.",
          "means": "That biggest item is where the room went. Photos and videos are the usual answer; apps you forgot are second. Tell me what's on top, or say **still** to keep cutting room."},
         {"check": "Clear the biggest pile first: in Photos, delete the big videos you don't need, then empty the recently-deleted album (that's a second step — deleted photos hold their space until that album is emptied).",
          "means": "Your taps do the deleting — I can't delete anything off your phone, and nothing should. If the pile was apps instead, say **still**."},
         {"check": "iPhone: back on the iPhone Storage screen, turn on Offload Unused Apps — it removes apps you never open and keeps their data. Android: uninstall the apps you don't recognize using; check the storage screen again after.",
          "means": "If the screen still reads full after all that, the honest rest is: back the phone up, then a shop can check for a storage fault. A phone that says full on an empty store has a fault, not a settings problem."}]},
    {"key": "no_signal", "label": "No signal / no service",
     "pat": re.compile(r"\bno\s*(?:signal|service|bars)\b|\bcan'?t\s*(?:get|make)\s*(?:a\s*)?(?:signal|calls?)\b|\bcalls?\s*(?:won'?t|not)\s*(?:go\s*through|work)\b|\bsos\s*only\b", re.I),
     "steps": [
         {"check": "Check Airplane mode is OFF (swipe down from the top — the airplane icon should be dark), then look: any bars at all?",
          "means": "Airplane mode left on is the most common cause and the easiest fix. Bars showing but calls failing is a different animal — say **still** either way it lands."},
         {"check": "Restart the phone — full power off, ten seconds, back on — and give it a full minute to find the network.",
          "means": "A restart forces a fresh network registration, which clears most signal ghosts. Still nothing after a minute: say **still**."},
         {"check": "Power OFF, then pop the SIM tray (the pinhole on the side — a paperclip works), slide the SIM out, slide it back in firm, and power on.",
          "means": "A SIM that's crept loose reads exactly like a dead network, and reseating it costs nothing. If it still shows no service after that, the remaining suspects are your carrier (an outage or an account hold — their app or a call from another phone answers it) and the phone's antenna, which is a shop job. One warning before anyone suggests it: a network-settings reset is LAST — it wipes every saved Wi-Fi password with it."}]},
    {"key": "app_crash", "label": "An app keeps crashing",
     "pat": re.compile(r"\bapp\b[^.?!]*\b(?:crash|crashes|crashing|keeps\s*closing|closes|won'?t\s*(?:open|stay\s*open))\b|\bkeeps\s*crashing\b|\bapp\s*won'?t\s*open\b", re.I),
     "steps": [
         {"check": "Update the app: App Store → your picture at the top → pull down to refresh, update the app if it's listed. (Android: Play Store → your picture → Manage apps & device → Update.)",
          "means": "A crash right after an OS update is usually an app that hasn't caught up; its update is the fix. Still crashing on the newest version: say **still**."},
         {"check": "Restart the phone, then open the app once more.",
          "means": "If it survives the restart, something it was holding in memory was corrupt and it's gone now. Still dying: say **still**."},
         {"check": "Reinstall it. iPhone: press and hold the app → Remove App → Offload App keeps its data; Delete App wipes it — read which one the button says before you tap. Android: Settings → Apps → the app → Uninstall, then reinstall from the Play Store.",
          "means": "Offload/reinstall fixes a corrupt install. One honest warning: an app that doesn't sync its data to an account loses that data on a full delete — if this app holds something precious, back it up first (ask me for the backup walk). Crashing after a clean reinstall, on an updated OS, is the app maker's bug — their support page, not your phone."}]},
    {"key": "no_charge", "label": "Phone won't charge",
     "pat": re.compile(r"\bwon'?t\s*charge\b|\bnot\s*charging\b|\bcharge[rs]?\s*(?:won'?t|doesn'?t|isn'?t)\b|\bcharging\s*(?:problem|issue)\b", re.I),
     "safety": "SAFETY FIRST: if the battery is swollen — the screen lifting, the back bulging — or the phone is too hot to hold, STOP. Don't charge it, don't press on it. That's a fire risk and a shop job today, not a walk-through.",
     "steps": [
         {"check": "Swap ONE piece at a time: first try a different cable with the same brick, then the same cable with a different brick (or a computer's USB port).",
          "means": "Cables die far more often than phones do, and one-at-a-time tells you WHICH piece died. Charging on any combo names the dead piece. Nothing works: say **still**."},
         {"check": "Clean the charging port: power the phone OFF, then work a soft dry brush or a wooden toothpick gently around inside the port — pocket lint packs in there. Nothing metal, nothing wet.",
          "means": "A lint plug stops the cable seating fully — the classic 'charges at an angle' fault. Try the known-good cable again after. Still nothing: say **still**."},
         {"check": "If the phone does wireless charging, set it on a pad.",
          "means": "Charges wirelessly = the battery and board are fine and the PORT is the fault — a shop can replace or clean it properly. Charges nowhere at all = battery or board, also a shop job. Said plainly: past this check there's no setting that fixes a dead charge path."}]},
    {"key": "frozen", "label": "Phone frozen / unresponsive",
     "pat": re.compile(r"\bfrozen\b|\bfroze\b|\bunresponsive\b|\bscreen\s*(?:won'?t|doesn'?t|isn'?t)\s*respond|\bstuck\s*screen\b|\bwon'?t\s*respond\b|\btouchscreen\s*(?:won'?t|not)\b", re.I),
     "steps": [
         {"check": "Force-restart it. iPhone: tap Volume Up, tap Volume Down, then HOLD the side button until the Apple logo shows (about 10 seconds — keep holding through the slider). Samsung: hold Power + Volume Down together for 10 seconds. Pixel: hold Power alone for 30 seconds.",
          "means": "A force restart cuts power to a hung system without wiping anything — your photos and apps are untouched. If it boots: watch what app was on screen when it froze; that app is the suspect. Won't boot at all: say **still**."},
         {"check": "Charge it for 30 minutes on a known-good cable and brick, then try the force restart once more.",
          "means": "A battery at true zero can look exactly like a dead phone and needs the half hour before it will answer. Still black and silent after that: it's hardware — a shop job, said plainly. Don't keep forcing it."}]},
    {"key": "stuck_update", "label": "Update stuck",
     "pat": re.compile(r"\bupdate\b[^.?!]*\b(?:stuck|frozen|won'?t\s*(?:finish|install|complete)|hanging|hours)\b|\bstuck\s*(?:on\s*)?(?:an?\s*)?update\b|\bupdate\s*won'?t\s*(?:finish|install)\b", re.I),
     "steps": [
         {"check": "Check the three things an update needs: on Wi-Fi, plugged in or over 50% battery, and at least ~5 GB free (storage screen). Fix whichever one's missing, then give it 15 minutes.",
          "means": "Most 'stuck' updates are quietly waiting on one of those three. Progress after: let it finish plugged in. Still frozen after 15 minutes with all three right: say **still**."},
         {"check": "Restart the phone, then open the update screen again and let it resume.",
          "means": "The download resumes where it left off — nothing is lost by the restart. Resumes and finishes: done. Freezes at the same spot again: say **still**."},
         {"check": "Delete the downloaded update and pull it fresh. iPhone: Settings → General → iPhone Storage → tap the update in the list → Delete Update, then back to Software Update and download again. Android: restart into the update screen and retry; if your brand offers 'clear cache' for the updater, that's its version of this step.",
          "means": "A corrupt download freezes at the same byte every time; a fresh one doesn't. If a FRESH download also dies at the same spot, stop retrying — that's a shop or carrier-store visit, and more retries won't change it."}]},
]

_WALKS_BY_KEY = {w["key"]: w for w in _WALKS}


# --- Guides (backups + moving to a new phone) ----------------------------------------

_GUIDES = [
    {"key": "backup", "label": "Back up the phone",
     "pat": re.compile(r"\bback\s*(?:up|ing)\b[^.?!]*\b(?:phone|iphone|android|my\s*(?:stuff|photos|data))\b|\bbackup\b[^.?!]*\bphone\b|\bback\s*up\s*my\b", re.I),
     "ios": ["Get on Wi-Fi and plug the phone in.",
             "Open Settings.",
             "Tap your name, at the very top.",
             "Tap iCloud.",
             "Tap iCloud Backup.",
             "Tap Back Up Now.",
             "Stay on Wi-Fi until it finishes — the screen shows the time when it's done. That time is your proof it worked."],
     "android": ["Get on Wi-Fi and plug the phone in.",
                 "Open Settings.",
                 "Tap Google.",
                 "Tap Backup.",
                 "Tap Back up now.",
                 "Wait for it to finish — the screen shows the time of the last backup. That time is your proof it worked."],
     "android_note": "On a Samsung: Settings → Accounts and backup → Back up data (under Google Drive or Samsung Cloud)."},
    {"key": "new_phone", "label": "Move to a new phone",
     "pat": re.compile(r"\bnew\s*(?:phone|iphone|android)\b[^.?!]*\b(?:move|transfer|switch|set\s*up|setup|everything|my\s*stuff)\b|\b(?:move|transfer|switch)\b[^.?!]*\bnew\s*(?:phone|iphone|android)\b|\bset\s*up\s*my\s*new\s*(?:phone|iphone|android)\b", re.I),
     "ios": ["Back up the OLD phone first (ask me for the backup walk if you haven't — do not skip this).",
             "Turn the NEW iPhone on and put it beside the old one.",
             "The old phone pops up a Quick Start card — tap Continue.",
             "Point the old phone's camera at the swirl on the new phone's screen.",
             "Follow the new phone's steps: passcode, Face ID, then 'Transfer from iPhone'.",
             "Keep both phones plugged in and together until the bar finishes — a big phone can take an hour."],
     "android": ["Back up the OLD phone first (ask me for the backup walk if you haven't — do not skip this).",
                 "Turn the NEW phone on and tap Start.",
                 "When it asks, say yes to copying apps and data.",
                 "Samsung-to-Samsung: open Smart Switch on both phones and follow its steps. Any other pair: connect both with the cable the new phone suggests (or the same Wi-Fi) and follow the copy steps.",
                 "Sign in on the new phone, then check the things that matter BEFORE wiping anything: photos, messages, contacts, and your two-step codes app.",
                 "Only wipe the old phone once the new one provably has everything — a factory reset is forever."],
     "note": "Two-step codes (authenticator apps) are the classic thing that doesn't move by itself — check yours on the new phone before the old one is wiped."},
]

_GUIDES_BY_KEY = {g["key"]: g for g in _GUIDES}


# --- The live surfaces (Round 90's store + Round 73's switch — READ, never copied) -----


def _permissions_live(uid: str):
    """The caller's permission rows, read off
    og_permissions at answer time. None when the read
    itself fails — the seat then says so, it does
    NOT answer from a copy."""
    try:
        import og_permissions
        rows = og_permissions.permissions_for(uid)
        return rows if isinstance(rows, list) else None
    except Exception as e:
        logger.warning(f"Phone perms read failed: {e}")
        return None


def _location_live(uid: str):
    """Round 73's switch, read live. None on a
    failed read."""
    try:
        import og_gps
        return bool(og_gps.location_on_for(uid))
    except Exception as e:
        logger.warning(f"Phone location read failed: {e}")
        return None


# --- Operations ------------------------------------------------------------------------


def _note_for(entry: Dict, device: str) -> str:
    note = entry.get("note") or ""
    if device == "android":
        note = entry.get("android_note") or note
    return ("\n" + note) if note else ""


def _do_settings(uid: str, claim: Dict) -> List[Dict]:
    entry = _SETTINGS_BY_KEY[claim["setting"]]
    device = claim.get("device") or _bench(uid).get("device")
    bench = _bench(uid)
    if claim.get("device"):
        bench["device"] = claim["device"]
        bench["last"] = entry["label"]
        _save_bench(uid, bench)
    if not device:
        _park_pending(uid, {"await_os_for": entry["key"]})
        return _result(
            _TAG, f"Phone — {entry['label']}",
            f"Quick one first: is it an **iPhone** or an "
            f"**Android**? The tap path is different on "
            f"each, and I won't guess at yours. Say the "
            f"word and I'll give you the exact steps for "
            f"{entry['label']}.")
    steps = entry["ios"] if device == "iphone" else entry["android"]
    return _result(
        _TAG, f"Phone — {entry['label']} ({_device_label(device)})",
        f"On your {_device_label(device)}, for "
        f"**{entry['label']}** — one tap at a time:\n"
        + _steps_block(steps) + _note_for(entry, device))


def _walk_intro(uid: str, walk: Dict) -> str:
    bench = _bench(uid)
    bench["walk"] = walk["key"]
    bench["step"] = 0
    bench["last"] = walk["label"]
    _save_bench(uid, bench)
    step = walk["steps"][0]
    safety = (walk.get("safety") + "\n\n") if walk.get("safety") else ""
    return (f"**{walk['label']}** — we take it one check "
            f"at a time, likeliest first.\n\n{safety}"
            f"FIRST CHECK: {step['check']}\n\n{step['means']}\n\n"
            f"Do that check and tell me: say **still** if "
            f"it's still doing it, or **fixed** if that got it.")


def _do_walk_start(uid: str, claim: Dict) -> List[Dict]:
    walk = _WALKS_BY_KEY[claim["walk"]]
    if claim.get("device"):
        bench = _bench(uid)
        bench["device"] = claim["device"]
        _save_bench(uid, bench)
    return _result(_TAG, f"Phone — {walk['label']}",
                   _walk_intro(uid, walk))


def _do_walk_next(uid: str, claim: Dict) -> List[Dict]:
    bench = _bench(uid)
    walk = _WALKS_BY_KEY.get(bench.get("walk") or "")
    if walk is None:
        return _result(
            _TAG, "Phone — no walk running",
            "There's no phone check running on your bench "
            "right now, so there's nothing to advance. Tell "
            "me the symptom — battery draining, no signal, "
            "an app that keeps crashing — and we'll start "
            "at the first check.")
    if claim["op"] == "ph_fixed":
        bench["walk"] = None
        bench["step"] = 0
        _save_bench(uid, bench)
        return _result(
            _TAG, f"Phone — {walk['label']}: fixed",
            f"Good — **{walk['label']}** is closed out. If "
            f"it creeps back, we pick the walk up at the "
            f"same first check; a problem that returns "
            f"after this fix is telling us something new.")
    nxt = int(bench.get("step") or 0) + 1
    if nxt >= len(walk["steps"]):
        bench["walk"] = None
        bench["step"] = 0
        _save_bench(uid, bench)
        last = walk["steps"][-1]
        return _result(
            _TAG, f"Phone — {walk['label']}: the verdict",
            f"That's the end of the walk — every check "
            f"landed and it's still doing it. Where the "
            f"last check left it stands: {last['means']}\n\n"
            f"The walk is closed. If a shop finds the "
            f"cause, telling me what it was helps the next "
            f"person with the same symptom — and if the "
            f"symptom changes shape, say the word and we "
            f"start fresh.")
    bench["step"] = nxt
    _save_bench(uid, bench)
    step = walk["steps"][nxt]
    return _result(
        _TAG, f"Phone — {walk['label']} (check {nxt + 1})",
        f"Ruled out. Next — CHECK {nxt + 1} of "
        f"{len(walk['steps'])}:\n\n{step['check']}\n\n"
        f"{step['means']}\n\nSay **still** if it keeps "
        f"doing it, or **fixed** if that got it.")


def _do_perms(uid: str, claim: Dict) -> List[Dict]:
    rows = _permissions_live(uid)
    if rows is None:
        return _result(
            _TAG, "Phone — OG's permissions (read failed)",
            "I tried to read OG's permission list off the "
            "folder just now and the read failed — so I "
            "won't recite states I couldn't check. The "
            "list itself lives in the menu, in the "
            "Permissions folder, and it always paints the "
            "real answer.")
    if claim.get("cap") == "gps":
        loc = _location_live(uid)
        state = ("ON" if loc else "OFF") if loc is not None \
            else "unreadable just now"
        return _result(
            _TAG, "Phone — OG's location switch",
            f"OG's location switch for your account reads "
            f"**{state}** — read live off the switch just "
            f"now, not from memory. It's the same switch "
            f"the Permissions folder shows on its GPS row, "
            f"and the folder (or asking OG to flip it in "
            f"chat) is how it changes — it stays how you "
            f"leave it until you move it.")
    lines = ["Here's what OG can use on your phone — read "
             "live off the Permissions folder just now, not "
             "from memory:"]
    for r in rows:
        lines.append(f"• {r['name']} — "
                     f"{'ON' if r['on'] else 'OFF'}")
    lines.append("\nThat list is the ONE source — the "
                 "folder, chat, and this answer all read "
                 "the same list. To change any of it, open "
                 "the Permissions folder from the menu; "
                 "flipping a row there saves to this same "
                 "list. Two honest notes: Health, Apple "
                 "Watch, and CarPlay are permissions held "
                 "for the phone build — OG holds the "
                 "permission now, the capability arrives "
                 "with that build — and OG's location row "
                 "there is the account location switch "
                 "itself, not a copy.")
    bench = _bench(uid)
    bench["last"] = "OG's phone permissions"
    _save_bench(uid, bench)
    return _result(_TAG, "Phone — OG's permissions on your phone",
                   "\n".join(lines))


def _do_remote(uid: str, claim: Dict) -> List[Dict]:
    """THE FENCE, spoken first — then the guided way
    to do the very thing asked, when it's parseable."""
    head = ("Plain line first: **OG cannot remote-control "
            "your phone** — I can't reach into it, tap "
            "things on it, or change a setting on it from "
            "here. Not this one, not any. What I CAN do is "
            "guide you to it exactly, one tap at a time — "
            "you tap, it changes, same result.\n")
    inner = claim.get("inner")
    if inner:
        if inner["op"] == "ph_settings":
            res = _do_settings(uid, inner)
            return _result(
                _TAG, res[0]["title"],
                head + "\n" + res[0]["body"].split("] ", 1)[-1])
        if inner["op"] == "ph_walk":
            res = _do_walk_start(uid, inner)
            return _result(
                _TAG, res[0]["title"],
                head + "\n" + res[0]["body"].split("] ", 1)[-1])
        if inner["op"] == "ph_guide":
            res = _do_guide(uid, inner)
            return _result(
                _TAG, res[0]["title"],
                head + "\n" + res[0]["body"].split("] ", 1)[-1])
    return _result(
        _TAG, "Phone — the honest limit",
        head + "\nTell me what you're trying to change "
        "or fix on the phone — a setting, a symptom, a "
        "backup — and I'll walk you to it step by step.")


def _do_guide(uid: str, claim: Dict) -> List[Dict]:
    entry = _GUIDES_BY_KEY[claim["guide"]]
    device = claim.get("device") or _bench(uid).get("device")
    bench = _bench(uid)
    if claim.get("device"):
        bench["device"] = claim["device"]
        bench["last"] = entry["label"]
        _save_bench(uid, bench)
    if not device:
        _park_pending(uid, {"await_os_for_guide": entry["key"]})
        return _result(
            _TAG, f"Phone — {entry['label']}",
            f"Quick one first: is it an **iPhone** or an "
            f"**Android**? The steps differ, and I won't "
            f"guess at yours. Say the word and I'll lay "
            f"out {entry['label']} for your phone.")
    steps = entry["ios"] if device == "iphone" else entry["android"]
    return _result(
        _TAG, f"Phone — {entry['label']} ({_device_label(device)})",
        f"**{entry['label']}** on your "
        f"{_device_label(device)} — one step at a time:\n"
        + _steps_block(steps) + _note_for(entry, device))


def _do_status(uid: str, claim: Dict) -> List[Dict]:
    bench = _bench(uid)
    rows = _permissions_live(uid)
    lines = ["The Phone Specialist's bench — your phone "
             "help, in chat. I guide and you tap; OG "
             "cannot remote-control your phone, and "
             "everything I state about OG's access I read "
             "off the real surfaces at the moment."]
    device = bench.get("device")
    lines.append(f"Your phone on record: "
                 f"**{_device_label(device)}**."
                 if device else
                 "Your phone isn't on record yet — name it "
                 "once (iPhone or Android) on any walk and "
                 "I'll remember it here.")
    walk = _WALKS_BY_KEY.get(bench.get("walk") or "")
    if walk:
        lines.append(f"A check is OPEN: **{walk['label']}**, "
                     f"at check "
                     f"{int(bench.get('step') or 0) + 1} of "
                     f"{len(walk['steps'])}. Say **still** "
                     f"or **fixed** to move it.")
    if rows is not None:
        on = sum(1 for r in rows if r["on"])
        lines.append(f"OG's phone permissions, read live "
                     f"just now: **{on} of {len(rows)} on**. "
                     f"Ask 'what can OG use on my phone' for "
                     f"the full list.")
    else:
        lines.append("OG's permission list couldn't be "
                     "read just now — the Permissions folder "
                     "in the menu always paints the real list.")
    if bench.get("last"):
        lines.append(f"Last thing we worked: "
                     f"**{bench['last']}**.")
    lines.append("Ask me: 'how do I turn on the hotspot', "
                 "'my battery drains fast', 'what's using "
                 "my storage', 'back up my phone', or 'what "
                 "can OG use on my phone'.")
    return _result(_TAG, "Phone — status", "\n".join(lines))


def phone_results(claim: Dict, uid: str) -> List[Dict]:
    op = claim.get("op")
    if op == "ph_settings":
        return _do_settings(uid, claim)
    if op == "ph_walk":
        return _do_walk_start(uid, claim)
    if op in ("ph_still", "ph_fixed"):
        return _do_walk_next(uid, claim)
    if op == "ph_perms":
        return _do_perms(uid, claim)
    if op == "ph_remote":
        return _do_remote(uid, claim)
    if op == "ph_guide":
        return _do_guide(uid, claim)
    if op == "ph_status":
        return _do_status(uid, claim)
    return _result(_TAG, "Phone — glitch",
                   "That phone ask glitched on my end — "
                   "nothing was changed anywhere. Say it "
                   "again and I'll retry.")


def status_for(uid: str) -> Dict:
    if not uid:
        return {"bench": None}
    bench = _bench(uid)
    if not bench:
        return {"bench": None}
    return {"bench": {
        "device": bench.get("device"),
        "walk": bench.get("walk"),
        "step": bench.get("step"),
        "last": bench.get("last")}}


# --- Chat claims (narrow frames — the crew/PM/filer doctrine) --------------------------

_GARBLE_VOCAB = ("phone", "iphone", "android", "settings",
                 "battery", "storage", "signal", "hotspot",
                 "update", "backup", "screen", "samsung",
                 "pixel", "service", "charge", "charging",
                 "location", "permissions", "airplane",
                 "bluetooth", "captions", "passcode")

_STATUS_RE = re.compile(
    r"\bphone\s*(?:status|help|specialist)\b|"
    r"\bhelp\s*(?:me\s*)?with\s*my\s*phone\b|"
    r"\bphone\s*helper\b", re.I)
_PERMS_RE = re.compile(
    r"\bwhat\b[^.?!]*\bpermissions?\b[^.?!]*\bog\b|"
    r"\bog'?s?\s*permissions?\b|"
    r"\bpermissions?\b[^.?!]*\bdoes\s*og\b|"
    r"\bcan\s*og\s*(?:use|access|see|touch)\b|"
    r"\bdoes\s*og\s*have\s*access\b|"
    r"\bwhat\s*can\s*og\s*use\s*on\s*my\s*phone\b|"
    r"\bwhat\s*(?:does|can)\s*og\s*(?:use|access|see)\s*on\s*my\s*phone\b",
    re.I)
_LOC_RE = re.compile(
    r"\bis\s*(?:my\s*)?location\s*(?:on|off|turned\s*on)\b|"
    r"\bog'?s?\s*location\s*(?:switch|on|off)\b|"
    r"\blocation\s*(?:switch|permission)\b[^.?!]*\bog\b", re.I)
_REMOTE_RE = re.compile(
    r"\b(?:control|take\s*over|remote\s*(?:control|access|into)|"
    r"log\s*into|get\s*into)\b[^.?!]*\bmy\s*phone\b|"
    r"\bdo\s*it\s*(?:on|for)\s*(?:my\s*phone|me)\b[^.?!]*\bphone\b|"
    r"\bjust\s*do\s*it\s*for\s*me\b|"
    r"\b(?:fix|change|set\s*up|turn\s*(?:on|off))\b[^.?!]*"
    r"\bfor\s*me\b[^.?!]*\b(?:on\s*)?my\s*phone\b|"
    r"\bcan\s*you\b[^.?!]*\bmy\s*phone\s*for\s*me\b|"
    r"\bremote-?control\b", re.I)
_FRAME_RE = re.compile(
    r"\bhow\s*(?:do|can|to)\b|\bwhere\s*(?:do|is)\b|"
    r"\bturn\s*(?:on|off)\b|\benable\b|\bdisable\b|"
    r"\bset\s*up\b|\bsetup\b|\bchange\b|\bfind\b|"
    r"\bshow\s*me\b|\bcheck\b|\bread\b|\blook\s*at\b", re.I)
_PHONE_ANCHOR_RE = re.compile(
    r"\bphone\b|\biphone\b|\bandroid\b|\bsamsung\b|"
    r"\bgalaxy\b|\bpixel\b|\bscreen\b|\bbattery\b", re.I)
_STILL_RE = re.compile(
    r"^\W*(?:still|nope|not\s*yet|didn'?t\s*work|"
    r"not\s*fixed|no\s*luck|same)\b", re.I)
_FIXED_RE = re.compile(
    r"^\W*(?:fixed|that\s*worked|worked|got\s*it|"
    r"it\s*works|working\s*now|solved)\b", re.I)
_OS_ANSWER_RE = re.compile(
    r"^\W*(?:it'?s\s+)?(?:an?\s+)?"
    r"(iphone|android|samsung|galaxy|pixel)\W*$", re.I)


def _other_claim(message: str, uid: str):
    """(source, claim) of the first inner seat that
    claims this message exactly, else None. og_github
    FIRST — its pendings, clarifying questions, and
    every repo-shaped ask are the R60 path's own. Then
    the PM, the Filer, the crew, the Builder, the
    Wood Pro, MaintEng, the Mechanic, the Blueprint
    Specialist, Shopping, Buying, Selling, Coding,
    the R54 marketplace chain, and the ordering pack:
    checked exactly, never executed from here."""
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


def _parse_topic(raw: str, device: Optional[str]) -> Optional[Dict]:
    """The settings / walk / guide topic a message
    carries, parsed bare (no claims discipline) — used
    by the remote-control fence to guide the very
    thing asked."""
    for entry in _SETTINGS:
        if entry["pat"].search(raw):
            return {"op": "ph_settings",
                    "setting": entry["key"], "device": device}
    for walk in _WALKS:
        if walk["pat"].search(raw):
            return {"op": "ph_walk", "walk": walk["key"],
                    "device": device}
    for guide in _GUIDES:
        if guide["pat"].search(raw):
            return {"op": "ph_guide", "guide": guide["key"],
                    "device": device}
    return None


def _claim_exact(message: str, uid: str) -> Optional[Dict]:
    raw = str(message or "").strip()
    if not raw or not uid:
        return None
    device = _device_of(raw)
    bench = _bench(uid)
    # A parked iPhone-or-Android question binds its
    # bare answer first — one word, nobody else's ask.
    pend = _live_pending(uid)
    if pend:
        m = _OS_ANSWER_RE.match(raw)
        if m:
            dev = "iphone" if m.group(1).lower() == "iphone" \
                else "android"
            _drop_pending(uid)
            if pend.get("await_os_for"):
                return {"op": "ph_settings",
                        "setting": pend["await_os_for"],
                        "device": dev}
            if pend.get("await_os_for_guide"):
                return {"op": "ph_guide",
                        "guide": pend["await_os_for_guide"],
                        "device": dev}
    # The seat's own walk follow-ups bind while a walk
    # is live — but never across another seat's claim
    # on the same words (their walks speak first).
    if bench.get("walk"):
        if _FIXED_RE.search(raw) and not _defer_to_others(raw, uid):
            return {"op": "ph_fixed", "raw": raw}
        if _STILL_RE.search(raw) and not _defer_to_others(raw, uid):
            return {"op": "ph_still", "raw": raw}
    # THE FENCE: an ask shaped like doing it ON the
    # user's phone for them is this seat's to answer —
    # the honest line first, then the guided steps.
    if _REMOTE_RE.search(raw) and _PHONE_ANCHOR_RE.search(raw):
        return {"op": "ph_remote", "raw": raw,
                "inner": _parse_topic(raw, device)}
    if _defer_to_others(raw, uid):
        return None
    if _LOC_RE.search(raw):
        return {"op": "ph_perms", "cap": "gps", "raw": raw}
    if _PERMS_RE.search(raw):
        return {"op": "ph_perms", "raw": raw}
    for entry in _SETTINGS:
        if entry["pat"].search(raw) and (
                _FRAME_RE.search(raw) or device
                or _PHONE_ANCHOR_RE.search(raw)):
            return {"op": "ph_settings",
                    "setting": entry["key"],
                    "device": device, "raw": raw}
    for walk in _WALKS:
        if walk["pat"].search(raw) and _PHONE_ANCHOR_RE.search(raw):
            return {"op": "ph_walk", "walk": walk["key"],
                    "device": device, "raw": raw}
    for guide in _GUIDES:
        if guide["pat"].search(raw):
            return {"op": "ph_guide", "guide": guide["key"],
                    "device": device, "raw": raw}
    if _STATUS_RE.search(raw):
        return {"op": "ph_status", "raw": raw}
    return None


def _claim_phone(message: str, uid: str):
    """(claim, garble_info): the exact claim first; on
    a miss, the Round 75 second chance — og_garble
    repairs the TRIGGER words only; the payload (the
    symptom words, the setting named) passes as the
    user wrote it for the answer itself."""
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


def install_phone_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search OUTSIDE the
    Coding seam (installed last, from the og_fixqueue
    chain) so phone-specialist asks claim here and
    everything else delegates back untouched. Persona
    files never touched."""
    if getattr(agent_instance, "_og_phone_installed",
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
            if uid and phone_enabled():
                claim, info = _claim_phone(
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
            logger.warning(f"Phone trigger check failed: {e}")
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
                results = phone_results(claim, uid)
            except Exception as e:
                logger.warning(f"Phone op failed: {e}")
                results = _result(
                    _TAG, "Phone — glitch",
                    "That phone ask glitched on my end — "
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
    agent_instance._og_phone_installed = True


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


def register_phone_routes(app):
    """GET /phone/status — the caller's own bench:
    the phone on record, any open check, and the
    last thing worked."""

    @app.get("/phone/status")
    async def phone_status_route(request: Request):
        uid = _route_uid(request)
        view = status_for(uid) if uid else {"bench": None}
        return {"ok": True, "enabled": phone_enabled(),
                **view}
