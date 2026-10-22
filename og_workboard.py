"""Ticket N2 — the menu work board's read (Brent, 2026-10-21:
"make the menu what program is what all he is working on —
what's all in his inquiry or job that you gave him").

GET /workboard — the signed-in caller's OWN work state,
grouped for the menu: what OG is working on right now,
what is waiting on the caller's approval, what is queued
or standing, and what just finished.

THE HONESTY LAW (same as og_activity / og_progress): this
module composes NOTHING of its own. Every row comes from
og_progress.collect_progress — the one existing composer
that reads each owning store live (generators, browser,
queries, watches, price tracks, the fix queue's open
proposals, crew jobs, projects) with each section
fail-soft on its own. This module only GROUPS those rows
and trims them to what a menu row needs. No row is
invented, no state is copied into a store here, and an
empty board is an honest empty board.

TICKET S (Brent, 2026-10-22: "All these fixes should not
be in there — I don't want users seeing all that. That
only what he working for the user"): the board shows ONLY
what OG is working on FOR the signed-in user. OG's
INTERNAL construction work — the self-fix queue's
proposals (section "fixes") and the nightly auto-update
block — never enters this payload: the split happens
HERE, at the source, not in the client (the rows are
absent from the response, not hidden by the glass). The
filter is an ALLOWLIST of the user-work sections, so any
future internal section fails closed instead of leaking
by default. og_progress itself is untouched: the same
unfiltered read still feeds the owner-side surfaces
(the Progress seat / Manager crew dashboard), where the
internal line legitimately lives.

Signed out: 200 with signed_in False and empty groups
(the /activity/status precedent — the menu must render
truthfully for a signed-out visitor too, and there is no
signed-out work to show).
"""
import logging
import time
from typing import Dict, List, Optional

from fastapi import Request

logger = logging.getLogger(__name__)

_CAPS = {"working": 8, "waiting": 8, "queue": 12,
         "finished": 5}
_FINISHED_STATES = ("done", "failed")

# Ticket S: the ONLY sections that may ship to a user
# client — work OG is doing FOR the signed-in user, each
# read from that user's own store key: their generator
# jobs ("making"), their browser session ("browser"),
# their parked approvals ("queries"), their watches and
# price tracks ("watching"), the crew jobs they
# dispatched ("crew"), their projects ("projects").
# Everything else the shared composer returns — today
# the self-fix queue ("fixes"); tomorrow anything new —
# is internal and is dropped here, at the source.
_USER_SECTIONS = frozenset({
    "making", "browser", "queries", "watching", "crew",
    "projects",
})


def _route_uid(request) -> str:
    """The caller's ONE identity — og_activity's resolver
    (the live session's account uid, else the presented
    cookie), imported lazily so this module rides the
    exact same identity the activity bubble reads."""
    try:
        import og_activity
        return og_activity._route_uid(request)
    except Exception:
        try:
            return request.cookies.get("ogai_uid", "") or ""
        except Exception:
            return ""


def _trim(row: Dict) -> Dict:
    return {
        "icon": str(row.get("icon") or ""),
        "title": str(row.get("title") or "Untitled"),
        "state": str(row.get("state") or ""),
        "state_label": str(row.get("state_label")
                           or row.get("state") or ""),
        "pct": row.get("pct"),
        "sub": str(row.get("sub") or "")[:140],
        "ts": float(row.get("ts") or 0),
    }


def board_for(uid: str, request) -> Dict:
    """{signed_in, groups:{working, waiting, queue,
    finished}, counts, owner_note} for ONE account.
    Fail-soft: a composer failure reads as an empty
    board, never an error page in the menu."""
    empty = {"working": [], "waiting": [], "queue": [],
             "finished": []}
    if not uid:
        return {"signed_in": False, "groups": empty,
                "counts": {k: 0 for k in empty},
                "owner_note": None}
    rows: List[Dict] = []
    try:
        import og_progress
        data = og_progress.collect_progress(uid, request)
        # Ticket S split at the source: only the caller's
        # own user-work sections continue; internal rows
        # (the self-fix queue today) are dropped before
        # grouping, and the composer's auto_update block —
        # OG's nightly self-upkeep — is never read into
        # this payload at all (owner_note stays None; the
        # block still ships on the owner-side surfaces
        # that read og_progress directly).
        rows = [r for r in (data.get("rows") or [])
                if isinstance(r, dict)
                and r.get("section") in _USER_SECTIONS]
    except Exception as e:
        logger.warning(f"Workboard compose failed: {e}")
        rows = []
    groups: Dict[str, List[Dict]] = {k: [] for k in _CAPS}
    for row in rows:
        state = str(row.get("state") or "")
        if state == "waiting":
            groups["waiting"].append(_trim(row))
        elif row.get("working"):
            groups["working"].append(_trim(row))
        elif state in _FINISHED_STATES:
            groups["finished"].append(_trim(row))
        else:
            groups["queue"].append(_trim(row))
    # Waiting reads oldest-first (it has waited longest);
    # everything else newest-first.
    groups["waiting"].sort(key=lambda r: r["ts"])
    for key in ("working", "queue", "finished"):
        groups[key].sort(key=lambda r: -r["ts"])
    counts = {k: len(v) for k, v in groups.items()}
    capped = {k: v[:_CAPS[k]] for k, v in groups.items()}
    return {"signed_in": True, "groups": capped,
            "counts": counts, "owner_note": None}


def register_workboard_routes(app):
    """GET /workboard — the caller's own grouped work
    state for the menu board."""

    @app.get("/workboard")
    # Round 321 (Step 3.1) discipline: def, not async
    # def — the body is sync store reads; FastAPI runs
    # def handlers in its threadpool.
    def workboard_route(request: Request):
        uid = _route_uid(request)
        return {"ok": True, **board_for(uid, request)}
