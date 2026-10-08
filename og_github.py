"""
OG GitHub connect (Round 14) — ships DARK, exactly like Round 3's
Google connect and Round 10's Spotify connect.

A visitor can connect their GitHub account to OG ("Connect GitHub" in
the slide-over menu). OAuth 2.0 web flow, the standard way: OG never
sees or stores a password — GitHub itself confirms who they are and
hands back a token, stored per visitor (keyed by ogai_uid) the same
way the Google/Spotify tokens are: in Postgres (table
og_github_tokens) when OG_MEMORY_DB_URL is set, else a JSON file
(github_store.json) beside the other stores. Tokens are never logged
and never shared across visitors; a visitor's GitHub data is only ever
touched with that visitor's own token.

Scopes: read:user + user:email (identity: who connected) and repo.
The `repo` scope is broad — it covers private repos and is what lets
OG open pull requests at all under GitHub's classic OAuth App model,
where scopes are coarse and there is no per-repo or read/write split
short of the fine-grained permissions of a full GitHub App. v1
accepts that tradeoff knowingly: OG's own code only ever reads repo
metadata/READMEs and creates NEW files on NEW branches via PRs (see
below), and the migration path to a GitHub App (per-repo install,
fine-grained contents+pull-requests permissions) is documented in
the Round 14 report. A visitor who doesn't want the broad scope
simply doesn't connect.

Ships DISABLED: the menu items stay hidden, /auth/github answers 404
and the status route reports enabled:false until OG_GITHUB_ENABLED=
true plus OG_GITHUB_CLIENT_ID / OG_GITHUB_CLIENT_SECRET are set
(Brent registers the OAuth App in his own GitHub developer settings —
the steps are in the Round 14 report). While disabled — or while a
visitor simply hasn't connected — the chat capability below is fully
inert and chat behaves exactly as it did before this round.

Capabilities (enabled + connected only), wired through the same
app-layer seam as Rounds 3/4/6/9/10/12/13 — install_github_tools
wraps the agent's detect_intent + web_search hooks:

READS (1 unit of the shared per-tier lookup budget per real answer):
- "show my repos" / "my recent repos" — the visitor's own repos,
  newest-updated first, grounded in the API response only.
- "what's in my repo X" / "read the README of X" — repo metadata +
  README excerpt, grounded; OG never invents repo contents.

WRITES — PR-ONLY and APPROVAL-GATED (the standing prepare ->
approve -> execute rule):
- "write a <thing> for my repo X" / "add a file to X" — OG drafts
  the file through the normal agent path and presents a PREVIEW in
  the thread (repo, branch og/<slug>, file path, full content in a
  code block) ending in "Reply YES to open the PR". The draft step
  writes NOTHING to GitHub and spends no budget; a single pending
  change is kept per visitor for 30 minutes (process-local — a
  server restart simply drops the draft, and since nothing was
  written, nothing is lost but the draft itself).
- The visitor's next message affirming (YES & friends) executes:
  the module re-reads the visitor's own thread, extracts the exact
  content from the preview THEY approved (what you saw is what
  ships), creates branch og/<slug> from the default branch, commits
  the file with the Contents API, opens the pull request and returns
  its URL — 1 lookup-budget unit at creation. Declining (NO &
  friends) or expiry discards the draft. v1 limits: new files only
  (never edit/delete, never commit to the default branch), one file
  per PR, 64 KB content cap.

GitHub OAuth App tokens don't expire and have no refresh flow: if
the visitor revokes the app (or the token otherwise dies), the API
answers 401 and the stored connection is dropped gracefully — the
visitor gets a reconnect prompt, never an error page. Persona files
are never touched.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional
from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)

# --- Config (mirrors the Spotify connect block in og_spotify.py) ------------

GITHUB_CLIENT_ID = os.getenv("OG_GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.environ.get("OG_GITHUB_CLIENT_" + "SECRET", "")
GITHUB_ENABLED = (os.getenv("OG_GITHUB_ENABLED", "false").lower() == "true"
                  and bool(GITHUB_CLIENT_ID) and bool(GITHUB_CLIENT_SECRET))
GITHUB_REDIRECT_URI = os.getenv(
    "OG_GITHUB_REDIRECT_URI",
    "https://og-ai-service.onrender.com/auth/github/callback")
GITHUB_SCOPES = "read:user user:email repo"
GITHUB_STORE_FILE = "github_store.json"
_github_lock = threading.Lock()

_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_API_BASE = "https://api.github.com"

_MAX_REPOS = 10
_MAX_README_CHARS = 12000
_MAX_FILE_BYTES = 64 * 1024
_PENDING_TTL = 30 * 60  # seconds a drafted PR preview stays approvable

# Durable backend (opt-in, same rule as the memory/Google/Spotify
# stores): when OG_MEMORY_DB_URL points at a Postgres database the
# tokens live there; psycopg missing simply means the JSON file
# backend is used.
MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg not installed — DB backend unavailable
    psycopg = None
    _Jsonb = None

# Bound by app.py via bind_app (values it owns: the cookie age, and
# the visitor-history reader the approval step uses to lift the exact
# approved content out of the visitor's own thread).
_deps: Dict = {}


def bind_app(deps):
    _deps.update(deps)


def _cookie_max_age() -> int:
    return int(_deps.get("cookie_max_age", 365 * 24 * 60 * 60))


# --- Token store (mirrors the Spotify store in og_spotify.py) ----------------

def _github_db_connect():
    """Connect to the durable DB, creating the GitHub tokens table."""
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_github_tokens ("
            "uid TEXT PRIMARY KEY, data JSONB)"
        )
    conn.commit()
    return conn


def _load_github_store() -> Dict:
    """Load all GitHub connections (durable DB when configured,
    otherwise the JSON github store)."""
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _github_db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT uid, data FROM og_github_tokens")
                    return {uid: data for uid, data in cur.fetchall()}
        except Exception as e:
            logger.warning(f"GitHub store DB load failed, using file: {e}")
    if os.path.exists(GITHUB_STORE_FILE):
        try:
            with open(GITHUB_STORE_FILE, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Could not load github store: {e}")
    return {}


def _save_github_store(store: Dict):
    """Save all GitHub connections (durable DB when configured,
    otherwise the JSON github store)."""
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _github_db_connect() as conn:
                with conn.cursor() as cur:
                    for uid, data in store.items():
                        cur.execute(
                            "INSERT INTO og_github_tokens (uid, data) "
                            "VALUES (%s, %s) ON CONFLICT (uid) DO UPDATE "
                            "SET data = EXCLUDED.data",
                            (uid, _Jsonb(data)),
                        )
                    cur.execute("SELECT uid FROM og_github_tokens")
                    existing = {row[0] for row in cur.fetchall()}
                    for stale in existing - set(store.keys()):
                        cur.execute(
                            "DELETE FROM og_github_tokens WHERE uid = %s",
                            (stale,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"GitHub store DB save failed, using file: {e}")
    try:
        with open(GITHUB_STORE_FILE, 'w') as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Could not save github store: {e}")


def _github_connection(uid: str) -> Optional[Dict]:
    """This visitor's stored GitHub connection (profile + token)."""
    if not uid:
        return None
    with _github_lock:
        store = _load_github_store()
    entry = store.get(uid)
    return entry if isinstance(entry, dict) else None


