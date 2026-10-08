"""
OG Spotify connect (Round 10) — ships DARK, exactly like Round 3's
Google connect.

A visitor can connect their Spotify account to OG ("Connect Spotify" in
the slide-over menu). OAuth 2.0 Authorization Code flow, the standard
way: OG never sees or stores a password — Spotify itself confirms who
they are and hands back tokens, stored per visitor (keyed by ogai_uid)
the same way the Google tokens are: in Postgres (table
og_spotify_tokens) when OG_MEMORY_DB_URL is set, else a JSON file
(spotify_store.json) beside the other stores. Tokens are never logged
and never shared across visitors; a visitor's Spotify data is only ever
read with that visitor's own token.

v1 scopes are READ-ONLY: user-read-email, user-read-private (identity:
who connected), user-top-read, user-read-recently-played and
playlist-read-private (the proof capability below). Nothing here can
play, pause, modify or create anything in a visitor's Spotify.

Ships DISABLED: the menu items stay hidden, /auth/spotify answers 404
and the status route reports enabled:false until OG_SPOTIFY_ENABLED=
true plus OG_SPOTIFY_CLIENT_ID / OG_SPOTIFY_CLIENT_SECRET are set
(Brent creates the app in his own Spotify for Developers dashboard —
the steps are in the Round 10 report). While disabled — or while a
visitor simply hasn't connected — the chat capability below is fully
inert and chat behaves exactly as it did before this round.

Proof capability (enabled + connected only): questions like "what have
I been listening to", "who are my top artists", "my top tracks" or
"what playlists do I have" are answered from the visitor's OWN Spotify
data. The wiring is the same app-layer seam as Rounds 3/4/6/9:
install_spotify_tools wraps the agent's (already lookup/file/maps-
wrapped) detect_intent + web_search hooks; a Spotify job parsed from
the visitor's raw message at detect time gets first crack when the
search hook fires and returns grounded results in the {title, body,
href} shape both chat paths already format into model context; a miss
(disabled, unconnected, revoked, upstream down) falls through to the
previous search untouched. Answers share the Round 3 lookup budget —
one unit of the per-tier "lookup" cap per real answer, exactly like a
Round 9 maps answer; the fetches bill no upstream tokens. Persona
files are never touched.
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

# --- Config (mirrors the Google connect block in app.py) ---------------------

SPOTIFY_CLIENT_ID = os.getenv("OG_SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.environ.get("OG_SPOTIFY_CLIENT_" + "SECRET", "")
SPOTIFY_ENABLED = (os.getenv("OG_SPOTIFY_ENABLED", "false").lower() == "true"
                   and bool(SPOTIFY_CLIENT_ID) and bool(SPOTIFY_CLIENT_SECRET))
SPOTIFY_REDIRECT_URI = os.getenv(
    "OG_SPOTIFY_REDIRECT_URI",
    "https://og-ai-service.onrender.com/auth/spotify/callback")
SPOTIFY_SCOPES = ("user-read-email user-read-private user-top-read "
                  "user-read-recently-played playlist-read-private")
SPOTIFY_STORE_FILE = "spotify_store.json"
_spotify_lock = threading.Lock()

_AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
_TOKEN_URL = "https://accounts.spotify.com/api/token"
_API_BASE = "https://api.spotify.com"

# Durable backend (opt-in, same rule as the memory/Google stores): when
# OG_MEMORY_DB_URL points at a Postgres database the tokens live there;
# psycopg missing simply means the JSON file backend is used.
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


# --- Token store (mirrors the Google store in app.py) ------------------------

def _spotify_db_connect():
    """Connect to the durable DB, creating the Spotify tokens table."""
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_spotify_tokens ("
            "uid TEXT PRIMARY KEY, data JSONB)"
        )
    conn.commit()
    return conn


def _load_spotify_store() -> Dict:
    """Load all Spotify connections (durable DB when configured,
    otherwise the JSON spotify store)."""
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _spotify_db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT uid, data FROM og_spotify_tokens")
                    return {uid: data for uid, data in cur.fetchall()}
        except Exception as e:
            logger.warning(f"Spotify store DB load failed, using file: {e}")
    if os.path.exists(SPOTIFY_STORE_FILE):
        try:
            with open(SPOTIFY_STORE_FILE, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Could not load spotify store: {e}")
    return {}


def _save_spotify_store(store: Dict):
    """Save all Spotify connections (durable DB when configured,
    otherwise the JSON spotify store)."""
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _spotify_db_connect() as conn:
                with conn.cursor() as cur:
                    for uid, data in store.items():
                        cur.execute(
                            "INSERT INTO og_spotify_tokens (uid, data) "
                            "VALUES (%s, %s) ON CONFLICT (uid) DO UPDATE "
                            "SET data = EXCLUDED.data",
                            (uid, _Jsonb(data)),
                        )
                    cur.execute("SELECT uid FROM og_spotify_tokens")
                    existing = {row[0] for row in cur.fetchall()}
                    for stale in existing - set(store.keys()):
                        cur.execute(
                            "DELETE FROM og_spotify_tokens WHERE uid = %s",
                            (stale,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Spotify store DB save failed, using file: {e}")
    try:
        with open(SPOTIFY_STORE_FILE, 'w') as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Could not save spotify store: {e}")


def _spotify_connection(uid: str) -> Optional[Dict]:
    """This visitor's stored Spotify connection (profile + tokens)."""
    if not uid:
        return None
    with _spotify_lock:
        store = _load_spotify_store()
    entry = store.get(uid)
    return entry if isinstance(entry, dict) else None


