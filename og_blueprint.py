"""Round 115: THE BLUEPRINT SPECIALIST HELPER
(Brent's order, 2026-10-12 11:11 PDT: "Blue print
specialist helper" — specced in round-115/SPEC.md.)

WHAT THIS IS. The crew's blueprint specialist — it
builds on the Construction folder's Blueprint reader
(Round 6 file reading, og_file_read) instead of
forking it: the reader shows the sheet; THIS seat
reads it like a tradesperson and does the plan math.
With a blueprint image in chat (the visitor's
attached file) or a filed sheet from the visitor's
locker, the specialist:

  - READS the sheet through the shipped reader (the
    vision read for images, the extracted text for
    PDF/TXT), steered to a word-for-word transcription
    — title block, dimension strings, notes and
    callouts — and caches that read in its own store,
    so follow-up questions read the same words.
  - Parses the read deterministically: the scale as
    printed, every dimension with its label, every
    note with its source tag. Nothing the read doesn't
    carry is ever stated as sheet fact.
  - Does TAKEOFF math from those parsed dimensions —
    areas, perimeters, sheet counts, concrete volume —
    with the work shown and the source named for every
    number.
  - Explains symbols and abbreviations in plain words
    from a standard drafting sheet built into the
    module, and quotes what the sheet itself says when
    asked "what does this call for here?"
  - CROSS-CHECKS two sheets: the same label dimensioned
    differently on two sheets is flagged BY NAME, with
    both values and both sheets cited.
  - Hands a takeoff off through the shipped seams:
    the PM (R109) carries it as a project, the takeoff
    document lands in the locker, and the Filer (R110)
    links it under the project.

THE FENCES.
- Every answer names its source on the sheet (sheet
  id/name + the label, note, or detail the number or
  words came from). Nothing is invented: if the sheet
  doesn't say, the specialist says the sheet doesn't
  say.
- If part of the sheet can't be read — the read marks
  it UNREADABLE, or the reader can't read the sheet at
  all — the answer is "can't read this part", never a
  guessed dimension, never an invented callout.
- Knowledge + reading only. The module imports no
  ordering/trading machinery (suite-pinned) and never
  deletes locker items. It reads the visitor's OWN
  attached file and OWN locker only — every read is
  namespaced by the caller's uid (cross-account
  isolation drilled).
- PDF sheets ride the reader's standing limit: a
  scanned PDF with no text layer can't be read as
  text — the honest path is the export-as-image one
  the reader already names (attach the sheet as an
  image and the vision read takes it).
- NO new loop (R72): nothing polls, nothing ticks;
  state is read on exchanges and on the status route.
- Persona untouched; app.py never touched (install
  rides the og_fixqueue chain AFTER the Car Mechanic
  seat — this seam wraps outermost and defers to the
  PM/Filer/crew/Builder/WoodPro/MaintEng/Mechanic
  claims checked exactly first).

STATE. Own durable store, the Round 18 pattern:
Postgres table og_blueprint_data when OG_MEMORY_DB_URL
is set, else a JSON file. Blob kinds per visitor:
'sheets' (the sheets read, newest last, keep 4) and
'table' (which sheet is on the table). Switch:
OG_BLUEPRINT_ENABLED (unset = ON), the SPEC's name.
"""

import base64
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from typing import Dict, List, Optional

import og_garble

from fastapi import Request

logger = logging.getLogger(__name__)

_TAG = "OG BLUEPRINT SPECIALIST"

CANT_READ = "can't read this part"

READ_FAILED_LINE = (
    "the reader couldn't pull a read off this sheet "
    "just now — the photo read is down or the file "
    "came through empty. Nothing got guessed; slide "
    "the sheet again in a bit and I'll read it then.")

NO_TEXT_PDF_LINE = (
    "that PDF has no readable text layer in it — "
    "it's a scanned picture of a sheet, and the text "
    "reader can't take those. The standing path: "
    "export the sheet as an image (PNG/JPG) and "
    "attach that — the vision read takes images "
    "directly.")

# The specialist's transcription brief, handed to the
# reader's vision read as its question: word-for-word
# where it matters, UNREADABLE where it isn't legible.
TRANSCRIBE_QUESTION = (
    "Transcribe this blueprint / construction drawing "
    "sheet EXACTLY, for a quantity takeoff. Report: "
    "(1) the title block — sheet number, sheet title, "
    "and the scale exactly as printed; (2) every "
    "dimension string exactly as printed, each on its "
    "own line, prefixed by what it measures (a room "
    "name, 'Overall width', 'Ceiling height', and so "
    "on); (3) every note and callout word for word, "
    "each on its own line, prefixed by its note number "
    "or detail reference; (4) any grid references. "
    "For anything printed but NOT clearly legible, "
    "write that line with the word UNREADABLE where "
    "the value would be — never guess a number you "
    "cannot actually read.")

# --- Config -------------------------------------------------------------------

SHEETS_KEEP = 4          # sheets kept per visitor
READ_CAP = 60000         # chars of a read kept per sheet
DAY = 86400.0

BLUEPRINT_STORE_FILE = "blueprint_store.json"
# RLock, not Lock: store updates hold the lock across a
# read-modify-write whose save path re-enters it through
# _put_blob's file branch (the og_mechanic shape).
_blueprint_lock = threading.RLock()

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, StoreError, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None


def _api_key() -> str:
    """The house way (app.py binds the same env lookup
    into every seam that needs the model key)."""
    return os.getenv("OPENAI_API_KEY", "") or ""


# --- Switch ---------------------------------------------------------------------

def blueprint_enabled() -> bool:
    """OG_BLUEPRINT_ENABLED=false is the kill switch.
    Unset = ON."""
    val = os.environ.get("OG_BLUEPRINT_ENABLED")
    if val is not None and val.strip().lower() in (
            "0", "false", "no", "off"):
        return False
    return True


# --- Durable store (Round 18 pattern, mirroring og_mechanic) ----------------------

def _db_connect():
    conn = psycopg.connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_blueprint_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(BLUEPRINT_STORE_FILE):
        try:
            with open(BLUEPRINT_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Blueprint store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(BLUEPRINT_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Blueprint store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_blueprint_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"Blueprint DB load failed: {e}")
            raise StoreError("blueprint store unavailable (Round 99)") from e
    with _blueprint_lock:
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
                        "INSERT INTO og_blueprint_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Blueprint DB save failed: {e}")
            raise StoreError("blueprint store unavailable (Round 99)") from e
    with _blueprint_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


# --- State: the sheets read + the one on the table -------------------------------

def _sheets(uid: str) -> List[Dict]:
    got = _blob(uid, "sheets", [])
    return got if isinstance(got, list) else []


def _save_sheets(uid: str, rows: List[Dict]):
    rows.sort(key=lambda s: float(s.get("added", 0) or 0))
    _put_blob(uid, "sheets", rows[-SHEETS_KEEP:])


def _table_key(uid: str) -> str:
    got = _blob(uid, "table", {})
    if isinstance(got, dict):
        return str(got.get("current", "") or "")
    return ""


def _set_table(uid: str, key: str):
    _put_blob(uid, "table", {"current": key})


def _current_sheet(uid: str) -> Optional[Dict]:
    key = _table_key(uid)
    rows = _sheets(uid)
    if key:
        for s in rows:
            if isinstance(s, dict) and s.get("key") == key:
                return s
    return rows[-1] if rows else None


def _sheet_by_key(uid: str, key: str) -> Optional[Dict]:
    for s in _sheets(uid):
        if isinstance(s, dict) and s.get("key") == key:
            return s
    return None


# --- Sheet sources: the attached file + the caller's own locker ------------------
# The reader (og_file_read) owns the attached file; the
# locker (og_storage) owns filed files. This seat only
# ever resolves sources inside the CALLER's own account.

def _attached_ref(uid: str) -> Optional[Dict]:
    try:
        import og_file_read
        meta = og_file_read.get_meta(uid)
    except Exception:
        meta = None
    if not meta:
        return None
    stamp = (f"a|{meta.get('name', '')}|{meta.get('size', 0)}|"
             f"{meta.get('uploaded_at', 0)}")
    return {"source": "attached", "name": meta.get("name") or "file",
            "kind": meta.get("kind", ""), "stamp": stamp,
            "key": "att:" + stamp}


def _locker_refs(uid: str) -> List[Dict]:
    out = []
    try:
        import og_storage
        for rec in og_storage.list_records(uid):
            kind = rec.get("kind", "") or ""
            if kind not in ("image", "pdf", "text"):
                continue
            stamp = (f"l|{rec.get('id', '')}|"
                     f"{rec.get('uploaded_at', 0)}|{rec.get('size', 0)}")
            out.append({"source": "locker",
                        "name": rec.get("name") or "file",
                        "kind": kind,
                        "item_id": rec.get("id", ""),
                        "content_type": rec.get("content_type", ""),
                        "stamp": stamp, "key": "lck:" + stamp})
    except Exception as e:
        logger.warning(f"Blueprint locker read failed: {e}")
    return [r for r in out if r["item_id"]]


