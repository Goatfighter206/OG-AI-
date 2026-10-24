"""The wall-clock claim budget (Round 321c).

The P0 (round-321 NOTES): a plain chat turn paid the FULL
claim-chain deferral cascade — seat._claim_exact ->
_defer_to_others -> _other_claim -> the next seat's
_claim_exact, recursively, each miss also paying
og_garble's difflib repair — 1,113 s for one plain turn,
so the stream died and the parked browser claim captured
a turn it should never have seen. The cure is a budget,
not a redesign of 200 seats' claim semantics: while a
turn's claim phase runs, a thread-local deadline stands
(og_fixqueue's detect wrapper and app.py's chat workers
open and close it); once it passes, the claim FUNNELS
answer "not mine" at once — _defer_to_others reports a
claim elsewhere (stepping aside is always legal), the
other-claim probes raise a private exception every real
call site already swallows, og_garble stops repairing —
the cascade unwinds in seconds and the turn falls
through to plain generation. The claim ENTRIES
(_claim_exact / _claim_job / _claim_<seat>) are cut
too: once the budget blows they answer None at once —
profiling the first cut showed the ~200 seats' local
parses still SUMMING to 83 s of detect (the exact
length of production's one successful chat that
hour), because each seat loads its store and parses
before deferring. A claim found BEFORE the deadline
is never cut, and the forget's chain walks watch ->
browser in the first seconds of the pass, which is
how the deep owner commands survive the budget. While the
budget is OPEN (no deadline, or not yet passed) every
wrapped function is an exact pass-through — fast turns
and non-chat callers (the suites call claims directly,
with no budget begun) see the old behavior.

DBPOOL batch-4 (2026-10-18): the entry guards alone did
not bound the phase in production — an entry in flight
at the deadline, and every seat wrapper's unguarded
per-turn delivery read, ran to completion at DB prices
(one plain turn's detect makes ~6,900 store loads; see
round-321/work/dbpool4/RECON.md). The phase now has a
mid-flight bound too: og_fixqueue's detect wrapper marks
the claim phase (begin/end_claim_phase) and the store
choke point (og_db.pooled_connect) raises
_ClaimBudgetBlown via raise_if_claim_phase_blown once
the phase is blown, so an in-flight entry unwinds at
its next store op. The phase scoping is what keeps the
check honest: outside the phase (history load/save,
metering — all on a thread whose budget has blown by
design) the predicate is False and nothing changes.

DBPOOL batch-5 (2026-10-18): the claim budget bounds the
claim DECISION, but production convicted the turn's
remaining DB traffic — the ~57 seat wrappers' unguarded
per-turn delivery reads (depth-0 loads the batch-4 choke
deliberately cannot touch), the post-detect seat-result
work, and the metering/save tail — of eating the whole
~271 s platform sever (see round-321/work/dbpool5/
DIAGNOSIS.md). So a second, wider clock now stands over
the whole turn: the TURN budget (45 s,
OG_TURN_BUDGET_SECONDS overrides), begun by app.py's
chat workers AFTER the memory lock is held (a queued
turn must not start pre-blown — the batch-5 convoy fix)
and ended after the history save. og_db's choke consults
it for EVERY store op on the turn thread, claim call or
not — except the legs that must always be able to run:
history_load, history_save and metering (a turn that
cannot save its own thread or meter itself has failed
worse than a slow one). A blown turn budget degrades
exactly the way a blown claim budget does: the seat
wrappers' own handlers step aside, detect falls through
to reduced-context generation, and the visitor still
gets a reply — the choke never invents a new failure
shape, it only makes the old one arrive on time. With
no turn budget begun (suites, non-chat callers, the
monitor loop) every predicate below is an exact
pass-through, like the claim budget itself. The turn
markers (turn_marker) ride this module's logger — the
one whose warnings provably reach the production log —
so a turn's leg timings are visible in the server log
instead of reconstructed from outside.

The value: 3 s (Round 321 Step 6 re-derivation). The
original 8 s was sized against "gunicorn --timeout 12"
— a truncated log reading; production runs --timeout
120 (Procfile / render.yaml), and the patience that
actually kills instances is Render's 5 s health check.
Since Round 321 Step 3 the claim work runs on worker
THREADS, never on an event loop, so the budget was
never a loop-safety device: it is a turn-quality bound.
It caps the pre-blow census spend (CPU on a 0.1-CPU
instance, summed store reads per turn) at 3 s; the
post-blow unwind is seconds-fast, and the deep owner
commands survive it — the forget chain walks watch ->
browser in the first seconds of the pass and completed
inside a 1 s test budget in the Step 3 repro. A bare
detect pass measured 409 s locally for a plain message
(forget's claim at 99 s), so without the budget every
parked-state turn was a worker-killer.
OG_CLAIM_BUDGET_SECONDS overrides.

Step 6 arity fix: the claim guard used to answer a bare
None for EVERY wrapped entry once the budget blew —
but 25 of the 67 _claim_* entries return a 2-TUPLE
(claim, garble_info), and their callers unpack it
(claim, info = _claim_<seat>(...)), so a blown budget
threw "cannot unpack non-iterable NoneType object" in
~23 seats on one production turn (2026-10-16 09:11) —
each seat's detect wrapper logged a misleading
"trigger check failed" and discarded preamble work.
The guard is now shape-aware at wrap time
(_tuple_contract): tuple entries step aside as
(None, None), single entries as None — each kind's
own "not mine", exactly like the garble guards below
always did. A unified (None, None) would be wrong:
single-return callers test truthiness, and a tuple is
truthy — it would read as a claim.
"""

