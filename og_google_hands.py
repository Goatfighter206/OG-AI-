"""
OG Google hands (Round 12) — Gmail read/search + Google Calendar
read & add, riding the Round 3 Google connection.

Round 3 connected a visitor's Google account for IDENTITY only
(openid/email/profile): OG learned who's talking, nothing more. This
round gives OG hands on that same connection — the visitor can ask
about THEIR OWN mail and calendar, and OG answers from the real data:

  * Gmail SEARCH + READ (read-only): "any email from my landlord",
    "search my mail for the invoice", "what's my latest email" run a
    real Gmail API search with the visitor's own token. Answers are
    grounded ONLY in the returned subjects/senders/dates/snippets.
    Snippet-level fetches by default; a full body is pulled only when
    the visitor explicitly asks to read that message. v1 never sends,
    replies, deletes or labels anything.
  * Calendar READ: "what's on my calendar tomorrow / this week" lists
    real events off the visitor's primary calendar, formatted in the
    calendar's own timezone (from the API).
  * Calendar ADD: "add dentist Tuesday 3pm to my calendar" creates the
    event on the visitor's own calendar from their explicit in-chat
    instruction and confirms with title + formatted time. Anything
    essential missing (no date or no time) becomes ONE clarifying
    question instead — nothing half-guessed is ever created.

Extra scopes (gmail.readonly, calendar.readonly, calendar.events)
ride the SAME connect flow, but only when OG_GOOGLE_HANDS_ENABLED=
true (default OFF): with the flag off, the Round 3 scopes and
behavior are byte-for-byte what they were. Google requires fresh
consent for new scopes, so a connection stored under v1 scopes (or
with no scope record at all — every pre-Round-12 connection) that
asks for a hands capability gets an in-persona reconnect prompt
instead of an error or invented data. While OG_GOOGLE_ENABLED is off
entirely — or while the hands flag itself is off — this module is a
pure pass-through: nothing is parsed, claimed or spent, and Round 3
behavior is exactly what it was.

Wiring is the same app-layer seam as Rounds 3/4/6/9/10:
install_google_hands wraps the agent's (already
lookup/file/maps/spotify-wrapped) detect_intent + web_search hooks;
a hands job parsed from the visitor's raw message at detect time gets
first crack when the search hook fires and returns grounded results
in the {title, body, href} shape both chat paths format into model
context. Hands answers share the Round 3 lookup budget — one unit
per real answer (a created event counts; clarifying questions,
connect/reconnect prompts and misses spend nothing). Tokens come
from Round 3's per-visitor store (bound in from app.py), are
refreshed here when expired, and are never logged; email body
contents are never logged either. Persona files are never touched.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# --- Config -------------------------------------------------------------------

HANDS_ENABLED = os.getenv(
    "OG_GOOGLE_HANDS_ENABLED", "false").lower() == "true"

BASE_SCOPES = "openid email profile"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
CAL_READ_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
CAL_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
HANDS_SCOPES = f"{GMAIL_SCOPE} {CAL_READ_SCOPE} {CAL_EVENTS_SCOPE}"

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_CAL_BASE = "https://www.googleapis.com/calendar/v3"
_GMAIL_WEB = "https://mail.google.com/mail/u/0/#inbox"
_CAL_WEB = "https://calendar.google.com/calendar/u/0/r"
_MAX_RESULTS = 5          # messages shown per Gmail answer
_MAX_EVENTS = 10          # events shown per calendar answer
_BODY_CAP = 3500          # chars of a full email body handed to the model


def requested_scope() -> str:
    """The scope string the Round 3 connect flow asks Google for:
    identity only while the extra rounds are off; identity + hands
    scopes when OG_GOOGLE_HANDS_ENABLED=true; the Round 13 Drive/
    Tasks scopes appended when OG_GOOGLE_DRIVE_ENABLED=true (the
    two flags are independent — each round's scopes join only while
    its own flag is on)."""
    scope = f"{BASE_SCOPES} {HANDS_SCOPES}" if HANDS_ENABLED \
        else BASE_SCOPES
    try:
        import og_google_drive as _drive
        extra = _drive.extra_scopes()
    except Exception:
        extra = ""
    if extra:
        scope = f"{scope} {extra}"
    return scope


# Bound by app.py (install call): Round 3's store + config, which
# app.py owns — {"connection", "load_store", "save_store", "lock",
# "client_id", "client_secret", "google_enabled"}.
_deps: Dict = {}


def _google_enabled() -> bool:
    return bool(_deps.get("google_enabled"))


def _connection(uid: str) -> Optional[Dict]:
    fn = _deps.get("connection")
    if fn is None or not uid:
        return None
    try:
        return fn(uid)
    except Exception as e:
        logger.warning(f"Google hands connection lookup failed: {e}")
        return None


# --- Granted scopes + token lifecycle ------------------------------------------

def _granted_scopes(entry: Dict) -> set:
    """Scopes this connection actually holds, from the record stored
    at connect time. A pre-Round-12 entry has no record at all —
    that's exactly the v1-only case (identity scopes only)."""
    return set(str((entry or {}).get("scope", "")).split())