def _find_locker_ref(uid: str, query: str) -> Optional[Dict]:
    """A locker sheet the ask names (name substring,
    own locker only — another account's sheet can
    never resolve here)."""
    q = " ".join(str(query or "").lower().split())
    refs = _locker_refs(uid)
    if not refs:
        return None
    if not q:
        return refs[0]
    hits = [r for r in refs if q in r["name"].lower()]
    if hits:
        exact = [r for r in hits if r["name"].lower() == q]
        return (exact or hits)[0]
    toks = [t for t in re.split(r"[^a-z0-9]+", q) if len(t) >= 3]
    if toks:
        hits = [r for r in refs if toks[0] in r["name"].lower()]
        if len(hits) == 1:
            return hits[0]
    return None


def _read_source_text(uid: str, ref: Dict):
    """(text, how) — the sheet's words through the
    SHIPPED reader's machinery, or (None, why) with why
    in {'vision', 'notext', 'bytes'}."""
    import og_file_read
    if ref["source"] == "attached":
        meta = og_file_read.get_meta(uid)
        if not meta:
            return None, "bytes"
        if meta.get("kind") in ("pdf", "text"):
            text = og_file_read._read_text(uid)
            if not text.strip():
                return None, "notext"
            return text, "file text (the reader's extraction)"
        uri = og_file_read._image_data_uri(uid, meta)
        if not uri:
            return None, "bytes"
        text = og_file_read.vision_read(
            _api_key(), uri, TRANSCRIBE_QUESTION,
            meta.get("name", "sheet"))
        if not text:
            return None, "vision"
        return text, "vision transcription (the reader's read)"
    # locker
    import og_storage
    rec = None
    for r in og_storage.list_records(uid):
        if r.get("id") == ref.get("item_id"):
            rec = r
            break
    if rec is None:
        return None, "bytes"
    data = og_storage.locker_bytes(uid, rec)
    if data is None:
        return None, "bytes"
    if rec.get("kind") == "pdf":
        try:
            text, _pages = og_file_read._extract_pdf_text(data)
        except Exception:
            text = ""
        if not text.strip():
            # A locker copy of a temp PDF can hold
            # extracted text under a .pdf name.
            text = og_file_read._decode_text(data)
        if not text.strip():
            return None, "notext"
        return text, "file text (the reader's extraction)"
    if rec.get("kind") == "text":
        text = og_file_read._decode_text(data)
        if not text.strip():
            return None, "notext"
        return text, "file text (the reader's extraction)"
    uri = (f"data:{rec.get('content_type') or 'image/png'};base64,"
           + base64.b64encode(data).decode())
    text = og_file_read.vision_read(
        _api_key(), uri, TRANSCRIBE_QUESTION,
        rec.get("name", "sheet"))
    if not text:
        return None, "vision"
    return text, "vision transcription (the reader's read)"


# --- The deterministic parse of a read -------------------------------------------
# Everything the seat states as sheet fact comes out of
# this parse and nowhere else: dimensions with labels,
# the scale as printed, notes with source tags, counts
# as printed, and the regions the read itself marked
# unreadable. A line the read marks UNREADABLE yields
# NO dimension — a number next to a blur warning is
# not trusted.

_DIM_RX = re.compile(
    r"(?P<fi>\d+(?:\.\d+)?)\s*'\s*-?\s*"
    r"(?:(?P<inch>\d+(?:\.\d+)?)\s*"
    r"(?:(?P<frac>\d+)\s*/\s*(?P<fden>\d+))?\s*"
    r"(?:\"|in\b|inches\b))?"
    r"|(?P<fracft>\d+)\s*/\s*(?P<fracftden>\d+)\s*'\s*"
    r"|(?P<ftonly>\d+(?:\.\d+)?)\s*(?:ft|feet)\b"
    r"|(?P<fractin>\d+)\s+(?P<fractnum>\d+)\s*/\s*"
    r"(?P<fractden>\d+)\s*(?:\"|in\b|inches\b)"
    r"|(?P<inchonly>\d+(?:\.\d+)?)\s*(?:\"|in\b|inches\b)")

_SHEET_ID_RX = re.compile(r"\b([A-Z]{1,3}-\d{1,3}[A-Z]?)\b")
_DETAIL_RX = re.compile(r"\b(\d{1,2})\s*/\s*([A-Z]{1,3}-\d{1,3}[A-Z]?)\b")
_GRID_RX = re.compile(r"\bgrid\s+([A-Z]{1,2})\s*[-/ ]?\s*(\d{1,2})\b", re.I)
_UNREADABLE_RX = re.compile(
    r"\bunreadable\b|\bnot legible\b|\billegible\b|"
    r"\btoo blurry\b|\bblurred\b", re.I)
_NOTE_START_RX = re.compile(
    r"^\s*(?:note|notes|callout|keynote)\s*#?\s*(\d+)?\s*[:.)-]?\s*",
    re.I)
_NUMBERED_RX = re.compile(r"^\s*(\d{1,2})\s*[.)]\s+\S")
_SCALE_EQ_RX = re.compile(
    r"(\d+(?:\.\d+)?|\d+\s*/\s*\d+)\s*(?:\"|in\.?|inch(?:es)?)\s*"
    r"=\s*1\s*(?:'|ft\.?|foot|feet)\s*(?:-\s*0\s*(?:\"|in\.?)?)?", re.I)
_SCALE_ENG_RX = re.compile(
    r"1\s*(?:\"|in\.?|inch(?:es)?)\s*=\s*(\d+(?:\.\d+)?)\s*"
    r"(?:'|ft\.?|feet|foot)\b", re.I)
_SCALE_ANY_RX = re.compile(r"\bscale\b[^.\n]{0,40}", re.I)

_COUNT_NOUNS = (
    "recessed cans", "can lights", "sprinkler heads",
    "doors", "windows", "lights", "fixtures", "outlets",
    "switches", "diffusers", "toilets", "sinks",
    "cabinets", "drawers", "posts", "columns")
_COUNT_RX = re.compile(
    r"\b(\d{1,3})\s+(" + "|".join(_COUNT_NOUNS) + r")\b", re.I)


def _dim_feet(m: "re.Match") -> Optional[float]:
    g = m.groupdict()
    try:
        if g.get("fi") is not None:
            v = float(g["fi"])
            if g.get("inch") is not None:
                v += float(g["inch"]) / 12.0
                if g.get("frac") and g.get("fden"):
                    v += (float(g["frac"]) / float(g["fden"])) / 12.0
            return v
        if g.get("fracft") is not None:
            return float(g["fracft"]) / float(g["fracftden"])
        if g.get("ftonly") is not None:
            return float(g["ftonly"])
        if g.get("fractin") is not None:
            return (float(g["fractin"])
                    + float(g["fractnum"]) / float(g["fractden"])) / 12.0
        if g.get("inchonly") is not None:
            return float(g["inchonly"]) / 12.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return None


def _frac_val(text: str) -> Optional[float]:
    text = str(text or "").replace(" ", "")
    if "/" in text:
        a, _, b = text.partition("/")
        try:
            return float(a) / float(b)
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_scale(text: str) -> Dict:
    out = {"text": "", "in_per_ft": None, "nts": False}
    if re.search(r"\bNTS\b|\bNOT TO SCALE\b", text, re.I):
        out["nts"] = True
        m = _SCALE_ANY_RX.search(text)
        out["text"] = (m.group(0).strip() if m else "NTS (not to scale)")
        return out
    m = _SCALE_EQ_RX.search(text)
    if m:
        v = _frac_val(m.group(1))
        if v:
            out["in_per_ft"] = v
            out["text"] = m.group(0).strip()
            return out
    m = _SCALE_ENG_RX.search(text)
    if m:
        v = _frac_val(m.group(1))
        if v:
            out["in_per_ft"] = 1.0 / v
            out["text"] = m.group(0).strip()
            return out
    m = _SCALE_ANY_RX.search(text)
    if m:
        out["text"] = m.group(0).strip()
    return out


def _clean_label(raw: str) -> str:
    lab = str(raw or "").strip()
    lab = re.sub(r"^[\s•\-–—*#]+", "", lab)
    lab = re.sub(r"[\s:=\-–—]+$", "", lab)
    lab = re.sub(r"^(?:dimension|dim|size)\s*[:\-]\s*", "", lab, flags=re.I)
    return lab[:60].strip()


def _note_tag(line: str) -> Optional[str]:
    m = _NOTE_START_RX.match(line)
    if m and re.match(r"^\s*(?:note|notes|callout|keynote)\b",
                      line, re.I):
        return f"Note {m.group(1)}" if m.group(1) else "Sheet note"
    m = _NUMBERED_RX.match(line)
    if m:
        return f"Note {m.group(1)}"
    m = _DETAIL_RX.search(line)
    if m:
        return f"Detail {m.group(1)}/{m.group(2)}"
    m = _GRID_RX.search(line)
    if m:
        return f"Grid {m.group(1)}-{m.group(2)}"
    if re.search(r"\bcallout\b", line, re.I):
        return "Callout"
    return None


