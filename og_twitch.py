"""
OG Twitch connect (Round 15) — ships DARK, exactly like Round
3's Google connect, Round 10's Spotify connect and Round 14's
GitHub connect.

A visitor can connect their Twitch account to OG ("Connect
Twitch" in the slide-over menu). OAuth 2.0 Authorization Code
flow against id.twitch.tv, the standard way: OG never sees or
stores a password — Twitch itself confirms who they are and
hands back tokens, stored per visitor (keyed by ogai_uid) the
same way the other connects' tokens are: in Postgres (table
og_twitch_tokens) when OG_MEMORY_DB_URL is set, else a JSON
file (twitch_store.json) beside the other stores. Tokens are
never logged and never shared across visitors; a visitor's
Twitch data is only ever read with that visitor's own token,
and every Helix call carries OG's Client-Id header as Twitch
requires.

Scopes: user:read:email (who connected) + user:read:follows.
Scope check against Twitch's CURRENT Helix documentation
(verified 2026-10-08): GET /helix/channels/followed ("channels
the user follows") and GET /helix/streams/followed ("followed
channels currently streaming") both require user:read:follows
for a user access token — the scope is current for new apps,
NOT deprecated. (The similarly named moderator:read:followers
is the reverse direction — a broadcaster reading their own
followers — and is not used here.)

Twitch access tokens are short-lived and come with a refresh
token; the module refreshes quietly (sync, in the chat seam)
and a dead grant (revoked token / 401 that survives a refresh)
drops the connection gracefully to a reconnect prompt, never
an error page.

Ships DISABLED: the menu items stay hidden, /auth/twitch
answers 404 and the status route reports enabled:false until
OG_TWITCH_ENABLED=true plus OG_TWITCH_CLIENT_ID /
OG_TWITCH_CLIENT_SECRET are set (steps in the Round 15
report). While disabled — or while a visitor simply hasn't
connected — the chat capability below is fully inert and chat
behaves exactly as it did before this round.

Capabilities (enabled + connected only), wired through the
same app-layer seam as Rounds 3/4/6/9/10/12/13/14 —
install_twitch_tools wraps the agent's detect_intent +
web_search hooks. Every real answer costs 1 unit of the
shared per-tier lookup budget; guidance and misses cost 0:

- "who do I follow on Twitch" — their followed channels (≤10),
  grounded in /channels/followed.
- "who's live that I follow" — followed channels streaming
  right now (≤10), grounded in /streams/followed with the
  stream title, game and viewer count Twitch reports.

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

TWITCH_CLIENT_ID = os.getenv("OG_TWITCH_CLIENT_ID", "")
TWITCH_CLIENT_SECRET = os.environ.get("OG_TWITCH_CLIENT_" + "SECRET", "")
TWITCH_ENABLED = (os.getenv("OG_TWITCH_ENABLED", "false").lower() == "true"
                  and bool(TWITCH_CLIENT_ID) and bool(TWITCH_CLIENT_SECRET))
TWITCH_REDIRECT_URI = os.getenv(
    "OG_TWITCH_REDIRECT_URI",
    "https://og-ai-service.onrender.com/auth/twitch/callback")
TWITCH_SCOPES = "user:read:email user:read:follows"
TWITCH_STORE_FILE = "twitch_store.json"
_twitch_lock = threading.Lock()

_AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_API_BASE = "https://api.twitch.tv/helix"

_MAX_FOLLOWS = 10
_MAX_LIVE = 10

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

def _twitch_db_connect():
    """Connect to the durable DB, creating the Twitch tokens table."""
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_twitch_tokens ("
            "uid TEXT PRIMARY KEY, data JSONB)"
        )
    conn.commit()
    return conn


def _load_twitch_store() -> Dict:
    """Load all Twitch connections (durable DB when configured,
    otherwise the JSON twitch store)."""
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _twitch_db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT uid, data FROM og_twitch_tokens")
                    return {uid: data for uid, data in cur.fetchall()}
        except Exception as e:
            logger.warning(f"Twitch store DB load failed, using file: {e}")
    if os.path.exists(TWITCH_STORE_FILE):
        try:
            with open(TWITCH_STORE_FILE, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Could not load twitch store: {e}")
    return {}


def _save_twitch_store(store: Dict):
    """Save all Twitch connections (durable DB when configured,
    otherwise the JSON twitch store)."""
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _twitch_db_connect() as conn:
                with conn.cursor() as cur:
                    for uid, data in store.items():
                        cur.execute(
                            "INSERT INTO og_twitch_tokens (uid, data) "
                            "VALUES (%s, %s) ON CONFLICT (uid) DO UPDATE "
                            "SET data = EXCLUDED.data",
                            (uid, _Jsonb(data)),
                        )
                    cur.execute("SELECT uid FROM og_twitch_tokens")
                    existing = {row[0] for row in cur.fetchall()}
                    for stale in existing - set(store.keys()):
                        cur.execute(
                            "DELETE FROM og_twitch_tokens WHERE uid = %s",
                            (stale,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Twitch store DB save failed, using file: {e}")
    try:
        with open(TWITCH_STORE_FILE, 'w') as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Could not save twitch store: {e}")


def _twitch_connection(uid: str) -> Optional[Dict]:
    """This visitor's stored Twitch connection (profile + tokens)."""
    if not uid:
        return None
    with _twitch_lock:
        store = _load_twitch_store()
    entry = store.get(uid)
    return entry if isinstance(entry, dict) else None


