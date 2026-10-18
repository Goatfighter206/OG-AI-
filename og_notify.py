"""Notifications for OG (Brent's order, 2026-10-09):
"OG needs a notification system."

WHAT THIS IS. Until now OG's events — Round 29 watch
matches, Round 18 price-watch fires, story-video and song
completions, the Round 21 locker near-full warning — only
surfaced at the TOP of the visitor's next chat. There was
no record, no center, and no out-of-band channel. This
module is the missing layer, in three parts:

1. A per-user notification CENTER. Every event is recorded
   (kind, title, body, time, read flag) in the established
   durable pattern (Postgres table og_notify_data when
   OG_MEMORY_DB_URL is set, else a JSON file), keyed by
   ogai_uid — the identity the Round-signin account owns,
   so notifications belong to the account. The page's bell
   reads GET /notifications; POST /notifications/read
   marks one or all read.

2. An internal record(uid, kind, title, body) seam the
   producers call (og_watch deliveries, og_monitor fires,
   og_video / og_songs completions, og_storage's near-full
   warning) through the codebase's optional-import pattern.
   record() is FAIL-SAFE BY CONTRACT: every step is
   swallowed internally, so a notification failure can
   never break a watch check, a render, or a chat. The
   existing in-chat delivery is untouched — this is an
   additional record + channels, not a replacement.

3. Two opt-in out-of-band channels (prefs default BOTH
   OFF; GET/POST /notifications/prefs):
   - EMAIL: only when the user opted in AND the uid
     belongs to a signed-in account (the account email,
     resolved through og_accounts) AND SMTP is configured.
     It reuses the accounts module's OG_SMTP_* settings
     contract verbatim — no second config exists. SMTP
     dark or anonymous uid -> no send, by construction.
   - WEB PUSH: VAPID + Push API. One keypair is generated
     on first use and persisted in the durable store; the
     private key NEVER leaves the server and appears in
     no response (GET /notifications/vapid returns the
     public key only). A service worker is served at
     /sw.js (root scope); subscriptions are stored per
     user (POST /notifications/subscribe / unsubscribe)
     and dead endpoints (404/410) are pruned on send.
     Sending uses pywebpush (requirements.txt). HONEST
     LIMIT: on iPhone, Web Push only works after the
     user adds OG to the Home Screen — an Apple rule,
     stated plainly in the panel copy and here.

TRUST POSTURE. The /notifications* routes are keyed by
the caller's own ogai_uid cookie — the same posture as
/history (the page reads them as chrome, including
before sign-in). They expose nothing about anyone else:
a uid only ever sees its own records, prefs and subs.

STORAGE HYGIENE. At most 100 records kept per user
(newest wins); storage_warning records dedupe: a second
unread warning within 24 hours is not recorded again.

ROUND 38 (approval + sign-in alerts; Brent, 2026-10-10).
Two new kinds — approval_needed and signin_needed — are
recorded by og_browser when OG needs the visitor: a parked
approval, or a login wall only the visitor can pass. These
kinds carry their OWN switches in the prefs contract
(all DEFAULT TRUE, all merged per-key on POST):
  alerts       — the master switch (the producers check it
                 via alerts_enabled(); record() enforces it
                 too: off means NO record at all).
  alerts_email — email channel for THESE kinds only.
  alerts_push  — push channel for THESE kinds only.
The two channel switches OVERRIDE the global email/push
prefs for these kinds (no double gate): an alert-kind
record emails iff alerts_email is on, pushes iff
alerts_push is on (and a subscription exists — the fanout
already no-ops without one). Every other kind keeps the
global email/push behavior exactly as before. The web
switches live on the "Notification settings" page
(/static/og_notification_settings.js).

ROUND 41 PHASE A (native push, SERVER SIDE ONLY, DARK).
The missing channel the plan names: Web Push does not exist
inside the app surfaces (Capacitor shell / Unity), so a
device registry + an FCM HTTP v1 send path land here,
behind the OG_PUSH_NATIVE_ENABLED gate (default OFF --
record() still calls the fanout, it no-ops instantly).
Devices register via POST /notifications/devices
(records keyed devices:<uid>, cap 5, the token is the
unique key; a token presented under a different uid MOVES
to that uid), and unregister via
POST /notifications/devices/remove. Tokens are never
logged and never returned by any route. The prefs rule is
the EXISTING one, unchanged: _native_fanout is
transport-only exactly like _push_fanout, and record()
calls it inside the same prefs-gated branches -- regular
kinds obey the global push pref, alert kinds obey
alerts_push overriding it. purge_uid wipes devices: too.
The send uses the service-account OAuth2 flow (an RS256
JWT signed with the `cryptography` library, exchanged for
an access token cached until near expiry) and one FCM
HTTP v1 message per device; UNREGISTERED /
registration-token-not-registered prunes that token,
every other failure keeps it. No producer can be broken
by this channel -- same fail-safe swallow as record().

ROUND 95 (notifications cleanup; Brent, 2026-10-12: "OG
has a hard time cleaning up after himself" + "Get rid of
the bell notification because it's in the idea at the
bottom"). Three changes:
- MANUAL CLEAR: POST /notifications/clear removes one
  record ({"id": ...}) or a scope ({"scope": "read"|"all"})
  from the CALLER'S OWN list only — another identity's id
  simply matches nothing. The clear is server-side truth:
  the Feed seat (the bell's successor) re-reads the store,
  so a row is gone or it isn't cleared. Clearing touches
  ONLY the items:<uid> list: kinds that POINT at live
  things (approval_needed -> og_browser's pending store,
  signin_needed -> the browser session's wall list,
  fix_needed -> og_fixqueue's proposals) never delete the
  thing pointed at — those live in their own stores.
- AUTO-CLEAR: a read record expires READ_TTL_SECONDS
  (7 days) after it was read (the read_ts the read route
  stamps; legacy read records without one age from their
  ts — the honest field the record carries); an unread
  record expires UNREAD_TTL_SECONDS (30 days) after its
  ts. Expiry runs inside _items() — the existing read
  path every surface already uses (Round 72: no new
  always-on loop) — and the prune is persisted, so the
  store itself stays clean. MAX_ITEMS stays as backstop.
- ONE IDENTITY: the routes resolve the caller through
  og_accounts.session_uid_or (the tierfix resolver — the
  live session's ACCOUNT uid wins over the presented
  cookie). Producers write under the account uid; every
  reader surface (these routes, /feed, /ideas) now
  resolves the same way, so they can never read different
  piles for the same person.
The bell itself is GONE from the shell (owner's order);
these routes remain — the Feed seat consumes them.

ROUND 321 (pointer hygiene; FIX-PLAN Addendum V1/V2):
signin_needed and approval_needed records POINT at live
things (a session's wall list / a waiting watch, a parked
browser pending). When the target dies, the record used to
live on for up to 30 days and steer surfaces — the gold
bubble told a signed-in owner "OG needs you to sign in"
off a corpse record from a dead October session. Now
_items() re-validates pointer records at read time
(_pointer_backed, fail-safe toward keeping) and prunes
the unbacked, persisted like the Round 95 expiry prune.
The two kinds age by the owner's approved numbers
(2026-10-15 lock): a signin_needed record dies with its
target (no live wall / waiting watch behind it, it goes
at read); an approval_needed record lives by the
10-minute approval expiry — it is pruned when no live
pending backs it AND it is older than
APPROVAL_POINTER_TTL_SECONDS; a live pending always
backs it, and a fresh record always survives, so an
in-flight approval is never eaten by the read path.
fix_needed records are NOT pruned this way: their target
(og_fixqueue's proposals) is a durable owner-decision
record with its own lifecycle, not a live pointer.
"""

