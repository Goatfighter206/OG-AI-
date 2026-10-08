"""
FastAPI Web Service for OG-AI Agent
Exposes REST API endpoints for interacting with the AI agent.
"""

import asyncio
import hashlib
import hmac
import json
import os
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Optional
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

# Try to import enhanced agent, fallback to basic agent
try:
    from ai_agent_enhanced import EnhancedAIAgent as AIAgent
    print("*** Enhanced AI Agent loaded - Full intelligence mode activated! ***")
except Exception as e:
    print(f"*** Enhanced features not available: {e}")
    print("*** Installing required packages will enable full features")
    from ai_agent import AIAgent

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Check if running in development mode (for error detail control)
DEVELOPMENT_MODE = os.getenv("DEVELOPMENT_MODE", "false").lower() == "true"

# --- OG Pro (money layer) ----------------------------------------------------
# Free tier: each visitor (tracked by an `ogai_uid` cookie) gets a daily
# budget of model tokens per UTC day (prompt + completion, metered from the
# API's usage report; tokenizer estimate where no usage is reported). Pro
# visitors (holding a valid `ogai_pro` cookie) are unmetered. See /pro and
# /pro/success below. (This replaced the original 10-messages/day cap;
# OG_FREE_DAILY_LIMIT is still honored as a legacy alias for the budget so
# existing deployments/test setups that set it keep a working quota knob.)
FREE_DAILY_TOKENS = int(os.getenv(
    "OG_FREE_DAILY_TOKENS", os.getenv("OG_FREE_DAILY_LIMIT", "25000")))
# (Default raised 10,000 → 25,000 on 2026-10-08 at Brent's direction —
# he chose a bigger free budget over shortening OG's replies.)
PRO_UPGRADE_URL = os.getenv("OG_PRO_LINK", "#")
# SECURITY: set OG_PRO_TOKEN to a long random secret in production. The
# fallback below is only a placeholder and MUST be rotated before launch —
# anyone who knows the token can give themselves a Pro cookie.
PRO_TOKEN = os.getenv("OG_PRO_TOKEN", "CHANGE_ME_PRO_TOKEN")
USAGE_STORE_FILE = "usage_store.json"
COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # 1 year
_usage_lock = threading.Lock()


def _load_usage_store() -> Dict:
    """Load per-visitor daily chat counts from the JSON usage store."""
    if os.path.exists(USAGE_STORE_FILE):
        try:
            with open(USAGE_STORE_FILE, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Could not load usage store: {e}")
    return {}


def _save_usage_store(store: Dict):
    """Save per-visitor daily chat counts to the JSON usage store."""
    try:
        with open(USAGE_STORE_FILE, 'w') as f:
            json.dump(store, f, indent=2)
    except Exception as e:
        logger.warning(f"Could not save usage store: {e}")


def _today_entry(store: Dict, uid: str) -> Dict:
    """This visitor's usage entry for today (UTC), fresh if the day rolled."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry = store.get(uid)
    if not isinstance(entry, dict) or entry.get("date") != today:
        entry = {"date": today, "count": 0, "tokens": 0}
    entry.setdefault("count", 0)
    entry.setdefault("tokens", 0)
    return entry


def _free_tokens_remaining(uid: str) -> int:
    """Free tokens this visitor has left today (UTC)."""
    with _usage_lock:
        store = _load_usage_store()
    entry = _today_entry(store, uid)
    return max(0, FREE_DAILY_TOKENS - int(entry.get("tokens", 0)))


def _has_free_tokens(uid: str) -> bool:
    """True while the visitor still has free tokens today. A chat is allowed
    while any budget remains; its actual token cost is deducted afterwards,
    so the final call of the day can run the balance to (or past) zero."""
    return _free_tokens_remaining(uid) > 0


def _record_chat_usage(uid: str, tokens_used: int):
    """Record one /chat exchange for a free visitor: counts the message and
    deducts the model call's total tokens from today's free budget."""
    with _usage_lock:
        store = _load_usage_store()
        entry = _today_entry(store, uid)
        entry["count"] = int(entry.get("count", 0)) + 1
        entry["tokens"] = int(entry.get("tokens", 0)) + max(0, int(tokens_used))
        store[uid] = entry
        _save_usage_store(store)


_token_encoder = None
_token_encoder_tried = False


def _count_tokens(text) -> int:
    """Count tokens with tiktoken (cl100k_base) when available; otherwise a
    ~4-chars-per-token estimate. Only used where the API reports no usage."""
    global _token_encoder, _token_encoder_tried
    if not text:
        return 0
    if not _token_encoder_tried:
        _token_encoder_tried = True
        try:
            import tiktoken
            _token_encoder = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _token_encoder = None
    if _token_encoder is not None:
        try:
            return len(_token_encoder.encode(str(text)))
        except Exception:
            pass
    return max(1, len(str(text)) // 4)


def _estimate_call_tokens(agent_instance, reply_text: str) -> int:
    """Estimate a model call's TOTAL tokens (prompt + completion) for paths
    where the API hands back no usage: the agent's system prompt + the
    history window that is actually sent + the reply (counted once)."""
    total = _count_tokens(getattr(agent_instance, "system_prompt", "") or "")
    history = getattr(agent_instance, "conversation_history", []) or []
    window = [m for m in history[-10:]
              if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    reply_counted = False
    for m in window:
        total += _count_tokens(m.get("content", "")) + 4  # per-message overhead
        if reply_text and m.get("content") == reply_text:
            reply_counted = True
    if reply_text and not reply_counted:
        total += _count_tokens(reply_text)
    return total


# --- AI voice (text-to-speech) ----------------------------------------------
# When OPENAI_API_KEY is set, /tts turns OG's replies into realistic
# spoken audio (OpenAI TTS, deep male "onyx" voice). Without the key the
# endpoint answers 503 and the web page falls back to the device voice.
# A per-visitor daily cap keeps the key from being run up by strangers.
TTS_DAILY_LIMIT = int(os.getenv("OG_TTS_DAILY_LIMIT", "60"))
TTS_MAX_CHARS = 600


def _consume_tts_call(uid: str) -> bool:
    """Record one /tts call for this visitor today (UTC); False at cap."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"tts:{uid}"
    with _usage_lock:
        store = _load_usage_store()
        entry = store.get(key)
        if not isinstance(entry, dict) or entry.get("date") != today:
            entry = {"date": today, "count": 0}
        if entry["count"] >= TTS_DAILY_LIMIT:
            return False
        entry["count"] += 1
        store[key] = entry
        _save_usage_store(store)
        return True


# --- OG Pro entitlement v2 (Stripe webhook, ships dark) ----------------------
# v1 grants Pro to anyone who lands on /pro/success. v2 verifies payment with
# Stripe first: Stripe calls POST /stripe/webhook when a checkout completes,
# and the buyer is identified by the `ogai_uid` passed through checkout as
# client_reference_id (added by /pro while v2 is on). Entitlements live in the
# usage store, and /chat honors them directly — a confirmed buyer is Pro even
# if they never land back on /pro/success.
# Ships DISABLED: nothing changes until OG_WEBHOOK_ENABLED=true and
# STRIPE_WEBHOOK_SECRET are set in the service environment (see the report).
WEBHOOK_ENABLED = os.getenv("OG_WEBHOOK_ENABLED", "false").lower() == "true"
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
PRO_ENTITLED_KEY = "__pro_entitled__"
STRIPE_SIG_TOLERANCE = 300  # seconds


def _uid_is_entitled(uid: str) -> bool:
    """True when Stripe has confirmed this visitor's Pro payment (v2 only)."""
    if not WEBHOOK_ENABLED or not uid:
        return False
    with _usage_lock:
        store = _load_usage_store()
    entitled = store.get(PRO_ENTITLED_KEY)
    return isinstance(entitled, dict) and uid in entitled


def _grant_entitlement(uid: str, session_id: str = "", email: str = ""):
    """Record a Stripe-confirmed Pro entitlement for a visitor uid."""
    if not uid:
        return
    with _usage_lock:
        store = _load_usage_store()
        entitled = store.get(PRO_ENTITLED_KEY)
        if not isinstance(entitled, dict):
            entitled = {}
        entitled[uid] = {
            "granted": datetime.now(timezone.utc).isoformat(),
            "session": session_id,
            "email": email,
        }
        store[PRO_ENTITLED_KEY] = entitled
        _save_usage_store(store)


def _verify_stripe_signature(payload: bytes, sig_header: str, secret: str) -> bool:
    """
    Verify a Stripe webhook signature (the v1 HMAC-SHA256 scheme) without
    the Stripe SDK: the signed payload is "<timestamp>.<raw body>".
    """
    if not secret or not sig_header:
        return False
    try:
        fields: Dict[str, List[str]] = {}
        for part in sig_header.split(","):
            k, _, v = part.strip().partition("=")
            fields.setdefault(k, []).append(v)
        timestamp = fields["t"][0]
        signatures = fields.get("v1", [])
        if abs(time.time() - int(timestamp)) > STRIPE_SIG_TOLERANCE:
            return False
        signed = f"{timestamp}.".encode() + payload
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        return any(hmac.compare_digest(expected, sig) for sig in signatures)
    except Exception:
        return False


# --- Web lookup (Round 3) ----------------------------------------------------
# When a visitor's message needs current/external information, OG looks it
# up on the live web BEFORE answering, and the results ride into the model
# call as context — OG then answers grounded in fresh facts, in his own
# voice. Implemented entirely in this app layer: the agent's own
# detect_intent / web_search hooks are wrapped per instance (the agent's
# files are never modified), so BOTH chat paths — classic process_message
# and the streaming twin — get the upgrade through the one seam they share.
#
# Primary route: OpenAI's Responses API web_search tool, using the same
# OPENAI_API_KEY the service already runs on — no new signup. Fallbacks,
# in order: Tavily when OG_SEARCH_API_KEY is set, then the agent's original
# DuckDuckGo search. If every route fails, chat carries on without lookup
# results instead of erroring. A per-visitor daily cap (OG_LOOKUP_DAILY_LIMIT)
# keeps the search bill bounded; the lookup's tokens are added to the chat
# exchange's metered total — weighed at OG_LOOKUP_METER_CAP, because the
# search API counts the whole results page it read as input tokens (one
# lookup reported ~8k), which would otherwise eat a free visitor's entire
# 10k day in a single question. The exchange is still metered end to end;
# the cap only bounds the lookup's share of the bill.
SEARCH_MODEL = os.getenv("OG_SEARCH_MODEL", "gpt-4o-mini")
SEARCH_API_KEY = os.getenv("OG_SEARCH_API_KEY", "")  # optional Tavily key
LOOKUP_DAILY_LIMIT = int(os.getenv("OG_LOOKUP_DAILY_LIMIT", "25"))
LOOKUP_METER_CAP = int(os.getenv("OG_LOOKUP_METER_CAP", "2000"))
# Tokens spent by lookups during the exchange currently being processed,
# drained into that exchange's metered total by the chat paths. All chat
# processing is serialized under _memory_lock, so a single slot is safe.
_lookup_tokens_stash = {"tokens": 0}
# The visitor whose message is being processed right now (same reasoning).
_current_uid = {"uid": ""}
# The raw message being processed right now (set by the wrapped
# detect_intent). The agent's own intent detection sometimes hands the
# search hook a fragment ("in seattle today?"), so the Round 4 data
# router tries the raw message first and the search query second.
_current_message = {"text": ""}

_LOOKUP_TRIGGER_PHRASES = (
    "look up", "lookup", "search for", "search the web", "google it",
    "google search", "check online", "on the internet", "on the web",
)
_LOOKUP_TRIGGER_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\b(today|tonight|right now|currently|latest|this week|this month)\b",
    r"\b(news|headlines|weather|forecast)\b",
    r"\b(score|who won|who is winning|standings)\b",
    r"\b(price of|how much is|stock price|market cap|exchange rate)\b",
    r"\b(current|new)\s+(president|ceo|champion|pope|prime minister)\b",
    r"\bwhen (is|does|did)\b.*\b(release|launch|come out|happen|start)\b",
))


def _message_needs_lookup(message: str) -> bool:
    """App-layer heuristic: does this message need current/external info?"""
    if not message:
        return False
    low = message.lower()
    if any(p in low for p in _LOOKUP_TRIGGER_PHRASES):
        return True
    return any(p.search(message) for p in _LOOKUP_TRIGGER_PATTERNS)


def _consume_lookup(uid: str) -> bool:
    """Record one web lookup for this visitor today (UTC); False at cap."""
    if not uid:
        return True
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"lookup:{uid}"
    with _usage_lock:
        store = _load_usage_store()
        entry = store.get(key)
        if not isinstance(entry, dict) or entry.get("date") != today:
            entry = {"date": today, "count": 0}
        if entry["count"] >= LOOKUP_DAILY_LIMIT:
            return False
        entry["count"] += 1
        store[key] = entry
        _save_usage_store(store)
        return True


