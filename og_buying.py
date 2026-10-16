"""Round 117: THE BUYING HELPER (Brent's order,
2026-10-12 11:15 PDT: "Buying helper" — specced in
round-117/SPEC.md.)

WHAT THIS IS. The crew's buyer — the seat that takes
a CHOSEN item the rest of the way to the approval
wall, and not one step past it. Audit-first, no
duplicates: the item arrives from the seats that
already own finding it —

  - the Shopping helper's (R116) saved pick or live
    shortlist, read at the data level
    (og_shopping._pick / og_shopping._search — the
    hand-off record R116 saved for exactly this);
  - a direct ask that names the item, its price, and
    where it comes from (the shape a laptop-hunt
    (R103) pick arrives in, until that seat lands);
  - the ordering pack keeps its own orders whole:
    food/service orders are claimed by og_ordering /
    og_ordering_flows exactly, and this seat defers
    to those claims untouched — it prepares RETAIL
    buys off picks, it does not fork the order pack.

WHAT IT DOES. Prepares the purchase END TO END up
to the approval wall: the right item and variant,
the quantity, the delivery or pickup terms, and the
money as ONE number — item + shipping + tax + fees,
each line shown over the total. A component the ask
and the listing don't state is labeled UNKNOWN, and
while anything is unknown there is NO total: the
card shows the known-so-far figure and calls it
exactly that, never a partial price dressed as the
total. Before any card exists, the destination is
verified (the anti-phishing discipline): a checkout
address that merely LOOKS like the named store
(lookalike host, a real host hidden behind an @, a
bare IP, a named store whose address doesn't match)
is flagged BEFORE any approval is asked, and no card
is parked for it. A prepared buy lands as an
approval card (item, lines, total-or-known, terms,
destination) — no card, no buy: the hand-off exists
only off a live card the user approves.

THE FENCE — the hardest in the shopping cluster.
OG NEVER PAYS. Not from this seat, not on any
wording: no card entry, no checkout completion, no
buy button pressed by OG. This module imports no
payment, trading, or browser machinery and calls no
order-placing function — approval produces a
HAND-OFF (the exact page + the finishing step), and
the payment is the user's own hand at the
merchant's checkout. Store-app purchases name the
standing rule (Brent, 2026-10-11): they run through
Apple/Google billing on the user's store account.
Pickup/meetup arrangements are the user's move
(the laptop-hunt rule) — the card says so.
Messaging and haggling stay the Round 54 chain's,
deferred to exactly.

CLAIMS. This seam installs AFTER Shopping (outermost
in the og_fixqueue chain) and defers EXACTLY first
to the PM/Filer/crew/Builder/WoodPro/MaintEng/
Mechanic/Blueprint claims, to the R54 marketplace
claims, and to the ordering pack's claims. ONE
takeover, by design: Shopping's own buy-stop claim
(the "buy it" fence R116 answered with before this
seat existed) is the door R116 pointed at — this
seat takes that claim and prepares the buy;
every other Shopping claim (search, refine, pick,
show, the message/haggle stops) defers untouched.
Bare approve/decline words bind ONLY to a live card
held for the caller, and only when no other flow
claims the words first. Per-account isolation: the
pick read, the card, and the record are the
caller's own, namespaced by uid at every read
(drilled). NO new loop (R72). Persona untouched;
app.py never touched. Switch: OG_BUYING_ENABLED
(unset = ON), the SPEC's name.

STATE. Own durable store, the Round 18 pattern:
Postgres table og_buying_data when OG_MEMORY_DB_URL
is set, else a JSON file; blob kind 'buy' = the
caller's last prepared buy (with its status). The
LIVE card is process-local (og_ordering's pending
pattern): a restart drops the card, never the
record — the status route shows what stood.
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

_TAG = "OG BUYING"

BUYING_STORE_FILE = "buying_store.json"
_buying_lock = threading.RLock()

# The live approval cards, process-local by design
# (og_ordering's pending pattern). uid -> record.
_cards: Dict[str, Dict] = {}

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, StoreError, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None


# --- Switch -----------------------------------------------------------------------


def buying_enabled() -> bool:
    """OG_BUYING_ENABLED=false is the kill switch.
    Unset = ON."""
    val = os.environ.get("OG_BUYING_ENABLED")
    if val is not None and val.strip().lower() in (
            "0", "false", "no", "off"):
        return False
    return True


# --- Durable store (Round 18 pattern, mirroring og_shopping) ----------------------


def _db_connect():
    conn = psycopg.connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_buying_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(BUYING_STORE_FILE):
        try:
            with open(BUYING_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Buying store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(BUYING_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Buying store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_buying_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"Buying DB load failed: {e}")
            raise StoreError("buying store unavailable (Round 99)") from e
    with _buying_lock:
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
                        "INSERT INTO og_buying_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Buying DB save failed: {e}")
            raise StoreError("buying store unavailable (Round 99)") from e
    with _buying_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


# --- The durable record + the live card ----------------------------------------------


def _buy_rec(uid: str) -> Optional[Dict]:
    got = _blob(uid, "buy", None)
    return got if isinstance(got, dict) and got.get("item") else None


def _save_buy_rec(uid: str, rec: Dict):
    _put_blob(uid, "buy", rec)


def _park_card(uid: str, rec: Dict):
    with _buying_lock:
        _cards[uid] = rec


def _card(uid: str) -> Optional[Dict]:
    with _buying_lock:
        rec = _cards.get(uid)
    return rec if isinstance(rec, dict) and rec.get("item") else None


def _drop_card(uid: str):
    with _buying_lock:
        _cards.pop(uid, None)


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


# --- Destination verification (the anti-phishing discipline) -------------------------
#
# The known-store table: brand key -> official host.
# A destination is CONFIRMED when its host IS the
# official host (or a subdomain of it) for the store
# it names or implies; SUSPICIOUS when the host merely
# resembles a brand (leet-normalized containment), when
# a real host hides behind an @, when a checkout sits
# on a bare IP, or when a NAMED store's address doesn't
# match the store; UNVERIFIED when nothing known can
# be checked — named honestly on the card.


_BRAND_HOSTS = {
    "amazon": "amazon.com", "ebay": "ebay.com",
    "walmart": "walmart.com", "target": "target.com",
    "bestbuy": "bestbuy.com", "homedepot": "homedepot.com",
    "lowes": "lowes.com", "etsy": "etsy.com",
    "newegg": "newegg.com", "costco": "costco.com",
    "wayfair": "wayfair.com",
    "harborfreight": "harborfreight.com",
}
_BRAND_LABELS = {
    "amazon": "Amazon", "ebay": "eBay", "walmart": "Walmart",
    "target": "Target", "bestbuy": "Best Buy",
    "homedepot": "Home Depot", "lowes": "Lowe's",
    "etsy": "Etsy", "newegg": "Newegg", "costco": "Costco",
    "wayfair": "Wayfair", "harborfreight": "Harbor Freight",
}
_BRAND_ALIASES = {
    "best buy": "bestbuy", "home depot": "homedepot",
    "harbor freight": "harborfreight", "lowe's": "lowes",
    "lowes": "lowes",
}
_APP_HOSTS = {"apps.apple.com": "the Apple App Store",
              "play.google.com": "Google Play"}
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e",
                       "5": "s", "7": "t", "@": "a"})


def _host_parts(url: str):
    """(host, had_userinfo) for a URL or bare host."""
    text = str(url or "").strip()
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urllib.parse.urlparse(text)
        netloc = parts.netloc or ""
    except Exception:
        return "", False
    userinfo = "@" in netloc
    host = netloc.rsplit("@", 1)[-1].split(":")[0].lower()
    if host.startswith("www."):
        host = host[4:]
    return host, userinfo


def _official_match(host: str, official: str) -> bool:
    return host == official or host.endswith("." + official)


def _brand_in_host(host: str) -> Optional[str]:
    norm = host.lower().translate(_LEET)
    squashed = re.sub(r"[^a-z]", "", norm)
    for brand in _BRAND_HOSTS:
        if brand in squashed:
            return brand
    return None


def _verdict(host: str, named_brand: str = "",
             userinfo: bool = False,
             from_pick: bool = False):
    """(verdict, line) — verdict in confirmed /
    unverified / suspicious."""
    if userinfo:
        return ("suspicious",
                "the address hides its real host behind an "
                "@ — the part before the @ is decoration; "
                "the real destination is what follows it")
    if not host:
        return ("unverified",
                "no address to check — confirm the store's "
                "name letter-for-letter in your browser "
                "before you pay")
    if host in _APP_HOSTS:
        return ("confirmed",
                f"the address is {_APP_HOSTS[host]} itself")
    if host == "facebook.com" or host.endswith(".facebook.com"):
        return ("confirmed",
                "the address is Facebook Marketplace's own "
                "(facebook.com)")
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host):
        return ("suspicious",
                "a bare IP address, not a store's named "
                "address — real stores check out at their "
                "own name")
    if named_brand and named_brand in _BRAND_HOSTS:
        official = _BRAND_HOSTS[named_brand]
        if _official_match(host, official):
            return ("confirmed",
                    f"the address is {_BRAND_LABELS[named_brand]}'s "
                    f"own ({official})")
        if _brand_in_host(host) == named_brand:
            return ("suspicious",
                    f"this address only LOOKS like "
                    f"{_BRAND_LABELS[named_brand]} — the real "
                    f"store lives at {official}, and this "
                    f"isn't it")
        return ("suspicious",
                f"you named {_BRAND_LABELS[named_brand]} but "
                f"the address is {host} — the name and the "
                f"address don't match")
    implied = _brand_in_host(host)
    if implied:
        official = _BRAND_HOSTS[implied]
        if _official_match(host, official):
            return ("confirmed",
                    f"the address is {_BRAND_LABELS[implied]}'s "
                    f"own ({official})")
        return ("suspicious",
                f"this address only LOOKS like "
                f"{_BRAND_LABELS[implied]} — the real store "
                f"lives at {official}, and this isn't it")
    if from_pick:
        return ("confirmed",
                "the address is the pick's own listing "
                "page — the one the shopping seat found")
    return ("unverified",
            f"{host} isn't a store I can confirm from my "
            "known list — check the address letter-for-"
            "letter in your browser before you pay")


# --- Ask extraction (deterministic, off the ask's own words) ---------------------------

_URL_RE = re.compile(r"https?://[^\s\"')\]]+", re.I)
_HOST_TAIL_RE = re.compile(
    r"\b(?:from|at)\s+([a-z0-9\-]+\.[a-z]{2,}"
    r"(?:\.[a-z]{2,})?)", re.I)
_STORE_NAME_RE = re.compile(
    r"\b(?:from|at|on)\s+(amazon|ebay|walmart|target|"
    r"best\s*buy|home\s*depot|lowe'?s|etsy|newegg|"
    r"costco|wayfair|harbor\s*freight)\b", re.I)
_PRICE_RE = re.compile(r"\$\s?([\d,]{1,7}(?:\.\d{1,2})?)")
_SHIP_FREE_RE = re.compile(r"\bfree\s+shipping\b", re.I)
_SHIP_RE = re.compile(
    r"shipping\s*(?:is|of|:|costs?)?\s*\$\s?"
    r"([\d,]{1,7}(?:\.\d{1,2})?)", re.I)
_SHIP2_RE = re.compile(
    r"\+\s*\$\s?([\d,]{1,7}(?:\.\d{1,2})?)\s+"
    r"(?:for\s+)?shipping\b", re.I)
_TAX_AMT_RE = re.compile(
    r"tax\s*(?:is|of|:)?\s*\$\s?"
    r"([\d,]{1,7}(?:\.\d{1,2})?)", re.I)
_TAX_RATE_RE = re.compile(
    r"tax\s*(?:is|of|:)?\s*([\d.]+)\s*(?:%|percent)", re.I)
_TAX_RATE2_RE = re.compile(
    r"([\d.]+)\s*(?:%|percent)\s*tax\b", re.I)
_FEE_RE = re.compile(
    r"fees?\s*(?:is|of|:)?\s*\$\s?"
    r"([\d,]{1,7}(?:\.\d{1,2})?)", re.I)
_QTY_RE = re.compile(
    r"\b(\d{1,2})\s*(?:x|of\s+them|of\s+those)\b", re.I)
_QTY2_RE = re.compile(r"\bqty\s*(?:of)?\s*(\d{1,2})\b", re.I)
_SIZE_RE = re.compile(r"\bsize\s+([a-z0-9.\-]+)", re.I)
_VARIANT_RE = re.compile(
    r"\b(\d+(?:\.\d+)?\s?(?:v|volt|gb|tb|inch|in\.))\b",
    re.I)


def _blank_spans(text: str, spans) -> str:
    chars = list(text)
    for a, b in spans:
        for i in range(a, min(b, len(chars))):
            chars[i] = " "
    return "".join(chars)


def _extract_shipping(raw: str):
    """(value|None, note, span|None)."""
    if _SHIP_FREE_RE.search(raw):
        return 0.0, "free, as you stated", None
    for rx in (_SHIP_RE, _SHIP2_RE):
        m = rx.search(raw)
        if m:
            return _amount(m.group(1)), "as you stated", m.span()
    return None, "", None


def _extract_tax(raw: str):
    """(kind, value, span|None) — kind 'amount' | 'rate'."""
    m = _TAX_AMT_RE.search(raw)
    if m:
        return "amount", _amount(m.group(1)), m.span()
    for rx in (_TAX_RATE_RE, _TAX_RATE2_RE):
        m = rx.search(raw)
        if m:
            return "rate", _amount(m.group(1)), m.span()
    return "", None, None


def _extract_fees(raw: str):
    """(value|None, span|None)."""
    m = _FEE_RE.search(raw)
    if m:
        return _amount(m.group(1)), m.span()
    return None, None


def _extract_qty(raw: str, tail_start: int = 0) -> int:
    m = _QTY_RE.search(raw) or _QTY2_RE.search(raw)
    if m:
        try:
            n = int(m.group(1))
            if 1 <= n <= 99:
                return n
        except ValueError:
            pass
    m = re.match(r"\s*(?:buy|purchase)\s+(\d{1,2})\s+",
                 str(raw or ""), re.I)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            pass
    return 1


def _brand_key(text: str) -> str:
    t = " ".join(str(text or "").lower().split())
    if t in _BRAND_ALIASES:
        return _BRAND_ALIASES[t]
    t = t.replace(" ", "").replace("'", "")
    return t if t in _BRAND_HOSTS else ""


def _extract_destination(raw: str) -> Optional[Dict]:
    """A destination named in the ask (URL, bare host,
    known store name, an app store), or None."""
    low = str(raw or "").lower()
    if "app store" in low:
        return {"label": "the Apple App Store",
                "url": "https://apps.apple.com",
                "host": "apps.apple.com",
                "kind": "app_store", "brand": ""}
    if "google play" in low:
        return {"label": "Google Play",
                "url": "https://play.google.com",
                "host": "play.google.com",
                "kind": "app_store", "brand": ""}
    named = ""
    m = _STORE_NAME_RE.search(raw)
    if m:
        named = _brand_key(m.group(1))
    url, host, userinfo = "", "", False
    m = _URL_RE.search(raw)
    if m:
        url = m.group(0).rstrip(".,")
        host, userinfo = _host_parts(url)
    else:
        m = _HOST_TAIL_RE.search(raw)
        if m:
            host, userinfo = _host_parts(m.group(1))
            url = "https://" + host
    if not host and named:
        host = _BRAND_HOSTS[named]
        url = "https://" + host
    if not host:
        return None
    if host in _APP_HOSTS:
        kind = "app_store"
    elif "facebook.com" in host:
        kind = "marketplace"
    else:
        kind = "retail"
    label = (_BRAND_LABELS.get(named) if named
             else _APP_HOSTS.get(host) or host)
    verdict, line = _verdict(host, named, userinfo)
    return {"label": label, "url": url, "host": host,
            "kind": kind, "brand": named,
            "verdict": verdict, "verdict_line": line}


def _pick_destination(option: Dict) -> Dict:
    """The destination a saved pick carries: its own
    listing page."""
    href = str(option.get("href") or "")
    host, userinfo = _host_parts(href) if href else ("", False)
    where = str(option.get("where") or "")
    lane = str(option.get("lane") or "")
    if host in _APP_HOSTS:
        kind = "app_store"
    elif lane == "mp" or "facebook" in where.lower() \
            or "facebook.com" in host:
        kind = "marketplace"
    else:
        kind = "retail"
    verdict, line = _verdict(host, "", userinfo,
                             from_pick=True)
    label = where or host or "the listing"
    if kind == "marketplace":
        label = "Facebook Marketplace"
    return {"label": label, "url": href, "host": host,
            "kind": kind, "brand": "",
            "verdict": verdict, "verdict_line": line}


def _extract_variant(raw: str) -> str:
    m = _SIZE_RE.search(raw)
    if m:
        return f"size {m.group(1)}"
    m = _VARIANT_RE.search(raw)
    if m:
        return " ".join(m.group(1).split())
    return ""


_TAIL_SUBS = (
    _URL_RE, _HOST_TAIL_RE, _STORE_NAME_RE, _SHIP_RE,
    _SHIP2_RE, _TAX_AMT_RE, _TAX_RATE_RE, _TAX_RATE2_RE,
    _FEE_RE, _PRICE_RE, _QTY_RE,
    re.compile(r"\bfree\s+shipping\b", re.I),
    re.compile(r"\bplease\b|\bfor\s+me\b|\bfrom\b|\bat\b",
               re.I),
)


def _clean_item_tail(tail: str) -> str:
    t = " " + str(tail or "") + " "
    for rx in _TAIL_SUBS:
        t = rx.sub(" ", t)
    t = re.sub(r"[^a-z0-9\s+.\-]", " ", t.lower())
    words = [w for w in t.split()
             if w not in ("a", "an", "the", "one", "of")]
    return " ".join(words).strip()


# --- The money: lines, and the ONE total only when it's whole -------------------------


def _totals(rec: Dict):
    """(total|None, known, unknown_names). The total
    exists only when every line is known — a partial
    figure is never dressed as one."""
    subtotal = round(float(rec["qty"]) *
                     float(rec["unit_price"]), 2)
    known = subtotal
    unknown = []
    for key in ("shipping", "tax", "fees"):
        v = rec.get(key)
        if v is None:
            unknown.append(key)
        else:
            known = round(known + float(v), 2)
    total = round(known, 2) if not unknown else None
    return total, round(known, 2), unknown


def _money_line(label: str, value, note: str = "") -> str:
    if value is None:
        return f"{label}: unknown" + (
            f" ({note})" if note else
            " — not stated in the ask or the listing")
    line = f"{label}: {_money(value)}"
    if note:
        line += f" ({note})"
    return line


def _total_block(rec: Dict) -> str:
    total, known, unknown = _totals(rec)
    subtotal = round(float(rec["qty"]) *
                     float(rec["unit_price"]), 2)
    lines = [
        f"Item line: {rec['qty']} × "
        f"{_money(rec['unit_price'])} = {_money(subtotal)}",
        _money_line("Shipping", rec.get("shipping"),
                    rec.get("shipping_note", "")),
        _money_line("Tax", rec.get("tax"),
                    rec.get("tax_note", "")),
        _money_line("Fees", rec.get("fees"),
                    rec.get("fees_note", "")),
    ]
    if total is not None:
        lines.append(
            f"TOTAL: {_money(total)} — one number, every "
            "line above inside it.")
    else:
        lines.append(
            f"Known so far: {_money(known)} — that is NOT "
            f"the total: {' and '.join(unknown)} still "
            "unknown. The checkout names them before you "
            "pay a cent; I won't dress a partial price up "
            "as a total.")
    return "\n".join(lines)


def _card_body(rec: Dict) -> str:
    dest = rec["destination"]
    lines = [
        "Approval card — a prepared buy. Nothing is "
        "ordered and nothing is charged by me, ever: I "
        "prepare, you approve, your own hand pays.",
        f"Item: {rec['title']}",
        f"Variant: {rec.get('variant') or 'as listed'}",
        _total_block(rec),
        f"Destination: {dest.get('label', '')} — "
        f"{dest.get('verdict_line', '')}.",
        f"Terms: {rec.get('terms', '')}",
        f"Price: {rec.get('price_note', '')}",
    ]
    if dest.get("kind") == "app_store":
        lines.append(
            "This is an app-store purchase: the payment "
            "runs through Apple/Google billing on your "
            "store account — OG's hands never touch it.")
    if dest.get("verdict") == "unverified":
        lines.append(
            "That destination is UNVERIFIED — I don't "
            "know it as the real store. If the address "
            "in your browser isn't exactly this one, "
            "don't pay.")
    lines.append(
        "Say \"approve\" and I'll hand you the exact "
        "page and the finishing step. Say \"decline\" "
        "and this buy dies here — no card, no buy.")
    return "\n".join(lines)


def _flag_body(rec: Dict) -> str:
    dest = rec["destination"]
    return (
        "STOP — that destination is flagged BEFORE any "
        "approval ask, on purpose, and no card exists "
        f"for it. The address is {dest.get('host', '')} "
        f"— {dest.get('verdict_line', '')}. Nothing was "
        "prepared, nothing was ordered, and nothing was "
        "charged (nothing ever is from me). If you meant "
        "the real store, name its true address and I'll "
        "prepare the buy against that.")


# --- Operations -------------------------------------------------------------------------


def _do_prepare(uid: str, claim: Dict) -> List[Dict]:
    rec = claim["rec"]
    dest = rec["destination"]
    if dest.get("verdict") == "suspicious":
        return _result(_TAG, "Buying — destination "
                             "doesn't check out",
                       _flag_body(rec))
    rec["status"] = "prepared"
    rec["at"] = time.time()
    old = _card(uid)
    _save_buy_rec(uid, rec)
    _park_card(uid, rec)
    body = _card_body(rec)
    if old and old.get("title") != rec.get("title"):
        body = ("The old card is dropped — here's the "
                "new one.\n" + body)
    return _result(_TAG, "Buying — approval card", body)


def _do_approve(uid: str, claim: Dict) -> List[Dict]:
    rec = _card(uid)
    if rec is None:
        return _result(
            _TAG, "Buying — no live card",
            "There's no live approval card to approve — "
            "no card, no buy. Prepare one first (\"buy "
            "it\" on a saved pick) and the hand-off "
            "exists off that card.")
    rec = dict(rec)
    rec["status"] = "handed_off"
    rec["at"] = time.time()
    _save_buy_rec(uid, rec)
    _drop_card(uid)
    dest = rec["destination"]
    lines = [
        "Approved — handed off. Your hand finishes it, "
        "here:",
        f"Item: {rec['title']}",
        _total_block(rec),
        f"Destination: {dest.get('label', '')}"
        + (f" — {dest['url']}" if dest.get("url") else "")
        + ".",
        f"Terms: {rec.get('terms', '')}",
        "The payment itself is yours at the checkout — "
        "OG never enters card details, never presses "
        "buy, and never pays. If the page shows a "
        "different number than this card, stop: that "
        "difference is yours to judge before you pay.",
    ]
    if dest.get("kind") == "app_store":
        lines.append(
            "This one runs through Apple/Google billing "
            "on your store account, start to finish.")
    if dest.get("kind") == "marketplace":
        lines.append(
            "The meetup and the money change hands in "
            "person — your move, as always.")
    return _result(_TAG, "Buying — handed off",
                   "\n".join(lines))


def _do_decline(uid: str, claim: Dict) -> List[Dict]:
    rec = _card(uid)
    if rec is None:
        return _result(
            _TAG, "Buying — nothing to decline",
            "There's no live card standing, so there's "
            "nothing to kill. Nothing was ordered and "
            "nothing was charged (nothing ever is from "
            "me).")
    rec = dict(rec)
    rec["status"] = "declined"
    rec["at"] = time.time()
    _save_buy_rec(uid, rec)
    _drop_card(uid)
    return _result(
        _TAG, "Buying — declined, dead",
        f"Declined — dead. The prepared buy of "
        f"'{rec['title']}' is dropped: no card stands, "
        "nothing was ordered, and nothing was charged "
        "(nothing ever is from me). The pick itself "
        "stays saved if you want it later.")


def _do_status(uid: str, claim: Dict) -> List[Dict]:
    rec = _card(uid) or _buy_rec(uid)
    if not rec:
        return _result(
            _TAG, "Buying — nothing on the table",
            "No buy is prepared on your account. Pick "
            "something with the shopping seat, then say "
            "\"buy it\" and I'll prepare the card.")
    total, known, unknown = _totals(rec)
    live = _card(uid) is not None
    bits = [f"Your last prepared buy: {rec['title']} — "
            f"status: {rec.get('status', '')}"
            + (" (the card is live, waiting on your "
               "approve/decline)" if live else "")]
    bits.append(_total_block(rec))
    bits.append(f"Destination: "
                f"{rec['destination'].get('label', '')}.")
    return _result(_TAG, "Buying — your buy",
                   "\n".join(bits))


def _do_missing(uid: str, claim: Dict) -> List[Dict]:
    missing = claim.get("missing") or []
    bits = ["I can prepare that buy, but not yet — "
            "here's what's missing:"]
    for m in missing:
        bits.append(f"- {m}")
    bits.append(
        "Two ways to fill it: pick the item with the "
        "shopping seat first (\"find me a ... under "
        "$N\", then \"I'll take #1\"), then say \"buy "
        "it\" — or say it in one line: \"buy the "
        "<item> for $<price> from <store or link>, "
        "shipping $<n>, tax <n>%\". I prepare to the "
        "approval wall; I never pay.")
    return _result(_TAG, "Buying — what's missing",
                   "\n".join(bits))


def buying_results(claim: Dict, uid: str) -> List[Dict]:
    op = claim.get("op")
    if op == "by_prepare":
        return _do_prepare(uid, claim)
    if op == "by_approve":
        return _do_approve(uid, claim)
    if op == "by_decline":
        return _do_decline(uid, claim)
    if op == "by_status":
        return _do_status(uid, claim)
    if op == "by_missing":
        return _do_missing(uid, claim)
    return _do_status(uid, claim)


def status_for(uid: str) -> Dict:
    """The read seam (/buying/status): the caller's
    own last prepared buy + whether its card is live."""
    out: Dict = {"buy": None, "card_live": False}
    rec = _card(uid) or _buy_rec(uid)
    if rec:
        total, known, unknown = _totals(rec)
        out["buy"] = {
            "item": rec.get("item", ""),
            "title": rec.get("title", ""),
            "status": rec.get("status", ""),
            "total": total, "known": known,
            "unknown": unknown,
            "destination":
                (rec.get("destination") or {}).get("label", ""),
            "at": float(rec.get("at", 0) or 0)}
        out["card_live"] = _card(uid) is not None
    return out


# --- Resolving the item: the pick, the shortlist, or the ask itself ---------------------

_ORDINALS = {"first": 1, "second": 2, "third": 3,
             "fourth": 4, "fifth": 5}
_BUY_VERB_RE = re.compile(r"\b(?:buy|purchase)\b", re.I)
_BUY_LEAD_RE = re.compile(
    r"^\s*(?:ok\s+|okay\s+|so\s+|please\s+|yeah\s+)?"
    r"(?:buy|purchase)\s+", re.I)
_PREPARE_RE = re.compile(
    r"\bprepare\s+(?:the\s+|my\s+)?(?:purchase|buy)\b|"
    r"\bprepare\s+it\b", re.I)
_REF_IT_RE = re.compile(
    r"\b(?:buy|purchase)\s+(?:it|that|this)\b", re.I)
_REF_HASH_RE = re.compile(r"#\s?(\d)\b")
_REF_ORD_RE = re.compile(
    r"\b(?:buy|purchase)\s+the\s+"
    r"(first|second|third|fourth|fifth)\s+one\b", re.I)
_REF_NAME_RE = re.compile(
    r"\b(?:buy|purchase)\s+the\s+"
    r"([a-z][a-z0-9&+.\-]{2,20})\s+one\b", re.I)
_PICKUP_RE = re.compile(
    r"\bpickup\s+in\s+([a-z][a-z ]{1,24}?)\s*(?:\.|,|$)",
    re.I)

_MISSING_PICK = (
    "a chosen item — there's no saved pick on your "
    "account for me to prepare from")


def _shopping_state(uid: str):
    """(pick, shown) read at the data level off the
    Shopping seat — its own store, never forked."""
    pick, shown = None, []
    try:
        import og_shopping as SH
        pick = SH._pick(uid)
        state = SH._search(uid)
        if state:
            shown = state.get("shown") or []
    except Exception:
        pass
    return pick, shown


def _find_named(shown, pick, needle: str):
    needle = str(needle or "").lower()
    if not needle:
        return None
    for o in shown:
        if needle in str(o.get("title") or "").lower():
            return o
    if pick and needle in str(
            (pick.get("option") or {}).get("title")
            or "").lower():
        return pick.get("option")
    return None


def _terms_for(raw: str, dest: Dict) -> str:
    m = _PICKUP_RE.search(raw)
    if m:
        town = " ".join(m.group(1).split())
        return (f"Pickup in {town} — the meetup is "
                "your move; I don't arrange it.")
    if dest.get("kind") == "marketplace":
        return ("Pickup from the seller — the meetup "
                "is your move; I don't arrange it.")
    if dest.get("kind") == "app_store":
        return ("Delivery is instant off the store "
                "once your hand completes it there.")
    return ("Delivery — the merchant's page names the "
            "window before you pay; no date is promised "
            "here.")


def _build_rec(raw: str, uid: str, option: Optional[Dict],
               item_label: str, search_item: str = "") -> Dict:
    """Assemble the prepared-buy record from the
    resolved option (if any) + the ask's own stated
    numbers. Ask-stated numbers outrank the listing's:
    they're the freshest word."""
    ship_v, ship_n, ship_span = _extract_shipping(raw)
    tax_kind, tax_v, tax_span = _extract_tax(raw)
    fee_v, fee_span = _extract_fees(raw)
    dest_ask = _extract_destination(raw)
    variant = _extract_variant(raw)
    qty = _extract_qty(raw)

    title, unit, price_note = "", None, ""
    dest = dest_ask
    if option is not None:
        title = str(option.get("title") or "")
        unit = option.get("price")
        asof = str(option.get("asof") or "")
        price_note = ("the pick's printed price"
                      + (f", as of {asof}" if asof else "")
                      + " — the listing's own word, not a "
                        "live quote")
        if dest is None:
            dest = _pick_destination(option)
    # The ask's own price: the first $ left after the
    # shipping/tax/fee spans are blanked out.
    work = _blank_spans(raw, [s for s in
                              (ship_span, tax_span, fee_span)
                              if s])
    if dest_ask is not None and dest_ask.get("url"):
        work = work.replace(dest_ask["url"], " ")
    m = _PRICE_RE.search(work)
    ask_price = _amount(m.group(1)) if m else None
    if ask_price is not None:
        unit = ask_price
        price_note = "as you stated it in this ask"
        if not title:
            title = item_label
    if not title:
        title = item_label
    if not variant and option is not None:
        variant = "as listed"
    # Shipping / tax defaults ride the lane.
    shipping, shipping_note = ship_v, ship_n
    tax, tax_note = None, ""
    if tax_kind == "amount":
        tax, tax_note = tax_v, "as you stated"
    elif tax_kind == "rate" and tax_v is not None \
            and unit is not None:
        tax = round(qty * float(unit) * tax_v / 100.0, 2)
        tax_note = f"at the {tax_v:g}% you named"
    elif tax_kind == "rate":
        tax_note = ("a rate was named but there's no "
                    "price to run it on")
    fees = fee_v if fee_v is not None else 0.0
    fees_note = "as you stated" if fee_v is not None \
        else "none stated"
    if dest is not None and dest.get("kind") == "marketplace":
        if shipping is None:
            shipping, shipping_note = 0.0, "none — pickup"
        if tax is None and tax_kind == "":
            tax, tax_note = 0.0, "private seller — no tax on the listing"
    rec = {
        "item": search_item or item_label or title,
        "title": title, "variant": variant, "qty": qty,
        "unit_price": unit, "price_note": price_note,
        "shipping": shipping, "shipping_note": shipping_note,
        "tax": tax, "tax_note": tax_note,
        "fees": fees, "fees_note": fees_note,
        "destination": dest or {},
        "terms": _terms_for(raw, dest or {}),
        "status": "draft",
    }
    return rec


