"""Ticket W — OG WATCH (general): OG watches what the
user asks, and reports back in the Feed.

Filed 2026-10-22 on Brent's order: "he does not watch
anything for the user even when the user asks. He need
to be a lot more helpful."

AUDIT (2026-10-23, cand-watch/REPORT.md has the full
verdict). OG already had watch-shaped pieces, all
narrow, none of them this ticket:

- og_watch (Round 29) watches FACEBOOK MARKETPLACE
  only, through the Steel browser; its claims require
  the word "marketplace", and its alerts ride the next
  chat exchange — never the Feed.
- og_mpwatch (Round 54) is the Marketplace haggle
  extension of the same.
- og_monitor (Round 18) watches PRICES, but a price
  watch fires ONCE and is delivered at the top of the
  next chat — no record the user owns, no cadence, no
  Feed report.
- og_notify IS the Feed seam: record(uid, kind, ...)
  lands in the mailbox og_library.feed_rows renders.
  It had no watch-report kind and no producer that
  reports on a cadence.
- Gmail (og_google_hands, Round 12) is READ-ONLY by
  scope (gmail.readonly): OG can read and count mail
  but cannot label, move, or file it. Nothing in the
  tree holds a modify scope.

THIS MODULE is the missing general layer, built only
from those pieces:

- ONE written record per watch (No Junk Drawer):
  what, source, check cadence, report cadence (DAILY
  by default; the user's stated frame overrides),
  stop/expiry. One authoritative store, an owner door
  (chat list/stop + GET /watches, the watch's own
  page), records die with their target, with the
  account (purge_owner, the og_store_ready cascade),
  or when stopped/expired.
- ASKING CREATES THE WATCH: the chat seam claims
  watch asks, creates exactly one record, and answers
  with a grounded directive the persona voices — what
  is watched and when it reports (Ticket V: the reply
  serves the want). An ask outside OG's reach creates
  NOTHING and says plainly what would connect it.
- CHECKS RUN OFF THE MAIN THREAD (Main Thread rule):
  an asyncio scheduler in the og_watch pattern —
  sleep, then asyncio.to_thread(run_tick) — claims
  due watches ahead (next_check/next_report move
  forward BEFORE any check runs, so two workers can
  never double-run or double-report), then checks.
- REPORTS LAND IN FEED: at the report cadence, one
  og_notify record (kind "watch_report") per watch —
  what changed, plain and short. No change = a
  one-line "no change" report, or silence when the
  watch's own setting says so. Never invented
  activity: a checker that cannot read its source
  says so in the report instead of guessing.
- SOURCES (what a check can honestly read today):
  price (og_monitor.get_price — crypto + stocks),
  web (a URL the user names; the page's text hash +
  title), email (the user's connected Gmail, read
  seam: newest match + count for their query),
  library (their own Library/artifact state).
- EMAIL SEPARATION (ticket item 4), honestly split:
  a watch on email with a filing rule ("file receipts
  under Receipts") stores the rule as part of the
  record and every report names the matches the rule
  WOULD file. Actually applying a Gmail label needs
  the gmail.modify scope the connection does not
  hold — this module never claims a filing it did
  not do. The scope upgrade + re-consent is the
  named gap in the report, a separate ticket.
- APPROVALS HOLD (ticket item 6): a watch ask whose
  action spends, sends, buys, or trades creates NO
  watch. It parks a 10-minute pending (the house
  approval expiry) and answers that OG can watch it
  and report, but the action itself needs the user's
  approval through the existing gates when the
  moment comes. Watch is never a side door.
- STUCK WATCHES PARK THEMSELVES (the approved
  numbers, mirroring og_watch's Round 321 watchdog):
  a watch stuck in needs_connect / failing for 7
  days parks to "stalled" — checks stop, ONE notice
  is queued, "resume my watch" revives it.

Kill switch: OG_USERWATCH_ENABLED — enabled unless
the env value is one of 0/false/no/off (the house
pattern). Disabled, the routes answer 404, the
scheduler never starts, and the chat seam claims
nothing, so chat behaves exactly as before.

Handlers are plain def (FastAPI threadpool); the
scheduler loop is the only asyncio here and it hands
all work to a thread. app.py carries only the import,
the tool install, and the register lines.
"""

import asyncio
import hashlib
import html
import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Dict, List, Optional

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)


# --- Kill switch ---------------------------------------------------------------


def userwatch_enabled() -> bool:
    """OG_USERWATCH_ENABLED is the kill switch: enabled
    unless the env value is an explicit off."""
    return os.environ.get(
        "OG_USERWATCH_ENABLED", "true").strip().lower() not in (
            "0", "false", "no", "off")


