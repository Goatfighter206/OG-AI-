"""Round 275 — THE ACTIVITY SOURCE + the header
bubble's live line (Brent, 2026-10-13: "Og should be
in the same bubble and og should be gold and the
online and ready should be showing what he doing or
what he has he's helpers doing").

WHAT THIS IS. OG's header used to say "Online &
Ready" no matter what his crew was doing. This
module is the ONE per-account activity source the
header bubble reads — and the same source the Crew
Dashboard (Round 267) will read when it builds, so
the bubble and the dashboard can never disagree.
It holds exactly two kinds of truth, both read or
recorded from REAL events:

  1. THE LAST CLAIM. When a helper seat answers a
     chat ask, the seat's routed result carries its
     tag ("[OG SELLING] ..." — the house result
     shape). The observer installed by this module
     wraps web_search OUTERMOST (after Messenger in
     og_fixqueue's install chain), reads the tag off
     the REAL routing result — it routes nothing,
     claims nothing, answers nothing — and records
     {tag, seat, title, time} on the caller's own
     bench (og_activity_data). A claim is "current"
     for CURRENT_WINDOW seconds after its turn.
  2. BACKGROUND WORK, READ LIVE. Watches and price
     tracks and crew jobs are read at answer time
     from the stores that own them (og_watch,
     og_monitor, og_crew — the Tracker seat's and
     the crew board's own read seams). Nothing is
     copied; a watch that stops stops being named.

THE HONESTY LAW (the SPEC's, binding): the bubble
NEVER shows activity that is not happening. No
rotating fake statuses, no seat named unless that
seat actually claimed work for THIS account, no
count that is not read live from a real store.
Per-account isolation is absolute: every read and
every record is keyed by the caller's own uid.

SURFACES.
  GET /activity/status — {ok, enabled, current,
  last, background, text}. `text` is the composed
  bubble line (the current claim's line, else the
  first background line, else None = leave the
  bubble at its truthful idle states). The client
  (static/og_r275.js) paints it only over the idle
  text, so the pill's existing priorities stand:
  needs-user states (approval > sign-in > browser)
  and the transient states (Thinking… / Browsing /
  Making…) are NEVER demoted by helper text.
  activity_snapshot(uid) is the structured read the
  Crew Dashboard reuses.

THE SWITCH. OG_ACTIVITY_ENABLED (unset = ON). Off =
the observer records nothing, the route reports
enabled False with an empty snapshot, and the
bubble falls back to its static truthful states.
The bubble itself (Round 275's page change) stays.

NO new loop, no new timer: the record is written on
chat turns that already happen; the reads happen on
the status route's own requests. Persona untouched.
"""

import json
import logging
import os
import re
import threading
import time
from typing import Dict, List, Optional

from fastapi import Request

logger = logging.getLogger(__name__)

_TAG = "OG ACTIVITY"

ACTIVITY_STORE_FILE = os.getenv(
    "OG_ACTIVITY_STORE", "activity_store.json")

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, DB_OPTIONS as _DB_OPTS, StoreError
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_activity_lock = threading.RLock()

# A claim stays "current" on the bubble for this
# long after the turn that produced it — the user
# may still be reading the reply or following up
# with the same seat; every follow-up turn the seat
# claims refreshes the record. After the window the
# claim is history (kept as `last`), never dressed
# as ongoing work.
CURRENT_WINDOW = 600.0

# The seats the bubble may name, by the tag their
# routed results carry, with the plain name the
# bubble uses. An unmapped tag is NEVER recorded —
# the bubble names only seats it can name truly.
# (OG BROWSER is deliberately absent: the browser's
# activity already owns the pill through the
# /browser/status channel — "Browsing <host>" — and
# naming it again from claims would fight that
# channel. The generator seats are absent for the
# same reason: "Making your video / song" is the
# page updater's own line.)
SEAT_NAMES = {
    "OG SELLING": "Selling",
    "OG CODING": "Coding",
    "OG MESSENGER": "Messenger",
    "OG FACEBOOK": "Facebook",
    "OG MARKETPLACE": "Marketplace",
    "OG SHOPPING": "Shopping",
    "OG BUYING": "Buying",
    "OG PHONE": "Phone",
    "OG CARPLAY": "CarPlay",
    "OG TRACKER": "Tracker",
    "OG CAR MECHANIC": "Mechanic",
    "OG MAINTENANCE + ENGINEER": "Maintenance",
    "OG BLUEPRINT SPECIALIST": "Blueprint",
    "OG PROJECT MANAGER": "Project Manager",
    "OG PROJECT FILING": "Filing",
    "OG PROJECT BUILDER": "Builder",
    "OG WOOD PROJECT BUILDER PRO": "Wood Builder",
    "OG CREW": "The crew",
    "OG WATCH": "Watch",
}