def _parse_read(text: str) -> Dict:
    """The whole craft layer in one pure function:
    read text -> {sheet_id, title, scale, dims, pairs,
    notes, counts, unreadable}."""
    out = {"sheet_id": "", "title": "", "scale": _parse_scale(text),
           "dims": [], "pairs": [], "notes": [], "counts": [],
           "unreadable": []}
    lines = [ln.rstrip() for ln in str(text or "").splitlines()]
    head = lines[:10]
    for ln in head:
        m = re.search(r"\btitle\s*[:\-]\s*(.+)$", ln, re.I)
        if m and not out["title"]:
            out["title"] = m.group(1).strip()[:80]
    for ln in head + lines:
        m = _SHEET_ID_RX.search(ln)
        if m and re.search(r"[A-Z]", ln):
            out["sheet_id"] = m.group(1)
            break
    if not out["title"]:
        for ln in head:
            m = re.search(r"\bsheet\s*[:\-]?\s*(.+)$", ln, re.I)
            if m and not _SHEET_ID_RX.fullmatch(m.group(1).strip()):
                out["title"] = m.group(1).strip()[:80]
                break
    for idx, ln in enumerate(lines):
        stripped = ln.strip()
        if not stripped:
            continue
        if _UNREADABLE_RX.search(ln):
            lab = _clean_label(ln.split("UNREADABLE")[0]
                                if "UNREADABLE" in ln.upper()
                                else ln)
            out["unreadable"].append(
                {"label": lab or stripped[:60], "line": idx})
            continue
        if re.search(r"\bscale\b", ln, re.I) and (
                "=" in ln or "NTS" in ln.upper()):
            continue  # the scale statement is not a dimension
        tag = _note_tag(ln)
        if tag:
            out["notes"].append(
                {"tag": tag, "text": stripped, "line": idx})
        found = []
        for m in _DIM_RX.finditer(ln):
            feet = _dim_feet(m)
            if feet is None or feet <= 0:
                continue
            found.append((m, feet))
        if found:
            label = _clean_label(ln[:found[0][0].start()])
            between = ""
            if len(found) >= 2:
                between = ln[found[0][0].end():found[1][0].start()]
            if len(found) >= 2 and re.search(
                    r"\bx\b|×|\bby\b", between, re.I):
                a, b = found[0][1], found[1][1]
                out["pairs"].append(
                    {"label": label, "a": a, "b": b,
                     "area": a * b, "raw": stripped, "line": idx})
                for (m, feet) in found[:2]:
                    out["dims"].append(
                        {"label": label, "feet": feet,
                         "raw": m.group(0), "line": idx})
            else:
                for (m, feet) in found:
                    out["dims"].append(
                        {"label": label, "feet": feet,
                         "raw": m.group(0), "line": idx})
        for m in _COUNT_RX.finditer(ln):
            out["counts"].append(
                {"n": int(m.group(1)), "noun": m.group(2).lower(),
                 "tag": tag or "Sheet text", "line": idx})
    return out


# --- Number rendering -------------------------------------------------------------

def _fmt(x: float) -> str:
    if abs(x - round(x)) < 0.05:
        return str(int(round(x)))
    return f"{x:,.1f}".rstrip("0").rstrip(".")


def _ft_str(v: float) -> str:
    """Feet float -> the sheet's own dialect: 12'-6"."""
    if v is None:
        return ""
    neg = v < 0
    v = abs(float(v))
    feet = int(v)
    inches = (v - feet) * 12.0
    whole = int(inches)
    frac = inches - whole
    if frac >= 0.875:
        whole += 1
        frac = 0.0
    elif frac >= 0.375:
        frac = 0.5
    else:
        frac = 0.0
    if whole >= 12:
        feet += 1
        whole = 0
    tail = ""
    if whole:
        tail = f"{whole}"
        if frac:
            tail += " 1/2"
        tail += '"'
    elif frac:
        tail = "1/2\""
    else:
        tail = "0\""
    return ("-" if neg else "") + f"{feet}'-{tail}"


def _sheet_name(sheet: Dict) -> str:
    pid = (sheet.get("parsed") or {}).get("sheet_id", "")
    base = sheet.get("name", "") or "sheet"
    return f"{pid} ({base})" if pid else base


def _cite(sheet: Dict, source: str) -> str:
    return f"(Source: {_sheet_name(sheet)}, {source}.)"


def _result(tag: str, title: str, body: str,
            href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body,
             "href": href}]


# --- Getting a sheet onto the table ------------------------------------------------

def _store_sheet(uid: str, ref: Dict, text: str, how: str) -> Dict:
    parsed = _parse_read(text)
    sheet = {"key": ref["key"], "source": ref["source"],
             "name": ref.get("name", "sheet"),
             "kind": ref.get("kind", ""),
             "item_id": ref.get("item_id", ""),
             "read": str(text)[:READ_CAP], "how": how,
             "parsed": parsed, "added": time.time(),
             "project_id": ""}
    rows = [s for s in _sheets(uid)
            if isinstance(s, dict) and s.get("key") != sheet["key"]]
    rows.append(sheet)
    _save_sheets(uid, rows)
    _set_table(uid, sheet["key"])
    return sheet


def _ensure_sheet(uid: str, claim: Dict):
    """(sheet, err_body) — the sheet for this ask: the
    one already on the table, or a fresh read of the
    attached file / a named locker sheet. err_body is a
    full result body when no sheet can be produced."""
    if "locker" in claim:
        want_locker = str(claim.get("locker") or "")
        ref = _find_locker_ref(uid, want_locker)
        if ref is None:
            return None, ("No sheet by that name in your "
                          "locker — I only read sheets "
                          "from your own locker, and that "
                          "one isn't in it. Name it the "
                          "way it's filed, or attach the "
                          "sheet here.")
        have = _sheet_by_key(uid, ref["key"])
        if have:
            _set_table(uid, ref["key"])
            return have, ""
        text, how = _read_source_text(uid, ref)
        if text is None:
            return None, _read_fail_body(how, ref)
        return _store_sheet(uid, ref, text, how), ""
    ref = _attached_ref(uid)
    if ref is not None:
        have = _sheet_by_key(uid, ref["key"])
        if have:
            _set_table(uid, ref["key"])
            return have, ""
        text, how = _read_source_text(uid, ref)
        if text is None:
            return None, _read_fail_body(how, ref)
        return _store_sheet(uid, ref, text, how), ""
    cur = _current_sheet(uid)
    if cur:
        return cur, ""
    refs = _locker_refs(uid)
    if len(refs) == 1:
        return _ensure_sheet(uid, {"locker": refs[0]["name"]})
    if refs:
        names = ", ".join(f"\"{r['name']}\"" for r in refs[:4])
        return None, (f"No sheet attached right now, and "
                      f"your locker holds more than one: "
                      f"{names}. Say \"read the locker "
                      f"sheet <name>\" and I'll put that "
                      f"one on the table.")
    return None, ("No sheet on the table and nothing "
                  "attached. Attach the blueprint (a photo "
                  "or PNG/JPG of the sheet reads best; a "
                  "PDF works when it carries a real text "
                  "layer) and say \"read this blueprint\" "
                  "— or save the sheet to your locker and "
                  "name it.")


def _read_fail_body(why: str, ref: Dict) -> str:
    if why == "notext":
        return NO_TEXT_PDF_LINE + f" ({CANT_READ} as text.)"
    if why == "bytes":
        return ("I couldn't pull that sheet's file at "
                "all just now — nothing was read and "
                "nothing got guessed. Try again in a bit.")
    return READ_FAILED_LINE


# --- Renderers ----------------------------------------------------------------------

def _summary_body(sheet: Dict) -> str:
    p = sheet.get("parsed") or {}
    lines = [f"Sheet on the table: {_sheet_name(sheet)} "
             f"— read via {sheet.get('how', 'the reader')}."]
    if p.get("title"):
        lines.append(f"Title as printed: {p['title']}.")
    sc = p.get("scale") or {}
    if sc.get("text"):
        lines.append(f"Scale as printed: {sc['text']} "
                     + _cite(sheet, "title block"))
    else:
        lines.append("No scale statement found in the "
                     "read — scale math off this sheet "
                     "isn't on until a scale is stated.")
    if p.get("pairs") or p.get("dims"):
        lines.append("Dimensions as read (each with its "
                     "source):")
        seen = set()
        for d in p.get("dims", []):
            lab = d.get("label") or "dimension"
            row = (f"• {lab}: {_ft_str(d['feet'])} "
                   f"(as printed: {d['raw']}) "
                   + _cite(sheet, f"\"{lab}\" callout"))
            if row not in seen:
                seen.add(row)
                lines.append(row)
    else:
        lines.append("No dimensions found in the read.")
    if p.get("notes"):
        lines.append("Notes and callouts as read:")
        for n in p["notes"][:12]:
            lines.append(f"• {n['text']} "
                         + _cite(sheet, n["tag"]))
    if p.get("unreadable"):
        names = "; ".join(
            u.get("label") or "a region"
            for u in p["unreadable"][:6])
        lines.append(
            f"Heads-up — parts of this sheet are "
            f"UNREADABLE in the read: {names}. Ask me "
            f"about one of those and the answer is "
            f"\"{CANT_READ}\" — I won't guess a number "
            f"off a blurry plan.")
    lines.append("Ask me for a takeoff, what a note "
                 "calls for, what a symbol means, or scale "
                 "math off this sheet — every number comes "
                 "off these words and names its source.")
    return "\n".join(lines)


