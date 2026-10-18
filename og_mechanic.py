"""Round 114: THE CAR MECHANIC SPECIALIST HELPER
(Brent's order, 2026-10-12 11:11 PDT: "Car Mechanic
specialist helper" — specced in round-114/SPEC.md.)

WHAT THIS IS. The crew's mechanic: vehicle trouble
diagnosed in chat, in OG's voice. Four crafts:

  - SYMPTOM WALKS with diagnosis discipline (the
    Round 113 pattern): describe the symptom (or the
    sound, in words) -> the ONE most likely cause +
    the check that proves it, in DRIVEWAY ORDER —
    free checks first, one check at a time. The user
    does the check and says "worked" or "still", and
    the walk moves, printing what each result rules
    out. Deterministic trees: no-start, battery keeps
    dying, brake noise, overheating, rough idle /
    stalling, car AC warm, vibration / pull, the
    check-engine light, and fluid leaks.
  - OBD CODES explained plain: what the code
    actually reports, the likely causes RANKED, the
    check that proves it, and whether it's a
    driveway job or a shop job. A code names the
    system that reported — not always the bad part —
    and the answer says so.
  - THE SHOP-JOB LINE, honest and early: work that
    needs a lift, a scan tool, a recovery machine,
    or a pro's hands — airbags, transmission
    internals, engine internals, high-voltage
    hybrid gear, refrigerant recovery — gets the
    plain line ("this one's a shop job") with the
    reason, what the shop does, and a cost-sanity
    range labeled ESTIMATE, instead of a walk that
    marches a user into a job they can't finish
    safely. Brakes-gone / fuel-leak / steering-loss
    answers are an EMERGENCY line first, always.
  - THE VEHICLE LIST + INTERVALS: the vehicles the
    user maintains in chat, per account ("my car is
    a 2019 Traverse", "what cars do you have for
    me", "forget the truck"), and maintenance
    intervals + fluid/part guidance for the named
    vehicle — planning numbers, labeled, with the
    owner's manual named as the final word on specs.

THE FENCES.
- Knowledge + guidance ONLY. The module imports no
  ordering/trading machinery (suite-pinned): an
  "order the parts" ask returns the open context's
  own parts sheet + the ordering pack's approval
  routing. Never buys, never orders.
- Cost-sanity ranges are labeled ESTIMATE wherever
  they ride — planning ranges, not quotes.
- Per-account isolation (drill-proven cross-account):
  walks, vehicles, everything. No account ever sees
  another account's vehicles.
- NO new loop (R72): nothing polls, nothing ticks;
  state is read on exchanges and on the status route.
- Persona untouched; app.py never touched (install
  rides the og_fixqueue chain AFTER the Maintenance
  + Engineer seat — this seam wraps outermost and
  defers to the PM/Filer/crew/Builder/WoodPro/
  MaintEng claims checked exactly first). Two
  shared-vocabulary exceptions, both requiring an
  explicit vehicle anchor in the ask: MaintEng's
  home-AC tree and its no-equipment schedule
  ask-back yield to this seat when the ask names a
  vehicle (car AC and vehicle intervals are this
  seat's SPEC lanes).

STATE. Own durable store, the Round 18 pattern:
Postgres table og_mechanic_data when OG_MEMORY_DB_URL
is set, else a JSON file. Blob kinds per visitor:
'diag' (the active walk) and 'vehicles'. Switch:
OG_MECHANIC_ENABLED (unset = ON), the SPEC's name.
"""

import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Dict, List, Optional

import og_garble

from fastapi import Request

logger = logging.getLogger(__name__)

_TAG = "OG CAR MECHANIC"

SHOP_LINE = (
    "Plain line: this one's a shop job — it needs a "
    "lift, a scan tool, or a pro's hands, and a "
    "mistake here can hurt somebody. I'll tell you "
    "exactly what the shop will do and what it "
    "should cost; I won't walk you into it.")

ESTIMATE_NOTE = (
    "ESTIMATE — a planning range, not a quote; "
    "prices move and your market differs.")

# --- Config -------------------------------------------------------------------

VEHICLE_KEEP = 6         # vehicles kept per visitor
DAY = 86400.0

MECHANIC_STORE_FILE = "mechanic_store.json"
# RLock, not Lock: store updates hold the lock across a
# read-modify-write whose save path re-enters it through
# _put_blob's file branch (the og_mainteng shape).
_mechanic_lock = threading.RLock()

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, StoreError, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo, pooled_connect as _pooled_connect
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None


# --- Switch ---------------------------------------------------------------------

def mechanic_enabled() -> bool:
    """OG_MECHANIC_ENABLED=false is the kill switch.
    Unset = ON."""
    val = os.environ.get("OG_MECHANIC_ENABLED")
    if val is not None and val.strip().lower() in (
            "0", "false", "no", "off"):
        return False
    return True


# --- Durable store (Round 18 pattern, mirroring og_mainteng) ----------------------

def _db_connect():
    conn = _pooled_connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_mechanic_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(MECHANIC_STORE_FILE):
        try:
            with open(MECHANIC_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Mechanic store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(MECHANIC_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Mechanic store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_mechanic_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"Mechanic DB load failed: {e}")
            raise StoreError("mechanic store unavailable (Round 99)") from e
    with _mechanic_lock:
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
                        "INSERT INTO og_mechanic_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Mechanic DB save failed: {e}")
            raise StoreError("mechanic store unavailable (Round 99)") from e
    with _mechanic_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


# --- State: the active diagnosis walk + the vehicle list -------------------------

def _diag(uid: str) -> Optional[Dict]:
    got = _blob(uid, "diag", None)
    if isinstance(got, dict) and got.get("status") == "active":
        return got
    return None


def _save_diag(uid: str, diag: Optional[Dict]):
    _put_blob(uid, "diag", diag if diag is not None else {})


def _vehicles(uid: str) -> List[Dict]:
    got = _blob(uid, "vehicles", [])
    return got if isinstance(got, list) else []


def _save_vehicles(uid: str, rows: List[Dict]):
    rows.sort(key=lambda v: float(v.get("added", 0) or 0))
    _put_blob(uid, "vehicles", rows[-VEHICLE_KEEP:])


def _vehicle_named(uid: str, text: str) -> Optional[Dict]:
    """The stored vehicle the ask names (token overlap),
    or the only vehicle on file when there's exactly
    one — the ask's own context, nothing invented."""
    rows = [v for v in _vehicles(uid) if isinstance(v, dict)]
    raw = str(text or "").lower()
    for v in rows:
        name = str(v.get("name", "")).lower()
        toks = [t for t in re.split(r"[^a-z0-9]+", name)
                if len(t) >= 3 and not t.isdigit()]
        if toks and any(t in raw for t in toks):
            return v
    if len(rows) == 1:
        return rows[0]
    return None


# --- Troubleshooting trees -------------------------------------------------------
# Each step: the ONE best check, what it means when that check
# FIXES it (fix), and what moving past it rules out (means).
# Driveway order: free checks first, money last. The walk
# renders one step at a time; "worked" closes with the fix
# line, "still" prints the ruled-out line and advances.

def _step(check, fix, means):
    return {"check": check, "fix": fix, "means": means}