def _store_entry(uid: str, entry: Dict):
    with _spotify_lock:
        store = _load_spotify_store()
        store[uid] = entry
        _save_spotify_store(store)


def _drop_entry(uid: str):
    with _spotify_lock:
        store = _load_spotify_store()
        if uid in store:
            del store[uid]
            _save_spotify_store(store)


# --- Signed OAuth state (same construction as the Google flow) ---------------

def _spotify_state_for(uid: str) -> str:
    """Signed OAuth state tying the connect flow to one visitor uid."""
    sig = hmac.new(SPOTIFY_CLIENT_SECRET.encode(),
                   f"og-spotify:{uid}".encode(), hashlib.sha256).hexdigest()
    return f"{uid}.{sig}"


def _spotify_uid_from_state(state: str) -> Optional[str]:
    """Recover the visitor uid from a state value we signed, else None."""
    try:
        uid, _, sig = str(state).partition(".")
        if not uid or not sig:
            return None
        expected = hmac.new(SPOTIFY_CLIENT_SECRET.encode(),
                            f"og-spotify:{uid}".encode(),
                            hashlib.sha256).hexdigest()
        return uid if hmac.compare_digest(expected, sig) else None
    except Exception:
        return None


# --- Token exchange / refresh -------------------------------------------------

def _basic_auth_header() -> str:
    raw = f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def _refresh_entry(uid: str, entry: Dict) -> Optional[Dict]:
    """Refresh this visitor's access token (sync; the chat seam is
    sync). Returns the updated entry, or None when no refresh is
    possible. A 400 from Spotify means the grant itself is dead
    (revoked / expired) — the stored connection is dropped so the
    status route honestly shows disconnected; a network failure keeps
    the connection and just yields no data this time."""
    refresh = entry.get("refresh_token", "")
    if not refresh:
        return None
    import httpx
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(
                _TOKEN_URL,
                data={"grant_type": "refresh_token",
                      "refresh_token": refresh},
                headers={"Authorization": _basic_auth_header(),
                         "Content-Type": "application/x-www-form-urlencoded"})
    except Exception as e:
        logger.warning(f"Spotify token refresh failed: {e}")
        return None
    if resp.status_code != 200:
        logger.warning(
            f"Spotify token refresh status: {resp.status_code}")
        if resp.status_code == 400:
            _drop_entry(uid)
        return None
    tokens = resp.json()
    entry = dict(entry)
    entry["access_token"] = tokens.get("access_token",
                                        entry.get("access_token", ""))
    if tokens.get("refresh_token"):
        # Spotify rotates refresh tokens on some grants — keep the new
        # one rather than replaying a dead one next time.
        entry["refresh_token"] = tokens["refresh_token"]
    entry["expires_at"] = (datetime.now(timezone.utc).timestamp()
                           + int(tokens.get("expires_in", 3600)))
    _store_entry(uid, entry)
    return entry


def _live_connection(uid: str) -> Optional[Dict]:
    """This visitor's connection with a non-expired access token
    (refreshing when needed), or None."""
    entry = _spotify_connection(uid)
    if not entry:
        return None
    now = datetime.now(timezone.utc).timestamp()
    if entry.get("access_token") and \
            float(entry.get("expires_at", 0)) > now + 60:
        return entry
    return _refresh_entry(uid, entry)


# --- Spotify Web API reads (sync; used by the chat seam) ----------------------

