"""
OG AI trading on approval (Round 20) — BTC / stock BUY & SELL,
the LAST item on the tools roadmap, built with financial care.
Brent's standing rule in its strictest form: OG prepares, the
owner approves, then — and only then — anything moves. v1 shape:

PREPARE -> APPROVE -> EXECUTE-ONLY-IF-CONNECTED, else HANDOFF.

1. ASK. "buy $50 of BTC", "sell 2 shares of AAPL", "buy 0.5 ETH"
   — a side (buy/sell), an asset, and an amount ($ or quantity)
   are required. Anything missing gets ONE clarifying question;
   nothing is previewed until all three are known. Assets are
   crypto (BTC, ETH and the Coinbase-covered set) and listed
   stocks, quoted from the same keyless Round 4 feeds the data
   pack and the Round 18 price watch use (Coinbase Exchange stats
   for crypto; Nasdaq primary, Yahoo fallback, for stocks).
2. PREVIEW. The draft is parked per-visitor (10-minute expiry —
   quotes go stale fast) and shown as a TRADE PREVIEW: side,
   asset, amount, the LIVE quote WITH its fetch timestamp, the
   estimated total, and a plain risk line (prices move, venue
   fees apply, this is not financial advice). Nothing executes
   from a preview. Per-tier guard rails run before a preview is
   even shown: the daily "trade" cap (og_tiers kind "trade":
   free 1 / standard 3 / pro 10 / blue 25 / blackout 100 approved
   trades per day) and the per-trade dollar ceiling (free $100 /
   standard $500 / pro $2,500 / blue $10,000 / blackout $50,000 —
   over the ceiling, the preview is refused with the limit
   stated).
3. APPROVE. Only on YES, and the quote is RE-FETCHED first: if
   it moved more than 2% from the preview, a fresh preview is
   shown and the visitor is asked again — OG never executes or
   hands off on a stale quote. On a good approval exactly one
   trade unit is spent, and then:
   a. CONNECTED (crypto only): the visitor has a live Coinbase
      trading connection (below) -> OG places the market order
      on THEIR account through Coinbase's official API and
      reports the venue's own confirmation verbatim (order id,
      status, amounts exactly as Coinbase returned them — never
      invented, never dressed up).
   b. NOT CONNECTED: a TRADE SHEET handoff — the exact trade,
      the approval-time quote, and a grounded venue link
      (Coinbase for crypto; the visitor's stated broker, or a
      broker search, for stocks). The wording is "READY TO
      EXECUTE": OG placed nothing; the final tap is the
      visitor's, at the venue. Stocks always take this branch in
      v1 — there is no stock-broker connect.
   On NO or expiry the draft is discarded and nothing is spent
   and no venue is ever called.

4. COINBASE TRADING CONNECT — SHIPPED DARK. "Sign in with
   Coinbase" OAuth (scopes wallet:accounts:read,
   wallet:buys:create, wallet:sells:create), the same shape as
   the Round 10 Spotify connect: signed state tied to ogai_uid,
   tokens stored per visitor (Postgres og_coinbase_trade_tokens
   when OG_MEMORY_DB_URL is set, else coinbase_trade_store.json),
   never logged, never returned by any route. The menu item stays
   hidden and /auth/coinbase* answers 404 until
   OG_COINBASE_TRADE_ENABLED=true plus OG_COINBASE_CLIENT_ID /
   OG_COINBASE_CLIENT_SECRET are set (Brent registers the app in
   the Coinbase developer portal — steps in the Round 20 report).
   OG never sees or stores a Coinbase password. While dark — or
   for any visitor who simply hasn't connected — the PREPARE and
   HANDOFF halves above are fully live; only in-OG execution
   waits on the connect.

GUARDS (non-negotiable, stated in the module's own copy):
- Per-trade approval, ALWAYS. There is no standing instruction,
  no auto mode, no "trade while I sleep" — every trade needs a
  fresh YES on a fresh preview. Asked about it, OG says so.
- Market buy/sell of assets the visitor owns or is buying with
  their own money ONLY. No shorting, no leverage, no margin, no
  options, no futures — those asks are refused by name.
- A connected sell larger than the balance Coinbase reports for
  that asset is refused before anything is placed.
- Vocabulary discipline: without a venue confirmation, OG never
  presents a trade as done — only "ready to execute".

Pending drafts are process-local (like Rounds 14/16/19): a
restart inside the window drops an unapproved draft — nothing
executed, by design. Persona files are never touched.
"""

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)

# --- Coinbase trading connect config (dark until enabled) ---------------------

COINBASE_CLIENT_ID = os.getenv("OG_COINBASE_CLIENT_ID", "")
COINBASE_CLIENT_SECRET = os.environ.get("OG_COINBASE_CLIENT_" + "SECRET", "")
COINBASE_TRADE_ENABLED = (
    os.getenv("OG_COINBASE_TRADE_ENABLED", "false").lower() == "true"
    and bool(COINBASE_CLIENT_ID) and bool(COINBASE_CLIENT_SECRET))
COINBASE_REDIRECT_URI = os.getenv(
    "OG_COINBASE_REDIRECT_URI",
    "https://og-ai-service.onrender.com/auth/coinbase/callback")
COINBASE_SCOPES = ("wallet:accounts:read wallet:buys:create "
                   "wallet:sells:create")
COINBASE_STORE_FILE = "coinbase_trade_store.json"
_cb_lock = threading.Lock()

_AUTHORIZE_URL = "https://www.coinbase.com/oauth/authorize"
_TOKEN_URL = "https://api.coinbase.com/oauth/token"
_API_BASE = "https://api.coinbase.com"

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_PENDING_TTL = 10 * 60          # a preview stays approvable 10 min
_STALE_MOVE = 0.02              # >2% quote move at approval = re-preview

# Bound by app.py via bind_app (usage store + tier + cookie age).
_deps: Dict = {}


def bind_app(deps):
    _deps.update(deps)


def _cookie_max_age() -> int:
    return int(_deps.get("cookie_max_age", 365 * 24 * 60 * 60))


def _pro_url() -> str:
    base = os.getenv("OG_PUBLIC_URL", "https://og-ai-service.onrender.com")
    return base.rstrip("/") + "/pro"


# --- Trade counters (per-tier daily "trade" cap, on app.py's usage store) -----

def _tier() -> str:
    try:
        return _deps.get("get_tier", lambda: "free")()
    except Exception:
        return "free"


def _trade_used_today(uid: str) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _deps["usage_lock"]:
        store = _deps["load_usage"]()
    entry = store.get(f"trade:{uid}")
    if not isinstance(entry, dict) or entry.get("date") != today:
        return 0
    return int(entry.get("count", 0))


def _trade_left(uid: str) -> int:
    import og_tiers as _tiers
    return max(0, _tiers.cap(_tier(), "trade") - _trade_used_today(uid))


