"""Round 111: THE PROJECT BUILDER HELPER (Brent's order,
2026-10-12 11:11 PDT: "Project builder helper" — specced
right after the Project Manager + Project Filing pair:
"The PM runs the work; the Filer keeps the paperwork;
the Builder draws the plan.")

WHAT THIS IS. A crew specialist (the R81 pattern) for
PHYSICAL builds. The user brings a build idea in chat —
"build a 12x16 deck", "help me build a fence" — and the
Builder produces a real plan:

  - the DESIGN in words + dimensions (what it is, how
    big, what it's made of);
  - a MATERIALS list with quantities (deterministic
    take-off math per build kind, waste factored);
  - a TOOLS list;
  - a STEP-BY-STEP build sequence;
  - a rough COST RANGE, always labeled an ESTIMATE
    (planning rates, never a quote — prices move);
  - the SAFETY callouts a pro would give (this round's
    own plain lines — 811 before digging, permits,
    PPE — not a safety system, just the callouts in
    the plan where they belong).

The plan is a living draft: the user adjusts it in chat
("make it 10x14", "use cedar instead", "add a gate")
and every list re-computes from the same stored
parameters — the numbers the user sees always come from
the one stored plan, never from the model improvising.

HAND-OFF. When the user says the plan is ready ("save
the plan", "make it a project"), the Builder:
  1. creates a Project Manager project for the build
     (og_pm's own create path — one project truth);
  2. saves the plan document into the user's locker
     (og_storage.save_bytes, the tier the storage seam
     itself resolves) and files it under that project
     through og_filing.file_link — a link, never a move.
If the locker refuses (dark, free tier, quota), the
answer says so plainly: the project still exists, the
plan still lives in the Builder's own store, and
nothing pretends to be filed.

THE FENCES.
- Knowledge + planning ONLY. The Builder holds no
  ordering machinery and imports none (suite-pinned at
  source level): an ordering ask that references the
  plan ("order the materials") is answered with the
  plan's own materials sheet and the plain routing —
  buying runs through OG's ordering pack, which
  prepares the order and stops for the user's approval
  before anything is paid (prepare -> approve ->
  handoff). The Builder never self-buys.
- Per-account isolation: plans live in this module's
  own per-account store; every read seam is keyed by
  the caller's own uid (drill-proven cross-account).
- NO new loop (R72 doctrine): the Builder works on
  exchanges only — no timer, no thread of its own.
- Persona untouched — OG's voice, carpenter's pencil.
  app.py is never touched (install rides the
  og_fixqueue chain after the Filer; routes ride
  og_library).

STATE. Own durable store, the Round 18 pattern
(mirroring og_pm/og_filing): Postgres table
og_builder_data when OG_MEMORY_DB_URL is set, else a
JSON file. Blob kind per visitor: 'plans'. Switch:
OG_BUILDER_ENABLED (unset = ON), in the crew switch
family.
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

from fastapi import Request

logger = logging.getLogger(__name__)

# --- Config -------------------------------------------------------------------

PLAN_KEEP = 20           # plans kept per visitor (drafts + handed off)
PLAN_DOC_CAP = 12000     # chars of the saved plan document

BUILDER_STORE_FILE = "builder_store.json"
# RLock, not Lock: store updates hold the lock across a
# read-modify-write whose save path re-enters it through
# _put_blob's file branch (the og_pm/og_crew shape).
_builder_lock = threading.RLock()

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, StoreError, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None


# --- Switch ---------------------------------------------------------------------

def builder_enabled() -> bool:
    """OG_BUILDER_ENABLED=false is the Builder's own kill
    switch (any other value, or unset, leaves it on)."""
    return os.environ.get(
        "OG_BUILDER_ENABLED", "true").strip().lower() not in (
            "0", "false", "no", "off")


# --- Durable store (Round 18 pattern, mirroring og_pm) --------------------------

def _db_connect():
    conn = psycopg.connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_builder_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(BUILDER_STORE_FILE):
        try:
            with open(BUILDER_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Builder store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(BUILDER_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Builder store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_builder_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"Builder DB load failed: {e}")
            raise StoreError("builder store unavailable (Round 99)") from e
    with _builder_lock:
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
                        "INSERT INTO og_builder_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Builder DB save failed: {e}")
            raise StoreError("builder store unavailable (Round 99)") from e
    with _builder_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


# --- Plans (stored parameters; every rendering recomputes from them) ----------

def _plans(uid: str) -> List[Dict]:
    got = _blob(uid, "plans", [])
    return got if isinstance(got, list) else []


def _save_plans(uid: str, plans: List[Dict]):
    plans.sort(key=lambda p: float(p.get("created", 0) or 0))
    _put_blob(uid, "plans", plans[-PLAN_KEEP:])


def _get_plan(uid: str, plan_id: str) -> Optional[Dict]:
    for p in _plans(uid):
        if isinstance(p, dict) and p.get("id") == plan_id:
            return p
    return None


def _active_plan(uid: str) -> Optional[Dict]:
    """The newest DRAFT plan — the one adjustments and the
    hand-off act on. Handed-off plans are closed."""
    drafts = [p for p in _plans(uid)
              if isinstance(p, dict) and p.get("status") == "draft"]
    if not drafts:
        return None
    drafts.sort(key=lambda p: float(p.get("updated", 0) or 0),
                reverse=True)
    return drafts[0]


def _update_plan(uid: str, plan_id: str, **fields) -> Optional[Dict]:
    with _builder_lock:
        plans = _plans(uid)
        hit = None
        for p in plans:
            if isinstance(p, dict) and p.get("id") == plan_id:
                p.update(fields)
                p["updated"] = time.time()
                hit = p
                break
        if hit is not None:
            _save_plans(uid, plans)
        return hit


def plans_for(uid: str) -> List[Dict]:
    """The read seam (/builder/status): the caller's own
    plan views, newest first."""
    out = []
    for p in _plans(uid):
        if not isinstance(p, dict):
            continue
        r = _render(p)
        out.append({
            "id": p.get("id", ""), "title": p.get("title", ""),
            "kind": p.get("kind", ""), "status": p.get("status", ""),
            "dims": dict(p.get("dims") or {}),
            "material": p.get("material", ""),
            "cost_low": r["cost_low"], "cost_high": r["cost_high"],
            "project_id": p.get("project_id", ""),
            "created": float(p.get("created", 0) or 0),
            "updated": float(p.get("updated", 0) or 0),
        })
    out.sort(key=lambda x: -x["created"])
    return out


# --- Dimensions + materials parsing ----------------------------------------------

_NUM = r"(\d+(?:\.\d+)?)"
_DIMS_RE = re.compile(
    _NUM + r"\s*(ft|feet|in|inch|inches|'|\")?\s*"
    r"(?:x|by|\u00d7)\s*"
    + _NUM + r"\s*(ft|feet|in|inch|inches|'|\")?"
    r"(?:\s*(?:x|by|\u00d7)\s*"
    + _NUM + r"\s*(ft|feet|in|inch|inches|'|\")?)?",
    re.I)


def _to_ft(value: float, unit: Optional[str]) -> float:
    if unit and unit.strip().lower() in ("in", "inch", "inches", '"'):
        return value / 12.0
    return value


def _parse_dims(text: str):
    """(l, w, h) in feet from '12x16', '12 by 16 ft',
    '8x4x1', '48 in x 24 in' — None when no pair is named."""
    m = _DIMS_RE.search(str(text or ""))
    if not m:
        return None
    l = _to_ft(float(m.group(1)), m.group(2))
    w = _to_ft(float(m.group(3)), m.group(4))
    h = _to_ft(float(m.group(5)), m.group(6)) if m.group(5) else None
    if l <= 0 or w <= 0:
        return None
    return (round(l, 2), round(w, 2),
            round(h, 2) if h else None)


def _fmt_num(x: float) -> str:
    if abs(x - round(x)) < 0.01:
        return str(int(round(x)))
    return f"{x:g}"


def _dims_text(dims: Dict) -> str:
    l = _fmt_num(float(dims.get("l", 0) or 0))
    w = _fmt_num(float(dims.get("w", 0) or 0))
    h = dims.get("h")
    out = f"{l} x {w} ft"
    if h:
        out += f" x {_fmt_num(float(h))} ft"
    return out


_MATERIALS = (
    ("pressure treated", "Pressure-treated lumber", 1.0),
    ("pressure-treated", "Pressure-treated lumber", 1.0),
    ("treated pine", "Pressure-treated lumber", 1.0),
    ("cedar", "Cedar", 1.35),
    ("redwood", "Redwood", 1.5),
    ("composite", "Composite", 1.9),
    ("pine", "Pine", 0.9),
    ("vinyl", "Vinyl", 1.5),
    ("chain-link", "Chain-link", 0.6),
    ("chain link", "Chain-link", 0.6),
    ("aluminum", "Aluminum", 1.4),
)


def _find_material(text: str):
    low = " " + str(text or "").lower() + " "
    for key, label, factor in _MATERIALS:
        if key in low:
            return label, factor
    return None, None


# --- Build kinds ------------------------------------------------------------------

_KIND_WORDS = (
    ("raised bed", ("raised bed", "garden bed", "planter box",
                    "planter", "garden box")),
    ("chicken coop", ("chicken coop", "coop", "hen house")),
    ("dog house", ("dog house", "doghouse", "dog kennel")),
    ("workbench", ("workbench", "work bench", "work table",
                   "workbench table")),
    ("bookshelf", ("bookshelf", "bookcase", "book shelf",
                   "shelves", "shelving", "shelf")),
    ("pergola", ("pergola", "gazebo", "arbor", "trellis")),
    ("fence", ("fence", "fencing", "gate")),
    ("shed", ("shed", "storage building", "barn")),
    ("deck", ("deck", "porch", "patio")),
    ("stairs", ("stairs", "staircase", "stair")),
    ("table", ("table", "desk", "bench")),
)

_KIND_LABELS = {"raised bed": "raised garden bed",
                "generic": "build"}

_DEFAULT_DIMS = {
    "deck": (12.0, 16.0, None), "fence": (40.0, 6.0, None),
    "shed": (8.0, 10.0, None), "raised bed": (8.0, 4.0, 1.0),
    "workbench": (6.0, 2.5, 3.0), "pergola": (10.0, 12.0, None),
    "bookshelf": (3.0, 6.0, 1.0), "dog house": (4.0, 3.0, 3.0),
    "chicken coop": (8.0, 4.0, 6.0), "stairs": (3.0, 3.5, None),
    "table": (6.0, 3.0, 2.5), "generic": (8.0, 8.0, None),
}


def _find_kind(text: str) -> Optional[str]:
    low = " " + re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower()) + " "
    for kind, words in _KIND_WORDS:
        for w in words:
            if f" {w} " in low or f" {w}s " in low:
                return kind
    return None


_BASE_SAFETY = [
    "Eye and ear protection on every cut; gloves when you "
    "handle treated lumber, and a dust mask when you cut it.",
    "Measure twice, cut once — and check for square as you "
    "go, not after it's all together.",
]
_DIG_SAFETY = (
    "Call 811 BEFORE you dig — the utility locate is free "
    "and it needs a few days' lead. Never guess where lines "
    "run.")


def _ceil(x: float) -> int:
    return int(math.ceil(x - 1e-9))


def _money(x: float) -> int:
    return int(round(x / 10.0) * 10)


# Each template returns (design, materials, tools, steps,
# safety, (cost_low, cost_high), basis). Materials rows are
# (name, qty_text, note). All quantities are deterministic
# take-offs from the stored dims — waste factored, labeled
# as planning math in the plan text.

def _tpl_deck(d, mat_label, factor):
    L, W = d["l"], d["w"]
    area = L * W
    cover = 0.47                       # ft of width per 5/4x6 board + gap
    lin = _ceil(area / cover * 1.10)   # linear ft of decking, 10% waste
    boards = _ceil(lin / 12.0)
    joists = _ceil(W * 12 / 16) + 1
    posts = (_ceil(L / 6) + 1) * 2
    bags = posts * 2
    screws = max(2, _ceil(area / 50 * 2.5))
    mats = [
        ("5/4 x 6 deck boards (12 ft)", f"{boards} boards (~{lin} linear ft)",
         f"{mat_label}; covers {_fmt_num(area)} sq ft at a 10% waste factor"),
        (f"2 x 8 joists, {_fmt_num(L)} ft", f"{joists}",
         "16 in on center across the width"),
        ("2 x 8 rim / beam boards", "4", "rim joists + doubled beam"),
        (f"4 x 4 posts, 8 ft", f"{posts}", "one per footing, ~6 ft spacing"),
        ("80 lb concrete bags", f"{bags}", "~2 per footing"),
        ("3 in deck screws", f"~{screws} lb", ""),
        ("Joist hangers + structural screws", f"{joists} hangers",
         "for the ledger/rim connections"),
        ("Ledger board + flashing", "1 set",
         "only if the deck ties into the house"),
        ("Railing materials", "as needed",
         "required if the deck surface sits 30 in or more above grade"),
    ]
    tools = ["Tape measure + string line", "Post-hole digger or auger",
             "Circular saw", "Drill/driver", "4 ft level", "Speed square",
             "Chalk line", "Wheelbarrow (for concrete)"]
    steps = [
        "Lay out the footprint with string lines; check both diagonals "
        "match (that's your square check).",
        "Call 811, wait for the locate, then dig footing holes below "
        "your local frost line.",
        "Pour footings, set post bases, and let the concrete cure.",
        "Set posts, then the beams; attach the ledger to the house "
        "with flashing and structural fasteners if it ties in.",
        "Hang the joists 16 in on center, rim them, and block the "
        "field.",
        "Run the deck boards, gapping a screw-width apart; keep the "
        "pattern straight with a chalk line every few rows.",
        "Build the railing + any stairs, then seal or stain once the "
        "lumber has dried a season (treated) or right away (cedar/"
        "composite per the maker).",
    ]
    safety = [_DIG_SAFETY,
              "A deck tied to a house lives or dies on the ledger: "
              "flashing + structural screws/bolts, never nails alone.",
              "Most places want a permit for a deck — check before "
              "you pour, not after."] + _BASE_SAFETY
    design = (f"A {_dims_text(d)} ground-level deck in {mat_label.lower()} "
              f"— boards running the {_fmt_num(L)} ft way, joists 16 in "
              "on center, posts on poured footings about 6 ft apart.")
    return design, mats, tools, steps, safety, \
        (area * 13 * factor, area * 27 * factor), \
        "deck materials at planning rates of $13-$27 per sq ft"


def _tpl_fence(d, mat_label, factor):
    F, H = d["l"], d["w"]
    sections = _ceil(F / 8)
    posts = sections + 1
    rails_per = 3 if H > 5 else 2
    rails = sections * rails_per
    pickets = _ceil(F * 12 / 5.75)
    bags = posts * 2
    mats = [
        (f"4 x 4 posts, {_fmt_num(H + 2)} ft", f"{posts}",
         "8 ft spacing, buried ~2 ft"),
        ("80 lb concrete bags", f"{bags}", "~2 per post"),
        (f"2 x 4 rails, 8 ft", f"{rails}",
         f"{rails_per} per 8 ft section"),
        ('1 x 6 pickets (5.5 in wide)', f"{pickets}",
         f"covers {_fmt_num(F)} linear ft, tight-butted"),
        ("2.5 in exterior screws or ring-shank nails", "~5 lb", ""),
        ("Post caps", f"{posts}", "keeps the post tops from rotting"),
        ("Gate hardware kit", "1 (if a gate goes in)",
         "hinges + latch, sized to the gate width"),
    ]
    tools = ["Post-hole digger or auger", "String line + stakes",
             "Circular saw", "Drill/driver", "Level", "Tape measure"]
    steps = [
        "Confirm the property line first — a fence on the wrong side "
        "of it moves at your cost. Stake and string the run.",
        "Call 811, wait for the locate, then dig post holes ~2 ft "
        "deep.",
        "Set the two end/corner posts plumb in concrete; string "
        "between them and set the line posts to the string.",
        "Let the concrete set (a day is honest), then hang the rails "
        "level between posts.",
        "Hang pickets to the string, checking plumb every few boards; "
        "leave a small ground gap so bottoms don't sit in water.",
        "Build + hang the gate last, with a diagonal brace so it "
        "doesn't sag.",
    ]
    safety = [_DIG_SAFETY,
              "Check local rules on fence height and which side "
              "faces out before you buy pickets."] + _BASE_SAFETY
    design = (f"A {_fmt_num(F)} ft long, {_fmt_num(H)} ft tall privacy "
              f"fence in {mat_label.lower()} — posts every 8 ft set in "
              "concrete, pickets tight-butted.")
    return design, mats, tools, steps, safety, \
        (F * 14 * factor, F * 26 * factor), \
        "fence materials at planning rates of $14-$26 per linear ft"


def _tpl_shed(d, mat_label, factor):
    L, W = d["l"], d["w"]
    area = L * W
    perim = 2 * (L + W)
    floor_sheets = _ceil(area / 32)
    joists = _ceil(W * 12 / 16) + 1
    studs = _ceil(perim * 12 / 16) + 6
    plates = _ceil(perim * 2 / 8)
    siding = _ceil(perim * 7 / 32)
    roof_area = area * 1.18
    bundles = _ceil(roof_area / 100 * 3)
    mats = [
        ("3/4 in plywood, 4 x 8 sheets", f"{floor_sheets + _ceil(roof_area / 32)}",
         "floor + roof deck"),
        (f"2 x 6 floor joists, {_fmt_num(L)} ft", f"{joists}",
         "16 in on center, on skids or blocks"),
        ("4 x 4 skids / concrete blocks", "2 skids or ~8 blocks",
         "the foundation — keep the floor off the ground"),
        (f"2 x 4 studs, 8 ft", f"{studs}", "walls at 16 in on center "
         f"for {_fmt_num(perim)} ft of wall"),
        (f"2 x 4 plates, 8 ft", f"{plates}", "top + bottom plates"),
        ("T1-11 siding sheets, 4 x 8", f"{siding}", "7 ft walls"),
        ("Asphalt shingles", f"{bundles} bundles",
         f"~{_fmt_num(roof_area)} sq ft of roof at slope"),
        ("Roofing felt / underlayment", "1 roll", ""),
        ("Framing nails + screws assortment", "1 lot", ""),
        ("Door hinges + latch + handle", "1 set", ""),
    ]
    tools = ["Circular saw", "Framing nailer or drill/driver",
             "Level", "Speed square", "Ladder", "Tape measure",
             "Chalk line"]
    steps = [
        "Level the site; set skids or blocks on gravel so the floor "
        "stays dry.",
        "Frame the floor (2 x 6 at 16 in on center), sheet it with "
        "the 3/4 plywood, and check the diagonals.",
        "Frame the walls flat on the floor, sheet the outside, then "
        "stand, plumb, and brace them.",
        "Set the roof: rafters or trusses, sheet it, felt it, shingle "
        "it bottom-up.",
        "Hang the door, trim the corners, and paint or stain the "
        "siding the same season.",
        "Add vents high and low — a sealed shed cooks its contents.",
    ]
    safety = ["Ladder discipline on the roof deck: three points of "
              "contact, and never sheet a roof in wind.",
              "Check your local permit line — many places exempt "
              "small sheds, many don't; a tape measure on the rule "
              "book beats a tear-down."] + _BASE_SAFETY
    design = (f"A {_dims_text(d)} shed with 7 ft walls — plywood "
              "floor on skids, stud walls, shingled gable roof. "
              f"Skin: {mat_label.lower()} frame with T1-11 siding.")
    return design, mats, tools, steps, safety, \
        (area * 22 * factor, area * 40 * factor), \
        "shed materials at planning rates of $22-$40 per sq ft of floor"


def _tpl_raised_bed(d, mat_label, factor):
    L, W, H = d["l"], d["w"], d["h"] or 1.0
    rows = max(1, _ceil(H * 12 / 5.5))       # stacked 2x6s
    sides = rows * 2
    soil_cf = L * W * H * 0.9
    bags = _ceil(soil_cf / 1.5)
    mats = [
        (f"2 x 6 boards, {_fmt_num(L)} ft", f"{sides}",
         f"long sides, {rows} row(s) high"),
        (f"2 x 6 boards, {_fmt_num(W)} ft", f"{sides}", "short sides"),
        ("4 x 4 corner posts", "4, cut to height", "inside corners"),
        ("3 in exterior screws", "~1 lb", ""),
        ("Raised-bed soil mix, 1.5 cu ft bags", f"{bags}",
         f"~{_fmt_num(soil_cf)} cu ft to fill"),
        ("Landscape fabric roll", "1", "lines the bottom — drainage "
         "without the dirt escaping"),
    ]
    tools = ["Circular or hand saw", "Drill/driver", "Tape measure",
             "Speed square", "Staple gun"]
    steps = [
        "Cut the boards and corner posts to size.",
        "Build the two long walls flat, then join the short sides "
        "into the corners — a box, checked square by diagonals.",
        "Set it where it will live and level it (dig the high side "
        "down, don't shim the low side up).",
        "Line the bottom with fabric, staple it up the insides.",
        "Fill with the soil mix, water it in, top it off after it "
        "settles.",
    ]
    safety = ["Use untreated or food-safe-rated lumber for beds "
              "that grow food — ask at the yard if the tag doesn't "
              "say."] + _BASE_SAFETY
    design = (f"A {_dims_text(d)} raised garden bed in "
              f"{mat_label.lower()} — stacked 2 x 6 walls on inside "
              "corner posts, fabric-lined.")
    return design, mats, tools, steps, safety, \
        ((45 + soil_cf * 2.2) * factor, (85 + soil_cf * 3.4) * factor), \
        "bed materials + soil at planning rates"


def _tpl_workbench(d, mat_label, factor):
    L, W, H = d["l"], d["w"], d["h"] or 3.0
    sheets = max(1, _ceil(L * W / 32))
    mats = [
        ("3/4 in plywood, 4 x 8 sheets", f"{sheets}",
         "the top (double it for a heavy-duty top)"),
        (f"4 x 4 legs", f"4, cut to {_fmt_num(H)} ft", ""),
        (f"2 x 4 frame boards, 8 ft", "6", "aprons + stretchers + "
         "lower shelf frame"),
        ("3 in screws + wood glue", "1 lot", ""),
        ("Hardboard / sacrificial top sheet", "1",
         "takes the beating so the top doesn't"),
        ("Sandpaper + finish (oil or poly)", "1 lot", ""),
    ]
    tools = ["Circular saw", "Drill/driver", "Clamps", "Speed square",
             "Sander", "Tape measure"]
    steps = [
        "Cut the legs and frame boards; build the two leg frames.",
        "Join the frames with the long aprons — check square before "
        "the glue sets.",
        "Add the lower shelf frame and stretchers; rack-test it "
        "(push it sideways — it shouldn't move).",
        "Attach the plywood top flush, then the sacrificial sheet.",
        "Sand, ease the edges, and finish. Add a vise if you run "
        "one.",
    ]
    safety = list(_BASE_SAFETY)
    design = (f"A {_dims_text(d)} workbench — plywood top on a "
              f"4 x 4 leg frame in {mat_label.lower()}, with a lower "
              "shelf and a sacrificial top sheet.")
    return design, mats, tools, steps, safety, \
        (130 * factor, 280 * factor), "workbench materials, flat planning range"


def _tpl_pergola(d, mat_label, factor):
    L, W = d["l"], d["w"]
    posts = 6 if max(L, W) > 12 else 4
    rafters = _ceil(L * 12 / 24) + 1
    mats = [
        ("6 x 6 posts, 10 ft", f"{posts}", "set in concrete or on "
         "post bases"),
        (f"2 x 8 beams, {_fmt_num(W)} ft", f"{2 if posts == 4 else 3}",
         "doubled across the tops"),
        (f"2 x 6 rafters, {_fmt_num(L)} ft", f"{rafters}",
         "24 in on center, overhang past the beams"),
        ("80 lb concrete bags", f"{posts * 2}", "~2 per post"),
        ("Lag bolts + structural screws", "1 lot", ""),
        ("2 x 2 shade slats", "as wanted",
         "optional top layer — sets how much shade you get"),
    ]
    tools = ["Post-hole digger or auger", "Ladder", "Circular saw",
             "Drill/driver", "Level", "String line"]
    steps = [
        "Lay out the post grid and check diagonals.",
        "Call 811, dig, and set posts plumb in concrete (or on bases "
        "bolted to a slab).",
        "Notch or bolt the beams across the post tops — level both "
        "ways.",
        "Set the rafters 24 in on center with the overhang even.",
        "Add shade slats to taste; seal or stain.",
    ]
    safety = [_DIG_SAFETY,
              "Beams go up with two people — a 2 x 8 overhead is "
              "not a one-person lift."] + _BASE_SAFETY
    design = (f"A {_dims_text(d)} pergola in {mat_label.lower()} — "
              f"{posts} posts, doubled beams, rafters at 24 in on "
              "center with an even overhang.")
    return design, mats, tools, steps, safety, \
        (L * W * 16 * factor, L * W * 30 * factor), \
        "pergola materials at planning rates of $16-$30 per sq ft of footprint"


def _tpl_bookshelf(d, mat_label, factor):
    Wd, H, D = d["l"], d["w"], d["h"] or 1.0
    shelves = max(2, int(H / 1.25))
    mats = [
        ("3/4 in plywood or 1 x boards", f"{max(1, _ceil(Wd * H / 32))} "
         "sheet(s) / board lot",
         f"sides + top + {shelves} shelves at {_fmt_num(D)} ft deep"),
        ("1 x 2 face trim", "1 lot", "covers the plywood edges"),
        ("Wood screws + glue", "1 lot", ""),
        ("Shelf pins or fixed-shelf cleats", f"{shelves} sets", ""),
        ("Wall anchor / anti-tip strap", "1",
         "non-negotiable with kids in the house"),
        ("Paint or finish", "1 lot", ""),
    ]
    tools = ["Circular saw", "Drill/driver", "Clamps", "Level",
             "Stud finder"]
    steps = [
        "Cut the sides, top, and shelves to size.",
        "Assemble the carcass on its back — glue + screws, check "
        "square.",
        "Fit the shelves (fixed or on pins), add the face trim.",
        "Sand + finish, then stand it up.",
        "Anchor it to a stud. Every time, no exceptions.",
    ]
    safety = ["Anchor tall shelves to the wall — a loaded "
              "bookshelf is a tip-over hazard, full stop."] \
        + _BASE_SAFETY
    design = (f"A {_fmt_num(Wd)} ft wide x {_fmt_num(H)} ft tall "
              f"bookshelf, {_fmt_num(D)} ft deep, in "
              f"{mat_label.lower()} with {shelves} shelves.")
    return design, mats, tools, steps, safety, \
        ((60 + Wd * H * 6) * factor, (140 + Wd * H * 12) * factor), \
        "shelf materials at planning rates"


def _tpl_dog_house(d, mat_label, factor):
    L, W, H = d["l"], d["w"], d["h"] or 3.0
    mats = [
        ("3/4 in plywood sheets", "2", "floor, walls, and roof"),
        ("2 x 2 / 2 x 4 framing boards", "1 lot (~10 boards)", ""),
        ("Exterior screws", "1 lot", ""),
        ("Shingles or rolled roofing", "1 bundle / small roll", ""),
        ("Exterior paint", "1 quart", ""),
        ("Insulation board (cold climates)", "optional", ""),
    ]
    tools = ["Circular or jig saw", "Drill/driver", "Tape measure",
             "Speed square"]
    steps = [
        "Cut and frame the floor; sheet it.",
        "Frame the walls with the door opening sized to the dog "
        "(shoulder height, not more).",
        "Sheet the walls, cut the door with rounded top corners.",
        "Roof it with a slope and an overhang so rain drips clear "
        "of the walls.",
        "Paint it, raise it a few inches off the ground on blocks, "
        "and put the opening away from the prevailing wind.",
    ]
    safety = list(_BASE_SAFETY)
    design = (f"A {_dims_text(d)} dog house in {mat_label.lower()} — "
              "framed plywood, sloped roof, raised off the ground.")
    return design, mats, tools, steps, safety, \
        (120 * factor, 240 * factor), "dog house materials, flat planning range"


def _tpl_coop(d, mat_label, factor):
    L, W, H = d["l"], d["w"], d["h"] or 6.0
    mats = [
        ("2 x 4 framing boards, 8 ft", "~24", "coop + run frame"),
        ("Plywood sheets (siding + floor)", "4", ""),
        ("Hardware cloth, 1/2 in, 3 ft roll",
         "1 roll (50 ft)", "NEVER chicken wire for the run — "
         "raccoons go through it"),
        ("Roofing (metal panel or shingles)", "1 lot", ""),
        ("Hinges, latches (raccoon-proof, two-step)", "2 sets", ""),
        ("Roost bars + nesting box material", "1 lot",
         "~1 nesting box per 4 hens"),
        ("Exterior screws + staples", "1 lot", ""),
    ]
    tools = ["Circular saw", "Drill/driver", "Tin snips (for the "
             "hardware cloth)", "Level", "Tape measure"]
    steps = [
        "Frame the coop floor up on posts or blocks — dry floor, "
        "happy hens.",
        "Frame walls + a sloped roof; sheet and roof it.",
        "Frame the run and wrap it in hardware cloth — bury the "
        "skirt 12 in out flat so diggers hit wire.",
        "Fit roosts (wide side up) and nesting boxes low and dark.",
        "Hang doors with two-step latches a raccoon can't work.",
    ]
    safety = ["Hardware cloth edges are razors — gloves on, and "
              "fold every cut edge."] + _BASE_SAFETY
    design = (f"A {_dims_text(d)} chicken coop with an attached "
              f"run, framed in {mat_label.lower()}, wrapped in "
              "hardware cloth (not chicken wire).")
    return design, mats, tools, steps, safety, \
        (300 * factor, 650 * factor), "coop materials, flat planning range"


def _tpl_stairs(d, mat_label, factor):
    rise = d["l"]
    steps_n = max(2, _ceil(rise * 12 / 7.5))
    run = steps_n * 10.5 / 12
    mats = [
        (f"2 x 12 stringer boards, {_ceil(run + 4)} ft", "3",
         "two outside + one center"),
        (f"5/4 x 6 tread boards", f"{steps_n * 2}",
         f"{steps_n} steps at ~7.5 in rise / 10.5 in run"),
        ("Structural screws / carriage bolts", "1 lot",
         "stringers get hung, not toe-nailed"),
        ("Railing posts + rail boards", "1 lot",
         "any run over 30 in of rise needs a rail"),
        ("Concrete pad / footing blocks", "1 lot at the base", ""),
    ]
    tools = ["Circular saw", "Framing square", "Drill/driver",
             "Level", "Tape measure"]
    steps = [
        "Measure the TOTAL rise (finished floor to finished "
        "floor) — every step divides from that one number.",
        f"Divide it out: {steps_n} risers here. Lay the stringer "
        "out with a framing square, step by step.",
        "Cut one stringer, TEST-FIT it, then use it as the "
        "pattern for the rest.",
        "Set the stringers on a solid base, hang the tops with "
        "structural fasteners.",
        "Treads on, then the railing — graspable, 34-38 in above "
        "the nosing line.",
    ]
    safety = ["Stair math is code math: risers within 3/8 in of "
              "each other or the steps trip people. The first and "
              "last step get measured twice."] + _BASE_SAFETY
    design = (f"Stairs for a {_fmt_num(rise)} ft total rise — "
              f"{steps_n} steps, cut stringers, treads in "
              f"{mat_label.lower()}.")
    return design, mats, tools, steps, safety, \
        (180 * factor, 420 * factor), "stairs materials, flat planning range"


def _tpl_generic(kind, d, mat_label, factor, topic):
    L, W = d["l"], d["w"]
    name = topic or kind
    mats = [
        ("Framing lumber (2 x 4 / 2 x 6)", "measure on site",
         "sized from the final drawing — don't buy framing off "
         "a guess"),
        ("Sheet goods (plywood/OSB)", "measure on site", ""),
        ("Fastener assortment (screws, nails, brackets)",
         "1 lot", ""),
        ("Finish (paint / stain / sealant)", "1 lot",
         "to the look you picked"),
    ]
    tools = ["Tape measure", "Circular saw", "Drill/driver",
             "Level", "Speed square"]
    steps = [
        "Sketch it with real dimensions and check the fit where "
        "it will live.",
        "Finalize the materials list off the sketch (quantities "
        "come from the drawing, not the idea).",
        "Cut and dry-fit the frame; adjust before fasteners.",
        "Assemble, check square/plumb, then skin or top it.",
        "Finish, then the final check against the sketch.",
    ]
    safety = list(_BASE_SAFETY)
    design = (f"A {name} at about {_dims_text(d)} in "
              f"{mat_label.lower()} — a frame-and-skin build. "
              "This one's outside my standard take-offs, so the "
              "materials below are a framework: the real "
              "quantities come off a measured sketch.")
    return design, mats, tools, steps, safety, (0.0, 0.0), \
        "not ranged yet — a cost range needs the material picks " \
        "locked to the sketch"


_TEMPLATES = {"deck": _tpl_deck, "fence": _tpl_fence,
              "shed": _tpl_shed, "raised bed": _tpl_raised_bed,
              "workbench": _tpl_workbench, "pergola": _tpl_pergola,
              "bookshelf": _tpl_bookshelf,
              "dog house": _tpl_dog_house,
              "chicken coop": _tpl_coop, "stairs": _tpl_stairs,
              "table": _tpl_workbench}


def _render(plan: Dict) -> Dict:
    """Deterministic render of a stored plan: design,
    materials, tools, steps, safety, cost range. Extras
    (user-added lines) ride the materials list with a
    planning allowance added to the cost range."""
    kind = plan.get("kind", "generic")
    dims = dict(plan.get("dims") or {})
    d = {"l": float(dims.get("l", 8) or 8),
         "w": float(dims.get("w", 8) or 8),
         "h": float(dims["h"]) if dims.get("h") else None}
    mat_label = plan.get("material") or "Pressure-treated lumber"
    factor = float(plan.get("material_factor", 1.0) or 1.0)
    fn = _TEMPLATES.get(kind)
    if fn is None:
        design, mats, tools, steps, safety, (lo, hi), basis = \
            _tpl_generic(kind, d, mat_label, factor,
                         plan.get("topic", ""))
    else:
        design, mats, tools, steps, safety, (lo, hi), basis = \
            fn(d, mat_label, factor)
    mats = list(mats)
    for extra in plan.get("extras") or []:
        mats.append((f"Extra: {extra}", "1 (as spec'd)",
                     "added on your ask"))
        lo += 20
        hi += 80
    return {"design": design, "materials": mats, "tools": tools,
            "steps": steps, "safety": safety,
            "cost_low": _money(lo) if lo else 0,
            "cost_high": _money(hi) if hi else 0,
            "cost_basis": basis}


def _plan_text(plan: Dict) -> str:
    r = _render(plan)
    dims = plan.get("dims") or {}
    lines = [f"BUILD PLAN — {plan.get('title', '')}",
             "",
             f"Design: {r['design']}",
             "",
             "Materials (quantities include a waste factor — "
             "planning math, confirm against your final "
             "measurements):"]
    for name, qty, note in r["materials"]:
        line = f"  • {name} — {qty}"
        if note:
            line += f" ({note})"
        lines.append(line)
    lines.append("")
    lines.append("Tools:")
    for t in r["tools"]:
        lines.append(f"  • {t}")
    lines.append("")
    lines.append("Build sequence:")
    for i, s in enumerate(r["steps"], 1):
        lines.append(f"  {i}. {s}")
    lines.append("")
    if r["cost_low"]:
        lines.append(
            f"Rough cost: ${r['cost_low']:,}–${r['cost_high']:,}"
            f" — ESTIMATE, not a quote ({r['cost_basis']}; "
            "materials only, prices move, tax and delivery "
            "not in it).")
    else:
        lines.append(
            f"Rough cost: {r['cost_basis']} — labeled an "
            "ESTIMATE once it's ranged.")
    lines.append("")
    lines.append("Pro safety callouts:")
    for s in r["safety"]:
        lines.append(f"  • {s}")
    return "\n".join(lines)


def _title_for(kind: str, dims: Dict, topic: str) -> str:
    label = _KIND_LABELS.get(kind) or kind
    if kind == "generic" and topic:
        label = topic
    return f"{_fmt_num(float(dims.get('l', 0) or 0))}x" \
           f"{_fmt_num(float(dims.get('w', 0) or 0))} {label}"


# --- Chat claims (narrow frames — the crew/PM/filer doctrine) ---------------------

_GARBLE_VOCAB = ("build", "builder", "plan", "deck", "fence",
                 "shed", "file", "order")

_BUILDABLE = (r"(?:deck|porch|fence|fencing|shed|raised bed|"
              r"garden bed|planter(?: box)?|workbench|pergola|"
              r"gazebo|bookshelf|bookcase|shelves|shelving|"
              r"shelf|dog ?house|dog kennel|chicken coop|coop|"
              r"hen house|stairs|staircase|table|bench|"
              r"treehouse|tree house|ramp|gate|pergola|patio|"
              r"storage building|barn)")
_CREATE_RES = (
    re.compile(r"\bbuild plan\b\s*(?:for\s+)?(.+)?$", re.I),
    re.compile(r"\bplan\s+(?:a\s+|my\s+|the\s+)?(?:build|building)\b"
               r"\s*(?:for\s+)?(.+)?$", re.I),
    re.compile(r"\b(?:help me|i want to|i'd like to|i wanna|"
               r"how (?:do|would) i|how to|let's|lets)\s+build\s+"
               r"(?:a\s+|an\s+|my\s+|the\s+)?(.+)$", re.I),
    re.compile(r"\bbuild\s+(?:me\s+)?(?:a\s+|an\s+|my\s+|the\s+)"
               + _BUILDABLE + r"\b(.*)$", re.I),
    re.compile(r"\b(?:design|make)\s+(?:a\s+|an\s+|my\s+|the\s+)"
               + _BUILDABLE + r"\b(.*)$", re.I),
    re.compile(r"\bbuild\s+(?:a\s+|an\s+|my\s+)?(.+)$", re.I),
)
_SHOW_RE = re.compile(
    r"^(?:show(?:\s+me)?|pull up|see|what'?s|what is)\s+"
    r"(?:the\s+|my\s+)?(?:build\s+)?plan\b"
    r"|^the\s+build\s+plan$|^build\s+plan$", re.I)
_COST_RE = re.compile(
    r"^what(?:'s| would| will)\s+(?:it|this|that)\s+cost\b"
    r"|^how much\s+(?:would|will|is)\s+(?:it|this|that)\b"
    r"|^what'?s\s+the\s+(?:cost|price|damage)\b", re.I)
_HANDOFF_RE = re.compile(
    r"^(?:save|file|keep)\s+(?:this|the|my|that)\s+plan\b"
    r"|^(?:make|turn)\s+(?:this|it|that|the plan)\s+"
    r"(?:in)?to\s+a\s+project\b"
    r"|^start\s+(?:a\s+)?project\s+(?:from|with)\s+"
    r"(?:this|the|that)\b"
    r"|^(?:hand|pass)\s+(?:it|this|the plan)\s+off\b"
    r"|^plan'?s\s+(?:done|ready|good|final)\b", re.I)
_CLOSE_RE = re.compile(
    r"^(?:scrap|cancel|forget|drop|kill|toss)\s+"
    r"(?:the\s+|my\s+|that\s+|this\s+)?(?:build\s+)?plan\b", re.I)
_ORDER_RE = re.compile(
    r"\b(?:order|buy|purchase|get)\b[^.?!]*\b(?:materials?|"
    r"lumber|supplies|everything|it all|the list|stuff|"
    r"boards|wood)\b"
    r"|^(?:order|buy)\s+(?:it|that|this)\s+all\b", re.I)
_ADJ_DIMS_HINT = re.compile(r"\d", re.I)
_ADJ_RES = (
    re.compile(r"^(?:make|change|resize|set)\s+it\s+(.+)$", re.I),
    re.compile(r"^(?:use|go with|switch to)\s+(.+)$", re.I),
    re.compile(r"\b(.+?)\s+instead\b", re.I),
    re.compile(r"^(?:add|throw in|include)\s+(?:a\s+|an\s+|the\s+)?"
               r"(.+)$", re.I),
    re.compile(r"^(?:remove|take off|drop)\s+(?:the\s+|a\s+|an\s+)?"
               r"(.+)$", re.I),
    re.compile(r"^make\s+it\s+(bigger|smaller|longer|shorter|"
               r"wider|narrower|taller)$", re.I),
)


def _defer_to_others(message: str, uid: str) -> bool:
    """The PM, the Filer and the crew own their asks —
    checked exactly, first (the PM's own doctrine). If any
    of them claims this message, the Builder never does."""
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
    return False


def _create_payload_hit(message: str) -> Optional[str]:
    raw = str(message or "").strip()
    for rx in _CREATE_RES:
        m = rx.search(raw)
        if m:
            payload = (m.group(m.lastindex) if m.lastindex
                       else "") or ""
            payload = payload.strip(" .!?")
            if rx is _CREATE_RES[-1]:
                # The bare "build <anything>" frame only
                # claims when the payload names a buildable
                # or carries dimensions — "build me a
                # website" is not a physical build.
                if not _find_kind(raw) and not _parse_dims(raw):
                    return None
            return payload
    return None


def _claim_exact(message: str, uid: str) -> Optional[Dict]:
    raw = str(message or "").strip()
    if not raw or not uid:
        return None
    if _defer_to_others(raw, uid):
        return None
    plan = _active_plan(uid)
    if plan:
        if _CLOSE_RE.search(raw):
            return {"op": "build_close"}
        if _HANDOFF_RE.search(raw):
            return {"op": "build_handoff"}
        if _ORDER_RE.search(raw):
            return {"op": "build_order"}
        if _COST_RE.search(raw):
            return {"op": "build_show", "focus": "cost"}
        # An explicit create frame starts a FRESH plan even
        # while a draft is open — "build a 12x16 deck" is
        # never a resize of whatever happens to be open.
        created = _create_payload_hit(raw)
        if created is not None:
            return {"op": "build_create", "payload": created,
                    "raw": raw}
        if _parse_dims(raw) and len(raw) < 40:
            return {"op": "build_adjust", "payload": raw}
        for rx in _ADJ_RES:
            m = rx.search(raw)
            if m:
                # The op re-parses the full ask itself
                # (dims / material / add / remove), so the
                # payload is the whole message, verbatim.
                return {"op": "build_adjust", "payload": raw}
        if _SHOW_RE.search(raw):
            return {"op": "build_show"}
    payload = _create_payload_hit(raw)
    if payload is not None:
        return {"op": "build_create", "payload": payload,
                "raw": raw}
    if _SHOW_RE.search(raw):
        return {"op": "build_show"}
    # No open draft, but a plan was already handed off:
    # a repeat hand-off ask still claims, so the answer can
    # say "already with the Project Manager" instead of
    # the seam going silent.
    if _HANDOFF_RE.search(raw) and any(
            p.get("status") == "handed_off"
            for p in _plans(uid) if isinstance(p, dict)):
        return {"op": "build_handoff"}
    return None


def _claim_builder(message: str, uid: str):
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


# --- Operations -------------------------------------------------------------------

def _result(tag: str, title: str, body: str,
            href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body,
             "href": href}]


def _plan_intro(plan: Dict) -> str:
    return (_plan_text(plan)
            + "\n\n(The plan above is the Builder's — relay it "
              "in full, in persona, without dropping the "
              "estimate label or the safety callouts. The user "
              "can adjust it in chat — dimensions, materials, "
              "extras — and say \"save the plan\" to hand it "
              "to the Project Manager as a project with the "
              "plan filed under it.)")


def _do_create(uid: str, claim: Dict) -> List[Dict]:
    payload = claim.get("payload") or ""
    raw = claim.get("raw") or payload
    kind = _find_kind(raw) or _find_kind(payload) or "generic"
    dims_hit = _parse_dims(raw) or _parse_dims(payload)
    dl, dw, dh = _DEFAULT_DIMS.get(kind, (8.0, 8.0, None))
    if dims_hit:
        dl, dw = dims_hit[0], dims_hit[1]
        dh = dims_hit[2] if dims_hit[2] else dh
    mat_label, factor = _find_material(raw)
    if not mat_label:
        mat_label, factor = "Pressure-treated lumber", 1.0
    topic = ""
    if kind == "generic":
        topic = re.sub(r"\s+", " ",
                       payload or raw).strip(" .!?")[:60]
    plan = {"id": uuid.uuid4().hex[:12], "uid": uid,
            "kind": kind, "topic": topic,
            "dims": {"l": dl, "w": dw, "h": dh},
            "material": mat_label, "material_factor": factor,
            "extras": [], "status": "draft",
            "project_id": "", "doc_id": "",
            "created": time.time(), "updated": time.time()}
    plan["title"] = _title_for(kind, plan["dims"], topic)
    with _builder_lock:
        plans = _plans(uid)
        plans.append(plan)
        _save_plans(uid, plans)
    return _result("OG PROJECT BUILDER",
                   f"Project Builder — plan: {plan['title']}",
                   _plan_intro(plan))


def _do_show(uid: str, claim: Dict) -> List[Dict]:
    plan = _active_plan(uid)
    if not plan:
        handed = [p for p in plans_for(uid)
                  if p.get("status") == "handed_off"]
        if handed:
            return _result(
                "OG PROJECT BUILDER", "Project Builder — plans",
                "Your newest plan is already handed off ("
                f"\"{handed[0].get('title', '')}\" — Project "
                "Manager has it). No open draft right now — "
                "say \"build a …\" and I'll draw a fresh one.")
        return _result(
            "OG PROJECT BUILDER", "Project Builder — no plan yet",
            "No build plan on the table yet. Say \"build a "
            "12x16 deck\" (or whatever you're building, with "
            "the size) and I'll draw the full plan — "
            "materials, tools, steps, cost range, safety.")
    body = _plan_text(plan)
    if claim.get("focus") == "cost":
        r = _render(plan)
        if r["cost_low"]:
            body = (f"On the current plan ({plan['title']}): "
                    f"rough cost ${r['cost_low']:,}–"
                    f"${r['cost_high']:,} — ESTIMATE, not a "
                    f"quote ({r['cost_basis']}; materials "
                    "only).\n\n" + body)
    return _result("OG PROJECT BUILDER",
                   f"Project Builder — plan: {plan['title']}",
                   body)


def _do_adjust(uid: str, claim: Dict) -> List[Dict]:
    plan = _active_plan(uid)
    if not plan:
        return _do_show(uid, claim)
    payload = claim.get("payload") or ""
    dims = dict(plan.get("dims") or {})
    changes = []
    hit = _parse_dims(payload)
    if hit:
        dims["l"], dims["w"] = hit[0], hit[1]
        if hit[2]:
            dims["h"] = hit[2]
        changes.append(f"size is now {_dims_text(dims)}")
    label, factor = _find_material(payload)
    low = " " + payload.lower() + " "
    if label and ("instead" in low or "use " in low
                  or "go with" in low or "switch" in low
                  or payload.lower().strip()
                  == label.lower()):
        plan["material"] = label
        plan["material_factor"] = factor
        changes.append(f"material is now {label.lower()}")
    scale_words = {"bigger": ("l", 1.25), "longer": ("l", 1.25),
                   "smaller": ("l", 0.8), "shorter": ("l", 0.8),
                   "wider": ("w", 1.25), "narrower": ("w", 0.8),
                   "taller": ("h", 1.25)}
    for word, (axis, f) in scale_words.items():
        if word in low and axis in dims and dims.get(axis):
            dims[axis] = round(float(dims[axis]) * f, 2)
            changes.append(f"{word} — now {_dims_text(dims)}")
            break
    extras = list(plan.get("extras") or [])
    if re.search(r"^(?:add|throw in|include)\b", payload, re.I):
        extra = re.sub(r"^(?:add|throw in|include)\s+"
                       r"(?:a\s+|an\s+|the\s+)?", "", payload,
                       flags=re.I).strip(" .!?")[:60]
        if extra and extra.lower() not in [e.lower()
                                           for e in extras]:
            extras.append(extra)
            changes.append(f"added: {extra}")
    if re.search(r"^(?:remove|take off|drop)\b", payload, re.I):
        extra = re.sub(r"^(?:remove|take off|drop)\s+"
                       r"(?:the\s+|a\s+|an\s+)?", "", payload,
                       flags=re.I).strip(" .!?")[:60]
        kept = [e for e in extras
                if extra.lower() not in e.lower()]
        if len(kept) != len(extras):
            extras = kept
            changes.append(f"removed: {extra}")
    if not changes:
        # A claimed adjustment frame whose payload named
        # nothing computable — ask plainly instead of
        # pretending something changed.
        return _result(
            "OG PROJECT BUILDER", "Project Builder — adjust",
            f"I've got the \"{plan['title']}\" plan open, "
            "but I didn't catch what to change in that. "
            "Name the new size (\"make it 10x14\"), the "
            "material (\"use cedar instead\"), or an extra "
            "(\"add a gate\") and I'll re-run the numbers.")
    plan["dims"] = dims
    plan["extras"] = extras
    plan["title"] = _title_for(plan.get("kind", "generic"),
                               dims, plan.get("topic", ""))
    _update_plan(uid, plan["id"], dims=dims, extras=extras,
                 material=plan.get("material"),
                 material_factor=plan.get("material_factor"),
                 title=plan["title"])
    fresh = _get_plan(uid, plan["id"]) or plan
    return _result(
        "OG PROJECT BUILDER",
        f"Project Builder — updated: {fresh['title']}",
        "Updated — " + "; ".join(changes) + ".\n\n"
        + _plan_intro(fresh))


def _do_close(uid: str) -> List[Dict]:
    plan = _active_plan(uid)
    if not plan:
        return _result("OG PROJECT BUILDER",
                       "Project Builder — nothing to scrap",
                       "No open build plan right now — nothing "
                       "to scrap.")
    _update_plan(uid, plan["id"], status="cancelled")
    return _result(
        "OG PROJECT BUILDER", "Project Builder — scrapped",
        f"Scrapped — the \"{plan['title']}\" draft is "
        "closed. Nothing was ordered or started; it was "
        "only ever a plan. Say \"build a …\" when you want "
        "a fresh one.")


def _do_order(uid: str) -> List[Dict]:
    """The ordering-route, honest by construction: the
    Builder prepares the materials sheet FROM THE PLAN and
    names the door it goes through — OG's ordering pack
    (prepare -> the user approves the preview -> handoff
    to the store). Nothing here buys, imports, or calls
    any ordering machinery."""
    plan = _active_plan(uid)
    if not plan:
        return _result(
            "OG PROJECT BUILDER", "Project Builder — no plan",
            "There's no build plan to order from yet — say "
            "\"build a …\" first and I'll draw one up.")
    r = _render(plan)
    lines = [f"Order sheet from the plan "
             f"\"{plan['title']}\" (materials only):"]
    for name, qty, note in r["materials"]:
        lines.append(f"  • {name} — {qty}")
    if r["cost_low"]:
        lines.append(f"Rough materials total: "
                     f"${r['cost_low']:,}–${r['cost_high']:,}"
                     " (ESTIMATE).")
    lines.append(
        "How buying works here: I don't order anything "
        "myself — this sheet goes through OG's ordering "
        "flow, which prepares the order at the store you "
        "pick and stops for YOUR approval on the preview "
        "before a single dollar moves. Say \"order from "
        "<store>\" with a store in mind (or paste this "
        "sheet there) and the ordering flow takes it from "
        "here. Nothing has been bought or charged.")
    return _result("OG PROJECT BUILDER",
                   "Project Builder — order sheet", "\n".join(lines))


def _tier_for_save(uid: str) -> str:
    """The tier the storage seam itself resolves for the
    current caller (og_storage's app-bound get_tier) —
    reused, never re-derived here."""
    try:
        import og_storage
        return og_storage._tier_now(uid)
    except Exception:
        return "free"


def _save_plan_doc(uid: str, plan: Dict):
    """Save the plan document into the caller's locker.
    Returns (record, reason): exactly one is set. Reasons
    are the storage layer's own honest words, shortened to
    their kind: dark / free / quota / other."""
    import og_storage
    if not og_storage.storage_enabled():
        return None, "dark"
    name = f"{plan['title']} — build plan.txt"
    data = _plan_text(plan).encode("utf-8")[:PLAN_DOC_CAP]
    try:
        rec = og_storage.save_bytes(uid, _tier_for_save(uid),
                                    name, data, "text/plain")
        return rec, ""
    except Exception as e:
        msg = str(e).strip()
        if msg in ("dark", "free"):
            return None, msg
        if "only got" in msg or "left in your locker" in msg:
            return None, "quota"
        logger.warning(f"Builder plan save failed: {e}")
        return None, "other"


def _do_handoff(uid: str) -> List[Dict]:
    plan = _active_plan(uid)
    if not plan:
        views = [p for p in plans_for(uid)
                 if p.get("status") == "handed_off"]
        if views:
            return _result(
                "OG PROJECT BUILDER", "Project Builder — handed off",
                f"The \"{views[0].get('title', '')}\" plan is "
                "already with the Project Manager — nothing "
                "new to hand off. Say \"build a …\" for a "
                "fresh plan.")
        return _result(
            "OG PROJECT BUILDER", "Project Builder — no plan",
            "No build plan to hand off yet — say \"build "
            "a …\" and I'll draw one up first.")
    # 1) The PM project (og_pm's own create path).
    try:
        import og_pm
        before = {p.get("id") for p in og_pm._projects(uid)
                  if isinstance(p, dict)}
        goal = f"Build the {plan['title']}"
        og_pm._create_project(uid, goal)
        after = [p for p in og_pm._projects(uid)
                 if isinstance(p, dict)
                 and p.get("id") not in before]
        proj = after[0] if after else None
    except Exception as e:
        logger.warning(f"Builder PM handoff failed: {e}")
        proj = None
    if not proj:
        return _result(
            "OG PROJECT BUILDER", "Project Builder — handoff glitch",
            "The Project Manager didn't take the plan — "
            "nothing was created and the draft is still "
            "open here. Say \"save the plan\" again and "
            "I'll retry.")
    pid = proj.get("id", "")
    ptitle = proj.get("title", "")
    # 2) The plan document into the locker, 3) filed.
    rec, why = _save_plan_doc(uid, plan)
    doc_line = ""
    filed = False
    if rec:
        try:
            import og_filing
            link = og_filing.file_link(uid, pid, "locker",
                                        rec.get("kind", ""),
                                        rec.get("id", ""))
            filed = link is not None
        except Exception as e:
            logger.warning(f"Builder filing failed: {e}")
            filed = False
        if filed:
            doc_line = (f"The plan itself is saved in your "
                        f"locker as \"{rec.get('name', '')}\" "
                        f"and filed under the project.")
        else:
            doc_line = (f"The plan document is saved in "
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
        doc_line = (f"The plan document could NOT be saved "
                    f"to your locker — {reasons.get(why, why)}"
                    " — so there's nothing for the Filer to "
                    "link yet. The plan still lives right "
                    "here with the Builder, and the project "
                    "below carries the work.")
    _update_plan(uid, plan["id"], status="handed_off",
                 project_id=pid,
                 doc_id=(rec or {}).get("id", ""))
    body = (f"Handed off — the Project Manager now has "
            f"\"{ptitle}\" as a project (Progress shows it), "
            "with its steps queued there. " + doc_line
            + " The Builder's draft is closed; changes from "
              "here run through the project.")
    return _result("OG PROJECT BUILDER",
                   "Project Builder — handed off", body)


def builder_results(claim: Dict, uid: str) -> List[Dict]:
    op = claim.get("op")
    if op == "build_create":
        return _do_create(uid, claim)
    if op == "build_show":
        return _do_show(uid, claim)
    if op == "build_adjust":
        return _do_adjust(uid, claim)
    if op == "build_close":
        return _do_close(uid)
    if op == "build_order":
        return _do_order(uid)
    if op == "build_handoff":
        return _do_handoff(uid)
    return _do_show(uid, claim)


# --- The seam ---------------------------------------------------------------------

_pending = {"claim": None, "garble": None}


def install_builder_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search OUTSIDE the Filing
    seam (installed last, from the og_fixqueue chain) so
    build-planning asks claim here and everything else
    delegates back untouched. Persona files never
    touched."""
    if getattr(agent_instance, "_og_builder_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["claim"] = None
        _pending["garble"] = None
        try:
            uid = get_uid()
            if uid and builder_enabled():
                claim, info = _claim_builder(str(message), uid)
                if claim:
                    _pending["claim"] = claim
                    _pending["garble"] = info
                    if isinstance(intent, dict):
                        intent["needs_code_generation"] = False
                        if not intent.get("needs_web_search"):
                            intent["needs_web_search"] = True
                            intent["search_query"] = \
                                str(message).strip()
        except Exception as e:
            logger.warning(f"Builder trigger check failed: {e}")
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
                results = builder_results(claim, uid)
            except Exception as e:
                logger.warning(f"Builder op failed: {e}")
                results = _result(
                    "OG PROJECT BUILDER",
                    "Project Builder — glitch",
                    "The build ask glitched — nothing was "
                    "planned, ordered, or handed off. Say it "
                    "again and I'll retry.")
        if results is None:
            results = prev_search(query, num_results)
        if garble and results:
            results = og_garble.annotate(results, garble,
                                         prepend=True)
        return results

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_builder_installed = True


# --- Routes -------------------------------------------------------------------------

def register_builder_routes(app):
    """GET /builder/status — the caller's own build plans
    (id, title, kind, status, cost range, the PM project
    once handed off). Identity is the caller's cookie uid,
    like every Round 95+ surface."""

    @app.get("/builder/status")
    def builder_status_route(request: Request):
        uid = request.cookies.get("ogai_uid") or ""
        return {"ok": True, "enabled": builder_enabled(),
                "plans": plans_for(uid) if uid else []}