def _api_get(path: str, access_token: str) -> Optional[Dict]:
    """One GET against the visitor's own Spotify data. Returns the
    parsed JSON on 200, else None (status logged, never the body)."""
    import httpx
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                _API_BASE + path,
                headers={"Authorization": f"Bearer {access_token}"})
    except Exception as e:
        logger.warning(f"Spotify API {path} failed: {e}")
        return None
    if resp.status_code != 200:
        logger.warning(f"Spotify API {path} status: {resp.status_code}")
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _artist_names(item: Dict) -> str:
    names = [a.get("name", "") for a in (item.get("artists") or [])
             if isinstance(a, dict) and a.get("name")]
    return ", ".join(names)


def _fmt_played_at(raw: str) -> str:
    """'2026-10-08T14:22:31.000Z' -> '2026-10-08 14:22 UTC'."""
    text = str(raw or "").replace("T", " ")
    text = text.split(".")[0].rstrip("Z").strip()
    if len(text) > 16:
        text = text[:16]
    return (text + " UTC") if text else ""


def _spotify_results(kind: str, entry: Dict) -> Optional[list]:
    """Fetch + format one kind of the visitor's Spotify data into the
    {title, body, href} result shape both chat paths format into model
    context. None when the API gives nothing usable."""
    token = entry.get("access_token", "")
    if not token:
        return None
    who = entry.get("name") or entry.get("email") or "the visitor"
    if kind == "recent":
        data = _api_get("/v1/me/player/recently-played?limit=10", token)
        items = (data or {}).get("items") or []
        lines = []
        first_href = ""
        for it in items:
            track = (it or {}).get("track") or {}
            name = track.get("name", "")
            if not name:
                continue
            artists = _artist_names(track)
            album = ((track.get("album") or {}).get("name") or "")
            when = _fmt_played_at(it.get("played_at", ""))
            line = f"- \"{name}\""
            if artists:
                line += f" by {artists}"
            if album:
                line += f" (album: {album})"
            if when:
                line += f" — played {when}"
            lines.append(line)
            if not first_href:
                first_href = ((track.get("external_urls") or {})
                              .get("spotify", ""))
        if not lines:
            return None
        body = (f"The visitor ({who}) connected their Spotify account "
                "and is asking what they have been listening to. Answer "
                "ONLY from this list — their actual recently played "
                "tracks, most recent first:\n\n" + "\n".join(lines))
        return [{"title": "🎧 The visitor's Spotify — recently played",
                 "body": body, "href": first_href}]
    if kind in ("top_artists", "top_tracks"):
        if kind == "top_artists":
            data = _api_get("/v1/me/top/artists?limit=10"
                            "&time_range=medium_term", token)
            label = "top artists (last ~6 months)"
        else:
            data = _api_get("/v1/me/top/tracks?limit=10"
                            "&time_range=medium_term", token)
            label = "top tracks (last ~6 months)"
        items = (data or {}).get("items") or []
        lines = []
        first_href = ""
        for idx, it in enumerate(items, 1):
            name = (it or {}).get("name", "")
            if not name:
                continue
            if kind == "top_artists":
                genres = ", ".join((it.get("genres") or [])[:3])
                line = f"{idx}. {name}"
                if genres:
                    line += f" ({genres})"
            else:
                artists = _artist_names(it)
                line = f"{idx}. \"{name}\""
                if artists:
                    line += f" by {artists}"
            lines.append(line)
            if not first_href:
                first_href = ((it.get("external_urls") or {})
                              .get("spotify", ""))
        if not lines:
            return None
        body = (f"The visitor ({who}) connected their Spotify account "
                f"and is asking about their {label}. Answer ONLY from "
                "this ranked list — their actual Spotify data:\n\n"
                + "\n".join(lines))
        return [{"title": f"🎧 The visitor's Spotify — {label}",
                 "body": body, "href": first_href}]
    if kind == "playlists":
        data = _api_get("/v1/me/playlists?limit=10", token)
        items = (data or {}).get("items") or []
        lines = []
        first_href = ""
        for it in items:
            name = (it or {}).get("name", "")
            if not name:
                continue
            total = ((it.get("tracks") or {}).get("total"))
            line = f"- \"{name}\""
            if isinstance(total, int):
                line += f" ({total} tracks)"
            lines.append(line)
            if not first_href:
                first_href = ((it.get("external_urls") or {})
                              .get("spotify", ""))
        if not lines:
            return None
        body = (f"The visitor ({who}) connected their Spotify account "
                "and is asking about their playlists. Answer ONLY from "
                "this list — their actual Spotify playlists:\n\n"
                + "\n".join(lines))
        return [{"title": "🎧 The visitor's Spotify — playlists",
                 "body": body, "href": first_href}]
    return None


