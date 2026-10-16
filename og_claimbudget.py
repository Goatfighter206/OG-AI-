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


def _budget_guard(fn, kind, blown=None):
    if kind == "claim":
        def _g(*a, **k):
            if not claim_budget_open():
                return blown
            return fn(*a, **k)
    elif kind == "defer":
        def _g(message, uid):
            return True if not claim_budget_open() \
                else fn(message, uid)
    elif kind == "blown":
        def _g(message, uid):
            if not claim_budget_open():
                raise _ClaimBudgetBlown()
            return fn(message, uid)
    else:  # garble text/info: the no-repair answer
        def _g(text, vocab, homophones=None):
            if not claim_budget_open():
                return str(text or ""), \
                    [] if kind == "text" else None
            return fn(text, vocab, homophones)
    _g._og_budget_wrapped = True
    return _g


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
                _g = _budget_guard(fn, kind)
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
                    _g = _budget_guard(
                        fn, "claim",
                        blown=(None, None) if _tuple_contract(fn)
                        else None)
                    _g.__module__ = modname
                    setattr(mod, attr, _g)
                    wrapped += 1
        except Exception as e:
            logger.warning(f"Claim-budget wrap skipped: {e}")
    if wrapped:
        logger.info(f"Claim budget: {wrapped} funnels wrapped.")
    return wrapped
