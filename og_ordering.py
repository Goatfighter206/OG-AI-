"""
OG AI online ordering (Round 19) — PREPARE -> APPROVE -> HANDOFF,
Brent's standing rule (OG prepares, the owner approves, then it
executes) in the only shape v1 can honestly take: most food and
retail ordering has NO public consumer ordering API, so OG never
submits an order and never touches a payment. The flow, all inside
the visitor's own chat thread:

1. ASK. "order me a large pepperoni from Domino's", "order a
   Whopper combo", "build my grocery order" — a store and at least
   one item are required. When either is missing, ONE clarifying
   question asks for just the missing piece; nothing is drafted
   until both are known.
2. DRAFT. OG assembles the order and checks each item's price
   through the Round 3 lookup seam. A price is shown ONLY when a
   lookup result actually grounds it; anything not found is
   listed as "price not confirmed — the store will show it". The
   draft is parked as a per-visitor pending order (60-minute
   expiry) and shown as an ORDER PREVIEW: items, quantities, the
   grounded prices, and an estimated total ONLY when every item
   is priced (otherwise "Total: the store will show it"). Drafts,
   previews and revisions are free.
3. APPROVE -> HANDOFF. The preview ends with "Reply YES to place
   it in front of you." On YES — and only on YES — the module
   spends one unit of the per-tier "order" cap (og_tiers kind
   "order": free 1 / standard 3 / pro 10 / blue 25 / blackout 100
   handoffs per day) and produces the handoff: (a) the merchant's
   ordering link, grounded by lookup (the store's official order
   page; when none is found, a search link for the store + order),
   and (b) a copy-ready ORDER SHEET the visitor pastes/types at
   checkout. Payment happens at the store, by the visitor — OG
   never sees card data, never takes a payment, and the reply
   says so plainly. The vocabulary is "ready to place" and
   "order sheet": OG never claims an order was submitted. On NO
   or expiry the draft is discarded and nothing is spent.
4. USUALS. "save that as my usual at <store>" stores the approved
   (or previewed) order per-visitor in the durable store (Postgres
   og_ordering_data when OG_MEMORY_DB_URL is set, else
   ordering_store.json). "order my usual from <store>" re-drafts
   it straight into the preview step with prices re-checked fresh
   or marked unconfirmed — a saved price is never shown as if it
   were current. "my usuals" lists them.

Pending drafts are process-local (like the Round 14/16 drafts):
a server restart inside the window drops an unapproved draft —
nothing was handed off, by design; the visitor just asks again.
Persona files are never touched.
"""

import json
import logging
import os
import re
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

ORDER_STORE_FILE = "ordering_store.json"
_PENDING_TTL = 60 * 60          # a draft stays approvable 1 h
_MAX_ITEMS = 12                 # items per order
_MAX_PRICED = 8                 # price lookups per draft

_order_lock = threading.Lock()

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None


def _pro_url() -> str:
    base = os.getenv("OG_PUBLIC_URL", "https://og-ai-service.onrender.com")
    return base.rstrip("/") + "/pro"


# --- Durable per-visitor store (usuals + last handed-off order) ---------------