TREES = [
    {
        "id": "no_start", "title": "won't start",
        "safety": [
            "Safety first: never crank it over and over in "
            "a closed garage — exhaust is carbon monoxide. "
            "Door open or car outside before repeated tries.",
        ],
        "steps": [
            _step(
                "Turn the key (or press start) and read what "
                "it does — this one look splits the whole "
                "tree. Dash lights on? And the sound: ONE "
                "heavy click, rapid clicking, a slow tired "
                "crank, a normal-speed crank that never "
                "catches, or total silence?",
                "You just named the branch: clicking = the "
                "battery/starter circuit can't deliver "
                "current; a healthy crank that won't catch "
                "= fuel or spark, a different hunt entirely "
                "(check 5). Everything below follows YOUR "
                "answer, not a guess.",
                "the symptom is named — now we prove which "
                "side of the fence it's on."),
            _step(
                "Battery terminals — eyes and hands, free. "
                "Pop the hood: white/green crust on the "
                "posts, a clamp you can twist by hand, or a "
                "cable that's frayed? Then the headlight "
                "test: lights on, try to start — do they "
                "stay bright, or dive?",
                "Crust or a loose clamp IS the failure more "
                "often than the battery is: the starter "
                "wants 200+ amps through that joint. Clean "
                "to bright metal (baking soda + water on the "
                "crust, battery disconnected, negative "
                "first off / last on), clamp it TIGHT, "
                "retry. Lights diving under crank = the "
                "battery can't deliver — that's check 3.",
                "the connections are clean and tight and "
                "the lights hold — the joint isn't the "
                "break."),
            _step(
                "The jump test — the great separator, still "
                "free. Jump it from a running car or a pack, "
                "let it sit connected 2–3 minutes, then try. "
                "Starts on the jump?",
                "Starts on a jump = YOUR battery is done or "
                "drained (why it drained is the next "
                "question — age, a light left on, or the "
                "alternator not charging; a parts store "
                "tests the battery free). Won't start even "
                "on a jump = the battery is innocent; the "
                "starter circuit is the hunt — check 4.",
                "a jump changes nothing — battery and "
                "cables are off the hook."),
            _step(
                "Try it in Neutral (foot on the brake, "
                "parking brake set). An automatic that "
                "starts in Neutral but not Park has a tired "
                "neutral-safety switch — it just told you. "
                "Push-button car? Hold the fob against the "
                "start button itself and press — a dead fob "
                "battery still starts it that way, by "
                "design.",
                "Either answer names the part: the safety "
                "switch (or the fob battery, a $5 coin "
                "cell). Both are small money next to a "
                "starter — which is why this check comes "
                "before anyone sells you one.",
                "not the switch, not the fob — the starter "
                "is getting its orders and not turning, or "
                "the engine cranks fine and the fault is "
                "fuel/spark (check 5 sorts which)."),
            _step(
                "If it CRANKS strong but won't catch: key "
                "to ON (not start) and listen at the back — "
                "a 2-second hum is the fuel pump priming. "
                "No hum = no fuel pressure (pump, its fuse, "
                "or its relay — swap the relay with an "
                "identical one from the fuse box, free, and "
                "listen again). Hum but no catch = spark "
                "or the immobilizer (a flashing key/security "
                "light on the dash is that talking).",
                "The hum test split fuel from spark without "
                "a single tool. From here it's part-level "
                "work — the verdict below is honest about "
                "which side of the shop line that lands on.",
                "fuel delivery and the basics are proven — "
                "what's left needs a scan and gauges."),
        ],
        "verdict": (
            "You've proven the battery, the connections, "
            "and the start circuit one check at a time. "
            "What's left — a starter that's dead under "
            "load, a fuel pump, or an immobilizer/sensor "
            "fault — is scan-and-tools work: " + SHOP_LINE),
        "parts": ["battery", "battery terminal clamps",
                  "starter motor", "fuel pump relay"],
        "est": "starter",
    },
    {
        "id": "battery_drain", "title": "battery keeps dying",
        "safety": [
            "Safety: a battery that keeps dying WILL strand "
            "you somewhere worse than your driveway — treat "
            "this as fix-it, not live-with-it.",
        ],
        "steps": [
            _step(
                "How old is the battery? The date sticker is "
                "on top (month/year). Car batteries live "
                "3–5 years; past 4, a battery that dies "
                "overnight is usually just DONE, not "
                "haunted. Any parts store load-tests it "
                "free — that test is the verdict, not an "
                "opinion.",
                "Old + fails the load test = the whole "
                "story. New battery, and write the date on "
                "it with a paint pen so the next owner (or "
                "you in 4 years) knows.",
                "the battery itself tests good — something "
                "is drinking it overnight."),
            _step(
                "The lights hunt, free: at night, look for a "
                "glovebox or trunk light staying on (phone "
                "camera recording inside the closed trunk "
                "sees it), a dome light on a door that "
                "doesn't fully latch, and anything plugged "
                "into a power port that stays live — "
                "dashcams and phone chargers are the classic "
                "overnight drinkers.",
                "A stuck trunk light pulls exactly enough "
                "to kill a battery by morning and not "
                "enough to notice by day. Fix the switch or "
                "the latch and the 'mystery drain' ends.",
                "nothing is visibly on — the draw is "
                "electrical, not a lamp."),
            _step(
                "What changed? New stereo, amp, remote "
                "start, alarm, or a trailer harness in the "
                "last few months — aftermarket gear wired "
                "to a constant-hot circuit is the #1 "
                "parasitic draw on a car that 'suddenly' "
                "started dying.",
                "Name the newest install to any shop and "
                "you've halved their diagnostic time — and "
                "the bill that comes with it.",
                "nothing new is wired in — the draw is a "
                "factory circuit failing awake."),
            _step(
                "Alternator diode check with any cheap "
                "multimeter: engine running, meter on AC "
                "volts across the battery — more than "
                "~0.5 V AC means a diode is leaking, and a "
                "leaking diode drains the battery overnight "
                "AND undercharges it all day.",
                "That reading is the alternator confessing. "
                "It's a replace-the-unit part, driveway-"
                "doable on most cars with the belt routing "
                "diagram (under the hood / in the manual) "
                "photographed BEFORE the belt comes off.",
                "the alternator's diodes are clean — the "
                "draw is a module staying awake."),
        ],
        "verdict": (
            "Battery proven, lamps proven, gear proven, "
            "alternator proven. What's left is a parasitic-"
            "draw hunt: a meter in series and fuses pulled "
            "one at a time until the draw dies. It's "
            "meter work, driveway-possible with patience — "
            "and " + SHOP_LINE),
        "parts": ["battery", "alternator",
                  "trunk/glovebox light switch"],
        "est": "alternator",
    },
    {
        "id": "brake_noise", "title": "brake noise",
        "safety": [
            "Safety first: a GRINDING brake is metal on "
            "metal — the pad is gone and the rotor is "
            "being eaten. Stopping distance grows every "
            "mile. Squeal is a warning; grinding is an "
            "invoice growing — treat grinding as this-week, "
            "not someday.",
        ],
        "steps": [
            _step(
                "When does it sound? Squeal ONLY while "
                "braking, that stops when you let off = the "
                "pads' wear indicators — little metal tabs "
                "built to sing when the pad is nearly done. "
                "That's the designed warning, and it's "
                "doing its job.",
                "Braking-only squeal = pads at the wear "
                "tab. The fix is pads (and rotors if "
                "they're scored) — the verdict has the "
                "honest numbers.",
                "it isn't braking-only squeal — keep "
                "walking."),
            _step(
                "Squeal for the first minutes of the day "
                "that fades as you drive = overnight rust "
                "film on the rotors, scrubbed off by the "
                "first stops. Annoying, normal, and NOT a "
                "repair — unless it's joined by a pulse in "
                "the pedal.",
                "Morning-only squeal that fades = rust "
                "film. No parts fix that, and anyone who "
                "sells you one for it is selling, not "
                "fixing.",
                "the noise persists warm — it isn't "
                "surface rust."),
            _step(
                "Look through the wheel spokes with a "
                "flashlight: pad material left on the "
                "visible pad (you want to see friction "
                "material, ideally 1/4 inch or better), and "
                "the rotor face — smooth with light lines "
                "is fine; deep grooves or a blue/purple "
                "heat tint is not.",
                "Thin pad + scored rotor = pads AND "
                "rotors, together — new pads on a chewed "
                "rotor die young and squeal forever. "
                "Measured, that's a driveway job on most "
                "cars IF you're set up for it (check 4 "
                "is the honest fence).",
                "the pads have meat and the rotor is "
                "clean — the noise is hardware or a "
                "sticking caliper, check 4."),
            _step(
                "The honest fence: brakes are driveway work "
                "ONLY if you have a jack AND stands (never "
                "a jack alone), a torque wrench, and you'll "
                "do the finish right — lug nuts torqued in "
                "a star pattern to spec (most cars "
                "80–120 ft-lb; 100 is typical; yours is in "
                "the manual), then RE-torqued after ~50 "
                "miles. Missing any of that, it isn't "
                "stubbornness to hand it over — it's the "
                "job's actual entry fee.",
                "Set up right, pads+rotors is the most "
                "satisfying money-saving job on a car. Not "
                "set up, the answer below is the correct "
                "one and there's no shame in it.",
                "the entry fee is real either way — the "
                "verdict prices both doors."),
        ],
        "verdict": (
            "Brake verdict: if the pads are at the tabs or "
            "the rotor is scored, it's pads + rotors per "
            "axle. Driveway door: parts money only, with "
            "the stands-and-torque discipline from check "
            "4. Shop door: " + SHOP_LINE),
        "parts": ["brake pads", "brake rotors",
                  "brake hardware kit"],
        "est": "pads_rotors",
    },
    {
        "id": "overheat", "title": "overheating",
        "safety": [
            "Safety first: NEVER open the radiator or "
            "overflow cap on a hot engine — the system is "
            "pressurized and the coolant is scalding. "
            "Every check below starts COLD. And if the "
            "gauge is in the red, shut it off — an "
            "overheated aluminum engine warps its head, "
            "which turns a hose bill into an engine bill.",
        ],
        "steps": [
            _step(
                "COLD engine: the overflow tank level "
                "against its marks, and the coolant's look — "
                "it should be its proper color (green, "
                "orange, pink by make) and clean. Low tank "
                "= coolant went somewhere: it's a sealed "
                "system, it doesn't 'use' coolant.",
                "Low + clean = a leak to find (check 2) or "
                "a cap that stopped holding pressure — a "
                "cap is $15 and criminally under-diagnosed. "
                "Rusty or oily coolant changes the hunt: "
                "that's the verdict's territory, named "
                "plainly.",
                "the level is right — the coolant isn't "
                "leaving; the problem is moving heat, not "
                "losing coolant."),
            _step(
                "Leak hunt, cold and free: squeeze the big "
                "hoses (spongy, crunchy, or swollen = aged "
                "out), look for crusty colored residue at "
                "every hose end, the water pump's weep hole "
                "(a stain trail under the pump), and the "
                "radiator's seams and fins. Cardboard under "
                "the car overnight catches what eyes miss "
                "and tells you WHERE.",
                "Residue at a joint = that joint. A hose is "
                "driveway money; a radiator or pump is the "
                "verdict's call. Either way you found it "
                "for free.",
                "dry everywhere — no leak. The system is "
                "full and sealed; the failure is "
                "circulation or airflow."),
            _step(
                "Airflow: start it, AC on max — the electric "
                "fan(s) behind the radiator should spin up. "
                "No spin = no cooling at idle and in "
                "traffic, while highway speed is fine (that "
                "pattern — hot in traffic, cool at speed — "
                "IS the fan confessing). Also look through "
                "the grille: a condenser matted with bugs "
                "and cottonwood is a blanket. Hose it from "
                "the engine side out, gently.",
                "Fan dead = check its fuse and relay first "
                "(swap an identical relay, free) before "
                "anyone sells you a fan motor. Matted "
                "condenser = a garden hose just saved you "
                "a diagnostic fee.",
                "fans spin and the fins are clear — "
                "airflow is proven; it's circulation."),
            _step(
                "Thermostat behavior: from cold, the top "
                "radiator hose should stay cool while the "
                "gauge climbs, then turn hot all at once "
                "when the stat opens (~195°F). Hose hot "
                "from the first minute = stat stuck OPEN "
                "(runs cold, poor heat). Gauge in the red "
                "with the hose still cool = stat stuck "
                "SHUT — the classic overheat, and a stat "
                "is cheap; its housing bolts are not the "
                "place to learn about torque, they're "
                "snug-not-heroic.",
                "The hose told you which way it stuck. A "
                "thermostat is one of the last honest "
                "$20-part fixes on a car — gasket, "
                "orientation (spring toward the engine), "
                "and a proper coolant refill with the air "
                "bled out.",
                "the stat cycles right — circulation is "
                "down to the water pump, or the head "
                "gasket signs in the verdict."),
        ],
        "verdict": (
            "What's left is the water pump (a weep or a "
            "wobble you can see, or an impeller gone "
            "inside) — or the bad one: exhaust in the "
            "coolant, milky oil, or white exhaust smoke "
            "means head gasket, and that's deep work. "
            + SHOP_LINE),
        "parts": ["thermostat + gasket", "radiator cap",
                  "coolant (the correct spec — see the "
                  "manual)", "water pump"],
        "est": "water_pump",
    },
    {
        "id": "rough_idle", "title": "rough idle / stalling",
        "safety": [
            "Safety: a car that stalls in traffic stalls "
            "without power steering or brake boost — "
            "until it's sorted, leave room and expect it.",
        ],
        "steps": [
            _step(
                "The air filter — free, 2 minutes. If it's "
                "grey-black and won't pass light, the engine "
                "is breathing through a pillow. A clogged "
                "filter roughs the idle and lies to every "
                "sensor downstream.",
                "A $15 filter just out-diagnosed a dozen "
                "guesses. If the idle smooths, the filter "
                "was it — and note the interval (15–30k "
                "miles) so it doesn't sneak back.",
                "the engine is breathing fine — air in "
                "isn't the break."),
            _step(
                "Vacuum hoses: with it idling, listen for a "
                "hiss around the intake, and eyeball every "
                "rubber hose for cracks, collapse, or a "
                "loose end. A vacuum leak is unmetered air "
                "— the computer fuels for air it measured, "
                "so a leak leans the mixture and roughs "
                "the idle.",
                "A cracked elbow or a hose off its barb IS "
                "the fix — small rubber, big symptom. "
                "Replace with the same diameter; vacuum "
                "hose is sold by the foot.",
                "no hiss, no cracks — the air path is "
                "honest."),
            _step(
                "The code question: is the check-engine "
                "light on? A misfire code (P0300–P0304) "
                "names the cylinder; a lean code (P0171) "
                "points back at air/fuel metering. Most "
                "parts stores read codes free — get the "
                "NUMBER, and bring it here; I'll read it "
                "plain (codes are this seat's home turf).",
                "A code turned a symptom into a suspect "
                "list with a ranking. That's the whole "
                "game — the number is the diagnosis's "
                "first sentence.",
                "no light, no code — it's mechanical or "
                "maintenance-due: check 4."),
            _step(
                "Spark plugs: how many miles on them? "
                "Iridium plugs run 60–100k, copper 30k — "
                "past the interval, worn plugs misfire "
                "first at idle, where the spark is "
                "weakest. One plug pulled tells the story "
                "of all of them: worn-rounded electrode = "
                "a set, torqued to spec (snug + a small "
                "turn on most — the box and the manual "
                "name it; plugs snap when gorilla'd).",
                "Plugs past their interval with a rough "
                "idle is the oldest story in the book — "
                "and one of the cheapest endings.",
                "plugs are in their window and healthy — "
                "what's left is fuel delivery or a sensor, "
                "and that's the verdict."),
        ],
        "verdict": (
            "Air, vacuum, codes, and plugs are all "
            "proven. What's left — an injector, a coil "
            "failing under heat, a fuel-pressure problem "
            "— wants a scan tool and a fuel gauge on it. "
            + SHOP_LINE),
        "parts": ["air filter", "spark plugs",
                  "vacuum hose (by the foot)", "MAF cleaner"],
        "est": "plugs",
    },
    {
        "id": "ac_warm", "title": "car AC blows warm",
        "safety": [
            "Safety: refrigerant work is where this walk "
            "honestly ends — the system is pressurized, "
            "and venting refrigerant is illegal as well as "
            "a frostbite risk. Everything below is the "
            "free stuff BEFORE that line.",
        ],
        "steps": [
            _step(
                "The cabin filter — behind the glovebox on "
                "most cars, 5 minutes, free to check. A "
                "plugged cabin filter strangles airflow so "
                "the AC feels weak even when it's cold at "
                "the coil. Weak FLOW and weak COLD are "
                "different diseases; this separates them.",
                "Grey mat of a filter = found it. Airflow "
                "is half the AC story and the cheapest "
                "half.",
                "airflow is strong — the air moves; it "
                "just isn't cold."),
            _step(
                "Condenser: look through the grille at the "
                "radiator-like panel at the very front. "
                "Matted with bugs, leaves, and road film, "
                "it can't shed heat. Hose it gently from "
                "the engine side outward — never a pressure "
                "washer, the fins fold if you look at them "
                "wrong.",
                "A condenser wearing a blanket can't do "
                "its one job. Clean fins + a test drive is "
                "the honest verdict on this check.",
                "the condenser breathes — heat can leave; "
                "the fault is in the cycle itself."),
            _step(
                "The clutch test: engine on, AC to max — "
                "you should hear/see the compressor clutch "
                "click in and the cooling fan start. No "
                "click = the system is either low on "
                "refrigerant (a pressure switch is "
                "protecting the compressor) or the clutch/"
                "its fuse/relay failed. Check the fuse and "
                "swap the relay first — free before money.",
                "A dead fuse/relay just saved you a "
                "compressor quote. A system too low to "
                "engage is the verdict's story: it has a "
                "leak, and topping it is renting cold air "
                "by the week.",
                "the clutch engages and the fan runs — "
                "the cycle runs; its charge or its "
                "compressor is weak."),
        ],
        "verdict": (
            "Airflow, condenser, and engagement are "
            "proven. What's left is the sealed cycle — "
            "charge level, a leak, or the compressor — "
            "and that takes gauges and a recovery machine "
            "by law. " + SHOP_LINE),
        "parts": ["cabin air filter", "AC relay",
                  "condenser (if damaged)"],
        "est": "ac_service",
    },
    {
        "id": "vibration", "title": "vibration / pulling",
        "safety": [
            "Safety first: before any diagnosis — check "
            "the lug nuts on all four wheels are TIGHT, "
            "by hand with the wrench, now. A wheel that "
            "works loose starts as a vibration and ends "
            "as a wheel off. If any lug is loose, torque "
            "them all (star pattern, 80–120 ft-lb typical "
            "— yours is in the manual) before another "
            "mile.",
        ],
        "steps": [
            _step(
                "Tire pressures, all four, cold — the door-"
                "jamb sticker numbers, not the tire's side"
                "wall max. One soft tire pulls the car "
                "toward its side and wears its shoulders. "
                "While you're there, READ the tread: wear "
                "in the middle = overinflated; both "
                "shoulders = underinflated; ONE edge = "
                "alignment; cupped/scalloped = balance or "
                "worn shocks.",
                "The tread is a written record of the "
                "last 10,000 miles — it just told you "
                "which system to blame, for free.",
                "pressures right and the tread reads "
                "clean — the rubber is innocent."),
            _step(
                "The speed question: a shake that arrives "
                "at one speed (classically 55–70) and "
                "leaves above it is wheel BALANCE. A shake "
                "under BRAKING is warped rotors (that's "
                "the brake walk). A hum that changes when "
                "you swerve gently side to side — louder "
                "one way, quieter the other — is a wheel "
                "BEARING naming its side.",
                "Each pattern is a signature: balance, "
                "brakes, or bearing. Naming the pattern "
                "just named the repair — no parts cannon "
                "required.",
                "no clean pattern — it's suspension or "
                "alignment geometry: check 3."),
            _step(
                "The bounce test at each corner: push down "
                "hard and let go — a healthy corner rises "
                "and settles in one move. Two or more "
                "bounces = that strut/shock is done, and "
                "dead shocks cup tires and lengthen stops. "
                "Also: clunks over bumps point at sway-bar "
                "links and bushings, the cheap seats of the "
                "suspension.",
                "A corner that keeps bouncing named its "
                "strut. Struts are spring-compressed work "
                "— the verdict is honest about whose "
                "hands that's for.",
                "corners settle right — suspension passes "
                "the driveway tests."),
        ],
        "verdict": (
            "If it's alignment, balance, or struts, "
            "you're at equipment this seat can't fake: "
            "a balance machine, an alignment rack, a "
            "spring compressor. " + SHOP_LINE),
        "parts": ["tires (if worn out)", "sway-bar links",
                  "wheel bearing hub"],
        "est": "alignment",
    },
    {
        "id": "cel_light", "title": "check-engine light",
        "safety": [
            "Safety first: a FLASHING check-engine light "
            "is different from a solid one — flashing "
            "means an active misfire dumping raw fuel "
            "into the exhaust, which can cook the "
            "catalytic converter in miles. Slow down, no "
            "towing, no highway pulls until it's read.",
        ],
        "steps": [
            _step(
                "The gas cap — the #1 free fix in all of "
                "car repair. Click it until it clicks (one "
                "firm click past snug). A loose or tired "
                "cap seal is an evaporative leak the "
                "computer reports as a code (P0442/P0455 "
                "family). If it was loose, the light clears "
                "itself after a few drive cycles — days, "
                "not minutes.",
                "A loose cap wears a $150 diagnostic's "
                "clothes. If the light stays after a few "
                "days of driving, the cap seal itself is "
                "the cheap next suspect.",
                "the cap is tight — the code is real; "
                "get it read (check 2)."),
            _step(
                "Get the code NUMBER — most parts stores "
                "read it free; a basic scanner is $25 and "
                "pays for itself the first time. Bring the "
                "number here: 'what does code P0171 mean' "
                "gets you the plain read, the ranked "
                "causes, and the proving check — that's "
                "this seat's home turf.",
                "The number is the diagnosis's first "
                "sentence. Without it, everyone is "
                "guessing — with it, the list is ranked "
                "and short.",
                "code in hand (or scheduled) — the walk "
                "continues in the code lane."),
            _step(
                "The symptom sweep while you wait: is it "
                "running DIFFERENT — rough, gutless, "
                "stalling, drinking fuel — or running fine "
                "with just the light? Light + fine = "
                "usually emissions-side (evap, sensor "
                "drift) and schedule-able. Light + "
                "symptoms = the rough-idle walk, and it "
                "matters sooner.",
                "Running fine buys you time; running "
                "wrong spends it. That one distinction "
                "sets the urgency honestly.",
                "the sweep is done — what remains is the "
                "code's own story."),
        ],
        "verdict": (
            "A check-engine light with no code read is a "
            "sealed envelope. Get the number (free at "
            "most parts stores), bring it here, and the "
            "ranked causes come with the proving check. "
            "If it ever FLASHES or the car runs wrong "
            "with it — that's not a wait-and-see, that's "
            + SHOP_LINE),
        "parts": ["gas cap", "the code's own parts — "
                  "named when the number lands"],
    },
    {
        "id": "fluid_leak", "title": "fluid leaking",
        "safety": [
            "Safety first: if the fluid is BRAKE fluid "
            "(clear to yellow, slippery, eats paint) and "
            "the pedal feels any different — do NOT drive "
            "it. Brakes are the one system with no "
            "backup. Tow money is cheaper than the "
            "alternative.",
        ],
        "steps": [
            _step(
                "Read the puddle — color, feel, smell, on "
                "white cardboard overnight for a clean "
                "sample. Coolant: colored (green/orange/"
                "pink), sweet-smelling, watery-slick. "
                "Engine oil: brown to black, slick. "
                "Transmission fluid: red to dark red, "
                "slick, faintly sweet. Power steering: "
                "red or clear, thin. Brake fluid: clear/"
                "yellow, very slippery, strips paint. "
                "Water that's just water under the AC "
                "drain in summer is NORMAL — condensation "
                "is not a leak.",
                "The puddle just named its system — that "
                "one read replaces an hour of crawling "
                "around guessing.",
                "the fluid is identified — now its level "
                "names the urgency (check 2)."),
            _step(
                "Check THAT system's level (cold, level "
                "ground): the reservoir or dipstick it "
                "feeds. Low + still leaking = top it to "
                "the mark with the CORRECT fluid (the cap "
                "and the manual name the spec — brake "
                "fluid especially is spec-critical) and "
                "the leak becomes scheduled work, not a "
                "stranding. Empty = don't drive; it's "
                "telling you the leak is fast.",
                "A level turns a puddle into a rate: drops "
                "per day is a schedule; a drained reservoir "
                "is a tow.",
                "levels hold — the leak is a seep, which "
                "buys time but not forever."),
            _step(
                "Position tells the source: under the FRONT "
                "= engine/transmission/steering country. "
                "Under the MIDDLE = fuel lines or the tank "
                "(fuel smell + leak = the emergency lane, "
                "not this walk — stop and say so). Under "
                "the REAR = rear diff, rear brake lines, "
                "or the tank again. Fresh cardboard under "
                "a clean, dry underside overnight pinpoints "
                "it to the drip point.",
                "Position + color + level is a complete "
                "sentence — any shop quotes faster and "
                "straighter when you hand them all three.",
                "the source zone is named — the fix is "
                "the verdict's call."),
        ],
        "verdict": (
            "A seep you can monitor is a schedule; a "
            "drip that drains a reservoir is a repair "
            "this week; brake fluid or fuel is a tow, "
            "full stop. Seals, hoses, and pump work are "
            "mostly lift work — " + SHOP_LINE),
        "parts": ["the correct fluid for the system "
                  "(spec on the cap / in the manual)",
                  "hoses / seals as named by the source"],
    },
]

