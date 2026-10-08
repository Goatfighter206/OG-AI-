"""
OG file reading helpers (Round 6).

The self-contained half of the file-reading feature: per-visitor file
storage (one active file per visitor, keyed by the ogai_uid cookie value),
text extraction (PDF via pypdf, plain text by decoding), image keeping,
the vision read for photos (OpenAI multimodal call on the existing key),
and the reference heuristic that decides whether a chat message is about
the attached file. The endpoints, the per-visitor daily upload caps, the
history notes, and the chat-context seam live in app.py with the other
identity/money layers.

Privacy: file contents are NEVER logged — only kind/size metadata. Files
live on local disk (ephemeral on Render, which matches the retention:
a file is kept at most 24 hours, and only until the visitor removes it,
replaces it, or it expires). Nothing is shared across visitors: every
read/write is namespaced by a hash of the visitor's own uid.
"""

import base64
import hashlib
import json
import logging
import os
import re
import time

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = int(os.getenv("OG_UPLOAD_MAX_BYTES", str(8 * 1024 * 1024)))
TEXT_STORE_CHARS = 60000      # extracted text kept per file
TEXT_CONTEXT_CHARS = 12000    # excerpt handed to the model per question
RETENTION_SECONDS = 24 * 60 * 60
UPLOAD_DIR = os.getenv("OG_UPLOAD_DIR", "uploads")
VISION_MODEL = os.getenv(
    "OG_VISION_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))

_EXT_KIND = {
    ".pdf": "pdf",
    ".txt": "text",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
}
_IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".webp": "image/webp",
}

TOO_BIG_LINE = (
    "Yo, that file's too heavy — I max out at 8 MB. "
    "Shrink it down and slide it again."
)
BAD_TYPE_LINE = (
    "Yo, I can't read that kind of file — I take PDF, TXT, PNG, JPG "
    "or WEBP. Slide me one of those."
)
NO_TEXT_LINE = (
    "Yo, that PDF's got no readable text in it — looks like scanned "
    "pictures of pages. I can't read that one yet, fam."
)
GARBLED_LINE = (
    "Yo, that file came through garbled — I couldn't read a word of it. "
    "Try sliding it again."
)


class UploadRejected(Exception):
    """Carries the in-persona line the visitor should see."""


# --------------------------------------------------------------------------
# Storage (one active file per visitor, on local disk)
# --------------------------------------------------------------------------

def _key(uid: str) -> str:
    return hashlib.sha256(str(uid).encode()).hexdigest()[:32]


def _paths(uid: str):
    base = os.path.join(UPLOAD_DIR, _key(uid))
    return base + ".json", base + ".txt", base + ".img"


def _write_meta(uid: str, meta: dict):
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    meta_path, _, _ = _paths(uid)
    tmp = meta_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f)
    os.replace(tmp, meta_path)


def get_meta(uid: str):
    """The visitor's active file metadata, or None (expired files are
    purged on read). Never returns another visitor's file: the path is
    a hash of this uid alone."""
    if not uid:
        return None
    meta_path, _, _ = _paths(uid)
    try:
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:
        return None
    if time.time() - float(meta.get("uploaded_at", 0)) > RETENTION_SECONDS:
        clear_upload(uid)
        return None
    return meta


def clear_upload(uid: str) -> bool:
    """Delete the visitor's file (remove action / expiry / replacement).
    Returns True when something was attached."""
    if not uid:
        return False
    had = False
    for path in _paths(uid):
        try:
            os.remove(path)
            had = True
        except OSError:
            pass
    return had


def _sniff_kind(filename: str, data: bytes) -> str:
    """Kind from magic bytes first (a renamed file can't lie), then the
    extension. '' when neither knows it."""
    if data[:5] == b"%PDF-":
        return "pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image"
    if data[:3] == b"\xff\xd8\xff":
        return "image"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image"
    ext = os.path.splitext(filename or "")[1].lower()
    return _EXT_KIND.get(ext, "")


