"""Reboot OG (Round 108, Brent's order 2026-10-12):
"Add a re boot for og in the menu under tools" — and, for
Part 2 (Brent, minutes later): "Yes add it for me" (the
owner-only server-restart version).

PART 1 — THE SOFT REBOOT (every signed-in account).
POST /reboot runs a per-account soft reboot server-side
and answers a truthful report of what it did:

  * ends the caller's live browser session cleanly —
    og_browser._end_session, the ONE funnel every ending
    already takes (panel, chat, reaper, budget, idle):
    the Steel session is released, the minutes metered,
    the session record (which IS the Round 72 warm
    record) cleared, and any parked browser approval
    dies with it — Round 36 hygiene, unchanged;
  * clears the caller's browser "starting" marker, so a
    start caught mid-flight by the reboot cannot leave
    a stuck marker behind;
  * counts the caller's crew jobs still working/queued
    and REPORTS them — running work is never deleted
    or silently killed by a reboot.

That is the whole of it. A reboot is NOT a reset: the
account's memory, history, locker, picks and settings
are never touched (the confirm card says exactly that:
"Your stuff stays. This just gives OG a fresh start.").
Own-account only: the uid is resolved from the
server-issued session (og_accounts.session_uid_or), so
a caller can only ever reboot their own account.

PART 2 — RESTART OG'S SERVER (owner only).
POST /reboot/server asks Render to restart OG's own
service. The gate chain, re-checked server-side on
every call (the card's rendering is a courtesy, never
the gate):

  1. owner: the SERVER-ISSUED session's email must equal
     OG_OWNER_EMAIL (the tierfix3 rule — the email comes
     from the session record, never a client value). A
     non-owner call gets an honest 403 + a log line.
  2. wired: OG_RENDER_API_KEY + OG_RENDER_SERVICE_ID
     must both be present in env (Maya wired them via
     the Render dashboard 2026-10-12; the key's value
     never passes through code, chat, or logs — it is
     read from env inside the firing thread and placed
     in the Authorization header, nowhere else).
  3. enabled: the kill switch OG_SERVER_RESTART_ENABLED
     — UNSET = OFF for this Part (the mirror image of
     the house switch shape, on purpose: this door
     stays shut until Maya opens it after the round
     verifies).
  4. rate stamp: at most one restart per 10 minutes,
     stamped BEFORE the call fires (a double-tap can
     never double-fire), in this module's own durable
     store (og_gps pattern; Postgres og_reboot_data
     when OG_MEMORY_DB_URL is set, else a JSON file).

A restart that passes every gate: writes the stamp,
writes a server log line, writes an owner notification
record (og_notify, kind "notice") — the trail — then
fires the Render call in a background thread after a
short delay and answers the client FIRST. The card
shows "Server restarting — OG will be back in about
2 minutes" and the page polls /health until OG
answers, then reloads fresh.

The Render call itself: POST
https://api.render.com/v1/services/{service_id}/restart
with the bearer key — Render's documented restart
endpoint. The key is not in the sandbox, so the call is
proven here up to its shape (URL, headers, thread,
stamp, gates) with the transport stubbed in the suite;
the first live firing is Brent's first tap, named in
the round NOTES.

ROUTES (registered fail-safe from og_library; NOT in
PUBLIC_PATHS, so the Round 44 sign-in gate covers
them, and every handler re-checks for a uid in the
house shape — no uid -> 401 sign_in_required):

    GET  /reboot/status -> {"ok": true, "owner": bool,
        "server_restart": {"wired": bool, "enabled": bool,
        "cooldown_s": int}}
    POST /reboot        -> the soft-reboot report
    POST /reboot/forget -> Round 321: the soft reboot PLUS
        the saved-login wipe (the door that can actually
        un-wedge a stuck log-in; the plain reboot cannot
        reach the vault, by its own never-touch-data law)
    POST /reboot/server -> the gate chain's answer

Every handler does its store/Steel work through
asyncio.to_thread (the Round 107 doctrine: nothing
sync and slow ever runs on the event loop).
"""

import asyncio
import json
import logging
import os
import threading
import time
import urllib.request

from fastapi import Request
from fastapi.responses import JSONResponse

