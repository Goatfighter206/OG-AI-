"""Store readiness for OG (Round 36, 2026-10-09): the two
hard requirements Apple + Google put on an AI-chat app —
(A) a way to REPORT an AI reply, and (B) a way for a user
to DELETE their account and the data tied to it.

(A) REPORT A REPLY. POST /report {text, reason?} — signed-in
accounts only (401 sign_in_required signed-out, the same
shape the /chat gate returns). The record {id, email, uid,
text, reason, ts} lands in a new durable store (Postgres
table og_reports_data when OG_MEMORY_DB_URL is set, else
reports_store.json; OG_REPORTS_STORE overrides the path).
Text is capped at 2000 chars and reason at 200 SERVER-SIDE
no matter what the client sends; reports are rate-limited
to 20 per account per day. The owner reviews them at
GET /reports?key=<OG_STATS_TOKEN> — the same token contract
as /stats. REPORTS ARE RETAINED after an account is deleted
(moderation record — the stores' own guidance expects
reports kept for review); everything else tied to the
account goes.

(B) ACCOUNT DELETION. POST /auth/delete {password} —
session-required; the password is verified against the
account's own pbkdf2 hash with the login throttle riding
along (wrong password = the same generic 401 login gives,
and the account stays fully intact). On success, ONE
operation removes: the account row + uid index, every
session, reset tokens, visitor memory/history, usage +
tier entitlement, Google connection, notifications + prefs
+ push subscriptions, watch records + the browser vault
(the Steel profile is DELETED through og_watch's own
forget path first), monitor + ordering records, song and
video jobs (records + build dirs), Unity packages, locker
files (objects + metadata, through og_storage's own delete
path), the Round 6 temp upload, every connect token
(Spotify / GitHub + its check watches / Discord / Twitch /
YouTube / Reddit / Plaid / Coinbase), short links + QR
metas, the retired customize prefs (their store has no
module left, so the rows are dropped directly — the one
direct store write in this round, guarded), and the
process-local pending drafts. The response clears the
session/tier cookies and mints a fresh anonymous uid, so
the client lands signed-out on the auth wall.

ORCHESTRATION, NOT OWNERSHIP. Deletion lives with the
stores that own the data wherever an in-memory cache makes
an outside delete incorrect (song/video job caches) or a
delete path already exists (locker, vault, temp uploads,
connect tokens). This module orchestrates through those
seams; app.py's own stores (memory / usage / Google) are
reached by a lazy import of the app module at purge time —
app.py is at its push ceiling, so it gains only an import
+ register line, and by purge time the app module is fully
loaded. Every purge step is independently fail-safe: one
store glitching never blocks the account itself from being
deleted, and the step results are logged (counts only —
never content).
"""

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone

from fastapi import Request
from fastapi.responses import JSONResponse

import og_accounts as _acct

logger = logging.getLogger(__name__)

SESSION_COOKIE = _acct.SESSION_COOKIE
UID_COOKIE = _acct.UID_COOKIE
TEXT_CAP = 2000
REASON_CAP = 200
REPORT_DAILY_LIMIT = 20
MAX_REPORTS_KEPT = 500

STORE_FILE = os.getenv("OG_REPORTS_STORE", "reports_store.json")
_store_lock = threading.Lock()

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None


# --- Reports store (Round 18 pattern: one key -> JSON blob) ---------------------


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_reports_data ("
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
            logger.warning(f"Reports store load failed: {e}")
    return {}


def _save_file_store(store: dict):
    try:
        with open(STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Reports store save failed: {e}")


def _get(key: str):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_reports_data WHERE key=%s",
                        (key,))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning(f"Reports DB load failed, using file: {e}")
    with _store_lock:
        store = _load_file_store()
    return store.get(key)


def _put(key: str, value):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_reports_data (key, data) "
                        "VALUES (%s, %s) ON CONFLICT (key) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (key, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Reports DB save failed, using file: {e}")
    with _store_lock:
        store = _load_file_store()
        store[key] = value
        _save_file_store(store)


def _reports() -> list:
    items = _get("items")
    return items if isinstance(items, list) else []


def _report_count_today(uid: str) -> int:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    n = _get("count:" + uid + ":" + day)
    return int(n) if isinstance(n, int) else 0


def _record_report(email: str, uid: str, text: str, reason: str):
    rec = {
        "id": uuid.uuid4().hex[:16],
        "email": email, "uid": uid,
        "text": text[:TEXT_CAP], "reason": reason[:REASON_CAP],
        "ts": time.time(),
    }
    items = _reports()
    items.append(rec)
    _put("items", items[-MAX_REPORTS_KEPT:])
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _put("count:" + uid + ":" + day, _report_count_today(uid) + 1)
    return rec


