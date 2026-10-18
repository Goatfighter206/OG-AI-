"""2-step verification for OG accounts (Round 63, Brent's
approval of the queue, 2026-10-10): "2-step verification."

WHAT THIS IS. A second factor on top of the og_accounts
password, owned by the ACCOUNT (keyed by the account's
ogai_uid server-side — never by a device or a browser):

    POST /auth/2fa/setup    (signed in) mint a PENDING TOTP
                            secret; returns {secret,
                            otpauth_uri} for manual entry
                            into any authenticator app.
    POST /auth/2fa/enable   {code} verifies the code against
                            the pending secret (+-1 step),
                            turns TOTP on, and returns 8
                            one-time RECOVERY CODES — shown
                            ONCE; only their sha256 hashes
                            are stored.
    POST /auth/2fa/disable  {password, code} — password plus
                            a current TOTP or recovery code;
                            wipes secret, codes and passkeys.
    GET  /auth/2fa/status   {enabled, methods:{totp,
                            passkey, push}}.
    POST /auth/2fa/verify   {challenge, code} — the login
                            second step (see below).

LOGIN STEP. og_accounts' /auth/login checks twofa_enabled()
after the password passes: for a 2FA account it does NOT
mint a session — it returns {"ok": true,
"two_factor_required": true, "challenge": <opaque token>}
and this module holds a single-use challenge server-side
for 5 minutes. /auth/2fa/verify with a right TOTP code OR
an unused recovery code burns the challenge and mints the
session EXACTLY as login does (og_accounts._start_session:
both cookies, remember honoured from the password step).
Wrong codes record failures on the SAME per email+IP
throttle login uses. A TOTP step can satisfy only one
verify (last_step is remembered — no replay inside the
validity window). Accounts without 2FA never touch any of
this: their login response is byte-identical to before.

STEP-UP. og_store_ready's POST /auth/delete requires a
current TOTP/recovery code (extra `code` field) when 2FA
is enabled, on top of the password. Round 76 adds the
signed-in password change to this module (it lives in the
web's Security section next to these controls): POST
/auth/password/change {current_password, new_password} —
the current password is checked with the disable flow's
throttle + generic-failure idiom, the new hash goes
through og_accounts' pbkdf2 machinery, every OTHER session
on the account is revoked, and the caller's own session
stays signed in.

PASSWORD CHANGE + PASSKEY LIST/REMOVE (Round 76). GET
/auth/2fa/passkey/list returns the caller's OWN passkeys
as {id, created} ONLY: the credential id is the client's
remove handle (register options already returns every
stored id to the signed-in client in excludeCredentials);
the public-key halves and sign_count never leave the
server, and the store holds no label to show. POST
/auth/2fa/passkey/remove {credential_id, password} takes
the same password step-up as disable, and the lookup is
scoped to the caller's own record, so another account's
passkey id simply isn't there to remove. Removing the last
passkey follows this module's own 2-step rule —
twofa_enabled() is TOTP OR any passkey, and disable
already lets a passkey-only account turn 2-step off with
the password alone — so a last-passkey removal with TOTP
off turns 2-step off, and with TOTP on it leaves TOTP +
recovery codes untouched.

PASSKEYS (WebAuthn). The `cryptography` package is already
in the deployed environment (a hard dependency of
pywebpush, which requirements.txt pins) — nothing was
added. Registration + assertion are real: a minimal stdlib
CBOR codec, attestation format "none" only, ES256 signature
verification via cryptography. Challenges are single-use,
5-minute, server-held, exactly like the login challenge.
Round 74: both verify handlers also check the
clientDataJSON origin against the RP ID (host equal to the
RP ID or a subdomain of it) — see _origin_ok. The RP ID
itself stays ONE fixed value (OG_WEBAUTHN_RP_ID, else the
OG_PUBLIC_URL host, default getogai.com.co): a passkey is
bound to that one RP by the browser, so passkeys work on
the canonical domain and its subdomains, and never on an
unrelated host — that binding is the phishing resistance,
not a bug (round-74 NOTES.md has the full finding).
If cryptography were ever unavailable, the passkey routes
answer 501 and status reports passkey "unavailable" —
honest dark, never faked.

PUSH APPROVAL. NOT built: status reports methods.push as
"pending_push_round" (verification via push stays dark
until the push round, Round 41's territory). No endpoint
pretends to send or approve anything.

APP LOCK. PINs and biometrics are a client-only device
lock; the server never sees them and this module stores
nothing about them.

STORE. The og_notify/og_reactions durable form: Postgres
table og_2fa_data when OG_MEMORY_DB_URL is set, else a JSON
file (OG_2FA_STORE overrides; default 2fa_store.json).
TOTP secrets are stored per account (the server must be
able to verify codes — that is what a TOTP server is);
recovery codes and challenge tokens are sha256-only.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import struct
import threading
import time
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse

import og_accounts as _acct

logger = logging.getLogger(__name__)

SESSION_COOKIE = _acct.SESSION_COOKIE
CHALLENGE_SECONDS = 5 * 60
TOTP_STEP = 30
TOTP_DIGITS = 6
RECOVERY_COUNT = 8
ISSUER = "OG"

STORE_FILE = os.getenv("OG_2FA_STORE", "2fa_store.json")
_store_lock = threading.RLock()

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, StoreError, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo, pooled_connect as _pooled_connect
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

try:
    from cryptography.exceptions import InvalidSignature as _InvSig
    from cryptography.hazmat.primitives import hashes as _hashes
    from cryptography.hazmat.primitives.asymmetric import ec as _ec
    _CRYPTO_OK = True
except Exception:  # cryptography missing — passkeys honestly dark
    _CRYPTO_OK = False


def webauthn_available() -> bool:
    return _CRYPTO_OK


# --- Durable store (og_notify pattern: one key -> JSON blob) -------------------


def _db_connect():
    conn = _pooled_connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_2fa_data ("
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
            logger.warning(f"2FA store load failed: {e}")
    return {}


def _save_file_store(store: dict):
    try:
        with open(STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"2FA store save failed: {e}")


def _get(key: str):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_2fa_data WHERE key=%s",
                        (key,))
                    row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning(f"2FA DB load failed: {e}")
            raise StoreError("2fa store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
    return store.get(key)


def _put(key: str, value):
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_2fa_data (key, data) "
                        "VALUES (%s, %s) ON CONFLICT (key) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (key, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"2FA DB save failed: {e}")
            raise StoreError("2fa store unavailable (Round 99)") from e
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
                        "DELETE FROM og_2fa_data WHERE key=%s", (key,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"2FA DB delete failed: {e}")
            raise StoreError("2fa store unavailable (Round 99)") from e
    with _store_lock:
        store = _load_file_store()
        store.pop(key, None)
        _save_file_store(store)


# --- Account records -------------------------------------------------------------


def _rec_key(uid: str) -> str:
    return "2fa:" + uid


def get_record(uid: str) -> dict:
    rec = _get(_rec_key(uid)) if uid else None
    return rec if isinstance(rec, dict) else {}


def _save_record(uid: str, rec: dict):
    _put(_rec_key(uid), rec)


def twofa_enabled(uid: str) -> bool:
    """The login step applies when the account has a live
    second factor: TOTP on, or at least one passkey."""
    rec = get_record(uid)
    return bool(rec.get("totp_enabled")) or bool(rec.get("passkeys"))


def purge_uid(uid: str):
    """Account deletion seam (og_store_ready): the whole
    2FA record — secret, recovery hashes, passkeys — goes."""
    if uid:
        _del(_rec_key(uid))


# --- TOTP (RFC 6238, stdlib only) -------------------------------------------------


def _b32_decode(secret_b32: str) -> bytes:
    s = "".join(str(secret_b32).split()).upper()
    s += "=" * ((8 - len(s) % 8) % 8)
    return base64.b32decode(s)


def _new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def totp_code(secret_b32: str, for_time=None, step=TOTP_STEP,
              digits=TOTP_DIGITS) -> str:
    """The RFC 6238 code for a moment in time."""
    t = int(time.time() if for_time is None else for_time)
    counter = t // step
    msg = struct.pack(">Q", counter)
    digest = hmac.new(_b32_decode(secret_b32), msg, hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) \
        % (10 ** digits)
    return str(code).zfill(digits)


def _match_step(secret_b32: str, code: str, window=1):
    """The time-step a code matches (+-window), or None."""
    now = int(time.time())
    for delta in range(-window, window + 1):
        t = now + delta * TOTP_STEP
        if hmac.compare_digest(totp_code(secret_b32, t), code):
            return t // TOTP_STEP
    return None


def _norm_code(raw) -> str:
    return "".join(str(raw or "").split())


def _norm_recovery(raw) -> str:
    return _norm_code(raw).replace("-", "").lower()


def _recovery_hash(norm: str) -> str:
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _new_recovery_codes() -> list:
    out = []
    for _ in range(RECOVERY_COUNT):
        raw = secrets.token_hex(4)  # 8 hex chars
        out.append(raw[:4] + "-" + raw[4:])
    return out


def check_totp(uid: str, raw_code, consume=True) -> bool:
    """A current TOTP code for the account. With consume, a
    matched step is burned (last_step) so the same code can
    never be replayed inside its validity window."""
    rec = get_record(uid)
    if not rec.get("totp_enabled") or not rec.get("secret"):
        return False
    code = _norm_code(raw_code)
    if len(code) != TOTP_DIGITS or not code.isdigit():
        return False
    step_no = _match_step(rec["secret"], code)
    if step_no is None:
        return False
    if consume:
        if step_no <= int(rec.get("last_step") or 0):
            return False
        rec["last_step"] = step_no
        _save_record(uid, rec)
    return True


def check_recovery(uid: str, raw_code, consume=True) -> bool:
    """An unused recovery code; burns on use."""
    rec = get_record(uid)
    hashes = rec.get("recovery") or []
    if not hashes:
        return False
    h = _recovery_hash(_norm_recovery(raw_code))
    for stored in hashes:
        if hmac.compare_digest(stored, h):
            if consume:
                rec["recovery"] = [x for x in hashes if x != stored]
                _save_record(uid, rec)
            return True
    return False


def verify_code_or_recovery(uid: str, raw_code) -> bool:
    """Login verify + step-up seam: TOTP first, then a
    recovery code (which burns). Either consumes."""
    if not isinstance(raw_code, str) or not raw_code.strip():
        return False
    return check_totp(uid, raw_code) or check_recovery(uid, raw_code)


# --- Login challenges ---------------------------------------------------------------


def _chal_key(token: str) -> str:
    return "chal:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_challenge(email: str, uid: str, remember: bool,
                     guest_uid: str = "") -> str:
    token = secrets.token_urlsafe(32)
    _put(_chal_key(token), {
        "email": email, "uid": uid, "remember": bool(remember),
        "guest_uid": guest_uid or "",
        "exp": time.time() + CHALLENGE_SECONDS})
    return token


def get_challenge(token):
    if not isinstance(token, str) or not token:
        return None
    rec = _get(_chal_key(token))
    if not isinstance(rec, dict):
        return None
    if float(rec.get("exp") or 0) <= time.time():
        _del(_chal_key(token))
        return None
    return rec


def burn_challenge(token: str):
    _del(_chal_key(token))


# --- Minimal CBOR (WebAuthn payloads only) -------------------------------------------


def _cbor_decode(data: bytes, pos=0):
    """Decode one CBOR item -> (value, next_pos). Supports
    the subset WebAuthn uses: unsigned/negative ints, byte
    + text strings, arrays, maps, booleans/null."""
    if pos >= len(data):
        raise ValueError("cbor: eof")
    first = data[pos]
    pos += 1
    major, info = first >> 5, first & 0x1F
    if info < 24:
        arg = info
    elif info == 24:
        arg = data[pos]; pos += 1
    elif info == 25:
        arg = struct.unpack(">H", data[pos:pos + 2])[0]; pos += 2
    elif info == 26:
        arg = struct.unpack(">I", data[pos:pos + 4])[0]; pos += 4
    elif info == 27:
        arg = struct.unpack(">Q", data[pos:pos + 8])[0]; pos += 8
    elif info == 31:
        raise ValueError("cbor: indefinite not supported")
    else:
        raise ValueError("cbor: bad info")
    if major == 0:
        return arg, pos
    if major == 1:
        return -1 - arg, pos
    if major in (2, 3):
        raw = data[pos:pos + arg]; pos += arg
        return (raw if major == 2 else raw.decode("utf-8")), pos
    if major == 4:
        out = []
        for _ in range(arg):
            v, pos = _cbor_decode(data, pos)
            out.append(v)
        return out, pos
    if major == 5:
        out = {}
        for _ in range(arg):
            k, pos = _cbor_decode(data, pos)
            v, pos = _cbor_decode(data, pos)
            out[k] = v
        return out, pos
    if major == 7:
        if info == 20:
            return False, pos
        if info == 21:
            return True, pos
        if info == 22:
            return None, pos
        raise ValueError("cbor: simple/float not supported")
    raise ValueError("cbor: bad major")


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64u_decode(text) -> bytes:
    s = "".join(str(text or "").split())
    s += "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode(s.encode("ascii"))


# --- WebAuthn -------------------------------------------------------------------------


def _rp_id() -> str:
    rp = os.getenv("OG_WEBAUTHN_RP_ID", "").strip()
    if rp:
        return rp
    base = os.getenv("OG_PUBLIC_URL", "https://getogai.com.co")
    return urlparse(base).hostname or "getogai.com.co"


def _wa_key(chal_bytes: bytes) -> str:
    return "wachal:" + hashlib.sha256(chal_bytes).hexdigest()


def _new_wa_challenge(uid: str, purpose: str, twofa_chal: str = "") -> bytes:
    raw = secrets.token_bytes(32)
    _put(_wa_key(raw), {
        "uid": uid, "purpose": purpose, "twofa_chal": twofa_chal or "",
        "exp": time.time() + CHALLENGE_SECONDS})
    return raw


def _take_wa_challenge(client_data: dict, uid: str, purpose: str):
    """Validate + burn the WebAuthn challenge inside a
    clientDataJSON dict. Returns the stored record or None."""
    try:
        raw = _b64u_decode(client_data.get("challenge"))
    except Exception:
        return None
    rec = _get(_wa_key(raw))
    if not isinstance(rec, dict):
        return None
    _del(_wa_key(raw))  # single-use either way
    if float(rec.get("exp") or 0) <= time.time():
        return None
    if rec.get("uid") != uid or rec.get("purpose") != purpose:
        return None
    return rec


def _parse_auth_data(auth: bytes):
    """(rp_hash, flags, sign_count, rest) or None."""
    if len(auth) < 37:
        return None
    return auth[:32], auth[32], struct.unpack(">I", auth[33:37])[0], auth[37:]


def _client_data(raw_b64):
    try:
        data = json.loads(_b64u_decode(raw_b64).decode("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _origin_ok(client_data: dict) -> bool:
    """WebAuthn origin check (Round 74): the clientDataJSON
    origin's host must be the RP ID itself or a subdomain
    of it — the relation a browser enforces between an
    origin and the RP it runs a ceremony for (so the
    canonical host and its www subdomain both pass, and a
    ceremony replayed from any other host fails). A missing
    or garbled origin fails. Before R74 the origin field
    was never read; the rpIdHash comparison and the
    single-use challenge carried the ceremony."""
    origin = client_data.get("origin")
    if not isinstance(origin, str) or not origin:
        return False
    host = urlparse(origin).hostname
    if not host:
        return False
    rp = _rp_id()
    return host == rp or host.endswith("." + rp)


# --- Shared route helpers -------------------------------------------------------------


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status)


def _session(request: Request):
    return _acct._session_for(request.cookies.get(SESSION_COOKIE))


def _status_payload(uid: str) -> dict:
    rec = get_record(uid)
    totp = bool(rec.get("totp_enabled"))
    passkey = bool(rec.get("passkeys")) if _CRYPTO_OK else "unavailable"
    return {
        "enabled": bool(totp or rec.get("passkeys")),
        "methods": {
            "totp": totp,
            "passkey": passkey,
            # Verification by push approval is NOT built —
            # it stays dark until the push round. Stated,
            # never faked.
            "push": "pending_push_round",
        },
    }


def _revoke_other_sessions(email: str, keep_token):
    """Password change (Round 76): every OTHER session on
    the account dies; the caller's own session record
    survives, so changing your password never silently logs
    you out. The same store scan og_accounts' reset flow
    performs to kill ALL sessions — narrowed to spare the
    current token's record."""
    keep = _acct._token_key(keep_token) if keep_token else None
    for key, data in _acct._all_items():
        if (key.startswith("sess:") and key != keep
                and isinstance(data, dict)
                and data.get("email") == email):
            _acct._del(key)


