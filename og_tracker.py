"""Round 122: THE TRACKER SPECIALIST HELPER (Brent's
order, 2026-10-12 11:25 PDT: "Tracker specialist
helper" — specced in round-122/SPEC.md.) The crew's
tracker — ONE chat front-end that unifies OG's
tracking asks and routes every one of them through
the machinery that ALREADY owns it. Audit-first, NO
duplicates: this seat builds no watch engine, no
scheduler, no alert path of its own.

WHAT ALREADY EXISTED (ridden, never forked):

  - PRICE TRACKS ARE ROUND 18'S, EXACTLY. og_monitor
    owns price watches — its caps (the per-tier
    "watch" cap), its poll cadence (OG_ALERT_POLL_
    SECONDS), its fire-once rule, and its delivery
    (the fired alert stored, delivered at the top of
    the next exchange, plus the og_notify "price_
    alert" record — evaluate_all). og_monitor's own
    claim takes "watch BTC" / "alert me if..." —
    this seat takes the TRACK verb it never claimed
    ("track the price of BTC", "track AAPL below
    200") and hands og_monitor a job in its own
    shape, run through og_monitor's own runner
    (_watch_set / _watch_cancel). Same store, same
    caps, same fire path — a price track set here IS
    a monitor watch, listed by "my watchlist" too.
  - PAGE TRACKS ARE ROUND 29'S, EXACTLY. og_watch
    owns Marketplace page watches — the fbwatch slot
    ladder per tier, the seen-store, the Steel
    check cadence, the active / waiting_login /
    paused_checkpoint states, zero-send by
    construction. og_mpwatch (Round 54) extends
    those records with best-deal matches and the
    message/haggle approval chain — untouched here.
    This seat takes "track <item> on marketplace"
    (a verb og_watch never claimed) and runs the
    create / stop through og_watch.watch_results —
    its caps and its duplicate rule ride whole.
  - WHEREABOUTS READ ROUND 73'S GPS STORE, LIVE.
    og_gps.points_for (the caller's own saved
    points), og_gps.location_on_for (their location
    switch) — read at answer time, never copied. A
    saved point is a SAVED point: it is reported as
    the place the user pinned, never dressed as a
    live position. OG holds NO live tracker feed —
    no vehicle tracker, no pet collar, no phone
    finder is connected — and this seat invents
    none.
  - PACKAGE / ORDER STATUS HAS NO FEED, AND THE
    SEAT SAYS SO. No carrier hookup (UPS / FedEx /
    USPS) exists anywhere in OG, and OG never
    places the user's orders (the Buying seat,
    Round 117, prepares to the approval wall; the
    user's own hand pays at the merchant). A
    package ask therefore answers from the caller's
    OWN records — the Buying seat's book, read via
    og_buying.status_for — and names the honest
    limit plainly: the merchant's shipping email
    carries the carrier's live tracking link. No
    shipment state is ever invented here.
  - CHANGES REPORT THE MACHINERY'S ONE WAY. A price
    move fires through og_monitor's poll, a listing
    change through og_watch's check — each lands
    exactly one stored alert, delivered at the top
    of the next exchange plus its og_notify record
    (the R81 way). This seat adds NO second report
    path, so a change can never report twice.
  - THE CREW / PM / FILER AND EVERY EARLIER SEAT
    KEEP THEIR ASKS. This seat checks the inner
    claims exactly (og_github first, then PM,
    Filer, crew, Builder, Wood Pro, MaintEng,
    Mechanic, Blueprint, Shopping, Buying, Selling,
    Coding, Phone, CarPlay, the R54 marketplace
    chain, og_watch, og_monitor, the ordering pack)
    and steps aside — checked, never executed.

WHAT IT DOES, in chat:
  - TRACK A PRICE — "track BTC", "track the price
    of AAPL above 250", "keep track of ETH" — set
    on og_monitor's machinery with the seat's own
    already-tracking rule (the same symbol already
    active is reported, never duplicated).
  - TRACK A PAGE — "track ladders on marketplace
    under $200 near Tacoma" — created on og_watch's
    machinery, same slots, same states.
  - TRACK A PACKAGE — answered off the caller's
    own Buying book + the plain no-carrier-feed
    line (above).
  - WHERE IS MY <thing> — the caller's own things:
    a matching saved GPS point is reported AS a
    saved point; anything else gets the honest
    no-live-feed answer naming what IS on record.
  - STOP TRACKING <x> — routed to whichever
    machinery holds it (monitor symbol / page
    watch) and stopped THERE.
  - WHAT AM I TRACKING? — the ONE unified list:
    page watches with their live states + price
    tracks with theirs, own account only. A price
    track that FIRED carries the handoff line: it
    hit the user's number, and buying runs through
    the Buying helper (Round 117) under its rules —
    this seat stops there.
  - THE BENCH. Small and honest: the last tracking
    action taken (the watches themselves live in
    the machinery's stores, never copied here).
    Per-account, durable, Round 18 store pattern
    (og_tracker_data table, tracker_store.json
    fallback, OG_TRACKER_STORE). GET /tracker/
    status reports the caller's own counts, read
    live off the machinery stores, + the bench.

THE FENCES — binding:
- TRACKERS TRACK THE USER'S OWN THINGS, NEVER A
  PERSON. Tracking another person — following
  someone, finding someone's location, watching a
  partner's / kid's / employer's device to locate
  THEM — is REFUSED BY NAME, first, before any
  other lane: a person is not a device. The user's
  own household devices they own and manage are
  theirs; the refusal is about locating a PERSON,
  and it never bends.
- READ-ONLY TRACKING. This seat never buys, books,
  messages, or pays: it holds no send machinery,
  no payment machinery, no browser-driving path
  (source-pinned). A tracked price that hits its
  target hands off to the Buying helper / the user
  under those rules, and stops there.
- STATES ARE READ OFF THE REAL STORES, NEVER
  INVENTED. A watch state, a price, a saved point,
  a buy's status — each is read live from the
  store that owns it at answer time; a value that
  cannot be read is said plainly, never guessed.
- NO NEW LOOPS, NO NEW TIMERS (R72 doctrine): the
  seat reads/writes only on chat turns; all cadence
  rides the existing monitor poll and watch checks
  (source-pinned: no threads, no tasks). The seat
  dispatches no crew jobs, so the R81 crew caps
  ride og_crew untouched. Switch OG_TRACKER_
  ENABLED (unset = ON; off = the seat doesn't
  exist).
- Persona file never touched; app.py NOT touched
  — module + og_library/og_fixqueue seams only.
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

_TAG = "OG TRACKER"

TRACKER_STORE_FILE = os.getenv("OG_TRACKER_STORE",
                               "tracker_store.json")

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, StoreError, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_tracker_lock = threading.RLock()

# Lazily-resolved app deps (the og_crew precedent):
# the caller's tier rides app.py's context var; tests
# bind their own through bind_app.
_deps: Dict = {}


def bind_app(deps):
    _deps.update(deps)


def _tier_now() -> str:
    if "get_tier" in _deps:
        try:
            return str(_deps["get_tier"]() or "free")
        except Exception:
            return "free"
    try:
        import app as _appmod
        return str(_appmod._current_tier.get(
            "tier", "free") or "free")
    except Exception:
        return "free"


# --- Switch -----------------------------------------------------------------------


def tracker_enabled() -> bool:
    """OG_TRACKER_ENABLED=false is the kill switch.
    Unset = ON."""
    val = os.environ.get("OG_TRACKER_ENABLED")
    if val is not None and val.strip().lower() in (
            "0", "false", "no", "off"):
        return False
    return True


# --- Durable store (Round 18 pattern, mirroring og_carplay) -----------------------


def _db_connect():
    conn = psycopg.connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_tracker_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(TRACKER_STORE_FILE):
        try:
            with open(TRACKER_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Tracker store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(TRACKER_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Tracker store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_tracker_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"Tracker DB load failed: {e}")
            raise StoreError("tracker store unavailable (Round 99)") from e
    with _tracker_lock:
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
                        "INSERT INTO og_tracker_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Tracker DB save failed: {e}")
            raise StoreError("tracker store unavailable (Round 99)") from e
    with _tracker_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


# --- The bench (only the last action — the watches live in the machinery) ---------


def _bench(uid: str) -> Dict:
    got = _blob(uid, "bench", {})
    return got if isinstance(got, dict) else {}


def _save_bench(uid: str, rec: Dict):
    _put_blob(uid, "bench", rec)


def _note(uid: str, text: str):
    _save_bench(uid, {"last": text, "at": time.time()})


def _result(tag: str, title: str, body: str,
            href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body,
             "href": href}]


# --- The unified view (read live off the machinery's own stores) ------------------


def _tracking_view(uid: str) -> Dict:
    """The caller's tracks, read at answer time from the
    stores that own them — og_watch's watches, og_monitor's
    watches + both pending-alert queues. Nothing is copied
    into this seat's store."""
    view: Dict = {"pages": [], "prices": [], "fired": [],
                  "alerts_waiting": 0}
    if not uid:
        return view
    try:
        import og_watch as W
        for w in W._watches(uid):
            if isinstance(w, dict):
                view["pages"].append(w)
        view["alerts_waiting"] += len(W._pending_alerts(uid))
    except Exception:
        pass
    try:
        import og_monitor as M
        for w in M._watches(uid):
            if not isinstance(w, dict):
                continue
            if w.get("status") == "active":
                view["prices"].append(w)
            elif w.get("status") == "fired":
                view["fired"].append(w)
        view["alerts_waiting"] += len(M._pending_alerts(uid))
    except Exception:
        pass
    return view