import ast
import inspect
import logging
import os
import textwrap
import threading
import time

logger = logging.getLogger(__name__)

CLAIM_BUDGET_SECONDS = float(
    os.getenv("OG_CLAIM_BUDGET_SECONDS", "3") or 3)
_claim_budget_local = threading.local()


class _ClaimBudgetBlown(Exception):
    """Private: the claim budget closed mid-cascade."""


def begin_claim_budget(seconds: float = None) -> bool:
    """Open a claim budget on this thread; True when THIS
    call set the deadline (it owns the matching end call).
    Nested begins keep the earliest deadline, return False."""
    depth = getattr(_claim_budget_local, "depth", 0)
    _claim_budget_local.depth = depth + 1
    if depth > 0:
        return False
    span = CLAIM_BUDGET_SECONDS if seconds is None else seconds
    _claim_budget_local.deadline = time.monotonic() + max(0.0, span)
    _claim_budget_local.blew = False
    return True


def end_claim_budget(started: bool):
    """Close a begin_claim_budget: only the deadline's
    owner clears it, once the nesting has drained."""
    depth = getattr(_claim_budget_local, "depth", 0) - 1
    _claim_budget_local.depth = max(0, depth)
    if started and depth <= 0:
        _claim_budget_local.deadline = None


def begin_claim_phase() -> None:
    """DBPOOL batch-4: mark the start of the claim PHASE
    on this thread (og_fixqueue's detect wrapper — the
    phase's exact boundary). The budget spans a whole chat
    turn (begun before the memory lock, ended after the
    history save), but the phase is only the detect pass.
    The distinction is load-bearing: store choke points
    may raise the blown signal ONLY inside the phase —
    the turn-end history save runs with the budget blown
    by design and must save normally."""
    _claim_budget_local.phase_depth = \
        getattr(_claim_budget_local, "phase_depth", 0) + 1


def end_claim_phase() -> None:
    """Close a begin_claim_phase (depth-counted)."""
    depth = getattr(_claim_budget_local, "phase_depth", 0) - 1
    _claim_budget_local.phase_depth = max(0, depth)