# --- Config (the approved No Junk Drawer numbers) -------------------------------

TICK_SECONDS = max(15, int(os.getenv("OG_USERWATCH_TICK_SECONDS", "60")))
MAX_WATCHES = 25             # per user — a watch list is not a junk drawer
DEFAULT_REPORT_S = 86400     # the ticket: once a day by default
DEFAULT_CHECK_MIN = 60       # checks hourly unless the frame says otherwise
STALLED_AFTER_S = 7 * 86400  # a stuck watch parks itself after 7 days
PENDING_TTL_S = 600          # approvals expire in 10 minutes
DEFAULT_EXPIRY_S = 90 * 86400  # a watch with no stated end dies in 90 days
FAILURES_TO_STALL_SOFT = 0   # (kept explicit: stalls ride the 7-day clock)

_CRYPTO = {"BTC", "ETH", "SOL", "DOGE", "XRP", "ADA", "LTC", "LINK",
           "MATIC", "DOT", "AVAX", "ATOM", "UNI", "NEAR", "APT"}


# --- Store (house blob pattern, as in og_artifacts) -----------------------------
# One authoritative record set per user: table
# og_userwatch_data (uid, kind) via og_db when
# OG_MEMORY_DB_URL is set, else the JSON file below.
# kind "watches" -> {id: record}; kind "pending" ->
# the parked gated-action ask ({} when none).

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, \
    apply_bounds as _apply_bounds, \
    normalize_conninfo as _normalize_conninfo, pooled_connect as _pooled_connect
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

USERWATCH_STORE_FILE = os.getenv(
    "OG_USERWATCH_STORE", "userwatch_store.json")
_store_lock = threading.RLock()


