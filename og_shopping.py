"""Round 116: THE SHOPPING HELPER (Brent's order,
2026-10-12 11:15 PDT: "Shopping helper" — specced in
round-116/SPEC.md.)

WHAT THIS IS. The crew's shopper — the specialist
face of shopping OG can already do. Audit-first, no
duplicates: this seat EXTENDS two shipped lanes and
forks neither.

  - THE WEB LANE. The agent's own web search (the
    DuckDuckGo result dicts every seat rides) is
    harvested through the seam: the seat asks the
    chain below it for raw results, parses listings
    out of them (title, price as printed, where, the
    condition as printed), and ranks them itself.
  - THE MARKETPLACE LANE. Round 54's watch + haggle
    machinery (og_watch + og_mpwatch) already keeps
    the caller's Marketplace matches ranked best-deal
    first, every price known and at or under the
    watch ceiling by construction. When one of the
    caller's own mpwatch watches covers the item, its
    matches ride into the same shortlist at the data
    level (og_watch._watches + og_mpwatch.
    ranked_matches) — no second watcher, no browser
    spin-up from here.

The audited third lane, the live-data pack
(og_monitor), is symbol / price-alert domain — it has
no product lane — so the seat does not ride it; that
is stated here rather than papered over.

WHAT IT DOES in chat: "find me a good used circular
saw under $120" runs both lanes and answers with the
locked presentation — a FEW options in range, BEST
DEAL FIRST, each with its price, where it is, and the
one-line why (cheapest of the bunch / the brand asked
for / local off your watch / under the cap). Prices
carry their source and their as-of moment: web prices
as of the harvest, watch matches as of the watch's
last check. Follow-ups refine the SAME search —
"cheaper", "only DeWalt", "under $100", "closer",
"used only" — never a fresh start. "I'll take #2"
saves the pick (the hand-off record the Buying seat
reads when it lands) and names the doors that exist
today.

THE FENCES.
- Shopping NEVER buys, checks out, or pays. There is
  no buying machinery in this module: it imports no
  trading module, no browser module, no payment
  anything, and it calls no order-placing function —
  its only contact with the ordering pack is
  claim-checking (deferring to that seat's own
  claims, the crew doctrine), never executing one.
- Shopping never messages or haggles either. Those
  crossings belong to the Round 54 chain, unchanged:
  a message ask stops at the approval ask (message
  approvals and haggle approvals are SEPARATE, the
  haggle offer is drafted at or under a ceiling the
  USER sets, and a send happens only through the
  approval-gated browser flow on the user's yes).
  The variants the R54 frames claim exactly are
  deferred to it untouched; the variants they don't
  ("text the seller", "lowball them") stop here with
  the same door named.
- A buy-shaped ask ends at the product + the honest
  line: the purchase is the user's own tap, or the
  ordering pack's prepare -> approve -> handoff chain
  when they want OG to run it as an order.
- Prices are the listings' own words, never dressed
  up: a listing with no printed price is shown as
  "price not printed", ranked after the priced ones,
  and a pass that finds nothing says it found
  nothing — no listing is ever invented.
- Per-account isolation: the search state, the pick,
  and the watch matches read are the caller's own,
  namespaced by uid at every read (drilled).
- NO new loop (R72): nothing polls, nothing ticks;
  state is read on exchanges and on the status route.
- Persona untouched; app.py never touched (install
  rides the og_fixqueue chain AFTER the Blueprint
  Specialist — this seam wraps outermost and defers
  to the PM/Filer/crew/Builder/WoodPro/MaintEng/
  Mechanic/Blueprint claims checked exactly first,
  plus the R54 marketplace claims and the ordering
  pack's claims).

STATE. Own durable store, the Round 18 pattern:
Postgres table og_shopping_data when OG_MEMORY_DB_URL
is set, else a JSON file. Blob kinds per visitor:
'search' (the current shortlist + its constraints)
and 'pick' (the saved pick). Switch:
OG_SHOPPING_ENABLED (unset = ON), the SPEC's name.
"""

import json
import logging
import os
import re
import threading
import time
import urllib.parse
from typing import Dict, List, Optional

import og_garble

from fastapi import Request

logger = logging.getLogger(__name__)

_TAG = "OG SHOPPING"

SHOW_CAP = 4           # options presented per pass
KEEP_CAP = 8           # in-range options kept in state
TITLE_CAP = 90

SHOPPING_STORE_FILE = "shopping_store.json"
_shopping_lock = threading.RLock()

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, DB_OPTIONS as _DB_OPTS, StoreError
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None


# --- Switch -----------------------------------------------------------------------


def shopping_enabled() -> bool:
    """OG_SHOPPING_ENABLED=false is the kill switch.
    Unset = ON."""
    val = os.environ.get("OG_SHOPPING_ENABLED")
    if val is not None and val.strip().lower() in (
            "0", "false", "no", "off"):
        return False
    return True