def _store_entry(uid: str, entry: Dict):
    with _github_lock:
        store = _load_github_store()
        store[uid] = entry
        _save_github_store(store)


def _drop_entry(uid: str):
    with _github_lock:
        store = _load_github_store()
        if uid in store:
            del store[uid]
            _save_github_store(store)


# --- Signed OAuth state (same construction as the Google/Spotify flows) ------

def _github_state_for(uid: str) -> str:
    """Signed OAuth state tying the connect flow to one visitor uid."""
    sig = hmac.new(GITHUB_CLIENT_SECRET.encode(),
                   f"og-github:{uid}".encode(), hashlib.sha256).hexdigest()
    return f"{uid}.{sig}"


def _github_uid_from_state(state: str) -> Optional[str]:
    """Recover the visitor uid from a state value we signed, else None."""
    try:
        uid, _, sig = str(state).partition(".")
        if not uid or not sig:
            return None
        expected = hmac.new(GITHUB_CLIENT_SECRET.encode(),
                            f"og-github:{uid}".encode(),
                            hashlib.sha256).hexdigest()
        return uid if hmac.compare_digest(expected, sig) else None
    except Exception:
        return None


# --- GitHub API (sync; used by the chat seam) ---------------------------------
# Every API call rides this one helper so the surface stays auditable
# (and unit-testable). The token is sent as a Bearer header; bodies
# and tokens are never logged — statuses only.

def _gh_request(method: str, path: str, token: str,
                json_body: Optional[Dict] = None, raw: bool = False):
    """One call against the visitor's own GitHub. Returns
    (status, data): data is parsed JSON for JSON calls, text for
    raw=True. status is None on a transport failure."""
    import httpx
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": ("application/vnd.github.raw+json" if raw
                   else "application/vnd.github+json"),
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "OG-AI",
    }
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.request(method, _API_BASE + path,
                                  headers=headers, json=json_body)
    except Exception as e:
        logger.warning(f"GitHub API {method} {path} failed: {e}")
        return None, None
    if raw:
        return resp.status_code, resp.text
    try:
        data = resp.json()
    except Exception:
        data = None
    return resp.status_code, data



# --- Pending PR drafts (approval gate state) ----------------------------------
# One pending change per visitor, kept in process memory with a hard
# 30-minute expiry. A draft is only the INSTRUCTION to draft (repo,
# branch, path hint) — the content itself lives in the preview the
# agent posts in the visitor's thread, and the approval step lifts it
# back out of that thread, so what the visitor saw is what ships.

_pending_changes: Dict[str, Dict] = {}
_pending_lock = threading.Lock()


def _set_pending(uid: str, change: Dict):
    change = dict(change)
    change["created"] = time.time()
    with _pending_lock:
        _pending_changes[uid] = change


def _get_pending(uid: str) -> Optional[Dict]:
    if not uid:
        return None
    with _pending_lock:
        change = _pending_changes.get(uid)
        if change and time.time() - float(change.get("created", 0)) \
                > _PENDING_TTL:
            del _pending_changes[uid]
            return None
        return dict(change) if change else None


def _clear_pending(uid: str):
    with _pending_lock:
        _pending_changes.pop(uid, None)


