"""
OG Google round 2 (Round 13) — Google Drive/Docs READ + Google
Tasks (reminders), riding the same Google connection as Rounds 3
(identity) and 12 (Gmail + Calendar hands).

What a connected visitor can now ask about THEIR OWN Google data:

  * Drive SEARCH + LIST (read-only): "find my resume in Drive",
    "search my drive for the truck budget", "what docs do I have
    about the lease" run a real Drive files.list. Answers are
    grounded ONLY in the returned names/types/modified dates.
  * Doc READ: "read my resume", "summarize my doc about the truck"
    resolves ONE file (search first; more than one plausible match
    becomes a "which one?" with the candidates listed — nothing is
    guessed), then hands the model an excerpt (<= 12,000 chars, the
    Round 6 cap): Google Docs/Slides export as plain text, Sheets
    export as CSV (first sheet, row-capped), plain-text files
    download directly. Other types get an honest "can't read that
    type as text yet". v1 is strictly read-only for Drive: no
    uploads, edits, deletes or shares.
  * Tasks / REMINDERS: "remind me to call the landlord tomorrow",
    "add a task: renew plates" create a Google Task on the
    visitor's default list from their explicit in-chat instruction
    and confirm it; "what are my tasks / reminders" lists their open
    tasks, grounded. No title = ONE clarifying question, nothing
    created. (Google has no separate consumer Reminders API anymore
    — Tasks is the surface OG writes to.)

Scopes (drive.readonly, tasks.readonly, tasks) ride the SAME Round
3 connect flow, but only when OG_GOOGLE_DRIVE_ENABLED=true (default
OFF, independent of OG_GOOGLE_HANDS_ENABLED): with the flag off, the
connect flow's requested scopes are exactly what Round 12 left and
this module never parses, claims or spends. Granted-scope records
(the Round 12 pattern, stored at connect time) gate each capability
individually — a visitor whose connection predates these scopes (or
who only holds some of them) gets an in-persona reconnect prompt for
the missing piece, never an error or invented data.

Wiring is the established app-layer seam: install_google_drive
wraps the agent's (already lookup/file/maps/spotify/hands-wrapped)
detect_intent + web_search hooks; og_google_hands.install_google_
hands chains this install in so the Drive/Tasks wrapper sits
OUTERMOST and gets first crack, falling through to the previous
search on a miss. Answers spend one unit of the shared Round 3
lookup budget per real answer (a created task counts; clarifying
questions, connect/reconnect prompts and misses spend nothing).
Tokens come from Round 3's per-visitor store via og_google_hands'
shared helpers (refresh included); token values and file/task
contents are never logged. Persona files are never touched.
"""

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import og_google_hands as _hands

logger = logging.getLogger(__name__)

# --- Config -------------------------------------------------------------------

DRIVE_ENABLED = os.getenv(
    "OG_GOOGLE_DRIVE_ENABLED", "false").lower() == "true"

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
TASKS_READ_SCOPE = "https://www.googleapis.com/auth/tasks.readonly"
TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"
DRIVE_SCOPES = f"{DRIVE_SCOPE} {TASKS_READ_SCOPE} {TASKS_SCOPE}"

_DRIVE_BASE = "https://www.googleapis.com/drive/v3"
_TASKS_BASE = "https://tasks.googleapis.com/tasks/v1"
_DRIVE_WEB = "https://drive.google.com/drive/my-drive"
_TASKS_WEB = "https://tasks.google.com/"
_MAX_RESULTS = 5            # files shown per Drive answer
_MAX_TASKS = 15             # tasks shown per list answer
_EXCERPT_CAP = 12000        # chars of a doc handed to the model
_SHEET_ROW_CAP = 60         # CSV rows of a Sheet handed to the model
_MAX_FETCH_BYTES = 1000000  # raw download/export ceiling


def extra_scopes() -> str:
    """The scope string this round adds to the Round 3 connect flow
    when OG_GOOGLE_DRIVE_ENABLED=true (og_google_hands'
    requested_scope appends it); empty while the flag is off."""
    return DRIVE_SCOPES if DRIVE_ENABLED else ""


# --- Granted scopes ---------------------------------------------------------------

