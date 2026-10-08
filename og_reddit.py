"""
OG Reddit connect (Round 15) — ships DARK, exactly like Round
3's Google connect, Round 10's Spotify connect and Round 14's
GitHub connect.

A visitor can connect their Reddit account to OG ("Connect
Reddit" in the slide-over menu). OAuth 2.0 with
duration=permanent (so Reddit issues a refresh token), the
standard way for a web app registered at reddit.com/prefs/apps:
OG never sees or stores a password — Reddit itself confirms who
they are and hands back tokens, stored per visitor (keyed by
ogai_uid) the same way the other connects' tokens are: in
Postgres (table og_reddit_tokens) when OG_MEMORY_DB_URL is set,
else a JSON file (reddit_store.json) beside the other stores.
Tokens are never logged and never shared across visitors; a
visitor's Reddit data is only ever read with that visitor's own
token, from oauth.reddit.com, with the descriptive User-Agent
Reddit's API rules require.

Scopes: identity (who connected), mysubreddits (their
subreddits), history (their posts/comments), read (the general
read scope the subscriber list rides). READ-ONLY v1 — every
scope here is a read scope and the module makes no write calls.

Reddit API terms (standing compliance, also in the Round 15
report): the app Brent registers must comply with Reddit's API
terms — the data is accessed only through the official OAuth
API on behalf of the connected user, only to answer that user
about their own account; no scraping, no bulk collection, and
NO use of Reddit data to train models. The capability below
honors that by design: it fetches the visitor's own small
answers live, grounds OG's reply in them, and persists nothing
but the connection itself.

Reddit access tokens last about an hour and come with a
refresh token (duration=permanent); the module refreshes
quietly (sync, in the chat seam) and a dead grant (401 that
survives a refresh) drops the connection gracefully to a
reconnect prompt, never an error page.

Ships DISABLED: the menu items stay hidden, /auth/reddit
answers 404 and the status route reports enabled:false until
OG_REDDIT_ENABLED=true plus OG_REDDIT_CLIENT_ID /
OG_REDDIT_CLIENT_SECRET are set (steps in the Round 15
report). While disabled — or while a visitor simply hasn't
connected — the chat capability below is fully inert and chat
behaves exactly as it did before this round.

Capabilities (enabled + connected only), wired through the
same app-layer seam as Rounds 3/4/6/9/10/12/13/14 —
install_reddit_tools wraps the agent's detect_intent +
web_search hooks. Every real answer costs 1 unit of the
shared per-tier lookup budget; guidance and misses cost 0:

- "my subreddits" — the subreddits they subscribe to (≤10),
  grounded, with subscriber counts when Reddit gives them.
- "my recent posts/comments" — their latest overview items
  (≤5, posts and comments labeled, subreddit named), grounded.
- "my karma" — their total/link/comment karma, grounded in a
  live /api/v1/me read.

Persona files are never touched.
"""

import base64
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