import base64
import json
import logging
import os
import smtplib
import threading
import time
import uuid
from email.message import EmailMessage

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

UID_COOKIE = "ogai_uid"
STORE_FILE = os.getenv("OG_NOTIFY_STORE", "notify_store.json")
MAX_ITEMS = 100
LIST_CAP = 50
MAX_SUBS = 10
MAX_DEVICES = 5
DEVICE_PLATFORMS = ("ios", "android")
DEVICE_SURFACES = ("shell", "unity")
STORAGE_DEDUPE_SECONDS = 24 * 60 * 60
# Round 95 auto-clear (Brent: "there's no automatic clear
# to get rid of the old notifications"): read records die
# a week after they were read; unread records die a month
# after they landed. MAX_ITEMS above stays as backstop.
READ_TTL_SECONDS = 7 * 24 * 60 * 60
UNREAD_TTL_SECONDS = 30 * 24 * 60 * 60
# Round 321: the owner's approved approval expiry
# (Brent, 2026-10-15: "approvals expire in 10 minutes") —
# an approval_needed pointer record with no live pending
# behind it is pruned once it is older than this.
APPROVAL_POINTER_TTL_SECONDS = 600
TITLE_CAP = 160
BODY_CAP = 600
KINDS = ("watch_match", "price_alert", "video_done",
         "song_done", "podcast_done", "book_done", "motion_done",
         "model_done", "sfx_done", "voice_done",
         "storage_warning", "notice",
         "approval_needed", "signin_needed", "system_check",
         "fix_needed",
         # Round 69: the nightly auto-update's outcome reports
         # (applied + verified, or rolled back) to the operator.
         "auto_update",
         # Round 54: Marketplace watch matches (og_mpwatch via
         # og_watch delivery), target {"view": "watch", ...}.
         "mp_match")

# Round 59: a notification may carry a route descriptor ("target") so
# tapping it takes the user to the thing it is about. Additive and
# stable — the Unity client consumes the SAME shape:
#   {"view": "approvals"|"browser"|"library"|"watch"|"system",
#    "id": "<optional>", "kind": "<optional>"}
# Old records have no target; clients treat missing/null as no-route.
TARGET_VIEWS = ("approvals", "browser", "library", "watch", "system")


def _clean_target(target):
    """Coerce a producer-supplied target into the stored shape, or
    None. Anything malformed becomes no-target — a bad target must
    never break the producer that called record()."""
    try:
        if not isinstance(target, dict):
            return None
        view = target.get("view")
        if view not in TARGET_VIEWS:
            return None
        out = {"view": view}
        tid = target.get("id")
        if isinstance(tid, str) and tid.strip():
            out["id"] = tid[:64]
        kind = target.get("kind")
        if isinstance(kind, str) and kind.strip():
            out["kind"] = kind[:32]
        return out
    except Exception:
        return None
# Round 50: system_check joins the alert kinds — the twice-daily
# self-check reports through the same own-switches posture.
# Round 53: fix_needed joins too — a prepared fix waiting on the
# owner's approval is an alert-family event, same posture.
# Round 69: auto_update joins too — the owner hears the night's
# outcome (or its rollback) with the same posture.
ALERT_KINDS = ("approval_needed", "signin_needed", "system_check",
               "fix_needed", "auto_update")

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, StoreError, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo, pooled_connect as _pooled_connect
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_store_lock = threading.Lock()