# --- Durable store (Round 18 pattern, mirroring og_blueprint) ----------------------


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=_DB_CT, options=_DB_OPTS)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_shopping_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(SHOPPING_STORE_FILE):
        try:
            with open(SHOPPING_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Shopping store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(SHOPPING_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Shopping store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_shopping_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"Shopping DB load failed: {e}")
            raise StoreError("shopping store unavailable (Round 99)") from e
    with _shopping_lock:
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
                        "INSERT INTO og_shopping_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Shopping DB save failed: {e}")
            raise StoreError("shopping store unavailable (Round 99)") from e
    with _shopping_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


# --- State: the current search + the saved pick -------------------------------------


def _search(uid: str) -> Optional[Dict]:
    got = _blob(uid, "search", None)
    return got if isinstance(got, dict) and got.get("options") else None


def _save_search(uid: str, state: Dict):
    _put_blob(uid, "search", state)


def _pick(uid: str) -> Optional[Dict]:
    got = _blob(uid, "pick", None)
    return got if isinstance(got, dict) and got.get("option") else None


def _save_pick(uid: str, pick: Dict):
    _put_blob(uid, "pick", pick)


def _result(tag: str, title: str, body: str,
            href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body,
             "href": href}]


def _money(value) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "$?"
    if f == int(f):
        return f"${int(f):,}"
    return f"${f:,.2f}"


def _asof_text(ts: float) -> str:
    return time.strftime("%I:%M %p, %b %d", time.localtime(ts))


# --- The harvest: raw web results through the seam --------------------------------
#
# install_shopping_tools parks the search chain below this
# seat in _harvest; the ops call it for RAW results and do
# their own parsing/ranking. Uninstalled (unit drills set
# it directly), the harvest is empty and the seat says so.

_harvest = {"fn": None}


def _web_harvest(query: str, num_results: int = 8) -> List[Dict]:
    fn = _harvest.get("fn")
    if fn is None:
        return []
    try:
        out = fn(query, num_results) or []
    except Exception as e:
        logger.warning(f"Shopping harvest failed: {e}")
        return []
    return [r for r in out
            if isinstance(r, dict) and "error" not in r]


# --- Listing parsing (deterministic, off the results' own words) -------------------

_PRICE_RE = re.compile(r"\$\s?([\d,]{1,7}(?:\.\d{2})?)")
_COND_RES = (
    ("refurbished", re.compile(r"\brefurbished\b|\brenewed\b", re.I)),
    ("open box", re.compile(r"\bopen[-\s]?box\b", re.I)),
    ("used", re.compile(r"\bused\b|\bpre[-\s]?owned\b", re.I)),
    ("new", re.compile(r"\bbrand[-\s]new\b|\bnew\b", re.I)),
)
_TOKEN_STOP = {"the", "a", "an", "for", "me", "my", "with",
               "and", "or", "of", "in", "on", "to", "buy"}


def _price_of(text: str) -> Optional[float]:
    m = _PRICE_RE.search(str(text or ""))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _condition_of(text: str) -> str:
    blob = str(text or "")
    for label, rx in _COND_RES:
        if rx.search(blob):
            return label
    return ""


def _item_tokens(item: str) -> List[str]:
    out = []
    for w in re.split(r"[^a-z0-9+]+", str(item or "").lower()):
        if len(w) >= 3 and w not in _TOKEN_STOP and w not in out:
            out.append(w)
    return out


def _relevant(item: str, text: str) -> bool:
    """Every content token of the item must appear in the
    listing's words — a 'table saw' is not a circular saw.
    (The deterministic limit is named in the round NOTES.)"""
    toks = _item_tokens(item)
    if not toks:
        return False
    blob = str(text or "").lower()
    return all(t in blob for t in toks)


def _where_of(href: str):
    href = str(href or "")
    try:
        parts = urllib.parse.urlparse(href)
        host = (parts.netloc or "").lower()
    except Exception:
        host = ""
    if "facebook.com" in host:
        return "mp", "Facebook Marketplace"
    if host:
        return "web", host[4:] if host.startswith("www.") else host
    return "web", "the web"


def _parse_web_option(raw: Dict, asof: str) -> Optional[Dict]:
    title = " ".join(str(raw.get("title") or "").split())[:TITLE_CAP]
    body = " ".join(str(raw.get("body") or "").split())
    if not title:
        return None
    price = _price_of(title)
    if price is None:
        price = _price_of(body)
    lane, where = _where_of(raw.get("href") or "")
    return {"title": title, "price": price, "where": where,
            "lane": lane, "href": str(raw.get("href") or ""),
            "condition": _condition_of(title + " " + body),
            "town": "", "watch_id": "", "listing_id": "",
            "asof": asof}