def _drain_lookup_tokens() -> int:
    """Take the lookup tokens stashed by the current exchange (and reset)."""
    tokens = int(_lookup_tokens_stash.get("tokens", 0))
    _lookup_tokens_stash["tokens"] = 0
    return tokens


def _openai_web_lookup(query: str):
    """One grounded lookup via OpenAI's Responses API web_search tool.

    Returns (answer_text, sources, tokens_used) — sources a list of
    {"title", "url"} — or None when the route is unavailable or fails."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    import httpx
    try:
        with httpx.Client(timeout=45) as client:
            r = client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": SEARCH_MODEL,
                    "tools": [{"type": "web_search"}],
                    "input": (
                        "Search the web and answer this with current facts, "
                        "briefly and concretely (dates, numbers, names): "
                        + str(query)
                    ),
                },
            )
        if r.status_code != 200:
            logger.warning(f"Web lookup upstream status: {r.status_code}")
            return None
        data = r.json()
    except Exception as e:
        logger.warning(f"Web lookup failed: {e}")
        return None
    text = (data.get("output_text") or "").strip()
    sources = []
    for item in data.get("output") or []:
        for content in item.get("content") or []:
            if not text and content.get("text"):
                text = str(content["text"]).strip()
            for ann in content.get("annotations") or []:
                if ann.get("type") == "url_citation" and ann.get("url"):
                    src = {"title": ann.get("title") or ann["url"],
                           "url": ann["url"]}
                    if src not in sources:
                        sources.append(src)
    if not text:
        return None
    usage = data.get("usage") or {}
    try:
        tokens = int(usage.get("total_tokens") or 0)
    except Exception:
        tokens = 0
    return text, sources[:3], tokens


def _tavily_web_lookup(query: str):
    """Fallback lookup via Tavily (only when OG_SEARCH_API_KEY is set).
    Same return shape as _openai_web_lookup, or None."""
    if not SEARCH_API_KEY:
        return None
    import httpx
    try:
        with httpx.Client(timeout=30) as client:
            r = client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {SEARCH_API_KEY}"},
                json={"query": query, "max_results": 3,
                      "include_answer": True},
            )
        if r.status_code != 200:
            return None
        data = r.json()
    except Exception as e:
        logger.warning(f"Tavily lookup failed: {e}")
        return None
    answer = (data.get("answer") or "").strip()
    sources = [{"title": s.get("title") or s.get("url", ""),
                "url": s.get("url", "")}
               for s in (data.get("results") or [])[:3] if s.get("url")]
    if not answer:
        return None
    return answer, sources, 0


def _og_web_search(agent_instance, query: str, num_results: int = 5):
    """The app-layer search behind the agent's web_search hook.

    OpenAI lookup first, Tavily second, the agent's built-in DuckDuckGo
    search last. Returns the same List[Dict] shape the agent's own
    web_search returns ({title, body, href}) so both chat paths format the
    results into model context exactly as before."""
    uid = _current_uid.get("uid", "")
    if uid and not _consume_lookup(uid):
        logger.info("Web lookup skipped: visitor at daily lookup cap")
        return []
    # Round 4: structured live data (weather, scores, quotes, headlines)
    # gets first crack at the question; a miss falls through to the
    # general web lookup routes below.
    data_results = _og_data_tools(query)
    if data_results:
        return data_results[: max(1, num_results)]
    for route in (_openai_web_lookup, _tavily_web_lookup):
        try:
            got = route(query)
        except Exception as e:
            logger.warning(f"Web lookup route failed: {e}")
            got = None
        if got:
            text, sources, tokens = got
            if tokens:
                _lookup_tokens_stash["tokens"] += min(
                    int(tokens), LOOKUP_METER_CAP)
            results = [{
                "title": "Live web lookup",
                "body": text[:1800],
                "href": sources[0]["url"] if sources else "",
            }]
            for s in sources:
                results.append({"title": s["title"], "body": s["url"],
                                "href": s["url"]})
            return results[: max(1, num_results)]
    original = getattr(agent_instance, "_og_original_web_search", None)
    if original is not None:
        try:
            return original(query, num_results)
        except Exception as e:
            logger.warning(f"Built-in web search failed: {e}")
    return []


def _install_lookup_tools(agent_instance):
    """Wrap the agent instance's detect_intent + web_search — app layer
    only, the agent's files are never modified — so lookup triggers cover
    current-events questions and search runs on the OpenAI route."""
    if getattr(agent_instance, "_og_lookup_installed", False):
        return
    original_detect = getattr(agent_instance, "detect_intent", None)
    original_search = getattr(agent_instance, "web_search", None)
    if original_detect is None or original_search is None:
        return
    agent_instance._og_original_web_search = original_search

    def detect_intent_wrapped(message):
        _current_message["text"] = str(message)
        intent = original_detect(message)
        try:
            if (isinstance(intent, dict)
                    and not intent.get("needs_web_search")
                    and not intent.get("needs_code_generation")
                    and (_message_needs_lookup(message)
                         or _message_needs_data(message))):
                intent["needs_web_search"] = True
                intent["search_query"] = str(message).strip()
        except Exception as e:
            logger.warning(f"Lookup trigger check failed: {e}")
        return intent

    def web_search_wrapped(query, num_results=5):
        return _og_web_search(agent_instance, query, num_results)

    agent_instance.detect_intent = detect_intent_wrapped
    agent_instance.web_search = web_search_wrapped
    agent_instance._og_lookup_installed = True


# --- Live data pack (Round 4) ------------------------------------------------
# First-class data tools behind the same web_search seam as Round 3's
# lookup: when a visitor's question is really a data question — weather,
# a score, a stock or crypto quote, headlines — OG answers from a live
# structured source instead of a general web search. Every route is
# keyless/public (no new signups):
#   WEATHER  Open-Meteo geocoding + forecast APIs
#   SPORTS   ESPN's public scoreboard / team-schedule JSON
#   STOCKS   Nasdaq API quote info, Yahoo Finance chart API fallback
#            (Stooq's CSV feed is bot-walled behind a JS challenge from
#            server IPs, so it can't serve as a server-side route)
#   CRYPTO   Coinbase Exchange public stats, CoinGecko free fallback
#   NEWS     Google News RSS (top stories, or a per-topic search feed)
# A tool returns None when its category doesn't parse (weather with no
# place named, an unrecognized ticker) or its routes fail — the question
# then falls through to Round 3's web lookup chain untouched, so every
# category still answers live via some route. Results ride into the
# model call in the same {title, body, href} shape both chat paths
# already format, size-capped like a lookup result. Responses are
# cached briefly (2–5 minutes per category; team lists an hour) to stay
# a good citizen on free endpoints.
# Metering: data questions pass through _og_web_search AFTER its
# per-visitor daily lookup-cap check, so they share the Round 3 lookup
# budget (OG_LOOKUP_DAILY_LIMIT); the fetches themselves are keyless and
# bill no upstream tokens, so nothing is added to the lookup token
# stash — the exchange's metered total (which includes this context)
# remains the honest end-to-end bill, exactly like a lookup.
_DATA_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0 Safari/537.36"}
_data_cache: Dict[str, tuple] = {}
_data_cache_lock = threading.Lock()
_DATA_TTL = {"weather": 300, "sports": 120, "stocks": 180,
             "crypto": 120, "news": 300}


def _data_cached(key: str):
    with _data_cache_lock:
        hit = _data_cache.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
    return None


def _data_store(key: str, value, ttl: int):
    with _data_cache_lock:
        _data_cache[key] = (time.time() + ttl, value)
    return value


def _fetch_json(url: str, timeout: float = 20.0):
    """GET a JSON document for the data pack; None on any failure."""
    import httpx
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.get(url, headers=_DATA_UA)
        if r.status_code != 200:
            logger.warning(f"Data fetch {r.status_code}: {url[:90]}")
            return None
        return r.json()
    except Exception as e:
        logger.warning(f"Data fetch failed ({url[:60]}...): {e}")
        return None


def _fetch_text(url: str, timeout: float = 20.0):
    """GET a text document for the data pack; None on any failure."""
    import httpx
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.get(url, headers=_DATA_UA)
        if r.status_code != 200:
            logger.warning(f"Data fetch {r.status_code}: {url[:90]}")
            return None
        return r.text
    except Exception as e:
        logger.warning(f"Data fetch failed ({url[:60]}...): {e}")
        return None


# --- Round 4: weather (Open-Meteo) -------------------------------------------
_WMO_CODES = {
    0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy with frost", 51: "light drizzle",
    53: "drizzle", 55: "steady drizzle", 56: "freezing drizzle",
    57: "freezing drizzle", 61: "light rain", 63: "rain",
    65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light rain showers", 81: "rain showers",
    82: "violent rain showers", 85: "snow showers", 86: "snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail",
    99: "thunderstorms with hail",
}
_PLACE_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\bweather\s+(?:in|for|at|near)\s+([A-Za-z][A-Za-z .'-]{1,40})",
    r"\bforecast\s+(?:in|for|at|near)\s+([A-Za-z][A-Za-z .'-]{1,40})",
    r"\btemperature\s+in\s+([A-Za-z][A-Za-z .'-]{1,40})",
    r"\braining\s+in\s+([A-Za-z][A-Za-z .'-]{1,40})",
    r"\bsnowing\s+in\s+([A-Za-z][A-Za-z .'-]{1,40})",
    r"^([A-Za-z][A-Za-z .'-]{1,40}?)\s+weather\b",
))
_PLACE_STOPWORDS = {"today", "tomorrow", "tonight", "outside", "here",
                    "there", "the", "my", "this", "week", "weekend"}


def _extract_place(query: str):
    """Pull the place out of a weather question; None when unnamed."""
    q = " ".join(str(query).split())
    for pat in _PLACE_PATTERNS:
        m = pat.search(q)
        if not m:
            continue
        place = m.group(1)
        low = place.lower()
        for stop in (" today", " tomorrow", " tonight", " this week",
                     " this weekend", " right now", " please", " near me"):
            idx = low.find(stop)
            if idx > 0:
                place = place[:idx]
                low = place.lower()
        place = place.strip(" .,'-")
        if len(place) >= 2 and low.strip() not in _PLACE_STOPWORDS:
            return place
    return None


def _geocode(place: str):
    """Resolve a place name to (lat, lon, name, region) or None.
    Open-Meteo's geocoder first, Nominatim (OpenStreetMap) as fallback —
    the two live on different hosts, so one being unreachable from the
    server doesn't kill the weather tool."""
    import urllib.parse
    geo = _fetch_json("https://geocoding-api.open-meteo.com/v1/search?"
                      + urllib.parse.urlencode(
                          {"name": place, "count": 1, "language": "en",
                           "format": "json"}))
    results = (geo or {}).get("results") or []
    if results:
        g = results[0]
        return (g["latitude"], g["longitude"], g.get("name") or place,
                g.get("admin1") or g.get("country") or "")
    data = _fetch_json("https://nominatim.openstreetmap.org/search?"
                       + urllib.parse.urlencode(
                           {"q": place, "format": "json", "limit": 1,
                            "addressdetails": 1}))
    if data:
        try:
            top = data[0]
            addr = top.get("address") or {}
            name = (addr.get("city") or addr.get("town")
                    or addr.get("village")
                    or (top.get("display_name") or place).split(",")[0])
            region = (addr.get("state") or addr.get("country") or "")
            return (float(top["lat"]), float(top["lon"]), name, region)
        except Exception as e:
            logger.warning(f"Nominatim geocode parse failed: {e}")
    return None