REDDIT_CLIENT_ID = os.getenv("OG_REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("OG_REDDIT_CLIENT_" + "SECRET", "")
REDDIT_ENABLED = (os.getenv("OG_REDDIT_ENABLED", "false").lower() == "true"
                  and bool(REDDIT_CLIENT_ID) and bool(REDDIT_CLIENT_SECRET))
REDDIT_REDIRECT_URI = os.getenv(
    "OG_REDDIT_REDIRECT_URI",
    "https://og-ai-service.onrender.com/auth/reddit/callback")
REDDIT_SCOPES = "identity mysubreddits history read"
REDDIT_STORE_FILE = "reddit_store.json"
_reddit_lock = threading.Lock()

_AUTHORIZE_URL = "https://www.reddit.com/api/v1/authorize"
_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_API_BASE = "https://oauth.reddit.com"
# Reddit's API rules require a descriptive, non-browser User-Agent.
_USER_AGENT = "web:og-ai-service:1.0 (OG AI connect; per-user OAuth reads)"

_MAX_SUBS = 10
_MAX_ACTIVITY = 5

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

def _reddit_db_connect():
    """Connect to the durable DB, creating the Reddit tokens table."""
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_reddit_tokens ("
            "uid TEXT PRIMARY KEY, data JSONB)"
        )
    conn.commit()
    return conn


def _load_reddit_store() -> Dict:
    """Load all Reddit connections (durable DB when configured,
    otherwise the JSON reddit store)."""
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _reddit_db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT uid, data FROM og_reddit_tokens")
                    return {uid: data for uid, data in cur.fetchall()}
        except Exception as e:
            logger.warning(f"Reddit store DB load failed, using file: {e}")
    if os.path.exists(REDDIT_STORE_FILE):
        try:
            with open(REDDIT_STORE_FILE, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Could not load reddit store: {e}")
    return {}


def _save_reddit_store(store: Dict):
    """Save all Reddit connections (durable DB when configured,
    otherwise the JSON reddit store)."""
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _reddit_db_connect() as conn:
                with conn.cursor() as cur:
                    for uid, data in store.items():
                        cur.execute(
                            "INSERT INTO og_reddit_tokens (uid, data) "
                            "VALUES (%s, %s) ON CONFLICT (uid) DO UPDATE "
                            "SET data = EXCLUDED.data",
                            (uid, _Jsonb(data)),
                        )
                    cur.execute("SELECT uid FROM og_reddit_tokens")
                    existing = {row[0] for row in cur.fetchall()}
                    for stale in existing - set(store.keys()):
                        cur.execute(
                            "DELETE FROM og_reddit_tokens WHERE uid = %s",
                            (stale,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Reddit store DB save failed, using file: {e}")
    try:
        with open(REDDIT_STORE_FILE, 'w') as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Could not save reddit store: {e}")


def _reddit_connection(uid: str) -> Optional[Dict]:
    """This visitor's stored Reddit connection (profile + tokens)."""
    if not uid:
        return None
    with _reddit_lock:
        store = _load_reddit_store()
    entry = store.get(uid)
    return entry if isinstance(entry, dict) else None


def _store_entry(uid: str, entry: Dict):
    with _reddit_lock:
        store = _load_reddit_store()
        store[uid] = entry
        _save_reddit_store(store)


def _drop_entry(uid: str):
    with _reddit_lock:
        store = _load_reddit_store()
        if uid in store:
            del store[uid]
            _save_reddit_store(store)


# --- Signed OAuth state (same construction as the other flows) ---------------

def _reddit_state_for(uid: str) -> str:
    """Signed OAuth state tying the connect flow to one visitor uid."""
    sig = hmac.new(REDDIT_CLIENT_SECRET.encode(),
                   f"og-reddit:{uid}".encode(), hashlib.sha256).hexdigest()
    return f"{uid}.{sig}"


def _reddit_uid_from_state(state: str) -> Optional[str]:
    """Recover the visitor uid from a state value we signed, else None."""
    try:
        uid, _, sig = str(state).partition(".")
        if not uid or not sig:
            return None
        expected = hmac.new(REDDIT_CLIENT_SECRET.encode(),
                            f"og-reddit:{uid}".encode(),
                            hashlib.sha256).hexdigest()
        return uid if hmac.compare_digest(expected, sig) else None
    except Exception:
        return None


# --- Reddit API (sync; used by the chat seam) -----------------------------------
# Every API call rides this one helper so the surface stays auditable
# (and unit-testable). The token endpoint takes HTTP Basic auth
# (client id/secret); oauth.reddit.com takes the Bearer token;
# everything carries the descriptive User-Agent. Tokens are never
# logged — statuses only.

def _basic_auth_header() -> str:
    raw = f"{REDDIT_CLIENT_ID}:{REDDIT_CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _request(method: str, url: str, token: str = "",
             params: Optional[Dict] = None, form: Optional[Dict] = None,
             basic: bool = False):
    """One call against Reddit. Returns (status, data): data is
    parsed JSON (dict/list) or None; status is None on a transport
    failure."""
    import httpx
    headers = {"User-Agent": _USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if basic:
        headers["Authorization"] = _basic_auth_header()
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.request(method, url, headers=headers,
                                  params=params, data=form)
    except Exception as e:
        logger.warning(f"Reddit API {method} {url} failed: {e}")
        return None, None
    try:
        data = resp.json()
    except Exception:
        data = None
    return resp.status_code, data


def _refresh_entry(uid: str, entry: Dict) -> Optional[Dict]:
    """Refresh this visitor's access token (sync; the chat seam is
    sync). Returns the updated entry, or None when no refresh is
    possible. A 400/401 from Reddit means the grant itself is dead
    (revoked) — the stored connection is dropped so the status
    route honestly shows disconnected; a network failure keeps
    the connection and just yields no data this time."""
    refresh = entry.get("refresh_token", "")
    if not refresh:
        return None
    status, tokens = _request("POST", _TOKEN_URL, basic=True, form={
        "grant_type": "refresh_token",
        "refresh_token": refresh,
    })
    if status != 200 or not isinstance(tokens, dict):
        logger.warning(f"Reddit token refresh status: {status}")
        if status in (400, 401):
            _drop_entry(uid)
        return None
    entry = dict(entry)
    entry["access_token"] = tokens.get("access_token",
                                       entry.get("access_token", ""))
    entry["expires_at"] = (datetime.now(timezone.utc).timestamp()
                           + int(tokens.get("expires_in", 3600)))
    _store_entry(uid, entry)
    return entry


def _live_connection(uid: str) -> Optional[Dict]:
    """This visitor's connection with a non-expired access token
    (refreshing when needed), or None."""
    entry = _reddit_connection(uid)
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
        logger.warning(f"Reddit budget consume failed: {e}")
        return False


# --- Guidance results (never spend budget) -------------------------------------

def _guidance(kind: str, who: str) -> list:
    if kind == "reconnect":
        body = (f"The visitor ({who}) had their Reddit account "
                "connected to OG, but Reddit is now refusing the "
                "connection (it was revoked or cut off on Reddit's "
                "side). Do NOT invent any subreddits, posts or "
                "karma. Tell them, in persona, that the connection "
                "dropped and they need to tap Connect Reddit once "
                "more (slide-over menu), then ask again. Keep it "
                "short and helpful.")
        title = "🟠 Reddit reconnect needed"
    else:
        body = ("The visitor is asking about their OWN Reddit "
                "account, but they have NOT connected Reddit to "
                "OG. You have no access to their account. Do NOT "
                "invent any subreddits, posts or karma. Tell them, "
                "in persona, to connect Reddit first — the "
                "Connect Reddit button in the slide-over menu — "
                "and then ask again. Keep it short and helpful.")
        title = "🟠 Reddit not connected"
    return [{"title": title, "body": body, "href": "/auth/reddit"}]


def _revoked(uid: str, who: str) -> list:
    """The API just told us this visitor's token is dead (401):
    drop the stored connection and answer with the reconnect
    prompt — graceful, never an error."""
    _drop_entry(uid)
    return _guidance("reconnect", who)


# --- Chat intent parsing -------------------------------------------------------
# Deliberately first-person and service-named: every pattern is
# about the VISITOR's own Reddit. General Reddit questions
# ("what's hot on r/funny", "is reddit down") match nothing here
# and keep flowing to the normal lookup/chat paths exactly as
# before.

_SUBS_RES = (
    r"\bmy (reddit )?subreddits\b",
    r"\bsubreddits (do )?i (follow|subscribe|belong|am i in)\b",
    r"\bwhat subreddits (am i|do i)\b",
    r"\bsubreddits i('m| am) (in|subscribed to|following)\b",
)
_ACTIVITY_RES = (
    r"\bmy recent (reddit )?(posts|comments|activity|post history)\b",
    r"\bwhat have i (posted|commented) on reddit\b",
    r"\bmy reddit (posts|comments|history|activity)\b",
    r"\bmy (posts|comments) on reddit\b",
)
_KARMA_RES = (
    r"\bmy (reddit )?karma\b",
    r"\bhow much karma (do i|have i)\b",
)


def parse_reddit_intent(message: str) -> Optional[Dict]:
    """Parse a first-person Reddit job from the raw message.
    Returns {'kind': 'subs'|'activity'|'karma'}, or None when
    the message isn't about the visitor's own Reddit."""
    low = " " + re.sub(r"\s+", " ", str(message).lower()).strip() + " "
    for pattern in _SUBS_RES:
        if re.search(pattern, low):
            return {"kind": "subs"}
    for pattern in _ACTIVITY_RES:
        if re.search(pattern, low):
            return {"kind": "activity"}
    for pattern in _KARMA_RES:
        if re.search(pattern, low):
            return {"kind": "karma"}
    return None


# --- Grounded answers ------------------------------------------------------------

def _children(data) -> list:
    if isinstance(data, dict):
        inner = data.get("data") or {}
        if isinstance(inner, dict) and \
                isinstance(inner.get("children"), list):
            out = []
            for child in inner["children"]:
                if isinstance(child, dict) and \
                        isinstance(child.get("data"), dict):
                    out.append((child.get("kind", ""), child["data"]))
            return out
    return []


def _subs_answer(entry: Dict, uid: str, consume_lookup) -> Optional[list]:
    token = entry.get("access_token", "")
    who = entry.get("username") or entry.get("name") or "the visitor"
    status, data = _request(
        "GET", _API_BASE + "/subreddits/mine/subscriber", token,
        params={"limit": _MAX_SUBS})
    if status == 401:
        return _revoked(uid, who)
    if status != 200:
        return None
    if not _spend(consume_lookup, uid):
        logger.info("Reddit subs answer skipped: visitor at daily "
                    "lookup cap")
        return None
    items = _children(data)
    if not items:
        body = (f"The visitor (u/{who}) connected their Reddit "
                "account and is asking about their subreddits. "
                "Their account subscribes to NO subreddits "
                "visible to OG. Tell them plainly, in persona — "
                "do not invent subreddits.")
        return [{"title": "🟠 The visitor's Reddit — no subreddits",
                 "body": body, "href": "https://www.reddit.com"}]
    lines = []
    first_href = ""
    for _kind, sub in items[:_MAX_SUBS]:
        name = sub.get("display_name", "")
        if not name:
            continue
        line = f"- r/{name}"
        subs_count = sub.get("subscribers")
        if isinstance(subs_count, int):
            line += f" ({subs_count} members)"
        lines.append(line)
        if not first_href:
            first_href = f"https://www.reddit.com/r/{name}"
    if not lines:
        return None
    body = (f"The visitor (u/{who}) connected their Reddit "
            "account and is asking about their subreddits. "
            "Answer ONLY from this list — their actual subscribed "
            "subreddits:\n\n" + "\n".join(lines))
    return [{"title": "🟠 The visitor's subreddits",
             "body": body, "href": first_href or "https://www.reddit.com"}]


def _activity_answer(entry: Dict, uid: str, consume_lookup) -> Optional[list]:
    token = entry.get("access_token", "")
    who = entry.get("username") or entry.get("name") or "the visitor"
    if not who or who == "the visitor":
        return None
    status, data = _request(
        "GET", _API_BASE + f"/user/{who}/overview", token,
        params={"limit": _MAX_ACTIVITY, "sort": "new"})
    if status == 401:
        return _revoked(uid, who)
    if status != 200:
        return None
    if not _spend(consume_lookup, uid):
        logger.info("Reddit activity answer skipped: visitor at "
                    "daily lookup cap")
        return None
    items = _children(data)
    if not items:
        body = (f"The visitor (u/{who}) connected their Reddit "
                "account and is asking about their recent posts "
                "and comments. Their account has NO recent posts "
                "or comments visible to OG. Tell them plainly, in "
                "persona — do not invent activity.")
        return [{"title": "🟠 The visitor's Reddit — no recent activity",
                 "body": body, "href": "https://www.reddit.com"}]
    lines = []
    first_href = ""
    for kind, item in items[:_MAX_ACTIVITY]:
        sub = item.get("subreddit", "")
        if kind == "t3":  # a post
            title = item.get("title", "")
            if not title:
                continue
            line = f"- POST in r/{sub}: \"{title}\""
        elif kind == "t1":  # a comment
            text = re.sub(r"\s+", " ", str(item.get("body", ""))).strip()
            if not text:
                continue
            if len(text) > 120:
                text = text[:117] + "..."
            line = f"- COMMENT in r/{sub}: \"{text}\""
        else:
            continue
        score = item.get("score")
        if isinstance(score, int):
            line += f" ({score} points)"
        lines.append(line)
        permalink = item.get("permalink", "")
        if permalink and not first_href:
            first_href = "https://www.reddit.com" + permalink
    if not lines:
        return None
    body = (f"The visitor (u/{who}) connected their Reddit "
            "account and is asking about their recent posts and "
            "comments. Answer ONLY from this list — their actual "
            "recent activity, newest first:\n\n" + "\n".join(lines))
    return [{"title": "🟠 The visitor's recent Reddit activity",
             "body": body,
             "href": first_href or "https://www.reddit.com"}]


def _karma_answer(entry: Dict, uid: str, consume_lookup) -> Optional[list]:
    token = entry.get("access_token", "")
    who = entry.get("username") or entry.get("name") or "the visitor"
    status, data = _request("GET", _API_BASE + "/api/v1/me", token)
    if status == 401:
        return _revoked(uid, who)
    if status != 200 or not isinstance(data, dict):
        return None
    if not _spend(consume_lookup, uid):
        logger.info("Reddit karma answer skipped: visitor at "
                    "daily lookup cap")
        return None
    total = data.get("total_karma")
    link = data.get("link_karma")
    comment = data.get("comment_karma")
    name = data.get("name") or who
    if total is None and link is None and comment is None:
        return None
    parts = []
    if total is not None:
        parts.append(f"total karma: {total}")
    if link is not None:
        parts.append(f"post karma: {link}")
    if comment is not None:
        parts.append(f"comment karma: {comment}")
    body = (f"The visitor (u/{name}) connected their Reddit "
            "account and is asking about their karma. Answer "
            "ONLY from these live figures from their account: "
            + "; ".join(parts) + ".")
    return [{"title": "🟠 The visitor's Reddit karma",
             "body": body, "href": "https://www.reddit.com"}]


def reddit_results(job, uid, consume_lookup):
    """Run one parsed Reddit job for this visitor. Returns
    web_search-shaped results on a hit, guidance for enabled-but-
    unconnected/revoked visitors, or None on any miss — disabled,
    upstream down, or budget spent — so the caller falls through
    to the previous search untouched. One unit of the shared
    lookup budget is consumed per REAL answer; a miss or guidance
    consumes nothing."""
    if not job or not REDDIT_ENABLED or not uid:
        return None
    kind = job.get("kind")
    raw = _reddit_connection(uid)
    if not raw:
        return _guidance("connect", "the visitor")
    who_hint = raw.get("username") or raw.get("name") or "the visitor"
    entry = _live_connection(uid)
    if not entry:
        return _guidance("reconnect", who_hint)
    if kind == "subs":
        return _subs_answer(entry, uid, consume_lookup)
    if kind == "activity":
        return _activity_answer(entry, uid, consume_lookup)
    if kind == "karma":
        return _karma_answer(entry, uid, consume_lookup)
    return None


# ---------------------------------------------------------------------------
# The seam (app.py installs this on the agent, after Rounds 3/4/6/9/10/12/13/14)
# ---------------------------------------------------------------------------

# The Reddit job parsed for the exchange currently being processed.
# All chat processing is serialized under app.py's _memory_lock, so
# a single slot is safe — the same reasoning as the other modules.
_pending = {"job": None}


def install_reddit_tools(agent_instance, get_uid, consume_lookup):
    """Wrap the agent's (already lookup/file/maps/spotify/github/
    hands-wrapped) detect_intent + web_search hooks so a connected
    visitor's Reddit questions try og_reddit FIRST and fall
    through to the previous search on a miss. get_uid is a zero-arg
    callable returning the current visitor's uid; consume_lookup(uid)
    spends one unit of the shared Round 3 lookup budget and returns
    False at the cap. While the feature is disabled the wrapper is
    a pure pass-through — nothing is parsed, forced or spent.
    Persona files never touched."""
    if getattr(agent_instance, "_og_reddit_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["job"] = None
        if REDDIT_ENABLED:
            try:
                uid = get_uid()
                job = parse_reddit_intent(str(message)) if uid else None
                if job:
                    _pending["job"] = job
                    if isinstance(intent, dict) \
                            and not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
            except Exception as e:
                logger.warning(f"Reddit trigger check failed: {e}")
                _pending["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _pending.get("job")
        _pending["job"] = None
        if job:
            try:
                results = reddit_results(job, get_uid(), consume_lookup)
            except Exception as e:
                logger.warning(f"Reddit search failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_reddit_installed = True


# --- Reddit account connect routes (dark until enabled) ---------------------

def register_reddit_routes(app):
    """Attach the four /auth/reddit* routes to the FastAPI app.
    Mirrors the Round 10/14 routes one for one."""

    @app.get("/auth/reddit")
    async def reddit_auth_start(raw_request: Request):
        """
        Begin Reddit connect: bounce the visitor to Reddit's own
        consent page. Answers 404 while the feature is dark (keys
        not set), so nothing about it is discoverable on the live
        site until Brent enables it.
        """
        if not REDDIT_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        from urllib.parse import urlencode
        uid = raw_request.cookies.get("ogai_uid")
        fresh_uid = None
        if not uid:
            uid = uuid.uuid4().hex
            fresh_uid = uid
        params = {
            "client_id": REDDIT_CLIENT_ID,
            "redirect_uri": REDDIT_REDIRECT_URI,
            "response_type": "code",
            "state": _reddit_state_for(uid),
            "duration": "permanent",
            "scope": REDDIT_SCOPES,
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

    @app.get("/auth/reddit/callback")
    async def reddit_auth_callback(raw_request: Request, code: str = "",
                                   state: str = "", error: str = ""):
        """
        Reddit sends the visitor back here with a code. The signed
        state tells us which visitor this is; the code is exchanged
        for tokens, the profile is fetched, and the connection is
        stored under their uid. Any failure lands back on the chat
        with ?reddit=failed — no error page.
        """
        if not REDDIT_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        uid = _reddit_uid_from_state(state)
        if error or not code or not uid:
            return RedirectResponse(url="/?reddit=failed", status_code=302)
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                token_resp = await client.post(
                    _TOKEN_URL,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": REDDIT_REDIRECT_URI,
                    },
                    headers={
                        "Authorization": _basic_auth_header(),
                        "User-Agent": _USER_AGENT,
                    })
                if token_resp.status_code != 200:
                    logger.warning(
                        "Reddit token exchange status: "
                        f"{token_resp.status_code}")
                    return RedirectResponse(url="/?reddit=failed",
                                            status_code=302)
                tokens = token_resp.json()
                access_token = tokens.get("access_token", "")
                if not access_token:
                    logger.warning("Reddit token exchange: no token")
                    return RedirectResponse(url="/?reddit=failed",
                                            status_code=302)
                info_resp = await client.get(
                    _API_BASE + "/api/v1/me",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "User-Agent": _USER_AGENT,
                    })
                profile = info_resp.json() \
                    if info_resp.status_code == 200 else {}
        except Exception as e:
            logger.warning(f"Reddit connect failed: {e}")
            return RedirectResponse(url="/?reddit=failed", status_code=302)
        entry = {
            "username": profile.get("name", ""),
            "name": profile.get("name", ""),
            "access_token": access_token,
            "refresh_token": tokens.get("refresh_token", ""),
            "scope": tokens.get("scope", ""),
            "expires_at": (datetime.now(timezone.utc).timestamp()
                           + int(tokens.get("expires_in", 3600))),
            "connected": datetime.now(timezone.utc).isoformat(),
        }
        _store_entry(uid, entry)
        response = RedirectResponse(url="/?reddit=connected",
                                    status_code=302)
        response.set_cookie(
            "ogai_uid", uid,
            max_age=_cookie_max_age(), path="/", httponly=True,
            samesite="lax")
        return response

    @app.get("/auth/reddit/status")
    async def reddit_auth_status(raw_request: Request):
        """What the slide-over menu needs: is connect enabled, and
        if this visitor is connected, as whom? (Never returns
        tokens.)"""
        entry = None
        if REDDIT_ENABLED:
            entry = _reddit_connection(raw_request.cookies.get("ogai_uid"))
        return {
            "enabled": REDDIT_ENABLED,
            "connected": bool(entry),
            "username": entry.get("username", "") if entry else "",
            "name": entry.get("name", "") if entry else "",
        }

    @app.post("/auth/reddit/disconnect")
    async def reddit_auth_disconnect(raw_request: Request):
        """Forget this visitor's Reddit connection — tokens
        deleted server-side."""
        if not REDDIT_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        uid = raw_request.cookies.get("ogai_uid")
        if uid:
            _drop_entry(uid)
        return {"status": "disconnected"}
