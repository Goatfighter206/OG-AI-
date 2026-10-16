"""Round 113: THE MAINTENANCE + ENGINEER HELPER
(Brent's order, 2026-10-12 11:11 PDT: "a maintenance
+ engineer helper" — specced in round-113/SPEC.md.)

WHAT THIS IS. The crew's maintenance-and-engineering
specialist for the BUILT WORLD — HVAC, electrical,
plumbing, pumps, motors, building systems. Two crafts:

  - TROUBLESHOOTING with diagnosis discipline:
    symptoms -> the ONE best first check -> what the
    result means -> the next step. Deterministic trees
    (furnace no-heat, no hot water, AC no-cool, fridge
    warm, dryer no-heat, washer no-drain, tripping
    breaker, running toilet, low water pressure) walked
    one check at a time — the user does the check and
    says "worked" or "still", and the walk moves. A
    gas smell / CO alarm / sparking answer is an
    EMERGENCY line first, always.
  - PREVENTIVE-MAINTENANCE SCHEDULES for named
    equipment (filter changes, tank flushes, vent
    cleaning, alarm tests...), stored per account with
    real intervals and next-due dates computed from
    them. "What's due" answers from the schedule;
    "mark the filter done" pushes its next due out by
    the interval. A schedule hands off through the
    shipped seams — a Project Manager project carries
    it (og_pm._create_project), the schedule document
    lands in the locker (og_storage.save_bytes), and
    the Filer links it (og_filing.file_link).
  - ENGINEERING MATH with the work SHOWN: concrete
    volume (+waste, +bags), tank gallons, slope/grade,
    a beam span check (bending stress + deflection
    against stated planning values), floor/deck load
    in psf, and a small unit converter. Every number
    is computed from the ask, formulas printed.

THE LICENSED-PRO LINE. The math is planning-grade
reasoning, never an engineer's stamp. Anything
structural or life-safety carries the plain line:
get a licensed pro to sign off on the real thing.
OG still gives the math + the reasoning — it just
never dresses them as a formal sign-off (drilled).

THE FENCES.
- Knowledge + guidance ONLY. The module imports no
  ordering/trading machinery (suite-pinned): "order
  a filter" returns the context's own parts sheet +
  the ordering pack's approval routing. Never buys.
- Per-account isolation (drill-proven cross-account).
- NO new loop (R72): schedules are dates on a stored
  record, read on exchanges and on the status route —
  nothing polls, nothing ticks.
- Persona untouched; app.py never touched (install
  rides the og_fixqueue chain AFTER the Wood Pro —
  this seam wraps outermost and defers to the
  PM/Filer/crew/Builder/WoodPro claims checked
  exactly first).

STATE. Own durable store, the Round 18 pattern:
Postgres table og_mainteng_data when OG_MEMORY_DB_URL
is set, else a JSON file. Blob kinds per visitor:
'diag' (the active walk) and 'schedules'. Switch:
OG_MAINTENG_ENABLED (unset = ON), the SPEC's name.
"""

import json
import logging
import math
import os
import re
import threading
import time
import uuid
from typing import Dict, List, Optional

import og_garble
import og_builder

from fastapi import Request

logger = logging.getLogger(__name__)

_TAG = "OG MAINTENANCE + ENGINEER"

PRO_LINE = (
    "Plain line, because this is structural / "
    "life-safety territory: this math is planning-grade "
    "reasoning, NOT a licensed engineer's stamp — get a "
    "licensed pro to sign off before you build on it. "
    "The numbers above are real; the sign-off has to "
    "come from somebody licensed to give it.")

# --- Config -------------------------------------------------------------------

SCHEDULE_KEEP = 12       # schedules kept per visitor
DOC_CAP = 14000          # chars of the saved schedule document
DAY = 86400.0

MAINTENG_STORE_FILE = "mainteng_store.json"
# RLock, not Lock: store updates hold the lock across a
# read-modify-write whose save path re-enters it through
# _put_blob's file branch (the og_builder/og_woodpro shape).
_mainteng_lock = threading.RLock()

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, StoreError, apply_bounds as _apply_bounds
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None


# --- Switch ---------------------------------------------------------------------

def mainteng_enabled() -> bool:
    """OG_MAINTENG_ENABLED=false is the kill switch.
    Unset = ON."""
    val = os.environ.get("OG_MAINTENG_ENABLED")
    if val is not None and val.strip().lower() in (
            "0", "false", "no", "off"):
        return False
    return True


# --- Durable store (Round 18 pattern, mirroring og_woodpro) ----------------------

def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_mainteng_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(MAINTENG_STORE_FILE):
        try:
            with open(MAINTENG_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"MaintEng store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(MAINTENG_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"MaintEng store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_mainteng_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"MaintEng DB load failed: {e}")
            raise StoreError("mainteng store unavailable (Round 99)") from e
    with _mainteng_lock:
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
                        "INSERT INTO og_mainteng_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"MaintEng DB save failed: {e}")
            raise StoreError("mainteng store unavailable (Round 99)") from e
    with _mainteng_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


# --- State: the active diagnosis walk + the schedules ----------------------------

def _diag(uid: str) -> Optional[Dict]:
    got = _blob(uid, "diag", None)
    if isinstance(got, dict) and got.get("status") == "active":
        return got
    return None


def _save_diag(uid: str, diag: Optional[Dict]):
    _put_blob(uid, "diag", diag if diag is not None else {})


def _schedules(uid: str) -> List[Dict]:
    got = _blob(uid, "schedules", [])
    return got if isinstance(got, list) else []


def _save_schedules(uid: str, rows: List[Dict]):
    rows.sort(key=lambda s: float(s.get("created", 0) or 0))
    _put_blob(uid, "schedules", rows[-SCHEDULE_KEEP:])


def _get_schedule(uid: str, schedule_id: str) -> Optional[Dict]:
    for s in _schedules(uid):
        if isinstance(s, dict) and s.get("id") == schedule_id:
            return s
    return None


# --- Troubleshooting trees -------------------------------------------------------
# Each step: the ONE best check, what it means when that check
# FIXES it (fix), and what moving past it rules out (means).
# The walk renders one step at a time; "worked" closes with
# the fix line, "still" prints the ruled-out line and advances.

def _step(check, fix, means):
    return {"check": check, "fix": fix, "means": means}


