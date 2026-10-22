"""Crypto Hub surface for OG W (Round 321 dashboards).

Brent's rules for this surface, binding:

- REAL ABILITIES ONLY. Live prices from the same keyless feeds
  the data pack / og_trading use (Coinbase Exchange stats,
  CoinGecko fallback). A connected Coinbase portfolio shows the
  venue's own account balances. Everything else is an honest
  line, never an invented number.
- Ticket M: the connected portfolio also carries a HOLDINGS
  view — only assets with a balance above zero, each priced
  from those same feeds, with the total they sum to. The hub
  client leads with it (compact, holdings-only); the full
  core-coin price list stays in the payload as feed data
  for the trade hand-off and the bid-range machinery —
  it is never rendered as a display board (the board was
  removed by the 2026-10-22 scope cut below).
- NO MONEY MOVES HERE. This module registers no route that can
  place, execute, or hand off an order. The trade panel's only
  action hands the ask to chat, where the existing trade-sheet
  + per-trade approval machinery (og_trading) owns it end to
  end. Coinbase trading connect itself is SHIPPED DARK in
  og_trading until OG_COINBASE_TRADE_ENABLED plus the client
  id/secret are set; while dark this surface says so plainly.
- NO OG-HELD WALLET, no real-money games, no revenue board.
  The Games section of the design ships as COMING SOON in the
  client renderer only.
Scope cut (Brent, 2026-10-22): the watch-only address list
is REMOVED from the hub — its routes, its per-account store,
and its balance fetchers are gone from this module, not
hidden. The hub is the user's own money only: holdings,
bid ranges, and the trade hand-off. (The paper-trading
simulator, a client-side book in og_markets.js, was removed
by the same order; no trace of it remains in the client.)

Kill switch: OG_CRYPTOHUB_ENABLED — enabled unless the env
value is one of 0/false/no/off (the og_crew family pattern).
Disabled, every route here answers 404 and the surface does
not exist.

Handlers are plain def (FastAPI threadpool), never async: the
upstream fetches are sync network work and the Main Thread
Rule keeps waiting off the event loop. Fetches ride a short
TTL cache so an open dashboard can't stampede the upstreams.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# --- Kill switch ---------------------------------------------------------------


def cryptohub_enabled() -> bool:
    """OG_CRYPTOHUB_ENABLED is the kill switch: enabled unless
    the env value is an explicit off (0/false/no/off)."""
    return os.environ.get(
        "OG_CRYPTOHUB_ENABLED", "true").strip().lower() not in (
            "0", "false", "no", "off")


# --- Core price set --------------------------------------------------------------
# The Coinbase-covered majors: app.py's data-pack _CRYPTO_MAP
# products union og_trading's _CRYPTO_SYMBOLS, each with its
# CoinGecko id for the fallback feed (app.py's mapping).

CORE_COINS = [
    {"symbol": "BTC", "name": "Bitcoin", "gecko": "bitcoin"},
    {"symbol": "ETH", "name": "Ethereum", "gecko": "ethereum"},
    {"symbol": "SOL", "name": "Solana", "gecko": "solana"},
    {"symbol": "DOGE", "name": "Dogecoin", "gecko": "dogecoin"},
    {"symbol": "XRP", "name": "XRP", "gecko": "ripple"},
    {"symbol": "ADA", "name": "Cardano", "gecko": "cardano"},
    {"symbol": "LTC", "name": "Litecoin", "gecko": "litecoin"},
    {"symbol": "LINK", "name": "Chainlink", "gecko": "chainlink"},
    {"symbol": "DOT", "name": "Polkadot", "gecko": "polkadot"},
    {"symbol": "AVAX", "name": "Avalanche", "gecko": "avalanche-2"},
    {"symbol": "MATIC", "name": "Polygon", "gecko": "matic-network"},
    {"symbol": "BCH", "name": "Bitcoin Cash", "gecko": "bitcoin-cash"},
    {"symbol": "XLM", "name": "Stellar", "gecko": "stellar"},
    {"symbol": "ATOM", "name": "Cosmos", "gecko": "cosmos"},
    {"symbol": "UNI", "name": "Uniswap", "gecko": "uniswap"},
    {"symbol": "SHIB", "name": "Shiba Inu", "gecko": "shiba-inu"},
    {"symbol": "NEAR", "name": "Near Protocol", "gecko": "near"},
    {"symbol": "APT", "name": "Aptos", "gecko": "aptos"},
    {"symbol": "SUI", "name": "Sui", "gecko": "sui"},
]

_PRICE_TTL = 60.0  # seconds; the data pack's crypto TTL is 120
_price_cache: Dict = {"at": 0.0, "prices": None}
_price_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(value) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


# --- Upstream fetchers (the patch points for tests) ------------------------------


def _fetch_json(url: str, params: Dict = None) -> Optional[Dict]:
    """One JSON GET through og_trading's feed funnel (browser UA,
    tight timeout); None on any failure."""
    try:
        import og_trading
        return og_trading._fetch_json(url, params=params)
    except Exception as e:
        logger.warning(f"CryptoHub fetch failed ({url[:60]}): {e}")
        return None


def _fetch_coinbase_stats(symbol: str) -> Optional[Dict]:
    return _fetch_json(
        "https://api.exchange.coinbase.com/products/"
        f"{symbol}-USD/stats")


def _fetch_coingecko(gecko_id: str) -> Optional[Dict]:
    data = _fetch_json(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": gecko_id, "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_24hr_vol": "true"})
    if not data:
        return None
    row = data.get(gecko_id)
    return row if isinstance(row, dict) else None


def _price_for(coin: Dict) -> Optional[Dict]:
    """One coin's price row from the real feeds, or None when
    both feeds give nothing back — the row is then omitted,
    never fabricated."""
    symbol = coin["symbol"]
    stats = _fetch_coinbase_stats(symbol)
    if stats:
        last = _f(stats.get("last"))
        opn = _f(stats.get("open"))
        if last and last > 0:
            change = ((last - opn) / opn * 100) if opn else None
            return {
                "symbol": symbol, "name": coin["name"], "usd": last,
                "change_24h": round(change, 4)
                if change is not None else None,
                "high": _f(stats.get("high")),
                "low": _f(stats.get("low")),
                "volume": _f(stats.get("volume")),
                "source": "Coinbase Exchange",
            }
    gecko = _fetch_coingecko(coin["gecko"])
    if gecko:
        usd = _f(gecko.get("usd"))
        if usd and usd > 0:
            return {
                "symbol": symbol, "name": coin["name"], "usd": usd,
                "change_24h": _f(gecko.get("usd_24h_change")),
                "high": None, "low": None,
                "volume": _f(gecko.get("usd_24h_vol")),
                "source": "CoinGecko",
            }
    return None


def _prices() -> List[Dict]:
    """The core set's live rows, TTL-cached. An empty list means
    the upstreams gave nothing back — callers flag that honestly
    instead of showing stale or invented numbers."""
    with _price_lock:
        if _price_cache["prices"] is not None and \
                time.time() - _price_cache["at"] < _PRICE_TTL:
            return list(_price_cache["prices"])
    rows = []
    for coin in CORE_COINS:
        try:
            row = _price_for(coin)
        except Exception as e:
            logger.warning(f"CryptoHub price {coin['symbol']}: {e}")
            row = None
        if row is not None:
            rows.append(row)
    if rows:
        with _price_lock:
            _price_cache["prices"] = rows
            _price_cache["at"] = time.time()
    return rows


# --- Coinbase portfolio (read-only; connect state from og_trading) ---------------


def _portfolio_for(uid: str) -> Dict:
    """The caller's Coinbase portfolio picture, from the real
    seams in og_trading. States: dark (connect not switched on),
    disconnected (on, but this visitor hasn't connected or the
    grant is dead), connected (the venue's own balances)."""
    try:
        import og_trading
    except Exception:
        return {"state": "dark", "accounts": None,
                "note": "Coinbase trading isn't switched on yet — "
                        "real trades need the connect finished "
                        "in chat."}
    if not og_trading.COINBASE_TRADE_ENABLED:
        return {"state": "dark", "accounts": None,
                "note": "Coinbase trading isn't switched on yet — "
                        "real trades need the connect finished "
                        "in chat."}
    if not uid:
        return {"state": "disconnected", "accounts": None,
                "note": "Connect Coinbase in chat to see your "
                        "portfolio here."}
    stored = og_trading._cb_connection(uid)
    if not stored:
        return {"state": "disconnected", "accounts": None,
                "note": "Coinbase isn't connected for this "
                        "account yet — connect it and your real "
                        "balances show here."}
    live = og_trading._live_cb_connection(uid)
    if not live:
        return {"state": "disconnected", "accounts": None,
                "note": "Your Coinbase connection needs a refresh "
                        "— reconnect in chat and your balances "
                        "show here."}
    accounts = []
    for acct in og_trading._cb_accounts(live.get("access_token", "")):
        cur = acct.get("currency")
        code = ""
        if isinstance(cur, dict):
            code = str(cur.get("code") or "")
        elif isinstance(cur, str):
            code = cur
        bal = acct.get("balance") or {}
        amount = bal.get("amount") if isinstance(bal, dict) else None
        if not code or amount is None:
            continue
        accounts.append({
            "currency": code,
            "balance": str(amount),
            "name": str(acct.get("name") or code),
        })
    return {"state": "connected", "accounts": accounts,
            "holdings": _holdings_view(accounts),
            "note": "Balances exactly as Coinbase reports them."}


_CASH_CODES = ("USD", "USDC")
_CORE_BY_SYMBOL = {c["symbol"]: c for c in CORE_COINS}


def _holdings_view(accounts: List[Dict]) -> Dict:
    """Ticket M: the caller's holdings, compact — ONLY
    assets with a real balance above zero, each with its
    live price from the hub's own feeds and its value
    (balance x price). A coin whose price feed gives
    nothing back keeps its balance row with price/value
    null — never an invented figure. Cash (USD/USDC)
    values 1:1. total_usd sums the valued rows only and
    values_complete says whether every row was valued."""
    rows = []
    for acct in accounts:
        code = str(acct.get("currency") or "")
        bal = _f(acct.get("balance"))
        if not code or bal is None or bal <= 0:
            continue
        rows.append((code, str(acct.get("name") or code),
                     acct.get("balance")))
    price_map = {r["symbol"]: r for r in _prices()}
    holdings = []
    total = 0.0
    any_valued = False
    for code, name, bal_raw in rows:
        bal = _f(bal_raw)
        price = None
        change = None
        source = None
        if code in _CASH_CODES:
            price = 1.0
            source = "cash"
        else:
            row = price_map.get(code)
            if row is None:
                core = _CORE_BY_SYMBOL.get(code) or {}
                try:
                    row = _price_for({
                        "symbol": code, "name": name,
                        "gecko": core.get("gecko")})
                except Exception as e:
                    logger.warning(
                        f"CryptoHub holding price {code}: {e}")
                    row = None
            if row:
                price = _f(row.get("usd"))
                change = _f(row.get("change_24h"))
                source = row.get("source")
        value = None
        if price is not None and bal is not None:
            value = round(bal * price, 2)
            total += value
            any_valued = True
        holdings.append({
            "symbol": code, "name": name,
            "balance": str(bal_raw),
            "price": price, "change_24h": change,
            "value_usd": value, "source": source,
            "kind": "cash" if code in _CASH_CODES else "coin",
        })
    holdings.sort(key=lambda h: (
        h["value_usd"] is None, -(h["value_usd"] or 0.0)))
    return {
        "rows": holdings,
        "total_usd": round(total, 2) if any_valued else None,
        "values_complete": all(
            h["value_usd"] is not None for h in holdings),
    }


# --- Routes ---------------------------------------------------------------------------
# No route in this module moves money: there is no buy, sell,
# execute, send, or order route here by design. Real trades go
# through chat (the og_trading approval flow) only.


def register_cryptohub_routes(app):

    @app.get("/cryptohub/data")
    def cryptohub_data(request: Request):
        if not cryptohub_enabled():
            raise HTTPException(status_code=404, detail="Not found")
        uid = request.cookies.get("ogai_uid") or ""
        prices = _prices()
        return {"ok": True, "enabled": True,
                "prices": prices,
                "prices_available": bool(prices),
                "portfolio": _portfolio_for(uid),
                "as_of": _now_iso()}