def claim_phase_blown() -> bool:
    """True only while ALL of: a claim phase is active on
    this thread, a budget was begun, and its deadline has
    passed. With no phase or no budget (suites calling
    claims directly, non-chat callers) always False — an
    exact pass-through, like the budget itself."""
    if getattr(_claim_budget_local, "phase_depth", 0) <= 0:
        return False
    return not claim_budget_open()


def raise_if_claim_phase_blown() -> None:
    """The mid-flight choke check (DBPOOL batch-4): raise
    _ClaimBudgetBlown when this thread's claim phase has
    blown its budget, so a claim entry in flight at the
    deadline unwinds at its next store op instead of
    running its load+parse to completion. Real call sites
    already swallow this exception (the funnel probes
    raise it by design). The og_db choke applies it only
    to loads issued from inside a census-wrapped claim
    call (see in_claim_call below): the fleet's
    _load_store idiom would otherwise wrap the raise into
    a misleading "<seat> store unavailable" StoreError."""
    if claim_phase_blown():
        raise _ClaimBudgetBlown()


# --- the turn budget (DBPOOL batch 5) ---
# A thread-local 45 s wall clock over the WHOLE chat turn
# (see the docstring). app.py's chat workers own the
# begin/end; og_fixqueue's detect wrapper emits the detect
# marker; og_db's choke enforces it on every store op
# whose leg is not allowlisted. The leg is app.py's
# existing _turn_leg vocabulary, mirrored here because
# og_db cannot import app (the thread-local IS the
# channel, exactly like the claim phase marker above).

TURN_BUDGET_SECONDS = float(
    os.getenv("OG_TURN_BUDGET_SECONDS", "45") or 45)

# Legs a blown turn must still be able to run: loading the
# thread it is answering, saving that thread back, and
# metering the exchange. Everything else (claims,
# generation-leg seat work) steps aside once blown.
TURN_CHOKE_ALLOWLIST = frozenset(
    {"history_load", "history_save", "metering"})


class _TurnBudgetBlown(Exception):
    """Private: the turn budget closed mid-turn. Raised by
    the og_db choke; the fleet's store idiom wraps it into
    the same StoreError a dead database produces, and the
    seat wrappers step aside on it — no new failure shape."""


def begin_turn_budget(seconds: float = None) -> bool:
    """Open a turn budget on this thread; True when THIS
    call set the deadline (it owns the matching end call).
    Nested begins keep the earliest deadline, return
    False — the claim budget's exact contract."""
    depth = getattr(_claim_budget_local, "turn_depth", 0)
    _claim_budget_local.turn_depth = depth + 1
    if depth > 0:
        return False
    span = TURN_BUDGET_SECONDS if seconds is None else seconds
    _claim_budget_local.turn_deadline = \
        time.monotonic() + max(0.0, span)
    _claim_budget_local.turn_blew = False
    _claim_budget_local.turn_leg = None
    return True


def end_turn_budget(started: bool):
    """Close a begin_turn_budget: only the deadline's
    owner clears it (deadline AND leg), once the nesting
    has drained — a pooled thread must not carry a stale
    leg into its next turn."""
    depth = getattr(_claim_budget_local, "turn_depth", 0) - 1
    _claim_budget_local.turn_depth = max(0, depth)
    if started and depth <= 0:
        _claim_budget_local.turn_deadline = None
        _claim_budget_local.turn_leg = None


def turn_budget_active() -> bool:
    """True while a turn budget stands on this thread."""
    return getattr(_claim_budget_local, "turn_deadline", None) \
        is not None


def turn_budget_blown() -> bool:
    """True when a turn budget stands on this thread and
    its deadline has passed. The first call past the
    deadline latches and logs — the claim budget's
    warning contract, one line per turn."""
    deadline = getattr(_claim_budget_local, "turn_deadline", None)
    if deadline is None or time.monotonic() < deadline:
        return False
    if not getattr(_claim_budget_local, "turn_blew", False):
        _claim_budget_local.turn_blew = True
        logger.warning(
            "Turn budget blown: remaining store work steps "
            "aside, the turn falls through to its reply.")
    return True