TREES = [
    {
        "id": "furnace_no_heat", "equip": "furnace",
        "problem": "no heat",
        "safety": [
            "Safety first: if you smell gas (rotten eggs) at "
            "any point — stop, no switches, get out, and call "
            "the gas company from outside.",
        ],
        "steps": [
            _step(
                "Thermostat: mode on HEAT, set 5 degrees above "
                "the room temp, fan on AUTO. If it's a battery "
                "stat, swap in fresh batteries.",
                "That was it — a stat calling wrong or running "
                "on dead batteries is the #1 no-heat service "
                "call, and you just saved it.",
                "the stat is calling fine — the failure is "
                "downstream at the furnace itself."),
            _step(
                "Power: the furnace has a breaker AND a wall "
                "switch by the unit (looks like a light switch). "
                "Both ON. Flip the breaker fully OFF, then ON, "
                "exactly once.",
                "A breaker that trips once and holds is fine. "
                "If it trips again — stop there; a repeat trip "
                "is a short, and that's a pro call, not a "
                "retry loop.",
                "power is reaching the unit."),
            _step(
                "Filter: pull it and hold it up to a light. If "
                "you can't see light through it, it's plugged — "
                "a plugged filter overheats the furnace and "
                "trips its limit switch, which kills the heat.",
                "New filter, same size as printed on the frame, "
                "airflow arrow pointing TOWARD the furnace. "
                "Give it 10 minutes for the limit to reset, "
                "then call for heat again.",
                "airflow is fine — it isn't a limit-switch trip."),
            _step(
                "Watch one heat call at the unit: inducer fan "
                "spins up first, then the igniter glows orange, "
                "then the burners light. Note exactly where the "
                "sequence stops.",
                "Where it stops names the part: no inducer hum "
                "= inducer or control fault; glow but no flame "
                "= igniter or gas valve; flame that dies in "
                "seconds = a dirty flame sensor (power OFF, "
                "wipe the rod with a dollar bill, retry).",
                "the start sequence is running end to end — "
                "so ignition is not the break."),
            _step(
                "Error code: the control board blinks an LED "
                "pattern (long-short). Count it and match it "
                "against the chart printed inside the blower "
                "door.",
                "That code names the failed circuit — it IS "
                "the diagnosis, and it's what a tech starts "
                "from when they walk in.",
                "the board isn't flagging a stored fault."),
        ],
        "verdict": (
            "You've ruled out the stat, the power, the filter, "
            "and the start sequence, one check at a time. "
            "What's left is a failed part in the ignition or "
            "control chain — that's a tech call, and you can "
            "hand them exactly what you checked, which is the "
            "part of the visit you'd otherwise pay to watch."),
        "parts": ["furnace filter (size on the frame)",
                  "hot-surface igniter", "flame sensor"],
    },
    {
        "id": "wh_no_hot", "equip": "water heater",
        "problem": "no hot water",
        "safety": [
            "Safety: keep the tank at 120°F — hotter scalds, "
            "and kids get it worst. Never plug a dripping T&P "
            "relief valve; a drip there is the valve doing "
            "its job or asking for a new one.",
        ],
        "steps": [
            _step(
                "Breaker + reset (electric) or pilot (gas). "
                "Electric: reset the water-heater breaker once "
                "(fully OFF, then ON), then the red high-limit "
                "reset button on the upper thermostat — breaker "
                "OFF while the cover is off, press it, cover "
                "back, breaker on. Gas: look through the sight "
                "glass — is the pilot lit?",
                "A popped high-limit reset that STAYS reset "
                "buys you hot water today — but it popped for "
                "a reason, so if it pops again, an element or "
                "thermostat is failing and that's the repair.",
                "power/pilot is not the break."),
            _step(
                "Settings: the tank thermostat(s) — electric "
                "tanks have TWO, upper and lower, and they must "
                "match. Set both to 120°F. One bumped to "
                "vacation or zero is a free fix.",
                "Settings it is — give it an hour to recover "
                "before you judge it.",
                "the settings weren't the story."),
            _step(
                "Read the pattern: hot for a few minutes, then "
                "cold? Electric tanks: that's the LOWER element "
                "burned out (the upper one heats the top of "
                "the tank, then there's nothing). Rumbling or "
                "popping while a gas tank heats = sediment "
                "cooked onto the bottom.",
                "The pattern named the part — lower element "
                "on electric, sediment flush (and often the "
                "anode conversation) on gas.",
                "no clean pattern — keep walking."),
            _step(
                "Sediment: if the tank has never been flushed "
                "and it's older, drain a few gallons from the "
                "bottom valve into a bucket. Rusty or gritty "
                "water confirms the tank is loading up.",
                "Grit in the bucket = the flush was overdue; "
                "a full flush is the fix, and it quiets the "
                "rumbling too.",
                "the water ran clean — sediment isn't it."),
        ],
        "verdict": (
            "Stat, reset, settings, and sediment are all "
            "cleared. What remains is element/thermostat "
            "testing with a meter (electric) or gas-valve / "
            "thermocouple work (gas) — honest plumber "
            "territory, and you can tell them exactly what's "
            "already been ruled out."),
        "parts": ["lower heating element", "upper/lower "
                  "thermostat", "thermocouple (gas)"],
    },
    {
        "id": "ac_no_cool", "equip": "air conditioner",
        "problem": "no cooling",
        "safety": [
            "Safety: never open the outdoor unit's electrical "
            "box — its capacitor holds a charge with the power "
            "off. Eyes and ears only outside.",
        ],
        "steps": [
            _step(
                "Thermostat: mode on COOL, set 5 degrees below "
                "room temp, fan AUTO. Some stats delay up to 5 "
                "minutes protecting the compressor — give it "
                "that long before you judge.",
                "Stat-side fix — watch it through one full "
                "cycle to be sure it holds.",
                "the stat is calling — the break is at the "
                "equipment."),
            _step(
                "Filter: same test as heat — hold it to a "
                "light. A plugged filter starves the coil until "
                "it ices, and an iced coil moves almost no air.",
                "New filter in, and if the indoor coil iced, "
                "run fan-only for 2–3 hours to thaw it fully "
                "before cooling again — cooling on ice just "
                "rebuilds it.",
                "airflow is fine."),
            _step(
                "Outdoor unit: is the big fan spinning and the "
                "compressor humming? Feel the two copper lines: "
                "the fat insulated one should be cold and "
                "sweating, the thin one warm. Fat line FROSTY "
                "= stop, that's ice, not cooling.",
                "Fan not turning but humming = start capacitor "
                "territory (a tech swap — see the safety line). "
                "Frost = airflow or charge; thaw it fully "
                "first, fan-only.",
                "the outdoor unit is running its side of the "
                "job."),
            _step(
                "Breakers: there are two — the air handler "
                "inside and the condenser outside, plus a "
                "disconnect box by the outdoor unit. All on.",
                "One tripped leg explains a half-running "
                "system — reset once; a repeat trip is a pro "
                "call.",
                "full power both ends."),
        ],
        "verdict": (
            "Stat, filter, airflow, and power are cleared. "
            "What's left lives in the refrigerant circuit or "
            "the compressor — sealed-system work that legally "
            "takes a licensed tech with gauges. You ruled out "
            "everything free first, which is the right order."),
        "parts": ["AC filter (size on the frame)",
                  "run capacitor (pro swap)"],
    },
    {
        "id": "fridge_warm", "equip": "refrigerator",
        "problem": "not cooling",
        "safety": [],
        "steps": [
            _step(
                "Settings first: fridge 37°F, freezer 0°F. "
                "Controls get bumped by groceries more often "
                "than compressors die.",
                "Give it a full 24 hours at the right settings "
                "before judging — a loaded fridge moves slow.",
                "the settings were honest."),
            _step(
                "Coils: pull the fridge out (or pop the front "
                "grille) and vacuum the condenser coils. "
                "Blanketed coils can't dump heat, so the box "
                "runs warm and runs forever.",
                "Dusty coils were the whole story surprisingly "
                "often — vacuum them and check temps tomorrow.",
                "the coils can breathe."),
            _step(
                "Freezer fan: open the freezer — you should "
                "hear the evaporator fan and feel cold air "
                "moving. No fan + a warm fridge = the cold "
                "never leaves the freezer.",
                "A dead evaporator fan motor is a real DIY "
                "part swap on most models — power off, panel "
                "off, swap, done.",
                "air is moving between the compartments."),
            _step(
                "Door seals: close the door on a dollar bill "
                "and pull. It should drag hard. Test all four "
                "sides — a leaking corner pours cold out all "
                "day.",
                "A seal that won't drag gets replaced, or "
                "sometimes just cleaned and warmed back into "
                "shape with a hair dryer on low.",
                "the box seals tight."),
        ],
        "verdict": (
            "Settings, coils, fan, and seals are all cleared. "
            "What's left is the sealed refrigerant system or "
            "the compressor — appliance-tech territory. Food "
            "rule while you wait: 40°F is the line; when in "
            "doubt, throw it out."),
        "parts": ["evaporator fan motor", "door gasket"],
    },
    {
        "id": "dryer_slow", "equip": "dryer",
        "problem": "not drying / no heat",
        "safety": [
            "Safety: a dryer that takes two cycles is a lint "
            "fire asking politely first. The vent path is "
            "check #1 for drying AND for fire risk.",
        ],
        "steps": [
            _step(
                "The vent path, end to end: lint trap clean, "
                "then the hose behind the dryer — not crushed — "
                "then the outside flap, which should blow open "
                "hard while it runs. Weak air outside = plugged "
                "somewhere in that run.",
                "A plugged vent was the whole problem — and "
                "cleaning it fixes the drying, the electric "
                "bill, and the fire risk in one shot.",
                "the vent breathes free."),
            _step(
                "Breaker: an electric dryer runs on 240V — one "
                "leg tripped means it TUMBLES with NO heat, "
                "which fools everybody. Flip its breaker fully "
                "OFF, then ON, once.",
                "Half-power explained it — if that leg trips "
                "again, the element is shorting and that's the "
                "repair, not another reset.",
                "full 240V at the dryer."),
            _step(
                "Heat source: electric — a burned-open element "
                "is the usual corpse (visible coil break on "
                "many models, power OFF to look). Gas — through "
                "the peep, the igniter should glow then light "
                "the burner; glow with no flame = gas valve "
                "coils.",
                "The observation named the part — element on "
                "electric, valve coils on gas.",
                "the heat source fires."),
            _step(
                "Load + settings honesty check: one overloaded "
                "drum or a sensor-dry cycle fooled by a coated "
                "moisture sensor (wipe the metal strips inside "
                "the drum with a cloth) mimics a dead dryer.",
                "Sensor strips or the load size — cheapest "
                "fix on the tree.",
                "not the load, not the sensor."),
        ],
        "verdict": (
            "Vent, power, and heat source are cleared. Left: "
            "thermal fuse, cycling thermostat, or the motor — "
            "a meter and the back panel decide it, and that's "
            "a fair DIY with the power off or a one-hour "
            "appliance call."),
        "parts": ["heating element (electric)", "thermal fuse",
                  "gas valve coils (gas)"],
    },
    {
        "id": "washer_no_drain", "equip": "washing machine",
        "problem": "won't drain",
        "safety": [
            "Safety: unplug it before hands go near the pump "
            "— and have towels down; a full tub is gallons "
            "looking for the floor.",
        ],
        "steps": [
            _step(
                "Pump filter (front-loaders): the little door "
                "low on the front hides a coin trap/filter. "
                "Drain the small hose first, then twist it out "
                "— coins, hairpins, and button batteries live "
                "there.",
                "Found treasure = found the problem. Clean "
                "it, reseat it snug, run a drain cycle.",
                "the trap is clean."),
            _step(
                "Drain hose: not kinked, not pushed too far "
                "down the standpipe (4–8 inches in is right — "
                "too deep siphons or blocks), and the standpipe "
                "itself isn't the clog (pour a bucket in — does "
                "IT drain?).",
                "The hose or the house drain was it — a "
                "standpipe that won't take a bucket is a "
                "plumber's clog, not a washer fault.",
                "the path out is open."),
            _step(
                "Lid/door switch: the machine won't drain or "
                "spin if it thinks the lid is open. Listen for "
                "the click when you close it; a silent switch "
                "is a suspect.",
                "A dead lid switch is a cheap part and a "
                "classic no-drain fake-out.",
                "the machine knows it's closed."),
        ],
        "verdict": (
            "Trap, hose, house drain, and switch are cleared. "
            "What's left is the drain pump itself — hum with "
            "no pumping = jammed or dead pump; total silence "
            "= no power to it (control side). The pump is a "
            "two-hose, two-wire swap on most machines."),
        "parts": ["drain pump", "lid switch"],
    },
    {
        "id": "breaker_trip", "equip": "breaker",
        "problem": "keeps tripping",
        "safety": [
            "Safety: a breaker that trips instantly, sparks, "
            "smells burnt, or a panel that's hot to the touch "
            "= stop and call an electrician today. Panels are "
            "where DIY ends.",
        ],
        "steps": [
            _step(
                "Isolate: unplug EVERYTHING on that circuit, "
                "reset the breaker. Holds with nothing plugged "
                "in? The breaker and wiring just passed — a "
                "load is the problem. Trips empty? Stop: the "
                "fault is in the wiring or the breaker itself.",
                "An empty-circuit trip is the one result that "
                "ends the DIY walk — that's an electrician, "
                "full stop.",
                "it holds empty — a plugged-in load is "
                "tripping it."),
            _step(
                "Plug back in ONE at a time, running each. The "
                "one that trips it is your culprit — space "
                "heaters, hair dryers, and anything with a "
                "heating element top the list.",
                "Name the culprit: that appliance gets "
                "repaired or replaced, and it doesn't go back "
                "on this circuit meanwhile.",
                "no single load trips it — so it's the total."),
            _step(
                "Add it up: watts ÷ 120 = amps. A 15-amp "
                "circuit carries 1,800W max, 1,440W all-day. "
                "Two heaters, or a heater plus a microwave, "
                "trips a healthy breaker doing its exact job.",
                "Not a fault — arithmetic. Move a load to "
                "another circuit and the 'problem' retires.",
                "the total is inside the limit and it still "
                "trips."),
        ],
        "verdict": (
            "Loads and arithmetic are cleared and it still "
            "trips — that's a weak breaker or a wiring fault, "
            "and both live inside the panel."),
        "parts": [],
        "pro": True,
    },
    {
        "id": "toilet_running", "equip": "toilet",
        "problem": "keeps running",
        "safety": [],
        "steps": [
            _step(
                "Food-coloring test: a few drops in the TANK, "
                "don't flush, wait 15 minutes. Color in the "
                "BOWL = the flapper leaks tank water past, "
                "24/7. That's most running toilets.",
                "Flapper. A five-dollar part and ten minutes: "
                "water off, old one unclips, new one clips on "
                "the same pegs.",
                "no leak past the flapper."),
            _step(
                "Chain slack: the flapper chain needs a little "
                "slack — about half an inch. A chain caught "
                "under the flapper or pulled tight holds it "
                "open a crack.",
                "Chain adjusted — the free fix. Test with two "
                "flushes.",
                "the chain hangs right."),
            _step(
                "Water level: it should sit about an inch "
                "below the overflow tube. Higher, and water "
                "spills down the tube forever — adjust the "
                "fill valve float down.",
                "Level set — the fill valve stops where it "
                "should now.",
                "the level is right and it still runs."),
        ],
        "verdict": (
            "Flapper, chain, and level are cleared — the "
            "fill valve itself isn't shutting off. Fill valves "
            "are the other cheap part in the tank, and the "
            "swap is water-off, one nut, one supply line."),
        "parts": ["flapper", "fill valve"],
    },
    {
        "id": "low_pressure", "equip": "water pressure",
        "problem": "low pressure",
        "safety": [],
        "steps": [
            _step(
                "Scope it: is it ONE fixture or the WHOLE "
                "house? One fixture is a local story; the whole "
                "house is a main/PRV/city story. Check a "
                "bathroom tap and the hose spigot.",
                "One fixture only — that narrows everything "
                "that follows to that fixture's path.",
                "it's the whole house."),
            _step(
                "Aerators + showerhead (single fixture): unscrew "
                "the faucet aerator and rinse the grit out of "
                "the screen — sediment there strangles one tap "
                "while the house is fine.",
                "Grit in the screen was the whole 'plumbing "
                "problem'. Rinse, reinstall, full pressure.",
                "the screens are clean."),
            _step(
                "Valves: the main shutoff fully open, and if "
                "there's a pressure-reducing valve (bell-shaped, "
                "on the main line), has anyone touched it? "
                "Also ask a neighbor — city work on the street "
                "drops pressure for everyone.",
                "A half-closed main or the city working "
                "upstream explains a whole-house drop with "
                "zero broken parts.",
                "valves are open and the neighbors are fine."),
        ],
        "verdict": (
            "Scope, screens, and valves are cleared. Whole-"
            "house low pressure now points at a failing PRV "
            "or a supply-side restriction — a plumber with a "
            "gauge reads static vs. flowing pressure and "
            "names it in one visit."),
        "parts": ["faucet aerator", "pressure-reducing valve"],
    },
]

