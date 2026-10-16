"""
OG designer (Generator 7b) — thumbnail / poster designer +
meme maker, extending the Round 5 image generator:

1. THUMBNAIL. "make a thumbnail for <topic>, title: <TITLE>"
   generates bold focal art through the EXISTING image path
   (og_image_gen.openai_generate_image, 1536x1024) and
   composites the TITLE onto it locally — bottom band, big
   bold lettering, stroke + scrim for pop — then cover-crops
   to a true 1280x720 (16:9).
2. POSTER. Same machinery, portrait (1024x1536 art): title at
   the top, an optional subtitle line under it.
3. MEME. "make a meme: <image idea> / top: <text> /
   bottom: <text>" (natural variants accepted) generates the
   base image (1024x1024) and composites the captions —
   classic white bold with a heavy black outline, top and
   bottom, NO band.

TEXT IS VERBATIM AND NEVER MODEL-RENDERED. Image models
garble lettering, so the words the visitor gave are NEVER
part of the prompt sent to the image model: prompts are
built only from the topic / image idea plus style shaping
and an explicit "no text, no words, no letters, no logos"
instruction. The exact user strings are burned in locally,
character for character (no upper-casing, no rewording).

COMPOSITING TOOL. Pillow (PIL) is the primary compositor —
it is present in the working venv (12.3.0) with DejaVu Sans
Bold on the system. Pillow was NOT in requirements.txt
before this round and nothing else in the codebase imports
it, so two honest safeguards ship with the pack: (a)
Pillow>=10 is now declared in requirements.txt (a loud,
documented addition — see round-gen-designer/NOTES.md,
never a silent dependency), and (b) if PIL cannot be
imported at runtime the compositor falls back to ffmpeg
drawtext plus a system bold font — probing for a binary
that actually HAS drawtext, since og_video's bundled
imageio-ffmpeg build lacks it in this environment while
the system ffmpeg carries it.
If neither tool exists, generation refuses honestly BEFORE
any charge: designs are not shipped without their words.

CHARGING — exactly the /image posture (Round 5 + R55):
- The weekly images pool fences first: og_tiers.weekly_cap
  (tier, "images") against the visitor's trailing-7-day
  usage in the SAME entry app.py's _consume_image writes,
  "image:<uid>" (carry_days / note_day substrate).
- Past the wall, token packs fund the design at the same
  rate /image uses (og_tokenpacks.RATES["image"] = 750).
- At the wall with no funding: the refusal IS /image's
  refusal (og_tiers.weekly_image_line) and NOTHING is
  generated.
- The charge lands SUCCESS-ONLY: after a verified composited
  PNG exists. A failed generation or composite charges
  nothing (no weekly consume, no token debit).

DELIVERY. The finished PNG is returned in-chat (the
/designer/generate response carries the data URI, rendered
by static/og_designer.js exactly like /image results; the
claim-seam path surfaces through the /designer/status card),
is downloadable owner-only at GET /designer/download/<id>
(24 h job store), and is AUTO-SAVED to the visitor's locker
through og_storage.save_bytes — which is what lands it in
the Library Images tab (the R33 rule: Images = locker
records whose kind is "image"; save_bytes sniffs the PNG
magic to kind "image"). The locker record carries no
free-form metadata column, so the design tag rides the
saved NAME: "design-<kind>-<slug>.png" (the Library title
is the record name), and the job record here keeps the
design kind + the verbatim texts. When the locker refuses
(free tier, locker dark, quota full) the design is STILL
delivered via the download route and the response says
saved=False with the honest reason — never a fake save.

No approval gate: the task specifies none and a design is a
single image-priced unit, prepared and delivered in one
turn (unlike the song/podcast prepare->approve flows).
Generation is synchronous, like /image itself.

DARK GATE: live only when an OpenAI key is configured AND
a compositing tool exists (PIL importable, or ffmpeg +
a font). Dark = claims never fire, /designer/status answers
{"enabled": false}, the generate route 404s.

Persona files are NEVER touched by this module.
"""

import asyncio
import base64
import glob
import hashlib
import io
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

import og_tiers as _tiers

logger = logging.getLogger(__name__)

STORE_DIR = os.getenv("OG_DESIGNER_STORE_DIR", "designer_builds")
_JOB_TTL = 24 * 60 * 60         # finished designs kept 24 h
_STALE_S = 45 * 60

# Per-kind request size on the image API + delivered size.
_KIND_SIZE = {"thumbnail": "1536x1024", "poster": "1024x1536",
              "meme": "1024x1024"}
_KIND_OUT = {"thumbnail": (1280, 720), "poster": (1024, 1536),
             "meme": (1024, 1024)}

# Bound by app.py via bind_app when a host binds them (tests);
# unbound (production) each dep resolves lazily from the
# fully-loaded app module — the og_podcast precedent.
_deps: Dict = {}

_APP_ATTRS = {
    "load_usage": "_load_usage_store",
    "save_usage": "_save_usage_store",
    "usage_lock": "_usage_lock",
    "load_history": "_load_visitor_history_locked",
}


def bind_app(deps):
    _deps.update(deps)


def _dep(name):
    if name in _deps:
        return _deps[name]
    if name == "get_api_key":
        return lambda: os.getenv("OPENAI_API_KEY", "")
    if name == "get_tier":
        def _get_tier():
            try:
                import app as _appmod
                return str(_appmod._current_tier.get("tier", "free")
                           or "free")
            except Exception:
                return "free"
        return _get_tier
    if name == "tier_of":
        def _tier_of(uid, req):
            try:
                import app as _appmod
                return _appmod._tier_of(req.cookies, uid)
            except Exception:
                return "free"
        return _tier_of
    attr = _APP_ATTRS.get(name)
    if attr is None:
        return None
    try:
        import app as _appmod
        return getattr(_appmod, attr, None)
    except Exception as e:
        logger.warning(f"og_designer: app dep {name} unavailable: {e}")
        return None


def _pro_url() -> str:
    return _tiers.public_pro_url()


# --- Compositing tool availability ----------------------------------------------


def _pil():
    try:
        import PIL.Image  # noqa: F401
        return True
    except Exception:
        return False


_FONT_PATTERNS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/**/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/**/NotoSans-Bold.ttf",
    "/usr/share/fonts/**/NotoSansDisplay-Bold.ttf",
    "/usr/share/fonts/**/LiberationSans-Bold.ttf",
    "/usr/share/fonts/**/*Sans*-Bold.ttf",
    "/usr/share/fonts/**/*-Bold.ttf",
)


def _font_path() -> str:
    env = os.getenv("OG_DESIGNER_FONT", "").strip()
    if env and os.path.exists(env):
        return env
    for pat in _FONT_PATTERNS:
        for hit in sorted(glob.glob(pat, recursive=True)):
            return hit
    return ""