# --- Small helpers --------------------------------------------------------------

def _slugify(text: str, fallback: str = "change") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:40].strip("-")
    return slug or fallback


def _valid_path(path: str) -> bool:
    """A safe repo-relative path for a new file (no traversal, no
    absolute paths, no backslashes or spaces)."""
    if not path or len(path) > 255 or path.startswith("/"):
        return False
    if "\\" in path or " " in path:
        return False
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return False
    return bool(parts[-1])


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
        logger.warning(f"GitHub budget consume failed: {e}")
        return False


# --- Guidance results (never spend budget) --------------------------------------

def _guidance(kind: str, who: str) -> list:
    if kind == "reconnect":
        body = (f"The visitor ({who}) had their GitHub account "
                "connected to OG, but GitHub is now refusing the "
                "connection (it was revoked or cut off on GitHub's "
                "side). Do NOT invent any repos or code. Tell them, "
                "in persona, that the connection dropped and they "
                "need to tap Connect GitHub once more (slide-over "
                "menu), then ask again. Keep it short and helpful.")
        title = "🐙 GitHub reconnect needed"
    else:
        body = ("The visitor is asking about their OWN GitHub "
                "account, but they have NOT connected GitHub to OG. "
                "You have no access to their repos. Do NOT invent "
                "any repos or code. Tell them, in persona, to "
                "connect GitHub first — the Connect GitHub button "
                "in the slide-over menu — and then ask again. Keep "
                "it short and helpful.")
        title = "🐙 GitHub not connected"
    return [{"title": title, "body": body, "href": "/auth/github"}]


def _revoked(uid: str, who: str) -> list:
    """The API just told us this visitor's token is dead (401):
    drop the stored connection and answer with the reconnect
    prompt — graceful, never an error."""
    _drop_entry(uid)
    _clear_pending(uid)
    return _guidance("reconnect", who)


# --- Chat intent parsing --------------------------------------------------------
# Deliberately first-person: every pattern is about the VISITOR's own
# GitHub. General GitHub questions ("is github down", "github
# pricing", "write a python script" with no repo named) match
# nothing here and keep flowing to the normal lookup/chat paths
# exactly as before.

_FILE_EXT = (r"(?:py|md|txt|js|ts|jsx|tsx|json|html|css|java|go|rb|"
             r"sh|yml|yaml|toml|c|h|cpp|rs|php|sql)")
_PATH_TOKEN_RE = re.compile(
    r"\b([A-Za-z0-9_][\w./-]*\." + _FILE_EXT + r")\b")
_REPO_AFTER_RE = re.compile(
    r"\brepos?(?:itory)?\s+(?:called\s+|named\s+)?([A-Za-z0-9_.-]+"
    r"(?:/[A-Za-z0-9_.-]+)?)")
_README_OF_RE = re.compile(
    r"\breadme\s+of\s+(?:my\s+)?(?:repo\s+)?([A-Za-z0-9_.-]+"
    r"(?:/[A-Za-z0-9_.-]+)?)")
_WRITE_VERB_RE = re.compile(
    r"\b(write|add|create|make|put|generate)\b")
_REPO_WORD_RE = re.compile(r"\brepos?(?:itory)?\b")
_LIST_RES = (
    r"\bmy (recent )?repos\b",
    r"\bmy (recent )?repositories\b",
    r"\bwhat repos(itories)? do i have\b",
    r"\b(show|list) (me )?(all )?(my )?repos\b",
)
_READ_RES = (
    r"\bwhat'?s in (my |the )?repo\b",
    r"\breadme\b",
    r"\b(tell me about|describe|show|open) (my |the )?repo\b",
    r"\bmy repo\b",
)
_APPROVE_RE = re.compile(
    r"^\W*(yes|yeah|yep|yup|approve|approved|go ahead|do it|open it"
    r"|ship it|confirm|ok|okay)\b")
_DECLINE_RE = re.compile(
    r"^\W*(no|nope|nah|cancel|scrap|discard|never ?mind|stop|don'?t"
    r"|do not)\b")


def _extract_repo_name(message: str) -> Optional[str]:
    """Pull a repo reference ('name' or 'owner/name') out of a
    message that talks about 'my repo <name>' / 'repo <name>' /
    'README of <name>'."""
    low = str(message).lower()
    m = _README_OF_RE.search(low) or _REPO_AFTER_RE.search(low)
    if not m:
        return None
    name = m.group(1).strip(" .?!")
    # Words that are grammar, not repo names.
    if name in ("my", "the", "a", "this", "that", "it", "code",
                "readme", "file", "files"):
        return None
    return name


def _extract_path(message: str) -> Optional[str]:
    m = _PATH_TOKEN_RE.search(str(message))
    if m and _valid_path(m.group(1)):
        return m.group(1)
    m = re.search(r"\b(?:called|named|file)\s+([A-Za-z0-9_][\w./-]*)",
                  str(message))
    if m and _valid_path(m.group(1)):
        return m.group(1)
    return None