_TREE_BY_ID = {t["id"]: t for t in TREES}


# --- Preventive-maintenance schedules ---------------------------------------------
# (task, interval days, note). First due = created + interval;
# marking a task done re-arms it from today. Deterministic.

def _task(name, days, note=""):
    return {"name": name, "days": int(days), "note": note}


EQUIPMENT = [
    {"key": "furnace", "name": "Furnace",
     "rx": r"\bfurnace\b|\bheating system\b",
     "tasks": [
         _task("Change the furnace filter", 90,
               "size is printed on the filter frame; airflow "
               "arrow points toward the furnace"),
         _task("Annual furnace tune-up", 365,
               "before heating season — burner, igniter, "
               "and heat-exchanger check (a pro visit)"),
     ]},
    {"key": "ac", "name": "Air conditioner",
     "rx": r"\bair conditioner\b|\bcentral air\b|\bac unit\b|\bhvac\b",
     "tasks": [
         _task("Change the HVAC filter", 90,
               "size is printed on the filter frame"),
         _task("Clean the outdoor coil + clear brush back "
               "2 feet", 365, "garden hose, gentle — fins bend "
               "if you look at them wrong"),
         _task("Clear the condensate drain line", 182,
               "a clogged drain is the one that floods the "
               "furnace closet"),
     ]},
    {"key": "water_heater", "name": "Water heater",
     "rx": r"\bwater heater\b|\bhot water tank\b",
     "tasks": [
         _task("Flush the tank (sediment)", 365,
               "a few gallons off the bottom valve, or a "
               "full flush — kills the rumbling"),
         _task("Test the T&P relief valve", 365,
               "lift the lever, let it snap shut; replace "
               "the valve if it won't reseat — never plug it"),
         _task("Check the anode rod", 1095,
               "the rod sacrifices itself so the tank "
               "doesn't; when it's eaten to the wire, swap it"),
     ]},
    {"key": "fridge", "name": "Refrigerator",
     "rx": r"\bfridge\b|\brefrigerator\b",
     "tasks": [
         _task("Vacuum the condenser coils", 182,
               "dusty coils = warm box + long run times"),
         _task("Check the door seals", 365,
               "dollar-bill drag test on all four sides"),
     ]},
    {"key": "washer", "name": "Washing machine",
     "rx": r"\bwashing machine\b|\bwasher\b",
     "tasks": [
         _task("Run a cleaning cycle + wipe the door gasket",
               30, "front-loaders grow the funk in that "
               "gasket fold"),
         _task("Replace the supply hoses with braided steel",
               1825, "a burst washer hose is a flood claim — "
               "this is the cheapest insurance in the house"),
     ]},
    {"key": "dryer", "name": "Dryer",
     "rx": r"\bdryer\b|\bclothes dryer\b",
     "tasks": [
         _task("Clean the full vent run to outside", 365,
               "drying time AND fire risk ride on this one"),
         _task("Wash the lint trap screen", 182,
               "dryer-sheet film plugs the mesh invisibly — "
               "water should pour through it"),
     ]},
    {"key": "sump", "name": "Sump pump",
     "rx": r"\bsump pump\b",
     "tasks": [
         _task("Test the sump pump (bucket of water)", 90,
               "fill the pit until the float fires it — test "
               "it before the storm does"),
         _task("Check the backup battery", 365,
               "a dead backup is a decoration, not a backup"),
     ]},
    {"key": "alarms", "name": "Smoke + CO alarms",
     "rx": r"\bsmoke (?:detector|alarm)s?\b|\bco alarms?\b"
           r"|\bsmoke alarms?\b",
     "tasks": [
         _task("Test every smoke + CO alarm", 30,
               "the button on each one, all of them"),
         _task("Replace alarm batteries", 365,
               "or check the date on sealed 10-year units"),
         _task("Replace the alarms themselves", 3650,
               "sensors die of old age at 10 years even if "
               "the beep still works"),
     ]},
    {"key": "generator", "name": "Generator",
     "rx": r"\bgenerator\b",
     "tasks": [
         _task("Run the generator under load 15 minutes", 30,
               "a generator that only starts on the day you "
               "need it starts half the time"),
         _task("Oil + filter change", 365,
               "or per the hour meter, whichever comes first"),
     ]},
    {"key": "septic", "name": "Septic tank",
     "rx": r"\bseptic\b",
     "tasks": [
         _task("Pump the septic tank", 1095,
               "3–5 years is the window; solids that reach "
               "the drain field are the expensive ending"),
     ]},
]

_EQUIP_BY_KEY = {e["key"]: e for e in EQUIPMENT}


def _find_equipment(text: str) -> Optional[Dict]:
    raw = str(text or "")
    for e in EQUIPMENT:
        if re.search(e["rx"], raw, re.I):
            return e
    return None


