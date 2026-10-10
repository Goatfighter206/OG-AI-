"""
OG AI file storage locker (Round 21) — the locked storage ladder,
built once Postgres went live (OG_MEMORY_DB_URL, 2026-10-08).

THE LADDER (Brent, locked 2026-10-08 — og_tiers.storage_bytes):
Free gets NO locker (uploads stay temporary/24h, exactly the
Round 6 behavior) · Standard 5 GB · Pro 25 GB · Blue 50 GB ·
Blackout 100 GB. Quotas are enforced server-side by tier on every
save; an upload that would overflow is refused whole, with the
numbers — never a partial save.

HOUSEKEEPING RULE (Brent, 2026-10-08): when a locker nears full
(>=90%), OG warns the visitor and points at the unimportant stuff
(oldest / largest files). OG may delete ONLY what the visitor
explicitly approves: single-file deletes happen on the visitor's
explicit naming of that file; bulk cleanup is propose -> YES ->
delete exactly the proposed list. No silent deletion, no
auto-purge, ever. A declined or expired proposal deletes nothing.

WHERE THE BYTES LIVE: S3-compatible object storage (Cloudflare
R2 / Backblaze B2 / AWS S3 / MinIO), spoken to with stdlib HTTP +
AWS SigV4 signing (hashlib/hmac — no new dependency). The whole
locker ships DARK behind env: OG_STORAGE_ENABLED=true plus
OG_STORAGE_ENDPOINT / OG_STORAGE_BUCKET / OG_STORAGE_ACCESS_KEY /
OG_STORAGE_SECRET_KEY (+ OG_STORAGE_REGION, default "auto";
OG_STORAGE_VIRTUAL_HOST=true for AWS-style bucket hosts). While
dark: the menu item stays hidden, /storage/* routes answer 404,
and locker asks in chat get OG's honest line — paid tiers hear
"storage isn't switched on yet", free visitors get the /pro
upsell. Metadata + quota accounting live in the memory store
(Postgres table og_storage_files when OG_MEMORY_DB_URL is set,
else storage_store.json), one row per file: id, owner
(sha256(ogai_uid)), name, size, content type, kind, uploaded-at,
object key. Object keys are namespaced by the owner hash, and a
visitor can only ever list / read / download / delete their OWN
files — a stranger's file id is a 404 everywhere.

ROUND 6 INTERPLAY: the temp upload flow is untouched. "Save that
file to my locker" right after a temp upload copies it in (for
PDF/TXT the Round 6 store keeps only the extracted text, so the
locker copy holds that text; photos copy byte-for-byte) and
clears the temp slot. Locker files can be read back through the
same excerpt/vision path as Round 6 ("read my file <name>").
Persona files are never touched.
"""

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import og_file_read as _og_files
import og_tiers as _og_tiers

logger = logging.getLogger(__name__)

# --- Config (dark until every piece is set) -----------------------------------

STORAGE_ENDPOINT = os.getenv("OG_STORAGE_ENDPOINT", "").strip()
STORAGE_BUCKET = os.getenv("OG_STORAGE_BUCKET", "").strip()
STORAGE_ACCESS_KEY = os.getenv("OG_STORAGE_ACCESS_KEY", "").strip()
STORAGE_SECRET_KEY = os.getenv("OG_STORAGE_SECRET_KEY", "").strip()
STORAGE_REGION = os.getenv("OG_STORAGE_REGION", "auto").strip() or "auto"
STORAGE_VIRTUAL_HOST = (
    os.getenv("OG_STORAGE_VIRTUAL_HOST", "false").lower() == "true")
STORAGE_ENABLED = (
    os.getenv("OG_STORAGE_ENABLED", "false").lower() == "true"
    and bool(STORAGE_ENDPOINT) and bool(STORAGE_BUCKET)
    and bool(STORAGE_ACCESS_KEY) and bool(STORAGE_SECRET_KEY))

STORE_FILE = os.getenv("OG_STORAGE_STORE_FILE", "storage_store.json")
NEAR_FULL_RATIO = 0.90
_CLEANUP_TTL = 600  # a cleanup proposal lives 10 minutes

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
except Exception:  # psycopg missing — file backend for metadata
    psycopg = None

_store_lock = threading.Lock()


class StorageError(Exception):
    """Carries the in-persona line the visitor should see."""


class QuotaExceeded(StorageError):
    pass


# --- Object storage backends ----------------------------------------------------