_TREE_BY_ID = {t["id"]: t for t in TREES}


# --- Cost-sanity ranges (ALL labeled ESTIMATE where they ride) ---------------------
# Planning ranges for common work, shop door (parts + labor)
# unless noted. Prices move; the label in the text says so.

_EST = {
    "battery": "Battery (parts-store, installed): "
               "$120–$250.",
    "starter": "Starter (shop, parts + labor): "
               "$300–$650; battery (parts): $120–$250.",
    "alternator": "Alternator (shop, parts + labor): "
                  "$400–$800; driveway door is the part "
                  "alone, roughly half that.",
    "pads_rotors": "Pads + rotors per axle — shop: "
                   "$300–$600; parts alone: $120–$280.",
    "water_pump": "Water pump (shop): $450–$950 — on "
                  "timing-belt engines it rides the belt "
                  "job, which is the smart time to do both.",
    "plugs": "Spark plugs — parts: $40–$120 a set; shop "
             "with labor: $150–$350.",
    "ac_service": "AC leak repair + recharge (shop): "
                  "$250–$700 depending on the leaking part; "
                  "a compressor pushes past $800.",
    "alignment": "Alignment (shop): $90–$180; balance: "
                 "$15–$25 a wheel.",
    "cat": "Catalytic converter (shop): $900–$2,500 — "
           "which is exactly why the causes above get "
           "checked before the converter gets blamed.",
    "airbag": "Airbag / SRS work (shop): $400–$1,200+ "
              "depending on the part — modules and clock "
              "springs sit in that range; a dealer quote "
              "runs higher.",
}


def _est_line(key: str) -> str:
    txt = _EST.get(key or "", "")
    if not txt:
        return ""
    return f"\nCost sanity: {txt} {ESTIMATE_NOTE}"


# --- OBD codes: the plain read, ranked causes, the proving check -------------------
# A code reports that a SYSTEM saw something out of range —
# it names the circuit that reported, not always the bad
# part. Causes are ranked most-likely-first; the check is
# what proves it before money moves.

def _code(means, causes, check, lane, est=""):
    return {"means": means, "causes": causes,
            "check": check, "lane": lane, "est": est}