def _find_tree(text: str) -> Optional[Dict]:
    """Symptom -> tree. Equipment noun + a symptom word, or
    a strong symptom phrase on its own."""
    raw = str(text or "").lower()
    if re.search(r"\bfurnace\b", raw) and re.search(
            r"no heat|not heating|won'?t heat|no warm|cold air|"
            r"won'?t (?:run|start|fire)|stopped heating", raw):
        return _TREE_BY_ID["furnace_no_heat"]
    if re.search(r"\bwater heater\b|\bhot water\b", raw) and re.search(
            r"no hot|not hot|luke ?warm|cold shower|no warm", raw):
        return _TREE_BY_ID["wh_no_hot"]
    if re.search(r"\b(?:ac|a/c|air condition\w*|cooling)\b", raw) \
            and re.search(r"no cool|not cool|won'?t cool|warm air|"
                          r"not cold|no cold", raw):
        return _TREE_BY_ID["ac_no_cool"]
    if re.search(r"\bfridge\b|\brefrigerator\b", raw) and re.search(
            r"warm|not (?:cold|cooling)|won'?t cool|spoiling", raw):
        return _TREE_BY_ID["fridge_warm"]
    if re.search(r"\bdryer\b", raw) and re.search(
            r"no heat|not dry|won'?t dry|takes (?:two|2|forever)|"
            r"still (?:wet|damp)|not heating", raw):
        return _TREE_BY_ID["dryer_slow"]
    if re.search(r"\bwasher\b|\bwashing machine\b", raw) and re.search(
            r"drain|won'?t drain|full of water|standing water", raw):
        return _TREE_BY_ID["washer_no_drain"]
    if re.search(r"\bbreaker\b", raw) and re.search(
            r"trip|keeps (?:tripping|shutting)|blows|pops", raw):
        return _TREE_BY_ID["breaker_trip"]
    if re.search(r"\btoilet\b", raw) and re.search(
            r"run|running|won'?t stop|keeps filling", raw):
        return _TREE_BY_ID["toilet_running"]
    if re.search(r"low (?:water )?pressure|pressure (?:is )?(?:low|bad|dropped)", raw):
        return _TREE_BY_ID["low_pressure"]
    # Equipment alone, problem unstated: the equipment's
    # signature problem starts the walk, named as a guess.
    equip_default = [
        (r"\bfurnace\b", "furnace_no_heat"),
        (r"\bwater heater\b", "wh_no_hot"),
        (r"\bdryer\b", "dryer_slow"),
        (r"\bfridge\b|\brefrigerator\b", "fridge_warm"),
        (r"\bwashing machine\b|\bwasher\b", "washer_no_drain"),
        (r"\btoilet\b", "toilet_running"),
        (r"\bbreaker\b", "breaker_trip"),
    ]
    for rx, tid in equip_default:
        if re.search(rx, raw):
            return _TREE_BY_ID[tid]
    return None


# --- Engineering math data -----------------------------------------------------------
# Section modulus S and moment of inertia I for dimensional
# lumber; planning values: Fb = 1,000 psi, E = 1.4M psi
# (SPF No.2 — labeled planning numbers, never a stamp).

LUMBER = {
    "2x4": (3.06, 5.36), "2x6": (7.56, 20.80),
    "2x8": (13.14, 47.63), "2x10": (21.39, 98.93),
    "2x12": (31.64, 178.09), "2x14": (43.89, 290.80),
}
FB_ALLOW = 1000.0
E_MOD = 1_400_000.0

_NUM = r"(\d+(?:\.\d+)?(?:\s*/\s*\d+)?)"


def _num(text: str) -> float:
    text = str(text).strip()
    if "/" in text:
        a, b = text.split("/", 1)
        return float(a) / float(b)
    return float(text)


def _fmt(x: float) -> str:
    if abs(x - round(x)) < 0.05:
        return str(int(round(x)))
    return f"{x:,.1f}".rstrip("0").rstrip(".")


def _due_text(ts: float, now: float) -> str:
    days = (ts - now) / DAY
    if days < -1:
        return f"OVERDUE by {int(round(-days))} days"
    if days < 0:
        return "due today"
    if days < 1.5:
        return "due tomorrow"
    return f"due in {int(round(days))} days"


# --- Renderers ----------------------------------------------------------------------

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
    head = (f"Diagnosis — {tree.get('equip', '')}: "
            f"{tree.get('problem', '')}. "
            f"Check {idx + 1} of {len(steps)} (one check at "
            f"a time — that's the discipline):\n"
            f"{step['check']}\n"
            "Do it, then tell me \"worked\" if it fixed it, "
            "or \"still\" if it didn't — I'll tell you what "
            "that rules out and hand you the next check.")
    safety = tree.get("safety", [])
    if idx == 0 and safety:
        head = "\n".join(safety) + "\n" + head
    return head


def _schedule_text(sched: Dict, now: Optional[float] = None) -> str:
    now = now if now is not None else time.time()
    lines = [f"Maintenance schedule — {sched.get('equipment', '')}:"]
    for t in sched.get("tasks", []):
        due = _due_text(float(t.get("next_due", now) or now), now)
        every = int(t.get("days", 0) or 0)
        span = (f"every {every} days" if every < 365
                else f"every {every // 365} year"
                + ("s" if every >= 730 else ""))
        line = f"• {t.get('name', '')} — {span}; next {due}."
        if t.get("note"):
            line += f" ({t['note']})"
        lines.append(line)
    lines.append(
        "Say \"mark the filter done\" (name the task) after "
        "you do one and its clock restarts. Say \"track this "
        "in projects\" and the Project Manager carries the "
        "schedule as a project.")
    return "\n".join(lines)


def status_for(uid: str) -> Dict:
    """The read seam (/mainteng/status): the caller's own
    walk + schedules, due states computed."""
    now = time.time()
    out = {"diagnosis": None, "schedules": []}
    diag = _diag(uid)
    if diag:
        tree = _TREE_BY_ID.get(diag.get("tree", "")) or {}
        out["diagnosis"] = {
            "tree": diag.get("tree", ""),
            "equipment": tree.get("equip", ""),
            "problem": tree.get("problem", ""),
            "step": int(diag.get("step", 0) or 0) + 1,
            "steps": len(tree.get("steps", [])),
            "started": float(diag.get("started", 0) or 0),
        }
    for s in _schedules(uid):
        if not isinstance(s, dict):
            continue
        tasks = []
        for t in s.get("tasks", []):
            tasks.append({
                "name": t.get("name", ""),
                "days": int(t.get("days", 0) or 0),
                "next_due": float(t.get("next_due", 0) or 0),
                "due": _due_text(
                    float(t.get("next_due", now) or now), now),
                "last_done": float(t.get("last_done", 0) or 0),
            })
        out["schedules"].append({
            "id": s.get("id", ""),
            "equipment": s.get("equipment", ""),
            "status": s.get("status", ""),
            "project_id": s.get("project_id", ""),
            "created": float(s.get("created", 0) or 0),
            "tasks": tasks,
        })
    return out


# --- Operations: the diagnosis walk ---------------------------------------------------

def _do_diagnose(uid: str, claim: Dict) -> List[Dict]:
    tree = _TREE_BY_ID.get(claim.get("tree", ""))
    if not tree:
        return _do_emergency_free_show(uid)
    old = _diag(uid)
    note = ""
    if old and old.get("tree") != tree["id"]:
        note = ("(Closing the earlier walk — one at a time.) "
                )
    diag = {"tree": tree["id"], "step": 0, "status": "active",
            "started": time.time()}
    _save_diag(uid, diag)
    body = note + _step_text(diag)
    if claim.get("guessed"):
        body = (f"Taking the {tree['equip']}'s signature "
                f"problem — {tree['problem']} — say the word "
                "if it's doing something else.\n" + body)
    return _result(_TAG, "Maintenance + Engineer — diagnosis",
                   body)


def _do_advance(uid: str, claim: Dict) -> List[Dict]:
    diag = _diag(uid)
    if not diag:
        return _result(
            _TAG, "Maintenance + Engineer — no walk open",
            "No diagnosis is open right now — tell me what's "
            "misbehaving (\"my furnace has no heat\") and "
            "I'll walk it one check at a time.")
    tree = _TREE_BY_ID.get(diag.get("tree", "")) or {}
    steps = tree.get("steps", [])
    idx = int(diag.get("step", 0) or 0)
    step = steps[idx] if idx < len(steps) else {}
    if claim.get("worked"):
        _save_diag(uid, None)
        body = (f"Fixed at check {idx + 1} of {len(steps)}. "
                f"{step.get('fix', '')} "
                "Walk closed — if it comes back, we start "
                "where the pattern points, not from zero.")
        if tree.get("pro"):
            body += "\n" + PRO_LINE
        return _result(
            _TAG, "Maintenance + Engineer — fixed", body)
    # "still": print what that rules out, advance or close.
    ruled = step.get("means", "")
    nxt = idx + 1
    if nxt >= len(steps):
        _save_diag(uid, None)
        body = (f"That rules out: {ruled}\n"
                + tree.get("verdict", ""))
        if tree.get("pro"):
            body += "\n" + PRO_LINE
        return _result(
            _TAG, "Maintenance + Engineer — walked out", body)
    diag["step"] = nxt
    _save_diag(uid, diag)
    body = (f"That rules out: {ruled}\n" + _step_text(diag))
    return _result(_TAG, "Maintenance + Engineer — diagnosis",
                   body)


def _do_diag_show(uid: str) -> List[Dict]:
    diag = _diag(uid)
    if not diag:
        return _result(
            _TAG, "Maintenance + Engineer — no walk open",
            "No diagnosis is open. Tell me the symptom "
            "(\"dryer takes two cycles\") and I'll start "
            "one, one check at a time.")
    return _result(_TAG, "Maintenance + Engineer — diagnosis",
                   _step_text(diag))


def _do_close(uid: str) -> List[Dict]:
    had_diag = _diag(uid) is not None
    if had_diag:
        _save_diag(uid, None)
    if had_diag:
        return _result(
            _TAG, "Maintenance + Engineer — closed",
            "Walk closed — nothing was ordered or changed. "
            "Name the symptom any time and we start again "
            "from check 1.")
    return _result(
        _TAG, "Maintenance + Engineer — nothing open",
        "No walk is open here. Name a symptom and I'll "
        "start one.")


def _do_emergency(uid: str, claim: Dict) -> List[Dict]:
    kind = claim.get("kind", "")
    if kind == "gas":
        body = ("STOP — this one is not a walk-through. If "
                "you smell gas: get everyone OUT of the house "
                "now, don't touch light switches or anything "
                "electrical (a spark is the danger), don't "
                "re-enter, and call 911 or your gas company's "
                "emergency line from OUTSIDE or a neighbor's. "
                "No check I have outranks that. When you're "
                "clear and the utility has been called, tell "
                "me and we'll talk about what happens next.")
    elif kind == "co":
        body = ("STOP — a sounding CO alarm is an emergency, "
                "not a battery reminder until proven otherwise: "
                "get everyone (pets too) OUT into fresh air "
                "now and call 911 from outside. Do NOT open "
                "windows and stay inside 'to check'. Headache, "
                "dizziness, or nausea with the alarm = treat "
                "it as real. The fire department clears the "
                "house with a meter — that reading, not time "
                "passed, says when it's safe.")
    else:
        body = ("STOP — sparking, arcing, smoke from an "
                "outlet/panel, or a burnt-electrical smell: "
                "kill that circuit at the breaker if you can "
                "do it without touching anything hot or "
                "damaged, keep hands and water away from it, "
                "and if there's flame or the panel itself is "
                "involved, get out and call 911. No appliance "
                "is worth a panel fire. Tell me once it's "
                "safe and we'll walk what happened.")
    return _result(_TAG, "Maintenance + Engineer — EMERGENCY",
                   body)