class S3Backend:
    """Minimal S3-compatible client: stdlib HTTP + SigV4 signing.

    Path-style addressing by default ({endpoint}/{bucket}/{key}) —
    what R2, B2 and MinIO expect; OG_STORAGE_VIRTUAL_HOST=true
    switches to AWS-style {bucket}.{host}/{key}.
    """

    def __init__(self, endpoint, bucket, access_key, secret_key,
                 region="auto"):
        ep = endpoint.rstrip("/")
        if "://" not in ep:
            ep = "https://" + ep
        self._endpoint = ep
        self._bucket = bucket
        self._ak = access_key
        self._sk = secret_key
        self._region = region

    def _url(self, key):
        qkey = urllib.parse.quote(key, safe="/")
        if STORAGE_VIRTUAL_HOST:
            parts = urllib.parse.urlsplit(self._endpoint)
            return (f"{parts.scheme}://{self._bucket}.{parts.netloc}"
                    f"{parts.path.rstrip('/')}/{qkey}")
        return f"{self._endpoint}/{self._bucket}/{qkey}"

    def _signed_request(self, method, key, data=None, content_type=""):
        url = self._url(key)
        parts = urllib.parse.urlsplit(url)
        now = datetime.now(timezone.utc)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = now.strftime("%Y%m%d")
        payload = data or b""
        payload_hash = hashlib.sha256(payload).hexdigest()
        headers = {
            "host": parts.netloc,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        if content_type:
            headers["content-type"] = content_type
        signed = ";".join(sorted(headers))
        canon_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
        canon = "\n".join([method, parts.path, "", canon_headers,
                           signed, payload_hash])
        scope = f"{date_stamp}/{self._region}/s3/aws4_request"
        to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                             hashlib.sha256(canon.encode()).hexdigest()])

        def _h(key_bytes, msg):
            return hmac.new(key_bytes, msg.encode(), hashlib.sha256).digest()

        k_date = _h(("AWS4" + self._sk).encode(), date_stamp)
        k_region = _h(k_date, self._region)
        k_service = _h(k_region, "s3")
        k_signing = _h(k_service, "aws4_request")
        signature = hmac.new(k_signing, to_sign.encode(),
                             hashlib.sha256).hexdigest()
        headers["Authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self._ak}/{scope}, "
            f"SignedHeaders={signed}, Signature={signature}")
        req = urllib.request.Request(url, data=data, method=method,
                                     headers=headers)
        return req

    def put(self, key, data, content_type="application/octet-stream"):
        req = self._signed_request("PUT", key, data, content_type)
        with urllib.request.urlopen(req, timeout=60) as resp:
            if resp.status not in (200, 201, 204):
                raise StorageError(f"object store PUT failed ({resp.status})")

    def get(self, key):
        req = self._signed_request("GET", key)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise StorageError(f"object store GET failed ({e.code})")

    def delete(self, key):
        req = self._signed_request("DELETE", key)
        with urllib.request.urlopen(req, timeout=60) as resp:
            if resp.status not in (200, 204, 404):
                raise StorageError(
                    f"object store DELETE failed ({resp.status})")


_backend = None


def _default_backend():
    global _backend
    if _backend is None and STORAGE_ENABLED:
        _backend = S3Backend(STORAGE_ENDPOINT, STORAGE_BUCKET,
                             STORAGE_ACCESS_KEY, STORAGE_SECRET_KEY,
                             STORAGE_REGION)
    return _backend


def set_backend(backend):
    """Test seam: inject a fake in-memory object store."""
    global _backend
    _backend = backend


def get_backend():
    return _default_backend()


def storage_enabled() -> bool:
    return bool(STORAGE_ENABLED and get_backend() is not None)


# --- Metadata store (Postgres og_storage_files, else JSON file) -----------------

def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_storage_files ("
            "id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT, "
            "size BIGINT, content_type TEXT, kind TEXT, "
            "uploaded_at DOUBLE PRECISION, s3_key TEXT)")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS og_storage_files_owner "
            "ON og_storage_files (owner)")
    conn.commit()
    return conn


_COLS = ("id", "owner", "name", "size", "content_type", "kind",
         "uploaded_at", "s3_key")


def _row_to_record(row) -> Dict:
    rec = dict(zip(_COLS, row))
    rec["size"] = int(rec.get("size") or 0)
    rec["uploaded_at"] = float(rec.get("uploaded_at") or 0)
    rec["key"] = rec.pop("s3_key")
    return rec


def _load_file_records() -> List[Dict]:
    if os.path.exists(STORE_FILE):
        try:
            with open(STORE_FILE) as f:
                data = json.load(f)
            files = data.get("files") if isinstance(data, dict) else None
            if isinstance(files, dict):
                return list(files.values())
        except Exception as e:
            logger.warning(f"Storage store load failed: {e}")
    return []


def _save_file_records(records: List[Dict]):
    try:
        with open(STORE_FILE, "w") as f:
            json.dump({"files": {r["id"]: r for r in records}}, f)
    except Exception as e:
        logger.warning(f"Storage store save failed: {e}")


def _all_records() -> List[Dict]:
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, owner, name, size, content_type, "
                        "kind, uploaded_at, s3_key FROM og_storage_files")
                    return [_row_to_record(r) for r in cur.fetchall()]
        except Exception as e:
            logger.warning(f"Storage DB load failed, using file: {e}")
    with _store_lock:
        return _load_file_records()


def _insert_record(rec: Dict):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_storage_files (id, owner, name, "
                        "size, content_type, kind, uploaded_at, s3_key) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (rec["id"], rec["owner"], rec["name"], rec["size"],
                         rec["content_type"], rec["kind"],
                         rec["uploaded_at"], rec["key"]))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Storage DB insert failed, using file: {e}")
    with _store_lock:
        records = _load_file_records()
        records.append(rec)
        _save_file_records(records)


