"""Financial Smart Agent surface for OG W (Round 321 dashboards).

Brent's rules for this surface, binding:

- REAL DATA ONLY. Stock quotes come from the same keyless feeds
  the data pack uses — Yahoo Finance chart API (primary here:
  it carries the day range and volume) and the Nasdaq API
  (fallback for price/change). Market sentiment is COMPUTED
  from the three real index quotes (^GSPC, ^IXIC, ^DJI) and
  ships with the figures it was computed from.
- NO INVENTED NUMBERS. The design file's predicted price,
  confidence percentage, and buy/sell recommendation do NOT
  exist here — no such key is ever produced by this module.
  Where a prediction would go, the client shows an honest line:
  OG doesn't predict prices.
- PORTFOLIO IS HONESTLY UNAVAILABLE. No stock broker is
  connected to OG (Plaid is parked; og_trading's v1 has no
  stock-broker connect — stocks there are a chat handoff).
  There is no per-account stock-holdings source in the tree,
  so /finagent/portfolio says so instead of inventing
  positions or a default cash figure.
- LEARNING & ADAPTATION has no real feed either; that card is
  rendered client-side with an honest unavailable line and no
  route here pretends otherwise.

Kill switch: OG_FINAGENT_ENABLED — enabled unless the env
value is one of 0/false/no/off (the og_crew family pattern).
Disabled, every route here answers 404.

Handlers are plain def (FastAPI threadpool), never async: the
upstream fetches are sync network work and the Main Thread
Rule keeps waiting off the event loop. Quotes ride a short
TTL cache so an open dashboard can't stampede the upstreams.
"""

import logging
import os
import re
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# --- Kill switch ---------------------------------------------------------------


def finagent_enabled() -> bool:
    """OG_FINAGENT_ENABLED is the kill switch: enabled unless
    the env value is an explicit off (0/false/no/off)."""
    return os.environ.get(
        "OG_FINAGENT_ENABLED", "true").strip().lower() not in (
            "0", "false", "no", "off")


_QUOTE_TTL = 120.0  # seconds (the data pack's stocks TTL is 180)
_quote_cache: Dict[str, tuple] = {}
_quote_lock = threading.Lock()

_SYMBOL_RE = re.compile(r"^[A-Z0-9.^=\-]{1,12}$")

