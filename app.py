"""
FastAPI Web Service for OG-AI Agent
Exposes REST API endpoints for interacting with the AI agent.
"""

import asyncio
import faulthandler
import hashlib
import hmac
import json
import os
import logging
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Optional
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse, StreamingResponse, JSONResponse
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

# --- OG Pro (money layer): chat tokens are metered WEEKLY
# (Round 55 — trailing 7 days, every tier).
PRO_UPGRADE_URL = os.getenv("OG_PRO_LINK", "#")
# SECURITY: set OG_PRO_TOKEN to a long random secret in production;
# the fallback placeholder lets anyone who knows it mint a Pro cookie.
PRO_TOKEN = os.getenv("OG_PRO_TOKEN", "CHANGE_ME_PRO_TOKEN")
USAGE_STORE_FILE = "usage_store.json"
COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # 1 year
_usage_lock = threading.Lock()

def _load_usage_store() -> Dict:
    """Load per-visitor daily chat counts from the JSON usage store.

    Ticket A4d: a load that hits a torn file must not be read
    as "the store is empty". Saves are atomic now (below), so
    a parse failure here means a pre-fix writer mid-dump (or
    real corruption); retry briefly — a dump finishes in
    milliseconds — before degrading. The old behaviour
    (return {} on any error) is what turned a torn read into
    a whole-store wipe: the next load-modify-save from that
    {} deleted every other key, live browser session records
    included (ogtest14, 2026-10-19)."""
    if os.path.exists(USAGE_STORE_FILE):
        last_err = None
        for attempt in range(4):
            try:
                with open(USAGE_STORE_FILE, 'r') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
                last_err = ValueError("usage store is not a dict")
            except Exception as e:
                last_err = e
            if attempt < 3:
                time.sleep(0.05)
        logger.warning(f"Could not load usage store: {last_err}")
    return {}

def _save_usage_store(store: Dict):
    """Save per-visitor daily chat counts to the JSON usage store.

    Ticket A4d: ATOMIC — the dump goes to a per-process temp
    file and is os.replace()d over the store, so a reader in
    the other gunicorn worker only ever sees a complete file.
    The old truncate-then-dump let a concurrent load parse a
    half-written file, fail, and read the store as empty —
    and any load-modify-save built on that empty read then
    wiped every key it did not know about (the ogtest14
    session record vanished this way mid-drill and the
    window closed on the next status reads). Belt and
    braces: an EMPTY store is refused over a non-empty file
    — no flow in this codebase legitimately empties the
    store, so an empty save is a failed load's fingerprint,
    refused loudly instead of wiping the world."""
    try:
        if not store:
            try:
                if os.path.getsize(USAGE_STORE_FILE) > 2:
                    logger.warning(
                        "Refused to save an empty usage store "
                        "over a non-empty file (failed-load "
                        "guard).")
                    return
            except OSError:
                pass
        tmp = f"{USAGE_STORE_FILE}.tmp{os.getpid()}"
        with open(tmp, 'w') as f:
            json.dump(store, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, USAGE_STORE_FILE)
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

def _tokens_left_week(uid: str, tier: str = "free"):
    """Tokens left in the tier's weekly chat pool (R55)."""
    pool = _og_tiers.weekly_chat_tokens(tier)
    if pool is None:
        return None
    return max(0, pool - _week_used(uid, "token_days", "tokens"))

def _free_tokens_remaining(uid: str) -> int:
    """Free tokens this visitor has left this week (R55: the
    free pool is weekly — 25,000/day x7, same total)."""
    return _tokens_left_week(uid, "free") or 0

def _record_chat_usage(uid: str, tokens_used: int):
    """Record one /chat exchange + tokens (all tiers, R55)."""
    with _usage_lock:
        store = _load_usage_store()
        entry = _today_entry(store, uid)
        _og_tiers.carry_days(store.get(uid), entry, "token_days")
        entry["count"] = int(entry.get("count", 0)) + 1
        entry["tokens"] = int(entry.get("tokens", 0)) + max(0, int(tokens_used))
        _og_tiers.note_day(entry, entry["date"], entry["tokens"], "token_days")
        store[uid] = entry
        _save_usage_store(store)

def _week_used(key: str, field="days", value_key="count") -> int:
    with _usage_lock:
        store = _load_usage_store()
    return _og_tiers.week_used(store.get(key), field, value_key)

_token_encoder = None
_token_encoder_tried = False