def _delete_record_row(fid: str):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM og_storage_files WHERE id=%s", (fid,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Storage DB delete failed, using file: {e}")
    with _store_lock:
        records = [r for r in _load_file_records() if r.get("id") != fid]
        _save_file_records(records)


# --- Ownership + quota ------------------------------------------------------------

def owner_of(uid: str) -> str:
    return hashlib.sha256(str(uid).encode()).hexdigest()


def quota_bytes(tier: str) -> int:
    return _og_tiers.storage_bytes(tier)


def list_records(uid: str) -> List[Dict]:
    if not uid:
        return []
    owner = owner_of(uid)
    mine = [r for r in _all_records() if r.get("owner") == owner]
    return sorted(mine, key=lambda r: r.get("uploaded_at", 0))


def usage_bytes(uid: str) -> int:
    return sum(int(r.get("size") or 0) for r in list_records(uid))


def get_record(uid: str, fid: str) -> Optional[Dict]:
    """The visitor's OWN record by id — a stranger's id is None."""
    if not uid or not fid:
        return None
    owner = owner_of(uid)
    for r in _all_records():
        if r.get("id") == fid:
            return r if r.get("owner") == owner else None
    return None


def find_by_name(uid: str, query: str) -> List[Dict]:
    q = (query or "").strip().lower()
    if not q:
        return []
    mine = list_records(uid)
    exact = [r for r in mine if r["name"].lower() == q]
    if exact:
        return exact
    return [r for r in mine if q in r["name"].lower()
            or r["name"].lower() in q]


def fmt_size(n) -> str:
    n = float(n or 0)
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{int(n)} B"


def _fmt_date(ts) -> str:
    try:
        return datetime.fromtimestamp(
            float(ts), tz=timezone.utc).strftime("%b %d, %Y")
    except Exception:
        return "unknown date"


_MIME_BY_EXT = {
    ".pdf": "application/pdf", ".txt": "text/plain",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def save_bytes(uid: str, tier: str, name: str, data: bytes,
               content_type: str = "") -> Dict:
    """Save bytes into the visitor's locker, enforcing the ladder.

    Refuses (StorageError/QuotaExceeded, nothing written) when the
    locker is dark, the tier has no locker, the kind is unreadable,
    or the file would overflow the quota — with the real numbers.
    """
    if not storage_enabled():
        raise StorageError("dark")
    quota = quota_bytes(tier)
    if quota <= 0:
        raise StorageError("free")
    kind = _og_files._sniff_kind(name, data)
    if not kind:
        raise StorageError(_og_files.BAD_TYPE_LINE)
    used = usage_bytes(uid)
    if used + len(data) > quota:
        raise QuotaExceeded(
            f"That file is {fmt_size(len(data))} but you only got "
            f"{fmt_size(quota - used)} left in your locker "
            f"({fmt_size(used)} of {fmt_size(quota)} used). "
            f"Delete something first — say \"my storage\" and I'll "
            f"point at the old stuff — or step up a plan: "
            f"{_og_tiers.public_pro_url()}")
    owner = owner_of(uid)
    fid = uuid.uuid4().hex
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_",
                  os.path.basename(str(name or "file")))[:80] or "file"
    key = f"{owner[:32]}/{fid}-{safe}"
    backend = get_backend()
    backend.put(key, data,
                content_type or _MIME_BY_EXT.get(
                    os.path.splitext(safe)[1].lower(),
                    "application/octet-stream"))
    rec = {
        "id": fid, "owner": owner,
        "name": os.path.basename(str(name or "file")).strip()[:80] or "file",
        "size": len(data),
        "content_type": content_type or _MIME_BY_EXT.get(
            os.path.splitext(safe)[1].lower(), "application/octet-stream"),
        "kind": kind, "uploaded_at": time.time(), "key": key,
    }
    _insert_record(rec)
    logger.info(f"Locker file stored (kind={kind}, bytes={len(data)})")
    return rec


def save_temp_upload(uid: str, tier: str) -> Dict:
    """Move the visitor's current Round 6 temp upload into the
    locker, then clear the temp slot. PDF/TXT temp files exist
    only as extracted text in the Round 6 store, so the locker
    copy holds that text (same name); photos copy byte-for-byte.
    Raises StorageError(\"no-temp\") when nothing is attached."""
    meta = _og_files.get_meta(uid)
    if not meta:
        raise StorageError("no-temp")
    _, txt_path, img_path = _og_files._paths(uid)
    if meta["kind"] == "image":
        try:
            with open(img_path, "rb") as f:
                data = f.read()
        except Exception:
            raise StorageError("no-temp")
        ctype = meta.get("mime", "image/png")
    else:
        text = _og_files._read_text(uid)
        if not text:
            raise StorageError("no-temp")
        data = text.encode("utf-8")
        ctype = "text/plain"
    rec = save_bytes(uid, tier, meta["name"], data, ctype)
    _og_files.clear_upload(uid)
    return rec