def status_for(uid: str) -> Dict:
    """The read seam (/blueprint/status): the caller's
    own sheets + the one on the table."""
    cur = _current_sheet(uid)
    out = {"sheet": None, "sheets": []}
    for s in _sheets(uid):
        if not isinstance(s, dict):
            continue
        p = s.get("parsed") or {}
        out["sheets"].append({
            "key": s.get("key", ""), "name": s.get("name", ""),
            "sheet_id": p.get("sheet_id", ""),
            "title": p.get("title", ""),
            "source": s.get("source", ""),
            "scale": (p.get("scale") or {}).get("text", ""),
            "dims": len(p.get("dims", [])),
            "notes": len(p.get("notes", [])),
            "added": float(s.get("added", 0) or 0),
        })
    if cur:
        for row in out["sheets"]:
            if row["key"] == cur.get("key"):
                out["sheet"] = row
                break
    return out


# --- Operations: read ----------------------------------------------------------------

def _do_read(uid: str, claim: Dict) -> List[Dict]:
    sheet, err = _ensure_sheet(uid, claim)
    if sheet is None:
        return _result(_TAG, "Blueprint Specialist — no sheet", err)
    return _result(_TAG, "Blueprint Specialist — sheet read",
                   _summary_body(sheet))


# --- Operations: takeoff (deterministic math, work shown) ---------------------------

def _takeoff_body(uid: str, sheet: Dict, claim: Dict) -> str:
    p = sheet.get("parsed") or {}
    ask = str(claim.get("raw") or "").lower()
    subject = str(claim.get("subject") or "").lower()
    lines = [f"Takeoff off {_sheet_name(sheet)} — every "
             f"number from the read, work shown:"]
    pairs = p.get("pairs", [])
    if subject:
        pairs = [pr for pr in pairs
                 if subject in (pr.get("label") or "").lower()] \
            or pairs
    if pairs:
        total = 0.0
        for pr in pairs:
            lab = pr.get("label") or "area"
            lines.append(
                f"• {lab}: {_ft_str(pr['a'])} × "
                f"{_ft_str(pr['b'])} = "
                f"{_fmt(pr['a'])} ft × {_fmt(pr['b'])} ft "
                f"= {_fmt(pr['area'])} sq ft "
                + _cite(sheet, f"\"{lab}\" dimensions"))
            total += pr["area"]
        if len(pairs) > 1:
            lines.append(
                f"• Total of the dimensioned areas: "
                f"{_fmt(total)} sq ft (sum of the areas "
                f"above, each off its own callout).")
        for pr in pairs:
            lab = pr.get("label") or "area"
            per = 2 * (pr["a"] + pr["b"])
            lines.append(
                f"• {lab} perimeter: 2 × ({_fmt(pr['a'])} + "
                f"{_fmt(pr['b'])}) = {_fmt(per)} linear ft "
                + _cite(sheet, f"\"{lab}\" dimensions"))
        area = total
        sheets32 = math.ceil(area / 32.0) if area else 0
        if sheets32:
            lines.append(
                f"• 4×8 sheet goods over {_fmt(area)} sq ft: "
                f"{_fmt(area)} ÷ 32 = {_fmt(area / 32.0)} → "
                f"{sheets32} sheets (rounded up, no waste "
                f"factor — add yours before you order).")
        thick = claim.get("thickness_in")
        if thick is None:
            m = re.search(r"(\d+(?:\.\d+)?)\s*(?:\"|in\b|inch)"
                          r"[^.]{0,30}\bslab\b", ask)
            if not m:
                m = re.search(r"\bslab\b[^.]{0,30}?"
                              r"(\d+(?:\.\d+)?)\s*(?:\"|in\b|inch)",
                              ask)
            if m:
                thick = float(m.group(1))
        if thick:
            vol_ft3 = area * (float(thick) / 12.0)
            vol_yd3 = vol_ft3 / 27.0
            lines.append(
                f"• Concrete at {_fmt(float(thick))} in thick "
                f"over {_fmt(area)} sq ft: {_fmt(area)} × "
                f"{_fmt(float(thick))} ÷ 12 = "
                f"{_fmt(vol_ft3)} cu ft ÷ 27 = "
                f"{_fmt(vol_yd3)} cu yd (thickness from "
                f"your ask — the sheet read doesn't state "
                f"one).")
    if p.get("counts"):
        lines.append("Counts as printed on the sheet:")
        for c in p["counts"][:12]:
            lines.append(
                f"• {c['n']} {c['noun']} "
                + _cite(sheet, c["tag"]))
    singles = [d for d in p.get("dims", [])
               if not any(pr.get("label") == d.get("label")
                          and abs(pr["a"] - d["feet"]) < 1e-9
                          for pr in p.get("pairs", []))]
    if singles and not pairs:
        lines.append("Dimensions as read (no paired "
                     "dimensions, so no areas off this "
                     "sheet):")
        for d in singles[:12]:
            lab = d.get("label") or "dimension"
            lines.append(f"• {lab}: {_ft_str(d['feet'])} "
                         + _cite(sheet, f"\"{lab}\" callout"))
    if not pairs and not p.get("counts") and not singles:
        lines.append(
            "The read carries no dimensions I can take "
            "off — nothing measured gets invented here.")
    if p.get("unreadable"):
        names = "; ".join(u.get("label") or "a region"
                          for u in p["unreadable"][:4])
        lines.append(
            f"Not taken off (unreadable on the sheet): "
            f"{names} — {CANT_READ}, so no quantity "
            f"rides on those.")
    return "\n".join(lines)


def _do_takeoff(uid: str, claim: Dict) -> List[Dict]:
    sheet, err = _ensure_sheet(uid, claim)
    if sheet is None:
        return _result(_TAG, "Blueprint Specialist — no sheet", err)
    return _result(_TAG, "Blueprint Specialist — takeoff",
                   _takeoff_body(uid, sheet, claim))


# --- Operations: ask the sheet -------------------------------------------------------

def _subject_tokens(text: str) -> List[str]:
    stop = {"what", "does", "the", "sheet", "plan", "it",
            "this", "that", "for", "here", "say", "says",
            "call", "called", "calls", "spec", "specs",
            "show", "about", "there", "any", "and", "use",
            "used", "with", "from", "blueprint", "drawing",
            "on", "in", "of", "a", "an", "is", "do", "me"}
    return [t for t in re.split(r"[^a-z0-9]+", text.lower())
            if len(t) >= 3 and t not in stop]


def _do_ask(uid: str, claim: Dict) -> List[Dict]:
    sheet, err = _ensure_sheet(uid, claim)
    if sheet is None:
        return _result(_TAG, "Blueprint Specialist — no sheet", err)
    p = sheet.get("parsed") or {}
    subject = str(claim.get("subject") or "")
    toks = _subject_tokens(subject or claim.get("raw", ""))
    read_lines = [ln.strip() for ln in sheet.get("read", "").splitlines()
                  if ln.strip()]
    # An unreadable region that matches the ask gets the
    # fence answer, by name.
    for u in p.get("unreadable", []):
        lab = (u.get("label") or "").lower()
        if toks and any(t in lab for t in toks):
            return _result(
                _TAG, "Blueprint Specialist — can't read it",
                f"On {_sheet_name(sheet)}, the "
                f"\"{u.get('label')}\" part is marked "
                f"UNREADABLE in the read — {CANT_READ}, "
                f"and I won't guess what's under the "
                f"blur. A sharper photo of that corner "
                f"of the sheet gets it read for real.")
    hits = []
    for n in p.get("notes", []):
        low = n["text"].lower()
        if toks and any(t in low for t in toks):
            hits.append((n["tag"], n["text"]))
    if not hits:
        for d in p.get("dims", []):
            lab = (d.get("label") or "").lower()
            if toks and any(t in lab for t in toks):
                hits.append((f"\"{d.get('label')}\" callout",
                             f"{d.get('label')}: "
                             f"{_ft_str(d['feet'])} "
                             f"(as printed: {d['raw']})"))
    if hits:
        lines = [f"What {_sheet_name(sheet)} says about "
                 f"that — quoted from the read:"]
        for tag, text in hits[:5]:
            lines.append(f"• \"{text}\" " + _cite(sheet, tag))
        return _result(_TAG, "Blueprint Specialist — the sheet says",
                       "\n".join(lines))
    if toks:
        # Last door: a raw line of the read that carries
        # the subject (never a paraphrase presented as
        # fact — the line itself, quoted, with its line
        # named as the source).
        for ln in read_lines:
            low = ln.lower()
            if any(t in low for t in toks):
                return _result(
                    _TAG, "Blueprint Specialist — the sheet says",
                    f"The one line of {_sheet_name(sheet)} "
                    f"that touches it, quoted word for "
                    f"word: \"{ln}\" "
                    + _cite(sheet, "sheet text as read"))
    return _result(
        _TAG, "Blueprint Specialist — not on the sheet",
        f"The sheet doesn't say — I searched the read of "
        f"{_sheet_name(sheet)} ({len(p.get('notes', []))} "
        f"notes/callouts, {len(p.get('dims', []))} "
        f"dimensions) and nothing on it covers "
        f"\"{subject or 'that'}\". I'm not filling the "
        f"gap from memory: if it's not printed on the "
        f"sheet, it isn't sheet fact.")


