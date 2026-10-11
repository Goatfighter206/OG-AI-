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
metadata/READMEs/files and writes on NEW branches via PRs —
creating files, and (since the Code-Works extension) editing
existing ones, always approval-gated, never a delete, never the
default branch (see below) — and the migration path to a GitHub
App (per-repo install,
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

CODE THAT WORKS, IN THE RIGHT PLACE (2026-10-09, Brent: "have OG
make the code work and put it in the correct place" + "If he needs
to test the code, use GitHub or Python") — extends the write flow
above, same laws, never replaces them:
- EDITS: a coding ask can now target an EXISTING file. The draft
  step reads the repo tree + the target file first, so the change
  lands in the correct place — an edit to the file the visitor
  named, or a new file at the path the repo's own layout implies
  (a test for utils.py goes to tests/test_utils.py when the tree
  has a tests/ dir, beside the code when that's the repo's shape).
- VERIFIED PREVIEW: edits (any type) and new Python files take one
  extra beat. The first YES makes the module extract the draft,
  re-check the base file on GitHub (if it moved since the preview,
  the write ABORTS honestly and a fresh preview is needed), compute
  the EXACT unified diff itself (difflib — what the visitor
  approves is character-exact), run the Python checks below, and
  present a second, verified preview. The second YES executes.
  New non-Python files keep the original single-YES flow (their
  preview already IS the full content).
- TEST WITH PYTHON: for changes touching Python files, the module
  assembles the repo's Python files in a throwaway temp dir,
  applies the change, runs py_compile on the changed files and the
  repo's own pytest suite when it has one. Guardrails are hard:
  subprocess with a 60s cap, an environment scrubbed to a minimal
  whitelist (no OG_* secrets, no tokens), cwd = the temp dir, the
  temp dir deleted after, and ONLY the visitor's own repo files +
  the pending change are ever executed — there is NO general
  run-this-code endpoint or chat trigger. The verified preview
  states the outcome plainly (compiled OK / N passed, M failed
  with names / could not run + why). A failing test never blocks:
  it is reported and the human decides — but OG never claims
  "works" without the run.
- TEST WITH GITHUB: after an approved PR opens, OG tracks its
  GitHub Actions check runs (bounded poll, ~20 minutes) and
  records the outcome through og_notify exactly once; "did the
  checks pass on my PR?" answers from the stored state (with one
  live refresh while still pending). A repo with no Actions
  checks is stated plainly, once.
- Ambiguity about WHICH repo or file earns exactly ONE clarifying
  question, never a guess.

GitHub OAuth App tokens don't expire and have no refresh flow: if
the visitor revokes the app (or the token otherwise dies), the API
answers 401 and the stored connection is dropped gracefully — the
visitor gets a reconnect prompt, never an error page. Persona files
are never touched.
"""

import base64
import difflib
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
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

# Code-Works extension (edits + placement + Python/GitHub testing).
_MAX_EDIT_CHARS = 48 * 1000  # an edit target must fit whole in the
# drafting context — OG never edits a file it cannot show in full
_MAX_DIFF_CHARS = 100 * 1000  # diff text stored/shown cap (the write
# itself always uses the full approved content; truncation is noted)
_MAX_TREE_LINES = 150  # repo paths shown to the drafter per preview
_SANDBOX_TIMEOUT = 60  # hard per-subprocess cap, seconds (Brent's
# Python-testing rule runs under this, never without it)
_SANDBOX_MAX_FILES = 120  # .py files assembled for one test run
_SANDBOX_MAX_BYTES = 6 * 1024 * 1024  # total assembled bytes cap
_DRAFT_MODEL = os.getenv("OG_CODE_MODEL", "gpt-4o-mini")
# Round 51 competence loop: when a verification run fails, OG may
# revise his OWN draft and re-verify — at most this many check
# runs total (the original draft's run is attempt 1).
_MAX_FIX_ATTEMPTS = 3
_CHECKS_POLL_FIRST = 20  # first Actions poll delay, seconds
_CHECKS_POLL_SECONDS = 60  # Actions poll cadence after that
_CHECKS_POLL_BUDGET = 20 * 60  # stop tracking a PR after ~20 minutes
_CHECKS_STORE_FILE = "github_checks.json"
_CHECKS_KEEP = 5  # tracked PRs remembered per visitor

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
# Code-Works: edit verbs claim a write only with a concrete target
# in the message (a file path, or bug/code/file/function/error) so
# questions ABOUT a repo ("what changed in my repo X") never get
# hijacked into the write flow.
_EDIT_VERB_RE = re.compile(
    r"\b(fix|edit|change|patch|correct|repair|refactor)\b")
_UPDATE_VERB_RE = re.compile(r"\bupdate\b")
_EDIT_TARGET_RE = re.compile(r"\b(bug|code|file|function|error)\b")
_TEST_FOR_RE = re.compile(r"\btests?\s+for\b|\btest\s+file\s+for\b")
# "Did the checks pass on my PR?" — GitHub Actions status asks.
_CHECKS_RES = (
    r"\b(checks?|ci|actions)\b[^.?!]*\b(pass|passed|fail|failed|"
    r"failing|status|done|finish|finished|green|red)\b",
    r"\bdid (the |my )?(checks?|ci|actions|pr|pull request)\b",
    r"\bhow (did|are) (the |my )?(checks|ci|actions)\b",
    r"\bpull request\b[^.?!]*\b(checks?|ci|actions)\b",
)


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
    a job dict — {'kind': 'repos'|'repo_read'|'pr_write'|
    'pr_checks'|'pr_clarify', ...} — or None when the message isn't
    about the visitor's own GitHub. Approval/decline replies are
    NOT parsed here (they only mean something against a pending
    draft — see github_results)."""
    low = " " + re.sub(r"\s+", " ", str(message).lower()).strip() + " "
    # Actions-checks status: no write/read verb overlap possible.
    for pattern in _CHECKS_RES:
        if re.search(pattern, low):
            return {"kind": "pr_checks"}
    # Writes first: a write ask also mentions the repo by name and
    # must not be claimed as a read.
    verb = bool(_WRITE_VERB_RE.search(low))
    if not verb and _EDIT_VERB_RE.search(low):
        verb = bool(_extract_path(message)
                    or _EDIT_TARGET_RE.search(low))
    if not verb and _UPDATE_VERB_RE.search(low):
        verb = bool(_extract_path(message))
    if verb and _REPO_WORD_RE.search(low):
        repo = _extract_repo_name(message)
        if repo:
            return {"kind": "pr_write", "repo": repo,
                    "thing": _extract_thing(message),
                    "path": _extract_path(message)}
        # A repo is being talked about but never named: exactly one
        # clarifying question downstream (the thread may still
        # supply the repo — see _claim_job).
        return {"kind": "pr_clarify", "repo": None,
                "path": _extract_path(message),
                "thing": _extract_thing(message),
                "repo_word": True}
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


# --- Repo tree + file reads (Code-Works: correct placement) -------------------
# Before drafting ANY change, OG reads the repo's tree and the
# target file, so an edit lands in the file that exists and a new
# file lands where the repo's own layout says it belongs.

def _fetch_tree(token: str, owner: str, repo: str, ref: str):
    """The repo's full recursive tree at a ref. Returns
    (status, entries) — entries are {path, type, sha, size} — or
    (status, None) when the tree can't be read."""
    status, data = _gh_request(
        "GET", f"/repos/{owner}/{repo}/git/trees/"
        + quote(ref, safe="/") + "?recursive=1", token)
    if status != 200 or not isinstance(data, dict):
        return status, None
    entries = []
    for item in data.get("tree") or []:
        if isinstance(item, dict) and item.get("path"):
            entries.append({"path": item["path"],
                            "type": item.get("type", ""),
                            "sha": item.get("sha", ""),
                            "size": item.get("size") or 0})
    return status, entries


def _fetch_file(token: str, owner: str, repo: str, path: str,
                ref: str = ""):
    """One file on a ref (default branch when ref is ''). Returns
    (status, info) — info {"sha", "size", "text"} for a decodable
    text file; info is None for a miss or a binary file (status
    still says which: 200 = exists but not text, 404 = absent)."""
    url = f"/repos/{owner}/{repo}/contents/" + quote(path, safe="/")
    if ref:
        url += "?ref=" + quote(ref, safe="")
    status, data = _gh_request("GET", url, token)
    if status != 200 or not isinstance(data, dict):
        return status, None
    if data.get("type") != "file":
        return status, None
    try:
        blob = base64.b64decode(data.get("content") or "")
        text = blob.decode("utf-8")
    except Exception:
        return status, None
    return status, {"sha": data.get("sha", ""),
                    "size": data.get("size", len(blob)),
                    "text": text}


def _is_test_path(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return (base.startswith("test_") and base.endswith(".py")) \
        or base.endswith("_test.py")


def _placement(tree_paths, named_path: str, message: str):
    """Decide where a change lands, from the repo's own shape.
    Returns (mode, path): 'edit' an existing file or 'new' at a
    path; path is '' only for generic new-file asks, where the
    drafter still chooses (with the tree in front of it)."""
    paths = set(tree_paths)
    low = str(message).lower()
    if named_path:
        if _TEST_FOR_RE.search(low) and named_path.endswith(".py") \
                and not _is_test_path(named_path):
            # "write a test for utils.py" — the change belongs in
            # the TEST file for that module, not the module.
            d, _, base = named_path.rpartition("/")
            stem = base[:-3]
            prefix = (d + "/") if d else ""
            for cand in (f"tests/test_{stem}.py",
                         f"test/test_{stem}.py",
                         f"{prefix}test_{stem}.py",
                         f"{prefix}{stem}_test.py"):
                if cand in paths:
                    return "edit", cand
            if any(p.startswith("tests/") for p in paths):
                return "new", f"tests/test_{stem}.py"
            if any(p.startswith("test/") for p in paths):
                return "new", f"test/test_{stem}.py"
            return "new", f"{prefix}test_{stem}.py"
        if named_path in paths:
            return "edit", named_path
        return "new", named_path
    return "new", ""


def _tree_excerpt(tree_paths, focus_path: str) -> str:
    """A bounded path listing for the drafting instruction: the
    target's directory first, then Python files, then the rest,
    capped with an honest 'N more' note."""
    if tree_paths is None:
        return "(the repo tree could not be read this time)"
    focus_dir = focus_path.rpartition("/")[0] if focus_path else ""

    def rank(p):
        if focus_dir and p.rpartition("/")[0] == focus_dir:
            return 0
        if p.endswith(".py"):
            return 1
        return 2

    ordered = sorted(set(tree_paths), key=lambda p: (rank(p), p))
    shown = ordered[:_MAX_TREE_LINES]
    text = "\n".join("- " + p for p in shown)
    if len(ordered) > len(shown):
        text += f"\n- … and {len(ordered) - len(shown)} more files"
    return text or "(empty repo)"


def _ask_one_question(uid: str, pending_fields: Dict, question: str,
                      who: str) -> list:
    """Park the clarify state and have the agent ask exactly ONE
    question — OG's law for ambiguity. Nothing is drafted and
    nothing is written."""
    fields = {"mode": "clarify"}
    fields.update(pending_fields)
    _set_pending(uid, fields)
    body = (f"The visitor ({who}) wants OG to do GitHub repo work, "
            "but the ask is ambiguous and OG does NOT guess which "
            "repo or file. Ask EXACTLY this one question, in "
            "persona, and nothing else yet — do not draft or "
            f"promise anything:\n\n{question}")
    return [{"title": "🐙 GitHub — one question", "body": body,
             "href": "/auth/github"}]


# --- The write flow: draft (preview) then approval-gated execution --------------

_PREVIEW_MARKER = "PR PREVIEW"


def _draft_answer(entry: Dict, job: Dict, message: str,
                  uid: str) -> Optional[list]:
    """Step 1 of the write flow: verify the repo is real, read its
    tree (+ the target file for an edit), park the pending change,
    and hand the agent an instruction to draft the change + present
    the exact preview shape. NOTHING is written to GitHub here, and
    the draft spends no budget (the unit is spent at PR creation)."""
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
    default_branch = data.get("default_branch") or "main"
    thing = job.get("thing") or ""
    ask_text = job.get("orig") or str(message)
    low = " " + re.sub(r"\s+", " ", ask_text.lower()).strip() + " "

    # Read the tree so the change lands in the correct place.
    tstatus, entries = _fetch_tree(token, owner, repo, default_branch)
    if tstatus == 401:
        return _revoked(uid, who)
    tree_paths = None
    if entries is not None:
        tree_paths = [e["path"] for e in entries
                      if e.get("type") == "blob"]
    named = job.get("path") or ""
    if tree_paths is not None:
        mode, path = _placement(tree_paths, named, ask_text)
    else:
        # Tree unreadable: keep the Round 14 behavior (named path
        # is a new file; un-named is drafter's choice). No edit is
        # ever guessed blind.
        mode, path = "new", named

    # An edit ask with no resolvable file earns ONE question.
    if not path and _EDIT_VERB_RE.search(low) \
            and not _TEST_FOR_RE.search(low):
        return _ask_one_question(
            uid,
            {"repo_full": full_name, "owner": owner, "repo": repo,
             "thing": thing, "orig": ask_text},
            f"Which file in {full_name} should OG change for that?",
            who)

    base_text = None
    base_sha = ""
    if mode == "edit":
        fstatus, info = _fetch_file(token, owner, repo, path,
                                    default_branch)
        if fstatus == 401:
            return _revoked(uid, who)
        if info is None:
            body = (f"The visitor ({who}) asked OG to change "
                    f"{path} in {full_name}, but OG could not read "
                    "that file as text (it is missing or binary), "
                    "so OG will NOT draft an edit it cannot see. "
                    "Tell them plainly, in persona — no draft, no "
                    "pull request promised.")
            return [{"title": "🐙 GitHub — file unreadable",
                     "body": body,
                     "href": data.get("html_url", "")}]
        if len(info["text"]) > _MAX_EDIT_CHARS:
            body = (f"The visitor ({who}) asked OG to change "
                    f"{path} in {full_name}, but that file is "
                    f"{len(info['text'])} characters — over OG's "
                    f"{_MAX_EDIT_CHARS}-character edit limit, because "
                    "OG only edits a file it can show whole. Tell "
                    "them plainly, in persona — no draft, no pull "
                    "request promised.")
            return [{"title": "🐙 GitHub — file too large to edit",
                     "body": body,
                     "href": data.get("html_url", "")}]
        base_text, base_sha = info["text"], info["sha"]

    slug_src = thing or (path.rsplit("/", 1)[-1] if path else repo)
    branch = "og/" + _slugify(slug_src)
    verify_first = mode == "edit" or not path \
        or path.endswith(".py")
    _set_pending(uid, {
        "repo_full": full_name, "owner": owner, "repo": repo,
        "branch": branch, "path_hint": named, "thing": thing,
        "mode": mode, "path": path, "base_sha": base_sha,
        "base_text": base_text, "default_branch": default_branch,
        "stage": "draft", "verify_first": verify_first,
        "py_paths": [p for p in (tree_paths or [])
                     if p.endswith(".py")],
        "tree_paths": tree_paths or [],
    })
    closing = (
        "Reply YES — OG will run the checks and show you the "
        "final verified preview before anything is written — or "
        "NO to scrap it." if verify_first else
        "Reply YES to open the PR — or NO to scrap it.")
    shape = (
        "Present the draft in EXACTLY this shape, in this order, "
        "with nothing before the first line:\n"
        f"Line 1: 🧾 {_PREVIEW_MARKER} — nothing is on GitHub yet\n"
        f"Line 2: Repo: {full_name}\n"
        f"Line 3: Branch: {branch}\n")
    if mode == "edit":
        body = (
            f"The visitor ({who}) asked you to change code in "
            f"their GitHub repo {full_name}: \"{ask_text}\". The "
            f"repo is real and connected. The change belongs in "
            f"the EXISTING file {path} — its current content is at "
            "the end of this instruction. DRAFT the complete new "
            "content of that file NOW — the whole finished file "
            "with the change applied, not a snippet and not a "
            "patch. NOTHING has been written to GitHub yet: OG "
            "only ever writes as a pull request the owner "
            "approves.\n\n"
            "Repo files (context for the draft):\n"
            + _tree_excerpt(tree_paths, path) + "\n\n"
            + shape
            + f"Line 4: File: {path}\n"
              "Line 5: Mode: edit\n"
              "Then the COMPLETE new content of the file in ONE "
              "fenced code block.\n"
              "Then ONE short plain-words line saying what "
              "changed.\n"
              f"Then this closing line, word for word: {closing}\n\n"
              "Rules: one file, new content under 64 KB. Do NOT "
              "claim the change or the PR exists yet — this is a "
              "preview awaiting their YES.\n\n"
              f"Current content of {path} on {default_branch} — "
              "draft the new version FROM this, keeping everything "
              f"that should not change:\n{base_text}")
    else:
        path_line = (
            f"Line 4: File: {path}\n" if path else
            "Line 4: File: <the file path> — Choose ONE sensible "
            "file path yourself (e.g. hello.py, notes.md — match "
            "what they asked for, and the repo layout above) and "
            "put it on the File line\n")
        body = (
            f"The visitor ({who}) asked you to write a file for "
            f"their GitHub repo {full_name}: \"{ask_text}\". The "
            "repo is real and connected. DRAFT the complete file "
            "content NOW — the actual code/text they asked for, "
            "finished and usable, not a sketch. NOTHING has been "
            "written to GitHub yet: OG only ever writes as a pull "
            "request the owner approves first.\n\n"
            "Repo files (context for the draft):\n"
            + _tree_excerpt(tree_paths, path) + "\n\n"
            + shape
            + path_line
            + "Line 5: Mode: new\n"
              "Then the FULL file content in ONE fenced code "
              "block.\n"
              f"Then this closing line, word for word: {closing}\n\n"
              "Rules: new file only, one file, content under "
              "64 KB. Do NOT claim the file or the PR exists yet — "
              "this is a preview awaiting their YES.")
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
                and _PREVIEW_MARKER in str(entry.get("content", "")) \
                and "(verified)" not in str(entry.get("content", "")):
            # Verified previews are never extracted: for an edit
            # their first block is a DIFF, not shipping content.
            # The verified package rides the pending change itself.
            text = str(entry["content"])
            break
    if not text:
        return None
    m_repo = re.search(r"^Repo:\s*(\S+)\s*$", text, re.M)
    m_branch = re.search(r"^Branch:\s*(\S+)\s*$", text, re.M)
    m_file = re.search(r"^File:\s*(\S+)\s*$", text, re.M)
    m_mode = re.search(r"^Mode:\s*(\S+)\s*$", text, re.M)
    if not m_repo or m_repo.group(1) != pending.get("repo_full"):
        return None
    if not m_branch or m_branch.group(1) != pending.get("branch"):
        return None
    if pending.get("mode") in ("edit", "new") and m_mode \
            and m_mode.group(1) != pending["mode"]:
        return None
    if pending.get("path"):
        # The change's place was fixed at draft time (an edit
        # target or a layout-derived path): the preview must be
        # for THAT file.
        if m_file and m_file.group(1) != pending["path"]:
            return None
        path = pending["path"]
    else:
        path = m_file.group(1) if m_file \
            else pending.get("path_hint", "")
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


# --- The Python sandbox (Brent's rule: test it with Python) --------------------
# The ONLY code path in OG that ever executes visitor code, and it
# exists only inside this PR flow: there is no route, no chat
# trigger and no other caller. Hard guardrails, all enforced here:
# the visitor's OWN repo files + the pending change only; a minimal
# whitelist environment (nothing inherited — no OG_* secrets, no
# tokens); cwd pinned to a throwaway temp dir that is deleted
# after; every subprocess under a hard timeout with its whole
# process group killed on expiry.

def _sandbox_env(tmp: str) -> Dict:
    return {
        "PATH": os.defpath,
        "HOME": tmp,
        "TMPDIR": tmp,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONHASHSEED": "0",
    }


def _run_capped(argv, cwd: str, env: Dict, timeout: int):
    """One subprocess under a hard timeout. Returns
    (returncode|None, stdout, stderr, timed_out)."""
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
            start_new_session=True)
    except Exception as e:
        return None, "", str(e), False
    try:
        out, err = proc.communicate(timeout=timeout)
        return proc.returncode, out or "", err or "", False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = "", ""
        return None, out or "", err or "", True


def _safe_rel(path: str) -> bool:
    """A path safe to materialize inside the sandbox temp dir."""
    if not path or path.startswith("/") or "\\" in path:
        return False
    return all(p not in ("", ".", "..") for p in path.split("/"))


def _pytest_available() -> bool:
    try:
        import pytest  # noqa: F401
        return True
    except Exception:
        return False


def run_python_checks(files: Dict, changed_paths, timeout: int =
                      _SANDBOX_TIMEOUT, tests_skip_reason: str = ""
                      ) -> Dict:
    """Assemble `files` (repo-relative path -> bytes) in a
    throwaway dir and run: (a) py_compile on every changed .py;
    (b) the repo's own pytest suite when test files are present
    and pytest exists in this environment. Returns
    {"compile": {"ok", "errors"}|None, "tests": {"status",
    "passed", "failed", "failing", "tail", "reason"}} — status is
    passed|failed|error|timeout|none|unavailable|skipped. Nothing
    here ever raises."""
    tests = {"status": "skipped", "passed": 0, "failed": 0,
             "failing": [], "tail": "", "reason": ""}
    result = {"compile": None, "tests": tests}
    changed_py = [p for p in changed_paths if p.endswith(".py")]
    test_files = [p for p in files if _is_test_path(p)]
    tmp = tempfile.mkdtemp(prefix="og_code_")
    try:
        for path, blob in files.items():
            if not _safe_rel(path):
                continue
            if isinstance(blob, str):
                blob = blob.encode("utf-8", "replace")
            dest = os.path.join(tmp, path)
            parent = os.path.dirname(dest)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(dest, "wb") as f:
                f.write(blob)
        env = _sandbox_env(tmp)
        if changed_py:
            errors = []
            for p in changed_py:
                if p not in files:
                    continue
                rc, out, err, timed = _run_capped(
                    [sys.executable, "-m", "py_compile", p],
                    tmp, env, timeout)
                if timed:
                    errors.append(
                        f"{p}: compile timed out after {timeout}s")
                elif rc is None:
                    errors.append(
                        f"{p}: could not start Python "
                        f"({(err or '').strip()[:120]})")
                elif rc != 0:
                    lines = (err or out).strip().splitlines()
                    errors.append(
                        f"{p}: "
                        f"{lines[-1][:200] if lines else 'compile failed'}")
            result["compile"] = {"ok": not errors, "errors": errors}
        if not test_files:
            tests["status"] = "none"
        elif result["compile"] and not result["compile"]["ok"]:
            tests["status"] = "skipped"
            tests["reason"] = "the changed Python doesn't compile"
        elif tests_skip_reason:
            tests["status"] = "skipped"
            tests["reason"] = tests_skip_reason
        elif not _pytest_available():
            tests["status"] = "unavailable"
            tests["reason"] = ("pytest is not installed on OG's "
                               "server")
        else:
            rc, out, err, timed = _run_capped(
                [sys.executable, "-m", "pytest", "-q", "-rf",
                 "--tb=line", "-p", "no:cacheprovider"],
                tmp, env, timeout)
            blob = ((out or "") + "\n" + (err or "")).strip()
            tests["tail"] = blob[-1200:]
            if timed:
                tests["status"] = "timeout"
                tests["reason"] = f"timed out after {timeout}s"
            else:
                mp = re.search(r"(\d+) passed", blob)
                mf = re.search(r"(\d+) failed", blob)
                tests["passed"] = int(mp.group(1)) if mp else 0
                tests["failed"] = int(mf.group(1)) if mf else 0
                tests["failing"] = re.findall(
                    r"^FAILED (\S+)", blob, re.M)[:10]
                if rc == 0:
                    tests["status"] = "passed"
                elif rc == 1:
                    tests["status"] = "failed"
                elif rc == 5:
                    tests["status"] = "none"
                else:
                    tests["status"] = "error"
                    tests["failing"] = re.findall(
                        r"^ERROR (\S+)", blob, re.M)[:10]
                    first = next((ln for ln in blob.splitlines()
                                  if ln.strip()), "")
                    tests["reason"] = first[:200]
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- Self-drafting + the competence loop (Round 51) -----------------------------
# Until now OG's code was drafted by the agent in chat, and a failed
# verification run was only REPORTED — the human decided, from a
# failing preview, whether to try again by hand. Two upgrades:
#
# (1) THE LOOP. When the checks on a draft fail in a way a code
# change can fix (compile errors, failing/erroring tests), OG gets
# the failure fed back and revises the SAME edit himself, then
# re-verifies — bounded at _MAX_FIX_ATTEMPTS check runs total. The
# loop runs entirely in preparation: nothing is written anywhere
# until the human approves, exactly as before. The verified preview
# reports the attempts honestly; OG never claims "works" without a
# green run (the standing rule, unchanged).
#
# (2) SYSTEM DRAFTS. draft_system_fix() lets the fix-approval
# pipeline (og_fixqueue) pre-draft a fix for a diagnosed problem in
# OG's OWN codebase — the files beside this module ARE the deployed
# OG server repo — through the same loop. Green draft or nothing:
# an exhausted loop produces no code proposal at all.
#
# Drafting rides one model seam (_draft_completion), in the
# og_monitor house pattern: a direct chat-completions call on the
# app's OpenAI key, None on ANY failure. With no key (or a dead
# upstream) drafting is simply unavailable and every caller falls
# back to exactly the pre-Round-51 behavior.

def _draft_available() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", ""))


def _draft_completion(messages, timeout: int = 30) -> Optional[str]:
    """One drafting completion. None on any failure — no key,
    upstream down, empty answer. Never raises."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return None
    import httpx
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": _DRAFT_MODEL, "messages": messages,
                      "temperature": 0.2})
        if resp.status_code != 200:
            logger.warning(
                f"Draft completion status: {resp.status_code}")
            return None
        data = resp.json()
        content = ((data.get("choices") or [{}])[0].get("message")
                   or {}).get("content", "")
        return content or None
    except Exception as e:
        logger.warning(f"Draft completion failed: {e}")
        return None


def _extract_code_block(text: str) -> Optional[str]:
    """The fenced block out of a drafting answer, else None.
    The drafter is told to reply with ONLY one block holding a
    COMPLETE file — and a complete file can itself contain
    fence sequences (regexes, markdown handling), so the block
    runs from the FIRST fence to the LAST one, not the first
    inner match."""
    if not text:
        return None
    m = re.search(r"```[^\n]*\n", text)
    if not m:
        return None
    end = text.rfind("```")
    if end <= m.end():
        return None
    block = text[m.end():end]
    return block if block.strip() else None


def _usable_revision(content, current: str) -> bool:
    """A drafted revision must exist, differ from what failed, and
    fit the same caps as any drafted file."""
    if not isinstance(content, str) or not content.strip():
        return False
    if content == current:
        return False
    return len(content.encode("utf-8")) <= _MAX_FILE_BYTES


def _consult_lessons(fingerprint=None, path=None) -> list:
    """Round 51 Part 3: the lessons a drafter must see before
    drafting. Fail-safe — a broken lessons book reads as no
    lessons, never as a drafting failure."""
    try:
        import og_lessons
        return og_lessons.relevant_lessons(
            fingerprint=fingerprint, path=path, limit=5) or []
    except Exception as e:
        logger.warning(f"Lessons consult failed: {e}")
        return []


def _lessons_block(lessons) -> str:
    if not lessons:
        return ""
    lines = ["", "Lessons OG has already learned the hard way — "
             "do NOT repeat these mistakes:"]
    for les in lessons[:5]:
        lines.append(f"- [{les.get('title', 'Lesson')}] "
                     f"{les.get('lesson', '')}")
    return "\n".join(lines)


def checks_green(test) -> bool:
    """A check run is GREEN when nothing failed: no compile
    errors, and the test suite passed or there was no suite to
    run. Anything else (failed, errored, timed out, unavailable,
    never ran) is not green — OG never claims 'works' off it."""
    if not isinstance(test, dict):
        return False
    comp = test.get("compile") or {}
    if comp.get("errors"):
        return False
    status = (test.get("tests") or {}).get("status", "")
    return status in ("passed", "none")


def _revisable(test) -> bool:
    """A failure a code revision could plausibly fix: compile
    errors, failing tests, or a test run that errored (collection
    / import errors are code-shaped). Timeouts and unavailable
    runners are infrastructure — presented as-is, never burned
    revisions on."""
    if not isinstance(test, dict):
        return False
    comp = test.get("compile") or {}
    if comp.get("errors"):
        return True
    status = (test.get("tests") or {}).get("status", "")
    return status in ("failed", "error")


def _attempt_record(n: int, test) -> Dict:
    failing = []
    comp = (test or {}).get("compile") or {}
    failing += [str(e)[:160] for e in (comp.get("errors") or [])]
    tests = (test or {}).get("tests") or {}
    failing += [str(f) for f in (tests.get("failing") or [])]
    if tests.get("status") == "failed" and not tests.get("failing"):
        failing.append(f"{tests.get('failed', 0)} test(s) failed")
    if tests.get("status") == "error" and tests.get("reason"):
        failing.append(str(tests["reason"])[:160])
    return {"n": n, "green": checks_green(test),
            "failing": failing[:5],
            "status": tests.get("status", "")}


def _failure_brief(test) -> str:
    """What the reviser sees: the failing names, the compile
    errors and the tail of the run's own output."""
    comp = (test or {}).get("compile") or {}
    tests = (test or {}).get("tests") or {}
    lines = []
    for e in comp.get("errors") or []:
        lines.append(f"COMPILE ERROR: {e}")
    if tests.get("status"):
        lines.append(f"Test run status: {tests['status']} "
                     f"({tests.get('passed', 0)} passed, "
                     f"{tests.get('failed', 0)} failed)")
    for name in tests.get("failing") or []:
        lines.append(f"FAILING: {name}")
    tail = str(tests.get("tail") or "").strip()
    if tail:
        lines.append("Run output (tail):\n" + tail[-1000:])
    return "\n".join(lines)


def _revise_content(path: str, base_text: str, current: str,
                    test, thing: str, lessons) -> tuple:
    """Feed one failed run back and draft the SAME edit again.
    Returns (content|None, cited_lesson_titles)."""
    raw = _draft_completion([
        {"role": "system", "content":
         "You are OG's code drafter. A draft change just FAILED "
         "its verification run. Diagnose the failure from the run "
         "output and revise the SAME change — fix the code, do "
         "not change what the change is trying to do, and do not "
         "weaken or delete tests to make them pass. Reply with "
         "ONLY the complete revised file content in ONE fenced "
         "code block — no explanation."},
        {"role": "user", "content":
         f"The change: {thing or 'fix the code'}\n"
         f"File: {path}\n\n"
         f"Why the last draft failed:\n{_failure_brief(test)}\n"
         + _lessons_block(lessons)
         + f"\n\nThe current (failing) draft of {path}:\n"
           f"```\n{current}\n```\n\n"
           "Reply with the complete revised file in one fenced "
           "code block."},
    ])
    revised = _extract_code_block(raw or "")
    if not _usable_revision(revised, current):
        return None, []
    cited = [str(les.get("title") or "") for les in (lessons or [])][:3]
    return revised, [t for t in cited if t]


def _fix_loop(run_checks, path: str, base_text: str, content: str,
              thing: str, lessons) -> tuple:
    """The bounded diagnose -> revise -> re-verify loop.
    run_checks(content) -> a run_python_checks record. Returns
    (content, test, attempts, cited_lesson_titles): the LAST
    attempt's content and run, the honest attempt history, and
    the lessons the winning revision was drafted with."""
    attempts = []
    cited: list = []
    test = run_checks(content)
    attempts.append(_attempt_record(1, test))
    while len(attempts) < _MAX_FIX_ATTEMPTS \
            and not checks_green(test) and _revisable(test):
        revised, used = _revise_content(
            path, base_text, content, test, thing, lessons)
        if revised is None:
            break
        content = revised
        cited = used
        test = run_checks(content)
        attempts.append(_attempt_record(len(attempts) + 1, test))
    return content, test, attempts, cited


def _attempts_line(attempts, test) -> str:
    """The verified preview's honest line about the loop — empty
    when the first draft simply passed (pre-Round-51 output)."""
    if not attempts or len(attempts) < 2:
        return ""
    first_fail = ", ".join(attempts[0].get("failing") or []) \
        or attempts[0].get("status") or "checks failed"
    n = attempts[-1].get("n", len(attempts))
    if checks_green(test):
        return (f"🔁 OG's first draft failed his own checks "
                f"({first_fail}) — he diagnosed it, revised the "
                f"code himself, and attempt {n} of "
                f"{_MAX_FIX_ATTEMPTS} passed. Nothing was written "
                f"at any point.")
    last_fail = ", ".join(attempts[-1].get("failing") or []) \
        or attempts[-1].get("status") or "checks failed"
    return (f"🔁 OG drafted and revised this {len(attempts)} "
            f"times himself and the checks STILL fail "
            f"({last_fail}). The last draft is what's shown — "
            f"OG does NOT claim it works.")


# --- System drafts (Part 2: the fix pipeline drafts against this repo) ----------


def _local_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _local_paths() -> list:
    """The deployed codebase's own files (paths relative to the
    source dir): every .py module plus the dependency/runtime
    pins a fix might legitimately touch."""
    try:
        names = os.listdir(_local_dir())
    except Exception:
        return []
    paths = [n for n in names if n.endswith(".py")]
    for extra in ("requirements.txt", "pyproject.toml",
                  "runtime.txt"):
        if extra in names:
            paths.append(extra)
    return sorted(paths)


def _local_read(path: str) -> Optional[str]:
    if not _safe_rel(path):
        return None
    try:
        with open(os.path.join(_local_dir(), path),
                  encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def _local_sandbox_files(changed_path: str, content: str) -> Dict:
    """Assemble this codebase for a sandbox run with the draft
    applied. Test files are excluded by design (the repo's own
    suites run in the builders' harness — src's legacy test_*.py
    carry known environment reds that would fail every draft for
    reasons that have nothing to do with it); the changed file
    itself is always in, and py_compile still validates it."""
    files: Dict = {}
    total = 0
    for p in _local_paths():
        if not p.endswith(".py") or p == changed_path:
            continue
        if _is_test_path(p):
            continue
        text = _local_read(p)
        if text is None:
            continue
        blob = text.encode("utf-8")
        if len(files) >= _SANDBOX_MAX_FILES \
                or total + len(blob) > _SANDBOX_MAX_BYTES:
            continue
        files[p] = blob
        total += len(blob)
    files[changed_path] = content.encode("utf-8")
    # pytest config is deliberately NOT copied: src's own
    # pyproject.toml carries coverage addopts meant for the
    # repo's full suite, which would break (or slow) a draft's
    # sandbox run. The draft's check is py_compile + nothing
    # collected, stated plainly in the run record.
    for cfg in ("pytest.ini", "setup.cfg", "tox.ini"):
        if cfg not in files:
            text = _local_read(cfg)
            if text is not None and len(text) < 64 * 1024:
                files[cfg] = text.encode("utf-8")
    return files


def run_local_checks(changed_path: str, content: str) -> Dict:
    """Sandbox checks for a draft against OG's own codebase (the
    fix pipeline's re-verify at approval time runs exactly this).
    A non-Python target (requirements.txt) can't be compiled or
    tested as Python — its check is simply that the draft is a
    real, non-empty change."""
    if not changed_path.endswith(".py"):
        ok = bool(content and content.strip())
        return {"compile": {"ok": ok,
                            "errors": [] if ok else
                            ["the draft is empty"]},
                "tests": {"status": "none", "passed": 0,
                          "failed": 0, "failing": [], "tail": ""},
                "timeout": False}
    result = run_python_checks(
        _local_sandbox_files(changed_path, content), [changed_path])
    tests = result.get("tests") or {}
    if tests.get("status") == "error" \
            and "exit 5" in str(tests.get("reason") or ""):
        # pytest exit 5 = nothing collected: the server tree
        # ships no suite beside its modules (excluded by
        # design), so the compile pass IS the check here.
        tests["status"] = "none"
        tests["reason"] = ("no pytest suite ships with the "
                            "server modules — py_compile is "
                            "the check")
    return result


def _choose_fix_file(title: str, diagnosis: str, spec: Dict,
                     paths: list, lessons) -> Optional[str]:
    """Pick the ONE existing file a diagnosed system problem lives
    in. Strict JSON out; anything unparseable or off-tree = None."""
    excerpt = "\n".join("- " + p for p in paths[:_MAX_TREE_LINES])
    raw = _draft_completion([
        {"role": "system", "content":
         "You are OG's code drafter working on OG's own server "
         "codebase. A system problem was diagnosed. Choose the ONE "
         "existing file from the list where the fix belongs. "
         "Reply with STRICT JSON only: {\"path\": \"<file>\"}. "
         "No other text."},
        {"role": "user", "content":
         f"Problem: {title}\nDiagnosis: {diagnosis}\n"
         f"Fix brief: {spec.get('what', '')}\n"
         + _lessons_block(lessons)
         + f"\n\nFiles in the codebase:\n{excerpt}"},
    ])
    if not raw:
        return None
    try:
        parsed = json.loads(raw.strip().strip("`"))
    except Exception:
        m = re.search(r'"path"\s*:\s*"([^"]+)"', raw)
        if not m:
            return None
        parsed = {"path": m.group(1)}
    path = str((parsed or {}).get("path") or "")
    if path in paths and path.endswith(".py") and _valid_path(path):
        return path
    return None


def _draft_fix_content(title: str, diagnosis: str, spec: Dict,
                       path: str, base_text: str,
                       lessons) -> Optional[str]:
    raw = _draft_completion([
        {"role": "system", "content":
         "You are OG's code drafter working on OG's own server "
         "codebase. Draft the SMALLEST complete fix for the "
         "diagnosed problem: return the ENTIRE new content of "
         "the one file, with the fix applied and everything else "
         "kept as it is. Reply with ONLY the file content in ONE "
         "fenced code block — no explanation."},
        {"role": "user", "content":
         f"Problem: {title}\nDiagnosis: {diagnosis}\n"
         f"Fix brief: {spec.get('what', '')}\n"
         + _lessons_block(lessons)
         + f"\n\nCurrent content of {path}:\n```\n{base_text}\n```"},
    ])
    return _extract_code_block(raw or "")


def draft_system_fix(title: str, diagnosis: str, spec: Dict,
                     lessons=None) -> tuple:
    """Pre-draft a fix for a diagnosed OPERATOR problem against
    OG's own codebase, through the competence loop. Returns
    (draft|None, attempts): a draft only when the loop reached a
    GREEN run — {path, base_text, content, diff, test, attempts,
    approach_key, lessons_cited}. Exhaustion or any failure =
    (None, attempts) and the caller falls back to owner steps.
    Preparation only: this NEVER writes a file or touches the
    repo — the returned content ships, if ever, through the
    Round 32 PR flow after the owner's approval."""
    attempts: list = []
    try:
        if not _draft_available():
            return None, attempts
        spec = spec or {}
        paths = _local_paths()
        if not paths:
            return None, attempts
        lessons = lessons or []
        target = str(spec.get("target_path") or "")
        if target:
            if target not in paths:
                return None, attempts
            path = target
        else:
            path = _choose_fix_file(title, diagnosis, spec,
                                    paths, lessons)
            if not path:
                return None, attempts
        base_text = _local_read(path)
        if base_text is None or len(base_text) > _MAX_EDIT_CHARS:
            return None, attempts
        content = _draft_fix_content(title, diagnosis, spec, path,
                                     base_text, lessons)
        if not _usable_revision(content, base_text):
            return None, attempts

        def _run(c):
            return run_local_checks(path, c)

        content, test, attempts, cited = _fix_loop(
            _run, path, base_text, content,
            str(spec.get("what") or title), lessons)
        if not checks_green(test) or content == base_text:
            return None, attempts
        digest = hashlib.sha256(
            content.encode("utf-8")).hexdigest()[:16]
        return {
            "path": path,
            "base_text": base_text,
            "content": content,
            "diff": _make_diff(base_text, content, path),
            "test": test,
            "attempts": attempts,
            "approach_key": f"code:{path}:{digest}",
            "lessons_cited": cited,
        }, attempts
    except Exception as e:
        logger.warning(f"System fix draft failed: {e}")
        return None, attempts


# --- The verified preview (first YES on an edit / Python change) ---------------

def _make_diff(base_text: str, new_text: str, path: str) -> str:
    """The EXACT unified diff, computed by the module — never by
    the drafting model — so what the visitor approves is
    character-exact. Display/store truncation is noted inline."""
    diff = "".join(difflib.unified_diff(
        base_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile="a/" + path, tofile="b/" + path))
    if len(diff) > _MAX_DIFF_CHARS:
        diff = (diff[:_MAX_DIFF_CHARS]
                + "\n… (diff truncated here — the full new "
                  "content was verified and is what ships)\n")
    return diff


def _checks_line(test, path: str) -> str:
    """The one plain line the verified preview (and the PR body)
    carries about testing. OG never says 'works' without a run."""
    if test is None:
        return ("no Python in this change — nothing to run here. "
                "If this repo has GitHub Actions checks, they "
                "report after the PR opens.")
    compile_ = test.get("compile") or {}
    if compile_.get("errors"):
        return (f"❌ COMPILE FAILED — {compile_['errors'][0]} "
                "Nothing is written unless you still say YES.")
    tests = test.get("tests") or {}
    status = tests.get("status", "")
    passed = tests.get("passed", 0)
    failed = tests.get("failed", 0)
    if status == "passed":
        if passed:
            return (f"✅ compiled OK · tests: {passed} passed, "
                    "0 failed.")
        return "✅ compiled OK · tests ran clean (0 failures)."
    if status == "failed":
        names = ", ".join((tests.get("failing") or [])[:5])
        return (f"⚠️ compiled OK · tests: {passed} passed, "
                f"{failed} failed ({names}). Your call — YES "
                "still opens the PR.")
    if status == "none":
        return ("✅ compiled OK · no test suite found in this "
                "repo.")
    if status == "unavailable":
        return ("✅ compiled OK · tests could not run here — "
                "pytest isn't installed on OG's server.")
    if status == "timeout":
        return (f"⚠️ compiled OK · tests {tests.get('reason', '')}"
                " — treated as unknown, not as a pass.")
    if status == "error":
        return ("⚠️ compiled OK · the test run itself errored "
                f"({tests.get('reason', '')}) — that's the run, "
                "not necessarily the code.")
    # skipped
    return (f"✅ compiled OK · test suite not run "
            f"({tests.get('reason', '')}).")


def _assemble_test_files(entry: Dict, pending: Dict,
                         changed_path: str, content: str):
    """Collect the repo's Python files for the sandbox, with the
    pending change applied over the current files. Returns
    (files, hard_reason, soft_note): a hard_reason means the suite
    must NOT run (assembly limits — a partial run would mislead);
    a soft_note means some files couldn't be read and the preview
    must say the run was partial."""
    token = entry.get("access_token", "")
    owner, repo = pending["owner"], pending["repo"]
    ref = pending.get("default_branch", "")
    files = {changed_path: content.encode("utf-8")}
    py_paths = [p for p in (pending.get("py_paths") or [])
                if p != changed_path]
    if len(py_paths) > _SANDBOX_MAX_FILES:
        return files, (f"the repo has {len(py_paths)} Python "
                       f"files — over OG's "
                       f"{_SANDBOX_MAX_FILES}-file assembly limit"), ""
    total = len(files[changed_path])
    fetched = 0
    for p in py_paths:
        st, info = _fetch_file(token, owner, repo, p, ref)
        if info is None:
            continue
        blob = info["text"].encode("utf-8")
        total += len(blob)
        if total > _SANDBOX_MAX_BYTES:
            return files, ("the repo's Python exceeds OG's "
                           f"{_SANDBOX_MAX_BYTES // (1024 * 1024)} MB "
                           "assembly limit"), ""
        files[p] = blob
        fetched += 1
    for cfg in ("pytest.ini", "pyproject.toml", "setup.cfg",
                "tox.ini"):
        if cfg in (pending.get("tree_paths") or []) \
                and cfg not in files:
            st, info = _fetch_file(token, owner, repo, cfg, ref)
            if info is not None and len(info["text"]) < 64 * 1024:
                files[cfg] = info["text"].encode("utf-8")
    soft = ""
    if fetched < len(py_paths):
        soft = (f"assembled {fetched} of {len(py_paths)} Python "
                "files — the rest could not be read")
    return files, "", soft


def _verify_pending(entry: Dict, pending: Dict, extracted: Dict,
                    uid: str) -> list:
    """The first YES on a change that needs verification: re-check
    the base on GitHub (moved = abort, honestly), compute the
    exact diff, run the Python checks, park the verified package
    and present the FINAL preview. The second YES executes. Any
    failure here writes NOTHING."""
    token = entry.get("access_token", "")
    who = entry.get("login") or entry.get("name") or "the visitor"
    owner, repo, full = (pending["owner"], pending["repo"],
                         pending["repo_full"])
    path = extracted["path"]
    content = extracted["content"]
    ref = pending.get("default_branch", "")

    def abort(title: str, body: str) -> list:
        _clear_pending(uid)
        return [{"title": title, "body": body,
                 "href": f"https://github.com/{full}"}]

    diff = None
    if pending.get("mode") == "edit":
        st, info = _fetch_file(token, owner, repo, path, ref)
        if st == 401:
            _clear_pending(uid)
            return _revoked(uid, who)
        if info is None or info.get("sha") != pending.get("base_sha"):
            return abort(
                "🐙 GitHub PR — file changed",
                f"The visitor ({who}) approved a draft edit to "
                f"{path} in {full}, but that file has CHANGED on "
                "GitHub since the preview was drafted — OG will "
                "not write over someone else's newer work. NOTHING "
                "was written. Tell them plainly, in persona, and "
                "invite them to ask again so OG drafts a fresh "
                "preview against the current file.")
        base_text = pending.get("base_text") or ""
        if content == base_text:
            return abort(
                "🐙 GitHub PR — no change",
                f"The visitor ({who}) approved a draft for "
                f"{full}, but the drafted content is IDENTICAL to "
                f"the current {path} — there is no change to ship. "
                "NOTHING was written. Tell them plainly, in "
                "persona.")
        # The diff is computed AFTER the checks loop below: a
        # self-revision (Round 51) changes the content, and the
        # diff the visitor approves must match what ships.
    else:
        st, info = _fetch_file(token, owner, repo, path, ref)
        if st == 401:
            _clear_pending(uid)
            return _revoked(uid, who)
        if st == 200:
            return abort(
                "🐙 GitHub PR — file appeared",
                f"The visitor ({who}) approved a draft for "
                f"{full}, but a file now EXISTS at {path} on the "
                "default branch — it appeared after the preview. "
                "NOTHING was written. Tell them plainly, in "
                "persona, and invite them to ask again.")

    test = None
    attempts: list = []
    cited: list = []
    if path.endswith(".py"):
        def _run(c):
            files, hard, soft = _assemble_test_files(
                entry, pending, path, c)
            t = run_python_checks(files, [path],
                                  tests_skip_reason=hard)
            if soft:
                t["note"] = soft
            return t

        # Round 51: a failed run is fed back and OG revises the
        # SAME edit himself (bounded), consulting the lessons
        # book for this file. Inert without a drafting key.
        lessons = _consult_lessons(path=path)
        content, test, attempts, cited = _fix_loop(
            _run, path, pending.get("base_text") or "", content,
            pending.get("thing") or "", lessons)
        if pending.get("mode") == "edit" \
                and content == (pending.get("base_text") or ""):
            return abort(
                "🐙 GitHub PR — no change",
                f"The visitor ({who}) approved a draft for "
                f"{full}, but after OG's own revisions the content "
                f"of {path} is back to IDENTICAL with the current "
                "file — there is no change to ship. NOTHING was "
                "written. Tell them plainly, in persona.")
    if pending.get("mode") == "edit":
        diff = _make_diff(pending.get("base_text") or "", content,
                          path)
    pkg = {"path": path, "content": content, "diff": diff,
           "test": test, "attempts": attempts, "lessons": cited}
    verified = dict(pending)
    verified["stage"] = "verified"
    verified["pkg"] = pkg
    _set_pending(uid, verified)  # fresh window for THIS preview

    checks = _checks_line(test, path)
    if test is not None and test.get("note"):
        checks += f" (Partial run: {test['note']}.)"
    extra_lines = ""
    aline = _attempts_line(attempts, test)
    if aline:
        extra_lines += aline + "\n"
    for title_ in cited:
        extra_lines += f"📘 Lesson applied: {title_}\n"
    block = (f"```diff\n{diff}```" if diff is not None
             else f"```\n{content}\n```")
    preview = (
        f"🧾 {_PREVIEW_MARKER} (verified) — nothing is on GitHub "
        f"yet\nRepo: {full}\nBranch: {pending['branch']}\n"
        f"File: {path}\nMode: {pending.get('mode', 'new')}\n"
        f"{block}\nChecks: {checks}\n"
        f"{extra_lines}"
        "Reply YES to open the PR — or NO to scrap it.")
    body = (
        f"The visitor ({who}) said YES to the draft for {full}. "
        "OG has now VERIFIED the change: the base on GitHub is "
        "unchanged, the diff below was computed by OG itself (it "
        "is exact), and the Python checks were actually run — "
        "their outcome is on the Checks line. NOTHING has been "
        "written to GitHub yet. Present the following preview "
        "EXACTLY, word for word — every line, the block and the "
        "Checks line — and add nothing about the change "
        f"yourself:\n\n{preview}")
    return [{"title": "🐙 PR verified preview — "
                       f"{full}", "body": body,
             "href": f"https://github.com/{full}"}]


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

    # The standing law, enforced at the moment of writing: the
    # base the visitor approved against must not have moved. An
    # edit whose target changed — or a "new" file that now exists
    # — aborts BEFORE any branch or commit exists.
    mode = pending.get("mode", "new")
    vstatus, vinfo = _fetch_file(token, owner, repo, path,
                                 default_branch)
    if vstatus == 401:
        return _revoked(uid, who)
    if mode == "edit":
        if vinfo is None or vinfo.get("sha") != pending.get("base_sha"):
            body = (f"The visitor ({who}) approved a pull request "
                    f"for {full}, but {path} CHANGED on GitHub "
                    "after the preview — OG will not write over "
                    "newer work. NOTHING was written and no branch "
                    "was created. Tell them plainly, in persona, "
                    "and invite them to ask again for a fresh "
                    "preview.")
            return [{"title": "🐙 GitHub PR — file changed",
                     "body": body,
                     "href": f"https://github.com/{full}"}]
    elif vstatus == 200:
        body = (f"The visitor ({who}) approved a pull request for "
                f"{full}, but a file already exists at {path} on "
                f"{default_branch} — it appeared after the preview. "
                "NOTHING was written and no branch was created. "
                "Tell them plainly, in persona.")
        return [{"title": "🐙 GitHub PR — file exists",
                 "body": body, "href": f"https://github.com/{full}"}]

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
    put_body = {"message": (f"Update {path} (via OG AI)"
                            if mode == "edit" else
                            f"Add {path} (via OG AI)"),
                "content": encoded, "branch": branch}
    if mode == "edit":
        # The Contents API requires the current blob sha to update
        # an existing file — the one verified moments ago.
        put_body["sha"] = pending.get("base_sha", "")
    status, _ = _gh_request(
        "PUT", f"/repos/{owner}/{repo}/contents/"
        + quote(path, safe="/"), token, json_body=put_body)
    if status == 401:
        return _revoked(uid, who)
    if status in (409, 422):
        if mode == "edit":
            why = (f"the file {path} moved again while OG was "
                   "writing (its sha no longer matches the verified "
                   "preview)")
        else:
            why = (f"a file already exists at {path} on the new "
                   "branch — GitHub refused to overwrite it")
        body = (f"The visitor ({who}) approved a pull request for "
                f"{full}, and OG created the branch {branch} — but "
                f"GitHub refused the file commit: {why}. No pull "
                "request was opened. Tell them plainly, in persona.")
        return [{"title": "🐙 GitHub PR — commit refused",
                 "body": body, "href": f"https://github.com/{full}"}]
    if status not in (200, 201):
        return failed("file commit",
                      f" (the branch {branch} WAS created)")

    thing = (pending.get("thing") or "").strip()
    fallback = f"Update {path}" if mode == "edit" else f"Add {path}"
    title = (thing[:60] if thing else fallback)
    title = title[0].upper() + title[1:] if title else fallback
    pr_body = (f"Drafted by OG in chat and approved by the repo "
               f"owner before anything was written.\n\n"
               f"{'Edited file' if mode == 'edit' else 'New file'}: "
               f"`{path}` on branch `{branch}`.")
    pkg_test = (pending.get("pkg") or {}).get("test")
    if pkg_test is not None:
        pr_body += ("\n\nChecks OG ran before opening: "
                    + _checks_line(pkg_test, path))
    pkg_attempts = (pending.get("pkg") or {}).get("attempts") or []
    if len(pkg_attempts) > 1:
        pr_body += (f"\n\nOG's first draft failed those checks; "
                    f"he diagnosed it and revised the code "
                    f"himself — attempt "
                    f"{pkg_attempts[-1].get('n', len(pkg_attempts))} "
                    f"of {_MAX_FIX_ATTEMPTS} is what was approved.")
    pkg_lessons = (pending.get("pkg") or {}).get("lessons") or []
    for title_ in pkg_lessons:
        pr_body += f"\nLesson applied: {title_}"
    status, pr = _gh_request(
        "POST", f"/repos/{owner}/{repo}/pulls", token,
        json_body={"title": title, "head": branch,
                   "base": default_branch, "body": pr_body})
    if status == 401:
        return _revoked(uid, who)
    if status != 201 or not isinstance(pr, dict) \
            or not pr.get("html_url"):
        return failed("pull request opening",
                      f" (the file WAS committed on branch {branch})")
    pr_url = pr["html_url"]
    head_sha = (pr.get("head") or {}).get("sha", "")
    if head_sha:
        _register_checks_watch(uid, {
            "repo_full": full, "owner": owner, "repo": repo,
            "pr_number": pr.get("number"), "pr_url": pr_url,
            "branch": branch, "head_sha": head_sha,
            "status": "pending", "checks": [], "notified": False,
            "created": time.time(), "updated": time.time()})
        _start_checks_watch(uid, pr_url)
    action = "edited file" if mode == "edit" else "new file"
    body = (f"DONE — the pull request the visitor ({who}) approved "
            f"is now OPEN on {full}: \"{title}\" — {action} {path} "
            f"on branch {branch}. The PR URL is {pr_url}. Confirm "
            "it to them in persona with that URL — it is already "
            "open; do not say you will open it."
            + (" OG is also tracking the PR's GitHub Actions "
               "checks: the outcome lands in the visitor's "
               "notifications, and they can ask 'did the checks "
               "pass on my PR?' — mention that once, briefly."
               if head_sha else ""))
    return [{"title": "🐙 GitHub PR opened", "body": body,
             "href": pr_url}]


# --- GitHub Actions check tracking (Brent's rule: test it with GitHub) ---------
# After a PR opens, OG watches its check runs: a bounded background
# poll (~20 min) plus a lazy refresh when the visitor asks. The
# terminal outcome is recorded through og_notify exactly once per
# PR. Watches are per visitor and durable (Postgres when configured,
# else a JSON file next to the token store).

_checks_lock = threading.Lock()
_CHECK_TERMINAL = ("success", "failure", "none", "stopped")


def _checks_db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_github_checks ("
            "uid TEXT PRIMARY KEY, data JSONB)")
    conn.commit()
    return conn


def _load_checks_store() -> Dict:
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _checks_db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT uid, data FROM og_github_checks")
                    return {u: d for u, d in cur.fetchall()}
        except Exception as e:
            logger.warning(f"GitHub checks DB load failed, "
                           f"using file: {e}")
    if os.path.exists(_CHECKS_STORE_FILE):
        try:
            with open(_CHECKS_STORE_FILE, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Could not load checks store: {e}")
    return {}


def _save_checks_store(store: Dict):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _checks_db_connect() as conn:
                with conn.cursor() as cur:
                    for uid, data in store.items():
                        cur.execute(
                            "INSERT INTO og_github_checks (uid, data) "
                            "VALUES (%s, %s) ON CONFLICT (uid) DO "
                            "UPDATE SET data = EXCLUDED.data",
                            (uid, _Jsonb(data)))
                    cur.execute("SELECT uid FROM og_github_checks")
                    existing = {row[0] for row in cur.fetchall()}
                    for stale in existing - set(store.keys()):
                        cur.execute(
                            "DELETE FROM og_github_checks WHERE "
                            "uid = %s", (stale,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"GitHub checks DB save failed, "
                           f"using file: {e}")
    try:
        with open(_CHECKS_STORE_FILE, 'w') as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Could not save checks store: {e}")


def _get_watches(uid: str) -> list:
    if not uid:
        return []
    with _checks_lock:
        store = _load_checks_store()
    watches = store.get(uid)
    return list(watches) if isinstance(watches, list) else []


def _register_checks_watch(uid: str, watch: Dict):
    with _checks_lock:
        store = _load_checks_store()
        watches = store.get(uid)
        if not isinstance(watches, list):
            watches = []
        watches.append(watch)
        store[uid] = watches[-_CHECKS_KEEP:]
        _save_checks_store(store)


def _update_watch(uid: str, pr_url: str, **fields):
    with _checks_lock:
        store = _load_checks_store()
        watches = store.get(uid)
        if not isinstance(watches, list):
            return
        for watch in watches:
            if isinstance(watch, dict) \
                    and watch.get("pr_url") == pr_url:
                watch.update(fields)
                watch["updated"] = time.time()
        _save_checks_store(store)


def _get_watch(uid: str, pr_url: str) -> Optional[Dict]:
    for watch in _get_watches(uid):
        if isinstance(watch, dict) \
                and watch.get("pr_url") == pr_url:
            return watch
    return None


def _aggregate_check_runs(data) -> tuple:
    """(state, lines) from a check-runs API payload. state is
    'none' (the repo runs no Actions checks), 'pending',
    'success' or 'failure'; lines are (name, status, conclusion)
    per run."""
    if not isinstance(data, dict):
        return "pending", []
    runs = [r for r in (data.get("check_runs") or [])
            if isinstance(r, dict)]
    total = data.get("total_count", len(runs))
    if not total or not runs:
        return "none", []
    lines = [(r.get("name") or "check", r.get("status") or "",
              r.get("conclusion") or "") for r in runs]
    state = "success"
    for _name, st, concl in lines:
        if st != "completed":
            state = "pending"
        elif concl in ("failure", "cancelled", "timed_out",
                       "action_required", "startup_failure"):
            if state != "pending":
                state = "failure"
    return state, lines


def _notify_checks(uid: str, watch: Dict, state: str):
    """Record a PR's terminal checks outcome exactly once, via
    the notification seam (kind 'notice' — og_notify's tidy
    general kind). Fail-safe: a notification can never break
    tracking."""
    verdict = {
        "success": ("✅ GitHub checks passed",
                    "All GitHub Actions checks passed."),
        "failure": ("❌ GitHub checks FAILED",
                    "At least one GitHub Actions check failed."),
        "none": ("🐙 No GitHub Actions checks",
                 "That repo has no GitHub Actions checks, so "
                 "there was nothing to run."),
    }.get(state)
    if verdict is None:
        return
    title, line = verdict
    try:
        import og_notify
        og_notify.record(
            uid, "notice",
            f"{title} — {watch.get('repo_full')}"
            f"#{watch.get('pr_number')}",
            f"{line} PR: {watch.get('pr_url')}")
    except Exception as e:
        logger.warning(f"Checks notify failed: {e}")


def _refresh_watch(entry: Dict, uid: str, watch: Dict) -> Optional[str]:
    """One live check-runs read for a tracked PR; updates the
    stored state and notifies (once) at a terminal state. Returns
    the state, or None when the read itself failed."""
    token = entry.get("access_token", "")
    status, data = _gh_request(
        "GET", f"/repos/{watch['owner']}/{watch['repo']}/commits/"
        + quote(str(watch.get("head_sha", "")), safe="")
        + "/check-runs", token)
    if status == 401:
        _update_watch(uid, watch["pr_url"], status="stopped")
        return "stopped"
    if status != 200:
        return None
    state, lines = _aggregate_check_runs(data)
    fields = {"status": state,
              "checks": [f"{n}: {c or s}" for n, s, c in lines]}
    fresh = _get_watch(uid, watch["pr_url"]) or watch
    if state in _CHECK_TERMINAL and not fresh.get("notified"):
        fields["notified"] = True
        _update_watch(uid, watch["pr_url"], **fields)
        _notify_checks(uid, fresh, state)
    else:
        _update_watch(uid, watch["pr_url"], **fields)
    return state


def _start_checks_watch(uid: str, pr_url: str):
    """The bounded background poller: first look after a short
    delay, then on the poll cadence, until a terminal state or
    the ~20-minute budget runs out (then the watch is marked
    'stopped' — a later ask can still refresh it lazily)."""
    def run():
        deadline = time.time() + _CHECKS_POLL_BUDGET
        delay = _CHECKS_POLL_FIRST
        while time.time() < deadline:
            time.sleep(delay)
            delay = _CHECKS_POLL_SECONDS
            watch = _get_watch(uid, pr_url)
            if watch is None \
                    or watch.get("status") in _CHECK_TERMINAL:
                return
            entry = _github_connection(uid)
            if not entry:
                _update_watch(uid, pr_url, status="stopped")
                return
            try:
                state = _refresh_watch(entry, uid, watch)
            except Exception as e:
                logger.warning(f"Checks poll failed: {e}")
                state = None
            if state in _CHECK_TERMINAL:
                return
        watch = _get_watch(uid, pr_url)
        if watch is not None \
                and watch.get("status") not in _CHECK_TERMINAL:
            _update_watch(uid, pr_url, status="stopped")

    threading.Thread(target=run, daemon=True).start()


def _checks_answer(entry: Optional[Dict], uid: str,
                   consume_lookup) -> Optional[list]:
    """'Did the checks pass on my PR?' — answered from the stored
    watch state, with ONE live refresh while the latest is still
    pending and inside its tracking budget."""
    watches = [w for w in _get_watches(uid) if isinstance(w, dict)]
    who = (entry or {}).get("login") or (entry or {}).get("name") \
        or "the visitor"
    if not watches:
        if not entry:
            return None
        if not _spend(consume_lookup, uid):
            return None
        body = (f"The visitor ({who}) is asking about GitHub "
                "Actions checks, but OG is not tracking any pull "
                "request for them — no PR they approved through OG "
                "is being watched. Tell them plainly, in persona; "
                "do not invent check results.")
        return [{"title": "🐙 GitHub checks — none tracked",
                 "body": body, "href": "/auth/github"}]
    latest = watches[-1]
    if entry and latest.get("status") == "pending" \
            and time.time() - float(latest.get("created", 0)) \
            <= _CHECKS_POLL_BUDGET:
        try:
            _refresh_watch(entry, uid, latest)
        except Exception as e:
            logger.warning(f"Checks lazy refresh failed: {e}")
        latest = _get_watch(uid, latest["pr_url"]) or latest
    if not _spend(consume_lookup, uid):
        logger.info("GitHub checks answer skipped: visitor at "
                    "daily lookup cap")
        return None
    state = latest.get("status", "pending")
    state_line = {
        "success": "✅ ALL CHECKS PASSED",
        "failure": "❌ CHECKS FAILED",
        "pending": "⏳ checks are still running",
        "none": "this repo has NO GitHub Actions checks — "
                "nothing ran",
        "stopped": "OG stopped tracking after ~20 minutes — the "
                   "PR page shows the current state",
    }.get(state, state)
    facts = [
        f"Pull request: {latest.get('repo_full')}"
        f"#{latest.get('pr_number')} ({latest.get('pr_url')})",
        f"Branch: {latest.get('branch')}",
        f"Checks state: {state_line}",
    ]
    for line in latest.get("checks") or []:
        facts.append(f"- {line}")
    if len(watches) > 1:
        others = "; ".join(
            f"{w.get('repo_full')}#{w.get('pr_number')}: "
            f"{w.get('status')}" for w in watches[:-1])
        facts.append(f"Also tracked earlier: {others}")
    body = (f"The visitor ({who}) is asking about the GitHub "
            "Actions checks on their pull request. Answer ONLY "
            "from these facts — OG's tracked state, refreshed "
            "live just now when it was still pending:\n\n"
            + "\n".join(facts))
    return [{"title": "🐙 GitHub checks — "
                       f"{latest.get('repo_full')}",
             "body": body, "href": latest.get("pr_url", "")}]


def _clarify_answer(entry: Dict, job: Dict, message: str,
                    uid: str) -> list:
    """Results-level clarifying question: the ask wants repo work
    but the repo (or the file) can't be pinned down. Exactly ONE
    question, parked so the answer completes the ask."""
    who = entry.get("login") or entry.get("name") or "the visitor"
    path = job.get("path") or ""
    thing = job.get("thing") or ""
    if path:
        question = f"Which repo should OG put {path} in?"
        fields = {"repo_full": "", "thing": thing,
                  "path_hint": path, "orig": str(message)}
    else:
        question = ("Which repo — and which file in it — should "
                    "OG work on?")
        fields = {"repo_full": "", "thing": thing,
                  "path_hint": "", "orig": str(message)}
    return _ask_one_question(uid, fields, question, who)


# --- Execution --------------------------------------------------------------------

def github_results(job, message, uid, consume_lookup):
    """Run one parsed GitHub job for this visitor. Returns
    web_search-shaped results on a hit (real data, grounded empty
    answers, connect/reconnect guidance, draft instructions,
    verified previews, PR outcomes, checks answers), or None on a
    true miss — disabled, upstream failure — so the caller falls
    through to the previous search untouched. Reads and checks
    answers spend one lookup-budget unit per real answer; the PR
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
            file_label = pending.get("path") \
                or pending.get("path_hint") or "as previewed"
            body = (f"The visitor ({who}) said NO to the drafted "
                    f"pull request for {pending.get('repo_full')} "
                    f"(file {file_label}, branch "
                    f"{pending.get('branch')}). The draft is "
                    "SCRAPPED — nothing was written to GitHub. "
                    "Confirm that to them in persona, briefly.")
            return [{"title": "🐙 GitHub PR — scrapped", "body": body,
                     "href": "/auth/github"}]
        if pending.get("stage") == "verified" \
                and pending.get("pkg"):
            # Second YES: the package the visitor approved in the
            # verified preview ships — after the budget spend and
            # _create_pr's own final base re-verification.
            pkg = pending["pkg"]
            if not _spend(consume_lookup, uid):
                _clear_pending(uid)
                body = (f"The visitor ({who}) approved the "
                        "verified pull request preview, but "
                        "they've hit today's lookup limit, so OG "
                        "can't open it right now. NOTHING was "
                        "written to GitHub and the draft is "
                        "scrapped. Tell them plainly, in persona.")
                return [{"title": "🐙 GitHub PR — daily cap",
                         "body": body, "href": "/auth/github"}]
            try:
                return _create_pr(
                    entry, pending,
                    {"path": pkg["path"],
                     "content": pkg["content"]}, uid)
            finally:
                _clear_pending(uid)
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
        if pending.get("verify_first"):
            # First YES on an edit / Python change: verify, test
            # and present the final preview. No budget spent, no
            # write — execution needs the second YES.
            return _verify_pending(entry, pending, extracted, uid)
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

    if kind == "pr_checks":
        # Answered from the watch store; works even when the
        # connection was dropped after the PR opened.
        return _checks_answer(_github_connection(uid), uid,
                              consume_lookup)
    entry = _github_connection(uid)
    if not entry:
        if kind == "pr_clarify" and not job.get("repo_word"):
            # A file-scoped coding ask that never mentioned a
            # repo — generic chat keeps it; no hijack.
            return None
        return _guidance("connect", who_hint)
    if kind == "pr_clarify":
        return _clarify_answer(entry, job, str(message), uid)
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


def _repo_from_history(uid: str) -> Optional[str]:
    """The repo this thread most recently established — read/preview
    answers print a 'Repo: owner/name' line, and that is the only
    history signal trusted for picking a repo (never a guess)."""
    load_history = _deps.get("load_history")
    if load_history is None or not uid:
        return None
    try:
        history = load_history(uid)
    except Exception as e:
        logger.warning(f"GitHub history read failed: {e}")
        return None
    for entry in reversed(history or []):
        if not isinstance(entry, dict):
            continue
        m = re.search(r"^Repo:\s*(\S+/\S+)\s*$",
                      str(entry.get("content", "")), re.M)
        if m:
            return m.group(1)
    return None


def _claim_job(message: str, uid: str) -> Optional[Dict]:
    """What this message means for GitHub: an approval/decline when
    a draft is pending, an answer to a pending clarifying question,
    else a first-person read/write/checks ask."""
    low = " " + re.sub(r"\s+", " ", str(message).lower()).strip() + " "
    pending = _get_pending(uid)
    if pending is not None:
        trimmed = low.strip()
        if _DECLINE_RE.match(trimmed):
            return {"kind": "decline"}
        if pending.get("mode") == "clarify":
            # The visitor is answering OG's ONE clarifying
            # question: a file path (repo already known) or a
            # repo + file. Anything else falls through to a
            # normal parse (which re-earns the question or not).
            path = _extract_path(message) \
                or pending.get("path_hint") or ""
            repo = pending.get("repo_full") \
                or _extract_repo_name(message)
            if not repo:
                # A bare "demo" / "owner/demo" IS the answer to
                # "which repo?" — take the whole reply as the name.
                tok = str(message).strip().strip(".?! ")
                if re.fullmatch(
                        r"[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?", tok):
                    repo = tok
            if path and repo:
                return {"kind": "pr_write", "repo": repo,
                        "thing": pending.get("thing", ""),
                        "path": path,
                        "orig": (pending.get("orig", "") + " "
                                 + str(message)).strip()}
        if _APPROVE_RE.match(trimmed):
            return {"kind": "approve"}
    job = parse_github_intent(message)
    if job is not None:
        if job.get("kind") == "pr_clarify" and not job.get("repo"):
            repo = _repo_from_history(uid)
            if repo:
                return {"kind": "pr_write", "repo": repo,
                        "thing": job.get("thing", ""),
                        "path": job.get("path")}
        return job
    # A file-scoped coding ask with no repo named in the message:
    # claim it for the thread's repo when one is established, else
    # it earns the one clarifying question.
    if _WRITE_VERB_RE.search(low) or _EDIT_VERB_RE.search(low):
        path = _extract_path(message)
        if path:
            repo = _repo_from_history(uid)
            if repo:
                return {"kind": "pr_write", "repo": repo,
                        "thing": _extract_thing(message),
                        "path": path}
            return {"kind": "pr_clarify", "repo": None,
                    "path": path,
                    "thing": _extract_thing(message),
                    "repo_word": False}
    return None


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