def _store_entry(uid: str, entry: Dict):
    with _twitch_lock:
        store = _load_twitch_store()
        store[uid] = entry
        _save_twitch_store(store)


def _drop_entry(uid: str):
    with _twitch_lock:
        store = _load_twitch_store()
        if uid in store:
            del store[uid]
            _save_twitch_store(store)


# --- Signed OAuth state (same construction as the other flows) ---------------

def _twitch_state_for(uid: str) -> str:
    """Signed OAuth state tying the connect flow to one visitor uid."""
    sig = hmac.new(TWITCH_CLIENT_SECRET.encode(),
                   f"og-twitch:{uid}".encode(), hashlib.sha256).hexdigest()
    return f"{uid}.{sig}"


def _twitch_uid_from_state(state: str) -> Optional[str]:
    """Recover the visitor uid from a state value we signed, else None."""
    try:
        uid, _, sig = str(state).partition(".")
        if not uid or not sig:
            return None
        expected = hmac.new(TWITCH_CLIENT_SECRET.encode(),
                            f"og-twitch:{uid}".encode(),
                            hashlib.sha256).hexdigest()
        return uid if hmac.compare_digest(expected, sig) else None
    except Exception:
        return None


# --- Twitch Helix API (sync; used by the chat seam) ------------------------------
# Every API call rides this one helper so the surface stays auditable
# (and unit-testable). Helix requires the Client-Id header beside
# the Bearer token. Tokens are never logged — statuses only.