def _extract_thing(message: str) -> str:
    """The '<thing>' out of 'write a <thing> for my repo X' — used
    for the branch slug and the PR title; the full raw message also
    rides the job so nothing of the ask is lost."""
    low = " " + re.sub(r"\s+", " ", str(message).lower()).strip() + " "
    m = re.search(r"\b(?:write|create|make|add|generate)\s+(?:a\s+|an"
                  r"\s+|the\s+)?(.+?)\s+(?:for|to|in|into)\s+(?:my"
                  r"\s+)?repos?\b", low)
    if m:
        return m.group(1).strip()
    m = re.search(r"\b(?:write|create|make|add|generate)\s+(.+?)\s*$",
                  low)
    if m:
        return m.group(1).strip(" .?!")
    return ""


def parse_github_intent(message: str) -> Optional[Dict]:
    """Parse a first-person GitHub job from the raw message. Returns
    a job dict — {'kind': 'repos'|'repo_read'|'pr_write', ...} — or
    None when the message isn't about the visitor's own GitHub.
    Approval/decline replies are NOT parsed here (they only mean
    something against a pending draft — see github_results)."""
    low = " " + re.sub(r"\s+", " ", str(message).lower()).strip() + " "
    # Writes first: a write ask also mentions the repo by name and
    # must not be claimed as a read.
    if _WRITE_VERB_RE.search(low) and _REPO_WORD_RE.search(low):
        repo = _extract_repo_name(message)
        if repo:
            return {"kind": "pr_write", "repo": repo,
                    "thing": _extract_thing(message),
                    "path": _extract_path(message)}
    for pattern in _READ_RES:
        if re.search(pattern, low):
            repo = _extract_repo_name(message)
            if repo:
                return {"kind": "repo_read", "repo": repo}
            if "readme" in low:
                # "read my readme" with no repo named — the module
                # can't guess which repo; leave it to normal chat.
                return None
    for pattern in _LIST_RES:
        if re.search(pattern, low):
            return {"kind": "repos"}
    if re.search(r"\bmy github\b", low) and \
            re.search(r"\b(repos?|repositories|code|projects)\b", low):
        return {"kind": "repos"}
    return None


# --- Repo resolution + read answers ---------------------------------------------

def _resolve_repo(entry: Dict, repo_ref: str):
    """Turn a visitor's repo reference into (owner, repo) using
    their own GitHub login for bare names."""
    ref = str(repo_ref or "").strip().strip("/")
    if "/" in ref:
        owner, _, repo = ref.partition("/")
        return owner, repo
    return entry.get("login", ""), ref


def _repos_answer(entry: Dict, uid: str, consume_lookup) -> Optional[list]:
    token = entry.get("access_token", "")
    who = entry.get("login") or entry.get("name") or "the visitor"
    status, data = _gh_request(
        "GET", "/user/repos?sort=updated&per_page="
        + str(_MAX_REPOS), token)
    if status == 401:
        return _revoked(uid, who)
    if status != 200 or not isinstance(data, list):
        return None
    if not _spend(consume_lookup, uid):
        logger.info("GitHub repos answer skipped: visitor at daily "
                    "lookup cap")
        return None
    if not data:
        body = (f"The visitor ({who}) connected their GitHub "
                "account and is asking about their repos. Their "
                "GitHub account has NO repos visible to OG. Tell "
                "them plainly, in persona — do not invent repos.")
        return [{"title": "🐙 The visitor's GitHub — no repos",
                 "body": body, "href": "https://github.com"}]
    lines = []
    first_href = ""
    for repo in data[:_MAX_REPOS]:
        if not isinstance(repo, dict) or not repo.get("full_name"):
            continue
        lang = repo.get("language") or "—"
        vis = "private" if repo.get("private") else "public"
        line = (f"- {repo['full_name']} ({lang}, {vis}, updated "
                f"{_fmt_date(repo.get('updated_at', ''))})")
        desc = (repo.get("description") or "").strip()
        if desc:
            line += f" — {desc[:100]}"
        lines.append(line)
        if not first_href:
            first_href = repo.get("html_url", "")
    if not lines:
        return None
    body = (f"The visitor ({who}) connected their GitHub account "
            "and is asking about their repos. Answer ONLY from this "
            "list — their actual GitHub repos, most recently "
            "updated first:\n\n" + "\n".join(lines))
    return [{"title": "🐙 The visitor's GitHub repos", "body": body,
             "href": first_href or "https://github.com"}]


