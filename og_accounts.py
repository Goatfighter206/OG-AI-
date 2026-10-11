"""Sign-in / accounts for OG (Brent's order, 2026-10-09):
"Let's make sign in and login. And a button to remember user,
to make sure no kids are able to talk to OG."

WHAT THIS IS. Real accounts for OG: email + password sign-up
and login, sessions, and a hard gate — POST /chat refuses
anyone without a valid session (401 sign_in_required). Kids
are kept out at the door two ways: there is no anonymous
talking anymore, and sign-up collects a date of birth and
refuses anyone under 18.

HONEST LIMITS (stated, never oversold):
- The DOB is SELF-REPORTED. It stops casual kid access and
  puts a real account + birthdate attestation in front of
  every conversation; it is NOT ID verification.
- Password reset exists (forgot -> emailed link -> set a
  new password; tokens are sha256-stored, 1-hour, single-
  use, and a reset kills every session on the account) —
  BUT the email sender ships DARK: until OG_SMTP_PASS is
  set on the host (a Google App Password step Brent does
  later), /auth/forgot answers with its usual generic
  success and sends NOTHING. Nobody claims delivery while
  the sender is dark.
- The gate covered POST /chat only in v1. ROUND 44 (Brent,
  2026-10-10: "You should not be able to use OG without
  signing in") extends it to the whole feature surface:
  every non-public route requires a live session. The
  public set is only what a guest strictly needs (the wall
  + its assets, the /auth/* machinery itself, the legal
  pages, /health, the signature-checked Stripe webhook,
  OG's own avatar media) plus the special-handling routes
  documented at PUBLIC_PATHS below (buyer flow, OAuth
  callbacks, owner-key pages, shortlinks).

PASSWORDS. hashlib.pbkdf2_hmac('sha256', ...) with a
per-user 16-byte random salt and 260,000 iterations. Only
"salt_hex$hash_hex" is stored. Passwords and session tokens
are never logged and never stored in plain text — sessions
are stored by their sha256 only.

IDENTITY ATTACH. Every account owns ONE ogai_uid. Sign-up
attaches the caller's CURRENT ogai_uid, so the visitor's
existing memory, tier entitlements, locker, watches and
browser vault (all keyed by uid) ride into the account. A
uid already attached to a different account refuses
(generic error). Login sets the ogai_uid cookie to the
account's uid, so the whole app follows the account to
any device.

SESSIONS + REMEMBER ME. Login/signup mint a session token
(secrets.token_urlsafe); only its sha256 is stored. Cookie
ogai_session is httpOnly + Secure + SameSite=Lax. Remember
me -> persistent cookie, 30-day expiry (server + cookie).
No remember me -> browser session cookie, 24h server-side
expiry. Logout deletes the session server-side, clears the
cookie, and mints a fresh anonymous ogai_uid.

ERRORS. Duplicate signup, uid conflict, unknown email and
wrong password all return the SAME generic error (no
account-existence leak). Login failures are throttled per
email+IP (5 in a row locks that pair for 15 minutes).

STORE. The Round 18/29 durable pattern: Postgres table
og_accounts_data when OG_MEMORY_DB_URL is set, else a JSON
file (accounts_store.json; OG_ACCOUNTS_STORE overrides the
path, used by the test suite).

GATE ACTIVATION. The /chat gate follows the codebase's env
convention: OG_SIGNIN_GATE=on forces it on, =off forces it
off; unset, it is ON on the hosted runtime (Render sets
RENDER=true on every service) and OFF in a bare local/test
process — which is what keeps the repo's own test suite
(it posts /chat with no account) at pristine parity while
production is walled.
"""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import uuid
from datetime import date, datetime, timezone
from http.cookies import SimpleCookie

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

SESSION_COOKIE = "ogai_session"
UID_COOKIE = "ogai_uid"
UID_COOKIE_MAX_AGE = 365 * 24 * 60 * 60  # matches app.py COOKIE_MAX_AGE
PBKDF2_ITERATIONS = 260_000
MIN_PASSWORD_LEN = 8
REMEMBER_SECONDS = 30 * 24 * 60 * 60
SESSION_SECONDS = 24 * 60 * 60
FAIL_LIMIT = 5
FAIL_LOCK_SECONDS = 15 * 60