_PAGE_STATUS_WORDS = {
    "active": "watching",
    "waiting_login": "WAITING for your Facebook log-in",
    "paused_checkpoint": "PAUSED — Facebook wants a fresh "
                         "log-in",
}


def _page_row(w: Dict) -> str:
    import og_watch as W
    last = w.get("last_check")
    when = ("never yet" if not last else
            str(last)[:16].replace("T", " ") + " UTC")
    crit = f"'{w.get('item')}'"
    if w.get("max_price"):
        crit += f" under {W._fmt_price(w['max_price'])}"
    if w.get("place"):
        crit += f" near {w['place']}"
    return (f"• {crit} — "
            f"{_PAGE_STATUS_WORDS.get(w.get('status'), w.get('status'))}, "
            f"checks about every {w.get('cadence_min')} min, "
            f"last check {when}")


def _price_row(w: Dict) -> str:
    import og_monitor as M
    return (f"• {w.get('symbol')} ({w.get('kind')}): "
            f"{M._watch_terms(w)} (set at "
            f"{M._fmt_price(w.get('base'))})")


# --- Ops ----------------------------------------------------------------------------


def _do_person(uid: str, claim: Dict) -> List[Dict]:
    """THE PRIVACY FENCE. Tracking a person is refused
    BY NAME — first, plainly, and without a workaround."""
    return _result(
        _TAG, "Tracker — people are not tracked",
        "I don't track people — full stop. Not a partner, "
        "not a kid, not an employee, not anybody: following "
        "someone, finding someone's location, or watching a "
        "person's phone or car to locate THEM is refused by "
        "name at this seat, and it doesn't bend.\n"
        "What I track is YOUR stuff on YOUR account: prices, "
        "Marketplace listings, your own saved places and "
        "gear. A device YOU own and manage is yours — where "
        "a feed you connected exists for it, I'll read it. "
        "But a person is not a device, and locating a person "
        "is not a tracking job I'll run.")