def _tool_weather(query: str):
    """Live weather via Open-Meteo. (label, text, source) or None."""
    low = str(query).lower()
    if not any(w in low for w in ("weather", "forecast", "temperature",
                                  "raining", "snowing")):
        return None
    place = _extract_place(query)
    if not place:
        return None  # no place named — the web lookup route takes it
    key = "weather:" + place.lower()
    hit = _data_cached(key)
    if hit:
        return hit
    import urllib.parse
    geo = _geocode(place)
    if not geo:
        return None
    g = {"latitude": geo[0], "longitude": geo[1], "name": geo[2],
         "admin1": geo[3]}
    fc = _fetch_json("https://api.open-meteo.com/v1/forecast?"
                     + urllib.parse.urlencode({
                         "latitude": g["latitude"],
                         "longitude": g["longitude"],
                         "current": "temperature_2m,relative_humidity_2m,"
                                    "apparent_temperature,weather_code,"
                                    "wind_speed_10m",
                         "daily": "temperature_2m_max,temperature_2m_min,"
                                  "weather_code,"
                                  "precipitation_probability_max",
                         "temperature_unit": "fahrenheit",
                         "wind_speed_unit": "mph",
                         "forecast_days": 3, "timezone": "auto"}))
    if not fc:
        return None
    cur = fc.get("current") or {}
    daily = fc.get("daily") or {}

    def _wmo(code):
        try:
            return _WMO_CODES.get(int(code), "mixed conditions")
        except Exception:
            return "mixed conditions"

    where = g.get("name") or place
    region = g.get("admin1") or g.get("country") or ""
    lines = ["Weather for " + str(where)
             + (f", {region}" if region else "") + " (Open-Meteo, live):"]
    if cur:
        lines.append(
            f"Right now: {cur.get('temperature_2m')}°F, "
            f"{_wmo(cur.get('weather_code'))}, feels like "
            f"{cur.get('apparent_temperature')}°F, humidity "
            f"{cur.get('relative_humidity_2m')}%, wind "
            f"{cur.get('wind_speed_10m')} mph.")
    days = daily.get("time") or []
    highs = daily.get("temperature_2m_max") or []
    lows_d = daily.get("temperature_2m_min") or []
    codes = daily.get("weather_code") or []
    pops = daily.get("precipitation_probability_max") or []
    for i in range(min(3, len(days), len(highs), len(lows_d), len(codes))):
        label = "Today" if i == 0 else ("Tomorrow" if i == 1 else days[i])
        line = (f"{label}: high {highs[i]}°F / low {lows_d[i]}°F, "
                f"{_wmo(codes[i])}")
        if i < len(pops) and pops[i] is not None:
            line += f", rain chance {pops[i]}%"
        lines.append(line + ".")
    return _data_store(key, ("Live weather data", "\n".join(lines),
                             "https://open-meteo.com/"),
                       _DATA_TTL["weather"])


# --- Round 4: sports (ESPN public JSON) --------------------------------------
_ESPN_LEAGUES = {
    "nfl": ("football", "nfl", "NFL"),
    "nba": ("basketball", "nba", "NBA"),
    "mlb": ("baseball", "mlb", "MLB"),
    "nhl": ("hockey", "nhl", "NHL"),
}
_SPORTS_CUE_RE = re.compile(
    r"\b(score|scores|game|games|win|won|beat|playing|played|plays|"
    r"schedule|standings|playoffs?|final|results?|tonight|today|"
    r"yesterday|season|next|last|doing|vs|versus)\b", re.IGNORECASE)


def _espn_teams(league_key: str) -> Dict[str, Dict]:
    """All teams in one ESPN league with alias sets (cached an hour).
    Aliases come from the display name and mascot name only — bare city
    names are ambiguous across teams, so they never match a team."""
    key = "teams:" + league_key
    hit = _data_cached(key)
    if hit is not None:
        return hit
    sport, league, _label = _ESPN_LEAGUES[league_key]
    data = _fetch_json("https://site.api.espn.com/apis/site/v2/sports/"
                       f"{sport}/{league}/teams")
    teams: Dict[str, Dict] = {}
    try:
        raw = data["sports"][0]["leagues"][0]["teams"]
    except Exception:
        raw = []
    for entry in raw or []:
        t = entry.get("team") or {}
        if not t.get("id"):
            continue
        aliases = set()
        for field in ("displayName", "name"):
            v = str(t.get(field) or "").strip().lower()
            if len(v) >= 3:
                aliases.add(v)
        teams[str(t["id"])] = {
            "id": str(t["id"]),
            "name": t.get("displayName") or t.get("name") or "?",
            "abbr": t.get("abbreviation") or "?",
            "aliases": aliases,
        }
    if teams:
        _data_store(key, teams, 3600)
    return teams


def _find_team(query_low: str, league_key: str = None):
    """Longest-alias team match: (league_key, team, alias) or None."""
    best = None
    for lk in ([league_key] if league_key else list(_ESPN_LEAGUES)):
        for team in _espn_teams(lk).values():
            for alias in team["aliases"]:
                if alias in query_low:
                    if best is None or len(alias) > best[0]:
                        best = (len(alias), lk, team, alias)
    return (best[1], best[2], best[3]) if best else None


def _espn_score_str(competitor) -> str:
    s = (competitor or {}).get("score")
    if isinstance(s, dict):
        return str(s.get("displayValue") or "0")
    return str(s) if s is not None else "0"


def _espn_game_state(event) -> str:
    try:
        return event["competitions"][0]["status"]["type"]["state"] or ""
    except Exception:
        return ""


def _espn_game_line(event) -> str:
    comp = (event.get("competitions") or [{}])[0]
    st = ((comp.get("status") or {}).get("type") or {})
    state = st.get("state") or ""
    detail = st.get("shortDetail") or st.get("detail") or ""
    cs = comp.get("competitors") or []

    def _nm(c):
        return (c.get("team") or {}).get("abbreviation") or "?"

    away = next((c for c in cs if c.get("homeAway") == "away"),
                cs[0] if cs else {})
    home = next((c for c in cs if c.get("homeAway") == "home"),
                cs[1] if len(cs) > 1 else away)
    if state == "in":
        return (f"{_nm(away)} {_espn_score_str(away)} at "
                f"{_nm(home)} {_espn_score_str(home)} — live ({detail})")
    if state == "post":
        return (f"Final: {_nm(away)} {_espn_score_str(away)} at "
                f"{_nm(home)} {_espn_score_str(home)}")
    return f"{_nm(away)} at {_nm(home)} — {detail or event.get('date', '')}"


def _espn_events(url: str, cache_key: str):
    hit = _data_cached(cache_key)
    if hit is not None:
        return hit
    data = _fetch_json(url, timeout=30.0)
    events = (data or {}).get("events") or []
    return _data_store(cache_key, events, _DATA_TTL["sports"])


def _tool_sports(query: str):
    """Live scores/schedules via ESPN. (label, text, source) or None."""
    from datetime import timedelta
    low = str(query).lower()
    league_key = None
    for lk in _ESPN_LEAGUES:
        if re.search(rf"\b{lk}\b", low):
            league_key = lk
            break
    found = _find_team(low, league_key)
    if found:
        lk, team, alias_hit = found
        # A bare mascot match with no sports cue at all ("movie stars")
        # is not a sports question — let another route take it.
        if not (league_key or _SPORTS_CUE_RE.search(low)
                or " at " in f" {low} " or len(alias_hit) >= 8):
            found = None
    if found:
        lk, team, _alias = found
        sport, league, label = _ESPN_LEAGUES[lk]
        sched_url = ("https://site.api.espn.com/apis/site/v2/sports/"
                     f"{sport}/{league}/teams/{team['id']}/schedule")
        events = _espn_events(sched_url, f"sched:{lk}:{team['id']}")
        if not events:
            # The default schedule only covers the current phase (e.g.
            # MLB's postseason) — a team that's done for the year shows
            # zero games there, so retry the regular-season phase.
            events = _espn_events(sched_url + "?seasontype=2",
                                  f"sched:{lk}:{team['id']}:reg")
        if not events:
            return None
        live = [e for e in events if _espn_game_state(e) == "in"]
        finals = [e for e in events if _espn_game_state(e) == "post"]
        upcoming = [e for e in events if _espn_game_state(e) == "pre"]
        lines = [f"{team['name']} ({label}) — latest from ESPN:"]
        for e in live[:1]:
            lines.append(_espn_game_line(e) + ".")
        if finals:
            lines.append(_espn_game_line(finals[-1])
                         + f" (played {finals[-1].get('date', '')[:10]}).")
        if upcoming:
            lines.append("Next game: " + _espn_game_line(upcoming[0]) + ".")
        if len(lines) == 1:
            return None
        return ("Live sports data", "\n".join(lines),
                "https://www.espn.com/")
    if league_key or "score" in low:
        keys = [league_key] if league_key else list(_ESPN_LEAGUES)
        blocks = []
        for lk in keys:
            sport, league, label = _ESPN_LEAGUES[lk]
            base = ("https://site.api.espn.com/apis/site/v2/sports/"
                    f"{sport}/{league}/scoreboard")
            merged: Dict[str, Dict] = {}
            urls = [(base, f"sb:{lk}")]
            yday = (datetime.now(timezone.utc) - timedelta(days=1)
                    ).strftime("%Y%m%d")
            urls.append((f"{base}?dates={yday}", f"sb:{lk}:{yday}"))
            if lk == "nfl":
                d4 = (datetime.now(timezone.utc) - timedelta(days=4)
                      ).strftime("%Y%m%d")
                urls.append((f"{base}?dates={d4}", f"sb:{lk}:{d4}"))
            for url, ck in urls:
                for e in _espn_events(url, ck):
                    merged[str(e.get("id"))] = e
            events = list(merged.values())
            if not events:
                continue
            live = [e for e in events if _espn_game_state(e) == "in"]
            finals = sorted(
                (e for e in events if _espn_game_state(e) == "post"),
                key=lambda e: e.get("date", ""))
            sched = sorted(
                (e for e in events if _espn_game_state(e) == "pre"),
                key=lambda e: e.get("date", ""))
            picks = live[:2] + finals[-3:] + sched[:2]
            if picks:
                blocks.append(label + " — " + "; ".join(
                    _espn_game_line(e) for e in picks))
        if blocks:
            return ("Live sports scores", "\n".join(blocks),
                    "https://www.espn.com/")
    return None


# --- Round 4: stocks (Nasdaq API, Yahoo Finance fallback) --------------------
_STOCK_NAME_MAP = {
    "apple": "AAPL", "microsoft": "MSFT", "tesla": "TSLA",
    "amazon": "AMZN", "nvidia": "NVDA", "google": "GOOGL",
    "alphabet": "GOOGL", "meta": "META", "facebook": "META",
    "netflix": "NFLX", "amd": "AMD", "advanced micro devices": "AMD",
    "ford": "F", "boeing": "BA", "disney": "DIS", "walmart": "WMT",
    "jpmorgan": "JPM", "bank of america": "BAC", "exxon": "XOM",
    "chevron": "CVX", "pfizer": "PFE", "coca-cola": "KO", "coke": "KO",
    "pepsi": "PEP", "nike": "NKE", "starbucks": "SBUX",
    "paypal": "PYPL", "intel": "INTC", "coinbase": "COIN",
    "gamestop": "GME", "amc": "AMC", "palantir": "PLTR", "visa": "V",
    "mastercard": "MA", "home depot": "HD", "costco": "COST",
    "target": "TGT", "uber": "UBER", "lyft": "LYFT", "airbnb": "ABNB",
    "snap": "SNAP", "spotify": "SPOT", "roblox": "RBLX",
    "rivian": "RIVN", "lucid": "LCID", "alibaba": "BABA",
    "shopify": "SHOP", "salesforce": "CRM", "oracle": "ORCL",
    "adobe": "ADBE", "broadcom": "AVGO", "micron": "MU", "arm": "ARM",
    "berkshire hathaway": "BRK.B", "berkshire": "BRK.B",
}
_STOCK_INDEX_MAP = {
    "s&p 500": "^GSPC", "s&p500": "^GSPC", "sp500": "^GSPC",
    "s&p": "^GSPC", "dow jones": "^DJI", "the dow": "^DJI", "dow": "^DJI",
    "nasdaq composite": "^IXIC", "nasdaq": "^IXIC",
    "russell 2000": "^RUT",
}
_STOCK_ETFS = {"SPY", "QQQ", "DIA", "IWM", "VOO", "VTI", "GLD", "SLV",
               "TLT", "ARKK", "XLF", "XLK", "EEM", "AGG", "BND", "USO",
               "SMH", "SOXX", "HYG", "LQD"}
_CAPS_STOPWORDS = {
    "THE", "AND", "FOR", "YOU", "ARE", "NOT", "ALL", "CAN", "GET",
    "HAS", "HOW", "NEW", "NOW", "ONE", "OUR", "OUT", "SEE", "TWO",
    "WAY", "WHO", "DID", "SAY", "SHE", "TOO", "USE", "USA", "GDP",
    "CEO", "CFO", "IPO", "FED", "SEC", "FDA", "CDC", "FBI", "CIA",
    "NFL", "NBA", "MLB", "NHL", "AI", "OK", "VS", "AM", "PM", "ET",
    "PT", "EST", "PST", "EDT", "PDT", "USD", "LOL", "OMG", "FAQ",
}
_STOCK_CTX = ("stock", "share", "ticker", "trading", "quote", "market",
              "nasdaq", "nyse", "earnings", "dividend")


def _stock_quote_nasdaq(ticker: str):
    order = ("etf", "stocks") if ticker in _STOCK_ETFS else ("stocks", "etf")
    for asset in order:
        data = _fetch_json("https://api.nasdaq.com/api/quote/"
                           f"{ticker}/info?assetclass={asset}")
        d = (data or {}).get("data") or {}
        p = d.get("primaryData") or {}
        if p.get("lastSalePrice"):
            vol = p.get("volume")
            try:
                vol = f"{float(str(vol).replace(',', '')):,.0f}"
            except Exception:
                vol = vol or "n/a"
            return (f"{d.get('companyName') or ticker} ({ticker}) — "
                    f"{p.get('lastSalePrice')}, {p.get('netChange') or ''} "
                    f"({p.get('percentageChange') or ''}) on the day "
                    f"(Nasdaq, live). Volume {vol}. "
                    f"Market status: {d.get('marketStatus') or 'unknown'}; "
                    f"last trade {p.get('lastTradeTimestamp') or 'n/a'}.")
    return None