def _do_emergency_free_show(uid: str) -> List[Dict]:
    return _do_diag_show(uid)


# --- Operations: schedules ------------------------------------------------------------

def _do_sched_create(uid: str, claim: Dict) -> List[Dict]:
    equip = _EQUIP_BY_KEY.get(claim.get("equip", ""))
    if not equip:
        return _result(
            _TAG, "Maintenance + Engineer — which equipment?",
            "Name the equipment and I'll build its schedule "
            "— furnace, air conditioner, water heater, "
            "fridge, washer, dryer, sump pump, smoke + CO "
            "alarms, generator, septic.")
    for s in _schedules(uid):
        if isinstance(s, dict) and s.get("equip_key") == \
                equip["key"] and s.get("status") == "active":
            return _result(
                _TAG, "Maintenance + Engineer — schedule",
                "Already on the books:\n"
                + _schedule_text(s)
                + "\n(Say \"mark <task> done\" as you do "
                  "them — one schedule per piece of equipment "
                  "keeps the dates honest.)")
    now = time.time()
    sched = {
        "id": uuid.uuid4().hex[:12], "equip_key": equip["key"],
        "equipment": equip["name"], "status": "active",
        "created": now, "project_id": "",
        "tasks": [{"name": t["name"], "days": t["days"],
                   "note": t["note"], "last_done": 0.0,
                   "next_due": now + t["days"] * DAY}
                  for t in equip["tasks"]],
    }
    rows = _schedules(uid)
    rows.append(sched)
    _save_schedules(uid, rows)
    try:
        import og_notify
        og_notify.record(
            uid, "maintenance",
            f"Maintenance schedule set — {equip['name']}",
            "Intervals are on the books; ask \"what "
            "maintenance is due\" any time.")
    except Exception as e:
        logger.warning(f"MaintEng notify failed: {e}")
    return _result(
        _TAG, "Maintenance + Engineer — schedule set",
        _schedule_text(sched, now)
        + "\nThe dates are computed from the intervals "
          "above — nothing runs in the background; ask any "
          "time and I'll read the clocks.")


def _do_sched_show(uid: str, claim: Dict) -> List[Dict]:
    rows = [s for s in _schedules(uid) if isinstance(s, dict)]
    if not rows:
        return _result(
            _TAG, "Maintenance + Engineer — no schedules",
            "No maintenance schedules yet — say \"maintenance "
            "schedule for my furnace\" (or water heater, "
            "dryer, sump pump…) and I'll put its intervals "
            "on the books.")
    now = time.time()
    if claim.get("due_only"):
        due_lines = []
        for s in rows:
            for t in s.get("tasks", []):
                if float(t.get("next_due", 0) or 0) <= \
                        now + 7 * DAY:
                    due_lines.append(
                        f"• {s.get('equipment', '')}: "
                        f"{t.get('name', '')} — "
                        + _due_text(float(
                            t.get("next_due", now) or now), now)
                        + ".")
        if not due_lines:
            return _result(
                _TAG, "Maintenance + Engineer — nothing due",
                "Nothing is due or within a week of due "
                "across your schedules. The full list is "
                "there on \"show my maintenance schedules\".")
        return _result(
            _TAG, "Maintenance + Engineer — due",
            "Due now / this week:\n" + "\n".join(due_lines))
    body = "\n\n".join(_schedule_text(s, now) for s in rows)
    return _result(_TAG, "Maintenance + Engineer — schedules",
                   body)


_STOPWORDS = {
    "the", "a", "an", "my", "i", "did", "done", "changed",
    "change", "replaced", "replace", "cleaned", "clean",
    "flushed", "tested", "checked", "mark", "got", "just",
    "it", "that", "this", "finished", "did the", "on",
}


def _tokens(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", str(text or "").lower())
    return {w for w in words if w not in _STOPWORDS
            and len(w) > 1}


def _match_task(uid: str, text: str):
    """(schedule, task) whose name shares real tokens with
    the user's done-message; None unless exactly one fits."""
    want = _tokens(text)
    hits = []
    for s in _schedules(uid):
        if not isinstance(s, dict):
            continue
        equip_toks = _tokens(s.get("equipment", ""))
        for t in s.get("tasks", []):
            name_toks = _tokens(t.get("name", ""))
            shared = want & name_toks
            # equipment word in the message boosts the match
            score = len(shared) + (1 if want & equip_toks
                                   and shared else 0)
            if shared and score >= 1 and (
                    len(shared) >= 2 or shared & {
                        "filter", "coil", "vent", "anode",
                        "hoses", "battery", "batteries",
                        "septic", "gasket", "trap", "oil"}):
                hits.append((score, s, t))
    if not hits:
        return None, None
    hits.sort(key=lambda h: -h[0])
    if len(hits) > 1 and hits[0][0] == hits[1][0] \
            and hits[0][2] is not hits[1][2]:
        return None, None
    return hits[0][1], hits[0][2]


def _do_task_done(uid: str, claim: Dict) -> List[Dict]:
    sched, task = _match_task(uid, claim.get("raw", ""))
    if not sched or not task:
        return _result(
            _TAG, "Maintenance + Engineer — which task?",
            "I couldn't pin that to one scheduled task. "
            "Name it like the schedule does — \"mark the "
            "furnace filter done\", \"flushed the water "
            "heater\" — and I'll restart its clock.")
    now = time.time()
    rows = _schedules(uid)
    for s in rows:
        if s.get("id") != sched.get("id"):
            continue
        for t in s.get("tasks", []):
            if t.get("name") == task.get("name"):
                t["last_done"] = now
                t["next_due"] = now + int(
                    t.get("days", 0) or 0) * DAY
    _save_schedules(uid, rows)
    fresh = _get_schedule(uid, sched.get("id", "")) or sched
    return _result(
        _TAG, "Maintenance + Engineer — marked done",
        f"Done: {task.get('name', '')} "
        f"({sched.get('equipment', '')}) — next "
        + _due_text(float(task.get("days", 0) or 0) * DAY
                    + now, now) + ".\n"
        + _schedule_text(fresh, now))


# --- Operations: ordering + hand-off ----------------------------------------------------

def _do_order(uid: str) -> List[Dict]:
    diag = _diag(uid)
    scheds = [s for s in _schedules(uid) if isinstance(s, dict)]
    if diag:
        tree = _TREE_BY_ID.get(diag.get("tree", "")) or {}
        parts = tree.get("parts", [])
        head = (f"Parts in play on the open diagnosis "
                f"({tree.get('equip', '')} — "
                f"{tree.get('problem', '')}):")
        sheet = "\n".join(f"• {p}" for p in parts) \
            or "• (this walk's parts are named as the checks land)"
    elif scheds:
        nxt = []
        for s in scheds:
            for t in s.get("tasks", []):
                nxt.append((float(t.get("next_due", 0) or 0),
                            s.get("equipment", ""),
                            t.get("name", "")))
        nxt.sort()
        head = "Coming up on your schedules (stock these):"
        sheet = "\n".join(f"• {e}: {n}" for _, e, n in nxt[:6])
    else:
        return _result(
            _TAG, "Maintenance + Engineer — nothing to list",
            "No open diagnosis and no schedules here, so "
            "there's no parts list to order from. Start a "
            "walk (\"my dryer won't dry\") or a schedule "
            "(\"maintenance schedule for my furnace\") and "
            "the parts name themselves.")
    body = (head + "\n" + sheet + "\n"
            "Buying runs through OG's ordering pack like "
            "always — it prepares the order, you approve "
            "the preview, then it hands off. I don't order "
            "anything from here; this sheet is the list to "
            "take in.")
    return _result(_TAG, "Maintenance + Engineer — parts sheet",
                   body)


def _save_schedule_doc(uid: str, sched: Dict):
    """The schedule document into the caller's locker (the
    Builder/WoodPro shape, the same seams). Returns
    (record, reason): exactly one is set."""
    import og_storage
    if not og_storage.storage_enabled():
        return None, "dark"
    name = f"{sched.get('equipment', '')} — maintenance schedule.txt"
    data = _schedule_text(sched).encode("utf-8")[:DOC_CAP]
    try:
        rec = og_storage.save_bytes(
            uid, og_builder._tier_for_save(uid),
            name, data, "text/plain")
        return rec, ""
    except Exception as e:
        msg = str(e).strip()
        if msg in ("dark", "free"):
            return None, msg
        if "only got" in msg or "left in your locker" in msg:
            return None, "quota"
        logger.warning(f"MaintEng schedule save failed: {e}")
        return None, "other"


def _do_handoff(uid: str) -> List[Dict]:
    rows = [s for s in _schedules(uid) if isinstance(s, dict)
            and s.get("status") == "active"]
    if not rows:
        done = [s for s in _schedules(uid)
                if isinstance(s, dict)
                and s.get("status") == "handed_off"]
        if done:
            return _result(
                _TAG, "Maintenance + Engineer — handed off",
                f"The {done[0].get('equipment', '')} schedule "
                "is already carried by the Project Manager — "
                "nothing new to hand off.")
        return _result(
            _TAG, "Maintenance + Engineer — no schedule",
            "No schedule to hand off yet — say \"maintenance "
            "schedule for my …\" first and I'll put its "
            "intervals on the books.")
    sched = rows[-1]
    tasks_brief = "; ".join(
        f"{t.get('name', '')} every {int(t.get('days', 0) or 0)} days"
        for t in sched.get("tasks", []))
    goal = (f"{sched.get('equipment', '')} maintenance "
            f"schedule — {tasks_brief}")
    try:
        import og_pm
        before = {p.get("id") for p in og_pm._projects(uid)
                  if isinstance(p, dict)}
        og_pm._create_project(uid, goal)
        after = [p for p in og_pm._projects(uid)
                 if isinstance(p, dict)
                 and p.get("id") not in before]
        proj = after[0] if after else None
    except Exception as e:
        logger.warning(f"MaintEng PM handoff failed: {e}")
        proj = None
    if not proj:
        return _result(
            _TAG, "Maintenance + Engineer — handoff glitch",
            "The Project Manager didn't take the schedule — "
            "nothing was created and the schedule is still "
            "on the books here. Say \"track this in "
            "projects\" again and I'll retry.")
    pid = proj.get("id", "")
    ptitle = proj.get("title", "")
    rec, why = _save_schedule_doc(uid, sched)
    filed = False
    if rec:
        try:
            import og_filing
            link = og_filing.file_link(uid, pid, "locker",
                                       rec.get("kind", ""),
                                       rec.get("id", ""))
            filed = link is not None
        except Exception as e:
            logger.warning(f"MaintEng filing failed: {e}")
            filed = False
        if filed:
            doc_line = (f"The schedule document is saved in "
                        f"your locker as "
                        f"\"{rec.get('name', '')}\" and filed "
                        "under the project.")
        else:
            doc_line = (f"The schedule document is saved in "
                        f"your locker as "
                        f"\"{rec.get('name', '')}\", but the "
                        "filing link didn't land — say \"file "
                        "this under the project\" and the Filer "
                        "will link it.")
    else:
        reasons = {
            "dark": "the locker is switched off right now",
            "free": "your plan doesn't include locker "
                    "storage (it starts at Standard)",
            "quota": "your locker is full",
            "other": "the locker save glitched",
        }
        doc_line = (f"The schedule document could NOT be "
                    f"saved to your locker — "
                    f"{reasons.get(why, why)} — so there's "
                    "nothing for the Filer to link yet. The "
                    "schedule still lives right here, and the "
                    "project below carries the work.")
    rows_all = _schedules(uid)
    for s in rows_all:
        if s.get("id") == sched.get("id"):
            s["status"] = "handed_off"
            s["project_id"] = pid
    _save_schedules(uid, rows_all)
    body = (f"Handed off — the Project Manager now carries "
            f"\"{ptitle}\" as a project (Progress shows it), "
            f"with the schedule's tasks in its plan. "
            + doc_line
            + " Due dates still read from the schedule "
              "here any time you ask.")
    return _result(_TAG, "Maintenance + Engineer — handed off",
                   body)


# --- Operations: engineering math (work shown, deterministic) -------------------------

def _do_concrete(uid: str, claim: Dict) -> List[Dict]:
    raw = claim.get("raw", "")
    dims = og_builder._parse_dims(raw)
    thick_m = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:in|inch|inches|\"|″)?\s*thick",
        raw, re.I)
    thick_in = None
    if thick_m:
        thick_in = float(thick_m.group(1))
    elif dims and dims[2]:
        # A third dimension on a slab ask is the thickness;
        # _parse_dims speaks feet — a value >= 1 ft thick is
        # a wall, not a slab, so read small thirds as inches
        # only when the text says inches.
        if re.search(r"\d\s*(?:in|inch|inches|\")\s*$", raw, re.I):
            thick_in = dims[2] * 12
    if not dims or not thick_in:
        return _result(
            _TAG, "Maintenance + Engineer — concrete",
            "Give me the pour as length × width × thickness — "
            "\"concrete for a 10x12 slab, 4 inches thick\" — "
            "and I'll show the yardage, the waste, and the "
            "bag count with the math.")
    L, W = dims[0], dims[1]
    area = L * W
    cf = area * (thick_in / 12.0)
    cy = cf / 27.0
    cy_order = math.ceil((cy * 1.10) * 2) / 2.0
    bags80 = math.ceil(cf / 0.6)
    bags60 = math.ceil(cf / 0.45)
    body = (
        f"Concrete — {_fmt(L)} x {_fmt(W)} ft slab, "
        f"{_fmt(thick_in)} in thick.\n"
        f"Work: area = {_fmt(L)} x {_fmt(W)} = {_fmt(area)} "
        f"sq ft. Volume = {_fmt(area)} x ({_fmt(thick_in)}/12) "
        f"= {_fmt(cf)} cu ft. Yards = {_fmt(cf)} / 27 = "
        f"{cy:.2f} cu yd.\n"
        f"Order: {_fmt(cy_order)} cu yd (the pour plus 10% "
        "waste, rounded up to the half yard — short loads "
        "cost more than a little extra mud).\n"
        f"Bagging it instead: about {bags80} x 80 lb bags "
        f"(0.6 cu ft each) or {bags60} x 60 lb bags (0.45 cu "
        "ft each), before waste — past a yard, a truck is "
        "kinder to your back than a mixer and a pallet.")
    return _result(_TAG, "Maintenance + Engineer — concrete",
                   body)