_drawtext_cache: Dict = {}


def _drawtext_ffmpeg() -> str:
    """An ffmpeg binary that actually HAS the drawtext filter
    (probed, cached). NOTE: og_video's bundled imageio-ffmpeg
    binary is built WITHOUT libfreetype/drawtext in this
    environment, while the system ffmpeg has it — so the
    fallback probes both candidates instead of assuming."""
    if "exe" in _drawtext_cache:
        return _drawtext_cache["exe"]
    import subprocess
    cands: List[str] = []
    try:
        import og_video as _video
        if _video._ffmpeg_exe():
            cands.append(_video._ffmpeg_exe())
    except Exception:
        pass
    sys_ff = shutil.which("ffmpeg") or ""
    if sys_ff and sys_ff not in cands:
        cands.append(sys_ff)
    found = ""
    for exe in cands:
        try:
            r = subprocess.run([exe, "-hide_banner", "-filters"],
                               capture_output=True, text=True,
                               timeout=30)
            if " drawtext " in (r.stdout or ""):
                found = exe
                break
        except Exception:
            continue
    _drawtext_cache["exe"] = found
    return found


def compositing_tool() -> str:
    """'pil' when Pillow imports (primary), else 'ffmpeg' when
    a drawtext-capable ffmpeg AND a font exist, else ''."""
    if _pil():
        return "pil"
    if _drawtext_ffmpeg() and _font_path():
        return "ffmpeg"
    return ""


def _api_key() -> str:
    fn = _dep("get_api_key")
    try:
        return str(fn() or "") if fn else ""
    except Exception:
        return ""


def enabled() -> bool:
    return bool(_api_key()) and bool(compositing_tool())


# --- Usage / caps (app.py /image's own shapes) -----------------------------------


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _usage_get(key: str):
    load, lock = _dep("load_usage"), _dep("usage_lock")
    if load is None or lock is None:
        return None
    with lock:
        store = load()
    entry = store.get(key)
    return dict(entry) if isinstance(entry, dict) else None


def _image_used_week(uid: str) -> int:
    return _tiers.week_used(_usage_get(f"image:{uid}"))


def _image_week_left(uid: str, tier: str) -> int:
    cap_w = _tiers.weekly_cap(tier, "images")
    if cap_w is None:
        return 0
    return max(0, int(cap_w) - _image_used_week(uid))


_local_image_counts: Dict[str, int] = {}


def _consume_image(uid: str) -> int:
    """Record ONE generated image, in the exact entry shape
    app.py's _consume_image writes ("image:<uid>", carry_days
    + note_day substrate). Returns the new weekly total. When
    the usage store is unbound (unit seams), a process-local
    counter keeps the accounting observable."""
    load, save, lock = (_dep("load_usage"), _dep("save_usage"),
                        _dep("usage_lock"))
    if load is None or save is None or lock is None:
        _local_image_counts[uid] = _local_image_counts.get(uid, 0) + 1
        return _local_image_counts[uid]
    key = f"image:{uid}"
    with lock:
        store = load()
        entry = store.get(key)
        if not isinstance(entry, dict) or entry.get("date") != _today():
            old, entry = entry, {"date": _today(), "count": 0}
            _tiers.carry_days(old, entry)
        entry["count"] = int(entry.get("count", 0)) + 1
        _tiers.note_day(entry, _today(), entry["count"])
        store[key] = entry
        save(store)
        return _tiers.week_used(entry)


def _token_balance(uid: str) -> int:
    try:
        import og_tokenpacks as _tp
        return _tp.balance(uid)
    except Exception:
        return 0


def _token_funded(uid: str) -> bool:
    try:
        import og_tokenpacks as _tp
        return _tp.can_afford(uid, _tp.RATES["image"])
    except Exception:
        return False


def _token_debit(uid: str):
    try:
        import og_tokenpacks as _tp
        _tp.debit(uid, _tp.RATES["image"], "image")
    except Exception as e:
        logger.warning(f"Designer token debit failed: {e}")


def _tier_now(uid: str) -> str:
    fn = _dep("get_tier")
    try:
        return str(fn() or "free") if fn else "free"
    except Exception:
        return "free"


# --- Job records (Postgres when OG_MEMORY_DB_URL, else JSON — og_podcast shape) ---

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo
try:
    import psycopg
except Exception:
    psycopg = None

_jobs_lock = threading.Lock()
_jobs_mem: Dict[str, Dict] = {}


def _key(uid: str) -> str:
    return hashlib.sha256(str(uid).encode()).hexdigest()[:32]


def _json_store_path() -> str:
    os.makedirs(STORE_DIR, exist_ok=True)
    return os.path.join(STORE_DIR, "designer_jobs.json")


def _db_connect():
    conn = psycopg.connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_designer_jobs ("
            "id TEXT PRIMARY KEY, owner TEXT, updated DOUBLE PRECISION,"
            " data TEXT)")
    conn.commit()
    return conn