def _stock_quote_yahoo(symbol: str):
    import urllib.parse
    data = _fetch_json("https://query1.finance.yahoo.com/v8/finance/chart/"
                       + urllib.parse.quote(symbol, safe="")
                       + "?range=5d&interval=1d")
    try:
        meta = data["chart"]["result"][0]["meta"]
    except Exception:
        return None
    price = meta.get("regularMarketPrice")
    if price is None:
        return None
    name = meta.get("longName") or meta.get("shortName") or symbol
    out = f"{name} ({symbol}) — ${price:,.2f} (Yahoo Finance, live)"
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if prev:
        chg = price - prev
        out += f", {chg:+,.2f} ({chg / prev * 100:+.2f}%) vs previous close"
    out += "."
    lo = meta.get("regularMarketDayLow")
    hi = meta.get("regularMarketDayHigh")
    if lo is not None and hi is not None:
        out += f" Day range ${lo:,.2f}–${hi:,.2f}."
    vol = meta.get("regularMarketVolume")
    if vol:
        out += f" Volume {vol:,}."
    return out


def _tool_stocks(query: str):
    """Live stock quote. (label, text, source) or None."""
    low = str(query).lower()
    ticker = None
    m = re.search(r"\$([A-Za-z]{1,5}(?:\.[A-Za-z]{1,2})?)\b", str(query))
    if m:
        ticker = m.group(1).upper()
    if not ticker:
        for phrase, sym in sorted(_STOCK_INDEX_MAP.items(),
                                  key=lambda kv: -len(kv[0])):
            if phrase in low:
                ticker = sym
                break
    ctx = (any(w in low for w in _STOCK_CTX) or "price" in low
           or "doing" in low or "worth" in low)
    if not ticker and ctx:
        for name, sym in sorted(_STOCK_NAME_MAP.items(),
                                key=lambda kv: -len(kv[0])):
            if re.search(rf"\b{re.escape(name)}\b", low):
                ticker = sym
                break
    if not ticker and ctx:
        for tok in re.findall(r"\b[A-Z]{2,5}\b", str(query)):
            if tok not in _CAPS_STOPWORDS:
                ticker = tok
                break
    if not ticker:
        return None
    key = "stock:" + ticker
    hit = _data_cached(key)
    if hit:
        return hit
    if ticker.startswith("^"):
        text = _stock_quote_yahoo(ticker)
    elif "." in ticker:
        text = _stock_quote_yahoo(ticker.replace(".", "-"))
    else:
        text = _stock_quote_nasdaq(ticker) or _stock_quote_yahoo(ticker)
    if not text:
        return None
    return _data_store(key, ("Live stock quote", text,
                             "https://www.nasdaq.com/market-activity"),
                       _DATA_TTL["stocks"])


# --- Round 4: crypto (Coinbase Exchange, CoinGecko fallback) ------------------
# alias -> (Coinbase product or None, CoinGecko id, display, needs_context)
# Short/ambiguous aliases (btc, link, dot...) only route with a price-ish
# context or a very short query, so ordinary chat never trips them.
_CRYPTO_MAP = {
    "bitcoin": ("BTC-USD", "bitcoin", "Bitcoin (BTC)", False),
    "btc": ("BTC-USD", "bitcoin", "Bitcoin (BTC)", True),
    "ethereum": ("ETH-USD", "ethereum", "Ethereum (ETH)", False),
    "eth": ("ETH-USD", "ethereum", "Ethereum (ETH)", True),
    "solana": ("SOL-USD", "solana", "Solana (SOL)", False),
    "sol": ("SOL-USD", "solana", "Solana (SOL)", True),
    "dogecoin": ("DOGE-USD", "dogecoin", "Dogecoin (DOGE)", False),
    "doge": ("DOGE-USD", "dogecoin", "Dogecoin (DOGE)", True),
    "ripple": ("XRP-USD", "ripple", "XRP", False),
    "xrp": ("XRP-USD", "ripple", "XRP", True),
    "cardano": ("ADA-USD", "cardano", "Cardano (ADA)", False),
    "ada": ("ADA-USD", "cardano", "Cardano (ADA)", True),
    "litecoin": ("LTC-USD", "litecoin", "Litecoin (LTC)", False),
    "ltc": ("LTC-USD", "litecoin", "Litecoin (LTC)", True),
    "chainlink": ("LINK-USD", "chainlink", "Chainlink (LINK)", False),
    "link": ("LINK-USD", "chainlink", "Chainlink (LINK)", True),
    "polkadot": ("DOT-USD", "polkadot", "Polkadot (DOT)", False),
    "dot": ("DOT-USD", "polkadot", "Polkadot (DOT)", True),
    "avalanche": ("AVAX-USD", "avalanche-2", "Avalanche (AVAX)", False),
    "avax": ("AVAX-USD", "avalanche-2", "Avalanche (AVAX)", True),
    "polygon": ("MATIC-USD", "matic-network", "Polygon (MATIC)", False),
    "matic": ("MATIC-USD", "matic-network", "Polygon (MATIC)", True),
    "shiba inu": ("SHIB-USD", "shiba-inu", "Shiba Inu (SHIB)", False),
    "shib": ("SHIB-USD", "shiba-inu", "Shiba Inu (SHIB)", True),
    "bitcoin cash": ("BCH-USD", "bitcoin-cash", "Bitcoin Cash (BCH)",
                     False),
    "bch": ("BCH-USD", "bitcoin-cash", "Bitcoin Cash (BCH)", True),
    "stellar": ("XLM-USD", "stellar", "Stellar (XLM)", False),
    "xlm": ("XLM-USD", "stellar", "Stellar (XLM)", True),
    "cosmos": ("ATOM-USD", "cosmos", "Cosmos (ATOM)", False),
    "atom": ("ATOM-USD", "cosmos", "Cosmos (ATOM)", True),
    "uniswap": ("UNI-USD", "uniswap", "Uniswap (UNI)", False),
    "uni": ("UNI-USD", "uniswap", "Uniswap (UNI)", True),
    "aptos": ("APT-USD", "aptos", "Aptos (APT)", False),
    "sui": ("SUI-USD", "sui", "Sui (SUI)", True),
    "pepe": (None, "pepe", "Pepe (PEPE)", True),
    "near protocol": ("NEAR-USD", "near", "Near Protocol (NEAR)", False),
}
_CRYPTO_CTX_RE = re.compile(
    r"\b(price|worth|crypto|coin|trading|market|doing|cost|much)\b",
    re.IGNORECASE)


def _tool_crypto(query: str):
    """Live crypto quote. (label, text, source) or None."""
    low = str(query).lower()
    if "stock" in low or "shares" in low:
        return None  # "Coinbase stock" belongs to the stock tool
    found = None
    for alias, info in sorted(_CRYPTO_MAP.items(),
                              key=lambda kv: -len(kv[0])):
        if not re.search(rf"\b{re.escape(alias)}\b", low):
            continue
        if not info[3]:
            found = info
            break
        if (len(str(query).split()) <= 4 or _CRYPTO_CTX_RE.search(low)
                or " at " in f" {low} "):
            found = info
            break
    if not found:
        return None
    product, gecko, name = found[0], found[1], found[2]
    key = "crypto:" + gecko
    hit = _data_cached(key)
    if hit:
        return hit
    text = None
    source = "https://www.coinbase.com/"
    if product:
        data = _fetch_json("https://api.exchange.coinbase.com/products/"
                           f"{product}/stats")
        try:
            last = float(data["last"])
            opn = float(data.get("open") or last)
            high = float(data["high"])
            low24 = float(data["low"])
            vol = float(data.get("volume") or 0)
            chg = ((last - opn) / opn * 100) if opn else 0.0
            text = (f"{name} — ${last:,.2f} (Coinbase Exchange, live). "
                    f"Last 24h: high ${high:,.2f} / low ${low24:,.2f}, "
                    f"{chg:+.2f}% vs the 24h open, volume {vol:,.2f}.")
        except Exception:
            text = None
    if not text:
        import urllib.parse
        data = _fetch_json(
            "https://api.coingecko.com/api/v3/simple/price?"
            + urllib.parse.urlencode(
                {"ids": gecko, "vs_currencies": "usd",
                 "include_24hr_change": "true",
                 "include_market_cap": "true"}))
        d = (data or {}).get(gecko) or {}
        if d.get("usd"):
            text = (f"{name} — ${d['usd']:,.2f} (CoinGecko, live). "
                    f"24h change {d.get('usd_24h_change') or 0:+.2f}%, "
                    f"market cap ${d.get('usd_market_cap') or 0:,.0f}.")
            source = "https://www.coingecko.com/"
    if not text:
        return None
    return _data_store(key, ("Live crypto quote", text, source),
                       _DATA_TTL["crypto"])


# --- Round 4: news (Google News RSS) ------------------------------------------
def _clean_topic(topic: str):
    t = " ".join(str(topic).split()).strip(" ?.!,")
    low = t.lower()
    for stop in (" today", " right now", " please", " headlines", " news"):
        idx = low.find(stop)
        if idx > 2:
            t = t[:idx]
            low = t.lower()
    t = t.strip(" ?.!,")
    if len(t) < 3 or low in ("top", "top stories", "the", "latest"):
        return None
    return t[:80]


