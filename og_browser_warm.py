"""og_browser_warm — Round 72 warmth rotation for og_browser.

Split out of og_browser.py purely for size (the push
wrapper's byte cap; the og_browser_pending / starting /
claim splits are the same precedent). Holds NO state and
imports nothing from og_browser — og_browser passes its
own module object as the deps handle (the functions here
call back through its underscore names), so there is no
import cycle.

ALSO HERE: the active-window meter (Brent's condition on
keeping the browser warm, 2026-10-11: warm standby must
NOT burn the visitor's minutes). Tier-mode minutes accrue
ONLY inside active-use windows — OG driving/loading,
an approval parked and waiting, and the visitor's own
takeover. Windows bank into the record's accrued_s; an
open window rides win_start. The free taste keeps its
hard wall clock everywhere (budget math below is the
pre-R72 formula, untouched).

WHAT IT DOES. Steel kills a browser session 15 minutes
after it is created no matter what the visitor's plan
allows; the OG session RECORD (and the remaining budget)
lives on in og_browser's store. Before this round, a
visitor returning at minute 16 drove a corpse and got the
honest drive failure — a cold-start glitch. Now, at an ask
boundary, rotate_if_stale swaps a stale Steel session for
a fresh one on the SAME vault profile (Round 29's persisted
profileId, so logins survive), re-opens the page the
visitor was on, best effort, and releases the old session.

The rules that keep this honest:

* NEVER under the visitor's hands: while control is
  "visitor" (take control) the record is returned
  untouched. Rotation waits for hand-back / the next OG
  ask. If Steel's cap lands mid-takeover anyway, the
  visitor's next input meets og_browser's existing
  session-over flow, and the next OG ask rotates onto the
  same profile — logins intact.
* The free 10-minute taste never rotates: its own wall
  cap is shorter than Steel's, so it can never outlive
  its Steel session.
* Stale = the Steel session's age is within 120s of the
  cap, OR Steel reports it gone/released (a best-effort
  GET; a read failure never forces a rotation).
* A failed create keeps the OLD record — the drive then
  tells the truth exactly as it did before this round.
* The re-open is metered (it is OG loading a page for the
  visitor) and goes through the same URL gate as any
  navigation; watch sessions never pass through here at
  all — they spin up per check and shut down (Round 29).
"""

import math
import time
from typing import Optional

_DEAD_STATES = ("released", "dead", "expired", "failed", "closed")
_ROTATE_MARGIN_S = 120


# --- the active-window meter ---------------------------------

def session_elapsed_min(rec: dict) -> float:
    """Wall-clock minutes since the session started (the
    taste's clock; tier mode no longer meters by it)."""
    return max(0.0, (time.time() - float(rec.get("started", 0))) / 60.0)


def active_elapsed_s(rec: dict) -> float:
    """Banked active seconds plus the open window, if any.
    Pre-R72 records carry neither field and read as 0."""
    total = float(rec.get("accrued_s") or 0)
    ws = rec.get("win_start")
    if isinstance(ws, (int, float)):
        total += max(0.0, time.time() - float(ws))
    return total


def minutes_left(rec: dict) -> int:
    if rec.get("mode") == "taste":
        left = float(rec.get("budget", 0)) - session_elapsed_min(rec)
    else:
        left = float(rec.get("budget", 0)) - active_elapsed_s(rec) / 60.0
    return max(0, int(math.ceil(left)))


def meter_open(uid: str, D) -> None:
    """Open an active-use window on uid's session (no-op
    when one is already open or there is no session)."""
    rec = D._get_session(uid)
    if rec and not isinstance(rec.get("win_start"), (int, float)):
        rec = dict(rec)
        rec["win_start"] = time.time()
        D._set_session(uid, rec)


def carry_meter(cur, rec: dict) -> dict:
    """Merge the stored record's meter state into an incoming
    whole-record write. Drive-path writers (the snapshot
    merge, the frame pump) carry copies that predate the
    meter, and og_browser writes sessions whole — without
    this carry, a stale write would unspend banked seconds
    and silently close an open window. Rules: banked
    seconds never decrease; an open stored window survives
    a write whose own window is closed UNLESS the write
    banked (its accrued grew — that write IS the bank
    closing it). A different Steel session id is a new
    session: no carry."""
    if not isinstance(cur, dict):
        return rec
    if cur.get("steel_id") != rec.get("steel_id"):
        return rec
    try:
        c_acc = float(cur.get("accrued_s") or 0)
        r_acc = float(rec.get("accrued_s") or 0)
        c_win = cur.get("win_start")
        if r_acc < c_acc:
            rec = dict(rec)
            rec["accrued_s"] = c_acc
            if isinstance(c_win, (int, float)) \
                    and rec.get("win_start") is None:
                rec["win_start"] = c_win
        elif r_acc == c_acc and isinstance(c_win, (int, float)) \
                and rec.get("win_start") is None:
            rec = dict(rec)
            rec["win_start"] = c_win
    except Exception:
        pass
    return rec


def meter_bank(uid: str, cap, D) -> None:
    """Bank the open window into accrued_s. cap (an epoch)
    bounds the banked end — a lapsed approval accrues only
    to its expiry, never past it."""
    rec = D._get_session(uid)
    if not rec:
        return
    ws = rec.get("win_start")
    if isinstance(ws, (int, float)):
        end = time.time()
        if cap is not None:
            end = min(end, float(cap))
        rec = dict(rec)
        rec["accrued_s"] = float(rec.get("accrued_s") or 0) \
            + max(0.0, end - float(ws))
        rec["win_start"] = None
        D._set_session(uid, rec)