def _scope_ok(entry: Dict, kind: str) -> bool:
    granted = _granted_scopes(entry)
    if kind.startswith("gmail"):
        return GMAIL_SCOPE in granted
    if kind == "cal_add":
        return CAL_EVENTS_SCOPE in granted
    # cal_read: either calendar scope can list events.
    return CAL_READ_SCOPE in granted or CAL_EVENTS_SCOPE in granted


def _store_entry(uid: str, entry: Dict):
    lock = _deps.get("lock")
    load, save = _deps.get("load_store"), _deps.get("save_store")
    if load is None or save is None:
        return
    if lock is not None:
        with lock:
            store = load()
            store[uid] = entry
            save(store)
    else:
        store = load()
        store[uid] = entry
        save(store)


def _drop_entry(uid: str):
    lock = _deps.get("lock")
    load, save = _deps.get("load_store"), _deps.get("save_store")
    if load is None or save is None:
        return

    def _drop():
        store = load()
        if uid in store:
            del store[uid]
            save(store)
    if lock is not None:
        with lock:
            _drop()
    else:
        _drop()


def _refresh_entry(uid: str, entry: Dict) -> Optional[Dict]:
    """Refresh this visitor's Google access token (sync; the chat
    seam is sync). Returns the updated entry, or None when no refresh
    is possible. A 400 means the grant itself is dead (revoked) — the
    stored connection is dropped so the status route honestly shows
    disconnected; a network failure keeps the connection and just
    yields no data this time. Token values are never logged."""
    refresh = entry.get("refresh_token", "")
    if not refresh:
        return None
    import httpx
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.post(_TOKEN_URL, data={
                "client_id": _deps.get("client_id", ""),
                "client_secret": _deps.get("client_secret", ""),
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            })
    except Exception as e:
        logger.warning(f"Google token refresh failed: {e}")
        return None
    if resp.status_code != 200:
        logger.warning(f"Google token refresh status: {resp.status_code}")
        if resp.status_code == 400:
            _drop_entry(uid)
        return None
    try:
        tokens = resp.json()
    except Exception:
        return None
    entry = dict(entry)
    entry["access_token"] = tokens.get(
        "access_token", entry.get("access_token", ""))
    if tokens.get("scope"):
        entry["scope"] = tokens["scope"]
    entry["expires_at"] = (datetime.now(timezone.utc).timestamp()
                           + int(tokens.get("expires_in", 3600)))
    _store_entry(uid, entry)
    return entry


def _live_entry(uid: str) -> Optional[Dict]:
    """This visitor's connection with a non-expired access token
    (refreshing when needed), or None."""
    entry = _connection(uid)
    if not entry:
        return None
    now = datetime.now(timezone.utc).timestamp()
    if entry.get("access_token") \
            and float(entry.get("expires_at", 0)) > now + 60:
        return entry
    return _refresh_entry(uid, entry)


# --- Google API transport (ONE helper — the tests stub this) ---------------------

def _api(method: str, url: str, token: str,
         params: Optional[Dict] = None,
         json_body: Optional[Dict] = None) -> Optional[Dict]:
    """One call against the visitor's own Google data with their own
    token. Returns the parsed JSON on success, else None (status
    logged, never the body — bodies carry the visitor's mail)."""
    import httpx
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.request(
                method, url, params=params, json=json_body,
                headers={"Authorization": f"Bearer {token}"})
    except Exception as e:
        logger.warning(f"Google API {method} {url} failed: {e}")
        return None
    if resp.status_code not in (200, 201):
        logger.warning(
            f"Google API {method} {url} status: {resp.status_code}")
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


# --- Intent parsing --------------------------------------------------------------
# Deliberately first-person: every pattern is about the VISITOR's own
# mail/calendar. General questions ("what's a good email provider",
# "how do calendars work", news about Google) match nothing here and
# keep flowing to the normal lookup/chat paths exactly as before.

_WS = re.compile(r"\s+")
_GMAIL_HINT = re.compile(
    r"\b(gmail|e-?mails?|inbox|my mail)\b")
_CAL_ADD_RE = re.compile(
    r"\b(add|put|schedule|book|create)\b[^.?!]*\bcalendar\b")
_GMAIL_READ_RE = re.compile(
    r"\b(read|open)\b[^.?!]*\b(e-?mail|mail|message|it|that|one)\b")
