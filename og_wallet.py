"""
OG Wallet (Phase A — tokenize, DARK) — plan:
~/workspace/goals/og-ai-project/files/og-wallet-plan.md §7.

WHAT PHASE A IS. Every OG user can CONNECT one payment
method and REVOKE it, with OG never seeing, receiving, or
storing a card number. Card entry happens only on Stripe's
hosted setup page (a Checkout Session in `setup` mode).
OG's durable store holds ONLY Stripe ids (Customer id,
PaymentMethod id) plus display fields (brand, last4,
expiry) and the consent record (timestamp + terms version)
Stripe's compliance posture requires. There is NO charge
execution in Phase A: no PaymentIntent is ever created
here. `_wallet_charge` is a guard-only skeleton (gate,
connection, ceilings, daily count) that Phase B fills in;
it refuses with "charging_not_available_phase_a" after the
guards pass and never calls a Stripe charge endpoint.

DARK GATE: live only when OG_WALLET_ENABLED is truthy AND
OG_WALLET_STRIPE_KEY (a restricted key: Customers,
PaymentMethods, SetupIntents, PaymentIntents, Charges
read) is present. Dark = GET /wallet/status answers exactly
{"enabled": false}, the action routes 404, and the
Connections Wallet row is never injected by the web asset
(gate-off = invisible, plan §7). The key lives in Render
env only — never chat, never git. Brent creates it in the
Stripe dashboard when Phase B starts (a Brent-hands step,
parked, not attempted here).

STRIPE SEAM: every Stripe call goes through the ONE
function `_stripe_http(method, path, params)` — stdlib
urllib, form-encoded, Bearer <redacted> — so tests stub
exactly that and no real network is ever touched in a
suite. Fail-safe contract (og_notify's): a Stripe outage
degrades the wallet routes to "unavailable right now"
(502 wallet_unavailable on setup/remove), never breaks
chat — this module registers routes only, and holds no
chat seam at all in Phase A.

STORE: og_notify's durable pattern — Postgres table
og_wallet_data (key TEXT PRIMARY KEY, data JSONB) when
OG_MEMORY_DB_URL is set, else wallet_store.json
(OG_WALLET_STORE overrides). Keys:
  wallet:<uid>         {stripe_customer_id, payment_method_id,
                        brand, last4, exp_month, exp_year,
                        consent_ts, consent_version, status,
                        created, setup_session_id}
  wallet_pending:<uid> the parked prepared charge (Phase B's
                        executor consumes it; Phase A only
                        stores/clears it via the guard)
  wallet_charges:<uid> audit list, newest first, cap 100:
                        {ts, amount_cents, currency, merchant,
                        description, path, status,
                        stripe_payment_intent, stripe_charge,
                        failure_code}
  wallet_setup:<uid>   {session_id, created} for the
                        in-flight hosted setup (idempotency)

ROUTES (sign-in gated by Round 44's middleware in
production — /wallet/* is not a public path — and
uid-keyed here: a live session's account uid wins, else
the caller's own ogai_uid cookie):
  GET  /wallet/status    {enabled, connected, brand, last4,
                          exp, charges_today_cents,
                          ceiling_cents, daily_ceiling_cents}
                         (+ charges list when ?history=1 is
                         not used — history has its own route)
  POST /wallet/setup     creates/reuses the Stripe Customer
                         + a setup-mode Checkout Session,
                         returns {"ok", "url", "session_id"};
                         idempotent per uid (an active wallet
                         returns connected; an in-flight open
                         session's URL is reused)
  GET  /wallet/complete  the hosted-setup return: syncs the
                         session -> SetupIntent -> PaymentMethod
                         display fields, records consent, and
                         lands on a small confirmation page
  GET  /wallet/history   {enabled, charges: [...]}
  POST /wallet/remove    detach PaymentMethod at Stripe +
                         wipe wallet:<uid> (+ pending/setup)
                         in the same request; {"ok": true}.
                         The audit list stays (it is the
                         record that charges happened).

WEBHOOK: handle_wallet_event(event) below handles
setup_intent.succeeded, payment_intent.succeeded,
payment_intent.payment_failed, charge.refunded and
charge.dispute.created, filtered by metadata og_uid.
FOLLOW-UP (documented, not forced): app.py is at its push
ceiling and its /stripe/webhook handler dispatches only
checkout.session.completed, so nothing in the current tree
can call this handler without an app.py edit. When the
ceiling frees (or a generic event-dispatch seam lands),
wire: `if event type in WALLET_EVENT_TYPES:
og_wallet.handle_wallet_event(event)` inside the existing
handler, fail-safe. The live webhook destination must also
subscribe to these event types (Brent dashboard step,
plan §4a).

CEILINGS: og_tiers.wallet_ceiling / wallet_daily_ceiling
(USD, per tier, env-overridable) + the "wallet" count kind
(charges/day, charged only on success — Phase B enforces
the spend; the guard skeleton checks it). Status reports
the caller's tier ceilings in cents.

Persona files are NEVER touched by this module.
"""

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger(__name__)

