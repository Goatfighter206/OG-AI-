"""
OG Discord connect (Round 15) — ships DARK, exactly like Round
3's Google connect, Round 10's Spotify connect and Round 14's
GitHub connect.

A visitor can connect their Discord account to OG ("Connect
Discord" in the slide-over menu). OAuth 2.0, the standard way: OG
never sees or stores a password — Discord itself confirms who they
are and hands back tokens, stored per visitor (keyed by ogai_uid)
the same way the other connects' tokens are: in Postgres (table
og_discord_tokens) when OG_MEMORY_DB_URL is set, else a JSON file
(discord_store.json) beside the other stores. Tokens are never
logged and never shared across visitors; a visitor's Discord data
is only ever read with that visitor's own token.

Scopes: identify (who connected) + guilds (the servers they are
a member of). READ-ONLY v1, deliberately: the OAuth2 user flow
can read the visitor's own profile and server list, and nothing
more — OG cannot read or send messages with it. Messaging would
need a Discord BOT inside each server (a different product: bot
token, server-admin invites, message-content privileged intent);
documented as out of scope in the Round 15 report.

Discord access tokens are short-lived and come with a refresh
token; the module refreshes quietly (sync, in the chat seam) and
a dead grant (invalid_grant / 401) drops the connection
gracefully to a reconnect prompt, never an error page.

Ships DISABLED: the menu items stay hidden, /auth/discord
answers 404 and the status route reports enabled:false until
OG_DISCORD_ENABLED=true plus OG_DISCORD_CLIENT_ID /
OG_DISCORD_CLIENT_SECRET are set (steps in the Round 15 report).
While disabled — or while a visitor simply hasn't connected —
the chat capability below is fully inert and chat behaves
exactly as it did before this round.

Capabilities (enabled + connected only), wired through the same
app-layer seam as Rounds 3/4/6/9/10/12/13/14 — install_discord_
tools wraps the agent's detect_intent + web_search hooks. Every
real answer costs 1 unit of the shared per-tier lookup budget;
guidance and misses cost 0:

- "what servers am I in" / "my Discord servers" — their guilds
  (≤10), grounded, with member counts when Discord provides
  them (with_counts).
- "my Discord username" — their connected identity, grounded in
  /users/@me (refreshed live, not just the connect-time copy).

Persona files are never touched.
"""

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)

# --- Config (mirrors the connect blocks in og_spotify/og_github) ------------

