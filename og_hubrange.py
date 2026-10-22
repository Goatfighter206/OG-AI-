"""Ticket M — the Crypto Hub's BID RANGES (OG W,
dashboards wave follow-on, Brent 2026-10-19).

Brent's spec: on the Crypto Hub, per coin the user arms
a bid range — buy at-or-below, sell at-or-above — and
OG acts when the price enters the band, the way Brent's
own Bitcoin plan works (owner arms the bounds, caps
hold, every fill is reported, one-tap disarm).

What exists in the tree today, and what this module is:

- The arm / store / watch / report machinery is REAL:
  ranges are one authoritative stored record per
  (signed-in account, coin), shown in the hub panel
  with their live band state, and a band entry is
  recorded as a trigger event the moment a read sees
  it — edge-triggered, once per entry.
- EXECUTION IS NOT LIVE, and this module says so in
  every payload. Coinbase trading connect is shipped
  DARK in og_trading (OG_COINBASE_TRADE_ENABLED plus
  the client id/secret are unset), Brent's own
  Coinbase stays parked, and og_trading's standing
  law is a fresh approval per trade — there is no
  standing executor to route an armed range to, and
  this module does not invent one. A trigger event is
  therefore recorded with executed=false and the
  payload's execution block states plainly why
  nothing executed. The day a live standing executor
  is wired through the trading machinery, it reads
  THESE records — the arm store does not change.

Binding rules:

- PER-USER, SERVER-SIDE. The caller is resolved from
  the account session (the og_accounts seam, as in
  og_findash); no signed-in account -> 401. A range
  can only ever be read or written by its owner.
- REAL DATA ONLY. Band state is computed from the
  hub's own live price funnel (og_cryptohub prices:
  Coinbase Exchange stats, CoinGecko fallback). No
  price -> zone "no_price", never an invented figure.
- NO MONEY MOVES HERE. This module registers one GET
  and one POST (arm/disarm/remove on the caller's own
  records). It contains no order, buy, sell, or
  execute call — the source is audited for exactly
  that by the Ticket M gate.
- NO JUNK DRAWER. One record per coin per account in
  one store blob; the panel is the owner door (arm,
  disarm, remove all live there); trigger events are
  capped and the record dies with its range's remove.

Kill switch: OG_HUBRANGE_ENABLED — enabled unless the
env value is one of 0/false/no/off (the house
pattern). Disabled, both routes answer 404.

Handlers are plain def (FastAPI threadpool), never
async: reads ride the hub's TTL price cache and the
store is local — the Main Thread Rule keeps waiting
off the event loop.
"""

import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import Body, HTTPException, Request

logger = logging.getLogger(__name__)

# --- Kill switch ---------------------------------------------------------------


def hubrange_enabled() -> bool:
    """OG_HUBRANGE_ENABLED is the kill switch: enabled
    unless the env value is an explicit off."""
    return os.environ.get(
        "OG_HUBRANGE_ENABLED", "true").strip().lower() not in (
            "0", "false", "no", "off")


MAX_RANGES = 20
MAX_EVENTS = 50
_EVENTS_SHOWN = 20

_TRIGGER_NOTE = ("Trigger reported only — nothing "
                 "executed. See the execution note.")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(value) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def _account_uid(request) -> Optional[str]:
    """The signed-in account uid behind the request, or
    None — the og_findash seam verbatim. A visitor
    cookie alone is NOT a signed-in account."""
    try:
        import og_accounts
        return og_accounts.session_account_uid(
            request.cookies.get(og_accounts.SESSION_COOKIE))
    except Exception:
        return None


# --- Store (house blob pattern, as in og_cryptohub) ------------------------------
# One authoritative record set per account: table
# og_hubrange_data (uid, kind) via og_db when
# OG_MEMORY_DB_URL is set, else the JSON file below.
# kind "ranges" -> {SYMBOL: record}; kind
# "range_events" -> [event, ...] in append order.

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

RANGE_STORE_FILE = os.getenv(
    "OG_HUBRANGE_STORE", "hubrange_store.json")
_store_lock = threading.RLock()