# --- The Marketplace lane: the caller's OWN R54 matches -----------------------------


def _marketplace_options(uid: str, item: str,
                         budget: Optional[float]):
    """(options, over_count) off the caller's mpwatch
    watches whose item shares a content token with this
    search. og_mpwatch.ranked_matches is already
    best-deal-first; prices are known by construction."""
    opts, over = [], 0
    try:
        import og_watch as W
        import og_mpwatch as MP
    except Exception:
        return opts, over
    toks = set(_item_tokens(item))
    try:
        watches = W._watches(uid)
    except Exception:
        return opts, over
    for w in watches:
        if not isinstance(w, dict) or w.get("kind") != MP.KIND:
            continue
        witem = str(w.get("item") or "")
        if not toks & set(_item_tokens(witem)):
            continue
        last = str(w.get("last_check") or "")
        asof = ("your watch's check " + last[:10]) if last \
            else "your Marketplace watch"
        try:
            matches = MP.ranked_matches(uid, w.get("id") or "")
        except Exception:
            matches = []
        for m in matches:
            if not isinstance(m, dict):
                continue
            price = m.get("price")
            if budget is not None and price is not None \
                    and float(price) > budget:
                over += 1
                continue
            opts.append({
                "title": " ".join(
                    str(m.get("title") or "").split())[:TITLE_CAP],
                "price": price, "where": "Facebook Marketplace",
                "lane": "mp", "href": str(m.get("url") or ""),
                "condition": "", "town": str(m.get("town") or ""),
                "watch_id": w.get("id") or "",
                "listing_id": m.get("id") or "", "asof": asof})
    return opts, over


# --- Ranking + the one-line why ------------------------------------------------------


def _rank(opts: List[Dict], place_pref: bool = False) -> List[Dict]:
    def key(o):
        price = o.get("price")
        price = float(price) if price is not None else float("inf")
        lane = 0 if o.get("lane") == "mp" else 1
        if place_pref:
            placed = 0 if (o.get("town") or o.get("lane") == "mp") \
                else 1
            return (placed, price, lane)
        return (price, lane)
    return sorted(opts, key=key)


def _why(o: Dict, idx: int, state: Dict) -> str:
    price = o.get("price")
    brand = str(state.get("brand") or "")
    budget = state.get("budget")
    pref = str(state.get("condition") or "")
    if idx == 0 and price is not None:
        why = "cheapest of the bunch"
    elif brand and brand.lower() in o.get("title", "").lower():
        why = f"the {brand} you asked for"
    elif o.get("lane") == "mp" and o.get("town"):
        why = f"local — {o['town']}, off your Marketplace watch"
    elif o.get("lane") == "mp":
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
            f"{o.get('where', 'the web')}")
    if o.get("town"):
        line += f" ({o['town']})"
    line += f" — {_why(o, idx, state)}."
    if o.get("href"):
        line += f"\n   {o['href']}"
    return line


_CROSSING_LINE = (
    "Pick one (\"I'll take #2\") and I'll save the pick — "
    "buying, messaging, and haggling each cross at their "
    "own approval door, never from here.")


def _present(state: Dict, lead: str = "",
             only_new: bool = False) -> str:
    opts = state.get("options") or []
    shown = state.get("shown") or []
    item = state.get("item") or "that"
    lines = []
    if lead:
        lines.append(lead)
    head = (f"Shopping — {item}, best deal first. Prices as "
            f"of {state.get('asof', 'just now')} from the web")
    if any(o.get("lane") == "mp" for o in opts):
        head += " + your Marketplace watch matches"
    head += (" — listings move, so a price is the listing's "
             "own word, not a promise.")
    lines.append(head)
    if only_new:
        # shown is always a prefix of options, so a shown
        # row's rank index is its own position.
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
    lines.append("Say \"cheaper\", \"only <brand>\", \"under "
                 "$N\", or \"closer\" to tighten the same "
                 "search.")
    lines.append(_CROSSING_LINE)
    return "\n".join(lines)


# --- The run: harvest both lanes, filter, rank, remember -----------------------------