# --- The symbol + abbreviation sheet (standard drafting vocabulary) ---------------
# Built-in knowledge, stated as what it is — the
# standard sheet this seat carries. It is never
# presented as something read off the visitor's sheet.

_ABBREV = {
    "aff": ("AFF — Above Finished Floor",
            "a height measured from the finished floor "
            "surface up, not from the slab or subfloor."),
    "cmu": ("CMU — Concrete Masonry Unit",
            "a concrete block. Sizes are named nominal: "
            "an '8-inch CMU' is 7-5/8 in actual, the "
            "mortar joint makes up the rest."),
    "gwb": ("GWB — Gypsum Wall Board",
            "drywall. The sheet usually specs the "
            "thickness and type (regular, type X "
            "fire-rated, moisture-resistant) right in "
            "the wall type or the notes."),
    "lvl": ("LVL — Laminated Veneer Lumber",
            "an engineered beam/header material — thin "
            "wood veneers glued into a strong, straight "
            "member. Sized by depth and width on the "
            "plan, and it is NOT interchangeable with a "
            "same-size sawn board without an engineer."),
    "nts": ("NTS — Not To Scale",
            "that drawing is a picture, not a measuring "
            "tool. Never scale a dimension off an NTS "
            "drawing — use the printed dimensions only."),
    "oc": ("O.C. — On Center",
            "spacing measured from the center of one "
            "member to the center of the next: '16 in "
            "O.C.' studs or joists repeat every 16 in "
            "center-to-center."),
    "rcp": ("RCP — Reflected Ceiling Plan",
            "the drawing of the ceiling as if reflected "
            "in a mirror on the floor — lights, diffusers, "
            "ceiling heights and finishes live there."),
    "typ": ("TYP — Typical",
            "whatever it labels repeats wherever the "
            "same condition shows up, so it only gets "
            "drawn once."),
    "sim": ("SIM — Similar",
            "the same as the referenced condition, "
            "usually mirrored or rotated — check the "
            "reference before assuming identical."),
    "uno": ("UNO — Unless Noted Otherwise",
            "the note or spec it rides on applies "
            "everywhere except where another note "
            "overrides it."),
    "vif": ("VIF — Verify In Field",
            "the drawing's number is a plan, not a "
            "promise: measure the real thing on site "
            "before cutting or ordering."),
    "nic": ("NIC — Not In Contract",
            "shown for reference, but somebody else's "
            "scope pays for and provides it."),
    "sog": ("SOG — Slab On Grade",
            "a concrete slab poured on the ground, as "
            "opposed to a suspended/structural slab."),
    "ffl": ("FFL — Finished Floor Level",
            "the elevation of the finished floor "
            "surface — tile, wood and all — the datum "
            "other heights hang off."),
    "cl": ("CL — Centerline",
            "the middle of a part, opening or grid — "
            "dimensions to CL land on the center, not "
            "a face."),
    "ro": ("R.O. — Rough Opening",
            "the framed hole a door or window unit "
            "drops into — bigger than the unit itself "
            "by the shim space the manufacturer specs."),
    "dlo": ("DLO — Daylight Opening",
            "the glass-only size of a window you can "
            "actually see through — smaller than the "
            "unit and the rough opening."),
    "elev": ("ELEV — Elevation",
            "either a height above the datum, or a "
            "straight-on drawing of a wall/building "
            "face — the context on the sheet tells "
            "you which."),
    "det": ("DET — Detail",
            "a blown-up drawing of one spot, drawn "
            "bigger so the layers and fasteners show."),
    "sect": ("SECT — Section",
            "a cut-through drawing: the building sliced "
            "at the section line so the inside "
            "construction shows."),
    "tg": ("T&G — Tongue And Groove",
            "boards milled to lock edge-to-edge — "
            "flooring, decking, paneling."),
    "osb": ("OSB — Oriented Strand Board",
            "the flake-panel sheathing used like "
            "plywood for roofs, walls and subfloors."),
    "pt": ("PT — Pressure Treated",
            "lumber treated against rot and bugs — "
            "required where wood touches concrete or "
            "weather; needs rated fasteners, the "
            "treatment eats plain steel."),
    "galv": ("GALV — Galvanized",
            "zinc-coated steel — the rust protection "
            "on exterior hardware, flashing and duct."),
    "hdwr": ("HDWR — Hardware",
            "the door/connection hardware — hinges, "
            "locks, straps, hangers — usually keyed to "
            "a hardware schedule."),
    "lf": ("LF — Linear Foot",
            "a length count: one foot along the run, "
            "regardless of width or height."),
    "sf": ("SF — Square Foot",
            "an area count: length × width in feet."),
    "cy": ("CY — Cubic Yard",
            "a volume count — 27 cubic feet. Concrete "
            "and soil are ordered in CY."),
    "ea": ("EA — Each",
            "the count unit: priced or noted per piece."),
    "spec": ("SPEC — Specification",
            "the written half of the plans — the book "
            "that says what the materials and work "
            "must be, where the drawings show where "
            "they go."),
}

_SYMBOLS = {
    "detail callout": (
        "The detail callout (a circle, often split, "
        "with two numbers)",
        "it points at a spot and says 'drawn bigger "
        "somewhere else': the top number is the detail "
        "number, the bottom is the sheet that detail "
        "lives on — like 3/A-301 means detail 3 on "
        "sheet A-301."),
    "detail bubble": (
        "The detail callout (a circle, often split, "
        "with two numbers)",
        "it points at a spot and says 'drawn bigger "
        "somewhere else': the top number is the detail "
        "number, the bottom is the sheet that detail "
        "lives on — like 3/A-301 means detail 3 on "
        "sheet A-301."),
    "section cut": (
        "The section cut line (a heavy line ending in "
        "a circled arrow/flag)",
        "the building is sliced along that line; the "
        "arrow shows which way you're looking, and "
        "the circle's numbers name the section drawing "
        "and its sheet."),
    "section line": (
        "The section cut line (a heavy line ending in "
        "a circled arrow/flag)",
        "the building is sliced along that line; the "
        "arrow shows which way you're looking, and "
        "the circle's numbers name the section drawing "
        "and its sheet."),
    "grid bubble": (
        "The grid bubble (a circle with a letter or "
        "number, on a long light line)",
        "it names a column grid line. Dimensions and "
        "callouts locate things by grid — 'at grid "
        "B-4' means the crossing of grid B and grid 4."),
    "elevation marker": (
        "The elevation marker (a circle with a "
        "pointing triangle/arrow and numbers)",
        "it names an elevation view — a straight-on "
        "drawing of the wall it points at — and, as a "
        "spot marker, an exact height at that point."),
    "door swing": (
        "The door swing (the thin arc next to a door)",
        "the arc is the door's travel: it shows which "
        "way the door opens and which side the hinges "
        "sit on. Handing (LH/RH) is read off it."),
    "door tag": (
        "The door tag (a small circle with a number, "
        "in or beside the door)",
        "it keys that door to the door schedule — "
        "size, type, hardware and rating live in the "
        "schedule row with that number."),
    "window tag": (
        "The window tag (a diamond or hexagon with a "
        "number, in the window)",
        "it keys that window to the window schedule — "
        "unit size, type and glass live in the "
        "schedule row with that number."),
    "north arrow": (
        "The north arrow",
        "the plan's orientation: the arrow points to "
        "true (or plan) north, so 'the south wall' on "
        "the sheet maps to the real building."),
    "revision triangle": (
        "The revision triangle (a small triangle with "
        "a number, the 'delta')",
        "it flags a changed spot on a reissued sheet. "
        "The number keys to the revision list, which "
        "says what changed and when — clouded areas "
        "with a delta are the newest word."),
    "revision delta": (
        "The revision triangle (a small triangle with "
        "a number, the 'delta')",
        "it flags a changed spot on a reissued sheet. "
        "The number keys to the revision list, which "
        "says what changed and when — clouded areas "
        "with a delta are the newest word."),
    "centerline": (
        "The centerline (a long-dash/short-dash line, "
        "often marked CL)",
        "the middle of a part, opening or grid. "
        "Dimensions to a centerline land on the "
        "center, not on a face."),
    "hidden line": (
        "The hidden line (a dashed line)",
        "something real is there but behind or above "
        "what the drawing cuts through — a beam over "
        "a ceiling, a footing under a slab."),
    "break line": (
        "The break line (a zigzag across a drawing)",
        "the drawing stops there on purpose — the part "
        "keeps going the same way, it just isn't all "
        "drawn. Never read a break as an edge."),
    "match line": (
        "The match line (a heavy line labeled with "
        "another sheet)",
        "the drawing continues past that line on the "
        "named sheet — the two sheets join exactly at "
        "the match line."),
    "keynote": (
        "The keynote (a number in a hexagon, diamond "
        "or square)",
        "it keys that spot to a written keynote list "
        "on the same sheet — the words for that number "
        "are part of the drawing, read them before "
        "building that spot."),
}


