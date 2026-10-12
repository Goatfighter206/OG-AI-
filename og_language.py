"""Languages for OG (Round 65, Brent's order 2026-10-11): a
per-user language preference with a Languages menu in the web
side menu. Pick a language and OG's next reply comes back in
it — same personality, same style, just that language.

    GET  /language -> {"current": code,
                       "languages": [{"code", "name"}, ...]}
    POST /language {"code": "es"} -> sets the CALLER's pref;
         unknown code -> 400, nothing written.

English (en) and Spanish (es) are PINNED FIRST in the list,
then: French, German, Portuguese, Italian, Dutch, Russian,
Polish, Ukrainian, Turkish, Arabic, Hindi, Bengali, Chinese
(Simplified), Japanese, Korean, Vietnamese, Thai, Indonesian,
Tagalog (21 total — the full list lives in LANGUAGES below
and in round-65/NOTES.md).

STORAGE. The codebase's established durable pattern — one
key -> JSON blob — in this module's own store, exactly the
og_reactions form (Postgres table og_language_data when
OG_MEMORY_DB_URL is set, else a JSON file: OG_LANGUAGE_STORE
or language_store.json). Records are keyed "language:<uid>";
a user only ever reads/writes their own pref. Default (no
record) is English.

GATE. These routes are NOT in og_accounts.PUBLIC_PATHS, so
the Round 44 sign-in gate covers them by construction.

EFFECT — RUNTIME DIRECTIVE, PERSONA FILE UNTOUCHED. When a
signed-in user's pref is not English, prompt assembly appends
a runtime directive for that turn: converse in that language,
replies entirely in it, same personality/style. English pref
-> nothing appended, assembly byte-identical (suite-pinned).
The seam is the swearing switch's pattern (wrap the agent's
_generate_ai_response + detect_intent), installed fail-safe
from og_fixqueue.install_fixqueue_tools with its get_uid
closure. This module's installer is the COMBINED Round 65
installer: it also appends og_comebacks' retaliation
directive when that detector fires, so the two Round 65
directives share one wrapper and one marker block and can
never double-wrap or strip each other. The block:

    base prompt + MARKER + language directive
                         [+ "\n" + comeback directive]

Stripping a prior block truncates at MARKER (anything after
it — e.g. a clean-register suffix the swearing wrapper added
later in a previous turn — is recomputed by that wrapper on
the next turn anyway). detect_intent's wrap sets the prompt
for the turn (app.py's streaming branches read
agent.system_prompt after detect_intent); _generate_ai_response's
wrap recomputes for the call and restores the entry prompt
afterwards (classic /chat and og_respond drafts).
"""

import json
import logging
import os
import threading

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

UID_COOKIE = "ogai_uid"
STORE_FILE = os.getenv("OG_LANGUAGE_STORE", "language_store.json")
DEFAULT_CODE = "en"

LANGUAGES = [
    {"code": "en", "name": "English"},
    {"code": "es", "name": "Spanish"},
    {"code": "fr", "name": "French"},
    {"code": "de", "name": "German"},
    {"code": "pt", "name": "Portuguese"},
    {"code": "it", "name": "Italian"},
    {"code": "nl", "name": "Dutch"},
    {"code": "ru", "name": "Russian"},
    {"code": "pl", "name": "Polish"},
    {"code": "uk", "name": "Ukrainian"},
    {"code": "tr", "name": "Turkish"},
    {"code": "ar", "name": "Arabic"},
    {"code": "hi", "name": "Hindi"},
    {"code": "bn", "name": "Bengali"},
    {"code": "zh-CN", "name": "Chinese (Simplified)"},
    {"code": "ja", "name": "Japanese"},
    {"code": "ko", "name": "Korean"},
    {"code": "vi", "name": "Vietnamese"},
    {"code": "th", "name": "Thai"},
    {"code": "id", "name": "Indonesian"},
    {"code": "tl", "name": "Tagalog"},
]
_BY_CODE = {lang["code"]: lang for lang in LANGUAGES}

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, DB_OPTIONS as _DB_OPTS, StoreError
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_store_lock = threading.RLock()


# --- Durable store (og_reactions pattern: one key -> JSON blob) ----------------


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=_DB_CT, options=_DB_OPTS)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_language_data ("
            "key TEXT PRIMARY KEY, data JSONB)")
    conn.commit()
    return conn


