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
STORAGE_DEDUPE_SECONDS = 24 * 60 * 60
TITLE_CAP = 160
BODY_CAP = 600
KINDS = ("watch_match", "price_alert", "video_done",
         "song_done", "storage_warning", "notice",
         "approval_needed", "signin_needed", "system_check")
# Round 50: system_check joins the alert kinds — the twice-daily
# self-check reports through the same own-switches posture.
ALERT_KINDS = ("approval_needed", "signin_needed", "system_check")

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_store_lock = threading.Lock()


# --- Durable store (Round 18 pattern: one key -> JSON blob) --------------------


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
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
            logger.warning(f"Notify DB load failed, using file: {e}")
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
            logger.warning(f"Notify DB save failed, using file: {e}")
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
            logger.warning(f"Notify DB delete failed, using file: {e}")
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
    for prefix in ("items:", "prefs:", "subs:"):
        _del(prefix + uid)


# --- Records ---------------------------------------------------------------------


def _items(uid: str) -> list:
    items = _get("items:" + uid)
    return items if isinstance(items, list) else []


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


def _site_url() -> str:
    base = os.getenv(
        "OG_PUBLIC_URL", "https://og-ai-service.onrender.com")
    return base.rstrip("/") + "/"


def record(uid, kind, title, body) -> None:
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
                _push_fanout(uid, kind, title, body)
        else:
            if prefs.get("email"):
                _email_fanout(uid, title, body)
            if prefs.get("push"):
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
    return request.cookies.get(UID_COOKIE) or ""


def register_notify_routes(app):

    @app.get("/notifications")
    async def notifications_list(request: Request):
        uid = _uid_of(request)
        if not uid:
            return {"items": [], "unread": 0}
        items = _items(uid)
        out = [{
            "id": it.get("id"), "kind": it.get("kind"),
            "title": it.get("title"), "body": it.get("body"),
            "ts": it.get("ts"), "read": bool(it.get("read")),
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
        items = _items(uid)
        if body.get("all"):
            for it in items:
                it["read"] = True
        elif isinstance(body.get("id"), str):
            for it in items:
                if it.get("id") == body["id"]:
                    it["read"] = True
        _put("items:" + uid, items)
        return {"ok": True,
                "unread": sum(1 for it in items
                              if not it.get("read"))}

    @app.get("/notifications/prefs")
    async def notifications_prefs_get(request: Request):
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
    async def notifications_vapid(request: Request):
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

    @app.get("/sw.js")
    async def service_worker(request: Request):
        return PlainTextResponse(
            _SW_JS, media_type="application/javascript",
            headers={"Cache-Control": "no-cache"})