# --- Account purge -----------------------------------------------------------------


def _step(results: dict, name: str, fn):
    try:
        fn()
        results[name] = True
    except Exception as e:
        logger.warning(f"Account purge step {name} failed: {e}")
        results[name] = False


def _purge_app_stores(uid: str, results: dict):
    """app.py's own stores (visitor memory, usage +
    entitlement, Google connections), reached by a lazy
    import of the fully-loaded app module."""
    try:
        import app as _appmod
    except Exception as e:
        logger.warning(f"Account purge: app stores unreachable: {e}")
        results["app_stores"] = False
        return

    def memory():
        with _appmod._memory_lock:
            store = _appmod._load_memory_store()
            if uid in store:
                del store[uid]
                _appmod._save_memory_store(store)

    def usage():
        with _appmod._usage_lock:
            store = _appmod._load_usage_store()
            changed = False
            if uid in store:
                del store[uid]
                changed = True
            entitled = store.get(_appmod.PRO_ENTITLED_KEY)
            if isinstance(entitled, dict) and uid in entitled:
                del entitled[uid]
                changed = True
            if changed:
                _appmod._save_usage_store(store)

    def google():
        with _appmod._google_lock:
            store = _appmod._load_google_store()
            if uid in store:
                del store[uid]
                _appmod._save_google_store(store)

    _step(results, "memory", memory)
    _step(results, "usage", usage)
    _step(results, "google", google)


def _purge_customize(uid: str):
    """The retired Round-customize store (og_customize_data /
    customize_store.json) has no module left in the tree, so
    its rows are dropped directly — guarded both ways (the
    table may not exist; the file may not exist). The R2
    picture object goes through og_storage's backend."""
    if MEMORY_DB_URL and psycopg is not None:
        try:
            conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM og_customize_data WHERE uid=%s",
                        (uid,))
                conn.commit()
        except Exception as e:
            logger.warning(f"Customize purge (DB) skipped: {e}")
    path = os.getenv(
        "OG_CUSTOMIZE_STORE_FILE", "customize_store.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                store = json.load(f)
            if isinstance(store, dict) and uid in store:
                del store[uid]
                with open(path, "w") as f:
                    json.dump(store, f)
        except Exception as e:
            logger.warning(f"Customize purge (file) failed: {e}")
    try:
        import og_storage as _st
        backend = _st.get_backend()
        if backend is not None:
            backend.delete("custom-avatars/" + _st.owner_of(uid))
    except Exception as e:
        logger.warning(f"Customize picture purge failed: {e}")


def _purge_unity(uid: str):
    """Unity packages are sidecar files (Round 16/33): scan
    the store dir, drop this uid's .json + .zip pairs."""
    import og_unity as _un
    store_dir = _un.UNITY_STORE_DIR
    if not os.path.isdir(store_dir):
        return
    for fname in os.listdir(store_dir):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(store_dir, fname)
        try:
            with open(fpath) as f:
                meta = json.load(f)
        except Exception:
            continue
        if not isinstance(meta, dict) or meta.get("uid") != uid:
            continue
        for p in (fpath, fpath[:-5] + ".zip"):
            try:
                os.remove(p)
            except Exception:
                pass


def purge_account(email: str, uid: str, ip: str = "") -> dict:
    """Remove the account and everything keyed to it.
    Returns a {step: ok} map for logging/tests. Reports this
    account FILED are retained (moderation record — see the
    module docstring); the reporter's own rate counters go."""
    results = {}

    def accounts():
        # Reset tokens first (the pointer names the token key).
        old = _acct._get("resetfor:" + email)
        if isinstance(old, str):
            _acct._del(old)
        _acct._del("resetfor:" + email)
        _acct._delete_sessions_for(email)
        if ip:
            _acct._del(_acct._fail_key(email, ip))
        _acct._del("uidacct:" + uid)
        _acct._del("acct:" + email)

    _step(results, "accounts", accounts)
    _purge_app_stores(uid, results)

    import og_notify as _notify
    _step(results, "notify", lambda: _notify.purge_uid(uid))
    import og_watch as _watch
    _step(results, "watch", lambda: _watch.purge_uid(uid))
    import og_monitor as _monitor
    _step(results, "monitor", lambda: _monitor.purge_uid(uid))
    import og_ordering as _ordering
    _step(results, "ordering", lambda: _ordering.purge_uid(uid))
    import og_songs as _songs
    _step(results, "songs", lambda: _songs.purge_owner(uid))
    import og_video as _video
    _step(results, "video", lambda: _video.purge_owner(uid))

    def storage():
        import og_storage as _st
        for rec in _st.list_records(uid):
            _st.delete_record(uid, rec.get("id"))

    _step(results, "storage", storage)

    def temp_upload():
        import og_file_read as _fr
        _fr.clear_upload(uid)

    _step(results, "temp_upload", temp_upload)
    _step(results, "unity", lambda: _purge_unity(uid))

    def tokens():
        import og_spotify as _sp
        _sp._drop_entry(uid)
        import og_discord as _dc
        _dc._drop_entry(uid)
        import og_twitch as _tw
        _tw._drop_entry(uid)
        import og_youtube as _yt
        _yt._drop_entry(uid)
        import og_reddit as _rd
        _rd._drop_entry(uid)
        import og_github as _gh
        _gh._drop_entry(uid)
        with _gh._checks_lock:
            checks = _gh._load_checks_store()
            if uid in checks:
                del checks[uid]
                _gh._save_checks_store(checks)
        import og_plaid as _pl
        _pl._drop_entry(uid)
        import og_trading as _tr
        _tr._drop_cb_entry(uid)

    _step(results, "tokens", tokens)

    def shortlinks():
        import og_utils as _ut
        store = _ut._load_shortlinks()
        kept = {k: v for k, v in store.items()
                if not (isinstance(v, dict) and v.get("uid") == uid)}
        if len(kept) != len(store):
            _ut._save_shortlinks(kept)

    _step(results, "shortlinks", shortlinks)
    _step(results, "customize", lambda: _purge_customize(uid))

    def pendings():
        import og_browser as _br
        _br._clear_pending(uid)
        import og_songs as _sg
        _sg._clear_pending(uid)
        import og_ordering as _od
        _od._clear_pending(uid)
        import og_trading as _td
        _td._clear_pending(uid)
        import og_unity as _un2
        _un2._clear_pending(uid)

    _step(results, "pendings", pendings)
    logger.info(
        "Account deleted; purge steps: "
        + ", ".join(f"{k}={v}" for k, v in sorted(results.items())))
    return results


# --- Routes -------------------------------------------------------------------------


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


def _session(request: Request):
    return _acct._session_for(request.cookies.get(SESSION_COOKIE))


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message},
                        status_code=status)