def _do_package(uid: str, claim: Dict) -> List[Dict]:
    """Package / order status: the caller's OWN records
    only. OG has no carrier feed and never places the
    order — both said plainly; no state is invented."""
    buy = None
    try:
        import og_buying as BY
        st = BY.status_for(uid)
        if isinstance(st, dict):
            buy = st.get("buy")
    except Exception:
        buy = None
    lines = []
    if buy:
        item = (buy.get("title") or buy.get("item")
                or "your item")
        state = buy.get("status") or "on record"
        lines.append(
            f"Your own book first: the buy I have on record "
            f"for you — **{item}** — sits at **{state}** on "
            f"OG's side. That is the state of the buy WE "
            f"prepared here; it is not a shipment state. OG "
            f"never places the order — your own hand pays at "
            f"the merchant — so there is no OG order in "
            f"transit to follow.")
    lines.append(
        "Live package tracking needs a carrier feed — UPS, "
        "FedEx, USPS — and OG has NO carrier hookup "
        "connected on this account, so there is no live "
        "'where is it right now' read I can honestly give "
        "you. The merchant's shipping email carries the "
        "carrier's tracking link; that link is the live "
        "source, and I won't invent a status in its place.")
    lines.append(
        "What I CAN keep tracked for you from here: a price "
        "('track the price of …') or a Marketplace listing "
        "('track … on marketplace').")
    _note(uid, "package status ask (no carrier feed "
               "connected)")
    return _result(_TAG, "Tracker — package status",
                   "\n".join(lines))