def _scope_ok(entry: Dict, kind: str) -> bool:
    granted = _hands._granted_scopes(entry)
    if kind in ("drive_search", "doc_read"):
        return DRIVE_SCOPE in granted
    if kind == "tasks_list":
        return TASKS_READ_SCOPE in granted or TASKS_SCOPE in granted
    if kind == "tasks_add":
        return TASKS_SCOPE in granted
    return False


# --- Raw-text transport (exports / downloads — the tests stub this) ---------------

def _api_text(method: str, url: str, token: str,
              params: Optional[Dict] = None) -> Optional[str]:
    """One raw-text call against the visitor's own files (Doc/Sheet
    export, plain-text download). Returns the decoded text on
    success, else None. Bodies carry the visitor's documents — the
    body is never logged, only the status."""
    import httpx
    try:
        with httpx.Client(timeout=25) as client:
            resp = client.request(
                method, url, params=params,
                headers={"Authorization": f"Bearer {token}"})
    except Exception as e:
        logger.warning(f"Google Drive text fetch failed: {e}")
        return None
    if resp.status_code != 200:
        logger.warning(
            f"Google Drive text fetch status: {resp.status_code}")
        return None
    return resp.content[:_MAX_FETCH_BYTES].decode("utf-8", "replace")


# --- Intent parsing ---------------------------------------------------------------
# Deliberately first-person: every pattern is about the VISITOR's own
# Drive/Tasks. General questions ("what is Google Drive", "how do I
# share a doc") match nothing here and keep flowing to the normal
# lookup/chat paths exactly as before. Drive/Docs asks require a
# "my" or "drive" anchor so Round 6 upload questions ("summarize
# this pdf") and web searches are never claimed.

_WS = _hands._WS
_CLEAN = _hands._clean

_TASK_ADD_RE = re.compile(
    r"\bremind\s+me\b|"
    r"\b(add|create|make|put|set)\b[^.?!]*\b(task|reminder|to-?do)\b|"
    r"\bnew\s+(task|reminder)\b")
_TASK_LIST_RE = re.compile(
    r"\bmy\s+(tasks|reminders|task\s+list|to-?do\s+list)\b|"
    r"\bwhat('s| is| are)\b[^.?!]*\b(tasks|reminders|to-?dos?)\b|"
    r"\bshow\s+(me\s+)?my\s+(tasks|reminders)\b|"
    r"\blist\s+my\s+(tasks|reminders)\b|"
    r"\bwhat\s+do\s+i\s+(need|have)\s+to\s+do\b")
_DOC_WORD = (r"(doc|docs|document|documents|file|files|spreadsheet|"
             r"spreadsheets|sheet|sheets|slides|presentation|"
             r"presentations|note|notes|resume|photo|photos|"
             r"picture|pictures)")
_SELF_Q_RE = re.compile(
    r"\bwhat\s+(docs|documents|files|spreadsheets|sheets)\s+do\s+i"
    r"\s+have\b|"
    r"\bdo\s+i\s+have\s+(a\s+|any\s+)?(doc|document|file|"
    r"spreadsheet|sheet)\b")
_DOC_READ_RE = re.compile(
    r"\b(read|open|summarize|summarise|show|print)\b[^.?!]*\b"
    + _DOC_WORD + r"\b")
_DRIVE_SEARCH_RE = re.compile(
    r"\b(find|search|look\s+for|look\s+up|list)\b[^.?!]*\b"
    r"(drive|" + _DOC_WORD[1:-1] + r")\b|"
    r"\bwhat\s+(docs|documents|files|spreadsheets|sheets)\s+do\s+i"
    r"\s+have\b|"
    r"\bdo\s+i\s+have\s+(a\s+|any\s+)?(doc|document|file|"
    r"spreadsheet|sheet)\b")

_TERM_STOPWORDS = {
    "the", "a", "an", "my", "me", "please", "in", "on", "of", "for",
    "about", "regarding", "find", "search", "look", "looking",
    "show", "read", "open", "summarize", "summarise", "drive",
    "google", "doc", "docs", "document", "documents", "file",
    "files", "spreadsheet", "spreadsheets", "sheet", "sheets",
    "slides", "presentation", "presentations", "list", "recent",
    "what", "do", "i", "have", "any", "called", "named", "it",
    "that", "this", "one", "is", "there",
}