def steel_dead(sess) -> bool:
    """True when a Steel GET /sessions/<id> answer says the
    session is gone (or there is no usable answer)."""
    if not isinstance(sess, dict) or not sess:
        return True
    return str(sess.get("status") or "live").lower() in _DEAD_STATES


def rotate_if_stale(uid: str, D) -> Optional[dict]:
    """Swap uid's stale Steel session for a fresh one on the
    same profile; returns the live record (rotated or not),
    or None when there is no session. D is the og_browser
    module itself — every primitive is read off it by its
    underscore name (_get_session, _steel_create_session,
    _SteelError, _STEEL_SESSION_CAP_MS, …)."""
    rec = D._get_session(uid)
    if not rec:
        return None
    if rec.get("control") == "visitor" \
            or rec.get("mode") == "taste":
        return rec
    born = float(rec.get("steel_born") or rec.get("started") or 0)
    stale = (time.time() - born) \
        >= (D._STEEL_SESSION_CAP_MS // 1000) - _ROTATE_MARGIN_S
    if not stale:
        try:
            stale = steel_dead(D._steel_get_session(rec.get("steel_id", "")))
        except Exception:
            stale = False
    if not stale:
        return rec
    pid = rec.get("profile_id") or None
    get_hook = D._hook("get")
    try:
        sess = D._steel_create_session(
            max(1, D._session_minutes_left(rec)), profile_id=pid,
            persist=get_hook is not None or bool(pid))
    except D._SteelError as e:
        D.logger.warning(f"Warm rotation create failed: {e}")
        return rec
    old_sid = rec.get("steel_id")
    rec = dict(rec)
    rec["steel_id"] = sess["id"]
    rec["ws"] = sess["websocketUrl"]
    rec["steel_born"] = time.time()
    rec["frame_ready"] = False
    rec["focus"] = None
    new_pid = str(sess.get("profileId") or "")
    if new_pid:
        rec["profile_id"] = new_pid
        note_hook = D._hook("note")
        if note_hook is not None:
            try:
                note_hook(uid, new_pid)
            except Exception:
                pass
    D._set_session(uid, rec)
    if old_sid and old_sid != sess["id"]:
        try:
            D._steel_release(old_sid)
        except Exception:
            pass
    # Re-open the page they were on, best effort, inside a
    # metered window (it is OG loading a page for them).
    url = str(rec.get("last_url") or "")
    if url and not url.startswith(("about:", "chrome:", "data:")):
        ok, _reason = D._url_gate(url)
        if ok:
            D._meter_open(uid)
            try:
                D._drive(rec, {"do": "navigate", "url": url})
            except Exception:
                pass
            D._meter_bank(uid)
    return D._get_session(uid) or rec


# --- the approval-pending trio --------------------------------
# Moved verbatim from og_browser (Round 72 code motion,
# the byte-cap precedent): these are the meter-window
# endpoints — parking an approval opens its window,
# resolving or lapsing it banks it. og_browser keeps
# 1-line delegations, so its attribute surface is
# unchanged.

def set_pending(uid: str, state: dict, D) -> bool:
    """Park a pending proposal / gated action. Returns False when
    the park is REFUSED (Round 36: an action approval with no
    human description — no desc and no text it will post)."""
    state = dict(state)
    if state.get("kind") == "action":
        desc = str(state.get("desc") or "").strip()
        text = str(state.get("post_text")
                   or state.get("disp_text")
                   or state.get("text") or "").strip()
        if not desc and not text:
            D.logger.warning(
                "Browser pending refused: action approval with "
                "no description")
            return False
    state["created"] = time.time()
    with D._pending_lock:
        prev = D._pending_by_uid.get(uid)
        # Round 38: re-parking the SAME still-live approval
        # (identical identity, unexpired) is the same ask —
        # it must not alert twice. A park that replaces a
        # resolved / expired / different pending is a new ask.
        same_live = (
            prev is not None
            and prev.get("kind") == state.get("kind")
            and D._pending_identity(prev) == D._pending_identity(state)
            and time.time() - float(prev.get("created", 0))
            <= D._PENDING_TTL)
        D._pending_by_uid[uid] = state
    if state.get("kind") == "action" and not same_live:
        D._alert_approval(uid, state)
    if state.get("kind") == "action":
        # Round 72: an approval parked and waiting is an
        # active-use window; _clear_pending banks it (capped
        # at the expiry if it lapsed first).
        D._meter_open(uid)
    return True


def get_pending(uid: str, D) -> Optional[dict]:
    if not uid:
        return None
    expired = None
    with D._pending_lock:
        state = D._pending_by_uid.get(uid)
        if state and time.time() - float(state.get("created", 0)) \
                > D._PENDING_TTL:
            expired = state
            del D._pending_by_uid[uid]
            state = None
        else:
            state = dict(state) if state else None
    if expired is not None:
        # Round 72: a lapsed approval closes its meter
        # window here, capped at the moment it expired — it
        # never accrues past its own 10 minutes.
        if expired.get("kind") == "action":
            D._meter_bank(uid, cap=float(expired.get("created", 0))
                        + D._PENDING_TTL)
        return None
    if state and state.get("kind") == "action" \
            and not D._get_session(uid):
        # Orphan sweep: an action approval belongs to its session.
        # No session record -> the pending is already dead; clear
        # it here so /browser/status can never report it.
        D._clear_pending(uid)
        return None
    return state


def clear_pending(uid: str, D):
    with D._pending_lock:
        prev = D._pending_by_uid.pop(uid, None)
    if prev and prev.get("kind") == "action":
        # Round 72: resolving the approval banks its parked
        # window; one that already lapsed accrues only to
        # its expiry.
        cap = float(prev.get("created", 0)) + D._PENDING_TTL
        D._meter_bank(uid, cap=cap if cap < time.time() else None)
