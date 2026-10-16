"""Round 118: THE SELLING HELPER (Brent's order,
2026-10-12 11:15 PDT: "Selling helper" — specced in
round-118/SPEC.md.)

WHAT THIS IS. The crew's seller — the seat that helps
the user sell THEIR OWN stuff: pricing from real comps,
a listing draft, and buyer-message replies drafted for
approval. Audit-first, no duplicates:

  - PRICING rides the shipped finding lanes: the web
    results the agent's own search just produced,
    harvested through the seam (the same holder pattern
    the Shopping seat uses) and parsed by the Shopping
    seat's own listing parser (og_shopping.
    _parse_web_option / _relevant, read at the data
    level) — never a second search stack, never an
    invented comp.
  - MARKETPLACE stays Round 54's whole: og_mpwatch's
    watch / message / haggle chain is the BUYER side
    (finding listings to buy) and is deferred to
    exactly, untouched. There is NO poster in OG —
    this seat builds none: a listing is drafted here,
    approved here, and POSTED by the user's own hand
    on the marketplace itself. No duplicate poster.
  - The ordering pack and the Buying seat keep their
    own asks exactly; this seat defers to both.

WHAT IT DOES. "Sell my used circular saw" reads the
live comps (comparable asks on real listings: low /
middle / high, samples shown, the moment named) and
answers with a listing draft: a title, a description
built ONLY from facts the user stated (nothing is
invented — what's missing is named as worth adding,
outside the listing text), a photo plan in order
(what to photograph, in what order), the price (his
stated ask, or the recommended ask off the comps
with the reasoning shown), and the terms line —
meets / pickup / shipping are HIS call, surfaced in
the draft, never promised by OG anywhere in it.

THE FENCES — the mirror of the hunt rules, and they
bind.
- NOTHING POSTS, SENDS, OR LISTS WITHOUT THE USER'S
  APPROVAL OF THE EXACT TEXT. The draft parks as a
  live pending carrying the exact words; approval
  hands back EXACTLY those words (byte for byte) as
  the final text; any edit (a price change, a new
  draft) VOIDS a standing approval, because the
  approval was for the old words. This module imports
  no browser machinery and calls no send or post
  function: posting the listing and sending a reply
  are the user's own taps on the marketplace, always.
- THE PRICE FLOOR IS THE USER'S. He sets it ("my
  floor is $90"); OG never invents one, never moves
  one, and never sets one above his ask. An offer at
  or over the floor gets a reply drafted INSIDE the
  floor (a counter is never generated below it —
  enforced at draft generation). An offer BELOW the
  floor is never accepted and never countered: it
  PARKS AS AN ASK — flagged with the gap, waiting on
  his word. His two ways past it are his own: hold
  firm (OG drafts the firm reply AT his floor) or
  lower the floor (his word moves it, then the offer
  is re-read against the new floor). An offer with
  no floor set parks as an ask too — no floor, no
  drafted answer.
- OG NEVER COMPLETES A SALE OR TOUCHES PAYMENT.
  Approval of an acceptance draft still ends in a
  hand-off: the meet, the money, and the send are
  the user's own moves, in person / on the listing —
  the same hand-off doctrine as the Buying seat.

CLAIMS. This seam installs AFTER Buying (outermost
in the og_fixqueue chain) and defers EXACTLY first
to the PM/Filer/crew/Builder/WoodPro/MaintEng/
Mechanic/Blueprint claims, then to Shopping, then
to Buying, then to the R54 marketplace claims and
the ordering pack's claims. Bare approve/decline
words bind ONLY to a live pending held for the
caller, and only when no other flow claims the
words first (their pendings are checked exactly
and outrank this seat's — the Buying doctrine).
Per-account isolation: the draft, the listings, the
offers, and the pendings are the caller's own,
namespaced by uid at every read (drilled). NO new
loop (R72). Persona untouched; app.py never
touched. Switch: OG_SELLING_ENABLED (unset = ON),
the SPEC's name.

STATE. Own durable store, the Round 18 pattern:
Postgres table og_selling_data when OG_MEMORY_DB_URL
is set, else a JSON file; blob kinds 'listings' (the
caller's tracked listings, each with its offers and
reply drafts) and 'current' (the working listing's
id). The LIVE pending (the exact text awaiting
approval) is process-local, like the Buying seat's
card: a restart drops the pending, never the
records — the status route shows what stood.
"""

import json
import logging
import os
import re
import statistics
import threading
import time
import uuid
from typing import Dict, List, Optional

import og_garble

from fastapi import Request

logger = logging.getLogger(__name__)

_TAG = "OG SELLING"

SELLING_STORE_FILE = "selling_store.json"
_selling_lock = threading.RLock()

# The live exact-text pendings, process-local by design
# (the Buying seat's card pattern). uid -> pending.
_pendings: Dict[str, Dict] = {}

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, StoreError, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None


# --- Switch -----------------------------------------------------------------------


def selling_enabled() -> bool:
    """OG_SELLING_ENABLED=false is the kill switch.
    Unset = ON."""
    val = os.environ.get("OG_SELLING_ENABLED")
    if val is not None and val.strip().lower() in (
            "0", "false", "no", "off"):
        return False
    return True


# --- Durable store (Round 18 pattern, mirroring og_buying) -------------------------


def _db_connect():
    conn = psycopg.connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_selling_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(SELLING_STORE_FILE):
        try:
            with open(SELLING_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Selling store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(SELLING_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Selling store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_selling_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"Selling DB load failed: {e}")
            raise StoreError("selling store unavailable (Round 99)") from e
    with _selling_lock:
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
                        "INSERT INTO og_selling_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Selling DB save failed: {e}")
            raise StoreError("selling store unavailable (Round 99)") from e
    with _selling_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


# --- The listings book ---------------------------------------------------------------


def _listings(uid: str) -> List[Dict]:
    got = _blob(uid, "listings", [])
    return got if isinstance(got, list) else []


def _save_listings(uid: str, rows: List[Dict]):
    _put_blob(uid, "listings", rows)


def _current_id(uid: str) -> str:
    got = _blob(uid, "current", {})
    return str((got or {}).get("id") or "") \
        if isinstance(got, dict) else ""


def _set_current(uid: str, lid: str):
    _put_blob(uid, "current", {"id": lid})


def _find(uid: str, lid: str) -> Optional[Dict]:
    for rec in _listings(uid):
        if isinstance(rec, dict) and rec.get("id") == lid:
            return rec
    return None