_GMAIL_LATEST_RE = re.compile(
    r"\b(latest|newest|most recent|last)\s+(e-?mail|message)\b|"
    r"\bwhat'?s\s+(in\s+)?my\s+(inbox|gmail|mail)\b|"
    r"\bcheck\s+my\s+(e-?mail|inbox|gmail|mail)\b|"
    r"\bany\s+new\s+(e-?mail|messages?)\b")
_GMAIL_SEARCH_RE = re.compile(
    r"\b(search|find|look)\b[^.?!]*\b(mail|gmail|e-?mail|inbox)\b|"
    r"\b(e-?mails?|messages?)\s+(from|about|regarding|mentioning)\b|"
    r"\bany\s+e-?mail\s+from\b|"
    r"\bdo\s+i\s+have\s+(an?\s+)?e-?mail\b|"
    r"\bmy\s+(e-?mail|inbox|gmail)\b")
_CAL_READ_RE = re.compile(
    r"\bmy\s+(calendar|schedule)\b|"
    r"\bwhat'?s\s+on\s+my\s+(calendar|schedule)\b|"
    r"\bdo\s+i\s+have\s+anything\s+(on|scheduled|planned)\b|"
    r"\b(anything|something)\s+on\s+(today|tomorrow)\b|"
    r"\bmy\s+(meetings|events|appointments)\b")


def _clean(text: str) -> str:
    return " " + _WS.sub(" ", str(text).lower()).strip() + " "


def _gmail_query(low: str) -> str:
    """Build a Gmail search string from the visitor's ask. The caller
    has already decided this is a mail search; an empty result means
    'just show the latest mail'."""
    q = ""
    m = re.search(r"\bfrom\s+([a-z0-9 .&'\-]+?)(?=\s+(?:about|regarding|"
                  r"on|with)\b|[?.!,]|$)", low)
    if m:
        who = m.group(1).strip()
        who = re.sub(r"^(my|the)\s+", "", who)
        if who:
            q = f'from:"{who}"' if " " in who else f"from:{who}"
    m = re.search(r"\b(?:for|about|regarding|mentioning|containing)\s+"
                  r"(.+)$", low)
    if m:
        words = m.group(1).strip(" ?.!,")
        words = re.sub(
            r"\s+(in|on|from)\s+(my\s+)?(gmail|inbox|e-?mail|mail)$",
            "", words)
        words = re.sub(r"^(the|a|an)\s+", "", words).strip(" \"'")
        if words and words not in ("my email", "my mail", "my gmail",
                                   "my inbox", "email", "mail"):
            q = (q + " " + words).strip() if q else words
    return q[:120]


_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
             "friday": 4, "saturday": 5, "sunday": 6}
_WD_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_MO_SHORT = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul",
             "Aug", "Sep", "Oct", "Nov", "Dec"]


def _tzinfo(name: str):
    try:
        return ZoneInfo(name) if name else timezone.utc
    except Exception:
        return timezone.utc


def _fmt_dt(dt: datetime) -> str:
    """'Tue, Oct 13 at 3:00 PM' in dt's own timezone."""
    hour = dt.strftime("%I").lstrip("0") or "12"
    return (f"{_WD_SHORT[dt.weekday()]}, {_MO_SHORT[dt.month]} "
            f"{dt.day} at {hour}:{dt.strftime('%M')} "
            f"{dt.strftime('%p')}")


def _fmt_day(d) -> str:
    return (f"{_WD_SHORT[d.weekday()]}, {_MO_SHORT[d.month]} {d.day}")


def _cal_window(low: str, tz, now: datetime):
    """(timeMin, timeMax, label) for a calendar-read ask, as aware
    datetimes in the calendar's timezone."""
    today = now.date()
    day = timedelta(days=1)
    m = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|"
                  r"saturday|sunday)\b", low)
    if "tomorrow" in low:
        start = today + day
        return (datetime(start.year, start.month, start.day, tzinfo=tz),
                datetime(start.year, start.month, start.day,
                         tzinfo=tz) + day, "tomorrow")
    if "next week" in low:
        start = today + timedelta(days=7)
        end = start + timedelta(days=7)
        return (datetime(start.year, start.month, start.day, tzinfo=tz),
                datetime(end.year, end.month, end.day, tzinfo=tz),
                "next week")
    if "this week" in low or "week" in low:
        return (datetime(today.year, today.month, today.day, tzinfo=tz),
                datetime(today.year, today.month, today.day,
                         tzinfo=tz) + timedelta(days=7), "this week")
    if m:
        target = today + timedelta(
            days=(_WEEKDAYS[m.group(1)] - today.weekday()) % 7)
        return (datetime(target.year, target.month, target.day,
                         tzinfo=tz),
                datetime(target.year, target.month, target.day,
                         tzinfo=tz) + day, f"on {m.group(1).title()}")
    if "today" in low or "tonight" in low:
        return (datetime(today.year, today.month, today.day, tzinfo=tz),
                datetime(today.year, today.month, today.day,
                         tzinfo=tz) + day, "today")
    # Plain "what's on my calendar" — the next 7 days.
    return (datetime(today.year, today.month, today.day, tzinfo=tz),
            datetime(today.year, today.month, today.day, tzinfo=tz)
            + timedelta(days=7), "the next 7 days")