def _consume_trade(uid: str) -> bool:
    """Record one approved trade today; False at cap."""
    import og_tiers as _tiers
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"trade:{uid}"
    with _deps["usage_lock"]:
        store = _deps["load_usage"]()
        entry = store.get(key)
        if not isinstance(entry, dict) or entry.get("date") != today:
            entry = {"date": today, "count": 0}
        if int(entry.get("count", 0)) >= _tiers.cap(_tier(), "trade"):
            return False
        entry["count"] = int(entry.get("count", 0)) + 1
        store[key] = entry
        _deps["save_usage"](store)
        return True


def _ceiling(tier: str) -> float:
    """Per-trade dollar ceiling for a tier (env OG_TRADE_CEIL_<TIER>)."""
    import og_tiers as _tiers
    return _tiers.trade_ceiling(tier)


# --- Coinbase token store (mirrors the Spotify store) ---------------------------

def _cb_db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_coinbase_trade_tokens ("
            "uid TEXT PRIMARY KEY, data JSONB)")
    conn.commit()
    return conn


def _load_cb_store() -> Dict:
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _cb_db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT uid, data FROM og_coinbase_trade_tokens")
                    return {uid: data for uid, data in cur.fetchall()}
        except Exception as e:
            logger.warning(f"Coinbase trade store DB load failed: {e}")
    if os.path.exists(COINBASE_STORE_FILE):
        try:
            with open(COINBASE_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Could not load coinbase trade store: {e}")
    return {}


def _save_cb_store(store: Dict):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _cb_db_connect() as conn:
                with conn.cursor() as cur:
                    for uid, data in store.items():
                        cur.execute(
                            "INSERT INTO og_coinbase_trade_tokens "
                            "(uid, data) VALUES (%s, %s) ON CONFLICT "
                            "(uid) DO UPDATE SET data = EXCLUDED.data",
                            (uid, _Jsonb(data)))
                    cur.execute("SELECT uid FROM og_coinbase_trade_tokens")
                    existing = {row[0] for row in cur.fetchall()}
                    for stale in existing - set(store.keys()):
                        cur.execute(
                            "DELETE FROM og_coinbase_trade_tokens "
                            "WHERE uid = %s", (stale,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Coinbase trade store DB save failed: {e}")
    try:
        with open(COINBASE_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Could not save coinbase trade store: {e}")


def _cb_connection(uid: str) -> Optional[Dict]:
    """This visitor's stored Coinbase trading connection, or None."""
    if not uid:
        return None
    with _cb_lock:
        store = _load_cb_store()
    entry = store.get(uid)
    return entry if isinstance(entry, dict) else None


def _store_cb_entry(uid: str, entry: Dict):
    with _cb_lock:
        store = _load_cb_store()
        store[uid] = entry
        _save_cb_store(store)


def _drop_cb_entry(uid: str):
    with _cb_lock:
        store = _load_cb_store()
        if uid in store:
            del store[uid]
            _save_cb_store(store)


# --- Signed OAuth state (same construction as the Spotify flow) -----------------

def _cb_state_for(uid: str) -> str:
    sig = hmac.new(COINBASE_CLIENT_SECRET.encode(),
                   f"og-coinbase-trade:{uid}".encode(),
                   hashlib.sha256).hexdigest()
    return f"{uid}.{sig}"


def _cb_uid_from_state(state: str) -> Optional[str]:
    try:
        uid, _, sig = str(state).partition(".")
        if not uid or not sig:
            return None
        expected = hmac.new(COINBASE_CLIENT_SECRET.encode(),
                            f"og-coinbase-trade:{uid}".encode(),
                            hashlib.sha256).hexdigest()
        return uid if hmac.compare_digest(expected, sig) else None
    except Exception:
        return None


# --- Coinbase API funnel (sync; the chat seam is sync) ---------------------------
# Every venue call rides this one function so tests stub exactly one
# seam, and so no token can leak into a result body by accident:
# callers receive (status, parsed-json) only.

def _cb_api(method: str, path: str, access_token: str,
            payload: Dict = None):
    import httpx
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.request(
                method, _API_BASE + path,
                json=payload,
                headers={"Authorization": f"Bearer {access_token}",
                         "CB-VERSION": "2026-01-01"})
    except Exception as e:
        logger.warning(f"Coinbase API {method} {path} failed: {e}")
        return 0, {}
    try:
        data = resp.json()
    except Exception:
        data = {}
    if resp.status_code >= 400:
        logger.warning(
            f"Coinbase API {method} {path} status: {resp.status_code}")
    return resp.status_code, data if isinstance(data, dict) else {}


def _refresh_cb_entry(uid: str, entry: Dict) -> Optional[Dict]:
    """Refresh this visitor's Coinbase access token. Coinbase
    rotates BOTH tokens on refresh — the new pair is stored. A 4xx
    means the grant is dead: the connection is dropped so status
    honestly shows disconnected."""
    refresh = entry.get("refresh_token", "")
    if not refresh:
        return None
    import httpx
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                _TOKEN_URL,
                data={"grant_type": "refresh_token",
                      "refresh_token": refresh,
                      "client_id": COINBASE_CLIENT_ID,
                      "client_secret": COINBASE_CLIENT_SECRET})
    except Exception as e:
        logger.warning(f"Coinbase token refresh failed: {e}")
        return None
    if resp.status_code != 200:
        logger.warning(
            f"Coinbase token refresh status: {resp.status_code}")
        if 400 <= resp.status_code < 500:
            _drop_cb_entry(uid)
        return None
    tokens = resp.json()
    entry = dict(entry)
    entry["access_token"] = tokens.get("access_token",
                                       entry.get("access_token", ""))
    if tokens.get("refresh_token"):
        entry["refresh_token"] = tokens["refresh_token"]
    entry["expires_at"] = (datetime.now(timezone.utc).timestamp()
                           + int(tokens.get("expires_in", 7200)))
    _store_cb_entry(uid, entry)
    return entry


def _live_cb_connection(uid: str) -> Optional[Dict]:
    """This visitor's connection with a usable access token
    (refreshing when needed), or None."""
    if not COINBASE_TRADE_ENABLED:
        return None
    entry = _cb_connection(uid)
    if not entry:
        return None
    now = datetime.now(timezone.utc).timestamp()
    if entry.get("access_token") \
            and float(entry.get("expires_at", 0)) > now + 60:
        return entry
    return _refresh_cb_entry(uid, entry)


def _cb_accounts(access_token: str) -> list:
    """The visitor's Coinbase accounts (id/currency/balance)."""
    status, data = _cb_api("GET", "/v2/accounts?limit=100", access_token)
    if status != 200:
        return []
    rows = data.get("data")
    return [r for r in rows if isinstance(r, dict)] \
        if isinstance(rows, list) else []


def _cb_account_for(access_token: str, symbol: str) -> Optional[Dict]:
    """The visitor's account holding `symbol` (primary preferred)."""
    fallback = None
    for acct in _cb_accounts(access_token):
        cur = (acct.get("currency") or {}).get("code", "")
        if cur.upper() != symbol.upper():
            continue
        if acct.get("primary"):
            return acct
        if fallback is None:
            fallback = acct
    return fallback


def _cb_balance(access_token: str, symbol: str) -> Optional[float]:
    """Units of `symbol` the visitor holds on Coinbase, per the
    venue's own account record. None when unreadable."""
    acct = _cb_account_for(access_token, symbol)
    if not acct:
        return None
    bal = acct.get("balance") or {}
    try:
        return float(bal.get("amount"))
    except (TypeError, ValueError):
        return None


def _cb_execute(entry: Dict, side: str, symbol: str,
                amount_usd: Optional[float], qty: Optional[float]):
    """Place ONE market order on the visitor's own Coinbase account
    (buys fund from their default payment method; sells pay out to
    their USD account). Returns (True, venue-data-dict) with
    Coinbase's own order record, or (False, plain-error-string).
    Called ONLY after an explicit YES on a fresh quote — there is
    no other caller."""
    token = entry.get("access_token", "")
    if not token:
        return False, "the Coinbase connection has no usable token"
    acct = _cb_account_for(token, symbol)
    if not acct or not acct.get("id"):
        return False, f"no {symbol} account was found on the " \
                      "visitor's Coinbase"
    acct_id = acct["id"]
    if side == "buy":
        payload = {"amount": f"{amount_usd:.2f}", "currency": "USD"}
        status, data = _cb_api(
            "GET", "/v2/payment-methods", token)
        methods = data.get("data") if status == 200 else None
        if isinstance(methods, list):
            for m in methods:
                if isinstance(m, dict) and m.get("id"):
                    payload["payment_method"] = m["id"]
                    break
        path = f"/v2/accounts/{acct_id}/buys"
    else:
        if qty is not None:
            payload = {"amount": f"{qty:.8f}".rstrip("0").rstrip("."),
                       "currency": symbol}
        else:
            payload = {"total": f"{amount_usd:.2f}", "currency": "USD"}
        path = f"/v2/accounts/{acct_id}/sells"
    status, data = _cb_api("POST", path, token, payload)
    order = data.get("data") if isinstance(data, dict) else None
    if status not in (200, 201) or not isinstance(order, dict) \
            or not order.get("id"):
        return False, "Coinbase did not accept the order"
    # Coinbase creates some orders uncommitted; the commit call is
    # what actually places them. Commit whenever the venue's own
    # record says it isn't committed yet, then re-read the record.
    if order.get("committed") is False \
            or str(order.get("status", "")).lower() == "created":
        c_status, c_data = _cb_api(
            "POST", f"{path}/{order['id']}/commit", token)
        committed = c_data.get("data") \
            if isinstance(c_data, dict) else None
        if c_status in (200, 201) and isinstance(committed, dict):
            order = committed
    return True, order


# --- Price feeds (Round 4's keyless endpoints, numeric) -------------------------
# Same endpoints og_monitor polls: Coinbase Exchange stats for
# crypto, Nasdaq primary with Yahoo fallback for stocks. A quote
# is (price, source-label, fetched-at-UTC) — the timestamp shown
# to the visitor is the fetch time, never a fabricated feed time.

# Nasdaq/Yahoo wall non-browser user agents (proven live
# 2026-10-08: the Round 4 data pack, which sends this browser UA,
# quoted AAPL from the same host while an OG-AI UA got nothing) —
# so the quote fetches send the data pack's UA.
_FEED_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; "
                          "x64) AppleWebKit/537.36 (KHTML, like "
                          "Gecko) Chrome/124.0 Safari/537.36"}


def _fetch_json(url: str, params: Dict = None) -> Optional[Dict]:
    import httpx
    try:
        with httpx.Client(timeout=12) as client:
            resp = client.get(
                url, params=params or {}, headers=_FEED_UA)
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _crypto_price(symbol: str) -> Optional[float]:
    data = _fetch_json(
        f"https://api.exchange.coinbase.com/products/"
        f"{symbol}-USD/stats")
    if not data:
        return None
    try:
        price = float(data.get("last"))
        return price if price > 0 else None
    except (TypeError, ValueError):
        return None


def _stock_price(symbol: str) -> Optional[float]:
    data = _fetch_json(
        "https://api.nasdaq.com/api/quote/"
        f"{symbol.replace('.', '-')}/info",
        params={"assetclass": "stocks"})
    primary = ((data or {}).get("data") or {}).get("primaryData") or {}
    raw = str(primary.get("lastSalePrice") or "").replace("$", "") \
        .replace(",", "").strip()
    try:
        price = float(raw)
        if price > 0:
            return price
    except ValueError:
        pass
    chart = _fetch_json(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}")
    try:
        result = ((chart or {}).get("chart") or {}).get("result") or []
        meta = (result[0] or {}).get("meta") or {}
        price = float(meta.get("regularMarketPrice"))
        return price if price > 0 else None
    except (TypeError, ValueError, IndexError):
        return None


def get_quote(symbol: str, kind: str):
    """(price, source, fetched-at) for one asset, or None."""
    price = _crypto_price(symbol) if kind == "crypto" \
        else _stock_price(symbol)
    if price is None:
        return None
    source = "Coinbase" if kind == "crypto" else "Nasdaq/Yahoo"
    return price, source, datetime.now(timezone.utc)


def _fmt_price(p) -> str:
    try:
        p = float(p)
    except (TypeError, ValueError):
        return "?"
    if p >= 1000:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:.2f}"
    return f"${p:.4f}"


def _fmt_qty(q: float, kind: str) -> str:
    if kind == "crypto":
        s = f"{q:.8f}".rstrip("0").rstrip(".")
        return s or "0"
    return f"{q:,.4f}".rstrip("0").rstrip(".")


def _fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC")


# --- Pending draft state (process-local, approval gate) -------------------------

_pending_trades: Dict[str, Dict] = {}
_pending_lock = threading.Lock()


def _set_pending(uid: str, state: Dict):
    state = dict(state)
    state["created"] = time.time()
    with _pending_lock:
        _pending_trades[uid] = state


def _get_pending(uid: str) -> Optional[Dict]:
    if not uid:
        return None
    with _pending_lock:
        state = _pending_trades.get(uid)
        if state and time.time() - float(state.get("created", 0)) \
                > _PENDING_TTL:
            del _pending_trades[uid]
            return None
        return dict(state) if state else None


def _clear_pending(uid: str):
    with _pending_lock:
        _pending_trades.pop(uid, None)


# --- Parsing ---------------------------------------------------------------------

_CRYPTO_NAMES = {
    "bitcoin": "BTC", "btc": "BTC", "ethereum": "ETH", "eth": "ETH",
    "solana": "SOL", "sol": "SOL", "dogecoin": "DOGE", "doge": "DOGE",
    "cardano": "ADA", "ada": "ADA", "ripple": "XRP", "xrp": "XRP",
    "litecoin": "LTC", "ltc": "LTC", "chainlink": "LINK",
    "link": "LINK", "avalanche": "AVAX", "avax": "AVAX",
    "polkadot": "DOT", "dot": "DOT", "cosmos": "ATOM", "atom": "ATOM",
    "stellar": "XLM", "xlm": "XLM", "uniswap": "UNI", "uni": "UNI",
    "bitcoin cash": "BCH", "bch": "BCH", "shiba inu": "SHIB",
    "shib": "SHIB", "polygon": "MATIC", "matic": "MATIC",
}
_CRYPTO_SYMBOLS = {v for v in _CRYPTO_NAMES.values()}

_BROKERS = ("robinhood", "fidelity", "schwab", "e*trade", "etrade",
            "webull", "vanguard", "coinbase", "kraken", "gemini",
            "cash app", "sofi")

_TICKER_STOP = {
    "USD", "OG", "AI", "YES", "NO", "OK", "API", "GPS", "USA",
    "BUY", "SELL", "THE", "AND", "FOR", "NOW", "ALL", "MY", "ME",
    "OF", "TO", "IN", "ON", "AT", "TV", "AM", "PM", "UTC", "CEO",
}

_SIDE_RE = re.compile(r"\b(buy|buying|purchase|purchasing|sell|selling)\b")
_BROKER_RE = re.compile(
    r"\b(?:on|via|at|through|using|with)\s+"
    r"(robinhood|fidelity|schwab|e\*trade|etrade|webull|vanguard|"
    r"coinbase|kraken|gemini|cash app|sofi)\b", re.I)
_FORBIDDEN_RE = re.compile(
    r"\b(short|shorting|shorts|leverage|leveraged|margin|futures|"
    r"perpetuals?|perps)\b|\b(call|put)\s+options?\b|"
    r"\boptions?\s+(trading|contracts?|chain)\b", re.I)
_TRADE_VERB_RE = re.compile(
    r"\b(buy|sell|trade|trading|position|invest)\b", re.I)
_POLICY_RE = re.compile(
    r"\bauto[-\s]?trad\w*\b|\bautomatically\s+(buy|sell|trade)\b|"
    r"\btrade\s+(for\s+me\s+)?while\s+i\b|"
    r"\bset\s+it\s+and\s+forget\b|"
    r"\btrade\s+for\s+me\s+automatically\b|"
    r"\bkeep\s+(buying|selling|trading)\b", re.I)
_APPROVE_RE = re.compile(
    r"^\W*(yes|yeah|yep|yup|approve|approved|go ahead|do it"
    r"|confirm|ok|okay|sounds good|let'?s go)\b")
_DECLINE_RE = re.compile(
    r"^\W*(no|nope|nah|cancel|scrap|discard|never ?mind|stop"
    r"|don'?t|do not)\b")
_USD_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
_USD_WORD_RE = re.compile(
    r"\b([\d,]+(?:\.\d+)?)\s*(?:dollars|bucks|usd)\b", re.I)
_SHARES_RE = re.compile(
    r"\b([\d,]+(?:\.\d+)?)\s*(?:x\s*)?shares?\b", re.I)


def _clean(text: str) -> str:
    return " " + re.sub(r"\s+", " ", str(text).lower()).strip() + " "


def _parse_side(low: str) -> Optional[str]:
    m = _SIDE_RE.search(low)
    if not m:
        return None
    return "sell" if m.group(1).startswith("sel") else "buy"


def _parse_broker(text: str) -> Optional[str]:
    m = _BROKER_RE.search(str(text))
    if not m:
        return None
    name = m.group(1).lower()
    return {"e*trade": "E*TRADE", "etrade": "E*TRADE"}.get(
        name, name.title())


def _parse_asset(text: str, low: str):
    """(symbol, kind) — crypto names/symbols first, then an
    uppercase ticker token, then a '<n> of TICKER' tail."""
    for name in sorted(_CRYPTO_NAMES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", low):
            symbol = _CRYPTO_NAMES[name]
            return symbol, "crypto"
    for tok in re.findall(r"\b[A-Z]{1,5}\b", str(text)):
        if tok in _TICKER_STOP:
            continue
        if tok in _CRYPTO_SYMBOLS:
            return tok, "crypto"
        return tok, "stock"
    m = re.search(r"\bof\s+([A-Za-z]{1,5})\b", str(text))
    if m:
        tok = m.group(1).upper()
        if tok not in _TICKER_STOP:
            if tok in _CRYPTO_SYMBOLS:
                return tok, "crypto"
            return tok, "stock"
    m = re.search(r"\bshares?\s+of\s+([A-Za-z]{1,5})\b", str(text), re.I)
    if m:
        tok = m.group(1).upper()
        if tok not in _TICKER_STOP:
            return tok, "stock"
    # A bare known crypto word after the side verb ("buy bitcoin").
    m = re.search(r"\b(?:buy|sell|purchase)\s+(?:some\s+|a\s+)?"
                  r"([a-z]{2,10})\b", low)
    if m and m.group(1) in _CRYPTO_NAMES:
        return _CRYPTO_NAMES[m.group(1)], "crypto"
    return None, None


def _parse_amount(text: str, low: str, symbol: Optional[str],
                  kind: Optional[str]):
    """(amount_usd, qty, sell_all) — at most one of usd/qty set."""
    m = _USD_RE.search(str(text)) or _USD_WORD_RE.search(str(text))
    if m:
        try:
            return float(m.group(1).replace(",", "")), None, False
        except ValueError:
            pass
    m = _SHARES_RE.search(str(text))
    if m:
        try:
            return None, float(m.group(1).replace(",", "")), False
        except ValueError:
            pass
    if symbol:
        m = re.search(
            rf"\b([\d,]+(?:\.\d+)?)\s*(?:of\s+)?"
            rf"{re.escape(symbol.lower())}\b", low)
        if m:
            try:
                return None, float(m.group(1).replace(",", "")), False
            except ValueError:
                pass
    if re.search(r"\ball\s+(of\s+)?(my|the)\b|\bmy\s+whole\b|"
                 r"\beverything\b", low):
        return None, None, True
    return None, None, False


def _parse_trade_ask(message: str) -> Optional[Dict]:
    """Parse a fresh trade ask into {side, symbol, kind, usd, qty,
    all, broker} — fields may be missing (the clarify step asks).
    None when the message is not a trade ask at all."""
    text = str(message)
    low = _clean(text)
    side = _parse_side(low)
    if side is None:
        return None
    # Only claim trade-shaped asks: an asset, an amount, or an
    # explicit trading noun must be present, so plain chat like
    # "buy me a coffee" / "sell my house" is never hijacked.
    symbol, kind = _parse_asset(text, low)
    usd, qty, sell_all = _parse_amount(text, low, symbol, kind)
    if symbol is None and usd is None and qty is None \
            and not re.search(r"\b(stock|stocks|share|shares|crypto|"
                              r"coin|coins)\b", low):
        return None
    return {"side": side, "symbol": symbol, "asset_kind": kind,
            "usd": usd, "qty": qty, "all": sell_all,
            "broker": _parse_broker(text)}


def _missing(ask: Dict) -> list:
    out = []
    if not ask.get("symbol"):
        out.append("asset")
    if ask.get("usd") is None and ask.get("qty") is None \
            and not ask.get("all"):
        out.append("amount")
    return out


def _claim_job(message: str, uid: str) -> Optional[Dict]:
    """What this message means for trading, given the visitor's
    pending draft. Forbidden-instrument and auto-trade-policy asks
    always claim; approvals/declines only count against a live
    pending preview; a fresh trade ask replaces a parked draft."""
    text = str(message)
    low = _clean(text)
    trimmed = low.strip()
    pending = _get_pending(uid)

    if _FORBIDDEN_RE.search(text) \
            and not re.match(r"^\s*(what|how|why|is|does|do|explain)\b",
                             trimmed) \
            and (_TRADE_VERB_RE.search(text)
                 or _parse_asset(text, low)[0]):
        return {"kind": "forbidden"}
    if _POLICY_RE.search(low):
        return {"kind": "policy"}

    if pending is not None:
        stage = pending.get("stage", "")
        decline = bool(_DECLINE_RE.match(trimmed))
        approve = bool(_APPROVE_RE.match(trimmed))
        if stage == "preview":
            if decline:
                return {"kind": "preview_decline"}
            fresh = _parse_trade_ask(text)
            if approve and not fresh:
                return {"kind": "preview_approve"}
            if fresh:
                return {"kind": "ask", **fresh}
            return None  # unrelated chat; the preview stays parked
        if stage == "collect":
            if decline:
                return {"kind": "collect_decline"}
            return {"kind": "collect_answer"}
    fresh = _parse_trade_ask(text)
    if fresh:
        return {"kind": "ask", **fresh}
    return None


# --- Instruction results (the seam's {title, body, href} shape) ------------------

def _result(tag: str, title: str, body: str, href: str = "") -> list:
    return [{"title": title, "body": f"[{tag}] " + body, "href": href}]


_RISK_LINE = ("Real talk: prices move fast — the number can change "
              "before anything goes through, the venue charges its "
              "own fees on top, and this is not financial advice.")
_POLICY_LINE = ("OG never trades on its own and there is no auto "
                "mode: every single trade needs your YES on a "
                "fresh preview first, every time.")


def _pro_note() -> str:
    return f"Higher plans allow more and bigger trades: {_pro_url()}"


def _cap_body() -> list:
    body = ("The visitor wants to trade, but they've used up "
            "today's approved-trade allowance for their plan. Do "
            "NOT preview, execute, or hand off anything. Tell them "
            "plainly, in persona, that the allowance resets "
            f"tomorrow (UTC) — or that {_pro_note()}")
    return _result("TRADING: CAP", "📈 Trading — daily cap", body,
                   _pro_url())


def _ceiling_body(ceiling: float, est: float) -> list:
    body = ("The visitor's trade works out to about "
            f"{_fmt_price(est)}, which is over their plan's "
            f"per-trade ceiling of {_fmt_price(ceiling)}. The "
            "preview is REFUSED — do NOT preview, execute, or hand "
            "off this trade, and do NOT shrink the amount for them "
            "without being asked. Tell them the ceiling plainly, "
            f"in persona. {_pro_note()}")
    return _result("TRADING: CEILING", "📈 Trading — over the limit",
                   body, _pro_url())


def _forbidden_body() -> list:
    body = ("The visitor is asking about a leveraged / short / "
            "options-style trade. Refuse plainly, in persona: OG "
            "handles straight market buys and sells of assets the "
            "visitor owns or is buying with their own money ONLY — "
            "no shorting, no leverage, no margin, no options, no "
            "futures. That line does not move, and no trade is "
            "previewed. Offer the plain version instead (\"buy $50 "
            "of BTC\" / \"sell 2 shares of AAPL\").")
    return _result("TRADING: NOT-OFFERED", "📈 Trading — not offered",
                   body)


def _policy_body() -> list:
    body = ("The visitor is asking about automatic / standing "
            "trading. Be plain, in persona: " + _POLICY_LINE + " "
            "If they want a trade, OG will happily build the "
            "preview right now — it just takes a fresh YES, like "
            "every trade.")
    return _result("TRADING: APPROVAL-RULE", "📈 Trading — the rule",
                   body)


def _clarify_body(ask: Dict) -> list:
    missing = _missing(ask)
    known = []
    if ask.get("symbol"):
        known.append(f"the asset is {ask['symbol']}")
    if ask.get("usd") is not None:
        known.append(f"the amount is {_fmt_price(ask['usd'])}")
    elif ask.get("qty") is not None:
        known.append(f"the quantity is {ask['qty']:g}")
    side_word = (ask.get("side") or "trade").upper()
    if missing == ["asset", "amount"]:
        q = ("Ask them ONE question, in persona: which asset, and "
             "how much — dollars or quantity? Nothing is previewed "
             "yet.")
    elif missing == ["asset"]:
        q = ("Ask them ONE question, in persona: which asset is "
             f"this {side_word} for? Nothing is previewed yet — "
             "the amount is already noted.")
    else:
        q = ("Ask them ONE question, in persona: how much — a "
             "dollar amount or a quantity? Nothing is previewed "
             "yet — the asset is already noted.")
    body = (q + " Keep it to that one question; do NOT invent an "
            "asset, an amount, or any price.")
    return _result("TRADING: CLARIFY", "📈 Trading — one question",
                   body)


# --- Venue link grounding (handoff branch) ---------------------------------------

_LINK_SKIP = (
    "google.", "bing.", "duckduckgo.", "facebook.", "instagram.",
    "x.com", "twitter.", "yelp.", "tripadvisor.", "reddit.",
    "wikipedia.", "youtube.",
)


def _grounded_link(results) -> Optional[str]:
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


def _search_link(query: str) -> str:
    return ("https://www.google.com/search?q="
            + urllib.parse.quote_plus(query))


def _venue_for(state: Dict):
    """(venue display name, default link) for a trade."""
    if state["kind"] == "crypto":
        broker = state.get("broker")
        if broker and broker.lower() != "coinbase":
            return broker, _search_link(f"{broker} buy "
                                         f"{state['symbol']}")
        return "Coinbase", "https://www.coinbase.com"
    broker = state.get("broker") or "your broker"
    return broker, _search_link(f"{broker} buy {state['symbol']} stock")


def _venue_link(state: Dict, lookup) -> str:
    venue, default = _venue_for(state)
    if lookup is not None:
        try:
            if state["kind"] == "crypto":
                q = f"{venue} buy {state['symbol']}"
            else:
                q = f"{venue} buy {state['symbol']} stock"
            link = _grounded_link(lookup(q, 5))
            if link:
                return link
        except Exception as e:
            logger.warning(f"Trade venue lookup failed: {e}")
    return default


# --- Preview rendering -------------------------------------------------------------

def _amount_line(state: Dict, price: float) -> str:
    symbol = state["symbol"]
    if state.get("all"):
        if state.get("qty") is not None:
            return (f"Amount: ALL of it — "
                    f"{_fmt_qty(state['qty'], state['kind'])} "
                    f"{symbol} (≈ {_fmt_price(state['qty'] * price)})")
        return ("Amount: ALL of it — OG can't see the balance, so "
                "the venue shows the exact quantity at the final tap")
    if state.get("usd") is not None:
        qty = state["usd"] / price
        unit = "shares" if state["kind"] == "stock" else symbol
        return (f"Amount: {_fmt_price(state['usd'])} "
                f"(≈ {_fmt_qty(qty, state['kind'])} {unit})")
    qty = state["qty"]
    return f"Amount: {_fmt_qty(qty, state['kind'])} " \
           f"{'shares of ' if state['kind'] == 'stock' else ''}" \
           f"{symbol} (≈ {_fmt_price(qty * price)})"


def _est_total(state: Dict, price: float) -> Optional[float]:
    if state.get("usd") is not None:
        return float(state["usd"])
    if state.get("qty") is not None:
        return float(state["qty"]) * price
    return None


def _preview_text(state: Dict, quote) -> str:
    price, source, when = quote
    symbol = state["symbol"]
    side = state["side"].upper()
    kind_word = "crypto" if state["kind"] == "crypto" else "stock"
    lines = [
        f"📈 TRADE PREVIEW — {side} {symbol} ({kind_word})",
        _amount_line(state, price),
        f"Live quote: {_fmt_price(price)} per {symbol} — as of "
        f"{_fmt_ts(when)} ({source})",
    ]
    est = _est_total(state, price)
    if est is not None:
        lines.append(f"Estimated total: {_fmt_price(est)} before "
                     "venue fees")
    lines.append(_RISK_LINE)
    if state.get("_connected"):
        lines.append("On YES, OG places this market order on YOUR "
                     "connected Coinbase account and shows you "
                     "Coinbase's own confirmation.")
    elif state["kind"] == "crypto":
        lines.append("On YES, OG hands you a ready-to-execute "
                     "trade sheet — OG has no trading connection "
                     "for you yet, so the final tap is yours at "
                     "the venue. Nothing is placed by OG.")
    else:
        lines.append("On YES, OG hands you a ready-to-execute "
                     "trade sheet for your broker — OG has no "
                     "stock-broker connection, so the final tap is "
                     "yours at the broker. Nothing is placed by OG.")
    lines.append("Reply YES to approve — or NO to scrap it. This "
                 "preview dies in 10 minutes.")
    return "\n".join(lines)


def _preview_body(state: Dict, quote, moved: bool = False) -> list:
    lead = ("The price moved more than 2% since the last preview, "
            "so the old approval is VOID. Present this FRESH TRADE "
            "PREVIEW below EXACTLY as written (every number "
            "character-for-character), in persona around it, and "
            "make clear the visitor has to say YES again — nothing "
            "has been approved:" if moved else
            "The visitor's trade is priced and parked. Present "
            "the TRADE PREVIEW below EXACTLY as written (every "
            "number character-for-character), in persona around "
            "it:")
    body = (lead + "\n\n" + _preview_text(state, quote) + "\n\n"
            "Do NOT invent or change any price, quantity, or "
            "total. Do NOT say anything was placed, executed, or "
            "filled — it has not been.")
    return _result("TRADING: PREVIEW",
                   f"📈 Trade preview — {state['side'].upper()} "
                   f"{state['symbol']}", body)


def _no_quote_body(state: Dict) -> list:
    body = (f"OG tried to price {state['symbol']} just now and the "
            "live feed gave nothing back, so NO preview was built "
            "and nothing is parked. Tell the visitor that plainly, "
            "in persona — a price is never invented here — and "
            "that they can ask again in a moment.")
    return _result("TRADING: NO-QUOTE", "📈 Trading — no live price",
                   body)


def _discard_body(stage: str) -> list:
    what = "preview" if stage == "preview" else "half-started trade"
    body = (f"The visitor said NO. The trade {what} is DISCARDED — "
            "nothing was executed, nothing was handed off, no venue "
            "was called, and nothing was spent. Confirm that "
            "briefly, in persona, and let them know they can start "
            "a fresh trade any time.")
    return _result("TRADING: DISCARDED", "📈 Trading — discarded",
                   body)


# --- Job execution -------------------------------------------------------------------

def _connected(uid: str) -> bool:
    """True when this visitor has a stored Coinbase trading
    connection (used for preview wording; execution re-checks
    with a live, refreshed connection)."""
    return bool(COINBASE_TRADE_ENABLED and _cb_connection(uid))


def _build_preview(ask: Dict, uid: str) -> list:
    """Quote it, guard it, park it, show it."""
    if _trade_left(uid) <= 0:
        return _cap_body()
    quote = get_quote(ask["symbol"], ask["kind"])
    if quote is None:
        return _no_quote_body(ask)
    price = quote[0]
    state = {"stage": "preview", "side": ask["side"],
             "symbol": ask["symbol"], "kind": ask["kind"],
             "usd": ask.get("usd"), "qty": ask.get("qty"),
             "all": bool(ask.get("all")),
             "broker": ask.get("broker"),
             "quote": price, "quote_ts": _fmt_ts(quote[2]),
             "_connected": _connected(uid)}
    # A connected sell-all resolves against the venue's own
    # balance, so the preview carries real numbers.
    if state["all"] and state["kind"] == "crypto":
        entry = _live_cb_connection(uid)
        if entry:
            bal = _cb_balance(entry.get("access_token", ""),
                              state["symbol"])
            if bal is not None:
                state["qty"] = bal
    est = _est_total(state, price)
    ceiling = _ceiling(_tier())
    if est is not None and est > ceiling:
        return _ceiling_body(ceiling, est)
    _set_pending(uid, state)
    return _preview_body(state, quote)


def _ask(job: Dict, uid: str) -> Optional[list]:
    ask = {k: job.get(k) for k in
           ("side", "symbol", "usd", "qty", "all", "broker")}
    ask["kind"] = job.get("asset_kind")
    if _missing(ask):
        _set_pending(uid, {"stage": "collect", **ask})
        return _clarify_body(ask)
    return _build_preview(ask, uid)


def _collect_answer(message: str, uid: str) -> Optional[list]:
    pending = _get_pending(uid)
    if pending is None:
        return None
    fresh = _parse_trade_ask(message)
    merged = {k: pending.get(k) for k in
              ("side", "symbol", "kind", "usd", "qty", "all",
               "broker")}
    if fresh:
        for k, v in fresh.items():
            if v is not None and v != "" and v is not False:
                merged["kind" if k == "asset_kind" else k] = v
        if fresh.get("all"):
            merged["all"] = True
    else:
        low = _clean(message)
        if merged.get("symbol") is None:
            symbol, kind = _parse_asset(message, low)
            if symbol:
                merged["symbol"], merged["kind"] = symbol, kind
        if merged.get("usd") is None and merged.get("qty") is None \
                and not merged.get("all"):
            usd, qty, sell_all = _parse_amount(
                message, low, merged.get("symbol"),
                merged.get("kind"))
            merged["usd"], merged["qty"] = usd, qty
            if sell_all:
                merged["all"] = True
    if not _missing(merged):
        _clear_pending(uid)
        return _build_preview(merged, uid)
    if fresh or merged.get("symbol") or merged.get("usd") is not None \
            or merged.get("qty") is not None:
        _set_pending(uid, {"stage": "collect", **merged})
        return _clarify_body(merged)
    _clear_pending(uid)
    body = ("The visitor was asked for the missing piece of a "
            "trade but their answer didn't include it. The "
            "half-started trade is dropped. Tell them plainly, in "
            "persona, and give them the one-line formats that "
            "always work: \"buy $50 of BTC\" or \"sell 2 shares "
            "of AAPL\". Do NOT invent any asset, amount, or price.")
    return _result("TRADING: FORMAT-HINT", "📈 Trading — format",
                   body)


def _handoff_body(state: Dict, quote, lookup) -> list:
    price, _source, when = quote
    venue, _default = _venue_for(state)
    link = _venue_link(state, lookup)
    symbol = state["symbol"]
    side = state["side"].upper()
    sheet_lines = [
        f"TRADE SHEET — {side} {symbol}",
        _amount_line(state, price),
        f"Quote at approval: {_fmt_price(price)} per {symbol} — "
        f"as of {_fmt_ts(when)} (already moving; the venue shows "
        "the live one)",
        f"Venue: {venue}",
        f"Trading page: {link}",
        "Status: READY TO EXECUTE — OG placed nothing. Open the "
        "venue, enter this exact trade, and confirm there.",
        _POLICY_LINE,
    ]
    body = ("The visitor APPROVED the trade and there is no live "
            "trading connection to execute on, so hand it off with "
            "exactly this TRADE SHEET, in persona around it:\n\n"
            + "\n".join(sheet_lines) + "\n\n"
            "Give the trading page URL EXACTLY as written — do not "
            "shorten it, do not swap the domain. Do NOT say the "
            "trade was executed, filled, bought, or sold — it was "
            "NOT. It is ready for the visitor to execute at the "
            "venue.")
    return _result("TRADING: HANDOFF",
                   f"📈 Trade sheet — {side} {symbol}", body, link)


def _executed_body(state: Dict, order: Dict) -> list:
    amt = order.get("amount") or {}
    tot = order.get("total") or {}
    lines = ["Coinbase's own confirmation, exactly as returned:"]
    if order.get("id"):
        lines.append(f"- Order ID: {order['id']}")
    if order.get("status"):
        lines.append(f"- Status: {order['status']}")
    if amt.get("amount"):
        lines.append(f"- Amount: {amt['amount']} "
                     f"{amt.get('currency', '')}".rstrip())
    if tot.get("amount"):
        lines.append(f"- Total: {tot['amount']} "
                     f"{tot.get('currency', '')}".rstrip())
    fee = order.get("fee") or {}
    if isinstance(fee, dict) and fee.get("amount"):
        lines.append(f"- Fee: {fee['amount']} "
                     f"{fee.get('currency', '')}".rstrip())
    body = ("The visitor APPROVED the trade and OG placed the "
            "market order on THEIR OWN connected Coinbase account. "
            "Present Coinbase's confirmation below EXACTLY as "
            "written, in persona around it — these are the venue's "
            "own words and numbers; do not add to them, round "
            "them, or upgrade the status (if Coinbase says "
            "'pending', it is pending):\n\n"
            + "\n".join(lines) + "\n\n" + _RISK_LINE)
    return _result("TRADING: EXECUTED",
                   f"📈 Coinbase confirmation — "
                   f"{state['side'].upper()} {state['symbol']}", body)


def _execute_or_handoff(state: Dict, uid: str, quote,
                        lookup) -> list:
    """Post-approval: execute on a live Coinbase connection when
    there is one (crypto only), else the trade-sheet handoff.
    The caller's YES is already recorded; cap is consumed by the
    caller only when this returns a spent-worthy outcome — here
    we report via _spend markers on the state dict."""
    if state["kind"] == "crypto":
        entry = _live_cb_connection(uid)
        if entry:
            token = entry.get("access_token", "")
            if state["side"] == "sell":
                bal = _cb_balance(token, state["symbol"])
                need = state.get("qty")
                if need is None and state.get("usd") is not None:
                    need = state["usd"] / quote[0]
                if bal is not None and need is not None \
                        and need > bal + 1e-9:
                    body = (
                        "The visitor approved a sell of "
                        f"{_fmt_qty(need, 'crypto')} "
                        f"{state['symbol']}, but their connected "
                        f"Coinbase account holds "
                        f"{_fmt_qty(bal, 'crypto')} "
                        f"{state['symbol']} — the venue's own "
                        "number. The sell is REFUSED: nothing was "
                        "placed and nothing was spent. Tell them "
                        "the two numbers plainly, in persona.")
                    return _result("TRADING: INSUFFICIENT",
                                   "📈 Trading — not enough to sell",
                                   body)
            ok, outcome = _cb_execute(
                entry, state["side"], state["symbol"],
                state.get("usd"), state.get("qty"))
            if not ok:
                body = ("The visitor approved the trade, but "
                        f"Coinbase did not take it: {outcome}. "
                        "NOTHING was executed and nothing was "
                        "spent. Tell them plainly, in persona — no "
                        "retry happens behind their back; they can "
                        "ask for a fresh preview any time.")
                return _result("TRADING: VENUE-FAILED",
                               "📈 Trading — venue said no", body)
            state["_spent"] = True
            return _executed_body(state, outcome)
    state["_spent"] = True
    return _handoff_body(state, quote, lookup)


def _preview_approve(uid: str, lookup) -> Optional[list]:
    pending = _get_pending(uid)
    if pending is None or pending.get("stage") != "preview":
        return None
    quote = get_quote(pending["symbol"], pending["kind"])
    if quote is None:
        body = ("The visitor said YES, but OG could not re-check "
                "the live price just now, so NOTHING was executed "
                "or handed off and nothing was spent — OG never "
                "moves on an unchecked price. The preview is still "
                "parked: tell them plainly, in persona, to say YES "
                "again in a moment to retry the check.")
        return _result("TRADING: RECHECK-FAILED",
                       "📈 Trading — price check failed", body)
    old = float(pending.get("quote") or 0)
    new = quote[0]
    if old > 0 and abs(new - old) / old > _STALE_MOVE:
        state = dict(pending)
        state["quote"] = new
        state["quote_ts"] = _fmt_ts(quote[2])
        state["_connected"] = _connected(uid)
        _set_pending(uid, state)
        return _preview_body(state, quote, moved=True)
    if _trade_left(uid) <= 0:
        _clear_pending(uid)
        return _cap_body()
    result = _execute_or_handoff(pending, uid, quote, lookup)
    spent = bool(pending.get("_spent"))
    _clear_pending(uid)
    if spent:
        _consume_trade(uid)
    return result


def trading_results(job, message, uid, lookup) -> Optional[list]:
    """Run one claimed trading job. Returns web_search-shaped
    results, or None on a true miss so the caller falls through to
    the previous search untouched. Only an approved execution or
    handoff spends a trade unit."""
    if not job or not uid:
        return None
    kind = job.get("kind", "")
    if kind == "ask":
        return _ask(job, uid)
    if kind == "collect_answer":
        return _collect_answer(message, uid)
    if kind == "collect_decline":
        _clear_pending(uid)
        return _discard_body("collect")
    if kind == "preview_decline":
        _clear_pending(uid)
        return _discard_body("preview")
    if kind == "preview_approve":
        return _preview_approve(uid, lookup)
    if kind == "forbidden":
        return _forbidden_body()
    if kind == "policy":
        return _policy_body()
    return None


# ---------------------------------------------------------------------------
# The seam (app.py installs this LAST, after Rounds 3/4/6/9–19)
# ---------------------------------------------------------------------------

# The trading job parsed for the exchange currently being processed.
# All chat processing is serialized under app.py's _memory_lock, so
# a single slot is safe — the same reasoning as app.py's own slots.
_pending = {"job": None, "message": ""}


def install_trading_tools(agent_instance, get_uid):
    """Wrap the agent's (already fully wrapped) detect_intent +
    web_search hooks so trade asks, the preview/approve flow, and
    the policy guards ride the established seam. get_uid() returns
    the current visitor's uid; the per-tier trade cap + ceilings
    run on this module's own counters (bound to app.py's usage
    store via bind_app). The seam's previous search is passed in
    as the lookup (venue-link grounding at handoff). The trading
    PREP half is always live; the Coinbase connect ships dark and
    only gates in-OG execution. Persona files never touched."""
    if getattr(agent_instance, "_og_trading_installed", False):
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
            logger.warning(f"Trading trigger check failed: {e}")
            _pending["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _pending.get("job")
        message = _pending.get("message", "")
        _pending["job"] = None
        _pending["message"] = ""
        if job:
            try:
                results = trading_results(
                    job, message, get_uid(), prev_search)
            except Exception as e:
                logger.warning(f"Trading job failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_trading_installed = True


# --- Coinbase trading connect routes (dark until enabled) ------------------------

def register_trading_routes(app):
    """Attach the four /auth/coinbase* routes to the FastAPI app.
    Mirrors the Round 10 Spotify routes one for one."""

    @app.get("/auth/coinbase")
    async def coinbase_auth_start(raw_request: Request):
        """Begin Coinbase trading connect: bounce the visitor to
        Coinbase's own consent page. Answers 404 while the feature
        is dark (keys not set), so nothing about it is discoverable
        on the live site until Brent enables it."""
        if not COINBASE_TRADE_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        from urllib.parse import urlencode
        uid = raw_request.cookies.get("ogai_uid")
        fresh_uid = None
        if not uid:
            uid = uuid.uuid4().hex
            fresh_uid = uid
        params = {
            "client_id": COINBASE_CLIENT_ID,
            "redirect_uri": COINBASE_REDIRECT_URI,
            "response_type": "code",
            "scope": COINBASE_SCOPES,
            "state": _cb_state_for(uid),
        }
        response = RedirectResponse(
            url=_AUTHORIZE_URL + "?" + urlencode(params),
            status_code=302)
        if fresh_uid:
            response.set_cookie(
                "ogai_uid", fresh_uid,
                max_age=_cookie_max_age(), path="/", httponly=True,
                samesite="lax")
        return response

    @app.get("/auth/coinbase/callback")
    async def coinbase_auth_callback(raw_request: Request,
                                     code: str = "", state: str = "",
                                     error: str = ""):
        """Coinbase sends the visitor back here with a code. The
        signed state tells us which visitor this is; the code is
        exchanged for tokens, the profile is fetched, and the
        connection is stored under their uid. Any failure lands
        back on the chat with ?coinbase=failed — no error page."""
        if not COINBASE_TRADE_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        uid = _cb_uid_from_state(state)
        if error or not code or not uid:
            return RedirectResponse(url="/?coinbase=failed",
                                    status_code=302)
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                token_resp = await client.post(
                    _TOKEN_URL,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "client_id": COINBASE_CLIENT_ID,
                        "client_secret": COINBASE_CLIENT_SECRET,
                        "redirect_uri": COINBASE_REDIRECT_URI,
                    })
                if token_resp.status_code != 200:
                    logger.warning(
                        "Coinbase token exchange status: "
                        f"{token_resp.status_code}")
                    return RedirectResponse(url="/?coinbase=failed",
                                            status_code=302)
                tokens = token_resp.json()
                info_resp = await client.get(
                    _API_BASE + "/v2/user",
                    headers={
                        "Authorization":
                            f"Bearer {tokens.get('access_token', '')}"
                    })
                profile = (info_resp.json() or {}).get("data") or {} \
                    if info_resp.status_code == 200 else {}
        except Exception as e:
            logger.warning(f"Coinbase connect failed: {e}")
            return RedirectResponse(url="/?coinbase=failed",
                                    status_code=302)
        entry = {
            "coinbase_id": profile.get("id", ""),
            "name": profile.get("name", ""),
            "email": profile.get("email", ""),
            "access_token": tokens.get("access_token", ""),
            "refresh_token": tokens.get("refresh_token", ""),
            "scope": tokens.get("scope", COINBASE_SCOPES),
            "expires_at": (datetime.now(timezone.utc).timestamp()
                           + int(tokens.get("expires_in", 7200))),
            "connected": datetime.now(timezone.utc).isoformat(),
        }
        _store_cb_entry(uid, entry)
        response = RedirectResponse(url="/?coinbase=connected",
                                    status_code=302)
        response.set_cookie(
            "ogai_uid", uid,
            max_age=_cookie_max_age(), path="/", httponly=True,
            samesite="lax")
        return response

    @app.get("/auth/coinbase/status")
    async def coinbase_auth_status(raw_request: Request):
        """What the slide-over menu needs: is connect enabled, and
        if this visitor is connected, as whom? (Never returns
        tokens.)"""
        entry = None
        if COINBASE_TRADE_ENABLED:
            entry = _cb_connection(raw_request.cookies.get("ogai_uid"))
        return {
            "enabled": COINBASE_TRADE_ENABLED,
            "connected": bool(entry),
            "email": entry.get("email", "") if entry else "",
            "name": entry.get("name", "") if entry else "",
        }

    @app.post("/auth/coinbase/disconnect")
    async def coinbase_auth_disconnect(raw_request: Request):
        """Forget this visitor's Coinbase trading connection —
        tokens deleted server-side (the grant is also revoked at
        Coinbase on a best-effort basis)."""
        if not COINBASE_TRADE_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        uid = raw_request.cookies.get("ogai_uid")
        if uid:
            entry = _cb_connection(uid)
            if entry and entry.get("access_token"):
                try:
                    import httpx
                    with httpx.Client(timeout=10) as client:
                        client.post(
                            "https://api.coinbase.com/oauth/revoke",
                            data={"token": entry["access_token"]})
                except Exception:
                    pass
            _drop_cb_entry(uid)
        return {"status": "disconnected"}
