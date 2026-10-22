"""Ticket L — the Financial Smart Agent's BANK picture
(OG W, dashboards wave follow-on, Brent 2026-10-19).

Brent's spec: the swipe-right Financial Smart Agent shows
the SIGNED-IN USER'S OWN bank + budget picture — "all my
bank information there like a budget-bank — kinda like
this stuff you tell me every day for my spending."

Binding rules for this module:

- REAL DATA ONLY. Every number in the payload is read
  from the caller's own Plaid item through og_plaid's
  own API funnel (/accounts/get, /transactions/get) —
  the same reads the chat banking answers use. Nothing
  is computed from anything but those two payloads, and
  nothing is ever invented: no bank connected reads as
  the connect state, a dead item reads as reconnect, an
  upstream failure reads as an honest error.
- READ-ONLY. This module registers exactly one GET.
  There is no transfer, payment, or write path here —
  OG's bank actions live in the prepare -> approve ->
  execute lanes, never on this dashboard (the og_plaid
  posture: OG can look, never move money).
- PER-USER, SERVER-SIDE. The caller is resolved from
  the account session (the og_avatar_identity seam);
  a request with no signed-in account is refused 401.
  The Plaid entry is looked up ONLY under identifiers
  this request itself presented (its account uid, its
  ogai_uid cookie — the key the connect flow stored
  under). No other user's entry can be named here.
- NO BUDGET LAYER IS INVENTED. The tree holds no
  budget records or spending targets for a user, so
  there are no budget bars and no "vs budget" figures.
  What ships instead is the honest derivation: money
  in / money out and spending grouped by Plaid's own
  transaction categories over the fetched window —
  every figure a sum of the very transactions listed,
  with the counted number stated in the payload.

Kill switch: OG_FINDASH_ENABLED — enabled unless the
env value is one of 0/false/no/off (the og_finagent
pattern). Disabled, the route answers 404.

Handlers are plain def (FastAPI threadpool), never
async: the Plaid reads are sync network work and the
Main Thread Rule keeps waiting off the event loop.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# --- Kill switch ---------------------------------------------------------------


def findash_enabled() -> bool:
    """OG_FINDASH_ENABLED is the kill switch: enabled
    unless the env value is an explicit off."""
    return os.environ.get(
        "OG_FINDASH_ENABLED", "true").strip().lower() not in (
            "0", "false", "no", "off")


_TXN_WINDOW_DAYS = 30
_TXN_FETCH_COUNT = 50
_TXN_SHOW = 15


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _account_uid(request) -> Optional[str]:
    """The signed-in account uid behind the request, or
    None — the og_avatar_identity seam verbatim. A
    visitor cookie alone is NOT a signed-in account."""
    try:
        import og_accounts
        return og_accounts.session_account_uid(
            request.cookies.get(og_accounts.SESSION_COOKIE))
    except Exception:
        return None


def _plaid():
    """og_plaid, imported lazily (house style) — its
    store, its API funnel, its dead-item law."""
    import og_plaid
    return og_plaid


def _find_entry(plaid, account_uid: str, cookie_uid: str):
    """The caller's own Plaid entry + the key it was
    found under. Only identifiers THIS request presented
    are ever tried: the account uid, then the ogai_uid
    cookie (the connect flow stores under the cookie)."""
    for key in (account_uid, cookie_uid):
        if key:
            entry = plaid._plaid_connection(key)
            if entry:
                return entry, key
    return None, None


def _category_of(txn: Dict) -> str:
    pfc = txn.get("personal_finance_category")
    if isinstance(pfc, dict) and pfc.get("primary"):
        return str(pfc["primary"]).replace("_", " ").capitalize()
    cats = txn.get("category")
    if isinstance(cats, list) and cats:
        return str(cats[0])
    return "Other"


def _num(value) -> Optional[float]:
    try:
        if value is None:
            return None
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def bank_for(request) -> Dict:
    """The caller's bank picture, as a state payload.
    The route owns the 401; this composer owns the
    states: unavailable / connect / reconnect / error /
    connected."""
    plaid = _plaid()
    if not plaid.PLAID_ENABLED:
        return {
            "state": "unavailable", "enabled": False,
            "connected": False,
            "reason": "Bank connect isn't switched on yet, "
                      "so there is no bank picture to show. "
                      "OG won't fake one — when connect goes "
                      "live, your real balances land here.",
            "as_of": _now_iso(),
        }
    account_uid = _account_uid(request)
    cookie_uid = ""
    try:
        cookie_uid = request.cookies.get("ogai_uid", "") or ""
    except Exception:
        cookie_uid = ""
    entry, entry_key = _find_entry(plaid, account_uid or "",
                                   cookie_uid)
    if not entry:
        return {
            "state": "connect", "enabled": True,
            "connected": False,
            "reason": "No bank is connected to your account "
                      "yet. Connect through Plaid — OG never "
                      "sees or stores your bank password — "
                      "and your real balances, recent "
                      "activity, and spending by category "
                      "show up here. Read-only: OG can look, "
                      "never move money.",
            "as_of": _now_iso(),
        }
    token = entry.get("access_token", "")
    status, data = plaid._api("/accounts/get",
                              {"access_token": token})
    if status != 200 or not data:
        if plaid._error_code(data) in plaid._DEAD_ITEM_CODES:
            plaid._drop_entry(entry_key)
            return {
                "state": "reconnect", "enabled": True,
                "connected": False,
                "reason": "Your bank connection stopped "
                          "working (the bank wants you to "
                          "sign in again), so OG disconnected "
                          "it here. Reconnect to see your "
                          "bank picture — OG won't show "
                          "stale numbers as if they were "
                          "live.",
                "as_of": _now_iso(),
            }
        return {
            "state": "error", "enabled": True,
            "connected": True,
            "reason": "Your bank is connected, but Plaid "
                      "didn't answer just now, so no "
                      "balances are shown rather than old "
                      "ones. Try again in a moment.",
            "as_of": _now_iso(),
        }
    accounts: List[Dict] = []
    for a in (data.get("accounts") or []):
        bal = a.get("balances") or {}
        name = a.get("official_name") or a.get("name") \
            or "Account"
        accounts.append({
            "name": name,
            "mask": a.get("mask") or "",
            "type": a.get("type") or "",
            "subtype": a.get("subtype") or "",
            "current": _num(bal.get("current")),
            "available": _num(bal.get("available")),
            "currency": bal.get("iso_currency_code") or "USD",
        })

    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=_TXN_WINDOW_DAYS)
    tstatus, tdata = plaid._api("/transactions/get", {
        "access_token": token,
        "start_date": start.isoformat(),
        "end_date": today.isoformat(),
        "options": {"count": _TXN_FETCH_COUNT, "offset": 0},
    })
    transactions: Optional[List[Dict]] = None
    spending: Optional[Dict] = None
    txns_note = ""
    if tstatus != 200 or not tdata:
        if plaid._error_code(tdata) in plaid._DEAD_ITEM_CODES:
            plaid._drop_entry(entry_key)
            return {
                "state": "reconnect", "enabled": True,
                "connected": False,
                "reason": "Your bank connection stopped "
                          "working (the bank wants you to "
                          "sign in again), so OG disconnected "
                          "it here. Reconnect to see your "
                          "bank picture — OG won't show "
                          "stale numbers as if they were "
                          "live.",
                "as_of": _now_iso(),
            }
        txns_note = ("Recent activity didn't come back "
                     "from Plaid just now — balances above "
                     "are live, activity is not, and OG "
                     "won't fill the gap with guesses.")
    else:
        raw = [t for t in (tdata.get("transactions") or [])
               if isinstance(t, dict)]
        raw.sort(key=lambda t: str(t.get("date") or ""),
                 reverse=True)
        shown: List[Dict] = []
        for t in raw[:_TXN_SHOW]:
            amount = _num(t.get("amount"))
            shown.append({
                "date": t.get("date") or "",
                "name": t.get("name")
                        or t.get("merchant_name")
                        or "Transaction",
                # Plaid's sign: positive = money OUT.
                # The dashboard's sign: negative = out.
                "amount": (-amount) if amount is not None
                          else None,
                "pending": bool(t.get("pending")),
                "category": _category_of(t),
            })
        transactions = shown
        counted = 0
        money_out = 0.0
        money_in = 0.0
        by_cat: Dict[str, float] = {}
        for t in raw:
            if t.get("pending"):
                continue  # pending can vanish; sums stay
                # on settled money only, stated below.
            amount = _num(t.get("amount"))
            if amount is None:
                continue
            counted += 1
            if amount > 0:  # Plaid positive = money out
                money_out += amount
                cat = _category_of(t)
                by_cat[cat] = by_cat.get(cat, 0.0) + amount
            elif amount < 0:
                money_in += -amount
        spending = {
            "window_days": _TXN_WINDOW_DAYS,
            "counted": counted,
            "money_out": round(money_out, 2),
            "money_in": round(money_in, 2),
            "by_category": [
                {"category": k, "total": round(v, 2)}
                for k, v in sorted(
                    by_cat.items(),
                    key=lambda kv: -kv[1])],
            "basis": "Sums of your settled transactions "
                     "from the last 30 days, grouped by "
                     "Plaid's own categories. OG keeps no "
                     "budget targets for you, so there is "
                     "no 'vs budget' figure — just the "
                     "real spending itself.",
        }
    return {
        "state": "connected", "enabled": True,
        "connected": True,
        "institution": entry.get("institution", ""),
        "connected_at": entry.get("connected", ""),
        "accounts": accounts,
        "transactions": transactions,
        "transactions_note": txns_note,
        "spending": spending,
        "read_only": True,
        "as_of": _now_iso(),
    }


def register_findash_routes(app):
    """GET /finagent/bank — the signed-in caller's own
    bank picture for the Financial Smart Agent."""

    @app.get("/finagent/bank")
    # Main Thread Rule discipline: def, not async def —
    # the body is sync Plaid network work; FastAPI runs
    # def handlers in its threadpool.
    def finagent_bank(request: Request):
        if not findash_enabled():
            raise HTTPException(status_code=404,
                                detail="Not found")
        if _account_uid(request) is None:
            raise HTTPException(
                status_code=401, detail="sign_in_required")
        return bank_for(request)