def _load_file_store() -> dict:
    if os.path.exists(STORE_FILE):
        try:
            with open(STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Language store load failed: {e}")
    return {}


def _save_file_store(store: dict):
    try:
        with open(STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Language store save failed: {e}")


def _get(key: str):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_language_data "
                        "WHERE key=%s", (key,))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning(f"Language DB load failed: {e}")
            raise StoreError("language store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
    return store.get(key)


def _put(key: str, value):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_language_data (key, data) "
                        "VALUES (%s, %s) ON CONFLICT (key) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (key, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Language DB save failed: {e}")
            raise StoreError("language store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
        store[key] = value
        _save_file_store(store)


def purge_uid(uid: str):
    """Account deletion helper: drop this uid's pref."""
    if uid:
        _put("language:" + uid, {})


# --- Preference -------------------------------------------------------------------


def is_valid_code(code) -> bool:
    return isinstance(code, str) and code in _BY_CODE


def language_name(code: str) -> str:
    lang = _BY_CODE.get(code or "")
    return lang["name"] if lang else "English"


def get_language(uid: str) -> str:
    """The uid's language code; English when unset/unknown."""
    if not uid:
        return DEFAULT_CODE
    data = _get("language:" + uid)
    code = data.get("code") if isinstance(data, dict) else None
    return code if is_valid_code(code) else DEFAULT_CODE


def set_language(uid: str, code: str) -> bool:
    """Store the uid's pref. False (nothing written) on an
    unknown code or a missing uid."""
    if not uid or not is_valid_code(code):
        return False
    _put("language:" + uid, {"code": code})
    return True


# --- Runtime directives -----------------------------------------------------------

MARKER = "\n\n--- OG RUNTIME (R65) ---\n"


def language_directive(uid: str) -> str:
    """The converse-in-<Language> directive for a non-English
    pref; "" for English (assembly stays byte-identical)."""
    code = get_language(uid)
    if code == DEFAULT_CODE:
        return ""
    name = language_name(code)
    return (
        f"LANGUAGE — CONVERSE IN {name.upper()}: reply entirely "
        f"in {name} for this turn — every word of your reply in "
        f"{name}. Keep the exact same personality and style and "
        "the same one-best-answer, decisive approach — just in "
        f"{name}.")


def round65_directives(uid: str, message) -> list:
    """All Round 65 directives for one turn, in order:
    language first, then the retaliation comeback (which
    og_comebacks itself gates on the detector + swearing_on()).

    Round 84: the account's own shit-talking switch gates
    what rides here — OFF for this user drops the comeback
    (its own master gate already ran inside og_comebacks)
    and silences the new bank; ON + an explicit shit-talk
    invite + no comeback this turn appends og_shittalk's
    bank directive (one quoted line per turn, retaliation
    owns its turn)."""
    out = []
    try:
        directive = language_directive(uid)
        if directive:
            out.append(directive)
    except Exception as e:  # assembly never eats a turn
        logger.warning(f"Language directive failed: {e}")
    user_on = True
    try:
        import og_shittalk as _st
        user_on = _st.on_for(uid)
    except Exception as e:
        logger.warning(f"Shittalk gate failed: {e}")
    comeback = ""
    try:
        import og_comebacks as _cb
        comeback = _cb.comeback_directive(message)
        if comeback and user_on:
            out.append(comeback)
    except Exception as e:
        logger.warning(f"Comeback directive failed: {e}")
    if user_on and not comeback:
        try:
            import og_shittalk as _st
            directive = _st.shittalk_directive(uid, message)
            if directive:
                out.append(directive)
        except Exception as e:
            logger.warning(f"Shittalk directive failed: {e}")
    return out


def _strip_block(prompt: str) -> str:
    if prompt and MARKER in prompt:
        return prompt.split(MARKER, 1)[0]
    return prompt


def effective_prompt(base, uid: str, message) -> str:
    """The system prompt for one chat call with Round 65
    directives applied. No directives and no stale block:
    `base` returned unchanged (the same object — byte-
    identical assembly, suite-pinned)."""
    directives = round65_directives(uid, message)
    if not directives:
        if base and MARKER in str(base):
            return _strip_block(str(base))
        return base
    clean = _strip_block(base or "")
    return clean + MARKER + "\n".join(directives)


def install_language_tools(agent_instance, get_uid=None) -> bool:
    """Wrap the agent's prompt-assembly seams with the combined
    Round 65 installer (language + comebacks — one wrapper, one
    marker block). Idempotent; returns True when installed (or
    already installed). Never raises on a missing seam."""
    if getattr(agent_instance, "_og_r65_installed", False):
        return True
    if get_uid is None:
        get_uid = lambda: ""  # noqa: E731 — uid-less: language off,
        # comebacks still keyed to the message alone.
    installed = False

    def _uid():
        try:
            return get_uid() or ""
        except Exception:
            return ""

    prev_generate = getattr(agent_instance, "_generate_ai_response", None)
    if callable(prev_generate):

        def generate_wrapped(message, context=""):
            original = getattr(agent_instance, "system_prompt", None)
            try:
                if hasattr(agent_instance, "system_prompt"):
                    agent_instance.system_prompt = effective_prompt(
                        agent_instance.system_prompt, _uid(), message)
            except Exception as e:  # assembly never eats a turn
                logger.warning(f"R65 assemble failed: {e}")
            try:
                return prev_generate(message, context)
            finally:
                try:
                    if hasattr(agent_instance, "system_prompt"):
                        agent_instance.system_prompt = original
                except Exception:
                    pass

        agent_instance._generate_ai_response = generate_wrapped
        installed = True

    prev_detect = getattr(agent_instance, "detect_intent", None)
    if callable(prev_detect):

        def detect_wrapped(message):
            try:
                if hasattr(agent_instance, "system_prompt"):
                    agent_instance.system_prompt = effective_prompt(
                        agent_instance.system_prompt, _uid(), message)
            except Exception as e:  # normalization never eats a turn
                logger.warning(f"R65 normalize failed: {e}")
            return prev_detect(message)

        agent_instance.detect_intent = detect_wrapped
        installed = True

    if installed:
        agent_instance._og_r65_installed = True
    return installed


# --- Routes ------------------------------------------------------------------------


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


def register_language_routes(app):

    @app.get("/language")
    async def language_list(request: Request):
        uid = request.cookies.get(UID_COOKIE) or ""
        return {"current": get_language(uid),
                "languages": [dict(lang) for lang in LANGUAGES]}

    @app.post("/language")
    async def language_set(request: Request):
        uid = request.cookies.get(UID_COOKIE) or ""
        body = await _json_body(request)
        code = body.get("code")
        if not is_valid_code(code):
            return JSONResponse(
                {"ok": False, "detail": "unknown language code"},
                status_code=400)
        if not uid:
            return JSONResponse(
                {"ok": False, "detail": "no visitor id"},
                status_code=400)
        set_language(uid, code)
        return {"ok": True, "current": code,
                "languages": [dict(lang) for lang in LANGUAGES]}