def _fully_specified(raw: str) -> bool:
    """A direct ask that states the whole buy — a price
    AND a destination. That's a chosen item (the
    laptop-hunt shape), not a search: Shopping's
    search claim on it is not a deferral. Anything
    less specified stays a search, Shopping's job."""
    if _extract_destination(raw) is None:
        return False
    ship_span = _extract_shipping(raw)[2]
    tax_span = _extract_tax(raw)[2]
    fee_span = _extract_fees(raw)[1]
    work = _blank_spans(raw, [s for s in
                              (ship_span, tax_span, fee_span)
                              if s])
    return _PRICE_RE.search(work) is not None


def _parse_buy(raw: str, uid: str) -> Optional[Dict]:
    """The buy parse: a claim dict (by_prepare with the
    record, or by_missing naming what's absent), or
    None when this isn't a buy this seat can stand
    behind."""
    pick, shown = _shopping_state(uid)
    option, item_label = None, ""
    has_verb = bool(_BUY_VERB_RE.search(raw)) \
        or bool(_PREPARE_RE.search(raw))
    if not has_verb:
        return None
    idx = None
    m = _REF_HASH_RE.search(raw)
    if m:
        idx = int(m.group(1))
    m = _REF_ORD_RE.search(raw)
    if m:
        idx = _ORDINALS[m.group(1).lower()]
    if idx is not None:
        if shown and 1 <= idx <= len(shown):
            option = shown[idx - 1]
        elif pick:
            option = pick.get("option")
        else:
            return {"op": "by_missing",
                    "missing": [_MISSING_PICK],
                    "raw": raw}
    elif _REF_IT_RE.search(raw) or _PREPARE_RE.search(raw):
        # A short reference ("buy it", "prepare the
        # purchase") means the saved pick — but only
        # when the ask carries nothing of its own (no
        # price, no destination): a priced, placed ask
        # falls through to the direct path below.
        short = len(raw.split()) <= 5 \
            and _PRICE_RE.search(raw) is None \
            and _extract_destination(raw) is None
        if short:
            if pick:
                option = pick.get("option")
                item_label = str(pick.get("item") or "")
            else:
                return {"op": "by_missing",
                        "missing": [_MISSING_PICK],
                        "raw": raw}
    if option is None:
        m = _REF_NAME_RE.search(raw)
        named = _find_named(shown, pick,
                            m.group(1)) if m else None
        if named is not None:
            option = named
        else:
            lead = _BUY_LEAD_RE.match(raw)
            if not lead:
                return None
            tail = raw[lead.end():]
            item_label = _clean_item_tail(tail)
            if item_label in ("it", "that", "this", "one"):
                # "buy it from <place>" — the pronoun
                # still means the saved pick.
                if pick:
                    option = pick.get("option")
                    item_label = str(pick.get("item")
                                     or "")
                else:
                    return {"op": "by_missing",
                            "missing": [_MISSING_PICK],
                            "raw": raw}
            elif not item_label:
                return None
            elif option is None:
                dest_ask = _extract_destination(raw)
                work = _blank_spans(raw, [s for s in (
                    _extract_shipping(raw)[2],
                    _extract_tax(raw)[2],
                    _extract_fees(raw)[1]) if s])
                has_price = _PRICE_RE.search(work) is not None
                if not has_price and dest_ask is None:
                    # A bare "buy the X" with nothing
                    # priced and nowhere named isn't a
                    # buy this seat can prepare — leave
                    # it to the general lane (finding it
                    # is Shopping's).
                    return None
    rec = _build_rec(raw, uid, option, item_label,
                     search_item=(str(pick.get("item") or "")
                                  if option is not None and pick
                                  and option is pick.get("option")
                                  else ""))
    missing = []
    if rec.get("unit_price") is None:
        missing.append(
            "the price — nothing in the ask or the "
            "listing states one")
    if not rec.get("destination"):
        missing.append(
            "where it comes from — name the store or "
            "paste the listing's address")
    if missing:
        return {"op": "by_missing", "missing": missing,
                "raw": raw}
    return {"op": "by_prepare", "rec": rec, "raw": raw}