def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_ordering_data ("
            "uid TEXT, kind TEXT, data JSONB, PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(ORDER_STORE_FILE):
        try:
            with open(ORDER_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Ordering store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(ORDER_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Ordering store save failed: {e}")


def _blob(uid: str, kind: str, default):
    """One visitor's blob of one kind ('usuals' | 'last')."""
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_ordering_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"Ordering DB load failed, using file: {e}")
    with _order_lock:
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
                        "INSERT INTO og_ordering_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Ordering DB save failed, using file: {e}")
    with _order_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


def _usuals(uid: str) -> Dict:
    usuals = _blob(uid, "usuals", {})
    return usuals if isinstance(usuals, dict) else {}


def _save_usual(uid: str, store: str, items: list):
    usuals = _usuals(uid)
    usuals[_store_key(store)] = {
        "store": store,
        "items": [{"name": i["name"], "qty": int(i.get("qty", 1))}
                  for i in items],
        "saved": datetime.now(timezone.utc).isoformat(),
    }
    _put_blob(uid, "usuals", usuals)


def _store_key(store: str) -> str:
    return re.sub(r"\s+", " ", str(store).lower()).strip()


# --- Handoff counters (per-tier daily "order" cap) ------------------------------
# Kept in this module (on app.py's usage store, bound via bind_app)
# so app.py stays at binding lines only — it is at the push ceiling.

_deps: Dict = {}


def bind_app(deps):
    _deps.update(deps)


def _order_used_today(uid: str) -> int:
    """Approved order handoffs this visitor completed today (UTC)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _deps["usage_lock"]:
        store = _deps["load_usage"]()
    entry = store.get(f"order:{uid}")
    if not isinstance(entry, dict) or entry.get("date") != today:
        return 0
    return int(entry.get("count", 0))


def _order_left(uid: str) -> int:
    """Order handoffs left today for the current request's tier."""
    import og_tiers as _tiers
    cap = _tiers.cap(_deps.get("get_tier", lambda: "free")(), "order")
    return max(0, cap - _order_used_today(uid))


def _consume_order(uid: str) -> bool:
    """Record one approved order handoff today; False at cap."""
    import og_tiers as _tiers
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"order:{uid}"
    with _deps["usage_lock"]:
        store = _deps["load_usage"]()
        entry = store.get(key)
        if not isinstance(entry, dict) or entry.get("date") != today:
            entry = {"date": today, "count": 0}
        cap = _tiers.cap(_deps.get("get_tier", lambda: "free")(),
                         "order")
        if int(entry.get("count", 0)) >= cap:
            return False
        entry["count"] = int(entry.get("count", 0)) + 1
        store[key] = entry
        _deps["save_usage"](store)
        return True


# --- Pending draft state (process-local, approval gate) -------------------------

_pending_orders: Dict[str, Dict] = {}
_pending_lock = threading.Lock()


def _set_pending(uid: str, state: Dict):
    state = dict(state)
    state["created"] = time.time()
    with _pending_lock:
        _pending_orders[uid] = state


def _get_pending(uid: str) -> Optional[Dict]:
    if not uid:
        return None
    with _pending_lock:
        state = _pending_orders.get(uid)
        if state and time.time() - float(state.get("created", 0)) \
                > _PENDING_TTL:
            del _pending_orders[uid]
            return None
        return dict(state) if state else None


def _clear_pending(uid: str):
    with _pending_lock:
        _pending_orders.pop(uid, None)


# --- Parsing ---------------------------------------------------------------------

_KNOWN_STORES = (
    "domino's", "dominos", "pizza hut", "papa john's", "little caesars",
    "mcdonald's", "mcdonalds", "burger king", "wendy's", "wendys",
    "taco bell", "chipotle", "subway", "kfc", "popeyes", "chick-fil-a",
    "five guys", "shake shack", "in-n-out", "jack in the box",
    "sonic", "dairy queen", "arby's", "arbys", "panera", "starbucks",
    "dunkin", "safeway", "walmart", "target", "costco", "kroger",
    "winco", "fred meyer", "whole foods", "trader joe's", "aldi",
    "publix", "h-e-b", "heb", "amazon fresh", "instacart", "doordash",
    "uber eats", "grubhub", "7-eleven", "cvs", "walgreens",
)

_STORE_TAIL_RE = re.compile(
    r"\b(?:from|at)\s+([A-Za-z][A-Za-z0-9&'.\-]*"
    r"(?:\s+[A-Za-z0-9&'.\-]+){0,3})", re.I)
_STORE_TAIL_STOP = {
    "for", "tonight", "today", "tomorrow", "please", "now", "asap",
    "with", "and", "to", "delivery", "pickup",
}

_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
              "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
              "eleven": 11, "twelve": 12, "a": 1, "an": 1}

_SEGMENT_JUNK = {
    "", "for me", "for us", "for delivery", "for pickup", "delivery",
    "pickup", "please", "thanks", "thank you", "tonight", "today",
    "now", "asap", "the usual", "my usual", "usual",
}

_APPROVE_RE = re.compile(
    r"^\W*(yes|yeah|yep|yup|approve|approved|go ahead|do it|ship it"
    r"|confirm|ok|okay|sounds good|let'?s go)\b")
_DECLINE_RE = re.compile(
    r"^\W*(no|nope|nah|cancel|scrap|discard|never ?mind|stop|don'?t"
    r"|do not)\b")
_REVISE_MARKERS = (
    "change", "add", "remove", "drop", "instead", "replace", "swap",
    "switch", "make it", "more", "less", "extra", "without", "bigger",
    "smaller", "faster",
)
_PAY_RE = re.compile(
    r"\b(just\s+)?pay\s+(for\s+)?(it|this|the order|my order)\b"
    r"|\bpay\s+for\s+it\b|\bcheck\s?out\b.*\bfor me\b", re.I)

_USUALS_LIST_RE = re.compile(
    r"\bmy usuals\b|\bwhat('?s| is| are) my usual\b"
    r"|\blist my usuals\b", re.I)
_SAVE_USUAL_RE = re.compile(
    r"\bsave\b[^.?!]{0,40}\bmy usual\b|\bsave\b[^.?!]{0,20}\bas (a |the |my )?usual\b",
    re.I)