def _repo_read_answer(entry: Dict, job: Dict, uid: str,
                      consume_lookup) -> Optional[list]:
    token = entry.get("access_token", "")
    who = entry.get("login") or entry.get("name") or "the visitor"
    owner, repo = _resolve_repo(entry, job.get("repo", ""))
    if not owner or not repo:
        return None
    status, data = _gh_request("GET", f"/repos/{owner}/{repo}", token)
    if status == 401:
        return _revoked(uid, who)
    if status == 404:
        if not _spend(consume_lookup, uid):
            return None
        body = (f"The visitor ({who}) asked about their repo "
                f"'{owner}/{repo}', but GitHub says there is NO "
                "repo by that name on their account. Tell them "
                "plainly, in persona — do not invent repo contents.")
        return [{"title": "🐙 GitHub — repo not found", "body": body,
                 "href": "https://github.com"}]
    if status != 200 or not isinstance(data, dict):
        return None
    # README (raw text). A 404 here just means the repo has none.
    rstatus, rtext = _gh_request(
        "GET", f"/repos/{owner}/{repo}/readme", token, raw=True)
    if rstatus == 401:
        return _revoked(uid, who)
    readme = ""
    if rstatus == 200 and isinstance(rtext, str):
        readme = rtext.strip()
    if not _spend(consume_lookup, uid):
        logger.info("GitHub repo answer skipped: visitor at daily "
                    "lookup cap")
        return None
    facts = [
        f"Repo: {data.get('full_name', f'{owner}/{repo}')}",
        f"Language: {data.get('language') or '—'}",
        f"Visibility: "
        f"{'private' if data.get('private') else 'public'}",
        f"Default branch: {data.get('default_branch', 'main')}",
        f"Last updated: {_fmt_date(data.get('updated_at', ''))}",
        f"URL: {data.get('html_url', '')}",
    ]
    desc = (data.get("description") or "").strip()
    if desc:
        facts.append(f"Description: {desc}")
    body = (f"The visitor ({who}) is asking about their own GitHub "
            "repo. Answer ONLY from these facts"
            + (" and the README below" if readme else "")
            + " — do not invent contents, files or code that "
              "aren't here:\n\n" + "\n".join(facts))
    if readme:
        body += ("\n\nREADME (excerpt):\n"
                 + readme[:_MAX_README_CHARS])
    else:
        body += "\n\n(This repo has no README OG can read.)"
    return [{"title": f"🐙 The visitor's repo — "
                       f"{data.get('full_name', repo)}",
             "body": body, "href": data.get("html_url", "")}]


# --- The write flow: draft (preview) then approval-gated execution --------------

_PREVIEW_MARKER = "PR PREVIEW"


def _draft_answer(entry: Dict, job: Dict, message: str,
                  uid: str) -> Optional[list]:
    """Step 1 of the write flow: verify the repo is real, park the
    pending change, and hand the agent an instruction to draft the
    file + present the exact preview shape. NOTHING is written to
    GitHub here, and the draft spends no budget (the unit is spent
    at PR creation)."""
    token = entry.get("access_token", "")
    who = entry.get("login") or entry.get("name") or "the visitor"
    owner, repo = _resolve_repo(entry, job.get("repo", ""))
    if not owner or not repo:
        return None
    status, data = _gh_request("GET", f"/repos/{owner}/{repo}", token)
    if status == 401:
        return _revoked(uid, who)
    if status == 404:
        body = (f"The visitor ({who}) asked OG to write a file for "
                f"their repo '{owner}/{repo}', but GitHub says there "
                "is NO repo by that name on their account. Tell them "
                "plainly, in persona — do NOT draft anything and do "
                "NOT promise a pull request.")
        return [{"title": "🐙 GitHub — repo not found", "body": body,
                 "href": "https://github.com"}]
    if status != 200 or not isinstance(data, dict):
        return None
    full_name = data.get("full_name", f"{owner}/{repo}")
    thing = job.get("thing") or ""
    path_hint = job.get("path") or ""
    slug_src = thing or (path_hint.rsplit("/", 1)[-1] if path_hint
                         else repo)
    branch = "og/" + _slugify(slug_src)
    _set_pending(uid, {"repo_full": full_name, "owner": owner,
                       "repo": repo, "branch": branch,
                       "path_hint": path_hint, "thing": thing})
    path_line = (f"Use this file path (the visitor named it): "
                 f"{path_hint}" if path_hint else
                 "Choose ONE sensible file path for it yourself "
                 "(e.g. hello.py, notes.md — match what they asked "
                 "for) and put it on the File line")
    body = (
        f"The visitor ({who}) asked you to write a file for their "
        f"GitHub repo {full_name}: \"{message}\". The repo is real "
        "and connected. DRAFT the complete file content NOW — the "
        "actual code/text they asked for, finished and usable, not "
        "a sketch. NOTHING has been written to GitHub yet: OG only "
        "ever writes as a pull request the owner approves first.\n\n"
        "Present the draft in EXACTLY this shape, in this order, "
        "with nothing before the first line:\n"
        f"Line 1: 🧾 {_PREVIEW_MARKER} — nothing is on GitHub yet\n"
        f"Line 2: Repo: {full_name}\n"
        f"Line 3: Branch: {branch}\n"
        "Line 4: File: <the file path> — " + path_line + "\n"
        "Then the FULL file content in ONE fenced code block.\n"
        "Then this closing line, word for word: Reply YES to open "
        "the PR — or NO to scrap it.\n\n"
        "Rules: new file only, one file, content under 64 KB. Do "
        "NOT claim the file or the PR exists yet — this is a "
        "preview awaiting their YES.")
    return [{"title": f"🐙 PR draft for {full_name}", "body": body,
             "href": data.get("html_url", "")}]