def _finish_login(request: Request, rec: dict, chal_token: str):
    """Mint the session exactly as password login does."""
    email, uid = rec.get("email") or "", rec.get("uid") or ""
    burn_challenge(chal_token)
    _acct._del(_acct._fail_key(email, _acct._client_ip(request)))
    _acct._fire_attach_hooks(rec.get("guest_uid") or "", uid, email)
    resp = JSONResponse({"ok": True, "email": email})
    _acct._start_session(resp, email, uid, bool(rec.get("remember")))
    return resp


# --- Routes -----------------------------------------------------------------------------


def register_2fa_routes(app):

    @app.post("/auth/2fa/setup")
    def tf_setup(request: Request):
        sess = _session(request)
        if sess is None:
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        uid = sess.get("uid") or ""
        rec = get_record(uid)
        if rec.get("totp_enabled"):
            return _err("2-step is already on. Turn it off first "
                        "to set up a new code.")
        secret = _new_secret()
        rec["secret_pending"] = secret
        rec["email"] = sess.get("email") or ""
        _save_record(uid, rec)
        email = sess.get("email") or "account"
        uri = (f"otpauth://totp/{ISSUER}:{email}"
               f"?secret={secret}&issuer={ISSUER}"
               f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_STEP}")
        return {"ok": True, "secret": secret, "otpauth_uri": uri}

    @app.post("/auth/2fa/enable")
    async def tf_enable(request: Request):
        sess = _session(request)
        if sess is None:
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        uid = sess.get("uid") or ""
        rec = get_record(uid)
        pending = rec.get("secret_pending")
        if not pending:
            return _err("Set up 2-step first, then enter the code.")
        code = _norm_code((await _json_body(request)).get("code"))
        step_no = _match_step(pending, code) \
            if len(code) == TOTP_DIGITS and code.isdigit() else None
        if step_no is None:
            return _err("That code didn't match — check your "
                        "authenticator app and try again.")
        codes = _new_recovery_codes()
        rec["secret"] = pending
        rec.pop("secret_pending", None)
        rec["totp_enabled"] = True
        rec["last_step"] = step_no
        rec["recovery"] = [_recovery_hash(_norm_recovery(c))
                           for c in codes]
        _save_record(uid, rec)
        # Shown ONCE — only the hashes above are kept.
        return {"ok": True, "recovery_codes": codes}

    @app.post("/auth/2fa/disable")
    async def tf_disable(request: Request):
        sess = _session(request)
        if sess is None:
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        uid = sess.get("uid") or ""
        email = sess.get("email") or ""
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
        rec = get_record(uid)
        if rec.get("totp_enabled"):
            if not verify_code_or_recovery(uid, body.get("code")):
                return _err("Enter a current 2-step code (or a "
                            "recovery code) to turn 2-step off.",
                            status=403)
        _del(_rec_key(uid))
        return {"ok": True}

    @app.post("/auth/password/change")
    async def pw_change(request: Request):
        sess = _session(request)
        if sess is None:
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        email = sess.get("email") or ""
        ip = _acct._client_ip(request)
        if _acct._throttled(email, ip):
            return _err(_acct.ERR_THROTTLED, status=429)
        body = await _json_body(request)
        current = body.get("current_password")
        acct = _acct._get("acct:" + email) if email else None
        stored = acct.get("pw") if isinstance(acct, dict) \
            else _acct._DUMMY_HASH
        ok = isinstance(current, str) and _acct._check_password(
            current, stored)
        if not isinstance(acct, dict) or not ok:
            _acct._record_failure(email, ip)
            return _err(_acct.ERR_GENERIC, status=401)
        new = body.get("new_password")
        if not isinstance(new, str) \
                or len(new) < _acct.MIN_PASSWORD_LEN:
            return _err("New password needs at least "
                        f"{_acct.MIN_PASSWORD_LEN} characters.")
        acct["pw"] = _acct._hash_password(new)
        _acct._put("acct:" + email, acct)
        _acct._del(_acct._fail_key(email, ip))
        _revoke_other_sessions(
            email, request.cookies.get(SESSION_COOKIE))
        return {"ok": True}

    @app.get("/auth/2fa/status")
    async def tf_status(request: Request):
        # Round 107 (B3): both reads are sync store reads
        # (the session lookup, then the 2FA record) that
        # used to run INLINE on the event loop — a slow
        # database froze every visitor's worker behind this
        # poll. Off the loop now; answers are unchanged.
        sess = await asyncio.to_thread(_session, request)
        if sess is None:
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        return await asyncio.to_thread(_status_payload,
                                       sess.get("uid") or "")

    @app.post("/auth/2fa/verify")
    async def tf_verify(request: Request):
        body = await _json_body(request)
        chal_token = body.get("challenge")
        rec = get_challenge(chal_token)
        if rec is None:
            return _err("That sign-in timed out — log in again.",
                        status=401)
        email = rec.get("email") or ""
        ip = _acct._client_ip(request)
        if _acct._throttled(email, ip):
            return _err(_acct.ERR_THROTTLED, status=429)
        if verify_code_or_recovery(rec.get("uid") or "",
                                   body.get("code")):
            return _finish_login(request, rec, chal_token)
        _acct._record_failure(email, ip)
        return _err("That code didn't match — try again.",
                    status=401)

    # --- Passkeys -----------------------------------------------------------------

    @app.post("/auth/2fa/passkey/register/options")
    def pk_reg_options(request: Request):
        sess = _session(request)
        if sess is None:
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        if not _CRYPTO_OK:
            return _err("Passkeys are unavailable on this server.",
                        status=501)
        uid = sess.get("uid") or ""
        chal = _new_wa_challenge(uid, "reg")
        return {"ok": True, "options": {
            "challenge": _b64u(chal),
            "rp": {"id": _rp_id(), "name": ISSUER},
            "user": {"id": _b64u(uid.encode("utf-8")),
                     "name": sess.get("email") or "",
                     "displayName": sess.get("email") or ""},
            "pubKeyCredParams": [{"type": "public-key", "alg": -7}],
            "attestation": "none",
            "authenticatorSelection": {
                "residentKey": "preferred",
                "userVerification": "preferred"},
            "excludeCredentials": [
                {"type": "public-key", "id": p.get("id")}
                for p in get_record(uid).get("passkeys") or []],
        }}

    @app.post("/auth/2fa/passkey/register/verify")
    async def pk_reg_verify(request: Request):
        sess = _session(request)
        if sess is None:
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        if not _CRYPTO_OK:
            return _err("Passkeys are unavailable on this server.",
                        status=501)
        uid = sess.get("uid") or ""
        body = await _json_body(request)
        cd = _client_data(body.get("client_data_json"))
        if not cd or cd.get("type") != "webauthn.create":
            return _err("That passkey didn't check out.")
        if not _origin_ok(cd):
            return _err("That passkey didn't check out.")
        if _take_wa_challenge(cd, uid, "reg") is None:
            return _err("That passkey setup timed out — try again.")
        try:
            att, _ = _cbor_decode(_b64u_decode(
                body.get("attestation_object")))
            if not isinstance(att, dict) or att.get("fmt") != "none":
                return _err("That passkey didn't check out.")
            parsed = _parse_auth_data(att.get("authData") or b"")
            if parsed is None:
                return _err("That passkey didn't check out.")
            rp_hash, flags, _count, rest = parsed
            if not hmac.compare_digest(
                    rp_hash, hashlib.sha256(
                        _rp_id().encode("utf-8")).digest()):
                return _err("That passkey didn't check out.")
            if not (flags & 0x40) or len(rest) < 18:  # AT flag + aaguid
                return _err("That passkey didn't check out.")
            cred_len = struct.unpack(">H", rest[16:18])[0]
            cred_id = rest[18:18 + cred_len]
            cose, _ = _cbor_decode(rest[18 + cred_len:])
            if not isinstance(cose, dict) or cose.get(1) != 2 \
                    or cose.get(3) != -7 or cose.get(-1) != 1:
                return _err("That passkey didn't check out.")
            x, y = cose.get(-2), cose.get(-3)
            if not isinstance(x, bytes) or len(x) != 32 \
                    or not isinstance(y, bytes) or len(y) != 32:
                return _err("That passkey didn't check out.")
        except Exception:
            return _err("That passkey didn't check out.")
        cred_b64 = _b64u(cred_id)
        if body.get("credential_id") and \
                body.get("credential_id") != cred_b64:
            return _err("That passkey didn't check out.")
        rec = get_record(uid)
        keys = [p for p in rec.get("passkeys") or []
                if p.get("id") != cred_b64]
        keys.append({"id": cred_b64, "x": x.hex(), "y": y.hex(),
                     "sign_count": 0,
                     "created": int(time.time())})
        rec["passkeys"] = keys
        rec["email"] = sess.get("email") or ""
        _save_record(uid, rec)
        return {"ok": True}

    @app.get("/auth/2fa/passkey/list")
    def pk_list(request: Request):
        sess = _session(request)
        if sess is None:
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        uid = sess.get("uid") or ""
        # Own passkeys only, safe metadata only: the id is
        # the remove handle (register options already hands
        # it to the signed-in client); x/y/sign_count never
        # leave the server.
        keys = [{"id": p.get("id"),
                 "created": p.get("created") or 0}
                for p in get_record(uid).get("passkeys") or []]
        return {"ok": True, "passkeys": keys}

    @app.post("/auth/2fa/passkey/remove")
    async def pk_remove(request: Request):
        sess = _session(request)
        if sess is None:
            return JSONResponse({"detail": "sign_in_required"},
                                status_code=401)
        uid = sess.get("uid") or ""
        email = sess.get("email") or ""
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
        rec = get_record(uid)
        keys = rec.get("passkeys") or []
        kept = [p for p in keys
                if p.get("id") != body.get("credential_id")]
        if len(kept) == len(keys):
            return _err("That passkey isn't on this account.",
                        status=404)
        rec["passkeys"] = kept
        _save_record(uid, rec)
        return {"ok": True, "passkeys": len(kept),
                "enabled": twofa_enabled(uid)}

    @app.post("/auth/2fa/passkey/options")
    async def pk_assert_options(request: Request):
        if not _CRYPTO_OK:
            return _err("Passkeys are unavailable on this server.",
                        status=501)
        body = await _json_body(request)
        rec = get_challenge(body.get("challenge"))
        if rec is None:
            return _err("That sign-in timed out — log in again.",
                        status=401)
        uid = rec.get("uid") or ""
        keys = get_record(uid).get("passkeys") or []
        if not keys:
            return _err("No passkey on this account — use a code.")
        chal = _new_wa_challenge(uid, "assert",
                                 twofa_chal=body.get("challenge"))
        return {"ok": True, "options": {
            "challenge": _b64u(chal),
            "rpId": _rp_id(),
            "allowCredentials": [
                {"type": "public-key", "id": p.get("id")}
                for p in keys],
            "userVerification": "preferred",
        }}

    @app.post("/auth/2fa/passkey/verify")
    async def pk_assert_verify(request: Request):
        if not _CRYPTO_OK:
            return _err("Passkeys are unavailable on this server.",
                        status=501)
        body = await _json_body(request)
        chal_token = body.get("challenge")
        rec = get_challenge(chal_token)
        if rec is None:
            return _err("That sign-in timed out — log in again.",
                        status=401)
        uid = rec.get("uid") or ""
        email = rec.get("email") or ""
        ip = _acct._client_ip(request)
        if _acct._throttled(email, ip):
            return _err(_acct.ERR_THROTTLED, status=429)
        bad = _err("That passkey didn't check out.", status=401)
        cd_raw = None
        try:
            cd_raw = _b64u_decode(body.get("client_data_json"))
            cd = json.loads(cd_raw.decode("utf-8"))
            auth = _b64u_decode(body.get("authenticator_data"))
            sig = _b64u_decode(body.get("signature"))
        except Exception:
            return bad
        if not isinstance(cd, dict) or cd.get("type") != "webauthn.get":
            return bad
        if not _origin_ok(cd):
            return bad
        wa = _take_wa_challenge(cd, uid, "assert")
        if wa is None or wa.get("twofa_chal") != chal_token:
            return bad
        parsed = _parse_auth_data(auth)
        if parsed is None:
            return bad
        rp_hash, _flags, count, _rest = parsed
        if not hmac.compare_digest(
                rp_hash, hashlib.sha256(
                    _rp_id().encode("utf-8")).digest()):
            return bad
        cred_id = body.get("credential_id")
        key = next((p for p in get_record(uid).get("passkeys") or []
                    if p.get("id") == cred_id), None)
        if key is None:
            return bad
        try:
            pub = _ec.EllipticCurvePublicNumbers(
                int(key["x"], 16), int(key["y"], 16),
                _ec.SECP256R1()).public_key()
            pub.verify(sig, auth + hashlib.sha256(cd_raw).digest(),
                       _ec.ECDSA(_hashes.SHA256()))
        except _InvSig:
            _acct._record_failure(email, ip)
            return bad
        except Exception:
            return bad
        acct_rec = get_record(uid)
        for p in acct_rec.get("passkeys") or []:
            if p.get("id") == cred_id:
                p["sign_count"] = count
        _save_record(uid, acct_rec)
        return _finish_login(request, rec, chal_token)