def turn_deadline_left():
    """Seconds left on this thread's turn budget, or None
    when no turn budget stands. Negative once blown. The
    og_db pool wait caps itself by this so a turn never
    parks past its own deadline waiting for a connection."""
    deadline = getattr(_claim_budget_local, "turn_deadline", None)
    if deadline is None:
        return None
    return deadline - time.monotonic()


def set_turn_leg(leg) -> None:
    """Name the turn leg this thread is in (app.py's
    _turn_leg vocabulary: history_load / claims /
    generation / metering / history_save)."""
    _claim_budget_local.turn_leg = leg


def turn_leg():
    """This thread's current turn leg, or None."""
    return getattr(_claim_budget_local, "turn_leg", None)


def turn_choke_active() -> bool:
    """The og_db turn choke's predicate: a turn budget
    stands on this thread, it has blown, and the current
    leg is not one that must always run (history load /
    save, metering). No budget (suites, non-chat threads)
    -> False, an exact pass-through."""
    if not turn_budget_active():
        return False
    if turn_leg() in TURN_CHOKE_ALLOWLIST:
        return False
    return turn_budget_blown()


def turn_marker(name: str, ms: float, outcome: str = None):
    """One turn-timing line on this module's logger
    (DBPOOL batch 5 instrumentation): leg name, elapsed
    milliseconds, and — on the total marker — the turn's
    outcome. WARNING level because that is the level the
    production log provably keeps for this logger."""
    extra = f" outcome={outcome}" if outcome else ""
    logger.warning(f"Turn {name}: {ms:.0f} ms{extra}")


# --- in-claim-call depth (DBPOOL batch 4, choke scope) ---
# The og_db pooled_connect choke raises _ClaimBudgetBlown
# on a store load only when the load is issued from
# inside a census-wrapped claim call (depth > 0 here):
# loads from detect wrappers' auxiliary helpers, the
# generation phase, or direct suite calls keep their
# exact old behaviour — the fleet's _load_store idiom
# wraps ANY exception into a "<seat> store unavailable"
# StoreError, and a blown budget must never masquerade
# as a store outage (r321h B5 pins the quiet signature).
_call_local = threading.local()


def _call_depth() -> int:
    return getattr(_call_local, "depth", 0)


def in_claim_call() -> bool:
    return _call_depth() > 0


class _claim_call:
    def __enter__(self):
        _call_local.depth = _call_depth() + 1
        return self

    def __exit__(self, *exc):
        _call_local.depth = _call_depth() - 1
        return False


# --- Ticket D: the smarter door's guard hooks --------------
# The door itself (glance / regulars / coordination) lives in
# og_door; THIS module owns the thread-local pass state the
# census guards consult, because the guards are the one
# place every claim entry already passes through. A door
# pass has two phases: PASS 1 (shortlist) — wrapped claim
# entries whose module is not on the turn's shortlist answer
# "not mine" at once (the shape-aware blown value, exactly
# like a blown budget), nested refusals mark the pass
# probe-inconclusive so a running seat's deferral probes
# cannot be misread as "nobody else wants this" (precedence
# is preserved: the seat yields, the sweep decides); and
# the SWEEP — the same state with pass1 off, where every
# entry runs, its result memoized per pass so the cascade's
# repeated subtrees (measured 2026-10-23: ~89,000 funnel
# calls for one plain message, two-thirds of the phase in
# og_garble repairs inside recursive probes) collapse to
# one real run per (entry, message, uid). With no door
# pass armed (suites, direct calls) every hook below is an
# exact pass-through, like the budgets themselves.

_CENSUS_REGISTRY = set()


def door_registry():
    """(modname, attr) of every census-wrapped claim entry
    — og_door's glance harvest reads the fleet from here."""
    return sorted(_CENSUS_REGISTRY)