def _do_tank(uid: str, claim: Dict) -> List[Dict]:
    raw = claim.get("raw", "")
    d_m = re.search(
        _NUM + r"\s*(in|inch|inches|ft|feet|\")\s*"
        r"(?:diameter|dia|across|wide)", raw, re.I)
    h_m = re.search(
        _NUM + r"\s*(in|inch|inches|ft|feet|\")\s*"
        r"(?:tall|high|deep|long)", raw, re.I)
    if not d_m or not h_m:
        return _result(
            _TAG, "Maintenance + Engineer — tank volume",
            "Give me the tank as diameter × height — \"how "
            "many gallons in a tank 24 inches across, 48 "
            "inches tall\" — and I'll run it with the math "
            "shown.")
    d = _num(d_m.group(1)) * (12 if d_m.group(2).lower()
                              .startswith("f") else 1)
    h = _num(h_m.group(1)) * (12 if h_m.group(2).lower()
                              .startswith("f") else 1)
    r = d / 2.0
    ci = math.pi * r * r * h
    gal = ci / 231.0
    body = (
        f"Tank volume — {_fmt(d)} in diameter, {_fmt(h)} in "
        f"tall (round tank, standing upright).\n"
        f"Work: radius = {_fmt(d)} / 2 = {_fmt(r)} in. "
        f"Volume = pi x {_fmt(r)}^2 x {_fmt(h)} = "
        f"{ci:,.0f} cu in. Gallons = {ci:,.0f} / 231 = "
        f"{gal:,.1f} gal.\n"
        f"So: about {_fmt(gal)} gallons brim-full — working "
        "capacity is less once you leave headspace, and a "
        "horizontal tank reads different at part-full (the "
        "math above is the full cylinder).")
    return _result(_TAG, "Maintenance + Engineer — tank", body)


def _do_slope(uid: str, claim: Dict) -> List[Dict]:
    raw = claim.get("raw", "")
    m = re.search(
        _NUM + r"\s*(in|inch|inches|ft|feet|\")\s*"
        r"(?:of\s+)?(?:rise|drop|fall)?\s*(?:over|in|per|across)\s*"
        + _NUM + r"\s*(in|inch|inches|ft|feet)", raw, re.I)
    if not m:
        return _result(
            _TAG, "Maintenance + Engineer — slope",
            "Give me rise over run — \"3 inches of fall over "
            "10 feet\" — and I'll give you percent, inches "
            "per foot, and the ratio. (For reference: patios "
            "and slabs drain at 1/4 in per foot, about 2%; "
            "yard swales get away with 1% if the run is "
            "clean.)")
    rise = _num(m.group(1)) * (12 if m.group(2).lower()
                               .startswith("f") else 1)
    run = _num(m.group(3)) * (12 if m.group(4).lower()
                              .startswith("f") else 1)
    if run <= 0:
        return _do_slope(uid, {"raw": ""})
    pct = rise / run * 100.0
    in_per_ft = rise / (run / 12.0)
    ratio = run / rise if rise else 0
    body = (
        f"Slope — {_fmt(rise)} in of rise over "
        f"{_fmt(run / 12.0)} ft.\n"
        f"Work: percent = {_fmt(rise)} / {_fmt(run)} x 100 = "
        f"{pct:.2f}%. Inches per foot = {_fmt(rise)} / "
        f"{_fmt(run / 12.0)} = {in_per_ft:.2f} in/ft. "
        f"Ratio = 1 : {ratio:.0f} (1 unit up per "
        f"{ratio:.0f} along).\n"
        "Reading it: drainage wants roughly 2% (1/4 in per "
        "foot) away from a foundation; 1% is the floor for "
        "a swale; ramps for wheels live under 8.33% (1:12).")
    return _result(_TAG, "Maintenance + Engineer — slope", body)


