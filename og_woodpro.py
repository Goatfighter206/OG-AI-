"""Round 112: THE WOOD PROJECT BUILDER HELPER, PRO
(Brent's order, 2026-10-12 11:11 PDT: "a pro project
builder of wood helper" — specced right after the
Project Builder: R111 is the general builder; this is
the WOOD specialist, the shop brain of the crew.)

WHAT THIS IS. A crew specialist (the R81 pattern) for
WOODWORKING, one shelf above the Project Builder
(R111). The Builder takes off decks and sheds; the Pro
designs WOOD pieces like a cabinetmaker:

  - lumber SPECIES + grade talk with the why (hardness,
    workability, movement, where each species wins);
  - JOINERY chosen by LOAD — mortise & tenon where the
    piece racks, dadoes where a shelf carries weight,
    floating panels where wood moves — with the reason
    printed next to the choice;
  - a real CUT LIST: every piece part with species,
    stock, FINISHED dimensions, ROUGH dimensions,
    grain direction, and board feet per part;
  - BOARD-FOOT MATH, SHOWN — the formula rides the
    plan, every part carries its own computation, and
    the purchase math shows the waste factor and the
    saw KERF (1/8 in per cut) before it names how many
    boards to buy;
  - a HARDWARE list and a FINISH SCHEDULE (prep grits,
    coats, dry + cure times — and food-safe finishes
    where food touches);
  - the build sequence in SHOP ORDER: mill, cut, join,
    assemble, sand, finish;
  - BIG-JOB breakdowns: ask for 4 stools and the plan
    batches — per-piece board feet, totals across the
    run, and a batch milling order.

Numbers are deterministic: the cut list and the math
recompute from the stored plan's parameters on every
render — never from the model improvising.

HOME, LATER. Brent's queued Woodworking page (the app
Construction folder's full-screen page, last build in
the line) is this helper's natural home when it lands;
THIS round is chat-first only — no page is built here.

HAND-OFF (the R111 shape, through the shipped seams).
"Save the plan" creates a Project Manager project
(og_pm._create_project), saves the cut-list document
into the locker (og_storage.save_bytes), and files it
under the project (og_filing.file_link). If the
locker refuses, the answer says so plainly — nothing
pretends to be filed.

THE FENCES.
- Knowledge + planning ONLY. The module imports no
  ordering/trading machinery (suite-pinned): "order
  the lumber" returns the plan's own lumber sheet +
  the ordering pack's approval routing. Never buys.
- Per-account isolation (drill-proven cross-account).
- NO new loop (R72): exchanges only.
- Persona untouched; app.py never touched (install
  rides the og_fixqueue chain AFTER the Builder —
  the Pro wraps outermost, defers to the PM/Filer/
  crew claims checked exactly first, and takes only
  wood-flavored asks: furniture pieces, cut lists,
  joinery, species, finishes, board feet. Structures
  — decks, fences, sheds — stay the Builder's.)

STATE. Own durable store, the Round 18 pattern:
Postgres table og_woodpro_data when OG_MEMORY_DB_URL
is set, else a JSON file. Blob kind per visitor:
'plans'. Switch: OG_WOODPRO_ENABLED (unset = ON) —
the SPEC's OG_WOODBUILDER_ENABLED name is honored as
an alias: either one set false kills the seat (the
round brief and the spec named it differently; both
work, named in round-112/NOTES.md).
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

# --- Config -------------------------------------------------------------------

PLAN_KEEP = 20           # plans kept per visitor (drafts + handed off)
PLAN_DOC_CAP = 14000     # chars of the saved cut-list document
KERF_IN = 0.125          # saw kerf per cut, inches (a standard blade)
WASTE = 1.15             # 15% waste factor (defects, grain matching, slips)
WASTE_BOARD = 1.20       # cutting boards: grain matching eats more

WOODPRO_STORE_FILE = "woodpro_store.json"
# RLock, not Lock: store updates hold the lock across a
# read-modify-write whose save path re-enters it through
# _put_blob's file branch (the og_pm/og_builder shape).
_woodpro_lock = threading.RLock()

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, StoreError, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None


# --- Switch ---------------------------------------------------------------------

def woodpro_enabled() -> bool:
    """OG_WOODPRO_ENABLED=false is the Pro's own kill
    switch (the spec's OG_WOODBUILDER_ENABLED alias is
    honored the same way). Unset anywhere = ON."""
    for name in ("OG_WOODPRO_ENABLED", "OG_WOODBUILDER_ENABLED"):
        val = os.environ.get(name)
        if val is not None and val.strip().lower() in (
                "0", "false", "no", "off"):
            return False
    return True


# --- Durable store (Round 18 pattern, mirroring og_builder) ----------------------

def _db_connect():
    conn = psycopg.connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_woodpro_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(WOODPRO_STORE_FILE):
        try:
            with open(WOODPRO_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"WoodPro store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(WOODPRO_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"WoodPro store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_woodpro_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"WoodPro DB load failed: {e}")
            raise StoreError("woodpro store unavailable (Round 99)") from e
    with _woodpro_lock:
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
                        "INSERT INTO og_woodpro_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"WoodPro DB save failed: {e}")
            raise StoreError("woodpro store unavailable (Round 99)") from e
    with _woodpro_lock:
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
    """The newest DRAFT wood plan — the one adjustments
    and the hand-off act on. Handed-off plans are closed."""
    drafts = [p for p in _plans(uid)
              if isinstance(p, dict) and p.get("status") == "draft"]
    if not drafts:
        return None
    drafts.sort(key=lambda p: float(p.get("updated", 0) or 0),
                reverse=True)
    return drafts[0]


def _update_plan(uid: str, plan_id: str, **fields) -> Optional[Dict]:
    with _woodpro_lock:
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
    """The read seam (/woodpro/status): the caller's own
    wood plan views, newest first."""
    out = []
    for p in _plans(uid):
        if not isinstance(p, dict):
            continue
        r = _render(p)
        out.append({
            "id": p.get("id", ""), "title": p.get("title", ""),
            "kind": p.get("kind", ""), "status": p.get("status", ""),
            "species": p.get("species", ""),
            "qty": int(p.get("qty", 1) or 1),
            "board_feet": r["bf_total"],
            "buy_board_feet": r["bf_buy"],
            "cost_low": r["cost_low"], "cost_high": r["cost_high"],
            "project_id": p.get("project_id", ""),
            "created": float(p.get("created", 0) or 0),
            "updated": float(p.get("updated", 0) or 0),
        })
    out.sort(key=lambda x: -x["created"])
    return out


# --- Species ------------------------------------------------------------------
# rate = planning $ per board foot (rough lumber, 2026
# yard talk — labeled an estimate wherever it renders).

_SPECIES = (
    ("walnut", "Black walnut", 11.0,
     "Janka ~1,010 — medium-hard, works beautifully, "
     "steams and glues well; the dust is an irritant, "
     "mask up. Brown heartwood that lightens with age; "
     "the showpiece species."),
    ("white oak", "White oak", 7.0,
     "Janka ~1,360 — hard, rot-resistant (closed pores), "
     "quartersawn stock shows ray fleck. Tannins blacken "
     "iron — use stainless or brass near it."),
    ("red oak", "Red oak", 5.0,
     "Janka ~1,290 — hard, open grain, takes stain like "
     "a sponge (watch blotching on end grain). The "
     "cabinet-shop standard."),
    ("hard maple", "Hard maple", 6.0,
     "Janka ~1,450 — very hard, tight grain, burns under "
     "a dull blade; the workbench-top and cutting-board "
     "species. Sharp tools, light passes."),
    ("maple", "Hard maple", 6.0,
     "Janka ~1,450 — very hard, tight grain, burns under "
     "a dull blade; the workbench-top and cutting-board "
     "species. Sharp tools, light passes."),
    ("cherry", "American cherry", 7.0,
     "Janka ~950 — medium, machines cleanly, darkens "
     "richly in sunlight over the first year. Blotches "
     "under stain; oil lets it age honest."),
    ("ash", "White ash", 5.0,
     "Janka ~1,320 — hard, springy, open grain like oak "
     "but paler. The tool-handle and stool-leg species."),
    ("hickory", "Hickory", 6.0,
     "Janka ~1,820 — the hardest common domestic; brutal "
     "on blades, unbeatable where parts take hits."),
    ("poplar", "Poplar", 4.0,
     "Janka ~540 — soft, cheap, machines like a dream; "
     "the paint-grade and drawer-side species. Green "
     "streaks don't take clear finishes gracefully."),
    ("pine", "Pine", 3.0,
     "Janka ~380-690 — soft, dents easy, knots bleed "
     "through paint unless sealed with shellac first. "
     "Honest utility wood."),
    ("cedar", "Western red cedar", 5.0,
     "Janka ~350 — very soft, rot-proof, splits easy; "
     "outdoor and closet work, not load-bearing "
     "furniture."),
    ("birch", "Yellow birch", 5.5,
     "Janka ~1,260 — hard, fine grain, the plywood-face "
     "species; solid stock works like maple's cousin."),
    ("mahogany", "Mahogany", 9.0,
     "Janka ~800-900 — medium, legendary stability and "
     "workability; the fine-furniture species, priced "
     "like it."),
)

_SPECIES_DEFAULT = ("Hard maple", 6.0, _SPECIES[3][3])


def _find_species(text: str):
    low = " " + str(text or "").lower() + " "
    for key, label, rate, note in _SPECIES:
        if key in low:
            return label, rate, note
    return None, None, None


_KIND_DEFAULT_SPECIES = {
    "workbench": "Hard maple", "table": "White oak",
    "bookshelf": "Pine", "cutting board": "Hard maple",
    "stool": "Pine", "bench": "Pine",
    "cabinet": "Hard maple", "generic": "Red oak",
}


def _species_rate(label: str) -> float:
    for _key, lab, rate, _note in _SPECIES:
        if lab == label:
            return rate
    return 6.0


def _species_note(label: str) -> str:
    for _key, lab, _rate, note in _SPECIES:
        if lab == label:
            return note
    return ""


# --- Finishes -----------------------------------------------------------------

_FINISH_SCHEDULES = {
    "danish oil": (
        "Danish oil (oil/varnish blend)",
        ["Sand to 220, raising the grain with a damp wipe "
         "between 150 and 220.",
         "Flood coat 1, keep it wet 15 minutes, wipe off "
         "every bit of excess.",
         "Coat 2 after 24 hours, same wipe-off discipline.",
         "Optional coat 3 after another 24 hours for "
         "sheen.",
         "Cure: handle in 24-48 hours, full cure ~7 days. "
         "Re-oil yearly on working surfaces."]),
    "hardwax oil": (
        "Hardwax oil",
        ["Sand to 180 (220 on show faces).",
         "Thin coat worked in with a white pad, buff off "
         "within 10 minutes — nothing sits wet.",
         "Second coat after 12-24 hours, same method.",
         "Cure: use gently at 24 hours, full cure ~7 "
         "days. Spot-repairable forever — its whole "
         "point."]),
    "wipe-on poly": (
        "Wipe-on polyurethane",
        ["Sand to 220; tack off the dust — poly shows "
         "every speck.",
         "Thin wipe-on coats, 4-6 of them, 2-3 hours "
         "apart, scuffing with 320 between coats.",
         "Last coat goes on and stays untouched.",
         "Cure: use gently at 24 hours, full cure ~30 "
         "days for heavy loads."]),
    "poly": (
        "Wipe-on polyurethane",
        ["Sand to 220; tack off the dust — poly shows "
         "every speck.",
         "Thin wipe-on coats, 4-6 of them, 2-3 hours "
         "apart, scuffing with 320 between coats.",
         "Last coat goes on and stays untouched.",
         "Cure: use gently at 24 hours, full cure ~30 "
         "days for heavy loads."]),
    "shellac": (
        "Shellac",
        ["Sand to 220.",
         "Pound-cut thinned with denatured alcohol; pad "
         "or brush fast, thin coats, 30-45 minutes apart, "
         "3-4 coats.",
         "Scuff 320 before the final coat; optional wax "
         "after a week.",
         "Cure: hard in days. Note: alcohol and heat "
         "rings mark it — coasters are part of the "
         "deal."]),
    "mineral oil": (
        "Mineral oil + board butter (FOOD-SAFE)",
        ["Sand to 220 (320 on the faces that touch "
         "knives).",
         "Flood with food-grade mineral oil, let it drink "
         "20 minutes, wipe dry. Repeat until it stops "
         "drinking (usually 3-4 rounds over a day).",
         "Top with board butter (mineral oil + beeswax), "
         "buff off after 15 minutes.",
         "No cure time — food-safe the moment it's wiped. "
         "Re-oil monthly; NEVER soak the board in water."]),
    "tung oil": (
        "Pure tung oil",
        ["Sand to 220.",
         "Cut the first coats 50/50 with citrus solvent; "
         "flood, wait 30 minutes, wipe fully dry.",
         "4-6 coats, 24 hours apart, each wiped bone dry "
         "— tung oil left wet stays gummy.",
         "Cure: full cure ~30 days. Water-resistant, not "
         "waterproof."]),
    "paint": (
        "Paint (over a shellac-based primer)",
        ["Sand to 150; the primer wants tooth.",
         "Shellac-based primer first — it seals knots and "
         "tannins so they never bleed through.",
         "Two top coats, scuffing 220 between; thin coats "
         "beat one thick one.",
         "Cure: dry in hours, full hardness ~7-30 days by "
         "product."]),
    "lacquer": (
        "Lacquer",
        ["Sand to 220; seal coat of thinned lacquer or "
         "vinyl sealer.",
         "3-4 spray coats, 30-60 minutes apart, scuffing "
         "320 lightly once.",
         "Cure: hard overnight, rub out after ~7 days. "
         "Spray only with ventilation and a respirator — "
         "the fumes are the real thing."]),
}

_FINISH_WORDS = ("danish oil", "hardwax oil", "hard-wax oil",
                 "wipe-on poly", "poly", "polyurethane",
                 "shellac", "mineral oil", "tung oil",
                 "paint", "lacquer")


def _find_finish(text: str):
    low = " " + str(text or "").lower() + " "
    for w in _FINISH_WORDS:
        if w in low:
            key = "hardwax oil" if w == "hard-wax oil" else w
            if key == "polyurethane":
                key = "poly"
            return key
    return None


_KIND_DEFAULT_FINISH = {
    "workbench": "danish oil", "table": "wipe-on poly",
    "bookshelf": "shellac", "cutting board": "mineral oil",
    "stool": "danish oil", "bench": "danish oil",
    "cabinet": "wipe-on poly", "generic": "danish oil",
}

# Joinery a user might request by name; the plan honors
# the pick and says plainly when it's under-built for
# the load (the Pro's honesty is the product).
_JOINERY_WANTS = ("mortise and tenon", "mortise & tenon",
                  "dowels", "dowel", "pocket screws",
                  "pocket holes", "biscuits", "dovetails",
                  "dovetail", "box joints", "box joint",
                  "dados", "screws and glue", "butt joints")
_WEAK_FOR_LOAD = {"pocket screws", "pocket holes",
                  "biscuits", "butt joints",
                  "screws and glue"}


def _find_joinery(text: str):
    low = " " + str(text or "").lower() + " "
    for w in _JOINERY_WANTS:
        if w in low:
            return w.replace("mortise & tenon",
                             "mortise and tenon")
    return None


# --- Helpers --------------------------------------------------------------------

def _ceil(x: float) -> int:
    return int(math.ceil(x - 1e-9))


def _money(x: float) -> int:
    return int(round(x / 10.0) * 10)


def _fmt(x: float) -> str:
    if abs(x - round(x)) < 0.01:
        return str(int(round(x)))
    return f"{x:g}"


def _fmt_in(x: float) -> str:
    """Inches, the way a tape reads them (quarters)."""
    whole = int(x)
    frac = x - whole
    q = {0.0: "", 0.25: " 1/4", 0.5: " 1/2", 0.75: " 3/4"}
    key = round(frac * 4) / 4
    if key == 1.0:
        return str(whole + 1)
    if key in q:
        return f"{whole}{q[key]}" if whole else q[key].strip()
    return f"{x:g}"


def _bf(t: float, w: float, l: float) -> float:
    """Board feet from ROUGH (nominal) inches — how the
    yard sells it: (T x W x L) / 144."""
    return t * w * l / 144.0


def _part(name, qty, stock, rough, fin, grain, note=""):
    return {"part": name, "qty": qty, "stock": stock,
            "rough": rough, "fin": fin, "grain": grain,
            "note": note,
            "bf_each": _bf(rough[0], rough[1], rough[2])}


def _dims_ft(plan: Dict):
    dims = dict(plan.get("dims") or {})
    return (float(dims.get("l", 4) or 4),
            float(dims.get("w", 2) or 2),
            float(dims["h"]) if dims.get("h") else None)


# --- Part templates -------------------------------------------------------------
# Each returns (parts, joinery_lines, hardware, sequence,
# safety, design). Parts are ONE piece's worth; the
# render multiplies by the plan quantity. Dimensions in
# inches inside the templates (converted from the
# stored feet). Grain direction rides every part.

_BASE_SAFETY = [
    "Eye protection on every cut, hearing protection at "
    "the planer and router, and a real dust mask — "
    "walnut, oak, and maple dust are lung irritants, "
    "not shop perfume.",
    "Guards on, push sticks anywhere a hand comes "
    "within a hand's width of a blade, and never reach "
    "over a spinning blade to clear a cutoff.",
    "Oily finish rags go in a metal can with water or "
    "laid flat outside to dry — a wadded oily rag can "
    "spontaneously combust. Every firefighter will "
    "back this line.",
]


def _tpl_workbench(plan):
    L, W, H = _dims_ft(plan)
    Li, Wi, Hi = L * 12, W * 12, (H or 3.0) * 12
    top_t = 1.75
    n_top = max(3, _ceil(Wi / 6.0))
    parts = [
        _part("Top boards", n_top, "8/4",
              (2, 6, Li + 2), (top_t, 5.75, Li),
              "grain runs the length; faces book-matched "
              "for a flat glue-up",
              "mill oversize, glue up, then flatten and "
              "trim to final size"),
        _part("Legs", 4, "12/4",
              (3, 3, Hi - top_t + 1.5),
              (2.75, 2.75, Hi - top_t),
              "straight grain, vertical — reject any leg "
              "blank with runout",
              "12/4 stock, or laminate two 8/4 boards face "
              "to face"),
        _part("Long aprons", 2, "4/4",
              (1, 6, Li - 2.75 * 2 + 4),
              (0.75, 5.5, Li - 2.75 * 2),
              "grain runs the length (the span)",
              "length shown is the shoulder-to-shoulder "
              "span; the rough length carries +4 in for "
              "the two tenons"),
        _part("Short aprons", 2, "4/4",
              (1, 6, Wi - 2.75 * 2 + 4),
              (0.75, 5.5, Wi - 2.75 * 2),
              "grain runs the length (the span)",
              "+4 in of rough length is the tenons"),
        _part("Long stretchers", 2, "4/4",
              (1, 4, Li - 2.75 * 2 + 2),
              (0.75, 3.5, Li - 2.75 * 2),
              "grain runs the length", ""),
        _part("Shelf slats", max(3, _ceil((Wi - 6) / 5.5)),
              "4/4", (1, 5.5, Li - 8),
              (0.75, 5.25, Li - 10),
              "grain runs the length",
              "the lower shelf — sized off the stretchers"),
    ]
    joinery = [
        "Legs to aprons: MORTISE AND TENON, drawbored if "
        "you want it heirloom. A bench lives under racking "
        "loads — you lean, you plane, you shove — and M&T "
        "is the joint that shrugs racking off for decades.",
        "Top to base: figure-8 fasteners or shop-made "
        "buttons in a groove — NEVER a rigid screw-down. "
        "A top this wide moves ~1/4 in across seasons; "
        "the fasteners let it breathe instead of "
        "splitting.",
        "Stretchers: short tenons or housed dowels — "
        "they carry the shelf, not the racking.",
    ]
    hardware = ["Figure-8 desktop fasteners or bench "
                "buttons (8-12)",
                "Wood glue (type II; type III if the "
                "shop runs damp)",
                "Optional: bench dogs + a face vise — "
                "hardware, not lumber, buy when ready"]
    seq = ["MILL: flatten and thickness the top boards "
           "first (they're the reference for everything), "
           "then legs, then aprons. Sticker the milled "
           "stock overnight — milled wood moves.",
           "CUT: top boards to rough length, legs to "
           "final, aprons with the +4 in tenon allowance "
           "still on.",
           "JOIN: cut the leg mortises, fit apron tenons "
           "to a friction fit (shaving-thin adjustments), "
           "dry-assemble the whole base and check the "
           "diagonals.",
           "ASSEMBLE: glue the base in stages (end frames "
           "first, then the long aprons), glue up the "
           "top separately, flatten it, then fasten it "
           "to the base loose enough to move.",
           "SAND: base to 150, top to 180 — a bench top "
           "gets re-flattened and re-oiled, not babied.",
           "FINISH: per the schedule below; the top earns "
           "the extra coat."]
    design = (f"A {_fmt(L)} x {_fmt(W)} ft workbench, "
              f"{_fmt(Hi)} in tall, in "
              f"{plan.get('species', '').lower()} — "
              "laminated top on a mortise-and-tenon base "
              "with a slatted lower shelf.")
    return parts, joinery, hardware, seq, list(
        _BASE_SAFETY), design


def _tpl_table(plan):
    L, W, H = _dims_ft(plan)
    Li, Wi, Hi = L * 12, W * 12, (H or 2.5) * 12
    n_top = max(4, _ceil(Wi / 7.0))
    parts = [
        _part("Top boards", n_top, "4/4",
              (1, 7, Li + 2), (0.8, 6.75, Li),
              "grain runs the length; alternate growth "
              "rings up/down to fight cup",
              "breadboard ends are the pro upgrade — ask "
              "and I'll spec them"),
        _part("Legs", 4, "8/4",
              (2.5, 2.5, Hi - 0.8 + 1),
              (2.25, 2.25, Hi - 0.8),
              "straight grain, vertical",
              "taper the two inside faces below the apron "
              "for the classic look"),
        _part("Long aprons", 2, "4/4",
              (1, 4.5, Li - 4.5 + 4), (0.75, 4.25, Li - 4.5),
              "grain runs the length (the span)",
              "+4 in of rough length is the tenons"),
        _part("Short aprons", 2, "4/4",
              (1, 4.5, Wi - 4.5 + 4), (0.75, 4.25, Wi - 4.5),
              "grain runs the length (the span)",
              "+4 in of rough length is the tenons"),
    ]
    joinery = [
        "Aprons to legs: MORTISE AND TENON. A table's "
        "whole life is people dragging it sideways — "
        "racking is the load, M&T is the answer.",
        "Top: buttons or figure-8 fasteners, never glued "
        "down — a wide top MUST move across the grain.",
    ]
    hardware = ["Figure-8 fasteners (8)", "Wood glue "
                "(type II)", "Felt pads for the feet"]
    seq = ["MILL: top boards first, sticker overnight; "
           "legs and aprons after.",
           "CUT: top boards rough-long; legs final; "
           "aprons with tenon allowance on.",
           "JOIN: leg mortises, apron tenons, dry-fit "
           "the base; check diagonals before glue ever "
           "opens.",
           "ASSEMBLE: base first (end frames, then long "
           "aprons); glue the top up flat, scrape it "
           "flat, fasten it with movement room.",
           "SAND: base 150, top 220 — a tabletop is a "
           "show face.",
           "FINISH: per the schedule below."]
    design = (f"A {_fmt(L)} x {_fmt(W)} ft table, "
              f"{_fmt(Hi)} in tall, in "
              f"{plan.get('species', '').lower()} — "
              "plank top on a mortise-and-tenon apron base.")
    return parts, joinery, hardware, seq, list(
        _BASE_SAFETY), design


def _tpl_bookshelf(plan):
    # Stored dims: l = width, w = height, h = depth.
    L, W, H = _dims_ft(plan)
    Wi, Hi, Di = L * 12, W * 12, (H or 1.0) * 12
    shelves = max(2, int(Hi / 14))
    parts = [
        _part("Sides (uprights)", 2, "4/4",
              (1, Di + 0.5, Hi + 1), (0.75, Di, Hi),
              "grain runs VERTICAL (the height) — a shelf "
              "side with horizontal grain is a shelf "
              "waiting to snap", ""),
        _part("Shelves", shelves, "4/4",
              (1, Di + 0.5, Wi - 1.5 + 1),
              (0.75, Di - 0.25, Wi - 1.5),
              "grain runs along the SPAN (side to side) — "
              "the long grain carries the load", ""),
        _part("Top", 1, "4/4",
              (1, Di + 0.5, Wi + 1), (0.75, Di, Wi),
              "grain runs along the width", ""),
        _part("Kick / plinth", 1, "4/4",
              (1, 4, Wi - 1.5 + 1), (0.75, 3.5, Wi - 1.5),
              "grain runs the length", ""),
    ]
    joinery = [
        "Shelves into sides: DADOES (housed shelves). "
        "The dado shoulder carries the book load "
        "directly — a butt joint hangs it all on screws "
        "in end grain, which is a slow-motion failure.",
        "Back: a rabbet for the 1/4 in plywood back "
        "(sheet goods — in the hardware list, not the "
        "lumber math); the back is what keeps the whole "
        "case from racking into a parallelogram.",
        "Anchor the finished case to a wall stud — a "
        "loaded tall shelf is a tip-over hazard, full "
        "stop.",
    ]
    hardware = ["1/4 in plywood back, one sheet cut to "
                f"{_fmt_in(Wi)} x {_fmt_in(Hi)} in (sheet "
                "goods — sold by the sheet, not the board "
                "foot)",
                "Wood glue (type II)",
                "Anti-tip wall anchor strap (non-negotiable)"]
    seq = ["MILL: sides first (they set the height), "
           "then shelves.",
           "CUT: sides to final height; shelves to the "
           "span; dry-stack and confirm the shelf spacing "
           "against your tallest books.",
           "JOIN: rout the dadoes (stop them 3/4 in "
           "short of the front edge so they don't show), "
           "rabbet the back edges.",
           "ASSEMBLE: glue shelves into dadoes one bay at "
           "a time, square the case, pin the back on "
           "while it's square.",
           "SAND: 150 overall, 180 on the show faces.",
           "FINISH: per the schedule below — then anchor "
           "it to the wall before a single book goes in."]
    safety = ["Rout dadoes with the workpiece clamped "
              "and the bit guarded — a climbing router "
              "bit writes its own rules."] + _BASE_SAFETY
    design = (f"A {_fmt(L)} ft wide x {_fmt(W)} ft tall "
              f"bookshelf, {_fmt(Di)} in deep, in "
              f"{plan.get('species', '').lower()} — housed-"
              "dado shelves, plywood back, wall-anchored.")
    return parts, joinery, hardware, seq, safety, design


def _tpl_cutting_board(plan):
    L, W, _H = _dims_ft(plan)
    Li, Wi = max(L, W) * 12, min(L, W) * 12
    n = max(4, _ceil(Wi / 2.0))
    parts = [
        _part("Board strips", n, "8/4",
              (2, 2, Li + 1), (1.75, Wi / n, Li),
              "grain runs the length; alternate the ring "
              "direction strip to strip so the board "
              "stays flat",
              "edge-glued; mixing a walnut strip or two "
              "into maple is the classic look"),
    ]
    joinery = [
        "Long-grain glue-up only — no joinery, no screws. "
        "Waterproof (type III) glue: this board will meet "
        "water daily.",
        "End grain up is the butcher-block upgrade (easier "
        "on knives, thirstier for oil) — ask and I'll "
        "re-cut the list for it.",
    ]
    hardware = ["Wood glue (type III, waterproof — the "
                "only honest choice here)",
                "Rubber or wooden feet (optional; keeps the "
                "board out of counter water)"]
    seq = ["MILL: rip the 8/4 stock to 2 in strips, "
           "joint one face and edge.",
           "CUT: strips to rough length (+1 in).",
           "JOIN: arrange for color + ring direction, "
           "glue-up in cauls so it stays flat.",
           "ASSEMBLE: unclamp after 24 hours, scrape the "
           "glue lines, cut to final size, rout a juice "
           "groove if you want one, ease every edge.",
           "SAND: to 220, 320 on the faces.",
           "FINISH: food-safe only — the mineral-oil "
           "schedule below. NEVER poly, varnish, or "
           "vegetable oils (they go rancid)."]
    safety = ["A cutting board's finish is a food "
              "surface — if it isn't food-grade, it "
              "doesn't touch the board."] + _BASE_SAFETY
    design = (f"An edge-grain cutting board, "
              f"{_fmt_in(Li)} x {_fmt_in(Wi)} in finished, "
              f"~1 3/4 in thick, in "
              f"{plan.get('species', '').lower()} — glued "
              "strips, food-safe oil finish.")
    return parts, joinery, hardware, seq, safety, design


def _tpl_stool(plan):
    L, _W, H = _dims_ft(plan)
    Si, Hi = L * 12, (H or 1.5) * 12
    parts = [
        _part("Seat blank", 1, "8/4",
              (2, Si + 1, Si + 1), (1.5, Si, Si),
              "grain runs front-to-back; pick the "
              "prettiest face for the top",
              "glue two boards if the stock runs narrow"),
        _part("Legs", 4, "8/4",
              (2, 2, Hi - 1.5 + 2), (1.5, 1.5, Hi - 1.5),
              "straight grain, vertical — a stool leg is "
              "a lever people trust", ""),
        _part("Stretchers", 4, "4/4",
              (1, 2.5, Si - 3 + 2), (0.75, 2, Si - 3),
              "grain runs the length (the span)",
              "+2 in of rough length is the tenons"),
    ]
    joinery = [
        "Legs to seat: WEDGED THROUGH-TENONS if you want "
        "it to outlive you; blind mortise and tenon if "
        "you don't want the wedges showing. A stool takes "
        "its whole load through these four joints plus "
        "sideways abuse — this is not a pocket-screw job.",
        "Stretchers: short tenons into the legs — they "
        "stop the legs splaying under load.",
    ]
    hardware = ["Wood glue (type II)", "Wedges for the "
                "through-tenons (a contrasting species "
                "is the classic move)"]
    seq = ["MILL: seat blank first, then legs.",
           "CUT: seat to final (shape it after glue-up), "
           "legs with tenon stock on, stretchers with "
           "allowance.",
           "JOIN: mortise the seat (angled if you're "
           "splaying the legs — compound angles, go "
           "slow), fit the tenons.",
           "ASSEMBLE: dry-fit everything, then glue legs "
           "in pairs, then the stretchers; level the "
           "feet LAST by scribing to the floor.",
           "SAND: 150, ease every edge a hand or a shin "
           "can meet.",
           "FINISH: per the schedule below."]
    design = (f"A stool with a {_fmt_in(Si)} in seat at "
              f"{_fmt(Hi)} in tall, in "
              f"{plan.get('species', '').lower()} — "
              "wedged-tenon legs with side stretchers.")
    return parts, joinery, hardware, seq, list(
        _BASE_SAFETY), design


def _tpl_bench(plan):
    L, W, H = _dims_ft(plan)
    Li, Wi, Hi = L * 12, W * 12, (H or 1.5) * 12
    n_top = max(2, _ceil(Wi / 5.5))
    parts = [
        _part("Seat boards", n_top, "8/4",
              (2, 5.5, Li + 2), (1.75, 5.25, Li),
              "grain runs the length", ""),
        _part("Legs", 4, "8/4",
              (2.5, 2.5, Hi - 1.75 + 1),
              (2.25, 2.25, Hi - 1.75),
              "straight grain, vertical", ""),
        _part("Long apron", 1, "4/4",
              (1, 4.5, Li - 4.5 + 4), (0.75, 4.25, Li - 4.5),
              "grain runs the length (the span)",
              "+4 in of rough length is the tenons"),
        _part("Stretchers", 2, "4/4",
              (1, 3, Wi + 6), (0.75, 2.5, Wi),
              "grain runs the length", ""),
    ]
    joinery = [
        "Apron to legs: MORTISE AND TENON — a bench is a "
        "table people sit on; racking loads double.",
        "Seat boards: buttons or slotted screws — the "
        "seat moves across its width.",
    ]
    hardware = ["Figure-8 fasteners or buttons (6)",
                "Wood glue (type II)"]
    seq = ["MILL: seat boards and legs first.",
           "CUT: seat boards rough-long; legs final; "
           "apron with allowance.",
           "JOIN: leg mortises, apron tenons, dry-fit.",
           "ASSEMBLE: base square first, then the seat "
           "fastened with movement room.",
           "SAND: 150, ease the seat edges well — people "
           "slide onto a bench.",
           "FINISH: per the schedule below."]
    design = (f"A {_fmt(L)} ft bench, {_fmt(Hi)} in tall, "
              f"in {plan.get('species', '').lower()} — "
              "plank seat on a mortise-and-tenon base.")
    return parts, joinery, hardware, seq, list(
        _BASE_SAFETY), design


def _tpl_cabinet(plan):
    # Stored dims: l = width, w = height, h = depth.
    L, W, H = _dims_ft(plan)
    Wi, Hi, Di = L * 12, W * 12, (H or 2.0) * 12
    door_w = Wi / 2 - 1
    parts = [
        _part("Face-frame stiles", 2, "4/4",
              (1, 2, Hi + 1), (0.75, 1.75, Hi),
              "grain runs VERTICAL (the height)", ""),
        _part("Face-frame rails", 3, "4/4",
              (1, 2, Wi - 3.5 + 1), (0.75, 1.75, Wi - 3.5),
              "grain runs the length (the span)", ""),
        _part("Door stiles", 4, "4/4",
              (1, 2.25, Hi - 10 + 1), (0.75, 2, Hi - 10),
              "grain runs VERTICAL", "two doors"),
        _part("Door rails", 4, "4/4",
              (1, 2.25, door_w - 4 + 1),
              (0.75, 2, door_w - 4),
              "grain runs the length", "two doors"),
        _part("Door panels", 2, "4/4",
              (1, door_w - 2, Hi - 14),
              (0.5, door_w - 2.5, Hi - 14.5),
              "grain runs VERTICAL; the panel FLOATS in "
              "its grooves — never glue it in",
              "resawn or planed to 1/2 in; the float is "
              "what keeps the door from splitting"),
    ]
    joinery = [
        "Carcass: plywood box (sheet goods below), "
        "assembled with dadoes + screws — hidden joinery "
        "carries a cabinet fine.",
        "Face frame: pocket screws or mortise and tenon; "
        "it's the visible face, so flat and square "
        "matter more than brute strength.",
        "Doors: frame-and-panel — mortise and tenon (or "
        "cope-and-stick) frames with a FLOATING panel. "
        "The panel moves with the seasons; trapped, it "
        "splits the door or bows it.",
    ]
    hardware = ["3/4 in plywood for the carcass, ~1 "
                "sheet (sheet goods — sold by the sheet, "
                "not the board foot)",
                "1/4 in plywood back, ~1/2 sheet",
                "Soft-close hinges (2 per door)",
                "Knobs or pulls (2)", "Wood glue (type II)"]
    seq = ["MILL: face-frame and door stock first — the "
           "carcass is sheet goods and waits.",
           "CUT: plywood carcass parts on the panel saw "
           "or with a track guide; solid stock to the "
           "cut list.",
           "JOIN: frame-and-panel doors (build these "
           "EARLY — they take the longest to get right), "
           "face frame second.",
           "ASSEMBLE: carcass square, face frame on, "
           "doors hung and gapped even (a nickel's "
           "thickness all round is the pro gap).",
           "SAND: 150 carcass, 180 doors and frame.",
           "FINISH: per the schedule below — finish "
           "doors off the case, lying flat."]
    design = (f"A {_fmt(L)} ft wide x {_fmt(W)} ft tall "
              f"cabinet, {_fmt(Di)} in deep — plywood "
              f"carcass, solid "
              f"{plan.get('species', '').lower()} face "
              "frame and frame-and-panel doors.")
    return parts, joinery, hardware, seq, list(
        _BASE_SAFETY), design


def _tpl_generic(plan):
    L, W, H = _dims_ft(plan)
    Li, Wi = L * 12, W * 12
    parts = [
        _part("Frame stock (legs/uprights)", 4, "8/4",
              (2, 2, (H or 2.0) * 12 + 1),
              (1.75, 1.75, (H or 2.0) * 12),
              "straight grain, vertical", ""),
        _part("Rails/aprons", 4, "4/4",
              (1, 4, Li - 3.5 + 4), (0.75, 3.5, Li - 3.5),
              "grain runs the length (the span)",
              "+4 in of rough length is tenon allowance"),
        _part("Top/panel stock", max(2, _ceil(Wi / 6.0)),
              "4/4", (1, 6, Li + 2), (0.8, 5.75, Li),
              "grain runs the length", ""),
    ]
    joinery = [
        "Default pro pattern for a piece outside my "
        "standard templates: mortise and tenon anywhere "
        "the piece can rack, dadoes anywhere a horizontal "
        "part carries load, and every wide panel left "
        "free to move. Tell me the piece in standard "
        "terms (table, stool, cabinet...) and I'll give "
        "it its own exact list.",
    ]
    hardware = ["Wood glue (type II)",
                "Fasteners per the joinery once the piece "
                "is pinned down"]
    seq = ["MILL: frame stock first, panel stock second.",
           "CUT: rough lengths with joinery allowance on.",
           "JOIN: dry-fit before glue — always.",
           "ASSEMBLE: square, clamped, diagonals checked.",
           "SAND: 150, show faces 180.",
           "FINISH: per the schedule below."]
    topic = plan.get("topic") or "wood piece"
    design = (f"A {topic} at about {_fmt(L)} x "
              f"{_fmt(W)} ft in "
              f"{plan.get('species', '').lower()} — a "
              "frame-and-panel build on the standard pro "
              "pattern (this piece is outside my named "
              "templates, so treat the list as the "
              "framework it is).")
    return parts, joinery, hardware, seq, list(
        _BASE_SAFETY), design


_TEMPLATES = {"workbench": _tpl_workbench,
              "table": _tpl_table,
              "bookshelf": _tpl_bookshelf,
              "cutting board": _tpl_cutting_board,
              "stool": _tpl_stool, "bench": _tpl_bench,
              "cabinet": _tpl_cabinet}


_TOOLS_BASE = ["Tape measure + marking knife (a knife "
               "line beats a pencil line at the saw)",
               "Jointer + planer (or a mill that will "
               "surface stock for you)",
               "Table saw", "Miter saw", "Drill/driver",
               "Router (dadoes, edge profiles, grooves)",
               "Chisels + mallet (the joinery kit)",
               "Clamps — more than you think (bar/pipe "
               "clamps for glue-ups, F-clamps for "
               "assembly)",
               "Random-orbit sander", "Combination square",
               "Card scraper (a sharp scraper out-finishes "
               "sandpaper on show faces)"]

_TOOLS_EXTRA = {
    "workbench": ["Mallet + holdfasts if you run dog "
                  "holes"],
    "cutting board": ["Cauls for a flat glue-up",
                      "A router sled if the glue-up "
                      "needs flattening"],
    "cabinet": ["Pocket-hole jig (optional, face frame)",
                "Hinge boring jig or a careful Forstner "
                "bit"],
    "bookshelf": ["A dado stack or a router jig for the "
                  "housed shelves"],
}


def _tools_for(kind: str) -> List[str]:
    return list(_TOOLS_BASE) + list(_TOOLS_EXTRA.get(kind, []))


# --- The render (all math recomputed from the stored plan) ---------------------

def _render(plan: Dict) -> Dict:
    kind = plan.get("kind", "generic")
    fn = _TEMPLATES.get(kind, _tpl_generic)
    parts1, joinery, hardware, seq, safety, design = fn(plan)
    qty = max(1, int(plan.get("qty", 1) or 1))
    species = plan.get("species") or "Hard maple"
    parts = []
    for p in parts1:
        q = dict(p)
        q["qty_total"] = p["qty"] * qty
        q["bf_total"] = p["bf_each"] * p["qty"] * qty
        parts.append(q)
    bf_total = sum(p["bf_total"] for p in parts)
    waste = WASTE_BOARD if kind == "cutting board" else WASTE
    bf_buy = bf_total * waste
    # Board count per stock group: rough length the group
    # eats, + 1/8 in of kerf per part cut, in 8 ft boards.
    groups: Dict[str, Dict] = {}
    for p in parts:
        g = groups.setdefault(
            p["stock"], {"bf": 0.0, "lin_in": 0.0, "cuts": 0})
        g["bf"] += p["bf_total"]
        g["lin_in"] += p["rough"][2] * p["qty_total"]
        g["cuts"] += p["qty_total"]
    purchase = []
    for stock in sorted(groups):
        g = groups[stock]
        need_in = g["lin_in"] + g["cuts"] * KERF_IN
        boards = max(1, _ceil(need_in / 96.0))
        purchase.append({"stock": stock, "bf": g["bf"],
                         "lin_in": g["lin_in"],
                         "cuts": g["cuts"], "boards": boards})
    rate = _species_rate(species)
    lumber_lo = bf_buy * rate * 0.85
    lumber_hi = bf_buy * rate * 1.15
    hw = {"workbench": (25, 60), "table": (15, 40),
          "bookshelf": (20, 45), "cutting board": (5, 15),
          "stool": (5, 15), "bench": (10, 30),
          "cabinet": (60, 140), "generic": (15, 50)}.get(
              kind, (15, 50))
    fin_key = plan.get("finish") or _KIND_DEFAULT_FINISH.get(
        kind, "danish oil")
    fin_label, fin_steps = _FINISH_SCHEDULES.get(
        fin_key, _FINISH_SCHEDULES["danish oil"])
    return {"design": design, "parts": parts,
            "joinery": list(joinery), "hardware": hardware,
            "tools": _tools_for(kind), "steps": seq,
            "safety": safety, "species": species,
            "species_note": _species_note(species),
            "finish_label": fin_label, "finish_steps": fin_steps,
            "bf_total": round(bf_total, 2),
            "bf_per_piece": round(bf_total / qty, 2),
            "waste": waste, "bf_buy": round(bf_buy, 1),
            "purchase": purchase,
            "cost_low": _money(lumber_lo + hw[0] + 15),
            "cost_high": _money(lumber_hi + hw[1] + 35),
            "qty": qty}


def _plan_text(plan: Dict) -> str:
    r = _render(plan)
    qty = r["qty"]
    lines = [f"WOOD PLAN — {plan.get('title', '')}",
             "",
             f"Design: {r['design']}",
             "",
             f"Species: {r['species']} — {r['species_note']}",
             ""]
    if plan.get("joinery_pref"):
        lines.append(
            f"Joinery (your pick): {plan['joinery_pref']} "
            "— see the note in the joinery section.")
        lines.append("")
    lines.append("Joinery (chosen by load):")
    for j in r["joinery"]:
        lines.append(f"  • {j}")
    if plan.get("joinery_pref"):
        weak = plan["joinery_pref"] in _WEAK_FOR_LOAD \
            and plan.get("kind") in ("workbench", "table",
                                     "stool", "bench")
        if weak:
            lines.append(
                f"  • HONEST NOTE on your pick: "
                f"{plan['joinery_pref']} racks under this "
                "piece's loads — it will loosen years "
                "before mortise and tenon would. Your "
                "call stands in the plan; the pro call "
                "stays on the record.")
        else:
            lines.append(
                "  • Your pick works for this piece — "
                "the cut list's joinery allowance already "
                "carries it.")
    lines.append("")
    lines.append(
        "Cut list (finished size is what the part ENDS "
        "UP; buy/cut from the rough size — the difference "
        "is your milling and joinery allowance):")
    for p in r["parts"]:
        ft_, fw, fl = p["fin"]
        rt, rw, rl = p["rough"]
        line = (f"  • {p['part']} x{p['qty_total']} — "
                f"{r['species']}, {p['stock']} stock — "
                f"finished {_fmt_in(ft_)} x {_fmt_in(fw)} x "
                f"{_fmt_in(fl)} in — rough {rt:g} x {rw:g} x "
                f"{rl:g} in — grain: {p['grain']} — "
                f"{p['bf_each']:.2f} BF each "
                f"({p['bf_each']:.2f} = "
                f"{rt:g}x{rw:g}x{rl:g}/144)")
        if p["note"]:
            line += f" — {p['note']}"
        lines.append(line)
    lines.append("")
    lines.append("Board-foot math (shown, the way the "
                 "yard figures it):")
    lines.append("  board feet = (rough thickness in x "
                 "rough width in x rough length in) / 144")
    for g in r["purchase"]:
        lines.append(
            f"  • {g['stock']} stock: {g['bf']:.1f} BF "
            f"across {g['cuts']} parts")
    if qty > 1:
        lines.append(
            f"  • Per piece: {r['bf_per_piece']:.1f} BF x "
            f"{qty} pieces = {r['bf_total']:.1f} BF")
    lines.append(
        f"  • Total: {r['bf_total']:.1f} BF, plus "
        f"{round((r['waste'] - 1) * 100)}% waste = buy "
        f"{r['bf_buy']:.1f} BF")
    lines.append(
        f"  • Kerf: every cut eats {KERF_IN:g} in of "
        "blade — counted below in the board count.")
    lines.append("Lumber to buy (8 ft boards):")
    for g in r["purchase"]:
        lines.append(
            f"  • {g['stock']}: {g['boards']} board(s) — "
            f"{g['lin_in']:.0f} in of rough length + "
            f"{g['cuts']} cuts x {KERF_IN:g} in kerf")
    lines.append("")
    lines.append("Hardware + consumables:")
    for h in r["hardware"]:
        lines.append(f"  • {h}")
    lines.append("")
    lines.append(f"Finish schedule — {r['finish_label']}:")
    for s in r["finish_steps"]:
        lines.append(f"  • {s}")
    lines.append("")
    lines.append("Tools:")
    for t in r["tools"]:
        lines.append(f"  • {t}")
    lines.append("")
    lines.append("Build sequence (shop order — mill, "
                 "cut, join, assemble, sand, finish):")
    for i, s in enumerate(r["steps"], 1):
        lines.append(f"  {i}. {s}")
    if qty > 1:
        lines.append("")
        lines.append(
            f"Batch order for {qty} pieces: mill ALL the "
            "stock for the whole run first, cut every "
            "part for every piece before the first joint "
            "is cut (one setup per operation, not per "
            "piece), join and assemble piece by piece, "
            "then sand and finish the run together.")
    lines.append("")
    lines.append(
        f"Rough cost: ${r['cost_low']:,}–${r['cost_high']:,}"
        f" — ESTIMATE, not a quote (lumber at ~"
        f"${_species_rate(r['species']):g}/BF planning "
        "rate for the buy-amount, plus hardware + finish "
        "allowances; real yards price real boards, and "
        "the board you pick off the rack is the price "
        "that counts).")
    lines.append("")
    lines.append("Pro safety callouts:")
    for s in r["safety"]:
        lines.append(f"  • {s}")
    return "\n".join(lines)


def _plan_intro(plan: Dict) -> str:
    return (_plan_text(plan)
            + "\n\n(The plan above is the Wood Project "
              "Builder Pro's — relay it in full, in "
              "persona, without dropping the board-foot "
              "math, the kerf, or the food-safety and "
              "finish-rag lines where they ride. The "
              "user can adjust it in chat — dimensions, "
              "species, joinery, finish, quantity — and "
              "say \"save the plan\" to hand it to the "
              "Project Manager as a project with the cut "
              "list filed under it.)")


# --- Parsing (dimensions ride og_builder's parser — one truth) ------------------

_KIND_WORDS = (
    ("cutting board", ("cutting board", "chopping board",
                       "butcher block", "cutting boards")),
    ("workbench", ("workbench", "work bench",
                   "workbenches")),
    ("bookshelf", ("bookshelf", "bookcase", "book shelf",
                   "bookshelves")),
    ("cabinet", ("cabinet", "dresser", "nightstand",
                 "chest of drawers", "cabinets")),
    ("table", ("coffee table", "dining table", "end table",
               "side table", "table", "desk")),
    ("stool", ("stool", "stools")),
    ("bench", ("bench", "benches")),
    ("shelf", ("shelf", "shelves", "shelving")),
)

_DEFAULT_DIMS = {
    "workbench": (6.0, 2.5, 3.0), "table": (6.0, 3.0, 2.5),
    "bookshelf": (3.0, 6.0, 1.0),
    "cutting board": (1.5, 1.0, None),
    "stool": (1.33, 1.33, 1.5), "bench": (4.0, 1.25, 1.5),
    "cabinet": (3.0, 2.875, 2.0), "generic": (4.0, 2.0, 2.0),
}

# Plausible foot-ranges per kind; a dimension past the
# max was almost surely spoken in inches ("an 18x12
# cutting board"), so it converts instead of planning a
# cutting board the size of a deck.
_KIND_MAX_FT = {
    "workbench": (12, 6, 5), "table": (12, 7, 5),
    "bookshelf": (8, 10, 3), "cutting board": (3, 3, 1),
    "stool": (3, 3, 3), "bench": (10, 4, 4),
    "cabinet": (8, 9, 4), "generic": (12, 8, 8),
}


def _find_piece(text: str) -> Optional[str]:
    low = " " + re.sub(r"[^a-z0-9 ]", " ",
                       str(text or "").lower()) + " "
    for kind, words in _KIND_WORDS:
        for w in words:
            if f" {w} " in low or f" {w}s " in low:
                return "bookshelf" if kind == "shelf" else kind
    return None


def _fit_dims(kind: str, dims):
    """Dims in feet, sanity-fit per kind (see _KIND_MAX_FT)."""
    if not dims:
        return None
    mx = _KIND_MAX_FT.get(kind, (12, 8, 8))
    out = []
    for i, v in enumerate(dims):
        if v is None:
            out.append(None)
            continue
        out.append(round(v / 12.0, 2) if v > mx[i] else v)
    return tuple(out)


_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4,
              "five": 5, "six": 6, "seven": 7, "eight": 8,
              "nine": 9, "ten": 10, "eleven": 11,
              "twelve": 12}


def _find_qty(text: str, kind: Optional[str]) -> int:
    low = str(text or "").lower()
    m = re.search(r"\b(\d{1,2})\s+(?:of\s+)?(?:the\s+)?[a-z ]*?"
                  r"(?:stools?|tables?|benches|boards?|"
                  r"workbench|workbenches|shelves|bookcases|"
                  r"cabinets?)\b", low)
    if m:
        return max(1, min(50, int(m.group(1))))
    for word, n in _NUM_WORDS.items():
        if re.search(rf"\b{word}\s+(?:stools?|tables?|"
                     r"benches|boards?|workbenches|"
                     r"shelves|bookcases|cabinets?)\b", low):
            return n
    return 1


def _title_for(kind: str, species: str, qty: int,
               topic: str) -> str:
    label = {"cutting board": "cutting board",
             "generic": topic or "wood piece"}.get(
                 kind, kind)
    base = f"{species} {label}"
    if qty > 1:
        base = f"{qty} x {base}"
    return base


# --- Chat claims (narrow frames — the crew/PM/filer doctrine) ---------------------

_GARBLE_VOCAB = ("wood", "cut", "list", "board", "feet",
                 "walnut", "oak", "maple", "joinery",
                 "finish", "lumber", "workbench", "table",
                 "stool")

_CUTLIST_RE = re.compile(r"\bcut\s*list\b", re.I)
_BF_RE = re.compile(r"\bboard\s*f(?:ee|oo)t\b", re.I)
_JOINERY_RE = re.compile(
    r"\bjoinery\b|\bwhat\s+(?:joint|joinery)\b"
    r"|\bwhich\s+(?:joint|joinery)\b"
    r"|\bhow\s+(?:do|should)\s+i\s+join\b", re.I)
_FINISH_RE = re.compile(
    r"\bfinish(?:ing)?\s+schedule\b"
    r"|\bhow\s+(?:do|should)\s+i\s+finish\b"
    r"|\bwhat\s+finish\b", re.I)
_SPECIES_RE = re.compile(
    r"\bwhat\s+(?:wood|species)\b"
    r"|\bwhich\s+(?:wood|species)\b"
    r"|\bbest\s+(?:wood|species)\b", re.I)
_CREATE_RES = (
    re.compile(r"\bbuild plan\b\s*(?:for\s+)?(.+)?$", re.I),
    re.compile(r"\b(?:help me|i want to|i'd like to|"
               r"i wanna|i need|how (?:do|would) i|"
               r"how to|let's|lets)\s+(?:build|make|design)"
               r"\s+(?:a\s+|an\s+|my\s+|the\s+)?(.+)$", re.I),
    re.compile(r"\b(?:build|make|design|plan)\s+"
               r"(?:me\s+)?(?:a\s+|an\s+|my\s+|the\s+)?(.+)$",
               re.I),
)
_SHOW_RE = re.compile(
    r"^(?:show(?:\s+me)?|pull up|see|what'?s|what is)\s+"
    r"(?:the\s+|my\s+)?(?:wood\s+)?(?:plan|cut\s*list)\b"
    r"|^the\s+(?:wood\s+)?(?:plan|cut\s*list)$"
    r"|^cut\s*list$", re.I)
_COST_RE = re.compile(
    r"^what(?:'s| would| will)\s+(?:it|this|that)\s+cost\b"
    r"|^how much\s+(?:would|will|is)\s+(?:it|this|that)\b"
    r"|^what'?s\s+the\s+(?:cost|price|damage)\b", re.I)
_HANDOFF_RE = re.compile(
    r"^(?:save|file|keep)\s+(?:this|the|my|that)\s+"
    r"(?:plan|cut\s*list)\b"
    r"|^(?:make|turn)\s+(?:this|it|that|the plan)\s+"
    r"(?:in)?to\s+a\s+project\b"
    r"|^start\s+(?:a\s+)?project\s+(?:from|with)\s+"
    r"(?:this|the|that)\b"
    r"|^(?:hand|pass)\s+(?:it|this|the plan)\s+off\b"
    r"|^plan'?s\s+(?:done|ready|good|final)\b", re.I)
_CLOSE_RE = re.compile(
    r"^(?:scrap|cancel|forget|drop|kill|toss)\s+"
    r"(?:the\s+|my\s+|that\s+|this\s+)?(?:wood\s+)?"
    r"(?:plan|cut\s*list)\b", re.I)
_ORDER_RE = re.compile(
    r"\b(?:order|buy|purchase|get)\b[^.?!]*\b(?:lumber|"
    r"wood|boards?|materials?|supplies|everything|"
    r"it all|the list|stuff)\b"
    r"|^(?:order|buy)\s+(?:it|that|this)\s+all\b", re.I)
_ADJ_RES = (
    re.compile(r"^(?:make|change|resize|set)\s+it\s+(.+)$", re.I),
    re.compile(r"^(?:use|go with|switch to)\s+(.+)$", re.I),
    re.compile(r"\b(.+?)\s+instead\b", re.I),
    re.compile(r"^make\s+it\s+(bigger|smaller|longer|"
               r"shorter|wider|narrower|taller|\d{1,2})\b",
               re.I),
)


def _defer_to_others(message: str, uid: str) -> bool:
    """The PM, the Filer and the crew own their asks —
    checked exactly, first (the Builder's doctrine). If
    any of them claims this message, the Pro never does.
    (The Builder is NOT in this list on purpose: the Pro
    wraps outside it and takes the wood-flavored asks;
    everything else falls through to the Builder's own
    claim when this module returns no claim.)"""
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
            # A create frame claims ONLY when a wood
            # piece is named — "build a deck" stays the
            # Builder's, "build me a website" is nobody's
            # here.
            if _find_piece(payload) or _find_piece(raw):
                return payload
            return None
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
            return {"op": "wood_close"}
        if _HANDOFF_RE.search(raw):
            return {"op": "wood_handoff"}
        if _ORDER_RE.search(raw):
            return {"op": "wood_order"}
        if _COST_RE.search(raw):
            return {"op": "wood_show", "focus": "cost"}
        if _CUTLIST_RE.search(raw) and not _find_piece(raw):
            return {"op": "wood_show", "focus": "cutlist"}
        created = _create_payload_hit(raw)
        if created is not None:
            return {"op": "wood_create", "payload": created,
                    "raw": raw}
        # A verb-led NEW-build ask the Pro doesn't take
        # ("build a 12x16 deck") is never a resize of the
        # open draft — it falls through to the Builder.
        # ("Make it 5x2" is the adjustment idiom, exempt.)
        # The structure-noun half of the guard also covers
        # the garble path, where the verb itself may come
        # through mangled past recognition.
        if re.search(r"\b(?:deck|fence|fencing|shed|"
                     r"pergola|gazebo|stairs|staircase|"
                     r"coop|doghouse|dog house|raised bed|"
                     r"patio|porch)\b", raw, re.I):
            return None
        if re.search(r"\b(?:build|make|design|plan)\s+"
                     r"(?:me\s+)?(?:a\s+|an\s+|my\s+|the\s+)?"
                     r"[a-z]", raw, re.I) and not raw.lower(
                         ).startswith("make it"):
            return None
        if og_builder._parse_dims(raw) and len(raw) < 40:
            return {"op": "wood_adjust", "payload": raw}
        # A change that names its target — a species, a
        # finish, a joint — with a change word claims as
        # an adjustment even outside the verb frames
        # ("finish it with poly", "use dowels").
        if (_find_species(raw) or _find_finish(raw)
                or _find_joinery(raw)) and re.search(
                    r"\b(use|switch|instead|go with|finish|"
                    r"join|make)\b", raw, re.I):
            return {"op": "wood_adjust", "payload": raw}
        for rx in _ADJ_RES:
            if rx.search(raw):
                return {"op": "wood_adjust", "payload": raw}
        if _SHOW_RE.search(raw):
            return {"op": "wood_show"}
    # No open draft (or an ask the draft frames didn't
    # take): the specialist frames.
    if _CUTLIST_RE.search(raw) and _find_piece(raw):
        return {"op": "wood_create",
                "payload": _create_payload_hit(raw) or raw,
                "raw": raw, "focus": "cutlist"}
    if _BF_RE.search(raw) and (plan or _find_piece(raw)
                               or og_builder._parse_dims(raw)):
        if plan and not _find_piece(raw):
            return {"op": "wood_show", "focus": "cost"}
        return {"op": "wood_create",
                "payload": _create_payload_hit(raw) or raw,
                "raw": raw, "focus": "bf"}
    if (_JOINERY_RE.search(raw) or _FINISH_RE.search(raw)
            or _SPECIES_RE.search(raw)) and _find_piece(raw):
        return {"op": "wood_create",
                "payload": _create_payload_hit(raw) or raw,
                "raw": raw}
    payload = _create_payload_hit(raw)
    if payload is not None:
        return {"op": "wood_create", "payload": payload,
                "raw": raw}
    if _SHOW_RE.search(raw):
        return {"op": "wood_show"}
    if _BF_RE.search(raw) and not plan:
        return {"op": "wood_bf_none"}
    if _HANDOFF_RE.search(raw) and any(
            p.get("status") == "handed_off"
            for p in _plans(uid) if isinstance(p, dict)):
        return {"op": "wood_handoff"}
    return None


def _claim_woodpro(message: str, uid: str):
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


_TAG = "OG WOOD PROJECT BUILDER PRO"


def _do_create(uid: str, claim: Dict) -> List[Dict]:
    payload = claim.get("payload") or ""
    raw = claim.get("raw") or payload
    kind = _find_piece(raw) or _find_piece(payload) \
        or "generic"
    dims_hit = _fit_dims(
        kind, og_builder._parse_dims(raw)
        or og_builder._parse_dims(payload))
    dl, dw, dh = _DEFAULT_DIMS.get(kind, (4.0, 2.0, 2.0))
    if dims_hit:
        dl, dw = dims_hit[0], dims_hit[1]
        dh = dims_hit[2] if dims_hit[2] else dh
    label, _rate, _note = _find_species(raw)
    if not label:
        label = _KIND_DEFAULT_SPECIES.get(kind, "Red oak")
    fin = _find_finish(raw) or _KIND_DEFAULT_FINISH.get(
        kind, "danish oil")
    topic = ""
    if kind == "generic":
        topic = re.sub(r"\s+", " ",
                       payload or raw).strip(" .!?")[:60]
    plan = {"id": uuid.uuid4().hex[:12], "uid": uid,
            "kind": kind, "topic": topic,
            "dims": {"l": dl, "w": dw, "h": dh},
            "species": label, "finish": fin,
            "joinery_pref": _find_joinery(raw),
            "qty": _find_qty(raw, kind),
            "status": "draft", "project_id": "",
            "doc_id": "",
            "created": time.time(), "updated": time.time()}
    plan["title"] = _title_for(kind, label, plan["qty"], topic)
    with _woodpro_lock:
        plans = _plans(uid)
        plans.append(plan)
        _save_plans(uid, plans)
    body = _plan_intro(plan)
    if claim.get("focus") == "cutlist":
        body = ("Cut list first, the way the shop reads "
                "it — the full plan follows.\n\n" + body)
    return _result(_TAG, f"Wood Project Builder Pro — "
                   f"plan: {plan['title']}", body)


def _do_show(uid: str, claim: Dict) -> List[Dict]:
    plan = _active_plan(uid)
    if not plan:
        handed = [p for p in plans_for(uid)
                  if p.get("status") == "handed_off"]
        if handed:
            return _result(
                _TAG, "Wood Project Builder Pro — plans",
                "Your newest wood plan is already handed "
                f"off (\"{handed[0].get('title', '')}\" — "
                "the Project Manager has it). No open "
                "draft right now — say \"cut list for a "
                "workbench\" and I'll draw a fresh one.")
        return _result(
            _TAG, "Wood Project Builder Pro — no plan yet",
            "No wood plan on the bench yet. Say \"cut "
            "list for a workbench\" (or \"build a walnut "
            "table\", \"how many board feet for a "
            "bookshelf\") and I'll spec the piece — cut "
            "list, joinery, board-foot math shown, finish "
            "schedule.")
    body = _plan_text(plan)
    if claim.get("focus") == "cost":
        r = _render(plan)
        body = (f"On the current plan ({plan['title']}): "
                f"{r['bf_total']:.1f} BF of lumber in the "
                f"parts, {r['bf_buy']:.1f} BF to buy with "
                f"the waste factor; rough cost "
                f"${r['cost_low']:,}–${r['cost_high']:,}"
                " — ESTIMATE, not a quote.\n\n" + body)
    return _result(_TAG, f"Wood Project Builder Pro — "
                   f"plan: {plan['title']}", body)


def _do_adjust(uid: str, claim: Dict) -> List[Dict]:
    plan = _active_plan(uid)
    if not plan:
        return _do_show(uid, claim)
    payload = claim.get("payload") or ""
    low = " " + payload.lower() + " "
    dims = dict(plan.get("dims") or {})
    changes = []
    hit = _fit_dims(plan.get("kind", "generic"),
                    og_builder._parse_dims(payload))
    if hit:
        dims["l"], dims["w"] = hit[0], hit[1]
        if hit[2]:
            dims["h"] = hit[2]
        changes.append(
            f"size is now {_fmt(float(dims.get('l', 0) or 0))}"
            f" x {_fmt(float(dims.get('w', 0) or 0))} ft")
    label, _rate, _note = _find_species(payload)
    if label and ("instead" in low or "use " in low
                  or "go with" in low or "switch" in low
                  or "make it" in low
                  or payload.lower().strip()
                  == label.lower()):
        if label != plan.get("species"):
            plan["species"] = label
            changes.append(f"species is now {label.lower()}")
    fin = _find_finish(payload)
    if fin and ("finish" in low or "instead" in low
                or "use " in low or "go with" in low):
        plan["finish"] = fin
        changes.append(
            f"finish is now "
            f"{_FINISH_SCHEDULES[fin][0].lower()}")
    join = _find_joinery(payload)
    if join and ("join" in low or "use " in low
                 or "instead" in low or "go with" in low
                 or "switch" in low):
        plan["joinery_pref"] = join
        changes.append(f"joinery is your pick: {join}")
    qm = re.search(r"\bmake\s+(?:it\s+)?(\d{1,2})\b",
                   payload, re.I)
    new_qty = None
    if qm:
        new_qty = max(1, min(50, int(qm.group(1))))
    else:
        q2 = _find_qty(payload, plan.get("kind"))
        if q2 != 1 and (
                _find_piece(payload) or "pieces" in low):
            new_qty = q2
    if new_qty and new_qty != int(plan.get("qty", 1) or 1):
        plan["qty"] = new_qty
        changes.append(f"quantity is now {new_qty}")
    scale_words = {"bigger": ("l", 1.25), "longer": ("l", 1.25),
                   "smaller": ("l", 0.8), "shorter": ("l", 0.8),
                   "wider": ("w", 1.25), "narrower": ("w", 0.8),
                   "taller": ("h", 1.25)}
    if not changes:
        for word, (axis, f) in scale_words.items():
            if word in low and dims.get(axis):
                dims[axis] = round(float(dims[axis]) * f, 2)
                changes.append(f"{word} — the plan re-cut")
                break
    if not changes:
        return _result(
            _TAG, "Wood Project Builder Pro — adjust",
            f"I've got the \"{plan['title']}\" plan on "
            "the bench, but I didn't catch what to "
            "change in that. Name the new size (\"make "
            "it 5x2\"), the species (\"use walnut "
            "instead\"), the joinery (\"use dowels\"), "
            "the finish (\"finish it with poly\"), or "
            "the quantity (\"make it 4\") and I'll re-run "
            "the cut list and the math.")
    plan["dims"] = dims
    plan["title"] = _title_for(plan.get("kind", "generic"),
                               plan.get("species", ""),
                               int(plan.get("qty", 1) or 1),
                               plan.get("topic", ""))
    _update_plan(uid, plan["id"], dims=dims,
                 species=plan.get("species"),
                 finish=plan.get("finish"),
                 joinery_pref=plan.get("joinery_pref"),
                 qty=plan.get("qty"), title=plan["title"])
    fresh = _get_plan(uid, plan["id"]) or plan
    return _result(
        _TAG, f"Wood Project Builder Pro — updated: "
        f"{fresh['title']}",
        "Updated — " + "; ".join(changes) + ".\n\n"
        + _plan_intro(fresh))


def _do_close(uid: str) -> List[Dict]:
    plan = _active_plan(uid)
    if not plan:
        return _result(_TAG, "Wood Project Builder Pro — "
                       "nothing to scrap",
                       "No open wood plan right now — "
                       "nothing to scrap.")
    _update_plan(uid, plan["id"], status="cancelled")
    return _result(
        _TAG, "Wood Project Builder Pro — scrapped",
        f"Scrapped — the \"{plan['title']}\" draft is "
        "off the bench. Nothing was ordered or cut; it "
        "was only ever a plan.")


def _do_bf_none(uid: str) -> List[Dict]:
    return _result(
        _TAG, "Wood Project Builder Pro — board feet",
        "Board feet I can figure to the decimal — I just "
        "need the piece. Say \"cut list for a workbench\" "
        "or \"board feet for a 3x6 bookshelf\" and the "
        "plan will carry every part's math: (rough "
        "thickness x width x length) / 144, the waste "
        "factor, and the kerf, all shown.")


def _do_order(uid: str) -> List[Dict]:
    """The ordering-route, honest by construction (the
    Builder's shape): the lumber sheet FROM THE PLAN +
    the ordering pack's approval door. Nothing buys."""
    plan = _active_plan(uid)
    if not plan:
        return _result(
            _TAG, "Wood Project Builder Pro — no plan",
            "There's no wood plan to order lumber from "
            "yet — say \"cut list for a …\" first and "
            "I'll spec one.")
    r = _render(plan)
    lines = [f"Lumber sheet from the plan "
             f"\"{plan['title']}\" ({r['species']}):"]
    for g in r["purchase"]:
        lines.append(
            f"  • {g['stock']} {r['species']}: "
            f"{g['boards']} x 8 ft board(s) "
            f"({g['bf']:.1f} BF in the parts)")
    lines.append(
        f"Total to buy: {r['bf_buy']:.1f} BF (parts "
        f"{r['bf_total']:.1f} BF + waste + kerf counted).")
    lines.append(
        "How buying works here: I don't order anything "
        "myself — this sheet goes through OG's ordering "
        "flow, which prepares the order at the yard or "
        "store you pick and stops for YOUR approval on "
        "the preview before a single dollar moves. One "
        "shop note: buy lumber IN PERSON when you can — "
        "you pick straight boards off the rack; a "
        "delivery picker grabs what's on top. Nothing "
        "has been bought or charged.")
    return _result(_TAG, "Wood Project Builder Pro — "
                   "lumber sheet", "\n".join(lines))


def _save_plan_doc(uid: str, plan: Dict):
    """Save the cut-list document into the caller's
    locker (the Builder's shape, the same seams).
    Returns (record, reason): exactly one is set."""
    import og_storage
    if not og_storage.storage_enabled():
        return None, "dark"
    name = f"{plan['title']} — cut list.txt"
    data = _plan_text(plan).encode("utf-8")[:PLAN_DOC_CAP]
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
        logger.warning(f"WoodPro plan save failed: {e}")
        return None, "other"


def _do_handoff(uid: str) -> List[Dict]:
    plan = _active_plan(uid)
    if not plan:
        views = [p for p in plans_for(uid)
                 if p.get("status") == "handed_off"]
        if views:
            return _result(
                _TAG, "Wood Project Builder Pro — handed off",
                f"The \"{views[0].get('title', '')}\" plan "
                "is already with the Project Manager — "
                "nothing new to hand off. Say \"cut list "
                "for a …\" for a fresh plan.")
        return _result(
            _TAG, "Wood Project Builder Pro — no plan",
            "No wood plan to hand off yet — say \"cut "
            "list for a …\" and I'll spec one first.")
    # 1) The PM project (og_pm's own create path, the
    # Builder's diff-to-learn-the-id shape).
    try:
        import og_pm
        before = {p.get("id") for p in og_pm._projects(uid)
                  if isinstance(p, dict)}
        og_pm._create_project(uid, f"Build the {plan['title']}")
        after = [p for p in og_pm._projects(uid)
                 if isinstance(p, dict)
                 and p.get("id") not in before]
        proj = after[0] if after else None
    except Exception as e:
        logger.warning(f"WoodPro PM handoff failed: {e}")
        proj = None
    if not proj:
        return _result(
            _TAG, "Wood Project Builder Pro — handoff glitch",
            "The Project Manager didn't take the plan — "
            "nothing was created and the draft is still "
            "open here. Say \"save the plan\" again and "
            "I'll retry.")
    pid = proj.get("id", "")
    ptitle = proj.get("title", "")
    # 2) The cut-list document into the locker, 3) filed.
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
            logger.warning(f"WoodPro filing failed: {e}")
            filed = False
        if filed:
            doc_line = (f"The cut list itself is saved in "
                        f"your locker as "
                        f"\"{rec.get('name', '')}\" and "
                        "filed under the project.")
        else:
            doc_line = (f"The cut list is saved in your "
                        f"locker as \"{rec.get('name', '')}"
                        "\", but the filing link didn't "
                        "land — say \"file this under the "
                        "project\" and the Filer will link "
                        "it.")
    else:
        reasons = {
            "dark": "the locker is switched off right now",
            "free": "your plan doesn't include locker "
                    "storage (it starts at Standard)",
            "quota": "your locker is full",
            "other": "the locker save glitched",
        }
        doc_line = (f"The cut list could NOT be saved to "
                    f"your locker — {reasons.get(why, why)}"
                    " — so there's nothing for the Filer "
                    "to link yet. The plan still lives "
                    "right here with the Wood Pro, and the "
                    "project below carries the work.")
    _update_plan(uid, plan["id"], status="handed_off",
                 project_id=pid,
                 doc_id=(rec or {}).get("id", ""))
    body = (f"Handed off — the Project Manager now has "
            f"\"{ptitle}\" as a project (Progress shows "
            "it), with its steps queued there. " + doc_line
            + " The Wood Pro's draft is closed; changes "
              "from here run through the project.")
    return _result(_TAG, "Wood Project Builder Pro — "
                   "handed off", body)


def woodpro_results(claim: Dict, uid: str) -> List[Dict]:
    op = claim.get("op")
    if op == "wood_create":
        return _do_create(uid, claim)
    if op == "wood_show":
        return _do_show(uid, claim)
    if op == "wood_adjust":
        return _do_adjust(uid, claim)
    if op == "wood_close":
        return _do_close(uid)
    if op == "wood_order":
        return _do_order(uid)
    if op == "wood_handoff":
        return _do_handoff(uid)
    if op == "wood_bf_none":
        return _do_bf_none(uid)
    return _do_show(uid, claim)


# --- The seam ---------------------------------------------------------------------

_pending = {"claim": None, "garble": None}


def install_woodpro_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search OUTSIDE the
    Builder seam (installed last, from the og_fixqueue
    chain) so wood asks claim here and everything else
    delegates back untouched. Persona files never
    touched."""
    if getattr(agent_instance, "_og_woodpro_installed", False):
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
            if uid and woodpro_enabled():
                claim, info = _claim_woodpro(str(message), uid)
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
            logger.warning(f"WoodPro trigger check failed: {e}")
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
                results = woodpro_results(claim, uid)
            except Exception as e:
                logger.warning(f"WoodPro op failed: {e}")
                results = _result(
                    _TAG, "Wood Project Builder Pro — glitch",
                    "The wood ask glitched — nothing was "
                    "planned, ordered, or handed off. Say "
                    "it again and I'll retry.")
        if results is None:
            results = prev_search(query, num_results)
        if garble and results:
            results = og_garble.annotate(results, garble,
                                         prepend=True)
        return results

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_woodpro_installed = True


# --- Routes -------------------------------------------------------------------------

def register_woodpro_routes(app):
    """GET /woodpro/status — the caller's own wood plans
    (id, title, kind, species, board feet, cost range,
    the PM project once handed off). Identity is the
    caller's cookie uid, like every Round 95+ surface."""

    @app.get("/woodpro/status")
    def woodpro_status_route(request: Request):
        uid = request.cookies.get("ogai_uid") or ""
        return {"ok": True, "enabled": woodpro_enabled(),
                "plans": plans_for(uid) if uid else []}
