"""Shared Postgres connection discipline (Round 94).

Why this module exists: the distress episodes of
2026-10-11/12 — both gunicorn workers hanging at the same
moment at idle CPU and memory, health checks timing out,
the site 502ing for minutes, twice staying down ~1-2h
until a deploy booted a fresh instance. The Render metrics
hunt cleared CPU and memory; the shape that remained was
a SHARED dependency stalling, and the workers share
Postgres (Neon) above all.

Every store module in this codebase opens its connections
with the same literal — psycopg.connect(URL,
connect_timeout=5) — so a dead endpoint fails the
handshake in 5s. But nothing bounded the STATEMENTS: once
connected, a query against a stalled backend (a suspended
compute waking badly, a wedged session, a lock queue)
could wait forever, parking the thread — or, on the
paths that run on the event loop, the whole worker —
until gunicorn's 120s timeout killed it. Both workers
doing that at once is exactly the paired-hang evidence.

This module is the ONE place the bounds live, imported by
every module that connects (the census is in
round-94/NOTES.md):

  DB_CONNECT_TIMEOUT — seconds for the TCP/TLS/auth
    handshake. The standing value every site already
    used; a live endpoint answers in well under it.

  DB_STATEMENT_TIMEOUT_MS — any single statement is
    cancelled server-side after 10s. Twelve times inside
    the 120s worker timeout, and far above every
    legitimate statement in this codebase (single-row key
    lookups/upserts, plus two bounded whole-store reads
    that answer in about a second on a live backend) —
    so it only ever fires on a genuinely stalled backend,
    where it turns a parked worker into a fast failure.

Round 99 adds the second half of that sentence: WHAT a
store does with the fast failure. Before Round 99, every
store caught the exception and fell back to its local
JSON file — on the hosted runtime an empty or ancient
shadow — so a stalled database MANUFACTURED answers:
"account not found" for a correct password (the
2026-10-12 owner lockout), a live session read as
signed-out, writes silently swallowed into a file the
database would never see. The law now: when a database
IS configured, a store that cannot get an answer raises
StoreError (defined here, the one place every store
already imports from) and its caller fails honestly —
an error, never an invented answer. The file store
remains only for the genuinely unconfigured case (no
OG_MEMORY_DB_URL — local/dev), never as a runtime
fallback. The stores converted to the law, and the ones
named safe without it, are censused in
round-99/NOTES.md.

  DB_LOCK_TIMEOUT_MS — waiting to acquire a lock is
    bounded at 5s, half the statement budget, so a lock
    queue behind a stuck transaction fails the statement
    instead of silently eating the whole budget.

Until Round 321 Step 4c the two bounds also rode the
libpq startup packet, as DB_OPTIONS — a string built
from the constants below and passed as options= on
every connect. Round 321 Step 4 (R100) first qualified
that channel: a transaction pooler is NOT obliged to
carry startup-packet GUCs into the backend session it
hands out. Production then convicted it outright on
2026-10-15: the Neon pooler REFUSES the startup
parameter (unsupported startup parameter in options:
statement_timeout — use an unpooled connection or
remove the parameter), and 42 connects died on that
refusal in the first minutes after the pooler flip.
DB_OPTIONS is therefore deleted and every connect now
carries no options= at all. apply_bounds() below
re-asserts the two bounds with post-connect SETs,
which bind the session itself however the session was
obtained — the SETs are now the sole carrier of the
bounds, on the direct host and the pooled endpoint
alike. Step 4 also adopted app.py's remaining connect
site, so the R94-era note further down ("app.py's own
two connect sites are NOT covered") no longer holds.

app.py's own two connect sites are NOT covered by the
import sweep — app.py is at its push ceiling and is
edited only under its own rule; see round-94/NOTES.md
for the exact remainder and the one-line fix each site
needs.
"""

DB_CONNECT_TIMEOUT = 5
DB_STATEMENT_TIMEOUT_MS = 10000
DB_LOCK_TIMEOUT_MS = 5000

# No DB_OPTIONS (deleted in Round 321 Step 4c): the startup
# packet carries no bounds — the pooler rejects them there.
# apply_bounds() below is the sole carrier; see the docstring.

