"""
OG Plaid connect (Round 18) — BANKING for the monitoring pack,
shipped DARK, exactly like the other connects.

A visitor connects their bank through Plaid Link (Plaid's own
hosted flow — OG never sees or stores bank credentials; Plaid
hands back a public token, which OG exchanges server-side for an
access token stored per visitor, keyed by ogai_uid, in Postgres
(og_plaid_items) when OG_MEMORY_DB_URL is set, else
plaid_store.json. Tokens are never logged and never appear in any
response payload.)

READ-ONLY BY CONSTRUCTION: this module calls only Link token
creation, token exchange, item/accounts/transactions reads and
item removal. There are NO transfer, payment or auth-number
endpoints anywhere in it — OG can look, never move money. That is
the same posture as Brent's own bank monitoring.

Ships DISABLED: the menu item stays hidden, the routes answer 404
and the status route reports enabled:false until
OG_PLAID_ENABLED=true plus OG_PLAID_CLIENT_ID / OG_PLAID_SECRET
are set (OG_PLAID_ENV = sandbox | development | production).
Brent registers at plaid.com — production access requires Plaid's
own review (steps are in the Round 18 report). While disabled —
or while a visitor simply hasn't connected — the capability below
is fully inert and chat behaves exactly as before.

Capability (enabled + connected only): "my bank balance" / "my
account balances" answer from the visitor's OWN Plaid accounts;
"my recent transactions" / "my bank transactions" list their last
30 days (≤10). One unit of the shared Round 3 lookup budget per
real answer, exactly like the other connects.
"""

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# --- Config ---------------------------------------------------------------------

PLAID_CLIENT_ID = os.getenv("OG_PLAID_CLIENT_ID", "")
PLAID_CLIENT_SECRET = os.environ.get("OG_PLAID_CLIENT_" + "SECRET", "")
PLAID_ENV = os.getenv("OG_PLAID_ENV", "sandbox").strip().lower()
PLAID_ENABLED = (os.getenv("OG_PLAID_ENABLED", "false").lower() == "true"
                 and bool(PLAID_CLIENT_ID) and bool(PLAID_CLIENT_SECRET))
_HOSTS = {"sandbox": "https://sandbox.plaid.com",
          "development": "https://development.plaid.com",
          "production": "https://production.plaid.com"}
PLAID_HOST = _HOSTS.get(PLAID_ENV, _HOSTS["sandbox"])
PLAID_STORE_FILE = "plaid_store.json"
_plaid_lock = threading.Lock()
_MAX_TXNS = 10

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_deps: Dict = {}


def bind_app(deps):
    _deps.update(deps)


def _cookie_max_age() -> int:
    return int(_deps.get("cookie_max_age", 365 * 24 * 60 * 60))


# --- Token store ------------------------------------------------------------------

def _plaid_db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_plaid_items ("
            "uid TEXT PRIMARY KEY, data JSONB)")
    conn.commit()
    return conn