def _db_connect():
    conn = _pooled_connect(
        _normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_hubrange_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(RANGE_STORE_FILE):
        try:
            with open(RANGE_STORE_FILE) as f:
                import json
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"HubRange store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    import json
    try:
        with open(RANGE_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"HubRange store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_hubrange_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"HubRange DB load failed: {e}")
            return default
    with _store_lock:
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
                        "INSERT INTO og_hubrange_data "
                        "(uid, kind, data) VALUES (%s, %s, %s) "
                        "ON CONFLICT (uid, kind) DO UPDATE "
                        "SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"HubRange DB save failed: {e}")
            return
    with _store_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


def _ranges_for(uid: str) -> Dict:
    got = _blob(uid, "ranges", {})
    if not isinstance(got, dict):
        return {}
    return {str(k).upper(): v for k, v in got.items()
            if isinstance(v, dict)}


def _events_for(uid: str) -> List[Dict]:
    got = _blob(uid, "range_events", [])
    if not isinstance(got, list):
        return []
    return [e for e in got if isinstance(e, dict)]


# --- Prices + band state (the hub's own funnel) -----------------------------------


def _price_map() -> Dict:
    """symbol -> price row, from og_cryptohub's live
    prices (TTL-cached there). Rows only ever carry
    real feed figures."""
    try:
        import og_cryptohub
        return {row["symbol"]: row
                for row in og_cryptohub._prices()}
    except Exception as e:
        logger.warning(f"HubRange price map failed: {e}")
        return {}


def _price_for(symbol: str, price_map: Dict) -> Optional[Dict]:
    row = price_map.get(symbol)
    if row:
        return row
    try:
        import og_cryptohub
        gecko = None
        name = symbol
        for coin in og_cryptohub.CORE_COINS:
            if coin["symbol"] == symbol:
                gecko = coin["gecko"]
                name = coin["name"]
                break
        return og_cryptohub._price_for(
            {"symbol": symbol, "name": name, "gecko": gecko})
    except Exception as e:
        logger.warning(f"HubRange price {symbol}: {e}")
        return None


def _zone(rec: Dict, price: Optional[float]) -> str:
    if price is None:
        return "no_price"
    buy = _f(rec.get("buy_below"))
    sell = _f(rec.get("sell_above"))
    if buy is not None and price <= buy:
        return "buy_zone"
    if sell is not None and price >= sell:
        return "sell_zone"
    return "watching"


# --- Execution truth -----------------------------------------------------------------


def _execution_state(cookie_uid: str) -> Dict:
    """Why an armed range does or doesn't execute —
    computed from the REAL connect state in og_trading,
    stated plainly. live is False in this build: no
    standing executor exists to route a range to, and
    none is invented here."""
    base = {"live": False}
    try:
        import og_trading
    except Exception:
        base["reason"] = (
            "The trading module isn't available, so "
            "armed ranges are only watched and reported "
            "here — nothing executes.")
        return base
    if not og_trading.COINBASE_TRADE_ENABLED:
        base["reason"] = (
            "Coinbase trading isn't switched on yet — "
            "the connect is shipped dark — so armed "
            "ranges are watched and their triggers "
            "reported here, but nothing executes. "
            "Execution, when it comes, runs through "
            "OG's trading machinery; this panel never "
            "trades on its own.")
        return base
    if not cookie_uid:
        base["reason"] = (
            "No Coinbase connection is tied to this "
            "visit, so nothing can execute — armed "
            "ranges are watched and reported only.")
        return base
    stored = og_trading._cb_connection(cookie_uid)
    live = og_trading._live_cb_connection(cookie_uid) \
        if stored else None
    if not live:
        base["reason"] = (
            "Coinbase isn't connected for your account, "
            "so nothing can execute — armed ranges are "
            "watched and their triggers reported here "
            "only.")
        return base
    base["reason"] = (
        "Your Coinbase is connected, but live execution "
        "of standing ranges isn't wired yet: OG's "
        "trading machinery takes a fresh approval per "
        "trade, so an armed range raises a trigger "
        "report here instead of trading on its own. "
        "Nothing has executed from this panel.")
    return base


def _held_symbols(cookie_uid: str) -> List[str]:
    """Symbols the caller actually holds, from the
    hub's own portfolio view (connected state only) —
    best-effort: any failure means no held symbols."""
    if not cookie_uid:
        return []
    try:
        import og_cryptohub
        pf = og_cryptohub._portfolio_for(cookie_uid)
        hv = pf.get("holdings") or {}
        return [h["symbol"] for h in (hv.get("rows") or [])
                if h.get("symbol")]
    except Exception:
        return []


def _armable_symbols(cookie_uid: str) -> set:
    try:
        import og_cryptohub
        core = {c["symbol"] for c in og_cryptohub.CORE_COINS}
    except Exception:
        core = set()
    return core | set(_held_symbols(cookie_uid))


# --- The view (band state + edge-triggered events) ------------------------------------


def range_view(account_uid: str, cookie_uid: str) -> Dict:
    """The caller's ranges with live band state. A read
    that finds an ARMED range entering its band records
    exactly one trigger event per entry."""
    ranges = _ranges_for(account_uid)
    events = _events_for(account_uid)
    price_map = _price_map()
    out: List[Dict] = []
    dirty_ranges = False
    dirty_events = False
    for symbol in sorted(ranges):
        rec = dict(ranges[symbol])
        row = _price_for(symbol, price_map)
        price = _f(row.get("usd")) if row else None
        zone = _zone(rec, price)
        if rec.get("armed"):
            if zone != rec.get("last_zone"):
                if zone in ("buy_zone", "sell_zone"):
                    events.append({
                        "id": uuid.uuid4().hex[:12],
                        "symbol": symbol,
                        "side": "buy" if zone == "buy_zone"
                                else "sell",
                        "price": price,
                        "bound": _f(rec.get("buy_below"))
                                 if zone == "buy_zone"
                                 else _f(rec.get("sell_above")),
                        "ts": time.time(),
                        "iso": _now_iso(),
                        "executed": False,
                        "note": _TRIGGER_NOTE,
                    })
                    dirty_events = True
                rec["last_zone"] = zone
                ranges[symbol] = rec
                dirty_ranges = True
        out.append({
            "symbol": symbol,
            "buy_below": _f(rec.get("buy_below")),
            "sell_above": _f(rec.get("sell_above")),
            "armed": bool(rec.get("armed")),
            "price": price,
            "price_source": (row or {}).get("source"),
            "zone": zone,
            "created_at": rec.get("created_at"),
            "updated_at": rec.get("updated_at"),
        })
    if dirty_ranges:
        _put_blob(account_uid, "ranges", ranges)
    if dirty_events:
        events = events[-MAX_EVENTS:]
        _put_blob(account_uid, "range_events", events)
    shown = list(reversed(events))[:_EVENTS_SHOWN]
    return {
        "ok": True,
        "ranges": out,
        "events": shown,
        "execution": _execution_state(cookie_uid),
        "as_of": _now_iso(),
    }


# --- Writes (the caller's own records only) --------------------------------------------


def _clean_bound(payload: Dict, key: str) -> Optional[float]:
    raw = (payload or {}).get(key)
    if raw is None or raw == "":
        return None
    val = _f(raw)
    if val is None or val <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"{key} must be a positive price.")
    return val


def apply_action(account_uid: str, cookie_uid: str,
                 payload: Dict) -> Dict:
    action = str((payload or {}).get("action") or "").lower()
    symbol = str((payload or {}).get("symbol") or "") \
        .strip().upper()
    if action not in ("arm", "disarm", "remove"):
        raise HTTPException(
            status_code=400,
            detail="action must be arm, disarm, or remove")
    if not symbol:
        raise HTTPException(status_code=400,
                            detail="symbol is required")
    ranges = _ranges_for(account_uid)
    if action == "arm":
        if symbol not in _armable_symbols(cookie_uid):
            raise HTTPException(
                status_code=400,
                detail="That coin isn't in the hub's price "
                       "set or your connected holdings, so "
                       "a range can't be armed for it.")
        buy = _clean_bound(payload, "buy_below")
        sell = _clean_bound(payload, "sell_above")
        if buy is None and sell is None:
            raise HTTPException(
                status_code=400,
                detail="Set a buy-at-or-below price, a "
                       "sell-at-or-above price, or both.")
        if buy is not None and sell is not None \
                and buy >= sell:
            raise HTTPException(
                status_code=400,
                detail="The buy price must be below the "
                       "sell price — otherwise the range "
                       "is already crossed.")
        if symbol not in ranges \
                and len(ranges) >= MAX_RANGES:
            raise HTTPException(
                status_code=400,
                detail=f"Ranges are capped at {MAX_RANGES} "
                       "coins — remove one first.")
        now = time.time()
        rec = dict(ranges.get(symbol) or {})
        rec.update({
            "buy_below": buy, "sell_above": sell,
            "armed": True, "last_zone": None,
            "updated_at": now,
        })
        rec.setdefault("created_at", now)
        ranges[symbol] = rec
        _put_blob(account_uid, "ranges", ranges)
    elif action == "disarm":
        if symbol not in ranges:
            raise HTTPException(status_code=404,
                                detail="No range for that coin.")
        rec = dict(ranges[symbol])
        rec["armed"] = False
        rec["last_zone"] = None
        rec["updated_at"] = time.time()
        ranges[symbol] = rec
        _put_blob(account_uid, "ranges", ranges)
    else:  # remove
        if symbol not in ranges:
            raise HTTPException(status_code=404,
                                detail="No range for that coin.")
        del ranges[symbol]
        _put_blob(account_uid, "ranges", ranges)
    return range_view(account_uid, cookie_uid)


# --- Routes ---------------------------------------------------------------------------


def register_hubrange_routes(app):

    @app.get("/cryptohub/range")
    def cryptohub_range_get(request: Request):
        if not hubrange_enabled():
            raise HTTPException(status_code=404,
                                detail="Not found")
        uid = _account_uid(request)
        if uid is None:
            raise HTTPException(
                status_code=401, detail="sign_in_required")
        return range_view(
            uid, request.cookies.get("ogai_uid") or "")

    @app.post("/cryptohub/range")
    def cryptohub_range_post(request: Request,
                             payload: Dict = Body(...)):
        if not hubrange_enabled():
            raise HTTPException(status_code=404,
                                detail="Not found")
        uid = _account_uid(request)
        if uid is None:
            raise HTTPException(
                status_code=401, detail="sign_in_required")
        return apply_action(
            uid, request.cookies.get("ogai_uid") or "",
            payload or {})