def delete_record(uid: str, fid: str) -> Optional[Dict]:
    """Delete ONE of the visitor's own files (object + metadata).
    A stranger's id — or no such file — returns None (404 logic)."""
    rec = get_record(uid, fid)
    if not rec:
        return None
    backend = get_backend()
    if backend is not None:
        try:
            backend.delete(rec["key"])
        except Exception as e:
            logger.warning(f"Locker object delete failed: {e}")
            raise StorageError(
                "The locker glitched deleting that one — it's still "
                "there. Try again in a bit.")
    _delete_record_row(fid)
    return rec


def locker_bytes(uid: str, rec: Dict) -> Optional[bytes]:
    backend = get_backend()
    if backend is None:
        return None
    return backend.get(rec["key"])


# --- Housekeeping: near-full warning + approval-gated cleanup -------------------

def cleanup_candidates(uid: str) -> List[Dict]:
    """The 'unimportant stuff': the oldest files first, topped up
    with the largest — exactly what OG proposes for cleanup."""
    mine = list_records(uid)
    if not mine:
        return []
    picked: Dict[str, Dict] = {}
    for r in sorted(mine, key=lambda r: r.get("uploaded_at", 0))[:3]:
        picked[r["id"]] = r
    for r in sorted(mine, key=lambda r: -int(r.get("size") or 0)):
        if len(picked) >= 5:
            break
        picked[r["id"]] = r
    return sorted(picked.values(), key=lambda r: r.get("uploaded_at", 0))


def near_full(uid: str, tier: str) -> bool:
    quota = quota_bytes(tier)
    return quota > 0 and usage_bytes(uid) >= NEAR_FULL_RATIO * quota


def _warning_block(uid: str, tier: str) -> str:
    if not near_full(uid, tier):
        return ""
    cands = cleanup_candidates(uid)
    names = ", ".join(
        f"'{r['name']}' ({fmt_size(r['size'])})" for r in cands[:4])
    block = (
        f"\n⚠️ LOCKER NEAR FULL: {fmt_size(usage_bytes(uid))} of "
        f"{fmt_size(quota_bytes(tier))} used (90%+). The oldest/"
        f"biggest stuff OG would point at first: {names}. Tell the "
        f"visitor plainly their locker is almost full and that "
        f"saying \"clean up my storage\" lines those up for deletion "
        f"— NOTHING is deleted without their YES, ever.")
    # Notifications (optional layer): the near-full warning
    # also lands in the visitor's notification center
    # (+ opt-in channels); og_notify dedupes unread
    # storage warnings within 24h. Fail-safe.
    try:
        import og_notify as _notify
        _notify.record(
            uid, "storage_warning", "Your locker is almost full",
            f"Your locker is {fmt_size(usage_bytes(uid))} of "
            f"{fmt_size(quota_bytes(tier))} used (90%+). The "
            f"oldest/biggest stuff: {names}. Say \"clean up "
            f"my storage\" and OG lines them up for your OK — "
            f"nothing is deleted without your YES.",
            target={"view": "library"})
    except Exception:
        pass
    return block


_pending_cleanup: Dict[str, Dict] = {}


def propose_cleanup(uid: str) -> List[Dict]:
    cands = cleanup_candidates(uid)
    if cands:
        _pending_cleanup[owner_of(uid)] = {
            "ids": [r["id"] for r in cands],
            "expires": time.time() + _CLEANUP_TTL}
    return cands


def _pending_for(uid: str) -> Optional[Dict]:
    p = _pending_cleanup.get(owner_of(uid))
    if p and time.time() <= p.get("expires", 0):
        return p
    _pending_cleanup.pop(owner_of(uid), None)
    return None


def approve_cleanup(uid: str) -> List[Dict]:
    p = _pending_for(uid)
    if not p:
        return []
    _pending_cleanup.pop(owner_of(uid), None)
    deleted = []
    for fid in p["ids"]:
        rec = delete_record(uid, fid)
        if rec:
            deleted.append(rec)
    return deleted


def decline_cleanup(uid: str) -> bool:
    had = _pending_for(uid) is not None
    _pending_cleanup.pop(owner_of(uid), None)
    return had


# --- Locker reading (Round 6 excerpt/vision path) --------------------------------

