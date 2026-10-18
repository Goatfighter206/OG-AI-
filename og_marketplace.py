"""Round 123: THE FACEBOOK MARKETPLACE SPECIALIST
HELPER (Brent's words, 2026-10-12 11:39 PDT: "Facebook
marketplace specialist helper" — specced in
round-123/SPEC.md.) The crew's Marketplace hand — the
specialist face of hunting OG can already do. Audit-
first, NO duplicates: this seat EXTENDS the Round 54
marketplace watch/haggle machinery and sits beside
Shopping (R116) / Buying (R117) / Selling (R118) —
Marketplace is its home turf, not a fork of any of
them.

WHAT ALREADY EXISTED (ridden, never forked):

  - THE APPROVAL CHAIN IS ROUND 54'S, EXACTLY.
    og_mpwatch owns watched-match best-deal ranking,
    the message approval, the SEPARATE haggle
    approval inside the user's ceiling, the seller-
    reply deal approval, and the only send path (the
    Round 27 approval-gated browser flow). This seat
    holds NO approval records of its own, parks no
    drafts, and sends nothing: it checks
    og_mpwatch's claim exactly and steps aside, and
    when it shows a watched match it names that
    chain as the door. Brent's standing marketplace
    law (2026-10-08) binds the whole seat: OG asks
    before messaging a seller; asks SEPARATELY
    before haggling, inside a ceiling the user sets;
    takes a final yes before anything is agreed; and
    OG never pays.
  - THE WATCHES ARE ROUND 29'S, EXACTLY. og_watch
    owns the Marketplace page watches — the fbwatch
    slot ladder, the seen-store, the Steel check
    cadence, the active / waiting_login /
    paused_checkpoint states, zero-send by
    construction. A watch is created, listed, and
    stopped through og_watch's own words; this seat
    reads the caller's watches live for its book and
    never copies, creates, or stops one.
  - THE SEARCH READS RIDE ROUND 116'S, AT THE DATA
    LEVEL. The caller's own watched matches come
    from og_shopping._marketplace_options (which
    reads og_mpwatch.ranked_matches — best deal
    first, prices known by construction), and fresh
    listings come off the same web search the whole
    crew rides (the seam parked at install), parsed
    by og_shopping._parse_web_option + _relevant —
    no second parser, no second watcher, no second
    relevance rule. The budget and item cleaning are
    og_shopping's own (_budget_of / _clean_item /
    _condition_of_text); the place tail is
    og_watch's own _PLACE_TAIL_RE.
  - THE PICK HANDS OFF TO BUYING (R117) THROUGH THE
    ONE PICK STORE IT ALREADY READS. A pick from
    this seat's shortlist is saved with
    og_shopping._save_pick, in the pick shape the
    Buying seat consumes — so "buy it" runs the
    Buying lane under its rules (the true total, the
    destination check, the approval card, the final
    yes, payment always the user's own hand). This
    seat never runs a buy itself.
  - EVERY EARLIER SEAT KEEPS ITS ASKS. This seat
    checks the inner claims exactly (og_github
    first, then PM, Filer, crew, Builder, Wood Pro,
    MaintEng, Mechanic, Blueprint, Shopping, Buying,
    Selling, Coding, Phone, CarPlay, Tracker, the
    R54 marketplace chain, og_watch, og_monitor, the
    ordering pack) and steps aside — checked, never
    executed. In particular: "find me X on
    marketplace" is Shopping's (its find verb +
    marketplace signal); "watch for X on
    marketplace" is the R54 chain's create; "track X
    on marketplace" is the Tracker's verb; "message
    this seller" and every haggle/decide phrase are
    Round 54's. None of those are re-claimed here.

WHAT IT OWNS — the Marketplace-named shapes nobody
claimed:

  - A MARKETPLACE SEARCH — "search marketplace for
    ladders under $200 near Tacoma", "browse
    marketplace for a couch", "what's on marketplace
    for kayaks", "marketplace listings for a
    trailer". Marketplace-ONLY: the caller's watched
    matches plus fresh Marketplace listings off the
    web harvest, filtered to the stated range (over-
    range listings are left out and counted, never
    quietly shown), ranked BEST DEAL FIRST in the
    locked shopping shape — a few in range, price +
    where + the one-line why — every price carrying
    its source and as-of. A listing's state is READ
    (from the watch's checks or the listing's own
    page words), never assumed.
  - THE HUNT BOOK — the per-account record of the
    caller's Marketplace hunts: each search with its
    range and place, the pick taken from it, plus
    the live picture read at answer time — the
    caller's watches with their states and best
    matches (og_watch's store), the approvals
    waiting in the Round 54 chain
    (og_mpwatch.pending_approvals, read-only), and
    the saved pick. "My marketplace hunts" reads it
    back; "forget the X hunt" clears a hunt from the
    book (watches are NOT touched — stopping one is
    the Watch's own door, named in the answer).
  - THE PICK — "I'll take #2" against this seat's
    live hunt (when the Shopping seat holds no
    shortlist of its own claiming the words) saves
    the pick for the Buying hand-off and names the
    doors honestly: buying through the Buying
    helper; messaging/haggling through the Round 54
    chain for a watched match — and for a fresh
    listing, the watch door first, because the chain
    rides watched matches.
  - REFINES on the live hunt — "cheaper", "under
    $N", "closer", "show the rest" — the Shopping
    seat's own semantics, on this seat's hunt only.

THE FENCES — binding, Brent's standing marketplace
law at full strength:

  - ASK BEFORE MESSAGING A SELLER. Nothing in this
    seat messages anyone, ever — it holds no send
    machinery, no draft machinery, no browser path
    (source-pinned). The message door is Round 54's
    approval, named wherever a watched match shows.
  - ASK SEPARATELY BEFORE HAGGLING, INSIDE A
    CEILING THE USER SETS. A message approval never
    implies a haggle approval (Round 54's own rule,
    untouched); no counter goes out past the user's
    ceiling — a further counter comes back to the
    user first, every time (og_mpwatch enforces it
    at draft, request, and send; this seat never
    drafts an offer at all).
  - FINAL YES BEFORE ANYTHING IS AGREED. OG never
    agrees a price, a meetup, or a deal on its own;
    a seller's agreement becomes Round 54's fresh
    deal approval for the user, and approving it
    sends nothing.
  - OG NEVER PAYS. No payment machinery exists in
    this seat (source-pinned); the purchase is the
    user's own hand at the merchant, or the Buying
    helper's prepare -> approve -> handoff under its
    rules — and even Buying never pays.
  - SELLER THREADS CARRY PRICE TALK ONLY — never
    why the user wants the thing (the laptop-hunt
    discipline); pickup/meetup is the user's move,
    surfaced in the doors line, never promised
    anywhere in a draft — and this seat writes no
    drafts, so nothing of the user's business can
    leak through one.
  - LISTINGS ARE READ, NEVER INVENTED. A price is
    the listing's own word with its source and
    as-of; a listing with no printed price shows as
    "price not printed" and ranks after the priced
    ones; a pass that finds nothing says it found
    nothing.
  - Per-account isolation at every read (drilled);
    no new loop, no new timer (R72) — watches ride
    the existing monitor cadence, and this seat
    reads/writes only on chat turns; the seat
    dispatches no crew jobs, so the R81 crew caps
    ride og_crew untouched. Switch
    OG_MARKETPLACE_ENABLED (unset = ON; off = the
    seat doesn't exist).
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

_TAG = "OG MARKETPLACE"

SHOW_CAP = 4           # options presented per pass
KEEP_CAP = 8           # in-range options kept in state
BOOK_CAP = 20          # hunts kept in the book

MARKETPLACE_STORE_FILE = os.getenv(
    "OG_MARKETPLACE_STORE", "marketplace_store.json")

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, StoreError, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo, pooled_connect as _pooled_connect
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_marketplace_lock = threading.RLock()

# The harvest: raw web results through the seam.
# install_marketplace_tools parks the search chain
# below this seat in _harvest; the ops call it for
# RAW results and do their own parsing/ranking via
# og_shopping's parser. Uninstalled (unit drills set
# it directly), the harvest is empty and the seat
# says so.
_harvest = {"fn": None}


# --- Switch -----------------------------------------------------------------------


def marketplace_enabled() -> bool:
    """OG_MARKETPLACE_ENABLED=false is the kill
    switch. Unset = ON."""
    val = os.environ.get("OG_MARKETPLACE_ENABLED")
    if val is not None and val.strip().lower() in (
            "0", "false", "no", "off"):
        return False
    return True


# --- Durable store (Round 18 pattern, mirroring og_shopping) ----------------------


def _db_connect():
    conn = _pooled_connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_marketplace_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(MARKETPLACE_STORE_FILE):
        try:
            with open(MARKETPLACE_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Marketplace store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(MARKETPLACE_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Marketplace store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_marketplace_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"Marketplace DB load failed: {e}")
            raise StoreError("marketplace store unavailable (Round 99)") from e
    with _marketplace_lock:
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
                        "INSERT INTO og_marketplace_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Marketplace DB save failed: {e}")
            raise StoreError("marketplace store unavailable (Round 99)") from e
    with _marketplace_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


# --- The hunt state + the book ------------------------------------------------------


def _hunt(uid: str) -> Optional[Dict]:
    got = _blob(uid, "hunt", None)
    return got if isinstance(got, dict) and got else None


def _save_hunt(uid: str, state: Optional[Dict]):
    _put_blob(uid, "hunt", state or {})


def _book(uid: str) -> List[Dict]:
    got = _blob(uid, "book", [])
    return got if isinstance(got, list) else []


def _save_book(uid: str, book: List[Dict]):
    _put_blob(uid, "book", book[:BOOK_CAP])


def _record_hunt(uid: str, state: Dict):
    """One book entry per hunt: the ask's own facts —
    item, range, place, when, how many in range, and
    the pick when one is taken from it."""
    rec = {"item": state.get("item", ""),
           "budget": state.get("budget"),
           "place": state.get("place", ""),
           "at": state.get("at") or time.time(),
           "found": len(state.get("options") or []),
           "pick": None}
    # One record per hunt (item + place): re-running or
    # tightening the same hunt updates its record
    # instead of piling up near-duplicates.
    book = [r for r in _book(uid)
            if not (r.get("item") == rec["item"]
                    and r.get("place") == rec["place"])]
    book.insert(0, rec)
    _save_book(uid, book)


def _record_pick(uid: str, state: Dict, title: str):
    book = _book(uid)
    for r in book:
        if r.get("item") == state.get("item"):
            r["pick"] = title
            break
    _save_book(uid, book)


def _result(tag: str, title: str, body: str,
            href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body,
             "href": href}]


def _money(value) -> str:
    try:
        import og_shopping as SH
        return SH._money(value)
    except Exception:
        return f"${value}"


# --- The harvest (the crew's web search, Marketplace rows only) ---------------------


def _web_harvest(query: str, num_results: int = 10) -> List[Dict]:
    fn = _harvest.get("fn")
    if fn is None:
        return []
    try:
        out = fn(query, num_results) or []
    except Exception as e:
        logger.warning(f"Marketplace harvest failed: {e}")
        return []
    return [r for r in out
            if isinstance(r, dict) and "error" not in r]


_ITEM_ID_RE = re.compile(r"/marketplace/item/(\d+)")


def _listing_id(href: str) -> str:
    m = _ITEM_ID_RE.search(str(href or ""))
    return m.group(1) if m else ""


def _fresh_options(item: str, budget: Optional[float],
                   seen_ids: set, asof: str):
    """(options, over_count) off the web harvest,
    Marketplace rows only — parsed by og_shopping's
    own parser, filtered by og_shopping's own
    relevance rule, deduped against the caller's
    watched matches by listing id. Prices are the
    listings' own words; over-range rows are left
    out and counted, exactly the Shopping shape."""
    import og_shopping as SH
    query = f"{item} facebook marketplace"
    opts, over = [], 0
    for raw in _web_harvest(query):
        o = SH._parse_web_option(raw, asof)
        if o is None or o.get("lane") != "mp":
            continue
        lid = _listing_id(o.get("href") or "")
        if lid and lid in seen_ids:
            continue
        if lid:
            seen_ids.add(lid)
        blob = (o.get("title") or "") + " " + \
            str(raw.get("body") or "")
        if not SH._relevant(item, blob):
            continue
        price = o.get("price")
        if budget is not None and price is not None \
                and float(price) > float(budget):
            over += 1
            continue
        o = dict(o)
        o["source"] = "fresh"
        opts.append(o)
    return opts, over


# --- Ranking + the one-line why (the locked shopping shape) -------------------------


def _rank(opts: List[Dict], place_pref: bool = False) -> List[Dict]:
    def key(o):
        price = o.get("price")
        price = float(price) if price is not None else float("inf")
        watch = 0 if o.get("source") == "watch" else 1
        if place_pref:
            placed = 0 if (o.get("town")
                           or o.get("source") == "watch") else 1
            return (placed, price, watch)
        return (price, watch)
    return sorted(opts, key=key)


def _why(o: Dict, idx: int, state: Dict) -> str:
    price = o.get("price")
    budget = state.get("budget")
    pref = str(state.get("condition") or "")
    if idx == 0 and price is not None:
        why = "cheapest of the bunch"
    elif o.get("source") == "watch" and o.get("town"):
        why = f"local — {o['town']}, off your Marketplace watch"
    elif o.get("source") == "watch":
        why = "off your Marketplace watch"
    elif budget is not None and price is not None:
        why = (f"{_money(float(budget) - float(price))} under "
               f"your {_money(budget)} cap")
    elif price is None:
        why = "no price printed — the listing page carries it"
    elif o.get("condition"):
        why = f"sold as {o['condition']}"
    else:
        why = "in range"
    cond = str(o.get("condition") or "")
    if pref and cond and cond != pref:
        why += f"; heads-up, it's listed {cond}, not {pref}"
    elif pref and cond == pref:
        why += f"; {cond}, like you asked"
    return why


def _option_line(o: Dict, num: int, state: Dict, idx: int) -> str:
    price = o.get("price")
    pstr = _money(price) if price is not None else "price not printed"
    line = (f"{num}. {o.get('title', '')} — {pstr} at "
            f"Facebook Marketplace")
    if o.get("town"):
        line += f" ({o['town']})"
    line += f" — {_why(o, idx, state)}."
    if o.get("href"):
        line += f"\n   {o['href']}"
    return line


_DOORS_LINE = (
    "Messaging and haggling run the Marketplace approval "
    "chain, never from a bare ask: \"message this seller\" "
    "parks the exact words for YOUR approval first, a haggle "
    "is a SEPARATE approval inside a ceiling you set, and "
    "nothing is agreed without your final yes. OG never "
    "pays, never agrees a price on its own, and never "
    "arranges a meetup — pickup is your move.")

_PICK_LINE = (
    "Pick one (\"I'll take #2\") and I'll save the pick — "
    "buying runs through the Buying helper under its rules "
    "from there.")


def _present(state: Dict, lead: str = "",
             only_new: bool = False) -> str:
    opts = state.get("options") or []
    shown = state.get("shown") or []
    item = state.get("item") or "that"
    lines = []
    if lead:
        lines.append(lead)
    head = (f"Marketplace — {item}, best deal first. Prices "
            f"as of {state.get('asof', 'just now')} from "
            f"Facebook Marketplace")
    if any(o.get("source") == "watch" for o in opts):
        head += " + your own watch matches"
    head += (" — listings move and sell fast, so a price is "
             "the listing's own word, not a promise, and "
             "whether a listing is still live is read from "
             "the listing, never assumed.")
    lines.append(head)
    if only_new:
        start = int(state.get("_shown_before", 0) or 0)
        lines.append("More in range:")
        for i in range(start, len(shown)):
            lines.append(_option_line(shown[i], i + 1, state, i))
    else:
        for i, o in enumerate(shown):
            lines.append(_option_line(o, i + 1, state, i))
    over = int(state.get("over") or 0)
    if over and state.get("budget") is not None:
        lines.append(f"Over your {_money(state['budget'])} cap: "
                     f"{over} listing(s) left out.")
    more = len(opts) - len(shown)
    if more > 0:
        lines.append(f"{more} more in range — say \"show the "
                     f"rest\" to see them.")
    lines.append("Say \"cheaper\", \"only under $N\", or "
                 "\"closer\" to tighten the same hunt.")
    lines.append(_PICK_LINE)
    lines.append(_DOORS_LINE)
    return "\n".join(lines)


def _empty_body(state: Dict, note: str = "") -> str:
    item = state.get("item") or "that"
    cap = ""
    if state.get("budget") is not None:
        cap = f" under {_money(state['budget'])}"
    body = (f"No Marketplace listings for {item}{cap} turned "
            f"up in this pass — nothing in range, and I won't "
            f"invent one. ")
    if note:
        body += note + " "
    body += (f"Want eyes on it around the clock? Say \"watch "
             f"for {item} on marketplace\" and the Marketplace "
             f"watch keeps matches ranked best-deal first as "
             f"they post. Or loosen the range and I'll run the "
             f"hunt again.")
    return body


# --- The run: both lanes, filter, rank, remember -------------------------------------


def _run_hunt(uid: str, state: Dict) -> Dict:
    """Harvest the two lanes (the caller's watched
    matches via og_shopping's own lane reader, fresh
    Marketplace listings off the web harvest), apply
    the range rule, rank best deal first, and return
    the new state (shown = the first pass)."""
    import og_shopping as SH
    item = state["item"]
    budget = state.get("budget")
    asof = SH._asof_text(time.time())
    watch_opts, over = SH._marketplace_options(uid, item, budget)
    seen_ids = set()
    for o in watch_opts:
        lid = str(o.get("listing_id") or "") \
            or _listing_id(o.get("href") or "")
        if lid:
            seen_ids.add(lid)
        o["source"] = "watch"
    fresh, fresh_over = _fresh_options(item, budget, seen_ids,
                                       asof)
    over += fresh_over
    # Best deal first, pure — a stated place is recorded
    # on the hunt (and leads only when the user says
    # "closer", the Shopping seat's own semantics).
    opts = _rank(watch_opts + fresh)
    state = dict(state)
    state["options"] = opts[:KEEP_CAP]
    state["shown"] = opts[:SHOW_CAP]
    state["over"] = over
    state["asof"] = asof
    state["at"] = time.time()
    return state


# --- Ops --------------------------------------------------------------------------------


def _do_search(uid: str, claim: Dict) -> List[Dict]:
    state = {"item": claim["item"],
             "budget": claim.get("budget"),
             "place": claim.get("place", ""),
             "condition": claim.get("condition", "")}
    state = _run_hunt(uid, state)
    _save_hunt(uid, state)
    _record_hunt(uid, state)
    if not state["options"]:
        return _result(_TAG, "Marketplace — nothing in range",
                       _empty_body(state))
    return _result(_TAG, f"Marketplace — {state['item']}",
                   _present(state))


def _do_show(uid: str, claim: Dict) -> List[Dict]:
    state = _hunt(uid)
    if not state:
        return _result(
            _TAG, "Marketplace — no hunt yet",
            "No Marketplace hunt on your account yet. Say "
            "\"search marketplace for ... under $N\" and "
            "I'll build one.")
    return _result(_TAG, f"Marketplace — {state.get('item', '')}",
                   _present(state))


def _do_refine(uid: str, claim: Dict) -> List[Dict]:
    state = _hunt(uid)
    if not state:
        return _result(
            _TAG, "Marketplace — no hunt yet",
            "There's no Marketplace hunt to tighten yet. "
            "Say \"search marketplace for ...\" first.")
    kind = claim.get("kind")
    if kind == "more":
        opts = state.get("options") or []
        shown = state.get("shown") or []
        if len(shown) >= len(opts):
            return _result(
                _TAG, "Marketplace — that's the lot",
                "That's everything in range from the last "
                "pass — nothing held back.")
        state["_shown_before"] = len(shown)
        state["shown"] = opts[:KEEP_CAP]
        _save_hunt(uid, state)
        body = _present(state, only_new=True)
        state.pop("_shown_before", None)
        _save_hunt(uid, state)
        return _result(_TAG, "Marketplace — more listings",
                       body)
    if kind == "closer":
        state = dict(state)
        state["options"] = _rank(state.get("options") or [],
                                 place_pref=True)
        state["shown"] = state["options"][:SHOW_CAP]
        _save_hunt(uid, state)
        lead = ("Closer first — the listings that name a "
                "real place lead:")
        return _result(_TAG, "Marketplace — tightened",
                       _present(state, lead=lead))
    trial = dict(state)
    lead = ""
    if kind == "cheaper":
        priced = [float(o["price"])
                  for o in state["options"]
                  if o.get("price") is not None]
        if not priced:
            return _result(
                _TAG, "Marketplace — no prices to beat",
                "Nothing on the shortlist carries a price, "
                "so there's no number to go cheaper than. "
                "The list stands as shown.")
        floor = min(priced)
        new_budget = floor - 1
        if new_budget < 1:
            return _result(
                _TAG, "Marketplace — already at the floor",
                f"The cheapest in range is {_money(floor)} — "
                "there's no honest step cheaper from here. "
                "The shortlist stands.")
        trial["budget"] = new_budget
        lead = (f"Cheaper pass — hunting under "
                f"{_money(new_budget)} (the floor was "
                f"{_money(floor)}):")
    elif kind == "budget":
        trial["budget"] = claim.get("value")
        lead = f"Cap set to {_money(claim.get('value'))}:"
    trial = _run_hunt(uid, trial)
    if not trial["options"]:
        return _result(
            _TAG, "Marketplace — nothing tighter",
            _empty_body(trial,
                        "Your earlier shortlist still stands "
                        "— I didn't throw it away for an "
                        "empty pass."))
    _save_hunt(uid, trial)
    _record_hunt(uid, trial)
    return _result(_TAG, "Marketplace — tightened",
                   _present(trial, lead=lead))


def _pick_doors(o: Dict, item: str) -> str:
    if o.get("source") == "watch" or o.get("watch_id"):
        return ("From here: \"message this seller\" runs "
                "the Round 54 approval chain on this match "
                "— the exact words park for YOUR approval, "
                "a haggle is a separate approval inside a "
                "ceiling you set, and nothing is agreed "
                "without your final yes. Say \"buy it\" and "
                "the Buying helper takes it from this pick "
                "under its rules. OG never pays and never "
                "arranges the meetup — pickup is your move.")
    return ("From here: say \"buy it\" and the Buying "
            "helper takes it from this pick under its "
            "rules. This listing isn't on one of your "
            "watches yet, and the message/haggle approval "
            "chain rides watched matches — say \"watch "
            f"for {item} on marketplace\" and the watch "
            "will pick it up; then \"message this seller\" "
            "runs the chain. OG never pays and never "
            "arranges the meetup — pickup is your move.")


def _do_pick(uid: str, claim: Dict) -> List[Dict]:
    state = _hunt(uid)
    if not state:
        return _result(
            _TAG, "Marketplace — nothing to pick",
            "There's no Marketplace hunt to pick from yet. "
            "Say \"search marketplace for ...\" first.")
    shown = state.get("shown") or []
    opt = None
    idx = claim.get("index")
    if idx is not None:
        if 1 <= int(idx) <= len(shown):
            opt = shown[int(idx) - 1]
    else:
        needle = str(claim.get("match") or "").lower()
        for o in shown:
            if needle and needle in o.get("title", "").lower():
                opt = o
                break
    if opt is None:
        return _result(
            _TAG, "Marketplace — which one?",
            f"The shortlist runs to #{len(shown)} — name "
            "the number and I'll save that pick.")
    # THE BUYING HAND-OFF: the pick lands in the one
    # pick store the Buying seat reads, in its shape.
    try:
        import og_shopping as SH
        SH._save_pick(uid, {"option": opt,
                            "item": state.get("item", ""),
                            "at": time.time()})
    except Exception as e:
        logger.warning(f"Marketplace pick hand-off failed: {e}")
    _record_pick(uid, state, str(opt.get("title") or ""))
    price = opt.get("price")
    pstr = _money(price) if price is not None \
        else "price not printed"
    body = (f"Saved your pick: {opt.get('title', '')} — "
            f"{pstr} at Facebook Marketplace"
            + (f" ({opt['town']})" if opt.get("town") else "")
            + ". The pick is on your account for the "
              "buying hand-off. "
            + _pick_doors(opt, state.get("item", "")))
    return _result(_TAG, "Marketplace — pick saved", body)


_WATCH_STATUS_WORDS = {
    "active": "watching",
    "waiting_login": "WAITING for your Facebook log-in",
    "paused_checkpoint": "PAUSED — Facebook wants a fresh "
                         "log-in",
}


def _do_book(uid: str, claim: Dict) -> List[Dict]:
    """The hunt book: the caller's hunts from this
    seat's store + the live picture read off the
    stores that own it — watches (og_watch), open
    approvals (og_mpwatch, read-only), the saved pick
    (og_shopping). Own account only; nothing is
    copied, everything is read at answer time."""
    lines = []
    book = _book(uid)
    watches = []
    try:
        import og_watch as W
        watches = [w for w in W._watches(uid)
                   if isinstance(w, dict)]
    except Exception:
        watches = []
    approvals = []
    try:
        import og_mpwatch as MP
        approvals = MP.pending_approvals(uid)
    except Exception:
        approvals = []
    pick = None
    try:
        import og_shopping as SH
        pick = SH._pick(uid)
    except Exception:
        pick = None
    if not book and not watches and not approvals \
            and not pick:
        return _result(
            _TAG, "Marketplace — your hunt book is empty",
            "Nothing on your Marketplace book yet: no "
            "hunts run, no watches riding, no approvals "
            "waiting. Say \"search marketplace for ... "
            "under $N\" to start a hunt, or \"watch for "
            "... on marketplace\" and the watch hunts "
            "around the clock.")
    lines.append("Your Marketplace book — your hunts, "
                 "your watches, your open approvals, all "
                 "yours:")
    if book:
        lines.append("Hunts:")
        for r in book[:8]:
            line = f"• '{r.get('item')}'"
            if r.get("budget") is not None:
                line += f" under {_money(r['budget'])}"
            if r.get("place"):
                line += f" near {r['place']}"
            line += (f" — {int(r.get('found') or 0)} in "
                     f"range last pass")
            if r.get("pick"):
                line += f"; your pick: {r['pick']}"
            lines.append(line)
    if watches:
        lines.append("Watches riding (the Watch's own "
                     "states, read live):")
        for w in watches[:8]:
            import og_watch as W
            crit = f"'{w.get('item')}'"
            if w.get("max_price"):
                crit += f" under {W._fmt_price(w['max_price'])}"
            if w.get("place"):
                crit += f" near {w['place']}"
            kind = ("Marketplace watch"
                    if w.get("kind") == "mpwatch"
                    else "page watch")
            line = (f"• {crit} — {kind}, "
                    f"{_WATCH_STATUS_WORDS.get(w.get('status'), w.get('status'))}")
            matches = w.get("matches") or []
            if matches:
                best = min(
                    (m for m in matches
                     if m.get("price") is not None),
                    key=lambda m: float(m["price"]),
                    default=None)
                if best:
                    line += (f"; best match "
                             f"{W._fmt_price(best.get('price'))}"
                             f" — {best.get('title', '')}")
            lines.append(line)
    if approvals:
        lines.append("Approvals waiting in the chain "
                     "(nothing sends until you rule):")
        for r in approvals[:5]:
            if r.get("type") == "haggle":
                lines.append(
                    f"• haggle on '{r.get('listing_title', '')}'"
                    f" — offer "
                    f"{_money(r.get('offer'))} inside your "
                    f"ceiling of {_money(r.get('ceiling'))}")
            elif r.get("type") == "message":
                lines.append(
                    f"• message the seller of "
                    f"'{r.get('listing_title', '')}' "
                    f"({_money(r.get('price'))})")
            else:
                lines.append(
                    f"• seller reply on "
                    f"'{r.get('listing_title', '')}' — the "
                    f"deal call is yours")
        lines.append("Rule on them with \"approve "
                     "marketplace message\" / \"approve "
                     "marketplace haggle\" — or decline the "
                     "same way. Those words are the chain's "
                     "own.")
    if pick:
        o = pick.get("option") or {}
        lines.append(
            f"Saved pick: {o.get('title', '')} — "
            f"{_money(o.get('price')) if o.get('price') is not None else 'price not printed'}"
            f" — say \"buy it\" and the Buying helper "
            f"takes it from there.")
    lines.append(_DOORS_LINE)
    return _result(_TAG, "Marketplace — your hunt book",
                   "\n".join(lines))


def _do_forget(uid: str, claim: Dict) -> List[Dict]:
    words = str(claim.get("words") or "").lower()
    import og_shopping as SH
    toks = set(SH._item_tokens(words))
    book = _book(uid)
    kept, dropped = [], []
    for r in book:
        rtoks = set(SH._item_tokens(str(r.get("item") or "")))
        if toks and toks & rtoks:
            dropped.append(r)
        else:
            kept.append(r)
    hunt = _hunt(uid)
    hunt_cleared = False
    if hunt and toks and toks & set(
            SH._item_tokens(str(hunt.get("item") or ""))):
        _save_hunt(uid, None)
        hunt_cleared = True
    if dropped or hunt_cleared:
        _save_book(uid, kept)
        return _result(
            _TAG, "Marketplace — hunt forgotten",
            "Cleared from your hunt book. Your watches "
            "are untouched — a watch keeps riding until "
            "you stop it with the Watch's own words "
            "(\"stop watching ...\"); forgetting a hunt "
            "only clears this seat's record of it.")
    return _result(
        _TAG, "Marketplace — no such hunt",
        "I don't have a hunt by that name in your book — "
        "ask for \"my marketplace hunts\" and I'll read "
        "what's actually there.")


def marketplace_results(claim: Dict, uid: str) -> List[Dict]:
    op = claim.get("op")
    if op == "mk_search":
        return _do_search(uid, claim)
    if op == "mk_show":
        return _do_show(uid, claim)
    if op == "mk_refine":
        return _do_refine(uid, claim)
    if op == "mk_pick":
        return _do_pick(uid, claim)
    if op == "mk_book":
        return _do_book(uid, claim)
    if op == "mk_forget":
        return _do_forget(uid, claim)
    return _do_book(uid, claim)


def status_for(uid: str) -> Dict:
    """The read seam (/marketplace/status): the
    caller's own hunt summary + book count, with the
    watch and open-approval counts read live off the
    stores that own them."""
    out: Dict = {"hunt": None, "book": 0, "watches": 0,
                 "approvals_open": 0, "pick": None}
    if not uid:
        return out
    hunt = _hunt(uid)
    if hunt:
        out["hunt"] = {
            "item": hunt.get("item"),
            "budget": hunt.get("budget"),
            "place": hunt.get("place", ""),
            "options": len(hunt.get("options") or []),
            "shown": len(hunt.get("shown") or []),
            "asof": hunt.get("asof")}
    out["book"] = len(_book(uid))
    try:
        import og_watch as W
        out["watches"] = len(
            [w for w in W._watches(uid)
             if isinstance(w, dict)])
    except Exception:
        pass
    try:
        import og_mpwatch as MP
        out["approvals_open"] = len(MP.pending_approvals(uid))
    except Exception:
        pass
    try:
        import og_shopping as SH
        pick = SH._pick(uid)
        if pick:
            o = pick.get("option") or {}
            out["pick"] = {"title": o.get("title"),
                           "price": o.get("price"),
                           "item": pick.get("item")}
    except Exception:
        pass
    return out


# --- Chat claims (narrow frames — the crew/PM/filer doctrine) ---------------------

_GARBLE_VOCAB = ("marketplace", "facebook", "search",
                 "browse", "hunt", "hunting", "listings",
                 "find", "under", "near", "watch", "book",
                 "cheaper", "closer")

_BOOK_RE = re.compile(
    r"\bmy\s+marketplace\s+(?:hunts?|book)\b|"
    r"\bmarketplace\s+(?:hunts?|book|status|helper|"
    r"specialist)\b|"
    r"\bwhat\s+am\s+i\s+hunting\b|"
    r"\bshow\s+(?:me\s+)?my\s+marketplace\b", re.I)

_FORGET_RE = re.compile(
    r"\bforget\s+(?:the\s+|my\s+)?(.+?)\s+hunt\b|"
    r"\bstop\s+hunting\s+(?:for\s+)?(.+?)\s*[.?!]*$|"
    r"\bdrop\s+(?:the\s+)?(.+?)\s+hunt\b", re.I)

_SHOW_RE = re.compile(
    r"\bshow\s+(?:me\s+)?(?:my\s+|the\s+)?hunt\b|"
    r"\bmy\s+hunt\b|"
    r"\bmarketplace\s+results\b|"
    r"\bwhat\s+did\s+you\s+find\s+on\s+marketplace\b", re.I)

_CHEAPER_RE = re.compile(r"\bcheaper\b|"
                         r"\bless\s+expensive\b", re.I)
_CLOSER_RE = re.compile(r"\bcloser\b|\bnearer\b|"
                        r"\bnearby\b|\bnear\s+me\b", re.I)
_MORE_RE = re.compile(
    r"\bshow\s+(?:me\s+)?(?:the\s+)?rest\b|"
    r"\bmore\s+(?:options|listings)\b|"
    r"\bwhat\s+else\s+(?:is\s+)?(?:there|you\s+got)\b",
    re.I)

_ORDINALS = {"first": 1, "second": 2, "third": 3,
             "fourth": 4, "fifth": 5}
_PICK_NUM_RE = re.compile(
    r"\b(?:take|pick|choose|grab)\s+(?:the\s+)?"
    r"(?:#|number\s+|no\.?\s*)?(\d)\b", re.I)
_PICK_HASH_RE = re.compile(r"#\s?(\d)\b")
_PICK_ORD_RE = re.compile(
    r"\bthe\s+(first|second|third|fourth|fifth)\s+one\b",
    re.I)
_PICK_NAME_RE = re.compile(
    r"\bthe\s+([a-z][a-z0-9&+.\-]{2,20})\s+one\b", re.I)

# The Marketplace-named search frames nobody else
# claims. Shopping's verbs are find / search for /
# shop for / look for (+ deal frames) — "search
# marketplace for X", "browse marketplace for X",
# "what's on marketplace for X" match none of them,
# and the watch/track verbs belong to their owners.
_MP_SEARCH_RES = [
    re.compile(
        r"\bsearch\s+(?:(?:on|in|at)\s+)?(?:the\s+)?"
        r"(?:facebook\s+|fb\s+)?marketplace\s+for\s+(.+)$",
        re.I),
    re.compile(
        r"\b(?:browse|check|scan)\s+(?:(?:on|in|at)\s+)?"
        r"(?:the\s+)?(?:facebook\s+|fb\s+)?marketplace\s+"
        r"for\s+(.+)$", re.I),
    re.compile(
        r"\blook\s+(?:on|in|at)\s+(?:the\s+)?"
        r"(?:facebook\s+|fb\s+)?marketplace\s+for\s+(.+)$",
        re.I),
    re.compile(
        r"\bwhat(?:'s| is)\s+(?:on|in)\s+(?:the\s+)?"
        r"(?:facebook\s+|fb\s+)?marketplace\s+for\s+(.+)$",
        re.I),
    re.compile(
        r"\bmarketplace\s+(?:listings|search)\s+for\s+(.+)$",
        re.I),
    re.compile(
        r"\blistings\s+on\s+(?:facebook\s+|fb\s+)?"
        r"marketplace\s+for\s+(.+)$", re.I),
]


def _other_claim(message: str, uid: str):
    """(source, claim) of the first inner seat that
    claims this message exactly, else None. og_github
    FIRST — its pendings, clarifying questions, and
    every repo-shaped ask are the R60 path's own. Then
    the PM, the Filer, the crew, the Builder, the
    Wood Pro, MaintEng, the Mechanic, the Blueprint
    Specialist, Shopping, Buying, Selling, Coding,
    Phone, CarPlay, Tracker, the R54 marketplace
    chain, og_watch, og_monitor, and the ordering
    pack: checked exactly, never executed from here."""
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
        import og_tracker
        c = og_tracker._claim_exact(message, uid)
        if c:
            return "tracker", c
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


def _parse_mp_search(raw: str) -> Optional[Dict]:
    """The Marketplace-named search parse, or None.
    The tail is read with the owners' own tools:
    og_shopping's budget + condition + item cleaning,
    og_watch's place tail."""
    import og_shopping as SH
    import og_watch as W
    tail = None
    for rx in _MP_SEARCH_RES:
        m = rx.search(raw)
        if m:
            tail = m.group(1)
            break
    if tail is None:
        return None
    budget = SH._budget_of(raw)
    condition = SH._condition_of_text(tail)
    place = ""
    nm = W._PLACE_TAIL_RE.search(tail)
    if nm:
        place = " ".join(nm.group(1).split()).strip(" .,")
        tail = tail[:nm.start()]
    item = SH._clean_item(tail)
    if not item or not SH._item_tokens(item):
        return None
    if item in ("it", "one", "that", "this", "something"):
        return None
    return {"op": "mk_search", "item": item,
            "budget": budget, "place": place,
            "condition": condition, "raw": raw}