def _extract_preview(uid: str, pending: Dict) -> Optional[Dict]:
    """Lift the approved change out of the visitor's own thread:
    find the latest assistant message carrying the preview marker
    for THIS pending change and parse Repo/Branch/File + the code
    block. What the visitor saw is what ships — anything that
    doesn't line up with the pending change fails extraction."""
    load_history = _deps.get("load_history")
    if load_history is None:
        return None
    try:
        history = load_history(uid)
    except Exception as e:
        logger.warning(f"GitHub preview history read failed: {e}")
        return None
    text = ""
    for entry in reversed(history or []):
        if isinstance(entry, dict) and entry.get("role") == "assistant" \
                and _PREVIEW_MARKER in str(entry.get("content", "")):
            text = str(entry["content"])
            break
    if not text:
        return None
    m_repo = re.search(r"^Repo:\s*(\S+)\s*$", text, re.M)
    m_branch = re.search(r"^Branch:\s*(\S+)\s*$", text, re.M)
    m_file = re.search(r"^File:\s*(\S+)\s*$", text, re.M)
    if not m_repo or m_repo.group(1) != pending.get("repo_full"):
        return None
    if not m_branch or m_branch.group(1) != pending.get("branch"):
        return None
    path = m_file.group(1) if m_file else pending.get("path_hint", "")
    if not _valid_path(path or ""):
        return None
    # The content is the first fenced block after the File line.
    after = text[m_file.end():] if m_file else text
    m_block = re.search(r"```[^\n]*\n(.*?)```", after, re.S)
    if not m_block:
        return None
    content = m_block.group(1)
    if not content.strip():
        return None
    if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
        return {"error": "too_big", "path": path}
    return {"path": path, "content": content}


def _create_pr(entry: Dict, pending: Dict, extracted: Dict,
               uid: str) -> list:
    """Execute an APPROVED draft: branch from the default branch,
    commit the file with the Contents API, open the PR. Returns
    web_search-shaped results describing exactly what happened —
    including honest partial states when a step fails."""
    token = entry.get("access_token", "")
    who = entry.get("login") or entry.get("name") or "the visitor"
    owner = pending["owner"]
    repo = pending["repo"]
    full = pending["repo_full"]
    path = extracted["path"]
    content = extracted["content"]

    def failed(stage, note=""):
        body = (f"The visitor ({who}) approved a pull request for "
                f"{full}, but the GitHub write stopped at the "
                f"{stage} step{note}. No pull request was opened. "
                "Tell them plainly, in persona, exactly where it "
                "stopped — do NOT claim a PR exists.")
        return [{"title": "🐙 GitHub PR — stopped", "body": body,
                 "href": f"https://github.com/{full}"}]

    status, data = _gh_request("GET", f"/repos/{owner}/{repo}", token)
    if status == 401:
        return _revoked(uid, who)
    if status != 200 or not isinstance(data, dict):
        return failed("repo check")
    default_branch = data.get("default_branch") or "main"

    status, ref = _gh_request(
        "GET", f"/repos/{owner}/{repo}/git/ref/heads/"
        + quote(default_branch, safe="/"), token)
    if status == 401:
        return _revoked(uid, who)
    if status != 200 or not isinstance(ref, dict) \
            or not (ref.get("object") or {}).get("sha"):
        return failed("default-branch lookup")
    base_sha = ref["object"]["sha"]

    branch = pending["branch"]
    status = None
    for attempt in range(3):
        candidate = branch if attempt == 0 else f"{branch}-{attempt + 1}"
        status, _ = _gh_request(
            "POST", f"/repos/{owner}/{repo}/git/refs", token,
            json_body={"ref": f"refs/heads/{candidate}",
                       "sha": base_sha})
        if status == 401:
            return _revoked(uid, who)
        if status == 201:
            branch = candidate
            break
    else:
        return failed("branch creation")

    encoded = base64.b64encode(content.encode("utf-8")).decode()
    status, _ = _gh_request(
        "PUT", f"/repos/{owner}/{repo}/contents/"
        + quote(path, safe="/"), token,
        json_body={"message": f"Add {path} (via OG AI)",
                   "content": encoded, "branch": branch})
    if status == 401:
        return _revoked(uid, who)
    if status in (409, 422):
        body = (f"The visitor ({who}) approved a pull request for "
                f"{full}, and OG created the branch {branch} — but "
                f"GitHub refused the file: a file already exists at "
                f"{path}, and OG v1 only creates NEW files (it "
                "never edits existing ones). No pull request was "
                "opened. Tell them plainly, in persona.")
        return [{"title": "🐙 GitHub PR — file exists", "body": body,
                 "href": f"https://github.com/{full}"}]
    if status not in (200, 201):
        return failed("file commit",
                      f" (the branch {branch} WAS created)")

    thing = (pending.get("thing") or "").strip()
    title = (thing[:60] if thing else f"Add {path}")
    title = title[0].upper() + title[1:] if title else f"Add {path}"
    status, pr = _gh_request(
        "POST", f"/repos/{owner}/{repo}/pulls", token,
        json_body={
            "title": title,
            "head": branch,
            "base": default_branch,
            "body": (f"Drafted by OG in chat and approved by the "
                     f"repo owner before anything was written.\n\n"
                     f"New file: `{path}` on branch `{branch}`."),
        })
    if status == 401:
        return _revoked(uid, who)
    if status != 201 or not isinstance(pr, dict) \
            or not pr.get("html_url"):
        return failed("pull request opening",
                      f" (the file WAS committed on branch {branch})")
    pr_url = pr["html_url"]
    body = (f"DONE — the pull request the visitor ({who}) approved "
            f"is now OPEN on {full}: \"{title}\" — new file {path} "
            f"on branch {branch}. The PR URL is {pr_url}. Confirm "
            "it to them in persona with that URL — it is already "
            "open; do not say you will open it.")
    return [{"title": "🐙 GitHub PR opened", "body": body,
             "href": pr_url}]


