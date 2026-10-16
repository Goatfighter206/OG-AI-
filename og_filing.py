"""Round 110: THE PROJECT FILING HELPER (Brent's order,
2026-10-12 11:04 PDT: "Project filing helper" — ordered
right after the Project Manager, R109: "The PM runs the
work; the Filer keeps the paperwork.")

WHAT THIS IS. The Project Manager (og_pm) runs projects;
this module files things UNDER those projects and finds
them again:

  - WHAT GETS FILED: locker files (og_storage records —
    including fresh temp uploads once they land in the
    locker through the save-to-locker path) and Library
    creations (the og_library producers: videos, songs,
    podcasts, books, motions, 3D models, SFX, projects).
  - FILING IS A LINK, NEVER A MOVE. A filing is durable
    per-account metadata joining an item to a project row
    in og_pm's store. The item itself never moves storage,
    never changes owner, never changes bytes; unfile and
    refile change links only. Deleting a file stays the
    existing housekeeping flow (warn + approval), which
    this module never touches.
  - CHAT-FIRST (natural asks, R75 garble on triggers):
    "file this under the truck project", "what's filed
    under the kitchen job?", "pull up the files for the
    truck project", "take that out of the truck project",
    "move it to the kitchen job".
  - SURFACES: og_progress's project rows carry the files
    count (and the list endpoint); og_library's shelf
    items and the /storage/status list carry the project
    name on filed items. No new seat, no new folder.
  - AUTO-SUGGEST, NEVER AUTO-FILE: when a creation lands
    while one of the account's project Maker steps was
    waiting on it (the step completes after the creation
    appears), the Filer ASKS ONCE at the top of the next
    exchange — "File this under <project>?" — and files
    only on the user's yes. A no is final for that pair.

THE FENCES.
- Per-account isolation BY CONSTRUCTION: a link can only
  join an item and a project that BOTH resolve inside the
  caller's own account (items are enumerated from the
  caller's own locker/Library; projects from og_pm's
  per-account store). There is no code path that names
  another account's anything.
- No new powers, no new loop (R72 doctrine): the filer
  ticks on user exchanges and on its own routes — never
  on a timer, never in a thread.
- Bare YES/NO is claimed ONLY while a filing suggestion
  is pending for this visitor AND no other flow owns the
  answer (the og_storage discipline: ordering/trading
  previews and the storage cleanup proposal win).
- Persona untouched — OG's voice, filing-cabinet job.
  app.py is never touched (install rides the og_fixqueue
  chain after the PM; routes ride og_library).

STATE. Own durable store, the Round 18 pattern
(mirroring og_pm): Postgres table og_filing_data when
OG_MEMORY_DB_URL is set, else a JSON file. Blob kinds per
visitor: 'links' + 'suggest'. Switch: OG_FILING_ENABLED
(unset = ON), in the crew switch family.
"""

import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Dict, List, Optional

import og_garble

from fastapi import Request

logger = logging.getLogger(__name__)

# --- Config -------------------------------------------------------------------

LINK_KEEP = 200          # filing links kept per visitor
ASK_STALE_S = 24 * 3600.0  # a pending suggestion dies quietly after a day
LIST_CAP = 20            # rows in one chat file list

FILING_STORE_FILE = "filing_store.json"
# RLock, not Lock: store updates hold the lock across a
# read-modify-write whose save path re-enters it through
# _put_blob's file branch (the og_pm/og_crew shape).
_filing_lock = threading.RLock()

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, DB_OPTIONS as _DB_OPTS, StoreError, apply_bounds as _apply_bounds
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None


# --- Switch ---------------------------------------------------------------------

def filing_enabled() -> bool:
    """OG_FILING_ENABLED=false is the Filer's own kill
    switch (any other value, or unset, leaves it on)."""
    return os.environ.get(
        "OG_FILING_ENABLED", "true").strip().lower() not in (
            "0", "false", "no", "off")


# --- Durable store (Round 18 pattern, mirroring og_pm) --------------------------