def _current(uid: str) -> Optional[Dict]:
    """The working listing: the named current one if it
    still stands open, else the newest open one."""
    rows = [r for r in _listings(uid)
            if isinstance(r, dict)
            and r.get("status") not in ("sold", "closed")]
    if not rows:
        return None
    cid = _current_id(uid)
    for rec in rows:
        if rec.get("id") == cid:
            return rec
    return rows[-1]


def _upsert(uid: str, rec: Dict):
    rows = _listings(uid)
    for i, old in enumerate(rows):
        if isinstance(old, dict) and old.get("id") == rec.get("id"):
            rows[i] = rec
            _save_listings(uid, rows)
            return
    rows.append(rec)
    _save_listings(uid, rows)


# --- The live pending (the exact text awaiting approval) -------------------------------


def _park_pending(uid: str, pending: Dict):
    with _selling_lock:
        _pendings[uid] = pending


def _live_pending(uid: str) -> Optional[Dict]:
    with _selling_lock:
        got = _pendings.get(uid)
    return got if isinstance(got, dict) and got.get("text") else None


def _drop_pending(uid: str):
    with _selling_lock:
        _pendings.pop(uid, None)


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


def _amount(text: str) -> Optional[float]:
    try:
        return float(str(text).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _asof_text(ts: float) -> str:
    return time.strftime("%I:%M %p, %b %d", time.localtime(ts))


# --- The harvest: raw web results through the seam -----------------------------------
#
# install_selling_tools parks the search chain below this
# seat in _harvest; the comps read calls it for RAW results
# and parses them with the Shopping seat's own parser.
# Uninstalled (unit drills set it directly), the harvest is
# empty and the pricing says so.

_harvest = {"fn": None}


def _web_harvest(query: str, num_results: int = 8) -> List[Dict]:
    fn = _harvest.get("fn")
    if fn is None:
        return []
    try:
        out = fn(query, num_results) or []
    except Exception as e:
        logger.warning(f"Selling harvest failed: {e}")
        return []
    return [r for r in out
            if isinstance(r, dict) and "error" not in r]


# --- Comps (real listings only — parsed, counted, shown) --------------------------------


def _run_comps(item: str) -> Dict:
    """Comparable asks off live web results, parsed with
    the Shopping seat's parser: only results relevant to
    the item, only printed prices. The numbers are
    ASKING prices on live listings at the harvest moment
    — never dressed as sold prices."""
    now = time.time()
    asof = _asof_text(now)
    priced, seen = [], 0
    try:
        import og_shopping as SH
    except Exception:
        SH = None
    if SH is not None:
        query = f"{item} price"
        for raw in _web_harvest(query, 8):
            o = SH._parse_web_option(raw, asof)
            if o is None:
                continue
            if not SH._relevant(item, o["title"] + " " +
                                str(raw.get("body") or "")):
                continue
            seen += 1
            if o.get("price") is not None:
                priced.append(o)
    priced.sort(key=lambda o: float(o["price"]))
    out: Dict = {"n": len(priced), "seen": seen, "asof": asof,
                 "low": None, "median": None, "high": None,
                 "recommended": None, "samples": []}
    if priced:
        prices = [float(o["price"]) for o in priced]
        med = float(statistics.median(prices))
        out.update({
            "low": min(prices), "median": med,
            "high": max(prices),
            "recommended": float(round(med)),
            "samples": [
                {"title": o["title"], "price": float(o["price"]),
                 "where": o.get("where", "")}
                for o in priced[:3]]})
    return out


def _comps_block(comps: Dict) -> str:
    if not comps.get("n"):
        return ("Comps: I couldn't read a single comparable "
                "price off the live results just now — no "
                "comps means no recommended ask from me (I "
                "won't invent one). Name your price and the "
                "draft builds on it.")
    lines = [
        f"Comps — {comps['n']} comparable ask(s) on live "
        f"listings, as of {comps['asof']}: low "
        f"{_money(comps['low'])}, middle "
        f"{_money(comps['median'])}, high "
        f"{_money(comps['high'])}. These are ASKING prices "
        "— what sellers are asking, not what anything "
        "sold for."]
    for s in comps["samples"]:
        lines.append(f"  · {s['title']} — {_money(s['price'])}"
                     f" at {s['where']}")
    lines.append(
        f"Recommended ask: {_money(comps['recommended'])} "
        "— the middle of what's actually listed. Price "
        "toward the high end if yours is clean and you "
        "can wait; toward the low end if you want it "
        "gone fast.")
    return "\n".join(lines)


# --- Ask extraction (deterministic, off the ask's own words) ---------------------------

_PRICE_RE = re.compile(r"\$\s?([\d,]{1,7}(?:\.\d{1,2})?)")
_FLOOR_RE = re.compile(
    r"(?:my\s+)?floor\s+is\s*\$?\s*([\d,]{1,7}(?:\.\d{1,2})?)|"
    r"don'?t\s+go\s+below\s*\$?\s*([\d,]{1,7}(?:\.\d{1,2})?)|"
    r"won'?t\s+take\s+less\s+than\s*\$?\s*([\d,]{1,7}"
    r"(?:\.\d{1,2})?)|"
    r"no\s+less\s+than\s*\$?\s*([\d,]{1,7}(?:\.\d{1,2})?)|"
    r"lowest\s+(?:i'?ll\s+go|is)\s*\$?\s*([\d,]{1,7}"
    r"(?:\.\d{1,2})?)", re.I)
_OFFER_RE = re.compile(
    r"(?:buyer|someone|a\s+guy|a\s+gal|he|she|they|it)"
    r"?\s*offered\s+(?:me\s+)?\$?\s*([\d,]{1,7}"
    r"(?:\.\d{1,2})?)|"
    r"offer\s+of\s+\$?\s*([\d,]{1,7}(?:\.\d{1,2})?)|"
    r"got\s+an?\s+offer\s+(?:of\s+)?\$?\s*([\d,]{1,7}"
    r"(?:\.\d{1,2})?)", re.I)
_PRICE_SET_RE = re.compile(
    r"\bprice\s+it\s+at\s*\$?\s*([\d,]{1,7}(?:\.\d{1,2})?)|"
    r"\bset\s+the\s+price\s+to\s*\$?\s*([\d,]{1,7}"
    r"(?:\.\d{1,2})?)|"
    r"\bmake\s+(?:the\s+price|it)\s*\$?\s*([\d,]{1,7}"
    r"(?:\.\d{1,2})?)", re.I)
_COND_RE = re.compile(
    r"\b(refurbished|pre[-\s]?owned|used|new)\b", re.I)
_FACT_RES = (
    re.compile(r"\b(runs\s+(?:great|good|fine|well))\b", re.I),
    re.compile(r"\b(works\s+(?:great|good|fine|well))\b", re.I),
    re.compile(r"\b(barely\s+used)\b", re.I),
    re.compile(r"\b(like\s+new)\b", re.I),
    re.compile(r"\b(brand\s+new)\b", re.I),
    re.compile(r"\b(mint\s+condition)\b", re.I),
    re.compile(r"\b(needs\s+(?:a\s+|an\s+|some\s+)?\w+)\b", re.I),
)
_TERMS_RES = (
    ("Pickup only — as you stated it",
     re.compile(r"\bpickup\s+only\b", re.I)),
    ("Shipping offered — as you stated it",
     re.compile(r"\bcan\s+ship\b|\bwill\s+ship\b|"
                r"\bshipping\s+available\b", re.I)),
    ("Can meet — as you stated it",
     re.compile(r"\bcan\s+meet\b|\bwilling\s+to\s+meet\b",
                re.I)),
)
_TERMS_DEFAULT = ("Your call — this draft promises no "
                  "pickup, meet, or shipping terms "
                  "anywhere in it.")

_SELL_LEAD_RE = re.compile(
    r"^\s*(?:ok\s+|okay\s+|so\s+|please\s+|yeah\s+)?"
    r"(?:help\s+me\s+)?(?:sell|selling)\s+(?:my\s+|the\s+)?",
    re.I)
_WANT_SELL_RE = re.compile(
    r"\bi\s+want\s+to\s+sell\s+(?:my\s+)?", re.I)
_DRAFT_LEAD_RE = re.compile(
    r"\b(?:draft|write|make)\s+(?:a\s+|the\s+)?listing\s+"
    r"for\s+(?:my\s+)?", re.I)
_LIST_LEAD_RE = re.compile(
    r"\blist\s+my\s+", re.I)

_ITEM_STOP = {"a", "an", "the", "for", "me", "my", "on",
              "marketplace", "sale"}


def _blank_spans(text: str, spans) -> str:
    chars = list(text)
    for a, b in spans:
        for i in range(a, min(b, len(chars))):
            chars[i] = " "
    return "".join(chars)


def _first_group(m) -> Optional[str]:
    if not m:
        return None
    for g in m.groups():
        if g:
            return g
    return None


def _clean_item_tail(tail: str) -> str:
    t = " " + str(tail or "") + " "
    t = _FLOOR_RE.sub(" ", t)
    t = _PRICE_RE.sub(" ", t)
    for _label, rx in _TERMS_RES:
        t = rx.sub(" ", t)
    for rx in _FACT_RES:
        t = rx.sub(" ", t)
    t = re.sub(r"\bon\s+(?:facebook\s+)?marketplace\b", " ", t)
    t = re.sub(r"\bfor\s+sale\b", " ", t)
    t = re.sub(r"\brefurbished\b|\bpre[-\s]?owned\b|\bused\b|"
               r"\bbrand\s+new\b|\bnew\b", " ", t)
    t = re.sub(r"[^a-z0-9\s+.\-]", " ", t.lower())
    words = [w for w in t.split() if w not in _ITEM_STOP]
    return " ".join(words).strip()


def _condition_of(text: str) -> str:
    m = _COND_RE.search(str(text or ""))
    if not m:
        return ""
    w = m.group(1).lower().replace(" ", "-")
    if w.startswith("pre"):
        return "used"
    return w


def _facts_of(raw: str, condition: str) -> List[str]:
    facts = []
    if condition:
        facts.append(f"listed as {condition}")
    for rx in _FACT_RES:
        m = rx.search(str(raw or ""))
        if m:
            fact = " ".join(m.group(1).lower().split())
            if fact not in facts and \
                    not (condition and fact == "like new"
                         and condition == "new"):
                facts.append(fact)
    return facts


def _terms_of(raw: str) -> str:
    for label, rx in _TERMS_RES:
        if rx.search(str(raw or "")):
            return label
    return _TERMS_DEFAULT


_PHOTO_PLAN = [
    "1. The whole thing from the front, in daylight — "
    "the shot the listing leads with.",
    "2. Both sides and the back, so a buyer sees all "
    "of it.",
    "3. The label / model plate / serial tag, close "
    "enough to read.",
    "4. Any flaw or wear, close up — honesty in the "
    "photos sells and kills the haggle later.",
    "5. It working — plugged in, powered on, or "
    "running — if it does any of those.",
    "6. A tape measure or a common object beside it "
    "if its size isn't obvious.",
]


def _title_for(item: str, condition: str) -> str:
    base = " ".join(str(item or "").split()).title()
    if condition:
        base = f"{condition.title()} {base}"
    return base[:90]


def _description_for(rec: Dict) -> str:
    lines = [f"For sale: my {rec['item']}."]
    for fact in rec.get("facts") or []:
        lines.append(f"It is {fact}." if fact.startswith(
            "listed as") else f"{fact.capitalize()}.")
    lines.append("Message me if you want it or have a "
                 "question.")
    return "\n".join(lines)


def _exact_listing_text(rec: Dict) -> str:
    """THE exact text approval binds to — approval hands
    back these bytes, unchanged."""
    ask = rec.get("ask")
    price_line = (f"Price: {_money(ask)}"
                  if ask is not None else
                  "Price: (not set yet)")
    return "\n".join([
        f"Title: {rec['title']}",
        price_line,
        "Description:",
        _description_for(rec),
        f"Terms: {rec['terms']}",
    ])


# --- Reply drafting (fixed templates over the record's own numbers) -------------------
#
# Like R54's draft guards, but inverted: these are the SELLER's
# own words to their buyer. A counter is enforced INSIDE the
# floor at generation — below it, no draft can be built.


def _counter_for(offer: float, ask: float,
                 floor: Optional[float]) -> float:
    """The counter for an in-floor offer: the midpoint
    between the offer and the ask, never below the
    floor; a gap too small to split holds at the ask."""
    mid = float(int((offer + ask) // 2))
    if floor is not None and mid < floor:
        mid = float(floor)
    if mid <= offer:
        mid = float(ask)
    return mid


def _draft_counter(rec: Dict, offer: float,
                   counter: float) -> str:
    return (f"Appreciate the offer. I can't do "
            f"{_money(offer)} on it, but I can do "
            f"{_money(counter)} — that's my number. If "
            f"that works for you, it's yours.")


def _draft_firm(rec: Dict) -> str:
    floor = rec.get("floor")
    return (f"Appreciate it, but {_money(floor)} is my "
            f"floor on this one — I can't go under it. "
            f"If you can meet that, it's yours.")


def _draft_accept(rec: Dict, offer: float) -> str:
    return (f"That works — {_money(offer)} for the "
            f"{rec['item']}. It's yours at that price. "
            f"Tell me a public spot and a time that "
            f"suits you and I'll confirm — the meet "
            f"and the money stay between us in person.")


def _draft_available(rec: Dict) -> str:
    ask = rec.get("ask")
    if ask is not None:
        return (f"Yep, still available. It's listed at "
                f"{_money(ask)} — let me know if you "
                f"want it.")
    return ("Yep, still available — let me know if you "
            "want it.")


def _draft_fact(rec: Dict, fact: str) -> str:
    base = f"Good question — it {fact}." \
        if not fact.startswith("listed as") else \
        f"Good question — it's {fact}."
    ask = rec.get("ask")
    if ask is not None:
        base += (f" It's listed at {_money(ask)} if "
                 f"you're interested.")
    return base


def _draft_checkback(rec: Dict) -> str:
    return ("Good question — let me check on that and "
            "get right back to you.")


_REPLY_NOTE = (
    "That draft is parked for YOUR approval — nothing "
    "sends from me, ever: approve it and I'll hand you "
    "the exact words to send from your listing's "
    "messages. Meets, pickup, and payment are your "
    "call — the draft promises none of it, so edit "
    "any word before you approve.")


def _park_reply(uid: str, rec: Dict, buyer_text: str,
                draft_text: str, note: str = "") -> List[Dict]:
    replies = rec.get("replies") or []
    replies.append({"buyer": buyer_text[:200],
                    "text": draft_text, "status": "pending",
                    "at": time.time()})
    rec["replies"] = replies
    _upsert(uid, rec)
    _park_pending(uid, {"kind": "reply", "lid": rec["id"],
                        "text": draft_text})
    body = (f"Drafted reply to your buyer"
            + (f" (they said: \"{buyer_text[:120]}\""
               if buyer_text else "")
            + ("):" if buyer_text else ":")
            + f"\n\"{draft_text}\"\n" + _REPLY_NOTE)
    if note:
        body += "\n" + note
    return _result(_TAG, "Selling — reply drafted", body)


# --- Operations -------------------------------------------------------------------------


def _missing_listing_body() -> str:
    return ("There's no listing standing on your "
            "account to work on. Start one — \"sell my "
            "<thing>\" — and I'll price it off the live "
            "comps and draft the listing.")


def _do_draft(uid: str, claim: Dict) -> List[Dict]:
    raw = claim["raw"]
    item = claim["item"]
    comps = _run_comps(item)
    ask = claim.get("ask")
    ask_note = ""
    if ask is None and comps.get("recommended") is not None:
        ask = comps["recommended"]
        ask_note = " (the recommended ask off the comps)"
    condition = claim.get("condition", "")
    floor_note = ""
    floor = claim.get("floor")
    if floor is not None and ask is not None \
            and floor > float(ask):
        floor_note = (f"Heads-up: the floor you named "
                      f"({_money(floor)}) sits above the "
                      f"ask ({_money(float(ask))}), so I "
                      "didn't set it — a floor over the "
                      "price makes no sense. Set it again "
                      "under the ask.")
        floor = None
    rec = {
        "id": uuid.uuid4().hex[:12],
        "item": item,
        "title": _title_for(item, condition),
        "facts": _facts_of(raw, condition),
        "condition": condition,
        "ask": ask,
        "floor": floor,
        "terms": _terms_of(raw),
        "comps": {"n": comps.get("n", 0),
                  "low": comps.get("low"),
                  "median": comps.get("median"),
                  "high": comps.get("high"),
                  "recommended": comps.get("recommended"),
                  "asof": comps.get("asof", "")},
        "status": "draft",
        "offers": [], "replies": [],
        "at": time.time(), "updated": time.time(),
    }
    rec["exact_text"] = _exact_listing_text(rec)
    _upsert(uid, rec)
    _set_current(uid, rec["id"])
    old = _live_pending(uid)
    _park_pending(uid, {"kind": "listing", "lid": rec["id"],
                        "text": rec["exact_text"]})
    lines = []
    if old:
        lines.append("The old pending is dropped — "
                     "here's the new one.")
    lines.append(_comps_block(comps))
    if floor_note:
        lines.append(floor_note)
    if rec.get("floor") is not None:
        lines.append(f"Floor set at {_money(rec['floor'])}"
                     " — offers under it park as an ask, "
                     "never an answer.")
    lines.append("THE EXACT LISTING — nothing posts "
                 "without your approval of these words:")
    lines.append(rec["exact_text"])
    lines.append("Photos, in this order:")
    lines.extend("  " + p for p in _PHOTO_PLAN)
    missing_facts = [f for f in (
        "whether it works / runs, in your own words",
        "the model or size, if it has one",
        "why you're selling, if it's a good story")
        if True]
    lines.append("Worth adding before you post (NOT in "
                 "the listing — I don't invent facts): "
                 + "; ".join(missing_facts) + ". Tell me "
                 "any of them and I'll fold them in and "
                 "re-show the exact text.")
    if ask is None:
        lines.append("Price: not set — no comps to read "
                     "and no price named. Say \"price it "
                     "at $N\" and the draft carries it.")
    elif ask_note:
        lines.append(f"Asking {_money(ask)}{ask_note}. "
                     "Change it any time — \"price it "
                     "at $N\".")
    if rec.get("floor") is None:
        lines.append("No floor set. Set one — \"my "
                     "floor is $N\" — and offers below "
                     "it park as an ask instead of "
                     "getting answered.")
    lines.append("Say \"approve\" and I'll hand you "
                 "these exact words to post from your "
                 "listing — posting is your tap; I never "
                 "post it myself. Say \"decline\" and the "
                 "pending drops (the draft stands, "
                 "unposted).")
    return _result(_TAG, f"Selling — draft for {item}",
                   "\n".join(lines))


def _do_post(uid: str, claim: Dict) -> List[Dict]:
    rec = _current(uid)
    if not rec:
        return _result(_TAG, "Selling — nothing to post",
                       _missing_listing_body())
    if rec.get("status") == "approved":
        return _result(
            _TAG, "Selling — already approved",
            "That listing already stands approved — "
            "the exact words are in its hand-off above, "
            "and posting is your tap on the listing. "
            "Say \"mark it posted\" once it's live and "
            "I'll track it as posted.")
    text = _exact_listing_text(rec)
    rec["exact_text"] = text
    rec["updated"] = time.time()
    _upsert(uid, rec)
    _park_pending(uid, {"kind": "listing", "lid": rec["id"],
                        "text": text})
    return _result(
        _TAG, "Selling — the exact words, for your OK",
        "Nothing posts without your approval of the "
        "EXACT text — here it is, word for word:\n"
        + text
        + "\nSay \"approve\" and I'll hand you these "
          "same words as the final text to post from "
          "your listing (posting is your tap — I never "
          "post it myself). Say \"decline\" and nothing "
          "posts; the draft stands.")


def _do_floor(uid: str, claim: Dict) -> List[Dict]:
    rec = _current(uid)
    if not rec:
        return _result(_TAG, "Selling — no listing yet",
                       _missing_listing_body())
    floor = claim["floor"]
    ask = rec.get("ask")
    if ask is not None and floor > float(ask):
        return _result(
            _TAG, "Selling — floor above the ask",
            f"That floor ({_money(floor)}) sits ABOVE "
            f"your ask ({_money(ask)}) — a floor over "
            "the price makes no sense, so I didn't set "
            "it. Raise the ask or lower the floor and "
            "say it again.")
    rec = dict(rec)
    rec["floor"] = floor
    rec["updated"] = time.time()
    _upsert(uid, rec)
    lines = [f"Floor set: {_money(floor)} on "
             f"'{rec['title']}'. Offers under it park as "
             "an ask — never accepted, never countered "
             "under it. Only your word moves it."]
    # A parked below-floor offer is re-read against the
    # new floor at once — his word is what unblocks it.
    parked = [o for o in rec.get("offers") or []
              if o.get("outcome") in ("parked_below_floor",
                                      "parked_no_floor")]
    if parked:
        latest = parked[-1]
        offer = float(latest["amount"])
        if offer >= floor:
            lines.append(
                f"That unblocks the parked "
                f"{_money(offer)} offer — it reads "
                "inside the floor now. Here it is as a "
                "draft:")
            _save_listings(uid, _listings(uid))
            return _offer_draft(uid, rec, latest, lines)
        lines.append(
            f"The parked {_money(float(latest['amount']))}"
            " offer still sits under the new floor — it "
            "stays parked as an ask.")
    return _result(_TAG, "Selling — floor set",
                   "\n".join(lines))


def _offer_record(rec: Dict, offer: float,
                  outcome: str) -> Dict:
    rec2 = dict(rec)
    offers = list(rec2.get("offers") or [])
    entry = {"amount": offer, "at": time.time(),
             "outcome": outcome, "counter": None}
    offers.append(entry)
    rec2["offers"] = offers
    rec2["updated"] = time.time()
    return rec2, entry


def _offer_draft(uid: str, rec: Dict, entry: Dict,
                 lead_lines: List[str]) -> List[Dict]:
    """Draft the reply for an offer that reads at/over
    the floor: acceptance when it meets the ask, a
    counter inside the floor otherwise."""
    offer = float(entry["amount"])
    ask = rec.get("ask")
    floor = rec.get("floor")
    if ask is not None and offer >= float(ask):
        draft = _draft_accept(rec, offer)
        kind = "accepted_draft"
        counter = None
    else:
        counter = _counter_for(offer, float(ask),
                               floor)
        if floor is not None and counter < float(floor):
            # Enforced at generation: no draft can exist
            # below the floor. (Unreachable by the
            # midpoint rule; the guard stands anyway.)
            return _result(
                _TAG, "Selling — can't draft under the "
                      "floor",
                "That offer can't be answered inside "
                "your floor, so no draft exists for it. "
                "It stays parked as an ask.")
        draft = _draft_counter(rec, offer, counter)
        kind = "counter_draft"
    rec2, entry2 = rec, entry
    rows = _listings(uid)
    for i, old in enumerate(rows):
        if old.get("id") == rec.get("id"):
            offers = list(old.get("offers") or [])
            for j, o in enumerate(offers):
                if o is entry or o == entry:
                    offers[j] = {**o, "outcome": kind,
                                 "counter": counter}
                    entry2 = offers[j]
                    break
            old = {**old, "offers": offers,
                   "updated": time.time()}
            rows[i] = old
            rec2 = old
            break
    _save_listings(uid, rows)
    lead = list(lead_lines)
    lead.append(f"Offer on the table: {_money(offer)}"
                + (f" against your {_money(float(ask))} ask"
                   if ask is not None else "")
                + (f", floor {_money(float(floor))}"
                   if floor is not None else "")
                + " — inside the lines, so here's the "
                  "reply, drafted:")
    return _park_reply(uid, rec2, "", draft,
                       note="\n".join(lead))


def _do_offer(uid: str, claim: Dict) -> List[Dict]:
    rec = _current(uid)
    if not rec:
        return _result(_TAG, "Selling — no listing yet",
                       _missing_listing_body())
    offer = claim["offer"]
    ask = rec.get("ask")
    floor = rec.get("floor")
    if ask is None:
        rec, _e = _offer_record(rec, offer, "no_price")
        _upsert(uid, rec)
        return _result(
            _TAG, "Selling — offer noted, no price yet",
            f"Noted the {_money(offer)} offer — but "
            "this listing has no price set yet, so "
            "there's nothing to read it against. Set "
            "the price (\"price it at $N\") and a floor "
            "and I'll read it properly.")
    if floor is None:
        rec, _e = _offer_record(rec, offer, "parked_no_floor")
        _upsert(uid, rec)
        return _result(
            _TAG, "Selling — offer parked (no floor)",
            f"That {_money(offer)} offer is PARKED as "
            f"an ask — you haven't set a floor on "
            f"'{rec['title']}' (ask {_money(float(ask))}), "
            "and with no floor I draft nothing. Set one "
            "— \"my floor is $N\" — and I'll read the "
            "offer against it right away.")
    if offer < float(floor):
        rec, _e = _offer_record(rec, offer,
                                "parked_below_floor")
        _upsert(uid, rec)
        return _result(
            _TAG, "Selling — below your floor, parked",
            f"That {_money(offer)} offer is "
            f"{_money(float(floor) - offer)} UNDER your "
            f"floor of {_money(float(floor))} — so it's "
            "parked as an ask: not accepted, not "
            "countered, nothing drafted. Your calls: "
            "say \"hold firm\" and I'll draft the firm "
            "reply at your floor for your approval, or "
            "lower the floor with \"my floor is $N\" "
            "and I'll re-read the offer against it.")
    rec, entry = _offer_record(rec, offer, "read")
    _upsert(uid, rec)
    return _offer_draft(uid, rec, entry, [])


def _do_hold(uid: str, claim: Dict) -> List[Dict]:
    rec = _current(uid)
    if not rec:
        return _result(_TAG, "Selling — no listing yet",
                       _missing_listing_body())
    floor = rec.get("floor")
    parked = [o for o in rec.get("offers") or []
              if o.get("outcome") == "parked_below_floor"]
    if floor is None or not parked:
        return _result(
            _TAG, "Selling — nothing parked to hold on",
            "There's no below-floor offer parked on "
            "this listing, so there's nothing to hold "
            "firm against. Offers under your floor "
            "park as asks; that's when 'hold firm' "
            "drafts the reply.")
    draft = _draft_firm(rec)
    lead = (f"Holding firm at your floor "
            f"({_money(float(floor))}) against the "
            f"parked {_money(float(parked[-1]['amount']))}"
            " offer — the reply, drafted at the floor, "
            "never under it:")
    return _park_reply(uid, rec,
                       "", draft, note=lead)


def _do_reply(uid: str, claim: Dict) -> List[Dict]:
    rec = _current(uid)
    if not rec:
        return _result(_TAG, "Selling — no listing yet",
                       _missing_listing_body())
    text = str(claim.get("buyer_text") or "").strip()
    if not text:
        return _result(
            _TAG, "Selling — what did they say?",
            "Tell me what the buyer said and I'll draft "
            "the reply — like: buyer said: is this "
            "still available? Their words are the "
            "payload; I draft off them and off what's "
            "on your listing, nothing invented.")
    # A price inside the buyer's words IS an offer
    # report — it reads against the floor like one.
    m = _PRICE_RE.search(text)
    if m and re.search(r"\b(take|offer|give|pay)\b",
                        text, re.I):
        offer = _amount(m.group(1))
        if offer is not None:
            return _do_offer(uid, {"op": "se_offer",
                                   "offer": offer,
                                   "raw": claim["raw"]})
    low = text.lower()
    if re.search(r"\b(available|still\s+have|left)\b", low):
        return _park_reply(uid, rec, text,
                           _draft_available(rec))
    # A question the listing's stated facts answer can
    # be drafted FROM those facts — and only those.
    for fact in rec.get("facts") or []:
        words = [w for w in re.findall(r"[a-z]{3,}", fact)
                 if w not in ("listed", "great", "good",
                              "fine", "well")]
        cands = set(words)
        for w in words:
            if w.endswith("s") and len(w) > 3:
                cands.add(w[:-1])
        if cands and any(w in low for w in cands):
            return _park_reply(uid, rec, text,
                               _draft_fact(rec, fact))
    note = ("Heads-up: that asks something your "
            "listing doesn't state, so the draft "
            "doesn't claim an answer — it buys you the "
            "beat to check. If you know it, tell me "
            "the fact and I'll redraft with it in.")
    return _park_reply(uid, rec, text, _draft_checkback(rec),
                       note=note)


def _do_price(uid: str, claim: Dict) -> List[Dict]:
    rec = _current(uid)
    if not rec:
        return _result(_TAG, "Selling — no listing yet",
                       _missing_listing_body())
    price = claim["price"]
    rec = dict(rec)
    rec["ask"] = price
    rec["updated"] = time.time()
    voided = rec.get("status") == "approved"
    if voided:
        rec["status"] = "draft"
    rec["exact_text"] = _exact_listing_text(rec)
    _upsert(uid, rec)
    pend = _live_pending(uid)
    if pend and pend.get("kind") == "listing" \
            and pend.get("lid") == rec["id"]:
        _park_pending(uid, {"kind": "listing",
                            "lid": rec["id"],
                            "text": rec["exact_text"]})
    lines = [f"Price set: {_money(price)} on "
             f"'{rec['title']}'."]
    if rec.get("floor") is not None \
            and price <= float(rec["floor"]):
        lines.append(
            f"Heads-up: that sits at or under your "
            f"{_money(float(rec['floor']))} floor — "
            "offers now read against the ask first. "
            "Say the word if the floor should move "
            "too; I never move it myself.")
    if voided:
        lines.append("That CHANGED the exact words, so "
                     "the old approval is void — approve "
                     "the new text fresh before it "
                     "posts. Here it is:")
        lines.append(rec["exact_text"])
        _park_pending(uid, {"kind": "listing",
                            "lid": rec["id"],
                            "text": rec["exact_text"]})
    elif pend and pend.get("kind") == "listing":
        lines.append("The pending text now carries "
                     "the new price — approval binds to "
                     "the updated words.")
    return _result(_TAG, "Selling — price set",
                   "\n".join(lines))


def _do_approve(uid: str, claim: Dict) -> List[Dict]:
    pend = _live_pending(uid)
    if not pend:
        return _result(
            _TAG, "Selling — nothing waiting",
            "There's no exact text waiting on your "
            "approval — nothing was posted and nothing "
            "was sent (nothing ever is from me).")
    rec = _find(uid, str(pend.get("lid") or ""))
    _drop_pending(uid)
    if pend.get("kind") == "listing":
        if rec is not None:
            rec = dict(rec)
            rec["status"] = "approved"
            rec["updated"] = time.time()
            _upsert(uid, rec)
        return _result(
            _TAG, "Selling — approved, your words to post",
            "Approved — here are the EXACT words, "
            "unchanged, ready to post:\n" + pend["text"]
            + "\nPost them from your listing on the "
              "marketplace — your tap posts it; I never "
              "post it myself, and OG never completes "
              "the sale or touches the money. Once it's "
              "live, say \"mark it posted\" and I'll "
              "track it as posted.")
    # A reply: hand back the exact draft to send.
    if rec is not None:
        rec = dict(rec)
        replies = list(rec.get("replies") or [])
        for i, r in enumerate(replies):
            if r.get("text") == pend["text"] \
                    and r.get("status") == "pending":
                replies[i] = {**r, "status": "approved"}
                break
        rec["replies"] = replies
        rec["updated"] = time.time()
        _upsert(uid, rec)
    return _result(
        _TAG, "Selling — approved, your words to send",
        "Approved — the exact reply, unchanged:\n\""
        + pend["text"]
        + "\"\nSend it from your listing's messages — "
          "your tap sends it; I never send it myself. "
          "The meet and the money stay yours, in "
          "person.")


def _do_decline(uid: str, claim: Dict) -> List[Dict]:
    pend = _live_pending(uid)
    if not pend:
        return _result(
            _TAG, "Selling — nothing to decline",
            "There's no exact text waiting, so there's "
            "nothing to drop. Nothing was posted and "
            "nothing was sent (nothing ever is from me).")
    rec = _find(uid, str(pend.get("lid") or ""))
    _drop_pending(uid)
    if pend.get("kind") == "reply" and rec is not None:
        rec = dict(rec)
        replies = list(rec.get("replies") or [])
        for i, r in enumerate(replies):
            if r.get("text") == pend["text"] \
                    and r.get("status") == "pending":
                replies[i] = {**r, "status": "declined"}
                break
        rec["replies"] = replies
        rec["updated"] = time.time()
        _upsert(uid, rec)
        return _result(
            _TAG, "Selling — reply dropped",
            "Dropped — that reply draft is dead and "
            "nothing was sent. Say the buyer's words "
            "again any time and I'll redraft.")
    return _result(
        _TAG, "Selling — posting dropped",
        "Dropped — that exact text will not post. The "
        "draft itself stands, unposted: change the "
        "price or the floor, or say \"post it\" when "
        "the words are right and I'll show them for "
        "a fresh approval.")


def _do_mark(uid: str, claim: Dict) -> List[Dict]:
    rec = _current(uid)
    if not rec:
        return _result(_TAG, "Selling — no listing yet",
                       _missing_listing_body())
    rec = dict(rec)
    if claim["op"] == "se_posted":
        rec["status"] = "posted"
        rec["updated"] = time.time()
        _upsert(uid, rec)
        return _result(
            _TAG, "Selling — marked posted",
            f"Marked posted: '{rec['title']}'"
            + (f" at {_money(float(rec['ask']))}"
               if rec.get("ask") is not None else "")
            + ". Offers that come in — tell me the "
              "number and I'll read it against your "
              "floor.")
    price = claim.get("price")
    rec["status"] = "sold"
    rec["sold_price"] = price
    rec["updated"] = time.time()
    _upsert(uid, rec)
    _drop_pending(uid)
    return _result(
        _TAG, "Selling — marked sold",
        f"Marked sold: '{rec['title']}'"
        + (f" for {_money(price)}" if price is not None
           else "")
        + ". The money changed hands your way, in "
          "person — OG never touched it. The listing "
          "closes on your book.")


def _do_show(uid: str, claim: Dict) -> List[Dict]:
    rows = _listings(uid)
    if not rows:
        return _result(
            _TAG, "Selling — nothing on the book",
            "No listings on your account yet. Say "
            "\"sell my <thing>\" and I'll price it off "
            "the comps and draft the listing.")
    lines = ["Your listings:"]
    for rec in rows:
        ask = rec.get("ask")
        floor = rec.get("floor")
        offers = rec.get("offers") or []
        parked = [o for o in offers
                  if str(o.get("outcome", "")).startswith(
                      "parked")]
        lines.append(
            f"- {rec.get('title', '')} — status: "
            f"{rec.get('status', '')}"
            + (f", ask {_money(float(ask))}"
               if ask is not None else ", no price set")
            + (f", floor {_money(float(floor))}"
               if floor is not None else ", no floor")
            + (f", {len(offers)} offer(s)"
               if offers else "")
            + (f", {len(parked)} parked as ask(s)"
               if parked else ""))
    pend = _live_pending(uid)
    if pend:
        lines.append(
            f"One exact text is waiting on your "
            f"approval (a {pend.get('kind')}) — say "
            "\"approve\" or \"decline\".")
    return _result(_TAG, "Selling — your listings",
                   "\n".join(lines))


def selling_results(claim: Dict, uid: str) -> List[Dict]:
    op = claim.get("op")
    if op == "se_draft":
        return _do_draft(uid, claim)
    if op == "se_post":
        return _do_post(uid, claim)
    if op == "se_floor":
        return _do_floor(uid, claim)
    if op == "se_offer":
        return _do_offer(uid, claim)
    if op == "se_hold":
        return _do_hold(uid, claim)
    if op == "se_reply":
        return _do_reply(uid, claim)
    if op == "se_price":
        return _do_price(uid, claim)
    if op == "se_approve":
        return _do_approve(uid, claim)
    if op == "se_decline":
        return _do_decline(uid, claim)
    if op in ("se_posted", "se_sold"):
        return _do_mark(uid, claim)
    return _do_show(uid, claim)


def status_for(uid: str) -> Dict:
    """The read seam (/selling/status): the caller's own
    working listing + book summary + whether an exact
    text is waiting on approval."""
    out: Dict = {"listing": None, "listings": 0,
                 "offers_open": 0, "pending": None}
    rows = _listings(uid)
    out["listings"] = len(rows)
    for rec in rows:
        for o in rec.get("offers") or []:
            if str(o.get("outcome", "")).startswith("parked"):
                out["offers_open"] += 1
    rec = _current(uid)
    if rec:
        out["listing"] = {
            "id": rec.get("id", ""),
            "item": rec.get("item", ""),
            "title": rec.get("title", ""),
            "status": rec.get("status", ""),
            "ask": rec.get("ask"),
            "floor": rec.get("floor"),
            "offers": len(rec.get("offers") or []),
            "at": float(rec.get("at", 0) or 0)}
    pend = _live_pending(uid)
    if pend:
        out["pending"] = pend.get("kind")
    return out


# --- Chat claims (narrow frames — the crew/PM/filer doctrine) -------------------------

_GARBLE_VOCAB = ("selling", "sell", "listing", "list",
                 "buyer", "floor", "offer", "price",
                 "post", "sold", "marketplace", "draft")

_APPROVE_RE = re.compile(
    r"^\W*(yes|yeah|yep|yup|approve|approved|go ahead|"
    r"do it|confirm|ok|okay|sounds good|let'?s go)\b")
_DECLINE_RE = re.compile(
    r"^\W*(no|nope|nah|decline|declined|cancel|scrap|"
    r"discard|never ?mind|stop|don'?t|do not)\b")
_STATUS_RE = re.compile(
    r"\bselling\s+status\b|\bmy\s+listings\b|"
    r"\bwhat\s+am\s+i\s+selling\b|"
    r"\bshow\s+(?:me\s+)?my\s+listings\b", re.I)
_POST_RE = re.compile(
    r"^\s*(?:ok\s+|okay\s+|so\s+)?post\s+it\b|"
    r"\blist\s+it\b|"
    r"\bgo\s+ahead\s+and\s+(?:post|list)\b", re.I)
_POSTED_RE = re.compile(
    r"\bmark\s+(?:it\s+)?posted\b|\bit'?s\s+posted\b|"
    r"\bi\s+posted\s+it\b", re.I)
_SOLD_RE = re.compile(
    r"\bmark\s+(?:it|the\s+\w+)\s+(?:as\s+)?sold\b|"
    r"\bit\s+sold\b|\bsold\s+it\b", re.I)
_HOLD_RE = re.compile(
    r"\bhold\s+firm\b|\bstay\s+firm\b|\bhold\s+the\s+"
    r"floor\b", re.I)
_BUYER_RE = re.compile(
    r"\bbuyer\s+(?:said|says|asked|wrote|messaged|"
    r"replied)\s*[:\-]?\s*(.*)$", re.I)
_REPLY_RE = re.compile(
    r"\b(?:reply|respond|answer)\s+to\s+the\s+buyer\b"
    r"\s*[:\-]?\s*(.*)$", re.I)


def _other_claim(message: str, uid: str):
    """(source, claim) of the first inner seat that
    claims this message exactly, else None. The PM,
    the Filer, the crew, the Builder, the Wood Pro,
    MaintEng, the Mechanic, the Blueprint Specialist
    own their asks — and so do Shopping, Buying, the
    R54 marketplace chain, and the ordering pack:
    checked exactly, never executed from here."""
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


def _parse_sell_start(raw: str) -> Optional[Dict]:
    """A sell-shaped ask: (item, stated ask, stated
    floor, condition) or None. A stated price AND a
    named store is the BUYING seat's direct door, never
    a sell — this parse only fires on sell verbs."""
    tail = None
    for rx in (_SELL_LEAD_RE, _WANT_SELL_RE,
               _DRAFT_LEAD_RE, _LIST_LEAD_RE):
        m = rx.search(raw)
        if m:
            tail = raw[m.end():]
            break
    if tail is None:
        return None
    floor = None
    fm = _FLOOR_RE.search(raw)
    if fm:
        floor = _amount(_first_group(fm) or "")
    ask = None
    work = _blank_spans(raw, [fm.span()] if fm else [])
    pm = _PRICE_RE.search(work)
    if pm:
        ask = _amount(pm.group(1))
    item = _clean_item_tail(tail)
    if not item:
        return None
    try:
        import og_shopping as SH
        if not SH._item_tokens(item):
            return None
    except Exception:
        pass
    return {"op": "se_draft", "item": item, "ask": ask,
            "floor": floor,
            "condition": _condition_of(raw), "raw": raw}


def _claim_exact(message: str, uid: str) -> Optional[Dict]:
    raw = str(message or "").strip()
    if not raw or not uid:
        return None
    pend = _live_pending(uid)
    if pend is not None:
        # A live pending stands: approve/decline bind to
        # it — but only when no other flow claims the
        # words first (their pendings outrank this one).
        trimmed = raw.lower().strip()
        is_approve = bool(_APPROVE_RE.match(trimmed))
        is_decline = bool(_DECLINE_RE.match(trimmed))
        if (is_approve or is_decline) \
                and not _defer_to_others(raw, uid):
            return {"op": "se_approve" if is_approve
                    else "se_decline", "raw": raw}
    if _defer_to_others(raw, uid):
        return None
    if _STATUS_RE.search(raw):
        return {"op": "se_show", "raw": raw}
    if _POSTED_RE.search(raw):
        return {"op": "se_posted", "raw": raw}
    m = _SOLD_RE.search(raw)
    if m:
        pm = _PRICE_RE.search(raw)
        return {"op": "se_sold",
                "price": _amount(pm.group(1)) if pm else None,
                "raw": raw}
    if _POST_RE.search(raw):
        return {"op": "se_post", "raw": raw}
    fm = _FLOOR_RE.search(raw)
    if fm:
        return {"op": "se_floor",
                "floor": _amount(_first_group(fm) or ""),
                "raw": raw}
    if _HOLD_RE.search(raw):
        return {"op": "se_hold", "raw": raw}
    om = _OFFER_RE.search(raw)
    if om:
        return {"op": "se_offer",
                "offer": _amount(_first_group(om) or ""),
                "raw": raw}
    bm = _BUYER_RE.search(raw) or _REPLY_RE.search(raw)
    if bm:
        return {"op": "se_reply",
                "buyer_text": (bm.group(1) or "").strip(),
                "raw": raw}
    pm2 = _PRICE_SET_RE.search(raw)
    if pm2:
        return {"op": "se_price",
                "price": _amount(_first_group(pm2) or ""),
                "raw": raw}
    return _parse_sell_start(raw)


def _claim_selling(message: str, uid: str):
    """(claim, garble_info): the exact claim first; on
    a miss, the Round 75 second chance — og_garble
    repairs the TRIGGER words only; the payload (the
    prices, the buyer's words) passes verbatim."""
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


def install_selling_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search OUTSIDE the
    Buying seam (installed last, from the og_fixqueue
    chain) so selling asks claim here and everything
    else delegates back untouched. The search chain
    below the seat is parked in _harvest: the comps
    read calls it for RAW web results and parses them
    with the Shopping seat's parser. Persona files
    never touched."""
    if getattr(agent_instance, "_og_selling_installed",
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
            if uid and selling_enabled():
                claim, info = _claim_selling(
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
            logger.warning(f"Selling trigger check failed: {e}")
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
                results = selling_results(claim, uid)
            except Exception as e:
                logger.warning(f"Selling op failed: {e}")
                results = _result(
                    _TAG, "Selling — glitch",
                    "That ask glitched — nothing was "
                    "drafted, posted, or sent. Say it "
                    "again and I'll retry.")
        if results is None:
            results = prev_search(query, num_results)
        if garble and results:
            results = og_garble.annotate(results, garble,
                                         prepend=True)
        return results

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_selling_installed = True


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


def register_selling_routes(app):
    """GET /selling/status — the caller's own working
    listing + book summary + pending state."""

    @app.get("/selling/status")
    def selling_status_route(request: Request):
        uid = _route_uid(request)
        view = status_for(uid) if uid else {
            "listing": None, "listings": 0,
            "offers_open": 0, "pending": None}
        return {"ok": True, "enabled": selling_enabled(),
                **view}