def read_results(uid: str, rec: Dict, question: str,
                 api_key: str = "") -> list:
    """The locker file's context in web_search shape, via the same
    excerpt/vision machinery as Round 6 file reading."""
    data = locker_bytes(uid, rec)
    if data is None:
        return [{"title": "🗄️ Locker — read failed",
                 "body": ("The visitor asked about their locker file "
                          f"\"{rec['name']}\" but OG could not pull it "
                          "from storage just now. Say so plainly, in "
                          "persona, and tell them to try again in a "
                          "bit — do NOT invent its contents."),
                 "href": ""}]
    if rec["kind"] in ("pdf", "text"):
        if rec["kind"] == "pdf":
            try:
                text, _pages = _og_files._extract_pdf_text(data)
            except Exception:
                text = ""
        else:
            text = _og_files._decode_text(data)
        if not text.strip():
            # A locker copy of a temp PDF/TXT holds extracted text
            # under a .pdf/.txt name — decode it as text.
            text = _og_files._decode_text(data)
        meta = {"name": rec["name"], "kind": rec["kind"],
                "chars": len(text), "pages": rec.get("pages", "?"),
                "truncated": False}
        return [_og_files._text_result(meta, text)]
    # image — one vision read, like Round 6.
    import base64
    uri = (f"data:{rec.get('content_type', 'image/png')};base64,"
           + base64.b64encode(data).decode())
    desc = _og_files.vision_read(api_key, uri, question, rec["name"]) or ""
    if not desc:
        desc = ("(The image reader couldn't read this photo just now — "
                "tell the visitor the photo read is down and to try "
                "again in a bit.)")
    return [{"title": f"📎 Vision read of the visitor's locker photo "
                      f"\"{rec['name']}\"",
             "body": ("The visitor is asking about a photo from their "
                      "own file locker. A vision model described it "
                      "as follows — answer from THIS description:\n\n"
                      + desc),
             "href": ""}]


# --- Chat seam ---------------------------------------------------------------------

_deps: Dict = {}


def bind_app(deps):
    _deps.update(deps)


def _tier_now(uid: str) -> str:
    fn = _deps.get("get_tier")
    if fn is not None:
        try:
            return fn() or "free"
        except Exception:
            pass
    return "free"


def _result(tag: str, title: str, body: str, href: str = "") -> list:
    return [{"title": title, "body": f"[{tag}] " + body, "href": href}]


def _dark_or_free_body(tier: str) -> list:
    if tier == "free":
        body = (
            "The visitor asked about the file locker, but the FREE "
            "plan has no locker — uploads on free stay temporary "
            "(24 hours, one at a time). Tell them plainly, in "
            f"persona: storage starts at Standard (5 GB) — "
            f"{_og_tiers.public_pro_url()} — and climbs to 100 GB "
            "at the top. Do NOT claim any file was saved to a "
            "locker.")
        return _result("STORAGE: FREE", "🗄️ Storage — free plan", body)
    body = (
        "The visitor asked about their file locker. HONEST STATE: "
        "the locker is built but NOT switched on yet on this "
        "server (the storage backend hasn't been activated). Tell "
        "them plainly, in persona: file storage isn't switched on "
        "yet — their uploads still work as temporary files OG can "
        "read for 24 hours, and the locker opens the moment the "
        "boss flips it on. Do NOT claim anything was saved "
        "long-term.")
    return _result("STORAGE: DARK", "🗄️ Storage — not live yet", body)


def _usage_body(uid: str, tier: str, extra: str = "") -> list:
    quota = quota_bytes(tier)
    used = usage_bytes(uid)
    mine = list_records(uid)
    pct = int(round(100.0 * used / quota)) if quota else 0
    lines = [f"- '{r['name']}' — {fmt_size(r['size'])}, "
             f"{_fmt_date(r.get('uploaded_at'))}"
             for r in mine]
    listing = "\n".join(lines) if lines else "(locker is empty)"
    body = (
        "The visitor asked about their file locker. REAL DATA — "
        "answer with exactly these facts, in persona, no invented "
        f"files:\nUsed: {fmt_size(used)} of {fmt_size(quota)} "
        f"({pct}%).\nFiles ({len(mine)}):\n{listing}\n"
        "They can download any file from the locker links, say "
        "\"delete <name>\" to drop one, or \"read my file <name>\" "
        "to ask OG about one." + extra + _warning_block(uid, tier))
    return _result("STORAGE: LIST", "🗄️ My storage", body)


_YES = {"yes", "yeah", "yep", "yup", "yes please", "do it", "go",
        "go ahead", "confirm", "ok", "okay", "sure", "delete them",
        "delete it all", "yes delete", "yes delete them"}
_NO = {"no", "nope", "nah", "don't", "do not", "cancel", "stop",
       "never mind", "nevermind", "keep them", "keep it", "no way"}


def _other_pending(uid: str) -> bool:
    """Another module's approval flow is mid-flight — never steal
    its YES/NO (ordering previews, trade previews)."""
    try:
        import og_ordering
        if og_ordering._get_pending(uid):
            return True
    except Exception:
        pass
    try:
        import og_trading
        if og_trading._get_pending(uid):
            return True
    except Exception:
        pass
    return False