# Round 99: the waking-backend allowance. Field evidence
# (2026-10-12 ~05:55, live probes): in the hosted
# backend's cold regime, login attempts consistently
# take ~5.3s — right at DB_CONNECT_TIMEOUT — while plain
# reads answer in under a second: a connect can burn
# its whole 5s budget against a backend that is WAKING,
# not down. The shared connect timeout stays 5s
# (lengthening it would lengthen every genuinely-down
# failure at all 44 call sites); instead, a store whose
# failure locks a human out may retry the CONNECT once
# after a short pause: the first attempt is what pokes
# the backend awake, the second lands on it. Only
# connect-phase failures retry — a statement that was
# accepted and then cancelled/timed out is a wedged
# backend, and retrying it would just double the stall.
# Adopted by og_accounts (the identity store) in this
# round; other stores fail honestly for the length of a
# wake instead (see round-99/NOTES.md).
DB_CONNECT_RETRIES = 1
DB_CONNECT_RETRY_DELAY = 0.5


class StoreError(Exception):
    """The database is configured but did not answer.

    Raised by a store INSTEAD of falling back to its
    local file shadow (Round 99). Callers must treat it
    as "unknown / try again later" — never as "not
    found", never as a user's failed attempt, never as
    signed-out. og_accounts registers one app-wide
    handler that answers it with an honest 503."""

    def __init__(self, *args):
        super().__init__(*args)
        _count_store_error()


# ===========================================================================
# Round 321 (Step 2): StoreError counters — the stability instrumentation.
#
# Every StoreError raise is counted per raising store per hour, in
# memory, so the self-check report can turn "the database stalled"
# from a guess into a number (FIX-PLAN Piece 2, suspect S3 vs S1).
# The counters are a bounded record, not a pile: at most ~48 hours of
# buckets are kept, pruned on write. Counting NEVER raises — a
# failure inside the counter must not break the raise it rides on.
# The store name is the module that raised (the first stack frame
# outside this file). A store that catches a StoreError and re-wraps
# it in a new StoreError counts twice; the number is a stall
# indicator, not an audit ledger.
# ===========================================================================

import sys as _sys
import threading as _threading
import time as _time

_SE_LOCK = _threading.Lock()
_SE_COUNTS: dict = {}
_SE_KEEP_HOURS = 48


def _raising_store() -> str:
    try:
        frame = _sys._getframe(3)
        while frame is not None:
            name = str(frame.f_globals.get("__name__") or "")
            if name and name != __name__:
                return name
            frame = frame.f_back
    except Exception:
        pass
    return "unknown"


