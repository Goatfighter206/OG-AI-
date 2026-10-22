"""Ticket L4 — the Financial Smart Agent is a TRUE
separate page (OG W, Brent 2026-10-22).

Brent's discriminator (diagnosis og-l2-diagnosis-2026-
10-22.md): hard reload, still ghosting — the stale-copy
cause died and the nested-scroller iOS repaint mechanism
was convicted. L2 tuned the overlay stack; the phone
voted on stack-tuning twice. So the overlay is not tuned
a third time: the Financial Smart Agent leaves the app
document entirely and lives at GET /fin — its own route,
its own document, body scroll only. No position:fixed
surface, no scroller inside a surface, no app layers
underneath it. Brent's No Pile-Up law taken literally.

What this module registers:

- GET /fin — the standalone document (fin.html at the
  repo root, next to index_epic.html). Gating parity
  with the main page, one notch stricter where the
  diagnosis demands it: the main page serves its HTML
  openly and walls the UI client-side, but a deep link
  must never strand a signed-out visitor on a data page,
  so /fin resolves the account session server-side and
  bounces a signed-out hit to "/" — where the sign-in +
  18+ age flow lives (accounts are the 18+ artifact:
  signup enforces date of birth server-side). A signed-
  in account always passes; the three data reads below
  still 401 on their own, exactly like /finagent/bank.
- GET /finagent/budget — Ticket L3 section 2, THE
  BUDGET: the Monthly Budget workbook's shape (income /
  fixed / variable groups, estimate vs actual, the
  bottom line: total income, total expenses, leftover,
  savings rate). Actuals are computed from the caller's
  own Plaid transactions over a 90-day window (the
  workbook's actuals were 90-day bank averages; monthly
  figures are the window totals divided by 3), bucketed
  by Plaid's own categories through the documented map
  below — every settled dollar lands in exactly one
  row, so the totals are exact sums of real money.
  Estimates: the tree holds NO budget-estimate store
  for a user (the Ticket L audit's finding), so the
  payload carries estimates as null with the honest
  note — the estimate column exists in the shape and
  is never filled with invented numbers.
- GET /finagent/guard — Ticket L3 section 3, MONEY
  GUARD: the Cash Guard page's shape — in the bank
  now, paycheck landed, cleared so far, still
  reserved, safe to spend, day by day. Reserved bills
  and safe-to-spend need a bills record; OG holds
  none, so those two read as the honest unavailable
  state (never a guessed number). The rest is real:
  balances from /accounts/get, the paycheck and the
  cleared total and the day-by-day from the caller's
  settled transactions; earlier day balances are
  worked BACK from today's real balance using those
  same transactions, and the payload says so.

Binding rules (the og_findash posture, inherited):

- REAL DATA ONLY, from the caller's own Plaid item
  through og_plaid's own API funnel. No bank reads
  as the connect state; a dead item as reconnect (and
  the entry is dropped, mirroring og_plaid's law); an
  upstream failure as an honest error with no stale
  numbers.
- READ-ONLY. Exactly three GETs are registered here
  and two of them are reads of the caller's own bank;
  there is no transfer, payment, or write path.
- PER-USER, SERVER-SIDE. The caller is resolved from
  the account session (the og_avatar_identity seam,
  verbatim from og_findash); the Plaid entry is
  looked up ONLY under identifiers this request
  itself presented (account uid, then the ogai_uid
  cookie). A visitor cookie alone is NOT an account.

Kill switch: OG_FINPAGE_ENABLED — enabled unless the
env value is one of 0/false/no/off (the og_findash
pattern). Disabled, all three routes answer 404.

Cache defense (diagnosis section 6A rides this round):
the /fin document is served Cache-Control: no-cache,
so a resumed tab revalidates it instead of silently
running an old build; app.py carries the same header
for "/" and the statics, and both documents carry the
pageshow persisted->reload guard.

Handlers are plain def (FastAPI threadpool), never
async: the Plaid reads are sync network work and the
Main Thread Rule keeps waiting off the event loop.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

logger = logging.getLogger(__name__)

# --- Kill switch ---------------------------------------------------------------


def finpage_enabled() -> bool:
    """OG_FINPAGE_ENABLED is the kill switch: enabled
    unless the env value is an explicit off."""
    return os.environ.get(
        "OG_FINPAGE_ENABLED", "true").strip().lower() not in (
            "0", "false", "no", "off")


# --- Shared seams (og_findash's, verbatim in shape) -----------------------------


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


def _num(value) -> Optional[float]:
    try:
        if value is None:
            return None
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _state_unavailable() -> Dict:
    return {
        "state": "unavailable", "enabled": False,
        "connected": False,
        "reason": "Bank connect isn't switched on yet, "
                  "so there is no bank picture to show. "
                  "OG won't fake one — when connect goes "
                  "live, your real numbers land here.",
        "as_of": _now_iso(),
    }


def _state_connect() -> Dict:
    return {
        "state": "connect", "enabled": True,
        "connected": False,
        "reason": "No bank is connected to your account "
                  "yet. Connect through Plaid — OG never "
                  "sees or stores your bank password — "
                  "and this page fills with your real "
                  "bank picture. Read-only: OG can look, "
                  "never move money.",
        "as_of": _now_iso(),
    }


def _state_reconnect() -> Dict:
    return {
        "state": "reconnect", "enabled": True,
        "connected": False,
        "reason": "Your bank connection stopped "
                  "working (the bank wants you to "
                  "sign in again), so OG disconnected "
                  "it here. Reconnect to see your bank "
                  "picture — OG won't show stale "
                  "numbers as if they were live.",
        "as_of": _now_iso(),
    }


def _state_error() -> Dict:
    return {
        "state": "error", "enabled": True,
        "connected": True,
        "reason": "Your bank is connected, but Plaid "
                  "didn't answer just now, so no "
                  "numbers are shown rather than old "
                  "ones. Try again in a moment.",
        "as_of": _now_iso(),
    }


def _open_bank(request):
    """Resolve the caller's bank. Returns ("state",
    payload) when a truth state answers the request, or
    ("ok", plaid, token) when the caller has a live
    item. Accounts are read here, once, because every
    section needs the dead-item verdict before any
    number is shown: a dead item drops its entry and
    answers reconnect, an upstream failure answers the
    honest error — the og_findash order, kept."""
    plaid = _plaid()
    if not plaid.PLAID_ENABLED:
        return ("state", _state_unavailable())
    account_uid = _account_uid(request)
    cookie_uid = ""
    try:
        cookie_uid = request.cookies.get("ogai_uid", "") or ""
    except Exception:
        cookie_uid = ""
    entry, entry_key = _find_entry(plaid, account_uid or "",
                                   cookie_uid)
    if not entry:
        return ("state", _state_connect())
    token = entry.get("access_token", "")
    status, data = plaid._api("/accounts/get",
                              {"access_token": token})
    if status != 200 or not data:
        if plaid._error_code(data) in plaid._DEAD_ITEM_CODES:
            plaid._drop_entry(entry_key)
            return ("state", _state_reconnect())
        return ("state", _state_error())
    return ("ok", plaid, token, entry, data)


def _fetch_transactions(plaid, token: str, days: int,
                        max_count: int):
    """The caller's settled + pending transactions over
    the window, newest first, through the funnel.
    Returns ("ok", [txn, ...]) or ("state", payload)
    on a dead item; an ordinary upstream failure reads
    ("error", None) and the caller decides what an
    honest partial answer looks like (balances are
    already proven live by then)."""
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days)
    out: List[Dict] = []
    offset = 0
    total = None
    while len(out) < max_count:
        count = min(500, max_count - len(out))
        status, data = plaid._api("/transactions/get", {
            "access_token": token,
            "start_date": start.isoformat(),
            "end_date": today.isoformat(),
            "options": {"count": count, "offset": offset},
        })
        if status != 200 or not data:
            if plaid._error_code(data) in plaid._DEAD_ITEM_CODES:
                return ("state", _state_reconnect())
            return ("error", None)
        batch = [t for t in (data.get("transactions") or [])
                 if isinstance(t, dict)]
        out.extend(batch)
        if total is None:
            total = data.get("total_transactions")
        offset += len(batch)
        if not batch:
            break
        if len(batch) < count:
            break  # a short page is the end of the data
        if isinstance(total, int) and offset >= total:
            break
    out.sort(key=lambda t: str(t.get("date") or ""),
             reverse=True)
    return ("ok", out)


def _pfc(txn: Dict):
    """(primary, detailed) of Plaid's own category,
    upper-cased, or ("", "") when Plaid gave none."""
    pfc = txn.get("personal_finance_category")
    if isinstance(pfc, dict):
        return (str(pfc.get("primary") or "").upper(),
                str(pfc.get("detailed") or "").upper())
    return ("", "")


# --- Ticket L3 section 2: THE BUDGET -------------------------------------------
#
# The Monthly Budget workbook's rows, in its order, that
# bank data can actually see. The map is Plaid-category
# -> row; anything unmapped lands in the group's own
# "Other" row, so every settled dollar is counted
# exactly once and the group totals are exact.
#
# Rows deliberately NOT in the map, because Plaid data
# carries no signal for them and inventing them would
# be fake: "ATM cash" (no category marks a withdrawal
# as ATM money once it leaves) and "Coinbase (crypto)"
# (a merchant name in Brent's own sheet, hand-audited
# there — name-matching here would misfile strangers'
# money). Both are Brent's workbook rows for Brent's
# hand-kept sheet; this page shows only what the bank
# itself can prove.

_BUDGET_WINDOW_DAYS = 90
_BUDGET_MONTHS = 3
_BUDGET_MAX_TXNS = 2000

# (group, row label) in workbook order.
_BUDGET_ROWS = [
    ("Income", "Paychecks"),
    ("Income", "Other deposits & refunds"),
    ("Fixed expenses", "Rent"),
    ("Fixed expenses", "Car payments"),
    ("Fixed expenses", "Phone"),
    ("Fixed expenses", "Water / sewer / power"),
    ("Fixed expenses", "Cable & internet"),
    ("Fixed expenses", "Garbage"),
    ("Fixed expenses", "Subscriptions"),
    ("Fixed expenses", "Other fixed"),
    ("Variable expenses", "Food & drink"),
    ("Variable expenses", "Gas & transportation"),
    ("Variable expenses", "General merchandise"),
    ("Variable expenses", "Venmo / Zelle (person-to-person)"),
    ("Variable expenses", "Entertainment"),
    ("Variable expenses", "Bank fees"),
    ("Variable expenses", "Other variable"),
]


def _budget_row(primary: str, detailed: str,
                money_in: bool) -> str:
    """The workbook row one settled transaction belongs
    to. Money in is bucketed by direction first (a
    refund is income-shaped money whatever its
    category); money out by Plaid's own category."""
    if money_in:
        return "Paychecks" if primary == "INCOME" \
            else "Other deposits & refunds"
    if detailed == "RENT_AND_UTILITIES_RENT":
        return "Rent"
    if detailed == "LOAN_PAYMENTS_AUTO_PAYMENT":
        return "Car payments"
    if detailed == "RENT_AND_UTILITIES_TELEPHONE_SERVICES":
        return "Phone"
    if detailed in ("RENT_AND_UTILITIES_WATER",
                    "RENT_AND_UTILITIES_GAS_AND_ELECTRICITY",
                    "RENT_AND_UTILITIES_SEWAGE_AND_WASTE_MANAGEMENT",
                    "RENT_AND_UTILITIES_UTILITIES"):
        return "Water / sewer / power"
    if detailed == "RENT_AND_UTILITIES_INTERNET_AND_CABLE":
        return "Cable & internet"
    if "SUBSCRIPTION" in detailed:
        return "Subscriptions"
    if primary == "LOAN_PAYMENTS":
        return "Other fixed"
    if primary == "RENT_AND_UTILITIES":
        return "Other fixed"
    if primary == "FOOD_AND_DRINK":
        return "Food & drink"
    if primary == "TRANSPORTATION":
        return "Gas & transportation"
    if primary == "GENERAL_MERCHANDISE":
        return "General merchandise"
    if primary == "TRANSFER_OUT":
        return "Venmo / Zelle (person-to-person)"
    if primary == "ENTERTAINMENT":
        return "Entertainment"
    if primary == "BANK_FEES":
        return "Bank fees"
    return "Other variable"