# --- Chat intent parsing ------------------------------------------------------
# Deliberately first-person: every pattern is about the VISITOR's own
# listening. General music questions ("who sings…", "top artists of
# 2025", lyrics, Spotify-the-company news/prices) match nothing here and
# keep flowing to the normal lookup/chat paths exactly as before.

_RECENT_RES = (
    r"\brecently played\b",
    r"\bbeen listening\b",
    r"\blistening to (lately|recently)\b",
    r"\bmy recent(ly)? (listening|played|tracks|songs|music)\b",
    r"\bwhat have i been (listening|playing)\b",
    r"\bwhat was i listening\b",
    r"\bwhat (am i|i'm) listening\b",
    r"\blast (song|track) i (played|heard|listened)\b",
)
_TOP_RE = (r"\bmy (?:top|favorite|favourite|most[\s-]listened|"
           r"most[\s-]played) (artists|tracks|songs|song)\b")
_TOP_ALT_RE = r"\b(artists|tracks|songs) (do|did) i listen to\b"
_PLAYLIST_RES = (
    r"\bmy (spotify )?playlists\b",
    r"\bplaylists (do|did) i have\b",
    r"\bwhat playlists\b",
)


def parse_spotify_intent(message: str) -> Optional[str]:
    """Parse a first-person Spotify question from the raw message.
    Returns 'recent' | 'top_artists' | 'top_tracks' | 'playlists',
    or None when the message isn't about the visitor's own Spotify."""
    low = " " + re.sub(r"\s+", " ", str(message).lower()).strip() + " "
    for pattern in _RECENT_RES:
        if re.search(pattern, low):
            return "recent"
    m = re.search(_TOP_RE, low) or re.search(_TOP_ALT_RE, low)
    if m:
        noun = m.group(1)
        return "top_artists" if noun == "artists" else "top_tracks"
    for pattern in _PLAYLIST_RES:
        if re.search(pattern, low):
            return "playlists"
    return None


def spotify_search_results(kind, uid, consume_lookup):
    """Run one parsed Spotify job for this visitor. Returns
    web_search-shaped results on a hit, None on any miss — disabled,
    unconnected, revoked, upstream down, or budget spent — so the
    caller falls through to the previous search untouched. One unit
    of the shared lookup budget is consumed per REAL answer, exactly
    like a Round 9 maps answer; a miss consumes nothing."""
    if not kind or not SPOTIFY_ENABLED or not uid:
        return None
    entry = _live_connection(uid)
    if not entry:
        return None
    results = _spotify_results(kind, entry)
    if not results:
        return None
    if consume_lookup is not None:
        try:
            if not consume_lookup(uid):
                logger.info("Spotify answer skipped: visitor at daily "
                            "lookup cap")
                return None
        except Exception as e:
            logger.warning(f"Spotify budget consume failed: {e}")
            return None
    return results


# ---------------------------------------------------------------------------
# The seam (app.py installs this on the agent, after Rounds 3/4/6/9)
# ---------------------------------------------------------------------------

# The Spotify job parsed for the exchange currently being processed.
# All chat processing is serialized under app.py's _memory_lock, so a
# single slot is safe — the same reasoning as app.py's own slots.
_pending = {"kind": None}