ERR_GENERIC = "That didn't work — check your details and try again."
ERR_UNDERAGE = ("OG is 18+ only — you have to be 18 or older "
                "to create an account.")
ERR_THROTTLED = "Too many tries — wait a bit and try again."
FORGOT_OK = "If that email has an account, a reset link is on its way."
ERR_RESET = "That reset link didn't work. Ask for a new one and try again."
RESET_SECONDS = 60 * 60

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")

STORE_FILE = os.getenv("OG_ACCOUNTS_STORE", "accounts_store.json")
_store_lock = threading.Lock()

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None


# --- Durable store (Round 18 pattern: one key -> JSON blob) --------------------


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_accounts_data ("
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
            logger.warning(f"Accounts store load failed: {e}")
    return {}


def _save_file_store(store: dict):
    try:
        with open(STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Accounts store save failed: {e}")


def _get(key: str):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_accounts_data WHERE key=%s",
                        (key,))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning(f"Accounts DB load failed, using file: {e}")
    with _store_lock:
        store = _load_file_store()
    return store.get(key)


def _put(key: str, value):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_accounts_data (key, data) "
                        "VALUES (%s, %s) ON CONFLICT (key) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (key, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Accounts DB save failed, using file: {e}")
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
                        "DELETE FROM og_accounts_data WHERE key=%s", (key,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Accounts DB delete failed, using file: {e}")
    with _store_lock:
        store = _load_file_store()
        store.pop(key, None)
        _save_file_store(store)


# --- Passwords -------------------------------------------------------------------


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return salt.hex() + "$" + dk.hex()


def _check_password(password: str, stored: str) -> bool:
    try:
        salt_hex, hash_hex = stored.split("$", 1)
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"),
            bytes.fromhex(salt_hex), PBKDF2_ITERATIONS)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# A throwaway hash so an unknown email costs the same pbkdf2
# work as a known one (no timing leak on account existence).
_DUMMY_HASH = _hash_password(secrets.token_urlsafe(12))


# --- Age gate ----------------------------------------------------------------------


def _age_on(dob: date, today: date) -> int:
    years = today.year - dob.year
    if (today.month, today.day) < (dob.month, dob.day):
        years -= 1
    return years


def _parse_dob(raw):
    if not isinstance(raw, str):
        return None
    try:
        dob = datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except Exception:
        return None
    today = datetime.now(timezone.utc).date()
    if dob > today or _age_on(dob, today) > 120:
        return None
    return dob


# --- Sessions ------------------------------------------------------------------------


def _token_key(token: str) -> str:
    return "sess:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_session(email: str, uid: str, remember: bool) -> str:
    token = secrets.token_urlsafe(32)
    ttl = REMEMBER_SECONDS if remember else SESSION_SECONDS
    _put(_token_key(token), {
        "email": email, "uid": uid,
        "exp": time.time() + ttl, "remember": bool(remember)})
    return token


def _session_for(token):
    """The live session dict for a raw cookie token, or None."""
    if not token:
        return None
    sess = _get(_token_key(token))
    if not isinstance(sess, dict):
        return None
    if float(sess.get("exp") or 0) <= time.time():
        _del(_token_key(token))
        return None
    return sess


def _client_ip(request: Request) -> str:
    fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if fwd:
        return fwd
    return request.client.host if request.client else "unknown"


def _fail_key(email: str, ip: str) -> str:
    raw = (email + "|" + ip).encode("utf-8")
    return "fail:" + hashlib.sha256(raw).hexdigest()


def _throttled(email: str, ip: str) -> bool:
    rec = _get(_fail_key(email, ip))
    if not isinstance(rec, dict):
        return False
    return float(rec.get("locked_until") or 0) > time.time()


def _record_failure(email: str, ip: str):
    key = _fail_key(email, ip)
    rec = _get(key)
    count = int(rec.get("count") or 0) + 1 if isinstance(rec, dict) else 1
    locked = time.time() + FAIL_LOCK_SECONDS if count >= FAIL_LIMIT else 0
    _put(key, {"count": count, "locked_until": locked})


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