def budget_for(request) -> Dict:
    """The caller's budget picture in the workbook's
    shape: groups of rows with monthly actuals, the
    bottom line, and estimates carried as null — the
    tree holds no estimate store, so the column is
    honest about being empty."""
    opened = _open_bank(request)
    if opened[0] == "state":
        return opened[1]
    _, plaid, token, _entry, _accounts = opened
    got = _fetch_transactions(plaid, token,
                              _BUDGET_WINDOW_DAYS,
                              _BUDGET_MAX_TXNS)
    if got[0] == "state":
        return got[1]
    if got[0] == "error":
        return _state_error()
    raw = got[1]
    totals: Dict[str, float] = {label: 0.0
                                for _g, label in _BUDGET_ROWS}
    counted = 0
    truncated = len(raw) >= _BUDGET_MAX_TXNS
    for t in raw:
        if t.get("pending"):
            continue  # pending can vanish; actuals stay
            # on settled money only, stated in the basis.
        amount = _num(t.get("amount"))
        if amount is None or amount == 0:
            continue
        counted += 1
        primary, detailed = _pfc(t)
        # Plaid's sign: positive = money OUT.
        row = _budget_row(primary, detailed, amount < 0)
        totals[row] += abs(amount)

    def monthly(label: str) -> float:
        return round(totals[label] / _BUDGET_MONTHS, 2)

    groups: List[Dict] = []
    for group_name in ("Income", "Fixed expenses",
                       "Variable expenses"):
        rows = [{"label": label, "actual": monthly(label)}
                for g, label in _BUDGET_ROWS
                if g == group_name]
        groups.append({
            "name": group_name,
            "rows": rows,
            "total": round(
                sum(totals[label]
                    for g, label in _BUDGET_ROWS
                    if g == group_name) / _BUDGET_MONTHS, 2),
        })
    total_income = groups[0]["total"]
    total_expenses = round(groups[1]["total"]
                           + groups[2]["total"], 2)
    leftover = round(total_income - total_expenses, 2)
    savings_rate = (round(leftover / total_income, 4)
                    if total_income > 0 else None)
    basis = (
        "Actuals are sums of your settled transactions "
        "from the last 90 days, divided by 3 for the "
        "monthly figure — the same 90-day-average basis "
        "as the Monthly Budget workbook — grouped by "
        "Plaid's own categories into the workbook's "
        "rows. Pending transactions are excluded (they "
        "can vanish). " + (
            "The window held more transactions than this "
            "read counts, so the oldest days are missing "
            "and the figures run low. " if truncated else
            "") +
        "OG holds no budget estimates for your account, "
        "so the estimate column is empty rather than "
        "invented.")
    return {
        "state": "connected", "enabled": True,
        "connected": True,
        "window_days": _BUDGET_WINDOW_DAYS,
        "months": _BUDGET_MONTHS,
        "counted": counted,
        "groups": groups,
        "total_income": total_income,
        "total_expenses": total_expenses,
        "leftover": leftover,
        "savings_rate": savings_rate,
        "estimates": None,
        "basis": basis,
        "read_only": True,
        "as_of": _now_iso(),
    }