UID_COOKIE = "ogai_uid"
STORE_FILE = os.getenv("OG_WALLET_STORE", "wallet_store.json")
CONSENT_VERSION = "wallet-terms-v1-2026-10-11"
AUDIT_CAP = 100
_SETUP_TTL = 30 * 60          # reuse an in-flight setup session 30 min
_STRIPE_BASE = os.getenv(
    "OG_WALLET_STRIPE_URL", "https://api.stripe.com/v1").rstrip("/")

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, StoreError, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_store_lock = threading.Lock()
_deps: Dict = {}


def bind_app(deps):
    _deps.update(deps)


# --- Gate ------------------------------------------------------------------------


def _flag_on() -> bool:
    return os.getenv("OG_WALLET_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on")


def _stripe_key() -> str:
    return os.getenv("OG_WALLET_STRIPE_KEY", "").strip()


def enabled() -> bool:
    """Dark gate: the flag AND the restricted key must both exist."""
    return _flag_on() and bool(_stripe_key())


# --- Durable store (og_notify pattern: one key -> JSON blob) ---------------------


def _db_connect():
    conn = psycopg.connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_wallet_data ("
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
            logger.warning(f"Wallet store load failed: {e}")
    return {}


def _save_file_store(store: dict):
    try:
        with open(STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Wallet store save failed: {e}")


def _get(key: str):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_wallet_data WHERE key=%s",
                        (key,))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning(f"Wallet DB load failed: {e}")
            raise StoreError("wallet store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
    return store.get(key)


def _put(key: str, value):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_wallet_data (key, data) "
                        "VALUES (%s, %s) ON CONFLICT (key) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (key, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Wallet DB save failed: {e}")
            raise StoreError("wallet store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
        store[key] = value
        _save_file_store(store)


def _del(key: str):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM og_wallet_data WHERE key=%s",
                        (key,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Wallet DB delete failed: {e}")
            raise StoreError("wallet store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
        store.pop(key, None)
        _save_file_store(store)


# --- Wallet records ------------------------------------------------------------------


def _wallet_key(uid: str) -> str:
    return "wallet:" + uid


def _get_wallet(uid: str) -> Optional[dict]:
    rec = _get(_wallet_key(uid)) if uid else None
    return rec if isinstance(rec, dict) else None


def _connected_wallet(uid: str) -> Optional[dict]:
    rec = _get_wallet(uid)
    if rec and rec.get("status") == "active" \
            and rec.get("payment_method_id"):
        return rec
    return None


def _charges(uid: str) -> List[dict]:
    items = _get("wallet_charges:" + uid) if uid else None
    return items if isinstance(items, list) else []


def _record_charge(uid: str, entry: dict):
    """Append one audit record (newest first, capped 100).
    Fail-safe: an audit write must never break a caller."""
    if not uid:
        return
    try:
        rec = {"ts": time.time()}
        for k in ("amount_cents", "currency", "merchant",
                  "description", "path", "status",
                  "stripe_payment_intent", "stripe_charge",
                  "failure_code"):
            rec[k] = entry.get(k)
        items = _charges(uid)
        items.insert(0, rec)
        _put("wallet_charges:" + uid, items[:AUDIT_CAP])
    except Exception as e:
        logger.warning(f"Wallet audit write failed: {e}")


def charges_today_cents(uid: str) -> int:
    """Sum of today's (UTC) succeeded wallet charges, in cents."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    total = 0
    for rec in _charges(uid):
        try:
            if rec.get("status") != "succeeded":
                continue
            day = datetime.fromtimestamp(
                float(rec.get("ts", 0)), timezone.utc
            ).strftime("%Y-%m-%d")
            if day == today:
                total += int(rec.get("amount_cents", 0) or 0)
        except Exception:
            continue
    return total


def _charges_today_count(uid: str) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    n = 0
    for rec in _charges(uid):
        try:
            if rec.get("status") != "succeeded":
                continue
            day = datetime.fromtimestamp(
                float(rec.get("ts", 0)), timezone.utc
            ).strftime("%Y-%m-%d")
            if day == today:
                n += 1
        except Exception:
            continue
    return n


# --- THE Stripe seam (one function; tests stub exactly this) -----------------------


class WalletStripeError(Exception):
    """A Stripe call failed (network, HTTP, or a Stripe error body)."""


def _flatten(params: dict, prefix: str = "") -> dict:
    """{a: {b: 1}, c: [x, y]} -> bracket form keys Stripe expects."""
    out = {}
    for k, v in (params or {}).items():
        key = f"{prefix}[{k}]" if prefix else str(k)
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        elif isinstance(v, (list, tuple)):
            for i, item in enumerate(v):
                ikey = f"{key}[{i}]"
                if isinstance(item, dict):
                    out.update(_flatten(item, ikey))
                else:
                    out[ikey] = item
        else:
            out[key] = v
    return out


def _stripe_http(method: str, path: str, params: Optional[dict] = None):
    """ONE mockable Stripe HTTP seam. Stdlib urllib, form-encoded,
    restricted-key Bearer <redacted> Returns the parsed JSON dict.
    Raises WalletStripeError on any failure — callers degrade the
    wallet to unavailable, never chat."""
    key = _stripe_key()
    if not key:
        raise WalletStripeError("no stripe key configured")
    url = _STRIPE_BASE + path
    data = None
    if method.upper() == "GET":
        if params:
            url += "?" + urllib.parse.urlencode(_flatten(params))
    else:
        data = urllib.parse.urlencode(
            _flatten(params or {})).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method.upper())
    req.add_header("Authorization", "Bearer " + key)
    if data is not None:
        req.add_header("Content-Type",
                       "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            body = json.loads(e.read().decode("utf-8"))
            detail = ((body.get("error") or {}).get("message")
                      or "") if isinstance(body, dict) else ""
        except Exception:
            detail = ""
        raise WalletStripeError(
            f"stripe http {e.code}: {detail}".strip())
    except Exception as e:
        raise WalletStripeError(f"stripe unreachable: {e}")


# --- Identity / tier -------------------------------------------------------------------


def _route_uid(request: Request) -> str:
    """The caller's wallet uid: a live session's ACCOUNT uid
    (server-issued, the Round 44 identity) wins; otherwise the
    caller's own ogai_uid cookie. Lazy + fail-safe."""
    try:
        import og_accounts
        uid = og_accounts.session_account_uid(
            request.cookies.get(og_accounts.SESSION_COOKIE))
        if uid:
            return uid
    except Exception:
        pass
    try:
        return request.cookies.get(UID_COOKIE, "") or ""
    except Exception:
        return ""


def _session_required(request: Request) -> bool:
    """True when the sign-in gate is in force and this request
    carries no live session (mirrors the middleware's verdict;
    keeps bare local/test processes usable, like gate_enabled)."""
    try:
        import og_accounts
        if not og_accounts.gate_enabled():
            return False
        return og_accounts._session_for(
            request.cookies.get(og_accounts.SESSION_COOKIE)) is None
    except Exception:
        return False


def _route_tier(uid: str, request: Request) -> str:
    fn = _deps.get("tier_of")
    if fn:
        try:
            return str(fn(uid, request) or "free")
        except Exception:
            pass
    try:
        import og_tiers
        return og_tiers.tier_of(request.cookies, uid)
    except Exception:
        return "free"


def _public_base() -> str:
    return os.getenv(
        "OG_PUBLIC_URL",
        "https://og-ai-service.onrender.com").rstrip("/")


# --- Setup / completion / remove --------------------------------------------------------


def _start_setup(uid: str) -> dict:
    """Create/reuse the Stripe Customer + a setup-mode Checkout
    Session for uid. Idempotent: an active wallet returns
    connected; an in-flight OPEN session's URL is reused.
    Raises WalletStripeError on a Stripe failure."""
    rec = _get_wallet(uid)
    if rec and rec.get("status") == "active" \
            and rec.get("payment_method_id"):
        return {"ok": True, "connected": True, "url": "",
                "session_id": ""}
    setup = _get("wallet_setup:" + uid)
    if isinstance(setup, dict) and setup.get("session_id") \
            and time.time() - float(setup.get("created", 0)) < _SETUP_TTL:
        try:
            sess = _stripe_http(
                "GET", "/checkout/sessions/" + setup["session_id"])
            if sess.get("status") == "open" and sess.get("url"):
                return {"ok": True, "connected": False,
                        "url": sess["url"],
                        "session_id": setup["session_id"]}
        except WalletStripeError:
            pass  # fall through and mint a fresh session
    customer_id = (rec or {}).get("stripe_customer_id") or ""
    if not customer_id:
        cust = _stripe_http("POST", "/customers",
                            {"metadata": {"og_uid": uid}})
        customer_id = cust.get("id") or ""
        if not customer_id:
            raise WalletStripeError("stripe returned no customer id")
        base = dict(rec or {})
        base.update({
            "stripe_customer_id": customer_id,
            "payment_method_id": base.get("payment_method_id"),
            "status": base.get("status") or "pending_setup",
            "created": base.get("created") or time.time(),
        })
        _put(_wallet_key(uid), base)
        rec = base
    sess = _stripe_http("POST", "/checkout/sessions", {
        "mode": "setup",
        "customer": customer_id,
        "payment_method_types": ["card"],
        "success_url": _public_base()
        + "/wallet/complete?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": _public_base() + "/?wallet=cancelled",
        "metadata": {"og_uid": uid},
        "setup_intent_data": {"metadata": {"og_uid": uid}},
    })
    if not sess.get("id") or not sess.get("url"):
        raise WalletStripeError("stripe returned no checkout session")
    _put("wallet_setup:" + uid,
         {"session_id": sess["id"], "created": time.time()})
    return {"ok": True, "connected": False, "url": sess["url"],
            "session_id": sess["id"]}


# Display fields ONLY — the four card fields a PaymentMethod exposes
# for display. Nothing else about the card exists to store.
_PM_FIELDS = ("brand", "last4", "exp_month", "exp_year")


def _pm_display(pm: dict) -> dict:
    card = (pm or {}).get("card") or {}
    return {k: card.get(k) for k in _PM_FIELDS}


def _activate_from_payment_method(uid: str, pm_id: str,
                                  customer_id: str = "") -> dict:
    """Fetch the PaymentMethod's display fields and store the
    active wallet record, recording consent NOW (completion is
    the agreement moment). Raises WalletStripeError."""
    pm = _stripe_http("GET", "/payment_methods/" + pm_id)
    disp = _pm_display(pm)
    prev = _get_wallet(uid) or {}
    rec = {
        "stripe_customer_id": customer_id
        or prev.get("stripe_customer_id") or "",
        "payment_method_id": pm_id,
        "brand": disp.get("brand"),
        "last4": disp.get("last4"),
        "exp_month": disp.get("exp_month"),
        "exp_year": disp.get("exp_year"),
        "consent_ts": time.time(),
        "consent_version": CONSENT_VERSION,
        "status": "active",
        "created": prev.get("created") or time.time(),
    }
    _put(_wallet_key(uid), rec)
    _del("wallet_setup:" + uid)
    return rec


def _complete_setup(uid: str, session_id: str) -> Optional[dict]:
    """Sync one hosted-setup return: session -> SetupIntent ->
    PaymentMethod -> active wallet record (consent recorded).
    Returns the wallet record, or None when the session is not
    uid's completed setup. Idempotent (an already-active wallet
    for the same PaymentMethod just returns its record)."""
    if not uid or not session_id:
        return None
    setup = _get("wallet_setup:" + uid)
    if not isinstance(setup, dict) \
            or setup.get("session_id") != session_id:
        rec = _connected_wallet(uid)
        return rec  # a refresh of the return page: already done
    sess = _stripe_http("GET", "/checkout/sessions/" + session_id)
    meta = sess.get("metadata") or {}
    if meta.get("og_uid") not in (None, "", uid):
        return None
    si_id = sess.get("setup_intent")
    if isinstance(si_id, dict):
        si = si_id
    elif si_id:
        si = _stripe_http("GET", "/setup_intents/" + str(si_id))
    else:
        return None
    pm_id = si.get("payment_method")
    if isinstance(pm_id, dict):
        pm_id = pm_id.get("id")
    if not pm_id:
        return None
    return _activate_from_payment_method(
        uid, str(pm_id), sess.get("customer") or "")


def _remove_wallet(uid: str) -> bool:
    """The revoke (plan §1.6): detach the PaymentMethod at
    Stripe and wipe wallet:<uid> in the same action. A Stripe
    failure raises BEFORE the wipe, so the wallet is never
    half-revoked silently — the caller surfaces unavailable
    and the user retries; no other state is consulted first."""
    rec = _get_wallet(uid)
    pm_id = (rec or {}).get("payment_method_id")
    if pm_id:
        _stripe_http(
            "POST", f"/payment_methods/{pm_id}/detach", {})
    _del(_wallet_key(uid))
    _del("wallet_pending:" + uid)
    _del("wallet_setup:" + uid)
    return True


def purge_owner(uid: str):
    """Account deletion (Round 36, og_store_ready): wipe every
    wallet key for uid and attempt the Stripe detach
    FAIL-SAFE — a Stripe outage never blocks the deletion,
    and the local wipe always lands."""
    if not uid:
        return
    rec = _get_wallet(uid)
    pm_id = (rec or {}).get("payment_method_id")
    if pm_id and enabled():
        try:
            _stripe_http(
                "POST", f"/payment_methods/{pm_id}/detach", {})
        except Exception as e:
            logger.warning(f"Wallet purge detach failed: {e}")
    for key in (_wallet_key(uid), "wallet_pending:" + uid,
                "wallet_charges:" + uid, "wallet_setup:" + uid):
        _del(key)


# --- Charge guard skeleton (Phase B fills the execution body) -------------------------


def _wallet_charge(uid: str, pending: dict) -> dict:
    """GUARD SKELETON ONLY — Phase A executes NO charge.

    Re-checks, at would-be execution time (the plan's rule):
    the dark gate, a connected active wallet, the per-charge
    ceiling, the daily ceiling (against today's succeeded
    audit total), and the tier's daily wallet count cap.
    Every refusal names its reason. When every guard passes
    it STILL charges nothing — it returns
    charging_not_available_phase_a. Phase B replaces that
    final return with the off-session PaymentIntent call,
    the audit write, and the wallet count spend (on success
    only, the trading/ordering convention)."""
    if not enabled():
        return {"ok": False, "error": "wallet_off"}
    rec = _connected_wallet(uid)
    if rec is None:
        return {"ok": False, "error": "wallet_not_connected"}
    try:
        amount = int((pending or {}).get("amount_cents", 0) or 0)
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        return {"ok": False, "error": "bad_amount"}
    tier = str((pending or {}).get("tier") or "free")
    import og_tiers
    ceiling_c = int(round(og_tiers.wallet_ceiling(tier) * 100))
    daily_c = int(round(og_tiers.wallet_daily_ceiling(tier) * 100))
    if amount > ceiling_c:
        return {"ok": False, "error": "above_ceiling",
                "ceiling_cents": ceiling_c}
    if charges_today_cents(uid) + amount > daily_c:
        return {"ok": False, "error": "above_daily_ceiling",
                "daily_ceiling_cents": daily_c}
    if _charges_today_count(uid) >= og_tiers.cap(tier, "wallet"):
        return {"ok": False, "error": "daily_count_cap",
                "cap": og_tiers.cap(tier, "wallet")}
    return {"ok": False, "error": "charging_not_available_phase_a"}


# --- Webhook handler (wiring is a documented follow-up — see module docstring) --------


WALLET_EVENT_TYPES = (
    "setup_intent.succeeded",
    "payment_intent.succeeded",
    "payment_intent.payment_failed",
    "charge.refunded",
    "charge.dispute.created",
)


def handle_wallet_event(event: dict) -> bool:
    """Apply one Stripe webhook event to the wallet stores.
    Filtered by metadata og_uid — an event without it is not
    a wallet event and changes nothing. Returns True when the
    event was a wallet event and was applied. Fail-safe:
    any internal problem returns False, never raises (the
    webhook caller must still 200 Stripe)."""
    try:
        if not isinstance(event, dict):
            return False
        etype = event.get("type") or ""
        if etype not in WALLET_EVENT_TYPES:
            return False
        obj = (event.get("data") or {}).get("object") or {}
        if not isinstance(obj, dict):
            return False
        meta = obj.get("metadata") or {}
        uid = meta.get("og_uid") or ""
        if not uid:
            return False
        if etype == "setup_intent.succeeded":
            pm_id = obj.get("payment_method")
            if isinstance(pm_id, dict):
                pm_id = pm_id.get("id")
            if not pm_id:
                return False
            _activate_from_payment_method(
                uid, str(pm_id), obj.get("customer") or "")
            return True
        if etype in ("payment_intent.succeeded",
                     "payment_intent.payment_failed"):
            ok = etype.endswith("succeeded")
            _record_charge(uid, {
                "amount_cents": obj.get("amount"),
                "currency": obj.get("currency") or "usd",
                "merchant": "OG",
                "description": obj.get("description") or "",
                "path": "off_session",
                "status": "succeeded" if ok else "failed",
                "stripe_payment_intent": obj.get("id"),
                "stripe_charge": obj.get("latest_charge"),
                "failure_code": ((obj.get("last_payment_error")
                                  or {}).get("code") if not ok
                                 else None),
            })
            return True
        # charge.refunded / charge.dispute.created
        _record_charge(uid, {
            "amount_cents": obj.get("amount"),
            "currency": obj.get("currency") or "usd",
            "merchant": "OG",
            "description": obj.get("description") or "",
            "path": "off_session",
            "status": "refunded" if etype == "charge.refunded"
            else "disputed",
            "stripe_payment_intent": obj.get("payment_intent"),
            "stripe_charge": obj.get("id"),
            "failure_code": None,
        })
        return True
    except Exception as e:
        logger.warning(f"Wallet webhook event failed: {e}")
        return False


# --- Status payload ---------------------------------------------------------------------


def _exp_str(rec: dict) -> Optional[str]:
    m, y = rec.get("exp_month"), rec.get("exp_year")
    if m and y:
        return f"{int(m):02d}/{int(y)}"
    return None


def status_payload(uid: str, tier: str) -> dict:
    """The /wallet/status body for an enabled wallet."""
    import og_tiers
    rec = _connected_wallet(uid) if uid else None
    return {
        "enabled": True,
        "connected": bool(rec),
        "brand": rec.get("brand") if rec else None,
        "last4": rec.get("last4") if rec else None,
        "exp": _exp_str(rec) if rec else None,
        "charges_today_cents": charges_today_cents(uid) if uid else 0,
        "ceiling_cents": int(round(
            og_tiers.wallet_ceiling(tier) * 100)),
        "daily_ceiling_cents": int(round(
            og_tiers.wallet_daily_ceiling(tier) * 100)),
    }


# --- Routes ---------------------------------------------------------------------------------


_COMPLETE_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OG AI — Wallet</title>
<style>
  body { background:#0a0a0a; color:#f2f2f2; font-family: Arial, sans-serif; margin:0; padding:32px 20px; text-align:center; }
  h1 { color:#ffc107; letter-spacing:1px; }
  p { color:#ccc; line-height:1.6; max-width:520px; margin:12px auto; }
  a.btn { display:inline-block; margin-top:18px; padding:12px 30px; border:2px solid #ffc107; border-radius:999px; color:#ffc107; text-decoration:none; font-weight:bold; background:rgba(255,193,7,0.08); }
</style></head><body>
<h1>💳 Wallet __STATE__</h1>
<p>__DETAIL__</p>
<a class="btn" href="/">← Back to OG</a>
</body></html>"""


def register_wallet_routes(app):

    @app.get("/wallet/status")
    def wallet_status(request: Request):
        if not enabled():
            return {"enabled": False}
        uid = _route_uid(request)
        return status_payload(uid, _route_tier(uid, request))

    @app.get("/wallet/history")
    def wallet_history(request: Request):
        if not enabled():
            return JSONResponse({"enabled": False},
                                status_code=404)
        if _session_required(request):
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        uid = _route_uid(request)
        return {"enabled": True,
                "charges": _charges(uid) if uid else []}

    @app.post("/wallet/setup")
    def wallet_setup(request: Request):
        if not enabled():
            return JSONResponse({"ok": False, "error": "wallet_off"},
                                status_code=404)
        if _session_required(request):
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        uid = _route_uid(request)
        if not uid:
            return {"ok": False, "error": "no visitor identity"}
        try:
            out = _start_setup(uid)
        except WalletStripeError as e:
            logger.warning(f"Wallet setup failed: {e}")
            return JSONResponse(
                {"ok": False, "error": "wallet_unavailable"},
                status_code=502)
        return out

    @app.get("/wallet/complete", response_class=HTMLResponse)
    def wallet_complete(request: Request):
        if not enabled():
            return HTMLResponse(
                _COMPLETE_HTML.replace("__STATE__", "unavailable")
                .replace("__DETAIL__",
                         "The wallet is not switched on yet."),
                status_code=404)
        uid = _route_uid(request)
        session_id = request.query_params.get("session_id") or ""
        rec = None
        if uid and session_id:
            try:
                rec = _complete_setup(uid, session_id)
            except WalletStripeError as e:
                logger.warning(f"Wallet complete failed: {e}")
                rec = None
        if rec:
            detail = (f"Your {rec.get('brand') or 'card'} ending in "
                      f"{rec.get('last4') or '····'} is connected. "
                      "OG never saw the number — Stripe holds it. "
                      "You can remove it any time from Connections "
                      "→ Wallet.")
            state = "connected ✓"
        else:
            detail = ("That setup didn't finish, so nothing was "
                      "connected. Open OG and tap Connect a payment "
                      "method to try again.")
            state = "not connected"
        return HTMLResponse(
            _COMPLETE_HTML.replace("__STATE__", state)
            .replace("__DETAIL__", detail))

    @app.post("/wallet/remove")
    def wallet_remove(request: Request):
        if not enabled():
            return JSONResponse({"ok": False, "error": "wallet_off"},
                                status_code=404)
        if _session_required(request):
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        uid = _route_uid(request)
        if not uid:
            return {"ok": False, "error": "no visitor identity"}
        try:
            _remove_wallet(uid)
        except WalletStripeError as e:
            logger.warning(f"Wallet remove failed: {e}")
            return JSONResponse(
                {"ok": False, "error": "wallet_unavailable"},
                status_code=502)
        return {"ok": True}
