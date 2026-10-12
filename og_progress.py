"""Round 70 (addition): the Progress seat's ONE aggregate.

Brent (2026-10-11): a fifth seat, "Progress", shows
everything being worked on for the signed-in user, live —
in-flight generations, browser sessions, watch checks,
prepared fixes, the nightly auto-update state (owner
only), and the asks being processed.

THE % RULE (Brent, 2026-10-11 16:37, verbatim law here):
"Only tasks that are being worked on will have the live
%." A row's pct is a number ONLY when BOTH hold:
  1. the job is in an actively-working state right now, AND
  2. the job's own store carries real progress counts.
Queued / waiting / done / failed rows carry pct = None —
state words only, never a decorative number, never a
timer fake. Per kind, from the audited stores:
  video   scenes_done / scene_count while drawing /
          voicing / stitching (queued -> no %)
  book    chapters_done / chapters_total while generating
  podcast tts_calls / tts_est while generating (est > 0)
  song, motion, 3D model, sfx, designer — single-call
          jobs: NO progress counters exist in their
          stores, so state only, even while generating.
Browser sessions, watches, fixes, and parked approvals
have no progress data by nature: state only.

Rows for systems with no live job simply don't appear.
Done / failed rows linger LINGER_S after their last
update ("the item lands as done"), then drop off.

ALL QUERIES honesty note: there is NO server registry of
in-flight chat asks — a chat ask is answered over its own
open request in seconds and nothing tracks it. The
"queries" section therefore lists what the server DOES
hold as an in-flight ask: a parked approval waiting on
the user (the ask is mid-flight, parked on them). The
seat copy claims nothing more.

The auto-update block appears ONLY for the owner
(session email == OG_OWNER_EMAIL — the tierfix3 rule,
via og_tiers._session_email_for). Registered from
og_library like every late module; app.py untouched.
"""

import logging
import os
import time
from typing import Dict, List, Optional

from fastapi import Request

logger = logging.getLogger(__name__)

UID_COOKIE = "ogai_uid"
LINGER_S = 2 * 3600.0

_STATE_LABELS = {
    "queued": "Queued",
    "drawing": "Drawing scenes",
    "voicing": "Voicing",
    "stitching": "Stitching it together",
    "generating": "Working",
    "await_approval": "Waiting on your approval",
    "done": "Done",
    "failed": "Didn't make it",
}


def _pct(done, total) -> Optional[float]:
    try:
        d, t = float(done or 0), float(total or 0)
    except (TypeError, ValueError):
        return None
    if t <= 0:
        return None
    return round(100.0 * d / t, 1)


def _gen_rows(uid: str) -> List[Dict]:
    """One guarded pass per generator store. kind ->
    (module, icon, working states, pct extractor)."""
    rows: List[Dict] = []

    def add(module_name, icon, working_states, pct_of,
            view_fn="_job_view"):
        try:
            mod = __import__(module_name)
            for rec in mod.list_jobs_for_owner(uid):
                view = getattr(mod, view_fn)(rec)
                if not view:
                    continue
                state = view.get("state") or view.get(
                    "status") or ""
                updated = float(rec.get("updated", 0)
                                or rec.get("created", 0) or 0)
                working = state in working_states
                if state in ("done", "failed") and updated \
                        and time.time() - updated > LINGER_S:
                    continue
                # THE % RULE: number only while actively
                # working AND the store carries counts.
                pct = pct_of(view) if working else None
                rows.append({
                    "section": "making", "icon": icon,
                    "title": view.get("title")
                             or "Untitled",
                    "state": state,
                    "state_label": _STATE_LABELS.get(
                        state, state),
                    "working": working,
                    "pct": pct,
                    "sub": (view.get("detail") or "")[:140],
                    "ts": float(rec.get("created", 0) or 0),
                })
        except Exception as e:
            logger.warning(
                f"Progress section {module_name} failed: {e}")

    add("og_video", "🎬", ("drawing", "voicing", "stitching"),
        lambda v: _pct(v.get("scenes_done"),
                       v.get("scenes_total")))
    add("og_books", "📖", ("generating",),
        lambda v: _pct(v.get("chapters_done"),
                       v.get("chapters_total")),
        view_fn="_book_view")
    add("og_podcast", "🎙️", ("generating",),
        lambda v: _pct(v.get("tts_calls"), v.get("tts_est")))
    # Single-call generators: no counters exist — pct is
    # always None by construction (the extractor is None).
    add("og_songs", "🎵", ("generating",), lambda v: None)
    add("og_motion", "🎞️", ("generating",), lambda v: None)
    add("og_models3d", "🧊", ("generating",), lambda v: None)
    add("og_voice_lab", "🔊", ("generating",), lambda v: None)
    add("og_designer", "🎨", ("generating",), lambda v: None)

    order = {"working": 0, "queued": 1, "done": 2,
             "failed": 3}
    rows.sort(key=lambda r: (
        order.get("working" if r["working"] else r["state"],
                  4),
        -r["ts"]))
    return rows