def _remove_text(title: str, text: str) -> str:
    """Remove the first case-insensitive occurrence of text (a phrase
    matched in the lowercased message) from the original-case title."""
    if not text:
        return title
    i = title.lower().find(text.lower())
    if i < 0:
        return title
    return title[:i] + " " + title[i + len(text):]


def _parse_add(message: str, tz, now: datetime) -> Dict:
    """Parse a calendar-add ask into {title, start, end} or mark it
    incomplete with what's missing. Nothing is created here — the
    caller creates only a complete parse, per the design."""
    low = _clean(message)
    # --- time ---
    hour = minute = None
    time_text = ""
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", low)
    if m:
        hour = int(m.group(1)) % 12
        if m.group(3) == "pm":
            hour += 12
        minute = int(m.group(2) or 0)
        time_text = m.group(0).strip()
    else:
        m = re.search(r"\b(\d{1,2}):(\d{2})\b", low)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2))
            time_text = m.group(0).strip()
        else:
            m = re.search(r"\b(noon|midday|midnight)\b", low)
            if m:
                hour = 0 if m.group(1) == "midnight" else 12
                minute = 0
                time_text = m.group(0).strip()
    # --- date ---
    date = None
    date_text = ""
    today = now.date()
    m = re.search(r"\bday after tomorrow\b", low)
    if m:
        date = today + timedelta(days=2)
        date_text = m.group(0).strip()
    if date is None:
        m = re.search(r"\btomorrow\b", low)
        if m:
            date = today + timedelta(days=1)
            date_text = m.group(0).strip()
    if date is None:
        m = re.search(r"\b(today|tonight)\b", low)
        if m:
            date = today
            date_text = m.group(0).strip()
    if date is None:
        m = re.search(r"\bnext\s+(monday|tuesday|wednesday|thursday|"
                      r"friday|saturday|sunday)\b", low)
        if m:
            delta = (_WEEKDAYS[m.group(1)] - today.weekday()) % 7 + 7
            date = today + timedelta(days=delta)
            date_text = m.group(0).strip()
    if date is None:
        m = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|"
                      r"saturday|sunday)\b", low)
        if m:
            delta = (_WEEKDAYS[m.group(1)] - today.weekday()) % 7
            date = today + timedelta(days=delta)
            date_text = m.group(0).strip()
            if delta == 0 and hour is not None:
                cand = datetime(date.year, date.month, date.day,
                                hour, minute or 0, tzinfo=tz)
                if cand <= now:  # "Tuesday 3pm" said Tue 4pm = next wk
                    date = date + timedelta(days=7)
    if date is None:
        m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|"
                      r"nov|dec)[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?\b",
                      low)
        if m:
            try:
                date = datetime(now.year, _MONTHS[m.group(1)],
                                int(m.group(2))).date()
                date_text = m.group(0).strip()
            except ValueError:
                date = None
    if date is None:
        m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", low)
        if m:
            year = int(m.group(3)) if m.group(3) else now.year
            if year < 100:
                year += 2000
            try:
                date = datetime(year, int(m.group(1)),
                                int(m.group(2))).date()
                date_text = m.group(0).strip()
            except ValueError:
                date = None
    # --- title: the ORIGINAL message minus the scaffolding ---
    title = _WS.sub(" ", str(message)).strip()
    title = re.sub(r"^\s*(please\s+)?(add|put|schedule|book|create)"
                   r"\s+", "", title, flags=re.IGNORECASE)
    title = _remove_text(title, "to my calendar")
    title = _remove_text(title, "on my calendar")
    title = _remove_text(title, "in my calendar")
    title = _remove_text(title, "my calendar")
    title = _remove_text(title, date_text)
    if time_text:
        title = re.sub(r"\bat\s*$", "", _remove_text(title, time_text))
        title = _remove_text(title, time_text)
    title = _WS.sub(" ", title).strip(" ,.-")
    title = re.sub(r"^(to|on|at|for)\s+", "", title).strip(" ,.-")
    title = re.sub(r"\s+(to|on|at)$", "", title).strip(" ,.-")
    if title:
        title = title[0].upper() + title[1:]
    missing = []
    if date is None:
        missing.append("the day")
    if hour is None:
        missing.append("the time")
    if not title:
        missing.append("what it's called")
    job = {"kind": "cal_add", "complete": not missing,
           "missing": missing, "title": title}
    if not missing:
        start = datetime(date.year, date.month, date.day,
                         hour, minute or 0, tzinfo=tz)
        job["start"] = start
        job["end"] = start + timedelta(hours=1)
    return job