_USUAL_REDRAFT_RE = re.compile(
    r"\b(order|build|get|make)\b[^.?!]{0,30}\bmy usual\b"
    r"|\bmy usual\b[^.?!]{0,30}\border\b", re.I)
_SAVE_AT_RE = re.compile(
    r"\bmy usual at\s+(.+?)\s*$|\busual at\s+(.+?)\s*$", re.I)

_PAYLOAD_RES = (
    re.compile(r"\border(?:\s+me|\s+us)?\s*[:,\-]?\s*(.+)$", re.I),
    re.compile(r"\bbuild\s+(?:me\s+)?(?:a\s+|an\s+|my\s+)?(.+?)\s+order\b(.*)$",
               re.I),
    re.compile(r"\bplace\s+(?:an?|my|the)\s+order\s*(?:for\s+)?(.+)$", re.I),
    re.compile(r"\b(?:get|grab)\s+me\s+(.+)$", re.I),
    re.compile(r"\bi(?:'d| would)\s+like\s+to\s+order\s+(.+)$", re.I),
    re.compile(r"\bi\s+want\s+to\s+order\s+(.+)$", re.I),
    re.compile(r"\bcan\s+i\s+(?:get|order)\s+(.+)$", re.I),
)


def _clean(text: str) -> str:
    return " " + re.sub(r"\s+", " ", str(text).lower()).strip() + " "


def _trim_store(raw: str) -> str:
    """Tidy a captured store name: cut trailing connective words."""
    words = re.sub(r"[.,;!?]+$", "", str(raw or "").strip()).split()
    while words and words[-1].lower() in _STORE_TAIL_STOP:
        words.pop()
    name = " ".join(words).strip()
    if name.lower().startswith("the "):
        name = name[4:]
    return name[:48].strip()


def _find_store(text: str) -> Optional[str]:
    """The store named in a message: a from/at tail first, then a
    known chain mention. Returns the display name or None."""
    m = _STORE_TAIL_RE.search(str(text))
    if m:
        name = _trim_store(m.group(1))
        if len(name) >= 2:
            return name
    low = _clean(text)
    for store in _KNOWN_STORES:
        if f" {store} " in low or low.startswith(f" {store}"):
            return store.title().replace("'S", "'s")
    return None


def _parse_items(text: str) -> list:
    """Split an order payload into [{name, qty}] — segments on
    commas / 'and' / '+', an optional quantity prefix each."""
    if not text:
        return []
    cleaned = str(text)
    cleaned = _STORE_TAIL_RE.sub(" ", cleaned)
    for store in _KNOWN_STORES:
        cleaned = re.sub(re.escape(store), " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\b(please|for me|for us|tonight|today|asap)\b",
                     " ", cleaned, flags=re.I)
    parts = re.split(r",|;|\band\b|\+|&", cleaned)
    items = []
    for part in parts:
        seg = re.sub(r"\s+", " ", part).strip(" .")
        if seg.lower() in _SEGMENT_JUNK or len(seg) < 2 or len(seg) > 60:
            continue
        qty = 1
        m = re.match(r"^(\d{1,2})\s*x?\s+(.+)$", seg)
        if m:
            qty = max(1, min(99, int(m.group(1))))
            seg = m.group(2).strip()
        else:
            words = seg.split(" ", 1)
            if len(words) == 2 and words[0].lower() in _NUM_WORDS:
                qty = _NUM_WORDS[words[0].lower()]
                seg = words[1].strip()
            elif seg.lower().startswith("the "):
                seg = seg[4:]
        seg = seg.strip(" .")
        if seg.lower() in _SEGMENT_JUNK or len(seg) < 2:
            continue
        items.append({"name": seg, "qty": qty})
        if len(items) >= _MAX_ITEMS:
            break
    return items


def _fresh_payload(message: str) -> Optional[str]:
    """The item/store-bearing tail of a fresh order ask, or None
    when the message isn't one."""
    text = str(message).strip()
    low = text.lower()
    if "how do i order" in low or "how to order" in low \
            or "in order to" in low or "in order " in low \
            or "law and order" in low or "order of" in low \
            or "order by" in low:
        return None
    if re.match(r"^\s*(what|how|why|when|which|who)\b", low):
        return None  # a question ABOUT ordering, not an order ask
    for rx in _PAYLOAD_RES:
        m = rx.search(text)
        if m:
            payload = m.group(1)
            if rx is _PAYLOAD_RES[1] and m.group(2):
                payload = payload + " " + m.group(2)
            payload = payload.strip()
            if payload.lower().startswith(("of ", "by ")):
                return None
            return payload
    return None