def _count_store_error() -> None:
    try:
        hour = int(_time.time() // 3600)
        store = _raising_store()
        with _SE_LOCK:
            key = (store, hour)
            _SE_COUNTS[key] = _SE_COUNTS.get(key, 0) + 1
            if len(_SE_COUNTS) > _SE_KEEP_HOURS * 8:
                cutoff = hour - _SE_KEEP_HOURS
                for k in [k for k in _SE_COUNTS if k[1] < cutoff]:
                    del _SE_COUNTS[k]
    except Exception:
        pass


def store_error_snapshot() -> dict:
    """{store: {"<hour-epoch>": count}} — the self-check
    report's read of the counters (this process only; each
    worker counts its own raises)."""
    with _SE_LOCK:
        items = dict(_SE_COUNTS)
    out: dict = {}
    for (store, hour), n in items.items():
        out.setdefault(store, {})[str(hour)] = n
    return out


# ===========================================================================
# Round 321 (Step 4 — R100): post-connect bounds.
#
# The startup packet is a request, not a guarantee: a
# transaction pooler may hand out a backend session whose
# GUCs owe nothing to the options string the client sent
# (the session may predate the connect, or serve many
# clients in turn). A SET issued on the live connection
# has no such ambiguity — it binds THIS session, direct
# host or pooled. apply_bounds is therefore called
# immediately after connect at every psycopg.connect site
# (the census is in round-321/work/step4/NOTES-draft.md);
# the values are the same constants the startup packet
# already carries, so on the direct host this is a
# no-op-strengthening re-assert, and on a pooled endpoint
# it is the only thing making the bounds real. Cost: two
# extra statements per connect (~ms on a live backend).
#
# FAIL-SOFT, by law: a failed SET must never break the
# connect or its caller. The connection still carries
# whatever the startup packet bought it (on the direct
# host: the full bounds), so a failed re-assert leaves
# the status quo, never something worse — while a store
# that let the exception escape would manufacture an
# outage out of a hardening step. A failure is counted
# through the Step 2 machinery above (the raising-store
# attribution reads the adopting module, exactly as it
# does for a StoreError raised inside a store helper)
# and the connection is rolled back so a statement the
# backend cancelled mid-flight cannot leave the caller's
# first real statement facing an aborted transaction.
# Idempotent: re-asserting the same values is a no-op.
# ===========================================================================

def apply_bounds(conn) -> None:
    """Re-assert DB_STATEMENT_TIMEOUT_MS / DB_LOCK_TIMEOUT_MS
    on a live connection via post-connect SETs. Never
    raises."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SET statement_timeout = {DB_STATEMENT_TIMEOUT_MS}")
            cur.execute(
                f"SET lock_timeout = {DB_LOCK_TIMEOUT_MS}")
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        _count_store_error()


# ===========================================================================
# Round 321 (Step 4d): pooler-valid conninfo by construction.
#
# Conviction (2026-10-16, second pooler flip): with Step 4c's
# options= gone, the startup refusals stopped, but OG's
# background database work failed through the Neon pooler
# with two new refusals — `connection is insecure (try using
# sslmode=require)` and `database "neon" does not exist`.
# The tree was swept: every one of the 71 psycopg.connect
# sites passes the module's own MEMORY_DB_URL, captured
# identically from OG_MEMORY_DB_URL, and the string sslmode
# occurred NOWHERE in the tree — so every connection ran
# with libpq's compiled default sslmode=prefer (try TLS,
# silently permit a plaintext fallback). The direct Neon
# endpoint tolerates that; the PgBouncer pooler does not:
# a connection must ARRIVE with sslmode=require. The other
# refusal is env-owned, not code-owned: the configured
# DSN's path segment IS its dbname, and code must never
# invent, guess, or alter a database name (the dbname
# correction belongs to the OG_MEMORY_DB_URL value itself,
# corrected env-side from the Neon Console's authoritative
# pooled connection string). What code owns is the sslmode
# guarantee, and that is the whole job of the function
# below — guarantee it in code, at every connect site.
#
# normalize_conninfo(conninfo) is pure and NEVER raises:
#   - URL-form (postgres:// / postgresql://): sslmode is
#     added to the query when absent, and a weaker value
#     (disable / allow / prefer / require) is raised to
#     require IN PLACE; verify-ca / verify-full are
#     stronger and are preserved, never downgraded. All
#     other query tokens and their order, and the scheme /
#     userinfo / host / port / path, pass through
#     byte-for-byte (no parsing-and-rebuilding: credentials
#     are never decoded or re-encoded).
#   - Keyword-form conninfo strings get the same guarantee
#     on their sslmode token, without rewriting any other
#     token.
#   - Empty input, non-string input, malformed input, and
#     non-Postgres URI schemes return UNCHANGED — the
#     Round 99 unconfigured (no OG_MEMORY_DB_URL) file-store
#     behaviour is untouched, and a shape the normalizer
#     does not understand fails visibly at connect time
#     rather than being mangled here.
#   - Idempotent: normalize(normalize(x)) == normalize(x).
#   - No options= is reintroduced (Step 4c law); the bounds
#     still ride apply_bounds()' post-connect SETs only.
# Adopted at every psycopg.connect site in the tree as
# psycopg.connect(_normalize_conninfo(MEMORY_DB_URL),
# connect_timeout=_DB_CT) — census in
# round-321/work/step4d/NOTES-draft.md.
# ===========================================================================

_SSLMODE_STRONGER = ("verify-ca", "verify-full")


def _keyword_spans(text):
    """Tokenize a keyword conninfo string into (start, end,
    key, raw_value) spans, respecting single-quoted values
    and backslash escapes (libpq style). Returns None if
    any token is not a key=value pair (malformed)."""
    spans = []
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] in " \t":
            i += 1
        if i >= n:
            break
        start = i
        eq = text.find("=", i)
        if eq == -1:
            return None
        key = text[i:eq]
        if not key or any(c in key for c in " \t'"):
            return None
        i = eq + 1
        vstart = i
        if i < n and text[i] == "'":
            i += 1
            while i < n:
                if text[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                if text[i] == "'":
                    i += 1
                    break
                i += 1
            else:
                return None
        else:
            while i < n and text[i] not in " \t":
                if text[i] == "\\" and i + 1 < n:
                    i += 1
                i += 1
        spans.append((start, i, key, text[vstart:i]))
        if i < n and text[i] not in " \t":
            return None
    return spans or None


def _unquote(raw):
    if len(raw) >= 2 and raw[0] == "'" and raw[-1] == "'":
        return raw[1:-1].replace("\\'", "'").replace("\\\\", "\\")
    return raw


def normalize_conninfo(conninfo):
    """Return conninfo with an effective sslmode=require
    guaranteed (see the Step 4d block above). Pure; never
    raises; anything unrecognized returns unchanged."""
    try:
        if not isinstance(conninfo, str) or not conninfo.strip():
            return conninfo
        s = conninfo
        low = s.lower()
        if "://" in s:
            scheme = low.split("://", 1)[0]
            if scheme not in ("postgres", "postgresql"):
                return conninfo
            # URL form: string surgery on the query only.
            hash_i = s.find("#")
            head, frag = (s, "") if hash_i == -1 else \
                (s[:hash_i], s[hash_i:])
            q_i = head.find("?")
            if q_i == -1:
                return head + "?sslmode=require" + frag
            base, query = head[:q_i], head[q_i + 1:]
            if not query:
                return base + "?sslmode=require" + frag
            tokens = query.split("&")
            for idx, tok in enumerate(tokens):
                key, eq, val = tok.partition("=")
                if key != "sslmode":
                    continue
                v = val if eq else ""
                if v == "require" or v in _SSLMODE_STRONGER:
                    return conninfo
                tokens[idx] = "sslmode=require"
                return base + "?" + "&".join(tokens) + frag
            return base + "?" + query + "&sslmode=require" + frag
        # Keyword form: same guarantee on the sslmode token,
        # other tokens byte-identical (span replacement).
        spans = _keyword_spans(s)
        if spans is None:
            return conninfo
        for start, end, key, raw in spans:
            if key != "sslmode":
                continue
            v = _unquote(raw)
            if v == "require" or v in _SSLMODE_STRONGER:
                return conninfo
            return s[:start] + "sslmode=require" + s[end:]
        sep = "" if s[-1] in " \t" else " "
        return s + sep + "sslmode=require"
    except Exception:
        return conninfo


# ===========================================================================
# Round 321 (DBPOOL): pooled connections.
#
# Conviction (2026-10-16, measured against production with an
# authenticated test account): login 42s, /feed 55s for an
# 80-byte answer, /chat over 90s — while every anonymous or
# store-free request answered in ~1s. The stores' law since
# Round 18 was one FRESH psycopg connection per store op
# (connect, bounds, query, close). In the database's current
# regime a connect costs ~3-5s (see the Round 99 waking-backend
# note above: ~5.3s observed 2026-10-12), and a real request
# stacks 8-30 sequential ops, so every deadline-bound request
# (chat 30/45s, sheet/data 15s, feed 12s) died of old age while
# deadline-free polls eventually completed and made the logs
# look clean. The lab never saw it (the file store costs ~0ms);
# unauthenticated probes never saw it (no store op at all).
#
# The pool keeps a small set of live connections per conninfo
# per process and hands them out again. pooled_connect() is a
# drop-in for psycopg.connect(...) at the store wrappers: the
# caller uses the returned object exactly like a raw connection
# (with-block, cursor(), commit()); close() and context-exit
# RETURN it to the pool instead of closing it. Release is
# idempotent; a connection found broken at checkin or checkout
# is discarded and replaced, never handed out twice.
#
# Bounds law unchanged: apply_bounds() still runs at creation
# AND the wrappers still re-assert it on every checkout,
# exactly as they did on every fresh connect — on a transaction
# pooler the re-assert is what binds the session in hand.
#
# Fail-honest, by the Round 99 law: a creation failure raises
# exactly as a raw connect's would (the accounts retry wrapper
# still sees it); pool exhaustion NEVER fails a caller — after
# a bounded wait it hands out a transient unpooled connection
# that really closes on release.
# ===========================================================================

_POOL_MAX_IDLE = 8
_POOL_MAX_TOTAL = 12
_POOL_IDLE_PING_S = 30.0
_POOL_WAIT_S = 10.0


class _Pool:
    __slots__ = ("cond", "idle", "total")

    def __init__(self):
        self.cond = _threading.Condition(_threading.Lock())
        self.idle = []  # [(conn, released_monotonic)]
        self.total = 0  # live conns owned: idle + checked out


_POOLS = {}
_POOLS_LOCK = _threading.Lock()


def _pool_for(conninfo):
    with _POOLS_LOCK:
        pool = _POOLS.get(conninfo)
        if pool is None:
            pool = _Pool()
            _POOLS[conninfo] = pool
        return pool


def _fresh_conn(conninfo, connect_timeout):
    import psycopg
    conn = psycopg.connect(conninfo, connect_timeout=connect_timeout)
    apply_bounds(conn)
    return conn


def _pooled_conn_alive(conn):
    try:
        if conn.closed:
            return False
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        try:
            conn.rollback()
        except Exception:
            pass
        return True
    except Exception:
        return False


def _pool_checkout(conninfo, connect_timeout):
    """(conn, transient) — a live connection owned by the
    caller until _pool_checkin. Raises only on creation
    failure, exactly like a raw connect."""
    pool = _pool_for(conninfo)
    deadline = _time.monotonic() + _POOL_WAIT_S
    create = False
    with pool.cond:
        while True:
            while pool.idle:
                conn, ts = pool.idle.pop()
                if conn.closed:
                    pool.total -= 1
                    continue
                if (_time.monotonic() - ts > _POOL_IDLE_PING_S
                        and not _pooled_conn_alive(conn)):
                    pool.total -= 1
                    try:
                        conn.close()
                    except Exception:
                        pass
                    continue
                return conn, False
            if pool.total < _POOL_MAX_TOTAL:
                pool.total += 1
                create = True
                break
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                break
            pool.cond.wait(timeout=remaining)
    if not create:
        # Pool exhausted past the wait: a transient connection
        # the caller really closes — never fail the request.
        return _fresh_conn(conninfo, connect_timeout), True
    try:
        return _fresh_conn(conninfo, connect_timeout), False
    except Exception:
        with pool.cond:
            pool.total -= 1
            pool.cond.notify()
        raise


def _pool_checkin(conninfo, conn, transient):
    if transient:
        try:
            conn.close()
        except Exception:
            pass
        return
    pool = _pool_for(conninfo)
    discard = False
    try:
        if conn.closed:
            discard = True
        else:
            try:
                # psycopg's TransactionStatus.IDLE is 0; any
                # other state means a transaction is open —
                # roll it back so the next borrower starts
                # clean (post-commit this never fires).
                if int(conn.info.transaction_status) != 0:
                    conn.rollback()
            except Exception:
                discard = True
    except Exception:
        discard = True
    with pool.cond:
        if discard or len(pool.idle) >= _POOL_MAX_IDLE:
            pool.total -= 1
            try:
                conn.close()
            except Exception:
                pass
        else:
            pool.idle.append((conn, _time.monotonic()))
        pool.cond.notify()


class _PooledConnection:
    """Proxy for a pooled psycopg connection. Delegates every
    attribute to the real connection; close() and context-exit
    return it to the pool instead of closing it."""

    __slots__ = ("_conninfo", "_conn", "_transient", "_released")

    def __init__(self, conninfo, conn, transient):
        self._conninfo = conninfo
        self._conn = conn
        self._transient = transient
        self._released = False

    def _release(self):
        if self._released:
            return
        self._released = True
        _pool_checkin(self._conninfo, self._conn, self._transient)

    def close(self):
        self._release()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._release()
        return False

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __repr__(self):
        return "<_PooledConnection released=%r>" % (self._released,)


def pooled_connect(conninfo, connect_timeout=DB_CONNECT_TIMEOUT):
    """A connection to conninfo, reused from the process pool
    when one is warm. Drop-in for psycopg.connect(...) at the
    store wrappers (same arguments the wrappers already pass).
    """
    conn, transient = _pool_checkout(conninfo, connect_timeout)
    return _PooledConnection(conninfo, conn, transient)


def pool_snapshot():
    """{conninfo-tail: {idle, total}} for diagnostics. Keys are
    truncated so no credential material rides along."""
    out = {}
    with _POOLS_LOCK:
        items = list(_POOLS.items())
    for conninfo, pool in items:
        with pool.cond:
            out[str(conninfo)[-24:]] = {
                "idle": len(pool.idle), "total": pool.total}
    return out