from og_db import DB_CONNECT_TIMEOUT as _DB_CT, StoreError, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo, pooled_connect as _pooled_connect

logger = logging.getLogger(__name__)

UID_COOKIE = "ogai_uid"
STORE_FILE = os.getenv("OG_REBOOT_STORE", "reboot_store.json")

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_store_lock = threading.RLock()

# One restart per 10 minutes, stamped before firing.
RESTART_COOLDOWN_S = 600
# The client is answered first; the call fires this long
# after (the answer is long gone by the time OG drops).
RESTART_FIRE_DELAY_S = 2.0
# The Render API call's own timeout — a wedged call costs
# seconds, never a hang (the Round 107 discipline).
RENDER_API_TIMEOUT_S = 15
RESTART_ETA_S = 120

_STAMP_KEY = "server_restart"


# ===========================================================================
# Durable store (og_gps pattern: one key -> JSON blob)
# ===========================================================================


def _db_connect():
    conn = _pooled_connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_reboot_data ("
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
            logger.warning(f"Reboot store load failed: {e}")
    return {}


def _save_file_store(store: dict):
    try:
        with open(STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Reboot store save failed: {e}")


def _get(key: str):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_reboot_data "
                        "WHERE key=%s", (key,))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning(f"Reboot DB load failed: {e}")
            raise StoreError("reboot store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
    return store.get(key)


def _put(key: str, value):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_reboot_data (key, data) "
                        "VALUES (%s, %s) ON CONFLICT (key) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (key, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Reboot DB save failed: {e}")
            raise StoreError("reboot store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
        store[key] = value
        _save_file_store(store)


# ===========================================================================
# Env + identity (all read at call time — never cached at import)
# ===========================================================================


def _owner_email() -> str:
    return (os.environ.get("OG_OWNER_EMAIL") or "").strip().lower()


def _render_key() -> str:
    return (os.environ.get("OG_RENDER_API_KEY") or "").strip()


def _render_service_id() -> str:
    return (os.environ.get("OG_RENDER_SERVICE_ID") or "").strip()


def restart_wired() -> bool:
    """Both Render coordinates present in env. The key's
    VALUE is only ever read by the firing thread."""
    return bool(_render_key() and _render_service_id())


def restart_enabled() -> bool:
    """The kill switch — UNSET = OFF for this Part, on
    purpose (Maya opens it in Render only after the round
    verifies). Any of 1/true/yes/on opens it."""
    return (os.environ.get("OG_SERVER_RESTART_ENABLED")
            or "").strip().lower() in ("1", "true", "yes", "on")


def _resolve(cookies):
    """(account uid, session email) for a request's cookies.
    The uid follows the server-issued session when signed
    in (og_accounts.session_uid_or — the Round 95 lesson);
    the email comes from that same session record via
    og_tiers._session_email_for, which requires the
    session's uid to BE the resolved uid. Fail-safe:
    (presented uid or "", None) on any glitch."""
    cookies = cookies or {}
    presented = cookies.get(UID_COOKIE) or ""
    uid = presented
    try:
        import og_accounts
        uid = og_accounts.session_uid_or(cookies, presented) or ""
    except Exception:
        uid = presented
    email = None
    if uid:
        try:
            import og_tiers
            email = og_tiers._session_email_for(cookies, uid)
        except Exception:
            email = None
    return uid, email


def _is_owner(cookies):
    """(uid, is_owner): owner iff the server-issued
    session's email equals OG_OWNER_EMAIL (tierfix3)."""
    uid, email = _resolve(cookies)
    owner = _owner_email()
    return uid, bool(owner and email
                     and email.strip().lower() == owner)


# ===========================================================================
# Part 1 — the soft reboot
# ===========================================================================


def soft_reboot(uid: str) -> dict:
    """Run the caller's soft reboot and report it
    truthfully. Each piece is independent: one piece's
    glitch is logged and reported, never fatal to the
    others. DATA IS NEVER TOUCHED — no memory, history,
    locker, pick or setting is read for writing here."""
    report = {"ok": True, "browser_was_live": False,
              "browser_ended": False, "browser_minutes_used": None,
              "crew_running": 0, "cleared": []}
    if not uid:
        report["ok"] = False
        return report
    # The live browser session: the one funnel every
    # ending takes. Ending it also clears the warm
    # record (the session record IS the warm state) and
    # any parked browser approval (Round 36 hygiene).
    try:
        import og_browser
        was = og_browser._get_session(uid)
        report["browser_was_live"] = was is not None
        rec = og_browser._end_session(uid, "reboot")
        if rec is not None:
            report["browser_ended"] = True
            report["browser_minutes_used"] = rec.get("minutes_used")
            report["cleared"].append("browser session")
        # A start caught mid-flight has a marker but no
        # record yet — clear the marker either way.
        og_browser._clear_starting(uid)
        if report["browser_was_live"] or rec is not None:
            report["cleared"].append("browser warm state")
    except Exception as e:
        logger.warning(f"Reboot: browser end failed for {uid}: {e}")
    # Crew work in flight: counted and REPORTED — a
    # reboot never deletes or silently kills it.
    try:
        import og_crew
        live = [j for j in og_crew.jobs_for(uid)
                if isinstance(j, dict)
                and j.get("state") in ("working", "queued")]
        report["crew_running"] = len(live)
    except Exception as e:
        logger.warning(f"Reboot: crew count failed for {uid}: {e}")
    return report


def reboot_and_forget(uid: str) -> dict:
    """Round 321 (FIX-PLAN Piece 1): the second door on the
    Reboot card — the soft reboot AND the saved-login wipe
    (og_watch._forget_all: Steel profile DELETE, vault
    dropped, every watch stopped). It exists because the
    plain reboot provably cannot reach the vault: the
    wedge that stranded the owner in October 2026 was a
    stuck saved log-in, and every visible control left it
    exactly where it was. The two halves are independent
    — the report states each truthfully, and a forget
    glitch never masks the reboot's own result."""
    report = soft_reboot(uid)
    report["logins_forgotten"] = False
    report["forget_body"] = ""
    if not uid:
        report["ok"] = False
        return report
    try:
        import og_watch
        blocks = og_watch._forget_all(uid)
        body = "\n".join(str(b.get("body") or "")
                         for b in blocks)
        report["forget_body"] = body.replace("[OG WATCH] ",
                                             "", 1)
        report["logins_forgotten"] = True
        report["cleared"].append("saved logins")
    except Exception as e:
        logger.warning(
            f"Reboot+forget: forget failed for {uid}: {e}")
    return report


# ===========================================================================
# Part 2 — restart OG's server (owner only)
# ===========================================================================


def _cooldown_remaining(now=None) -> int:
    """Seconds left on the rate stamp (0 = free to fire).
    StoreError propagates to the route's R99 answer."""
    rec = _get(_STAMP_KEY)
    if not isinstance(rec, dict):
        return 0
    ts = float(rec.get("ts") or 0)
    if not ts:
        return 0
    now = time.time() if now is None else now
    return max(0, int(RESTART_COOLDOWN_S - (now - ts)))


def status_payload(cookies) -> dict:
    """What the Reboot card paints from: the caller's
    owner flag + the restart door's shape. owner=False
    callers get no owner detail beyond wired/enabled
    being presentational — the endpoint re-checks
    everything regardless."""
    uid, is_owner = _is_owner(cookies)
    return {"ok": True, "owner": is_owner,
            "server_restart": {
                "wired": restart_wired(),
                "enabled": restart_enabled(),
                "cooldown_s": _cooldown_remaining() if is_owner else 0}}


def _render_restart_call(service_id: str, api_key: str):
    """The ONE place the key is used: Render's documented
    restart endpoint, bearer header, nothing logged but
    the outcome status. Returns (status, snippet)."""
    url = f"https://api.render.com/v1/services/{service_id}/restart"
    req = urllib.request.Request(url, data=b"{}", method="POST")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(
                req, timeout=RENDER_API_TIMEOUT_S) as resp:
            body = resp.read(300).decode("utf-8", "replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        try:
            body = e.read(300).decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body


def _fire_restart(service_id: str):
    """Background thread body: wait out the delay (the
    client has its answer), read the key from env HERE
    (never earlier, never stored), fire once, log the
    outcome by status only."""
    try:
        time.sleep(RESTART_FIRE_DELAY_S)
        status, body = _render_restart_call(
            service_id, _render_key())
        if status and 200 <= status < 300:
            logger.info(
                f"Server restart: Render accepted (HTTP {status}) "
                f"for service {service_id}")
        else:
            logger.warning(
                f"Server restart: Render answered HTTP {status} "
                f"for service {service_id}: {body[:200]}")
    except Exception as e:
        logger.warning(f"Server restart: the Render call failed: {e}")


def request_server_restart(cookies):
    """The gate chain. Returns (http_status, payload).
    Every refusal is honest and named; the owner gate's
    refusals also write a log line (the spec's trail)."""
    uid, is_owner = _is_owner(cookies)
    if not is_owner:
        logger.warning(
            "Server restart refused: non-owner session "
            f"(uid={uid or 'none'})")
        return 403, {"ok": False, "reason": "not_owner",
                     "error": "Server restart is owner-only."}
    if not restart_wired():
        return 409, {"ok": False, "reason": "not_wired",
                     "error": "Server restart isn't wired yet."}
    if not restart_enabled():
        return 409, {"ok": False, "reason": "switch_off",
                     "error": "Server restart is switched off "
                              "right now."}
    remaining = _cooldown_remaining()
    if remaining > 0:
        mins = max(1, -(-remaining // 60))
        return 409, {"ok": False, "reason": "cooldown",
                     "retry_after_s": remaining,
                     "error": f"OG's server was just restarted — "
                              f"you can restart again in about "
                              f"{mins} minute{'s' if mins != 1 else ''}."}
    # Stamp FIRST: a double-tap can never double-fire.
    _put(_STAMP_KEY, {"ts": time.time(), "by": uid})
    logger.info(f"Server restart requested by the owner (uid={uid})")
    # The trail: an owner notification record. Best
    # effort — a notify glitch never blocks the restart,
    # and the log line above already stands.
    try:
        import og_notify
        when = time.strftime("%H:%M", time.localtime())
        og_notify.record(
            uid, "notice", "OG server restart",
            f"You restarted OG's server from the Reboot card "
            f"at {when}. OG was offline for everyone for "
            f"about 2 minutes.")
    except Exception as e:
        logger.warning(f"Server restart: notify record failed: {e}")
    threading.Thread(target=_fire_restart,
                     args=(_render_service_id(),),
                     name="og-server-restart", daemon=True).start()
    return 200, {"ok": True, "restarting": True,
                 "eta_s": RESTART_ETA_S}


# ===========================================================================
# Routes
# ===========================================================================

_R99_BODY = {"ok": False,
             "error": "My end hiccupped — try again in a moment."}


def register_reboot_routes(app):

    @app.get("/reboot/status")
    async def reboot_status(request: Request):
        cookies = dict(request.cookies)
        uid, _email = _resolve(cookies)
        if not uid:
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        try:
            payload = await asyncio.to_thread(status_payload, cookies)
        except StoreError:
            return JSONResponse(_R99_BODY, status_code=503)
        return payload

    @app.post("/reboot")
    async def reboot_soft(request: Request):
        cookies = dict(request.cookies)
        uid, _email = _resolve(cookies)
        if not uid:
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        report = await asyncio.to_thread(soft_reboot, uid)
        return report

    @app.post("/reboot/forget")
    async def reboot_forget(request: Request):
        """Round 321: Reboot + forget saved logins — the
        soft reboot plus og_watch._forget_all for the
        CALLER's own account only (same session-resolved
        uid discipline as every route here)."""
        cookies = dict(request.cookies)
        uid, _email = _resolve(cookies)
        if not uid:
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        try:
            report = await asyncio.to_thread(
                reboot_and_forget, uid)
        except StoreError:
            return JSONResponse(_R99_BODY, status_code=503)
        return report

    @app.post("/reboot/server")
    async def reboot_server(request: Request):
        cookies = dict(request.cookies)
        uid, _email = _resolve(cookies)
        if not uid:
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        try:
            status, payload = await asyncio.to_thread(
                request_server_restart, cookies)
        except StoreError:
            return JSONResponse(_R99_BODY, status_code=503)
        return JSONResponse(payload, status_code=status)
