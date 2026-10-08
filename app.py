"""
FastAPI Web Service for OG-AI Agent
Exposes REST API endpoints for interacting with the AI agent.
"""

import json
import os
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Optional
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse
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

# --- OG Pro (v1 money layer) -------------------------------------------------
# Free tier: each visitor (tracked by an `ogai_uid` cookie) gets a limited
# number of chat messages per UTC day. Pro visitors (holding a valid
# `ogai_pro` cookie) chat unlimited. See /pro and /pro/success below.
FREE_DAILY_LIMIT = int(os.getenv("OG_FREE_DAILY_LIMIT", "10"))
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


def _consume_free_message(uid: str) -> bool:
    """
    Record one chat message for this visitor today (UTC).

    Returns False when the visitor has already hit the free daily cap
    (the message is NOT recorded in that case).
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _usage_lock:
        store = _load_usage_store()
        entry = store.get(uid)
        if not isinstance(entry, dict) or entry.get("date") != today:
            entry = {"date": today, "count": 0}
        if entry["count"] >= FREE_DAILY_LIMIT:
            return False
        entry["count"] += 1
        store[uid] = entry
        _save_usage_store(store)
        return True


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


def _reset_memory_store():
    """Start the memory store empty (called when a fresh agent is created).

    A new agent instance is a new OG: it should not inherit a previous
    instance's visitor threads while its own in-memory state starts blank.
    This gives the store the same lifecycle as the usage counters and stats
    in usage_store.json — they live for the service's life and reset on a
    from-scratch rebuild.
    """
    with _memory_lock:
        _save_memory_store({})


def _load_memory_store() -> Dict:
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
_STAT_FIELDS = ("messages", "cap_hits", "pro_clicks", "pro_success")


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
        if key == STATS_KEY or key.startswith("tts:") or not isinstance(entry, dict):
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
        _reset_memory_store()

    return agent


# Pydantic models for request/response
class ChatRequest(BaseModel):
    message: str
    speak_response: bool = False
    
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


@app.post("/chat", response_model=ChatResponse, response_model_exclude_none=True)
async def chat(request: ChatRequest, raw_request: Request, http_response: Response):
    """
    Send a message to the AI agent and receive a response.

    Free visitors get FREE_DAILY_LIMIT messages per UTC day (tracked by an
    `ogai_uid` cookie); Pro visitors (valid `ogai_pro` cookie) are unlimited.
    A capped visitor still gets HTTP 200 with an in-persona reply pointing
    at the upgrade URL.

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

    # Freemium gate: Pro cookie holders skip the daily cap entirely.
    if raw_request.cookies.get("ogai_pro") != PRO_TOKEN:
        if not _consume_free_message(uid):
            _bump_stat("cap_hits")
            return {
                "response": (
                    f"Yo, real talk — you're outta free messages for today "
                    f"({FREE_DAILY_LIMIT} a day on the free plan), and the OG don't work for free forever. "
                    f"Go Pro for unlimited: {PRO_UPGRADE_URL} — or slide back tomorrow when your freebies reset."
                ),
                "agent_name": agent_instance.name,
                "timestamp": datetime.now().isoformat(),
                "upgrade_url": PRO_UPGRADE_URL
            }

    # Per-visitor memory: swap this visitor's own thread into the shared
    # agent and hold the memory lock until it is saved back below, so two
    # visitors chatting at once can never interleave each other's history.
    _memory_lock.acquire()
    agent_instance.conversation_history = _load_visitor_history_locked(uid)
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
async def pro_upgrade():
    """
    Send visitors to the Pro checkout.

    The destination is the Stripe payment link configured via OG_PRO_LINK;
    Stripe should be set to redirect buyers to /pro/success after checkout.
    """
    _bump_stat("pro_clicks")
    return RedirectResponse(url=PRO_UPGRADE_URL, status_code=302)


@app.get("/pro/success")
async def pro_success():
    """
    Pro unlock landing page (v1 entitlement model).

    v1 is deliberately simple: the Stripe payment link redirects buyers here
    after checkout and we just set the `ogai_pro` cookie, which lifts the
    daily cap. Anyone who reaches this URL gets Pro, so the v2 upgrade is
    to verify payment first (Stripe webhook or a signed/email-verified link)
    before granting the cookie.
    """
    _bump_stat("pro_success")
    response = RedirectResponse(url="/", status_code=302)
    response.set_cookie(
        "ogai_pro", PRO_TOKEN,
        max_age=COOKIE_MAX_AGE, path="/", httponly=True, samesite="lax"
    )
    return response



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
  {card("Hit the free cap", n(day, "cap_hits"), "ran out of freebies")}
  {card("Pro clicks", n(day, "pro_clicks"), "went to checkout")}
  {card("Pro signups", n(day, "pro_success"), "landed after payment")}
</div>
<h2>All time</h2>
<div class="grid">
  {card("Visitors", total_visitors)}
  {card("Messages answered", n(stats, "messages"))}
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
        return {
            "conversation": history,
            "history": history,  # Backward compatibility with Flask API
            "message_count": len(history)
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