def parse_google_intent(message: str, tz=None, now: datetime = None
                        ) -> Optional[Dict]:
    """Parse a first-person Google hands job from the raw message:
    {'kind': 'gmail_search'|'gmail_latest'|'gmail_read'|'cal_read'|
    'cal_add', ...} or None when the message isn't the visitor asking
    about their own mail/calendar. tz/now only matter for cal_add
    parsing (defaults: UTC/now) — the search-time caller re-parses
    cal_add with the calendar's real timezone."""
    low = _clean(message)
    if _CAL_ADD_RE.search(low) and _GMAIL_HINT.search(low) is None:
        job = _parse_add(str(message), tz or timezone.utc,
                         now or datetime.now(timezone.utc))
        return job
    if _GMAIL_HINT.search(low):
        q = _gmail_query(low)
        if _GMAIL_READ_RE.search(low):
            # Explicit read: pull that message's full text (newest
            # match when no query terms were given).
            return {"kind": "gmail_read", "query": q}
        if q or _GMAIL_SEARCH_RE.search(low):
            return ({"kind": "gmail_search", "query": q} if q
                    else {"kind": "gmail_latest"})
        if _GMAIL_LATEST_RE.search(low):
            return {"kind": "gmail_latest"}
        return None
    if _CAL_READ_RE.search(low):
        return {"kind": "cal_read"}
    return None


# --- Gmail reads -----------------------------------------------------------------

def _header_map(meta: Dict) -> Dict:
    headers = {}
    for h in ((meta.get("payload") or {}).get("headers") or []):
        if isinstance(h, dict) and h.get("name"):
            headers[h["name"].lower()] = h.get("value", "")
    return headers


def _fmt_mail_date(raw: str) -> str:
    try:
        dt = parsedate_to_datetime(raw)
        if dt is not None:
            return f"{_WD_SHORT[dt.weekday()]}, {_MO_SHORT[dt.month]} " \
                   f"{dt.day}, {dt.year}"
    except Exception:
        pass
    return str(raw or "")[:25]


def _gmail_list(token: str, query: str, limit: int) -> Optional[list]:
    """Message metadata for a search (snippet-level only). None when
    the API itself failed; [] when the search honestly matched
    nothing — the caller treats those very differently."""
    params: Dict = {"maxResults": limit}
    if query:
        params["q"] = query
    data = _api("GET", f"{_GMAIL_BASE}/messages", token, params=params)
    if data is None:
        return None
    out = []
    for ref in (data.get("messages") or [])[:limit]:
        mid = (ref or {}).get("id")
        if not mid:
            continue
        meta = _api("GET", f"{_GMAIL_BASE}/messages/{mid}", token,
                    params={"format": "metadata",
                            "metadataHeaders": ["Subject", "From",
                                                "Date"]})
        if not meta:
            continue
        headers = _header_map(meta)
        out.append({
            "id": mid,
            "subject": headers.get("subject", "(no subject)"),
            "from": headers.get("from", ""),
            "date": _fmt_mail_date(headers.get("date", "")),
            "snippet": str(meta.get("snippet", "")).strip(),
        })
    return out


def _extract_body_text(payload: Dict) -> str:
    """Plain-text body of a full message payload (the visitor asked
    to READ this one). Prefers text/plain parts; a crude tag-strip of
    the HTML part is the fallback. Capped hard."""
    plain: list = []
    html: list = []

    def walk(part):
        if not isinstance(part, dict):
            return
        mime = part.get("mimeType", "")
        body = part.get("body") or {}
        data64 = body.get("data")
        if data64 and mime in ("text/plain", "text/html"):
            import base64
            try:
                text = base64.urlsafe_b64decode(
                    data64 + "=" * (-len(data64) % 4)).decode(
                        "utf-8", "replace")
            except Exception:
                text = ""
            (plain if mime == "text/plain" else html).append(text)
        for sub in part.get("parts") or []:
            walk(sub)

    walk(payload)
    if plain:
        return _WS.sub(" ", " ".join(plain)).strip()[:_BODY_CAP]
    if html:
        text = re.sub(r"<[^>]+>", " ", " ".join(html))
        return _WS.sub(" ", text).strip()[:_BODY_CAP]
    return ""