def _do_where(uid: str, claim: Dict) -> List[Dict]:
    """Whereabouts of the caller's OWN things: a saved
    GPS point is read back AS a saved pin; anything else
    gets the honest no-live-feed answer. Never a live
    position — none exists to read."""
    thing = claim.get("thing") or ""
    head = claim.get("head") or ""
    points: List[Dict] = []
    loc_on = False
    try:
        import og_gps as G
        points = G.points_for(uid) or []
        loc_on = G.location_on_for(uid)
    except Exception:
        points, loc_on = [], False
    words = set(re.findall(r"[a-z0-9]+", thing.lower()))
    hit = None
    for p in points:
        name = str(p.get("name") or "").lower()
        nwords = set(re.findall(r"[a-z0-9]+", name))
        if name and (head and head in nwords or words & nwords):
            hit = p
            break
    if hit:
        body = (
            f"Your saved place **{hit.get('name')}** is "
            f"pinned at {hit.get('lat')}, {hit.get('lon')}"
            + (f" (note: {hit.get('note')})"
               if hit.get("note") else "")
            + ". That is the spot YOU SAVED in your GPS "
              "points, read back as a saved pin — NOT a live "
              "read. OG has no live tracker feed on it: no "
              "vehicle tracker, pet collar, or phone finder "
              "is connected to this account, so where it is "
              "right this second is not something I can see, "
              "and I won't guess.")
        _note(uid, f"whereabouts: saved pin "
                   f"'{hit.get('name')}' read back")
        return _result(_TAG, "Tracker — whereabouts", body)
    lines = [
        f"OG has no live tracker feed on your "
        f"{head or 'gear'} — no vehicle tracker, pet "
        f"collar, or phone finder is connected to this "
        f"account, so there is no live position I can "
        f"read, and I won't invent one."]
    names = ", ".join(str(p.get("name")) for p in points
                      if p.get("name"))
    if names:
        lines.append(
            f"Your saved GPS places on record: {names}. Ask "
            f"'where is my <name>' and I'll read the pin "
            f"back — as a saved pin, not a live fix.")
    else:
        lines.append(
            "You have no saved GPS places on record yet — "
            "places get pinned from the GPS folder, and I'll "
            "read them back here as saved pins.")
    lines.append(
        f"Your own location sharing to OG reads "
        f"{'ON' if loc_on else 'OFF'} right now — Round "
        f"73's switch, read live off its store.")
    _note(uid, f"whereabouts ask: {head or thing} "
               f"(no live feed)")
    return _result(_TAG, "Tracker — whereabouts",
                   "\n".join(lines))


def _do_price(uid: str, claim: Dict) -> List[Dict]:
    """A price track IS an og_monitor watch: the seat
    builds the monitor's own job shape and runs the
    monitor's own runner (its caps, its pricing, its
    fire path). The seat's one addition: the same
    symbol already active is reported, never
    duplicated."""
    import og_monitor as M
    sym = claim.get("sym")
    for w in M._active_watches(uid):
        if w.get("symbol") == sym:
            _note(uid, f"price track already live: {sym}")
            return _result(
                _TAG, "Tracker — already tracking",
                f"Already tracking **{sym}** for you — "
                f"{M._watch_terms(w)} (set at "
                f"{M._fmt_price(w.get('base'))}). One live "
                f"track per symbol here; say 'stop tracking "
                f"{sym}' first if you want different terms. "
                f"Nothing was added.")
    job = {"kind": "watch_set", "symbol": sym,
           "skind": claim.get("skind"),
           "cond": claim.get("cond")
           or {"type": "pct", "value": 10.0}}
    out = M._watch_set(job, uid, _tier_now)
    if out:
        _note(uid, f"price track set: {sym}")
        return out
    return _result(
        _TAG, "Tracker — glitch",
        "That price track glitched on my end — nothing "
        "was set. Say it again and I'll retry.")


def _do_page(uid: str, claim: Dict) -> List[Dict]:
    """A page track IS an og_watch fbwatch: created (and
    stopped) through og_watch.watch_results, so the slot
    ladder, the duplicate rule, and the login states are
    the Watch's own."""
    import og_watch as W
    job = {"op": "create", "item": claim.get("item") or "",
           "max_price": claim.get("max_price"),
           "place": claim.get("place") or "",
           "kind": "fbwatch"}
    out = W.watch_results(job, uid, _tier_now())
    _note(uid, f"page track ask: {claim.get('item') or ''}")
    return out


def _do_url(uid: str, claim: Dict) -> List[Dict]:
    return _result(
        _TAG, "Tracker — that link isn't a watch page",
        "Page watches in OG ride Marketplace searches — a "
        "bare link isn't a watchable page here, and I "
        "won't pretend a tracker exists for it. Open the "
        "link in my browser and I'll read it live WITH "
        "you; for a listing, say 'track <item> on "
        "marketplace' and the Watch machinery keeps "
        "checking it while you're away.")


def _do_stop(uid: str, claim: Dict) -> List[Dict]:
    """Stop lands on whichever machinery holds the
    track: a monitor symbol goes to og_monitor's own
    cancel; anything else to og_watch's own stop."""
    import og_monitor as M
    import og_watch as W
    words = claim.get("words") or ""
    sym, _kind = M._symbol(words)
    if sym and any(w.get("symbol") == sym
                   for w in M._active_watches(uid)):
        out = M._watch_cancel(
            {"kind": "watch_cancel", "symbol": sym}, uid)
        _note(uid, f"stopped price track: {sym}")
        return out
    if sym:
        fired = [w for w in M._watches(uid)
                 if w.get("symbol") == sym
                 and w.get("status") == "fired"]
        page_hit = any(
            words.lower() in str(w.get("item") or "").lower()
            for w in W._watches(uid))
        if fired and not page_hit:
            w = fired[-1]
            _note(uid, f"stop ask on spent track: {sym}")
            return _result(
                _TAG, "Tracker — that track already fired",
                f"**{sym}** already hit your number — it "
                f"fired at {M._fmt_price(w.get('fired_price'))} "
                f"({M._watch_terms(w)}). A fired track is "
                f"spent: nothing is still running on it, so "
                f"there's nothing left to stop. Say 'track "
                f"{sym}' again if you want a fresh one.")
    if W._watches(uid):
        out = W.watch_results(
            {"op": "stop", "words": words}, uid, _tier_now())
        _note(uid, f"stopped page track ask: {words}")
        return out
    if sym:
        out = M._watch_cancel(
            {"kind": "watch_cancel", "symbol": sym}, uid)
        _note(uid, f"stop ask, no such price track: {sym}")
        return out
    out = W.watch_results(
        {"op": "stop", "words": words}, uid, _tier_now())
    _note(uid, f"stop ask: {words}")
    return out