def _browser_rows(uid: str) -> List[Dict]:
    rows: List[Dict] = []
    try:
        import og_browser
        rec = og_browser._get_session(uid) if uid else None
        if rec:
            if rec.get("control") == "visitor":
                state, label = "you", "You're driving"
            elif not rec.get("frame_ready"):
                state, label = "loading", "Loading the page"
            else:
                state, label = "browsing", "Browsing"
            host = ""
            url = rec.get("last_url", "")
            if url:
                try:
                    from urllib.parse import urlparse
                    host = urlparse(url).hostname or ""
                except Exception:
                    host = ""
            rows.append({
                "section": "browser", "icon": "🌐",
                "title": rec.get("last_title") or host
                         or "Browser session",
                "state": state, "state_label": label,
                "working": state in ("loading", "browsing"),
                "pct": None,
                "sub": host,
                "ts": float(rec.get("last_action", 0) or 0),
            })
        else:
            starting = og_browser._get_starting(uid) \
                if uid else None
            if starting:
                rows.append({
                    "section": "browser", "icon": "🌐",
                    "title": "Browser session",
                    "state": "starting",
                    "state_label": "Opening the browser",
                    "working": True, "pct": None,
                    "sub": "",
                    "ts": float(starting.get("ts", 0) or 0),
                })
    except Exception as e:
        logger.warning(f"Progress browser section failed: {e}")
    return rows


def _query_rows(uid: str) -> List[Dict]:
    """The asks mid-flight that the server actually holds:
    a parked approval (it waits on the user, not on OG)."""
    rows: List[Dict] = []
    try:
        import og_browser
        pend = og_browser._get_pending(uid) if uid else None
        if pend:
            desc = pend.get("desc") or "a browser action"
            site = pend.get("site") or ""
            rows.append({
                "section": "queries", "icon": "✋",
                "title": "OG is waiting on your approval",
                "state": "waiting",
                "state_label": "Waiting on you",
                "working": False, "pct": None,
                "sub": (desc + (f" — {site}" if site else "")),
                "ts": float(pend.get("created", 0) or 0),
            })
    except Exception as e:
        logger.warning(f"Progress queries section failed: {e}")
    return rows


_WATCH_LABELS = {
    "active": "Watching",
    "waiting_login": "Waiting on your sign-in",
    "paused_checkpoint": "Paused at a security check",
}


def _watch_rows(uid: str) -> List[Dict]:
    rows: List[Dict] = []
    try:
        import og_watch
        for w in og_watch._watches(uid):
            if not isinstance(w, dict):
                continue
            status = w.get("status") or "active"
            if status not in _WATCH_LABELS:
                continue
            rows.append({
                "section": "watching", "icon": "👀",
                "title": w.get("item") or "Watch",
                "state": status,
                "state_label": _WATCH_LABELS[status],
                "working": False, "pct": None,
                "sub": w.get("place") or "",
                "ts": float(w.get("last_check", 0) or 0),
            })
    except Exception as e:
        logger.warning(f"Progress watch section failed: {e}")
    try:
        import og_monitor
        for w in og_monitor._watches(uid):
            if not isinstance(w, dict):
                continue
            if w.get("status") == "fired":
                continue
            rows.append({
                "section": "watching", "icon": "💲",
                "title": (w.get("symbol") or "Price watch"),
                "state": "active",
                "state_label": "Watching the price",
                "working": False, "pct": None,
                "sub": w.get("terms") or "",
                "ts": float(w.get("created", 0) or 0),
            })
    except Exception as e:
        logger.warning(f"Progress monitor section failed: {e}")
    return rows