def _parse_order_ask(message: str) -> Optional[Dict]:
    """Parse a fresh order ask into {store, items} — either field
    may be missing (the clarify step asks for it). None when the
    message is not an order ask at all."""
    payload = _fresh_payload(message)
    if payload is None:
        return None
    if "usual" in payload.lower():
        return None  # usuals have their own branch
    store = _find_store(message)
    items = _parse_items(payload)
    if store is None and not items:
        # "place an order" with no content at all still counts —
        # the clarify step asks for both pieces in one question.
        if re.search(r"\border\b", str(message), re.I):
            return {"store": None, "items": []}
        return None
    return {"store": store, "items": items}


def _usual_store_from(message: str, usuals: Dict) -> Optional[Dict]:
    """The saved usual a message points at (store named in the
    message, or the only usual on file)."""
    store = _find_store(message)
    if store:
        key = _store_key(store)
        if key in usuals:
            return usuals[key]
        for k, u in usuals.items():
            if key in k or k in key:
                return u
        return None
    if len(usuals) == 1:
        return next(iter(usuals.values()))
    return None


def _claim_job(message: str, uid: str) -> Optional[Dict]:
    """What this message means for ordering, given the visitor's
    pending draft. Approvals/declines only count against a live
    pending order; revisions only claim with change language, so
    unrelated chat is never hijacked."""
    text = str(message)
    low = _clean(text)
    trimmed = low.strip()
    pending = _get_pending(uid)
    usuals = _usuals(uid) if uid else {}

    if _USUALS_LIST_RE.search(text):
        return {"kind": "usual_list"}
    if _SAVE_USUAL_RE.search(text):
        m = _SAVE_AT_RE.search(text)
        at = (m.group(1) or m.group(2)) if m else None
        return {"kind": "usual_save",
                "at": _trim_store(at) if at else None}
    if _USUAL_REDRAFT_RE.search(text) and usuals:
        return {"kind": "usual_redraft"}
    if _USUAL_REDRAFT_RE.search(text) and not usuals:
        return {"kind": "usual_none"}

    if pending is not None:
        stage = pending.get("stage", "")
        decline = bool(_DECLINE_RE.match(trimmed))
        approve = bool(_APPROVE_RE.match(trimmed))
        if stage == "preview":
            revise = any(m in trimmed for m in _REVISE_MARKERS)
            fresh = _parse_order_ask(text)
            if _PAY_RE.search(text):
                return {"kind": "pay_note"}
            if decline and not revise:
                return {"kind": "preview_decline"}
            if approve and not revise:
                return {"kind": "preview_approve"}
            if fresh:
                return {"kind": "ask", "store": fresh.get("store"),
                        "items": fresh.get("items", [])}
            m_add = re.search(r"\badd\b\s+(.+)$", text, re.I)
            if m_add:
                new_items = _parse_items(m_add.group(1))
                if new_items:
                    return {"kind": "revise_add", "items": new_items}
            m_rm = re.search(
                r"\b(?:remove|drop|without|no more)\b\s+(.+)$", text, re.I)
            if m_rm:
                target = _parse_items(m_rm.group(1))
                if target:
                    return {"kind": "revise_remove", "items": target}
            if revise:
                return {"kind": "revise_note"}
            return None  # unrelated chat; the draft stays parked
        if stage == "collect":
            if decline:
                return {"kind": "collect_decline"}
            return {"kind": "collect_answer"}
    fresh = _parse_order_ask(text)
    if fresh:
        return {"kind": "ask", "store": fresh.get("store"),
                "items": fresh.get("items", [])}
    return None


# --- Instruction results (the seam's {title, body, href} shape) ------------------

def _result(tag: str, title: str, body: str, href: str = "") -> list:
    return [{"title": title, "body": f"[{tag}] " + body, "href": href}]


def _pro_note() -> str:
    return f"Higher plans hand off more orders a day: {_pro_url()}"


def _cap_body() -> list:
    body = ("The visitor wants to put an order together, but "
            "they've used up today's order-handoff allowance for "
            "their plan. Do NOT draft or hand off anything. Tell "
            "them plainly, in persona, that the allowance resets "
            f"tomorrow (UTC) — or that {_pro_note()}")
    return _result("ORDERING: CAP", "🛒 Ordering — daily cap", body,
                   _pro_url())


# --- Grounding: prices + the merchant's ordering link ---------------------------

_PRICE_RE = re.compile(r"\$([\d,]+(?:\.\d{1,2})?)")
_LINK_SKIP = (
    "google.", "bing.", "duckduckgo.", "facebook.", "instagram.",
    "x.com", "twitter.", "yelp.", "tripadvisor.", "reddit.",
    "wikipedia.", "youtube.",
)


def _tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9']+", str(text).lower())
            if len(w) >= 4}