# --- Chat claims (narrow frames — the crew/PM/filer doctrine) ---------------------------

_GARBLE_VOCAB = ("buying", "buy", "purchase", "approve",
                 "decline", "total", "seller",
                 "marketplace", "checkout", "price")

_APPROVE_RE = re.compile(
    r"^\W*(yes|yeah|yep|yup|approve|approved|go ahead|"
    r"do it|confirm|ok|okay|sounds good|let'?s go)\b")
_DECLINE_RE = re.compile(
    r"^\W*(no|nope|nah|decline|declined|cancel|scrap|"
    r"discard|never ?mind|stop|don'?t|do not)\b")
_STATUS_RE = re.compile(
    r"\bbuying\s+status\b|\bwhat'?s\s+my\s+buy\b|"
    r"\bshow\s+(?:me\s+)?(?:my\s+)?buy\b|"
    r"\bmy\s+prepared\s+buy\b", re.I)


def _other_claim(message: str, uid: str):
    """(source, claim) of the first inner seat that
    claims this message exactly, else None. The PM,
    the Filer, the crew, the Builder, the Wood Pro,
    MaintEng, the Mechanic, the Blueprint Specialist
    own their asks — and so do the R54 marketplace
    chain and the ordering pack: checked exactly,
    never executed from here. ONE exception, by
    design: Shopping's buy-stop claim is the door
    R116 pointed at this seat — it is NOT a deferral
    (every other Shopping claim is)."""
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
            if c.get("op") == "sh_stop" \
                    and c.get("kind") == "buy":
                pass  # this seat is that door — fall through
            elif c.get("op") == "sh_search" \
                    and (_fully_specified(message)
                         or (_REF_IT_RE.search(message)
                             and _shopping_state(uid)[0])):
                pass  # a chosen item stated whole
                      # (price + place): this seat's
                      # direct door, not a search — or
                      # "buy it" naming the saved pick,
                      # where the "new search" reading is
                      # an artifact of an address riding
                      # in the ask's tail
            else:
                return "shopping", c
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
    card = _card(uid)
    if card is not None:
        # A live card stands: approve/decline bind to it
        # — but only when no other flow claims the words
        # first (their pendings outrank this card).
        trimmed = raw.lower().strip()
        is_approve = bool(_APPROVE_RE.match(trimmed))
        is_decline = bool(_DECLINE_RE.match(trimmed))
        if (is_approve or is_decline) \
                and not _defer_to_others(raw, uid):
            return {"op": "by_approve" if is_approve
                    else "by_decline", "raw": raw}
    if _defer_to_others(raw, uid):
        return None
    if _STATUS_RE.search(raw):
        return {"op": "by_status", "raw": raw}
    return _parse_buy(raw, uid)