def _gmail_read_one(token: str, query: str) -> Optional[Dict]:
    """The full text of the newest message matching the ask — only
    ever called for an explicit read request."""
    params: Dict = {"maxResults": 3}
    if query:
        params["q"] = query
    data = _api("GET", f"{_GMAIL_BASE}/messages", token, params=params)
    if data is None:
        return None
    refs = data.get("messages") or []
    if not refs:
        return {}
    meta = _api("GET", f"{_GMAIL_BASE}/messages/{refs[0].get('id')}",
                token, params={"format": "full"})
    if not meta:
        return None
    headers = _header_map(meta)
    return {
        "subject": headers.get("subject", "(no subject)"),
        "from": headers.get("from", ""),
        "date": _fmt_mail_date(headers.get("date", "")),
        "text": _extract_body_text(meta.get("payload") or {}),
    }


def _mail_lines(messages: list) -> str:
    lines = []
    for i, m in enumerate(messages, 1):
        line = f"{i}. \"{m['subject']}\""
        if m["from"]:
            line += f" — from {m['from']}"
        if m["date"]:
            line += f", {m['date']}"
        if m["snippet"]:
            line += f"\n   {m['snippet']}"
        lines.append(line)
    return "\n".join(lines)


# --- Calendar reads / adds ---------------------------------------------------------

def _calendar_tz(token: str):
    """The visitor's primary-calendar timezone (the API's own), used
    for all window math and time formatting. UTC fallback."""
    data = _api("GET", f"{_CAL_BASE}/users/me/calendarList/primary",
                token)
    name = (data or {}).get("timeZone", "")
    return name or "UTC", _tzinfo(name)


def _event_start_text(ev: Dict, tz) -> str:
    start = ev.get("start") or {}
    if start.get("dateTime"):
        try:
            dt = datetime.fromisoformat(
                str(start["dateTime"]).replace("Z", "+00:00"))
            return _fmt_dt(dt.astimezone(tz))
        except Exception:
            return str(start["dateTime"])
    if start.get("date"):
        try:
            d = datetime.strptime(str(start["date"]), "%Y-%m-%d").date()
            return _fmt_day(d) + " (all day)"
        except Exception:
            return str(start["date"])
    return ""


def _cal_events(token: str, tmin: datetime, tmax: datetime,
                tz) -> Optional[list]:
    data = _api("GET", f"{_CAL_BASE}/calendars/primary/events", token,
                params={"timeMin": tmin.isoformat(),
                        "timeMax": tmax.isoformat(),
                        "singleEvents": "true",
                        "orderBy": "startTime",
                        "maxResults": _MAX_EVENTS})
    if data is None:
        return None
    out = []
    for ev in data.get("items") or []:
        title = ev.get("summary", "(no title)")
        when = _event_start_text(ev, tz)
        out.append(f"- {title} — {when}" if when else f"- {title}")
    return out


def _cal_insert(token: str, job: Dict, tz_name: str) -> Optional[Dict]:
    body = {
        "summary": job["title"],
        "start": {"dateTime": job["start"].isoformat(),
                  "timeZone": tz_name},
        "end": {"dateTime": job["end"].isoformat(),
                "timeZone": tz_name},
    }
    return _api("POST", f"{_CAL_BASE}/calendars/primary/events",
                token, json_body=body)


# --- Guidance results (never spend budget) -----------------------------------------

def _guidance(kind: str, who: str) -> list:
    if kind == "reconnect":
        body = (f"The visitor ({who}) connected their Google account "
                "BEFORE mail/calendar access existed, so this "
                "connection only covers identity — no Gmail or "
                "Calendar permission. Do NOT invent any emails or "
                "events. Tell them, in persona, that they need to tap "
                "Connect Google once more (slide-over menu) and "
                "approve the added mail/calendar access, then ask "
                "again. Keep it short and helpful.")
        title = "🔐 Google reconnect needed for mail/calendar"
    else:
        body = ("The visitor is asking about their OWN Gmail or "
                "Google Calendar, but they have NOT connected a "
                "Google account to OG. You have no access to their "
                "mail or calendar. Do NOT invent any emails or "
                "events. Tell them, in persona, to connect Google "
                "first — the Connect Google button in the slide-over "
                "menu — and then ask again. Keep it short and "
                "helpful.")
        title = "🔐 Google not connected"
    return [{"title": title, "body": body, "href": "/auth/google"}]


# --- Execution ---------------------------------------------------------------------

def _spend(consume_lookup, uid: str) -> bool:
    if consume_lookup is None:
        return True
    try:
        return bool(consume_lookup(uid))
    except Exception as e:
        logger.warning(f"Google hands budget consume failed: {e}")
        return False


