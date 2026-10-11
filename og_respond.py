"""OG respond (Round 62, Brent's approval 2026-10-10):
"have OG answer this one" — a drafted reply to a quoted
message that the user approves before it lands.

    POST /respond/draft {"text": quoted message text}
        -> {"ok": True, "draft": "..."}
    POST /respond/send  {"draft": "...", "quote": "..."}
        -> {"ok": True}

DRAFT — generation WITHOUT history writes. /chat's shape is
mirrored but its save is not: the caller's stored thread is
loaded, the draft prompt is appended to a THROWAWAY copy as
the last user turn ("reply to this message..."), the shared
agent's history is swapped to that copy under the app's
memory lock, the agent's own _generate_ai_response produces
the text (OG's locked persona — the system prompt and
ai_agent_enhanced.py are never touched by this module), the
agent's original history object is restored in a finally,
and NOTHING is saved. A draft therefore cannot enter the
conversation by construction; the r62 suite pins it. The
draft text passes og_register.enforce_reply, exactly like
/chat's final reply text.

CHARGE. A successful draft is charged as ONE chat call
through the SAME usage path /chat uses —
_record_chat_usage(uid, _estimate_call_tokens(agent, draft))
— so it draws on the caller's weekly chat token pool
(Round 55). The /chat pool pre-check and token-pack settle
are not duplicated here (documented in round-62 NOTES.md).

SEND — the approved draft, and only the draft, is appended
to the caller's stored history as ONE assistant message
({"role": "assistant", "content": draft, "timestamp": iso})
under the memory lock, through the app's own load/save
history helpers, so later /chat turns follow it. `quote`
is validated as context and never stored. One call appends
exactly once.

DEPS. bind_app(deps) when a host binds them (tests):
get_agent, load_history, save_history, memory_lock,
record_chat_usage, estimate_tokens. Unbound (production),
each resolves lazily from the fully-loaded app module — the
og_store_ready precedent (`import app as _appmod` inside the
call, never at import time, so no import cycle exists).

GATE. Neither route is in og_accounts.PUBLIC_PATHS, so the
Round 44 sign-in gate covers both by construction.
"""

import logging
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

UID_COOKIE = "ogai_uid"
TEXT_CAP = 4000
DRAFT_CAP = 4000

_deps: dict = {}

_APP_ATTRS = {
    "get_agent": "get_agent",
    "load_history": "_load_visitor_history_locked",
    "save_history": "_save_visitor_history_locked",
    "memory_lock": "_memory_lock",
    "record_chat_usage": "_record_chat_usage",
    "estimate_tokens": "_estimate_call_tokens",
}


def bind_app(deps):
    _deps.update(deps)


def _dep(name):
    if name in _deps:
        return _deps[name]
    attr = _APP_ATTRS.get(name)
    if attr is None:
        return None
    try:
        import app as _appmod
        return getattr(_appmod, attr, None)
    except Exception as e:
        logger.warning(f"og_respond: app dep {name} unavailable: {e}")
        return None


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _lock():
    lock = _dep("memory_lock")
    if lock is None:
        return _NoLock()
    return lock


def _prompt_for(text: str) -> str:
    return ("Reply to this message like it was just said to you "
            "in our conversation — your answer IS the reply, "
            "nothing else:\n\n\"" + text + "\"")


def draft_reply(uid: str, text: str) -> str:
    """Generate OG's reply draft to `text` for visitor `uid`
    without writing anything to their stored history."""
    get_agent = _dep("get_agent")
    load_history = _dep("load_history")
    if get_agent is None or load_history is None:
        raise RuntimeError("respond deps unavailable")
    agent = get_agent()
    with _lock():
        stored = load_history(uid)
        throwaway = list(stored or []) + [{
            "role": "user", "content": _prompt_for(text),
            "timestamp": datetime.now(timezone.utc).isoformat()}]
        original = getattr(agent, "conversation_history", None)
        agent.conversation_history = throwaway
        try:
            gen = getattr(agent, "_generate_ai_response", None)
            if callable(gen):
                raw = gen(_prompt_for(text))
            else:
                # Fallback agents: process_message mutates only
                # the throwaway list, which is discarded below.
                raw = agent.process_message(_prompt_for(text))
        finally:
            agent.conversation_history = original
    draft = str(raw or "").strip()
    try:
        import og_register
        draft = og_register.enforce_reply(draft)
    except Exception as e:
        logger.warning(f"og_respond: register enforce skipped: {e}")
    # Charge as ONE chat call via /chat's own usage path.
    try:
        record = _dep("record_chat_usage")
        estimate = _dep("estimate_tokens")
        if record is not None:
            tokens = 0
            if estimate is not None:
                tokens = int(estimate(agent, draft) or 0)
            if tokens <= 0:
                tokens = max(1, len(draft) // 4)
            record(uid, tokens)
    except Exception as e:
        logger.warning(f"og_respond: usage charge failed: {e}")
    return draft


def append_draft(uid: str, draft: str) -> bool:
    """Append the approved draft as ONE assistant message to
    the caller's stored history. True on success."""
    load_history = _dep("load_history")
    save_history = _dep("save_history")
    if load_history is None or save_history is None:
        return False
    with _lock():
        history = list(load_history(uid) or [])
        history.append({
            "role": "assistant", "content": draft,
            "timestamp": datetime.now(timezone.utc).isoformat()})
        save_history(uid, history)
    return True


# --- Routes ------------------------------------------------------------------------


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


def register_respond_routes(app):

    @app.post("/respond/draft")
    async def respond_draft(request: Request):
        uid = request.cookies.get(UID_COOKIE) or ""
        body = await _json_body(request)
        text = body.get("text")
        if not uid or not isinstance(text, str) \
                or not text.strip() or len(text) > TEXT_CAP:
            return JSONResponse(
                {"ok": False, "detail": "bad text"}, status_code=400)
        try:
            draft = draft_reply(uid, text.strip())
        except Exception as e:
            logger.warning(f"og_respond draft failed: {e}")
            return JSONResponse(
                {"ok": False, "detail": "draft failed"},
                status_code=502)
        if not draft:
            return JSONResponse(
                {"ok": False, "detail": "draft failed"},
                status_code=502)
        return {"ok": True, "draft": draft[:DRAFT_CAP]}

    @app.post("/respond/send")
    async def respond_send(request: Request):
        uid = request.cookies.get(UID_COOKIE) or ""
        body = await _json_body(request)
        draft = body.get("draft")
        quote = body.get("quote")
        if not uid or not isinstance(draft, str) \
                or not draft.strip() or len(draft) > DRAFT_CAP \
                or (quote is not None
                    and (not isinstance(quote, str)
                         or len(quote) > TEXT_CAP)):
            return JSONResponse(
                {"ok": False, "detail": "bad draft"}, status_code=400)
        if not append_draft(uid, draft.strip()):
            return JSONResponse(
                {"ok": False, "detail": "history unavailable"},
                status_code=502)
        return {"ok": True}