# --- Execution --------------------------------------------------------------------

def github_results(job, message, uid, consume_lookup):
    """Run one parsed GitHub job for this visitor. Returns
    web_search-shaped results on a hit (real data, grounded empty
    answers, connect/reconnect guidance, draft instructions, PR
    outcomes), or None on a true miss — disabled, upstream failure —
    so the caller falls through to the previous search untouched.
    Reads spend one lookup-budget unit per real answer; the PR
    creation spends one at creation; previews, guidance, declines
    and clarifications spend nothing."""
    if not job or not GITHUB_ENABLED or not uid:
        return None
    kind = job.get("kind", "")
    who_hint = "the visitor"

    # Approval / decline replies run against the pending draft.
    if kind in ("approve", "decline"):
        pending = _get_pending(uid)
        if pending is None:
            return None  # nothing pending — not ours after all
        entry = _github_connection(uid)
        if not entry:
            _clear_pending(uid)
            return _guidance("connect", who_hint)
        who = entry.get("login") or entry.get("name") or who_hint
        if kind == "decline":
            _clear_pending(uid)
            file_label = pending.get("path_hint") or "as previewed"
            body = (f"The visitor ({who}) said NO to the drafted "
                    f"pull request for {pending.get('repo_full')} "
                    f"(file {file_label}, branch "
                    f"{pending.get('branch')}). The draft is "
                    "SCRAPPED — nothing was written to GitHub. "
                    "Confirm that to them in persona, briefly.")
            return [{"title": "🐙 GitHub PR — scrapped", "body": body,
                     "href": "/auth/github"}]
        extracted = _extract_preview(uid, pending)
        if extracted is None or extracted.get("error") == "too_big":
            _clear_pending(uid)
            why = ("the drafted file came out over the 64 KB v1 "
                   "limit" if extracted and
                   extracted.get("error") == "too_big" else
                   "the preview in the thread couldn't be read back "
                   "cleanly (its repo/branch/file lines or code "
                   "block didn't line up)")
            body = (f"The visitor ({who}) approved the drafted "
                    "pull request, but OG could NOT open it: "
                    + why + ". NOTHING was written to GitHub. Tell "
                    "them plainly, in persona, and invite them to "
                    "ask for the file again so a fresh preview is "
                    "drafted.")
            return [{"title": "🐙 GitHub PR — couldn't read draft",
                     "body": body, "href": "/auth/github"}]
        if not _spend(consume_lookup, uid):
            _clear_pending(uid)
            body = (f"The visitor ({who}) approved the drafted "
                    "pull request, but they've hit today's lookup "
                    "limit, so OG can't open it right now. NOTHING "
                    "was written to GitHub and the draft is "
                    "scrapped. Tell them plainly, in persona.")
            return [{"title": "🐙 GitHub PR — daily cap",
                     "body": body, "href": "/auth/github"}]
        try:
            return _create_pr(entry, pending, extracted, uid)
        finally:
            _clear_pending(uid)

    entry = _github_connection(uid)
    if not entry:
        return _guidance("connect", who_hint)
    if kind == "repos":
        return _repos_answer(entry, uid, consume_lookup)
    if kind == "repo_read":
        return _repo_read_answer(entry, job, uid, consume_lookup)
    if kind == "pr_write":
        return _draft_answer(entry, job, str(message), uid)
    return None


# ---------------------------------------------------------------------------
# The seam (app.py installs this on the agent, after Rounds 3/4/6/9/10/12/13)
# ---------------------------------------------------------------------------

# The GitHub job parsed for the exchange currently being processed.
# All chat processing is serialized under app.py's _memory_lock, so a
# single slot is safe — the same reasoning as app.py's own slots.
_pending = {"job": None, "message": ""}


def _claim_job(message: str, uid: str) -> Optional[Dict]:
    """What this message means for GitHub: an approval/decline when
    a draft is pending, else a first-person read/write ask."""
    low = " " + re.sub(r"\s+", " ", str(message).lower()).strip() + " "
    if _get_pending(uid) is not None:
        trimmed = low.strip()
        if _DECLINE_RE.match(trimmed):
            return {"kind": "decline"}
        if _APPROVE_RE.match(trimmed):
            return {"kind": "approve"}
    return parse_github_intent(message)