def _claim_buying(message: str, uid: str):
    """(claim, garble_info): the exact claim first; on
    a miss, the Round 75 second chance — og_garble
    repairs the TRIGGER words only; the payload (the
    prices, the address) passes verbatim."""
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


def install_buying_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search OUTSIDE the
    Shopping seam (installed last, from the og_fixqueue
    chain) so buying asks claim here and everything
    else delegates back untouched. Persona files never
    touched."""
    if getattr(agent_instance, "_og_buying_installed",
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
            if uid and buying_enabled():
                claim, info = _claim_buying(
                    str(message), uid)
                if claim:
                    _pending["claim"] = claim
                    _pending["garble"] = info
                    # The one takeover: a Shopping
                    # buy-stop parked this exchange is
                    # superseded by this seat's claim —
                    # clear it so the inner seam can't
                    # also fire on the same words.
                    try:
                        import og_shopping as _sh
                        _sh._pending["claim"] = None
                        _sh._pending["garble"] = None
                    except Exception:
                        pass
                    if isinstance(intent, dict):
                        intent[
                            "needs_code_generation"] = False
                        if not intent.get("needs_web_search"):
                            intent["needs_web_search"] = True
                            intent["search_query"] = \
                                str(message).strip()
        except Exception as e:
            logger.warning(f"Buying trigger check failed: {e}")
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
                results = buying_results(claim, uid)
            except Exception as e:
                logger.warning(f"Buying op failed: {e}")
                results = _result(
                    _TAG, "Buying — glitch",
                    "That ask glitched — nothing was "
                    "prepared, ordered, or charged. Say "
                    "it again and I'll retry.")
        if results is None:
            results = prev_search(query, num_results)
        if garble and results:
            results = og_garble.annotate(results, garble,
                                         prepend=True)
        return results

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_buying_installed = True


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


def register_buying_routes(app):
    """GET /buying/status — the caller's own last
    prepared buy + whether its card is live."""

    @app.get("/buying/status")
    async def buying_status_route(request: Request):
        uid = _route_uid(request)
        view = status_for(uid) if uid else {
            "buy": None, "card_live": False}
        return {"ok": True, "enabled": buying_enabled(),
                **view}