CODES = {
    "P0300": _code(
        "Random / multiple-cylinder misfire — the "
        "computer sees the crankshaft's rhythm stumble "
        "across more than one cylinder and can't pin it "
        "to just one.",
        ["Worn spark plugs (past their interval — the "
         "#1 cause)", "A vacuum leak leaning everything "
         "out", "Weak fuel delivery (pump or filter)",
         "One coil failing and dragging the rhythm"],
        "Start with the plugs' age — if they're past "
        "60–100k, they're the answer until proven "
        "otherwise. A flashing light with this code = "
        "active misfire; don't drive it hard.",
        "Driveway-first (plugs, vacuum hoses).",
        "plugs"),
    "P0171": _code(
        "System too lean, bank 1 — the engine is "
        "getting more air (or less fuel) than the "
        "computer can correct for.",
        ["Vacuum leak (cracked hose, intake gasket)",
         "Dirty MAF sensor reading low",
         "Weak fuel pump / clogged filter",
         "An exhaust leak ahead of the O2 sensor"],
        "Vacuum hoses first (free): hiss + cracks. "
        "Then MAF cleaner on the sensor (never touch "
        "the element). Fuel pressure is the gauge "
        "check after those.",
        "Driveway-first (hoses, MAF cleaning)."),
    "P0174": _code(
        "System too lean, bank 2 — same story as P0171, "
        "the other bank. Both banks together (P0171 + "
        "P0174) point at shared causes: MAF, fuel "
        "delivery, a big vacuum leak.",
        ["Shared vacuum leak (intake / PCV hose)",
         "Dirty or failing MAF sensor",
         "Weak fuel pump"],
        "Same order as P0171 — the pair together is "
        "the tell that it's shared, not one bank's "
        "part.",
        "Driveway-first."),
    "P0172": _code(
        "System too rich, bank 1 — too much fuel or too "
        "little air for what the computer commanded.",
        ["Clogged air filter (check it — free)",
         "A leaking fuel injector",
         "Fuel-pressure regulator / pressure too high",
         "A lazy O2 sensor"],
        "Air filter first because it's free and "
         "embarrassingly common. Rich + a fuel smell "
         "at the exhaust moves injectors up the list.",
        "Driveway-first (filter), injectors are deeper."),
    "P0420": _code(
        "Catalyst efficiency below threshold, bank 1 — "
        "the rear O2 sensor sees exhaust the converter "
        "should have cleaned. Read this carefully: the "
        "code REPORTS the converter's report card — "
        "engines that burn oil or misfire kill "
        "converters, and a new converter behind a sick "
        "engine dies the same death.",
        ["The converter itself, worn out (miles/age)",
         "An exhaust leak ahead of the rear sensor",
         "A lazy rear O2 sensor lying about the "
         "converter", "An engine fault (misfire, oil "
         "burning) that poisoned it"],
        "Rule out the lie first: exhaust leaks and the "
        "rear sensor get checked before the converter "
        "gets blamed — and any misfire/oil use gets "
        "fixed FIRST, or the new part inherits the "
        "disease.",
        "Diagnosis-first; the part itself is bolt-in "
        "but expensive — most hand it over.",
        "cat"),
    "P0442": _code(
        "Evaporative system leak, small — the fuel "
        "vapor system can't hold its vacuum test. The "
        "car runs FINE; this is emissions housekeeping.",
        ["Gas cap seal (tired, cracked, or not "
         "clicked)", "A cracked evap hose", "The purge "
         "or vent valve not sealing"],
        "The cap: take it off, read the seal, click "
        "it back ONE firm click. Clear the code (parts "
        "store) and give it days of driving to retest "
        "itself.",
        "Driveway (cap first, always)."),
    "P0455": _code(
        "Evaporative system leak, LARGE — same system "
        "as P0442, a bigger hole. Still runs fine.",
        ["Gas cap off, cross-threaded, or its seal "
         "gone", "Filler neck rust / damage", "A vent "
         "valve stuck open or a split evap hose"],
        "Cap first (it's still the #1), then eyes on "
        "the filler neck and the hoses you can reach.",
        "Driveway-first (cap), hoses are deeper."),
    "P0456": _code(
        "Evaporative leak, VERY small — P0442's quieter "
        "sibling. Cap seal first, same discipline.",
        ["Gas cap seal", "Aging evap hoses",
         "Purge valve seeping"],
        "Cap seal under a fingernail: cracks or a "
        "flat, hard seal = a $15 cap before any "
        "diagnosis money.",
        "Driveway (cap first)."),
    "P0128": _code(
        "Coolant temperature below the thermostat's "
        "regulating range — the engine warms too "
        "slowly. Almost always the thermostat stuck "
        "OPEN or opening early. (Symptoms: slow cabin "
        "heat, gauge reading low, worse mileage.)",
        ["Thermostat stuck open (the classic)",
         "A coolant temp sensor reading low",
         "Low coolant making the reading lie"],
        "Feel the top radiator hose from a cold "
        "start: warming gradually from minute one = "
        "the stat is open when it should be shut. "
        "That's the proof, no tools.",
        "Driveway (a thermostat is cheap work on "
        "most cars)."),
    "P0401": _code(
        "EGR flow insufficient — the exhaust-gas "
        "recirculation passages are carboned up, "
        "common on higher-mile engines. (Symptoms: "
        "pinging under load, slight surge.)",
        ["Carbon-clogged EGR passages (the classic)",
         "The EGR valve itself stuck or tired",
         "A failed EGR position/pressure sensor"],
        "On many engines the valve comes off with "
        "two bolts and the carbon is VISIBLE — "
        "cleaning the passages is the fix more often "
        "than the part is.",
        "Driveway-possible (cleaning), varies by "
        "engine."),
    "P0507": _code(
        "Idle higher than expected — the computer "
        "can't pull the idle down to target.",
        ["A vacuum leak (extra air has to go "
         "somewhere)", "A carboned throttle body "
         "holding the plate off its seat", "The idle "
         "control system itself"],
        "Hoses first (free), then the throttle body's "
        "bore — a ring of carbon at the plate is the "
        "classic, cleaned with throttle-body cleaner "
        "and a rag.",
        "Driveway-first."),
    "P0325": _code(
        "Knock sensor circuit, bank 1 — the sensor "
        "that listens for engine knock isn't "
        "reporting. The computer plays safe and pulls "
        "timing, so it may feel gutless and drink "
        "fuel.",
        ["The sensor itself (they live in heat and "
         "vibration)", "Its wiring — rodent-chewed is "
         "a genuine classic", "Corrosion at the "
         "connector"],
        "Eyes on the wiring first if it's reachable — "
        "chewed insulation has ended more of these "
        "than failed sensors have.",
        "Varies — some sit on top (easy), some hide "
        "under the intake (shop)."),
    "P0335": _code(
        "Crankshaft position sensor circuit — the "
        "computer lost the engine's master timing "
        "signal. This one stalls cars and prevents "
        "starts: no crank signal, no spark, no fuel.",
        ["The crank sensor itself (heat kills them; "
         "a classic die-when-hot, start-when-cold "
         "pattern)", "Damaged wiring near the sensor",
         "A damaged reluctor ring (rarer)"],
        "The pattern IS the check: dies hot, "
        "restarts cooled, code returns — that's the "
        "sensor confessing. It's usually one bolt "
        "and a plug, low on the engine.",
        "Driveway-possible on many cars."),
    "P0340": _code(
        "Camshaft position sensor circuit — the "
        "computer lost the cam signal it uses to time "
        "injectors and spark. Long cranks, stalling, "
        "or a no-start.",
        ["The cam sensor itself", "Its connector / "
         "wiring", "On high-mile engines: a stretched "
         "timing chain confusing the correlation "
         "(that's the deep end)"],
        "Sensor and connector first — cheap and "
        "common. If a NEW sensor doesn't cure it and "
        "the miles are high, the chain conversation "
        "is shop territory, said plainly.",
        "Sensor: driveway. Chain: shop."),
    "P0113": _code(
        "Intake air temperature sensor reading high — "
        "a small sensor, usually built into the MAF. "
        "Often rides along after intake work.",
        ["The connector unplugged or loose (after "
         "air-filter work — check first)", "The sensor "
         "element failed", "Wiring damage"],
        "If anyone touched the airbox recently, the "
        "plug is the diagnosis. Reseat it, clear, "
        "retest.",
        "Driveway."),
}


def _code_entry(code: str) -> Optional[Dict]:
    """Exact table first, then the cylinder-misfire
    pattern (P0301–P0308), then None — the caller
    gives the honest structure read for unknown
    codes."""
    if code in CODES:
        return CODES[code]
    if re.fullmatch(r"P030[1-8]", code):
        cyl = code[-1]
        return _code(
            f"Misfire on cylinder {cyl} — the computer "
            "watched that cylinder's contribution stumble. "
            "One cylinder = a short suspect list, and the "
            "swap test below proves it without buying "
            "anything.",
            [f"Ignition coil on cylinder {cyl} (the "
             "#1 cause)", f"Spark plug on cylinder "
             f"{cyl} (worn or fouled)", f"Fuel injector "
             f"on cylinder {cyl}", "Low compression on "
             f"that cylinder (the deep end — last, not "
             "first)"],
            "THE SWAP TEST, free: swap that cylinder's "
            "coil with a neighbor's, clear the code, "
            "drive. Code follows the coil (P030 moves "
            "to the other cylinder) = the coil confessed. "
            "Stays on the cylinder = plug next, then "
            "injector. A flashing light = active misfire "
            "— don't drive it hard meanwhile.",
            "Driveway — the swap test is the whole game.",
            "plugs")
    return None


# --- The shop-job lane ---------------------------------------------------------------
# Topics whose honest answer is the shop line FIRST —
# with the reason, what the shop does, what the user
# CAN still do, and the cost-sanity range.

def _shop(body, est=""):
    return {"body": body, "est": est}


SHOP_JOBS = [
    {
        "id": "airbag",
        "rx": r"\bair ?bags?\b|\bsrs\b|\bsupplemental "
              r"restraint\b",
        "entry": _shop(
            "Airbags and the SRS system. " + SHOP_LINE
            + "\nWhy: an airbag is a controlled explosive "
              "with a hair trigger. Handling one wrong — "
              "static, a probe in the wrong wire, power "
              "where it shouldn't be — can fire it, and a "
              "firing bag breaks bones and worse. The "
              "system also arms itself off capacitors "
              "after the battery's disconnected, so "
              "'the battery's off' is not 'it's safe'."
            "\nWhat the shop does: scans the SRS module "
              "for the exact fault code (an SRS light "
              "names a circuit — a clock spring, a seat "
              "sensor, a bag), and handles bags with "
              "the disarm procedure for YOUR car."
            "\nWhat you can do: if the airbag LIGHT is "
              "on, treat it as real — the system usually "
              "disables itself with the light on, meaning "
              "the bags may not fire in a crash. Get the "
              "SRS code read (a dealer or a shop with an "
              "SRS-capable scanner; the free parts-store "
              "scanners often can't) and bring the number "
              "here — I'll read it plain.",
            "airbag"),
    },
    {
        "id": "transmission",
        "rx": r"\btransmission\b[^.?!]*\b(?:rebuild|"
              r"overhaul|internals?|replace|replacement|"
              r"slipping|slips)\b|\brebuild\b[^.?!]*"
              r"\btransmission\b|\btransmission rebuild\b",
        "entry": _shop(
            "Transmission internals. " + SHOP_LINE
            + "\nWhy: inside the case it's clutch packs, "
              "bands, and a valve body with a hundred "
              "passages where one speck of dirt is a "
              "comeback — and getting there means the "
              "transmission comes OUT, which is lift and "
              "transmission-jack work."
            "\nBefore anyone sells you a rebuild, the "
              "driveway checks that are NOT internals: "
              "fluid level and condition (low fluid slips "
              "exactly like a dying transmission; burnt-"
              "smelling, brown-black fluid is the unit "
              "telling its age), and the shift linkage/"
              "cable adjustment. A surprising number of "
              "'transmission' quotes start as a quart "
              "low."
            "\nWhat the shop does: pressure tests and a "
              "scan of the transmission's own codes "
              "before a rebuild is ever honest.",
            "trans_rebuild"),
    },
    {
        "id": "engine_internals",
        "rx": r"\bengine\b[^.?!]*\b(?:rebuild|overhaul"
              r"|internals?)\b|\brebuild\b[^.?!]*"
              r"\bengine\b|\bhead gasket\b|\btiming "
              r"chain\b[^.?!]*\b(?:replace|replacement|"
              r"stretch\w*)\b",
        "entry": _shop(
            "Engine internals. " + SHOP_LINE
            + "\nWhy: head gaskets, timing chains, and "
              "anything deeper live behind hours of "
              "disassembly where one skipped step (a "
              "head bolt torqued out of sequence, a "
              "chain a tooth off) turns the repair into "
              "a replacement. On an interference engine "
              "a timing mistake bends valves on the "
              "first crank."
            "\nWhat you can do: the diagnosis is still "
              "yours to own — the signs (milky oil, "
              "white sweet exhaust smoke, coolant "
              "vanishing with no puddle, a compression "
              "test's numbers) are driveway-readable, "
              "and walking in with them changes the "
              "conversation from a quote to a "
              "confirmation.",
            "engine"),
    },
    {
        "id": "timing_belt",
        "rx": r"\btiming belt\b",
        "entry": _shop(
            "Timing belt — the honest split: " + SHOP_LINE
            + "\nWhy the caution: on an INTERFERENCE "
              "engine (many are), a belt that breaks — "
              "or a new one installed a tooth off — "
              "lets pistons meet valves, and the belt "
              "job becomes an engine job. On a "
              "non-interference engine the same mistake "
              "just strands you."
            "\nThe driveway truth: if your engine is "
              "non-interference, you're experienced, and "
              "you have the timing marks documented "
              "before a single bolt moves, it's a "
              "Saturday job some owners do. First-timer "
              "on an interference engine is the exact "
              "profile of the horror stories."
            "\nEither way: the interval is real "
              "(60–100k miles by maker — check yours), "
              "and the water pump rides the same labor "
              "on most belt engines, so the pair is the "
              "smart quote to ask for.",
            "water_pump"),
    },
    {
        "id": "high_voltage",
        "rx": r"\bhybrid battery\b|\bev battery\b|"
              r"\bhigh[- ]voltage\b|\borange cables?\b|"
              r"\btraction battery\b",
        "entry": _shop(
            "Hybrid / EV high-voltage gear. " + SHOP_LINE
            + "\nWhy, in one sentence: the orange-cabled "
              "system carries 200–800 volts DC, it does "
              "not forgive, and it can stay lethal after "
              "shutdown. This is the hardest fence on "
              "this seat — not 'hard for a beginner', "
              "but 'trained techs wear rated gloves and "
              "follow a disarm sequence' territory."
            "\nWhat you CAN do: the 12-volt side is "
              "still a normal car battery with normal "
              "rules, and most hybrid 'won't start' "
              "stories are the 12V battery — that walk "
              "is the no-start tree, and it's yours.",
            ""),
    },
    {
        "id": "refrigerant",
        "rx": r"\brefrigerant\b|\bfreon\b|\b(?:recharge"
              r"|refill|top ?off|add)\b[^.?!]*\b(?:a/?c"
              r"|air conditioning|refrigerant)\b",
        "entry": _shop(
            "Refrigerant service. " + SHOP_LINE
            + "\nWhy: the system has to be RECOVERED "
              "with a machine before it's opened (venting "
              "refrigerant is illegal and a frostbite "
              "risk), then evacuated and charged BY "
              "WEIGHT — a gauge can in the driveway "
              "guesses, and an overcharged system blows "
              "warm AND strains the compressor."
            "\nThe honest nuance: if it's low, it LEAKS "
              "— refrigerant isn't consumed. A top-off "
              "without a leak fix is renting cold air by "
              "the week."
            "\nWhat you can do first — free: the car-AC "
              "walk on this seat (cabin filter, "
              "condenser, clutch) proves everything "
              "outside the sealed cycle before a dollar "
              "moves.",
            "ac_service"),
    },
    {
        "id": "alignment",
        "rx": r"\balignment\b|\balign\b[^.?!]*\b(?:my|"
              r"the)\s+(?:car|truck|wheels|front end)\b",
        "entry": _shop(
            "Alignment. " + SHOP_LINE
            + "\nWhy: it's measured in hundredths of a "
              "degree on a rack with laser heads — there "
              "is no driveway version of the measurement, "
              "which is the whole job."
            "\nWhat you can do first: read the tread "
              "(one-edge wear = alignment; the vibration "
              "walk has the full read) and set pressures "
              "to the door-jamb numbers, so the rack "
              "measures the car as it should sit. And if "
              "it started right after a curb or a "
              "pothole — say so; bent parts don't align "
              "out.",
            "alignment"),
    },
    {
        "id": "key_programming",
        "rx": r"\bprogram\w*\b[^.?!]*\b(?:key|fob)\b|"
              r"\bimmobilizer\b|\bkey fob\b[^.?!]*"
              r"\b(?:program\w*|replace|new)\b",
        "entry": _shop(
            "Key / immobilizer programming — a dealer "
            "or locksmith job, said plainly. " + SHOP_LINE
            + "\nWhy: the car has to be TAUGHT the new "
              "key with the maker's security access — "
              "that lockout is the anti-theft system "
              "working as designed, and there's no "
              "driveway bypass that isn't a theft tool."
            "\nThe money note: a good automotive "
              "locksmith usually beats the dealer's "
              "price on the same job, and a fob's BATTERY "
              "(a coin cell) is still yours — a fob that "
              "died slowly is a battery until proven "
              "otherwise; a fob the car won't recognize "
              "at all is this lane.",
            ""),
    },
]