INDEXES = [
    {"symbol": "^GSPC", "name": "S&P 500"},
    {"symbol": "^IXIC", "name": "Nasdaq Composite"},
    {"symbol": "^DJI", "name": "Dow Jones Industrial Average"},
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(str(value).replace("$", "").replace(",", "")
                     .replace("%", "").strip())
    except (TypeError, ValueError):
        return None


# --- Upstream fetchers (the patch points for tests) ------------------------------


def _fetch_json(url: str, params: Dict = None) -> Optional[Dict]:
    """One JSON GET through og_trading's feed funnel (the UA the
    Nasdaq/Yahoo feeds accept); None on any failure."""
    try:
        import og_trading
        return og_trading._fetch_json(url, params=params)
    except Exception as e:
        logger.warning(f"FinAgent fetch failed ({url[:60]}): {e}")
        return None


def _fetch_yahoo_chart(symbol: str) -> Optional[Dict]:
    return _fetch_json(
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(symbol, safe=""),
        params={"range": "5d", "interval": "1d"})


def _fetch_nasdaq_info(symbol: str) -> Optional[Dict]:
    return _fetch_json(
        "https://api.nasdaq.com/api/quote/"
        f"{symbol.replace('.', '-')}/info",
        params={"assetclass": "stocks"})


# --- Structured quote ----------------------------------------------------------------


def _quote_yahoo(symbol: str) -> Optional[Dict]:
    data = _fetch_yahoo_chart(symbol)
    try:
        result = ((data or {}).get("chart") or {}).get("result") or []
        first = result[0] or {}
        meta = first.get("meta") or {}
    except Exception:
        return None
    price = _f(meta.get("regularMarketPrice"))
    if not price or price <= 0:
        return None
    prev = _f(meta.get("chartPreviousClose")) or \
        _f(meta.get("previousClose"))
    change = (price - prev) if prev else None
    change_pct = (change / prev * 100) if (prev and change is not None) \
        else None
    open_ = _f(meta.get("regularMarketOpen"))
    if open_ is None:
        # Daily bars: the latest non-null open is today's open.
        try:
            quotes = ((first.get("indicators") or {}).get("quote")
                      or [{}])[0]
            opens = [o for o in (quotes.get("open") or [])
                     if o is not None]
            open_ = float(opens[-1]) if opens else None
        except Exception:
            open_ = None
    return {
        "symbol": symbol,
        "name": meta.get("longName") or meta.get("shortName")
        or symbol,
        "price": price,
        "change": change,
        "change_pct": change_pct,
        "open": open_,
        "day_high": _f(meta.get("regularMarketDayHigh")),
        "day_low": _f(meta.get("regularMarketDayLow")),
        "volume": _f(meta.get("regularMarketVolume")),
        "market_cap": None,  # the Yahoo chart feed gives none
        "source": "Yahoo Finance",
        "as_of": _now_iso(),
    }


def _quote_nasdaq(symbol: str) -> Optional[Dict]:
    data = _fetch_nasdaq_info(symbol)
    d = (data or {}).get("data") or {}
    p = d.get("primaryData") or {}
    price = _f(p.get("lastSalePrice"))
    if not price or price <= 0:
        return None
    vol = p.get("volume")
    return {
        "symbol": symbol,
        "name": d.get("companyName") or symbol,
        "price": price,
        "change": _f(p.get("netChange")),
        "change_pct": _f(p.get("percentageChange")),
        "open": None,
        "day_high": None,
        "day_low": None,
        "volume": _f(vol) if vol is not None else None,
        "market_cap": None,  # the Nasdaq info feed gives none here
        "source": "Nasdaq",
        "as_of": _now_iso(),
    }


def _get_quote(symbol: str) -> Optional[Dict]:
    """One real structured quote (Yahoo first for the richer
    fields, Nasdaq fallback), TTL-cached; None when both feeds
    give nothing back."""
    with _quote_lock:
        hit = _quote_cache.get(symbol)
        if hit and hit[0] > time.time():
            return dict(hit[1])
    quote = None
    try:
        quote = _quote_yahoo(symbol)
    except Exception as e:
        logger.warning(f"FinAgent Yahoo quote {symbol}: {e}")
    if quote is None:
        try:
            quote = _quote_nasdaq(symbol)
        except Exception as e:
            logger.warning(f"FinAgent Nasdaq quote {symbol}: {e}")
    if quote is not None:
        with _quote_lock:
            _quote_cache[symbol] = (time.time() + _QUOTE_TTL, quote)
    return quote


def _sentiment() -> Optional[Dict]:
    """Market mood computed ONLY from the three real index
    quotes. All three are required — a partial basis would be
    a mood invented from less than it claims. None when any
    index quote fails."""
    rows = []
    for idx in INDEXES:
        quote = _get_quote(idx["symbol"])
        if quote is None or quote.get("change_pct") is None:
            return None
        rows.append({"symbol": idx["symbol"], "name": idx["name"],
                     "price": quote["price"],
                     "change_pct": quote["change_pct"]})
    avg = sum(r["change_pct"] for r in rows) / len(rows)
    if avg >= 0.5:
        label = "Bullish"
    elif avg <= -0.5:
        label = "Bearish"
    elif avg >= 0.15:
        label = "Mildly bullish"
    elif avg <= -0.15:
        label = "Mildly bearish"
    else:
        label = "Mixed / flat"
    return {"available": True, "label": label,
            "average_change_pct": round(avg, 4),
            "indexes": rows, "as_of": _now_iso(),
            "basis": "Average of the day's % change for the "
                     "S&P 500, Nasdaq Composite, and Dow Jones "
                     "Industrial Average, from live index quotes."}


# --- Routes ---------------------------------------------------------------------------


def register_finagent_routes(app):

    @app.get("/finagent/quote")
    def finagent_quote(request: Request, symbol: str = ""):
        if not finagent_enabled():
            raise HTTPException(status_code=404, detail="Not found")
        sym = str(symbol or "").strip().upper()
        if not sym or not _SYMBOL_RE.match(sym):
            return {"available": False,
                    "reason": "Enter a valid stock symbol."}
        quote = _get_quote(sym)
        if quote is None:
            return {"available": False,
                    "reason": f"No live quote came back for {sym} "
                              "— OG won't invent one. Try again "
                              "in a moment."}
        return {"available": True, **quote}

    @app.get("/finagent/sentiment")
    def finagent_sentiment(request: Request):
        if not finagent_enabled():
            raise HTTPException(status_code=404, detail="Not found")
        view = _sentiment()
        if view is None:
            return {"available": False,
                    "reason": "The live index quotes didn't all "
                              "come back, so no market mood is "
                              "shown rather than a made-up one."}
        return view

    @app.get("/finagent/portfolio")
    def finagent_portfolio(request: Request):
        if not finagent_enabled():
            raise HTTPException(status_code=404, detail="Not found")
        return {"available": False,
                "reason": "No stock broker is connected to OG — "
                          "bank access (Plaid) is parked and OG "
                          "has no brokerage feed, so there are "
                          "no real positions to show. OG won't "
                          "invent a portfolio."}