def _db_connect():
    conn = _pooled_connect(
        _normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_userwatch_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(USERWATCH_STORE_FILE):
        try:
            with open(USERWATCH_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Userwatch store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(USERWATCH_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Userwatch store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_userwatch_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"Userwatch DB load failed: {e}")
            return default
    with _store_lock:
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
                        "INSERT INTO og_userwatch_data "
                        "(uid, kind, data) VALUES (%s, %s, %s) "
                        "ON CONFLICT (uid, kind) DO UPDATE "
                        "SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Userwatch DB save failed: {e}")
            return
    with _store_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


def _delete_blobs(uid: str):
    if not uid:
        return
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM og_userwatch_data "
                        "WHERE uid=%s", (uid,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Userwatch DB purge failed: {e}")
            return
    with _store_lock:
        store = _load_file_store()
        store.pop(uid, None)
        _save_file_store(store)


def _all_uids() -> list:
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT DISTINCT uid FROM og_userwatch_data")
                    return [r[0] for r in cur.fetchall()]
        except Exception as e:
            logger.warning(f"Userwatch uid scan failed: {e}")
            return []
    with _store_lock:
        return list(_load_file_store().keys())


# --- Records ---------------------------------------------------------------------


def _records(uid: str) -> Dict:
    got = _blob(uid, "watches", {})
    if not isinstance(got, dict):
        return {}
    return {k: v for k, v in got.items() if isinstance(v, dict)}


def _put_record(uid: str, rec: Dict):
    mine = _records(uid)
    mine[rec["id"]] = rec
    _put_blob(uid, "watches", mine)


def _save_records(uid: str, mine: Dict):
    _put_blob(uid, "watches", mine)


def _get_record(uid: str, watch_id: str) -> Optional[Dict]:
    if not uid or not watch_id:
        return None
    rec = _records(uid).get(watch_id)
    if rec is None or rec.get("owner") != uid:
        return None
    return rec


def list_watches_for_owner(uid: str) -> List[Dict]:
    """The owner's own watches, newest first. The store
    is keyed by owner and every record re-checks its
    owner — a stranger's watch is never in the list."""
    if not uid:
        return []
    out = [r for r in _records(uid).values()
           if r.get("owner") == uid]
    out.sort(key=lambda r: float(r.get("created") or 0),
             reverse=True)
    return out


def purge_owner(uid: str) -> bool:
    """Records die with the account (the og_store_ready
    cascade step)."""
    _delete_blobs(uid)
    return True


# --- Cadence parsing ---------------------------------------------------------------

_REPORT_FRAMES = (
    (re.compile(r"\bevery\s+hour\b", re.I), lambda m: 3600),
    (re.compile(r"\bevery\s+day\b", re.I), lambda m: 86400),
    (re.compile(r"\bevery\s+(\d+)\s*hours?\b", re.I),
     lambda m: int(m.group(1)) * 3600),
    (re.compile(r"\bevery\s+(\d+)\s*days?\b", re.I),
     lambda m: int(m.group(1)) * 86400),
    (re.compile(r"\bhourly\b", re.I), lambda m: 3600),
    (re.compile(r"\btwice\s+a\s+day\b", re.I), lambda m: 43200),
    (re.compile(r"\bevery\s+morning\b|\bdaily\b|\bonce\s+a\s+day\b",
                re.I), lambda m: 86400),
    (re.compile(r"\bweekly\b|\bonce\s+a\s+week\b", re.I),
     lambda m: 7 * 86400),
)


def _parse_report_s(text: str) -> Optional[int]:
    for rx, fn in _REPORT_FRAMES:
        m = rx.search(text or "")
        if m:
            try:
                secs = int(fn(m))
            except Exception:
                continue
            if 900 <= secs <= 30 * 86400:
                return secs
    return None


def _frame_label(secs: int) -> str:
    if secs == 3600:
        return "every hour"
    if secs % 86400 == 0:
        n = secs // 86400
        return "once a day" if n == 1 else f"every {n} days"
    if secs % 3600 == 0:
        return f"every {secs // 3600} hours"
    return f"every {secs // 60} minutes"


_EXPIRY_FRAMES = (
    (re.compile(r"\bfor\s+(\d+)\s*days?\b", re.I),
     lambda m: int(m.group(1)) * 86400),
    (re.compile(r"\bfor\s+(\d+)\s*weeks?\b", re.I),
     lambda m: int(m.group(1)) * 7 * 86400),
    (re.compile(r"\bfor\s+a\s+week\b", re.I), lambda m: 7 * 86400),
    (re.compile(r"\bfor\s+a\s+month\b", re.I), lambda m: 30 * 86400),
)


def _parse_expiry_s(text: str, now: float) -> float:
    for rx, fn in _EXPIRY_FRAMES:
        m = rx.search(text or "")
        if m:
            try:
                return now + int(fn(m))
            except Exception:
                continue
    return now + DEFAULT_EXPIRY_S


# --- Checkers (one per source; each returns (fingerprint, summary) or raises) -----
# Injectable for gates: a checker is fn(uid, rec) -> (str, str).
# A checker that cannot read its source raises _CheckFail with
# the plain reason; the tick records it honestly, never a guess.

class _CheckFail(Exception):
    def __init__(self, reason: str, needs_connect: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.needs_connect = needs_connect


def _check_price(uid: str, rec: Dict):
    import og_monitor
    symbol = str(rec.get("source", {}).get("symbol") or "")
    kind = "crypto" if symbol in _CRYPTO else "stock"
    price = og_monitor.get_price(symbol, kind)
    if price is None:
        raise _CheckFail(f"no live price came back for {symbol}")
    fp = f"{symbol}:{round(float(price), 4)}"
    return fp, f"{symbol} is at ${float(price):,.2f}."


def _check_web(uid: str, rec: Dict):
    import httpx
    url = str(rec.get("source", {}).get("url") or "")
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as c:
            resp = c.get(url, headers={"User-Agent": "OG-Watch/1.0"})
    except Exception as e:
        raise _CheckFail(f"the page could not be fetched ({e})")
    if resp.status_code != 200:
        raise _CheckFail(f"the page answered {resp.status_code}")
    text = re.sub(r"<[^>]+>", " ", resp.text or "")
    text = " ".join(text.split())
    title_m = re.search(r"<title[^>]*>(.*?)</title>",
                        resp.text or "", re.I | re.S)
    title = " ".join((title_m.group(1) if title_m else "").split())[:80]
    fp = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:24]
    return fp, (f"\"{title or url}\" — {len(text)} characters of text "
                f"on the page.")


def _check_email(uid: str, rec: Dict):
    import og_google_hands as hands
    entry = hands._live_entry(uid)
    if not entry:
        raise _CheckFail(
            "Gmail is not connected for this account — connect "
            "Google and the watch starts reading",
            needs_connect=True)
    if not hands._scope_ok(entry, "gmail"):
        raise _CheckFail(
            "the Google connection does not cover Gmail — "
            "reconnect Google with Gmail access and the watch "
            "starts reading", needs_connect=True)
    token = entry.get("access_token", "")
    query = str(rec.get("source", {}).get("query") or "")
    msgs = hands._gmail_list(token, query, 5)
    if msgs is None:
        raise _CheckFail("the Gmail read failed this time")
    if not msgs:
        return "none", "No matching mail."
    newest = msgs[0]
    fp = f"{newest.get('id', '')}:{len(msgs)}"
    summary = (f"{len(msgs)} recent match(es); newest: "
               f"\"{newest.get('subject', '(no subject)')}\" "
               f"from {newest.get('from', 'unknown sender')}.")
    rule = str(rec.get("source", {}).get("rule") or "")
    label = str(rec.get("source", {}).get("label") or "")
    if rule and label:
        # Honest split (audit): the connection is read-only,
        # so the rule is REPORTED, never silently "filed".
        summary += (f" Filing rule on record: matches like these "
                    f"are reported for the \"{label}\" file — "
                    f"applying the label itself needs the Gmail "
                    f"modify permission OG does not hold yet, so "
                    f"nothing was moved.")
    return fp, summary


def _check_library(uid: str, rec: Dict):
    import og_artifacts
    items = og_artifacts.list_artifacts_for_owner(uid)
    if not items:
        return "empty", "Your Library has no pages, documents, or tables yet."
    newest = items[0]
    fp = f"{newest.get('id', '')}:{len(items)}"
    return fp, (f"{len(items)} built item(s) in your Library; newest is "
                f"\"{newest.get('title', 'untitled')}\".")


CHECKERS = {
    "price": _check_price,
    "web": _check_web,
    "email": _check_email,
    "library": _check_library,
}


# --- Creation ---------------------------------------------------------------------


def create_watch(uid: str, what: str, source_kind: str,
                 source: Dict, report_s: Optional[int] = None,
                 check_min: Optional[int] = None,
                 no_change: str = "line",
                 expires_at: Optional[float] = None,
                 now: Optional[float] = None) -> Optional[Dict]:
    """Create exactly one watch record. A duplicate ask
    (same owner, same source, same target) returns the
    EXISTING record — asking twice never piles up two
    watches on the same thing (No Junk Drawer)."""
    if not uid or not what or source_kind not in CHECKERS:
        return None
    now = time.time() if now is None else now
    mine = _records(uid)
    key = (source_kind, json.dumps(source, sort_keys=True))
    for rec in mine.values():
        if rec.get("owner") != uid:
            continue
        if (rec.get("source_kind"),
                json.dumps(rec.get("source") or {},
                           sort_keys=True)) == key \
                and rec.get("status") not in ("stopped",):
            return rec
    if len([r for r in mine.values()
            if r.get("status") not in ("stopped",)]) >= MAX_WATCHES:
        return None
    report_s = int(report_s or DEFAULT_REPORT_S)
    if check_min is None:
        check_min = max(15, min(DEFAULT_CHECK_MIN,
                                max(15, report_s // 60)))
    rec = {
        "id": uuid.uuid4().hex[:16],
        "owner": uid,
        "what": str(what)[:200],
        "source_kind": source_kind,
        "source": source,
        "check_min": int(check_min),
        "report_s": report_s,
        "report_label": _frame_label(report_s),
        "no_change": "silent" if no_change == "silent" else "line",
        "status": "active",
        "status_since": now,
        "created": now,
        "expires_at": float(expires_at or (now + DEFAULT_EXPIRY_S)),
        "next_check": now,
        "next_report": now + report_s,
        "last_check": None,
        "last_state": None,
        "last_summary": "",
        "last_report_state": None,
        "failures": 0,
        "checks": 0,
        "reports": 0,
    }
    mine[rec["id"]] = rec
    _save_records(uid, mine)
    return rec


def stop_watch(uid: str, watch_id: str) -> bool:
    """Stopping kills the record outright — and with it
    every future report (reports are produced from the
    record; no record, no report)."""
    mine = _records(uid)
    rec = mine.get(watch_id)
    if rec is None or rec.get("owner") != uid:
        return False
    del mine[watch_id]
    _save_records(uid, mine)
    return True


def _find_watch(uid: str, words: str) -> Optional[Dict]:
    w = " ".join(str(words or "").lower().split()).strip(" .,!?")
    if not w:
        return None
    watches = list_watches_for_owner(uid)
    for rec in watches:
        if w in str(rec.get("what", "")).lower() \
                or w == str(rec.get("source", {}).get("symbol", "")).lower() \
                or w in str(rec.get("source", {}).get("url", "")).lower():
            return rec
    return watches[0] if len(watches) == 1 and w in (
        "it", "that", "the watch", "my watch") else None


# --- The tick (runs OFF the main thread, via asyncio.to_thread) -------------------


def _notify(uid: str, kind: str, title: str, body: str,
            target=None):
    try:
        import og_notify
        og_notify.record(uid, kind, title, body, target=target)
    except Exception as e:
        logger.warning(f"Userwatch notify failed: {e}")


def run_tick(now: Optional[float] = None) -> Dict:
    """One scheduler cycle over every owner's watches.
    Due watches are CLAIMED AHEAD — next_check /
    next_report move forward and are saved BEFORE any
    checker runs — so two workers can never double-run
    or double-report the same watch. Returns counters
    {checked, reported, expired, stalled}."""
    out = {"checked": 0, "reported": 0, "expired": 0, "stalled": 0}
    if not userwatch_enabled():
        return out
    now = time.time() if now is None else now
    for uid in _all_uids():
        try:
            mine = _records(uid)
            if not mine:
                continue
            changed = False
            # Expiry: the record dies, one final Feed line.
            for wid in list(mine.keys()):
                rec = mine[wid]
                if float(rec.get("expires_at") or 0) <= now:
                    del mine[wid]
                    changed = True
                    out["expired"] += 1
                    _notify(uid, "notice", "OG Watch — ended",
                            f"Your watch on {rec.get('what', 'it')} "
                            f"reached its end date and stopped. "
                            f"Nothing is being watched for it now.")
            # Stalled watchdog: stuck needs_connect / failing
            # for STALLED_AFTER_S parks itself, one notice.
            for rec in mine.values():
                if rec.get("status") in ("needs_connect", "failing") \
                        and now - float(rec.get("status_since") or now) \
                        > STALLED_AFTER_S:
                    rec["status"] = "stalled"
                    rec["status_since"] = now
                    changed = True
                    out["stalled"] += 1
                    _notify(uid, "notice", "OG Watch — parked",
                            f"Your watch on {rec.get('what', 'it')} "
                            f"has been stuck for 7 days, so it parked "
                            f"itself and stopped checking. Say "
                            f"\"resume my watch\" to start it again.")
            # Claim ahead.
            due_checks, due_reports = [], []
            for rec in mine.values():
                if rec.get("status") == "stalled":
                    continue
                if float(rec.get("next_check") or 0) <= now:
                    rec["next_check"] = now + int(
                        rec.get("check_min") or DEFAULT_CHECK_MIN) * 60
                    due_checks.append(rec)
                if float(rec.get("next_report") or 0) <= now:
                    rec["next_report"] = now + int(
                        rec.get("report_s") or DEFAULT_REPORT_S)
                    due_reports.append(rec)
            if due_checks or due_reports:
                _save_records(uid, mine)
                changed = False
            # Checks.
            for rec in due_checks:
                checker = CHECKERS.get(rec.get("source_kind") or "")
                rec["checks"] = int(rec.get("checks") or 0) + 1
                rec["last_check"] = now
                if checker is None:
                    rec["last_summary"] = "That source can't be checked."
                    rec["status"] = "failing"
                    rec["status_since"] = now
                else:
                    try:
                        fp, summary = checker(uid, rec)
                        rec["last_state"] = fp
                        rec["last_summary"] = summary
                        rec["failures"] = 0
                        if rec.get("status") != "active":
                            rec["status"] = "active"
                            rec["status_since"] = now
                    except _CheckFail as f:
                        rec["failures"] = int(rec.get("failures") or 0) + 1
                        rec["last_summary"] = f.reason + "."
                        new_status = "needs_connect" if f.needs_connect \
                            else "failing"
                        if rec.get("status") != new_status:
                            rec["status"] = new_status
                            rec["status_since"] = now
                    except Exception as e:
                        rec["failures"] = int(rec.get("failures") or 0) + 1
                        rec["last_summary"] = \
                            "The check failed this time — no reading taken."
                        if rec.get("status") != "failing":
                            rec["status"] = "failing"
                            rec["status_since"] = now
                changed = True
                out["checked"] += 1
            # Reports — one Feed record per due watch.
            for rec in due_reports:
                changed_state = rec.get("last_state") != \
                    rec.get("last_report_state")
                rec["last_report_state"] = rec.get("last_state")
                changed = True
                if not changed_state and rec.get("no_change") == "silent":
                    continue  # the watch's own setting: silence
                if rec.get("status") == "needs_connect":
                    body = (f"Still can't read this one: "
                            f"{rec.get('last_summary', '')}")
                elif changed_state or rec.get("last_summary"):
                    body = rec.get("last_summary") or "No change."
                    if not changed_state:
                        body = f"No change — {body}"
                else:
                    body = "No change since the last report."
                rec["reports"] = int(rec.get("reports") or 0) + 1
                out["reported"] += 1
                _notify(uid, "watch_report",
                        f"OG Watch — {rec.get('what', 'watch')}",
                        body,
                        target={"view": "watch", "id": rec.get("id"),
                                "kind": "userwatch"})
            if changed:
                _save_records(uid, mine)
        except Exception as e:
            logger.warning(f"Userwatch tick failed for a visitor: {e}")
    return out


# --- Approval parking (ticket item 6) ----------------------------------------------

def _pending(uid: str) -> Dict:
    got = _blob(uid, "pending", {})
    if not isinstance(got, dict):
        return {}
    if got and time.time() - float(got.get("ts") or 0) > PENDING_TTL_S:
        _put_blob(uid, "pending", {})
        return {}
    return got


def _park_pending(uid: str, ask: str, action: str):
    _put_blob(uid, "pending", {
        "ask": str(ask)[:300], "action": action, "ts": time.time()})


_GATED_ACTION_RE = re.compile(
    r"\b(buy|sell|purchase|order|pay|send|email|message|text|trade|book)\b"
    r"[^.!?]*\b(when|if|once|as soon as)\b"
    r"|\b(when|if|once)\b[^.!?]*\b(buy|sell|purchase|order|pay|send|"
    r"email|message|text|trade|book)\b", re.I)


# --- Chat claims ---------------------------------------------------------------------

_CREATE_PRICE_RE = re.compile(
    r"\bwatch\s+(?:the\s+)?(?:price\s+of\s+)?([A-Za-z]{1,6})\b", re.I)
_CREATE_WEB_RE = re.compile(
    r"\bwatch\s+(https?://\S+)", re.I)
_CREATE_EMAIL_RE = re.compile(
    r"\bwatch\s+(?:my\s+)?(?:gmail|inbox|e-?mail)\b"
    r"(?:\s+for\s+([^.!?]+))?", re.I)
_CREATE_FILE_RE = re.compile(
    r"\bfile\s+(.+?)\s+(?:under|into|in)\s+(?:my\s+)?([A-Za-z0-9 _\-]{2,40})"
    r"\s*(?:folder|label|file)?\b", re.I)
_CREATE_LIBRARY_RE = re.compile(
    r"\bwatch\s+(?:my\s+)?library\b", re.I)
_LIST_RE = re.compile(
    r"\bwhat are you watching for me\b|\bmy watches\b"
    r"|\blist (?:my )?watches\b|\bwhat.*watching\b", re.I)
_STOP_RE = re.compile(
    r"\bstop watching\s+(?:for\s+)?(.+?)\s*[.!?]*$"
    r"|\bstop (?:the|my) watch (?:on|for)\s+(.+?)\s*[.!?]*$"
    r"|\bcancel (?:the|my) watch (?:on|for)\s+(.+?)\s*[.!?]*$",
    re.I)
_RESUME_RE = re.compile(r"\bresume (?:my |the )?watch", re.I)
_GENERIC_WATCH_RE = re.compile(
    r"\b(?:watch|keep an eye on|monitor)\s+(.+?)\s*[.!?]*$", re.I)


def _claim_job(message: str, uid: str) -> Optional[Dict]:
    raw = str(message or "")
    low = raw.lower()
    if not uid or not userwatch_enabled():
        return None
    # Marketplace asks belong to og_watch (Round 29),
    # fire-once price alerts to og_monitor (Round 18):
    # this seam never steals either family's phrasing.
    if "marketplace" in low or "alert me" in low:
        return None
    mine = list_watches_for_owner(uid)
    if _LIST_RE.search(low) and mine:
        return {"op": "list"}
    if _RESUME_RE.search(low) and any(
            w.get("status") == "stalled" for w in mine):
        return {"op": "resume"}
    m = _STOP_RE.search(raw)
    if m and mine:
        words = next(g for g in m.groups() if g)
        return {"op": "stop", "words": words}
    # Email filing rule first — it is the more specific ask.
    m = _CREATE_FILE_RE.search(raw)
    if m and re.search(r"\b(watch|file|separate|sort|organize)\b", low):
        return {"op": "create", "source_kind": "email",
                "what": f"email matching \"{m.group(1).strip()}\"",
                "source": {"query": m.group(1).strip(),
                           "rule": "file",
                           "label": m.group(2).strip()},
                "message": raw}
    m = _CREATE_WEB_RE.search(raw)
    if m:
        url = m.group(1).rstrip(".,)")
        return {"op": "create", "source_kind": "web",
                "what": url, "source": {"url": url},
                "message": raw}
    m = _CREATE_EMAIL_RE.search(raw)
    if m:
        query = (m.group(1) or "").strip()
        return {"op": "create", "source_kind": "email",
                "what": (f"email matching \"{query}\"" if query
                         else "your inbox"),
                "source": {"query": query}, "message": raw}
    if _CREATE_LIBRARY_RE.search(low):
        return {"op": "create", "source_kind": "library",
                "what": "your Library", "source": {},
                "message": raw}
    m = _CREATE_PRICE_RE.search(raw)
    if m:
        symbol = m.group(1).upper()
        # A plain "watch BTC" is og_monitor's established
        # fire-once price watch. The general watch claims
        # a price only when the ask carries a REPORT frame
        # ("watch BTC and report daily") — the thing the
        # monitor watch has never offered.
        if symbol not in ("MY", "THE", "THIS", "THAT", "IT", "FOR") \
                and (_parse_report_s(raw) or "report" in low):
            return {"op": "create", "source_kind": "price",
                    "what": f"{symbol} price",
                    "source": {"symbol": symbol},
                    "message": raw}
    m = _GENERIC_WATCH_RE.search(raw)
    if m and "%" not in raw and not re.search(
            r"\b(video|youtube|movie|film|show|game|stream)\b", low):
        if len(m.group(1).split()) < 2:
            # A bare one-word target ("watch BTC") belongs
            # to the older seams (og_monitor's price watch)
            # — never claimed here.
            return None
        if _GATED_ACTION_RE.search(raw):
            return {"op": "gated", "ask": raw}
        return {"op": "unreachable", "target": m.group(1).strip(),
                "message": raw}
    return None


def _result(tag: str, title: str, body: str) -> List[Dict]:
    return [{"title": title, "body": body, "href": ""}]


def _directive(body: str) -> List[Dict]:
    """A grounded directive block the persona voices —
    the og_artifacts seam shape. Facts only; OG's own
    voice does the talking."""
    return _result("OG WATCH",
                   "OG Watch — say it like this",
                   "[USERWATCH] Deliver this in persona, "
                   "plainly and briefly, with exactly these "
                   f"facts:\n{body}")


def userwatch_results(job: Dict, uid: str) -> List[Dict]:
    op = job.get("op")
    if op == "create":
        raw = str(job.get("message") or "")
        report_s = _parse_report_s(raw) or DEFAULT_REPORT_S
        now = time.time()
        rec = create_watch(
            uid, job.get("what") or "", job.get("source_kind") or "",
            job.get("source") or {}, report_s=report_s,
            no_change=("silent" if re.search(
                r"\bonly (?:tell|report|ping) me if\b|\bno-change\s+silent\b",
                raw, re.I) else "line"),
            expires_at=_parse_expiry_s(raw, now), now=now)
        if rec is None:
            return _directive(
                "The watch could NOT be created — the watch "
                "list is full or the ask didn't carry a source "
                "OG can read. Say that plainly and ask what to "
                "drop or clarify.")
        facts = (f"Watch created. You are now watching "
                 f"{rec['what']}. You will report on it "
                 f"{rec['report_label']} in their Feed. "
                 f"First report lands "
                 f"{rec['report_label']} from now. They can "
                 f"say \"stop watching {rec['what']}\" or open "
                 f"their watches page to end it.")
        if rec.get("source", {}).get("rule"):
            facts += (" The filing rule is ON the record and "
                      "matches will be named in every report; "
                      "applying the Gmail label itself needs a "
                      "permission OG does not hold yet, so "
                      "nothing gets moved silently — say that "
                      "plainly too.")
        return _directive(facts)
    if op == "gated":
        action = "that action"
        _park_pending(uid, job.get("ask") or "", action)
        return _directive(
            "A watch was NOT created for the action part of "
            "that ask. Watching and reporting are fine, but "
            "buying, selling, sending, paying, or trading "
            "still needs their approval through the normal "
            "gate when the moment comes — a watch never does "
            "those silently. The ask is parked for 10 minutes "
            "as a pending approval item. Offer to set a plain "
            "watch-and-report instead.")
    if op == "unreachable":
        return _directive(
            f"You cannot watch \"{job.get('target', 'that')}\" "
            "yet — there is no connected source OG can read it "
            "from, so NO watch was created. Say that plainly, "
            "name what would connect it (a price symbol, a web "
            "page address, their Gmail, or their Library are "
            "watchable today), and offer one of those.")
    if op == "list":
        watches = list_watches_for_owner(uid)
        if not watches:
            return _directive(
                "They have no general watches running. "
                "Marketplace and price watches live in their "
                "own lists. Offer to start one: a price, a web "
                "page, their email, or their Library.")
        lines = []
        for w in watches:
            lines.append(
                f"- {w['what']} ({w['source_kind']}) — "
                f"{w['status']}, checks every "
                f"{w['check_min']} min, reports "
                f"{w['report_label']}, {w['checks']} checks so "
                f"far. Last reading: {w['last_summary'] or 'none yet'}")
        return _directive(
            "Their general watches:\n" + "\n".join(lines))
    if op == "stop":
        rec = _find_watch(uid, job.get("words") or "")
        if rec is None:
            return _directive(
                "No matching general watch was found, so "
                "nothing was stopped. Name the watches they "
                "do have if any, from a list ask.")
        stop_watch(uid, rec["id"])
        return _directive(
            f"The watch on {rec['what']} is stopped and its "
            "record is deleted. No more reports for it.")
    if op == "resume":
        mine = _records(uid)
        revived = 0
        for rec in mine.values():
            if rec.get("status") == "stalled":
                rec["status"] = "active"
                rec["status_since"] = time.time()
                rec["next_check"] = time.time()
                revived += 1
        if revived:
            _save_records(uid, mine)
        return _directive(
            f"{revived} parked watch(es) restarted." if revived
            else "No parked watch was waiting to resume.")
    return _directive("That watch ask didn't parse — name a "
                      "price, a web page, email, or the Library.")


# --- The chat seam (the og_artifacts pattern) -----------------------------------------

_slot: Dict = {"job": None}


def install_userwatch_tools(agent_instance, get_uid):
    """Wrap the agent's detect_intent + web_search hooks
    so general watch asks run here. Installed AFTER
    og_watch in app.py: Marketplace asks keep belonging
    to og_watch (its claims run first in the wrapper
    chain this seam delegates to), and this seam only
    claims what it leaves behind. Persona files never
    touched."""
    if getattr(agent_instance, "_og_userwatch_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _slot["job"] = None
        try:
            uid = get_uid()
            job = _claim_job(str(message), uid) if uid else None
            if job:
                _slot["job"] = job
                if isinstance(intent, dict):
                    intent["needs_code_generation"] = False
                    if not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
        except Exception as e:
            logger.warning(f"Userwatch trigger check failed: {e}")
            _slot["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _slot.get("job")
        _slot["job"] = None
        if job:
            try:
                results = userwatch_results(job, get_uid())
            except Exception as e:
                logger.warning(f"Userwatch job failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_userwatch_installed = True


# --- Owner door: GET /watches (the watch's own page) + stop route ----------------------

def _request_uid(request: Request) -> str:
    cookie_uid = request.cookies.get("ogai_uid") or ""
    try:
        import og_accounts as _acct
        return _acct.session_uid_or(request.cookies, cookie_uid)
    except Exception:
        return cookie_uid


def _render_watches_page(uid: str) -> str:
    watches = list_watches_for_owner(uid)
    rows = ""
    for w in watches:
        rows += (
            "<div style='border:1px solid #8a6d1f;padding:12px;"
            "margin:10px 0;border-radius:8px'>"
            f"<b>{html.escape(str(w.get('what', '')))}</b> "
            f"({html.escape(str(w.get('source_kind', '')))})<br>"
            f"Status: {html.escape(str(w.get('status', '')))} · "
            f"checks every {int(w.get('check_min') or 0)} min · "
            f"reports {html.escape(str(w.get('report_label', '')))} "
            f"in your Feed<br>"
            f"Last reading: {html.escape(str(w.get('last_summary') or 'none yet'))}<br>"
            f"<form method='post' action='/watches/{w.get('id')}/stop'>"
            "<button type='submit'>Stop this watch</button>"
            "</form></div>")
    if not rows:
        rows = "<p>No watches running. Ask OG in chat to watch a price, a web page, your email, or your Library.</p>"
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Your watches — OG</title></head>"
            "<body style='background:#111;color:#eee;font-family:"
            "system-ui;padding:20px'><h1>👁 Your watches</h1>"
            + rows + "</body></html>")


def register_userwatch_routes(app):
    @app.get("/watches")
    def watches_page(request: Request):
        if not userwatch_enabled():
            raise HTTPException(status_code=404, detail="Not found")
        uid = _request_uid(request)
        if not uid:
            raise HTTPException(status_code=404, detail="Not found")
        return HTMLResponse(_render_watches_page(uid),
                            headers={"Cache-Control": "no-cache"})

    @app.post("/watches/{watch_id}/stop")
    def watches_stop(request: Request, watch_id: str):
        if not userwatch_enabled():
            raise HTTPException(status_code=404, detail="Not found")
        uid = _request_uid(request)
        if not uid or not stop_watch(uid, watch_id):
            raise HTTPException(status_code=404, detail="Not found")
        return HTMLResponse(_render_watches_page(uid),
                            headers={"Cache-Control": "no-cache"})

    _loop_started = {"on": False}

    @app.on_event("startup")
    async def _start_userwatch_scheduler():
        if _loop_started["on"] or not userwatch_enabled():
            return
        _loop_started["on"] = True

        async def _loop():
            while True:
                await asyncio.sleep(TICK_SECONDS)
                try:
                    await asyncio.to_thread(run_tick)
                except Exception as e:
                    logger.warning(f"Userwatch scheduler cycle failed: {e}")

        asyncio.create_task(_loop())