def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=_DB_CT, options=_DB_OPTS)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_filing_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(FILING_STORE_FILE):
        try:
            with open(FILING_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Filing store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(FILING_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Filing store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_filing_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            if row and row[0] is not None:
                return row[0]
            return default
        except Exception as e:
            logger.warning(f"Filing DB load failed: {e}")
            raise StoreError("filing store unavailable (Round 99)") from e
    with _filing_lock:
        store = _load_file_store()
    entry = (store.get(uid) or {}).get(kind)
    return entry if entry is not None else default


def _put_blob(uid: str, kind: str, value):
    if not uid:
        return
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_filing_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data = EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Filing DB save failed: {e}")
            raise StoreError("filing store unavailable (Round 99)") from e
    with _filing_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


# --- Links ----------------------------------------------------------------------

def _links(uid: str) -> List[Dict]:
    got = _blob(uid, "links", [])
    return got if isinstance(got, list) else []


def _save_links(uid: str, links: List[Dict]):
    links.sort(key=lambda x: float(x.get("filed_at", 0) or 0))
    _put_blob(uid, "links", links[-LINK_KEEP:])


def _link_key(link: Dict) -> str:
    return (f"{link.get('source', '')}|{link.get('kind', '')}"
            f"|{link.get('item_id', '')}")


def links_for(uid: str) -> List[Dict]:
    """The read seam (routes + answers): the caller's own
    filing links, newest first."""
    out = [dict(x) for x in _links(uid) if isinstance(x, dict)]
    out.sort(key=lambda x: -float(x.get("filed_at", 0) or 0))
    return out


def counts_for(uid: str) -> Dict[str, int]:
    """{project_id: filed count} for the Progress rows."""
    out: Dict[str, int] = {}
    for x in _links(uid):
        if isinstance(x, dict):
            pid = x.get("project_id", "")
            if pid:
                out[pid] = out.get(pid, 0) + 1
    return out


def projects_by_item(uid: str) -> Dict[str, str]:
    """{item key: project title} — the annotation seam for
    the Library shelf and the locker list. Key shape:
    'locker||<record id>' for locker records (kind-agnostic:
    one record, one id) and 'library|<kind>|<job id>' for
    creations. The earliest filing wins."""
    out: Dict[str, str] = {}
    for x in sorted(_links(uid),
                    key=lambda y: float(y.get("filed_at", 0) or 0)):
        if not isinstance(x, dict):
            continue
        if x.get("source") == "locker":
            key = f"locker||{x.get('item_id', '')}"
        else:
            key = _link_key(x)
        if key and key not in out:
            out[key] = x.get("project_title", "")
    return out


# --- The item universe (the caller's own — isolation by construction) -----------

_KIND_LABELS = {"video": "Video", "song": "Song",
                "podcast": "Podcast", "book": "Book",
                "motion": "Motion video", "model3d": "3D model",
                "sfx": "Sound FX", "project": "Project",
                "image": "Image", "file": "File",
                "text": "Text file", "pdf": "PDF"}


def _kind_label(kind: str) -> str:
    return _KIND_LABELS.get(kind or "", (kind or "Item").capitalize())


def _fmt_date(ts) -> str:
    ts = float(ts or 0)
    if not ts:
        return ""
    return time.strftime("%b %d, %Y", time.localtime(ts))


def _locker_items(uid: str) -> List[Dict]:
    out = []
    try:
        import og_storage
        for rec in og_storage.list_records(uid):
            out.append({
                "source": "locker", "kind": rec.get("kind", "") or "file",
                "item_id": rec.get("id", ""),
                "name": rec.get("name") or "file",
                "ts": float(rec.get("uploaded_at", 0) or 0),
            })
    except Exception as e:
        logger.warning(f"Filing locker read failed: {e}")
    return [i for i in out if i["item_id"]]


def _library_items(uid: str) -> List[Dict]:
    out = []
    try:
        import og_library
        producers = (og_library._video_items, og_library._song_items,
                     og_library._podcast_items, og_library._book_items,
                     og_library._motion_items, og_library._model3d_items,
                     og_library._sfx_items, og_library._project_items)
        for fn in producers:
            try:
                got = fn(uid)
            except Exception as e:
                logger.warning(f"Filing library section failed: {e}")
                got = []
            for it in got or []:
                if not it.get("id"):
                    continue
                out.append({
                    "source": "library", "kind": it.get("kind", ""),
                    "item_id": it.get("id", ""),
                    "name": it.get("title") or "creation",
                    "ts": float(it.get("created_at", 0) or 0),
                })
    except Exception as e:
        logger.warning(f"Filing library read failed: {e}")
    return out


def _all_items(uid: str) -> List[Dict]:
    items = _locker_items(uid) + _library_items(uid)
    items.sort(key=lambda i: -i["ts"])
    return items


def _resolve_item(uid: str, source: str, kind: str,
                  item_id: str) -> Optional[Dict]:
    """The ownership proof in one place: an item ref only
    resolves if it appears in the CALLER's own locker or
    Library. Another account's id resolves to nothing."""
    for it in _all_items(uid):
        if it["source"] == source and it["item_id"] == item_id \
                and (source == "locker" or it["kind"] == kind):
            return it
    return None


def _last_item(uid: str) -> Optional[Dict]:
    items = _all_items(uid)
    return items[0] if items else None


def _find_item(uid: str, query: str) -> Optional[Dict]:
    q = " ".join(str(query or "").lower().split())
    if not q:
        return None
    matches = [i for i in _all_items(uid)
               if q in str(i.get("name", "")).lower()]
    if not matches:
        return None
    exact = [i for i in matches
             if str(i.get("name", "")).lower() == q]
    return (exact or matches)[0]


def _temp_upload_waiting(uid: str) -> bool:
    """A fresh upload that hasn't been saved to the locker
    yet — filing can't link it until it lands (the
    save-to-locker path), and the answer says so."""
    try:
        import og_file_read
        return bool(og_file_read.get_meta(uid))
    except Exception:
        return False


# --- Projects (og_pm's store — one project truth, no fork) -----------------------

def _projects(uid: str) -> List[Dict]:
    try:
        import og_pm
        return [p for p in og_pm.projects_for(uid)
                if p.get("state") != "cancelled"]
    except Exception as e:
        logger.warning(f"Filing projects read failed: {e}")
        return []


def _find_project(uid: str, query: str) -> Optional[Dict]:
    q = " ".join(str(query or "").lower().split())
    q = re.sub(r"\bprojects?\b$", "", q).strip()
    q = re.sub(r"^the\s+", "", q).strip()
    if not q:
        return None
    views = _projects(uid)
    for p in views:
        if str(p.get("title", "")).lower() == q:
            return p
    hits = [p for p in views if q in str(p.get("title", "")).lower()]
    if hits:
        return hits[0]  # projects_for is newest-first
    hits = [p for p in views
            if str(p.get("title", "")).lower()
            and str(p.get("title", "")).lower() in q]
    if hits:
        return hits[0]
    # Unique-token match: "the kitchen job" -> "Kitchen
    # remodel research" when exactly one project carries
    # the query's distinctive first token.
    toks = [t for t in re.split(r"[^a-z0-9]+", q) if len(t) >= 3]
    if toks:
        hits = [p for p in views
                if toks[0] in str(p.get("title", "")).lower()]
        if len(hits) == 1:
            return hits[0]
    return None


def _project_by_id(uid: str, project_id: str) -> Optional[Dict]:
    try:
        import og_pm
        p = og_pm._get_project(uid, project_id)
        if p and p.get("state") != "cancelled":
            return p
    except Exception as e:
        logger.warning(f"Filing project read failed: {e}")
    return None


# --- Filing operations ------------------------------------------------------------

def file_link(uid: str, project_id: str, source: str, kind: str,
              item_id: str) -> Optional[Dict]:
    """Create one link — after proving BOTH ends belong to
    the caller. Returns the link, or None when either end
    doesn't resolve in this account (the cross-account
    refusal is this returning None). Idempotent: re-filing
    the same pair returns the existing link."""
    proj = _project_by_id(uid, project_id)
    if not proj:
        return None
    item = _resolve_item(uid, source, kind, item_id)
    if not item:
        return None
    with _filing_lock:
        links = _links(uid)
        for x in links:
            if isinstance(x, dict) \
                    and x.get("project_id") == project_id \
                    and x.get("source") == item["source"] \
                    and x.get("item_id") == item["item_id"] \
                    and (item["source"] == "locker"
                         or x.get("kind") == item["kind"]):
                return x
        link = {"id": uuid.uuid4().hex[:12],
                "project_id": project_id,
                "project_title": proj.get("title", ""),
                "source": item["source"], "kind": item["kind"],
                "item_id": item["item_id"], "name": item["name"],
                "item_ts": item["ts"], "filed_at": time.time()}
        links.append(link)
        _save_links(uid, links)
    _clear_pending_if(uid, project_id, item)
    return link


def unfile_link(uid: str, project_id: str, source: str,
                kind: str, item_id: str) -> bool:
    """Remove one link. The item itself is never touched —
    links only (drill-pinned against usage + bytes)."""
    with _filing_lock:
        links = _links(uid)
        kept = [x for x in links
                if not (isinstance(x, dict)
                        and x.get("project_id") == project_id
                        and x.get("source") == source
                        and x.get("item_id") == item_id
                        and (source == "locker"
                             or x.get("kind") == kind))]
        if len(kept) == len(links):
            return False
        _save_links(uid, kept)
    return True


def files_for_project(uid: str, project_id: str) -> List[Dict]:
    """The project's file list (names + kinds + dates),
    newest filing first. A link whose item has since been
    deleted stays visible, flagged gone — filing never
    hides the paperwork."""
    out = []
    for x in links_for(uid):
        if x.get("project_id") != project_id:
            continue
        live = _resolve_item(uid, x.get("source", ""),
                             x.get("kind", ""), x.get("item_id", ""))
        out.append({
            "name": x.get("name", ""), "kind": x.get("kind", ""),
            "kind_label": _kind_label(x.get("kind", "")),
            "source": x.get("source", ""),
            "item_id": x.get("item_id", ""),
            "date": _fmt_date(x.get("item_ts", 0)),
            "filed_at": float(x.get("filed_at", 0) or 0),
            "exists": live is not None,
        })
    return out


def projects_of_item(uid: str, source: str, kind: str,
                     item_id: str) -> List[Dict]:
    out = []
    for x in _links(uid):
        if isinstance(x, dict) and x.get("source") == source \
                and x.get("item_id") == item_id \
                and (source == "locker" or x.get("kind") == kind):
            out.append(x)
    return out


# --- The auto-suggest (asks once, files only on yes) ------------------------------

def _suggest_state(uid: str) -> Dict:
    got = _blob(uid, "suggest", {})
    if not isinstance(got, dict):
        got = {}
    got.setdefault("pending", None)
    got.setdefault("asked", [])
    got.setdefault("seen", {})
    return got


def _save_suggest_state(uid: str, state: Dict):
    state["asked"] = state.get("asked", [])[-200:]
    _put_blob(uid, "suggest", state)


def _pending_suggestion(uid: str) -> Optional[Dict]:
    st = _suggest_state(uid)
    pend = st.get("pending")
    if not pend:
        return None
    if time.time() - float(pend.get("ts", 0) or 0) > ASK_STALE_S:
        st["pending"] = None
        _save_suggest_state(uid, st)
        return None
    return pend


def _clear_pending_if(uid: str, project_id: str, item: Dict):
    st = _suggest_state(uid)
    pend = st.get("pending")
    if pend and pend.get("project_id") == project_id \
            and pend.get("item_id") == item.get("item_id") \
            and pend.get("source") == item.get("source"):
        st["pending"] = None
        _save_suggest_state(uid, st)


def _ask_result(pend: Dict) -> Dict:
    return {
        "title": "📁 Project Filing — a quick one",
        "body": ("FIRST — before answering anything else — "
                 "ask the user this, plainly and in persona, "
                 "then answer their message: A new creation "
                 f"(\"{pend.get('name', '')}\") landed while "
                 "the project "
                 f"\"{pend.get('project_title', '')}\" was "
                 "waiting on a make step. Ask: \"File this "
                 f"under {pend.get('project_title', '')}?\" "
                 "— it files only if they say yes."),
        "href": ""}


def _tick(uid: str) -> Optional[Dict]:
    """The suggestion watcher — runs on exchanges and route
    reads only (no loop, R72). Diffs og_pm's maker steps
    against the last seen states: a maker step that was
    waiting/queued/working and is now DONE means a
    creation was made for that project; the newest unfiled
    item from that window is offered — once, ever, per
    (item, project) pair."""
    if not filing_enabled():
        return None
    try:
        import og_pm
        views = og_pm.projects_for(uid)
    except Exception as e:
        logger.warning(f"Filing tick projects failed: {e}")
        return None
    st = _suggest_state(uid)
    changed = False
    ask = None
    now = time.time()
    for p in views:
        if p.get("state") == "cancelled":
            continue
        pid = p.get("id", "")
        for s in p.get("steps") or []:
            if s.get("lane") != "maker":
                continue
            key = f"{pid}:{s.get('n')}"
            prev = st["seen"].get(key)
            state_now = s.get("state", "")
            if prev is None:
                st["seen"][key] = {"state": state_now, "since": now}
                changed = True
                continue
            if prev.get("state") != state_now:
                was = prev.get("state")
                since = float(prev.get("since", 0) or 0)
                prev["state"] = state_now
                prev["since"] = now
                changed = True
                if was in ("waiting", "queued", "working") \
                        and state_now == "done" \
                        and not st.get("pending"):
                    cand = _suggest_candidate(uid, p, since)
                    if cand:
                        st["pending"] = cand
                        st["asked"].append(
                            f"{cand['source']}|{cand['kind']}"
                            f"|{cand['item_id']}|{pid}")
                        ask = _ask_result(cand)
    if changed:
        _save_suggest_state(uid, st)
    return ask


def _suggest_candidate(uid: str, proj: Dict,
                       since: float) -> Optional[Dict]:
    st = _suggest_state(uid)
    asked = set(st.get("asked", []))
    pid = proj.get("id", "")
    filed = {(_link_key(x)) for x in _links(uid)
             if isinstance(x, dict)
             and x.get("project_id") == pid}
    for it in _all_items(uid):
        if it["ts"] and since and it["ts"] < since - 5:
            continue
        key = f"{it['source']}|{it['kind']}|{it['item_id']}|{pid}"
        if key in asked:
            continue
        if _link_key(it) in filed:
            continue
        return {"project_id": pid,
                "project_title": proj.get("title", ""),
                "source": it["source"], "kind": it["kind"],
                "item_id": it["item_id"], "name": it["name"],
                "ts": time.time()}
    return None


# --- Chat claims (narrow frames — the crew/PM doctrine) ----------------------------

_YES = {"yes", "yeah", "yep", "yup", "yes please", "do it", "go",
        "go ahead", "confirm", "ok", "okay", "sure", "file it",
        "yes file it"}
_NO = {"no", "nope", "nah", "don't", "do not", "cancel", "stop",
       "never mind", "nevermind", "skip it", "no thanks",
       "keep it out"}

_GARBLE_VOCAB = ("file", "filed", "filing", "unfile", "files",
                 "project", "projects")

_FILE_LAST_RE = re.compile(
    r"^file\s+(this|that|it|the file|the upload|my file)\s+"
    r"(?:under|in|into|to)\s+(.+)$", re.I)
_FILE_NAMED_RE = re.compile(
    r"^file\s+(.+?)\s+(?:under|in|into|to)\s+(.+)$", re.I)
_LIST_RE = re.compile(
    r"^(?:what'?s|what is)\s+filed\s+(?:under|in)\s+(.+)$", re.I)
_LIST2_RE = re.compile(
    r"^(?:pull up|show(?:\s+me)?|list|get)\s+(?:the\s+)?files?\s+"
    r"(?:for|under|in)\s+(.+)$", re.I)
_LIST3_RE = re.compile(
    r"^files\s+(?:for|under|in)\s+(.+)$", re.I)
_LIST4_RE = re.compile(
    r"^what\s+files\s+are\s+(?:filed\s+)?(?:under|in)\s+(.+)$", re.I)
_UNFILE_ANY_RE = re.compile(
    r"^unfile\s+(this|that|it)$", re.I)
_UNFILE_LAST_RE = re.compile(
    r"^(?:take|remove)\s+(this|that|it)\s+(?:out of|from)\s+(.+)$",
    re.I)
_UNFILE_NAMED_RE = re.compile(
    r"^(?:take|remove)\s+(.+?)\s+(?:out of|from)\s+(.+)$", re.I)
_MOVE_LAST_RE = re.compile(
    r"^(?:move|refile)\s+(this|that|it)\s+(?:to|under|into)\s+(.+)$",
    re.I)
_MOVE_NAMED_RE = re.compile(
    r"^move\s+(.+?)\s+to\s+(.+)$", re.I)
_OVERVIEW_RE = re.compile(
    r"^(?:what'?s|what is)\s+filed\??$|^my filings$|"
    r"^show\s+(?:me\s+)?(?:my\s+)?filings$", re.I)


def _other_flow_pending(uid: str) -> bool:
    """Another flow owns a bare YES/NO right now (the
    og_storage discipline): ordering/trading previews and
    the storage cleanup proposal win over the Filer's
    suggestion answer."""
    try:
        import og_storage
        if og_storage._other_pending(uid):
            return True
        if og_storage._pending_for(uid):
            return True
    except Exception:
        pass
    return False


def _project_claim_ok(uid: str, project_q: str) -> bool:
    """A filing frame only claims when it clearly means a
    project: the word 'project' is in the tail, or the tail
    resolves to one of the caller's projects (incl. the
    unique-token match). Otherwise the message delegates
    down the seam chain untouched — 'file a bug report to
    GitHub' is not the Filer's."""
    q = str(project_q or "").lower()
    if "project" in q:
        return True
    return _find_project(uid, project_q or "") is not None


def _claim_exact(message: str, uid: str) -> Optional[Dict]:
    raw = str(message or "").strip()
    if not raw or not uid:
        return None
    low = " ".join(raw.lower().split()).rstrip("!.")
    # The suggestion answer comes first — but ONLY while a
    # suggestion is pending for this visitor and no other
    # flow owns the yes/no (house discipline).
    if _pending_suggestion(uid) and not _other_flow_pending(uid):
        if low in _YES:
            return {"op": "suggest_yes"}
        if low in _NO:
            return {"op": "suggest_no"}
    m = _FILE_LAST_RE.match(raw)
    if m and _project_claim_ok(uid, m.group(2)):
        return {"op": "file", "item_q": None,
                "project_q": m.group(2)}
    m = _FILE_NAMED_RE.match(raw)
    if m and m.group(1).strip().lower() not in (
            "this", "that", "it") \
            and _project_claim_ok(uid, m.group(2)):
        return {"op": "file", "item_q": m.group(1),
                "project_q": m.group(2)}
    for rx in (_LIST_RE, _LIST2_RE, _LIST3_RE, _LIST4_RE):
        m = rx.match(raw)
        if m and _project_claim_ok(uid, m.group(1)):
            return {"op": "list", "project_q": m.group(1)}
    if _OVERVIEW_RE.match(raw):
        return {"op": "overview"}
    m = _MOVE_LAST_RE.match(raw)
    if m and _project_claim_ok(uid, m.group(2)):
        return {"op": "move", "item_q": None,
                "project_q": m.group(2)}
    m = _MOVE_NAMED_RE.match(raw)
    if m and _project_claim_ok(uid, m.group(2)):
        return {"op": "move", "item_q": m.group(1),
                "project_q": m.group(2)}
    m = _UNFILE_LAST_RE.match(raw)
    if m and _project_claim_ok(uid, m.group(2)):
        return {"op": "unfile", "item_q": None,
                "project_q": m.group(2)}
    m = _UNFILE_NAMED_RE.match(raw)
    if m and _project_claim_ok(uid, m.group(2)):
        return {"op": "unfile", "item_q": m.group(1),
                "project_q": m.group(2)}
    m = _UNFILE_ANY_RE.match(raw)
    if m:
        return {"op": "unfile", "item_q": None, "project_q": None}
    return None


def _claim_filing(message: str, uid: str):
    """(claim, garble_info): the exact claim first; on a
    miss, the Round 75 second chance — og_garble repairs
    the TRIGGER words only; the payload passes verbatim."""
    claim = _claim_exact(message, uid)
    if claim:
        return claim, None
    try:
        repaired, info = og_garble.repair_info(
            str(message or ""), _GARBLE_VOCAB)
        if info:
            claim = _claim_exact(repaired, uid)
            if claim:
                return claim, info
    except Exception:
        pass
    return None, None


# --- Operations (chat answers — facts for the model to relay in persona) -----------

def _result(tag: str, title: str, body: str,
            href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body,
             "href": href}]


def _project_or_complain(uid: str, project_q: str):
    proj = _find_project(uid, project_q or "")
    if proj:
        return proj, None
    views = _projects(uid)
    if views:
        names = "; ".join(f"\"{p.get('title', '')}\""
                          for p in views[:5])
        body = (f"There's no project matching "
                f"\"{project_q}\" on your board. Your projects: "
                f"{names}. Say the filing again with one of "
                "those names and I'll file it.")
    else:
        body = ("You don't have any projects on the board "
                "yet — hand OG a project first (\"plan a "
                "project to …\") and then I'll file things "
                "under it.")
    return None, _result("OG PROJECT FILING",
                         "Project Filing — no such project", body)


def _item_or_complain(uid: str, item_q: Optional[str]):
    if item_q:
        item = _find_item(uid, item_q)
        if item:
            return item, None
        body = (f"I can't find anything named \"{item_q}\" "
                "in your locker or Library — nothing was "
                "filed. Check the name and say it again.")
        return None, _result("OG PROJECT FILING",
                             "Project Filing — no such item", body)
    item = _last_item(uid)
    if item:
        return item, None
    if _temp_upload_waiting(uid):
        body = ("That upload isn't in your locker yet — "
                "filing links what's saved. Say \"save that "
                "to my locker\" first, then \"file this "
                "under …\" and I'll link it.")
    else:
        body = ("There's nothing in your locker or Library "
                "to file yet — make or save something first, "
                "then say \"file this under …\".")
    return None, _result("OG PROJECT FILING",
                         "Project Filing — nothing to file", body)


def _do_file(uid: str, claim: Dict) -> List[Dict]:
    proj, err = _project_or_complain(uid, claim.get("project_q", ""))
    if err:
        return err
    item, err = _item_or_complain(uid, claim.get("item_q"))
    if err:
        return err
    already = projects_of_item(uid, item["source"], item["kind"],
                               item["item_id"])
    if any(x.get("project_id") == proj.get("id") for x in already):
        return _result(
            "OG PROJECT FILING", "Project Filing — already filed",
            f"\"{item['name']}\" is already filed under "
            f"\"{proj.get('title', '')}\" — no duplicate link "
            "made.")
    link = file_link(uid, proj.get("id", ""), item["source"],
                     item["kind"], item["item_id"])
    if not link:
        return _result(
            "OG PROJECT FILING", "Project Filing — glitch",
            "That filing didn't land — nothing was linked. "
            "Say it again and I'll retry.")
    n = counts_for(uid).get(proj.get("id", ""), 0)
    return _result(
        "OG PROJECT FILING", "Project Filing — filed",
        f"Filed — \"{item['name']}\" "
        f"({_kind_label(item['kind'])}) is now under "
        f"\"{proj.get('title', '')}\" ({n} file"
        f"{'s' if n != 1 else ''} there). The item itself "
        "didn't move — filing is just the link.")


def _do_list(uid: str, claim: Dict) -> List[Dict]:
    proj, err = _project_or_complain(uid, claim.get("project_q", ""))
    if err:
        return err
    rows = files_for_project(uid, proj.get("id", ""))
    title = proj.get("title", "")
    if not rows:
        return _result(
            "OG PROJECT FILING", "Project Filing — the list",
            f"Nothing is filed under \"{title}\" yet. Say "
            "\"file this under "
            f"{title}\" with something in hand and I'll "
            "link it.")
    lines = [f"Filed under \"{title}\" ({len(rows)}):"]
    for r in rows[:LIST_CAP]:
        line = f"  • {r['name']} — {r['kind_label']}"
        if r["date"]:
            line += f", {r['date']}"
        if not r["exists"]:
            line += " (deleted since — the link stays as the record)"
        lines.append(line)
    if len(rows) > LIST_CAP:
        lines.append(f"  … and {len(rows) - LIST_CAP} more.")
    return _result("OG PROJECT FILING", "Project Filing — the list",
                   "\n".join(lines))


def _do_unfile(uid: str, claim: Dict) -> List[Dict]:
    item, err = _item_or_complain(uid, claim.get("item_q"))
    if err:
        return err
    filed = projects_of_item(uid, item["source"], item["kind"],
                             item["item_id"])
    if not filed:
        return _result(
            "OG PROJECT FILING", "Project Filing — not filed",
            f"\"{item['name']}\" isn't filed under any "
            "project — nothing to take out.")
    project_q = claim.get("project_q")
    if project_q:
        proj, err = _project_or_complain(uid, project_q)
        if err:
            return err
        target = proj.get("id", "")
    elif len(filed) == 1:
        target = filed[0].get("project_id", "")
    else:
        names = "; ".join(f"\"{x.get('project_title', '')}\""
                          for x in filed)
        return _result(
            "OG PROJECT FILING", "Project Filing — which project",
            f"\"{item['name']}\" is filed under more than "
            f"one project: {names}. Say \"take it out of "
            "the … project\" with the one you mean.")
    hit = next((x for x in filed
                if x.get("project_id") == target), None)
    if not hit:
        return _result(
            "OG PROJECT FILING", "Project Filing — not filed there",
            f"\"{item['name']}\" isn't filed under "
            f"\"{(proj if project_q else {}).get('title', '')}\" "
            "— nothing changed.")
    unfile_link(uid, target, item["source"], item["kind"],
                item["item_id"])
    return _result(
        "OG PROJECT FILING", "Project Filing — unfiled",
        f"Done — \"{item['name']}\" is out of "
        f"\"{hit.get('project_title', '')}\". Only the link "
        "came off; the file itself is untouched.")


def _do_move(uid: str, claim: Dict) -> List[Dict]:
    proj, err = _project_or_complain(uid, claim.get("project_q", ""))
    if err:
        return err
    item, err = _item_or_complain(uid, claim.get("item_q"))
    if err:
        return err
    filed = projects_of_item(uid, item["source"], item["kind"],
                             item["item_id"])
    if any(x.get("project_id") == proj.get("id") for x in filed):
        return _result(
            "OG PROJECT FILING", "Project Filing — already there",
            f"\"{item['name']}\" is already under "
            f"\"{proj.get('title', '')}\" — nothing to move.")
    if not filed:
        link = file_link(uid, proj.get("id", ""), item["source"],
                         item["kind"], item["item_id"])
        if not link:
            return _result(
                "OG PROJECT FILING", "Project Filing — glitch",
                "That move didn't land — nothing changed. "
                "Say it again and I'll retry.")
        return _result(
            "OG PROJECT FILING", "Project Filing — filed",
            f"\"{item['name']}\" wasn't filed anywhere, so "
            f"I filed it straight under "
            f"\"{proj.get('title', '')}\".")
    if len(filed) > 1:
        names = "; ".join(f"\"{x.get('project_title', '')}\""
                          for x in filed)
        return _result(
            "OG PROJECT FILING", "Project Filing — which project",
            f"\"{item['name']}\" is filed under more than "
            f"one project: {names}. Unfile it from the one "
            "you mean first, then move it.")
    src = filed[0]
    unfile_link(uid, src.get("project_id", ""), item["source"],
                item["kind"], item["item_id"])
    link = file_link(uid, proj.get("id", ""), item["source"],
                     item["kind"], item["item_id"])
    if not link:
        # restore the old link — a move never strands an item
        file_link(uid, src.get("project_id", ""), item["source"],
                  item["kind"], item["item_id"])
        return _result(
            "OG PROJECT FILING", "Project Filing — glitch",
            "That move didn't land — the filing is right "
            "where it was. Say it again and I'll retry.")
    return _result(
        "OG PROJECT FILING", "Project Filing — moved",
        f"Moved — \"{item['name']}\" went from "
        f"\"{src.get('project_title', '')}\" to "
        f"\"{proj.get('title', '')}\". Links only — the "
        "item itself never moved.")


def _do_overview(uid: str) -> List[Dict]:
    counts = counts_for(uid)
    if not counts:
        return _result(
            "OG PROJECT FILING", "Project Filing — overview",
            "Nothing is filed under any project yet. Say "
            "\"file this under …\" with something in hand "
            "and I'll start the paperwork.")
    titles = {p.get("id", ""): p.get("title", "")
              for p in _projects(uid)}
    lines = ["Your filings:"]
    for pid, n in sorted(counts.items(),
                         key=lambda kv: -kv[1]):
        lines.append(f"  • \"{titles.get(pid, 'a project')}\""
                     f" — {n} file{'s' if n != 1 else ''}")
    return _result("OG PROJECT FILING", "Project Filing — overview",
                   "\n".join(lines))


def _do_suggest_answer(uid: str, yes: bool) -> List[Dict]:
    pend = _pending_suggestion(uid)
    st = _suggest_state(uid)
    st["pending"] = None
    _save_suggest_state(uid, st)
    if not pend:
        return _result("OG PROJECT FILING", "Project Filing",
                       "No filing suggestion is open right now.")
    if not yes:
        return _result(
            "OG PROJECT FILING", "Project Filing — skipped",
            f"Okay — \"{pend.get('name', '')}\" stays "
            f"unfiled, and I won't ask about that one for "
            f"\"{pend.get('project_title', '')}\" again.")
    link = file_link(uid, pend.get("project_id", ""),
                     pend.get("source", ""), pend.get("kind", ""),
                     pend.get("item_id", ""))
    if not link:
        return _result(
            "OG PROJECT FILING", "Project Filing — glitch",
            "That filing didn't land — nothing was linked. "
            "Say \"file this under "
            f"{pend.get('project_title', '')}\" and I'll "
            "do it direct.")
    return _result(
        "OG PROJECT FILING", "Project Filing — filed",
        f"Filed — \"{pend.get('name', '')}\" is now under "
        f"\"{pend.get('project_title', '')}\".")


def filing_results(claim: Dict, uid: str) -> List[Dict]:
    op = claim.get("op")
    if op == "file":
        return _do_file(uid, claim)
    if op == "list":
        return _do_list(uid, claim)
    if op == "unfile":
        return _do_unfile(uid, claim)
    if op == "move":
        return _do_move(uid, claim)
    if op == "overview":
        return _do_overview(uid)
    if op == "suggest_yes":
        return _do_suggest_answer(uid, True)
    if op == "suggest_no":
        return _do_suggest_answer(uid, False)
    return _do_overview(uid)


# --- The seam ---------------------------------------------------------------------

_pending = {"claim": None, "garble": None, "ask": None}


def install_filing_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search OUTSIDE the PM seam
    (installed last, from the og_fixqueue chain) so filing
    asks claim here, a queued filing suggestion delivers at
    the top of the next exchange (the R81 pattern), and
    everything else delegates back untouched. Persona
    files never touched."""
    if getattr(agent_instance, "_og_filing_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["claim"] = None
        _pending["garble"] = None
        _pending["ask"] = None
        try:
            uid = get_uid()
            if uid:
                # Tick on the exchange (no timer — R72):
                # a maker-step completion detected now asks
                # now, at the top of this exchange.
                try:
                    _pending["ask"] = _tick(uid)
                except Exception as e:
                    logger.warning(f"Filing tick failed: {e}")
                if filing_enabled():
                    claim, info = _claim_filing(str(message), uid)
                    if claim:
                        _pending["claim"] = claim
                        _pending["garble"] = info
                if _pending["claim"] or _pending["ask"]:
                    if isinstance(intent, dict):
                        intent["needs_code_generation"] = False
                        if not intent.get("needs_web_search"):
                            intent["needs_web_search"] = True
                            intent["search_query"] = \
                                str(message).strip()
        except Exception as e:
            logger.warning(f"Filing trigger check failed: {e}")
            _pending["claim"] = None
            _pending["garble"] = None
            _pending["ask"] = None
        return intent

    def search_wrapped(query, num_results=5):
        claim = _pending.get("claim")
        garble = _pending.get("garble")
        ask = _pending.get("ask")
        _pending["claim"] = None
        _pending["garble"] = None
        _pending["ask"] = None
        uid = ""
        try:
            uid = get_uid()
        except Exception:
            uid = ""
        results = None
        if claim and uid:
            try:
                results = filing_results(claim, uid)
            except Exception as e:
                logger.warning(f"Filing op failed: {e}")
                results = _result(
                    "OG PROJECT FILING",
                    "Project Filing — glitch",
                    "The filing ask glitched — nothing was "
                    "linked or unlinked. Say it again and "
                    "I'll retry.")
        if results is None:
            results = prev_search(query, num_results)
        if ask and results is not None \
                and not (claim and claim.get("op") in
                         ("suggest_yes", "suggest_no")):
            results = [ask] + list(results)
        if garble and results:
            results = og_garble.annotate(results, garble,
                                         prepend=True)
        return results

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_filing_installed = True


# --- Routes -------------------------------------------------------------------------

def register_filing_routes(app):
    """GET /filing (the caller's projects with file counts
    + their links) and GET /filing/project/<id> (one
    project's file list — the Progress row's tap-through
    target). Identity is the caller's cookie uid; another
    account's project id answers no_such_project."""

    @app.get("/filing")
    async def filing_route(request: Request):
        uid = request.cookies.get("ogai_uid") or ""
        projects = []
        if uid:
            try:
                _tick(uid)
            except Exception as e:
                logger.warning(f"Filing tick failed: {e}")
            counts = counts_for(uid)
            for p in _projects(uid):
                projects.append({
                    "id": p.get("id", ""),
                    "title": p.get("title", ""),
                    "state": p.get("state", ""),
                    "files": counts.get(p.get("id", ""), 0)})
        return {"ok": True, "enabled": filing_enabled(),
                "projects": projects,
                "links": links_for(uid) if uid else []}

    @app.get("/filing/project/{pid}")
    async def filing_project_route(pid: str, request: Request):
        uid = request.cookies.get("ogai_uid") or ""
        proj = _project_by_id(uid, pid) if uid else None
        if not proj:
            return {"ok": False, "error": "no_such_project",
                    "files": []}
        return {"ok": True,
                "project": {"id": proj.get("id", ""),
                            "title": proj.get("title", ""),
                            "state": proj.get("state", "")},
                "files": files_for_project(uid, pid)}