_SHOP_BY_ID = {s["id"]: s for s in SHOP_JOBS}


_EST.update({
    "trans_rebuild": "Transmission rebuild / "
                     "replacement (shop): $2,500–"
                     "$5,500 — which is why the fluid "
                     "check always comes first.",
    "engine": "Head gasket (shop): $1,500–$3,000; a "
              "timing chain job: $1,200–$2,500; a "
              "rebuild runs past both.",
})


# --- Sounds, described in words ---------------------------------------------------------
# The sound + its context routes: some sounds open a
# tree (grinding while braking), some get their own
# read (clicking while turning = the CV axle telling
# on itself). Bodies are complete answers.

SOUND_READS = {
    "clicking_turn": (
        "Clicking or popping that keeps time with the "
        "wheel WHILE TURNING (parking lots are where it "
        "talks loudest) is the CV axle's outer joint — "
        "its boot tears, the grease leaves, dirt moves "
        "in, and the joint clicks under steering lock. "
        "Straight-line quiet + turning clicks is the "
        "signature; there's no other common part that "
        "does it. Check: crawl the wheel through a "
        "slow full-lock circle each way — the side it "
        "clicks loudest on is the side. It's a "
        "replace-the-axle job; a torn boot caught "
        "before the clicking starts is the cheap "
        "version of this story."),
    "squeal_start": (
        "A squeal or chirp on start-up, worst on cold "
        "or damp mornings, that fades in under a "
        "minute — and squeals again when the AC "
        "kicks on or you turn the wheel at a stop — "
        "is the serpentine belt: glazed, stretched, "
        "or its tensioner going soft. Check: with the "
        "engine OFF, look at the belt's ribbed side — "
        "cracks across the ribs or a shiny glaze, "
        "plus more than a half-inch of give on the "
        "longest run, names it. Belt + tensioner "
        "together is the honest fix; dressing is a "
        "perfume, not a repair."),
    "hum_speed": (
        "A hum or growl that rises with ROAD speed "
        "(not engine revs) and changes when you "
        "swerve gently — louder turning one way, "
        "quieter the other — is a wheel bearing "
        "naming its side: loading the bearing makes "
        "it sing, unloading quiets it. The side that "
        "gets LOUDER under load is the bad one. "
        "Bearings don't heal and they don't stay "
        "polite — a humming bearing becomes a roaring "
        "one, then a hot one. Many modern ones are "
        "bolt-in hub units (driveway-possible); "
        "press-in bearings are shop gear."),
    "clunk_bumps": (
        "A clunk or rattle over bumps at low speed, "
        "quiet on smooth road, is suspension "
        "small-parts country: sway-bar end links are "
        "the #1 (cheap, and they clunk out of all "
        "proportion to their size), then strut "
        "mounts, then ball joints — and ball joints "
        "are the one to respect, because a failed one "
        "folds a wheel. Check: bounce each corner and "
        "listen; grab the sway-bar links and shake — "
        "play you can feel is the confession."),
    "knock_engine": (
        "A knock from the ENGINE that follows the "
        "revs: stop and check the oil FIRST, before "
        "another run — a deep, heavy knock on low oil "
        "is bearings being eaten, and every minute "
        "running is the damage compounding. Oil low "
        "+ knock = do not run it; that's a tow, and "
        "the diagnosis is internal. A light top-end "
        "TICK that fades as it warms is usually a "
        "lifter — annoying, monitorable, and a "
        "different conversation. The line between "
        "them: deep and low in the engine = stop; "
        "light and high = talk."),
    "rattle_heat": (
        "A rattle from underneath at idle or on "
        "start-up that changes when the exhaust "
        "warms: heat-shield country first (a loose "
        "shield rattles like the car is dying and "
        "costs a clamp), then a dying catalytic "
        "converter's broken core (which often arrives "
        "with a P0420 keeping it company). Cold, "
        "gloved hand, engine off: shields shouldn't "
        "move. A shield that does is the whole "
        "repair."),
}


# --- Maintenance intervals + specs guidance ---------------------------------------------
# Planning numbers for the common fleet, labeled as
# such in the text: they vary by make/model/year and
# use (severe service shortens most of them). The
# owner's manual is the final word on specs — the
# answers say so. Fluid SPECS are never invented per
# vehicle: the answer names where the spec lives
# (the cap, the manual, the door book).

def _item(name, rx, spec, note=""):
    return {"name": name, "rx": rx, "spec": spec,
            "note": note}


INTERVALS = [
    _item("Engine oil + filter",
          r"\boil\b|\boil change\b",
          "Every 5,000–7,500 miles covers most cars "
          "honestly — conventional oil at the 3,000–"
          "5,000 end, full synthetic stretching to "
          "7,500–10,000 — and never past 12 months "
          "regardless of miles. Short trips, towing, "
          "and dust are 'severe service' and pull "
          "every number toward the low end.",
          "The viscosity spec is printed on the oil "
          "cap and in the manual — that number is the "
          "car's, not a preference."),
    _item("Tire rotation",
          r"\brotat\w*\b|\btires?\b",
          "Every 5,000–7,500 miles — booked with the "
          "oil change, it's the rhythm that makes both "
          "happen.",
          "Uneven wear patterns get read at rotation "
          "— the tread is a record, see the vibration "
          "walk."),
    _item("Engine air filter",
          r"\bair filter\b",
          "Every 15,000–30,000 miles, or yearly in "
          "dusty country.",
          "Hold it to the light: if light won't pass, "
          "it's done early regardless of miles."),
    _item("Cabin air filter",
          r"\bcabin\b",
          "Every 15,000–20,000 miles.",
          "A weak-blowing AC is this filter until "
          "proven otherwise — it's behind the "
          "glovebox on most cars."),
    _item("Spark plugs",
          r"\bplugs?\b|\bspark\b",
          "Iridium/platinum: 60,000–100,000 miles. "
          "Old-school copper: 30,000.",
          "One plug pulled reads the whole set — and "
          "the engine's health with it."),
    _item("Coolant",
          r"\bcoolant\b|\bantifreeze\b|\bradiator\b",
          "Modern long-life coolant: 100,000–"
          "150,000 miles or 5–10 years. Old green "
          "coolant: 30,000 miles / 2 years.",
          "NEVER mix colors on a guess — the spec "
          "(OAT/HOAT and the maker's own) is in the "
          "manual, and the wrong mix gels."),
    _item("Transmission fluid",
          r"\btransmission fluid\b|\batf\b",
          "60,000–100,000 miles is the honest window "
          "for most automatics.",
          "'Lifetime fluid' is a warranty word, not "
          "a chemistry word — fluid ages whether it's "
          "scheduled or not. The fluid TYPE is "
          "spec-critical; the manual names it."),
    _item("Brake fluid",
          r"\bbrake fluid\b",
          "Every 2–3 years, by TIME not miles.",
          "It absorbs water from the air, and wet "
          "brake fluid boils under hard braking. "
          "The spec (DOT 3 / DOT 4) is printed on "
          "the reservoir cap."),
    _item("Serpentine belt",
          r"\bbelt\b|\bserpentine\b",
          "Inspect from 60,000 miles; replace by "
          "90,000–100,000 or at cracks/glazing.",
          "A belt that lets go takes the water pump, "
          "alternator, and steering assist with it "
          "on most cars — cheap part, expensive "
          "exit."),
    _item("Timing (belt engines)",
          r"\btiming\b",
          "Belt: 60,000–100,000 miles by maker — "
          "the single most expensive interval to "
          "miss on an interference engine. Chain: "
          "no scheduled swap; chains are wear items "
          "with guides and tensioners that speak up "
          "as a start-up rattle.",
          "See the timing-belt answer on this seat "
          "for why this one carries the shop line."),
]

_INTERVAL_BY_RX = [(re.compile(i["rx"], re.I), i)
                     for i in INTERVALS]


def _fmt(x: float) -> str:
    if abs(x - round(x)) < 0.05:
        return str(int(round(x)))
    return f"{x:,.1f}".rstrip("0").rstrip(".")


def _result(tag: str, title: str, body: str,
            href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body,
             "href": href}]


def _step_text(diag: Dict) -> str:
    tree = _TREE_BY_ID.get(diag.get("tree", "")) or {}
    steps = tree.get("steps", [])
    idx = int(diag.get("step", 0) or 0)
    if idx >= len(steps):
        return tree.get("verdict", "")
    step = steps[idx]
    head = (f"Diagnosis — {tree.get('title', '')}. "
            f"Check {idx + 1} of {len(steps)} (one check "
            f"at a time — that's the discipline):\n"
            f"{step['check']}\n"
            "Do it, then tell me \"worked\" if it fixed "
            "it, or \"still\" if it didn't — I'll tell "
            "you what that rules out and hand you the "
            "next check.")
    safety = tree.get("safety", [])
    if idx == 0 and safety:
        head = "\n".join(safety) + "\n" + head
    return head