def _extract_terms(low: str) -> str:
    """The searchable topic words of a Drive/doc ask, with the
    scaffolding stripped ('find my resume in drive' -> 'resume';
    'what docs do I have about the truck' -> 'truck'). Empty means
    'no topic — list the recent files'."""
    phrase = ""
    m = re.search(r"\b(?:called|named)\s+\"?([^\"?.!,]+)\"?", low)
    if m:
        phrase = m.group(1)
    else:
        m = re.search(r"\b(?:about|regarding|mentioning|for)\s+(.+)$",
                      low)
        phrase = m.group(1) if m else low
    phrase = re.sub(r"\b(in|on)\s+(my\s+)?(google\s+)?drive\b.*$",
                    "", phrase)
    phrase = re.sub(r"\b(my\s+)?(google\s+)?drive\b", " ", phrase)
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9'\-]*", phrase)
             if w not in _TERM_STOPWORDS]
    return " ".join(words[:6]).replace("'", "")


def _parse_due(low: str, today):
    """(date, matched text) for a task's optional due date — date
    only; Google Tasks ignores the time of day anyway. Mirrors the
    date half of og_google_hands._parse_add."""
    day = timedelta(days=1)
    m = re.search(r"\bday after tomorrow\b", low)
    if m:
        return today + 2 * day, m.group(0).strip()
    m = re.search(r"\btomorrow\b", low)
    if m:
        return today + day, m.group(0).strip()
    m = re.search(r"\btoday\b", low)
    if m:
        return today, m.group(0).strip()
    m = re.search(r"\bnext\s+(monday|tuesday|wednesday|thursday|"
                  r"friday|saturday|sunday)\b", low)
    if m:
        delta = (_hands._WEEKDAYS[m.group(1)] - today.weekday()) % 7
        return today + timedelta(days=delta + 7), m.group(0).strip()
    m = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|"
                  r"saturday|sunday)\b", low)
    if m:
        delta = (_hands._WEEKDAYS[m.group(1)] - today.weekday()) % 7
        return today + timedelta(days=delta), m.group(0).strip()
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|"
                  r"nov|dec)[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?\b", low)
    if m:
        try:
            year = today.year
            d = datetime(year, _hands._MONTHS[m.group(1)],
                         int(m.group(2))).date()
            if d < today:
                d = datetime(year + 1, _hands._MONTHS[m.group(1)],
                             int(m.group(2))).date()
            return d, m.group(0).strip()
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", low)
    if m:
        year = int(m.group(3)) if m.group(3) else today.year
        if year < 100:
            year += 2000
        try:
            return datetime(year, int(m.group(1)),
                            int(m.group(2))).date(), m.group(0).strip()
        except ValueError:
            pass
    return None, ""


def _task_title(message: str, due_text: str) -> str:
    """The task's title: the ORIGINAL message minus the scaffolding
    (same approach as og_google_hands._parse_add's title)."""
    title = _WS.sub(" ", str(message)).strip()
    if re.search(r"\bremind\s+me\b", title, flags=re.IGNORECASE):
        # Anchored, word-boundaried strips — a plain substring cut
        # of "remind me to" would bite "remind me tomorrow".
        title = re.sub(r"^\s*remind\s+me\s+to\s+", "", title,
                       flags=re.IGNORECASE)
        title = re.sub(r"^\s*remind\s+me(\s+|$)", "", title,
                       flags=re.IGNORECASE)
    else:
        title = re.sub(
            r"^\s*(please\s+)?(add|create|make|put|set)\s+"
            r"(a\s+|the\s+)?(task|reminder|to-?do)(\s+list)?"
            r"\s*[:\-]?\s*", "", title, flags=re.IGNORECASE)
        title = re.sub(r"^\s*new\s+(task|reminder)\s*[:\-]?\s*", "",
                       title, flags=re.IGNORECASE)
    for phrase in ("to my task list", "on my task list",
                   "to my tasks", "on my to-do list",
                   "to my reminders"):
        title = _hands._remove_text(title, phrase)
    if due_text:
        title = _hands._remove_text(title, due_text)
    title = _WS.sub(" ", title).strip(" ,.-")
    title = re.sub(r"^(to|on|at|for)\s+", "", title).strip(" ,.-")
    title = re.sub(r"\s+(to|on|at)$", "", title).strip(" ,.-")
    if title:
        title = title[0].upper() + title[1:]
    return title


def _parse_task_add(message: str, today=None) -> Dict:
    low = _CLEAN(message)
    due, due_text = _parse_due(low, today or datetime.now(
        timezone.utc).date())
    title = _task_title(str(message), due_text)
    return {"kind": "tasks_add", "complete": bool(title),
            "title": title, "due": due}