class _DoorPass:
    __slots__ = ("allowed", "uid", "pass1", "memo", "skips",
                 "nested_skips", "claimed")

    def __init__(self, allowed, uid):
        self.allowed = allowed
        self.uid = uid
        self.pass1 = True
        self.memo = {}
        self.skips = 0
        self.nested_skips = 0
        self.claimed = set()


_door_local = threading.local()


def door_state():
    """This thread's armed door pass, or None."""
    return getattr(_door_local, "state", None)


def door_begin(allowed, uid):
    """Arm a door pass on this thread; returns the state,
    or None when a pass is already armed (the caller then
    runs the plain chain inside the outer pass)."""
    if getattr(_door_local, "state", None) is not None:
        return None
    st = _DoorPass(set(allowed), uid)
    _door_local.state = st
    return st


def door_to_sweep(st) -> None:
    """Flip a pass to its sweep phase: every entry runs
    (memoized); no entry is refused again this pass. The
    memo is CLEARED at the flip: a pass-1 result may have
    been computed with its deferral probes refused (the
    seat yielded on partial information), so it must never
    be reused as a sweep result — the sweep recomputes
    under full information, then memoizes its own."""
    st.pass1 = False
    st.memo.clear()


def door_end(st) -> None:
    """Disarm the pass this thread armed (identity-checked:
    a nested coordinator never clears the outer pass)."""
    if getattr(_door_local, "state", None) is st:
        _door_local.state = None


def claim_budget_open() -> bool:
    """True while claims may proceed: no budget begun on
    this thread, or its deadline has not passed yet."""
    deadline = getattr(_claim_budget_local, "deadline", None)
    if deadline is None or time.monotonic() < deadline:
        return True
    if not getattr(_claim_budget_local, "blew", False):
        _claim_budget_local.blew = True
        logger.warning(
            "Claim budget blown: claims step aside, the "
            "turn falls through to plain generation.")
    return False


def _tuple_contract(fn) -> bool:
    """Round 321 Step 6: True when fn's OWN returns are
    exclusively tuple literals — the (claim, info)
    contract. AST over the function's source with nested
    scopes pruned (their returns are not fn's). Fail-soft
    False (the pre-Step-6 bare-None shape) when the
    source cannot be read or parsed. The tree census
    this mirrors: 67 _claim_* functions, 25 tuple-2,
    every tuple entry returning tuple literals only."""
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except Exception:
        return False
    func = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) \
                and node.name == getattr(fn, "__name__", None):
            func = node
            break
    if func is None:
        return False

    def _walk(n):
        yield n
        for ch in ast.iter_child_nodes(n):
            if isinstance(ch, (ast.FunctionDef,
                               ast.AsyncFunctionDef,
                               ast.Lambda, ast.ClassDef)):
                continue
            yield from _walk(ch)

    shapes = set()
    for n in _walk(func):
        if isinstance(n, ast.Return):
            if n.value is None:
                shapes.add("bare")
            elif isinstance(n.value, ast.Tuple):
                shapes.add("tuple")
            elif isinstance(n.value, ast.Constant) \
                    and n.value.value is None:
                shapes.add("none")
            else:
                shapes.add("value")
    return shapes == {"tuple"}