def _vehicle_text(rows: List[Dict]) -> str:
    if not rows:
        return ("No vehicles on your list yet. Tell me "
                "\"my car is a 2019 Traverse\" (year, "
                "make, model) and it goes on the books — "
                "intervals and diagnosis then speak to "
                "YOUR car, and every account keeps its "
                "own list.")
    lines = ["Your vehicles on the books:"]
    for v in rows:
        lines.append(f"• {v.get('name', '')}")
    lines.append(
        "Say \"forget the <name>\" to take one off, or "
        "name another to add it.")
    return "\n".join(lines)


def status_for(uid: str) -> Dict:
    """The read seam (/mechanic/status): the caller's
    own walk + their own vehicles."""
    out = {"diagnosis": None, "vehicles": []}
    diag = _diag(uid)
    if diag:
        tree = _TREE_BY_ID.get(diag.get("tree", "")) or {}
        out["diagnosis"] = {
            "tree": diag.get("tree", ""),
            "problem": tree.get("title", ""),
            "step": int(diag.get("step", 0) or 0) + 1,
            "steps": len(tree.get("steps", [])),
            "started": float(diag.get("started", 0) or 0),
        }
    for v in _vehicles(uid):
        if not isinstance(v, dict):
            continue
        out["vehicles"].append({
            "id": v.get("id", ""),
            "name": v.get("name", ""),
            "added": float(v.get("added", 0) or 0),
        })
    return out


# --- Operations: the diagnosis walk ---------------------------------------------------

def _do_diagnose(uid: str, claim: Dict) -> List[Dict]:
    tree = _TREE_BY_ID.get(claim.get("tree", ""))
    if not tree:
        return _do_diag_show(uid)
    old = _diag(uid)
    note = ""
    if old and old.get("tree") != tree["id"]:
        note = ("(Closing the earlier walk — one at a "
                "time.) ")
    veh = _vehicle_named(uid, claim.get("raw", ""))
    diag = {"tree": tree["id"], "step": 0,
            "status": "active", "started": time.time(),
            "vehicle": (veh or {}).get("name", "")}
    _save_diag(uid, diag)
    body = note + _step_text(diag)
    if veh and veh.get("name"):
        body = (f"Working on your {veh['name']}.\n"
                + body)
    if claim.get("guessed"):
        body = ("Taking the signature problem for that "
                "symptom — say the word if it's doing "
                "something else.\n" + body)
    return _result(_TAG, "Car Mechanic — diagnosis", body)


def _do_advance(uid: str, claim: Dict) -> List[Dict]:
    diag = _diag(uid)
    if not diag:
        return _result(
            _TAG, "Car Mechanic — no walk open",
            "No diagnosis is open right now — tell me "
            "the symptom (\"my car won't start\") and "
            "I'll walk it one check at a time, free "
            "checks first.")
    tree = _TREE_BY_ID.get(diag.get("tree", "")) or {}
    steps = tree.get("steps", [])
    idx = int(diag.get("step", 0) or 0)
    step = steps[idx] if idx < len(steps) else {}
    if claim.get("worked"):
        _save_diag(uid, None)
        body = (f"Fixed at check {idx + 1} of "
                f"{len(steps)}. {step.get('fix', '')} "
                "Walk closed — if it comes back, we "
                "start where the pattern points, not "
                "from zero.")
        return _result(_TAG, "Car Mechanic — fixed", body)
    ruled = step.get("means", "")
    nxt = idx + 1
    if nxt >= len(steps):
        _save_diag(uid, None)
        body = (f"That rules out: {ruled}\n"
                + tree.get("verdict", "")
                + _est_line(tree.get("est", "")))
        return _result(
            _TAG, "Car Mechanic — walked out", body)
    diag["step"] = nxt
    _save_diag(uid, diag)
    body = (f"That rules out: {ruled}\n"
            + _step_text(diag))
    return _result(_TAG, "Car Mechanic — diagnosis", body)


def _do_diag_show(uid: str) -> List[Dict]:
    diag = _diag(uid)
    if not diag:
        return _result(
            _TAG, "Car Mechanic — no walk open",
            "No diagnosis is open. Tell me the symptom "
            "(\"my truck cranks but won't start\", "
            "\"brakes are grinding\") and I'll start "
            "one, one check at a time.")
    return _result(_TAG, "Car Mechanic — diagnosis",
                   _step_text(diag))


def _do_close(uid: str) -> List[Dict]:
    had_diag = _diag(uid) is not None
    if had_diag:
        _save_diag(uid, None)
        return _result(
            _TAG, "Car Mechanic — closed",
            "Walk closed — nothing was ordered or "
            "changed. Name the symptom any time and we "
            "start again from check 1.")
    return _result(
        _TAG, "Car Mechanic — nothing open",
        "No walk is open here. Name a symptom and "
        "I'll start one.")


def _do_emergency(uid: str, claim: Dict) -> List[Dict]:
    kind = claim.get("kind", "")
    if kind == "brakes":
        body = ("STOP — failed brakes are not a "
                "diagnosis, they're an emergency handled "
                "in this order. If you're ROLLING: pump "
                "the pedal fast (it can build a little "
                "pressure), pull the parking brake on "
                "GRADUALLY (a hard yank locks the rears), "
                "steer for the softest stop you can find, "
                "hazards on. If you're STOPPED: do not "
                "drive it — not home, not 'just to the "
                "shop'. A car with no brakes gets a TOW, "
                "full stop; brake fluid on the ground or "
                "an empty master cylinder is the system "
                "telling you why. Tell me once you're "
                "parked safe and we'll talk about what "
                "the shop will find.")
    elif kind == "fuel":
        body = ("STOP — leaking fuel or fire/smoke "
                "from under the hood: kill the engine, "
                "everybody OUT and away from the car, "
                "and if there's flame or a leak you can "
                "see pooling, call 911 from a distance. "
                "Do NOT open the hood on a smoking engine "
                "(the rush of air feeds it) and do NOT "
                "try to start it 'one more time'. Gasoline "
                "does not give second chances. Once "
                "you're clear and it's safe, tell me and "
                "we'll talk next steps.")
    elif kind == "steering":
        body = ("STOP — steering that locked or quit "
                "is a tow, not a drive. If you're rolling: "
                "hazards on, brake gently in a straight "
                "line (the wheel still turns with effort "
                "if the power assist died — HEAVY is "
                "drivable-to-a-stop, LOCKED is muscle it "
                "straight only, don't fight it into a "
                "turn). Parked safe? It stays parked — "
                "a steering fault doesn't heal in the "
                "driveway, and the next failure gets to "
                "pick the moment. Tow it.")
    else:  # co — exhaust in the cabin
        body = ("STOP — exhaust fumes in the cabin "
                "are carbon monoxide, and CO's trick is "
                "that it makes you too drowsy to notice "
                "it. Windows DOWN now, pull over, engine "
                "off, everybody OUT into fresh air. "
                "Headache, dizziness, or nausea = 911 "
                "and get checked — CO poisoning is "
                "treated, not slept off. The car: an "
                "exhaust leak or a rusted rear seal is "
                "feeding it, and it does not get driven "
                "with the windows up until a shop clears "
                "the exhaust. Tell me once you're out "
                "and safe.")
    return _result(_TAG, "Car Mechanic — EMERGENCY", body)


# --- Operations: codes -------------------------------------------------------------------

def _code_unknown(code: str) -> str:
    head = (code[0] if code else "P").upper()
    system = {"P": "powertrain (engine / transmission)",
              "B": "body (comfort, airbags, lighting)",
              "C": "chassis (brakes, steering, "
                   "suspension)",
              "U": "network (modules talking to each "
                   "other)"}.get(head, "powertrain")
    second = ("factory-generic — every maker reads it "
              "the same" if len(code) > 1 and
              code[1] == "0" else
              "manufacturer-specific — its exact read "
              "is your maker's list")
    return (
        f"Code {code}: I don't carry that exact one on "
        f"my sheet, so here's the honest read of the "
        f"code itself — never a guess dressed as a "
        f"definition. The letter says the system: "
        f"'{head}' = {system}. The second digit says "
        f"whose dictionary: {second}. The rest names "
        f"the circuit or test that reported. The "
        f"discipline holds whatever it is: a code names "
        f"the system that REPORTED — the sensor, its "
        f"wiring, and the thing it watches are all "
        f"suspects until one is PROVEN. Bring me the "
        f"symptoms with it (running rough? light "
        f"flashing? anything else changed?) and we'll "
        f"rank what it points at — or a shop with the "
        f"maker's list reads it exact.")


def _do_code(uid: str, claim: Dict) -> List[Dict]:
    code = str(claim.get("code", "")).upper()
    entry = _code_entry(code)
    if not entry:
        return _result(
            _TAG, f"Car Mechanic — code {code}",
            _code_unknown(code))
    lines = [f"Code {code} — plain read: "
             f"{entry['means']}",
             "Likely causes, ranked:"]
    for i, c in enumerate(entry["causes"], 1):
        lines.append(f"{i}. {c}")
    lines.append(f"The proving check: {entry['check']}")
    lines.append(f"Lane: {entry['lane']}")
    body = "\n".join(lines) + _est_line(entry["est"])
    veh = _vehicle_named(uid, claim.get("raw", ""))
    if veh and veh.get("name"):
        body = (f"For your {veh['name']} —\n" + body)
    return _result(
        _TAG, f"Car Mechanic — code {code}", body)


# --- Operations: the shop-job lane ---------------------------------------------------------

def _do_shop(uid: str, claim: Dict) -> List[Dict]:
    job = _SHOP_BY_ID.get(claim.get("job", ""))
    if not job:
        return _do_diag_show(uid)
    entry = job.get("entry", {})
    body = entry.get("body", "") + _est_line(
        entry.get("est", ""))
    return _result(
        _TAG, "Car Mechanic — shop job", body)


# --- Operations: sounds ----------------------------------------------------------------------

def _do_sound(uid: str, claim: Dict) -> List[Dict]:
    raw = str(claim.get("raw", "")).lower()
    sound = claim.get("sound", "")
    if sound == "grinding" and re.search(
            r"\bbrak", raw):
        claim2 = dict(claim)
        claim2["tree"] = "brake_noise"
        claim2.pop("sound", None)
        return _do_diagnose(uid, claim2)
    if sound == "clicking" and re.search(
            r"\bwon'?t start|no start|turn the key|"
            r"starting|crank", raw):
        claim2 = dict(claim)
        claim2["tree"] = "no_start"
        claim2.pop("sound", None)
        return _do_diagnose(uid, claim2)
    key = ""
    if sound == "clicking" and re.search(
            r"\bturn", raw):
        key = "clicking_turn"
    elif sound in ("squealing", "squeal", "chirping") \
            and re.search(r"\bstart|cold|morning|first", raw):
        key = "squeal_start"
    elif sound in ("humming", "whining", "growling") \
            and re.search(r"\bspeed|mph|driv|road|highway|"
                          r"faster|slower", raw):
        key = "hum_speed"
    elif sound in ("clunking", "clunk", "rattling") \
            and re.search(r"\bbump|rough road|pothole|"
                          r"over\b", raw):
        key = "clunk_bumps" if sound != "rattling" \
            else "rattle_heat"
    elif sound in ("knocking", "knock") and re.search(
            r"\bengine|motor\b", raw):
        key = "knock_engine"
    if key and key in SOUND_READS:
        return _result(
            _TAG, "Car Mechanic — that sound",
            SOUND_READS[key] + _est_line(""))
    # A sound we can hear the shape of but not place:
    # name the map plainly and route to a walk.
    return _result(
        _TAG, "Car Mechanic — that sound",
        "Sounds are diagnoses in words — give me the "
        "context with it and I'll place it: WHEN does "
        "it happen (braking, turning, starting, bumps, "
        "one speed), and does it follow the ENGINE's "
        "revs or the ROAD's speed? The map on this "
        "seat: clicking while turning = CV joint; "
        "squeal at start-up = belt; hum that changes "
        "when you swerve = wheel bearing; clunk over "
        "bumps = sway links/suspension; grind while "
        "braking = pads gone (that one opens the brake "
        "walk). Say it with the when and we start.")