def _do_beam(uid: str, claim: Dict) -> List[Dict]:
    raw = claim.get("raw", "")
    size_m = re.search(r"\b(2\s?x\s?(?:4|6|8|10|12|14))\b",
                       raw, re.I)
    span_m = re.search(
        r"(?:span|across|span of)\s*(?:of\s*)?" + _NUM
        + r"\s*(ft|feet|foot|in|inch|inches)?", raw, re.I) \
        or re.search(_NUM + r"\s*(?:ft|feet|foot)\s*span",
                     raw, re.I)
    if not size_m or not span_m:
        return _result(
            _TAG, "Maintenance + Engineer — span check",
            "Give me member + span — \"will a 2x8 span 12 "
            "feet\" (load and spacing optional; I default to "
            "50 psf total and 16 in on-center and say so) — "
            "and I'll run bending + deflection with the "
            "work shown.\n" + PRO_LINE)
    size = re.sub(r"\s+", "", size_m.group(1).lower())
    if size not in LUMBER:
        return _do_beam(uid, {"raw": ""})
    span_ft = _num(span_m.group(1))
    if (span_m.group(2) or "ft").lower().startswith("in"):
        span_ft = span_ft / 12.0
    psf_m = re.search(_NUM + r"\s*(?:psf|lb\s*/\s*(?:sq\s*)?ft)",
                      raw, re.I)
    psf = _num(psf_m.group(1)) if psf_m else 50.0
    oc_m = re.search(_NUM + r"\s*(?:in|inch|inches)\s*"
                     r"(?:on center|o\.?c\.?|apart|spacing)",
                     raw, re.I)
    oc = _num(oc_m.group(1)) if oc_m else 16.0
    S, I = LUMBER[size]
    w_plf = psf * (oc / 12.0)          # lb per linear foot
    L_in = span_ft * 12.0
    M = w_plf * span_ft ** 2 / 8.0 * 12.0   # lb-in
    stress = M / S
    d_in = 5 * (w_plf / 12.0) * L_in ** 4 / (384 * E_MOD * I)
    d_allow = L_in / 360.0
    ok_s = stress <= FB_ALLOW
    ok_d = d_in <= d_allow
    verdict = (
        f"Bending: {'inside' if ok_s else 'OVER'} the "
        f"{_fmt(FB_ALLOW)} psi planning limit. Deflection: "
        f"{'inside' if ok_d else 'OVER'} the L/360 floor "
        f"limit. "
        + ("On these planning numbers the member carries "
           "it — the sign-off line below still applies."
           if ok_s and ok_d else
           "On these planning numbers it does NOT carry it "
           "— go up a size, shorten the span, or tighten "
           "the spacing, and I'll re-run it."))
    body = (
        f"Span check — {size} spanning {_fmt(span_ft)} ft, "
        f"{_fmt(psf)} psf total load, {_fmt(oc)} in "
        f"on-center.\n"
        f"Work: each member carries {_fmt(psf)} x "
        f"({_fmt(oc)}/12) = {w_plf:.1f} lb per foot. "
        f"Moment = wL^2/8 = {w_plf:.1f} x {_fmt(span_ft)}^2 "
        f"/ 8 = {M:,.0f} lb-in. Bending stress = M/S = "
        f"{M:,.0f} / {S} = {stress:,.0f} psi (planning "
        f"allowable {FB_ALLOW:,.0f} psi, SPF No.2). "
        f"Deflection = 5wL^4/(384EI) = {d_in:.2f} in "
        f"(L/360 allows {d_allow:.2f} in).\n"
        + verdict + "\n" + PRO_LINE)
    return _result(_TAG, "Maintenance + Engineer — span check",
                   body)


def _do_load(uid: str, claim: Dict) -> List[Dict]:
    raw = claim.get("raw", "")
    w_m = re.search(_NUM + r"\s*(?:lbs?|pounds)\b", raw, re.I)
    area = None
    dims = og_builder._parse_dims(raw)
    if dims:
        area = dims[0] * dims[1]
    else:
        a_m = re.search(_NUM + r"\s*(?:sq\s*ft|sf|square feet)",
                        raw, re.I)
        if a_m:
            area = _num(a_m.group(1))
    if not w_m or not area:
        return _result(
            _TAG, "Maintenance + Engineer — load",
            "Give me weight + footprint — \"2,000 lbs on a "
            "10x12 deck\" — and I'll give you the pounds per "
            "square foot against the usual design numbers, "
            "work shown.")
    lbs = _num(w_m.group(1))
    psf = lbs / area
    body = (
        f"Load — {_fmt(lbs)} lbs over {_fmt(area)} sq ft.\n"
        f"Work: {_fmt(lbs)} / {_fmt(area)} = {psf:.1f} psf.\n"
        "Reading it: residential floors are designed around "
        "40 psf live load, decks 40 psf (50 near some "
        "codes), bedrooms 30. A hot tub is the classic "
        "trap: a filled 8-person tub runs 100+ psf — way "
        "past a standard deck's design, which is why tubs "
        "get their own footing conversation.\n" + PRO_LINE)
    return _result(_TAG, "Maintenance + Engineer — load", body)


_CONV = [
    (r"inch|inches|in\b|\"", r"foot|feet|ft\b", 1 / 12.0,
     "in", "ft"),
    (r"foot|feet|ft\b", r"inch|inches|in\b|\"", 12.0,
     "ft", "in"),
    (r"gallon|gallons|gal", r"liter|liters|litre|litres|l\b",
     3.78541, "gal", "L"),
    (r"liter|liters|litre|litres|l\b",
     r"gallon|gallons|gal", 1 / 3.78541, "L", "gal"),
    (r"cubic (?:feet|foot)|cu ft|cf",
     r"cubic yards?|cu yd|cy", 1 / 27.0, "cu ft", "cu yd"),
    (r"cubic yards?|cu yd|cy",
     r"cubic (?:feet|foot)|cu ft|cf", 27.0, "cu yd", "cu ft"),
]


def _do_convert(uid: str, claim: Dict) -> List[Dict]:
    raw = claim.get("raw", "")
    temp_m = re.search(
        _NUM + r"\s*(?:degrees?\s*)?([fc])\b.*?"
        r"(?:to|in)\s*(?:degrees?\s*)?([fc])\b", raw, re.I)
    if temp_m:
        v = _num(temp_m.group(1))
        a, b = temp_m.group(2).lower(), temp_m.group(3).lower()
        if a != b:
            out = (v - 32) * 5 / 9 if a == "f" else v * 9 / 5 + 32
            return _result(
                _TAG, "Maintenance + Engineer — convert",
                f"{_fmt(v)} °{a.upper()} = {out:.1f} "
                f"°{b.upper()}. "
                + ("Work: (F − 32) × 5/9." if a == "f"
                   else "Work: C × 9/5 + 32."))
    for from_rx, to_rx, factor, fu, tu in _CONV:
        m = re.search(
            _NUM + r"\s*(?:" + from_rx + r")\s*"
            r"(?:to|in|into|=)\s*(?:" + to_rx + r")",
            raw, re.I)
        if m:
            v = _num(m.group(1))
            out = v * factor
            return _result(
                _TAG, "Maintenance + Engineer — convert",
                f"{_fmt(v)} {fu} = {_fmt(out)} {tu}. "
                f"(x{factor:g} — that's the whole trick.)")
    return _result(
        _TAG, "Maintenance + Engineer — convert",
        "I convert inches↔feet, gallons↔liters, cubic "
        "feet↔cubic yards, and °F↔°C — say it like "
        "\"convert 36 inches to feet\" and I'll show it.")


def mainteng_results(claim: Dict, uid: str) -> List[Dict]:
    op = claim.get("op")
    if op == "me_emergency":
        return _do_emergency(uid, claim)
    if op == "me_diagnose":
        return _do_diagnose(uid, claim)
    if op == "me_advance":
        return _do_advance(uid, claim)
    if op == "me_diag_show":
        return _do_diag_show(uid)
    if op == "me_close":
        return _do_close(uid)
    if op == "me_sched_create":
        return _do_sched_create(uid, claim)
    if op == "me_sched_show":
        return _do_sched_show(uid, claim)
    if op == "me_task_done":
        return _do_task_done(uid, claim)
    if op == "me_done_nosched":
        return _result(
            _TAG, "Maintenance + Engineer — no schedule yet",
            "Noted — but there's no schedule on the books "
            "here to mark it against. Say \"maintenance "
            "schedule for my furnace\" (or the water "
            "heater, dryer, alarms…) and I'll put its "
            "intervals down; from then on your dones "
            "restart the right clocks.")
    if op == "me_order":
        return _do_order(uid)
    if op == "me_handoff":
        return _do_handoff(uid)
    if op == "me_concrete":
        return _do_concrete(uid, claim)
    if op == "me_tank":
        return _do_tank(uid, claim)
    if op == "me_slope":
        return _do_slope(uid, claim)
    if op == "me_beam":
        return _do_beam(uid, claim)
    if op == "me_load":
        return _do_load(uid, claim)
    if op == "me_convert":
        return _do_convert(uid, claim)
    return _do_diag_show(uid)


# --- Chat claims (narrow frames — the crew/PM/filer doctrine) -------------------------

_GARBLE_VOCAB = ("furnace", "filter", "heat", "water",
                 "heater", "breaker", "thermostat", "pump",
                 "maintenance", "schedule", "concrete",
                 "dryer", "fridge", "tank", "slope")

_EMERG_GAS_RE = re.compile(
    r"\bsmell(?:s|ing|ed)?\s+(?:like\s+)?gas\b"
    r"|\bgas\s+(?:smell|leak|odor)\b"
    r"|\brotten\s+eggs?\s+smell\b", re.I)
_EMERG_CO_RE = re.compile(
    r"\bco\s+(?:alarm|detector)\b[^.?!]*\b(?:going off|"
    r"sounding|beeping|alarming)\b"
    r"|\bcarbon\s+monoxide\b[^.?!]*\b(?:alarm|going off|"
    r"detected)\b", re.I)
_EMERG_SPARK_RE = re.compile(
    r"\b(?:outlet|panel|breaker|switch|receptacle)\b[^.?!]*"
    r"\b(?:spark\w*|arcing|buzzing and hot|smoke|burning "
    r"smell|scorch)\b"
    r"|\bsparks?\s+(?:coming|flying)\b", re.I)

_SCHED_CREATE_RE = re.compile(
    r"\b(?:maintenance|preventive|pm)\s+(?:schedule|plan|"
    r"checklist)\b|\bschedule\s+(?:of\s+)?maintenance\b"
    r"|\bset\s+up\s+maintenance\b|\bupkeep\s+schedule\b", re.I)