def _price_from_results(results, item_name: str, store: str) -> Optional[float]:
    """The first plausible price a lookup result grounds for this
    item — preferring results that mention the item or the store.
    None when nothing grounds a price (shown as unconfirmed)."""
    if not results:
        return None
    want = _tokens(item_name) | _tokens(store)
    fallback = None
    for r in results:
        if not isinstance(r, dict):
            continue
        if "price alert" in str(r.get("title", "")).lower():
            continue  # a monitoring block sharing the seam, not a menu
        hay = f"{r.get('title', '')} {r.get('body', '')}"
        amounts = []
        for m in _PRICE_RE.finditer(hay):
            try:
                v = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            if 0 < v <= 10000:
                amounts.append(v)
        if not amounts:
            continue
        if want & _tokens(hay):
            return amounts[0]
        if fallback is None:
            fallback = amounts[0]
    return fallback


def _grounded_link(results, store: str) -> Optional[str]:
    """The merchant's ordering page from lookup results — the
    first result link that isn't a search engine / social /
    review aggregator. None when nothing qualifies."""
    if not results:
        return None
    for r in results:
        if not isinstance(r, dict):
            continue
        href = str(r.get("href", "") or "").strip()
        if not href.startswith("http"):
            continue
        host = urllib.parse.urlparse(href).netloc.lower()
        if any(skip in host for skip in _LINK_SKIP):
            continue
        return href
    return None


def _search_link(store: str) -> str:
    q = urllib.parse.quote_plus(f"{store} order online")
    return f"https://www.google.com/search?q={q}"


def _price_items(store: str, items: list, lookup) -> list:
    """Attach a grounded price (or None) to each item via the
    lookup seam. Lookup failures simply leave prices unconfirmed."""
    priced = []
    lookups_left = _MAX_PRICED
    for item in items:
        price = None
        if lookup is not None and lookups_left > 0:
            lookups_left -= 1
            try:
                results = lookup(f"{store} {item['name']} price", 5)
            except Exception as e:
                logger.warning(f"Order price lookup failed: {e}")
                results = None
            price = _price_from_results(results, item["name"], store)
        priced.append({"name": item["name"],
                       "qty": int(item.get("qty", 1)), "price": price})
    return priced


def _find_link(store: str, lookup) -> str:
    if lookup is not None:
        try:
            results = lookup(f"{store} order online", 5)
        except Exception as e:
            logger.warning(f"Order link lookup failed: {e}")
            results = None
        link = _grounded_link(results, store)
        if link:
            return link
    return _search_link(store)


# --- Preview / order-sheet rendering ----------------------------------------------

def _fmt_price(v: float) -> str:
    return f"${v:,.2f}"


def _items_block(items: list) -> str:
    lines = []
    for i in items:
        qty = int(i.get("qty", 1))
        price = i.get("price")
        if price is None:
            lines.append(
                f"- {qty} × {i['name']} — price not confirmed "
                f"(the store will show it)")
        elif qty > 1:
            lines.append(
                f"- {qty} × {i['name']} — {_fmt_price(price)} each "
                f"({_fmt_price(price * qty)})")
        else:
            lines.append(f"- {qty} × {i['name']} — {_fmt_price(price)}")
    return "\n".join(lines)


def _total_line(store: str, items: list) -> str:
    if items and all(i.get("price") is not None for i in items):
        total = sum(i["price"] * int(i.get("qty", 1)) for i in items)
        return (f"Estimated total: {_fmt_price(total)} — from prices "
                f"found on {store}'s listings just now; {store} "
                f"confirms the final total at checkout.")
    return (f"Total: {store} will show it — some prices couldn't be "
            f"confirmed online.")


def _preview_text(store: str, items: list) -> str:
    return (f"🛒 ORDER PREVIEW — {store}\n"
            + _items_block(items) + "\n"
            + _total_line(store, items) + "\n"
            "Reply YES to place it in front of you — or tell me "
            "what to change (say \"add …\" or \"remove …\"). Nothing "
            "is charged by OG; you pay at the store.")


def _preview_body(store: str, items: list, revised: bool = False) -> list:
    lead = ("The visitor's order draft was updated. Present the "
            "updated ORDER PREVIEW below EXACTLY as written (every "
            "item, quantity and price character-for-character), in "
            "persona around it:" if revised else
            "The visitor's order draft is ready. Present the ORDER "
            "PREVIEW below EXACTLY as written (every item, quantity "
            "and price character-for-character), in persona around "
            "it:")
    body = (lead + "\n\n" + _preview_text(store, items) + "\n\n"
            "Do NOT invent or change any price. A line that says "
            "\"price not confirmed\" stays exactly that way.")
    return _result("ORDERING: PREVIEW",
                   f"🛒 Order preview — {store}", body)


