"""
OG YouTube connect (Round 15) — ships DARK, exactly like Round 3's
Google connect, Round 10's Spotify connect and Round 14's GitHub
connect.

A visitor can connect their YouTube account to OG ("Connect
YouTube" in the slide-over menu). OAuth 2.0, Google-family: OG
never sees or stores a password — Google itself confirms who they
are and hands back tokens, stored per visitor (keyed by ogai_uid)
the same way the Google/Spotify/GitHub tokens are: in Postgres
(table og_youtube_tokens) when OG_MEMORY_DB_URL is set, else a
JSON file (youtube_store.json) beside the other stores. Tokens are
never logged and never shared across visitors; a visitor's YouTube
data is only ever read with that visitor's own token.

This is its OWN OAuth flow with its OWN client (env
OG_YOUTUBE_CLIENT_ID / OG_YOUTUBE_CLIENT_SECRET) — deliberately
separate from the Round 3 Google identity connect: that connect
carries only openid/email/profile (+ the hands/drive scopes), and
YouTube access must not silently widen it. Brent CAN register this
client in the same Google Cloud project as the Round 3 one (one
project, two OAuth clients is normal); the one extra step is
enabling the YouTube Data API v3 in that project.

Scopes: openid + email (identity: who connected) and
https://www.googleapis.com/auth/youtube.readonly (their channel
data, strictly read). access_type=offline + prompt=consent ask
Google for a refresh token, so the connection survives access-token
expiry; a dead grant (invalid_grant / 401) drops the connection
gracefully to a reconnect prompt, never an error page.

Ships DISABLED: the menu items stay hidden, /auth/youtube answers
404 and the status route reports enabled:false until
OG_YOUTUBE_ENABLED=true plus OG_YOUTUBE_CLIENT_ID /
OG_YOUTUBE_CLIENT_SECRET are set (steps in the Round 15 report).
While disabled — or while a visitor simply hasn't connected — the
chat capability below is fully inert and chat behaves exactly as
it did before this round.

Capabilities (enabled + connected only), wired through the same
app-layer seam as Rounds 3/4/6/9/10/12/13/14 — install_youtube_tools
wraps the agent's detect_intent + web_search hooks. Every real
answer costs 1 unit of the shared per-tier lookup budget; guidance,
clarifies and misses cost 0:

- "my subscriptions" — the channels they subscribe to (≤10),
  grounded in subscriptions.list.
- "my recent uploads" / "my channel's latest videos" — their own
  channel's uploads playlist (≤5, with dates), grounded.
- "my playlists" (YouTube named, so it never collides with the
  Spotify capability) — their playlists (≤10), grounded.
- "search YouTube for X" / "find a video about X" — public video
  search via search.list (≤5, title/channel/date), grounded.

The search rides the VISITOR's own token (deliberate v1 choice):
one credential story, no extra server key for Brent to create, and
searches are performed as the connected visitor, consistent with
every other capability in this module. The tradeoff is quota: the
YouTube Data API quota belongs to the PROJECT (10,000 units/day;
search.list costs 100 units), so all visitors share roughly 100
searches/day — plenty at OG's current scale. If search ever gets
popular, the upgrade is a server key (OG_YOUTUBE_API_KEY) in a
SECOND Google Cloud project with its own quota; noted in the
Round 15 report, not built in v1. Persona files are never touched.
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

YOUTUBE_CLIENT_ID = os.getenv("OG_YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.environ.get("OG_YOUTUBE_CLIENT_" + "SECRET", "")
YOUTUBE_ENABLED = (os.getenv("OG_YOUTUBE_ENABLED", "false").lower() == "true"
                   and bool(YOUTUBE_CLIENT_ID) and bool(YOUTUBE_CLIENT_SECRET))
YOUTUBE_REDIRECT_URI = os.getenv(
    "OG_YOUTUBE_REDIRECT_URI",
    "https://og-ai-service.onrender.com/auth/youtube/callback")
YOUTUBE_SCOPES = ("openid email "
                  "https://www.googleapis.com/auth/youtube.readonly")
YOUTUBE_STORE_FILE = "youtube_store.json"
_youtube_lock = threading.Lock()

_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
_API_BASE = "https://www.googleapis.com/youtube/v3"

_MAX_SUBS = 10
_MAX_UPLOADS = 5
_MAX_PLAYLISTS = 10
_MAX_SEARCH = 5

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

def _youtube_db_connect():
    """Connect to the durable DB, creating the YouTube tokens table."""
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_youtube_tokens ("
            "uid TEXT PRIMARY KEY, data JSONB)"
        )
    conn.commit()
    return conn


def _load_youtube_store() -> Dict:
    """Load all YouTube connections (durable DB when configured,
    otherwise the JSON youtube store)."""
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _youtube_db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT uid, data FROM og_youtube_tokens")
                    return {uid: data for uid, data in cur.fetchall()}
        except Exception as e:
            logger.warning(f"YouTube store DB load failed, using file: {e}")
    if os.path.exists(YOUTUBE_STORE_FILE):
        try:
            with open(YOUTUBE_STORE_FILE, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Could not load youtube store: {e}")
    return {}


def _save_youtube_store(store: Dict):
    """Save all YouTube connections (durable DB when configured,
    otherwise the JSON youtube store)."""
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _youtube_db_connect() as conn:
                with conn.cursor() as cur:
                    for uid, data in store.items():
                        cur.execute(
                            "INSERT INTO og_youtube_tokens (uid, data) "
                            "VALUES (%s, %s) ON CONFLICT (uid) DO UPDATE "
                            "SET data = EXCLUDED.data",
                            (uid, _Jsonb(data)),
                        )
                    cur.execute("SELECT uid FROM og_youtube_tokens")
                    existing = {row[0] for row in cur.fetchall()}
                    for stale in existing - set(store.keys()):
                        cur.execute(
                            "DELETE FROM og_youtube_tokens WHERE uid = %s",
                            (stale,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"YouTube store DB save failed, using file: {e}")
    try:
        with open(YOUTUBE_STORE_FILE, 'w') as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Could not save youtube store: {e}")


def _youtube_connection(uid: str) -> Optional[Dict]:
    """This visitor's stored YouTube connection (profile + tokens)."""
    if not uid:
        return None
    with _youtube_lock:
        store = _load_youtube_store()
    entry = store.get(uid)
    return entry if isinstance(entry, dict) else None