# --- Ticket L3 section 3: MONEY GUARD -------------------------------------------

_GUARD_WINDOW_DAYS = 30
_GUARD_MAX_TXNS = 500


def guard_for(request) -> Dict:
    """The caller's money-guard picture in the Cash
    Guard page's shape. Reserved bills and safe-to-
    spend need a bills record OG does not hold, so
    they read as the honest unavailable state; the
    bank position, the paycheck, what has cleared
    since it landed, and the day-by-day are computed
    from the caller's real accounts + transactions."""
    opened = _open_bank(request)
    if opened[0] == "state":
        return opened[1]
    _, plaid, token, _entry, accounts_data = opened
    accounts: List[Dict] = []
    in_bank_now = 0.0
    for a in (accounts_data.get("accounts") or []):
        bal = a.get("balances") or {}
        current = _num(bal.get("current"))
        name = a.get("official_name") or a.get("name") \
            or "Account"
        accounts.append({
            "name": name,
            "mask": a.get("mask") or "",
            "current": current,
            "currency": bal.get("iso_currency_code")
            or "USD",
        })
        # "In the bank now" is liquid money: depository
        # balances (checking / savings / cash). Credit
        # and loan balances are debts, never spend
        # position; an untyped account with a balance
        # counts (Plaid always types real items).
        if current is not None and (
                (a.get("type") or "depository")
                == "depository"):
            in_bank_now += current
    in_bank_now = round(in_bank_now, 2)

    got = _fetch_transactions(plaid, token,
                              _GUARD_WINDOW_DAYS,
                              _GUARD_MAX_TXNS)
    if got[0] == "state":
        return got[1]
    txns_note = ""
    paycheck = None
    cleared = None
    days: List[Dict] = []
    if got[0] == "error":
        txns_note = ("Recent activity didn't come back "
                     "from Plaid just now — the bank "
                     "position above is live, activity "
                     "is not, and OG won't fill the gap "
                     "with guesses.")
    else:
        settled = [t for t in got[1]
                   if not t.get("pending")]
        # Paycheck: the newest settled INCOME money-in;
        # failing that, the newest settled money-in of
        # any kind, named as what it is.
        for t in settled:
            amount = _num(t.get("amount"))
            if amount is None or amount >= 0:
                continue
            primary, _d = _pfc(t)
            if primary == "INCOME":
                paycheck = {
                    "name": t.get("name")
                            or t.get("merchant_name")
                            or "Paycheck",
                    "amount": round(-amount, 2),
                    "date": t.get("date") or "",
                }
                break
        if paycheck is None:
            for t in settled:
                amount = _num(t.get("amount"))
                if amount is None or amount >= 0:
                    continue
                paycheck = {
                    "name": t.get("name")
                            or t.get("merchant_name")
                            or "Deposit",
                    "amount": round(-amount, 2),
                    "date": t.get("date") or "",
                }
                break
        # Cleared so far: settled money out since the
        # paycheck landed (its date counts — a charge
        # that day cleared against it). No paycheck in
        # the window: the whole window is the basis,
        # and the payload says which.
        since = paycheck["date"] if paycheck else ""
        cleared_total = 0.0
        cleared_count = 0
        for t in settled:
            amount = _num(t.get("amount"))
            if amount is None or amount <= 0:
                continue
            if since and str(t.get("date") or "") < since:
                continue
            cleared_total += amount
            cleared_count += 1
        cleared = {
            "total": round(cleared_total, 2),
            "count": cleared_count,
            "since": since,
            "basis": ("since your paycheck landed"
                      if since else
                      "in the last 30 days — no paycheck "
                      "shows in this window"),
        }
        # Day by day: net change per day from the real
        # transactions, balances worked BACK from
        # today's real in-bank total.
        net_by_day: Dict[str, float] = {}
        for t in settled:
            amount = _num(t.get("amount"))
            if amount is None:
                continue
            day = str(t.get("date") or "")
            if not day:
                continue
            # Human sign: negative = money out.
            net_by_day[day] = net_by_day.get(day, 0.0) \
                - amount
        today = datetime.now(timezone.utc).date() \
            .isoformat()
        if today not in net_by_day:
            net_by_day[today] = 0.0
        running = in_bank_now
        for day in sorted(net_by_day, reverse=True):
            net = round(net_by_day[day], 2)
            days.append({
                "date": day,
                "net": net,
                "balance_after": round(running, 2),
            })
            running = round(running - net, 2)
    return {
        "state": "connected", "enabled": True,
        "connected": True,
        "accounts": accounts,
        "in_bank_now": in_bank_now,
        "paycheck": paycheck,
        "cleared": cleared,
        "days": days,
        "transactions_note": txns_note,
        "reserved": {
            "available": False,
            "bills": [],
            "note": "OG has no bills reserved for you "
                    "on file — reserving bills is what "
                    "turns a balance into a safe-to-"
                    "spend number, and Money Guard "
                    "never guesses one.",
        },
        "safe_to_spend": None,
        "basis": "In the bank now is the sum of your "
                 "depository balances, read live. Day "
                 "balances before today are worked back "
                 "from that total using your settled "
                 "transactions — days with no activity "
                 "aren't listed.",
        "read_only": True,
        "as_of": _now_iso(),
    }


