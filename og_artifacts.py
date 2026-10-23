"""Ticket P — OG ARTIFACTS: OG builds pages, documents
and tables for a user on request (OG W, Brent 2026-10-19:
"Because that's cool and I want he to be able to do
that").

Before this module the Library was a SHELF, not a
factory: it gathered what OG's maker tools produced, but
OG could not build a page, a document or a tracker when
a user asked. This module is the factory for the three
v1 kinds:

- page     — the Cash Guard class: a titled page laying
             the user's own figures out in sections,
             with a section total per section and a
             what-if slider where the data supports it
             (a section with a total and at least two
             figures).
- document — a written deliverable: the user's own
             words, structured into paragraphs. OG
             invents no content for it.
- table    — a table/spreadsheet: rows and columns from
             the user's own data.

THE LOOP (the video/song makers' shape, as the ticket
orders). The chat seam (install_artifact_tools) claims
the ask in detect_intent; the wrapped web_search returns
a grounded directive block the persona voices — a short
claim line, one best answer (Tickets I/V). The build
itself runs on a background thread (Main Thread rule):
the record flips working -> ready, a notification lands
(og_notify, kind "artifact_done", Feed target = the
Library), and the artifact appears in the Library as its
own item kind ("Page" / "Document" / "Table"), newest
first, Open action, through og_library's aggregate.

REAL DATA ONLY (the Tickets L/M law). An artifact is
built ONLY from what the user put in the ask (or the
follow-up they answer with): figures, rows and words are
the user's own, never invented. The only numbers OG adds
are arithmetic on the user's numbers — a section total,
labeled as a total — and the what-if slider's live
recompute of that same total in the page. An ask with no
data in it builds nothing: OG asks, in persona, what goes
on it, and the follow-up message completes the claim
(the makers' pending discipline).

EDITABLE BY ASKING. "Update my budget page — rent went
up $50" revises the SAME record in place: same id, same
Library item, revision + 1, no duplicate (No Junk
Drawer: one authoritative version per artifact). Rows
change by label; a document gains a paragraph.

QUOTA. Artifact bytes count against the user's tier
storage quota like locker files: the claim checks the
combined usage (og_storage locker bytes + artifact
bytes) against og_storage.quota_bytes(tier) before
anything is built, and og_library's storage line reports
the same combined figure, so the accounting the user
sees is the accounting that binds.

PER-USER. Records live in this module's own store,
keyed by the caller's one identity (the og_library
_request_uid resolution: the signed-in account's uid,
else the visitor cookie). Every open re-checks the
owner: GET /artifacts/<id> answers the owner's own page
and 404 for anyone else — a stranger's id does not even
confirm the artifact exists. Records die with the
account (purge_owner, the og_store_ready cascade).

Kill switch: OG_ARTIFACTS_ENABLED — enabled unless the
env value is one of 0/false/no/off (the house pattern).
Disabled, the routes answer 404 and the chat seam claims
nothing, so chat behaves exactly as it did before.

Handlers are plain def (FastAPI threadpool), never
async; builds run on daemon threads. app.py is at its
size ceiling: this module self-contains everything and
app.py carries only the import, the tool install, and
the bind + register lines (the og_songs pattern).
"""

import html
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

# --- Kill switch ---------------------------------------------------------------


def artifacts_enabled() -> bool:
    """OG_ARTIFACTS_ENABLED is the kill switch: enabled
    unless the env value is an explicit off."""
    return os.environ.get(
        "OG_ARTIFACTS_ENABLED", "true").strip().lower() not in (
            "0", "false", "no", "off")


# --- App deps (bind_app, the og_songs pattern) ---------------------------------

_DEPS: Dict = {}


def bind_app(deps):
    _DEPS.update(deps or {})


def _tier_now(uid: str) -> str:
    fn = _DEPS.get("get_tier")
    try:
        if fn:
            return str(fn() or "free")
    except Exception:
        pass
    return "free"


# --- Limits (stated in the round report) ----------------------------------------

MAX_ARTIFACTS = 100          # per user — the shelf is not a junk drawer
MAX_WORKING = 3              # concurrent builds per user
MAX_ROWS = 200               # figures/rows per artifact
MAX_PARAGRAPHS = 100         # document paragraphs per artifact
MAX_ASK_CHARS = 8000         # one ask's data payload
PENDING_TTL_S = 600          # the makers' 10-minute pending discipline


# --- Store (house blob pattern, as in og_hubrange) ------------------------------
# One authoritative record set per user: table
# og_artifacts_data (uid, kind) via og_db when
# OG_MEMORY_DB_URL is set, else the JSON file below.
# kind "artifacts" -> {id: record}; kind "pending" ->
# the parked ask awaiting its data ({} when none).

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, \
    apply_bounds as _apply_bounds, \
    normalize_conninfo as _normalize_conninfo, pooled_connect as _pooled_connect
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

ARTIFACT_STORE_FILE = os.getenv(
    "OG_ARTIFACTS_STORE", "artifacts_store.json")
_store_lock = threading.RLock()