def _store_entry(uid: str, entry: Dict):
    with _youtube_lock:
        store = _load_youtube_store()
        store[uid] = entry
        _save_youtube_store(store)


def _drop_entry(uid: str):
    with _youtube_lock:
        store = _load_youtube_store()
        if uid in store:
            del store[uid]
            _save_youtube_store(store)


# --- Signed OAuth state (same construction as the other flows) ---------------

def _youtube_state_for(uid: str) -> str:
    """Signed OAuth state tying the connect flow to one visitor uid."""
    sig = hmac.new(YOUTUBE_CLIENT_SECRET.encode(),
                   f"og-youtube:{uid}".encode(), hashlib.sha256).hexdigest()
    return f"{uid}.{sig}"


def _youtube_uid_from_state(state: str) -> Optional[str]:
    """Recover the visitor uid from a state value we signed, else None."""
    try:
        uid, _, sig = str(state).partition(".")
        if not uid or not sig:
            return None
        expected = hmac.new(YOUTUBE_CLIENT_SECRET.encode(),
                            f"og-youtube:{uid}".encode(),
                            hashlib.sha256).hexdigest()
        return uid if hmac.compare_digest(expected, sig) else None
    except Exception:
        return None


# --- YouTube / Google API (sync; used by the chat seam) -----------------------
# Every API call rides this one helper so the surface stays auditable
# (and unit-testable). Tokens are never logged — statuses only.