# --- Operations: vehicles -----------------------------------------------------------------------

_MAKES = (r"chevy|chevrolet|ford|dodge|ram|jeep|"
          r"toyota|honda|nissan|gmc|subaru|bmw|audi|"
          r"volkswagen|vw|hyundai|kia|mazda|tesla|"
          r"volvo|cadillac|buick|lincoln|chrysler|"
          r"mitsubishi|lexus|acura|infiniti|porsche|"
          r"land rover|mini|fiat|genesis|rivian|"
          r"lucid|mercedes|scion|pontiac|saturn|"
          r"mercury|plymouth|hummer")
_SYMPTOM_WORDS = (r"broken|dead|making|leaking|"
                  r"smoking|stalling|leaks|smokes|"
                  r"loud|hot|dying|won'?t|wont|"
                  r"squealing|grinding|clicking|"
                  r"overheating|vibrating|shaking")


def _vehicle_descriptor(text: str) -> str:
    """A vehicle descriptor out of an add-frame, or
    '' — the fence: it must carry a year or a known
    make, and it must NOT be a symptom sentence
    ('my car is broken' is not a vehicle)."""
    raw = str(text or "").strip().strip(".!")
    if not raw or len(raw) > 60:
        return ""
    if re.search(_SYMPTOM_WORDS, raw, re.I):
        return ""
    if not (re.search(r"\b(?:19|20)\d\d\b", raw)
            or re.search(_MAKES, raw, re.I)):
        return ""
    return raw


def _do_vehicle_add(uid: str, claim: Dict) -> List[Dict]:
    desc = str(claim.get("desc", "")).strip()
    if not desc:
        return _result(
            _TAG, "Car Mechanic — which vehicle?",
            "Give it to me as year + make + model — "
            "\"my car is a 2019 Chevy Traverse\" — and "
            "it goes on your books.")
    rows = _vehicles(uid)
    for v in rows:
        if str(v.get("name", "")).lower() == desc.lower():
            return _result(
                _TAG, "Car Mechanic — already on the books",
                f"Your {desc} is already listed.\n"
                + _vehicle_text(rows))
    rows.append({"id": uuid.uuid4().hex[:12],
                 "name": desc, "added": time.time()})
    _save_vehicles(uid, rows)
    try:
        import og_notify
        og_notify.record(
            uid, "mechanic",
            f"Vehicle on the books — {desc}",
            "The Car Mechanic now speaks to this "
            "vehicle; intervals and diagnosis use it "
            "as context.")
    except Exception as e:
        logger.warning(f"Mechanic notify failed: {e}")
    return _result(
        _TAG, "Car Mechanic — vehicle added",
        f"On the books: {desc}. Diagnosis and "
        f"intervals for it come from this seat — and "
        f"its exact fluid specs live in its owner's "
        f"manual (the cap names the oil; I'll always "
        f"point you at the spec rather than invent "
        f"one).\n" + _vehicle_text(_vehicles(uid)))


def _do_vehicle_list(uid: str) -> List[Dict]:
    return _result(
        _TAG, "Car Mechanic — your vehicles",
        _vehicle_text(_vehicles(uid)))


def _do_vehicle_remove(uid: str, claim: Dict) -> List[Dict]:
    rows = _vehicles(uid)
    if not rows:
        return _result(
            _TAG, "Car Mechanic — nothing to forget",
            "Your vehicle list is already empty.")
    raw = str(claim.get("raw", "")).lower()
    hit = None
    for v in rows:
        toks = [t for t in re.split(
            r"[^a-z0-9]+", str(v.get("name", "")).lower())
            if len(t) >= 3 and not t.isdigit()]
        if any(t in raw for t in toks):
            hit = v
            break
    if hit is None and len(rows) == 1:
        hit = rows[0]
    if hit is None:
        return _result(
            _TAG, "Car Mechanic — which one?",
            "Name the one to forget:\n"
            + _vehicle_text(rows))
    rows = [v for v in rows if v.get("id") != hit["id"]]
    _save_vehicles(uid, rows)
    return _result(
        _TAG, "Car Mechanic — forgotten",
        f"Forgotten: {hit.get('name', '')}. Your "
        f"list is yours — add another any time with "
        f"\"my car is a …\".\n"
        + _vehicle_text(rows))


# --- Operations: intervals --------------------------------------------------------------------------

def _do_intervals(uid: str, claim: Dict) -> List[Dict]:
    item = claim.get("item")
    veh = _vehicle_named(uid, claim.get("raw", ""))
    vname = (veh or {}).get("name", "")
    head = (f"Maintenance intervals"
            + (f" for your {vname}" if vname else "")
            + " — planning numbers for the common "
              "fleet, labeled as such: they vary by "
              "make/model/year and use, and your "
              "owner's manual is the final word on "
              "every one.")
    if item:
        rows = [i for i in INTERVALS
                if i["name"] == item]
        if rows:
            i = rows[0]
            body = (head + f"\n• {i['name']}: "
                    f"{i['spec']}"
                    + (f" ({i['note']})" if i["note"]
                       else "")
                    + "\nSay \"maintenance intervals\" "
                      "for the whole sheet.")
            return _result(
                _TAG, "Car Mechanic — intervals", body)
    lines = [head]
    for i in INTERVALS:
        lines.append(f"• {i['name']}: {i['spec']}")
    lines.append(
        "Fluid specs (oil weight, coolant type, ATF "
        "type) are the car's own — printed on the cap "
        "or in the manual. I point at the spec; I "
        "don't invent one for a vehicle I haven't "
        "been told.")
    return _result(
        _TAG, "Car Mechanic — intervals",
        "\n".join(lines))


# --- Operations: ordering from context ----------------------------------------------------------------

def _do_order(uid: str, claim: Dict) -> List[Dict]:
    diag = _diag(uid)
    if diag:
        tree = _TREE_BY_ID.get(diag.get("tree", "")) or {}
        parts = tree.get("parts", [])
        head = (f"Parts in play on the open diagnosis "
                f"({tree.get('title', '')}):")
        sheet = "\n".join(f"• {p}" for p in parts) \
            or "• (this walk's parts name themselves " \
               "as the checks land)"
    else:
        entry = None
        code = str(claim.get("code", "") or "").upper()
        if code:
            entry = _code_entry(code)
        if entry:
            head = (f"Parts in play on code {code} "
                    f"(ranked causes, cheapest proof "
                    f"first):")
            sheet = "\n".join(
                f"• {c}" for c in entry["causes"])
        else:
            return _result(
                _TAG,
                "Car Mechanic — nothing to list",
                "No open diagnosis and no code on the "
                "table here, so there's no parts list "
                "to order from. Start a walk (\"my car "
                "won't start\") or bring a code "
                "(\"code P0420\") and the parts name "
                "themselves.")
    body = (head + "\n" + sheet + "\n"
            "Buying runs through OG's ordering pack "
            "like always — it prepares the order, you "
            "approve the preview, then it hands off. "
            "I don't order anything from here; this "
            "sheet is the list to take in.")
    return _result(
        _TAG, "Car Mechanic — parts sheet", body)


def mechanic_results(claim: Dict, uid: str) -> List[Dict]:
    op = claim.get("op")
    if op == "mc_emergency":
        return _do_emergency(uid, claim)
    if op == "mc_diagnose":
        return _do_diagnose(uid, claim)
    if op == "mc_advance":
        return _do_advance(uid, claim)
    if op == "mc_diag_show":
        return _do_diag_show(uid)
    if op == "mc_close":
        return _do_close(uid)
    if op == "mc_code":
        return _do_code(uid, claim)
    if op == "mc_shop":
        return _do_shop(uid, claim)
    if op == "mc_sound":
        return _do_sound(uid, claim)
    if op == "mc_vehicle_add":
        return _do_vehicle_add(uid, claim)
    if op == "mc_vehicle_list":
        return _do_vehicle_list(uid)
    if op == "mc_vehicle_remove":
        return _do_vehicle_remove(uid, claim)
    if op == "mc_intervals":
        return _do_intervals(uid, claim)
    if op == "mc_order":
        return _do_order(uid, claim)
    return _do_diag_show(uid)


# --- Chat claims (narrow frames — the crew/PM/filer doctrine) -------------------------

_GARBLE_VOCAB = ("car", "truck", "mechanic", "battery",
                 "brakes", "starter", "alternator",
                 "engine", "clicking", "grinding",
                 "squealing", "code", "coolant", "spark",
                 "plugs", "tires", "vehicle",
                 "transmission", "radiator",
                 "overheating")

_VEHICLE_RX = re.compile(
    r"\b(?:car|truck|suv|vehicle|auto|pickup|van)\b",
    re.I)

_EMERG_BRAKES_RE = re.compile(
    r"\bbrakes?\b[^.?!]*\b(?:failed|gone|went out|"
    r"stopped working|don'?t work|not working|out)\b"
    r"|\bbrake pedal\b[^.?!]*\b(?:floor|to the floor|"
    r"sinks?|goes to)\b|\bno brakes\b|\blost (?:my |the )?"
    r"brakes\b|\bi have no brakes\b", re.I)
_EMERG_FUEL_RE = re.compile(
    r"\bfuel\b[^.?!]*\b(?:leak\w*|dripping|pouring|"
    r"puddle|spraying)\b"
    r"|\b(?:gasoline|gas)\b[^.?!]*\bleak\w*\b[^.?!]*"
    r"\b(?:car|truck|under|tank|garage)\b"
    r"|\bfire\b[^.?!]*\b(?:hood|engine|car|truck)\b"
    r"|\bflames?\b[^.?!]*\b(?:hood|engine|car|truck)\b"
    r"|\bsmoke\b[^.?!]*\b(?:from )?(?:under )?(?:the )?"
    r"hood\b|\bunder the hood\b[^.?!]*\bsmok\w*\b", re.I)
_EMERG_STEER_RE = re.compile(
    r"\bsteering\b[^.?!]*\b(?:locked|failed|stopped "
    r"working|won'?t turn|gone|quit)\b"
    r"|\bsteering wheel\b[^.?!]*\b(?:locked|won'?t "
    r"turn|stuck|won't move)\b", re.I)
_EMERG_CO_RE = re.compile(
    r"\bexhaust\b[^.?!]*\b(?:in (?:the|my) (?:car|"
    r"cabin)|inside)\b|\bfumes\b[^.?!]*\b(?:in (?:the|"
    r"my) (?:car|truck|cabin)|inside)\b", re.I)

_CODE_TOKEN_RE = re.compile(r"\b([PCBU][0-9A-F]{4})\b")
_CODE_CTX_RE = re.compile(
    r"\bcode\b|\bobd\b|\bcheck engine\b|\bmean\w*\b|"
    r"\bthrowing\b|\bthrew\b|\bscan\b|\bdiagnostic\b",
    re.I)

_VEHICLE_ADD_RES = (
    re.compile(r"^my\s+(?:car|truck|vehicle|suv|van)"
               r"\s+is\s+(?:a\s+|an\s+)?(.+)$", re.I),
    re.compile(r"^i\s+drive\s+(?:a\s+|an\s+)?(.+)$",
               re.I),
    re.compile(r"^(?:add|save|remember|put\s+down)"
               r"\s+(?:my\s+)?(?:car|truck|vehicle|suv|"
               r"van)\s*[:\-]\s*(.+)$", re.I),
    re.compile(r"^add\s+(?:a\s+)?vehicle\s*[:\-]\s*"
               r"(.+)$", re.I),
)
_VEHICLE_LIST_RE = re.compile(
    r"\bwhat\s+(?:cars?|trucks?|vehicles?)\s+do\s+"
    r"(?:i|you)\s+have\b|\bmy\s+vehicles\b|\blist\s+"
    r"(?:my\s+)?(?:cars?|vehicles?)\b|\bwhat'?s\s+my\s+"
    r"(?:car|truck|vehicle)\b|\bshow\s+(?:my\s+)?"
    r"vehicles\b", re.I)
_VEHICLE_REMOVE_RE = re.compile(
    r"^(?:forget|remove|delete|drop|take\s+off)\s+"
    r"(?:my\s+|the\s+)?.+", re.I)