def _run_search(uid: str, state: Dict) -> Dict:
    """Harvest with the state's constraints, apply the
    hard filters (brand, hard condition), rank, and
    return the new state (shown = the first pass)."""
    item = state["item"]
    budget = state.get("budget")
    brand = str(state.get("brand") or "")
    hard_cond = str(state.get("condition_hard") or "")
    query = " ".join(p for p in (
        brand, "used" if state.get("condition") == "used" else "",
        item, "price") if p).strip()
    now = time.time()
    asof = _asof_text(now)
    opts, over = [], 0
    for raw in _web_harvest(query, 8):
        o = _parse_web_option(raw, asof)
        if o is None:
            continue
        if not _relevant(item, o["title"] + " " +
                         str(raw.get("body") or "")):
            continue
        if o["lane"] == "mp":
            # A Marketplace URL off the web lane is still a
            # listing the user must open themselves; it is
            # NOT one of their watch's matches.
            o["lane"] = "web"
        if budget is not None and o["price"] is not None \
                and o["price"] > budget:
            over += 1
            continue
        opts.append(o)
    mp_opts, mp_over = _marketplace_options(uid, item, budget)
    opts.extend(mp_opts)
    over += mp_over
    seen = set()
    deduped = []
    for o in opts:
        key = o.get("href") or (
            o["title"].lower() + "|" + str(o.get("price")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(o)
    if brand:
        deduped = [o for o in deduped
                   if brand.lower() in o["title"].lower()]
    if hard_cond:
        deduped = [o for o in deduped
                   if str(o.get("condition") or "") == hard_cond]
    ranked = _rank(deduped, bool(state.get("place_pref")))[:KEEP_CAP]
    out = dict(state)
    out.update({"options": ranked, "shown": ranked[:SHOW_CAP],
                "over": over, "at": now, "asof": asof,
                "query": query})
    out.pop("_shown_before", None)
    return out


def _empty_body(state: Dict, tightened: str = "") -> str:
    item = state.get("item") or "that"
    bits = [f"That pass came back empty — nothing in range "
            f"for '{item}'"]
    if state.get("budget") is not None:
        bits[0] += f" under {_money(state['budget'])}"
    bits[0] += (" in the web results or your Marketplace "
                "watch matches. No listings means no listings "
                "— I won't invent one.")
    if tightened:
        bits.append(tightened)
    bits.append("Loosen the cap or widen the words and "
                "I'll run it again.")
    return " ".join(bits)


# --- Operations -----------------------------------------------------------------------


def _do_search(uid: str, claim: Dict) -> List[Dict]:
    state = {"item": claim["item"], "budget": claim.get("budget"),
             "brand": claim.get("brand", ""),
             "condition": claim.get("condition", ""),
             "condition_hard": "", "place_pref": False}
    state = _run_search(uid, state)
    _save_search(uid, state)
    if not state["options"]:
        return _result(_TAG, "Shopping — nothing in range",
                       _empty_body(state))
    lead = ""
    if claim.get("buy_frame"):
        lead = ("Buying it stays your tap or the approval "
                "doors — from here I just find it. ")
    body = lead + _present(state)
    if not any(o.get("lane") == "mp" for o in state["options"]):
        body += ("\nWant eyes on it around the clock? Say "
                 f"\"watch for a {state['item']} on "
                 "marketplace\" and the Marketplace watch "
                 "keeps matches ranked best-deal first.")
    return _result(_TAG, f"Shopping — {state['item']}", body)


def _do_refine(uid: str, claim: Dict) -> List[Dict]:
    state = _search(uid)
    if not state:
        return _result(_TAG, "Shopping — no search yet",
                       "There's no shortlist to tighten yet. "
                       "Say \"find me a ... under $N\" and "
                       "I'll build one.")
    kind = claim.get("kind")
    if kind == "more":
        opts = state.get("options") or []
        shown = state.get("shown") or []
        if len(shown) >= len(opts):
            return _result(_TAG, "Shopping — that's the lot",
                           "That's everything in range from "
                           "the last pass — nothing held back.")
        state["_shown_before"] = len(shown)
        state["shown"] = opts[:KEEP_CAP]
        _save_search(uid, state)
        body = _present(state, only_new=True)
        state.pop("_shown_before", None)
        _save_search(uid, state)
        return _result(_TAG, "Shopping — more options", body)
    trial = dict(state)
    lead = ""
    if kind == "cheaper":
        priced = [float(o["price"]) for o in state["options"]
                  if o.get("price") is not None]
        if not priced:
            return _result(
                _TAG, "Shopping — no prices to beat",
                "Nothing on the shortlist carries a price, "
                "so there's no number to go cheaper than. "
                "The list stands as shown.")
        floor = min(priced)
        new_budget = floor - 1
        if new_budget < 1:
            return _result(
                _TAG, "Shopping — already at the floor",
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
    elif kind == "brand":
        trial["brand"] = claim.get("value", "")
        lead = f"Only {trial['brand']}:"
    elif kind == "condition":
        trial["condition_hard"] = claim.get("value", "")
        trial["condition"] = claim.get("value", "")
        lead = f"Only {claim.get('value')}, then:"
    elif kind == "closer":
        trial["place_pref"] = True
        lead = ("Closer first — the listings that name a "
                "real place lead; the web ones don't print "
                "a distance, so they follow by price:")
    trial = _run_search(uid, trial)
    if not trial["options"]:
        return _result(
            _TAG, "Shopping — nothing tighter",
            _empty_body(trial,
                        "Your earlier shortlist still stands "
                        "— I didn't throw it away for an "
                        "empty pass."))
    _save_search(uid, trial)
    return _result(_TAG, "Shopping — tightened",
                   _present(trial, lead=lead))


def _pick_doors(o: Dict) -> str:
    if o.get("lane") == "mp" or "facebook" in str(
            o.get("where") or "").lower():
        return ("From here: the seller message runs the "
                "Round 54 approval chain — OG drafts the "
                "exact words and parks them, nothing sends "
                "until you approve them; a haggle is a "
                "SEPARATE approval with a ceiling you set. "
                "The purchase itself is your tap on the "
                "listing — I never pay, message, or haggle "
                "from the shopping seat.")
    return ("From here: the purchase is your own tap at "
            f"{o.get('where', 'the listing')} — I don't "
            "check out or pay. If you'd rather OG run it "
            "as an order, that's the ordering pack's "
            "prepare -> approve -> handoff chain; this "
            "seat just hands you the pick.")


def _do_pick(uid: str, claim: Dict) -> List[Dict]:
    state = _search(uid)
    if not state:
        return _result(_TAG, "Shopping — nothing to pick",
                       "There's no shortlist to pick from "
                       "yet. Say \"find me a ...\" first.")
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
            _TAG, "Shopping — which one?",
            f"The shortlist runs to #{len(shown)} — name "
            "the number or the brand and I'll save that "
            "pick.")
    pick = {"option": opt, "item": state.get("item", ""),
            "at": time.time()}
    _save_pick(uid, pick)
    price = opt.get("price")
    pstr = _money(price) if price is not None else "price not printed"
    body = (f"Saved your pick: {opt.get('title', '')} — "
            f"{pstr} at {opt.get('where', 'the web')}"
            + (f" ({opt['town']})" if opt.get("town") else "")
            + ". The pick is on your account for the "
              "buying hand-off. " + _pick_doors(opt))
    return _result(_TAG, "Shopping — pick saved", body)


def _do_show(uid: str, claim: Dict) -> List[Dict]:
    state = _search(uid)
    if not state:
        return _result(_TAG, "Shopping — no search yet",
                       "No shortlist on your account yet. "
                       "Say \"find me a ... under $N\" and "
                       "I'll build one.")
    return _result(_TAG, f"Shopping — {state.get('item', '')}",
                   _present(state))


def _do_stop(uid: str, claim: Dict) -> List[Dict]:
    kind = claim.get("kind")
    pick = _pick(uid)
    if kind == "message":
        body = ("Stopped at the ask — on purpose. I don't "
                "message sellers from the shopping seat, "
                "and nothing sends off a bare ask, ever. "
                "Seller messages run the Round 54 chain: "
                "the exact draft is written and parked for "
                "YOUR approval first — message approvals "
                "and haggle approvals are separate — and "
                "the send only happens on your yes, "
                "through the approval-gated browser flow.")
        if pick:
            body += (f" Your saved pick is "
                     f"'{pick['option'].get('title', '')}' — "
                     "it rides that chain, not a shortcut "
                     "around it.")
        return _result(_TAG, "Shopping — messaging stops "
                             "at the approval ask", body)
    if kind == "haggle":
        body = ("Haggling crosses at its own door, and "
                "it's not this seat. The Round 54 chain "
                "keeps it separate from messaging: you set "
                "a ceiling, the offer is drafted at or "
                "under it, and the draft parks for YOUR "
                "approval before anything is sent. From "
                "the shopping seat, no offer is made — "
                "ever.")
        return _result(_TAG, "Shopping — haggling stops "
                             "at its own approval", body)
    # buy
    body = ("I never buy, check out, or pay — not from "
            "this seat, not on a bare \"buy it\". ")
    if pick:
        o = pick["option"]
        body += (f"Your saved pick is "
                 f"'{o.get('title', '')}' at "
                 f"{o.get('where', 'the listing')}. "
                 + _pick_doors(o))
    else:
        body += ("Pick one from a shortlist first and "
                 "I'll save it — the purchase itself is "
                 "your own tap on the listing, or the "
                 "ordering pack's prepare -> approve -> "
                 "handoff chain when you want OG to run "
                 "it as an order.")
    return _result(_TAG, "Shopping — buying is not "
                         "this seat", body)


def shopping_results(claim: Dict, uid: str) -> List[Dict]:
    op = claim.get("op")
    if op == "sh_search":
        return _do_search(uid, claim)
    if op == "sh_refine":
        return _do_refine(uid, claim)
    if op == "sh_pick":
        return _do_pick(uid, claim)
    if op == "sh_show":
        return _do_show(uid, claim)
    if op == "sh_stop":
        return _do_stop(uid, claim)
    return _do_show(uid, claim)


def status_for(uid: str) -> Dict:
    """The read seam (/shopping/status): the caller's
    own current shortlist summary + saved pick."""
    out: Dict = {"search": None, "pick": None}
    state = _search(uid)
    if state:
        out["search"] = {
            "item": state.get("item", ""),
            "budget": state.get("budget"),
            "brand": state.get("brand", ""),
            "options": len(state.get("options") or []),
            "shown": len(state.get("shown") or []),
            "asof": state.get("asof", ""),
            "at": float(state.get("at", 0) or 0)}
    pick = _pick(uid)
    if pick:
        o = pick.get("option") or {}
        out["pick"] = {"title": o.get("title", ""),
                       "price": o.get("price"),
                       "where": o.get("where", ""),
                       "item": pick.get("item", ""),
                       "at": float(pick.get("at", 0) or 0)}
    return out


# --- Chat claims (narrow frames — the crew/PM/filer doctrine) -------------------------

_GARBLE_VOCAB = ("shopping", "shop", "find", "cheaper",
                 "closer", "seller", "haggle", "marketplace",
                 "price", "prices", "deal", "deals", "buy",
                 "shortlist")

_BUDGET_RE = re.compile(
    r"(?:under|below|less\s+than|up\s+to|max(?:imum)?|"
    r"no\s+more\s+than|<=?)\s*\$?\s*([\d,]{1,7}(?:\.\d{1,2})?)",
    re.I)
_BUDGET2_RE = re.compile(
    r"\$\s*([\d,]{1,7}(?:\.\d{1,2})?)\s*(?:or\s+less|max\b|budget)",
    re.I)
_ONLY_RE = re.compile(
    r"\bonly\s+(?:the\s+)?([a-z][a-z0-9&+.\-]*"
    r"(?:\s+[a-z][a-z0-9&+.\-]*)?)", re.I)
_BRANDS = {
    "dewalt", "milwaukee", "makita", "bosch", "ryobi",
    "craftsman", "skil", "ridgid", "kobalt", "metabo",
    "festool", "hilti", "stanley", "husky", "irwin",
    "dremel", "hart", "bauer", "worx", "einhell",
    "greenworks", "ego", "toro", "honda", "generac",
    "dyson", "shark", "ninja", "kitchenaid", "cuisinart",
    "apple", "samsung", "sony", "lg", "lego", "fisher",
}
_ONLY_TRIM = {"one", "ones", "please", "brand", "stuff"}
_COND_WORD_RE = re.compile(
    r"\b(refurbished|pre[-\s]?owned|used|new)\b", re.I)

_SEARCH_RES = [
    re.compile(r"\b(?:find|search\s+for|shop\s+for|look\s+for|"
               r"looking\s+for|hunt\s+for|get\s+me|buy\s+me|buy)\s+"
               r"(?:me\s+)?(.+)", re.I),
    re.compile(r"\bbest\s+(?:price|deal)s?\s+(?:on|for)\s+(.+)",
               re.I),
    re.compile(r"\bcheapest\s+(.+)", re.I),
    re.compile(r"\bdeals?\s+on\s+(.+)", re.I),
    re.compile(r"\bshopping\s+for\s+(.+)", re.I),
]
_BUY_VERB_RE = re.compile(r"\bbuy\b", re.I)

_CHEAPER_RE = re.compile(r"\bcheaper\b|\bless\s+expensive\b", re.I)
_CLOSER_RE = re.compile(r"\bcloser\b|\bnearer\b|\bnearby\b|"
                        r"\bnear\s+me\b", re.I)
_COND_ONLY_RE = re.compile(
    r"\b(used|new|refurbished)\s+only\b|"
    r"\bonly\s+(used|new|refurbished)\b", re.I)
_MORE_RE = re.compile(r"\bshow\s+(?:me\s+)?(?:the\s+)?rest\b|"
                      r"\bmore\s+options\b|"
                      r"\bwhat\s+else\s+(?:is\s+)?"
                      r"(?:there|you\s+got)\b", re.I)
_SHOW_RE = re.compile(r"\bshow\s+(?:me\s+)?(?:my\s+|the\s+)?"
                      r"shortlist\b|\bwhat\s+did\s+you\s+find\b|"
                      r"\bmy\s+shortlist\b|"
                      r"\bshopping\s+status\b", re.I)

_ORDINALS = {"first": 1, "second": 2, "third": 3,
             "fourth": 4, "fifth": 5}
_PICK_NUM_RE = re.compile(
    r"\b(?:take|pick|choose|grab)\s+(?:the\s+)?"
    r"(?:#|number\s+|no\.?\s*)?(\d)\b", re.I)
_PICK_HASH_RE = re.compile(r"#\s?(\d)\b")
_PICK_ORD_RE = re.compile(
    r"\bthe\s+(first|second|third|fourth|fifth)\s+one\b", re.I)
_PICK_NAME_RE = re.compile(
    r"\bthe\s+([a-z][a-z0-9&+.\-]{2,20})\s+one\b", re.I)

_MSG_STOP_RE = re.compile(
    r"\b(?:text|dm|email|contact|message|msg)\s+"
    r"(?:the\s+|that\s+)?seller\b|"
    r"\bask\s+the\s+seller\b", re.I)
_HAGGLE_STOP_RE = re.compile(
    r"\blowball\b|\btalk\s+(?:them|him|her|the\s+seller)\s+"
    r"down\b|\bnegotiat\w*\b|\bhaggle\b", re.I)
_BUY_STOP_RE = re.compile(
    r"^\s*(?:ok\s+|okay\s+|yeah\s+|so\s+)?"
    r"(?:buy|purchase|get)\s+(?:it|that|this|the\s+"
    r"(?:first|second|third|fourth|fifth|#?\d+)|#\d+)\b|"
    r"\bcheck\s?out\b|\bbuy\s+it\b", re.I)


def _budget_of(text: str) -> Optional[float]:
    for rx in (_BUDGET_RE, _BUDGET2_RE):
        m = rx.search(str(text or ""))
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                return None
    return None


def _brand_of(text: str, item: str = "") -> str:
    m = _ONLY_RE.search(str(text or ""))
    if m:
        words = [w for w in m.group(1).lower().split()
                 if w not in _ONLY_TRIM]
        if words:
            return " ".join(words[:2])
    first = (str(item or "").strip().split() or [""])[0].lower()
    if first in _BRANDS:
        return first
    return ""


def _condition_of_text(text: str) -> str:
    m = _COND_WORD_RE.search(str(text or ""))
    if not m:
        return ""
    w = m.group(1).lower().replace(" ", "-")
    if w.startswith("pre"):
        return "used"
    return w


def _clean_item(tail: str) -> str:
    t = " " + str(tail or "").lower() + " "
    t = _BUDGET_RE.sub(" ", t)
    t = _BUDGET2_RE.sub(" ", t)
    t = _ONLY_RE.sub(" ", t)
    t = re.sub(r"\b(?:on|at)\s+(?:facebook\s+)?marketplace\b",
               " ", t)
    t = re.sub(r"\bmarketplace\b", " ", t)
    t = re.sub(r"\bnear\s+me\b|\bfor\s+sale\b|\bonline\b", " ", t)
    t = re.sub(r"\brefurbished\b|\bpre[-\s]?owned\b|\bused\b|"
               r"\bbrand\s+new\b|\bnew\b|\bgood\b|\bcheap\b|"
               r"\bcheaper\b|\bbest\b|\bquality\b|\bplease\b",
               " ", t)
    t = re.sub(r"[^a-z0-9\s+.\-]", " ", t)
    words = [w for w in t.split()
             if w not in ("a", "an", "the", "for", "me", "some")]
    return " ".join(words).strip()


def _parse_search(raw: str) -> Optional[Dict]:
    """The fresh-search parse, or None. A shopping signal
    is required beyond the verb: a budget, a brand, a
    condition word, a price/deal frame, or 'for sale' /
    'marketplace' in the ask — a bare 'find me a X'
    stays the model's general lane."""
    low = str(raw or "").lower()
    tail, buy_frame = None, False
    for i, rx in enumerate(_SEARCH_RES):
        m = rx.search(low)
        if m:
            tail = m.group(1)
            buy_frame = bool(_BUY_VERB_RE.search(low))
            break
    if tail is None:
        return None
    budget = _budget_of(low)
    item = _clean_item(tail)
    if not item or not _item_tokens(item):
        return None
    if item in ("it", "one", "that", "this", "something"):
        return None
    brand = _brand_of(low, item)
    condition = _condition_of_text(tail)
    signal = (budget is not None or bool(brand)
              or bool(condition) or "for sale" in low
              or "marketplace" in low
              or "price" in low or "deal" in low
              or "cheapest" in low)
    if not signal:
        return None
    return {"op": "sh_search", "item": item, "budget": budget,
            "brand": brand, "condition": condition,
            "buy_frame": buy_frame, "raw": raw}


def _other_claim(message: str, uid: str):
    """(source, claim) of the first inner seat that claims
    this message exactly, else None. The PM, the Filer,
    the crew, the Builder, the Wood Pro, MaintEng, the
    Mechanic, the Blueprint Specialist own their asks —
    and so do the R54 marketplace chain and the ordering
    pack: checked exactly, never executed from here."""
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


def _claim_exact(message: str, uid: str) -> Optional[Dict]:
    raw = str(message or "").strip()
    if not raw or not uid:
        return None
    if _defer_to_others(raw, uid):
        return None
    low = raw.lower()
    # The crossings stop first — they need no shortlist.
    if _MSG_STOP_RE.search(raw):
        return {"op": "sh_stop", "kind": "message", "raw": raw}
    if _HAGGLE_STOP_RE.search(raw):
        return {"op": "sh_stop", "kind": "haggle", "raw": raw}
    state = _search(uid)
    parsed = _parse_search(raw)
    if state:
        stored_toks = set(_item_tokens(state.get("item", "")))
        if parsed and set(_item_tokens(parsed["item"])) \
                != stored_toks:
            return parsed  # a different item = a fresh search
        if _CHEAPER_RE.search(raw):
            return {"op": "sh_refine", "kind": "cheaper",
                    "raw": raw}
        m = _COND_ONLY_RE.search(raw)
        if m:
            val = (m.group(1) or m.group(2) or "").lower()
            return {"op": "sh_refine", "kind": "condition",
                    "value": val, "raw": raw}
        if _ONLY_RE.search(raw):
            brand = _brand_of(low)
            if brand:
                return {"op": "sh_refine", "kind": "brand",
                        "value": brand, "raw": raw}
        if _CLOSER_RE.search(raw):
            return {"op": "sh_refine", "kind": "closer",
                    "raw": raw}
        budget = _budget_of(low)
        if budget is not None and parsed is None:
            return {"op": "sh_refine", "kind": "budget",
                    "value": budget, "raw": raw}
        if budget is not None and parsed is not None:
            return {"op": "sh_refine", "kind": "budget",
                    "value": budget, "raw": raw}
        if _MORE_RE.search(raw):
            return {"op": "sh_refine", "kind": "more",
                    "raw": raw}
        m = _PICK_NUM_RE.search(raw) or _PICK_HASH_RE.search(raw)
        if m:
            return {"op": "sh_pick", "index": int(m.group(1)),
                    "raw": raw}
        m = _PICK_ORD_RE.search(raw)
        if m:
            return {"op": "sh_pick",
                    "index": _ORDINALS[m.group(1).lower()],
                    "raw": raw}
        m = _PICK_NAME_RE.search(raw)
        if m:
            return {"op": "sh_pick", "index": None,
                    "match": m.group(1).lower(), "raw": raw}
        if _SHOW_RE.search(raw):
            return {"op": "sh_show", "raw": raw}
    if parsed:
        return parsed
    if _BUY_STOP_RE.search(raw) and (
            state or _pick(uid)
            or len(raw.split()) <= 6):
        return {"op": "sh_stop", "kind": "buy", "raw": raw}
    return None


def _claim_shopping(message: str, uid: str):
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


# --- The seam --------------------------------------------------------------------------

_pending = {"claim": None, "garble": None}


def install_shopping_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search OUTSIDE the
    Blueprint Specialist seam (installed last, from the
    og_fixqueue chain) so shopping asks claim here and
    everything else delegates back untouched. The search
    chain below the seat is parked in _harvest: the ops
    call it for RAW web results and rank them
    themselves. Persona files never touched."""
    if getattr(agent_instance, "_og_shopping_installed",
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
            if uid and shopping_enabled():
                claim, info = _claim_shopping(
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
            logger.warning(f"Shopping trigger check failed: {e}")
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
                results = shopping_results(claim, uid)
            except Exception as e:
                logger.warning(f"Shopping op failed: {e}")
                results = _result(
                    _TAG, "Shopping — glitch",
                    "That ask glitched — nothing was "
                    "searched, saved, or sent. Say it again "
                    "and I'll retry.")
        if results is None:
            results = prev_search(query, num_results)
        if garble and results:
            results = og_garble.annotate(results, garble,
                                         prepend=True)
        return results

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_shopping_installed = True


# --- Routes --------------------------------------------------------------------------------


def _route_uid(request) -> str:
    """Round 95: the caller's ONE identity — the live
    session's account uid when signed in, else the
    presented cookie (og_accounts.session_uid_or)."""
    uid = ""
    try:
        uid = request.cookies.get("ogai_uid", "") or ""
    except Exception:
        uid = ""
    try:
        import og_accounts as _acct
        return _acct.session_uid_or(request.cookies, uid)
    except Exception:
        return uid


def register_shopping_routes(app):
    """GET /shopping/status — the caller's own current
    shortlist summary + saved pick."""

    @app.get("/shopping/status")
    async def shopping_status_route(request: Request):
        uid = _route_uid(request)
        view = status_for(uid) if uid else {
            "search": None, "pick": None}
        return {"ok": True, "enabled": shopping_enabled(),
                **view}