def _do_list(uid: str, claim: Dict) -> List[Dict]:
    """WHAT AM I TRACKING? — the one unified list, own
    account only, every row read live. A fired price
    track carries the Buying handoff and stops there."""
    view = _tracking_view(uid)
    bench = _bench(uid)
    if not view["pages"] and not view["prices"] \
            and not view["fired"]:
        lines = [
            "You're not tracking anything right now — no "
            "page watches, no price tracks on your account.",
            "Start one: 'track the price of BTC', 'track "
            "ladders on marketplace under $200 near "
            "Tacoma'. Stopping is 'stop tracking <name>'."]
        if bench.get("last"):
            lines.append(f"Last tracking action here: "
                         f"**{bench['last']}**.")
        return _result(_TAG, "Tracker — your tracking",
                       "\n".join(lines))
    lines = ["Everything being tracked on YOUR account:"]
    if view["pages"]:
        lines.append("**Marketplace page watches** "
                     "(the Watch machinery checks these):")
        lines.extend(_page_row(w) for w in view["pages"])
    if view["prices"]:
        lines.append("**Price tracks** (the monitor's "
                     "watches — a fire lands at the top of "
                     "your next chat):")
        lines.extend(_price_row(w) for w in view["prices"])
    if view["fired"]:
        import og_monitor as M
        lines.append("**Price tracks that HIT your "
                     "number:**")
        for w in view["fired"]:
            lines.append(
                f"• {w.get('symbol')} ({w.get('kind')}): "
                f"{M._watch_terms(w)} — FIRED at "
                f"{M._fmt_price(w.get('fired_price'))}.")
        lines.append(
            "A fired track hit the number YOU set. Buying "
            "runs through the Buying helper — say 'buy it' "
            "with the item and it walks the buy to the "
            "approval wall under its rules. This seat never "
            "buys, and it stops at the handoff.")
    if view["alerts_waiting"]:
        lines.append(
            f"{view['alerts_waiting']} alert(s) are waiting "
            f"at the top of your chat.")
    lines.append("Stop any of them: 'stop tracking "
                 "<name>'.")
    return _result(_TAG, "Tracker — your tracking",
                   "\n".join(lines))


def _do_status(uid: str, claim: Dict) -> List[Dict]:
    view = _tracking_view(uid)
    bench = _bench(uid)
    total = (len(view["pages"]) + len(view["prices"])
             + len(view["fired"]))
    lines = [
        "The Tracker seat: one front door for OG's "
        "tracking — prices ride the monitor's watches, "
        "Marketplace pages ride the Watch machinery, and "
        "every state you see is read live off those "
        "stores."]
    if total:
        lines.append(
            f"On your account right now: "
            f"{len(view['pages'])} page watch(es), "
            f"{len(view['prices'])} live price track(s), "
            f"{len(view['fired'])} fired, "
            f"{view['alerts_waiting']} alert(s) waiting. "
            f"Ask 'what am I tracking?' for the full list.")
    else:
        lines.append("You're not tracking anything yet.")
    if bench.get("last"):
        lines.append(f"Last tracking action here: "
                     f"**{bench['last']}**.")
    lines.append(
        "Ask me: 'track the price of BTC', 'track ladders "
        "on marketplace', 'where is my truck', 'track my "
        "package', 'what am I tracking?', or 'stop "
        "tracking <name>'. One fence, said plain: I track "
        "your own things — never a person.")
    return _result(_TAG, "Tracker — status",
                   "\n".join(lines))


def tracker_results(claim: Dict, uid: str) -> List[Dict]:
    op = claim.get("op")
    if op == "tk_person":
        return _do_person(uid, claim)
    if op == "tk_package":
        return _do_package(uid, claim)
    if op == "tk_where":
        return _do_where(uid, claim)
    if op == "tk_price":
        return _do_price(uid, claim)
    if op == "tk_page":
        return _do_page(uid, claim)
    if op == "tk_url":
        return _do_url(uid, claim)
    if op == "tk_stop":
        return _do_stop(uid, claim)
    if op == "tk_list":
        return _do_list(uid, claim)
    if op == "tk_status":
        return _do_status(uid, claim)
    return _result(_TAG, "Tracker — glitch",
                   "That tracking ask glitched on my end — "
                   "nothing was changed anywhere. Say it "
                   "again and I'll retry.")