def _do_symbol(uid: str, claim: Dict) -> List[Dict]:
    token = str(claim.get("token") or "").strip()
    low = token.lower()
    head = body = None
    if low in _ABBREV:
        head, body = _ABBREV[low]
    else:
        for key, (h, b) in _SYMBOLS.items():
            if key in low or low in key:
                head, body = h, b
                break
    if head:
        return _result(
            _TAG, "Blueprint Specialist — plain words",
            f"{head}: {body} (That's the standard "
            f"drafting meaning — the seat's own "
            f"reference sheet, not a line read off "
            f"your plan.)")
    sheet = _current_sheet(uid)
    if sheet:
        read = sheet.get("read", "")
        for ln in read.splitlines():
            if token and token.lower() in ln.lower():
                return _result(
                    _TAG, "Blueprint Specialist — not on my sheet",
                    f"\"{token}\" isn't on the standard "
                    f"reference sheet this seat carries, "
                    f"so I won't invent a meaning for it. "
                    f"Your sheet does use it, though — "
                    f"the line as read: \"{ln.strip()}\" "
                    + _cite(sheet, "sheet text as read"))
    return _result(
        _TAG, "Blueprint Specialist — not on my sheet",
        f"\"{token}\" isn't on the standard reference "
        f"sheet this seat carries, and I won't invent a "
        f"meaning for it. If it's printed on your "
        f"sheet, the sheet's own legend or notes are "
        f"the authority — put the sheet on the table "
        f"and I'll read what it says there.")


# --- Operations: scale math -----------------------------------------------------------

_PAPER_RX = re.compile(
    r"(\d+(?:\.\d+)?|\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+)"
    r"\s*(?:\"|in\b|inch|inches)\b", re.I)


def _paper_inches(text: str) -> Optional[float]:
    m = _PAPER_RX.search(text or "")
    if not m:
        return None
    raw = m.group(1).strip()
    if " " in raw and "/" in raw:
        whole, _, fr = raw.partition(" ")
        v = _frac_val(fr)
        try:
            return float(whole) + (v or 0.0)
        except ValueError:
            return v
    return _frac_val(raw)


def _do_scale(uid: str, claim: Dict) -> List[Dict]:
    sheet, err = _ensure_sheet(uid, claim)
    ask = str(claim.get("raw") or "")
    paper = _paper_inches(ask)
    stated = None
    m = _SCALE_EQ_RX.search(ask)
    if m:
        v = _frac_val(m.group(1))
        if v:
            stated = (v, m.group(0).strip())
    if stated is None:
        m = _SCALE_ENG_RX.search(ask)
        if m:
            v = _frac_val(m.group(1))
            if v:
                stated = (1.0 / v, m.group(0).strip())
    if sheet is not None:
        sc = (sheet.get("parsed") or {}).get("scale") or {}
        if sc.get("nts"):
            return _result(
                _TAG, "Blueprint Specialist — not to scale",
                f"That sheet is marked NTS (not to scale) "
                f"as printed: {sc.get('text', 'NTS')} "
                + _cite(sheet, "title block")
                + " — scaling a measurement off it would "
                "be a guess wearing a number's clothes, "
                "and I don't do those. Use the sheet's "
                "printed dimensions; those are the "
                "contract.")
        if sc.get("in_per_ft"):
            if paper is None:
                return _result(
                    _TAG, "Blueprint Specialist — scale",
                    f"The sheet's scale as printed: "
                    f"{sc['text']} "
                    + _cite(sheet, "title block")
                    + ". Tell me the paper measurement "
                    "(\"what's 3 1/2 inches on the "
                    "sheet\") and I'll run it.")
            real_ft = paper / sc["in_per_ft"]
            return _result(
                _TAG, "Blueprint Specialist — scale math",
                f"Off {_sheet_name(sheet)} at "
                f"{sc['text']} "
                + _cite(sheet, "title block scale")
                + f": {_fmt(paper)} in on paper ÷ "
                f"{_fmt(sc['in_per_ft'])} in per foot = "
                f"{_fmt(real_ft)} ft real = "
                f"{_ft_str(real_ft)}. (Scaled numbers "
                f"are for layout sanity — the printed "
                f"dimension always outranks a scaled "
                f"one.)")
    if stated and paper is not None:
        real_ft = paper / stated[0]
        return _result(
            _TAG, "Blueprint Specialist — scale math",
            f"At the scale you stated ({stated[1]}): "
            f"{_fmt(paper)} in on paper ÷ "
            f"{_fmt(stated[0])} in per foot = "
            f"{_fmt(real_ft)} ft real = {_ft_str(real_ft)}. "
            f"(Your stated scale — no sheet scale read "
            f"yet.)")
    if sheet is None:
        return _result(_TAG, "Blueprint Specialist — no sheet", err)
    return _result(
        _TAG, "Blueprint Specialist — no scale read",
        "The sheet on the table carries no scale "
        "statement in its read, and your ask doesn't "
        "state one either — so there's no honest scale "
        "math to run. State the scale (\"at a quarter "
        "inch to a foot\") or use the printed "
        "dimensions, which outrank scaled numbers "
        "anyway.")


# --- Operations: the two-sheet cross-check ---------------------------------------------

def _norm_label(label: str) -> str:
    lab = re.sub(r"[^a-z0-9 ]+", " ", str(label or "").lower())
    return re.sub(r"\s+", " ", lab).strip()


def _dim_map(sheet: Dict) -> Dict[str, float]:
    p = sheet.get("parsed") or {}
    out: Dict[str, float] = {}
    for d in p.get("dims", []):
        lab = _norm_label(d.get("label") or "")
        if lab:
            out.setdefault(lab, d["feet"])
    for pr in p.get("pairs", []):
        lab = _norm_label(pr.get("label") or "")
        if lab:
            a, b = sorted((pr["a"], pr["b"]))
            out.setdefault(lab + " (short side)", a)
            out.setdefault(lab + " (long side)", b)
    return out


def _do_check(uid: str, claim: Dict) -> List[Dict]:
    rows = [s for s in _sheets(uid) if isinstance(s, dict)]
    if len(rows) < 2:
        sheet, err = _ensure_sheet(uid, claim)
        if sheet is None:
            return _result(
                _TAG, "Blueprint Specialist — one sheet only", err)
        return _result(
            _TAG, "Blueprint Specialist — one sheet only",
            f"Only one sheet is on file "
            f"({_sheet_name(sheet)}) — a cross-check "
            f"needs two. Put the second sheet on the "
            f"table (attach it, or name the locker one) "
            f"and say \"check the sheets\" again.")
    a, b = rows[-2], rows[-1]
    ma, mb = _dim_map(a), _dim_map(b)
    lines = [f"Cross-check: {_sheet_name(a)} against "
             f"{_sheet_name(b)} — same labels, both reads:"]
    conflicts = []
    agreed_labels = []
    for lab in sorted(set(ma) & set(mb)):
        va, vb = ma[lab], mb[lab]
        if abs(va - vb) > (1.0 / 24.0):
            conflicts.append((lab, va, vb))
        else:
            agreed_labels.append(lab)
    agreed = len(agreed_labels)
    for lab, va, vb in conflicts:
        lines.append(
            f"⚠️ CONFLICT — \"{lab}\": "
            f"{_sheet_name(a)} says {_ft_str(va)} "
            f"({_fmt(va)} ft); {_sheet_name(b)} says "
            f"{_ft_str(vb)} ({_fmt(vb)} ft). Both "
            f"values quoted from the reads — the sheets "
            f"disagree by {_fmt(abs(va - vb) * 12)} in, "
            f"and one of them is wrong for the build. "
            f"That's an RFI to the designer, not a "
            f"coin flip on site.")
    if not conflicts:
        lines.append("No dimension conflicts: every "
                     "label both sheets dimension "
                     "agrees.")
    if agreed:
        lines.append(f"{agreed} shared dimension"
                     f"{'s' if agreed != 1 else ''} agree: "
                     f"{', '.join(agreed_labels[:6])}.")
    only_a = sorted(set(ma) - set(mb))
    only_b = sorted(set(mb) - set(ma))
    if only_a:
        lines.append(f"Dimensioned only on "
                     f"{_sheet_name(a)} (nothing to check "
                     f"against): {', '.join(only_a[:6])}.")
    if only_b:
        lines.append(f"Dimensioned only on "
                     f"{_sheet_name(b)} (nothing to check "
                     f"against): {', '.join(only_b[:6])}.")
    sca = ((a.get("parsed") or {}).get("scale") or {}).get("text", "")
    scb = ((b.get("parsed") or {}).get("scale") or {}).get("text", "")
    if sca and scb and sca != scb:
        lines.append(f"Note: the sheets print different "
                     f"scales ({sca} vs {scb}) — normal "
                     f"across a set, just don't carry a "
                     f"scaled measurement from one onto "
                     f"the other.")
    return _result(_TAG, "Blueprint Specialist — cross-check",
                   "\n".join(lines))