def _extract_pdf_text(data: bytes):
    """(text, page_count) from PDF bytes; text carries [Page N] markers
    so 'what does page 2 say' has something to point at."""
    try:
        from pypdf import PdfReader
    except Exception as e:
        raise UploadRejected(GARBLED_LINE) from e
    import io
    try:
        reader = PdfReader(io.BytesIO(data))
        parts = []
        for i, page in enumerate(reader.pages, 1):
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                parts.append("")
    except Exception as e:
        raise UploadRejected(GARBLED_LINE) from e
    pages = len(parts)
    text = "\n\n".join(
        f"[Page {i}]\n{part.strip()}" for i, part in enumerate(parts, 1))
    return text.strip(), pages


def _decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def save_upload(uid: str, filename: str, data: bytes) -> dict:
    """Validate, extract and store the visitor's one active file
    (replacing any previous one). Returns the stored metadata.
    Raises UploadRejected with the in-persona line for every expected
    failure — never a bare exception for bad input."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadRejected(TOO_BIG_LINE)
    if not data:
        raise UploadRejected(GARBLED_LINE)
    kind = _sniff_kind(filename, data)
    if not kind:
        raise UploadRejected(BAD_TYPE_LINE)
    name = os.path.basename(str(filename or "file")).strip()[:80] or "file"
    meta = {
        "name": name, "kind": kind, "size": len(data),
        "uploaded_at": time.time(),
    }
    clear_upload(uid)
    meta_path, txt_path, img_path = _paths(uid)
    if kind in ("pdf", "text"):
        if kind == "pdf":
            text, pages = _extract_pdf_text(data)
            meta["pages"] = pages
        else:
            text = _decode_text(data)
        text = text.strip()
        if not text:
            raise UploadRejected(NO_TEXT_LINE if kind == "pdf"
                                 else GARBLED_LINE)
        meta["chars"] = len(text)
        meta["truncated"] = len(text) > TEXT_STORE_CHARS
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(text[:TEXT_STORE_CHARS])
    else:  # image — kept as-is; read later, only when asked about
        ext = os.path.splitext(name)[1].lower()
        meta["mime"] = _IMAGE_MIME.get(ext, "image/png")
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            meta["mime"] = "image/png"
        elif data[:3] == b"\xff\xd8\xff":
            meta["mime"] = "image/jpeg"
        elif data[:4] == b"RIFF":
            meta["mime"] = "image/webp"
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        with open(img_path, "wb") as f:
            f.write(data)
    _write_meta(uid, meta)
    logger.info(f"File stored for visitor (kind={kind}, "
                f"bytes={len(data)})")
    return meta


def _read_text(uid: str) -> str:
    _, txt_path, _ = _paths(uid)
    try:
        with open(txt_path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _image_data_uri(uid: str, meta: dict) -> str:
    _, _, img_path = _paths(uid)
    try:
        with open(img_path, "rb") as f:
            raw = f.read()
    except Exception:
        return ""
    if not raw:
        return ""
    return (f"data:{meta.get('mime', 'image/png')};base64,"
            + base64.b64encode(raw).decode())


def describe_meta(meta: dict) -> str:
    """Short human descriptor for the chip / status payload."""
    if not meta:
        return ""
    if meta["kind"] == "pdf":
        pages = meta.get("pages", "?")
        return f"PDF · {pages} page{'s' if pages != 1 else ''}"
    if meta["kind"] == "text":
        return f"TXT · {meta.get('chars', 0):,} characters"
    size = meta.get("size", 0)
    human = (f"{size / (1024 * 1024):.1f} MB" if size >= 1024 * 1024
             else f"{max(1, size // 1024)} KB")
    return f"Photo · {human}"


def public_meta(meta: dict) -> dict:
    """The file descriptor the endpoints hand the page."""
    return {"name": meta["name"], "kind": meta["kind"],
            "detail": describe_meta(meta)}


def ok_line(meta: dict) -> str:
    if meta["kind"] == "image":
        return (f"Got it — '{meta['name']}' is loaded up. 📎 "
                "Ask me what it shows, what's in it, whatever you need.")
    return (f"Got it — '{meta['name']}' is loaded up. 📎 "
            "Ask me anything about it: a summary, the details, "
            "a specific page — I got it all right here.")


# --------------------------------------------------------------------------
# Is the visitor talking about the file?
# --------------------------------------------------------------------------

_FILE_REF_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\b(file|files|pdf|document|documents|doc|docs|upload|uploaded|"
    r"attached|attachment|photo|picture|image|pic|screenshot|page|pages|"
    r"paper|letter|receipt|resume|article|story|essay|contract|notes)\b",
    r"\b(summarize|summarise|summary|describe|explain|read|quote)\b"
    r"[^.!?\n]{0,30}\b(this|it|that|the|my)\b",
    r"\bwhat('s| is| does| did| was)\b[^.!?\n]{0,24}"
    r"\b(say|says|said|in|on|about|mean|show|happen)\b",
    r"\b(according to|based on|from the|in the|on the)\b"
    r"[^.!?\n]{0,20}\b(file|doc|document|photo|image|pdf|page|text)\b",
    r"^\s*(summarize|summarise|describe|explain|read|tl;dr)\b",
))


def message_references_file(message: str) -> bool:
    """App-layer heuristic: is this message about the attached file?
    Tuned generous — while a file is attached, file-ish questions route
    to it; the app layer checks data/lookup intent first, so a question
    that's clearly about something else (weather, scores) still runs
    its normal route with the file riding along as extra context."""
    if not message:
        return False
    if any(p.search(message) for p in _FILE_REF_PATTERNS):
        return True
    # Short bare questions ("what is this?", "and page 2?") right after
    # an upload almost always mean the file.
    text = message.strip()
    return len(text) <= 60 and text.endswith("?") and bool(
        re.search(r"\b(this|it|that)\b", text, re.IGNORECASE))


# --------------------------------------------------------------------------
# The chat seam (installed from app.py — kept here to keep app.py lean)
# --------------------------------------------------------------------------

_inject = {"uid": "", "forced": False}


def install_file_tools(agent_instance, get_uid, get_api_key):
    """Wrap the agent's (already lookup-wrapped) detect_intent +
    web_search hooks so a message about the visitor's attached file
    pulls the file's context into the exchange, in the same
    {title, body, href} shape a web lookup returns — so BOTH chat paths
    (classic process_message and the streaming twin in app.py) format
    it into model context exactly like search results. Modes:
    - Forced (the question is only about the file): the wrapper returns
      the file results alone — the real search never runs, so file
      questions spend none of the visitor's lookup budget.
    - Append (the question also triggered a real lookup): the file
      result goes first, so it survives the [:3] result formatting,
      with the real results behind it.
    get_uid / get_api_key are zero-arg callables supplied by app.py
    (its per-exchange current-visitor slot and the OpenAI key lookup),
    so this module never imports app.py. Persona files untouched."""
    if getattr(agent_instance, "_og_file_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _inject["uid"] = ""
        _inject["forced"] = False
        try:
            uid = get_uid()
            if (uid and get_meta(uid)
                    and message_references_file(str(message))):
                already = isinstance(intent, dict) \
                    and bool(intent.get("needs_web_search"))
                _inject["uid"] = uid
                _inject["forced"] = not already
                if not already and isinstance(intent, dict):
                    intent["needs_web_search"] = True
                    intent["search_query"] = str(message).strip()
        except Exception as e:
            logger.warning(f"File trigger check failed: {e}")
        return intent

    def search_wrapped(query, num_results=5):
        uid = get_uid()
        if _inject.get("uid") and _inject["uid"] == uid:
            forced = _inject["forced"]
            _inject["uid"] = ""
            try:
                results = file_search_results(uid, str(query),
                                              get_api_key())
            except Exception as e:
                logger.warning(f"File context build failed: {e}")
                results = []
            if results:
                if forced:
                    return results
                inner = prev_search(query, num_results) or []
                return (results + list(inner))[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_file_installed = True


# --------------------------------------------------------------------------
# The context payload (rides the web_search seam in app.py)
# --------------------------------------------------------------------------

def _text_result(meta: dict, text: str) -> dict:
    total = len(text)
    excerpt = text[:TEXT_CONTEXT_CHARS]
    note = ""
    if total > TEXT_CONTEXT_CHARS or meta.get("truncated"):
        note = (f"\n[Excerpt — showing the first {TEXT_CONTEXT_CHARS:,} "
                f"of {meta.get('chars', total):,} characters; the file "
                "continues past this.]")
    label = "PDF" if meta["kind"] == "pdf" else "text file"
    title = (f"📎 The visitor's uploaded {label} \"{meta['name']}\" "
             f"({describe_meta(meta)}) — full content excerpt")
    body = ("The visitor uploaded this file and is asking about it. "
            "Answer from THIS content:\n\n" + excerpt + note)
    return {"title": title, "body": body, "href": ""}


def vision_read(api_key: str, data_uri: str, question: str,
                name: str):
    """One multimodal read of the visitor's photo via the OpenAI chat
    API (sync, like the other app-layer tool calls). Returns a detailed
    description text, or None when the route is unavailable/fails."""
    if not api_key or not data_uri:
        return None
    import httpx
    prompt = (
        "Describe this image in thorough detail: everything visible, "
        "any text in it (quote it exactly), people, objects, colors, "
        "layout. Another AI will answer the visitor's questions from "
        "your description alone, so leave nothing important out."
    )
    if question:
        prompt += (f"\nThe visitor's current question is: \"{question}\" "
                   "— make sure anything relevant to it is covered.")
    try:
        with httpx.Client(timeout=60) as client:
            r = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": VISION_MODEL,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url",
                             "image_url": {"url": data_uri}},
                        ],
                    }],
                    "max_tokens": 800,
                },
            )
        if r.status_code != 200:
            logger.warning(f"Vision read status: {r.status_code}")
            return None
        text = (r.json()["choices"][0]["message"]["content"] or "").strip()
        return text or None
    except Exception as e:
        logger.warning(f"Vision read failed: {e}")
        return None


def file_search_results(uid: str, question: str, api_key: str) -> list:
    """The file's context as web_search-shaped results ({title, body,
    href}) so both chat paths format it into model context exactly
    like a lookup. Text files ride as an excerpt; photos get ONE vision
    read (cached in the metadata) the first time they're asked about.
    Returns [] when nothing is attached."""
    meta = get_meta(uid)
    if not meta:
        return []
    if meta["kind"] in ("pdf", "text"):
        text = _read_text(uid)
        if not text:
            return []
        return [_text_result(meta, text)]
    # image
    desc = meta.get("vision") or ""
    if not desc:
        desc = vision_read(api_key, _image_data_uri(uid, meta),
                           question, meta["name"]) or ""
        if desc:
            meta["vision"] = desc
            try:
                _write_meta(uid, meta)
            except Exception as e:
                logger.warning(f"Could not cache vision read: {e}")
    if not desc:
        desc = ("(The image reader couldn't read this photo just now — "
                "tell the visitor the photo read is down and to try "
                "again in a bit.)")
    title = (f"📎 Vision read of the visitor's uploaded photo "
             f"\"{meta['name']}\" ({describe_meta(meta)})")
    body = ("The visitor uploaded this photo and is asking about it. "
            "A vision model described it as follows — answer from "
            "THIS description:\n\n" + desc)
    return [{"title": title, "body": body, "href": ""}]