def _budget_guard(fn, kind, blown=None, modname=None,
                  attr=None, is_tuple=False):
    # Every wrapped call runs inside _claim_call() so the
    # og_db mid-flight choke can tell claim-machinery
    # loads from every other load (see in_claim_call).
    # Ticket D: when a door pass is armed on this thread
    # the claim entries additionally consult it (shortlist
    # refusal in pass 1, per-pass memo in both phases);
    # the funnel kinds convert a probe that touched a
    # refused entry into the fleet's own "claimed
    # elsewhere" signals, so precedence survives pass 1.
    if kind == "claim":
        def _g(*a, **k):
            if not claim_budget_open():
                return blown
            st = door_state()
            if st is None:
                with _claim_call():
                    return fn(*a, **k)
            if st.pass1 and modname not in st.allowed:
                if _call_depth() > 0:
                    st.nested_skips += 1
                else:
                    st.skips += 1
                return blown
            key = None
            try:
                key = (modname, attr, a,
                       tuple(sorted(k.items())))
                hash(key)
            except Exception:
                key = None
            if key is not None and key in st.memo:
                return st.memo[key]
            with _claim_call():
                r = fn(*a, **k)
            if key is not None:
                st.memo[key] = r
            try:
                if (r[0] if is_tuple else r):
                    st.claimed.add(modname)
            except Exception:
                pass
            return r
    elif kind == "defer":
        def _g(message, uid):
            if not claim_budget_open():
                return True
            st = door_state()
            if st is not None and st.pass1:
                before = st.nested_skips
                try:
                    with _claim_call():
                        r = fn(message, uid)
                except _ClaimBudgetBlown:
                    # A nested probe answered "claimed
                    # elsewhere" because the door refused
                    # one of its seats: that IS a defer.
                    return True
                if st.nested_skips > before:
                    # A probe was refused mid-check: the
                    # honest answer is "someone else may
                    # want this" — defer; the sweep decides.
                    return True
                return r
            with _claim_call():
                return fn(message, uid)
    elif kind == "blown":
        def _g(message, uid):
            if not claim_budget_open():
                raise _ClaimBudgetBlown()
            st = door_state()
            if st is not None and st.pass1:
                before = st.nested_skips
                with _claim_call():
                    r = fn(message, uid)
                if r is None and st.nested_skips > before:
                    # The probe could not see every seat:
                    # answer exactly like a blown budget —
                    # "claimed elsewhere", which every real
                    # call site already swallows.
                    raise _ClaimBudgetBlown()
                return r
            with _claim_call():
                return fn(message, uid)
    else:  # garble text/info: the no-repair answer
        def _g(text, vocab, homophones=None):
            if not claim_budget_open():
                return str(text or ""), \
                    [] if kind == "text" else None
            with _claim_call():
                return fn(text, vocab, homophones)
    _g._og_budget_wrapped = True
    return _g


# Maker-seam fix (2026-10-23, og-maker-seam-ticket-
# 2026-10-22): the maker seats' claim ENTRIES join the
# og_browser carve-out below — they are never wrapped.
# Each is the A4c shape exactly (AST-audited 2026-10-23):
# a deterministic parse over the visitor's own words
# plus bounded reads of the visitor's OWN records
# (pending state, own games/artifacts) — no deferral
# funnel, no network, no LLM inside the entry, so the
# cascade this budget cuts cannot rebuild through one,
# and its cost is its own milliseconds whenever it runs.
# These seats install LAST in app.py's build chain
# (songs 20th, artifacts 21st, mystery 22nd of 23), so
# in production — where the pass pays Postgres store
# loads on a 0.1-CPU box and cannot reach them inside
# 3 s — the guard answered "not mine" for them on
# effectively every turn, on both chat paths, while
# the seat gates (one seat, no chain, no budget begun
# by a worker) could not see it: "let's play a murder
# mystery" and "make me a budget page…" got persona
# improvisation, no claim, no record (MM landing live
# proof, 2026-10-23). og_userwatch rides the same
# pattern and is named now so Ticket W claims the day
# it lands; the census no-ops on absent modules.
_MAKER_CLAIM_EXEMPT = frozenset({
    ("og_mystery", "_claim_job"),
    ("og_artifacts", "_claim_artifact"),
    ("og_songs", "_claim_song"),
    ("og_books", "_claim_book"),
    ("og_podcast", "_claim_podcast"),
    ("og_video", "_claim_job"),
    ("og_userwatch", "_claim_job"),
})


