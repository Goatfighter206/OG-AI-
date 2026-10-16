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