def parse_google_drive_intent(message: str) -> Optional[Dict]:
    """Parse a first-person Drive/Docs/Tasks job from the raw
    message: {'kind': 'drive_search'|'doc_read'|'tasks_list'|
    'tasks_add', ...} or None when the message isn't the visitor
    asking about their own Drive/Tasks. Order matters: adds before
    lists (a create is more specific), doc reads before searches
    (a read is a search with a fetch attached)."""
    low = _CLEAN(message)
    if _TASK_ADD_RE.search(low):
        return _parse_task_add(str(message))
    if _TASK_LIST_RE.search(low):
        return {"kind": "tasks_list"}
    anchored = bool(re.search(r"\bmy\b", low)) or "drive" in low
    if anchored and _DOC_READ_RE.search(low):
        return {"kind": "doc_read", "query": _extract_terms(low)}
    if _DRIVE_SEARCH_RE.search(low) and (anchored
                                         or _SELF_Q_RE.search(low)):
        return {"kind": "drive_search", "query": _extract_terms(low)}
    return None


# --- Drive reads -------------------------------------------------------------------

_MIME_LABELS = {
    "application/vnd.google-apps.document": "Google Doc",
    "application/vnd.google-apps.spreadsheet": "Google Sheet",
    "application/vnd.google-apps.presentation": "Google Slides",
    "application/vnd.google-apps.folder": "folder",
    "application/pdf": "PDF",
    "text/plain": "text file",
    "text/csv": "CSV file",
    "text/markdown": "Markdown file",
}


def _type_label(mime: str) -> str:
    if mime in _MIME_LABELS:
        return _MIME_LABELS[mime]
    if "/" in mime:
        top, sub = mime.split("/", 1)
        if top in ("image", "video", "audio"):
            return f"{top} file"
        return sub + " file"
    return "file"


def _fmt_modified(raw: str) -> str:
    try:
        dt = datetime.fromisoformat(
            str(raw).replace("Z", "+00:00"))
        return _hands._fmt_day(dt.date())
    except Exception:
        return str(raw or "")[:10]