def _wrap_claim_funnels() -> int:
    """Install-time census (run LAST by og_fixqueue's
    install, when every seat module is imported): wrap
    each loaded og_* module's claim funnels so they
    answer at once once the budget closes. Fail-soft
    per module, idempotent; returns the wrap count."""
    wrapped = 0
    import sys as _sys
    for modname, mod in list(_sys.modules.items()):
        if mod is None or not modname.startswith("og_"):
            continue
        if modname == __name__ or modname.endswith("_testing"):
            continue
        try:
            targets = (("repair", "text"), ("repair_info", "info")) \
                if modname == "og_garble" else (
                    ("_defer_to_others", "defer"),
                    ("_other_claim", "blown"),
                    ("_seat_other_claim", "blown"),
                    ("_probe_claim", "blown"))
            for attr, kind in targets:
                fn = getattr(mod, attr, None)
                if fn is None or not callable(fn):
                    continue
                if getattr(fn, "_og_budget_wrapped", False):
                    continue
                if getattr(fn, "__module__", None) != modname:
                    continue
                _g = _budget_guard(fn, kind, modname=modname,
                                   attr=attr)
                _g.__module__ = modname
                setattr(mod, attr, _g)
                wrapped += 1
            if modname != "og_garble":
                # The seats' claim ENTRIES (_claim_exact,
                # _claim_job, _claim_<seat>): once the
                # budget blows they answer "not mine"
                # (None) at once — the local parses of
                # ~200 seats otherwise SUM to minutes
                # even with the deferral cascade cut.
                for attr in dir(mod):
                    if not attr.startswith("_claim_"):
                        continue
                    if (modname == "og_browser"
                            and attr == "_claim_job"):
                        # Ticket A4c carve-out: the browser's
                        # claim ENTRY is a pure parse over the
                        # visitor's own words plus reads of the
                        # in-process usage-store file — no
                        # og_db, no network, bounded in
                        # milliseconds on its own. Cutting it
                        # when the SHARED clock has blown
                        # silently kills a deterministic user
                        # command (2026-10-19 evening: every
                        # "Open Facebook." narrated, none
                        # started — see window-rebuild
                        # NOTES.md # Ticket A4c) and defeats
                        # this budget's own design intent
                        # (the deep commands survive; the
                        # forget chain walks watch -> browser).
                        # Its expensive half (_do_start's
                        # Steel create) already runs in a
                        # background thread, and the deferral
                        # funnels + every other seat's entry
                        # stay guarded, so the cascade this
                        # budget exists to cut cannot rebuild
                        # through this one entry.
                        continue
                    if (modname, attr) in _MAKER_CLAIM_EXEMPT:
                        # Maker-seam carve-out (see
                        # _MAKER_CLAIM_EXEMPT above): the
                        # maker entries are A4c-shaped
                        # bounded parses; cutting them
                        # silently killed every "make me…"
                        # claim in production. Their
                        # deferral funnels stay guarded.
                        continue
                    fn = getattr(mod, attr, None)
                    if fn is None or not callable(fn):
                        continue
                    if getattr(fn, "_og_budget_wrapped", False):
                        continue
                    if getattr(fn, "__module__", None) != modname:
                        continue
                    # Step 6: a tuple-contract entry steps
                    # aside as (None, None); a single entry
                    # as None — the shape-aware "not mine".
                    _is_tuple = _tuple_contract(fn)
                    _g = _budget_guard(
                        fn, "claim",
                        blown=(None, None) if _is_tuple
                        else None,
                        modname=modname, attr=attr,
                        is_tuple=_is_tuple)
                    _g.__module__ = modname
                    setattr(mod, attr, _g)
                    _CENSUS_REGISTRY.add((modname, attr))
                    wrapped += 1
        except Exception as e:
            logger.warning(f"Claim-budget wrap skipped: {e}")
    if wrapped:
        logger.info(f"Claim budget: {wrapped} funnels wrapped.")
    return wrapped
