"""Crypto Hub surface for OG W (Round 321 dashboards).

Brent's rules for this surface, binding:

- REAL ABILITIES ONLY. Live prices from the same keyless feeds
  the data pack / og_trading use (Coinbase Exchange stats,
  CoinGecko fallback). A connected Coinbase portfolio shows the
  venue's own account balances. Everything else is an honest
  line, never an invented number.
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
- Watch-only addresses are PUBLIC addresses the visitor saves
  to look at. OG never holds keys for them and cannot move
  anything at those addresses. Balances come from keyless
  public APIs (mempool.space for BTC, eth.blockscout.com for
  ETH) and fail soft to an honest unavailable per address.

Store: the watchlist is a per-account written record, one
authoritative version in the house pattern (Postgres via
og_db when OG_MEMORY_DB_URL is configured, else a JSON file),
keyed by the caller's ogai_uid. The panel itself is the owner
door: the list is shown in the surface and every record can be
removed there, so no expiry is needed — a record lives until
its owner removes it (cleanup-on-owner-action, stated here per
the No Junk Drawer rule).

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
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import Body, HTTPException, Request

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
                        "OG can show prices and paper-trade; real "
                        "trades need the connect finished in chat."}
    if not og_trading.COINBASE_TRADE_ENABLED:
        return {"state": "dark", "accounts": None,
                "note": "Coinbase trading isn't switched on yet — "
                        "OG can show prices and paper-trade; real "
                        "trades need the connect finished in chat."}
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
            "note": "Balances exactly as Coinbase reports them."}


# --- Watch-only address store ------------------------------------------------------
# Per-account written record, the house pattern: Postgres table
# og_cryptohub_data (uid, kind) via og_db when OG_MEMORY_DB_URL
# is set, else the JSON file below. kind is always "watchlist".

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, \
    apply_bounds as _apply_bounds, \
    normalize_conninfo as _normalize_conninfo, pooled_connect as _pooled_connect
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

WATCH_STORE_FILE = os.getenv(
    "OG_CRYPTOHUB_STORE", "cryptohub_store.json")
WATCH_CAP = 10
_watch_lock = threading.RLock()


def _db_connect():
    conn = _pooled_connect(
        _normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_cryptohub_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(WATCH_STORE_FILE):
        try:
            with open(WATCH_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"CryptoHub store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(WATCH_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"CryptoHub store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_cryptohub_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"CryptoHub DB load failed: {e}")
            return default
    with _watch_lock:
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
                        "INSERT INTO og_cryptohub_data "
                        "(uid, kind, data) VALUES (%s, %s, %s) "
                        "ON CONFLICT (uid, kind) DO UPDATE "
                        "SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"CryptoHub DB save failed: {e}")
            return
    with _watch_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


def _watch_for(uid: str) -> List[Dict]:
    got = _blob(uid, "watchlist", [])
    if not isinstance(got, list):
        return []
    return [r for r in got if isinstance(r, dict)]


# --- Watch-only address validation + balances ---------------------------------------

_BTC_LEGACY_RE = re.compile(r"^[13][a-km-zA-HJ-NP-Z1-9]{25,34}$")
_BTC_BECH32_RE = re.compile(r"^bc1[a-z0-9]{11,71}$")
_ETH_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _valid_address(chain: str, address: str) -> bool:
    address = str(address or "").strip()
    if chain == "btc":
        if _BTC_LEGACY_RE.match(address):
            return True
        return bool(_BTC_BECH32_RE.match(address.lower()))
    if chain == "eth":
        return bool(_ETH_RE.match(address))
    return False


def _fetch_btc_address(address: str) -> Optional[Dict]:
    return _fetch_json(f"https://mempool.space/api/address/{address}")


def _fetch_eth_address(address: str) -> Optional[Dict]:
    return _fetch_json(
        f"https://eth.blockscout.com/api/v2/addresses/{address}")


def _watch_balance(chain: str, address: str) -> Optional[Dict]:
    """Live balance for one public address from a keyless public
    API, or None — the caller marks that address unavailable,
    honestly, and never invents a figure."""
    try:
        if chain == "btc":
            data = _fetch_btc_address(address)
            if not data:
                return None
            chain_stats = data.get("chain_stats") or {}
            funded = _f(chain_stats.get("funded_txo_sum"))
            spent = _f(chain_stats.get("spent_txo_sum"))
            if funded is None or spent is None:
                return None
            mempool = data.get("mempool_stats") or {}
            m_funded = _f(mempool.get("funded_txo_sum"))
            m_spent = _f(mempool.get("spent_txo_sum"))
            unconfirmed = None
            if m_funded is not None and m_spent is not None:
                unconfirmed = (m_funded - m_spent) / 1e8
            return {"balance": (funded - spent) / 1e8, "unit": "BTC",
                    "unconfirmed": unconfirmed,
                    "source": "mempool.space"}
        if chain == "eth":
            data = _fetch_eth_address(address)
            if not data:
                return None
            wei = _f(data.get("coin_balance"))
            if wei is None:
                return None
            eth = wei / 1e18
            rate = _f(data.get("exchange_rate"))
            return {"balance": eth, "unit": "ETH",
                    "usd": (eth * rate) if rate else None,
                    "exchange_rate": rate,
                    "source": "eth.blockscout.com"}
    except Exception as e:
        logger.warning(f"CryptoHub watch balance failed: {e}")
    return None


def _watchlist_view(uid: str) -> List[Dict]:
    out = []
    for rec in _watch_for(uid):
        balance = _watch_balance(
            str(rec.get("chain") or ""), str(rec.get("address") or ""))
        out.append({
            "id": rec.get("id", ""),
            "label": rec.get("label", ""),
            "chain": rec.get("chain", ""),
            "address": rec.get("address", ""),
            "created_at": rec.get("created_at"),
            "balance": balance,
            "balance_available": balance is not None,
        })
    return out


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

    @app.get("/cryptohub/watchlist")
    def cryptohub_watchlist(request: Request):
        if not cryptohub_enabled():
            raise HTTPException(status_code=404, detail="Not found")
        uid = request.cookies.get("ogai_uid") or ""
        if not uid:
            return {"ok": True, "items": [], "available": False,
                    "note": "No visitor id on this request — "
                            "watch-only addresses can't be saved."}
        return {"ok": True, "items": _watchlist_view(uid),
                "available": True}

    @app.post("/cryptohub/watchlist")
    def cryptohub_watchlist_post(request: Request,
                                 payload: Dict = Body(...)):
        if not cryptohub_enabled():
            raise HTTPException(status_code=404, detail="Not found")
        uid = request.cookies.get("ogai_uid") or ""
        if not uid:
            raise HTTPException(
                status_code=400,
                detail="No visitor id on this request.")
        action = str((payload or {}).get("action") or "").lower()
        rows = _watch_for(uid)
        if action == "remove":
            rid = str((payload or {}).get("id") or "")
            kept = [r for r in rows if r.get("id") != rid]
            if len(kept) == len(rows):
                raise HTTPException(status_code=404,
                                    detail="No such address.")
            _put_blob(uid, "watchlist", kept)
            return {"ok": True, "items": _watchlist_view(uid)}
        if action != "add":
            raise HTTPException(status_code=400,
                                detail="action must be add or remove")
        chain = str((payload or {}).get("chain") or "").lower()
        address = str((payload or {}).get("address") or "").strip()
        label = " ".join(
            str((payload or {}).get("label") or "").split())[:60]
        if chain not in ("btc", "eth"):
            raise HTTPException(status_code=400,
                                detail="chain must be btc or eth")
        if not _valid_address(chain, address):
            raise HTTPException(
                status_code=400,
                detail=f"That doesn't look like a valid {chain} "
                       "public address.")
        if chain == "btc" and address.lower().startswith("bc1"):
            address = address.lower()
        for r in rows:
            if r.get("chain") == chain and \
                    str(r.get("address") or "").lower() == \
                    address.lower():
                raise HTTPException(
                    status_code=400,
                    detail="That address is already on your list.")
        if len(rows) >= WATCH_CAP:
            raise HTTPException(
                status_code=400,
                detail=f"Watch list is capped at {WATCH_CAP} "
                       "addresses — remove one first.")
        rec = {"id": uuid.uuid4().hex[:12], "label": label,
               "chain": chain, "address": address,
               "created_at": time.time()}
        rows.append(rec)
        _put_blob(uid, "watchlist", rows)
        return {"ok": True, "items": _watchlist_view(uid)}