def _fix_rows(uid: str) -> List[Dict]:
    rows: List[Dict] = []
    try:
        import og_fixqueue
        data = og_fixqueue.list_proposals(uid)
        for p in (data or {}).get("proposals") or []:
            if not isinstance(p, dict) \
                    or p.get("status") != "open":
                continue
            rows.append({
                "section": "fixes", "icon": "🔧",
                "title": p.get("title") or "Prepared fix",
                "state": "waiting",
                "state_label": "Prepared — waiting on your "
                               "approval",
                "working": False, "pct": None,
                "sub": "",
                "ts": float(p.get("created", 0) or 0),
            })
    except Exception as e:
        logger.warning(f"Progress fixes section failed: {e}")
    return rows


def _auto_update_block(uid: str,
                       request: Request) -> Optional[Dict]:
    """Owner only — the tierfix3 rule: the caller's
    server-issued session email must match OG_OWNER_EMAIL."""
    try:
        import og_tiers
        owner = (os.environ.get("OG_OWNER_EMAIL")
                 or "").strip().lower()
        if not owner:
            return None
        email = og_tiers._session_email_for(
            request.cookies, uid)
        if not email or email.strip().lower() != owner:
            return None
        import og_autoupdate
        st = og_autoupdate.state_view()
        parts: List[str] = []
        if st.get("disabled"):
            parts.append("Turned off — it disabled itself "
                         "after repeated rollbacks; re-enable "
                         "from the system page.")
        elif st.get("enabled"):
            parts.append("On — runs nightly at 11 PM.")
        else:
            parts.append("Off — the switch isn't set yet.")
        if st.get("pending_verify"):
            titles = (st["pending_verify"].get("items")
                      or [])
            parts.append("Verifying last night's fixes: "
                         + ", ".join(titles) + ".")
        last = st.get("last_run")
        if isinstance(last, dict) and last.get("result"):
            when = ""
            if last.get("ts"):
                when = time.strftime(
                    "%b %d, %I:%M %p",
                    time.localtime(float(last["ts"])))
            applied = last.get("applied") or []
            if last["result"] == "passed":
                parts.append(
                    f"Last run {when}: passed"
                    + (f" — {', '.join(applied)}"
                       if applied else "") + ".")
            elif last["result"] == "rolled_back":
                parts.append(
                    f"Last run {when}: a fix failed its "
                    "checks and was rolled back.")
            else:
                parts.append(
                    f"Last run {when}: {last['result']}.")
        return {"title": "Nightly auto-update (11 PM)",
                "sub": " ".join(parts)}
    except Exception as e:
        logger.warning(f"Progress auto-update failed: {e}")
        return None


def collect_progress(uid: str, request: Request) -> Dict:
    if not uid:
        return {"rows": [], "auto_update": None}
    rows: List[Dict] = []
    rows.extend(_gen_rows(uid))
    rows.extend(_browser_rows(uid))
    rows.extend(_query_rows(uid))
    rows.extend(_watch_rows(uid))
    rows.extend(_fix_rows(uid))
    return {"rows": rows,
            "auto_update": _auto_update_block(uid, request)}


def register_progress_routes(app):
    @app.get("/progress")
    async def progress_list(request: Request):
        uid = request.cookies.get(UID_COOKIE) or ""
        data = collect_progress(uid, request)
        return {"ok": True, "rows": data["rows"],
                "auto_update": data["auto_update"]}