def _db_connect():
    conn = _pooled_connect(
        _normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_artifacts_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(ARTIFACT_STORE_FILE):
        try:
            with open(ARTIFACT_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Artifacts store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(ARTIFACT_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Artifacts store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_artifacts_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"Artifacts DB load failed: {e}")
            return default
    with _store_lock:
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
                        "INSERT INTO og_artifacts_data "
                        "(uid, kind, data) VALUES (%s, %s, %s) "
                        "ON CONFLICT (uid, kind) DO UPDATE "
                        "SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Artifacts DB save failed: {e}")
            return
    with _store_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


def _delete_blobs(uid: str):
    if not uid:
        return
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM og_artifacts_data "
                        "WHERE uid=%s", (uid,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Artifacts DB purge failed: {e}")
            return
    with _store_lock:
        store = _load_file_store()
        store.pop(uid, None)
        _save_file_store(store)


# --- Records ---------------------------------------------------------------------


def _records(uid: str) -> Dict:
    got = _blob(uid, "artifacts", {})
    if not isinstance(got, dict):
        return {}
    return {k: v for k, v in got.items() if isinstance(v, dict)}


def _put_record(uid: str, rec: Dict):
    mine = _records(uid)
    mine[rec["id"]] = rec
    _put_blob(uid, "artifacts", mine)


def _get_record(uid: str, artifact_id: str) -> Optional[Dict]:
    if not uid or not artifact_id:
        return None
    rec = _records(uid).get(artifact_id)
    if rec is None or rec.get("owner") != uid:
        return None
    return rec


def list_artifacts_for_owner(uid: str) -> List[Dict]:
    """The Library seam (og_library._artifact_items):
    the owner's own records, newest first. A stranger's
    records are never in the list — the store is keyed
    by owner and every record re-checks its owner."""
    if not uid:
        return []
    out = [r for r in _records(uid).values()
           if r.get("owner") == uid]
    out.sort(key=lambda r: float(r.get("created", 0) or 0),
             reverse=True)
    return out


def usage_bytes(uid: str) -> int:
    """This user's artifact bytes — counted against the
    tier storage quota like locker files (og_library's
    storage line and this module's claim check both use
    the combined figure)."""
    if not uid:
        return 0
    return sum(int(r.get("size", 0) or 0)
               for r in _records(uid).values()
               if r.get("owner") == uid)


def purge_owner(uid: str):
    """Account deletion (the og_store_ready cascade):
    every artifact record and any parked ask die with
    the account."""
    _delete_blobs(uid)


# --- Pending (the makers' discipline: an ask with no data waits one reply) --------


def _set_pending(uid: str, state: Dict):
    state = dict(state or {})
    state["created"] = time.time()
    _put_blob(uid, "pending", state)


def _get_pending(uid: str) -> Optional[Dict]:
    got = _blob(uid, "pending", {})
    if not isinstance(got, dict) or not got:
        return None
    if time.time() - float(got.get("created", 0) or 0) \
            > PENDING_TTL_S:
        _put_blob(uid, "pending", {})
        return None
    return got


def _clear_pending(uid: str):
    _put_blob(uid, "pending", {})


# --- Ask parsing -------------------------------------------------------------------
#
# The parser is deliberately conservative: it reads ONLY
# what the user put in the message. A figure is a label
# next to a number; a section head is a named group
# ("income:", "expenses:"); a table is rows that split
# the same way; a document's content is the user's own
# text. Anything the parser cannot ground in the message
# is not built — the claim asks for the data instead.

_BUILD_VERB = re.compile(
    r"\b(make|build|create|turn|write|put together)\b", re.I)
_KIND_NOUN = re.compile(
    r"\b(page|tracker|dashboard|document|doc|letter|"
    r"spreadsheet|table|sheet)\b", re.I)
_UPDATE_VERB = re.compile(
    r"\b(update|change|edit|revise|fix|correct|adjust)\b",
    re.I)
_AMOUNT = r"(-?\d[\d,]*\.?\d*)"
_FIGURE = re.compile(
    r"^\s*([^:=]{1,48}?)\s*[:=]?\s*\$?\s*" + _AMOUNT +
    r"\s*(?:dollars|usd|bucks)?\s*$", re.I)
_SECTION_HEAD = re.compile(
    r"^\s*(income|pay|money in|expenses?|bills?|costs?|"
    r"spending|outgoings?|money out)\s*[:.]?\s*$", re.I)
_SECTION_PREFIX = re.compile(
    r"^\s*(income|pay|money in|expenses?|bills?|costs?|"
    r"spending|outgoings?|money out)\s*[:.]\s+(.+)$",
    re.I)
_DECLINE = re.compile(
    r"^\s*(no|nope|nah|never ?mind|forget it|skip it|"
    r"cancel|stop)\W*$", re.I)

_KIND_OF_NOUN = {"page": "page", "tracker": "page",
                 "dashboard": "page", "document": "document",
                 "doc": "document", "letter": "document",
                 "spreadsheet": "table", "table": "table",
                 "sheet": "table"}
_KIND_LABEL = {"page": "Page", "document": "Document",
               "table": "Table"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _clean_label(raw: str) -> str:
    label = _norm(raw)
    label = re.sub(
        r"^(and|my|the|a|an|for|is|of|on|per month|monthly)"
        r"[\s:=-]+", "", label, flags=re.I)
    label = re.sub(r"[\s:=-]+$", "", label)
    label = re.sub(
        r"\s+(is|was|costs?|comes? to)$", "", label, flags=re.I)
    if label:
        label = label[0].upper() + label[1:]
    return label[:48]


def _num(raw: str) -> Optional[float]:
    try:
        return round(float(str(raw).replace(",", "")), 2)
    except (TypeError, ValueError):
        return None


def _split_segments(payload: str) -> List[str]:
    # Protect thousands separators before splitting on
    # commas ("1,200" is one figure, not two segments).
    text = str(payload or "")
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"(\d),(\d{3}\b)", r"\1\2", text)
    parts = re.split(r"[;\n]+", text)
    segs: List[str] = []
    for part in parts:
        for piece in part.split(","):
            piece = piece.strip(" .")
            if piece:
                segs.append(piece)
    return segs


def _parse_figures(payload: str):
    """(sections, notes) from the ask's own figures.
    sections: [{heading, rows: [{label, value, money}]}].
    A segment that is a bare section head switches the
    current section; a segment with no figure becomes a
    note line (the user's own words, kept as-is)."""
    sections: List[Dict] = []
    cur = {"heading": "Details", "rows": []}
    sections.append(cur)
    notes: List[str] = []
    for seg in _split_segments(payload):
        head = _SECTION_HEAD.match(seg)
        prefix = None if head else _SECTION_PREFIX.match(seg)
        if head or prefix:
            name = (head or prefix).group(1).lower()
            heading = ("Income" if name in (
                "income", "pay", "money in") else "Expenses")
            cur = {"heading": heading, "rows": []}
            sections.append(cur)
            if head:
                continue
            seg = prefix.group(2)
        m = _FIGURE.match(seg)
        if m and _clean_label(m.group(1)):
            value = _num(m.group(2))
            if value is not None:
                cur["rows"].append({
                    "label": _clean_label(m.group(1)),
                    "value": value,
                    "money": "$" in seg})
                continue
        if seg and len(seg) <= 200:
            notes.append(seg)
    sections = [s for s in sections if s["rows"]]
    total = sum(len(s["rows"]) for s in sections)
    return sections[:20], notes[:20], total


def _parse_table(payload: str):
    """(columns, rows) from the ask's own rows, or None
    when the payload has no row structure — then the
    claim asks for rows instead of inventing them."""
    raw_parts = [p.strip() for p in
                 re.split(r"[;\n]+", str(payload or "")) if
                 p.strip()]
    grids: List[List[str]] = []
    for part in raw_parts:
        if "|" in part:
            grids.append([c.strip() for c in
                          part.split("|")])
        elif "," in part:
            grids.append([c.strip() for c in
                          part.split(",")])
    if len(grids) >= 2:
        width = len(grids[0])
        if width >= 2 and all(len(g) == width
                              for g in grids):
            head = grids[0]
            if not any(_num(c) is not None and
                       re.search(r"\d", c) for c in head):
                return ([_clean_label(c) or c for c in head],
                        [g[:width] for g in grids[1:]])
    # Label: value pairs -> a two-column table.
    sections, _notes, count = _parse_figures(payload)
    if count:
        rows = [[r["label"],
                 _fmt_value(r["value"], r["money"])]
                for s in sections for r in s["rows"]]
        return ["Item", "Amount"], rows
    return None


def _parse_paragraphs(payload: str) -> List[str]:
    text = str(payload or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in
             re.split(r"\n\s*\n|\n", text) if p.strip()]
    return paras[:MAX_PARAGRAPHS]


def _payload_after_ask(raw: str, match_end: int) -> str:
    """The data part of the ask: everything after the
    kind noun, minus the politeness in between."""
    rest = raw[match_end:].strip()
    rest = re.sub(r"^[\s:,-]+", "", rest)
    rest = re.sub(
        r"^(for me|please|that|which)\b[\s:,-]*", "",
        rest, flags=re.I)
    return rest[:MAX_ASK_CHARS]


def _title_for(kind: str, raw: str, payload: str,
               sections) -> str:
    m = re.search(r"(?:called|titled|named)\s+[\"']([^\"']{1,60})[\"']",
                  raw, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(
        r"\b(?:tracker|page|document|doc|table|spreadsheet)"
        r"\s+(?:for|about|on)\s+([^:;,]{2,48})", raw, re.I)
    topic = ""
    if m:
        topic = _clean_label(m.group(1))
    low = raw.lower()
    labels = " ".join(
        r["label"].lower() for s in (sections or [])
        for r in s["rows"])
    budgety = ("budget" in low or "rent" in low or
               "paycheck" in low or "rent" in labels or
               "paycheck" in labels)
    if topic:
        topic = " ".join(
            w if w.lower() in ("for", "of", "the", "my")
            else w.capitalize()
            for w in topic.split())
        if "tracker" in low and kind == "page" and \
                not topic.lower().endswith("tracker"):
            return f"{topic} Tracker"
        return f"{topic} {_KIND_LABEL[kind]}" \
            if kind != "document" else topic
    if budgety:
        return f"Budget {_KIND_LABEL[kind]}"
    return f"My {_KIND_LABEL[kind]}"


def _draft_spec(kind: str, title: str, payload: str):
    """(spec, ok) — the artifact's content, parsed from
    the ask alone. ok=False means the ask carried no
    groundable data for this kind."""
    if kind == "document":
        paras = _parse_paragraphs(payload)
        words = sum(len(p.split()) for p in paras)
        if words < 3:
            return None, False
        return {"kind": kind, "title": title,
                "paragraphs": paras}, True
    if kind == "table":
        got = _parse_table(payload)
        if not got:
            return None, False
        columns, rows = got
        if len(rows) > MAX_ROWS:
            rows = rows[:MAX_ROWS]
        return {"kind": kind, "title": title,
                "columns": columns, "rows": rows}, True
    sections, notes, count = _parse_figures(payload)
    if not count:
        return None, False
    return {"kind": kind, "title": title,
            "sections": sections, "notes": notes}, True


# --- Update parsing -------------------------------------------------------------

_ADD = re.compile(
    r"^\s*(?:add|new|include)\s+(.+?)\s*[:=]?\s*\$?\s*" +
    _AMOUNT + r"\s*(?:dollars|usd|bucks)?\s*$", re.I)
_REMOVE = re.compile(
    r"^\s*(?:remove|delete|drop|take off|cut)\s+(.+?)\s*$",
    re.I)
_DELTA = re.compile(
    r"^\s*(.+?)\s+(?:went up|goes up|up|increased?|rose|"
    r"went down|goes down|down|decreased?|dropped|fell)"
    r"\s+(?:by\s+)?\$?\s*" + _AMOUNT +
    r"\s*(?:dollars|usd|bucks)?\s*$", re.I)
_SET = re.compile(
    r"^\s*(.+?)\s+(?:is now|is|to|now|=|:)\s*\$?\s*" +
    _AMOUNT + r"\s*(?:dollars|usd|bucks)?\s*$", re.I)


def _parse_ops(kind: str, payload: str) -> List[Dict]:
    """Change ops from the update ask's own words:
    [{op: set|delta|add|remove|append, label, value}]."""
    ops: List[Dict] = []
    if kind == "document":
        text = _norm(payload)
        if text:
            ops.append({"op": "append", "text": text})
        return ops
    for seg in _split_segments(payload):
        m = _ADD.match(seg)
        if m and _clean_label(m.group(1)):
            ops.append({"op": "add",
                        "label": _clean_label(m.group(1)),
                        "value": _num(m.group(2)),
                        "money": "$" in seg})
            continue
        m = _REMOVE.match(seg)
        if m and _clean_label(m.group(1)):
            ops.append({"op": "remove",
                        "label": _clean_label(m.group(1))})
            continue
        m = _DELTA.match(seg)
        if m and _clean_label(m.group(1)):
            down = bool(re.search(
                r"\b(down|decreas|drop|fell)\b", seg, re.I))
            val = _num(m.group(2))
            ops.append({"op": "delta",
                        "label": _clean_label(m.group(1)),
                        "value": -(val or 0) if down
                        else (val or 0),
                        "money": "$" in seg})
            continue
        m = _SET.match(seg)
        if m and _clean_label(m.group(1)):
            ops.append({"op": "set",
                        "label": _clean_label(m.group(1)),
                        "value": _num(m.group(2)),
                        "money": "$" in seg})
            continue
    return [o for o in ops
            if o.get("value") is not None or
            o["op"] in ("remove", "append")]


def _find_row(spec: Dict, label: str):
    """(section, index) of the row the user means —
    normalized label match, containment either way."""
    want = _norm(label).lower()

    def hit(text: str) -> bool:
        got = _norm(text).lower()
        return got == want or want in got or got in want

    if spec.get("kind") == "table":
        for i, row in enumerate(spec.get("rows") or []):
            if row and hit(str(row[0])):
                return None, i
        return None, -1
    for s_i, section in enumerate(spec.get("sections")
                                   or []):
        for r_i, row in enumerate(section.get("rows")
                                   or []):
            if hit(str(row.get("label", ""))):
                return s_i, r_i
    return None, -1


def _cell_value(cell) -> Optional[float]:
    return _num(re.sub(r"[^0-9.,-]", "", str(cell or "")))


def _apply_ops(spec: Dict, ops: List[Dict]) -> List[str]:
    """Apply the ops to the spec in place; returns the
    human change lines (label: old -> new) for the reply
    and the notification. Only rows the user named are
    touched; a named row that does not exist is reported,
    never invented — except an explicit "add"."""
    changes: List[str] = []
    kind = spec.get("kind")
    for op in ops:
        if op["op"] == "append":
            spec.setdefault("paragraphs", []).append(
                op["text"])
            changes.append("added a paragraph")
            continue
        label = op["label"]
        s_i, r_i = _find_row(spec, label)
        if kind == "table":
            rows = spec.setdefault("rows", [])
            if op["op"] == "add":
                rows.append([label, _fmt_value(
                    op["value"], op.get("money"))])
                changes.append(
                    f"added {label}: "
                    f"{_fmt_value(op['value'], op.get('money'))}")
            elif r_i < 0:
                changes.append(f"no row called {label}")
            elif op["op"] == "remove":
                rows.pop(r_i)
                changes.append(f"removed {label}")
            else:
                row = rows[r_i]
                old = _cell_value(row[1]) if len(row) > 1 \
                    else None
                new = op["value"] if op["op"] == "set" \
                    else round((old or 0) + op["value"], 2)
                if len(row) > 1:
                    row[1] = _fmt_value(
                        new, op.get("money"))
                changes.append(
                    f"{label}: {_fmt_value(old, op.get('money'))}"
                    f" -> {_fmt_value(new, op.get('money'))}")
            continue
        sections = spec.setdefault("sections", [])
        if not sections:
            sections.append({"heading": "Details",
                             "rows": []})
        if op["op"] == "add":
            sections[0]["rows"].append({
                "label": label, "value": op["value"],
                "money": bool(op.get("money"))})
            changes.append(
                f"added {label}: "
                f"{_fmt_value(op['value'], op.get('money'))}")
        elif r_i < 0:
            changes.append(f"no line called {label}")
        elif op["op"] == "remove":
            sections[s_i]["rows"].pop(r_i)
            changes.append(f"removed {label}")
        else:
            row = sections[s_i]["rows"][r_i]
            old = row.get("value")
            new = op["value"] if op["op"] == "set" \
                else round((old or 0) + op["value"], 2)
            row["value"] = new
            money = row.get("money") or op.get("money")
            changes.append(
                f"{label}: {_fmt_value(old, money)} -> "
                f"{_fmt_value(new, money)}")
    return changes


def _finalize_spec(spec: Dict) -> Dict:
    """The worker's arithmetic — section totals and the
    what-if pick, computed from the user's own figures
    and nothing else."""
    spec = json.loads(json.dumps(spec))
    if spec.get("kind") == "page":
        for section in spec.get("sections") or []:
            rows = section.get("rows") or []
            section["total"] = round(
                sum(float(r.get("value") or 0)
                    for r in rows), 2) if rows else None
        spec["whatif"] = None
        for section in spec.get("sections") or []:
            rows = section.get("rows") or []
            if section.get("total") is not None \
                    and len(rows) >= 2:
                big = max(rows, key=lambda r: abs(
                    float(r.get("value") or 0)))
                val = abs(float(big.get("value") or 0))
                if val > 0:
                    spec["whatif"] = {
                        "section": section.get("heading"),
                        "label": big.get("label"),
                        "min": 0,
                        "max": int(math.ceil(val * 2)),
                        "step": 1 if val >= 100 else 0.5,
                    }
                break
    return spec


def _spec_size(spec: Dict) -> int:
    return len(json.dumps(spec, ensure_ascii=False,
                          separators=(",", ":")).encode(
                              "utf-8"))


def _fmt_value(value, money) -> str:
    if value is None:
        return "—"
    v = float(value)
    if v == int(v):
        body = f"{int(v):,}"
    else:
        body = f"{v:,.2f}"
    return f"${body}" if money else body


# --- Quota ------------------------------------------------------------------------


def _combined_usage(uid: str) -> int:
    """Locker bytes + artifact bytes — the one figure
    the quota binds (og_library's storage line reports
    the same sum)."""
    locker = 0
    try:
        import og_storage
        locker = og_storage.usage_bytes(uid)
    except Exception:
        locker = 0
    return locker + usage_bytes(uid)


def _quota_bytes(tier: str) -> int:
    try:
        import og_storage
        return og_storage.quota_bytes(tier)
    except Exception:
        return 0


# --- The build worker (background thread — Main Thread rule) -----------------------


def _notify(uid: str, rec: Dict, updated: bool,
            changes: List[str]):
    try:
        import og_notify as _notify
        title = rec.get("title", "Artifact")
        if updated:
            body = (f"\"{title}\" was updated in place"
                    + (": " + "; ".join(changes[:3])
                       if changes else "")
                    + ". It's in your Library.")
        else:
            body = (f"\"{title}\" is ready — it's in "
                    f"your Library.")
        _notify.record(
            uid, "artifact_done",
            f"Your {_KIND_LABEL.get(rec.get('kind'), 'artifact')}"
            f" is ready: \"{title}\"",
            body,
            target={"view": "library",
                    "kind": rec.get("kind", ""),
                    "id": rec.get("id", "")})
    except Exception:
        pass


def _run_build(job_id: str, uid: str, ops=None,
               updated: bool = False):
    """Compose/finalize the record OFF the request path.
    The content was parsed from the user's own ask at
    claim time; the worker settles the arithmetic, the
    size and the status, then announces readiness."""
    rec = _get_record(uid, job_id)
    if rec is None:
        return
    try:
        spec = rec.get("spec") or {}
        changes: List[str] = []
        if ops:
            changes = _apply_ops(spec, ops)
        spec = _finalize_spec(spec)
        rec["spec"] = spec
        rec["size"] = _spec_size(spec)
        rec["status"] = "ready"
        rec["detail"] = (
            f"{_KIND_LABEL.get(rec.get('kind'), 'Artifact')}"
            f" · revision {rec.get('revision', 1)}")
        rec["updated"] = time.time()
        _put_record(uid, rec)
        _notify(uid, rec, updated, changes)
    except Exception as e:
        logger.warning(f"Artifact build {job_id} failed: {e}")
        try:
            rec["status"] = "failed"
            rec["detail"] = "The build didn't finish."
            rec["updated"] = time.time()
            _put_record(uid, rec)
        except Exception:
            pass


def _start_build(uid: str, rec: Dict, ops=None,
                 updated: bool = False):
    _put_record(uid, rec)
    thread = threading.Thread(
        target=_run_build, args=(rec["id"], uid),
        kwargs={"ops": ops, "updated": updated},
        daemon=True, name=f"og-artifact-{rec['id']}")
    thread.start()


# --- Claim parsing (the chat seam) ---------------------------------------------------


def _result(tag: str, title: str, body: str,
            href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body,
             "href": href}]


def _working_count(uid: str) -> int:
    return sum(1 for r in _records(uid).values()
               if r.get("owner") == uid
               and r.get("status") == "working")


def _target_artifact(uid: str, raw: str,
                     kind_hint: Optional[str]):
    """The artifact an update ask means: exact title
    match, then title-word match, then the only artifact
    of the named kind. Ambiguity returns (None, list) —
    the reply asks which one instead of guessing."""
    mine = list_artifacts_for_owner(uid)
    if not mine:
        return None, []
    low = raw.lower()
    exact = [r for r in mine
             if r.get("title", "").lower() in low]
    if exact:
        return exact[0], []
    scored = []
    for rec in mine:
        words = [w for w in re.split(
            r"[^a-z0-9]+", rec.get("title", "").lower())
            if len(w) > 2]
        hits = sum(1 for w in words if w in low)
        if kind_hint and rec.get("kind") != kind_hint:
            hits -= 1
        if hits > 0:
            scored.append((hits, rec))
    if scored:
        scored.sort(key=lambda t: -t[0])
        if len(scored) == 1 or \
                scored[0][0] > scored[1][0]:
            return scored[0][1], []
        return None, [r for _h, r in scored[:4]]
    if kind_hint:
        of_kind = [r for r in mine
                   if r.get("kind") == kind_hint]
        if len(of_kind) == 1:
            return of_kind[0], []
        if len(of_kind) > 1:
            return None, of_kind[:4]
    if len(mine) == 1:
        return mine[0], []
    return None, mine[:4]


def _claim_artifact(message: str, uid: str) -> Optional[Dict]:
    """Parse one chat message into an artifact job
    ({kind: build|update|need_data|pending_data|...}) or
    None — not ours. The job is resolved by
    artifact_results() on the search seam."""
    if not artifacts_enabled() or not uid:
        return None
    raw = str(message or "")
    low = raw.lower()
    noun = _KIND_NOUN.search(low)
    kind_hint = _KIND_OF_NOUN[noun.group(1).lower()] \
        if noun else None

    # An update ask about an existing artifact.
    if _UPDATE_VERB.search(low) or \
            re.search(r"\b(add|remove)\b.+\b(to|from)\b.*"
                      r"\b(page|document|table|tracker)\b",
                      low):
        target, ambiguous = _target_artifact(
            uid, raw, kind_hint)
        if target is not None:
            # The change clause rides after a separator
            # ("page: rent 2950", "page — rent went up
            # 50"), after the update verb, or is the
            # whole message — try each until one parses.
            candidates = []
            for sep in (":", ";", "—", " - "):
                if sep in raw:
                    candidates.append(
                        raw.rsplit(sep, 1)[-1])
            verb_parts = re.split(
                r"\b(?:update|change|edit|revise|fix|"
                r"correct|adjust)\b", raw, flags=re.I)
            if len(verb_parts) > 1:
                candidates.append(verb_parts[-1])
            candidates.append(raw)
            ops: List[Dict] = []
            for cand in candidates:
                ops = _parse_ops(target.get("kind"), cand)
                if ops:
                    break
            usable = [o for o in ops if o["op"] != "append"
                      ] or ops
            if usable:
                return {"kind": "update", "target": target,
                        "ops": usable}
            return {"kind": "update_noop", "target": target}
        if ambiguous:
            return {"kind": "which_one",
                    "candidates": ambiguous}

    # A fresh build ask: build verb + kind noun.
    verb = _BUILD_VERB.search(low)
    if verb and noun and noun.start() > verb.start() - 40:
        kind = kind_hint
        payload = _payload_after_ask(raw, noun.end())
        # "turn this into a document: <text>" — the data
        # rides after the colon, not after the noun.
        m = re.search(r":\s*(.+)$", raw, re.S)
        if m and len(m.group(1)) > len(payload):
            payload = m.group(1)[:MAX_ASK_CHARS]
        title = _title_for(kind, raw, payload, None)
        spec, ok = _draft_spec(kind, title, payload)
        if ok:
            return {"kind": "build", "spec": spec,
                    "title": title}
        pending = _get_pending(uid)
        # fall through to pending with this new kind
        return {"kind": "need_data", "want_kind": kind,
                "title": title}

    # A parked ask awaiting its data: this message may
    # BE the data.
    pending = _get_pending(uid)
    if pending:
        if _DECLINE.match(raw.strip()):
            _clear_pending(uid)
            return {"kind": "declined"}
        kind = pending.get("want_kind") or "page"
        title = pending.get("title") or \
            f"My {_KIND_LABEL.get(kind, 'Page')}"
        spec, ok = _draft_spec(kind, title, raw)
        if ok:
            return {"kind": "build", "spec": spec,
                    "title": title}
        return None
    return None


def artifact_results(job: Dict, message: str,
                     uid: str) -> Optional[List[Dict]]:
    """Resolve a claimed job into grounded result blocks
    the persona voices (the makers' seam)."""
    if not job or not uid:
        return None
    kind = job.get("kind")
    tier = _tier_now(uid)

    if kind == "declined":
        return _result(
            "ARTIFACT: SCRAPPED", "🧩 Artifacts — scrapped",
            "The visitor dropped the artifact they were "
            "about to build. Tell them, in persona, in ONE "
            "line: scrapped, nothing built, nothing stored.")

    if kind == "need_data":
        want = job.get("want_kind") or "page"
        _set_pending(uid, {"stage": "await_data",
                           "want_kind": want,
                           "title": job.get("title")})
        ask = {
            "page": "what numbers go on it? Give me the "
                    "lines — like: rent 2900, paycheck "
                    "2163, gas 200.",
            "document": "what goes in it? Say it or paste "
                        "it and I'll lay it out as-is.",
            "table": "what are the rows? Give them like: "
                     "item, cost; apples, 3; nails, 12.",
        }[want]
        return _result(
            "ARTIFACT: NEED-DATA", "🧩 Artifacts — needs data",
            f"The visitor wants a {want} but gave no data "
            f"to build it from, and you NEVER invent their "
            f"numbers. Ask them, in persona, in ONE line: "
            f"{ask} Whatever they answer next is the data "
            f"— build from exactly that.")

    if kind == "which_one":
        names = ", ".join(
            f"\"{r.get('title')}\""
            for r in job.get("candidates") or [])
        return _result(
            "ARTIFACT: WHICH-ONE", "🧩 Artifacts — which one",
            f"The visitor has more than one artifact this "
            f"could mean ({names}) and you don't guess at "
            f"their files. Ask them, in persona, in ONE "
            f"line: which one — name it.")

    if kind == "update_noop":
        title = (job.get("target") or {}).get("title", "")
        return _result(
            "ARTIFACT: NO-CHANGE", "🧩 Artifacts — no change read",
            f"The visitor wants \"{title}\" updated but "
            f"didn't name a change you can apply. Tell "
            f"them, in persona, in ONE line: name the "
            f"change — like \"rent is now 2950\" or \"add "
            f"gas 200\".")

    if kind == "build":
        spec = job["spec"]
        title = job["title"]
        mine = _records(uid)
        if len(mine) >= MAX_ARTIFACTS:
            return _result(
                "ARTIFACT: FULL", "🧩 Artifacts — shelf full",
                f"The visitor already has {MAX_ARTIFACTS} "
                f"artifacts — the shelf is capped so it "
                f"never turns into a junk drawer. Tell "
                f"them, in persona, in ONE line: the shelf "
                f"is full; delete one from the Library "
                f"first, then ask again.")
        if _working_count(uid) >= MAX_WORKING:
            return _result(
                "ARTIFACT: BUSY", "🧩 Artifacts — building",
                "Artifacts are ALREADY building for this "
                "visitor. Tell them, in persona, in ONE "
                "line: one at a time — the building ones "
                "land in their Library, then ask again.")
        size = _spec_size(_finalize_spec(spec))
        quota = _quota_bytes(tier)
        if quota <= 0 or _combined_usage(uid) + size > quota:
            return _result(
                "ARTIFACT: QUOTA", "🧩 Artifacts — no room",
                "The visitor's plan storage is full (or "
                "their plan has no storage), so this "
                "artifact can't be stored. Tell them "
                "plainly, in persona, in ONE line: no room "
                "on their plan for it — clearing locker "
                "space or a bigger plan makes room. Build "
                "nothing.")
        _clear_pending(uid)
        now = time.time()
        rec = {
            "id": "art-" + uuid.uuid4().hex[:12],
            "owner": uid,
            "kind": spec.get("kind"),
            "title": title,
            "status": "working",
            "detail": "Building…",
            "created": now, "updated": now,
            "revision": 1,
            "size": size,
            "spec": spec,
            "source_ask": _norm(message)[:300],
        }
        _start_build(uid, rec)
        return _result(
            "ARTIFACT: BUILDING", "🧩 Artifacts — building",
            f"The visitor asked for a {spec.get('kind')} "
            f"and it's BUILDING now, in the background. "
            f"Tell them, in persona, in ONE line: you're "
            f"building \"{title}\" — it lands in their "
            f"Library when it's ready and you'll say so "
            f"here. Do NOT read the numbers back or claim "
            f"it's already done.")

    if kind == "update":
        target = job["target"]
        ops = job["ops"]
        trial = json.loads(json.dumps(
            target.get("spec") or {}))
        trial_changes = _apply_ops(trial, ops)
        applied = [c for c in trial_changes
                   if not c.startswith("no ")]
        if not applied:
            # The ask named rows the artifact doesn't
            # have: nothing changes, no revision burns,
            # and the reply says what IS on it instead.
            return _result(
                "ARTIFACT: NO-CHANGE",
                "🧩 Artifacts — no change read",
                f"The visitor asked to change "
                f"\"{target.get('title')}\" but named "
                f"lines that aren't on it ({'; '.join(trial_changes[:2])}). "
                f"Tell them, in persona, in ONE line: "
                f"those lines aren't on it — name a line "
                f"that's there, or say \"add\" to add it.")
        delta = _spec_size(_finalize_spec(trial)) - \
            int(target.get("size", 0) or 0)
        quota = _quota_bytes(tier)
        if delta > 0 and quota <= 0 or \
                (delta > 0 and
                 _combined_usage(uid) + delta > quota):
            return _result(
                "ARTIFACT: QUOTA", "🧩 Artifacts — no room",
                "That update would grow the artifact past "
                "the visitor's plan storage. Tell them "
                "plainly, in persona, in ONE line: no room "
                "for that change on their plan. Change "
                "nothing.")
        rec = dict(target)
        rec["status"] = "working"
        rec["detail"] = "Updating…"
        rec["revision"] = int(rec.get("revision", 1)) + 1
        rec["updated"] = time.time()
        _start_build(uid, rec, ops=ops, updated=True)
        return _result(
            "ARTIFACT: UPDATING", "🧩 Artifacts — updating",
            f"The visitor's \"{target.get('title')}\" is "
            f"being revised IN PLACE — same artifact, no "
            f"duplicate. Tell them, in persona, in ONE "
            f"line: you're updating it now, and the "
            f"revised version lands in their Library when "
            f"it's ready.")
    return None


# --- Rendering (the artifact's own document — the /fin pattern) ---------------------


_PAGE_CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;font-family:-apple-system,'Segoe UI',Roboto,
Helvetica,Arial,sans-serif;background:#0c0c0e;color:#f2eede;
padding:20px 16px 48px}
html[data-og-rain-mode="day"] body{background:#fdfbf4;
color:#221f16}
a{color:#d8a92c}
.oga-head{max-width:720px;margin:0 auto 8px}
.oga-back{text-decoration:none;font-size:14px}
.oga-title{font-size:26px;margin:10px 0 2px;line-height:1.2}
.oga-sub{opacity:.72;font-size:13px;margin:0 0 18px}
.oga-main{max-width:720px;margin:0 auto}
.oga-card{background:#151517;border:1px solid #2a2a2e;
border-radius:14px;padding:16px;margin:0 0 14px}
html[data-og-rain-mode="day"] .oga-card{background:#fffdf4;
border-color:#ddd6bd}
.oga-card h3{margin:0 0 10px;font-size:16px;color:#d8a92c}
html[data-og-rain-mode="day"] .oga-card h3{color:#8a6d1f}
.oga-row{display:flex;justify-content:space-between;gap:12px;
padding:6px 0;border-bottom:1px solid #26262a;font-size:15px}
html[data-og-rain-mode="day"] .oga-row{border-color:#e7e0c8}
.oga-row:last-child{border-bottom:none}
.oga-row .v{font-variant-numeric:tabular-nums;white-space:nowrap}
.oga-total{display:flex;justify-content:space-between;
padding:9px 0 2px;font-weight:700;font-size:15px}
.oga-note{font-size:14px;opacity:.85;padding:3px 0}
.oga-doc p{font-size:16px;line-height:1.55;margin:0 0 12px;
white-space:pre-wrap}
table.oga-table{width:100%;border-collapse:collapse;
font-size:14px}
.oga-table th,.oga-table td{text-align:left;padding:7px 8px;
border-bottom:1px solid #26262a;vertical-align:top}
html[data-og-rain-mode="day"] .oga-table th,
html[data-og-rain-mode="day"] .oga-table td{
border-color:#e7e0c8}
.oga-table th{color:#d8a92c;font-size:13px;
text-transform:uppercase;letter-spacing:.04em}
html[data-og-rain-mode="day"] .oga-table th{color:#8a6d1f}
.oga-whatif{margin-top:12px;border-top:1px dashed #3a3a40;
padding-top:12px}
html[data-og-rain-mode="day"] .oga-whatif{border-color:#cfc5a0}
.oga-whatif label{font-size:14px;display:block;margin-bottom:8px}
.oga-whatif input[type=range]{width:100%}
.oga-foot{max-width:720px;margin:18px auto 0;font-size:12px;
opacity:.6}
"""


def _esc(text) -> str:
    return html.escape(str(text or ""), quote=True)


def _render_page(spec: Dict) -> str:
    out = []
    whatif = spec.get("whatif") or {}
    for s_i, section in enumerate(spec.get("sections")
                                  or []):
        rows_html = []
        for r_i, row in enumerate(section.get("rows")
                                  or []):
            rows_html.append(
                f'<div class="oga-row"><span>'
                f'{_esc(row.get("label"))}</span>'
                f'<span class="v" data-row="{s_i}:{r_i}">'
                f'{_esc(_fmt_value(row.get("value"), row.get("money")))}'
                f'</span></div>')
        total = section.get("total")
        total_html = ""
        if total is not None:
            money = any(r.get("money")
                        for r in section.get("rows") or [])
            total_html = (
                f'<div class="oga-total"><span>Total</span>'
                f'<span class="v" data-total="{s_i}">'
                f'{_esc(_fmt_value(total, money))}</span></div>')
        wi_html = ""
        if whatif and whatif.get("section") == \
                section.get("heading"):
            wi_html = (
                f'<div class="oga-whatif">'
                f'<label>What if {_esc(whatif.get("label"))} '
                f'changes? <span data-wi-val></span></label>'
                f'<input type="range" min="{whatif.get("min")}" '
                f'max="{whatif.get("max")}" '
                f'step="{whatif.get("step")}" '
                f'data-wi-section="{s_i}" '
                f'data-wi-label="{_esc(whatif.get("label"))}">'
                f'</div>')
        out.append(
            f'<section class="oga-card"><h3>'
            f'{_esc(section.get("heading"))}</h3>'
            + "".join(rows_html) + total_html + wi_html
            + "</section>")
    for note_line in spec.get("notes") or []:
        out.append(
            f'<section class="oga-card"><p class="oga-note">'
            f'{_esc(note_line)}</p></section>')
    return "".join(out)


def _render_document(spec: Dict) -> str:
    paras = "".join(
        f"<p>{_esc(p)}</p>"
        for p in spec.get("paragraphs") or [])
    return (f'<section class="oga-card oga-doc">{paras}'
            f"</section>")


def _render_table(spec: Dict) -> str:
    head = "".join(f"<th>{_esc(c)}</th>"
                   for c in spec.get("columns") or [])
    body = []
    for row in spec.get("rows") or []:
        body.append("<tr>" + "".join(
            f"<td>{_esc(c)}</td>" for c in row) + "</tr>")
    return (f'<section class="oga-card"><table '
            f'class="oga-table"><thead><tr>{head}</tr>'
            f'</thead><tbody>{"".join(body)}</tbody>'
            f"</table></section>")


_WHATIF_JS = """
<script>
(function(){
  var data = window.__OGA_SPEC__ || {};
  function fmt(v, money){
    var s = Math.abs(v).toLocaleString('en-US',
      {minimumFractionDigits: (v % 1 ? 2 : 0),
       maximumFractionDigits: 2});
    return (v < 0 ? '-' : '') + (money ? '$' : '') + s;
  }
  document.querySelectorAll('input[data-wi-section]')
    .forEach(function(inp){
      var sIdx = +inp.getAttribute('data-wi-section');
      var label = inp.getAttribute('data-wi-label');
      var section = (data.sections || [])[sIdx];
      if (!section) return;
      var row = (section.rows || []).find(function(r){
        return r.label === label; });
      if (!row) return;
      inp.value = row.value;
      var valEl = inp.parentElement.querySelector(
        '[data-wi-val]');
      var rowEl = document.querySelector(
        '[data-row="' + sIdx + ':' +
        section.rows.indexOf(row) + '"]');
      var totEl = document.querySelector(
        '[data-total="' + sIdx + '"]');
      var money = !!row.money;
      function paint(){
        var v = parseFloat(inp.value);
        if (valEl) valEl.textContent = fmt(v, money);
        if (rowEl) rowEl.textContent = fmt(v, money);
        if (totEl){
          var t = 0;
          section.rows.forEach(function(r){
            t += (r === row) ? v : (+r.value || 0); });
          var anyMoney = section.rows.some(function(r){
            return r.money; });
          totEl.textContent = fmt(t, anyMoney);
        }
      }
      inp.addEventListener('input', paint);
      paint();
    });
})();
</script>
"""


def render_artifact_html(rec: Dict) -> str:
    """The standalone document for one artifact (its
    own page, body scroll, nothing stacked under it —
    the L4 shape), in the day/night faces of Ticket G."""
    spec = rec.get("spec") or {}
    kind = rec.get("kind") or spec.get("kind") or "page"
    title = rec.get("title") or "Artifact"
    if kind == "document":
        body = _render_document(spec)
        spec_json = "null"
    elif kind == "table":
        body = _render_table(spec)
        spec_json = "null"
    else:
        body = _render_page(spec)
        spec_json = json.dumps(
            {"sections": spec.get("sections") or []})
    when = ""
    try:
        when = datetime.fromtimestamp(
            float(rec.get("created", 0) or 0),
            tz=timezone.utc).strftime("%b %d, %Y")
    except Exception:
        when = ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{_esc(title)} — OG</title>
<script>(function(){{try{{var q=new URLSearchParams(location.search).get('theme');var h=new Date().getHours();var mode=(q==='day'||q==='night')?q:((h>=7&&h<19)?'day':'night');document.documentElement.setAttribute('data-og-rain-mode',mode);}}catch(e){{}}}})();</script>
<script>window.addEventListener('pageshow', function (e) {{ if (e.persisted) {{ location.reload(); }} }});</script>
<style>{_PAGE_CSS}</style>
</head>
<body>
<header class="oga-head">
<a class="oga-back" href="/">&larr; OG</a>
<h1 class="oga-title">{_esc(title)}</h1>
<p class="oga-sub">{_esc(_KIND_LABEL.get(kind, 'Artifact'))} · built by OG from your own data</p>
</header>
<main class="oga-main">
{body}
</main>
<p class="oga-foot">Built by OG{_esc(' on ' + when if when else '')} · revision {int(rec.get('revision', 1) or 1)}. Every number on this page came from you; totals are OG's arithmetic on those numbers. Ask OG in chat to update it — the update revises this same page.</p>
<script>window.__OGA_SPEC__ = {spec_json};</script>
{_WHATIF_JS if spec_json != "null" else ""}
</body>
</html>"""


# --- Identity (the og_library resolution, verbatim in shape) -------------------------


def _request_uid(request: Request) -> str:
    """The caller's ONE identity — the live session's
    account uid when signed in, else the presented
    visitor cookie (og_accounts.session_uid_or)."""
    cookie_uid = request.cookies.get("ogai_uid") or ""
    try:
        import og_accounts as _acct
        return _acct.session_uid_or(request.cookies,
                                    cookie_uid)
    except Exception:
        return cookie_uid


# --- Routes ---------------------------------------------------------------------------


def register_artifact_routes(app):

    @app.get("/artifacts/{artifact_id}")
    def artifact_open(request: Request, artifact_id: str):
        if not artifacts_enabled():
            raise HTTPException(status_code=404,
                                detail="Not found")
        uid = _request_uid(request)
        rec = _get_record(uid, artifact_id) \
            if uid else None
        if rec is None or rec.get("status") != "ready":
            # Owner-checked on every open: a stranger's
            # id is a 404, and so is a still-building one
            # (it opens when the build announces ready).
            raise HTTPException(status_code=404,
                                detail="Not found")
        return HTMLResponse(
            render_artifact_html(rec),
            headers={"Cache-Control": "no-cache"})


# --- The chat seam (install_artifact_tools, the og_songs pattern) ----------------------

_slot: Dict = {"job": None, "message": ""}


def install_artifact_tools(agent_instance, get_uid):
    """Wrap the agent's detect_intent + web_search hooks
    so artifact asks, the need-data follow-up, and update
    asks ride the established seam. Persona files never
    touched."""
    if getattr(agent_instance, "_og_artifacts_installed",
               False):
        return
    prev_detect = getattr(agent_instance, "detect_intent",
                          None)
    prev_search = getattr(agent_instance, "web_search",
                          None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _slot["job"] = None
        _slot["message"] = ""
        try:
            uid = get_uid()
            job = _claim_artifact(str(message), uid) \
                if uid else None
            if job:
                _slot["job"] = job
                _slot["message"] = str(message)
                if isinstance(intent, dict):
                    intent["needs_code_generation"] = False
                    if not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = \
                            str(message).strip()
        except Exception as e:
            logger.warning(f"Artifact trigger check failed: {e}")
            _slot["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _slot.get("job")
        message = _slot.get("message", "")
        _slot["job"] = None
        _slot["message"] = ""
        if job:
            try:
                results = artifact_results(
                    job, message, get_uid())
            except Exception as e:
                logger.warning(f"Artifact job failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_artifacts_installed = True