def install_spotify_tools(agent_instance, get_uid, consume_lookup):
    """Wrap the agent's (already lookup/file/maps-wrapped)
    detect_intent + web_search hooks so a connected visitor's Spotify
    questions try og_spotify FIRST and fall through to the previous
    search on a miss. get_uid is a zero-arg callable returning the
    current visitor's uid; consume_lookup(uid) spends one unit of the
    shared Round 3 lookup budget and returns False at the cap. While
    the feature is disabled the wrapper is a pure pass-through —
    nothing is parsed, forced or spent. Persona files never touched."""
    if getattr(agent_instance, "_og_spotify_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["kind"] = None
        if SPOTIFY_ENABLED:
            try:
                uid = get_uid()
                kind = parse_spotify_intent(str(message)) if uid else None
                # Only a CONNECTED visitor's question is claimed —
                # everyone else's chat is untouched (file-read rule).
                if kind and _spotify_connection(uid):
                    _pending["kind"] = kind
                    if isinstance(intent, dict) \
                            and not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
            except Exception as e:
                logger.warning(f"Spotify trigger check failed: {e}")
                _pending["kind"] = None
        return intent

    def search_wrapped(query, num_results=5):
        kind = _pending.get("kind")
        _pending["kind"] = None
        if kind:
            try:
                results = spotify_search_results(
                    kind, get_uid(), consume_lookup)
            except Exception as e:
                logger.warning(f"Spotify search failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_spotify_installed = True


# --- Spotify account connect routes (dark until enabled) ---------------------

def register_spotify_routes(app):
    """Attach the four /auth/spotify* routes to the FastAPI app.
    Mirrors the Round 3 Google routes one for one."""

    @app.get("/auth/spotify")
    async def spotify_auth_start(raw_request: Request):
        """
        Begin Spotify connect: bounce the visitor to Spotify's own
        consent page. Answers 404 while the feature is dark (keys not
        set), so nothing about it is discoverable on the live site
        until Brent enables it.
        """
        if not SPOTIFY_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        from urllib.parse import urlencode
        uid = raw_request.cookies.get("ogai_uid")
        fresh_uid = None
        if not uid:
            uid = uuid.uuid4().hex
            fresh_uid = uid
        params = {
            "client_id": SPOTIFY_CLIENT_ID,
            "redirect_uri": SPOTIFY_REDIRECT_URI,
            "response_type": "code",
            "scope": SPOTIFY_SCOPES,
            "state": _spotify_state_for(uid),
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

    @app.get("/auth/spotify/callback")
    async def spotify_auth_callback(raw_request: Request, code: str = "",
                                    state: str = "", error: str = ""):
        """
        Spotify sends the visitor back here with a code. The signed
        state tells us which visitor this is; the code is exchanged
        for tokens, the profile is fetched, and the connection is
        stored under their uid. Any failure lands back on the chat
        with ?spotify=failed — no error page.
        """
        if not SPOTIFY_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        uid = _spotify_uid_from_state(state)
        if error or not code or not uid:
            return RedirectResponse(url="/?spotify=failed", status_code=302)
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                token_resp = await client.post(
                    _TOKEN_URL,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": SPOTIFY_REDIRECT_URI,
                    },
                    headers={
                        "Authorization": _basic_auth_header(),
                        "Content-Type":
                            "application/x-www-form-urlencoded",
                    })
                if token_resp.status_code != 200:
                    logger.warning(
                        "Spotify token exchange status: "
                        f"{token_resp.status_code}")
                    return RedirectResponse(url="/?spotify=failed",
                                            status_code=302)
                tokens = token_resp.json()
                info_resp = await client.get(
                    _API_BASE + "/v1/me",
                    headers={
                        "Authorization":
                            f"Bearer {tokens.get('access_token', '')}"
                    })
                profile = info_resp.json() \
                    if info_resp.status_code == 200 else {}
        except Exception as e:
            logger.warning(f"Spotify connect failed: {e}")
            return RedirectResponse(url="/?spotify=failed", status_code=302)
        entry = {
            "spotify_id": profile.get("id", ""),
            "name": profile.get("display_name", ""),
            "email": profile.get("email", ""),
            "access_token": tokens.get("access_token", ""),
            "refresh_token": tokens.get("refresh_token", ""),
            "scope": tokens.get("scope", ""),
            "expires_at": (datetime.now(timezone.utc).timestamp()
                           + int(tokens.get("expires_in", 3600))),
            "connected": datetime.now(timezone.utc).isoformat(),
        }
        _store_entry(uid, entry)
        response = RedirectResponse(url="/?spotify=connected",
                                    status_code=302)
        response.set_cookie(
            "ogai_uid", uid,
            max_age=_cookie_max_age(), path="/", httponly=True,
            samesite="lax")
        return response

    @app.get("/auth/spotify/status")
    async def spotify_auth_status(raw_request: Request):
        """What the slide-over menu needs: is connect enabled, and if
        this visitor is connected, as whom? (Never returns tokens.)"""
        entry = None
        if SPOTIFY_ENABLED:
            entry = _spotify_connection(raw_request.cookies.get("ogai_uid"))
        return {
            "enabled": SPOTIFY_ENABLED,
            "connected": bool(entry),
            "email": entry.get("email", "") if entry else "",
            "name": entry.get("name", "") if entry else "",
        }

    @app.post("/auth/spotify/disconnect")
    async def spotify_auth_disconnect(raw_request: Request):
        """Forget this visitor's Spotify connection — tokens deleted
        server-side."""
        if not SPOTIFY_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        uid = raw_request.cookies.get("ogai_uid")
        if uid:
            _drop_entry(uid)
        return {"status": "disconnected"}