# --- Operations: hand-off (PM carries the takeoff) ------------------------------------

DOC_CAP = 12000          # chars of the saved takeoff document


def _save_takeoff_doc(uid: str, sheet: Dict, body: str):
    """The takeoff document into the caller's locker
    (the Builder/MaintEng shape, the same seams).
    Returns (record, reason): exactly one is set."""
    import og_storage
    if not og_storage.storage_enabled():
        return None, "dark"
    name = (f"{_sheet_name(sheet)} — takeoff.txt")
    data = body.encode("utf-8")[:DOC_CAP]
    try:
        import og_builder
        rec = og_storage.save_bytes(
            uid, og_builder._tier_for_save(uid),
            name, data, "text/plain")
        return rec, ""
    except Exception as e:
        msg = str(e).strip()
        if msg in ("dark", "free"):
            return None, msg
        if "only got" in msg or "left in your locker" in msg:
            return None, "quota"
        logger.warning(f"Blueprint takeoff save failed: {e}")
        return None, "other"


def _do_handoff(uid: str, claim: Dict) -> List[Dict]:
    sheet, err = _ensure_sheet(uid, claim)
    if sheet is None:
        return _result(_TAG, "Blueprint Specialist — no sheet", err)
    if sheet.get("project_id"):
        return _result(
            _TAG, "Blueprint Specialist — handed off",
            f"The takeoff off {_sheet_name(sheet)} is "
            f"already carried by the Project Manager — "
            f"nothing new to hand off.")
    body = _takeoff_body(uid, sheet, {"raw": "", "subject": ""})
    goal = (f"Blueprint takeoff — {_sheet_name(sheet)}: "
            f"quantities off the sheet, conflicts checked, "
            f"materials ordered through the approval door")
    try:
        import og_pm
        before = {p.get("id") for p in og_pm._projects(uid)
                  if isinstance(p, dict)}
        og_pm._create_project(uid, goal)
        after = [p for p in og_pm._projects(uid)
                 if isinstance(p, dict)
                 and p.get("id") not in before]
        proj = after[0] if after else None
    except Exception as e:
        logger.warning(f"Blueprint PM handoff failed: {e}")
        proj = None
    if not proj:
        return _result(
            _TAG, "Blueprint Specialist — handoff glitch",
            "The Project Manager didn't take the takeoff "
            "— nothing was created and the sheet is "
            "still on the table here. Say \"track this "
            "takeoff in projects\" again and I'll retry.")
    pid = proj.get("id", "")
    ptitle = proj.get("title", "")
    rec, why = _save_takeoff_doc(uid, sheet, body)
    filed = False
    if rec:
        try:
            import og_filing
            link = og_filing.file_link(uid, pid, "locker",
                                       rec.get("kind", ""),
                                       rec.get("id", ""))
            filed = link is not None
        except Exception as e:
            logger.warning(f"Blueprint filing failed: {e}")
            filed = False
        if filed:
            doc_line = (f"The takeoff document is saved "
                        f"in your locker as "
                        f"\"{rec.get('name', '')}\" and "
                        f"filed under the project.")
        else:
            doc_line = (f"The takeoff document is saved "
                        f"in your locker as "
                        f"\"{rec.get('name', '')}\", but "
                        f"the filing link didn't land — "
                        f"say \"file this under the "
                        f"project\" and the Filer will "
                        f"link it.")
    else:
        reasons = {
            "dark": "the locker is switched off right now",
            "free": "your plan doesn't include locker "
                    "storage (it starts at Standard)",
            "quota": "your locker is full",
            "other": "the locker save glitched",
        }
        doc_line = (f"The takeoff document could NOT be "
                    f"saved to your locker — "
                    f"{reasons.get(why, why)} — so there's "
                    f"nothing for the Filer to link yet. "
                    f"The takeoff still reads off the "
                    f"sheet here any time you ask.")
    rows = _sheets(uid)
    for s in rows:
        if s.get("key") == sheet.get("key"):
            s["project_id"] = pid
    _save_sheets(uid, rows)
    return _result(
        _TAG, "Blueprint Specialist — handed off",
        f"Handed off — the Project Manager now carries "
        f"\"{ptitle}\" as a project (Progress shows "
        f"it), with the takeoff in its plan. " + doc_line)


# --- Operations: show -----------------------------------------------------------------

def _do_show(uid: str) -> List[Dict]:
    cur = _current_sheet(uid)
    if cur is None:
        return _result(
            _TAG, "Blueprint Specialist — nothing on the table",
            "No sheet on the table yet. Attach a "
            "blueprint and say \"read this blueprint\", "
            "or name a sheet from your locker.")
    return _result(_TAG, "Blueprint Specialist — sheet read",
                   _summary_body(cur))


def blueprint_results(claim: Dict, uid: str) -> List[Dict]:
    op = claim.get("op")
    if op == "bp_read":
        return _do_read(uid, claim)
    if op == "bp_takeoff":
        return _do_takeoff(uid, claim)
    if op == "bp_ask":
        return _do_ask(uid, claim)
    if op == "bp_symbol":
        return _do_symbol(uid, claim)
    if op == "bp_scale":
        return _do_scale(uid, claim)
    if op == "bp_check":
        return _do_check(uid, claim)
    if op == "bp_handoff":
        return _do_handoff(uid, claim)
    if op == "bp_show":
        return _do_show(uid)
    return _do_show(uid)


# --- Chat claims (narrow frames — the crew/PM/filer doctrine) -------------------------

_GARBLE_VOCAB = ("blueprint", "blueprints", "takeoff",
                 "takeoffs", "sheet", "sheets", "scale",
                 "plans", "drawings", "callout", "callouts",
                 "dimension", "dimensions", "symbols")

_BP_NOUN = (r"(?:blueprint|blue\s*print|sheet|plans?|"
            r"drawings?|print)")
_READ_RE = re.compile(
    r"\b(?:read|open|load|look at)\b[^.?!]*\b" + _BP_NOUN
    + r"\b|\bread\s+(?:this|it|that)\b", re.I)
_LOCKER_RE = re.compile(
    r"\blocker\b[^.?!]*\b" + _BP_NOUN + r"\b|\b" + _BP_NOUN
    + r"\b[^.?!]*\blocker\b", re.I)
_TAKEOFF_RE = re.compile(
    r"\btake ?offs?\b|\bquantit(?:y|ies)\b[^.?!]*\b"
    r"(?:off|from|for)\b|\bhow (?:many|much)\b", re.I)
_ASK_RE = re.compile(
    r"\bwhat\s+does\s+(?:it|this|the\s+" + _BP_NOUN
    + r")\s+(?:call\s+for|say|spec|show)\b"
    r"|\bdoes\s+the\s+" + _BP_NOUN + r"\b[^.?!]*\b"
    r"(?:say|spec|show|call\s+for)\b"
    r"|\bwhat'?s\s+(?:called\s+for|spec'?d|specified)\b"
    r"|\bwhat\s+does\s+this\s+call\s+for\b", re.I)
_CHECK_RE = re.compile(
    r"\bcross[-\s]?check\b|\bcheck\s+(?:the\s+)?sheets\b"
    r"|\bdo\s+(?:the\s+)?sheets\s+agree\b|\bcompare\s+"
    r"(?:the\s+)?(?:sheets|plans|drawings)\b|\bsheets\s+"
    r"(?:match|agree|conflict)\b|\bconflicts?\b[^.?!]*"
    r"\bsheets?\b", re.I)
_SCALE_RE = re.compile(
    r"\bscale\b[^.?!]*\b(?:sheet|plan|drawing|blueprint|"
    r"this|it)\b|\bwhat'?s\s+the\s+scale\b|\bin\s+real\s+"
    r"(?:life|feet)\b|\bon\s+(?:the\s+)?(?:sheet|plan|"
    r"drawing)\b[^.?!]*\b(?:in\s+real|scale)\b", re.I)