# --- Password reset ----------------------------------------------------------


def _all_items():
    """Every (key, data) pair in the store (reset housekeeping)."""
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT key, data FROM og_accounts_data")
                    return cur.fetchall()
        except Exception as e:
            logger.warning(f"Accounts DB scan failed, using file: {e}")
    with _store_lock:
        return list(_load_file_store().items())


def _delete_sessions_for(email: str):
    for key, data in _all_items():
        if (key.startswith("sess:") and isinstance(data, dict)
                and data.get("email") == email):
            _del(key)


def _reset_key(token: str) -> str:
    return "reset:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _issue_reset(email: str) -> str:
    """Mint a reset token for the account; any previous one dies."""
    old = _get("resetfor:" + email)
    if isinstance(old, str):
        _del(old)
    token = secrets.token_urlsafe(32)
    key = _reset_key(token)
    _put(key, {"email": email, "exp": time.time() + RESET_SECONDS})
    _put("resetfor:" + email, key)
    return token


def _smtp_settings() -> dict:
    return {
        "host": os.getenv("OG_SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.getenv("OG_SMTP_PORT", "587") or "587"),
        "user": os.getenv("OG_SMTP_USER", ""),
        "password": os.getenv("OG_SMTP_PASS", ""),
        "from": os.getenv("OG_SMTP_FROM", "") or os.getenv(
            "OG_SMTP_USER", ""),
    }


def _send_reset_email(email: str, reset_url: str) -> bool:
    """Plain-text reset email via stdlib smtplib (STARTTLS).
    DARK while OG_SMTP_PASS is unset: sends nothing, returns
    False. Never logs the address or the token."""
    cfg = _smtp_settings()
    if not cfg["password"] or not cfg["user"]:
        return False
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["From"] = cfg["from"]
    msg["To"] = email
    msg["Subject"] = "Reset your OG password"
    msg.set_content(
        "Someone asked to reset the password on this OG account.\n\n"
        "Set a new password here (the link works for 1 hour):\n"
        + reset_url + "\n\n"
        "This link expires in 1 hour. If you didn't ask for this, "
        "ignore this email — your password stays the same.\n")
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as smtp:
            smtp.starttls()
            smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(msg)
        logger.info("Password reset email sent")
        return True
    except Exception:
        logger.warning("Password reset email failed to send")
        return False


def _reset_url(token: str) -> str:
    base = os.getenv(
        "OG_PUBLIC_URL", "https://og-ai-service.onrender.com")
    return base.rstrip("/") + "/?reset=" + token


def _session_cookie(resp: JSONResponse, token: str, remember: bool):
    kwargs = dict(path="/", httponly=True, secure=True, samesite="lax")
    if remember:
        kwargs["max_age"] = REMEMBER_SECONDS
    resp.set_cookie(SESSION_COOKIE, token, **kwargs)


def _uid_cookie(resp: JSONResponse, uid: str):
    resp.set_cookie(
        UID_COOKIE, uid, max_age=UID_COOKIE_MAX_AGE,
        path="/", httponly=True, samesite="lax")


def _start_session(resp: JSONResponse, email: str, uid: str, remember: bool):
    token = _new_session(email, uid, remember)
    _session_cookie(resp, token, remember)
    _uid_cookie(resp, uid)


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


def _clean_email(raw) -> str:
    return raw.strip().lower() if isinstance(raw, str) else ""


# --- Attach hooks ---------------------------------------------------------------------
# A successful login swaps the visitor's identity from the pre-login
# ogai_uid cookie to the account's uid. Anything keyed by the guest
# uid that should follow the person (today: a Stripe entitlement a
# guest buyer earned in this browser) registers a hook here;
# app.py binds them. Hooks run fail-quiet after the login succeeds:
# fn(guest_uid, account_uid, email). Signup needs no hook — it
# attaches the caller's current uid as-is.

_ATTACH_HOOKS = []


def register_attach_hook(fn):
    _ATTACH_HOOKS.append(fn)


def _fire_attach_hooks(guest_uid, account_uid, email):
    if not guest_uid or not account_uid or guest_uid == account_uid:
        return
    for fn in list(_ATTACH_HOOKS):
        try:
            fn(guest_uid, account_uid, email)
        except Exception as e:
            logger.warning(f"Attach hook failed: {e}")


# --- The sign-in gate -------------------------------------------------------------------


def gate_enabled() -> bool:
    v = os.getenv("OG_SIGNIN_GATE", "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    # Unset: walled on the hosted runtime, open in a bare
    # local/test process (keeps the repo suite at parity).
    return bool(os.getenv("RENDER"))


# Round 44: the public surface. Everything NOT matched here needs
# a live session when the gate is on. Kept as data so the round's
# suite asserts the census against these exact tables.
#
# PUBLIC-BY-NECESSITY: the wall page + its static assets, the
# /auth/* machinery a guest must reach, the legal pages, the
# Render health check (static text only), the Stripe webhook
# (its signature is its auth), and OG's own avatar media (no
# user data; public cache headers by design).
# SPECIAL-HANDLING: /pro + /pro/success (buyer flow — the guest
# landing is handled in og_tiers), /tiers (Round 58: the public
# commercial grid — pricing info only, no user data, same
# posture as /pro), the OAuth callbacks (they
# authenticate by signed state naming the uid and must survive
# the cross-site return hop), /stats + /reports + /system/check
# (owner-key auth in their handlers), and /s/<code> shortlinks (a bearer link is
# its audience by design).
PUBLIC_PATHS = frozenset({
    "/", "/static",
    "/auth/signup", "/auth/login", "/auth/logout", "/auth/me",
    "/auth/forgot", "/auth/reset",
    "/privacy", "/terms", "/health",
    "/system/check", "/system/lessons",
    "/stripe/webhook",
    "/avatar/poster.webp",
    "/pro", "/pro/success", "/tiers",
    "/stats", "/reports",
    "/auth/google/callback", "/auth/discord/callback",
    "/auth/github/callback", "/auth/reddit/callback",
    "/auth/spotify/callback", "/auth/twitch/callback",
    "/auth/youtube/callback", "/auth/coinbase/callback",
    "/auth/cb/callback",
})

_PUBLIC_PREFIXES = ("/static/", "/s/")


def _is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    if path.startswith(_PUBLIC_PREFIXES):
        return True
    # OG's own avatar clips (/avatar/<state>.mp4) are public media;
    # /avatar/status is an API probe and stays gated.
    return path.startswith("/avatar/") and path.endswith(".mp4")


class SignInGateMiddleware:
    """Pure ASGI gate (Round 44: the whole feature surface):
    any non-public route without a live session gets a 401
    sign_in_required and never reaches the app — no handler
    runs, so no usage accrues, no uid is minted, nothing
    starts. Public routes (PUBLIC_PATHS) pass through, and a
    valid session's response is never wrapped or read
    (streaming-safe)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope.get("type") == "http"
                and gate_enabled()
                and not _is_public_path(scope.get("path") or "")):
            token = ""
            for name, value in scope.get("headers") or []:
                if name.lower() == b"cookie":
                    try:
                        jar = SimpleCookie()
                        jar.load(value.decode("latin-1"))
                        if SESSION_COOKIE in jar:
                            token = jar[SESSION_COOKIE].value
                    except Exception:
                        token = ""
                    break
            if _session_for(token) is None:
                body = json.dumps(
                    {"detail": "sign_in_required"}).encode("utf-8")
                await send({
                    "type": "http.response.start", "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


# --- Routes -----------------------------------------------------------------------------

def register_account_routes(app):
    app.add_middleware(SignInGateMiddleware)

    @app.post("/auth/signup")
    async def auth_signup(request: Request):
        body = await _json_body(request)
        email = _clean_email(body.get("email"))
        password = body.get("password")
        remember = bool(body.get("remember"))
        if (not EMAIL_RE.match(email or "")
                or not isinstance(password, str)
                or len(password) < MIN_PASSWORD_LEN):
            return _err(ERR_GENERIC)
        dob = _parse_dob(body.get("dob"))
        if dob is None:
            return _err(ERR_GENERIC)
        today = datetime.now(timezone.utc).date()
        if _age_on(dob, today) < 18:
            return _err(ERR_UNDERAGE)
        if _get("acct:" + email) is not None:
            return _err(ERR_GENERIC)
        uid = request.cookies.get(UID_COOKIE) or uuid.uuid4().hex
        owner = _get("uidacct:" + uid)
        if owner is not None and owner != email:
            return _err(ERR_GENERIC)
        _put("acct:" + email, {
            "email": email, "uid": uid,
            "pw": _hash_password(password),
            "dob": dob.isoformat(),
            "created": datetime.now(timezone.utc).isoformat()})
        _put("uidacct:" + uid, email)
        resp = JSONResponse({"ok": True, "email": email})
        _start_session(resp, email, uid, remember)
        return resp

    @app.post("/auth/login")
    async def auth_login(request: Request):
        body = await _json_body(request)
        email = _clean_email(body.get("email"))
        password = body.get("password")
        remember = bool(body.get("remember"))
        ip = _client_ip(request)
        if _throttled(email, ip):
            return _err(ERR_THROTTLED, status=429)
        acct = _get("acct:" + email) if email else None
        stored = acct.get("pw") if isinstance(acct, dict) else _DUMMY_HASH
        ok = isinstance(password, str) and _check_password(password, stored)
        if not isinstance(acct, dict) or not ok:
            _record_failure(email, ip)
            return _err(ERR_GENERIC, status=401)
        _del(_fail_key(email, ip))
        # Round 44: uid-keyed guest state (a buyer entitlement)
        # follows the person onto their account's uid.
        _fire_attach_hooks(
            request.cookies.get(UID_COOKIE), acct["uid"], email)
        resp = JSONResponse({"ok": True, "email": email})
        _start_session(resp, email, acct["uid"], remember)
        return resp

    @app.post("/auth/logout")
    async def auth_logout(request: Request):
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            _del(_token_key(token))
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            SESSION_COOKIE, "", max_age=0,
            path="/", httponly=True, secure=True, samesite="lax")
        _uid_cookie(resp, uuid.uuid4().hex)  # fresh anonymous visitor
        return resp

    @app.get("/auth/me")
    async def auth_me(request: Request):
        sess = _session_for(request.cookies.get(SESSION_COOKIE))
        if sess is None:
            return {"signed_in": False, "email": None}
        return {"signed_in": True, "email": sess.get("email")}

    @app.post("/auth/forgot")
    async def auth_forgot(request: Request):
        # ALWAYS the same generic success — whether the email
        # has an account or not (no existence leak). A real
        # account gets a fresh single-use token (the previous
        # one dies) and, once the sender is activated, the
        # email. While the sender is dark nothing is sent.
        body = await _json_body(request)
        email = _clean_email(body.get("email"))
        acct = _get("acct:" + email) if email else None
        if isinstance(acct, dict):
            token = _issue_reset(email)
            _send_reset_email(email, _reset_url(token))
        return {"ok": True, "message": FORGOT_OK}

    @app.post("/auth/reset")
    async def auth_reset(request: Request):
        body = await _json_body(request)
        token = body.get("token")
        password = body.get("password")
        rec = (_get(_reset_key(token))
               if isinstance(token, str) and token else None)
        if (not isinstance(rec, dict)
                or float(rec.get("exp") or 0) <= time.time()
                or not isinstance(password, str)
                or len(password) < MIN_PASSWORD_LEN):
            return _err(ERR_RESET)
        email = rec.get("email") or ""
        acct = _get("acct:" + email)
        if not isinstance(acct, dict):
            return _err(ERR_RESET)
        acct["pw"] = _hash_password(password)
        _put("acct:" + email, acct)
        _del(_reset_key(token))          # single-use: burn it
        _del("resetfor:" + email)
        _delete_sessions_for(email)      # every session dies
        return {"ok": True}