def _tool_news(query: str):
    """Top or topical headlines via Google News RSS. Tuple or None."""
    import urllib.parse
    import xml.etree.ElementTree as ET
    low = str(query).lower()
    if "news" not in low and "headline" not in low \
            and "happening" not in low:
        return None
    topic = None
    m = re.search(r"\b(?:news|headlines?)\s+(?:about|on|regarding|for)\s+(.+)",
                  str(query), re.IGNORECASE)
    if not m:
        m = re.search(r"\b(?:happening|going on)\s+with\s+(.+)",
                      str(query), re.IGNORECASE)
    if not m:
        m = re.search(r"^(.+?)\s+(?:news|headlines)\b", str(query),
                      re.IGNORECASE)
    if m:
        topic = _clean_topic(m.group(1))
    key = "news:" + (topic.lower() if topic else "top")
    hit = _data_cached(key)
    if hit:
        return hit
    if topic:
        url = ("https://news.google.com/rss/search?q="
               + urllib.parse.quote(topic)
               + "&hl=en-US&gl=US&ceid=US:en")
    else:
        url = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
    xml_text = _fetch_text(url)
    if not xml_text:
        return None
    try:
        root = ET.fromstring(xml_text)
        items = root.findall("./channel/item")
    except Exception as e:
        logger.warning(f"News parse failed: {e}")
        return None
    lines = []
    seen = set()
    for item in items:
        title = (item.findtext("title") or "").strip()
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        src = (item.findtext("source") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        date = " ".join(pub.split()[:4]) if pub else ""
        line = f"- {title}"
        if src:
            line += f" ({src}" + (f", {date})" if date else ")")
        lines.append(line)
        if len(lines) >= 5:
            break
    if not lines:
        return None
    head = ("Top headlines right now (Google News, live):" if not topic
            else f"Latest news on {topic} (Google News, live):")
    return _data_store(key, ("Live news headlines",
                             head + "\n" + "\n".join(lines),
                             "https://news.google.com/"),
                       _DATA_TTL["news"])


# --- Round 4: router + detect heuristic ---------------------------------------
def _og_data_tools(query: str):
    """Round 4 router: try the live data pack for this query. On a hit,
    return results in the web_search shape; on a miss return None and
    the caller falls through to the Round 3 lookup chain. Candidates
    are the visitor's raw message first (richest signal), then the
    search query the intent layer produced (sometimes a fragment)."""
    candidates = []
    raw = _current_message.get("text") or ""
    if raw.strip():
        candidates.append(raw)
    if str(query or "").strip() and str(query) not in candidates:
        candidates.append(str(query))
    for candidate in candidates:
        for tool in (_tool_weather, _tool_sports, _tool_crypto,
                     _tool_stocks, _tool_news):
            try:
                hit = tool(candidate)
            except Exception as e:
                logger.warning(f"Data tool {tool.__name__} failed: {e}")
                hit = None
            if hit:
                label, text, source = hit
                results = [{"title": label, "body": text[:1800],
                            "href": source}]
                if source:
                    results.append({"title": label + " — source",
                                    "body": source, "href": source})
                return results
    return None


_DATA_TEAM_WORDS = (
    "seahawks", "mariners", "cardinals", "falcons", "ravens", "bills",
    "panthers", "bears", "bengals", "browns", "cowboys", "broncos",
    "lions", "packers", "texans", "colts", "jaguars", "chiefs",
    "raiders", "chargers", "rams", "dolphins", "vikings", "patriots",
    "saints", "giants", "jets", "eagles", "steelers", "49ers",
    "buccaneers", "titans", "commanders", "celtics", "nets", "hornets",
    "bulls", "cavaliers", "mavericks", "nuggets", "pistons",
    "warriors", "rockets", "pacers", "clippers", "lakers", "grizzlies",
    "heat", "bucks", "timberwolves", "pelicans", "knicks", "thunder",
    "magic", "76ers", "suns", "blazers", "kings", "spurs", "raptors",
    "jazz", "wizards", "diamondbacks", "braves", "orioles", "red sox",
    "cubs", "white sox", "reds", "guardians", "rockies", "tigers",
    "astros", "royals", "angels", "dodgers", "marlins", "brewers",
    "twins", "mets", "yankees", "athletics", "phillies", "pirates",
    "padres", "rays", "rangers", "blue jays", "nationals", "ducks",
    "bruins", "sabres", "flames", "hurricanes", "blackhawks",
    "avalanche", "blue jackets", "stars", "red wings", "oilers",
    "canadiens", "predators", "devils", "islanders", "senators",
    "flyers", "penguins", "sharks", "kraken", "blues", "lightning",
    "maple leafs", "canucks", "golden knights", "capitals", "mammoth",
    "coyotes",
)


def _message_needs_data(message: str) -> bool:
    """App-layer heuristic for the Round 4 data pack (weather, sports,
    quotes, headlines) — complements _message_needs_lookup."""
    if not message:
        return False
    low = message.lower()
    if "news" in low:
        return True
    if any(w in low for w in ("weather", "forecast", "temperature in",
                              "headlines")):
        return True
    if re.search(r"\b(nfl|nba|mlb|nhl)\b", low):
        return True
    if any(w in low for w in ("score", "scores", "standings", "who won")):
        return True
    if any(t in low for t in _DATA_TEAM_WORDS):
        return True
    if re.search(r"\$[A-Za-z]{1,5}\b", str(message)):
        return True
    if "crypto" in low:
        return True
    for alias in _CRYPTO_MAP:
        if re.search(rf"\b{re.escape(alias)}\b", low):
            return True
    if any(p in low for p in _STOCK_INDEX_MAP):
        return True
    ctx = (any(w in low for w in _STOCK_CTX) or "price" in low
           or "doing" in low or "worth" in low)
    if ctx:
        for name in _STOCK_NAME_MAP:
            if re.search(rf"\b{re.escape(name)}\b", low):
                return True
        if re.search(r"\b[A-Z]{2,5}\b", str(message)):
            return True
    return False


# --- Google account connect (Round 3, ships dark) ---------------------------
# A visitor can connect their Google account to OG ("Connect Google" in
# the slide-over menu). OAuth 2.0 the standard way: OG never sees or stores
# a password — Google itself confirms who they are and hands back tokens,
# stored per visitor (keyed by ogai_uid) alongside the memory store: in
# Postgres when OG_MEMORY_DB_URL is set, else a JSON file. v1 scopes are
# identity only (openid email profile) — enough for OG to know who's
# talking; no mail or calendar access.
# Ships DISABLED: the menu button stays hidden and the routes answer 404
# until OG_GOOGLE_ENABLED=true plus OG_GOOGLE_CLIENT_ID /
# OG_GOOGLE_CLIENT_SECRET are set (Brent creates the OAuth client in his
# own Google Cloud console — the steps are in the Round 3 report).
GOOGLE_CLIENT_ID = os.getenv("OG_GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("OG_GOOGLE_CLIENT_SECRET", "")
GOOGLE_ENABLED = (os.getenv("OG_GOOGLE_ENABLED", "false").lower() == "true"
                  and bool(GOOGLE_CLIENT_ID) and bool(GOOGLE_CLIENT_SECRET))
GOOGLE_REDIRECT_URI = os.getenv(
    "OG_GOOGLE_REDIRECT_URI",
    "https://og-ai-service.onrender.com/auth/google/callback")
GOOGLE_STORE_FILE = "google_store.json"
_google_lock = threading.Lock()


def _google_db_connect():
    """Connect to the durable DB, creating the Google tokens table."""
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_google_tokens ("
            "uid TEXT PRIMARY KEY, data JSONB)"
        )
    conn.commit()
    return conn


def _load_google_store() -> Dict:
    """Load all Google connections (durable DB when configured, otherwise
    the JSON google store)."""
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _google_db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT uid, data FROM og_google_tokens")
                    return {uid: data for uid, data in cur.fetchall()}
        except Exception as e:
            logger.warning(f"Google store DB load failed, using file: {e}")
    if os.path.exists(GOOGLE_STORE_FILE):
        try:
            with open(GOOGLE_STORE_FILE, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Could not load google store: {e}")
    return {}


def _save_google_store(store: Dict):
    """Save all Google connections (durable DB when configured, otherwise
    the JSON google store)."""
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _google_db_connect() as conn:
                with conn.cursor() as cur:
                    for uid, data in store.items():
                        cur.execute(
                            "INSERT INTO og_google_tokens (uid, data) "
                            "VALUES (%s, %s) ON CONFLICT (uid) DO UPDATE "
                            "SET data = EXCLUDED.data",
                            (uid, _Jsonb(data)),
                        )
                    cur.execute("SELECT uid FROM og_google_tokens")
                    existing = {row[0] for row in cur.fetchall()}
                    for stale in existing - set(store.keys()):
                        cur.execute(
                            "DELETE FROM og_google_tokens WHERE uid = %s",
                            (stale,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Google store DB save failed, using file: {e}")
    try:
        with open(GOOGLE_STORE_FILE, 'w') as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Could not save google store: {e}")


def _google_connection(uid: str) -> Optional[Dict]:
    """This visitor's stored Google connection (profile + tokens), if any."""
    if not uid:
        return None
    with _google_lock:
        store = _load_google_store()
    entry = store.get(uid)
    return entry if isinstance(entry, dict) else None


def _google_state_for(uid: str) -> str:
    """Signed OAuth state tying the connect flow to one visitor uid."""
    sig = hmac.new(GOOGLE_CLIENT_SECRET.encode(),
                   f"og-google:{uid}".encode(), hashlib.sha256).hexdigest()
    return f"{uid}.{sig}"


def _google_uid_from_state(state: str) -> Optional[str]:
    """Recover the visitor uid from a state value we signed, else None."""
    try:
        uid, _, sig = str(state).partition(".")
        if not uid or not sig:
            return None
        expected = hmac.new(GOOGLE_CLIENT_SECRET.encode(),
                            f"og-google:{uid}".encode(),
                            hashlib.sha256).hexdigest()
        return uid if hmac.compare_digest(expected, sig) else None
    except Exception:
        return None


# --- Per-visitor memory -------------------------------------------------------
# OG remembers each visitor separately: conversation history is keyed by the
# `ogai_uid` cookie and persisted to a JSON store, so a returning visitor
# picks up right where they left off. (Before this, every visitor shared one
# global conversation — strangers' messages bled into each other's context,
# anyone could read the shared history at /history, and one person's /reset
# wiped it for everybody.) The store lives next to the usage store and has
# the same durability: it survives restarts, resets on a from-scratch rebuild.
MEMORY_STORE_FILE = "memory_store.json"
MEMORY_MAX_MESSAGES = 40    # most recent messages kept per visitor
MEMORY_MAX_VISITORS = 300   # least-recently-active threads pruned beyond this
_memory_lock = threading.Lock()



# Durable backend (opt-in): when OG_MEMORY_DB_URL points at a Postgres
# database (e.g. a free Neon or Supabase instance), visitor threads live
# there instead of the JSON file, so memory survives full rebuilds and
# redeploys. While the variable is unset — or the database is unreachable —
# the JSON file store is used, exactly as before.
MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg not installed — DB backend simply unavailable
    psycopg = None
    _Jsonb = None


def _memory_db_connect():
    """Connect to the durable memory DB, creating the table if needed."""
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_visitor_memory ("
            "uid TEXT PRIMARY KEY, updated TEXT, history JSONB)"
        )
    conn.commit()
    return conn


def _load_memory_store_db():
    """Load all visitor threads from Postgres; None on any failure."""
    if psycopg is None:
        return None
    try:
        with _memory_db_connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT uid, updated, history FROM og_visitor_memory")
                rows = cur.fetchall()
        return {uid: {"updated": updated, "history": history}
                for uid, updated, history in rows}
    except Exception as e:
        logger.warning(f"Durable memory load failed, using file store: {e}")
        return None


def _save_memory_store_db(store: Dict) -> bool:
    """Persist all visitor threads to Postgres; False on any failure."""
    if psycopg is None:
        return False
    try:
        with _memory_db_connect() as conn:
            with conn.cursor() as cur:
                for uid, entry in store.items():
                    if not isinstance(entry, dict):
                        continue
                    cur.execute(
                        "INSERT INTO og_visitor_memory (uid, updated, history) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid) DO UPDATE SET "
                        "updated = EXCLUDED.updated, history = EXCLUDED.history",
                        (uid, entry.get("updated", ""),
                         _Jsonb(entry.get("history", []))),
                    )
                cur.execute("SELECT uid FROM og_visitor_memory")
                existing = {row[0] for row in cur.fetchall()}
                for stale in existing - set(store.keys()):
                    cur.execute(
                        "DELETE FROM og_visitor_memory WHERE uid = %s", (stale,))
            conn.commit()
        return True
    except Exception as e:
        logger.warning(f"Durable memory save failed, using file store: {e}")
        return False


def _reset_memory_store():
    """Start the memory store empty (called when a fresh agent is created).

    A new agent instance is a new OG: it should not inherit a previous
    instance's visitor threads while its own in-memory state starts blank.
    This gives the store the same lifecycle as the usage counters and stats
    in usage_store.json — they live for the service's life and reset on a
    from-scratch rebuild.

    Exception: with the durable Postgres backend active (OG_MEMORY_DB_URL
    set) the store is deliberately NOT reset — wiping it on agent creation
    would defeat the entire point of durable memory.
    """
    if MEMORY_DB_URL:
        logger.info("Durable memory backend active — memory store not reset")
        return
    with _memory_lock:
        _save_memory_store({})


def _load_memory_store() -> Dict:
    """Load per-visitor conversation threads (durable DB when configured,
    otherwise the JSON memory store)."""
    if MEMORY_DB_URL:
        data = _load_memory_store_db()
        if data is not None:
            return data
    return _load_memory_store_file()


def _load_memory_store_file() -> Dict:
    """Load per-visitor conversation threads from the JSON memory store."""
    if os.path.exists(MEMORY_STORE_FILE):
        try:
            with open(MEMORY_STORE_FILE, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Could not load memory store: {e}")
    return {}


def _save_memory_store(store: Dict):
    """Save per-visitor conversation threads (durable DB when configured,
    otherwise the JSON memory store)."""
    if MEMORY_DB_URL and _save_memory_store_db(store):
        return
    _save_memory_store_file(store)


def _save_memory_store_file(store: Dict):
    """Save per-visitor conversation threads to the JSON memory store."""
    try:
        with open(MEMORY_STORE_FILE, 'w') as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Could not save memory store: {e}")


# The *_locked helpers assume the caller holds _memory_lock: /chat holds it
# across swap-in -> process -> save-back so one visitor's thread can never be
# clobbered by another request touching the shared agent instance.
def _load_visitor_history_locked(uid: str) -> List[Dict]:
    """This visitor's stored conversation thread (most recent last)."""
    entry = _load_memory_store().get(uid)
    if isinstance(entry, dict) and isinstance(entry.get("history"), list):
        return list(entry["history"])
    return []


def _save_visitor_history_locked(uid: str, history: List[Dict]):
    """Persist this visitor's thread, trimmed, pruning the stalest threads."""
    store = _load_memory_store()
    store[uid] = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "history": list(history)[-MEMORY_MAX_MESSAGES:],
    }
    if len(store) > MEMORY_MAX_VISITORS:
        ordered = sorted(
            store.items(),
            key=lambda kv: kv[1].get("updated", "") if isinstance(kv[1], dict) else "",
        )
        for old_uid, _ in ordered[: len(store) - MEMORY_MAX_VISITORS]:
            store.pop(old_uid, None)
    _save_memory_store(store)


def _clear_visitor_history(uid: str):
    """Forget one visitor's thread (their /reset or /clear, nobody else's)."""
    with _memory_lock:
        store = _load_memory_store()
        if uid in store:
            del store[uid]
            _save_memory_store(store)


# --- Business stats (for the owner) ------------------------------------------
# OG keeps his own scorecard: chat messages, visitors who hit the free cap,
# Pro checkout clicks, and post-payment landings. Counters live in the same
# JSON store as usage counts (key "__stats__"). The owner reads them at
# GET /stats?key=<OG_STATS_TOKEN>.
STATS_TOKEN = os.getenv("OG_STATS_TOKEN", "")
STATS_KEY = "__stats__"
_STAT_FIELDS = ("messages", "cap_hits", "pro_clicks", "pro_success", "tokens")


def _bump_stat(field: str, amount: int = 1):
    """Increment a business counter, all-time and for today (UTC)."""
    if field not in _STAT_FIELDS:
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _usage_lock:
        store = _load_usage_store()
        stats = store.get(STATS_KEY)
        if not isinstance(stats, dict):
            stats = {"since": today, "days": {}}
            for f in _STAT_FIELDS:
                stats[f] = 0
        stats[field] = int(stats.get(field, 0)) + amount
        day = stats.setdefault("days", {}).setdefault(
            today, {f: 0 for f in _STAT_FIELDS})
        day[field] = int(day.get(field, 0)) + amount
        store[STATS_KEY] = stats
        _save_usage_store(store)


def _visitor_counts(store: Dict):
    """(total distinct visitors, visitors active today) from usage entries."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = active_today = 0
    for key, entry in store.items():
        if key.startswith("__") or key.startswith("tts:") or not isinstance(entry, dict):
            continue
        total += 1
        if entry.get("date") == today:
            active_today += 1
    return total, active_today



# Initialize FastAPI app
app = FastAPI(
    title="OG-AI Agent API",
    description="A conversational AI agent REST API",
    version="1.0.0"
)

# Add CORS middleware to allow cross-origin requests
# NOTE: For production, set the ALLOWED_ORIGINS environment variable to specific allowed origins
# Example: ALLOWED_ORIGINS='["https://yourdomain.com"]'
allowed_origins_env = os.getenv("ALLOWED_ORIGINS")
if allowed_origins_env:
    try:
        allowed_origins = json.loads(allowed_origins_env)
        if not isinstance(allowed_origins, list):
            raise ValueError("ALLOWED_ORIGINS must be a JSON array")
    except Exception as e:
        logger.warning(f"Invalid ALLOWED_ORIGINS environment variable: {e}. Falling back to ['*'].")
        allowed_origins = ["*"]
else:
    allowed_origins = ["*"]  # Default for development/demo

# Security: Only enable credentials when not using wildcard origins
allow_credentials = allowed_origins != ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files directory
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Global agent instance
# NOTE: The agent object itself is shared, but conversation history is NOT:
# /chat swaps in each visitor's own persisted thread (see "Per-visitor
# memory" above) under _memory_lock for the duration of their request.
agent = None


def get_agent() -> AIAgent:
    """
    Get or create the global agent instance.
    
    Note: This returns a shared instance. For multi-user support, consider
    implementing session-based agent management.
    """
    global agent
    if agent is None:
        # Load config if exists
        config = {}
        if os.path.exists('config.json'):
            try:
                with open('config.json', 'r') as f:
                    config = json.load(f)
            except Exception as e:
                logger.warning(f"Could not load config.json: {e}")
        
        agent_name = config.get('agent_name', 'OG-AI')
        agent = AIAgent(name=agent_name, config=config)
        _install_lookup_tools(agent)
        _reset_memory_store()

    return agent


# Pydantic models for request/response
class ChatRequest(BaseModel):
    message: str
    speak_response: bool = False
    # When true, /chat answers as Server-Sent Events (reply text streamed in
    # chunks, then a final done event). Omit/false = the classic JSON reply.
    stream: bool = False
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Hello! How are you?",
                "speak_response": False
            }
        }
    )


class ChatResponse(BaseModel):
    response: str
    agent_name: str
    timestamp: str
    # Only set when a free visitor hits the daily cap; omitted otherwise.
    upgrade_url: Optional[str] = None
    # Free tokens the visitor has left today (free tier only; omitted for Pro).
    free_tokens_left: Optional[int] = None
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "response": "Hello! I'm OG-AI, your AI assistant. How can I help you today?",
                "agent_name": "OG-AI",
                "timestamp": "2025-11-05T20:00:00.000000"
            }
        }
    )


class HistoryResponse(BaseModel):
    conversation: List[Dict]
    history: List[Dict]  # Backward compatibility with Flask API
    message_count: int
    # Free tokens the visitor has left today (omitted/null for Pro).
    free_tokens_left: Optional[int] = None
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "conversation": [
                    {
                        "role": "user",
                        "content": "Hello!",
                        "timestamp": "2025-11-05T20:00:00.000000"
                    }
                ],
                "history": [
                    {
                        "role": "user",
                        "content": "Hello!",
                        "timestamp": "2025-11-05T20:00:00.000000"
                    }
                ],
                "message_count": 1
            }
        }
    )


class StatusResponse(BaseModel):
    status: str
    agent_name: str
    message: str
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "healthy",
                "agent_name": "OG-AI",
                "message": "Service is running"
            }
        }
    )


@app.get("/", response_class=FileResponse)
async def root():
    """
    Serve the epic frontend HTML interface.
    """
    return FileResponse("index_epic.html")


@app.get("/classic", response_class=FileResponse)
async def classic_ui():
    """
    Serve the classic frontend HTML interface.
    """
    return FileResponse("frontend.html")


@app.get("/qr", response_class=FileResponse)
async def qr_code():
    """
    Serve the QR code page for mobile access.
    """
    return FileResponse("qr.html")


@app.get("/api", response_model=StatusResponse)
async def api_info():
    """
    API information endpoint.
    """
    agent_instance = get_agent()
    return {
        "status": "healthy",
        "agent_name": agent_instance.name,
        "message": "OG-AI Agent API is running. Visit /docs for API documentation."
    }


@app.get("/health", response_model=StatusResponse)
async def health_check():
    """
    Health check endpoint for monitoring service status.
    """
    agent_instance = get_agent()
    return {
        "status": "healthy",
        "agent_name": agent_instance.name,
        "message": "Service is running"
    }



# --- Streaming chat (true token streaming) ------------------------------------
# /chat with {"stream": true} answers as Server-Sent Events: an `event: chunk`
# for each token piece the model API produces, as it arrives, then a final
# `event: done` whose payload matches the classic JSON response (plus
# `event: error` if generation fails). The classic non-streaming response is
# completely unchanged. A finished reply is never sliced up to fake streaming:
# the few paths that are not model token streams (the code-generation tool,
# the local pattern fallback) deliver their result whole, in a single chunk.


def _generate_reply_streaming(agent_instance, message: str,
                              speak_response: bool, sink):
    """
    Streaming twin of the agent's process_message(): the same intent
    detection, the same tools, the same persona prompt, the same model and
    parameters — the agent's own logic decides WHAT OG says; this only
    changes HOW the reply is delivered, pushing each token piece to
    sink("chunk", text) the moment the model API produces it. Agent files
    are never modified.

    Token streaming is implemented for the OpenAI path (the live provider)
    and the Anthropic path, in both cases building exactly the request the
    agent's own response methods build. If a token stream fails before any
    text is produced, it falls back to the agent's own full-text generation
    (which carries its own in-persona fallback inside), delivered whole.

    Returns (response_text, tokens_used): tokens_used is the API-reported
    total (prompt + completion) when the provider reports usage, otherwise
    a tokenizer estimate of the whole call.
    """
    agent = agent_instance
    agent.add_message('user', message)

    learned_hint = ""
    if getattr(agent, 'learning_system', None):
        learned_hint = agent.learning_system.get_learned_response(message)

    intent = agent.detect_intent(message)

    context = learned_hint + "\n" if learned_hint else ""

    def _finish(response: str, speech_text: str = None) -> str:
        agent.add_message('assistant', response)
        if getattr(agent, 'learning_system', None):
            agent.learning_system.learn_from_conversation(
                message, response, was_helpful=True)
        should_speak = speak_response if speak_response is not None \
            else getattr(agent, 'voice_enabled', False)
        if should_speak and getattr(agent, 'voice', None):
            agent.voice.speak(agent._prepare_for_speech(
                speech_text if speech_text is not None else response))
        return response

    # Handle CODE GENERATION first (same priority as process_message). The
    # code generator is a tool call with an atomic result — delivered whole.
    if intent['needs_code_generation'] and getattr(agent, 'code_generator', None):
        code, explanation = agent.code_generator.generate_code_from_request(message)
        if code:
            response = f"{explanation}\n\n```python\n{code}\n```"
            sink("chunk", response)
            finished = _finish(response, speech_text=explanation)
            return finished, _estimate_call_tokens(agent, finished)

    if intent['needs_web_search'] and intent['search_query']:
        search_results = agent.web_search(intent['search_query'])
        if search_results:
            context += "\n\n[WEB SEARCH RESULTS]:\n"
            for i, result in enumerate(search_results[:3], 1):
                if 'error' not in result:
                    context += f"{i}. {result.get('title', 'N/A')}: {result.get('body', 'N/A')}\n"
                else:
                    context += f"Search error: {result['error']}\n"

    if intent['needs_wikipedia'] and intent['search_query']:
        wiki_result = agent.wikipedia_search(intent['search_query'])
        context += f"\n\n[WIKIPEDIA]:\n{wiki_result}\n"

    if intent['needs_code_execution'] and intent['code']:
        exec_result = agent.execute_code(intent['code'], intent['language'])
        context += f"\n\n[CODE EXECUTION RESULT]:\n{exec_result}\n"

    if intent['needs_url_scrape'] and intent['url']:
        scrape_result = agent.scrape_webpage(intent['url'])
        context += f"\n\n[WEBPAGE CONTENT]:\n{scrape_result}\n"

    provider = getattr(agent, 'ai_provider', None)

    # --- OpenAI: true token streaming, the request built exactly like the
    # agent's own _openai_response (same prompt, history window, context,
    # model, temperature and token cap) — only stream=True is added.
    if provider == "openai" and getattr(agent, 'openai_client', None):
        parts = []
        try:
            messages = [{"role": "system", "content": agent.system_prompt}]
            for msg in agent.conversation_history[-10:]:
                if msg['role'] in ['user', 'assistant']:
                    messages.append({"role": msg['role'], "content": msg['content']})
            if context:
                messages.append({"role": "system",
                                 "content": f"Additional context:\n{context}"})
            stream = agent.openai_client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                messages=messages,
                temperature=0.9,
                max_tokens=1000,
                stream=True,
                stream_options={"include_usage": True}
            )
            usage_total = None
            for event in stream:
                usage = getattr(event, "usage", None)
                if usage is not None:
                    try:
                        usage_total = int(usage.total_tokens)
                    except Exception:
                        pass
                try:
                    delta = event.choices[0].delta.content or ""
                except Exception:
                    delta = ""
                if delta:
                    parts.append(delta)
                    sink("chunk", delta)
            if parts:
                finished = _finish("".join(parts))
                return finished, (usage_total if usage_total is not None
                                  else _estimate_call_tokens(agent, finished))
            logger.warning("Token stream produced no text; using full-text generation")
        except Exception as e:
            if parts:
                finished = _finish("".join(parts))
                return finished, _estimate_call_tokens(agent, finished)
            logger.warning(f"Token streaming failed, using full-text: {e}")

    # --- Anthropic: true token streaming, the request built exactly like
    # the agent's own _anthropic_response — only streamed.
    elif provider == "anthropic" and getattr(agent, 'anthropic_client', None):
        parts = []
        try:
            messages = []
            for msg in agent.conversation_history[-10:]:
                if msg['role'] in ['user', 'assistant']:
                    messages.append({"role": msg['role'], "content": msg['content']})
            if context and messages:
                messages[-1]['content'] += f"\n\nAdditional context:\n{context}"
            with agent.anthropic_client.messages.stream(
                model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
                max_tokens=1000,
                system=agent.system_prompt,
                messages=messages
            ) as stream:
                for text in stream.text_stream:
                    if text:
                        parts.append(text)
                        sink("chunk", text)
                usage_total = None
                try:
                    final_message = stream.get_final_message()
                    usage = getattr(final_message, "usage", None)
                    if usage is not None:
                        usage_total = (int(usage.input_tokens)
                                       + int(usage.output_tokens))
                except Exception:
                    pass
            if parts:
                finished = _finish("".join(parts))
                return finished, (usage_total if usage_total is not None
                                  else _estimate_call_tokens(agent, finished))
            logger.warning("Token stream produced no text; using full-text generation")
        except Exception as e:
            if parts:
                finished = _finish("".join(parts))
                return finished, _estimate_call_tokens(agent, finished)
            logger.warning(f"Token streaming failed, using full-text: {e}")

    # Every remaining path (Ollama, the local pattern fallback, a provider
    # whose stream failed above) produces its reply inside the agent, whole.
    # It is delivered as ONE chunk — never sliced to imitate streaming.
    response = agent._generate_ai_response(message, context)
    sink("chunk", response)
    finished = _finish(response)
    return finished, _estimate_call_tokens(agent, finished)


def _stream_chat_worker(agent_instance, uid: str, message: str,
                        speak_response: bool, sink, meter: bool = True):
    """
    Worker-thread body for a streaming /chat request. Mirrors the classic
    /chat bookkeeping exactly: this visitor's own thread is swapped into the
    shared agent under the memory lock, the message stat is bumped, and the
    thread is saved back in a finally block — so if the visitor's browser
    disconnects mid-stream, generation still finishes server-side and the
    conversation is still remembered (the non-streaming fallback).
    """
    _memory_lock.acquire()
    agent_instance.conversation_history = _load_visitor_history_locked(uid)
    _current_uid["uid"] = uid
    try:
        has_learning = hasattr(agent_instance, 'learning_system') \
            and agent_instance.learning_system is not None
        if hasattr(agent_instance, 'detect_intent'):
            response, tokens_used = _generate_reply_streaming(
                agent_instance, message, speak_response, sink)
        else:
            # Agent without streaming internals: its classic full-text reply,
            # delivered whole in a single chunk (never sliced).
            try:
                response = agent_instance.process_message(
                    message, speak_response=speak_response)
            except TypeError:
                response = agent_instance.process_message(message)
            sink("chunk", response)
            tokens_used = _estimate_call_tokens(agent_instance, response)

        # Bill the exchange for any web lookup it ran, too.
        tokens_used += _drain_lookup_tokens()
        history = agent_instance.get_conversation_history()
        latest_msg = history[-1] if history else None
        result = {
            "response": response,
            "agent_name": agent_instance.name,
            "timestamp": latest_msg['timestamp'] if latest_msg else ""
        }
        _bump_stat("messages")
        # Token metering (free visitors only; Pro is unmetered).
        if meter:
            _record_chat_usage(uid, tokens_used)
            _bump_stat("tokens", tokens_used)
            result["free_tokens_left"] = _free_tokens_remaining(uid)
        else:
            result["free_tokens_left"] = None
        if has_learning:
            report = agent_instance.learning_system.get_intelligence_report()
            result["intelligence"] = report.get("intelligence_level", 1.0)
        sink("done", result)
    except Exception as e:
        logger.error(f"Error processing streamed message: {str(e)}")
        detail = f"An error occurred while processing your message: {str(e)}" \
            if DEVELOPMENT_MODE else "An error occurred while processing your message"
        sink("error", {"detail": detail})
    finally:
        _current_uid["uid"] = ""
        try:
            _save_visitor_history_locked(
                uid, list(getattr(agent_instance, "conversation_history", []) or []))
        finally:
            _memory_lock.release()


def _sse_streaming_response(event_gen, http_response, entitled: bool):
    """Build the SSE response, carrying over cookies the handler already set
    (e.g. a fresh ogai_uid) plus the Pro cookie for entitled visitors."""
    resp = StreamingResponse(
        event_gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
    for name, value in http_response.raw_headers:
        if name.lower() == b"set-cookie":
            resp.raw_headers.append((name, value))
    if entitled:
        resp.set_cookie(
            "ogai_pro", PRO_TOKEN,
            max_age=COOKIE_MAX_AGE, path="/", httponly=True, samesite="lax"
        )
    return resp


def _stream_chat_response(agent_instance, uid: str, request: ChatRequest,
                          http_response: Response, entitled: bool,
                          meter: bool = True):
    """Start the worker thread and return the SSE response for /chat."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def sink(kind, payload):
        try:
            loop.call_soon_threadsafe(queue.put_nowait, (kind, payload))
        except RuntimeError:
            pass  # client/event loop gone — worker still finishes and saves

    worker = threading.Thread(
        target=_stream_chat_worker,
        args=(agent_instance, uid, request.message.strip(),
              request.speak_response, sink),
        kwargs={"meter": meter},
        daemon=True,
    )
    worker.start()

    async def event_gen():
        while True:
            kind, payload = await queue.get()
            if kind == "chunk":
                yield f"event: chunk\ndata: {json.dumps({'text': payload})}\n\n"
            elif kind == "done":
                yield f"event: done\ndata: {json.dumps(payload)}\n\n"
                return
            else:
                yield f"event: error\ndata: {json.dumps(payload)}\n\n"
                return

    return _sse_streaming_response(event_gen(), http_response, entitled)


@app.post("/chat", response_model=ChatResponse, response_model_exclude_none=True)
async def chat(request: ChatRequest, raw_request: Request, http_response: Response):
    """
    Send a message to the AI agent and receive a response.

    Free visitors get FREE_DAILY_TOKENS tokens of model usage per UTC day
    (tracked by an `ogai_uid` cookie); Pro visitors (valid `ogai_pro` cookie,
    or a Stripe-confirmed entitlement while v2 is enabled) are unmetered.
    A capped visitor still gets HTTP 200 with an in-persona reply pointing
    at the upgrade URL.

    With {"stream": true} the reply is delivered as Server-Sent Events —
    the model's tokens streamed as they are produced, then a done event
    carrying this same payload; cap counting and the per-visitor memory
    write are identical either way.

    Args:
        request: ChatRequest containing the user's message and optional voice setting

    Returns:
        ChatResponse with the agent's reply
    """
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    agent_instance = get_agent()

    # Visitor identity: hand out an `ogai_uid` cookie to new visitors.
    uid = raw_request.cookies.get("ogai_uid")
    if not uid:
        uid = uuid.uuid4().hex
        http_response.set_cookie(
            "ogai_uid", uid,
            max_age=COOKIE_MAX_AGE, path="/", httponly=True, samesite="lax"
        )

    # Freemium gate: Pro cookie holders skip the daily cap entirely. With
    # webhook entitlement (v2) enabled, a Stripe-confirmed buyer is Pro even
    # before the cookie lands — and is handed the cookie on this response.
    is_pro = raw_request.cookies.get("ogai_pro") == PRO_TOKEN
    entitled = False
    if not is_pro and _uid_is_entitled(uid):
        is_pro = True
        entitled = True

    if not is_pro:
        if not _has_free_tokens(uid):
            _bump_stat("cap_hits")
            cap_payload = {
                "response": (
                    f"Yo, real talk — you're outta free tokens for today "
                    f"({FREE_DAILY_TOKENS:,} a day on the free plan), and the OG don't work for free forever. "
                    f"Go Pro for unlimited: {PRO_UPGRADE_URL} — or slide back tomorrow when your freebies reset."
                ),
                "agent_name": agent_instance.name,
                "timestamp": datetime.now().isoformat(),
                "upgrade_url": PRO_UPGRADE_URL,
                "free_tokens_left": 0
            }
            if request.stream:
                async def cap_events():
                    yield f"event: chunk\ndata: {json.dumps({'text': cap_payload['response']})}\n\n"
                    yield f"event: done\ndata: {json.dumps(cap_payload)}\n\n"
                return _sse_streaming_response(cap_events(), http_response, entitled)
            return cap_payload

    if request.stream:
        return _stream_chat_response(
            agent_instance, uid, request, http_response, entitled,
            meter=not is_pro)

    if entitled:
        http_response.set_cookie(
            "ogai_pro", PRO_TOKEN,
            max_age=COOKIE_MAX_AGE, path="/", httponly=True, samesite="lax"
        )

    # Per-visitor memory: swap this visitor's own thread into the shared
    # agent and hold the memory lock until it is saved back below, so two
    # visitors chatting at once can never interleave each other's history.
    _memory_lock.acquire()
    agent_instance.conversation_history = _load_visitor_history_locked(uid)
    _current_uid["uid"] = uid
    try:
        # Check if agent has voice/learning capabilities
        has_voice = hasattr(agent_instance, 'voice') and agent_instance.voice is not None
        has_learning = hasattr(agent_instance, 'learning_system') and agent_instance.learning_system is not None
        
        # Process message with voice option if available
        if has_voice:
            response = agent_instance.process_message(request.message.strip(), speak_response=request.speak_response)
        else:
            response = agent_instance.process_message(request.message.strip())
        
        # Get the latest assistant message from history
        history = agent_instance.get_conversation_history()
        latest_msg = history[-1] if history else None
        
        result = {
            "response": response,
            "agent_name": agent_instance.name,
            "timestamp": latest_msg['timestamp'] if latest_msg else ""
        }
        
        _bump_stat("messages")

        # Token metering (free visitors only): the classic path gets no
        # usage back from the agent, so the call is counted by estimate.
        if not is_pro:
            _tokens_used = _estimate_call_tokens(agent_instance, response) \
                + _drain_lookup_tokens()
            _record_chat_usage(uid, _tokens_used)
            _bump_stat("tokens", _tokens_used)
            result["free_tokens_left"] = _free_tokens_remaining(uid)

        # Add intelligence info if learning is enabled
        if has_learning:
            report = agent_instance.learning_system.get_intelligence_report()
            result["intelligence"] = report.get("intelligence_level", 1.0)
        
        return result
    except Exception as e:
        logger.error(f"Error processing message: {str(e)}")
        detail = f"An error occurred while processing your message: {str(e)}" if DEVELOPMENT_MODE else "An error occurred while processing your message"
        raise HTTPException(status_code=500, detail=detail)
    finally:
        # Save this visitor's thread back (even on error, so what they said
        # is remembered), then hand the shared agent back.
        _current_uid["uid"] = ""
        try:
            _save_visitor_history_locked(
                uid, list(getattr(agent_instance, "conversation_history", []) or []))
        finally:
            _memory_lock.release()


@app.post("/tts")
async def text_to_speech(raw_request: Request):
    """
    Turn a chat reply into realistic spoken audio (OpenAI TTS).

    Needs OPENAI_API_KEY in the environment; without it, answers 503
    and the web page uses the visitor's device voice instead.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="AI voice not configured")
    try:
        body = await raw_request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body")
    text = str(body.get("text", "")).strip()[:TTS_MAX_CHARS]
    if not text:
        raise HTTPException(status_code=400, detail="No text to speak")
    uid = raw_request.cookies.get("ogai_uid") or "anon"
    if not _consume_tts_call(uid):
        raise HTTPException(status_code=429, detail="Daily voice limit reached")
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": "tts-1", "voice": "onyx", "input": text},
            )
    except Exception as e:
        logger.warning(f"TTS request failed: {e}")
        raise HTTPException(status_code=502, detail="Voice service unavailable")
    if r.status_code != 200:
        logger.warning(f"TTS upstream status: {r.status_code}")
        raise HTTPException(status_code=502, detail="Voice service error")
    return Response(content=r.content, media_type="audio/mpeg")


@app.get("/pro")
async def pro_upgrade(raw_request: Request):
    """
    Send visitors to the Pro checkout.

    The destination is the Stripe payment link configured via OG_PRO_LINK;
    Stripe should be set to redirect buyers to /pro/success after checkout.
    While webhook entitlement (v2) is enabled, the visitor's ogai_uid rides
    along as client_reference_id so the Stripe webhook can grant the
    entitlement to the right visitor.
    """
    _bump_stat("pro_clicks")
    url = PRO_UPGRADE_URL
    if WEBHOOK_ENABLED:
        uid = raw_request.cookies.get("ogai_uid")
        if uid and url.startswith("http"):
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}client_reference_id={uid}"
    return RedirectResponse(url=url, status_code=302)


@app.get("/pro/success")
async def pro_success(raw_request: Request):
    """
    Pro unlock landing page.

    v1 (default): the Stripe payment link redirects buyers here after
    checkout and we just set the `ogai_pro` cookie, which lifts the daily
    cap. Anyone who reaches this URL gets Pro.

    v2 (OG_WEBHOOK_ENABLED=true): the cookie is set only once the Stripe
    webhook has confirmed this visitor's payment. Until then this page asks
    them to give it a moment — and as soon as the webhook lands, /chat
    honors the entitlement directly, cookie or not.
    """
    if WEBHOOK_ENABLED:
        uid = raw_request.cookies.get("ogai_uid")
        if not uid or not _uid_is_entitled(uid):
            return HTMLResponse(content="""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OG AI — Confirming your Pro</title>
<style>
  body { background:#0a0a0a; color:#f2f2f2; font-family: Arial, sans-serif; margin:0; padding:32px 20px; text-align:center; }
  h1 { color:#ffc107; letter-spacing:1px; }
  p { color:#ccc; line-height:1.6; max-width:520px; margin:12px auto; }
  a.btn { display:inline-block; margin-top:18px; padding:12px 30px; border:2px solid #ffc107; border-radius:999px; color:#ffc107; text-decoration:none; font-weight:bold; background:rgba(255,193,7,0.08); }
</style></head><body>
<h1>💰 Almost there…</h1>
<p>If you just paid, give Stripe a few seconds to confirm it with OG —
then head back and chat, Pro will already be on.</p>
<p>If you didn't finish paying, no charge was made and nothing is unlocked yet.</p>
<a class="btn" href="/">← Back to OG</a>
</body></html>""", status_code=200)
    _bump_stat("pro_success")
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        "ogai_pro", PRO_TOKEN,
        max_age=COOKIE_MAX_AGE, path="/", httponly=True, samesite="lax"
    )
    return response


@app.post("/stripe/webhook")
async def stripe_webhook(raw_request: Request):
    """
    Stripe webhook: grant a Pro entitlement on checkout.session.completed.

    Inert unless OG_WEBHOOK_ENABLED=true and STRIPE_WEBHOOK_SECRET is set —
    while disabled it answers 404 and /pro/success keeps the v1 behavior.
    The buyer is identified by client_reference_id (the visitor's ogai_uid,
    added by /pro while v2 is enabled); the event signature is verified
    against STRIPE_WEBHOOK_SECRET before anything is granted.
    """
    if not WEBHOOK_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Webhook not configured")
    payload = await raw_request.body()
    signature = raw_request.headers.get("stripe-signature", "")
    if not _verify_stripe_signature(payload, signature, STRIPE_WEBHOOK_SECRET):
        raise HTTPException(status_code=400, detail="Invalid signature")
    try:
        event = json.loads(payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payload")
    if event.get("type") == "checkout.session.completed":
        session = (event.get("data") or {}).get("object") or {}
        uid = session.get("client_reference_id") or ""
        email = ((session.get("customer_details") or {}).get("email")
                 or session.get("customer_email") or "")
        if uid:
            _grant_entitlement(uid, session.get("id", ""), email)
            logger.info("Pro entitlement granted via Stripe webhook")
        else:
            logger.warning(
                "Stripe checkout completed without client_reference_id; "
                "no entitlement granted")
    return {"received": True}


@app.get("/stats", response_class=HTMLResponse)
async def stats_page(key: str = ""):
    """
    Owner-only scorecard: visitors, messages, cap hits, Pro clicks and
    post-payment landings — today and since counting started. Locked
    unless OG_STATS_TOKEN is set and passed as ?key=.
    """
    if not STATS_TOKEN or key != STATS_TOKEN:
        raise HTTPException(status_code=401, detail="Owner key required")
    with _usage_lock:
        store = _load_usage_store()
    stats = store.get(STATS_KEY)
    if not isinstance(stats, dict):
        stats = {"since": "today", "days": {}}
    total_visitors, today_visitors = _visitor_counts(store)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    day = (stats.get("days") or {}).get(today, {})

    def n(d, f):
        return int(d.get(f, 0) or 0)

    def card(label, value, sub=""):
        return (f'<div class="card"><div class="num">{value}</div>'
                f'<div class="lbl">{label}</div><div class="sub">{sub}</div></div>')

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OG AI — Owner Stats</title>
<style>
  body {{ background:#0a0a0a; color:#f2f2f2; font-family: Arial, sans-serif; margin:0; padding:24px; }}
  h1 {{ color:#ffc107; margin:0 0 4px; font-size:1.6em; letter-spacing:1px; }}
  h2 {{ color:#ffc107; margin:26px 0 10px; font-size:1.05em; text-transform:uppercase; letter-spacing:2px; }}
  .when {{ color:#999; margin-bottom:8px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; }}
  .card {{ background:#161616; border:1px solid #2c2c2c; border-left:4px solid #ffc107; border-radius:10px; padding:14px; }}
  .num {{ font-size:2em; font-weight:bold; color:#fff; }}
  .lbl {{ color:#ffc107; font-size:.85em; text-transform:uppercase; letter-spacing:1px; margin-top:2px; }}
  .sub {{ color:#888; font-size:.8em; min-height:1em; }}
  .note {{ color:#888; font-size:.85em; margin-top:26px; line-height:1.5; }}
</style></head><body>
<h1>💰 OG AI — The Scorecard</h1>
<div class="when">Today (UTC): {today} &nbsp;•&nbsp; Counting since: {stats.get("since", "today")}</div>
<h2>Today</h2>
<div class="grid">
  {card("Visitors", today_visitors)}
  {card("Messages answered", n(day, "messages"))}
  {card("Free tokens used", n(day, "tokens"), "metered today")}
  {card("Hit the free cap", n(day, "cap_hits"), "ran out of freebies")}
  {card("Pro clicks", n(day, "pro_clicks"), "went to checkout")}
  {card("Pro signups", n(day, "pro_success"), "landed after payment")}
</div>
<h2>All time</h2>
<div class="grid">
  {card("Visitors", total_visitors)}
  {card("Messages answered", n(stats, "messages"))}
  {card("Free tokens used", n(stats, "tokens"), "metered, free tier")}
  {card("Hit the free cap", n(stats, "cap_hits"))}
  {card("Pro clicks", n(stats, "pro_clicks"))}
  {card("Pro signups", n(stats, "pro_success"))}
</div>
<p class="note">Real talk on the numbers: "Pro signups" counts people who reached the
post-payment page — your Stripe dashboard is the money truth. These counters live on
the server and reset if the service gets rebuilt from scratch.</p>
</body></html>"""
    return HTMLResponse(content=html)


@app.get("/history", response_model=HistoryResponse)
async def get_history(raw_request: Request):
    """
    Get the requesting visitor's own conversation history.

    Keyed by the `ogai_uid` cookie — a visitor only ever sees their own
    thread, never anyone else's. Visitors without a cookie (they have not
    chatted yet) get an empty history.
    """
    get_agent()  # a fresh agent instance starts with a fresh memory store
    try:
        uid = raw_request.cookies.get("ogai_uid")
        history: List[Dict] = []
        if uid:
            with _memory_lock:
                history = _load_visitor_history_locked(uid)
        if raw_request.cookies.get("ogai_pro") == PRO_TOKEN or _uid_is_entitled(uid or ""):
            tokens_left = None
        elif uid:
            tokens_left = _free_tokens_remaining(uid)
        else:
            tokens_left = FREE_DAILY_TOKENS
        return {
            "conversation": history,
            "history": history,  # Backward compatibility with Flask API
            "message_count": len(history),
            "free_tokens_left": tokens_left
        }
    except Exception as e:
        logger.error(f"Error retrieving history: {str(e)}")
        detail = f"An error occurred while retrieving conversation history: {str(e)}" if DEVELOPMENT_MODE else "An error occurred while retrieving conversation history"
        raise HTTPException(status_code=500, detail=detail)


@app.post("/reset", response_model=StatusResponse)
async def reset_conversation(raw_request: Request):
    """
    Clear the requesting visitor's conversation history — only theirs.

    Returns:
        StatusResponse confirming the reset
    """
    agent_instance = get_agent()

    try:
        uid = raw_request.cookies.get("ogai_uid")
        if uid:
            _clear_visitor_history(uid)
        with _memory_lock:
            agent_instance.clear_history()
        return {
            "status": "success",
            "agent_name": agent_instance.name,
            "message": "Conversation history has been cleared"
        }
    except Exception as e:
        logger.error(f"Error resetting conversation: {str(e)}")
        detail = f"An error occurred while resetting conversation: {str(e)}" if DEVELOPMENT_MODE else "An error occurred while resetting conversation"
        raise HTTPException(status_code=500, detail=detail)


@app.get("/intelligence")
async def get_intelligence():
    """
    Get the agent's intelligence report (self-learning stats).
    
    Returns:
        Intelligence report with learning statistics
    """
    agent_instance = get_agent()
    
    try:
        # Check if agent has learning system
        if hasattr(agent_instance, 'learning_system') and agent_instance.learning_system:
            report = agent_instance.learning_system.get_intelligence_report()
            return report
        else:
            return {
                "status": "unavailable",
                "message": "Self-learning system not enabled. Set ENABLE_SELF_LEARNING=true in .env",
                "intelligence_level": 1.0,
                "total_conversations": 0,
                "successful_patterns_learned": 0
            }
    except Exception as e:
        logger.error(f"Error getting intelligence report: {str(e)}")
        return {
            "status": "error",
            "message": str(e),
            "intelligence_level": 1.0
        }


@app.post("/improve")
async def manual_improvement():
    """
    Manually trigger daily self-improvement routine.
    
    Returns:
        Improvement report
    """
    agent_instance = get_agent()
    
    try:
        if hasattr(agent_instance, 'learning_system') and agent_instance.learning_system:
            improvements = agent_instance.learning_system.daily_self_improvement()
            return {
                "status": "success",
                "improvements": improvements,
                "message": "Self-improvement routine completed"
            }
        else:
            return {
                "status": "unavailable",
                "message": "Self-learning system not enabled"
            }
    except Exception as e:
        logger.error(f"Error during improvement: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/clear", response_model=StatusResponse)
async def clear_history(raw_request: Request):
    """
    Clear the conversation history (Flask API backward compatibility alias for /reset).
    
    Returns:
        StatusResponse confirming the clear
    """
    return await reset_conversation(raw_request)


# --- Google account connect routes (Round 3, dark until enabled) ------------

@app.get("/auth/google")
async def google_auth_start(raw_request: Request):
    """
    Begin Google connect: bounce the visitor to Google's own consent page.
    Answers 404 while the feature is dark (keys not set), so nothing about
    it is discoverable on the live site until Brent enables it.
    """
    if not GOOGLE_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")
    from urllib.parse import urlencode
    uid = raw_request.cookies.get("ogai_uid")
    fresh_uid = None
    if not uid:
        uid = uuid.uuid4().hex
        fresh_uid = uid
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": _google_state_for(uid),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    response = RedirectResponse(
        url="https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params),
        status_code=302)
    if fresh_uid:
        response.set_cookie(
            "ogai_uid", fresh_uid,
            max_age=COOKIE_MAX_AGE, path="/", httponly=True, samesite="lax")
    return response


@app.get("/auth/google/callback")
async def google_auth_callback(raw_request: Request, code: str = "",
                               state: str = "", error: str = ""):
    """
    Google sends the visitor back here with a code. The signed state tells
    us which visitor this is; the code is exchanged for tokens, the
    profile is fetched, and the connection is stored under their uid.
    Any failure lands back on the chat with ?google=failed — no error page.
    """
    if not GOOGLE_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")
    uid = _google_uid_from_state(state)
    if error or not code or not uid:
        return RedirectResponse(url="/?google=failed", status_code=302)
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            token_resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": GOOGLE_REDIRECT_URI,
                    "grant_type": "authorization_code",
                })
            if token_resp.status_code != 200:
                logger.warning(
                    f"Google token exchange status: {token_resp.status_code}")
                return RedirectResponse(url="/?google=failed", status_code=302)
            tokens = token_resp.json()
            info_resp = await client.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={
                    "Authorization": f"Bearer {tokens.get('access_token', '')}"
                })
            profile = info_resp.json() if info_resp.status_code == 200 else {}
    except Exception as e:
        logger.warning(f"Google connect failed: {e}")
        return RedirectResponse(url="/?google=failed", status_code=302)
    entry = {
        "email": profile.get("email", ""),
        "name": profile.get("name", ""),
        "picture": profile.get("picture", ""),
        "access_token": tokens.get("access_token", ""),
        "refresh_token": tokens.get("refresh_token", ""),
        "expires_at": (datetime.now(timezone.utc).timestamp()
                       + int(tokens.get("expires_in", 3600))),
        "connected": datetime.now(timezone.utc).isoformat(),
    }
    with _google_lock:
        store = _load_google_store()
        old = store.get(uid)
        if not entry["refresh_token"] and isinstance(old, dict):
            # Google only sends a refresh token on first consent; keep the
            # one we already hold rather than wiping it.
            entry["refresh_token"] = old.get("refresh_token", "")
        store[uid] = entry
        _save_google_store(store)
    response = RedirectResponse(url="/?google=connected", status_code=302)
    response.set_cookie(
        "ogai_uid", uid,
        max_age=COOKIE_MAX_AGE, path="/", httponly=True, samesite="lax")
    return response


@app.get("/auth/google/status")
async def google_auth_status(raw_request: Request):
    """What the slide-over menu needs: is connect enabled, and if this
    visitor is connected, as whom? (Never returns tokens.)"""
    entry = None
    if GOOGLE_ENABLED:
        entry = _google_connection(raw_request.cookies.get("ogai_uid"))
    return {
        "enabled": GOOGLE_ENABLED,
        "connected": bool(entry),
        "email": entry.get("email", "") if entry else "",
        "name": entry.get("name", "") if entry else "",
    }


@app.post("/auth/google/disconnect")
async def google_auth_disconnect(raw_request: Request):
    """Forget this visitor's Google connection — tokens deleted server-side."""
    if not GOOGLE_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")
    uid = raw_request.cookies.get("ogai_uid")
    if uid:
        with _google_lock:
            store = _load_google_store()
            if uid in store:
                del store[uid]
                _save_google_store(store)
    return {"status": "disconnected"}


if __name__ == "__main__":
    import uvicorn
    
    # Get port from environment variable or default to 8000
    port = int(os.environ.get("PORT", 8000))
    
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=port,
        reload=False  # Set to True for development
    )