# --- Durable store (Round 18 pattern: one key -> JSON blob) --------------------


def _db_connect():
    conn = _pooled_connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_notify_data ("
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
            logger.warning(f"Notify store load failed: {e}")
    return {}


def _save_file_store(store: dict):
    try:
        with open(STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Notify store save failed: {e}")


def _get(key: str):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_notify_data WHERE key=%s",
                        (key,))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning(f"Notify DB load failed: {e}")
            raise StoreError("notify store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
    return store.get(key)


def _put(key: str, value):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_notify_data (key, data) "
                        "VALUES (%s, %s) ON CONFLICT (key) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (key, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Notify DB save failed: {e}")
            raise StoreError("notify store unavailable (Round 99)") from e
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
                        "DELETE FROM og_notify_data WHERE key=%s",
                        (key,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Notify DB delete failed: {e}")
            raise StoreError("notify store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
        store.pop(key, None)
        _save_file_store(store)


def purge_uid(uid: str):
    """Account deletion (Round 36, og_store_ready): every
    notification record, pref and push subscription this
    uid owns. Other uids' keys are never touched."""
    if not uid:
        return
    for token_uid in [d.get("token") for d in _devices(uid)]:
        _device_index_del(token_uid, uid)
    for prefix in ("items:", "prefs:", "subs:", "devices:"):
        _del(prefix + uid)


# --- Records ---------------------------------------------------------------------


def _expired(it, now: float) -> bool:
    """Round 95 auto-clear, one record: a READ record dies
    READ_TTL_SECONDS after it was read (its read_ts; a
    legacy read record without one ages from its ts — the
    honest field the record carries); an UNREAD record
    dies UNREAD_TTL_SECONDS after its ts. A malformed
    record is expired by construction."""
    if not isinstance(it, dict):
        return True
    ts = float(it.get("ts") or 0)
    if it.get("read"):
        anchor = it.get("read_ts")
        anchor = float(anchor) \
            if isinstance(anchor, (int, float)) else ts
        return now - anchor > READ_TTL_SECONDS
    return now - ts > UNREAD_TTL_SECONDS


def _norm_host(host: str) -> str:
    """og_browser._norm_host's mirror (strip one leading
    www./m./mobile. prefix, lowercase) so a record's site
    and a session's wall list compare in the same form."""
    h = str(host or "").strip().lower()
    for pre in ("www.", "m.", "mobile."):
        if h.startswith(pre):
            h = h[len(pre):]
            break
    return h


def _pointer_backed(uid: str, it: dict, now: float) -> bool:
    """Round 321 pointer hygiene (FIX-PLAN Addendum V1/V2;
    Brent's no-junk-drawer rule, numbers locked 2026-10-15):
    does the thing this record POINTS at still exist? A
    signin_needed record is backed by a live browser
    session whose wall list holds the site, or by a watch
    still waiting on its log-in — no backer, it dies at
    read. An approval_needed record is backed by a live
    parked browser pending; with no pending it still
    survives while it is inside the owner's approved
    10-minute approval expiry (APPROVAL_POINTER_TTL_SECONDS,
    aged from its ts) and is pruned past it. Every other
    kind answers True (not a pointer this hygiene judges).
    FAIL-SAFE: any doubt — an import or store glitch, an
    unreadable ts — answers True, so a record is only ever
    pruned on positive evidence its target is gone."""
    kind = it.get("kind")
    if kind not in ("signin_needed", "approval_needed"):
        return True
    try:
        import og_browser as _B
        if kind == "approval_needed":
            if _B._get_pending(uid) is not None:
                return True
            ts = float(it.get("ts") or 0)
            if ts <= 0:
                return True
            return (now - ts) <= APPROVAL_POINTER_TTL_SECONDS
        tgt = it.get("target")
        site = ""
        if isinstance(tgt, dict):
            site = _norm_host(str(tgt.get("id") or ""))
        rec = _B._get_session(uid)
        if site and isinstance(rec, dict) \
                and site in (rec.get("walls") or []):
            return True
        import og_watch as _W
        for w in _W._watches(uid):
            if isinstance(w, dict) and w.get("status") in (
                    "waiting_login", "paused_checkpoint"):
                return True
    except Exception:
        return True
    return False


def _items(uid: str) -> list:
    raw = _get("items:" + uid)
    if not isinstance(raw, list):
        return []
    now = time.time()
    live = [it for it in raw if not _expired(it, now)]
    # Round 321: pointer hygiene joins expiry in the same
    # read path — a pointer record whose target has died
    # (the session ended, the watch parked or was stopped)
    # is pruned HERE and the prune persisted, so no surface
    # (the pill, the Feed, the recents list) can act on a
    # corpse. The mirror of the Round 95 law: clearing a
    # record never deletes its target; the target dying
    # clears the record.
    live = [it for it in live
            if not isinstance(it, dict)
            or _pointer_backed(uid, it, now)]
    if len(live) != len(raw):
        # Expiry runs HERE, inside the read path every
        # surface already uses (Round 72 doctrine: no new
        # always-on loop), and the prune is persisted so
        # the store itself stays clean, not just the view.
        _put("items:" + uid, live)
    return live


def _clear(uid: str, item_id=None, scope=None):
    """Round 95 manual clear — the caller's OWN records
    only: one record by id, every read record (scope
    "read"), or the whole list (scope "all"). Returns
    (removed, unread). Only the items:<uid> list is ever
    touched: a cleared notification never deletes the
    thing its kind POINTS at (a parked approval, a fix
    proposal) — those live in their own stores."""
    items = _items(uid)
    if item_id:
        kept = [it for it in items
                if not (isinstance(it, dict)
                        and it.get("id") == item_id)]
    elif scope == "read":
        kept = [it for it in items
                if not (isinstance(it, dict) and it.get("read"))]
    elif scope == "all":
        kept = []
    else:
        return 0, sum(1 for it in items
                      if isinstance(it, dict)
                      and not it.get("read"))
    removed = len(items) - len(kept)
    if removed:
        _put("items:" + uid, kept)
    return removed, sum(1 for it in kept
                        if isinstance(it, dict)
                        and not it.get("read"))


def _prefs(uid: str) -> dict:
    prefs = _get("prefs:" + uid)
    if not isinstance(prefs, dict):
        prefs = {}
    return {"email": bool(prefs.get("email")),
            "push": bool(prefs.get("push")),
            # Round 38: the alert kinds' own switches —
            # all default ON (alerts send until turned off).
            "alerts": bool(prefs.get("alerts", True)),
            "alerts_email": bool(prefs.get("alerts_email", True)),
            "alerts_push": bool(prefs.get("alerts_push", True))}


def alerts_enabled(uid) -> bool:
    """Round 38: the producers' master-switch check for the
    approval + sign-in alerts. FAIL-SAFE by contract: any
    prefs-read failure means ON (the alert still sends)."""
    try:
        if not uid or not isinstance(uid, str):
            return True
        return bool(_prefs(uid).get("alerts", True))
    except Exception:
        return True


def _subs(uid: str) -> list:
    subs = _get("subs:" + uid)
    return subs if isinstance(subs, list) else []


# --- Native device registry (Round 41 Phase A) ---------------------------------
# Records keyed devices:<uid> in the same store, the subs:
# pattern with MAX_DEVICES in place of MAX_SUBS. The token
# is the unique key. A second tiny key, device_index
# (token -> uid), exists only so a token presented under a
# DIFFERENT uid can be moved off the old uid's list (the
# per-uid store has no scan); register / remove / prune /
# purge keep it in sync, and _devices() is the only read
# the fanout ever uses.


def _devices(uid: str) -> list:
    devices = _get("devices:" + uid)
    return devices if isinstance(devices, list) else []


def _device_index() -> dict:
    index = _get("device_index")
    return index if isinstance(index, dict) else {}


def _device_owner(token: str):
    owner = _device_index().get(token)
    return owner if isinstance(owner, str) and owner else None


def _device_index_set(token: str, uid: str):
    index = _device_index()
    index[token] = uid
    _put("device_index", index)


def _device_index_del(token, uid=None):
    if not isinstance(token, str) or not token:
        return
    index = _device_index()
    if token in index and (uid is None or index[token] == uid):
        del index[token]
        _put("device_index", index)


def _register_device(uid: str, token: str, platform: str,
                     surface: str, app_version: str = "") -> dict:
    """Upsert one device record for uid. Re-registering the
    same token refreshes it; a token owned by a DIFFERENT
    uid moves (removed from the old uid first, so a phone
    that changed accounts stops buzzing for the old one);
    the list is capped at MAX_DEVICES, newest wins (the
    MAX_SUBS tail-keep pattern) and an evicted token's
    index entry goes with it."""
    now = time.time()
    old_uid = _device_owner(token)
    if old_uid and old_uid != uid:
        _put("devices:" + old_uid,
             [d for d in _devices(old_uid)
              if d.get("token") != token])
    devices = _devices(uid)
    existing = next((d for d in devices
                     if d.get("token") == token), None)
    rec = {"token": token, "platform": platform,
           "surface": surface,
           "app_version": app_version or "",
           "created": (existing.get("created")
                       if existing else None) or now,
           "updated": now}
    devices = [d for d in devices if d.get("token") != token]
    devices.append(rec)
    for evicted in devices[:-MAX_DEVICES]:
        _device_index_del(evicted.get("token"), uid)
    _put("devices:" + uid, devices[-MAX_DEVICES:])
    _device_index_set(token, uid)
    return rec


def _remove_device(uid: str, token: str) -> bool:
    devices = _devices(uid)
    kept = [d for d in devices if d.get("token") != token]
    if len(kept) == len(devices):
        return False
    _put("devices:" + uid, kept)
    _device_index_del(token, uid)
    return True



def _site_url() -> str:
    base = os.getenv(
        "OG_PUBLIC_URL", "https://og-ai-service.onrender.com")
    return base.rstrip("/") + "/"


def record(uid, kind, title, body, target=None) -> None:
    """The producer seam. FAIL-SAFE: never raises, never
    returns anything a producer depends on. Records the
    notification, then fans out to the opt-in channels."""
    try:
        if not uid or not isinstance(uid, str):
            return
        kind = kind if kind in KINDS else "notice"
        title = str(title or "")[:TITLE_CAP]
        body = str(body or "")[:BODY_CAP]
        now = time.time()
        items = _items(uid)
        if kind == "storage_warning":
            for it in items:
                if (it.get("kind") == "storage_warning"
                        and not it.get("read")
                        and now - float(it.get("ts") or 0)
                        < STORAGE_DEDUPE_SECONDS):
                    return  # already warned, still unread
        # Round 38: the alert kinds answer to their own
        # switches. A prefs-read failure defaults to sending
        # ({} -> every alert switch reads as its True
        # default); other kinds are unaffected either way.
        try:
            prefs = _prefs(uid)
        except Exception:
            prefs = {}
        if kind in ALERT_KINDS and not prefs.get("alerts", True):
            return  # master switch off: no record at all
        items.append({
            "id": uuid.uuid4().hex[:16],
            "kind": kind,
            "title": title,
            "body": body,
            "ts": now,
            "read": False,
            # Round 59: additive route descriptor (None = no route;
            # tap then just marks read, as before).
            "target": _clean_target(target),
        })
        _put("items:" + uid, items[-MAX_ITEMS:])
        if kind in ALERT_KINDS:
            # The alert channel switches OVERRIDE the global
            # email/push prefs for these kinds — no double
            # gate. Push still needs a stored subscription;
            # the fanout no-ops without one.
            if prefs.get("alerts_email", True):
                _email_fanout(uid, title, body)
            if prefs.get("alerts_push", True):
                _native_fanout(uid, kind, title, body)
                _push_fanout(uid, kind, title, body)
        else:
            if prefs.get("email"):
                _email_fanout(uid, title, body)
            if prefs.get("push"):
                _native_fanout(uid, kind, title, body)
                _push_fanout(uid, kind, title, body)
    except Exception as e:
        logger.warning(f"Notify record failed (swallowed): {e}")


# --- Email channel -----------------------------------------------------------------


def _smtp_settings() -> dict:
    """The accounts module's OG_SMTP_* contract — reused,
    never re-invented. Falls back to reading the identical
    env contract here if og_accounts is unavailable."""
    try:
        import og_accounts as _acct
        return _acct._smtp_settings()
    except Exception:
        return {
            "host": os.getenv("OG_SMTP_HOST", "smtp.gmail.com"),
            "port": int(os.getenv("OG_SMTP_PORT", "587") or "587"),
            "user": os.getenv("OG_SMTP_USER", ""),
            "password": os.getenv("OG_SMTP_PASS", ""),
            "from": os.getenv("OG_SMTP_FROM", "") or os.getenv(
                "OG_SMTP_USER", ""),
        }


def _account_email(uid: str):
    """The email of the account that owns this uid, via
    og_accounts' uid->email index. Anonymous uids have no
    account and therefore no email — by construction."""
    try:
        import og_accounts as _acct
        email = _acct._get("uidacct:" + uid)
        return email if isinstance(email, str) and email else None
    except Exception:
        return None


def _send_email(to: str, subject: str, body: str) -> bool:
    cfg = _smtp_settings()
    if not cfg.get("password") or not cfg.get("user") or not to:
        return False  # sender dark — silently skip
    msg = EmailMessage()
    msg["From"] = cfg["from"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as smtp:
            smtp.starttls()
            smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(msg)
        return True
    except Exception:
        logger.warning("Notification email failed to send")
        return False


def _email_fanout(uid: str, title: str, body: str) -> None:
    email = _account_email(uid)
    if not email:
        return  # anonymous — no email on file, no send
    text = (body + "\n\nOpen OG: " + _site_url() + "\n") if body \
        else ("Open OG: " + _site_url() + "\n")
    _send_email(email, title or "OG notification", text)


# --- Web Push channel ---------------------------------------------------------------


class _SubscriptionGone(Exception):
    """The push service says this subscription is dead
    (404/410) — prune it."""


def _vapid_keys() -> dict:
    """The one VAPID keypair, generated on first use and
    persisted in the durable store. Returns
    {"public": <b64url uncompressed point>,
     "private": <b64url raw scalar>} — the private half is
    server-only and is NEVER returned by any route."""
    keys = _get("vapid")
    if isinstance(keys, dict) and keys.get("public") \
            and keys.get("private"):
        return keys
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    priv = ec.generate_private_key(ec.SECP256R1())
    # The private half is stored as the b64url raw EC scalar —
    # the form py-vapid's from_string actually parses (it
    # rejects PEM armor); it never leaves the server.
    scalar = priv.private_numbers().private_value.to_bytes(
        32, "big")
    raw = priv.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint)
    keys = {
        "public": base64.urlsafe_b64encode(raw).decode(
            "ascii").rstrip("="),
        "private": base64.urlsafe_b64encode(scalar).decode(
            "ascii").rstrip("="),
    }
    # NOTE: _get/_put take _store_lock themselves — never
    # hold it across them (plain Lock; that self-deadlocks).
    _put("vapid", keys)
    stored = _get("vapid")
    if isinstance(stored, dict) and stored.get("private"):
        return stored
    return keys


def _webpush_send(sub_info, payload: str, priv_pem: str,
                  claims: dict) -> bool:
    """One real Web Push protocol send (pywebpush).
    Raises _SubscriptionGone on 404/410; False on other
    failures; True when the push service accepted it."""
    from pywebpush import WebPushException, webpush
    try:
        webpush(subscription_info=sub_info, data=payload,
                vapid_private_key=priv_pem, vapid_claims=claims,
                timeout=15)
        return True
    except WebPushException as e:
        resp = getattr(e, "response", None)
        code = getattr(resp, "status_code", None)
        if code in (404, 410):
            raise _SubscriptionGone()
        logger.warning(f"Web push send failed ({code})")
        return False


def _push_fanout(uid: str, kind: str, title: str,
                 body: str) -> None:
    subs = _subs(uid)
    if not subs:
        return
    keys = _vapid_keys()
    payload = json.dumps({
        "title": title or "OG",
        "body": body,
        "kind": kind,
        "url": _site_url(),
    })
    claims = {"sub": "mailto:williamson.bt@gmail.com"}
    kept = []
    changed = False
    for sub in subs:
        info = {"endpoint": sub.get("endpoint"),
                "keys": sub.get("keys") or {}}
        try:
            _webpush_send(info, payload, keys["private"], claims)
            kept.append(sub)
        except _SubscriptionGone:
            changed = True  # dead endpoint — pruned
        except Exception as e:
            logger.warning(f"Web push fanout failed: {e}")
            kept.append(sub)
    if changed:
        _put("subs:" + uid, kept)


# --- Native Push channel (Round 41 Phase A, DARK) --------------------------------
# ONE transport: FCM HTTP v1 for both platforms (Firebase
# relays to APNs on iOS). Creds come from the environment
# ONLY: OG_FCM_PROJECT_ID + OG_FCM_SERVICE_ACCOUNT (the
# whole service-account JSON as the value), and the dark
# gate OG_PUSH_NATIVE_ENABLED. All three are read at CALL
# time. OG_FCM_TOKEN_URL / OG_FCM_BASE_URL override the
# Google endpoints only so the test suite can point the
# send path at a local stub; they default to the real ones.
# Tokens are never logged -- log lines carry counts and
# status codes only.


class _DeviceGone(Exception):
    """FCM says this registration token is dead
    (UNREGISTERED) -- prune it, the 404/410 pattern."""


_FCM_TOKEN_URL_DEFAULT = "https://oauth2.googleapis.com/token"
_FCM_BASE_URL_DEFAULT = "https://fcm.googleapis.com"
_FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
_FCM_CACHE = {"token": None, "exp": 0.0}


def _native_enabled() -> bool:
    return os.getenv("OG_PUSH_NATIVE_ENABLED", "").strip().lower() \
        in ("1", "true", "yes", "on")


def _fcm_settings() -> dict:
    project = os.getenv("OG_FCM_PROJECT_ID", "").strip()
    raw = os.getenv("OG_FCM_SERVICE_ACCOUNT", "").strip()
    sa = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                sa = parsed
        except Exception:
            sa = {}
    if not project:
        project = str(sa.get("project_id") or "").strip()
    return {"project_id": project, "service_account": sa}


def _fcm_token_url() -> str:
    return (os.getenv("OG_FCM_TOKEN_URL", "").strip()
            or _FCM_TOKEN_URL_DEFAULT)


def _fcm_base_url() -> str:
    return (os.getenv("OG_FCM_BASE_URL", "").strip()
            or _FCM_BASE_URL_DEFAULT).rstrip("/")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _fcm_jwt(sa: dict, token_url: str) -> str:
    """A short-lived service-account JWT, RS256-signed with
    the `cryptography` library (already deployed via
    pywebpush -- no new dependency, no firebase-admin)."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    email = sa.get("client_email")
    key_pem = sa.get("private_key")
    if not email or not key_pem:
        raise ValueError("service account lacks client_email/private_key")
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {"iss": email, "sub": email, "aud": token_url,
              "scope": _FCM_SCOPE, "iat": now, "exp": now + 3600}
    signing = (_b64url(json.dumps(header, separators=(",", ":")).encode())
               + "." + _b64url(json.dumps(
                   claims, separators=(",", ":")).encode()))
    key = serialization.load_pem_private_key(
        key_pem.encode("utf-8"), password=None)
    sig = key.sign(signing.encode("ascii"), padding.PKCS1v15(),
                   hashes.SHA256())
    return signing + "." + _b64url(sig)


def _http_post(url: str, body: bytes, headers: dict):
    """The ONE native-push HTTP seam (stdlib urllib, the
    og_wallet posture). Returns (status, body_bytes) for
    every HTTP response INCLUDING error statuses; a network
    failure raises and the caller treats it as keep-token /
    no-token, never as a producer failure."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _fcm_access_token(sa: dict):
    """Exchange the service-account JWT for an access token;
    the result is cached until near expiry (60s early)."""
    now = time.time()
    if _FCM_CACHE["token"] and _FCM_CACHE["exp"] - now > 60:
        return _FCM_CACHE["token"]
    token_url = _fcm_token_url()
    import urllib.parse
    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": _fcm_jwt(sa, token_url)}).encode("utf-8")
    status, raw = _http_post(
        token_url, body,
        {"Content-Type": "application/x-www-form-urlencoded"})
    if status != 200:
        logger.warning(f"FCM token exchange failed ({status})")
        return None
    data = json.loads(raw.decode("utf-8"))
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        return None
    try:
        expires_in = float(data.get("expires_in") or 3600)
    except Exception:
        expires_in = 3600.0
    _FCM_CACHE["token"] = token
    _FCM_CACHE["exp"] = now + expires_in
    return token


def _fcm_send(project_id: str, access_token: str, device_token: str,
              kind: str, title: str, body: str) -> bool:
    """ONE FCM HTTP v1 message to ONE device token -- the
    same title/body/kind/url payload the Web Push fanout
    sends. Raises _DeviceGone on UNREGISTERED /
    registration-token-not-registered; False on quota /
    5xx / other errors (token kept); True when accepted."""
    payload = {"message": {
        "token": device_token,
        "notification": {"title": title or "OG", "body": body},
        "data": {"kind": kind, "url": _site_url()}}}
    url = (_fcm_base_url() + "/v1/projects/" + project_id
           + "/messages:send")
    status, raw = _http_post(
        url, json.dumps(payload).encode("utf-8"),
        {"Authorization": "Bearer " + access_token,
         "Content-Type": "application/json"})
    if 200 <= status < 300:
        return True
    text = raw.decode("utf-8", "replace") \
        if isinstance(raw, (bytes, bytearray)) else str(raw)
    if "UNREGISTERED" in text \
            or "registration-token-not-registered" in text:
        raise _DeviceGone()
    logger.warning(f"FCM send failed ({status})")
    return False


def _native_fanout(uid: str, kind: str, title: str,
                   body: str) -> None:
    """Transport-only, exactly like _push_fanout: the prefs
    decision was already made by record() (regular kinds --
    the global push pref; alert kinds -- alerts_push
    overriding it). Instant no-op when the dark gate is
    off, no FCM creds are configured, or the uid has no
    devices. FAIL-SAFE: never raises."""
    try:
        if not _native_enabled():
            return
        cfg = _fcm_settings()
        sa = cfg["service_account"]
        if not cfg["project_id"] or not sa.get("client_email") \
                or not sa.get("private_key"):
            return  # no FCM creds -- dark by construction
        devices = _devices(uid)
        if not devices:
            return
        try:
            access = _fcm_access_token(sa)
        except Exception as e:
            logger.warning(f"FCM token exchange failed: {e}")
            return
        if not access:
            return
        title = str(title or "OG")[:TITLE_CAP]
        body = str(body or "")[:BODY_CAP]
        kept = []
        changed = False
        for dev in devices:
            token = dev.get("token")
            if not isinstance(token, str) or not token:
                kept.append(dev)
                continue
            try:
                _fcm_send(cfg["project_id"], access, token,
                          kind, title, body)
                kept.append(dev)
            except _DeviceGone:
                changed = True  # dead token -- pruned
                _device_index_del(token, uid)
            except Exception as e:
                logger.warning(f"Native push fanout failed: {e}")
                kept.append(dev)
        if changed:
            _put("devices:" + uid, kept)
    except Exception as e:
        logger.warning(f"Native push fanout failed (swallowed): {e}")


# --- Service worker -------------------------------------------------------------------


_SW_JS = """// OG notification service worker (Round: notifications).
// Shows Web Push notifications and focuses/opens OG on tap.
self.addEventListener('push', function (event) {
  var data = {};
  try { data = event.data ? event.data.json() : {}; }
  catch (e) { data = {}; }
  var title = data.title || 'OG';
  var opts = {
    body: data.body || '',
    icon: '/avatar/poster.webp',
    badge: '/avatar/poster.webp',
    data: { url: data.url || '/' }
  };
  event.waitUntil(self.registration.showNotification(title, opts));
});
self.addEventListener('notificationclick', function (event) {
  event.notification.close();
  var url = (event.notification.data
             && event.notification.data.url) || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then(function (list) {
        for (var i = 0; i < list.length; i++) {
          if ('focus' in list[i]) {
            if (list[i].navigate) list[i].navigate(url);
            return list[i].focus();
          }
        }
        if (clients.openWindow) return clients.openWindow(url);
      }));
});
"""


# --- Routes -----------------------------------------------------------------------------


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


def _uid_of(request: Request) -> str:
    """The caller's ONE identity (Round 95): the live
    session's ACCOUNT uid when signed in, else the
    presented ogai_uid cookie — og_accounts.session_uid_or,
    the tierfix resolver. Producers write under the
    account uid; every surface resolving identity this way
    reads the same person's pile."""
    cookie_uid = request.cookies.get(UID_COOKIE) or ""
    try:
        import og_accounts as _acct
        return _acct.session_uid_or(request.cookies,
                                    cookie_uid)
    except Exception:
        return cookie_uid


def register_notify_routes(app):

    @app.get("/notifications")
    # Round 321 (Step 3.1): def, not async def — the body is
    # sync store work; FastAPI runs def handlers in its
    # threadpool, so a stalled DB can never freeze the loop.
    def notifications_list(request: Request):
        uid = _uid_of(request)
        if not uid:
            return {"items": [], "unread": 0}
        items = _items(uid)
        out = [{
            "id": it.get("id"), "kind": it.get("kind"),
            "title": it.get("title"), "body": it.get("body"),
            "ts": it.get("ts"), "read": bool(it.get("read")),
            # Round 59: old records have no target -> null (no-route).
            "target": it.get("target"),
        } for it in reversed(items[-LIST_CAP:])]
        return {"items": out,
                "unread": sum(1 for it in items
                              if not it.get("read"))}

    @app.post("/notifications/read")
    async def notifications_read(request: Request):
        uid = _uid_of(request)
        if not uid:
            return {"ok": True, "unread": 0}
        body = await _json_body(request)
        import asyncio
        def _work():
            items = _items(uid)
            now = time.time()
            if body.get("all"):
                for it in items:
                    if isinstance(it, dict) and not it.get("read"):
                        it["read"] = True
                        # Round 95: the read moment is stamped —
                        # the auto-clear ages read records from
                        # here, not from their arrival.
                        it["read_ts"] = now
            elif isinstance(body.get("id"), str):
                for it in items:
                    if isinstance(it, dict) \
                            and it.get("id") == body["id"] \
                            and not it.get("read"):
                        it["read"] = True
                        it["read_ts"] = now
            _put("items:" + uid, items)
            return {"ok": True,
                    "unread": sum(1 for it in items
                                  if not it.get("read"))}
        return await asyncio.to_thread(_work)

    @app.post("/notifications/clear")
    async def notifications_clear(request: Request):
        """Round 95: the manual clear the bell never had —
        one record by id, or a scope ("read" / "all"),
        strictly the caller's own list. An id that belongs
        to someone else matches nothing here (removed 0) —
        it is never found and never deleted."""
        uid = _uid_of(request)
        if not uid:
            return {"ok": True, "unread": 0, "removed": 0}
        body = await _json_body(request)
        import asyncio
        def _work():
            item_id = body.get("id")
            item_id = item_id if isinstance(item_id, str) \
                and item_id else None
            scope = body.get("scope")
            if not item_id and scope not in ("read", "all"):
                return JSONResponse(
                    {"ok": False, "detail": "bad clear"},
                    status_code=400)
            removed, unread = _clear(uid, item_id, scope)
            return {"ok": True, "unread": unread,
                    "removed": removed}
        return await asyncio.to_thread(_work)

    @app.get("/notifications/prefs")
    def notifications_prefs_get(request: Request):
        uid = _uid_of(request)
        if not uid:
            return {"email": False, "push": False,
                    "alerts": True, "alerts_email": True,
                    "alerts_push": True}
        return _prefs(uid)

    @app.post("/notifications/prefs")
    async def notifications_prefs_post(request: Request):
        uid = _uid_of(request)
        if not uid:
            return {"email": False, "push": False,
                    "alerts": True, "alerts_email": True,
                    "alerts_push": True}
        body = await _json_body(request)
        # Partial updates MERGE: only the keys present in the
        # body change; every other field keeps its value, so
        # an older client posting just {email} can never
        # clobber the Round 38 alert switches (and vice versa).
        prefs = _prefs(uid)
        if "email" in body:
            prefs["email"] = bool(body.get("email"))
        if "push" in body:
            prefs["push"] = bool(body.get("push"))
        if "alerts" in body:
            prefs["alerts"] = bool(body.get("alerts"))
        if "alerts_email" in body:
            prefs["alerts_email"] = bool(body.get("alerts_email"))
        if "alerts_push" in body:
            prefs["alerts_push"] = bool(body.get("alerts_push"))
        _put("prefs:" + uid, prefs)
        return prefs

    @app.get("/notifications/vapid")
    def notifications_vapid(request: Request):
        try:
            keys = _vapid_keys()
            return {"public_key": keys["public"]}
        except Exception:
            return {"public_key": None}

    @app.post("/notifications/subscribe")
    async def notifications_subscribe(request: Request):
        uid = _uid_of(request)
        body = await _json_body(request)
        sub = body.get("subscription") or body
        endpoint = sub.get("endpoint") if isinstance(sub, dict) \
            else None
        skeys = sub.get("keys") if isinstance(sub, dict) else None
        if (not uid or not isinstance(endpoint, str)
                or not endpoint.startswith("https://")
                or not isinstance(skeys, dict)
                or not skeys.get("p256dh") or not skeys.get("auth")):
            return JSONResponse(
                {"ok": False, "detail": "bad subscription"},
                status_code=400)
        subs = [s for s in _subs(uid)
                if s.get("endpoint") != endpoint]
        subs.append({"endpoint": endpoint,
                     "keys": {"p256dh": skeys["p256dh"],
                              "auth": skeys["auth"]}})
        _put("subs:" + uid, subs[-MAX_SUBS:])
        return {"ok": True}

    @app.post("/notifications/unsubscribe")
    async def notifications_unsubscribe(request: Request):
        uid = _uid_of(request)
        body = await _json_body(request)
        endpoint = body.get("endpoint")
        if uid and isinstance(endpoint, str):
            _put("subs:" + uid,
                 [s for s in _subs(uid)
                  if s.get("endpoint") != endpoint])
        return {"ok": True}

    @app.post("/notifications/devices")
    async def notifications_devices(request: Request):
        uid = _uid_of(request)
        body = await _json_body(request)
        token = body.get("token")
        platform = body.get("platform")
        surface = body.get("surface")
        app_version = body.get("app_version")
        if (not uid or not isinstance(token, str)
                or not token.strip()
                or platform not in DEVICE_PLATFORMS
                or surface not in DEVICE_SURFACES):
            return JSONResponse(
                {"ok": False, "detail": "bad device"},
                status_code=400)
        # The token is never echoed back -- the response is
        # the same bare {"ok": true} as subscribe.
        _register_device(
            uid, token.strip(), platform, surface,
            app_version if isinstance(app_version, str) else "")
        return {"ok": True}

    @app.post("/notifications/devices/remove")
    async def notifications_devices_remove(request: Request):
        uid = _uid_of(request)
        body = await _json_body(request)
        token = body.get("token")
        if uid and isinstance(token, str) and token:
            _remove_device(uid, token)
        return {"ok": True}

    @app.get("/sw.js")
    async def service_worker(request: Request):
        return PlainTextResponse(
            _SW_JS, media_type="application/javascript",
            headers={"Cache-Control": "no-cache"})