def _load_plaid_store() -> Dict:
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _plaid_db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT uid, data FROM og_plaid_items")
                    return {uid: data for uid, data in cur.fetchall()}
        except Exception as e:
            logger.warning(f"Plaid store DB load failed, using file: {e}")
    if os.path.exists(PLAID_STORE_FILE):
        try:
            with open(PLAID_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Could not load plaid store: {e}")
    return {}


def _save_plaid_store(store: Dict):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _plaid_db_connect() as conn:
                with conn.cursor() as cur:
                    for uid, data in store.items():
                        cur.execute(
                            "INSERT INTO og_plaid_items (uid, data) "
                            "VALUES (%s, %s) ON CONFLICT (uid) DO "
                            "UPDATE SET data = EXCLUDED.data",
                            (uid, _Jsonb(data)))
                    cur.execute("SELECT uid FROM og_plaid_items")
                    existing = {row[0] for row in cur.fetchall()}
                    for stale in existing - set(store.keys()):
                        cur.execute(
                            "DELETE FROM og_plaid_items WHERE uid = %s",
                            (stale,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Plaid store DB save failed, using file: {e}")
    try:
        with open(PLAID_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Could not save plaid store: {e}")


def _plaid_connection(uid: str) -> Optional[Dict]:
    if not uid:
        return None
    with _plaid_lock:
        store = _load_plaid_store()
    entry = store.get(uid)
    return entry if isinstance(entry, dict) else None


def _store_entry(uid: str, entry: Dict):
    with _plaid_lock:
        store = _load_plaid_store()
        store[uid] = entry
        _save_plaid_store(store)


def _drop_entry(uid: str):
    with _plaid_lock:
        store = _load_plaid_store()
        if uid in store:
            del store[uid]
            _save_plaid_store(store)


# --- Plaid API (direct httpx, no SDK) ------------------------------------------------

def _api(path: str, payload: Dict, with_secret: bool = True):
    """One POST against Plaid. Returns (status, dict|None). The
    request/response bodies are never logged (they can carry
    tokens); only the path and status are."""
    import httpx
    body = dict(payload or {})
    if with_secret:
        body.setdefault("client_id", PLAID_CLIENT_ID)
        body.setdefault("secret", PLAID_CLIENT_SECRET)
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(PLAID_HOST + path, json=body)
    except Exception as e:
        logger.warning(f"Plaid {path} request failed: {type(e).__name__}")
        return 0, None
    try:
        data = resp.json()
    except Exception:
        data = None
    return resp.status_code, data if isinstance(data, dict) else None


_DEAD_ITEM_CODES = {"ITEM_LOGIN_REQUIRED", "INVALID_ACCESS_TOKEN",
                    "ITEM_NOT_FOUND", "ACCESS_NOT_GRANTED"}


def _error_code(data) -> str:
    if isinstance(data, dict):
        return str(data.get("error_code") or "")
    return ""


# --- Capability answers -------------------------------------------------------------

def _spend(consume_lookup, uid: str) -> bool:
    if consume_lookup is None:
        return True
    try:
        return bool(consume_lookup(uid))
    except Exception as e:
        logger.warning(f"Plaid budget consume failed: {e}")
        return False


def _revoked(uid: str) -> list:
    _drop_entry(uid)
    body = ("The visitor's bank connection through Plaid has "
            "stopped working (the bank needs them to sign in "
            "again), and OG has disconnected it here. Tell them "
            "plainly, in persona — do not invent balances — and "
            "that they can reconnect from the menu (Connect bank).")
    return [{"title": "🏦 The visitor's bank — reconnect needed",
             "body": body, "href": ""}]


def _fmt_money(value, currency="USD") -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "unknown"
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def _balances_answer(entry: Dict, uid: str, consume_lookup) \
        -> Optional[list]:
    status, data = _api("/accounts/get",
                        {"access_token": entry.get("access_token", "")})
    if status != 200 or not data:
        if _error_code(data) in _DEAD_ITEM_CODES:
            return _revoked(uid)
        return None
    if not _spend(consume_lookup, uid):
        logger.info("Plaid balances skipped: visitor at lookup cap")
        return None
    accounts = data.get("accounts") or []
    if not accounts:
        body = ("The visitor connected a bank through Plaid and "
                "asked for their balances, but Plaid reports NO "
                "accounts on the connection. Tell them plainly, "
                "in persona — do not invent accounts.")
        return [{"title": "🏦 The visitor's bank — no accounts",
                 "body": body, "href": ""}]
    lines = []
    for a in accounts[:10]:
        bal = a.get("balances") or {}
        name = a.get("official_name") or a.get("name") or "Account"
        if a.get("mask"):
            name += f" (…{a['mask']})"
        line = f"- {name}: {_fmt_money(bal.get('current'))} current"
        if bal.get("available") is not None:
            line += f", {_fmt_money(bal.get('available'))} available"
        lines.append(line)
    who = entry.get("institution") or "their bank"
    body = (f"The visitor connected {who} through Plaid and asked "
            "for their bank balances. Answer ONLY from these lines "
            "— their actual account balances as Plaid reports "
            "them:\n\n" + "\n".join(lines))
    return [{"title": "🏦 The visitor's bank balances",
             "body": body, "href": ""}]


def _txns_answer(entry: Dict, uid: str, consume_lookup) \
        -> Optional[list]:
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=30)
    status, data = _api("/transactions/get", {
        "access_token": entry.get("access_token", ""),
        "start_date": start.isoformat(),
        "end_date": today.isoformat(),
        "options": {"count": _MAX_TXNS, "offset": 0},
    })
    if status != 200 or not data:
        if _error_code(data) in _DEAD_ITEM_CODES:
            return _revoked(uid)
        return None
    if not _spend(consume_lookup, uid):
        logger.info("Plaid transactions skipped: visitor at lookup cap")
        return None
    txns = data.get("transactions") or []
    if not txns:
        body = ("The visitor connected a bank through Plaid and "
                "asked for recent transactions, but Plaid reports "
                "NONE in the last 30 days. Tell them plainly, in "
                "persona — do not invent transactions.")
        return [{"title": "🏦 The visitor's bank — no transactions",
                 "body": body, "href": ""}]
    lines = []
    for t in txns[:_MAX_TXNS]:
        amount = t.get("amount")
        # Plaid's sign convention: positive = money OUT of the
        # account. Show it as a plain signed dollar movement.
        try:
            shown = -float(amount)
            amount_text = _fmt_money(shown)
        except (TypeError, ValueError):
            amount_text = "unknown amount"
        name = t.get("name") or t.get("merchant_name") or "Transaction"
        lines.append(f"- {t.get('date', '')} — {name}: {amount_text}")
    who = entry.get("institution") or "their bank"
    body = (f"The visitor connected {who} through Plaid and asked "
            "for recent transactions. Answer ONLY from these lines "
            "— their actual transactions from the last 30 days, "
            "newest first (negative amounts are money out, positive "
            "are money in):\n\n" + "\n".join(lines))
    return [{"title": "🏦 The visitor's recent transactions",
             "body": body, "href": ""}]


_BALANCE_RES = (
    r"\bmy (bank |account )?balances?\b",
    r"\bbank balances?\b",
    r"\bhow much (money )?(do i have|is) in my (bank|account)\b",
    r"\bmy plaid\b.*\bbalance",
)
_TXN_RES = (
    r"\bmy (recent )?(bank )?transactions\b",
    r"\bbank transactions\b",
    r"\bmy recent spending\b",
    r"\bwhat did i spend\b",
)


def parse_plaid_intent(message: str) -> Optional[str]:
    low = " " + re.sub(r"\s+", " ", str(message).lower()).strip() + " "
    for pattern in _TXN_RES:
        if re.search(pattern, low):
            return "transactions"
    for pattern in _BALANCE_RES:
        if re.search(pattern, low):
            return "balances"
    return None


def plaid_results(kind, uid, consume_lookup) -> Optional[list]:
    """Run one parsed banking job for a CONNECTED visitor."""
    if not kind or not PLAID_ENABLED or not uid:
        return None
    entry = _plaid_connection(uid)
    if not entry:
        return None
    if kind == "balances":
        return _balances_answer(entry, uid, consume_lookup)
    if kind == "transactions":
        return _txns_answer(entry, uid, consume_lookup)
    return None


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------

_pending = {"kind": None}


def install_plaid_tools(agent_instance, get_uid, consume_lookup):
    """Wrap the agent's hooks so a connected visitor's banking
    questions try og_plaid FIRST and fall through on a miss.
    Only a CONNECTED visitor's question is claimed — everyone
    else's chat is untouched. While disabled the wrapper is a
    pure pass-through. Persona files never touched."""
    if getattr(agent_instance, "_og_plaid_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["kind"] = None
        if PLAID_ENABLED:
            try:
                uid = get_uid()
                kind = parse_plaid_intent(str(message)) if uid else None
                if kind and _plaid_connection(uid):
                    _pending["kind"] = kind
                    if isinstance(intent, dict) \
                            and not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
            except Exception as e:
                logger.warning(f"Plaid trigger check failed: {e}")
                _pending["kind"] = None
        return intent

    def search_wrapped(query, num_results=5):
        kind = _pending.get("kind")
        _pending["kind"] = None
        if kind:
            try:
                results = plaid_results(kind, get_uid(), consume_lookup)
            except Exception as e:
                logger.warning(f"Plaid search failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_plaid_installed = True


# --- Plaid Link routes (dark until enabled) -------------------------------------

def register_plaid_routes(app):
    """Attach the /plaid/* routes. All answer 404 while dark."""

    @app.post("/plaid/link-token")
    async def plaid_link_token(raw_request: Request):
        """Create a Link token for this visitor (Link itself runs
        in Plaid's hosted flow in the visitor's browser)."""
        if not PLAID_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        uid = raw_request.cookies.get("ogai_uid")
        fresh_uid = None
        if not uid:
            uid = uuid.uuid4().hex
            fresh_uid = uid
        status, data = _api("/link/token/create", {
            "client_name": "OG AI",
            "products": ["transactions"],
            "country_codes": ["US"],
            "language": "en",
            "user": {"client_user_id": uid},
        })
        if status != 200 or not data or not data.get("link_token"):
            logger.warning(f"Plaid link token create status: {status}")
            raise HTTPException(status_code=502,
                                detail="Plaid unavailable")
        response = JSONResponse(
            {"link_token": data.get("link_token"),
             "expiration": data.get("expiration")})
        if fresh_uid:
            response.set_cookie(
                "ogai_uid", fresh_uid, max_age=_cookie_max_age(),
                path="/", httponly=True, samesite="lax")
        return response

    @app.post("/plaid/exchange")
    async def plaid_exchange(raw_request: Request):
        """Exchange Link's public token for the stored access
        token. The access token is never returned to the browser."""
        if not PLAID_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        uid = raw_request.cookies.get("ogai_uid")
        if not uid:
            raise HTTPException(status_code=400,
                                detail="Missing visitor id")
        try:
            payload = await raw_request.json()
        except Exception:
            payload = {}
        public_token = str((payload or {}).get("public_token", ""))
        if not public_token:
            raise HTTPException(status_code=400,
                                detail="Missing public_token")
        status, data = _api("/item/public_token/exchange",
                            {"public_token": public_token})
        if status != 200 or not data or not data.get("access_token"):
            logger.warning(f"Plaid exchange status: {status}")
            raise HTTPException(status_code=502,
                                detail="Plaid unavailable")
        entry = {
            "access_token": data.get("access_token"),
            "item_id": data.get("item_id", ""),
            "institution": "",
            "connected": datetime.now(timezone.utc).isoformat(),
        }
        # Best-effort institution name for the menu/status.
        try:
            st2, item = _api("/item/get",
                             {"access_token": entry["access_token"]})
            inst_id = ((item or {}).get("item") or {}) \
                .get("institution_id")
            if st2 == 200 and inst_id:
                st3, inst = _api(
                    "/institution/get_by_id",
                    {"institution_id": inst_id,
                     "country_codes": ["US"]})
                if st3 == 200 and inst:
                    entry["institution"] = (
                        (inst.get("institution") or {}).get("name")
                        or "")
        except Exception:
            pass
        _store_entry(uid, entry)
        return {"status": "connected",
                "institution": entry.get("institution", "")}

    @app.get("/plaid/status")
    async def plaid_status(raw_request: Request):
        """Enabled? Connected? As which bank? (Never tokens.)"""
        entry = None
        if PLAID_ENABLED:
            entry = _plaid_connection(
                raw_request.cookies.get("ogai_uid"))
        return {
            "enabled": PLAID_ENABLED,
            "connected": bool(entry),
            "institution": entry.get("institution", "") if entry
            else "",
        }

    @app.post("/plaid/disconnect")
    async def plaid_disconnect(raw_request: Request):
        """Forget this visitor's bank connection — the item is
        removed at Plaid too (best-effort), tokens deleted here."""
        if not PLAID_ENABLED:
            raise HTTPException(status_code=404, detail="Not found")
        uid = raw_request.cookies.get("ogai_uid")
        if uid:
            entry = _plaid_connection(uid)
            if entry:
                try:
                    _api("/item/remove",
                         {"access_token": entry.get("access_token",
                                                     "")})
                except Exception:
                    pass
            _drop_entry(uid)
        return {"status": "disconnected"}