_SCHED_SHOW_RE = re.compile(
    r"^(?:show|see|list|what'?s|what is|pull up)\b[^.?!]*"
    r"\b(?:maintenance|schedules?|upkeep)\b"
    r"|^my\s+(?:maintenance\s+)?schedules?$", re.I)
_DUE_RE = re.compile(
    r"\bwhat(?:'s| is| maintenance is)\s+due\b"
    r"|\banything\s+due\b|\bmaintenance\s+due\b", re.I)
_DONE_RE = re.compile(
    r"\b(?:mark|marked)\b[^.?!]*\bdone\b"
    r"|^(?:i\s+)?(?:changed|replaced|cleaned|flushed|"
    r"tested|did|finished|swapped|vacuumed)\b", re.I)
_CLOSE_RE = re.compile(
    r"^(?:scrap|cancel|forget|drop|kill|close|stop)\s+"
    r"(?:the\s+|my\s+|that\s+|this\s+)?(?:diagnosis|walk|"
    r"troubleshoot\w*)\b|^never\s*mind$", re.I)
_SHOW_RE = re.compile(
    r"^(?:show|where were we|what'?s|what is|repeat)\b"
    r"[^.?!]*\b(?:check|step|diagnosis|walk)\b"
    r"|^(?:the\s+)?(?:current\s+)?check$", re.I)
_HANDOFF_RE = re.compile(
    r"\btrack\s+(?:this|it|the\s+schedule)\s+in\s+projects?\b"
    r"|\bmake\s+(?:this|it)\s+a\s+project\b"
    r"|\bhand\s+(?:this|it|the\s+schedule)\s+to\s+the\s+"
    r"project\s+manager\b|\bsave\s+the\s+schedule\b", re.I)
_ORDER_RE = re.compile(
    r"\b(?:order|buy|purchase|get)\b[^.?!]*\b(?:filter|"
    r"parts?|element|flapper|igniter|thermostat|"
    r"supplies|everything|it all|the list|stuff)\b", re.I)
_WORKED_RE = re.compile(
    r"\b(?:that|it)\s+(?:worked|fixed\s+it)\b|\bfixed\s+it\b"
    r"|\bworks\s+now\b|\bworking\s+now\b|\bit'?s\s+working\b"
    r"|\bback\s+on\b|^(?:yes|yeah|yep)$", re.I)
_STILL_RE = re.compile(
    r"\bstill\b|\bno\s+luck\b|\bdidn'?t\s+work\b"
    r"|\bnot\s+working\b|\bno\s+change\b|\bnothing\b"
    r"|\bnext\b|^(?:no|nope|done|checked|did\s+it)$", re.I)

_MATH_RES = (
    ("me_concrete", re.compile(
        r"\bconcrete\b|\bslab\b[^.?!]*\bthick\b", re.I)),
    ("me_tank", re.compile(
        r"\bhow\s+many\s+gallons\b|\bgallons\s+in\s+a\s+"
        r"(?:tank|drum|barrel)\b|\btank\b[^.?!]*\b(?:gallons|"
        r"volume|hold)\b", re.I)),
    ("me_beam", re.compile(
        r"\bwill\s+a\s+2\s?x\s?\d+\b|\bspan\b[^.?!]*\b2\s?x"
        r"\s?\d+\b|\b2\s?x\s?\d+\b[^.?!]*\bspan\b"
        r"|\bjoist\b[^.?!]*\bspan\b|\bspan\s+check\b", re.I)),
    ("me_slope", re.compile(
        r"\bslope\b|\bgrade\b[^.?!]*\b(?:percent|fall|rise)\b"
        r"|\b\d+(?:\.\d+)?\s*(?:in|inches)\s+(?:of\s+)?"
        r"(?:fall|rise|drop)\b", re.I)),
    ("me_load", re.compile(
        r"\bload\s+check\b|\bhow\s+much\s+(?:weight|load)\b"
        r"|\b\d[\d,]*\s*(?:lbs?|pounds)\s+(?:on|over|across)"
        r"\b|\bpsf\b", re.I)),
    ("me_convert", re.compile(
        r"\bconvert\b|\bhow\s+many\s+(?:inches|feet|"
        r"gallons|liters|yards)\b", re.I)),
)


def _defer_to_others(message: str, uid: str) -> bool:
    """The PM, the Filer, the crew, the Builder and the
    Wood Pro own their asks — checked exactly, first. If
    any of them claims this message, this seat never
    does (maintenance vocabulary is this seat's alone)."""
    try:
        import og_pm
        if og_pm._claim_exact(message):
            return True
    except Exception:
        pass
    try:
        import og_filing
        if og_filing._claim_exact(message, uid):
            return True
    except Exception:
        pass
    try:
        import og_crew
        if og_crew._claim_exact(message):
            return True
    except Exception:
        pass
    try:
        import og_builder
        job, _info = og_builder._claim_builder(message, uid)
        if job:
            return True
    except Exception:
        pass
    try:
        import og_woodpro
        if og_woodpro._claim_exact(message, uid):
            return True
    except Exception:
        pass
    return False


def _claim_exact(message: str, uid: str) -> Optional[Dict]:
    raw = str(message or "").strip()
    if not raw or not uid:
        return None
    # Emergencies first — before every defer and every
    # frame: the answer is the emergency line, full stop.
    if _EMERG_GAS_RE.search(raw):
        return {"op": "me_emergency", "kind": "gas"}
    if _EMERG_CO_RE.search(raw):
        return {"op": "me_emergency", "kind": "co"}
    if _EMERG_SPARK_RE.search(raw):
        return {"op": "me_emergency", "kind": "spark"}
    if _defer_to_others(raw, uid):
        return None
    diag = _diag(uid)
    scheds = _schedules(uid)
    # An open walk owns its advance words.
    if diag:
        if _CLOSE_RE.search(raw):
            return {"op": "me_close"}
        if _WORKED_RE.search(raw):
            return {"op": "me_advance", "worked": True}
        if _STILL_RE.search(raw):
            return {"op": "me_advance", "worked": False}
        if _SHOW_RE.search(raw):
            return {"op": "me_diag_show"}
    # Schedules: create / due / show / done / hand-off.
    if _SCHED_CREATE_RE.search(raw):
        equip = _find_equipment(raw)
        if equip:
            return {"op": "me_sched_create",
                    "equip": equip["key"]}
        return {"op": "me_sched_create", "equip": ""}
    if scheds and _DUE_RE.search(raw):
        return {"op": "me_sched_show", "due_only": True}
    if scheds and _HANDOFF_RE.search(raw):
        return {"op": "me_handoff"}
    if scheds and _DONE_RE.search(raw) and _match_task(
            uid, raw)[1] is not None:
        return {"op": "me_task_done", "raw": raw}
    if _SCHED_SHOW_RE.search(raw):
        return {"op": "me_sched_show"}
    # Ordering from a schedule's context beats a fresh
    # walk: "order a furnace filter" with a schedule on
    # the books is a parts ask, not a new symptom.
    if scheds and _ORDER_RE.search(raw):
        return {"op": "me_order"}
    # A done-report with no schedule to mark against
    # earns the ask-back, never a fresh walk.
    if not scheds and _DONE_RE.search(raw) and re.search(
            r"\bfilter|coil|vent|anode|hose|alarm|septic|"
            r"gasket|battery|batteries\b", raw, re.I):
        return {"op": "me_done_nosched", "raw": raw}
    # Diagnosis starts.
    tree = _find_tree(raw)
    if tree:
        guessed = not re.search(
            r"\bno\b|\bnot\b|\bwon'?t\b|\blow\b|\btrip", raw, re.I)
        return {"op": "me_diagnose", "tree": tree["id"],
                "guessed": guessed}
    # A done-claim that matches nothing still earns the
    # ask-back, but only when schedules exist to match.
    if scheds and _DONE_RE.search(raw):
        return {"op": "me_task_done", "raw": raw}
    # Ordering from context.
    if (diag or scheds) and _ORDER_RE.search(raw):
        return {"op": "me_order"}
    # Engineering math.
    for op, rx in _MATH_RES:
        if rx.search(raw):
            return {"op": op, "raw": raw}
    if _ORDER_RE.search(raw) and re.search(
            r"\bfurnace filter\b|\bair filter\b", raw, re.I):
        return {"op": "me_order"}
    return None


def _claim_mainteng(message: str, uid: str):
    """(claim, garble_info): the exact claim first; on a
    miss, the Round 75 second chance — og_garble repairs
    the TRIGGER words only; the payload passes verbatim."""
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


def install_mainteng_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search OUTSIDE the Wood
    Pro seam (installed last, from the og_fixqueue chain)
    so maintenance/engineering asks claim here and
    everything else delegates back untouched. Persona
    files never touched."""
    if getattr(agent_instance, "_og_mainteng_installed",
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
            if uid and mainteng_enabled():
                claim, info = _claim_mainteng(
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
            logger.warning(f"MaintEng trigger check failed: {e}")
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
                results = mainteng_results(claim, uid)
            except Exception as e:
                logger.warning(f"MaintEng op failed: {e}")
                results = _result(
                    _TAG, "Maintenance + Engineer — glitch",
                    "That ask glitched — nothing was "
                    "scheduled, ordered, or changed. Say it "
                    "again and I'll retry.")
        if results is None:
            results = prev_search(query, num_results)
        if garble and results:
            results = og_garble.annotate(results, garble,
                                         prepend=True)
        return results

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_mainteng_installed = True


# --- Routes ----------------------------------------------------------------------------

def register_mainteng_routes(app):
    """GET /mainteng/status — the caller's own walk +
    schedules (due states computed). Identity is the
    caller's cookie uid, like every Round 95+ surface."""

    @app.get("/mainteng/status")
    async def mainteng_status_route(request: Request):
        uid = request.cookies.get("ogai_uid") or ""
        view = status_for(uid) if uid else {
            "diagnosis": None, "schedules": []}
        return {"ok": True, "enabled": mainteng_enabled(),
                **view}