def _request(method: str, url: str, token: str = "",
             params: Optional[Dict] = None, form: Optional[Dict] = None):
    """One call against Google/YouTube. Returns (status, data):
    data is parsed JSON (dict/list) or None; status is None on a
    transport failure."""
    import httpx
    headers = {"User-Agent": "OG-AI"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.request(method, url, headers=headers,
                                  params=params, data=form)
    except Exception as e:
        logger.warning(f"YouTube API {method} {url} failed: {e}")
        return None, None
    try:
        data = resp.json()
    except Exception:
        data = None
    return resp.status_code, data


def _refresh_entry(uid: str, entry: Dict) -> Optional[Dict]:
    """Refresh this visitor's access token (sync; the chat seam is
    sync). Returns the updated entry, or None when no refresh is
    possible. A 400 from Google means the grant itself is dead
    (revoked / expired) — the stored connection is dropped so the
    status route honestly shows disconnected; a network failure
    keeps the connection and just yields no data this time."""
    refresh = entry.get("refresh_token", "")
    if not refresh:
        return None
    status, tokens = _request("POST", _TOKEN_URL, form={
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": YOUTUBE_CLIENT_ID,
        "client_secret": YOUTUBE_CLIENT_SECRET,
    })
    if status != 200 or not isinstance(tokens, dict):
        logger.warning(f"YouTube token refresh status: {status}")
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
    entry = _youtube_connection(uid)
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
        logger.warning(f"YouTube budget consume failed: {e}")
        return False


# --- Guidance results (never spend budget) -------------------------------------

def _guidance(kind: str, who: str) -> list:
    if kind == "reconnect":
        body = (f"The visitor ({who}) had their YouTube account "
                "connected to OG, but Google is now refusing the "
                "connection (it was revoked or cut off on Google's "
                "side). Do NOT invent any channels, videos or "
                "playlists. Tell them, in persona, that the "
                "connection dropped and they need to tap Connect "
                "YouTube once more (slide-over menu), then ask "
                "again. Keep it short and helpful.")
        title = "📺 YouTube reconnect needed"
    else:
        body = ("The visitor is asking about their OWN YouTube "
                "account, but they have NOT connected YouTube to "
                "OG. You have no access to their channel. Do NOT "
                "invent any channels, videos or playlists. Tell "
                "them, in persona, to connect YouTube first — the "
                "Connect YouTube button in the slide-over menu — "
                "and then ask again. Keep it short and helpful.")
        title = "📺 YouTube not connected"
    return [{"title": title, "body": body, "href": "/auth/youtube"}]


def _revoked(uid: str, who: str) -> list:
    """The API just told us this visitor's token is dead (401):
    drop the stored connection and answer with the reconnect
    prompt — graceful, never an error."""
    _drop_entry(uid)
    return _guidance("reconnect", who)


# --- Chat intent parsing -------------------------------------------------------
# Deliberately first-person and service-named: every pattern is
# about the VISITOR's own YouTube. General video questions ("who
# won the game last night", "play the trailer for…") match nothing
# here and keep flowing to the normal lookup/chat paths exactly as
# before. Playlist asks require "youtube" in the message — the
# Spotify capability owns the bare "my playlists".

_SUBS_RES = (
    r"\bmy (youtube )?subscriptions\b",
    r"\bchannels i('m| am) subscribed to\b",
    r"\bwhat channels do i (follow|watch) on youtube\b",
    r"\byoutube channels i follow\b",
)
_UPLOADS_RES = (
    r"\bmy (recent |latest )?uploads\b",
    r"\bmy channel'?s (latest|recent|newest) (videos|uploads)\b",
    r"\bmy (latest|recent) (youtube )?(videos|uploads)\b",
    r"\bmy youtube (videos|uploads)\b",
)
_PLAYLISTS_RES = (
    r"\bmy youtube playlists\b",
    r"\bplaylists (do i have )?on youtube\b",
    r"\bmy playlists on youtube\b",
)
_SEARCH_RES = (
    r"\bsearch youtube for\s+(.+)$",
    r"\byoutube search (for\s+)?(.+)$",
    r"\bfind (a |an |some )?videos? (about|on)\s+(.+)$",
    r"\blook up (a |an )?videos? (about|on)\s+(.+)$",
)


def parse_youtube_intent(message: str) -> Optional[Dict]:
    """Parse a first-person YouTube job from the raw message.
    Returns {'kind': 'subs'|'uploads'|'playlists'} or
    {'kind': 'search', 'query': str}, or None when the message
    isn't about the visitor's own YouTube / a YouTube search."""
    low = " " + re.sub(r"\s+", " ", str(message).lower()).strip() + " "
    for pattern in _SEARCH_RES:
        m = re.search(pattern, low)
        if m:
            query = (m.group(m.lastindex) or "").strip(" .?!")
            if query and len(query) >= 2:
                return {"kind": "search", "query": query}
    for pattern in _SUBS_RES:
        if re.search(pattern, low):
            return {"kind": "subs"}
    for pattern in _UPLOADS_RES:
        if re.search(pattern, low):
            return {"kind": "uploads"}
    for pattern in _PLAYLISTS_RES:
        if re.search(pattern, low):
            return {"kind": "playlists"}
    return None


# --- Grounded answers ------------------------------------------------------------

def _items(data) -> list:
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return [it for it in data["items"] if isinstance(it, dict)]
    return []


def _subs_answer(entry: Dict, uid: str, consume_lookup) -> Optional[list]:
    token = entry.get("access_token", "")
    who = entry.get("name") or entry.get("email") or "the visitor"
    status, data = _request(
        "GET", _API_BASE + "/subscriptions", token,
        params={"part": "snippet", "mine": "true",
                "maxResults": _MAX_SUBS})
    if status == 401:
        return _revoked(uid, who)
    if status != 200:
        return None
    if not _spend(consume_lookup, uid):
        logger.info("YouTube subs answer skipped: visitor at daily "
                    "lookup cap")
        return None
    items = _items(data)
    if not items:
        body = (f"The visitor ({who}) connected their YouTube "
                "account and is asking about their subscriptions. "
                "Their account has NO subscriptions visible to OG. "
                "Tell them plainly, in persona — do not invent "
                "channels.")
        return [{"title": "📺 The visitor's YouTube — no subscriptions",
                 "body": body, "href": "https://www.youtube.com"}]
    lines = []
    first_href = ""
    for it in items:
        snip = it.get("snippet") or {}
        title = snip.get("title", "")
        if not title:
            continue
        chan_id = ((snip.get("resourceId") or {}).get("channelId") or "")
        lines.append(f"- {title}")
        if chan_id and not first_href:
            first_href = f"https://www.youtube.com/channel/{chan_id}"
    if not lines:
        return None
    body = (f"The visitor ({who}) connected their YouTube account "
            "and is asking about their subscriptions. Answer ONLY "
            "from this list — their actual YouTube subscriptions:"
            "\n\n" + "\n".join(lines))
    return [{"title": "📺 The visitor's YouTube subscriptions",
             "body": body, "href": first_href or "https://www.youtube.com"}]


def _uploads_answer(entry: Dict, uid: str, consume_lookup) -> Optional[list]:
    token = entry.get("access_token", "")
    who = entry.get("name") or entry.get("email") or "the visitor"
    status, data = _request(
        "GET", _API_BASE + "/channels", token,
        params={"part": "contentDetails,snippet", "mine": "true"})
    if status == 401:
        return _revoked(uid, who)
    if status != 200:
        return None
    channels = _items(data)
    if not channels:
        if not _spend(consume_lookup, uid):
            return None
        body = (f"The visitor ({who}) connected their YouTube "
                "account and is asking about their uploads, but "
                "their account has NO YouTube channel. Tell them "
                "plainly, in persona — do not invent videos.")
        return [{"title": "📺 The visitor's YouTube — no channel",
                 "body": body, "href": "https://www.youtube.com"}]
    uploads_id = (((channels[0].get("contentDetails") or {})
                   .get("relatedPlaylists") or {}).get("uploads") or "")
    chan_title = ((channels[0].get("snippet") or {}).get("title") or "")
    if not uploads_id:
        return None
    status, data = _request(
        "GET", _API_BASE + "/playlistItems", token,
        params={"part": "snippet", "playlistId": uploads_id,
                "maxResults": _MAX_UPLOADS})
    if status == 401:
        return _revoked(uid, who)
    if status != 200:
        return None
    if not _spend(consume_lookup, uid):
        logger.info("YouTube uploads answer skipped: visitor at "
                    "daily lookup cap")
        return None
    items = _items(data)
    if not items:
        body = (f"The visitor ({who}) connected their YouTube "
                f"channel ({chan_title}) and is asking about their "
                "recent uploads. Their channel has NO public "
                "uploads visible to OG. Tell them plainly, in "
                "persona — do not invent videos.")
        return [{"title": "📺 The visitor's YouTube — no uploads",
                 "body": body, "href": "https://www.youtube.com"}]
    lines = []
    first_href = ""
    for it in items:
        snip = it.get("snippet") or {}
        title = snip.get("title", "")
        if not title:
            continue
        vid = ((snip.get("resourceId") or {}).get("videoId") or "")
        when = _fmt_date(snip.get("publishedAt", ""))
        line = f"- \"{title}\""
        if when:
            line += f" — uploaded {when}"
        lines.append(line)
        if vid and not first_href:
            first_href = f"https://www.youtube.com/watch?v={vid}"
    if not lines:
        return None
    body = (f"The visitor ({who}) connected their YouTube channel "
            f"({chan_title}) and is asking about their recent "
            "uploads. Answer ONLY from this list — their actual "
            "uploads, newest first:\n\n" + "\n".join(lines))
    return [{"title": "📺 The visitor's YouTube — recent uploads",
             "body": body, "href": first_href or "https://www.youtube.com"}]


def _playlists_answer(entry: Dict, uid: str, consume_lookup) -> Optional[list]:
    token = entry.get("access_token", "")
    who = entry.get("name") or entry.get("email") or "the visitor"
    status, data = _request(
        "GET", _API_BASE + "/playlists", token,
        params={"part": "snippet,contentDetails", "mine": "true",
                "maxResults": _MAX_PLAYLISTS})
    if status == 401:
        return _revoked(uid, who)
    if status != 200:
        return None
    if not _spend(consume_lookup, uid):
        logger.info("YouTube playlists answer skipped: visitor at "
                    "daily lookup cap")
        return None
    items = _items(data)
    if not items:
        body = (f"The visitor ({who}) connected their YouTube "
                "account and is asking about their playlists. Their "
                "account has NO playlists visible to OG. Tell them "
                "plainly, in persona — do not invent playlists.")
        return [{"title": "📺 The visitor's YouTube — no playlists",
                 "body": body, "href": "https://www.youtube.com"}]
    lines = []
    first_href = ""
    for it in items:
        snip = it.get("snippet") or {}
        title = snip.get("title", "")
        if not title:
            continue
        count = ((it.get("contentDetails") or {}).get("itemCount"))
        line = f"- \"{title}\""
        if isinstance(count, int):
            line += f" ({count} videos)"
        lines.append(line)
        if it.get("id") and not first_href:
            first_href = ("https://www.youtube.com/playlist?list="
                          + str(it["id"]))
    if not lines:
        return None
    body = (f"The visitor ({who}) connected their YouTube account "
            "and is asking about their playlists. Answer ONLY from "
            "this list — their actual playlists:\n\n"
            + "\n".join(lines))
    return [{"title": "📺 The visitor's YouTube playlists",
             "body": body, "href": first_href or "https://www.youtube.com"}]


def _search_answer(entry: Dict, query: str, uid: str,
                   consume_lookup) -> Optional[list]:
    token = entry.get("access_token", "")
    who = entry.get("name") or entry.get("email") or "the visitor"
    status, data = _request(
        "GET", _API_BASE + "/search", token,
        params={"part": "snippet", "type": "video",
                "maxResults": _MAX_SEARCH, "q": query})
    if status == 401:
        return _revoked(uid, who)
    if status != 200:
        return None
    if not _spend(consume_lookup, uid):
        logger.info("YouTube search answer skipped: visitor at "
                    "daily lookup cap")
        return None
    items = _items(data)
    if not items:
        body = (f"The visitor ({who}) asked OG to search YouTube "
                f"for \"{query}\" and the search came back with NO "
                "results. Tell them plainly, in persona — do not "
                "invent videos.")
        return [{"title": "📺 YouTube search — no results",
                 "body": body, "href": "https://www.youtube.com"}]
    lines = []
    first_href = ""
    for it in items:
        snip = it.get("snippet") or {}
        title = snip.get("title", "")
        if not title:
            continue
        channel = snip.get("channelTitle", "")
        when = _fmt_date(snip.get("publishedAt", ""))
        vid = ((it.get("id") or {}).get("videoId") or "")
        line = f"- \"{title}\""
        if channel:
            line += f" by {channel}"
        if when:
            line += f" — {when}"
        lines.append(line)
        if vid and not first_href:
            first_href = f"https://www.youtube.com/watch?v={vid}"
    if not lines:
        return None
    body = (f"The visitor ({who}) asked OG to search YouTube for "
            f"\"{query}\". Answer ONLY from these actual search "
            "results:\n\n" + "\n".join(lines))
    return [{"title": f"📺 YouTube search — {query}",
             "body": body, "href": first_href or "https://www.youtube.com"}]


def youtube_results(job, uid, consume_lookup):
    """Run one parsed YouTube job for this visitor. Returns
    web_search-shaped results on a hit, guidance for enabled-but-
    unconnected/revoked visitors, or None on any miss — disabled,
    upstream down, or budget spent — so the caller falls through
    to the previous search untouched. One unit of the shared
    lookup budget is consumed per REAL answer; a miss or guidance
    consumes nothing."""
    if not job or not YOUTUBE_ENABLED or not uid:
        return None
    kind = job.get("kind")
    raw = _youtube_connection(uid)
    if not raw:
        return _guidance("connect", "the visitor")
    who_hint = raw.get("name") or raw.get("email") or "the visitor"
    entry = _live_connection(uid)
    if not entry:
        return _guidance("reconnect", who_hint)
    if kind == "subs":
        return _subs_answer(entry, uid, consume_lookup)
    if kind == "uploads":
        return _uploads_answer(entry, uid, consume_lookup)
    if kind == "playlists":
        return _playlists_answer(entry, uid, consume_lookup)
    if kind == "search":
        return _search_answer(entry, job.get("query", ""), uid,
                              consume_lookup)
    return None


# ---------------------------------------------------------------------------
# The seam (app.py installs this on the agent, after Rounds 3/4/6/9/10/12/13/14)
# ---------------------------------------------------------------------------

# The YouTube job parsed for the exchange currently being processed.
# All chat processing is serialized under app.py's _memory_lock, so
# a single slot is safe — the same reasoning as the other modules.
_pending = {"job": None}


def install_youtube_tools(agent_instance, get_uid, consume_lookup):
    """Wrap the agent's (already lookup/file/maps/spotify/github/
    hands-wrapped) detect_intent + web_search hooks so a connected
    visitor's YouTube questions try og_youtube FIRST and fall
    through to the previous search on a miss. get_uid is a zero-arg
    callable returning the current visitor's uid; consume_lookup(uid)
    spends one unit of the shared Round 3 lookup budget and returns
    False at the cap. While the feature is disabled the wrapper is
    a pure pass-through — nothing is parsed, forced or spent.
    Persona files never touched."""
    if getattr(agent_instance, "_og_youtube_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["job"] = None
        if YOUTUBE_ENABLED:
            try:
                uid = get_uid()
                job = parse_youtube_intent(str(message)) if uid else None
                if job:
                    _pending["job"] = job
                    if isinstance(intent, dict) \
                            and not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
            except Exception as e:
                logger.warning(f"YouTube trigger check failed: {e}")
                _pending["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _pending.get("job")
        _pending["job"] = None
        if job:
            try:
                results = youtube_results(job, get_uid(), consume_lookup)
            except Exception as e:
                logger.warning(f"YouTube search failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_youtube_installed = True


# --- YouTube account connect routes (dark until enabled) ---------------------

def register_youtube_routes(app):
    """Attach the four /auth/youtube* routes to the FastAPI app.
    Mirrors the Round 10/14 routes one for one."""

    @app.get("/auth/youtube")
    async def youtube_auth_start(raw_request: Request):
        """
        Begin YouTube connect: bounce the visitor to Google's own
        consent page. Answers 404 while the feature is dark (keys
        not set), so nothing about it is discoverable on the live
        site until Brent enables it.
        """
        if not YOUTUBE_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        from urllib.parse import urlencode
        uid = raw_request.cookies.get("ogai_uid")
        fresh_uid = None
        if not uid:
            uid = uuid.uuid4().hex
            fresh_uid = uid
        params = {
            "client_id": YOUTUBE_CLIENT_ID,
            "redirect_uri": YOUTUBE_REDIRECT_URI,
            "response_type": "code",
            "scope": YOUTUBE_SCOPES,
            "state": _youtube_state_for(uid),
            "access_type": "offline",
            "prompt": "consent",
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

    @app.get("/auth/youtube/callback")
    async def youtube_auth_callback(raw_request: Request, code: str = "",
                                    state: str = "", error: str = ""):
        """
        Google sends the visitor back here with a code. The signed
        state tells us which visitor this is; the code is exchanged
        for tokens, the identity is fetched, and the connection is
        stored under their uid. Any failure lands back on the chat
        with ?youtube=failed — no error page.
        """
        if not YOUTUBE_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        uid = _youtube_uid_from_state(state)
        if error or not code or not uid:
            return RedirectResponse(url="/?youtube=failed", status_code=302)
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                token_resp = await client.post(
                    _TOKEN_URL,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": YOUTUBE_REDIRECT_URI,
                        "client_id": YOUTUBE_CLIENT_ID,
                        "client_secret": YOUTUBE_CLIENT_SECRET,
                    })
                if token_resp.status_code != 200:
                    logger.warning(
                        "YouTube token exchange status: "
                        f"{token_resp.status_code}")
                    return RedirectResponse(url="/?youtube=failed",
                                            status_code=302)
                tokens = token_resp.json()
                access_token = tokens.get("access_token", "")
                if not access_token:
                    logger.warning("YouTube token exchange: no token")
                    return RedirectResponse(url="/?youtube=failed",
                                            status_code=302)
                info_resp = await client.get(
                    _USERINFO_URL,
                    headers={"Authorization": f"Bearer {access_token}"})
                profile = info_resp.json() \
                    if info_resp.status_code == 200 else {}
        except Exception as e:
            logger.warning(f"YouTube connect failed: {e}")
            return RedirectResponse(url="/?youtube=failed", status_code=302)
        entry = {
            "google_id": profile.get("sub", ""),
            "name": profile.get("name", ""),
            "email": profile.get("email", ""),
            "access_token": access_token,
            "refresh_token": tokens.get("refresh_token", ""),
            "scope": tokens.get("scope", ""),
            "expires_at": (datetime.now(timezone.utc).timestamp()
                           + int(tokens.get("expires_in", 3600))),
            "connected": datetime.now(timezone.utc).isoformat(),
        }
        _store_entry(uid, entry)
        response = RedirectResponse(url="/?youtube=connected",
                                    status_code=302)
        response.set_cookie(
            "ogai_uid", uid,
            max_age=_cookie_max_age(), path="/", httponly=True,
            samesite="lax")
        return response

    @app.get("/auth/youtube/status")
    async def youtube_auth_status(raw_request: Request):
        """What the slide-over menu needs: is connect enabled, and
        if this visitor is connected, as whom? (Never returns
        tokens.)"""
        entry = None
        if YOUTUBE_ENABLED:
            entry = _youtube_connection(raw_request.cookies.get("ogai_uid"))
        return {
            "enabled": YOUTUBE_ENABLED,
            "connected": bool(entry),
            "email": entry.get("email", "") if entry else "",
            "name": entry.get("name", "") if entry else "",
        }

    @app.post("/auth/youtube/disconnect")
    async def youtube_auth_disconnect(raw_request: Request):
        """Forget this visitor's YouTube connection — tokens
        deleted server-side."""
        if not YOUTUBE_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        uid = raw_request.cookies.get("ogai_uid")
        if uid:
            _drop_entry(uid)
        return {"status": "disconnected"}