# Whole-line specials where "<Name> is on it" would
# read wrong.
SEAT_LINES = {
    "OG FIX": "Working on a fix",
    "OG CREW": "The crew is on it",
}
SEAT_NAMES["OG FIX"] = "Fixes"

_TAG_RE = re.compile(r"\[(OG [A-Z0-9 &+.'-]+)\]")


# --- Switch -----------------------------------------------------------------------


def activity_enabled() -> bool:
    """OG_ACTIVITY_ENABLED=false is the kill switch.
    Unset = ON."""
    val = os.environ.get("OG_ACTIVITY_ENABLED")
    if val is not None and val.strip().lower() in (
            "0", "false", "no", "off"):
        return False
    return True


# --- Durable store (Round 18 pattern, mirroring og_messenger) ----------------------


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=_DB_CT, options=_DB_OPTS)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_activity_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(ACTIVITY_STORE_FILE):
        try:
            with open(ACTIVITY_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Activity store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(ACTIVITY_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Activity store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_activity_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"Activity DB load failed: {e}")
            raise StoreError("activity store unavailable (Round 99)") from e
    with _activity_lock:
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
                        "INSERT INTO og_activity_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Activity DB save failed: {e}")
            raise StoreError("activity store unavailable (Round 99)") from e
    with _activity_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


# --- The claim record ---------------------------------------------------------------


def seat_for_results(results) -> Optional[Dict]:
    """The seat that produced a routed result list,
    read off the FIRST result's body — the house
    shape is "[TAG] body" (a Round 75 garble note may
    precede the tag on its own line; the first mapped
    tag in the body wins). Returns {tag, seat} or
    None. Pure: reads nothing, writes nothing."""
    if not isinstance(results, (list, tuple)) or not results:
        return None
    first = results[0]
    if not isinstance(first, dict):
        return None
    body = str(first.get("body") or "")
    for m in _TAG_RE.finditer(body):
        tag = m.group(1).strip()
        if tag in SEAT_NAMES:
            return {"tag": tag, "seat": SEAT_NAMES[tag]}
    return None


def _seat_line(tag: str, seat: str) -> str:
    return SEAT_LINES.get(tag) or f"{seat} is on it"


def record_claim(uid: str, results) -> bool:
    """Record that a helper seat claimed THIS turn
    for THIS account, from the real routing result.
    Fail-soft by design: a store failure loses one
    bubble update, never a chat turn."""
    if not uid or not activity_enabled():
        return False
    hit = seat_for_results(results)
    if not hit:
        return False
    title = ""
    try:
        title = str(results[0].get("title") or "")[:120]
    except Exception:
        title = ""
    rec = {"tag": hit["tag"], "seat": hit["seat"],
           "title": title, "ts": time.time()}
    try:
        _put_blob(uid, "claim", rec)
    except Exception as e:
        logger.warning(f"Activity claim record failed: {e}")
        return False
    return True


def last_claim(uid: str) -> Optional[Dict]:
    """The caller's last recorded claim, or None.
    Fail-soft: a store error reads as no record —
    the bubble falls back to its other truths."""
    if not uid:
        return None
    try:
        got = _blob(uid, "claim", None)
    except Exception:
        return None
    if isinstance(got, dict) and got.get("tag") \
            and got.get("seat"):
        return {"tag": str(got["tag"]),
                "seat": str(got["seat"]),
                "title": str(got.get("title") or ""),
                "ts": float(got.get("ts") or 0)}
    return None


# --- Background work, read live from the owning stores -------------------------------