def status_for(uid: str) -> Dict:
    if not uid:
        return {"bench": None, "tracking": None}
    view = _tracking_view(uid)
    bench = _bench(uid)
    return {
        "bench": ({"last": bench.get("last"),
                   "at": bench.get("at")} if bench
                  else None),
        "tracking": {
            "page_watches": len(view["pages"]),
            "price_active": len(view["prices"]),
            "price_fired": len(view["fired"]),
            "alerts_waiting": view["alerts_waiting"]}}


# --- Chat claims (narrow frames — the crew/PM/filer doctrine) ---------------------

_GARBLE_VOCAB = ("track", "tracking", "tracker", "price",
                 "marketplace", "facebook", "package",
                 "order", "delivery", "shipment", "where",
                 "stop", "list", "status", "alerts",
                 "under", "near", "watching")

# THE PRIVACY FENCE — the located subject is a PERSON.
# Shapes: "where is my wife", "where is my wife's car",
# "where is she", "track my husband's phone", "follow
# my kid", "track her location", "track him". The
# person's own THING (my kid's BIKE) is not the fence —
# the device tail set is the locating-a-person set.
_PERSON = (r"(?:wife|husband|spouse|girlfriend|boyfriend|"
          r"fiancee?|partner|kids?|son|daughter|children|"
          r"child|baby|mom|dad|mother|father|brother|"
          r"sister|friend|boss|employee|coworker|"
          r"co-worker|ex-wife|ex-husband)")
_PERSON_DEVICE = (r"(?:phone|car|device|location|tablet|"
                 r"laptop|truck)")
_END = r"(?:\s+(?:right\s+now|today|now))?\s*[.?!]*$"
_FENCE_RES = (
    re.compile(r"\bwhere(?:'s| is| has)\s+(?:my|the)\s+"
               + _PERSON + r"\b(?:'s\s+" + _PERSON_DEVICE
               + r"\b)?" + _END, re.I),
    re.compile(r"\bwhere(?:'s| is)\s+(?:he|she|they)\b",
               re.I),
    re.compile(r"\b(?:track|follow|locate|find|spy\s+on|"
               r"keep\s+tabs\s+on)\s+(?:my|the|his|her|"
               r"their)\s+" + _PERSON + r"\b(?:'s\s+"
               + _PERSON_DEVICE + r"\b)?" + _END, re.I),
    re.compile(r"\b(?:track|follow|find)\s+"
               r"(?:him|her|them)\b", re.I),
    re.compile(r"\b(?:track|follow|locate|find)\s+"
               r"(?:his|her|their)\s+" + _PERSON_DEVICE
               + r"\b", re.I),
)

_PACKAGE_RE = re.compile(
    r"\btrack\b[^.?!]*\b(?:package|parcel|shipment|"
    r"delivery|order)\b|"
    r"\bwhere(?:'s| is)\s+my\s+(?:package|parcel|"
    r"shipment|delivery|order)\b|"
    r"\b(?:package|delivery|order|shipment)\s+"
    r"(?:status|tracking)\b|"
    r"\bmy\s+(?:package|parcel|shipment)\b", re.I)

_LIST_RE = re.compile(
    r"\bwhat\s+am\s+i\s+tracking\b|"
    r"\bwhat(?:'s| is)\s+being\s+tracked\b|"
    r"\bmy\s+tracking\b|\btracking\s+list\b|"
    r"\bshow\s+(?:me\s+)?(?:my\s+)?tracking\b|"
    r"\bam\s+i\s+tracking\s+anything\b|"
    r"\blist\s+(?:my\s+)?tracks?\b|"
    r"\bwhat\s+tracks?\s+do\s+i\s+have\b", re.I)

_STATUS_RE = re.compile(
    r"\btracker\s+(?:status|help|specialist)\b|"
    r"\btracking\s+(?:status|help)\b|"
    r"\bhelp\s+(?:me\s+)?with\s+tracking\b|"
    r"\btracker\s+helper\b", re.I)

_STOP_RE = re.compile(
    r"\bstop\s+tracking\s+(?:the\s+|my\s+)?(.+?)\s*[.?!]*$|"
    r"\buntrack\s+(.+?)\s*[.?!]*$|"
    r"\b(?:cancel|remove)\s+(?:my\s+)?track(?:ing)?\s+"
    r"(?:on\s+|for\s+)?(.+?)\s*[.?!]*$", re.I)