def _draft(store: str, items: list, uid: str, lookup) -> list:
    """Price the draft, find the merchant link, park it as the
    pending preview, and return the preview result."""
    priced = _price_items(store, items, lookup)
    link = _find_link(store, lookup)
    _set_pending(uid, {"stage": "preview", "store": store,
                       "items": priced, "link": link})
    return _preview_body(store, priced)


def _clarify_body(missing_store: bool, missing_items: bool) -> list:
    if missing_store and missing_items:
        q = ("Ask them ONE question, in persona: what store, and "
             "what goes on the order? Nothing is drafted yet.")
    elif missing_store:
        q = ("Ask them ONE question, in persona: which store is "
             "this order from? Nothing is drafted yet — the items "
             "are already noted.")
    else:
        q = ("Ask them ONE question, in persona: what should go "
             "on the order? Nothing is drafted yet — the store is "
             "already noted.")
    body = (q + " Keep it to that one question; do NOT invent a "
            "store, items, or any price.")
    return _result("ORDERING: CLARIFY", "🛒 Ordering — one question",
                   body)


def _handoff_body(store: str, items: list, link: str) -> list:
    sheet = (f"ORDER SHEET — {store}\n" + _items_block(items) + "\n"
             + _total_line(store, items))
    body = (
        "The visitor APPROVED the order draft. Hand it off with "
        "exactly this, in persona around it:\n\n"
        f"Ordering page: {link}\n"
        "(give them that EXACT full URL, unchanged — do not "
        "shorten it, do not swap the domain)\n\n"
        f"{sheet}\n\n"
        "Then say, in substance word for word: This is READY TO "
        "PLACE — it is not submitted yet. OG never took a payment "
        "and never saw a card. Open the ordering page, put in the "
        "items from the order sheet, and pay at the store — that's "
        "the part that's always yours.")
    return _result("ORDERING: HANDOFF", f"🛒 Order sheet — {store}",
                   body, link)


def _discard_body(stage: str) -> list:
    what = "draft" if stage == "preview" else "half-started order"
    body = (f"The visitor said NO. The {what} is DISCARDED — "
            "nothing was handed off and nothing was spent. Confirm "
            "that briefly, in persona, and let them know they can "
            "start a fresh order any time.")
    return _result("ORDERING: DISCARDED", "🛒 Ordering — discarded",
                   body)


# --- Job execution -------------------------------------------------------------------

def _ask(job, message, uid, order_left, lookup) -> Optional[list]:
    left = None
    try:
        left = order_left(uid) if order_left else None
    except Exception as e:
        logger.warning(f"Order cap check failed: {e}")
    if left is not None and left <= 0:
        return _cap_body()
    store = job.get("store")
    items = job.get("items") or []
    if store and items:
        return _draft(store, items, uid, lookup)
    _set_pending(uid, {"stage": "collect", "store": store,
                       "items": items})
    return _clarify_body(not store, not items)


def _collect_answer(message, uid, lookup) -> Optional[list]:
    pending = _get_pending(uid)
    if pending is None:
        return None
    store = pending.get("store")
    items = pending.get("items") or []
    if pending.get("want") == "usual":
        usuals = _usuals(uid)
        usual = _usual_store_from(message, usuals)
        _clear_pending(uid)
        if usual:
            return _draft(usual["store"], usual.get("items", []),
                          uid, lookup)
        body = ("The visitor was asked WHICH saved usual they "
                "meant and their answer didn't match one. Their "
                "saved usuals are at: "
                + ", ".join(u["store"] for u in usuals.values())
                + ". Tell them that plainly, in persona, and ask "
                "them to name one of those stores.")
        return _result("ORDERING: USUAL-WHICH",
                       "🛒 Ordering — which usual?", body)
    fresh = _parse_order_ask(message)
    if fresh and fresh.get("store") and fresh.get("items"):
        # The reply restated the whole order — it wins wholesale.
        return _draft(fresh["store"], fresh["items"], uid, lookup)
    new_store = _find_store(message)
    new_items = _parse_items(message)
    if new_store and not store:
        store = new_store
    if new_items and not items:
        items = new_items
    if store and items:
        return _draft(store, items, uid, lookup)
    if new_store or new_items:
        # Partial progress only — keep what's known and ask for
        # the rest in the same single question thread.
        _set_pending(uid, {"stage": "collect", "store": store,
                           "items": items})
        return _clarify_body(not store, not items)
    _clear_pending(uid)
    body = ("The visitor was asked for the missing piece of an "
            "order but their answer didn't include it. The "
            "half-started order is dropped. Tell them plainly, in "
            "persona, and give them the one-line format that always "
            "works: \"order 2 large pepperoni pizzas and a Coke "
            "from Domino's\". Do NOT invent any store, item, or "
            "price.")
    return _result("ORDERING: FORMAT-HINT", "🛒 Ordering — format",
                   body)