# --- Routes ----------------------------------------------------------------------


def register_finpage_routes(app):
    """GET /fin (the standalone Financial Smart Agent
    document) + GET /finagent/budget + GET
    /finagent/guard (its two new reads)."""

    @app.get("/fin")
    # Main Thread Rule discipline: def, not async def —
    # the session read is a sync store read; FastAPI
    # runs def handlers in its threadpool.
    def fin_page(request: Request):
        if not finpage_enabled():
            raise HTTPException(status_code=404,
                                detail="Not found")
        if _account_uid(request) is None:
            # Deep-link law (diagnosis section 4): a
            # signed-out hit bounces to the app, where
            # the sign-in + 18+ age flow lives — never
            # a stranded data page, never any numbers.
            return RedirectResponse(url="/",
                                    status_code=302)
        return FileResponse(
            "fin.html",
            headers={"Cache-Control": "no-cache"})

    @app.get("/finagent/budget")
    def finagent_budget(request: Request):
        if not finpage_enabled():
            raise HTTPException(status_code=404,
                                detail="Not found")
        if _account_uid(request) is None:
            raise HTTPException(
                status_code=401, detail="sign_in_required")
        return budget_for(request)

    @app.get("/finagent/guard")
    def finagent_guard(request: Request):
        if not finpage_enabled():
            raise HTTPException(status_code=404,
                                detail="Not found")
        if _account_uid(request) is None:
            raise HTTPException(
                status_code=401, detail="sign_in_required")
        return guard_for(request)