def _gmail_answer(job: Dict, token: str, who: str,
                  consume_lookup, uid: str) -> Optional[list]:
    kind = job["kind"]
    query = job.get("query", "")
    if kind == "gmail_read":
        msg = _gmail_read_one(token, query)
        if msg is None:
            return None
        if not msg:
            body = ("The visitor asked to read an email, but a real "
                    "Gmail search found NO matching message. Tell "
                    "them plainly, in persona, that nothing matched — "
                    "do not invent a message.")
            results = [{"title": "📧 Gmail — no match", "body": body,
                        "href": _GMAIL_WEB}]
        else:
            body = (f"The visitor ({who}) asked to read this email "
                    "from their own Gmail. The full text is below — "
                    "answer ONLY from it, in persona, and do not add "
                    "anything it doesn't say:\n\n"
                    f"Subject: {msg['subject']}\n"
                    f"From: {msg['from']}\nDate: {msg['date']}\n\n"
                    f"{msg['text'] or '(the message has no readable text)'}")
            results = [{"title": "📧 The visitor's Gmail — message",
                        "body": body, "href": _GMAIL_WEB}]
    else:
        messages = _gmail_list(token, query, _MAX_RESULTS)
        if messages is None:
            return None
        if not messages:
            what = f" for '{query}'" if query else ""
            body = (f"The visitor ({who}) asked about their email. A "
                    f"real Gmail search{what} (their own mailbox) "
                    "returned NO messages. Tell them plainly, in "
                    "persona, that nothing matched — do not invent "
                    "messages.")
            results = [{"title": "📧 Gmail — no matches",
                        "body": body, "href": _GMAIL_WEB}]
        else:
            label = "latest email" if kind == "gmail_latest" \
                else f"Gmail search results for '{query}'"
            body = (f"The visitor ({who}) is asking about their own "
                    f"email. Answer ONLY from this list — their "
                    f"actual {label}, newest first, with the "
                    "date shown for each:\n\n"
                    + _mail_lines(messages))
            results = [{"title": f"📧 The visitor's {label}",
                        "body": body, "href": _GMAIL_WEB}]
    if not _spend(consume_lookup, uid):
        logger.info("Google hands answer skipped: visitor at daily "
                    "lookup cap")
        return None
    return results


def _cal_read_answer(job: Dict, message: str, token: str, who: str,
                     consume_lookup, uid: str) -> Optional[list]:
    tz_name, tz = _calendar_tz(token)
    now = datetime.now(tz)
    tmin, tmax, label = _cal_window(_clean(message), tz, now)
    events = _cal_events(token, tmin, tmax, tz)
    if events is None:
        return None
    if not events:
        body = (f"The visitor ({who}) asked what's on their calendar "
                f"({label}). Their real Google Calendar has NOTHING "
                f"scheduled in that window. Tell them plainly, in "
                f"persona — do not invent events.")
        results = [{"title": f"📅 The visitor's calendar — {label}",
                    "body": body, "href": _CAL_WEB}]
    else:
        body = (f"The visitor ({who}) is asking about their own "
                f"calendar. Answer ONLY from this list — their real "
                f"Google Calendar events for {label}:\n\n"
                + "\n".join(events))
        results = [{"title": f"📅 The visitor's calendar — {label}",
                    "body": body, "href": _CAL_WEB}]
    if not _spend(consume_lookup, uid):
        logger.info("Google hands answer skipped: visitor at daily "
                    "lookup cap")
        return None
    return results


def _cal_add_answer(job: Dict, message: str, token: str, who: str,
                    consume_lookup, uid: str) -> Optional[list]:
    tz_name, tz = _calendar_tz(token)
    now = datetime.now(tz)
    parsed = _parse_add(str(message), tz, now)
    if not parsed["complete"]:
        missing = " and ".join(parsed["missing"])
        known = (f" You do know this much: the event is "
                 f"\"{parsed['title']}\"." if parsed["title"] else "")
        body = ("The visitor asked to add something to their "
                "calendar, but the ask is missing " + missing + "."
                + known + " NOTHING was added. Ask them ONE short "
                "clarifying question for the missing piece, in "
                "persona — do NOT claim anything was added and do "
                "not guess the missing piece.")
        return [{"title": "📅 Calendar add — need one detail",
                 "body": body, "href": _CAL_WEB}]
    if not _spend(consume_lookup, uid):
        logger.info("Google hands add skipped: visitor at daily "
                    "lookup cap")
        return None
    created = _cal_insert(token, parsed, tz_name)
    if not created:
        body = ("The visitor asked to add \"" + parsed["title"] +
                "\" to their calendar, but Google Calendar refused "
                "the insert just now. Tell them plainly, in persona, "
                "that it didn't go on — do NOT claim it was added. "
                "They can try again in a bit.")
        return [{"title": "📅 Calendar add — failed", "body": body,
                 "href": _CAL_WEB}]
    when = _fmt_dt(parsed["start"])
    body = (f"DONE — the event was just created on the visitor's "
            f"({who}) own Google Calendar: \"{parsed['title']}\" on "
            f"{when} (1 hour). Confirm it to them in persona, "
            f"exactly with that title and time. Do not say you will "
            f"add it — it is already on their calendar.")
    return [{"title": "📅 Added to the visitor's calendar",
             "body": body, "href": _CAL_WEB}]


