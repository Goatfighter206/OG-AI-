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
    where it turns a parked worker into a fast, honest
    failure: the stores all fail soft (file fallback /
    error copy) on a DB exception.

  DB_LOCK_TIMEOUT_MS — waiting to acquire a lock is
    bounded at 5s, half the statement budget, so a lock
    queue behind a stuck transaction fails the statement
    instead of silently eating the whole budget.

DB_OPTIONS is the libpq `options` string carrying the
two server-side bounds; psycopg passes it through in the
startup packet, so it applies from the first statement
on every connection, pooled endpoint included.

app.py's own two connect sites are NOT covered by the
import sweep — app.py is at its push ceiling and is
edited only under its own rule; see round-94/NOTES.md
for the exact remainder and the one-line fix each site
needs.
"""

DB_CONNECT_TIMEOUT = 5
DB_STATEMENT_TIMEOUT_MS = 10000
DB_LOCK_TIMEOUT_MS = 5000

DB_OPTIONS = (
    f"-c statement_timeout={DB_STATEMENT_TIMEOUT_MS} "
    f"-c lock_timeout={DB_LOCK_TIMEOUT_MS}"
)