def _claim_job(message: str, uid: str) -> Optional[Dict]:
    if not uid or not message:
        return None
    text = " ".join(str(message).strip().split())
    low = text.lower().rstrip("!.")
    # Approval / decline of a proposed cleanup comes first — but
    # only while a proposal is actually pending for THIS visitor.
    if _pending_for(uid) and not _other_pending(uid):
        if low in _YES:
            return {"kind": "cleanup_approve"}
        if low in _NO:
            return {"kind": "cleanup_decline"}
    if re.search(r"\b(clean ?up|clear out)\b.*\b(storage|locker|old)\b", low) \
            or low in ("delete my old stuff", "delete the old stuff",
                       "delete my unimportant files",
                       "clean up my storage", "clean up my locker"):
        return {"kind": "cleanup_propose"}
    if re.search(r"\bsave\b.*\bto my (locker|storage)\b", low) \
            or low in ("save that to my locker", "save it to my locker",
                       "save that file to my locker",
                       "save this file to my locker"):
        return {"kind": "save_temp"}
    m = re.search(
        r"\b(?:delete|remove|drop)\b\s+(?:my file\s+|the file\s+|file\s+)?"
        r"(.+?)(?:\s+from my (?:locker|storage))?$", low)
    if m and ("locker" in low or "storage" in low or "my file" in low):
        name = m.group(1).strip(" \"'")
        if name and name not in ("my old stuff", "the old stuff"):
            return {"kind": "delete", "name": name}
    m = re.search(
        r"\b(?:read|open|summarize|summarise|show|describe|explain)\b"
        r".*?\bmy file\b\s*(.+)?$", low)
    if m or re.search(r"\bfrom my (locker|storage)\b", low) and \
            re.search(r"\b(read|open|show|summarize)\b", low):
        name = (m.group(1) or "").strip(" \"'") if m else ""
        name = re.sub(r"\s+from my (locker|storage)$", "", name).strip()
        return {"kind": "read", "name": name}
    if re.search(r"\b(my storage|my locker|my files|storage usage|"
                 r"what'?s in my (locker|storage))\b", low):
        return {"kind": "list"}
    return None


def storage_results(job, message, uid) -> Optional[list]:
    if not job or not uid:
        return None
    tier = _tier_now(uid)
    kind = job.get("kind", "")
    if not storage_enabled():
        return _dark_or_free_body(tier)
    if quota_bytes(tier) <= 0:
        return _dark_or_free_body("free")
    if kind == "list":
        return _usage_body(uid, tier)
    if kind == "save_temp":
        try:
            rec = save_temp_upload(uid, tier)
        except QuotaExceeded as e:
            return _result("STORAGE: QUOTA", "🗄️ Storage — no room",
                           "The visitor tried to save their attached "
                           f"file to the locker and it does NOT fit. "
                           f"Relay this exactly, in persona: {e}")
        except StorageError as e:
            if str(e) == "no-temp":
                body = ("The visitor asked to save a file to their "
                        "locker but NOTHING is attached right now. "
                        "Tell them plainly, in persona: upload a "
                        "file first (📎 Upload a file), then say "
                        "\"save that to my locker\".")
                return _result("STORAGE: NO-TEMP", "🗄️ Storage", body)
            return _result("STORAGE: ERROR", "🗄️ Storage",
                           "Saving to the locker failed. Tell the "
                           f"visitor plainly, in persona: {e}")
        extra = (f"\nJUST SAVED: '{rec['name']}' "
                 f"({fmt_size(rec['size'])}) is now in their "
                 "locker for good — the temporary copy was cleared. "
                 "Confirm it landed, with the size.")
        return _usage_body(uid, tier, extra)
    if kind == "read":
        name = job.get("name", "")
        matches = find_by_name(uid, name) if name else list_records(uid)
        if not matches:
            body = ("The visitor asked OG to read a locker file "
                    f"(\"{name}\") but their locker has NO file by "
                    "that name. Tell them plainly, in persona, and "
                    "list what IS in there: "
                    + (", ".join(f"'{r['name']}'"
                                  for r in list_records(uid))
                       or "(locker is empty)")
                    + ". Do NOT invent file contents.")
            return _result("STORAGE: READ-MISS", "🗄️ Storage", body)
        if len(matches) > 1:
            body = ("The visitor's locker has MORE THAN ONE file "
                    "matching that name: "
                    + ", ".join(f"'{r['name']}'" for r in matches)
                    + ". Ask which one they mean — read NOTHING "
                    "yet, invent NOTHING.")
            return _result("STORAGE: READ-AMBIG", "🗄️ Storage", body)
        api_key = ""
        fn = _deps.get("get_api_key")
        if fn is not None:
            try:
                api_key = fn() or ""
            except Exception:
                api_key = ""
        return read_results(uid, matches[0], message, api_key)
    if kind == "delete":
        name = job.get("name", "")
        matches = find_by_name(uid, name)
        if not matches:
            body = ("The visitor asked to delete a locker file "
                    f"(\"{name}\") but there is NO file by that "
                    "name in their locker. Nothing was deleted. "
                    "Tell them plainly, in persona, and list what "
                    "IS in there: "
                    + (", ".join(f"'{r['name']}'"
                                  for r in list_records(uid))
                       or "(locker is empty)") + ".")
            return _result("STORAGE: DELETE-MISS", "🗄️ Storage", body)
        if len(matches) > 1:
            body = ("MORE THAN ONE locker file matches that name: "
                    + ", ".join(f"'{r['name']}'" for r in matches)
                    + ". NOTHING was deleted — ask which one, "
                    "exactly. Never guess at a deletion.")
            return _result("STORAGE: DELETE-AMBIG", "🗄️ Storage", body)
        rec = delete_record(uid, matches[0]["id"])
        extra = (f"\nJUST DELETED (the visitor named this file "
                 f"explicitly): '{rec['name']}' "
                 f"({fmt_size(rec['size'])}) is gone from their "
                 "locker. Confirm the deletion and the freed space.")
        return _usage_body(uid, tier, extra)
    if kind == "cleanup_propose":
        cands = propose_cleanup(uid)
        if not cands:
            body = ("The visitor asked to clean up their locker "
                    "but it is EMPTY — nothing to clean. Tell them "
                    "plainly, in persona.")
            return _result("STORAGE: CLEAN-EMPTY", "🗄️ Storage", body)
        total = sum(int(r["size"]) for r in cands)
        lines = "\n".join(
            f"- '{r['name']}' — {fmt_size(r['size'])}, "
            f"{_fmt_date(r.get('uploaded_at'))}" for r in cands)
        body = (
            "HOUSEKEEPING — the visitor asked OG to clean up their "
            "locker. OG proposes deleting EXACTLY these oldest/"
            "biggest files (nothing else, nothing yet):\n"
            f"{lines}\nThat frees {fmt_size(total)}. Present the "
            "list plainly, in persona, and ask: reply YES to "
            "delete exactly these, or NO to keep everything. "
            "NOTHING is deleted unless they say YES — never imply "
            "otherwise. The proposal expires in 10 minutes.")
        return _result("STORAGE: CLEANUP-PROPOSE", "🗄️ Cleanup", body)
    if kind == "cleanup_approve":
        deleted = approve_cleanup(uid)
        if not deleted:
            return _usage_body(uid, tier)
        names = ", ".join(f"'{r['name']}'" for r in deleted)
        freed = fmt_size(sum(int(r["size"]) for r in deleted))
        extra = (f"\nCLEANUP DONE (the visitor approved this exact "
                 f"list): deleted {names} — freed {freed}. Confirm "
                 "exactly what went, nothing more.")
        return _usage_body(uid, tier, extra)
    if kind == "cleanup_decline":
        decline_cleanup(uid)
        body = ("The visitor DECLINED the proposed locker cleanup. "
                "NOTHING was deleted — every file stays. Confirm "
                "plainly, in persona: cleanup cancelled, locker "
                "untouched.")
        return _result("STORAGE: CLEANUP-DECLINED", "🗄️ Cleanup", body)
    return None