_CLOSE_RE = re.compile(
    r"^(?:scrap|cancel|forget|drop|kill|close|stop)\s+"
    r"(?:the\s+|my\s+|that\s+|this\s+)?(?:diagnosis|"
    r"walk|troubleshoot\w*)\b|^never\s*mind$", re.I)
_SHOW_RE = re.compile(
    r"^(?:show|where were we|what'?s|what is|repeat)"
    r"\b[^.?!]*\b(?:check|step|diagnosis|walk)\b"
    r"|^(?:the\s+)?(?:current\s+)?check$", re.I)
_WORKED_RE = re.compile(
    r"\b(?:that|it)\s+(?:worked|fixed\s+it)\b|\bfixed\s+"
    r"it\b|\bworks\s+now\b|\bworking\s+now\b|\bit'?s\s+"
    r"working\b|\bback\s+on\b|^(?:yes|yeah|yep)$",
    re.I)
_STILL_RE = re.compile(
    r"\bstill\b|\bno\s+luck\b|\bdidn'?t\s+work\b"
    r"|\bnot\s+working\b|\bno\s+change\b|\bnothing\b"
    r"|\bnext\b|^(?:no|nope|done|checked|did\s+it)$",
    re.I)
_ORDER_RE = re.compile(
    r"\b(?:order|buy|purchase|get)\b[^.?!]*\b(?:parts?|"
    r"battery|starter|alternator|pads?|rotors?|filter|"
    r"plugs?|thermostat|pump|everything|it all|"
    r"the list|stuff)\b", re.I)
_INTERVALS_RE = re.compile(
    r"\bmaintenance\s+(?:intervals?|schedule)\b"
    r"|\bhow\s+often\b[^.?!]*\b(?:change|replace|"
    r"flush|rotate|swap|do)\b|\bwhen\s+(?:should|do)\s+"
    r"i\s+(?:change|replace|flush|rotate|swap)\b"
    r"|\bservice\s+intervals?\b", re.I)
_SOUND_WORDS = ("clicking", "clunking", "grinding",
                "squealing", "squeal", "humming",
                "whining", "knocking", "rattling",
                "ticking", "chirping", "screeching",
                "growling", "clunk", "knock")
_SOUND_RE = re.compile(
    r"\b(" + "|".join(_SOUND_WORDS) + r")\b[^.?!]*"
    r"\b(?:noise|sound|when|coming|from)\b"
    r"|\b(?:noise|sound)\b[^.?!]*\b("
    + "|".join(_SOUND_WORDS) + r")\b"
    r"|\bmakes?\s+a\s+("
    + "|".join(_SOUND_WORDS) + r")\b", re.I)


def _find_tree(text: str) -> Optional[Dict]:
    """Symptom -> tree. Equipment/symptom phrases are
    car vocabulary; the two shared-vocabulary trees
    (AC, vibration) demand a vehicle anchor so the
    house's seats are never eaten."""
    raw = str(text or "").lower()
    veh = bool(_VEHICLE_RX.search(raw))
    if re.search(r"\bcheck engine\b|\bservice engine "
                 r"soon\b", raw):
        return _TREE_BY_ID["cel_light"]
    if re.search(r"\bwon'?t start\b|\bwill not start\b"
                 r"|\bno start\b|\bwon'?t crank\b"
                 r"|\bwon'?t turn over\b|\bnot starting\b"
                 r"|\bdoesn'?t start\b", raw):
        return _TREE_BY_ID["no_start"]
    if re.search(r"\bbattery\b[^.?!]*\b(?:keeps "
                 r"(?:dying|going dead)|dead every|dies "
                 r"overnight|dead again|keeps dying)\b"
                 r"|\bdead battery\b[^.?!]*\b(?:again|"
                 r"every|morning|overnight)\b", raw):
        return _TREE_BY_ID["battery_drain"]
    if re.search(r"\bbrakes?\b[^.?!]*\b(?:squeal\w*|"
                 r"grind\w*|screech\w*|noise|sound|"
                 r"pulsat\w*)\b|\bgrinding\b[^.?!]*"
                 r"\bbrak", raw):
        return _TREE_BY_ID["brake_noise"]
    if re.search(r"\boverheat\w*\b|\btemp(?:erature)? "
                 r"(?:gauge|light)\b|\brunning hot\b|"
                 r"\bgauge\b[^.?!]*\b(?:red|climb\w*|"
                 r"rising|high)\b", raw):
        return _TREE_BY_ID["overheat"]
    if re.search(r"\brough idle\b|\bidles? rough\b|"
                 r"\bruns rough\b|\bmisfir\w*\b|\bstalls?\b"
                 r"[^.?!]*\b(?:at idle|when I stop|"
                 r"in traffic)?\b|\bshakes? at idle\b",
                 raw):
        return _TREE_BY_ID["rough_idle"]
    if veh and re.search(r"\b(?:ac|a/c|air "
                         r"condition\w*)\b", raw) \
            and re.search(r"\bwarm\b|\bnot cold\b|"
                          r"\bwon'?t cool\b|\bno cold\b|"
                          r"\bblows hot\b|\bhot air\b", raw):
        return _TREE_BY_ID["ac_warm"]
    if (veh or re.search(r"\bpulls? to the\b", raw)) \
            and re.search(r"\bvibrat\w*\b|\bshakes?\b|"
                          r"\bshimm\w*\b|\bwobbl\w*\b|"
                          r"\bpulls? to the\b", raw):
        return _TREE_BY_ID["vibration"]
    if re.search(r"\b(?:puddle|fluid|leak|leaking|"
                 r"dripping)\b", raw) and veh:
        return _TREE_BY_ID["fluid_leak"]
    # Signature default: "my car is dead" / "car died"
    # alone starts the no-start walk, named as a guess.
    if veh and re.search(r"\b(?:dead|died|won'?t go)\b",
                         raw):
        return _TREE_BY_ID["no_start"]
    return None


def _other_claim(message: str, uid: str):
    """(source, claim) of the first inner seat that
    claims this message exactly, else None. The PM,
    the Filer, the crew, the Builder, the Wood Pro
    and MaintEng own their asks — checked exactly."""
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
        job, _info = og_builder._claim_builder(
            message, uid)
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
    return None


def _defer_to_others(message: str, uid: str) -> bool:
    """True when another seat owns this ask. The two
    named exceptions: with an explicit vehicle anchor,
    MaintEng's home-AC tree and its no-equipment
    schedule ask-back yield to this seat (car AC and
    vehicle intervals are this seat's SPEC lanes) —
    everything else defers whole."""
    other = _other_claim(message, uid)
    if not other:
        return False
    src, oclaim = other
    if src == "mainteng" and isinstance(oclaim, dict) \
            and _VEHICLE_RX.search(str(message or "")):
        op = oclaim.get("op")
        if op == "me_diagnose" and \
                oclaim.get("tree") == "ac_no_cool":
            return False
        if op == "me_sched_create" and \
                not oclaim.get("equip"):
            return False
    return True


def _claim_exact(message: str, uid: str) -> Optional[Dict]:
    raw = str(message or "").strip()
    if not raw or not uid:
        return None
    # Emergencies first — before every defer and every
    # frame: the answer is the emergency line, full stop.
    if _EMERG_BRAKES_RE.search(raw):
        return {"op": "mc_emergency", "kind": "brakes"}
    if _EMERG_FUEL_RE.search(raw):
        return {"op": "mc_emergency", "kind": "fuel"}
    if _EMERG_STEER_RE.search(raw):
        return {"op": "mc_emergency", "kind": "steering"}
    if _EMERG_CO_RE.search(raw):
        return {"op": "mc_emergency", "kind": "co"}
    if _defer_to_others(raw, uid):
        return None
    diag = _diag(uid)
    # An open walk owns its advance words.
    if diag:
        if _CLOSE_RE.search(raw):
            return {"op": "mc_close"}
        if _WORKED_RE.search(raw):
            return {"op": "mc_advance", "worked": True}
        if _STILL_RE.search(raw):
            return {"op": "mc_advance", "worked": False}
        if _SHOW_RE.search(raw):
            return {"op": "mc_diag_show"}
    # Vehicles: add (fenced descriptor), list, remove.
    for rx in _VEHICLE_ADD_RES:
        m = rx.match(raw)
        if m:
            desc = _vehicle_descriptor(m.group(1))
            if desc:
                return {"op": "mc_vehicle_add",
                        "desc": desc, "raw": raw}
    if _VEHICLE_LIST_RE.search(raw):
        return {"op": "mc_vehicle_list"}
    if _VEHICLE_REMOVE_RE.match(raw) and _vehicles(uid):
        return {"op": "mc_vehicle_remove", "raw": raw}
    # OBD codes: the token + a code context (or a
    # near-bare message that is just the code).
    m = _CODE_TOKEN_RE.search(raw.upper())
    if m and (_CODE_CTX_RE.search(raw)
              or len(raw.split()) <= 4):
        return {"op": "mc_code", "code": m.group(1),
                "raw": raw}
    # The shop-job lane before trees: these topics get
    # the line, never a walk.
    for s in SHOP_JOBS:
        if re.search(s["rx"], raw, re.I):
            return {"op": "mc_shop", "job": s["id"],
                    "raw": raw}
    # Sounds, described in words.
    m = _SOUND_RE.search(raw)
    if m:
        sound = next(g for g in m.groups() if g)
        return {"op": "mc_sound", "sound": sound.lower(),
                "raw": raw}
    # Diagnosis starts.
    tree = _find_tree(raw)
    if tree:
        guessed = not re.search(
            r"\bno\b|\bnot\b|\bwon'?t\b|\blow\b|\btrip",
            raw, re.I)
        return {"op": "mc_diagnose", "tree": tree["id"],
                "guessed": guessed, "raw": raw}
    # Intervals.
    if _INTERVALS_RE.search(raw):
        item = ""
        for rx, it in _INTERVAL_BY_RX:
            if rx.search(raw):
                item = it["name"]
                break
        return {"op": "mc_intervals", "item": item,
                "raw": raw}
    # Ordering from context (an open walk, or a code
    # on the table in this very ask).
    m = _CODE_TOKEN_RE.search(raw.upper())
    if _ORDER_RE.search(raw) and (diag or m):
        return {"op": "mc_order", "raw": raw,
                "code": m.group(1) if m else ""}
    return None


def _claim_mechanic(message: str, uid: str):
    """(claim, garble_info): the exact claim first; on
    a miss, the Round 75 second chance — og_garble
    repairs the TRIGGER words only; the payload
    passes verbatim."""
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


# --- The seam -------------------------------------------------------------------------

_pending = {"claim": None, "garble": None}


def install_mechanic_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search OUTSIDE the
    Maintenance + Engineer seam (installed last, from
    the og_fixqueue chain) so car asks claim here and
    everything else delegates back untouched. Persona
    files never touched."""
    if getattr(agent_instance, "_og_mechanic_installed",
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
            if uid and mechanic_enabled():
                claim, info = _claim_mechanic(
                    str(message), uid)
                if claim:
                    _pending["claim"] = claim
                    _pending["garble"] = info
                    if isinstance(intent, dict):
                        intent[
                            "needs_code_generation"] = False
                        if not intent.get("needs_web_search"):
                            intent["needs_web_search"] = True
                            intent["search_query"] = \
                                str(message).strip()
        except Exception as e:
            logger.warning(f"Mechanic trigger check failed: {e}")
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
                results = mechanic_results(claim, uid)
            except Exception as e:
                logger.warning(f"Mechanic op failed: {e}")
                results = _result(
                    _TAG, "Car Mechanic — glitch",
                    "That ask glitched — nothing was "
                    "ordered or changed. Say it again "
                    "and I'll retry.")
        if results is None:
            results = prev_search(query, num_results)
        if garble and results:
            results = og_garble.annotate(results, garble,
                                         prepend=True)
        return results

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_mechanic_installed = True


# --- Routes ----------------------------------------------------------------------------

def register_mechanic_routes(app):
    """GET /mechanic/status — the caller's own walk +
    their own vehicles. Identity is the caller's
    cookie uid, like every Round 95+ surface."""

    @app.get("/mechanic/status")
    def mechanic_status_route(request: Request):
        uid = request.cookies.get("ogai_uid") or ""
        view = status_for(uid) if uid else {
            "diagnosis": None, "vehicles": []}
        return {"ok": True, "enabled": mechanic_enabled(),
                **view}