def _preview_approve(uid, consume_order) -> Optional[list]:
    pending = _get_pending(uid)
    if pending is None or pending.get("stage") != "preview":
        return None
    store = pending.get("store", "")
    items = pending.get("items", [])
    link = pending.get("link") or _search_link(store)
    spent = True
    if consume_order is not None:
        try:
            spent = bool(consume_order(uid))
        except Exception as e:
            logger.warning(f"Order consume failed: {e}")
            spent = False
    if not spent:
        _clear_pending(uid)
        return _cap_body()
    _put_blob(uid, "last", {"store": store, "items": items,
                            "link": link,
                            "at": datetime.now(timezone.utc)
                            .isoformat()})
    _clear_pending(uid)
    return _handoff_body(store, items, link)


def _revise(job, uid, lookup) -> Optional[list]:
    pending = _get_pending(uid)
    if pending is None or pending.get("stage") != "preview":
        return None
    store = pending.get("store", "")
    items = [dict(i) for i in pending.get("items", [])]
    if job["kind"] == "revise_add":
        existing = {i["name"].lower() for i in items}
        new = [i for i in job.get("items", [])
               if i["name"].lower() not in existing]
        if new:
            items.extend(_price_items(store, new, lookup))
    elif job["kind"] == "revise_remove":
        targets = {i["name"].lower() for i in job.get("items", [])}
        items = [i for i in items
                 if not any(t in i["name"].lower()
                            or i["name"].lower() in t
                            for t in targets)]
    if not items:
        _clear_pending(uid)
        body = ("The change emptied the visitor's order, so the "
                "draft is DISCARDED — nothing was handed off and "
                "nothing was spent. Confirm that briefly, in "
                "persona.")
        return _result("ORDERING: DISCARDED",
                       "🛒 Ordering — discarded", body)
    _set_pending(uid, {"stage": "preview", "store": store,
                       "items": items,
                       "link": pending.get("link")})
    return _preview_body(store, items, revised=True)


def _revise_note(uid) -> Optional[list]:
    pending = _get_pending(uid)
    if pending is None or pending.get("stage") != "preview":
        return None
    store = pending.get("store", "")
    items = pending.get("items", [])
    body = ("The visitor wants a change to the order draft but "
            "didn't phrase it as an add or a remove. Re-present "
            "the ORDER PREVIEW below EXACTLY as written, then tell "
            "them: say \"add …\" or \"remove …\", or start a fresh "
            "order — free-text edits aren't applied.\n\n"
            + _preview_text(store, items))
    return _result("ORDERING: PREVIEW", f"🛒 Order preview — {store}",
                   body)


def _pay_note(uid) -> Optional[list]:
    pending = _get_pending(uid)
    store = pending.get("store", "") if pending else "the store"
    body = ("The visitor asked OG to just pay for the order. Be "
            "plain, in persona: OG never takes a payment and never "
            "sees a card — paying happens at "
            f"{store}, by the visitor, at checkout. The order "
            "draft is still waiting: if they reply YES, they get "
            "the ordering page link and the copy-ready order "
            "sheet; paying there is the part that's always "
            "theirs.")
    return _result("ORDERING: PAY-NOTE", "🛒 Ordering — payment",
                   body)


def _usual_save(job, uid) -> Optional[list]:
    pending = _get_pending(uid)
    draft = None
    if pending is not None and pending.get("stage") == "preview":
        draft = pending
    if draft is None:
        last = _blob(uid, "last", None)
        if isinstance(last, dict) and last.get("store"):
            draft = last
    if draft is None:
        body = ("The visitor asked to save a usual, but there's "
                "no order draft and no handed-off order on file "
                "yet. Tell them plainly, in persona: put an order "
                "together first, then say \"save that as my "
                "usual\".")
        return _result("ORDERING: USUAL-SAVE", "🛒 Ordering — usuals",
                       body)
    store = job.get("at") or draft.get("store", "")
    items = draft.get("items", [])
    _save_usual(uid, store, items)
    names = ", ".join(f"{int(i.get('qty', 1))} × {i['name']}"
                      for i in items)
    body = (f"Saved: the visitor's usual at {store} is now on "
            f"file ({names}). Confirm that, in persona, and tell "
            f"them \"order my usual from {store}\" brings it right "
            "back — prices get re-checked fresh every time, never "
            "carried over from today.")
    return _result("ORDERING: USUAL-SAVE", "🛒 Ordering — usual saved",
                   body)