def install_github_tools(agent_instance, get_uid, consume_lookup):
    """Wrap the agent's (already lookup/file/maps/spotify/hands/
    drive-wrapped) detect_intent + web_search hooks so a connected
    visitor's GitHub questions try og_github FIRST and fall through
    to the previous search on a miss. get_uid is a zero-arg callable
    returning the current visitor's uid; consume_lookup(uid) spends
    one unit of the shared Round 3 lookup budget and returns False
    at the cap. While the feature is disabled the wrapper is a pure
    pass-through — nothing is parsed, forced or spent. Persona
    files never touched."""
    if getattr(agent_instance, "_og_github_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["job"] = None
        _pending["message"] = ""
        if GITHUB_ENABLED:
            try:
                uid = get_uid()
                job = _claim_job(str(message), uid) if uid else None
                if job:
                    _pending["job"] = job
                    _pending["message"] = str(message)
                    if isinstance(intent, dict) \
                            and not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
            except Exception as e:
                logger.warning(f"GitHub trigger check failed: {e}")
                _pending["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _pending.get("job")
        message = _pending.get("message", "")
        _pending["job"] = None
        _pending["message"] = ""
        if job:
            try:
                results = github_results(
                    job, message, get_uid(), consume_lookup)
            except Exception as e:
                logger.warning(f"GitHub search failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_github_installed = True


# --- GitHub account connect routes (dark until enabled) ---------------------

def register_github_routes(app):
    """Attach the four /auth/github* routes to the FastAPI app.
    Mirrors the Round 10 Spotify routes one for one."""

    @app.get("/auth/github")
    async def github_auth_start(raw_request: Request):
        """
        Begin GitHub connect: bounce the visitor to GitHub's own
        consent page. Answers 404 while the feature is dark (keys
        not set), so nothing about it is discoverable on the live
        site until Brent enables it.
        """
        if not GITHUB_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        from urllib.parse import urlencode
        uid = raw_request.cookies.get("ogai_uid")
        fresh_uid = None
        if not uid:
            uid = uuid.uuid4().hex
            fresh_uid = uid
        params = {
            "client_id": GITHUB_CLIENT_ID,
            "redirect_uri": GITHUB_REDIRECT_URI,
            "scope": GITHUB_SCOPES,
            "state": _github_state_for(uid),
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

    @app.get("/auth/github/callback")
    async def github_auth_callback(raw_request: Request, code: str = "",
                                   state: str = "", error: str = ""):
        """
        GitHub sends the visitor back here with a code. The signed
        state tells us which visitor this is; the code is exchanged
        for a token, the profile is fetched, and the connection is
        stored under their uid. Any failure lands back on the chat
        with ?github=failed — no error page.
        """
        if not GITHUB_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        uid = _github_uid_from_state(state)
        if error or not code or not uid:
            return RedirectResponse(url="/?github=failed", status_code=302)
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                token_resp = await client.post(
                    _TOKEN_URL,
                    data={
                        "client_id": GITHUB_CLIENT_ID,
                        "client_secret":
                            GITHUB_CLIENT_SECRET,
                        "code": code,
                        "redirect_uri": GITHUB_REDIRECT_URI,
                    },
                    headers={"Accept": "application/json"})
                if token_resp.status_code != 200:
                    logger.warning(
                        "GitHub token exchange status: "
                        f"{token_resp.status_code}")
                    return RedirectResponse(url="/?github=failed",
                                            status_code=302)
                tokens = token_resp.json()
                access_token = tokens.get("access_token", "")
                if not access_token:
                    logger.warning("GitHub token exchange: no token")
                    return RedirectResponse(url="/?github=failed",
                                            status_code=302)
                api_headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "OG-AI",
                }
                info_resp = await client.get(
                    _API_BASE + "/user", headers=api_headers)
                profile = info_resp.json() \
                    if info_resp.status_code == 200 else {}
                email = profile.get("email") or ""
                if not email:
                    # GitHub users often keep their email private;
                    # the primary verified address lives here.
                    mail_resp = await client.get(
                        _API_BASE + "/user/emails", headers=api_headers)
                    if mail_resp.status_code == 200:
                        for item in mail_resp.json() or []:
                            if isinstance(item, dict) \
                                    and item.get("primary") \
                                    and item.get("verified"):
                                email = item.get("email", "")
                                break
        except Exception as e:
            logger.warning(f"GitHub connect failed: {e}")
            return RedirectResponse(url="/?github=failed", status_code=302)
        entry = {
            "github_id": profile.get("id", ""),
            "login": profile.get("login", ""),
            "name": profile.get("name", "") or "",
            "email": email,
            "access_token": access_token,
            "scope": tokens.get("scope", ""),
            "token_type": tokens.get("token_type", ""),
            "connected": datetime.now(timezone.utc).isoformat(),
        }
        _store_entry(uid, entry)
        response = RedirectResponse(url="/?github=connected",
                                    status_code=302)
        response.set_cookie(
            "ogai_uid", uid,
            max_age=_cookie_max_age(), path="/", httponly=True,
            samesite="lax")
        return response

    @app.get("/auth/github/status")
    async def github_auth_status(raw_request: Request):
        """What the slide-over menu needs: is connect enabled, and
        if this visitor is connected, as whom? (Never returns
        tokens.)"""
        entry = None
        if GITHUB_ENABLED:
            entry = _github_connection(raw_request.cookies.get("ogai_uid"))
        return {
            "enabled": GITHUB_ENABLED,
            "connected": bool(entry),
            "login": entry.get("login", "") if entry else "",
            "name": entry.get("name", "") if entry else "",
            "email": entry.get("email", "") if entry else "",
        }

    @app.post("/auth/github/disconnect")
    async def github_auth_disconnect(raw_request: Request):
        """Forget this visitor's GitHub connection — token deleted
        server-side (and any pending PR draft scrapped)."""
        if not GITHUB_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        uid = raw_request.cookies.get("ogai_uid")
        if uid:
            _drop_entry(uid)
            _clear_pending(uid)
        return {"status": "disconnected"}