def _store_put(rec: Dict) -> None:
    rec = dict(rec)
    rec["updated"] = time.time()
    with _jobs_lock:
        _jobs_mem[rec["id"]] = rec
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_designer_jobs (id, owner, updated,"
                        " data) VALUES (%s, %s, %s, %s) ON CONFLICT"
                        " (id) DO UPDATE SET owner = EXCLUDED.owner,"
                        " updated = EXCLUDED.updated,"
                        " data = EXCLUDED.data",
                        (rec["id"], rec.get("owner", ""), rec["updated"],
                         json.dumps(rec)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Designer job DB write failed: {e}")
    try:
        path = _json_store_path()
        data = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        data[rec["id"]] = rec
        cutoff = time.time() - _JOB_TTL
        data = {k: v for k, v in data.items()
                if float(v.get("updated", 0)) > cutoff}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        logger.warning(f"Designer job JSON write failed: {e}")


def _store_get(job_id: str) -> Optional[Dict]:
    with _jobs_lock:
        rec = _jobs_mem.get(job_id)
    if rec:
        return dict(rec)
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_designer_jobs WHERE id = %s",
                        (job_id,))
                    row = cur.fetchone()
            if row:
                rec = json.loads(row[0])
                with _jobs_lock:
                    _jobs_mem[job_id] = rec
                return dict(rec)
        except Exception as e:
            logger.warning(f"Designer job DB read failed: {e}")
    try:
        path = _json_store_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            rec = data.get(job_id)
            if rec:
                with _jobs_lock:
                    _jobs_mem[job_id] = rec
                return dict(rec)
    except Exception as e:
        logger.warning(f"Designer job JSON read failed: {e}")
    return None


def _store_latest_for_owner(owner: str) -> Optional[Dict]:
    best = None
    with _jobs_lock:
        cands = [dict(r) for r in _jobs_mem.values()
                 if r.get("owner") == owner]
    try:
        path = _json_store_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            cands.extend(v for v in data.values()
                         if v.get("owner") == owner)
    except Exception as e:
        logger.warning(f"Designer job JSON scan failed: {e}")
    for rec in cands:
        if best is None or float(rec.get("updated", 0)) \
                > float(best.get("updated", 0)):
            best = rec
    return best


def list_jobs_for_owner(uid: str) -> List[Dict]:
    owner = _key(uid)
    found: Dict[str, Dict] = {}
    with _jobs_lock:
        for r in _jobs_mem.values():
            if r.get("owner") == owner:
                found[r["id"]] = dict(r)
    try:
        path = _json_store_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            for v in data.values():
                if v.get("owner") == owner:
                    found[v["id"]] = v
    except Exception as e:
        logger.warning(f"Designer job JSON list failed: {e}")
    return sorted(found.values(),
                  key=lambda r: float(r.get("created", 0)),
                  reverse=True)


def purge_owner(uid: str):
    """Account deletion (Round 36): every design job this
    visitor owns — memory, DB, JSON and build dirs. Locker
    copies are purged by og_store_ready's storage step."""
    if not uid:
        return
    owner = _key(uid)
    ids = [r["id"] for r in list_jobs_for_owner(uid)]
    if ids:
        with _jobs_lock:
            for jid in ids:
                _jobs_mem.pop(jid, None)
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM og_designer_jobs WHERE owner = %s",
                        (owner,))
                conn.commit()
        except Exception as e:
            logger.warning(f"Designer job DB purge failed: {e}")
    try:
        path = _json_store_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            kept = {k: v for k, v in data.items()
                    if v.get("owner") != owner}
            if len(kept) != len(data):
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(kept, f)
    except Exception as e:
        logger.warning(f"Designer job JSON purge failed: {e}")
    for jid in ids:
        try:
            shutil.rmtree(os.path.join(STORE_DIR, jid),
                          ignore_errors=True)
        except Exception:
            pass


# --- Parsing (the visitor's exact words, split from the art subject) ---------------


_DESIGN_VERB = re.compile(
    r"\b(make|create|design|generate|build|draw|render|produce)\b",
    re.I)
_KIND_NOUN = re.compile(r"\b(thumbnail|poster|meme)\b", re.I)
_QUESTION_LEAD = re.compile(
    r"^\s*(what|why|how|when|where|who)\b[^.?!]{0,60}\?", re.I)
_TITLE_MARK = re.compile(
    r"\b(?:title|titled|called|headline)\s*[:\-—]\s*(.+)$", re.I)
_SUB_MARK = re.compile(
    r"\b(?:subtitle|sub\s*title|tagline)\s*[:\-—]\s*(.+)$", re.I)
_TOP_MARK = re.compile(
    r"\btop(?:\s+(?:text|caption|line))?\s*[:=]\s*"
    r"(\"[^\"]+\"|'[^']+'|.+?)"
    r"(?=\s+and\s+bottom\b|\s+bottom\s*(?:text|caption|line)?"
    r"\s*[:=]|\s*/\s*|\s*\|\s*|;|$)", re.I)
_BOT_MARK = re.compile(
    r"\bbottom(?:\s+(?:text|caption|line))?\s*[:=]\s*"
    r"(\"[^\"]+\"|'[^']+'|[^/|;]+)", re.I)
_QUOTED = re.compile(r"[\"“]([^\"”]{2,200})[\"”]|'([^']{2,200})'")


def _unquote(text: str) -> str:
    text = str(text or "").strip()
    if len(text) >= 2 and text[0] in "\"'“" and text[-1] in "\"'”":
        return text[1:-1].strip()
    return text


def _clean_subject(text: str) -> str:
    text = " ".join(str(text or "").split())
    text = re.sub(r"^(for|of|about|showing|with|on)\s+", "", text,
                  flags=re.I)
    text = re.sub(r"[\s,;]+$", "", text)
    text = re.sub(r"\s+(please|for me)\s*$", "", text, flags=re.I)
    return text.strip(" .:!")


def _segments(tail: str) -> List[str]:
    parts = re.split(r"\s*\|\s*|\s+/\s+|\s*;\s*", tail or "")
    return [p.strip() for p in parts if p and p.strip()]


def _split_marks(tail: str, marks) -> (Dict, str):
    """Pull 'title: X' / 'subtitle: X' style marks out of the
    tail (they run to the end or to the next segment break).
    Returns (found, remaining_tail)."""
    found: Dict[str, str] = {}
    rest = tail or ""
    _NEXT_MARK = re.compile(
        r",?\s+(?:subtitle|tagline|title|titled|called|"
        r"headline)\s*[:\-—]\s*", re.I)
    for key, rx in marks:
        m = rx.search(rest)
        if m:
            val = m.group(1)
            val = re.split(r"\s+\|\s+|\s+/\s+|\s*;\s*", val)[0]
            # A value runs until the NEXT mark in the same
            # breath ("title: X, subtitle: Y") — cut there and
            # leave the following mark in `rest` for its own
            # search to find.
            cut = _NEXT_MARK.search(val)
            end = m.end()
            if cut:
                val = val[:cut.start()]
                end = m.start(1) + cut.start()
            found[key] = _unquote(val.strip().rstrip(","))
            rest = (rest[:m.start()] + rest[end:]).strip()
    return found, rest


def _parse_thumbnail_poster(kind: str, tail: str) -> Optional[Dict]:
    marks = [("title", _TITLE_MARK), ("subtitle", _SUB_MARK)] \
        if kind == "poster" else [("title", _TITLE_MARK)]
    found, rest = _split_marks(tail, marks)
    title = found.get("title", "")
    subtitle = found.get("subtitle", "")
    if not title:
        q = _QUOTED.search(rest or "")
        if q:
            title = (q.group(1) or q.group(2) or "").strip()
            rest = (rest[:q.start()] + rest[q.end():]).strip()
    topic = ""
    segs = _segments(rest)
    bare: List[str] = []
    for seg in segs:
        low = seg.lower()
        m = re.match(r"(?:topic|subject|about)\s*[:\-—]\s*(.+)$",
                     seg, re.I)
        if m:
            topic = _clean_subject(m.group(1))
        elif low.startswith(("title", "subtitle", "tagline")):
            continue
        else:
            bare.append(seg)
    if bare:
        if title:
            # Title already given (mark or quotes): the first
            # bare segment is the subject, a second one is a
            # poster subtitle.
            if not topic:
                topic = _clean_subject(bare[0])
            if kind == "poster" and len(bare) > 1 and not subtitle:
                subtitle = _unquote(bare[1])
        elif len(bare) == 1:
            # One bare string is both the words and the subject
            # ("make a thumbnail for Dragon King").
            title = _unquote(_clean_subject(bare[0]))
            topic = topic or _clean_subject(bare[0])
        else:
            # Segmented form ("poster for <topic> | <title> |
            # <subtitle>"): subject first, then the title.
            if not topic:
                topic = _clean_subject(bare[0])
            title = _unquote(_clean_subject(bare[1]))
            if kind == "poster" and len(bare) > 2 and not subtitle:
                subtitle = _unquote(bare[2])
    if not title and topic:
        title = topic
    if not title:
        return {"design_kind": kind, "need": "title",
                "title": "", "subtitle": "", "topic": ""}
    return {"design_kind": kind, "title": title[:160],
            "subtitle": subtitle[:120],
            "topic": (topic or title)[:300]}