def _usual_redraft(uid, message, order_left, lookup) -> Optional[list]:
    left = None
    try:
        left = order_left(uid) if order_left else None
    except Exception as e:
        logger.warning(f"Order cap check failed: {e}")
    if left is not None and left <= 0:
        return _cap_body()
    usuals = _usuals(uid)
    usual = _usual_store_from(message, usuals)
    if usual is None:
        _set_pending(uid, {"stage": "collect", "want": "usual",
                           "store": None, "items": []})
        stores = ", ".join(u["store"] for u in usuals.values())
        body = ("The visitor asked for their usual but has more "
                f"than one on file ({stores}) and didn't name the "
                "store. Ask them ONE question, in persona: which "
                "store's usual?")
        return _result("ORDERING: CLARIFY",
                       "🛒 Ordering — one question", body)
    return _draft(usual["store"], usual.get("items", []), uid, lookup)


def _usual_list(uid) -> list:
    usuals = _usuals(uid)
    if not usuals:
        body = ("The visitor asked about their usuals but has "
                "none saved. Tell them plainly, in persona: put an "
                "order together, then say \"save that as my "
                "usual\" and it's on file.")
        return _result("ORDERING: USUALS", "🛒 Ordering — usuals", body)
    lines = []
    for u in usuals.values():
        names = ", ".join(f"{int(i.get('qty', 1))} × {i['name']}"
                          for i in u.get("items", []))
        lines.append(f"- {u['store']}: {names}")
    body = ("The visitor's saved usuals, exactly these (present "
            "them as-is, in persona around the list):\n"
            + "\n".join(lines)
            + "\n\"order my usual from <store>\" re-drafts one with "
            "prices checked fresh.")
    return _result("ORDERING: USUALS", "🛒 Ordering — usuals", body)


def ordering_results(job, message, uid, consume_order, order_left,
                      lookup) -> Optional[list]:
    """Run one claimed ordering job. Returns web_search-shaped
    results, or None on a true miss so the caller falls through
    to the previous search untouched. Only an approved handoff
    spends an order unit — via app.py's consume_order."""
    if not job or not uid:
        return None
    kind = job.get("kind", "")
    if kind == "ask":
        return _ask(job, message, uid, order_left, lookup)
    if kind == "collect_answer":
        return _collect_answer(message, uid, lookup)
    if kind == "collect_decline":
        _clear_pending(uid)
        return _discard_body("collect")
    if kind == "preview_decline":
        _clear_pending(uid)
        return _discard_body("preview")
    if kind == "preview_approve":
        return _preview_approve(uid, consume_order)
    if kind in ("revise_add", "revise_remove"):
        return _revise(job, uid, lookup)
    if kind == "revise_note":
        return _revise_note(uid)
    if kind == "pay_note":
        return _pay_note(uid)
    if kind == "usual_save":
        return _usual_save(job, uid)
    if kind == "usual_list":
        return _usual_list(uid)
    if kind == "usual_redraft":
        return _usual_redraft(uid, message, order_left, lookup)
    if kind == "usual_none":
        body = ("The visitor asked for their usual order but has "
                "none saved anywhere. Tell them plainly, in "
                "persona: no usual on file yet — put an order "
                "together and say \"save that as my usual\" after, "
                "or just name the store and the items now.")
        return _result("ORDERING: USUAL-NONE", "🛒 Ordering — usuals",
                       body)
    return None


# ---------------------------------------------------------------------------
# The seam (app.py installs this LAST, after Rounds 3/4/6/9–18)
# ---------------------------------------------------------------------------

# The ordering job parsed for the exchange currently being processed.
# All chat processing is serialized under app.py's _memory_lock, so a
# single slot is safe — the same reasoning as app.py's own slots.
_pending = {"job": None, "message": ""}


def install_ordering_tools(agent_instance, get_uid):
    """Wrap the agent's (already fully wrapped) detect_intent +
    web_search hooks so ordering asks, the draft/approve flow and
    usuals ride the established seam. get_uid() returns the current
    visitor's uid; the per-tier order cap runs on this module's own
    counters (bound to app.py's usage store via bind_app). The
    seam's previous search is passed into the flow as the lookup
    (Round 3 grounding for prices and the merchant's ordering
    page). Non-ordering messages pass through untouched. Persona
    files never touched."""
    if getattr(agent_instance, "_og_ordering_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["job"] = None
        _pending["message"] = ""
        try:
            uid = get_uid()
            job = _claim_job(str(message), uid) if uid else None
            if job:
                _pending["job"] = job
                _pending["message"] = str(message)
                if isinstance(intent, dict):
                    intent["needs_code_generation"] = False
                    if not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
        except Exception as e:
            logger.warning(f"Ordering trigger check failed: {e}")
            _pending["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _pending.get("job")
        message = _pending.get("message", "")
        _pending["job"] = None
        _pending["message"] = ""
        if job:
            try:
                results = ordering_results(
                    job, message, get_uid(), _consume_order,
                    _order_left, prev_search)
            except Exception as e:
                logger.warning(f"Ordering job failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_ordering_installed = True