_pending = {"job": None, "message": ""}


def install_storage_tools(agent_instance, get_uid):
    """Wrap the agent's (already fully wrapped) detect_intent +
    web_search hooks LAST, so locker asks ride the established
    seam. Non-locker messages pass through untouched; a locker
    read returns the file's own results, everything else returns
    one grounded instruction result for the persona to voice.
    Persona files never touched."""
    if getattr(agent_instance, "_og_storage_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["job"] = None
        _pending["message"] = ""
        try:
            uid = get_uid()
            job = _claim_job(str(message), uid) if uid else None
            if job:
                _pending["job"] = job
                _pending["message"] = str(message)
                if isinstance(intent, dict):
                    intent["needs_code_generation"] = False
                    if not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
        except Exception as e:
            logger.warning(f"Storage trigger check failed: {e}")
            _pending["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _pending.get("job")
        message = _pending.get("message", "")
        _pending["job"] = None
        _pending["message"] = ""
        if job:
            try:
                results = storage_results(job, message, get_uid())
            except Exception as e:
                logger.warning(f"Storage job failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_storage_installed = True


# --- Routes -------------------------------------------------------------------------

def register_storage_routes(app):
    """Mount /storage/status, /storage/upload, /storage/save-temp,
    /storage/download/<id> and /storage/delete/<id>. All answer
    404 while the locker is dark, so nothing about it is
    discoverable on the live site until Brent enables it."""
    from fastapi import Request as _Req
    from fastapi.responses import JSONResponse, Response

    def _uid_of(raw_request):
        return raw_request.cookies.get("ogai_uid")

    def _tier_of(raw_request, uid):
        fn = _deps.get("tier_of")
        if fn is not None:
            try:
                return fn(uid, raw_request)
            except Exception:
                pass
        return _og_tiers.tier_of(raw_request.cookies, uid or "")

    @app.get("/storage/status")
    async def storage_status(raw_request: _Req):
        if not storage_enabled():
            raise _not_found()
        uid = _uid_of(raw_request)
        tier = _tier_of(raw_request, uid)
        quota = quota_bytes(tier)
        used = usage_bytes(uid) if uid else 0
        files = [{"id": r["id"], "name": r["name"],
                  "size": int(r["size"]),
                  "date": _fmt_date(r.get("uploaded_at")),
                  "download": f"/storage/download/{r['id']}"}
                 for r in (list_records(uid) if uid else [])]
        return JSONResponse(content={
            "enabled": True, "tier": tier, "quota": quota,
            "used": used, "quota_human": fmt_size(quota),
            "used_human": fmt_size(used),
            "near_full": bool(quota and used >= NEAR_FULL_RATIO * quota),
            "files": files})

    @app.post("/storage/upload")
    async def storage_upload(raw_request: _Req):
        """Direct-to-locker upload (same kinds/size rules as the
        Round 6 temp upload, plus the locker quota)."""
        if not storage_enabled():
            raise _not_found()
        uid = _uid_of(raw_request) or uuid.uuid4().hex
        tier = _tier_of(raw_request, uid)
        if quota_bytes(tier) <= 0:
            return JSONResponse(content={
                "ok": False, "response":
                "Yo, the free plan ain't got a locker — storage "
                f"starts at Standard (5 GB): "
                f"{_og_tiers.public_pro_url()}"}, status_code=403)
        try:
            form = await raw_request.form()
        except Exception:
            form = {}
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return JSONResponse(content={
                "ok": False, "response": _og_files.BAD_TYPE_LINE})
        raw = await upload.read(_og_files._READ_CAP)
        if len(raw) > _og_files._max_bytes(tier):
            return JSONResponse(content={
                "ok": False,
                "response": f"Yo, that file's too heavy — I max out "
                            f"at {_og_files._max_bytes(tier) // (1024 * 1024)} "
                            f"MB on your plan. Shrink it down and "
                            f"slide it again."})
        try:
            rec = save_bytes(uid, tier,
                             getattr(upload, "filename", "") or "file",
                             raw)
        except QuotaExceeded as e:
            return JSONResponse(content={"ok": False, "quota": True,
                                         "response": str(e)})
        except StorageError as e:
            return JSONResponse(content={"ok": False,
                                         "response": str(e)})
        return JSONResponse(content={
            "ok": True, "file": {"id": rec["id"], "name": rec["name"],
                                 "size": int(rec["size"])},
            "used": usage_bytes(uid), "quota": quota_bytes(tier),
            "near_full": near_full(uid, tier),
            "response": f"Locked in — '{rec['name']}' "
                        f"({fmt_size(rec['size'])}) is in your "
                        f"locker for good. 🗄️"})

    @app.post("/storage/save-temp")
    async def storage_save_temp(raw_request: _Req):
        """Copy the current temp upload into the locker."""
        if not storage_enabled():
            raise _not_found()
        uid = _uid_of(raw_request)
        if not uid:
            return JSONResponse(content={
                "ok": False,
                "response": "Ain't nothin' attached right now, fam."})
        tier = _tier_of(raw_request, uid)
        if quota_bytes(tier) <= 0:
            return JSONResponse(content={
                "ok": False, "response":
                "Yo, the free plan ain't got a locker — storage "
                f"starts at Standard (5 GB): "
                f"{_og_tiers.public_pro_url()}"}, status_code=403)
        try:
            rec = save_temp_upload(uid, tier)
        except QuotaExceeded as e:
            return JSONResponse(content={"ok": False, "quota": True,
                                         "response": str(e)})
        except StorageError as e:
            if str(e) == "no-temp":
                return JSONResponse(content={
                    "ok": False,
                    "response": "Ain't nothin' attached right now, "
                                "fam."})
            return JSONResponse(content={"ok": False,
                                         "response": str(e)})
        return JSONResponse(content={
            "ok": True, "file": {"id": rec["id"], "name": rec["name"],
                                 "size": int(rec["size"])},
            "used": usage_bytes(uid), "quota": quota_bytes(tier),
            "near_full": near_full(uid, tier),
            "response": f"Locked in — '{rec['name']}' "
                        f"({fmt_size(rec['size'])}) is in your "
                        f"locker for good. 🗄️"})

    @app.get("/storage/download/{fid}")
    async def storage_download(fid: str, raw_request: _Req):
        if not storage_enabled():
            raise _not_found()
        uid = _uid_of(raw_request)
        rec = get_record(uid, fid) if uid else None
        if not rec:
            raise _not_found()
        data = locker_bytes(uid, rec)
        if data is None:
            raise _not_found()
        return Response(
            content=data,
            media_type=rec.get("content_type")
            or "application/octet-stream",
            headers={"Content-Disposition":
                     f'attachment; filename="{rec["name"]}"'})

    @app.post("/storage/delete/{fid}")
    async def storage_delete(fid: str, raw_request: _Req):
        if not storage_enabled():
            raise _not_found()
        uid = _uid_of(raw_request)
        rec = get_record(uid, fid) if uid else None
        if not rec:
            raise _not_found()
        try:
            gone = delete_record(uid, fid)
        except StorageError as e:
            return JSONResponse(content={"ok": False,
                                         "response": str(e)})
        return JSONResponse(content={
            "ok": True,
            "response": f"Done — '{gone['name']}' is out of your "
                        f"locker. 🗄️✌️",
            "used": usage_bytes(uid), "quota": quota_bytes(
                _tier_of(raw_request, uid))})


def _not_found():
    from fastapi import HTTPException
    return HTTPException(status_code=404, detail="Not found")