DISCORD_CLIENT_ID = os.getenv("OG_DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("OG_DISCORD_CLIENT_" + "SECRET", "")
DISCORD_ENABLED = (os.getenv("OG_DISCORD_ENABLED", "false").lower() == "true"
                   and bool(DISCORD_CLIENT_ID) and bool(DISCORD_CLIENT_SECRET))
DISCORD_REDIRECT_URI = os.getenv(
    "OG_DISCORD_REDIRECT_URI",
    "https://og-ai-service.onrender.com/auth/discord/callback")
DISCORD_SCOPES = "identify guilds"
DISCORD_STORE_FILE = "discord_store.json"
_discord_lock = threading.Lock()

_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
_TOKEN_URL = "https://discord.com/api/oauth2/token"
_API_BASE = "https://discord.com/api/v10"

_MAX_GUILDS = 10

# Durable backend (opt-in, same rule as the other stores): when
# OG_MEMORY_DB_URL points at a Postgres database the tokens live
# there; psycopg missing simply means the JSON file backend is used.
MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg not installed — DB backend unavailable
    psycopg = None
    _Jsonb = None

# Bound by app.py via bind_app (values it owns, e.g. the cookie age).
_deps: Dict = {}


def bind_app(deps):
    _deps.update(deps)


def _cookie_max_age() -> int:
    return int(_deps.get("cookie_max_age", 365 * 24 * 60 * 60))


# --- Token store (mirrors the GitHub store in og_github.py) ------------------

def _discord_db_connect():
    """Connect to the durable DB, creating the Discord tokens table."""
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_discord_tokens ("
            "uid TEXT PRIMARY KEY, data JSONB)"
        )
    conn.commit()
    return conn


def _load_discord_store() -> Dict:
    """Load all Discord connections (durable DB when configured,
    otherwise the JSON discord store)."""
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _discord_db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT uid, data FROM og_discord_tokens")
                    return {uid: data for uid, data in cur.fetchall()}
        except Exception as e:
            logger.warning(f"Discord store DB load failed, using file: {e}")
    if os.path.exists(DISCORD_STORE_FILE):
        try:
            with open(DISCORD_STORE_FILE, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Could not load discord store: {e}")
    return {}


def _save_discord_store(store: Dict):
    """Save all Discord connections (durable DB when configured,
    otherwise the JSON discord store)."""
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _discord_db_connect() as conn:
                with conn.cursor() as cur:
                    for uid, data in store.items():
                        cur.execute(
                            "INSERT INTO og_discord_tokens (uid, data) "
                            "VALUES (%s, %s) ON CONFLICT (uid) DO UPDATE "
                            "SET data = EXCLUDED.data",
                            (uid, _Jsonb(data)),
                        )
                    cur.execute("SELECT uid FROM og_discord_tokens")
                    existing = {row[0] for row in cur.fetchall()}
                    for stale in existing - set(store.keys()):
                        cur.execute(
                            "DELETE FROM og_discord_tokens WHERE uid = %s",
                            (stale,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Discord store DB save failed, using file: {e}")
    try:
        with open(DISCORD_STORE_FILE, 'w') as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Could not save discord store: {e}")


def _discord_connection(uid: str) -> Optional[Dict]:
    """This visitor's stored Discord connection (profile + tokens)."""
    if not uid:
        return None
    with _discord_lock:
        store = _load_discord_store()
    entry = store.get(uid)
    return entry if isinstance(entry, dict) else None


def _store_entry(uid: str, entry: Dict):
    with _discord_lock:
        store = _load_discord_store()
        store[uid] = entry
        _save_discord_store(store)


def _drop_entry(uid: str):
    with _discord_lock:
        store = _load_discord_store()
        if uid in store:
            del store[uid]
            _save_discord_store(store)


# --- Signed OAuth state (same construction as the other flows) ---------------

def _discord_state_for(uid: str) -> str:
    """Signed OAuth state tying the connect flow to one visitor uid."""
    sig = hmac.new(DISCORD_CLIENT_SECRET.encode(),
                   f"og-discord:{uid}".encode(), hashlib.sha256).hexdigest()
    return f"{uid}.{sig}"


def _discord_uid_from_state(state: str) -> Optional[str]:
    """Recover the visitor uid from a state value we signed, else None."""
    try:
        uid, _, sig = str(state).partition(".")
        if not uid or not sig:
            return None
        expected = hmac.new(DISCORD_CLIENT_SECRET.encode(),
                            f"og-discord:{uid}".encode(),
                            hashlib.sha256).hexdigest()
        return uid if hmac.compare_digest(expected, sig) else None
    except Exception:
        return None


# --- Discord API (sync; used by the chat seam) ---------------------------------
# Every API call rides this one helper so the surface stays auditable
# (and unit-testable). Tokens are never logged — statuses only.

def _request(method: str, url: str, token: str = "",
             params: Optional[Dict] = None, form: Optional[Dict] = None):
    """One call against Discord. Returns (status, data): data is
    parsed JSON (dict/list) or None; status is None on a transport
    failure."""
    import httpx
    headers = {"User-Agent": "OG-AI"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.request(method, url, headers=headers,
                                  params=params, data=form)
    except Exception as e:
        logger.warning(f"Discord API {method} {url} failed: {e}")
        return None, None
    try:
        data = resp.json()
    except Exception:
        data = None
    return resp.status_code, data


def _refresh_entry(uid: str, entry: Dict) -> Optional[Dict]:
    """Refresh this visitor's access token (sync; the chat seam is
    sync). Returns the updated entry, or None when no refresh is
    possible. A 400 from Discord means the grant itself is dead
    (revoked / expired) — the stored connection is dropped so the
    status route honestly shows disconnected; a network failure
    keeps the connection and just yields no data this time."""
    refresh = entry.get("refresh_token", "")
    if not refresh:
        return None
    status, tokens = _request("POST", _TOKEN_URL, form={
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
    })
    if status != 200 or not isinstance(tokens, dict):
        logger.warning(f"Discord token refresh status: {status}")
        if status == 400:
            _drop_entry(uid)
        return None
    entry = dict(entry)
    entry["access_token"] = tokens.get("access_token",
                                        entry.get("access_token", ""))
    if tokens.get("refresh_token"):
        entry["refresh_token"] = tokens["refresh_token"]
    entry["expires_at"] = (datetime.now(timezone.utc).timestamp()
                           + int(tokens.get("expires_in", 3600)))
    _store_entry(uid, entry)
    return entry


def _live_connection(uid: str) -> Optional[Dict]:
    """This visitor's connection with a non-expired access token
    (refreshing when needed), or None."""
    entry = _discord_connection(uid)
    if not entry:
        return None
    now = datetime.now(timezone.utc).timestamp()
    if entry.get("access_token") and \
            float(entry.get("expires_at", 0)) > now + 60:
        return entry
    return _refresh_entry(uid, entry)


# --- Small helpers -------------------------------------------------------------

def _spend(consume_lookup, uid: str) -> bool:
    if consume_lookup is None:
        return True
    try:
        return bool(consume_lookup(uid))
    except Exception as e:
        logger.warning(f"Discord budget consume failed: {e}")
        return False


# --- Guidance results (never spend budget) -------------------------------------

def _guidance(kind: str, who: str) -> list:
    if kind == "reconnect":
        body = (f"The visitor ({who}) had their Discord account "
                "connected to OG, but Discord is now refusing the "
                "connection (it was revoked or cut off on Discord's "
                "side). Do NOT invent any servers or usernames. "
                "Tell them, in persona, that the connection "
                "dropped and they need to tap Connect Discord once "
                "more (slide-over menu), then ask again. Keep it "
                "short and helpful.")
        title = "🎮 Discord reconnect needed"
    else:
        body = ("The visitor is asking about their OWN Discord "
                "account, but they have NOT connected Discord to "
                "OG. You have no access to their servers. Do NOT "
                "invent any servers or usernames. Tell them, in "
                "persona, to connect Discord first — the Connect "
                "Discord button in the slide-over menu — and then "
                "ask again. Keep it short and helpful.")
        title = "🎮 Discord not connected"
    return [{"title": title, "body": body, "href": "/auth/discord"}]


def _revoked(uid: str, who: str) -> list:
    """The API just told us this visitor's token is dead (401):
    drop the stored connection and answer with the reconnect
    prompt — graceful, never an error."""
    _drop_entry(uid)
    return _guidance("reconnect", who)


# --- Chat intent parsing -------------------------------------------------------
# Deliberately first-person and service-named: every pattern is
# about the VISITOR's own Discord. General Discord questions
# ("how do I make a discord bot", "is discord down") match
# nothing here and keep flowing to the normal lookup/chat paths
# exactly as before.

_SERVERS_RES = (
    r"\bmy (discord )?(servers|guilds)\b",
    r"\bwhat (discord )?servers am i in\b",
    r"\bwhat servers am i (in|on)\b",
    r"\bdiscord servers i('m| am) (in|on|part of)\b",
    r"\bservers i('m| am) (in|on) on discord\b",
)
_USERNAME_RES = (
    r"\bmy discord (username|name|handle|tag)\b",
    r"\bwhat'?s my discord (username|name|handle|tag)?\b",
    r"\bwho am i on discord\b",
)


def parse_discord_intent(message: str) -> Optional[Dict]:
    """Parse a first-person Discord job from the raw message.
    Returns {'kind': 'servers'|'username'}, or None when the
    message isn't about the visitor's own Discord."""
    low = " " + re.sub(r"\s+", " ", str(message).lower()).strip() + " "
    for pattern in _SERVERS_RES:
        if re.search(pattern, low):
            return {"kind": "servers"}
    for pattern in _USERNAME_RES:
        if re.search(pattern, low):
            return {"kind": "username"}
    return None


# --- Grounded answers ------------------------------------------------------------

def _servers_answer(entry: Dict, uid: str, consume_lookup) -> Optional[list]:
    token = entry.get("access_token", "")
    who = entry.get("username") or entry.get("name") or "the visitor"
    status, data = _request(
        "GET", _API_BASE + "/users/@me/guilds", token,
        params={"with_counts": "true", "limit": _MAX_GUILDS})
    if status == 401:
        return _revoked(uid, who)
    if status != 200 or not isinstance(data, list):
        return None
    if not _spend(consume_lookup, uid):
        logger.info("Discord servers answer skipped: visitor at "
                    "daily lookup cap")
        return None
    if not data:
        body = (f"The visitor ({who}) connected their Discord "
                "account and is asking what servers they are in. "
                "Their Discord account is in NO servers visible "
                "to OG. Tell them plainly, in persona — do not "
                "invent servers.")
        return [{"title": "🎮 The visitor's Discord — no servers",
                 "body": body, "href": "https://discord.com"}]
    lines = []
    for guild in data[:_MAX_GUILDS]:
        if not isinstance(guild, dict) or not guild.get("name"):
            continue
        count = guild.get("approximate_member_count")
        line = f"- {guild['name']}"
        if isinstance(count, int):
            line += f" (~{count} members)"
        if guild.get("owner"):
            line += " (they own this one)"
        lines.append(line)
    if not lines:
        return None
    body = (f"The visitor ({who}) connected their Discord account "
            "and is asking what servers they are in. Answer ONLY "
            "from this list — their actual Discord servers:\n\n"
            + "\n".join(lines))
    return [{"title": "🎮 The visitor's Discord servers",
             "body": body, "href": "https://discord.com"}]


def _username_answer(entry: Dict, uid: str, consume_lookup) -> Optional[list]:
    token = entry.get("access_token", "")
    who = entry.get("username") or entry.get("name") or "the visitor"
    status, data = _request("GET", _API_BASE + "/users/@me", token)
    if status == 401:
        return _revoked(uid, who)
    if status != 200 or not isinstance(data, dict):
        return None
    if not _spend(consume_lookup, uid):
        logger.info("Discord username answer skipped: visitor at "
                    "daily lookup cap")
        return None
    username = data.get("username", "")
    display = data.get("global_name") or ""
    if not username:
        return None
    line = f"Discord username: {username}"
    if display and display != username:
        line += f" (display name: {display})"
    body = (f"The visitor ({who}) connected their Discord account "
            "and is asking their Discord username. Answer ONLY "
            f"from this: {line}.")
    return [{"title": "🎮 The visitor's Discord username",
             "body": body, "href": "https://discord.com"}]


def discord_results(job, uid, consume_lookup):
    """Run one parsed Discord job for this visitor. Returns
    web_search-shaped results on a hit, guidance for enabled-but-
    unconnected/revoked visitors, or None on any miss — disabled,
    upstream down, or budget spent — so the caller falls through
    to the previous search untouched. One unit of the shared
    lookup budget is consumed per REAL answer; a miss or guidance
    consumes nothing."""
    if not job or not DISCORD_ENABLED or not uid:
        return None
    kind = job.get("kind")
    raw = _discord_connection(uid)
    if not raw:
        return _guidance("connect", "the visitor")
    who_hint = raw.get("username") or raw.get("name") or "the visitor"
    entry = _live_connection(uid)
    if not entry:
        return _guidance("reconnect", who_hint)
    if kind == "servers":
        return _servers_answer(entry, uid, consume_lookup)
    if kind == "username":
        return _username_answer(entry, uid, consume_lookup)
    return None


# ---------------------------------------------------------------------------
# The seam (app.py installs this on the agent, after Rounds 3/4/6/9/10/12/13/14)
# ---------------------------------------------------------------------------

# The Discord job parsed for the exchange currently being processed.
# All chat processing is serialized under app.py's _memory_lock, so
# a single slot is safe — the same reasoning as the other modules.
_pending = {"job": None}


def install_discord_tools(agent_instance, get_uid, consume_lookup):
    """Wrap the agent's (already lookup/file/maps/spotify/github/
    hands-wrapped) detect_intent + web_search hooks so a connected
    visitor's Discord questions try og_discord FIRST and fall
    through to the previous search on a miss. get_uid is a zero-arg
    callable returning the current visitor's uid; consume_lookup(uid)
    spends one unit of the shared Round 3 lookup budget and returns
    False at the cap. While the feature is disabled the wrapper is
    a pure pass-through — nothing is parsed, forced or spent.
    Persona files never touched."""
    if getattr(agent_instance, "_og_discord_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["job"] = None
        if DISCORD_ENABLED:
            try:
                uid = get_uid()
                job = parse_discord_intent(str(message)) if uid else None
                if job:
                    _pending["job"] = job
                    if isinstance(intent, dict) \
                            and not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
            except Exception as e:
                logger.warning(f"Discord trigger check failed: {e}")
                _pending["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _pending.get("job")
        _pending["job"] = None
        if job:
            try:
                results = discord_results(job, get_uid(), consume_lookup)
            except Exception as e:
                logger.warning(f"Discord search failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_discord_installed = True


# --- Discord account connect routes (dark until enabled) ---------------------

def register_discord_routes(app):
    """Attach the four /auth/discord* routes to the FastAPI app.
    Mirrors the Round 10/14 routes one for one."""

    @app.get("/auth/discord")
    async def discord_auth_start(raw_request: Request):
        """
        Begin Discord connect: bounce the visitor to Discord's own
        consent page. Answers 404 while the feature is dark (keys
        not set), so nothing about it is discoverable on the live
        site until Brent enables it.
        """
        if not DISCORD_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        from urllib.parse import urlencode
        uid = raw_request.cookies.get("ogai_uid")
        fresh_uid = None
        if not uid:
            uid = uuid.uuid4().hex
            fresh_uid = uid
        params = {
            "client_id": DISCORD_CLIENT_ID,
            "redirect_uri": DISCORD_REDIRECT_URI,
            "response_type": "code",
            "scope": DISCORD_SCOPES,
            "state": _discord_state_for(uid),
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

    @app.get("/auth/discord/callback")
    async def discord_auth_callback(raw_request: Request, code: str = "",
                                    state: str = "", error: str = ""):
        """
        Discord sends the visitor back here with a code. The signed
        state tells us which visitor this is; the code is exchanged
        for tokens, the profile is fetched, and the connection is
        stored under their uid. Any failure lands back on the chat
        with ?discord=failed — no error page.
        """
        if not DISCORD_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        uid = _discord_uid_from_state(state)
        if error or not code or not uid:
            return RedirectResponse(url="/?discord=failed", status_code=302)
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                token_resp = await client.post(
                    _TOKEN_URL,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": DISCORD_REDIRECT_URI,
                        "client_id": DISCORD_CLIENT_ID,
                        "client_secret": DISCORD_CLIENT_SECRET,
                    })
                if token_resp.status_code != 200:
                    logger.warning(
                        "Discord token exchange status: "
                        f"{token_resp.status_code}")
                    return RedirectResponse(url="/?discord=failed",
                                            status_code=302)
                tokens = token_resp.json()
                access_token = tokens.get("access_token", "")
                if not access_token:
                    logger.warning("Discord token exchange: no token")
                    return RedirectResponse(url="/?discord=failed",
                                            status_code=302)
                info_resp = await client.get(
                    _API_BASE + "/users/@me",
                    headers={"Authorization": f"Bearer {access_token}"})
                profile = info_resp.json() \
                    if info_resp.status_code == 200 else {}
        except Exception as e:
            logger.warning(f"Discord connect failed: {e}")
            return RedirectResponse(url="/?discord=failed", status_code=302)
        entry = {
            "discord_id": profile.get("id", ""),
            "username": profile.get("username", ""),
            "name": profile.get("global_name", "") or
                    profile.get("username", ""),
            "access_token": access_token,
            "refresh_token": tokens.get("refresh_token", ""),
            "scope": tokens.get("scope", ""),
            "expires_at": (datetime.now(timezone.utc).timestamp()
                           + int(tokens.get("expires_in", 3600))),
            "connected": datetime.now(timezone.utc).isoformat(),
        }
        _store_entry(uid, entry)
        response = RedirectResponse(url="/?discord=connected",
                                    status_code=302)
        response.set_cookie(
            "ogai_uid", uid,
            max_age=_cookie_max_age(), path="/", httponly=True,
            samesite="lax")
        return response

    @app.get("/auth/discord/status")
    async def discord_auth_status(raw_request: Request):
        """What the slide-over menu needs: is connect enabled, and
        if this visitor is connected, as whom? (Never returns
        tokens.)"""
        entry = None
        if DISCORD_ENABLED:
            entry = _discord_connection(raw_request.cookies.get("ogai_uid"))
        return {
            "enabled": DISCORD_ENABLED,
            "connected": bool(entry),
            "username": entry.get("username", "") if entry else "",
            "name": entry.get("name", "") if entry else "",
        }

    @app.post("/auth/discord/disconnect")
    async def discord_auth_disconnect(raw_request: Request):
        """Forget this visitor's Discord connection — tokens
        deleted server-side."""
        if not DISCORD_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        uid = raw_request.cookies.get("ogai_uid")
        if uid:
            _drop_entry(uid)
        return {"status": "disconnected"}