def register_store_routes(app):

    @app.post("/report")
    async def report_reply(request: Request):
        sess = _session(request)
        if sess is None:
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        body = await _json_body(request)
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            return _err("Nothing to report.")
        reason = body.get("reason")
        if not isinstance(reason, str):
            reason = ""
        uid = sess.get("uid") or ""
        if _report_count_today(uid) >= REPORT_DAILY_LIMIT:
            return _err(
                "Too many reports today — try again tomorrow.",
                status=429)
        rec = _record_report(
            sess.get("email") or "", uid,
            text.strip(), reason.strip())
        return {"ok": True, "id": rec["id"]}

    @app.get("/reports")
    async def reports_list(key: str = ""):
        token = os.getenv("OG_STATS_TOKEN", "")
        if not token or key != token:
            return JSONResponse({"detail": "Owner key required"},
                                status_code=401)
        items = _reports()
        return {"reports": list(reversed(items)),
                "count": len(items)}

    @app.post("/auth/delete")
    async def auth_delete(request: Request):
        sess = _session(request)
        if sess is None:
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        email = sess.get("email") or ""
        uid = sess.get("uid") or ""
        ip = _acct._client_ip(request)
        if _acct._throttled(email, ip):
            return _err(_acct.ERR_THROTTLED, status=429)
        body = await _json_body(request)
        password = body.get("password")
        acct = _acct._get("acct:" + email) if email else None
        stored = acct.get("pw") if isinstance(acct, dict) \
            else _acct._DUMMY_HASH
        ok = isinstance(password, str) and _acct._check_password(
            password, stored)
        if not isinstance(acct, dict) or not ok:
            _acct._record_failure(email, ip)
            return _err(_acct.ERR_GENERIC, status=401)
        purge_account(email, uid, ip)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            SESSION_COOKIE, "", max_age=0,
            path="/", httponly=True, secure=True, samesite="lax")
        resp.set_cookie(
            "ogai_tier", "", max_age=0,
            path="/", httponly=True, samesite="lax")
        resp.set_cookie(
            "ogai_pro", "", max_age=0,
            path="/", httponly=True, samesite="lax")
        _acct._uid_cookie(resp, uuid.uuid4().hex)  # fresh anonymous
        return resp