def _claim_exact(message: str, uid: str) -> Optional[Dict]:
    raw = str(message or "").strip()
    if not raw or not uid:
        return None
    if _defer_to_others(raw, uid):
        return None
    if _BOOK_RE.search(raw):
        return {"op": "mk_book", "raw": raw}
    m = _FORGET_RE.search(raw)
    if m:
        words = next(g for g in m.groups() if g)
        return {"op": "mk_forget", "words": words.strip(),
                "raw": raw}
    state = _hunt(uid)
    parsed = _parse_mp_search(raw)
    if state:
        import og_shopping as SH
        stored_toks = set(SH._item_tokens(
            state.get("item", "")))
        if parsed and set(SH._item_tokens(parsed["item"])) \
                != stored_toks:
            return parsed  # a different item = a fresh hunt
        if _CHEAPER_RE.search(raw):
            return {"op": "mk_refine", "kind": "cheaper",
                    "raw": raw}
        if _CLOSER_RE.search(raw):
            return {"op": "mk_refine", "kind": "closer",
                    "raw": raw}
        if _MORE_RE.search(raw):
            return {"op": "mk_refine", "kind": "more",
                    "raw": raw}
        budget = SH._budget_of(raw)
        if budget is not None and parsed is None:
            return {"op": "mk_refine", "kind": "budget",
                    "value": budget, "raw": raw}
        if budget is not None and parsed is not None:
            return {"op": "mk_refine", "kind": "budget",
                    "value": budget, "raw": raw}
        m = _PICK_NUM_RE.search(raw) or _PICK_HASH_RE.search(raw)
        if m:
            return {"op": "mk_pick", "index": int(m.group(1)),
                    "raw": raw}
        m = _PICK_ORD_RE.search(raw)
        if m:
            return {"op": "mk_pick",
                    "index": _ORDINALS[m.group(1).lower()],
                    "raw": raw}
        m = _PICK_NAME_RE.search(raw)
        if m:
            return {"op": "mk_pick", "index": None,
                    "match": m.group(1).lower(), "raw": raw}
        if _SHOW_RE.search(raw):
            return {"op": "mk_show", "raw": raw}
    if parsed:
        return parsed
    return None