def _count_tokens(text) -> int:
    """Count tokens via tiktoken when available, else ~4 chars/token."""
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
    """Estimate a model call's TOTAL tokens (prompt + completion)."""
    total = _count_tokens(getattr(agent_instance, "system_prompt", "") or "")
    history = getattr(agent_instance, "conversation_history", []) or []
    window = [m for m in history[-30:]
              if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
    reply_counted = False
    for m in window:
        total += _count_tokens(m.get("content", "")) + 4  # per-message overhead
        if reply_text and m.get("content") == reply_text:
            reply_counted = True
    if reply_text and not reply_counted:
        total += _count_tokens(reply_text)
    return total

# --- AI voice: /tts speaks replies (OpenAI TTS "onyx") when
# OPENAI_API_KEY is set; else 503 and the page uses device voice.
TTS_MAX_CHARS = 600
# Round 37: per-avatar voices for the Unity app stage avatars.
# /tts accepts an optional "voice" field; only these OpenAI
# voices pass through — missing/invalid keeps the default.
TTS_VOICES = {"onyx", "fable", "echo", "nova", "shimmer", "alloy"}
TTS_DEFAULT_VOICE = "onyx"

def _consume_tts_call(uid: str, weekly_limit: int = None) -> bool:
    """Record one /tts call; False at the weekly cap (R55:
    voice is metered weekly only — no daily layer)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"tts:{uid}"
    with _usage_lock:
        store = _load_usage_store()
        entry = store.get(key)
        if not isinstance(entry, dict) or entry.get("date") != today:
            old, entry = entry, {"date": today, "count": 0}
            _og_tiers.carry_days(old, entry)
        if weekly_limit is not None and _og_tiers.week_used(entry) >= weekly_limit:
            return False
        entry["count"] += 1
        _og_tiers.note_day(entry, today, entry["count"])
        store[key] = entry
        _save_usage_store(store)
        return True

# --- Image generation (Round 5): POST /image, one image per ask
from og_image_gen import (IMAGE_CAPTIONS as _IMAGE_CAPTIONS,
    IMAGE_DOWN_LINE as _IMAGE_DOWN_LINE,
    clean_image_prompt as _clean_image_prompt,
    openai_generate_image as _openai_generate_image)

def _consume_image(uid: str):
    """Record one generated image today (UTC); success-only.
    (R55: also mirrors the entry's per-day map, note_day.)"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"image:{uid}"
    with _usage_lock:
        store = _load_usage_store()
        entry = store.get(key)
        if not isinstance(entry, dict) or entry.get("date") != today:
            old, entry = entry, {"date": today, "count": 0}
            _og_tiers.carry_days(old, entry)
        entry["count"] = int(entry.get("count", 0)) + 1
        _og_tiers.note_day(entry, today, entry["count"])
        store[key] = entry
        _save_usage_store(store)

# --- Unity maker (Round 16): per-tier daily package cap ---
def _unity_used_today(uid: str) -> int:
    """Unity projects this visitor has packaged today (UTC)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _usage_lock:
        store = _load_usage_store()
    entry = store.get(f"unity:{uid}")
    if not isinstance(entry, dict) or entry.get("date") != today:
        return 0
    return int(entry.get("count", 0))

def _unity_left(uid: str) -> int:
    """Unity packages left today for the current request's tier."""
    cap = _og_tiers.cap(_current_tier.get("tier", "free"), "unity")
    return max(0, cap - _unity_used_today(uid))

def _consume_unity(uid: str) -> bool:
    """Record one packaged Unity project today; False at cap."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"unity:{uid}"
    with _usage_lock:
        store = _load_usage_store()
        entry = store.get(key)
        if not isinstance(entry, dict) or entry.get("date") != today:
            entry = {"date": today, "count": 0}
        cap = _og_tiers.cap(_current_tier.get("tier", "free"), "unity")
        if int(entry.get("count", 0)) >= cap:
            return False
        entry["count"] = int(entry.get("count", 0)) + 1
        store[key] = entry
        _save_usage_store(store)
        return True

# --- Round 17: per-tier daily short-link cap ---
def _consume_shortlink(uid: str) -> bool:
    """Record one created short link today; False at cap."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"shortlink:{uid}"
    with _usage_lock:
        store = _load_usage_store()
        entry = store.get(key)
        if not isinstance(entry, dict) or entry.get("date") != today:
            entry = {"date": today, "count": 0}
        cap = _og_tiers.cap(_current_tier.get("tier", "free"), "shortlink")
        if int(entry.get("count", 0)) >= cap:
            return False
        entry["count"] = int(entry.get("count", 0)) + 1
        store[key] = entry
        _save_usage_store(store)
        return True

# --- Round 18: per-tier daily calorie-log entry cap ---
def _calorie_used_today(uid: str) -> int:
    """Food-log entries this visitor stored today (UTC)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _usage_lock:
        store = _load_usage_store()
    entry = store.get(f"calorie:{uid}")
    if not isinstance(entry, dict) or entry.get("date") != today:
        return 0
    return int(entry.get("count", 0))

def _calorie_left(uid: str) -> int:
    """Food-log entries left today for the current request's tier."""
    cap = _og_tiers.cap(_current_tier.get("tier", "free"), "calorie")
    return max(0, cap - _calorie_used_today(uid))

def _consume_calorie(uid: str) -> bool:
    """Record one stored food-log entry today; False at cap."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"calorie:{uid}"
    with _usage_lock:
        store = _load_usage_store()
        entry = store.get(key)
        if not isinstance(entry, dict) or entry.get("date") != today:
            entry = {"date": today, "count": 0}
        cap = _og_tiers.cap(_current_tier.get("tier", "free"), "calorie")
        if int(entry.get("count", 0)) >= cap:
            return False
        entry["count"] = int(entry.get("count", 0)) + 1
        store[key] = entry
        _save_usage_store(store)
        return True

# File reading (Round 6): og_file_read.py
import og_file_read as _og_files
import og_tiers as _og_tiers
# Maps & places (Round 9): og_maps.py
import og_maps as _og_maps
# Spotify connect (Round 10): og_spotify.py
import og_spotify as _og_spotify
# Google hands (Round 12): og_google_hands.py
import og_google_hands as _og_google_hands
# GitHub connect (Round 14): og_github.py
import og_github as _og_github
# Connect pack 2 (Round 15): og_<service>.py modules
import og_youtube as _og_youtube
import og_discord as _og_discord
import og_twitch as _og_twitch
import og_reddit as _og_reddit
# Unity maker (Round 16): og_unity.py
import og_unity as _og_unity
# Utilities pack (Round 17): og_utils.py
import og_utils as _og_utils
# Monitoring pack (Round 18): og_monitor.py + og_plaid.py
import og_monitor as _og_monitor
import og_plaid as _og_plaid
# Online ordering (Round 19): og_ordering.py
import og_ordering as _og_ordering
# Trading on approval (Round 20): og_trading.py
import og_trading as _og_trading
# File locker (Round 21, dark): og_storage.py
import og_storage as _og_storage
# OG browser (Round 23, dark).
import og_browser as _og_browser
import og_watch as _og_watch
import og_register as _og_register
# Story videos (Round 28): og_video.py
import og_video as _og_video
# OG songs (Round 30, dark until OG_SONG_ENABLED + studio key).
import og_songs as _og_songs
import og_artifacts as _og_artifacts
import og_avatar as _og_avatar
# Legal pages (/privacy, /terms): og_legal.py
import og_legal as _og_legal
import og_accounts as _og_accounts
import og_memory_io as _og_memory_io
import og_notify as _og_notify
import og_library as _og_library
import og_store_ready as _ogs
import og_selfcheck as _og_selfcheck
import og_fixqueue as _og_fixqueue
import og_claimbudget as _og_claimbudget
import self_learning as _self_learning
# Round 321 DBPOOL batch-5: in the serving process, learning
# from a conversation runs on self_learning's daemon worker
# instead of the chat response path (see self_learning).
_self_learning.DEFER_LEARNING = True
import og_lessons as _og_lessons
import og_tokenpacks as _og_tokenpacks  # Round 56
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo, pooled_connect as _pooled_connect

# --- Entitlement v2 (Stripe webhook, dark): v2 verifies payment
# via POST /stripe/webhook; v1 grants on /pro/success landing.
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

def _grant_entitlement(uid: str, session_id: str = "", email: str = "",
                       tier: str = ""):
    """Record a Stripe-confirmed entitlement (+ tier bought)."""
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
            "tier": tier if tier in _og_tiers.PAID_TIERS else "standard",
        }
        store[PRO_ENTITLED_KEY] = entitled
        _save_usage_store(store)

def _uid_entitlement_tier(uid: str):
    return _og_tiers.entitlement_tier_for(uid)


def _tier_of(cookies, uid: str = "") -> str:
    # Tier follows the ACCOUNT (round tierfix).
    return _og_tiers.tier_of(cookies, _og_accounts.session_uid_or(cookies, uid))


def _request_tier(raw_request) -> str:
    return _tier_of(raw_request.cookies,
                    raw_request.cookies.get("ogai_uid") or "")


def _verify_stripe_signature(payload: bytes, sig_header: str, secret: str) -> bool:
    """
    Verify a Stripe webhook signature (v1 HMAC-SHA256, no SDK).
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

# --- Web lookup (Round 3): app-layer wrap of the agent's
# detect_intent/web_search hooks; Responses web_search + fallbacks.
SEARCH_MODEL = os.getenv("OG_SEARCH_MODEL", "gpt-4o-mini")
SEARCH_API_KEY = os.getenv("OG_SEARCH_API_KEY", "")  # optional Tavily key
LOOKUP_METER_CAP = int(os.getenv("OG_LOOKUP_METER_CAP", "2000"))
# Lookup tokens for the current exchange; single slots safe
# (chat is serialized).
_lookup_tokens_stash = {"tokens": 0}
_current_uid = {"uid": ""}
_current_tier = {"tier": "free"}
# Round 321c: the generic-error leg counter — when a chat
# turn dies, the counter names the LEG that threw
# (history_load / claims / generation / metering / history_save).
_CHAT_ERROR_LEGS: Dict[str, int] = {}
_turn_leg = {"leg": "generation"}

def _note_chat_error(leg: str, err: Exception):
    _CHAT_ERROR_LEGS[leg] = _CHAT_ERROR_LEGS.get(leg, 0) + 1
    logger.error(f"Chat error (leg={leg}, count={_CHAT_ERROR_LEGS[leg]}): {err}")

def _set_turn_leg(leg: str):
    """Round 321 DBPOOL batch-5: the leg lives in two places
    — _turn_leg for the error counter (unchanged) and
    og_claimbudget's thread-local, which is what the og_db
    turn choke scopes its allowlist by (og_db cannot import
    app, so the thread-local is the channel)."""
    _turn_leg["leg"] = leg
    try:
        _og_claimbudget.set_turn_leg(leg)
    except Exception:
        pass


def _load_hist(uid: str):
    # Fail-soft history load (321c): a throw names the
    # history_load leg, serves an empty thread, and
    # marks the uid so the paired save skips.
    _set_turn_leg("history_load")
    try:
        return _load_visitor_history_locked(uid)
    except Exception as exc:
        _note_chat_error("history_load", exc)
        _hist_load_failed.add(uid)
        return []


def _save_turn_history(agent_instance, uid: str):
    """A chat worker's history save-back (321c fail-soft: a
    throw counts the leg and is swallowed)."""
    # DBPOOL batch-5: name the leg before the save's own
    # store ops — history_save is turn-choke allowlisted,
    # so a blown turn still saves its thread.
    _set_turn_leg("history_save")
    try:
        _save_visitor_history_locked(
            uid, list(getattr(agent_instance, "conversation_history", []) or []))
    except Exception as e:
        _note_chat_error("history_save", e)
# Raw message being processed; Round 4 data router tries it first.
_current_message = {"text": ""}

_LOOKUP_TRIGGER_PHRASES = (
    "look up", "lookup", "search for", "search the web", "google it",
    "google search", "check online", "on the internet", "on the web",
)
_LOOKUP_TRIGGER_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\b(today|tonight|tomorrow|yesterday|right now|currently|latest|this week|this month)\b",
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
        _limit = _og_tiers.cap(_current_tier.get("tier", "free"), "lookup")
        if entry["count"] >= _limit:
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
    """One grounded lookup via OpenAI's Responses API web_search tool. Returns (answer_text, sources, tokens_used) or None."""
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
    """App-layer search behind web_search: OpenAI lookup first, Tavily second, built-in DuckDuckGo last. Returns List[Dict] ({title, body, href})."""
    uid = _current_uid.get("uid", "")
    if uid and not _consume_lookup(uid):
        logger.info("Web lookup skipped: visitor at daily lookup cap")
        return []
    # Round 4: structured live data gets first crack.
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
    """Wrap the agent's detect_intent + web_search (app layer only)."""
    if getattr(agent_instance, "_og_lookup_installed", False):
        return
    original_detect = getattr(agent_instance, "detect_intent", None)
    original_search = getattr(agent_instance, "web_search", None)
    if original_detect is None or original_search is None:
        return
    agent_instance._og_original_web_search = original_search

    # Round 40: the code-generation veto on the
    # lookup upgrade now lives in og_utils
    # (CODE_ARTIFACT_RE) — see the wrapper below.

    def detect_intent_wrapped(message):
        _current_message["text"] = str(message)
        intent = original_detect(message)
        try:
            if (isinstance(intent, dict)
                    and not intent.get("needs_web_search")
                    and (_message_needs_lookup(message)
                         or _message_needs_data(message))):
                vetoed = (intent.get("needs_code_generation")
                          and _og_utils.CODE_ARTIFACT_RE.search(str(message)))
                if not vetoed:
                    intent["needs_web_search"] = True
                    intent["search_query"] = str(message).strip()
                    if intent.get("needs_code_generation"):
                        intent["needs_code_generation"] = False
        except Exception as e:
            logger.warning(f"Lookup trigger check failed: {e}")
        return intent

    def web_search_wrapped(query, num_results=5):
        return _og_web_search(agent_instance, query, num_results)

    agent_instance.detect_intent = detect_intent_wrapped
    agent_instance.web_search = web_search_wrapped
    agent_instance._og_lookup_installed = True

# --- Live data pack (Round 4): keyless tools behind the seam
# (weather/sports/stocks/crypto/news); shares lookup budget.
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

# --- Round 4: weather (Open-Meteo) ---
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
    r"\b(?:rain|snow)\s+(?:in|for)\s+([A-Za-z][A-Za-z .'-]{1,40})",
    r"^([A-Za-z][A-Za-z .'-]{1,40}?)\s+weather\b",
    r"\bin\s+([A-Z][A-Za-z .'-]{1,40})",
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
    """Place name -> (lat, lon, name, region) or None (Open-Meteo, Nominatim fallback)."""
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

def _weather_wttr(place: str, key: str):
    """Weather fallback on wttr.in (resolves place names itself, different host than the Open-Meteo chain). Same return shape as _tool_weather, or None."""
    import urllib.parse
    data = _fetch_json("https://wttr.in/" + urllib.parse.quote(place)
                       + "?format=j1")
    if not data:
        return None
    try:
        cur = (data.get("current_condition") or [{}])[0]
        days = data.get("weather") or []
        area = (data.get("nearest_area") or [{}])[0]
        name = area.get("areaName", [{}])[0].get("value") or place
        region = area.get("region", [{}])[0].get("value") or ""
        desc = cur.get("weatherDesc", [{}])[0].get("value") or ""
        lines = ["Weather for " + str(name)
                 + (f", {region}" if region else "") + " (wttr.in, live):",
                 f"Right now: {cur.get('temp_F')}°F, {desc.strip().lower()}, "
                 f"feels like {cur.get('FeelsLikeF')}°F, humidity "
                 f"{cur.get('humidity')}%, wind "
                 f"{cur.get('windspeedMiles')} mph."]
        for i, d in enumerate(days[:3]):
            label = ("Today" if i == 0
                     else ("Tomorrow" if i == 1 else d.get("date", "")))
            hourly = d.get("hourly") or []
            pop = None
            if hourly:
                try:
                    pop = max(int(h.get("chanceofrain") or 0)
                              for h in hourly)
                except Exception:
                    pop = None
            line = (f"{label}: high {d.get('maxtempF')}°F / low "
                    f"{d.get('mintempF')}°F")
            if pop:
                line += f", rain chance up to {pop}%"
            lines.append(line + ".")
    except Exception as e:
        logger.warning(f"wttr.in parse failed: {e}")
        return None
    return _data_store(key, ("Live weather data", "\n".join(lines),
                             "https://wttr.in/"),
                       _DATA_TTL["weather"])

_WEATHER_GATE_RE = re.compile(
    r"\b(weather|forecast|temperature|rain|raining|snow|snowing)\b")

def _tool_weather(query: str):
    """Live weather via Open-Meteo. (label, text, source) or None."""
    low = str(query).lower()
    if not _WEATHER_GATE_RE.search(low):
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
        return _weather_wttr(place, key)
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
        return _weather_wttr(place, key)
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

# --- Round 4: sports (ESPN public JSON) ---
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
    """All teams in one ESPN league with alias sets (cached an hour). Aliases: display + mascot names only — bare city names never match."""
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
            # Default schedule = current phase only; retry regular season.
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
        elif finals and not live:
            lines.append("No upcoming game on the schedule — their "
                         "season is over (or not posted yet).")
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

# --- Round 4: stocks (Nasdaq API, Yahoo Finance fallback) ---
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

# --- Round 4: crypto (Coinbase, CoinGecko fallback) ---
# alias -> (product or None, CoinGecko id, display, needs_context).
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

# --- Round 4: news (Google News RSS) ---
_clean_topic = _og_utils.clean_news_topic

def _tool_news(query: str):
    """Top or topical headlines via Google News RSS. Tuple or None."""
    import urllib.parse
    import xml.etree.ElementTree as ET
    low = str(query).lower()
    if "news" not in low and "headline" not in low \
            and "happening" not in low and "top stories" not in low:
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

# --- Round 4: router + detect heuristic ---
def _og_data_tools(query: str):
    """Round 4 router: live data pack hit -> web_search-shaped results; miss -> None."""
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

_DATA_TEAM_WORDS = _og_utils.DATA_TEAM_WORDS

def _message_needs_data(message: str) -> bool:
    """App-layer heuristic for the Round 4 data pack (weather, sports,
    quotes, headlines) — complements _message_needs_lookup."""
    if not message:
        return False
    low = message.lower()
    if "news" in low:
        return True
    if any(w in low for w in ("weather", "forecast", "temperature in",
                              "headlines", "top stories")):
        return True
    if re.search(r"\b(rain|raining|snow|snowing)\s+in\s+\S+", low):
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

# --- Google connect (Round 3): OAuth 2.0, per-visitor tokens
# (Postgres or JSON file); Round 12 extends in og_google_hands.py.
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
    conn = _pooled_connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
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

# --- Per-visitor memory: history keyed by `ogai_uid` (JSON store).
MEMORY_STORE_FILE = "memory_store.json"
MEMORY_MAX_MESSAGES = 40    # most recent messages kept per visitor
MEMORY_MAX_VISITORS = 300   # least-recently-active threads pruned beyond this
_memory_lock = threading.Lock()
# Round 321 DBPOOL batch-3: the lock serializes whole turns on
# the shared agent (swap-in -> process -> save-back, see the
# *_locked helpers below), so a turn legitimately holds it
# across generation — generation itself is now bounded by the
# OpenAI client's own timeout (ai_agent_enhanced). What may
# never happen again is a NON-turn path queueing behind a slow
# turn forever (the 2026-10-17 silent /chat + /history +
# /reset outage): every non-turn acquirer waits at most
# _MEMORY_LOCK_WAIT_S and then fails fast and loud instead.
_MEMORY_LOCK_WAIT_S = 10.0
# Soft threshold for the turn paths themselves: a lock wait or
# a lock hold longer than this logs a warning, so a slow turn
# is visible in the log while it happens, not after the fact.
_MEMORY_TURN_WARN_S = 60.0

# Durable backend: OG_MEMORY_DB_URL (Postgres) keeps threads across
# rebuilds; unset/unreachable falls back to the JSON file store.
MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg not installed — DB backend simply unavailable
    psycopg = None
    _Jsonb = None

def _reset_memory_store():
    """Start the memory store empty on fresh agent creation (NOT reset with the Postgres backend)."""
    if MEMORY_DB_URL:
        logger.info("Durable memory backend active — memory store not reset")
        return
    # DBPOOL batch-3: bounded wait (see _MEMORY_LOCK_WAIT_S) —
    # a skipped reset is logged, never a silent hang at build.
    if not _memory_lock.acquire(timeout=_MEMORY_LOCK_WAIT_S):
        logger.warning("Memory store reset skipped: memory lock busy")
        return
    try:
        _save_memory_store({})
    finally:
        _memory_lock.release()

def _ensure_memory_store():
    """DBPOOL batch-4: the cheapest correct memory-store
    init for paths that must not build the agent
    (/history used to call get_agent() just for this —
    "a fresh agent instance starts with a fresh memory
    store"). DB backend: a no-op — the keyed store needs
    no init, exactly as _reset_memory_store() already
    returns untouched there. File backend: create the
    store file iff it is missing (an init, never a wipe —
    the build-time reset above keeps its exact semantics
    and its exact call site in _build_agent)."""
    if MEMORY_DB_URL:
        return
    if not os.path.exists(MEMORY_STORE_FILE):
        _save_memory_store_file({})

def _load_memory_store() -> Dict:
    """The JSON file store, whole (the no-DB backend's shape)."""
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
    """Save the JSON file store (no-DB backend)."""
    _save_memory_store_file(store)

def _save_memory_store_file(store: Dict):
    """Save per-visitor conversation threads to the JSON memory store."""
    try:
        with open(MEMORY_STORE_FILE, 'w') as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Could not save memory store: {e}")

# The *_locked helpers assume the caller holds _memory_lock (/chat
# holds it across swap-in -> process -> save-back).
# Round 321c (B6a): uids whose keyed load failed this turn
# — their paired save SKIPS (see the save helper).
_hist_load_failed = set()

def _load_visitor_history_locked(uid: str) -> List[Dict]:
    """This visitor's stored thread (most recent last) — Round 135
    (B6a): KEYED durable read via og_memory_io. Round 321c: the
    whole-store fallback is GONE (see _hist_load_failed)."""
    if MEMORY_DB_URL:
        entry = _og_memory_io.load_history_entry(uid)
        if entry is not None:
            if isinstance(entry, dict) and isinstance(entry.get("history"), list):
                return list(entry["history"])
            return []
        _hist_load_failed.add(uid)
        logger.warning(
            f"Keyed memory load failed for {uid}; its save will skip.")
        return []
    entry = _load_memory_store_file().get(uid)
    if isinstance(entry, dict) and isinstance(entry.get("history"), list):
        return list(entry["history"])
    return []

def _save_visitor_history_locked(uid: str, history: List[Dict]):
    """Persist this visitor's thread, trimmed, pruning the stalest threads."""
    trimmed = list(history)[-MEMORY_MAX_MESSAGES:]
    if uid in _hist_load_failed:
        _hist_load_failed.discard(uid)
        logger.warning(f"Memory save skipped for {uid} (load failed).")
        return
    if MEMORY_DB_URL:
        if not _og_memory_io.save_history(uid, trimmed, MEMORY_MAX_VISITORS):
            logger.warning(f"Keyed memory save failed for {uid}.")
        return
    store = _load_memory_store_file()
    store[uid] = {"updated": datetime.now(timezone.utc).isoformat(), "history": trimmed}
    if len(store) > MEMORY_MAX_VISITORS:
        ordered = sorted(
            store.items(),
            key=lambda kv: kv[1].get("updated", "") if isinstance(kv[1], dict) else "",
        )
        for old_uid, _ in ordered[: len(store) - MEMORY_MAX_VISITORS]:
            store.pop(old_uid, None)
    _save_memory_store_file(store)

def _clear_visitor_history(uid: str) -> bool:
    """Forget one visitor's thread (their /reset or /clear, nobody else's).
    DBPOOL batch-3: bounded wait — False means the memory lock
    was busy (a turn mid-flight); the caller answers 503 and
    the visitor retries, instead of queueing behind the turn
    forever. True means the thread is gone (or never was)."""
    if not _memory_lock.acquire(timeout=_MEMORY_LOCK_WAIT_S):
        logger.warning(f"Memory clear for {uid} refused: memory lock busy")
        return False
    try:
        # Round 321c: keyed delete first (og_memory_io, DB backend).
        if _og_memory_io.delete_history(uid):
            return True
        store = _load_memory_store_file()
        if uid in store:
            del store[uid]
            _save_memory_store_file(store)
        return True
    finally:
        _memory_lock.release()

# --- Business stats: owner scorecard counters in the usage store
# ("__stats__"); read at GET /stats?key=<OG_STATS_TOKEN>.
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


class _HealthShortcutMiddleware:
    """Round 321 Step 6: /health answered at the ASGI layer,
    before routing — a pre-built body + the live warm flag
    and nothing else. Render's 5 s health patience is met
    as long as the event loop itself is free to schedule
    this coroutine, which the Step 6 loop-occupancy
    invariant (no synchronous I/O or unbounded CPU in any
    handler body) guarantees. The /health route below
    stays as the fallback shape."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" \
                and scope.get("path") == "/health" \
                and scope.get("method", "GET") in ("GET", "HEAD"):
            warm = agent is not None
            body = json.dumps({
                "status": "healthy",
                "agent_name": agent.name if warm else "OG-AI",
                "message": "Service is running"}).encode()
            await send({
                "type": "http.response.start", "status": 200,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"content-length",
                     str(len(body)).encode()],
                    [b"x-og-warm", b"1" if warm else b"0"]]})
            await send({"type": "http.response.body",
                        "body": b"" if scope.get(
                            "method") == "HEAD" else body})
            return
        await self.app(scope, receive, send)


app.add_middleware(_HealthShortcutMiddleware)


class _CacheDefenseMiddleware:
    """Ticket L4 (diagnosis og-l2-diagnosis-2026-10-22,
    section 6A): the app set NO cache policy at all, so
    an iOS tab suspended across a deploy resumed the OLD
    page instance indefinitely — Brent re-drilled a fix
    and saw the pre-fix build, pixel for pixel. The
    defense: /static/* responses carry Cache-Control:
    no-cache, so a resumed or restored client must
    revalidate every static before running it (ETag /
    Last-Modified support already exists, so an
    unchanged file revalidates as a cheap 304, and the
    ?v=N stamp discipline still forces a first-seen URL
    after a real change). The HTML routes answer for
    themselves (/ and /fin carry the same header on
    their FileResponses). Nothing else is touched: no
    other path's headers change."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and \
                (scope.get("path") or "").startswith(
                    "/static/"):
            async def send_defended(message):
                if message.get("type") == \
                        "http.response.start":
                    headers = [
                        (k, v) for k, v in
                        message.get("headers", [])
                        if k.lower() != b"cache-control"]
                    headers.append(
                        (b"cache-control", b"no-cache"))
                    message = dict(message)
                    message["headers"] = headers
                await send(message)
            await self.app(scope, receive, send_defended)
            return
        await self.app(scope, receive, send)


app.add_middleware(_CacheDefenseMiddleware)

# Mount static files directory
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

agent = None

agent_build_lock = threading.Lock()


class AgentNotReady(Exception):
    """DBPOOL batch-6: raised by get_agent_bounded() when
    the shared agent did not publish within the caller's
    wait. Request handlers convert it to a fast, honest
    503 {"detail": "agent_warming_up"} instead of parking
    until the platform severs the connection (~271 s of
    silence: T8/T9/T10/T11, 2026-10-18). The build itself
    is untouched — it keeps running in the background;
    only the WAIT became bounded."""


# DBPOOL batch-6: set at the exact publish point in
# _build_agent() (after `agent = _built`), so bounded
# waiters wake the moment a build lands instead of
# discovering it on their next poll.
_agent_published = threading.Event()

# DBPOOL batch-7: PID-STAMPED BUILD STATE (fork
# self-heal). Prod conviction (2026-10-18, batch-6
# instruments): the build completes in ~8 s in the
# gunicorn ARBITER — app.py's import-time code (this
# state, the eager-build thread, the faulthandler arm)
# runs in the master BEFORE the workers fork — so each
# worker is forked WHILE the master's eager thread
# holds agent_build_lock mid-build and inherits the
# whole build state poisoned: agent=None, an
# agent_build_lock LOCKED by a thread that does not
# exist in the child, an unset _agent_published, a
# dead _eager_build_thread handle, _kick_in_flight
# stuck as the master left it. In the child nothing
# can ever build: get_agent() parks forever on the
# inherited lock (the T8–T15 chat hang), the bounded
# wait's kick thread parks the same way, and only the
# batch-4 no-build paths (/history, /reset) answer.
# The cure: the build state belongs to ONE process.
# Every build entry point (get_agent,
# get_agent_bounded, agent_if_built,
# _start_eager_agent_build, and the ASGI startup hook
# below) first calls _agent_state_for_this_process();
# a pid mismatch means fork-inherited state and it is
# reinitialized wholesale as FRESH objects (never
# "unlocked" — a lock whose owner died in another
# process is not released, it is REPLACED). Same-pid
# calls are a no-op, so single-process runs (every
# suite, local dev) see no behavior change at all.
# Audit (2026-10-18): agent_build_lock,
# _agent_published, _kick_lock / _kick_in_flight and
# _eager_build_thread are referenced ONLY by bare
# global name inside app.py functions — nothing
# captured the objects themselves into another
# structure at import (the tool-context dicts capture
# the get_agent FUNCTION, which reads the globals at
# call time), and no other module references them —
# so rebinding the names below re-points every user.
_agent_state_pid = None
_agent_state_lock = threading.Lock()


def _agent_state_for_this_process() -> None:
    """DBPOOL batch-7: claim the module build state
    for THIS process, reinitializing it when it was
    inherited across a fork. Cheap on the hot path:
    one getpid + compare; the meta-lock is held only
    for the check/rebind (no I/O, never across a
    build), so two threads racing a first touch in a
    fresh worker cannot double-reset."""
    global agent, agent_build_lock, _agent_published
    global _kick_lock, _kick_in_flight, _eager_build_thread
    global _agent_state_pid
    pid = os.getpid()
    if _agent_state_pid == pid:
        return
    with _agent_state_lock:
        if _agent_state_pid == pid:
            return
        _old_pid = _agent_state_pid
        agent = None
        agent_build_lock = threading.Lock()
        _agent_published = threading.Event()
        _kick_lock = threading.Lock()
        _kick_in_flight = False
        _eager_build_thread = None
        _agent_state_pid = pid
        if _old_pid is not None:
            logger.warning(
                "agent build state reinitialized for pid "
                "%s (fork self-heal: state inherited "
                "from pid %s discarded)", pid, _old_pid)


def get_agent() -> AIAgent:
    """The shared agent instance (created on first use)."""
    global agent
    _agent_state_for_this_process()
    if agent is None:
        with agent_build_lock:
            if agent is None:
                _build_agent()
    return agent


def agent_if_built():
    """DBPOOL batch-4: the shared agent if it exists, else
    None — NEVER builds and never queues on
    agent_build_lock. For paths whose real work does not
    need the agent (/history's store read, /reset's durable
    clear): a cold worker must answer from the durable
    store, not park behind a cold build (T7, 2026-10-18:
    the first agent-touching /history on a fresh worker
    hung past the platform's ~271 s sever while the build
    crawled on the 0.1-CPU box, and every agent-touching
    endpoint queued behind the same build lock)."""
    _agent_state_for_this_process()
    return agent


# DBPOOL batch-6: the bounded wait's build-kick state.
# At most one kick thread is ever outstanding; get_agent()
# itself single-flights the actual build on
# agent_build_lock, so a kick started while the eager
# build is in flight just queues behind it and returns
# when it publishes — it can never start a second build.
_kick_lock = threading.Lock()
_kick_in_flight = False


def _ensure_agent_build_async() -> None:
    """Start ONE daemon thread running the normal
    unbounded get_agent() if no kick is outstanding.
    Covers the first-touch case (the eager thread died,
    or a suite/request is the first caller ever) without
    thread spam from concurrent bounded waiters."""
    global _kick_in_flight
    if agent is not None:
        return
    with _kick_lock:
        if _kick_in_flight or agent is not None:
            return
        _kick_in_flight = True

    def _kick():
        global _kick_in_flight
        try:
            get_agent()
        except Exception as e:
            logger.warning(f"Bounded-wait build kick failed: {e}")
        finally:
            with _kick_lock:
                _kick_in_flight = False

    threading.Thread(
        target=_kick, name="og-agent-build-kick", daemon=True).start()


def get_agent_bounded(wait_s: float = None) -> AIAgent:
    """DBPOOL batch-6: the shared agent, waiting at most
    wait_s seconds for an in-flight build to publish
    (default 20 s, env OG_AGENT_WAIT_SECONDS). Returns
    instantly when the agent is already built. Raises
    AgentNotReady when the wait expires — request handlers
    turn that into a 503 instead of an unbounded park
    behind agent_build_lock (in prod the build has been
    observed to never publish while get_agent() callers
    queued silently past the ~271 s platform sever). The
    build is NEVER interrupted and never duplicated:
    this only bounds the waiting, polling the published
    global / the publish Event, and the polling thread
    never holds agent_build_lock."""
    _agent_state_for_this_process()
    if agent is not None:
        return agent
    if wait_s is None:
        try:
            wait_s = float(os.getenv("OG_AGENT_WAIT_SECONDS", "20"))
        except Exception:
            wait_s = 20.0
    _ensure_agent_build_async()
    deadline = time.monotonic() + max(wait_s, 0.0)
    while True:
        if agent is not None:
            return agent
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AgentNotReady(
                f"agent not published within {wait_s:.1f}s")
        _agent_published.wait(timeout=min(0.05, remaining))


def _build_agent() -> None:
    """Build the shared agent into a local and publish
    it (Round 321 Step 6): get_agent()'s global used to
    be assigned FIRST and _reset_memory_store() ran
    LAST, so a turn served by the half-built agent
    could save history the still-running build then
    wiped. Single-flight under _agent_build_lock;
    readers only ever see a complete agent."""
    global agent
    # DBPOOL batch-6: progress lines (WARNING = the level
    # that provably reaches prod logs). A build in flight
    # used to log NOTHING until its final install, so a
    # build parked mid-installs was indistinguishable
    # from a build that never started. The heartbeat
    # counts are the install calls completed at that
    # source point, of 23 total.
    logger.warning("_build_agent: start")
    config = {}
    if os.path.exists('config.json'):
        try:
            with open('config.json', 'r') as f:
                config = json.load(f)
        except Exception as e:
            logger.warning(f"Could not load config.json: {e}")

    agent_name = config.get('agent_name', 'OG-AI')
    _built = AIAgent(name=agent_name, config=config)
    _install_lookup_tools(_built)
    _og_files.install_file_tools(
        _built, lambda: _current_uid.get("uid", ""),
        lambda: os.getenv("OPENAI_API_KEY"))
    _og_maps.install_maps_tools(
        _built, lambda: _current_uid.get("uid", ""), _consume_lookup)
    _og_spotify.install_spotify_tools(
        _built, lambda: _current_uid.get("uid", ""), _consume_lookup)
    _og_github.install_github_tools(
        _built, lambda: _current_uid.get("uid", ""), _consume_lookup)
    _og_youtube.install_youtube_tools(
        _built, lambda: _current_uid.get("uid", ""), _consume_lookup)
    _og_discord.install_discord_tools(
        _built, lambda: _current_uid.get("uid", ""), _consume_lookup)
    _og_twitch.install_twitch_tools(
        _built, lambda: _current_uid.get("uid", ""), _consume_lookup)
    logger.warning("_build_agent: installs 8 done")
    _og_reddit.install_reddit_tools(
        _built, lambda: _current_uid.get("uid", ""), _consume_lookup)
    _og_google_hands.install_google_hands(
        _built, lambda: _current_uid.get("uid", ""), _consume_lookup,
        {"connection": _google_connection,
         "load_store": _load_google_store,
         "save_store": _save_google_store,
         "lock": _google_lock,
         "client_id": GOOGLE_CLIENT_ID,
         "client_secret": GOOGLE_CLIENT_SECRET,
         "google_enabled": GOOGLE_ENABLED})
    _og_unity.install_unity_tools(
        _built, lambda: _current_uid.get("uid", ""), _consume_unity,
        _unity_left)
    _og_utils.install_utils_tools(
        _built, lambda: _current_uid.get("uid", ""), _consume_lookup,
        _consume_shortlink)
    _og_plaid.install_plaid_tools(
        _built, lambda: _current_uid.get("uid", ""), _consume_lookup)
    _og_monitor.install_monitor_tools(
        _built, lambda: _current_uid.get("uid", ""), _consume_lookup,
        _consume_calorie, lambda: _calorie_left(
            _current_uid.get("uid", "")),
        lambda: _current_tier.get("tier", "free"))
    _og_ordering.install_ordering_tools(
        _built, lambda: _current_uid.get("uid", ""))
    _og_trading.install_trading_tools(
        _built, lambda: _current_uid.get("uid", ""))
    logger.warning("_build_agent: installs 16 done")
    _og_storage.install_storage_tools(
        _built, lambda: _current_uid.get("uid", ""))
    _og_browser.install_browser_tools(_built, lambda: _current_uid.get("uid", ""))
    _og_video.install_video_tools(_built, lambda: _current_uid.get("uid", ""))
    _og_watch.install_watch_tools(_built, lambda: _current_uid.get("uid", ""), lambda: _current_tier.get("tier", "free"))
    _og_songs.install_song_tools(_built, lambda: _current_uid.get("uid", ""))
    _og_artifacts.install_artifact_tools(_built, lambda: _current_uid.get("uid", ""))
    _og_selfcheck.install_selfcheck_tools(_built, lambda: _current_uid.get("uid", ""))
    _og_fixqueue.install_fixqueue_tools(_built, lambda: _current_uid.get("uid", ""))
    logger.warning("_build_agent: installs complete, publishing")
    _reset_memory_store()
    agent = _built
    _agent_published.set()
    logger.warning("_build_agent: published")

# DBPOOL batch-4: build the agent EAGERLY at worker startup
# on a background thread, so the first-touch latency is
# paid at boot — never by a user request, never on a
# request thread (T7). Single-flight through get_agent():
# the build still happens at most once per worker however
# many callers race it, and a request that genuinely needs
# the agent while the eager build is in flight waits on
# the build lock exactly as it did on a cold first touch.
# The thread is a daemon (it must never hold the worker
# open) and /health never waits on it — the health paths
# read the module global only, as they already did.
_eager_build_thread = None


def _start_eager_agent_build():
    """Start the one eager build thread; returns it, or
    None when the agent is already built or the thread was
    already started."""
    global _eager_build_thread
    _agent_state_for_this_process()
    if agent is not None or _eager_build_thread is not None:
        return None

    def _build():
        logger.warning("Eager agent build thread running")
        try:
            get_agent()
            logger.warning("Eager agent build thread returned: agent ready")
        except Exception as e:
            # A failed eager build must not kill the worker:
            # the next real request retries through
            # get_agent() exactly as it did before batch 4.
            logger.warning(f"Eager agent build failed: {e}")

    _eager_build_thread = threading.Thread(
        target=_build, name="og-eager-agent-build", daemon=True)
    _eager_build_thread.start()
    logger.warning("Eager agent build thread started")
    return _eager_build_thread


_start_eager_agent_build()

# DBPOOL batch-6: SCHEDULED THREAD-STACK DUMPS (diagnostic).
# Prod's chat park is invisible because a build in flight
# logs nothing and a caller parked on agent_build_lock
# logs nothing; these dumps put EVERY thread's stack in
# the Render log on a boot schedule (first at ~75 s, then
# every ~240 s, at most 4) so the parked thread names
# itself. faulthandler writes to stderr, which is where
# Render collects logs from. Arming is fail-soft: any
# error here must never break import or boot. Set
# OG_FAULTHANDLER_DUMPS=0 to disable (the batch-7 fix
# batch is expected to retire this).
_FAULTHANDLER_FIRST_S = 75.0
_FAULTHANDLER_PERIOD_S = 240.0
_FAULTHANDLER_MAX_DUMPS = 4


def _start_faulthandler_dumps():
    """Arm the scheduled stack dumps; returns the daemon
    thread, or None when disabled by env or when arming
    failed (fail-soft — diagnostics never break boot)."""
    if os.getenv("OG_FAULTHANDLER_DUMPS", "1").strip().lower() \
            in ("0", "false", "no", "off"):
        return None
    try:
        faulthandler.enable(file=sys.stderr)
    except Exception:
        return None

    def _dumps():
        delay = _FAULTHANDLER_FIRST_S
        for _ in range(max(_FAULTHANDLER_MAX_DUMPS, 0)):
            time.sleep(delay)
            delay = _FAULTHANDLER_PERIOD_S
            try:
                logger.warning(
                    "faulthandler dump: thread stacks follow on stderr")
                faulthandler.dump_traceback(file=sys.stderr)
            except Exception:
                pass

    try:
        t = threading.Thread(
            target=_dumps, name="og-faulthandler-dumps", daemon=True)
        t.start()
        logger.warning("faulthandler dumps armed (batch-6 diagnostic)")
        return t
    except Exception:
        return None


_faulthandler_thread = _start_faulthandler_dumps()

# DBPOOL batch-7: the import-time arm above ran in the
# importing process (in prod the gunicorn arbiter); a
# forked worker inherits the dead thread handle and a
# watchdog that will never fire in the child — the
# batch-6 dumps showed ONLY arbiter stacks, never a
# worker's. The dumps therefore arm PER PROCESS: the
# pid stamp records which process the live arm belongs
# to, and the ASGI startup hook below re-arms in every
# worker. _start_faulthandler_dumps() itself is
# unchanged (its direct callers keep batch-6 semantics);
# the stamp lives in this wrapper so a process never
# double-arms.
_faulthandler_armed_pid = (
    os.getpid() if _faulthandler_thread is not None else None)


def _arm_faulthandler_dumps_for_this_process():
    """DBPOOL batch-7: arm the stack dumps in THIS
    process unless this process already armed them;
    returns the live dump thread (or None when disabled
    by env / arming failed, exactly as
    _start_faulthandler_dumps)."""
    global _faulthandler_armed_pid, _faulthandler_thread
    if _faulthandler_armed_pid == os.getpid():
        return _faulthandler_thread
    _t = _start_faulthandler_dumps()
    if _t is not None:
        _faulthandler_armed_pid = os.getpid()
        _faulthandler_thread = _t
    return _t


@app.on_event("startup")
def _og_worker_startup():
    """DBPOOL batch-7: per-worker boot wiring. ASGI
    startup runs in EVERY worker process, unlike the
    import-time calls above, which run in whichever
    process imported the module (in prod the arbiter,
    whose build state the workers then inherit
    poisoned — see _agent_state_for_this_process).
    Order matters: heal this process's build state
    FIRST, then start THIS worker's eager build, then
    arm THIS worker's stack dumps, so batch-7
    verification reads worker stacks, not the
    arbiter's."""
    _agent_state_for_this_process()
    _start_eager_agent_build()
    _arm_faulthandler_dumps_for_this_process()

# Pydantic models for request/response
class ChatRequest(BaseModel):
    message: str
    speak_response: bool = False
    # stream=true: SSE streamed reply; omit/false = classic JSON.
    stream: bool = False
    # Round 11: "near me" coords for one answer (og_maps; unstored).
    coords: Optional[Dict[str, float]] = None
    # Composer patch: sent WITH a fresh picture attached ("+" flow).
    image: Optional[bool] = None

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
    upgrade_url: Optional[str] = None
    free_tokens_left: Optional[int] = None
    weekly_tokens_left: Optional[int] = None
    token_balance: Optional[int] = None

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
    tier: Optional[str] = None
    token_balance: Optional[int] = None

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
    # Ticket L4 cache defense: the HTML itself must
    # revalidate on every load, or a suspended tab can
    # hold a stale build against every ?v=N stamp
    # behind it (diagnosis og-l2-diagnosis-2026-10-22).
    return FileResponse(
        "index_epic.html",
        headers={"Cache-Control": "no-cache"})

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
def api_info():
    """
    API information endpoint.
    """
    try:
        agent_instance = get_agent_bounded()
    except AgentNotReady:
        raise HTTPException(status_code=503, detail="agent_warming_up")
    return {
        "status": "healthy",
        "agent_name": agent_instance.name,
        "message": "OG-AI Agent API is running. Visit /docs for API documentation."
    }

@app.get("/health", response_model=StatusResponse)
async def health_check(response: Response):
    """
    Health check endpoint for monitoring service status.
    Round 321c (B5): a COLD worker answers WITHOUT paying
    get_agent(); warmth rides the X-OG-Warm header.
    """
    response.headers["X-OG-Warm"] = "1" if agent is not None else "0"
    return {
        "status": "healthy",
        "agent_name": agent.name if agent is not None else "OG-AI",
        "message": "Service is running"
    }

# --- Streaming chat: /chat {"stream": true} = SSE chunks + done.

def _generate_reply_streaming(agent_instance, message: str,
                              speak_response: bool, sink):
    """Streaming twin of process_message(); returns (response, tokens)."""
    agent = agent_instance
    agent.add_message('user', message)

    learned_hint = ""
    if getattr(agent, 'learning_system', None):
        learned_hint = agent.learning_system.get_learned_response(message)

    _set_turn_leg("claims")  # Round 321c: detect IS the claim phase
    intent = agent.detect_intent(message)
    _set_turn_leg("generation")

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

    # Handle CODE GENERATION first (same priority as process_message):
    # a tool call with an atomic result — delivered whole.
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

    # --- OpenAI: true token streaming (same request, stream=True).
    if provider == "openai" and getattr(agent, 'openai_client', None):
        parts = []
        try:
            messages = [{"role": "system", "content": agent.system_prompt}]
            for msg in agent.conversation_history[-30:]:
                if msg['role'] in ['user', 'assistant']:
                    messages.append({"role": msg['role'], "content": msg['content']})
            if context:
                messages.append({"role": "system",
                                 "content": f"Additional context:\n{context}"})
            # DBPOOL batch-5: the same chat-scoped caps as
            # _openai_response (45 s, no retries), per call —
            # the shared client keeps its batch-3 bounds for
            # every other user. An SDK without with_options
            # just keeps those bounds, never a failure.
            _chat_client = agent.openai_client
            try:
                _chat_client = _chat_client.with_options(
                    timeout=45.0, max_retries=0)
            except Exception:
                pass
            stream = _chat_client.chat.completions.create(
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

    # --- Anthropic: true token streaming via _anthropic_response.
    elif provider == "anthropic" and getattr(agent, 'anthropic_client', None):
        parts = []
        try:
            messages = []
            for msg in agent.conversation_history[-30:]:
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

    response = agent._generate_ai_response(message, context)
    sink("chunk", response)
    finished = _finish(response)
    return finished, _estimate_call_tokens(agent, finished)

def _stream_chat_worker(agent_instance, uid: str, message: str,
                        speak_response: bool, sink, meter: bool = True,
                        tier: str = "free", coords=None, image=None,
                        token_funded: bool = False):
    """Streaming /chat worker: mirrors classic /chat bookkeeping."""
    # DBPOOL batch-5: BOTH budgets (the Round 321c claim
    # budget and the new turn budget) begin AFTER the lock
    # is held — a turn queued behind a slow turn must not
    # start with its clocks already blown (the convoy fix;
    # nothing between the old begin site and the acquire
    # touched claims or stores, so the move changes only
    # when the clocks start). The turn budget's deadline is
    # what og_db's choke enforces on the turn's store work.
    _entry_t0 = time.monotonic()
    # DBPOOL batch-3: the whole-turn hold stays (shared agent),
    # but a long wait / long hold now logs while it happens.
    _lock_t0 = time.monotonic()
    _memory_lock.acquire()
    _lock_waited = time.monotonic() - _lock_t0
    if _lock_waited > _MEMORY_TURN_WARN_S:
        logger.warning(
            f"Stream turn waited {_lock_waited:.0f}s for the memory lock")
    _budget_started = _og_fixqueue.begin_claim_budget()  # Round 321c claim budget
    _turn_started = _og_claimbudget.begin_turn_budget()
    _og_claimbudget.turn_marker("start", _lock_waited * 1000.0)
    _turn_outcome = "ok"
    _turn_t0 = time.monotonic()
    agent_instance.conversation_history = _load_hist(uid)
    _current_uid["uid"] = uid
    _current_tier["tier"] = tier
    _og_maps.note_request_coords(coords, uid)
    _og_files.note_attach_image(uid if image else None)
    try:
        has_learning = hasattr(agent_instance, 'learning_system') \
            and agent_instance.learning_system is not None
        _set_turn_leg("claims")
        _reply_t0 = time.monotonic()
        if hasattr(agent_instance, 'detect_intent'):
            # Round 26: chunks flow through the register sink —
            # complete sentences are enforced before they're emitted.
            _reg = _og_register.wrap_sink(sink)
            response, tokens_used = _generate_reply_streaming(
                agent_instance, message, speak_response, _reg)
            _reg.flush()
            if _reg.text():
                response = _reg.text()
        else:
            # Agent without streaming internals: its classic full-text reply,
            # delivered whole in a single chunk (never sliced).
            try:
                response = agent_instance.process_message(
                    message, speak_response=speak_response)
            except TypeError:
                response = agent_instance.process_message(message)
            response = _og_register.enforce_reply(response)
            sink("chunk", response)
            tokens_used = _estimate_call_tokens(agent_instance, response)
        _og_claimbudget.turn_marker(
            "reply", (time.monotonic() - _reply_t0) * 1000.0)

        # The saved history carries the enforced reply, same as the
        # visitor saw it.
        _hist = agent_instance.conversation_history
        if _hist and _hist[-1].get("role") == "assistant":
            _hist[-1]["content"] = response

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
        # Metering: record every tier (R55); free meter free-only.
        _set_turn_leg("metering")
        _record_chat_usage(uid, tokens_used)
        result["token_balance"] = _og_tokenpacks.settle_chat(
            uid, token_funded, tokens_used)
        if meter:
            _bump_stat("tokens", tokens_used)
            result["free_tokens_left"] = _free_tokens_remaining(uid)
        else:
            result["free_tokens_left"] = None
        if has_learning:
            report = agent_instance.learning_system.get_intelligence_report()
            result["intelligence"] = report.get("intelligence_level", 1.0)
        sink("done", result)
    except Exception as e:
        _turn_outcome = "error"
        _note_chat_error(_turn_leg["leg"], e)
        detail = f"An error occurred while processing your message: {str(e)}" \
            if DEVELOPMENT_MODE else "An error occurred while processing your message"
        sink("error", {"detail": detail})
    finally:
        _current_uid["uid"] = ""
        _current_tier["tier"] = "free"
        _og_maps.note_request_coords(None, None)
        _og_files.note_attach_image(None)
        try:
            _save_turn_history(agent_instance, uid)
        finally:
            _og_claimbudget.turn_marker(
                "total", (time.monotonic() - _entry_t0) * 1000.0,
                outcome=_turn_outcome)
            _held = time.monotonic() - _turn_t0
            if _held > _MEMORY_TURN_WARN_S:
                logger.warning(
                    f"Stream turn held the memory lock {_held:.0f}s")
            _memory_lock.release()
            _og_claimbudget.end_turn_budget(_turn_started)
            _og_fixqueue.end_claim_budget(_budget_started)

def _sse_streaming_response(event_gen, http_response, entitled: bool):
    """Build the SSE response, carrying over cookies the handler set."""
    resp = StreamingResponse(
        event_gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
    for name, value in http_response.raw_headers:
        if name.lower() == b"set-cookie":
            resp.raw_headers.append((name, value))
    if entitled:
        resp.set_cookie("ogai_tier", _og_tiers.cookie_value("standard", PRO_TOKEN),
                        max_age=COOKIE_MAX_AGE, path="/", httponly=True, samesite="lax")
    return resp

def _stream_chat_response(agent_instance, uid: str, request: ChatRequest,
                          http_response: Response, entitled: bool,
                          meter: bool = True, tier: str = "free",
                          token_funded: bool = False):
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
        kwargs={"meter": meter, "tier": tier, "coords": request.coords,
                "image": request.image, "token_funded": token_funded},
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

def _chat_once(agent_instance, uid, request, tier, is_pro, token_funded):
    """One non-streaming /chat exchange, awaited via to_thread (Round 80)."""
    # DBPOOL batch-5: BOTH budgets (the Round 321c claim
    # budget and the new turn budget) begin AFTER the lock
    # is held — a turn queued behind a slow turn must not
    # start with its clocks already blown (the convoy fix;
    # nothing between the old begin site and the acquire
    # touched claims or stores, so the move changes only
    # when the clocks start). The turn budget's deadline is
    # what og_db's choke enforces on the turn's store work.
    _entry_t0 = time.monotonic()
    # DBPOOL batch-3: the whole-turn hold stays (shared agent),
    # but a long wait / long hold now logs while it happens.
    _lock_t0 = time.monotonic()
    _memory_lock.acquire()
    _lock_waited = time.monotonic() - _lock_t0
    if _lock_waited > _MEMORY_TURN_WARN_S:
        logger.warning(
            f"Chat turn waited {_lock_waited:.0f}s for the memory lock")
    _budget_started = _og_fixqueue.begin_claim_budget()
    _turn_started = _og_claimbudget.begin_turn_budget()
    _og_claimbudget.turn_marker("start", _lock_waited * 1000.0)
    _turn_outcome = "ok"
    _turn_t0 = time.monotonic()
    agent_instance.conversation_history = _load_hist(uid)
    _current_uid["uid"] = uid
    _current_tier["tier"] = tier
    _og_maps.note_request_coords(request.coords, uid)
    _og_files.note_attach_image(uid if request.image else None)
    try:
        # Check if agent has voice/learning capabilities
        has_voice = hasattr(agent_instance, 'voice') and agent_instance.voice is not None
        has_learning = hasattr(agent_instance, 'learning_system') and agent_instance.learning_system is not None

        # Process message with voice option if available
        _set_turn_leg("claims")
        _reply_t0 = time.monotonic()
        if has_voice:
            response = agent_instance.process_message(request.message.strip(), speak_response=request.speak_response)
        else:
            response = agent_instance.process_message(request.message.strip())
        _og_claimbudget.turn_marker(
            "reply", (time.monotonic() - _reply_t0) * 1000.0)

        # Get the latest assistant message from history
        history = agent_instance.get_conversation_history()
        latest_msg = history[-1] if history else None

        result = {
            "response": response,
            "agent_name": agent_instance.name,
            "timestamp": latest_msg['timestamp'] if latest_msg else ""
        }

        _bump_stat("messages")

        # Metering (estimate; recorded for every tier, R55).
        _set_turn_leg("metering")
        _tokens_used = _estimate_call_tokens(agent_instance, response) \
            + _drain_lookup_tokens()
        _record_chat_usage(uid, _tokens_used)
        result["token_balance"] = _og_tokenpacks.settle_chat(
            uid, token_funded, _tokens_used)
        if not is_pro:
            _bump_stat("tokens", _tokens_used)
            result["free_tokens_left"] = _free_tokens_remaining(uid)

        # Add intelligence info if learning is enabled
        if has_learning:
            report = agent_instance.learning_system.get_intelligence_report()
            result["intelligence"] = report.get("intelligence_level", 1.0)

        # Round 26: per-sentence register enforcement on the FINAL
        # reply (og_register); the enforced text is also what's
        # saved into the visitor's history below.
        result["response"] = _og_register.enforce_reply(response)
        _hist = agent_instance.conversation_history
        if _hist and _hist[-1].get("role") == "assistant":
            _hist[-1]["content"] = result["response"]

        return result
    except Exception as e:
        _turn_outcome = "error"
        _note_chat_error(_turn_leg["leg"], e)
        detail = f"An error occurred while processing your message: {str(e)}" if DEVELOPMENT_MODE else "An error occurred while processing your message"
        raise HTTPException(status_code=500, detail=detail)
    finally:
        # Save this visitor's thread back (even on error, so what they said
        # is remembered), then hand the shared agent back.
        _current_uid["uid"] = ""
        _current_tier["tier"] = "free"
        _og_maps.note_request_coords(None, None)
        _og_files.note_attach_image(None)
        try:
            _save_turn_history(agent_instance, uid)
        finally:
            _og_claimbudget.turn_marker(
                "total", (time.monotonic() - _entry_t0) * 1000.0,
                outcome=_turn_outcome)
            _held = time.monotonic() - _turn_t0
            if _held > _MEMORY_TURN_WARN_S:
                logger.warning(
                    f"Chat turn held the memory lock {_held:.0f}s")
            _memory_lock.release()
            _og_claimbudget.end_turn_budget(_turn_started)
            _og_fixqueue.end_claim_budget(_budget_started)

@app.post("/chat", response_model=ChatResponse, response_model_exclude_none=True)
async def chat(request: ChatRequest, raw_request: Request, http_response: Response):
    """Send a message to the AI agent (weekly token pool, R55; {"stream": true} = SSE)."""
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # DBPOOL batch-6: markers bracket the agent wait so
    # prod logs prove WHERE a chat parks (entered without
    # ready = inside the agent wait; neither = middleware),
    # and the wait itself is bounded — a not-yet-published
    # agent answers a fast 503 instead of a ~271 s silence.
    logger.warning("chat route: entered, awaiting agent")
    try:
        agent_instance = await asyncio.to_thread(get_agent_bounded)
    except AgentNotReady:
        raise HTTPException(status_code=503, detail="agent_warming_up")
    logger.warning("chat route: agent ready")

    # Visitor identity: hand out an `ogai_uid` cookie to new visitors.
    uid = raw_request.cookies.get("ogai_uid")
    if not uid:
        uid = uuid.uuid4().hex
        http_response.set_cookie(
            "ogai_uid", uid,
            max_age=COOKIE_MAX_AGE, path="/", httponly=True, samesite="lax"
        )

    # Freemium gate (Round 7): paid tiers skip the token cap.
    # Round 135 (B2) + Round 321c: the preamble reads run OFF
    # the event loop and FAIL SOFT (leg "preamble", defaults).
    tier, _ent_tier, _pack_balance = "free", None, None
    _pool_hit = entitled = token_funded = False
    try:
        tier = await asyncio.to_thread(_tier_of, raw_request.cookies, uid)
        # R55 weekly pool; R56: pack balance takes over when dry.
        _tokens_used = await asyncio.to_thread(
            _week_used, uid, "token_days", "tokens")
        _pool_hit = _og_tiers.chat_pool_hit(tier, _tokens_used)
        _ent_tier = await asyncio.to_thread(_uid_entitlement_tier, uid)
        if _pool_hit:
            _pack_balance = await asyncio.to_thread(
                _og_tokenpacks.balance, uid)
        entitled = bool(_ent_tier) and not _og_tiers.valid_tier_cookie(
            raw_request.cookies.get("ogai_tier"), PRO_TOKEN)
        token_funded = bool(_pool_hit) and (_pack_balance or 0) > 0
    except Exception as e:
        _note_chat_error("preamble", e)
    is_pro = tier != "free"
    cap_payload = None
    if _pool_hit and not token_funded:
        await asyncio.to_thread(_bump_stat, "cap_hits")
        cap_payload = _og_tiers.weekly_chat_payload(tier, agent_instance.name)
    if cap_payload is not None:
        if request.stream:
            async def cap_events():
                yield f"event: chunk\ndata: {json.dumps({'text': cap_payload['response']})}\n\n"
                yield f"event: done\ndata: {json.dumps(cap_payload)}\n\n"
            return _sse_streaming_response(cap_events(), http_response, entitled)
        return cap_payload

    if request.stream:
        return _stream_chat_response(
            agent_instance, uid, request, http_response, entitled,
            meter=not is_pro, tier=tier, token_funded=token_funded)

    if entitled:
        http_response.set_cookie(
            "ogai_tier", _og_tiers.cookie_value(
                _ent_tier or "standard", PRO_TOKEN),
            max_age=COOKIE_MAX_AGE, path="/", httponly=True, samesite="lax"
        )

    return await asyncio.to_thread(_chat_once, agent_instance, uid, request, tier, is_pro, token_funded)

@app.post("/tts")
async def text_to_speech(raw_request: Request):
    """Chat reply -> spoken audio (OpenAI TTS). Needs OPENAI_API_KEY; without it, 503 and the page uses the device voice."""
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
    _voice = body.get("voice")
    voice = (_voice if isinstance(_voice, str) and _voice in TTS_VOICES
             else TTS_DEFAULT_VOICE)
    uid = raw_request.cookies.get("ogai_uid") or "anon"
    _tts_tier = await asyncio.to_thread(
        _tier_of, raw_request.cookies,
        uid if uid != "anon" else "")
    _tts_rate = _og_tokenpacks.RATES["tts"]
    _tts_token_funded = False
    _tts_used = await asyncio.to_thread(_week_used, f"tts:{uid}")
    if _og_tiers.weekly_hit(_tts_tier, "tts", _tts_used):
        if not await asyncio.to_thread(
                _og_tokenpacks.can_afford, uid, _tts_rate):
            raise HTTPException(status_code=429, detail=_og_tiers.weekly_tts_detail(_tts_tier))
        _tts_token_funded = True
    elif not await asyncio.to_thread(
            _consume_tts_call, uid,
            _og_tiers.weekly_cap(_tts_tier, "tts")):
        raise HTTPException(status_code=429, detail=_og_tiers.weekly_tts_detail(_tts_tier))
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": "tts-1", "voice": voice, "input": text},
            )
    except Exception as e:
        logger.warning(f"TTS request failed: {e}")
        raise HTTPException(status_code=502, detail="Voice service unavailable")
    if r.status_code != 200:
        logger.warning(f"TTS upstream status: {r.status_code}")
        raise HTTPException(status_code=502, detail="Voice service error")
    if _tts_token_funded:
        await asyncio.to_thread(
            _og_tokenpacks.debit, uid, _tts_rate, "tts")
    return Response(content=r.content, media_type="audio/mpeg")

@app.post("/image")
async def generate_image(raw_request: Request):
    """Generate ONE image for a visitor (Round 5); expected outcomes answer 200 JSON."""
    try:
        await asyncio.to_thread(get_agent_bounded)  # instantiate first:
        # its creation resets the file store (off-loop, Step 6)
    except AgentNotReady:
        raise HTTPException(status_code=503, detail="agent_warming_up")
    uid = raw_request.cookies.get("ogai_uid")
    fresh_uid = False
    if not uid:
        uid = uuid.uuid4().hex
        fresh_uid = True

    def _reply(payload, status=200):
        resp = JSONResponse(content=payload, status_code=status)
        if fresh_uid:
            resp.set_cookie(
                "ogai_uid", uid,
                max_age=COOKIE_MAX_AGE, path="/", httponly=True, samesite="lax"
            )
        return resp

    try:
        body = await raw_request.json()
    except Exception:
        return _reply({"ok": False, "response":
                       "Yo, that came through garbled — tell me what to draw."}, 400)
    raw_prompt = str(body.get("prompt", "")).strip()
    if not raw_prompt:
        return _reply({"ok": False, "response":
                       "Tell me what to draw, G."}, 400)
    prompt = _clean_image_prompt(raw_prompt)

    tier = await asyncio.to_thread(_request_tier, raw_request)
    # Weekly ceiling (R55): pictures are metered weekly only.
    cap_w = _og_tiers.weekly_cap(tier, "images") or 0
    _img_used = await asyncio.to_thread(
        _week_used, f"image:{uid}")
    left = max(0, cap_w - _img_used)
    img_token_funded = left <= 0 and await asyncio.to_thread(
        _og_tokenpacks.can_afford, uid,
        _og_tokenpacks.RATES["image"])
    if left <= 0 and not img_token_funded:
        return _reply({"ok": False, "capped": True, "weekly_capped": True,
                       "images_left": 0,
                       "response": _og_tiers.weekly_image_line(tier)})

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return _reply({"ok": False, "response": _IMAGE_DOWN_LINE,
                       "images_left": left})

    try:
        image_src, model_used = await _openai_generate_image(api_key, prompt)
    except Exception as e:
        logger.warning(f"Image generation failed: {e}")
        return _reply({"ok": False, "response": _IMAGE_DOWN_LINE,
                       "images_left": left})

    if img_token_funded:
        await asyncio.to_thread(
            _og_tokenpacks.debit, uid,
            _og_tokenpacks.RATES["image"], "image")
    else:
        await asyncio.to_thread(_consume_image, uid)
    left = 0 if img_token_funded else max(
        0, cap_w - await asyncio.to_thread(
            _week_used, f"image:{uid}"))
    caption = _IMAGE_CAPTIONS[len(prompt) % len(_IMAGE_CAPTIONS)]
    # Note the drawing in the visitor's thread (too big to store).
    def _note_image():
        now = datetime.now().isoformat()
        # DBPOOL batch-3: bounded wait — a skipped note is
        # logged and the image still ships; never hang the
        # image reply behind a slow turn.
        if not _memory_lock.acquire(timeout=_MEMORY_LOCK_WAIT_S):
            logger.warning("Image history note skipped: memory lock busy")
            return
        try:
            history = _load_hist(uid)
            history.append({"role": "user", "content": raw_prompt,
                            "timestamp": now})
            history.append({"role": "assistant",
                            "content": f"{caption}\n[🎨 OG drew an image: {prompt}]",
                            "timestamp": now})
            _save_visitor_history_locked(uid, history)
        finally:
            _memory_lock.release()
    try:
        await asyncio.to_thread(_note_image)
    except Exception as e:
        logger.warning(f"Could not note image in history: {e}")
    return _reply({"ok": True, "image": image_src, "model": model_used,
                   "prompt": prompt, "response": caption,
                   "images_left": left,
                   "token_balance": await asyncio.to_thread(
                       _og_tokenpacks.balance, uid)})

_og_files.bind_app({
    "get_agent": get_agent, "pro_url": _og_tiers.public_pro_url(),
    "cookie_max_age": COOKIE_MAX_AGE,
    "is_entitled": lambda uid, req: (
        req.cookies.get("ogai_pro") == PRO_TOKEN or _uid_is_entitled(uid)),
    "tier_of": lambda uid, req: _tier_of(req.cookies, uid),
    "load_usage": _load_usage_store, "save_usage": _save_usage_store,
    "usage_lock": _usage_lock, "memory_lock": _memory_lock,
    "load_history": _load_visitor_history_locked,
    "save_history": _save_visitor_history_locked})
_og_files.register_file_routes(app)

_og_tiers.bind({"pro_token": PRO_TOKEN, "pro_link": PRO_UPGRADE_URL,
                "webhook_enabled": WEBHOOK_ENABLED,
                "is_entitled": _uid_is_entitled, "bump_stat": _bump_stat,
                "load_store": _load_usage_store, "lock": _usage_lock,
                "save_store": _save_usage_store,
                "entitled_key": PRO_ENTITLED_KEY,
                "cookie_max_age": COOKIE_MAX_AGE})
_og_tiers.register_tier_routes(app)

import og_voice as _og_voice
_og_voice.bind_app({
    "get_agent": get_agent, "pro_url": _og_tiers.public_pro_url(),
    "cookie_max_age": COOKIE_MAX_AGE,
    "tier_of": lambda uid, req: _tier_of(req.cookies, uid),
    "get_api_key": lambda: os.getenv("OPENAI_API_KEY"),
    "load_usage": _load_usage_store, "save_usage": _save_usage_store,
    "usage_lock": _usage_lock})
_og_voice.register_voice_routes(app)

# Spotify connect (Round 10, dark): /auth/spotify* routes live in
# og_spotify.py.
_og_spotify.bind_app({"cookie_max_age": COOKIE_MAX_AGE})
_og_spotify.register_spotify_routes(app)

# GitHub connect (Round 14, dark): /auth/github* routes live in
# og_github.py (load_history = the approved-PR extraction reader).
_og_github.bind_app({"cookie_max_age": COOKIE_MAX_AGE,
                     "load_history": _load_visitor_history_locked})
_og_github.register_github_routes(app)

# Connect pack 2 (Round 15, dark): /auth/youtube*, /auth/discord*,
# /auth/twitch* and /auth/reddit* routes live in their modules.
_og_youtube.bind_app({"cookie_max_age": COOKIE_MAX_AGE})
_og_youtube.register_youtube_routes(app)
_og_discord.bind_app({"cookie_max_age": COOKIE_MAX_AGE})
_og_discord.register_discord_routes(app)
_og_twitch.bind_app({"cookie_max_age": COOKIE_MAX_AGE})
_og_twitch.register_twitch_routes(app)
_og_reddit.bind_app({"cookie_max_age": COOKIE_MAX_AGE})
_og_reddit.register_reddit_routes(app)

# Unity maker (Round 16, live): GET /unity/download/<id> lives in
# og_unity.py (load_history = the packaging extraction reader).
_og_unity.bind_app({"load_history": _load_visitor_history_locked})
_og_unity.register_unity_routes(app)

# Utilities pack (Round 17, live): routes live in og_utils.py.
_og_utils.register_utils_routes(app)

# Monitoring pack (Round 18): Plaid routes + watch poll loop.
_og_plaid.bind_app({"cookie_max_age": COOKIE_MAX_AGE})
_og_plaid.register_plaid_routes(app)
_og_monitor.register_monitor_routes(app)

# Online ordering (Round 19): counters on the usage store.
_og_ordering.bind_app({
    "load_usage": _load_usage_store, "save_usage": _save_usage_store,
    "usage_lock": _usage_lock,
    "get_tier": lambda: _current_tier.get("tier", "free")})

# Trading (Round 20, dark).
_og_trading.bind_app({"cookie_max_age": COOKIE_MAX_AGE,
    "load_usage": _load_usage_store, "save_usage": _save_usage_store,
    "usage_lock": _usage_lock,
    "get_tier": lambda: _current_tier.get("tier", "free")})
_og_trading.register_trading_routes(app)

# File locker (Round 21, dark).
_og_storage.bind_app({
    "get_tier": lambda: _current_tier.get("tier", "free"),
    "tier_of": lambda uid, req: _tier_of(req.cookies, uid),
    "get_api_key": lambda: os.getenv("OPENAI_API_KEY")})
_og_storage.register_storage_routes(app)

_og_browser.bind_app({"load_usage": _load_usage_store,
    "save_usage": _save_usage_store, "usage_lock": _usage_lock,
    "get_tier": lambda: _current_tier.get("tier", "free"),
    "tier_of": lambda uid, req: _tier_of(req.cookies, uid)})
_og_browser.register_browser_routes(app)
_og_video.bind_app({"load_usage": _load_usage_store,
    "save_usage": _save_usage_store, "usage_lock": _usage_lock,
    "get_tier": lambda: _current_tier.get("tier", "free"),
    "tier_of": lambda uid, req: _tier_of(req.cookies, uid),
    "get_api_key": lambda: os.getenv("OPENAI_API_KEY"),
    "load_history": _load_visitor_history_locked})
_og_video.register_video_routes(app)
_og_watch.bind_app({"tier_of": lambda uid, req: _tier_of(req.cookies, uid)})
_og_watch.register_watch_routes(app)
_og_songs.bind_app({"load_usage": _load_usage_store,
    "save_usage": _save_usage_store, "usage_lock": _usage_lock,
    "get_tier": lambda: _current_tier.get("tier", "free"),
    "tier_of": lambda uid, req: _tier_of(req.cookies, uid),
    "load_history": _load_visitor_history_locked})
_og_songs.register_song_routes(app)
_og_artifacts.bind_app({"get_tier": lambda: _current_tier.get("tier", "free")})
_og_artifacts.register_artifact_routes(app)
_og_avatar.register_avatar_routes(app)

# Avatar identity (Ticket E6): the selfie avatar + rename
# live in og_avatar_identity.py (bind + register only here).
import og_avatar_identity as _og_avatarid
_og_avatarid.bind_app({
    "load_usage": _load_usage_store, "save_usage": _save_usage_store,
    "usage_lock": _usage_lock,
    "tier_of": lambda uid, req: _tier_of(req.cookies, uid),
    "get_api_key": lambda: os.getenv("OPENAI_API_KEY")})
_og_avatarid.register_avatar_identity_routes(app)

# OG performs (Ticket E7): the idle rap + the roast song
# live in og_perform.py (bind + register only here).
import og_perform as _og_perform
_og_perform.bind_app({
    "get_api_key": lambda: os.getenv("OPENAI_API_KEY")})
_og_perform.register_perform_routes(app)

# Menu order (Ticket N2): the menu work board's grouped
# read lives in og_workboard.py (register only here — it
# composes og_progress's rows and owns no state).
import og_workboard as _og_workboard
_og_workboard.register_workboard_routes(app)
_og_legal.register_legal_routes(app)
_og_accounts.register_account_routes(app)
_og_accounts.register_attach_hook(_og_tiers.migrate_entitlement)
_og_accounts.register_attach_hook(_og_tiers.reconcile_entitlement_hook)
_og_notify.register_notify_routes(app)
_og_library.register_library_routes(app)
_ogs.register_store_routes(app)
_og_selfcheck.bind_app({"load_usage": _load_usage_store, "save_usage": _save_usage_store, "usage_lock": _usage_lock})
_og_selfcheck.register_selfcheck_routes(app)
_og_fixqueue.register_fixqueue_routes(app)
_og_lessons.bind_app({"load_usage": _load_usage_store, "save_usage": _save_usage_store, "usage_lock": _usage_lock})
_og_lessons.register_lessons_routes(app)
_og_tokenpacks.bind_app({"load_usage": _load_usage_store, "save_usage": _save_usage_store, "usage_lock": _usage_lock, "tier_of": lambda uid, req: _tier_of(req.cookies, uid)})  # R56
_og_tokenpacks.register_token_routes(app)

@app.post("/stripe/webhook")
async def stripe_webhook(raw_request: Request):
    """Stripe webhook: checkout.session.completed -> entitlement grant or token-pack credit (dark unless enabled + secret set)."""
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
        _bought_tier = ((session.get("metadata") or {}).get("tier") or "")
        if _og_tokenpacks.session_is_pack(session):
            _og_tokenpacks.credit_from_session(session, event.get("id", ""), uid)
        elif uid:
            _grant_entitlement(uid, session.get("id", ""), email,
                                _bought_tier)
            logger.info("Pro entitlement granted via Stripe webhook")
        else:
            logger.warning(
                "Stripe checkout completed without client_reference_id; "
                "no entitlement granted")
    return {"received": True}

@app.get("/stats", response_class=HTMLResponse)
def stats_page(key: str = ""):
    """Owner-only stats page (token-gated)."""
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
# Round 321 (Step 3.1): def, not async def — the body is
# sync store work; FastAPI runs def handlers in its
# threadpool, so a stalled DB can never freeze the loop.
def get_history(raw_request: Request):
    """The requesting visitor's own conversation history (ogai_uid cookie)."""
    # DBPOOL batch-4: store init WITHOUT building the agent
    # (was get_agent(), whose only job here was the fresh
    # store) — a cold worker now answers /history from the
    # durable store instead of queueing behind the build.
    _ensure_memory_store()
    try:
        uid = raw_request.cookies.get("ogai_uid")
        history: List[Dict] = []
        if uid:
            # DBPOOL batch-3: bounded wait — a parked turn
            # must not hang this read forever; answer 503.
            if not _memory_lock.acquire(timeout=_MEMORY_LOCK_WAIT_S):
                logger.warning("GET /history: memory lock busy; answering 503")
                raise HTTPException(status_code=503,
                                    detail="Busy — try again in a moment")
            try:
                history = _load_hist(uid)
            finally:
                _memory_lock.release()
        tier = _tier_of(raw_request.cookies, uid or "")
        if tier != "free":
            tokens_left = None
        elif uid:
            tokens_left = _free_tokens_remaining(uid)
        else:
            tokens_left = _og_tiers.weekly_chat_tokens("free") or 25000
        return {
            "conversation": history,
            "history": history,  # Backward compatibility with Flask API
            "message_count": len(history),
            "free_tokens_left": tokens_left,
            "tier": tier,
            "token_balance": _og_tokenpacks.balance(uid) if uid else 0
        }
    except HTTPException:
        raise  # DBPOOL batch-3: the 503 above stays a 503
    except Exception as e:
        logger.error(f"Error retrieving history: {str(e)}")
        detail = f"An error occurred while retrieving conversation history: {str(e)}" if DEVELOPMENT_MODE else "An error occurred while retrieving conversation history"
        raise HTTPException(status_code=500, detail=detail)

@app.post("/reset", response_model=StatusResponse)
def reset_conversation(raw_request: Request):
    """
    Clear the requesting visitor's conversation history — only theirs.

    Returns:
        StatusResponse confirming the reset
    """
    # DBPOOL batch-4: never build for a reset, and never
    # queue behind a build either — the durable clear below
    # is the real work; the in-memory clear applies only
    # when an agent already exists (with none built there
    # is no in-memory history to clear, and no turn can be
    # in flight, so skipping the section loses nothing).
    agent_instance = agent_if_built()

    try:
        uid = raw_request.cookies.get("ogai_uid")
        if uid:
            # DBPOOL batch-3: bounded waits, 503 when busy —
            # a reset must never queue behind a parked turn.
            if not _clear_visitor_history(uid):
                raise HTTPException(status_code=503,
                                    detail="Busy — try again in a moment")
        if agent_instance is not None:
            if not _memory_lock.acquire(timeout=_MEMORY_LOCK_WAIT_S):
                logger.warning("POST /reset: memory lock busy; answering 503")
                raise HTTPException(status_code=503,
                                    detail="Busy — try again in a moment")
            try:
                agent_instance.clear_history()
            finally:
                _memory_lock.release()
        return {
            "status": "success",
            "agent_name": agent_instance.name
                if agent_instance is not None else "OG-AI",
            "message": "Conversation history has been cleared"
        }
    except HTTPException:
        raise  # DBPOOL batch-3: the 503s above stay 503s
    except Exception as e:
        logger.error(f"Error resetting conversation: {str(e)}")
        detail = f"An error occurred while resetting conversation: {str(e)}" if DEVELOPMENT_MODE else "An error occurred while resetting conversation"
        raise HTTPException(status_code=500, detail=detail)

@app.get("/intelligence")
def get_intelligence():
    """
    Get the agent's intelligence report (self-learning stats).

    Returns:
        Intelligence report with learning statistics
    """
    try:
        agent_instance = get_agent_bounded()
    except AgentNotReady:
        raise HTTPException(status_code=503, detail="agent_warming_up")

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
            "message": str(e) if DEVELOPMENT_MODE else "Report failed",
            "intelligence_level": 1.0
        }

@app.post("/improve")
def manual_improvement():
    """
    Manually trigger daily self-improvement routine.

    Returns:
        Improvement report
    """
    try:
        agent_instance = get_agent_bounded()
    except AgentNotReady:
        raise HTTPException(status_code=503, detail="agent_warming_up")

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
        raise HTTPException(status_code=500, detail=str(e) if DEVELOPMENT_MODE else "Improvement failed")

@app.post("/clear", response_model=StatusResponse)
async def clear_history(raw_request: Request):
    """Clear the conversation history (backward-compat alias for /reset)."""
    # Round 321 Step 6: /reset is a def handler now (threadpool);
    # the alias hops to a thread to call it, never awaiting it
    # and never running its store work on this loop.
    return await asyncio.to_thread(reset_conversation, raw_request)

# --- Google account connect routes (Round 3, dark until enabled) ---

@app.get("/auth/google")
def google_auth_start(raw_request: Request):
    """Begin Google connect: bounce the visitor to Google's consent page. 404 while the feature is dark."""
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
        # Round 12: scope string from og_google_hands (hands-gated).
        "scope": _og_google_hands.requested_scope(),
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
    """Google OAuth callback: exchange the code, store tokens under the visitor uid."""
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
        # Round 12: granted-scope record (hands checks v1 vs hands).
        "scope": tokens.get("scope", ""),
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
# Round 321 (Step 3.1): def, not async def — the body is
# sync store work; FastAPI runs def handlers in its
# threadpool, so a stalled DB can never freeze the loop.
def google_auth_status(raw_request: Request):
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
def google_auth_disconnect(raw_request: Request):
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