def background_for(uid: str) -> List[Dict]:
    """The caller's REAL background work right now:
    crew jobs in a live state (og_crew's own read
    seam), page watches running (og_watch), price
    tracks active (og_monitor) — each read live,
    each in its own fail-soft leg, counts computed
    from the rows returned. Nothing is copied into
    this module's store."""
    out: List[Dict] = []
    if not uid:
        return out
    try:
        import og_crew as C
        live = [j for j in C.jobs_for(uid)
                if isinstance(j, dict)
                and j.get("state") in ("working", "queued")]
        if live:
            working = [j for j in live
                       if j.get("state") == "working"]
            if working:
                title = str(
                    working[0].get("title") or "").strip()
                text = ("Crew is working"
                        + (f" — {title}" if title else ""))
            else:
                text = (f"Crew has {len(live)} job"
                        f"{'s' if len(live) != 1 else ''} queued")
            out.append({"kind": "crew", "count": len(live),
                        "text": text})
    except Exception:
        pass
    try:
        import og_watch as W
        n = sum(1 for w in W._watches(uid)
                if isinstance(w, dict)
                and w.get("status") == "active")
        if n:
            out.append({
                "kind": "watch", "count": n,
                "text": f"Watching {n} page"
                        f"{'s' if n != 1 else ''} for you"})
    except Exception:
        pass
    try:
        import og_monitor as M
        n = sum(1 for w in M._watches(uid)
                if isinstance(w, dict)
                and w.get("status") == "active")
        if n:
            out.append({
                "kind": "monitor", "count": n,
                "text": f"Tracking {n} price"
                        f"{'s' if n != 1 else ''} for you"})
    except Exception:
        pass
    return out


# --- The snapshot: THE shared activity read (bubble now, R267 dashboard later) --------


def activity_snapshot(uid: str,
                      now: Optional[float] = None) -> Dict:
    """{current, last, background, text} for ONE
    account. `current` is the last claim while it is
    inside CURRENT_WINDOW; `last` is the last claim
    whenever it happened; `text` is the bubble's
    composed line — the current claim's line, else
    the first background line, else None (idle: the
    bubble keeps its static truthful states)."""
    now = time.time() if now is None else now
    last = last_claim(uid)
    current = None
    if last and now - last["ts"] <= CURRENT_WINDOW:
        current = {**last,
                   "line": _seat_line(last["tag"],
                                      last["seat"])}
    background = background_for(uid)
    text = None
    if current:
        text = current["line"]
    elif background:
        text = background[0]["text"]
    return {"current": current, "last": last,
            "background": background, "text": text}


def status_for(uid: str) -> Dict:
    if not activity_enabled():
        return {"current": None, "last": None,
                "background": [], "text": None}
    if not uid:
        return {"current": None, "last": None,
                "background": [], "text": None}
    return activity_snapshot(uid)


# --- The observer: records claims from the real routing result -----------------------


def install_activity_observer(agent_instance, get_uid):
    """Wrap web_search OUTERMOST (installed last,
    from the og_fixqueue chain, after Messenger) so
    every routed result passes here on its way back.
    The wrapper routes NOTHING and answers NOTHING:
    it calls the wrapped search untouched, records
    the claim the routing result itself names, and
    returns the results byte-identical. Fail-soft:
    any error in the observer leaves the results
    exactly as they were."""
    if getattr(agent_instance, "_og_activity_installed",
               False):
        return
    prev_search = getattr(agent_instance, "web_search",
                          None)
    if prev_search is None:
        return

    def search_wrapped(query, num_results=5):
        results = prev_search(query, num_results)
        try:
            if activity_enabled():
                uid = ""
                try:
                    uid = get_uid()
                except Exception:
                    uid = ""
                if uid:
                    record_claim(uid, results)
        except Exception as e:
            logger.warning(f"Activity observer failed: {e}")
        return results

    agent_instance.web_search = search_wrapped
    agent_instance._og_activity_installed = True


# --- Routes -----------------------------------------------------------------------


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


def register_activity_routes(app):
    """GET /activity/status — the caller's own
    activity snapshot: the current helper claim, the
    last claim, the live background work, and the
    composed bubble line."""

    @app.get("/activity/status")
    async def activity_status_route(request: Request):
        uid = _route_uid(request)
        return {"ok": True,
                "enabled": activity_enabled(),
                **status_for(uid)}