_WHERE_RE = re.compile(
    r"\bwhere(?:'s| is)\s+my\s+(.+?)\s*[.?!]*$", re.I)

_THING_NOUNS = {
    "car", "truck", "phone", "keys", "key", "wallet",
    "laptop", "tablet", "ipad", "iphone", "bike",
    "motorcycle", "trailer", "boat", "excavator",
    "gear", "van", "suv", "rig", "machine", "equipment",
    "dog", "cat", "pet", "vehicle", "camper", "rv",
    "generator", "compressor", "welder", "saw",
}

_URL_RE = re.compile(r"https?://|www\.", re.I)
_TRACK_VERB_RE = re.compile(
    r"\b(?:track|keep\s+track\s+of)\b", re.I)


def _other_claim(message: str, uid: str):
    """(source, claim) of the first inner seat that
    claims this message exactly, else None. og_github
    FIRST — its pendings, clarifying questions, and
    every repo-shaped ask are the R60 path's own. Then
    the PM, the Filer, the crew, the Builder, the
    Wood Pro, MaintEng, the Mechanic, the Blueprint
    Specialist, Shopping, Buying, Selling, Coding,
    Phone, CarPlay, the R54 marketplace chain,
    og_watch, og_monitor, and the ordering pack:
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
        import og_phone
        c = og_phone._claim_exact(message, uid)
        if c:
            return "phone", c
    except Exception:
        pass
    try:
        import og_carplay
        c = og_carplay._claim_exact(message, uid)
        if c:
            return "carplay", c
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
        import og_watch
        c = og_watch._claim_job(message, uid)
        if c:
            return "watch", c
    except Exception:
        pass
    try:
        import og_monitor
        c = og_monitor.parse_monitor_intent(message)
        if c:
            return "monitor", c
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


def _parse_page(raw: str) -> Optional[Dict]:
    """The TRACK verb on a Marketplace page ask — the
    one parse this seat owns. The price/place tails are
    og_watch's OWN tail regexes, and the create itself
    runs through og_watch.watch_results: caps, dedupe,
    states, all the Watch's."""
    import og_watch as W
    tail = None
    m = re.search(r"\btrack\s+(.+?)\s+on\s+(?:facebook\s+|fb\s+)?"
                  r"marketplace\b(.*)$", raw, re.I)
    if m:
        tail = m.group(1) + (m.group(2) or "")
    if tail is None:
        m = re.search(r"\btrack\s+(?:facebook\s+|fb\s+)?"
                      r"marketplace\s+for\s+(.+)$", raw, re.I)
        if m:
            tail = m.group(1)
    if tail is None:
        m = re.search(r"\bkeep\s+track\s+of\s+(.+?)\s+on\s+"
                      r"(?:facebook\s+|fb\s+)?marketplace\b(.*)$",
                      raw, re.I)
        if m:
            tail = m.group(1) + (m.group(2) or "")
    if tail is None:
        return None
    max_price = None
    pm = W._PRICE_TAIL_RE.search(tail)
    if pm:
        try:
            max_price = float(pm.group(1).replace(",", ""))
        except ValueError:
            max_price = None
        tail = tail[:pm.start()] + tail[pm.end():]
    place = ""
    nm = W._PLACE_TAIL_RE.search(tail)
    if nm:
        place = " ".join(nm.group(1).split()).strip(" .,")
        tail = tail[:nm.start()]
    item = " ".join(str(tail).split()).strip(" .,")
    item = re.sub(r"^(?:a|an|the)\s+", "", item, flags=re.I)
    if not item:
        return None
    return {"item": item, "max_price": max_price,
            "place": place}