_SYMBOL_WORD_RE = re.compile(
    r"\b(?:symbol|abbreviation|abbrev|callout|keynote|"
    r"legend)\b|\bdetail\s+(?:bubble|callout)\b|\bgrid\s+"
    r"bubble\b|\bsection\s+(?:cut|line)\b|\bdoor\s+swing"
    r"\b|\bnorth\s+arrow\b|\brevision\s+(?:triangle|"
    r"delta)\b|\bmatch\s+line\b|\bbreak\s+line\b|"
    r"\bhidden\s+line\b|\bcenterline\b", re.I)
_MEAN_RE = re.compile(
    r"\bmean\b|\bstands?\s+for\b|\brepresent\b", re.I)
_TOKEN_RE = re.compile(r"\b([A-Z][A-Z0-9./]{1,7})\b")
_HANDOFF_RE = re.compile(
    r"\btrack\s+this\b[^.?!]*\bprojects?\b|\bmake\s+"
    r"(?:this|the\s+takeoff|it)\b[^.?!]*\bproject\b|"
    r"\bhand\s*off\b[^.?!]*\btakeoff\b|\bsave\s+(?:the\s+"
    r"|this\s+)?takeoff\b|\bcarry\s+the\s+takeoff\b",
    re.I)
_SHOW_RE = re.compile(
    r"\bwhat\s+sheet\b[^.?!]*\btable\b|\bshow\s+(?:me\s+"
    r")?(?:the\s+)?sheet\b|\bsheet\s+status\b|\bwhat'?s\s+"
    r"on\s+the\s+table\b", re.I)
_SUBJECT_STOP = re.compile(
    r"\b(?:the|a|an|for|of|in|on|this|that|my|sheet|"
    r"plan|blueprint|drawing|please)\b", re.I)


def _other_claim(message: str, uid: str):
    """(source, claim) of the first inner seat that
    claims this message exactly, else None. The PM,
    the Filer, the crew, the Builder, the Wood Pro,
    MaintEng and the Mechanic own their asks —
    checked exactly."""
    try:
        import og_pm
        c = og_pm._claim_exact(message)
        if c:
            return "pm", c
    except Exception:
        pass
    try:
        import og_filing
        c = og_filing._claim_exact(message, uid)
        if c:
            return "filing", c
    except Exception:
        pass
    try:
        import og_crew
        c = og_crew._claim_exact(message)
        if c:
            return "crew", c
    except Exception:
        pass
    try:
        import og_builder
        job, _info = og_builder._claim_builder(
            message, uid)
        if job:
            return "builder", job
    except Exception:
        pass
    try:
        import og_woodpro
        c = og_woodpro._claim_exact(message, uid)
        if c:
            return "woodpro", c
    except Exception:
        pass
    try:
        import og_mainteng
        c = og_mainteng._claim_exact(message, uid)
        if c:
            return "mainteng", c
    except Exception:
        pass
    try:
        import og_mechanic
        c = og_mechanic._claim_exact(message, uid)
        if c:
            return "mechanic", c
    except Exception:
        pass
    return None


def _defer_to_others(message: str, uid: str) -> bool:
    return _other_claim(message, uid) is not None


def _has_sheet_context(uid: str, raw: str) -> bool:
    if _current_sheet(uid) is not None:
        return True
    if _attached_ref(uid) is not None:
        return True
    if re.search(r"\b" + _BP_NOUN + r"\b", raw, re.I):
        return True
    return False


def _locker_name_query(raw: str) -> str:
    m = re.search(r"\bcalled\s+\"?([^\".?!]+)\"?", raw, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"\blocker\s+sheet\s+\"?([^\".?!]+)\"?",
                  raw, re.I)
    if m:
        return _SUBJECT_STOP.sub(" ", m.group(1)).strip()
    return ""


def _claim_exact(message: str, uid: str) -> Optional[Dict]:
    raw = str(message or "").strip()
    if not raw or not uid:
        return None
    if _defer_to_others(raw, uid):
        return None
    low = raw.lower()
    ctx = _has_sheet_context(uid, raw)
    locker_q = ""
    if _LOCKER_RE.search(raw):
        locker_q = _locker_name_query(raw) or " "
    # A locker sheet read names its source first.
    if locker_q and _READ_RE.search(raw):
        return {"op": "bp_read", "locker": locker_q.strip(),
                "raw": raw}
    if _CHECK_RE.search(raw) and ctx:
        return {"op": "bp_check", "raw": raw}
    if _HANDOFF_RE.search(raw) and ctx:
        return {"op": "bp_handoff", "raw": raw}
    if _READ_RE.search(raw) and (
            re.search(r"\b" + _BP_NOUN + r"\b", raw, re.I)
            or _attached_ref(uid) is not None):
        out = {"op": "bp_read", "raw": raw}
        if locker_q:
            out["locker"] = locker_q.strip()
        return out
    if _SHOW_RE.search(raw) and ctx:
        return {"op": "bp_show", "raw": raw}
    # Symbols: an explicit symbol word, or a known
    # abbreviation token in a "what does X mean" shape.
    if _SYMBOL_WORD_RE.search(raw):
        m = _SYMBOL_WORD_RE.search(raw)
        tok = raw
        mm = re.search(r"\bwhat\s+(?:does\s+)?(?:the\s+|"
                       r"a\s+)?(.{3,40}?)\s*(?:mean|symbol"
                       r"|represent)", raw, re.I)
        if mm:
            tok = mm.group(1).strip()
        return {"op": "bp_symbol", "token": tok,
                "raw": raw}
    if _MEAN_RE.search(raw):
        for m in _TOKEN_RE.finditer(raw):
            tok = m.group(1).strip("./")
            if tok.lower() in _ABBREV:
                return {"op": "bp_symbol", "token": tok,
                        "raw": raw}
    if _SCALE_RE.search(raw) and ctx:
        return {"op": "bp_scale", "raw": raw}
    if _ASK_RE.search(raw) and ctx:
        subj = re.sub(r"^.*?\b(?:for|about|on)\b", " ", low)
        return {"op": "bp_ask", "raw": raw,
                "subject": subj.strip() or low}
    if _TAKEOFF_RE.search(raw) and ctx:
        subj = ""
        m = re.search(r"\bfor\s+(?:the\s+)?([a-z][a-z ]{2,30})",
                      low)
        if m:
            subj = m.group(1).strip()
        return {"op": "bp_takeoff", "raw": raw,
                "subject": subj}
    return None


def _claim_blueprint(message: str, uid: str):
    """(claim, garble_info): the exact claim first; on
    a miss, the Round 75 second chance — og_garble
    repairs the TRIGGER words only; the payload
    passes verbatim."""
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


# --- The seam -------------------------------------------------------------------------

_pending = {"claim": None, "garble": None}


def install_blueprint_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search OUTSIDE the Car
    Mechanic seam (installed last, from the og_fixqueue
    chain) so blueprint asks claim here and everything
    else delegates back untouched. Persona files never
    touched."""
    if getattr(agent_instance, "_og_blueprint_installed",
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
        _pending["claim"] = None
        _pending["garble"] = None
        try:
            uid = get_uid()
            if uid and blueprint_enabled():
                claim, info = _claim_blueprint(
                    str(message), uid)
                if claim:
                    _pending["claim"] = claim
                    _pending["garble"] = info
                    if isinstance(intent, dict):
                        intent[
                            "needs_code_generation"] = False
                        if not intent.get("needs_web_search"):
                            intent["needs_web_search"] = True
                            intent["search_query"] = \
                                str(message).strip()
        except Exception as e:
            logger.warning(f"Blueprint trigger check failed: {e}")
            _pending["claim"] = None
            _pending["garble"] = None
        return intent

    def search_wrapped(query, num_results=5):
        claim = _pending.get("claim")
        garble = _pending.get("garble")
        _pending["claim"] = None
        _pending["garble"] = None
        uid = ""
        try:
            uid = get_uid()
        except Exception:
            uid = ""
        results = None
        if claim and uid:
            try:
                results = blueprint_results(claim, uid)
            except Exception as e:
                logger.warning(f"Blueprint op failed: {e}")
                results = _result(
                    _TAG, "Blueprint Specialist — glitch",
                    "That ask glitched — nothing was read "
                    "or changed. Say it again and I'll "
                    "retry.")
        if results is None:
            results = prev_search(query, num_results)
        if garble and results:
            results = og_garble.annotate(results, garble,
                                         prepend=True)
        return results

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_blueprint_installed = True


# --- Routes ----------------------------------------------------------------------------

def register_blueprint_routes(app):
    """GET /blueprint/status — the caller's own sheets
    + the one on the table. Identity is the caller's
    cookie uid, like every Round 95+ surface."""

    @app.get("/blueprint/status")
    def blueprint_status_route(request: Request):
        uid = request.cookies.get("ogai_uid") or ""
        view = status_for(uid) if uid else {
            "sheet": None, "sheets": []}
        return {"ok": True, "enabled": blueprint_enabled(),
                **view}