def _request(method: str, url: str, token: str = "",
             params: Optional[Dict] = None, form: Optional[Dict] = None):
    """One call against Twitch. Returns (status, data): data is
    parsed JSON (dict/list) or None; status is None on a transport
    failure."""
    import httpx
    headers = {"User-Agent": "OG-AI"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["Client-Id"] = TWITCH_CLIENT_ID
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.request(method, url, headers=headers,
                                  params=params, data=form)
    except Exception as e:
        logger.warning(f"Twitch API {method} {url} failed: {e}")
        return None, None
    try:
        data = resp.json()
    except Exception:
        data = None
    return resp.status_code, data


def _refresh_entry(uid: str, entry: Dict) -> Optional[Dict]:
    """Refresh this visitor's access token (sync; the chat seam is
    sync). Returns the updated entry, or None when no refresh is
    possible. A 400 from Twitch means the grant itself is dead
    (revoked) — the stored connection is dropped so the status
    route honestly shows disconnected; a network failure keeps
    the connection and just yields no data this time."""
    refresh = entry.get("refresh_token", "")
    if not refresh:
        return None
    status, tokens = _request("POST", _TOKEN_URL, form={
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
    })
    if status != 200 or not isinstance(tokens, dict):
        logger.warning(f"Twitch token refresh status: {status}")
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
    entry = _twitch_connection(uid)
    if not entry:
        return None
    now = datetime.now(timezone.utc).timestamp()
    if entry.get("access_token") and \
            float(entry.get("expires_at", 0)) > now + 60:
        return entry
    return _refresh_entry(uid, entry)


# --- Small helpers -------------------------------------------------------------

def _fmt_date(raw: str) -> str:
    """'2026-10-08T14:22:31Z' -> '2026-10-08'."""
    text = str(raw or "")
    return text[:10] if len(text) >= 10 else text


def _spend(consume_lookup, uid: str) -> bool:
    if consume_lookup is None:
        return True
    try:
        return bool(consume_lookup(uid))
    except Exception as e:
        logger.warning(f"Twitch budget consume failed: {e}")
        return False


# --- Guidance results (never spend budget) -------------------------------------

def _guidance(kind: str, who: str) -> list:
    if kind == "reconnect":
        body = (f"The visitor ({who}) had their Twitch account "
                "connected to OG, but Twitch is now refusing the "
                "connection (it was revoked or cut off on Twitch's "
                "side). Do NOT invent any channels or streams. "
                "Tell them, in persona, that the connection "
                "dropped and they need to tap Connect Twitch once "
                "more (slide-over menu), then ask again. Keep it "
                "short and helpful.")
        title = "💜 Twitch reconnect needed"
    else:
        body = ("The visitor is asking about their OWN Twitch "
                "account, but they have NOT connected Twitch to "
                "OG. You have no access to their follows. Do NOT "
                "invent any channels or streams. Tell them, in "
                "persona, to connect Twitch first — the Connect "
                "Twitch button in the slide-over menu — and then "
                "ask again. Keep it short and helpful.")
        title = "💜 Twitch not connected"
    return [{"title": title, "body": body, "href": "/auth/twitch"}]


def _revoked(uid: str, who: str) -> list:
    """The API just told us this visitor's token is dead (401):
    drop the stored connection and answer with the reconnect
    prompt — graceful, never an error."""
    _drop_entry(uid)
    return _guidance("reconnect", who)


# --- Chat intent parsing -------------------------------------------------------
# Deliberately first-person and service-named: every pattern is
# about the VISITOR's own Twitch follows. General Twitch
# questions ("who won the twitch rivals", "is twitch down")
# match nothing here and keep flowing to the normal lookup/chat
# paths exactly as before.

_FOLLOWS_RES = (
    r"\bwho do i follow on twitch\b",
    r"\bmy (twitch )?(follows|following|followed channels)\b",
    r"\bchannels (do )?i follow on twitch\b",
    r"\bmy followed channels\b",
)
_LIVE_RES = (
    r"\bwho'?s live (that|who) i follow\b",
    r"\bwho (that|who) i follow is live\b",
    r"\bany(one| of the channels| streamers)? i follow (live|streaming)\b",
    r"\bfollowed (channels|streamers) (are )?(live|streaming)\b",
    r"\b(is|are) anyone (i follow )?(live|streaming)( on twitch)?\b",
    r"\bmy followed streams\b",
)


def parse_twitch_intent(message: str) -> Optional[Dict]:
    """Parse a first-person Twitch job from the raw message.
    Returns {'kind': 'follows'|'live'}, or None when the message
    isn't about the visitor's own Twitch."""
    low = " " + re.sub(r"\s+", " ", str(message).lower()).strip() + " "
    for pattern in _LIVE_RES:
        if re.search(pattern, low):
            return {"kind": "live"}
    for pattern in _FOLLOWS_RES:
        if re.search(pattern, low):
            return {"kind": "follows"}
    return None


# --- Grounded answers ------------------------------------------------------------

def _data_list(data) -> list:
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return [it for it in data["data"] if isinstance(it, dict)]
    return []


def _follows_answer(entry: Dict, uid: str, consume_lookup) -> Optional[list]:
    token = entry.get("access_token", "")
    who = entry.get("name") or entry.get("login") or "the visitor"
    user_id = entry.get("twitch_id", "")
    if not user_id:
        return None
    status, data = _request(
        "GET", _API_BASE + "/channels/followed", token,
        params={"user_id": user_id, "first": _MAX_FOLLOWS})
    if status == 401:
        return _revoked(uid, who)
    if status != 200:
        return None
    if not _spend(consume_lookup, uid):
        logger.info("Twitch follows answer skipped: visitor at "
                    "daily lookup cap")
        return None
    items = _data_list(data)
    total = data.get("total") if isinstance(data, dict) else None
    if not items:
        body = (f"The visitor ({who}) connected their Twitch "
                "account and is asking who they follow. Their "
                "Twitch account follows NO channels visible to "
                "OG. Tell them plainly, in persona — do not "
                "invent channels.")
        return [{"title": "💜 The visitor's Twitch — follows nobody",
                 "body": body, "href": "https://www.twitch.tv"}]
    lines = []
    for it in items[:_MAX_FOLLOWS]:
        name = it.get("broadcaster_name") or it.get("broadcaster_login")
        if not name:
            continue
        when = _fmt_date(it.get("followed_at", ""))
        line = f"- {name}"
        if when:
            line += f" (followed {when})"
        lines.append(line)
    if not lines:
        return None
    note = ""
    if isinstance(total, int) and total > len(lines):
        note = f" They follow {total} channels in total; this is " \
               "the most recent page."
    body = (f"The visitor ({who}) connected their Twitch account "
            "and is asking who they follow. Answer ONLY from this "
            "list — their actual followed channels:\n\n"
            + "\n".join(lines) + note)
    return [{"title": "💜 The visitor's Twitch follows",
             "body": body, "href": "https://www.twitch.tv"}]


def _live_answer(entry: Dict, uid: str, consume_lookup) -> Optional[list]:
    token = entry.get("access_token", "")
    who = entry.get("name") or entry.get("login") or "the visitor"
    user_id = entry.get("twitch_id", "")
    if not user_id:
        return None
    status, data = _request(
        "GET", _API_BASE + "/streams/followed", token,
        params={"user_id": user_id, "first": _MAX_LIVE})
    if status == 401:
        return _revoked(uid, who)
    if status != 200:
        return None
    if not _spend(consume_lookup, uid):
        logger.info("Twitch live answer skipped: visitor at daily "
                    "lookup cap")
        return None
    items = _data_list(data)
    if not items:
        body = (f"The visitor ({who}) connected their Twitch "
                "account and is asking who they follow is live "
                "right now. NOBODY they follow is currently "
                "streaming (Twitch's own followed-streams list is "
                "empty). Tell them plainly, in persona — do not "
                "invent streams.")
        return [{"title": "💜 The visitor's Twitch — nobody live",
                 "body": body, "href": "https://www.twitch.tv"}]
    lines = []
    first_href = ""
    for it in items[:_MAX_LIVE]:
        name = it.get("user_name") or it.get("user_login")
        if not name:
            continue
        line = f"- {name} is LIVE"
        if it.get("title"):
            line += f": \"{it['title']}\""
        if it.get("game_name"):
            line += f" [{it['game_name']}]"
        viewers = it.get("viewer_count")
        if isinstance(viewers, int):
            line += f" — {viewers} viewers"
        lines.append(line)
        login = it.get("user_login") or ""
        if login and not first_href:
            first_href = f"https://www.twitch.tv/{login}"
    if not lines:
        return None
    body = (f"The visitor ({who}) connected their Twitch account "
            "and is asking who they follow is live right now. "
            "Answer ONLY from this list — Twitch's actual "
            "followed-streams data at this moment:\n\n"
            + "\n".join(lines))
    return [{"title": "💜 The visitor's Twitch — live now",
             "body": body, "href": first_href or "https://www.twitch.tv"}]


def twitch_results(job, uid, consume_lookup):
    """Run one parsed Twitch job for this visitor. Returns
    web_search-shaped results on a hit, guidance for enabled-but-
    unconnected/revoked visitors, or None on any miss — disabled,
    upstream down, or budget spent — so the caller falls through
    to the previous search untouched. One unit of the shared
    lookup budget is consumed per REAL answer; a miss or guidance
    consumes nothing."""
    if not job or not TWITCH_ENABLED or not uid:
        return None
    kind = job.get("kind")
    raw = _twitch_connection(uid)
    if not raw:
        return _guidance("connect", "the visitor")
    who_hint = raw.get("name") or raw.get("login") or "the visitor"
    entry = _live_connection(uid)
    if not entry:
        return _guidance("reconnect", who_hint)
    if kind == "follows":
        return _follows_answer(entry, uid, consume_lookup)
    if kind == "live":
        return _live_answer(entry, uid, consume_lookup)
    return None


# ---------------------------------------------------------------------------
# The seam (app.py installs this on the agent, after Rounds 3/4/6/9/10/12/13/14)
# ---------------------------------------------------------------------------

# The Twitch job parsed for the exchange currently being processed.
# All chat processing is serialized under app.py's _memory_lock, so
# a single slot is safe — the same reasoning as the other modules.
_pending = {"job": None}


def install_twitch_tools(agent_instance, get_uid, consume_lookup):
    """Wrap the agent's (already lookup/file/maps/spotify/github/
    hands-wrapped) detect_intent + web_search hooks so a connected
    visitor's Twitch questions try og_twitch FIRST and fall
    through to the previous search on a miss. get_uid is a zero-arg
    callable returning the current visitor's uid; consume_lookup(uid)
    spends one unit of the shared Round 3 lookup budget and returns
    False at the cap. While the feature is disabled the wrapper is
    a pure pass-through — nothing is parsed, forced or spent.
    Persona files never touched."""
    if getattr(agent_instance, "_og_twitch_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["job"] = None
        if TWITCH_ENABLED:
            try:
                uid = get_uid()
                job = parse_twitch_intent(str(message)) if uid else None
                if job:
                    _pending["job"] = job
                    if isinstance(intent, dict) \
                            and not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
            except Exception as e:
                logger.warning(f"Twitch trigger check failed: {e}")
                _pending["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _pending.get("job")
        _pending["job"] = None
        if job:
            try:
                results = twitch_results(job, get_uid(), consume_lookup)
            except Exception as e:
                logger.warning(f"Twitch search failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_twitch_installed = True


# --- Twitch account connect routes (dark until enabled) ---------------------

def register_twitch_routes(app):
    """Attach the four /auth/twitch* routes to the FastAPI app.
    Mirrors the Round 10/14 routes one for one."""

    @app.get("/auth/twitch")
    async def twitch_auth_start(raw_request: Request):
        """
        Begin Twitch connect: bounce the visitor to Twitch's own
        consent page. Answers 404 while the feature is dark (keys
        not set), so nothing about it is discoverable on the live
        site until Brent enables it.
        """
        if not TWITCH_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        from urllib.parse import urlencode
        uid = raw_request.cookies.get("ogai_uid")
        fresh_uid = None
        if not uid:
            uid = uuid.uuid4().hex
            fresh_uid = uid
        params = {
            "client_id": TWITCH_CLIENT_ID,
            "redirect_uri": TWITCH_REDIRECT_URI,
            "response_type": "code",
            "scope": TWITCH_SCOPES,
            "state": _twitch_state_for(uid),
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

    @app.get("/auth/twitch/callback")
    async def twitch_auth_callback(raw_request: Request, code: str = "",
                                   state: str = "", error: str = ""):
        """
        Twitch sends the visitor back here with a code. The signed
        state tells us which visitor this is; the code is exchanged
        for tokens, the profile is fetched, and the connection is
        stored under their uid. Any failure lands back on the chat
        with ?twitch=failed — no error page.
        """
        if not TWITCH_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        uid = _twitch_uid_from_state(state)
        if error or not code or not uid:
            return RedirectResponse(url="/?twitch=failed", status_code=302)
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                token_resp = await client.post(
                    _TOKEN_URL,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": TWITCH_REDIRECT_URI,
                        "client_id": TWITCH_CLIENT_ID,
                        "client_secret": TWITCH_CLIENT_SECRET,
                    })
                if token_resp.status_code != 200:
                    logger.warning(
                        "Twitch token exchange status: "
                        f"{token_resp.status_code}")
                    return RedirectResponse(url="/?twitch=failed",
                                            status_code=302)
                tokens = token_resp.json()
                access_token = tokens.get("access_token", "")
                if not access_token:
                    logger.warning("Twitch token exchange: no token")
                    return RedirectResponse(url="/?twitch=failed",
                                            status_code=302)
                info_resp = await client.get(
                    _API_BASE + "/users",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Client-Id": TWITCH_CLIENT_ID,
                    })
                profile = {}
                if info_resp.status_code == 200:
                    rows = (info_resp.json() or {}).get("data") or []
                    profile = rows[0] if rows else {}
        except Exception as e:
            logger.warning(f"Twitch connect failed: {e}")
            return RedirectResponse(url="/?twitch=failed", status_code=302)
        entry = {
            "twitch_id": profile.get("id", ""),
            "login": profile.get("login", ""),
            "name": profile.get("display_name", "") or
                    profile.get("login", ""),
            "email": profile.get("email", ""),
            "access_token": access_token,
            "refresh_token": tokens.get("refresh_token", ""),
            "scope": tokens.get("scope", ""),
            "expires_at": (datetime.now(timezone.utc).timestamp()
                           + int(tokens.get("expires_in", 3600))),
            "connected": datetime.now(timezone.utc).isoformat(),
        }
        _store_entry(uid, entry)
        response = RedirectResponse(url="/?twitch=connected",
                                    status_code=302)
        response.set_cookie(
            "ogai_uid", uid,
            max_age=_cookie_max_age(), path="/", httponly=True,
            samesite="lax")
        return response

    @app.get("/auth/twitch/status")
    async def twitch_auth_status(raw_request: Request):
        """What the slide-over menu needs: is connect enabled, and
        if this visitor is connected, as whom? (Never returns
        tokens.)"""
        entry = None
        if TWITCH_ENABLED:
            entry = _twitch_connection(raw_request.cookies.get("ogai_uid"))
        return {
            "enabled": TWITCH_ENABLED,
            "connected": bool(entry),
            "login": entry.get("login", "") if entry else "",
            "name": entry.get("name", "") if entry else "",
            "email": entry.get("email", "") if entry else "",
        }

    @app.post("/auth/twitch/disconnect")
    async def twitch_auth_disconnect(raw_request: Request):
        """Forget this visitor's Twitch connection — tokens
        deleted server-side."""
        if not TWITCH_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        uid = raw_request.cookies.get("ogai_uid")
        if uid:
            _drop_entry(uid)
        return {"status": "disconnected"}