def _parse_price(raw: str) -> Optional[Dict]:
    """The TRACK verb on a price ask — builds og_monitor's
    own job shape (symbol + skind via og_monitor._symbol;
    cond in parse_monitor_intent's exact forms). Qualified
    narrowly: the ask must say "price", carry a $ or a
    condition tail, name a known crypto, or use a bare
    ALL-CAPS symbol — so 'track ladders' (a thing, lower
    case, no price word) is never mistaken for a stock."""
    import og_monitor as M
    if "marketplace" in raw.lower():
        return None
    pats = (
        r"\bkeep\s+track\s+of\s+(?:the\s+price\s+of\s+)?\$?"
        r"([A-Za-z][A-Za-z0-9.\-]{0,9})\b(.*)$",
        r"\btrack\s+(?:the\s+price\s+of\s+)?\$?"
        r"([A-Za-z][A-Za-z0-9.\-]{0,9})\b(.*)$",
        r"\btrack\s+\$?([A-Za-z][A-Za-z0-9.\-]{0,9})"
        r"'s\s+price\b(.*)$",
    )
    for pat in pats:
        m = re.search(pat, raw, re.I)
        if not m:
            continue
        token, tail = m.group(1), (m.group(2) or "")
        if token.lower() in ("of", "the", "a", "an", "my",
                             "this", "that", "it"):
            continue
        sym, skind = M._symbol(token)
        if not sym:
            continue
        cond = None
        cm = re.search(r"\b(above|over)\s+\$?([\d,]+(?:\.\d+)?)",
                       tail, re.I)
        if cm:
            cond = {"type": "threshold", "dir": "above",
                    "value": float(cm.group(2).replace(",", ""))}
        else:
            cm = re.search(r"\b(below|under)\s+\$?([\d,]+(?:\.\d+)?)",
                           tail, re.I)
            if cm:
                cond = {"type": "threshold", "dir": "below",
                        "value": float(cm.group(2).replace(",", ""))}
            else:
                cm = re.search(r"([\d.]+)\s*%", tail)
                if cm:
                    try:
                        cond = {"type": "pct",
                                "value": float(cm.group(1))}
                    except ValueError:
                        cond = None
        qualified = (
            "price" in raw.lower() or "$" in raw
            or sym in M._CRYPTO or cond is not None
            or token.isupper())
        if not qualified:
            continue
        return {"sym": sym, "skind": skind,
                "cond": cond or {"type": "pct", "value": 10.0}}
    return None


def _claim_exact(message: str, uid: str) -> Optional[Dict]:
    raw = str(message or "").strip()
    if not raw or not uid:
        return None
    # THE PRIVACY FENCE FIRST: an ask whose located
    # subject is a person is refused by this seat before
    # any other lane — including before deferral, so no
    # other seat is ever handed a locate-a-person ask
    # through this door.
    for rx in _FENCE_RES:
        if rx.search(raw):
            return {"op": "tk_person", "raw": raw}
    if _defer_to_others(raw, uid):
        return None
    if _PACKAGE_RE.search(raw):
        return {"op": "tk_package", "raw": raw}
    m = _WHERE_RE.search(raw)
    if m:
        tail = " ".join(m.group(1).split()).strip(" .,")
        head = (tail.lower().replace("'", "").split()
                or [""])[-1]
        words = set(re.findall(r"[a-z0-9]+", tail.lower()))
        known = bool(words & _THING_NOUNS)
        if not known:
            try:
                import og_gps as G
                for p in G.points_for(uid) or []:
                    nwords = set(re.findall(
                        r"[a-z0-9]+",
                        str(p.get("name") or "").lower()))
                    if words & nwords:
                        known = True
                        break
            except Exception:
                pass
        if known:
            return {"op": "tk_where", "thing": tail,
                    "head": head, "raw": raw}
    m = _STOP_RE.search(raw)
    if m:
        words = next(g for g in m.groups() if g)
        return {"op": "tk_stop", "words": words.strip(),
                "raw": raw}
    if _LIST_RE.search(raw):
        return {"op": "tk_list", "raw": raw}
    if _STATUS_RE.search(raw):
        return {"op": "tk_status", "raw": raw}
    if _URL_RE.search(raw) and _TRACK_VERB_RE.search(raw):
        return {"op": "tk_url", "raw": raw}
    page = _parse_page(raw)
    if page:
        return {"op": "tk_page", **page, "raw": raw}
    price = _parse_price(raw)
    if price:
        return {"op": "tk_price", **price, "raw": raw}
    return None


def _claim_tracker(message: str, uid: str):
    """(claim, garble_info): the exact claim first; on
    a miss, the Round 75 second chance — og_garble
    repairs the TRIGGER words only; the payload (the
    item, the symbol, the place) passes as written."""
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


# --- The seam ---------------------------------------------------------------------

_pending = {"claim": None, "garble": None}


def install_tracker_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search OUTSIDE the
    CarPlay seam (installed last, from the og_fixqueue
    chain) so Tracker-specialist asks claim here and
    everything else delegates back untouched. Persona
    files never touched."""
    if getattr(agent_instance, "_og_tracker_installed",
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
            if uid and tracker_enabled():
                claim, info = _claim_tracker(
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
            logger.warning(f"Tracker trigger check failed: {e}")
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
                results = tracker_results(claim, uid)
            except Exception as e:
                logger.warning(f"Tracker op failed: {e}")
                results = _result(
                    _TAG, "Tracker — glitch",
                    "That tracking ask glitched on my end — "
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
    agent_instance._og_tracker_installed = True


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


def register_tracker_routes(app):
    """GET /tracker/status — the caller's own tracking:
    counts read live off the machinery stores + the
    bench's last action."""

    @app.get("/tracker/status")
    def tracker_status_route(request: Request):
        uid = _route_uid(request)
        view = status_for(uid) if uid else {
            "bench": None, "tracking": None}
        return {"ok": True, "enabled": tracker_enabled(),
                **view}