def _parse_meme(tail: str, raw: str) -> Optional[Dict]:
    top = bottom = ""
    scrub = raw or ""
    m = _TOP_MARK.search(scrub)
    if m:
        top = _unquote(m.group(1).strip().rstrip(","))
        scrub = (scrub[:m.start()] + scrub[m.end():]).strip()
    m = _BOT_MARK.search(scrub)
    if m:
        bottom = _unquote(m.group(1).strip().rstrip(","))
        scrub = (scrub[:m.start()] + scrub[m.end():]).strip()
    # The idea is what's left of the tail once the caption
    # spans are scrubbed out of the message.
    noun = _KIND_NOUN.search(scrub)
    if noun:
        tail = re.sub(r"^\s*[:\-—]\s*", "", scrub[noun.end():])
    tail = re.sub(r"\s+with\s*$", "", tail or "")
    tail = re.sub(r"\s+and\s*$", "", tail or "")
    idea = ""
    segs = _segments(tail)
    for seg in segs:
        low = seg.lower()
        if re.match(r"(top|bottom)\b", low) and (":" in seg
                                                or "=" in seg):
            continue
        m2 = re.match(r"(?:idea|image|picture|of|about)\s*"
                      r"[:\-—]?\s*(.+)$", seg, re.I)
        idea = _clean_subject(m2.group(1) if m2 else seg)
        if idea:
            break
    if not idea:
        return {"design_kind": "meme", "need": "idea",
                "idea": "", "top": top, "bottom": bottom}
    if not top and not bottom:
        return {"design_kind": "meme", "need": "text",
                "idea": idea, "top": "", "bottom": ""}
    return {"design_kind": "meme", "idea": idea[:300],
            "top": top[:160], "bottom": bottom[:160]}


def parse_design_request(message: str) -> Optional[Dict]:
    """Parse a designer ask into {design_kind, topic/idea,
    title/subtitle/top/bottom} — or a dict with "need" set
    when a required piece is missing — or None when this is
    not a designer ask at all. The composited text fields
    carry the visitor's words VERBATIM."""
    raw = " ".join(str(message or "").split())
    if not raw or _QUESTION_LEAD.search(raw):
        return None
    noun = _KIND_NOUN.search(raw)
    if not noun:
        return None
    kind = noun.group(1).lower()
    verb = _DESIGN_VERB.search(raw[:noun.start() + 1])
    want = re.search(r"\bi want\b|\bi need\b|\bcan you\b|"
                     r"\bcould you\b", raw[:noun.start() + 1], re.I)
    colon_form = re.match(r"^\s*(thumbnail|poster|meme)\s*[:\-]",
                          raw, re.I)
    if not verb and not want and not colon_form:
        return None
    # A song/podcast/video ask that merely MENTIONS a poster
    # or meme in passing is not a design ask.
    head = raw[:noun.start()].lower()
    if re.search(r"\b(song|podcast|video)\b", head) and \
            not re.search(r"\b(thumbnail|poster|meme)\b", head):
        pass  # the noun IS the design kind; fall through
    tail = raw[noun.end():]
    tail = re.sub(r"^\s*[:\-—]\s*", "", tail)
    if kind == "meme":
        return _parse_meme(tail, raw)
    return _parse_thumbnail_poster(kind, tail)


# --- Art prompts (text-free by construction) ---------------------------------------

_STYLE = {
    "thumbnail": (
        "YouTube thumbnail artwork, one bold dramatic focal "
        "subject, high contrast, saturated colors, clean "
        "composition with calm empty space across the bottom "
        "third for a title, no text, no words, no letters, "
        "no numbers, no logos, no watermarks"),
    "poster": (
        "movie poster artwork, dramatic portrait composition, "
        "one striking focal subject, cinematic light, clean "
        "empty space across the top for a title, no text, "
        "no words, no letters, no numbers, no logos, "
        "no watermarks"),
    "meme": (
        "a single expressive scene, clear focal subject, "
        "plain uncluttered space at the very top and very "
        "bottom edges, no text, no words, no letters, "
        "no numbers, no logos, no watermarks"),
}


def build_art_prompt(spec: Dict) -> str:
    """The prompt sent to the image model. Built ONLY from
    the topic / image idea + style shaping — the composited
    text (title / subtitle / captions) is never included, so
    the model cannot garble the visitor's exact words."""
    kind = spec.get("design_kind", "")
    subject = spec.get("topic") or spec.get("idea") or ""
    return f"{subject.strip()} — {_STYLE.get(kind, _STYLE['meme'])}"[:1000]


# --- Generation seam (THE provider seam — stubbed in tests) -------------------------