def _drive_search_files(token: str, terms: str) -> Optional[list]:
    """Real Drive files matching the ask (newest first). None when
    the API itself failed; [] when the search honestly matched
    nothing — the caller treats those very differently."""
    clauses = ["trashed = false"]
    for word in terms.split():
        clauses.append(f"fullText contains '{word}'")
    data = _hands._api(
        "GET", f"{_DRIVE_BASE}/files", token,
        params={"q": " and ".join(clauses),
                "fields": "files(id,name,mimeType,modifiedTime,size)",
                "pageSize": _MAX_RESULTS,
                "orderBy": "modifiedTime desc"})
    if data is None:
        return None
    out = []
    for f in data.get("files") or []:
        if not isinstance(f, dict) or not f.get("id"):
            continue
        try:
            size = int(f.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        out.append({
            "id": f["id"],
            "name": str(f.get("name") or "(unnamed)"),
            "mime": str(f.get("mimeType") or ""),
            "modified": _fmt_modified(f.get("modifiedTime", "")),
            "size": size,
        })
    return out


_GOOGLE_DOC = "application/vnd.google-apps.document"
_GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
_GOOGLE_SLIDES = "application/vnd.google-apps.presentation"
_TEXT_MIMES = ("text/plain", "text/csv", "text/markdown")


def _doc_text(token: str, f: Dict):
    """(status, text) for one file's readable content: 'ok',
    'unsupported' (a real file OG can't read as text in v1 —
    including oversize plain-text files), or 'failed' (the API
    itself failed; the caller treats that as a plain miss)."""
    mime = f["mime"]
    if mime in (_GOOGLE_DOC, _GOOGLE_SLIDES):
        text = _api_text("GET", f"{_DRIVE_BASE}/files/{f['id']}/export",
                         token, params={"mimeType": "text/plain"})
        return ("ok", text) if text is not None else ("failed", "")
    if mime == _GOOGLE_SHEET:
        text = _api_text("GET", f"{_DRIVE_BASE}/files/{f['id']}/export",
                         token, params={"mimeType": "text/csv"})
        if text is None:
            return ("failed", "")
        rows = text.splitlines()
        if len(rows) > _SHEET_ROW_CAP:
            text = ("\n".join(rows[:_SHEET_ROW_CAP])
                    + f"\n[... {len(rows) - _SHEET_ROW_CAP} more rows "
                      "in the sheet, not shown]")
        return ("ok", text)
    if mime in _TEXT_MIMES:
        if f.get("size", 0) > _MAX_FETCH_BYTES:
            return ("unsupported", "")
        text = _api_text("GET", f"{_DRIVE_BASE}/files/{f['id']}",
                         token, params={"alt": "media"})
        return ("ok", text) if text is not None else ("failed", "")
    return ("unsupported", "")


def _file_lines(files: list) -> str:
    lines = []
    for i, f in enumerate(files, 1):
        lines.append(f"{i}. \"{f['name']}\" — {_type_label(f['mime'])}"
                     f", modified {f['modified']}")
    return "\n".join(lines)


# --- Tasks reads / writes ----------------------------------------------------------

def _tasks_list_open(token: str) -> Optional[list]:
    data = _hands._api(
        "GET", f"{_TASKS_BASE}/lists/@default/tasks", token,
        params={"showCompleted": "false", "showHidden": "false",
                "maxResults": _MAX_TASKS})
    if data is None:
        return None
    out = []
    for t in data.get("items") or []:
        if not isinstance(t, dict):
            continue
        title = str(t.get("title") or "(no title)")
        due = ""
        if t.get("due"):
            try:
                d = datetime.fromisoformat(
                    str(t["due"]).replace("Z", "+00:00")).date()
                due = f" — due {_hands._fmt_day(d)}"
            except Exception:
                due = ""
        out.append(f"- {title}{due}")
    return out


def _task_insert(token: str, job: Dict) -> Optional[Dict]:
    body: Dict = {"title": job["title"]}
    if job.get("due"):
        body["due"] = f"{job['due'].isoformat()}T00:00:00.000Z"
    return _hands._api(
        "POST", f"{_TASKS_BASE}/lists/@default/tasks", token,
        json_body=body)


# --- Guidance results (never spend budget) -------------------------------------------

def _guidance(kind: str, who: str) -> list:
    if kind == "reconnect":
        body = (f"The visitor ({who}) connected their Google account "
                "BEFORE Drive/Tasks access existed, so this connection "
                "doesn't include Google Drive or Tasks permission. Do "
                "NOT invent any files, documents or tasks. Tell them, "
                "in persona, that they need to tap Connect Google once "
                "more (slide-over menu) and approve the added Drive "
                "and Tasks access, then ask again. Keep it short and "
                "helpful.")
        title = "🔐 Google reconnect needed for Drive/Tasks"
    else:
        body = ("The visitor is asking about their OWN Google Drive "
                "or Tasks, but they have NOT connected a Google "
                "account to OG. You have no access to their files or "
                "tasks. Do NOT invent any files, documents or tasks. "
                "Tell them, in persona, to connect Google first — the "
                "Connect Google button in the slide-over menu — and "
                "then ask again. Keep it short and helpful.")
        title = "🔐 Google not connected"
    return [{"title": title, "body": body, "href": "/auth/google"}]


# --- Execution -----------------------------------------------------------------------

def _drive_search_answer(job: Dict, token: str, who: str,
                         consume_lookup, uid: str) -> Optional[list]:
    terms = job.get("query", "")
    files = _drive_search_files(token, terms)
    if files is None:
        return None
    if not files:
        what = f" for '{terms}'" if terms else ""
        body = (f"The visitor ({who}) asked about their Google "
                f"Drive. A real Drive search{what} (their own files) "
                "returned NO files. Tell them plainly, in persona, "
                "that nothing matched — do not invent files.")
        results = [{"title": "📁 Google Drive — no matches",
                    "body": body, "href": _DRIVE_WEB}]
    else:
        label = (f"Drive search results for '{terms}'" if terms
                 else "the visitor's most recent Drive files")
        body = (f"The visitor ({who}) is asking about their own "
                f"Google Drive. Answer ONLY from this list — {label}, "
                "newest first:\n\n" + _file_lines(files))
        results = [{"title": f"📁 The visitor's {label}",
                    "body": body, "href": _DRIVE_WEB}]
    if not _hands._spend(consume_lookup, uid):
        logger.info("Google drive answer skipped: visitor at daily "
                    "lookup cap")
        return None
    return results


def _doc_read_answer(job: Dict, token: str, who: str,
                     consume_lookup, uid: str) -> Optional[list]:
    terms = job.get("query", "")
    files = _drive_search_files(token, terms)
    if files is None:
        return None
    if not files:
        what = f" for '{terms}'" if terms else ""
        body = (f"The visitor ({who}) asked to read a document from "
                f"their Google Drive, but a real Drive search{what} "
                "found NO matching file. Tell them plainly, in "
                "persona, that nothing matched — do not invent a "
                "document or its contents.")
        results = [{"title": "📄 Drive doc — no match", "body": body,
                    "href": _DRIVE_WEB}]
        if not _hands._spend(consume_lookup, uid):
            logger.info("Google drive answer skipped: visitor at "
                        "daily lookup cap")
            return None
        return results
    if len(files) > 1:
        body = (f"The visitor ({who}) asked to read a document from "
                "their Google Drive, but the search matched MORE "
                "THAN ONE file:\n\n" + _file_lines(files) +
                "\n\nNothing was read. Ask them, in persona, WHICH "
                "one they mean (by name) — do not pick one for them "
                "and do not invent contents.")
        return [{"title": "📄 Which doc did you mean?",
                 "body": body, "href": _DRIVE_WEB}]
    f = files[0]
    status, text = _doc_text(token, f)
    if status == "failed":
        return None
    if status == "unsupported":
        body = (f"The visitor ({who}) asked to read \"{f['name']}\" "
                f"from their Google Drive. It is a real file "
                f"({_type_label(f['mime'])}, modified "
                f"{f['modified']}), but OG can't read that type as "
                "text yet — v1 reads Google Docs, Google Sheets and "
                "plain-text files. Tell them plainly, in persona, and "
                "do not invent contents.")
        results = [{"title": "📄 Drive doc — type not readable",
                    "body": body, "href": _DRIVE_WEB}]
    else:
        excerpt = text[:_EXCERPT_CAP]
        cut = ("\n[... the document continues past this excerpt]"
               if len(text) > _EXCERPT_CAP else "")
        body = (f"The visitor ({who}) asked to read this document "
                "from their own Google Drive. Its content is below — "
                "answer ONLY from it, in persona, and do not add "
                "anything it doesn't say:\n\n"
                f"\"{f['name']}\" — {_type_label(f['mime'])}, "
                f"modified {f['modified']}\n\n{excerpt}{cut}")
        results = [{"title": f"📄 The visitor's doc — {f['name']}",
                    "body": body, "href": _DRIVE_WEB}]
    if not _hands._spend(consume_lookup, uid):
        logger.info("Google drive answer skipped: visitor at daily "
                    "lookup cap")
        return None
    return results


def _tasks_list_answer(token: str, who: str, consume_lookup,
                       uid: str) -> Optional[list]:
    tasks = _tasks_list_open(token)
    if tasks is None:
        return None
    if not tasks:
        body = (f"The visitor ({who}) asked about their tasks. "
                "Their real Google Tasks list has NO open tasks. "
                "Tell them plainly, in persona — do not invent "
                "tasks.")
        results = [{"title": "✅ The visitor's tasks — none open",
                    "body": body, "href": _TASKS_WEB}]
    else:
        body = (f"The visitor ({who}) is asking about their own "
                "tasks/reminders. Answer ONLY from this list — their "
                "real open Google Tasks:\n\n" + "\n".join(tasks))
        results = [{"title": "✅ The visitor's tasks", "body": body,
                    "href": _TASKS_WEB}]
    if not _hands._spend(consume_lookup, uid):
        logger.info("Google drive answer skipped: visitor at daily "
                    "lookup cap")
        return None
    return results


def _tasks_add_answer(job: Dict, token: str, who: str,
                      consume_lookup, uid: str) -> Optional[list]:
    if not job["complete"]:
        body = ("The visitor asked OG to add a task/reminder, but "
                "the ask doesn't say WHAT the task is. NOTHING was "
                "created. Ask them ONE short clarifying question for "
                "the task itself, in persona — do NOT claim anything "
                "was added and do not guess the task.")
        return [{"title": "✅ Task add — need the task",
                 "body": body, "href": _TASKS_WEB}]
    if not _hands._spend(consume_lookup, uid):
        logger.info("Google drive task add skipped: visitor at "
                    "daily lookup cap")
        return None
    created = _task_insert(token, job)
    if not created:
        body = ("The visitor asked to add the task \"" +
                job["title"] + "\" to their Google Tasks, but Google "
                "Tasks refused the insert just now. Tell them "
                "plainly, in persona, that it didn't go on — do NOT "
                "claim it was added. They can try again in a bit.")
        return [{"title": "✅ Task add — failed", "body": body,
                 "href": _TASKS_WEB}]
    due = (f", due {_hands._fmt_day(job['due'])}"
           if job.get("due") else "")
    body = (f"DONE — the task was just created on the visitor's "
            f"({who}) own Google Tasks: \"{job['title']}\"{due}. "
            f"Confirm it to them in persona, exactly with that title"
            f"{' and due date' if due else ''}. Do not say you will "
            f"add it — it is already on their list.")
    return [{"title": "✅ Added to the visitor's tasks",
             "body": body, "href": _TASKS_WEB}]


def google_drive_results(job, message, uid, consume_lookup):
    """Run one parsed Drive/Tasks job for this visitor. Returns
    web_search-shaped results on a hit (real data, grounded empty
    answers, connect/reconnect guidance, add confirmations,
    clarifying questions), or None on a true miss — Google disabled
    or an upstream failure — so the caller falls through to the
    previous search untouched. Real answers spend one unit of the
    shared lookup budget; guidance and clarifying questions spend
    nothing."""
    if not job or not _hands._google_enabled() or not DRIVE_ENABLED \
            or not uid:
        return None
    entry = _hands._connection(uid)
    if not entry:
        return _guidance("connect", "the visitor")
    kind = job.get("kind", "")
    if not _scope_ok(entry, kind):
        return _guidance("reconnect",
                         entry.get("name") or entry.get("email")
                         or "the visitor")
    live = _hands._live_entry(uid)
    if not live:
        # A revoked grant drops the entry during refresh — coach a
        # fresh connect; any other failure is a plain miss.
        if _hands._connection(uid) is None:
            return _guidance("connect", "the visitor")
        return None
    token = live.get("access_token", "")
    if not token:
        return None
    who = live.get("name") or live.get("email") or "the visitor"
    if kind == "drive_search":
        return _drive_search_answer(job, token, who, consume_lookup,
                                    uid)
    if kind == "doc_read":
        return _doc_read_answer(job, token, who, consume_lookup, uid)
    if kind == "tasks_list":
        return _tasks_list_answer(token, who, consume_lookup, uid)
    if kind == "tasks_add":
        return _tasks_add_answer(job, token, who, consume_lookup,
                                 uid)
    return None


# ---------------------------------------------------------------------------
# The seam (installed by og_google_hands.install_google_hands, outside
# the Round 12 wrappers)
# ---------------------------------------------------------------------------

# The Drive/Tasks job parsed for the exchange currently being
# processed. All chat processing is serialized under app.py's
# _memory_lock, so a single slot is safe — the same reasoning as
# og_google_hands' own slot.
_pending = {"job": None, "message": ""}


def install_google_drive(agent_instance, get_uid, consume_lookup):
    """Wrap the agent's (already lookup/file/maps/spotify/hands-
    wrapped) detect_intent + web_search hooks so a connected
    visitor's Drive/Docs/Tasks questions try og_google_drive FIRST
    and fall through to the previous search on a miss. get_uid is a
    zero-arg callable returning the current visitor's uid;
    consume_lookup(uid) spends one unit of the shared Round 3 lookup
    budget and returns False at the cap. While Google connect is
    disabled — or while OG_GOOGLE_DRIVE_ENABLED is off — the wrapper
    is a pure pass-through: nothing is parsed, forced or spent.
    Persona files never touched."""
    if getattr(agent_instance, "_og_google_drive_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["job"] = None
        _pending["message"] = ""
        if _hands._google_enabled() and DRIVE_ENABLED:
            try:
                uid = get_uid()
                job = parse_google_drive_intent(str(message)) if uid \
                    else None
                if job:
                    _pending["job"] = job
                    _pending["message"] = str(message)
                    if isinstance(intent, dict) \
                            and not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
            except Exception as e:
                logger.warning(f"Google drive trigger check failed: "
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
                results = google_drive_results(
                    job, message, get_uid(), consume_lookup)
            except Exception as e:
                logger.warning(f"Google drive search failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_google_drive_installed = True