def _claim_marketplace(message: str, uid: str):
    """(claim, garble_info): the exact claim first; on
    a miss, the Round 75 second chance — og_garble
    repairs the TRIGGER words only; the payload (the
    item, the prices, the place) passes verbatim."""
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


def install_marketplace_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search OUTSIDE the
    Tracker seam (installed last, from the og_fixqueue
    chain) so Marketplace-specialist asks claim here
    and everything else delegates back untouched. The
    search chain below the seat is parked in _harvest:
    the ops call it for RAW web results and parse them
    with og_shopping's own parser. Persona files never
    touched."""
    if getattr(agent_instance, "_og_marketplace_installed",
               False):
        return
    prev_detect = getattr(agent_instance, "detect_intent",
                          None)
    prev_search = getattr(agent_instance, "web_search",
                          None)
    if prev_detect is None or prev_search is None:
        return
    _harvest["fn"] = prev_search

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["claim"] = None
        _pending["garble"] = None
        try:
            uid = get_uid()
            if uid and marketplace_enabled():
                claim, info = _claim_marketplace(
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
            logger.warning(
                f"Marketplace trigger check failed: {e}")
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
                results = marketplace_results(claim, uid)
            except Exception as e:
                logger.warning(f"Marketplace op failed: {e}")
                results = _result(
                    _TAG, "Marketplace — glitch",
                    "That Marketplace ask glitched on my "
                    "end — nothing was searched, saved, or "
                    "sent. Say it again and I'll retry.")
        if results is None:
            results = prev_search(query, num_results)
        if garble and results:
            results = og_garble.annotate(results, garble,
                                         prepend=True)
        return results

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_marketplace_installed = True


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


def register_marketplace_routes(app):
    """GET /marketplace/status — the caller's own hunt
    summary + book count, with watch and open-approval
    counts read live off the stores that own them."""

    @app.get("/marketplace/status")
    def marketplace_status_route(request: Request):
        uid = _route_uid(request)
        view = status_for(uid) if uid else {
            "hunt": None, "book": 0, "watches": 0,
            "approvals_open": 0, "pick": None}
        return {"ok": True,
                "enabled": marketplace_enabled(), **view}