def _run_coro(factory):
    """Run an async factory to completion from sync code,
    even inside a running event loop (via a helper thread)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    box: Dict = {}

    def _t():
        try:
            box["r"] = asyncio.run(factory())
        except Exception as e:  # noqa: BLE001
            box["e"] = e

    th = threading.Thread(target=_t, daemon=True)
    th.start()
    th.join(timeout=240)
    if "e" in box:
        raise box["e"]
    return box.get("r")


def _generate_image(prompt: str, size: str) -> bytes:
    """ONE base image through the existing image path
    (og_image_gen.openai_generate_image — the gpt-image-1 /
    dall-e-3 chain), at `size`; returns raw image bytes.
    Raises on failure so the caller can answer honestly and
    charge nothing."""
    import og_image_gen as _img
    src, _model = _run_coro(
        lambda: _img.openai_generate_image(_api_key(), prompt,
                                           size=size))
    src = str(src or "")
    if src.startswith("data:"):
        return base64.b64decode(src.split(",", 1)[1])
    if src.startswith("http"):
        import httpx
        with httpx.Client(timeout=60) as client:
            r = client.get(src)
        if r.status_code == 200 and r.content:
            return r.content
        raise RuntimeError("generated image download failed")
    raise RuntimeError("image path returned no image data")


# --- Compositing ---------------------------------------------------------------------


class _DesignError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _cover(img, size):
    """Resize + center-crop `img` (PIL) to exactly `size`."""
    from PIL import Image
    tw, th = size
    scale = max(tw / img.width, th / img.height)
    img = img.resize((max(1, round(img.width * scale)),
                      max(1, round(img.height * scale))),
                     Image.LANCZOS)
    x = (img.width - tw) // 2
    y = (img.height - th) // 2
    return img.crop((x, y, x + tw, y + th))


def _load_font(size: int):
    from PIL import ImageFont
    path = _font_path()
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def _wrap(draw, text: str, font, max_w: int) -> List[str]:
    words = str(text or "").split()
    lines: List[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if not cur or draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _fit(draw, text: str, max_w: int, max_lines: int,
         start: int, floor: int = 20):
    """Shrink the font until `text` wraps into <= max_lines
    lines inside max_w; returns (font, lines, line_height)."""
    size = start
    while size > floor:
        font = _load_font(size)
        lines = _wrap(draw, text, font, max_w)
        if len(lines) <= max_lines and all(
                draw.textlength(ln, font=font) <= max_w
                for ln in lines):
            return font, lines, int(size * 1.18)
        size = int(size * 0.88)
    font = _load_font(floor)
    return font, _wrap(draw, text, font, max_w)[:max_lines], \
        int(floor * 1.18)


def _draw_centered(draw, lines, font, line_h, cx, y, stroke):
    for ln in lines:
        w = draw.textlength(ln, font=font)
        draw.text((cx - w / 2, y), ln, font=font, fill=(255, 255, 255),
                  stroke_width=stroke, stroke_fill=(0, 0, 0))
        y += line_h
    return y


def composite_pil(base: bytes, spec: Dict) -> bytes:
    """Burn the verbatim text into the base image with PIL.
    Raises _DesignError on any failure."""
    from PIL import Image, ImageDraw
    try:
        img = Image.open(io.BytesIO(base)).convert("RGB")
    except Exception as e:
        raise _DesignError("bad_image",
                           f"base image unreadable: {e}")
    kind = spec.get("design_kind", "")
    img = _cover(img, _KIND_OUT.get(kind, (1024, 1024)))
    W, H = img.size
    draw = ImageDraw.Draw(img, "RGBA")
    if kind == "thumbnail":
        title = spec.get("title", "")
        font, lines, lh = _fit(draw, title, int(W * 0.92), 3,
                               start=104)
        band_h = lh * len(lines) + 44
        draw.rectangle([0, H - band_h, W, H],
                       fill=(0, 0, 0, 168))
        _draw_centered(draw, lines, font, lh, W // 2,
                       H - band_h + 22,
                       max(2, font.size // 14))
    elif kind == "poster":
        title = spec.get("title", "")
        sub = spec.get("subtitle", "")
        font, lines, lh = _fit(draw, title, int(W * 0.9), 3,
                               start=110)
        sub_font = sub_lines = None
        sub_lh = 0
        if sub:
            sub_font, sub_lines, sub_lh = _fit(
                draw, sub, int(W * 0.86), 2, start=52)
        total = lh * len(lines) + (sub_lh * len(sub_lines) + 14
                                   if sub_lines else 0) + 56
        draw.rectangle([0, 0, W, total], fill=(0, 0, 0, 150))
        y = _draw_centered(draw, lines, font, lh, W // 2, 26,
                           max(2, font.size // 14))
        if sub_lines:
            _draw_centered(draw, sub_lines, sub_font, sub_lh,
                           W // 2, y + 12,
                           max(2, sub_font.size // 14))
    else:  # meme — top and bottom captions, no band
        for text, at_top in ((spec.get("top", ""), True),
                             (spec.get("bottom", ""), False)):
            if not text:
                continue
            font, lines, lh = _fit(draw, text, int(W * 0.94), 3,
                                   start=76)
            block = lh * len(lines)
            y = 22 if at_top else H - block - 22
            _draw_centered(draw, lines, font, lh, W // 2, y,
                           max(3, font.size // 9))
    out = io.BytesIO()
    img.save(out, format="PNG")
    data = out.getvalue()
    if not data.startswith(b"\x89PNG"):
        raise _DesignError("bad_image", "composite is not a PNG")
    return data


def _wrap_chars(text: str, max_chars: int) -> List[str]:
    words = str(text or "").split()
    lines: List[str] = []
    cur = ""
    for w in words:
        trial = (cur + " " + w).strip()
        if not cur or len(trial) <= max_chars:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def composite_ffmpeg(base: bytes, spec: Dict, workdir: str) -> bytes:
    """Fallback compositor (PIL unavailable): ffmpeg drawtext
    with textfile= (the visitor's words never touch a shell
    or a filter-string escape), one filter per text block.
    Runs the probed drawtext-capable binary directly —
    og_video._run_ffmpeg forces og_video's own exe, whose
    bundled build lacks drawtext here."""
    exe_font = _font_path()
    exe = _drawtext_ffmpeg()
    if not exe_font or not exe:
        raise _DesignError("no_tool",
                           "no compositing tool available")
    os.makedirs(workdir, exist_ok=True)
    base_path = os.path.join(workdir, "base.png")
    with open(base_path, "wb") as f:
        f.write(base)
    # Normalize size first (scale + center crop via a probe of
    # the base dims is overkill: force the kind's frame).
    kind = spec.get("design_kind", "")
    tw, th = _KIND_OUT.get(kind, (1024, 1024))
    filters = [f"scale={tw}:{th}:force_original_aspect_ratio="
               f"increase,crop={tw}:{th}"]
    blocks = []  # (lines, y_expr, font_size, band)
    if kind == "thumbnail":
        size = 84
        lines = _wrap_chars(spec.get("title", ""), 18)
        band = int(size * 1.18) * len(lines) + 44
        filters.append(
            f"drawbox=x=0:y=ih-{band}:w=iw:h={band}:"
            "color=black@0.66:t=fill")
        blocks.append((lines, f"h-{band}+22", size))
    elif kind == "poster":
        size = 88
        lines = _wrap_chars(spec.get("title", ""), 16)
        sub = spec.get("subtitle", "")
        sub_lines = _wrap_chars(sub, 30) if sub else []
        band = int(size * 1.18) * len(lines) + \
            (int(44 * 1.18) * len(sub_lines) + 14
             if sub_lines else 0) + 56
        filters.append(
            f"drawbox=x=0:y=0:w=iw:h={band}:color=black@0.6:t=fill")
        blocks.append((lines, "26", size))
        if sub_lines:
            y = 26 + int(size * 1.18) * len(lines) + 12
            blocks.append((sub_lines, str(y), 44))
    else:
        size = 64
        top = spec.get("top", "")
        bottom = spec.get("bottom", "")
        if top:
            blocks.append((_wrap_chars(top, 20), "22", size))
        if bottom:
            lines = _wrap_chars(bottom, 20)
            y = f"h-{int(size * 1.18) * len(lines) + 22}"
            blocks.append((lines, y, size))
    for i, (lines, y, size) in enumerate(blocks):
        tf = os.path.join(workdir, f"text{i}.txt")
        with open(tf, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        filters.append(
            f"drawtext=fontfile={exe_font}:textfile={tf}:"
            f"fontsize={size}:fontcolor=white:"
            f"borderw={max(2, size // 9)}:bordercolor=black:"
            f"line_spacing={int(size * 0.18)}:"
            f"x=(w-text_w)/2:y={y}")
    out_path = os.path.join(workdir, "final.png")
    import subprocess
    proc = subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", "-y",
         "-i", base_path, "-vf", ",".join(filters),
         "-frames:v", "1", out_path],
        capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise _DesignError(
            "stitch", f"ffmpeg composite failed: "
                      f"{(proc.stderr or '')[:160]}")
    if not os.path.exists(out_path) or os.path.getsize(out_path) < 100:
        raise _DesignError("stitch", "ffmpeg composite failed")
    with open(out_path, "rb") as f:
        data = f.read()
    if not data.startswith(b"\x89PNG"):
        raise _DesignError("bad_image", "composite is not a PNG")
    return data


def composite(base: bytes, spec: Dict, workdir: str) -> (bytes, str):
    """Composite with the best available local tool; returns
    (png_bytes, tool_name)."""
    if _pil():
        return composite_pil(base, spec), "pil"
    return composite_ffmpeg(base, spec, workdir), "ffmpeg"


def image_dims(data: bytes):
    if _pil():
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                return [im.width, im.height]
        except Exception:
            pass
    if len(data) > 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        import struct
        w, h = struct.unpack(">II", data[16:24])
        return [int(w), int(h)]
    return [0, 0]


# --- Locker save (og_storage.save_bytes — the Images path) ---------------------------


class _LockerError(Exception):
    pass


def _locker_save(uid: str, tier: str, name: str, data: bytes) -> Dict:
    import og_storage as _storage
    try:
        return _storage.save_bytes(uid, tier, name, data,
                                   "image/png")
    except Exception as e:
        # StorageError ("dark"/"free"/quota/bad type) and
        # QuotaExceeded carry the honest reason; anything else
        # is a generic locker failure. Either way the design
        # itself is still delivered — save is best-effort on
        # top of delivery, never a silent claim.
        raise _LockerError(str(e) or "locker unavailable")


def _slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(text or "design"))
    return slug.strip("-")[:48].lower() or "design"


# --- Failure hook ---------------------------------------------------------------------


def _record_problem(uid: str, kind: str, err: str, title: str = ""):
    try:
        import og_fixqueue as _fq
        _fq.record_problem(
            uid, "designer", f"designer:{kind}", "design-failed",
            "A design didn't finish",
            f"The design \"{title}\" failed ({kind}): "
            f"{err[:200]}",
            fix={"kind": "owner_steps",
                 "summary": "The design didn't finish. Nothing "
                            "was charged.",
                 "steps": [
                     "Nothing was charged for the failed design.",
                     "Ask OG for the design again; a one-off "
                     "image failure usually clears on a fresh try.",
                 ]})
    except Exception:
        pass


# --- The core (one path for the route, the seam, and tests) -----------------------------


_FAIL_DETAILS = {
    "no_key": "The image lab is down right now (no image key "
              "on OG's side) — no design was made and nothing "
              "was charged.",
    "no_tool": "The design bench is missing its lettering "
               "tool on OG's side — no design was made and "
               "nothing was charged.",
    "gen": "The picture itself didn't come out of the image "
           "lab — no design was made and nothing was charged.",
    "bad_image": "The image lab sent back something that "
                 "isn't a usable picture — no design was "
                 "made and nothing was charged.",
    "stitch": "The lettering wouldn't burn into the picture "
              "— no design was made and nothing was charged.",
}


def make_design(uid: str, tier: str, spec: Dict) -> Dict:
    """Generate + composite + charge (success-only) + store +
    locker-save ONE design. Returns the full outcome dict;
    expected failures are honest dicts, never exceptions."""
    kind = spec.get("design_kind", "")
    out: Dict = {"ok": False, "design_kind": kind,
                 "title": spec.get("title", "") or
                 spec.get("idea", "")}
    if not _api_key():
        out.update({"error_kind": "no_key",
                    "error": _FAIL_DETAILS["no_key"]})
        return out
    if not compositing_tool():
        out.update({"error_kind": "no_tool",
                    "error": _FAIL_DETAILS["no_tool"]})
        return out
    # Weekly fence FIRST (exactly /image): no generation at
    # the wall unless token packs fund it.
    cap_w = _tiers.weekly_cap(tier, "images") or 0
    left = _image_week_left(uid, tier)
    token_funded = left <= 0 and _token_funded(uid)
    if left <= 0 and not token_funded:
        out.update({"ok": False, "capped": True,
                    "weekly_capped": True, "images_left": 0,
                    "error_kind": "capped",
                    "error": _tiers.weekly_image_line(tier),
                    "response": _tiers.weekly_image_line(tier)})
        return out
    job_id = uuid.uuid4().hex[:16]
    job_dir = os.path.join(STORE_DIR, job_id)
    prompt = build_art_prompt(spec)
    try:
        base = _generate_image(prompt, _KIND_SIZE.get(
            kind, "1024x1024"))
    except Exception as e:
        logger.warning(f"Designer generation failed: {e}")
        _record_problem(uid, "gen", str(e), out["title"])
        out.update({"error_kind": "gen",
                    "error": _FAIL_DETAILS["gen"]})
        return out
    try:
        data, tool = composite(base, spec, job_dir)
    except _DesignError as e:
        logger.warning(f"Designer composite failed ({e.kind}): {e}")
        _record_problem(uid, e.kind, str(e), out["title"])
        out.update({"error_kind": e.kind,
                    "error": _FAIL_DETAILS.get(
                        e.kind, _FAIL_DETAILS["stitch"])})
        return out
    except Exception as e:
        logger.warning(f"Designer composite failed: {e}")
        _record_problem(uid, "stitch", str(e), out["title"])
        out.update({"error_kind": "stitch",
                    "error": _FAIL_DETAILS["stitch"]})
        return out
    dims = image_dims(data)
    # SUCCESS — the charge lands now, exactly as /image
    # charges its success (weekly consume, or the token rate
    # when packs funded the wall-crossing).
    if token_funded:
        _token_debit(uid)
    else:
        _consume_image(uid)
    left_after = 0 if token_funded else _image_week_left(uid, tier)
    os.makedirs(job_dir, exist_ok=True)
    final = os.path.join(job_dir, "final.png")
    with open(final, "wb") as f:
        f.write(data)
    words = spec.get("title") or spec.get("top") or \
        spec.get("idea", "")
    name = f"design-{kind}-{_slug(words)}.png"
    rec = {
        "id": job_id, "owner": _key(uid),
        "design_kind": kind,
        "title": spec.get("title", "") or spec.get("idea", ""),
        "topic": spec.get("topic", ""), "idea": spec.get("idea", ""),
        "texts": {"title": spec.get("title", ""),
                  "subtitle": spec.get("subtitle", ""),
                  "top": spec.get("top", ""),
                  "bottom": spec.get("bottom", "")},
        "prompt": prompt, "tool": tool,
        "state": "done", "file": final, "size": len(data),
        "dims": dims, "name": name,
        "charged": True, "token_funded": bool(token_funded),
        "saved": False, "locker_id": "", "locker_name": "",
        "saved_error": "",
        "created": time.time(), "updated": time.time(),
    }
    try:
        saved = _locker_save(uid, tier, name, data)
        rec["saved"] = True
        rec["locker_id"] = saved.get("id", "")
        rec["locker_name"] = saved.get("name", "")
    except _LockerError as e:
        rec["saved_error"] = str(e)[:200]
    except Exception as e:
        logger.warning(f"Designer locker save failed: {e}")
        rec["saved_error"] = "locker unavailable"
    _store_put(rec)
    out.update({
        "ok": True, "id": job_id, "file": final,
        "image": "data:image/png;base64,"
                 + base64.b64encode(data).decode(),
        "size": len(data), "dims": dims, "tool": tool,
        "name": name, "saved": rec["saved"],
        "locker_id": rec["locker_id"],
        "locker_name": rec["locker_name"],
        "saved_error": rec["saved_error"],
        "design_url": f"/designer/download/{job_id}",
        "download_url": (f"/storage/download/{rec['locker_id']}"
                         if rec["saved"] else
                         f"/designer/download/{job_id}"),
        "images_left": left_after,
        "token_balance": _token_balance(uid),
        "charged": True, "token_funded": bool(token_funded),
    })
    return out


# --- Claim-seam results -------------------------------------------------------------------


def _result(tag: str, title: str, body: str, href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body,
             "href": href}]


_LABEL = {"thumbnail": "🖼️ Thumbnail", "poster": "🎬 Poster",
          "meme": "😂 Meme"}


def designer_results(spec: Dict, message: str, uid: str) -> List[Dict]:
    kind = spec.get("design_kind", "")
    label = _LABEL.get(kind, "🎨 Design")
    if spec.get("need") == "title":
        return _result(
            "DESIGNER: NEED-TITLE", f"{label} — needs the words",
            "The visitor wants a " + kind + " but didn't give "
            "the title/topic. Ask them, in persona, in one "
            "line: what's it for, and what should the big "
            "title say? (Offer the form: 'make a " + kind +
            " for <topic>, title: <TITLE>'.) Nothing is "
            "generated or charged yet.")
    if spec.get("need") == "idea":
        return _result(
            "DESIGNER: NEED-IDEA", f"{label} — needs the idea",
            "The visitor wants a meme but didn't say what the "
            "picture should show. Ask them, in persona, in one "
            "line: what's the meme picture of, and what are "
            "the top / bottom captions? Nothing is generated "
            "or charged yet.")
    if spec.get("need") == "text":
        return _result(
            "DESIGNER: NEED-TEXT", f"{label} — needs captions",
            "The visitor wants a meme of "
            f"\"{spec.get('idea', '')}\" but gave no captions. "
            "Ask them, in persona, in one line: what should "
            "the top and bottom text say? Their exact words "
            "get burned in — the form is 'make a meme: "
            "<idea> / top: <text> / bottom: <text>'. Nothing "
            "is generated or charged yet.")
    tier = _tier_now(uid)
    res = make_design(uid, tier, spec)
    if res.get("capped"):
        return _result(
            "DESIGNER: CAPPED", f"{label} — weekly wall",
            "The visitor is AT their weekly picture wall. "
            "Tell them exactly this, in persona, plainly: "
            + _tiers.weekly_image_line(tier)
            + " Nothing was generated.")
    if not res.get("ok"):
        return _result(
            "DESIGNER: FAILED", f"{label} — didn't finish",
            "The design FAILED honestly: "
            + str(res.get("error", ""))
            + " Tell the visitor exactly that, in persona — "
            "no fake design, no fake save claim.")
    texts = spec.get("title") or \
        " / ".join(t for t in (spec.get("top", ""),
                               spec.get("bottom", "")) if t)
    saved_line = (
        f"It is SAVED in their locker as \"{res['name']}\" — "
        "it shows in their Library under Images, and the "
        "locker download is " + res["download_url"] + "."
        if res.get("saved") else
        "It could NOT be locker-saved (" +
        str(res.get("saved_error", "locker unavailable")) +
        ") — do NOT claim it is in the Library; the download "
        "below still works for 24 hours.")
    body = (
        f"DESIGN MADE for real: a {kind} "
        f"({res['dims'][0]}x{res['dims'][1]} PNG, "
        f"{res['size']} bytes, lettering burned in locally "
        f"with {res['tool']}). The words on it, VERBATIM: "
        f"\"{texts}\""
        + (f" with subtitle \"{spec.get('subtitle')}\""
           if spec.get("subtitle") else "")
        + f". The picture itself is on the design card in "
        f"this chat and at {res['design_url']} (owner-only). "
        + saved_line
        + f" One picture was charged against their weekly "
        f"pool ({res['images_left']} left this week"
        + (", token-funded past the wall" if res.get("token_funded")
           else "")
        + "). Tell the visitor, in persona: it's DONE, name "
        "the kind and the exact words on it, and point at the "
        "card / download. Do not invent a different title or "
        "captions.")
    return _result("DESIGNER: DONE", f"{label} — made", body,
                   href=res["design_url"])


# --- Routes ---------------------------------------------------------------------------------


def _route_uid(request: Request) -> str:
    try:
        return request.cookies.get("ogai_uid", "") or ""
    except Exception:
        return ""


def _route_tier(uid: str, request: Request) -> str:
    fn = _dep("tier_of")
    try:
        return str(fn(uid, request) or "free") if fn else "free"
    except Exception:
        return "free"


def _job_view(rec: Optional[Dict]) -> Optional[Dict]:
    if not rec:
        return None
    fresh = time.time() - float(rec.get("updated", 0)) <= _JOB_TTL
    if rec.get("state") == "done" and (not fresh
                                       or not rec.get("file")
                                       or not os.path.exists(
                                           rec.get("file", ""))):
        return None
    return {"id": rec["id"], "design_kind": rec.get("design_kind", ""),
            "created": float(rec.get("created", 0) or 0),
            "title": rec.get("title", ""),
            "texts": rec.get("texts", {}),
            "state": rec.get("state", ""),
            "dims": rec.get("dims", []),
            "size": int(rec.get("size", 0) or 0),
            "tool": rec.get("tool", ""),
            "saved": bool(rec.get("saved", False)),
            "locker_id": rec.get("locker_id", ""),
            "name": rec.get("name", ""),
            "design_url": f"/designer/download/{rec['id']}",
            "download_url": (
                f"/storage/download/{rec['locker_id']}"
                if rec.get("saved") else
                f"/designer/download/{rec['id']}")}


def register_designer_routes(app):
    @app.get("/designer/status")
    def designer_status(request: Request):
        if not enabled():
            return {"enabled": False}
        uid = _route_uid(request)
        if not uid:
            return {"enabled": True, "job": None}
        tier = _route_tier(uid, request)
        return {"enabled": True, "tier": tier,
                "tool": compositing_tool(),
                "cap": int(_tiers.weekly_cap(tier, "images") or 0),
                "used": _image_used_week(uid),
                "left": _image_week_left(uid, tier),
                "job": _job_view(
                    _store_latest_for_owner(_key(uid)))}

    @app.post("/designer/generate")
    async def designer_generate(request: Request):
        """Make ONE design (the /image-shaped door the web
        card calls). Body: {"prompt": <the raw ask>} or
        {"kind": ..., "title"/"topic"/"idea"/"top"/...}."""
        if not enabled():
            return JSONResponse({"ok": False,
                                 "error": "designer is off"},
                                status_code=404)
        uid = _route_uid(request)
        if not uid:
            uid = uuid.uuid4().hex
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        spec = None
        if body.get("kind") in _KIND_SIZE:
            spec = {"design_kind": body["kind"],
                    "title": str(body.get("title", ""))[:160],
                    "subtitle": str(body.get("subtitle", ""))[:120],
                    "topic": str(body.get("topic", ""))[:300],
                    "idea": str(body.get("idea", ""))[:300],
                    "top": str(body.get("top", ""))[:160],
                    "bottom": str(body.get("bottom", ""))[:160]}
            if spec["design_kind"] in ("thumbnail", "poster"):
                if not spec["title"] and spec["topic"]:
                    spec["title"] = spec["topic"]
                if not spec["topic"] and spec["title"]:
                    spec["topic"] = spec["title"]
                if not spec["title"]:
                    spec = None
            elif not spec["idea"] or not (spec["top"]
                                          or spec["bottom"]):
                spec = None
        if spec is None:
            raw = str(body.get("prompt", "") or
                      body.get("message", ""))
            spec = parse_design_request(raw) if raw else None
        if not spec or spec.get("need"):
            return {"ok": False,
                    "error": "Tell me the design: 'make a "
                             "thumbnail for <topic>, title: "
                             "<TITLE>', 'make a poster for "
                             "<topic>' or 'make a meme: <idea> "
                             "/ top: <text> / bottom: <text>'."}
        tier = _route_tier(uid, request)
        res = make_design(uid, tier, spec)
        payload = {k: v for k, v in res.items() if k != "file"}
        if res.get("capped"):
            payload["response"] = res.get("error", "")
        elif res.get("ok"):
            label = _LABEL.get(spec["design_kind"], "🎨 Design")
            payload["response"] = (
                f"{label} — done. It's on the card and saved "
                "to your Library Images."
                if res.get("saved") else
                f"{label} — done. Download it from the card "
                "(the locker couldn't take this one).")
        else:
            payload["response"] = res.get("error", "")
        resp = JSONResponse(payload)
        if not request.cookies.get("ogai_uid"):
            resp.set_cookie("ogai_uid", uid, max_age=60 * 60
                            * 24 * 30, path="/", httponly=True,
                            samesite="lax")
        return resp

    @app.get("/designer/download/{job_id}")
    def designer_download(job_id: str, request: Request):
        uid = _route_uid(request)
        rec = _store_get(job_id) if uid else None
        if not rec or rec.get("owner") != _key(uid) \
                or rec.get("state") != "done" \
                or not rec.get("file") \
                or not os.path.exists(rec.get("file", "")) \
                or time.time() - float(rec.get("updated", 0)) \
                > _JOB_TTL:
            raise HTTPException(status_code=404,
                                detail="Not found")
        return FileResponse(rec["file"], media_type="image/png",
                            filename=rec.get("name")
                            or "design.png")

    @app.post("/designer/save/{job_id}")
    def designer_save(job_id: str, request: Request):
        uid = _route_uid(request)
        rec = _store_get(job_id) if uid else None
        if not rec or rec.get("owner") != _key(uid) \
                or rec.get("state") != "done" \
                or not rec.get("file") \
                or not os.path.exists(rec.get("file", "")):
            raise HTTPException(status_code=404,
                                detail="Not found")
        if rec.get("saved"):
            return {"ok": True, "already": True,
                    "name": rec.get("locker_name", "")}
        tier = _route_tier(uid, request)
        try:
            with open(rec["file"], "rb") as f:
                data = f.read()
            saved = _locker_save(uid, tier,
                                 rec.get("name") or "design.png",
                                 data)
        except _LockerError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(f"Designer locker save failed: {e}")
            return {"ok": False,
                    "error": "The locker wouldn't take it just "
                             "now — the download still works."}
        rec["saved"] = True
        rec["locker_id"] = saved.get("id", "")
        rec["locker_name"] = saved.get("name", "")
        rec["saved_error"] = ""
        _store_put(rec)
        return {"ok": True, "name": saved.get("name", "")}


# --- The seam (installed fail-safe from og_fixqueue, the last install) ---------------------


_slot = {"spec": None, "message": ""}


def install_designer_tools(agent_instance, get_uid):
    """Wrap the agent's detect_intent + web_search hooks so
    designer asks ride the established seam (the og_podcast
    pattern). Persona files never touched."""
    if getattr(agent_instance, "_og_designer_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _slot["spec"] = None
        _slot["message"] = ""
        try:
            uid = get_uid()
            spec = parse_design_request(str(message)) \
                if uid and enabled() else None
            if spec:
                _slot["spec"] = spec
                _slot["message"] = str(message)
                if isinstance(intent, dict):
                    intent["needs_code_generation"] = False
                    if not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
        except Exception as e:
            logger.warning(f"Designer trigger check failed: {e}")
            _slot["spec"] = None
        return intent

    def search_wrapped(query, num_results=5):
        spec = _slot.get("spec")
        message = _slot.get("message", "")
        _slot["spec"] = None
        _slot["message"] = ""
        if spec:
            try:
                results = designer_results(spec, message,
                                           get_uid())
            except Exception as e:
                logger.warning(f"Designer job failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_designer_installed = True