def google_hands_results(job, message, uid, consume_lookup):
    """Run one parsed hands job for this visitor. Returns
    web_search-shaped results on a hit (real data, grounded empty
    answers, connect/reconnect guidance, add confirmations), or None
    on a true miss — Google disabled upstream failures — so the
    caller falls through to the previous search untouched. Real
    answers spend one unit of the shared lookup budget; guidance and
    clarifying questions spend nothing."""
    if not job or not _google_enabled() or not HANDS_ENABLED \
            or not uid:
        return None
    entry = _connection(uid)
    if not entry:
        return _guidance("connect", "the visitor")
    kind = job.get("kind", "")
    if not _scope_ok(entry, kind):
        return _guidance("reconnect",
                         entry.get("name") or entry.get("email")
                         or "the visitor")
    live = _live_entry(uid)
    if not live:
        # A revoked grant drops the entry during refresh — coach a
        # fresh connect; any other failure is a plain miss.
        if _connection(uid) is None:
            return _guidance("connect", "the visitor")
        return None
    token = live.get("access_token", "")
    if not token:
        return None
    who = live.get("name") or live.get("email") or "the visitor"
    if kind.startswith("gmail"):
        return _gmail_answer(job, token, who, consume_lookup, uid)
    if kind == "cal_read":
        return _cal_read_answer(job, message, token, who,
                                consume_lookup, uid)
    if kind == "cal_add":
        return _cal_add_answer(job, message, token, who,
                               consume_lookup, uid)
    return None


# ---------------------------------------------------------------------------
# The seam (app.py installs this on the agent, after Rounds 3/4/6/9/10)
# ---------------------------------------------------------------------------

# The hands job parsed for the exchange currently being processed.
# All chat processing is serialized under app.py's _memory_lock, so a
# single slot is safe — the same reasoning as app.py's own slots.
_pending = {"job": None, "message": ""}


def install_google_hands(agent_instance, get_uid, consume_lookup,
                         deps):
    """Wrap the agent's (already lookup/file/maps/spotify-wrapped)
    detect_intent + web_search hooks so a connected visitor's Gmail /
    Calendar questions try og_google_hands FIRST and fall through to
    the previous search on a miss. get_uid is a zero-arg callable
    returning the current visitor's uid; consume_lookup(uid) spends
    one unit of the shared Round 3 lookup budget and returns False at
    the cap. deps carries Round 3's Google store + config from
    app.py. While Google connect is disabled the wrapper is a pure
    pass-through — nothing is parsed, forced or spent. Persona files
    never touched."""
    bind = dict(deps or {})
    _deps.update(bind)
    if getattr(agent_instance, "_og_google_hands_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["job"] = None
        _pending["message"] = ""
        if _google_enabled() and HANDS_ENABLED:
            try:
                uid = get_uid()
                # Parsing is cheap and connection-independent at
                # detect time; the search hook decides what the job
                # is worth (data, guidance, or a plain miss).
                job = parse_google_intent(str(message)) if uid \
                    else None
                if job:
                    _pending["job"] = job
                    _pending["message"] = str(message)
                    if isinstance(intent, dict) \
                            and not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
            except Exception as e:
                logger.warning(f"Google hands trigger check failed: "
                               f"{e}")
                _pending["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _pending.get("job")
        message = _pending.get("message", "")
        _pending["job"] = None
        _pending["message"] = ""
        if job:
            try:
                results = google_hands_results(
                    job, message, get_uid(), consume_lookup)
            except Exception as e:
                logger.warning(f"Google hands search failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_google_hands_installed = True

    # Round 13: Google Drive/Docs + Tasks (og_google_drive.py) wraps
    # OUTSIDE the hands wrappers so its jobs get first crack; it
    # self-gates on OG_GOOGLE_DRIVE_ENABLED and reads this round's
    # bound store/config through this module's shared helpers, so
    # app.py needs no new wiring.
    try:
        import og_google_drive as _drive
        _drive.install_google_drive(
            agent_instance, get_uid, consume_lookup)
    except Exception as e:
        logger.warning(f"Google drive install failed: {e}")
