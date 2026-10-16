"""Round 127: THE WEBSITE + BROWSER SPECIALIST
HELPER (Brent's words, 2026-10-12 11:39 PDT:
"Website and browser specialist helper" — specced
in round-127/SPEC.md.) The crew's browser hand —
the OG Browser's specialist front door. It EXTENDS
the Round 23 OG Browser (Steel) and the Round 81
crew Runner's read-only browser errands; it is
NEVER a second browser: no session machinery, no
driver, no gate of its own lives here.

WHAT ALREADY EXISTED (ridden, never forked):

  - THE BROWSER OWNS THE BROWSER, EXACTLY.
    og_browser + og_browser_claim own the session
    (one per visitor), the auto-open (a bare site
    name, "open X", "check my X" all start it in
    the same turn), every in-session command
    (click / type / read the page / scroll /
    navigate / back / enter), the takeover and
    hand-back, the parked-approval YES/NO, the
    posting flow, the always-allow list and its
    removal asks, and the dark text when the
    browser service is off. This seat checks
    og_browser._claim_job EXACTLY in its deferral
    chain — checked, never executed — and steps
    aside for every one of those shapes.
  - THE CREW RUNNER OWNS BACKGROUND READ ERRANDS.
    "Send your runner to <site>…" is the R81 crew's
    claim, dispatched under the crew caps and
    reported the crew way (prepended report +
    og_notify). This seat defers to og_crew
    exactly and dispatches nothing itself.
  - PAGE WATCHES ARE THE TRACKER'S (R122). A
    "watch this page / track X" ask rides og_watch
    through the Tracker seat's claims; this seat
    defers exactly and builds no watch machinery.
  - THE LOGIN WALL IS ROUND 27'S, EXACTLY. A read
    that lands on a log-in wall returns og_browser's
    own narration (B._narrate_snapshot) and STOPS:
    the user logs in THEMSELVES — OG never sees,
    types, or stores passwords. A checkpoint
    (og_watch._is_checkpoint) narrates and stops
    the same way — never answered, never worked
    around.
  - EVERY EARLIER SEAT KEEPS ITS ASKS. The
    deferral chain checks, exactly and in house
    order: og_github, PM, Filer, crew, Builder,
    Wood Pro, MaintEng, Mechanic, Blueprint,
    Shopping, Buying, Selling, Coding, Phone,
    CarPlay, Tracker, Marketplace, the R54
    marketplace chain, Messenger, Facebook,
    og_watch, og_monitor, the ordering pack, and
    LAST og_browser itself.

WHAT IT OWNS — the browser-specialist shapes
nobody claimed:

  - COMPARE TWO PAGES SIDE BY SIDE — "compare
    espn.com and cnn.com", "compare this page with
    bbc.com": both pages read READ-ONLY off the
    caller's live session (og_browser._drive with
    navigate + read only — og_watch's precedent),
    each gated through og_browser's own _url_gate
    before it is opened, reported side by side as
    the screens print, first screen only, and the
    browser is navigated back to the page it was
    on. The pair parks per-account so "compare
    them" re-reads the same two pages fresh.
  - BROWSER STATUS IN PLAIN WORDS — "browser
    status", "what's my browser doing", "how many
    browser minutes do I have left": the session,
    the plan minutes, a parked approval, the
    always-allow count — read off og_browser's own
    surfaces (_get_session, _plan_for,
    _session_minutes_left, _get_pending,
    _allowed_hosts) and reported exactly as they
    stand. GET /webbrowser/status serves the same.
  - THE ALWAYS-ALLOW LIST, READ BACK — "what
    sites do I always allow", "my always-allow
    list": the caller's list read off
    og_browser._allowed_hosts. This seat never
    adds to it and never removes from it —
    additions happen only at the browser's own
    approval gate, removals are og_browser's own
    disallow claim.
  - FILL A FORM RIGHT UP TO THE COMMIT WALL —
    "fill the form: name = …, email = …": each
    field is typed through og_browser's OWN hands
    (browser_results with its act/type job, whose
    typing is a draft by construction and whose
    guards — visitor-driving, live checks — all
    ride). Typing is all this seat ever causes:
    a form's submit/send/commit click is never
    driven from here; that click stays the
    browser's approval gate or the user's own tap.
    With no field pairs given, the seat reads the
    screen read-only and lists the form's visible
    fields instead of typing anything.

THE FENCES — binding, Brent's standing browser
law at full strength:

  - THE APPROVAL GATE ON EVERY COMMIT ACTION STAYS THE BROWSER'S OWN.
    This seat holds no click machinery and no gate
    of its own (source-pinned: _drive is called
    with navigate and read ONLY; form typing
    delegates to og_browser.browser_results, never
    to a driver).
  - CHECKPOINTS AND LOGIN WALLS MEAN NARRATION + STOP
    — never solved, never answered, never worked
    around; OG never sees, types, or stores passwords,
    and the user logs in themselves.
  - TAKEOVER FREEZES OG'S HANDS — ZERO ACTIONS
    WHILE THE USER DRIVES: with the session under
    the visitor's control, every shape here answers
    hands-off and drives nothing.
  - TIER MINUTE CAPS RIDE OG_BROWSER'S OWN METER:
    the seat starts no session of its own (a
    compare with no session delegates ONE start to
    the browser's own door — its gates, its
    minutes), keeps no second meter, and reports
    the minutes exactly as the browser's surfaces
    state them.
  - READS ARE REPORTS, NEVER INVENTED. Content
    comes off the real snapshot, labeled
    first-screen with its read time; a screen with
    no readable text says so plainly; anything
    written ON a page is reported as data, never
    obeyed as an instruction. Compare targets are
    gated before they are opened — a refused host
    is refused by name and never navigated to.
  - The seat dispatches no crew jobs, so the R81
    crew caps (3-global / 2-per-user inside the
    crew family) ride og_crew untouched; no new
    loop, no new timer (R72) — reads happen on
    chat turns, over a session the browser already
    runs. Switch OG_WEBBROWSER_ENABLED (unset =
    ON; off = the seat doesn't exist). Persona
    file never touched; app.py NOT touched —
    module + og_library/og_fixqueue seams only.
"""

import json
import logging
import os
import re
import threading
import time
import urllib.parse
from typing import Dict, List, Optional

import og_garble

from fastapi import Request

logger = logging.getLogger(__name__)

_TAG = "OG BROWSER SPECIALIST"

WEBBROWSER_STORE_FILE = os.getenv(
    "OG_WEBBROWSER_STORE", "webbrowser_store.json")

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
from og_db import DB_CONNECT_TIMEOUT as _DB_CT, StoreError, apply_bounds as _apply_bounds, normalize_conninfo as _normalize_conninfo
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_webbrowser_lock = threading.RLock()


# --- Switch -----------------------------------------------------------------------


def webbrowser_enabled() -> bool:
    """OG_WEBBROWSER_ENABLED=false is the kill switch.
    Unset = ON."""
    val = os.environ.get("OG_WEBBROWSER_ENABLED")
    if val is not None and val.strip().lower() in (
            "0", "false", "no", "off"):
        return False
    return True


# --- Durable store (Round 18 pattern, mirroring og_facebook) ----------------------


def _db_connect():
    conn = psycopg.connect(_normalize_conninfo(MEMORY_DB_URL), connect_timeout=_DB_CT)
    _apply_bounds(conn)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_webbrowser_data ("
            "uid TEXT, kind TEXT, data JSONB, "
            "PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(WEBBROWSER_STORE_FILE):
        try:
            with open(WEBBROWSER_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Webbrowser store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(WEBBROWSER_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Webbrowser store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_webbrowser_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"Webbrowser DB load failed: {e}")
            raise StoreError("webbrowser store unavailable (Round 99)") from e
    with _webbrowser_lock:
        store = _load_file_store()
    entry = (store.get(uid) or {}).get(kind)
    return entry if entry is not None else default


def _put_blob(uid: str, kind: str, value):
    if not uid:
        return
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO og_webbrowser_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Webbrowser DB save failed: {e}")
            raise StoreError("webbrowser store unavailable (Round 99)") from e
    with _webbrowser_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


# --- The bench: the parked compare pair --------------------------------------------


def _compare_pair(uid: str) -> Optional[Dict]:
    got = _blob(uid, "compare", None)
    if isinstance(got, dict) and got.get("a") and got.get("b"):
        return got
    return None


def _save_compare(uid: str, pair: Optional[Dict]):
    _put_blob(uid, "compare", pair or {})


def _result(tag: str, title: str, body: str,
            href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body,
             "href": href}]


def _asof() -> str:
    return time.strftime("%I:%M %p").lstrip("0") + " today"


def _host(url: str) -> str:
    try:
        return urllib.parse.urlparse(
            str(url or "")).hostname or ""
    except Exception:
        return ""


def _norm_url(url: str) -> str:
    u = str(url or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^(www\.|m\.|mobile\.)", "", u)
    return u.rstrip("/")


# --- Compare targets -----------------------------------------------------------------


def _resolve_target(side: str) -> str:
    """One compare side -> a URL, the sentinel
    "current" (this page), or "" when the words name
    no page this seat may open. Resolution rides
    og_browser_claim's own machinery (a typed URL, a
    well-known site name anywhere in the words), plus
    the house bare-host guess — a single token is a
    .com only when og_browser's allowlist admits it
    (the _disallow_host precedent)."""
    s = " ".join(str(side or "").lower().split()).strip(" .,!?")
    if not s:
        return ""
    if s in ("this page", "the page", "this one",
             "the current page", "current page"):
        return "current"
    try:
        import og_browser_claim as C
        url = C._extract_url(s) or C._resolve_site_url(s)
        if url:
            return url
        tok = s.split()[0] if s.split() else ""
        if tok and " " not in s:
            import og_browser as B
            cand = tok if "." in tok else tok + ".com"
            if B._host_allowed(cand) and not B._host_blocked(cand):
                return "https://" + cand
    except Exception:
        pass
    return ""


# --- The read layer -------------------------------------------------------------------
# Read-only by construction: og_browser._drive is called
# with navigate and read ONLY (og_watch's _check_one
# precedent). No click, no type, no gate machinery —
# form typing delegates to og_browser.browser_results,
# whose own guards and draft-only typing do the work.


def _session(uid: str):
    try:
        import og_browser as B
        return B._get_session(uid)
    except Exception:
        return None


def _wall_stop(uid: str, snap: Dict, lead: str):
    """The Round 27 law: a login wall returns
    og_browser's own narration and STOPS; a
    checkpoint narrates and STOPS. None = no wall."""
    import og_browser as B
    if B._login_wall(snap):
        return B._narrate_snapshot(uid, snap, lead)
    try:
        import og_watch as W
        if W._is_checkpoint(snap):
            return _result(
                _TAG, "Website + Browser — checkpoint, stopped",
                "That page is showing a CHECKPOINT — an "
                "identity check I never answer, click "
                "through, or work around. Take control "
                "in the panel under the chat, clear it "
                "yourself, hand back, and ask again. I "
                "stopped there: nothing else was read "
                "and nothing was touched.")
    except Exception:
        pass
    return None


def _read_target(uid: str, rec: Dict, url: str):
    """(snap, stop_results): read the target off the
    live session — a plain read when the session is
    already on it, one navigate when it is not."""
    import og_browser as B
    cur = str(rec.get("last_url") or "")
    try:
        if _norm_url(cur) == _norm_url(url):
            snap = B._drive(rec, {"do": "read"})
        else:
            snap = B._drive(rec, {"do": "navigate",
                                  "url": url})
    except B._DriveError as e:
        return None, _result(
            _TAG, "Website + Browser — read glitch",
            f"I couldn't read {_host(url) or url} just "
            f"now: {e}. Nothing was touched and nothing "
            "was read around it. Ask again and I'll "
            "retry.")
    try:
        B._update_rec_from_snap(uid, rec, snap)
    except Exception:
        pass
    return snap, None


def _guard_stop(uid: str, rec: Dict):
    """The shared guard order (_do_act's): visitor
    driving = hands off; a dead session = its honest
    reason. None = clear to read."""
    if rec.get("control") == "visitor":
        return _result(
            _TAG, "Website + Browser — you're driving",
            "You're at the wheel in the browser right "
            "now, so my hands are off — I run ZERO "
            "actions while you drive. Say 'hand back' "
            "and ask again, and I'll do it as it "
            "stands.")
    import og_browser as B
    why = B._live_checks(uid, rec)
    if why and why != "visitor_driving":
        return _result(
            _TAG, "Website + Browser — session ended",
            f"That browser session is over: {why}. "
            "Name a site to start a fresh one, then "
            "ask again.")
    return None


def _rotate(uid: str, rec: Dict) -> Dict:
    try:
        import og_browser as B
        return B._warm.rotate_if_stale(uid, B) or rec
    except Exception:
        return rec


def _snap_text(snap: Dict) -> str:
    return " ".join(str(snap.get("text") or "").split())


def _side_block(snap: Dict) -> str:
    title = str(snap.get("title") or "(untitled page)")
    url = str(snap.get("url") or "")
    text = _snap_text(snap)
    head = f"{_host(url) or url} — {title}"
    if text:
        return head + f"\n{text[:850]}"
    return head + ("\n(that screen read back with no "
                   "readable text on it — I won't invent "
                   "what isn't there)")


# --- The ops ---------------------------------------------------------------------


def _do_compare(uid: str, claim: Dict) -> List[Dict]:
    import og_browser as B
    if not B.enabled():
        return B._dark_text()
    raw = claim["raw"]
    a, b = claim["a"], claim["b"]
    rec = B._get_session(uid)
    if a == "current" or b == "current":
        cur = str((rec or {}).get("last_url") or "")
        if not cur.startswith(("http://", "https://")):
            return _result(
                _TAG, "Website + Browser — no page to "
                      "compare from",
                "You said 'this page', but my browser "
                "isn't sitting on a page right now, so "
                "there's nothing to compare from. Name "
                "both pages — 'compare espn.com and "
                "cnn.com' — or open one first.")
        if a == "current":
            a = cur
        if b == "current":
            b = cur
    if _norm_url(a) == _norm_url(b):
        return _result(
            _TAG, "Website + Browser — same page twice",
            f"Both sides of that compare resolve to "
            f"the same page — {_host(a) or a} — so "
            "there's nothing side by side to read. "
            "Name two different pages and I'll read "
            "them both.")
    for url in (a, b):
        ok, reason = B._url_gate(url)
        if not ok:
            return _result(
                _TAG, "Website + Browser — refused",
                f"I did NOT open {_host(url) or url}: "
                f"{reason}. The browser's gate refuses "
                "that host by name, and a compare never "
                "navigates to a refused page.")
    _save_compare(uid, {"a": a, "b": b,
                        "at": time.time()})
    if not rec:
        note = _result(
            _TAG, "Website + Browser — opening the "
                  "first page first",
            f"Neither page is open in my browser yet, "
            f"so there's nothing to read — and I won't "
            f"fake a compare. I've parked the pair "
            f"({_host(a)} vs {_host(b)}) on your "
            f"bench and I'm opening {_host(a)} through "
            f"the browser's own door now (its gates, "
            f"its minutes). Once it's up, say 'compare "
            f"them' and I'll read both pages fresh and "
            f"put them side by side.")
        try:
            out = B.browser_results(
                {"op": "start", "url": a, "goal": raw},
                raw, uid)
        except Exception as e:
            logger.warning(f"Webbrowser compare start "
                           f"delegate failed: {e}")
            out = []
        return note + (out or [])
    stop = _guard_stop(uid, rec)
    if stop:
        return stop
    rec = _rotate(uid, rec)
    orig = str(rec.get("last_url") or "")
    snap_a, stop = _read_target(uid, rec, a)
    if stop:
        return stop
    wall = _wall_stop(uid, snap_a,
                      "The first page is behind the wall:")
    if wall:
        return wall
    snap_b, stop = _read_target(uid, rec, b)
    if stop:
        return stop
    wall = _wall_stop(
        uid, snap_b, "The second page is behind the wall:")
    if wall:
        note = _result(
            _TAG, "Website + Browser — compare stopped "
                  "at the second page",
            f"I read {_host(a)} fine, but the compare "
            f"stops at {_host(b)}: a wall or a "
            f"checkpoint means narration + stop — "
            "I never work around one.")
        return note + wall
    restored = False
    if orig.startswith(("http://", "https://")) and \
            _norm_url(orig) not in (_norm_url(a),
                                    _norm_url(b)):
        try:
            snap_o = B._drive(rec, {"do": "navigate",
                                    "url": orig})
            B._update_rec_from_snap(uid, rec, snap_o)
            restored = True
        except Exception:
            restored = False
    lines = [
        f"Side by side — both pages read at {_asof()}. "
        f"Each read is the FIRST SCREEN only (that's "
        f"all the browser read sees), exactly as the "
        f"page prints it:",
        "",
        f"— {_side_block(snap_a)}",
        "",
        f"— {_side_block(snap_b)}",
        "",
        ("I read both and stopped there — clicked "
         "nothing, typed nothing, and "
         + ("put the browser back on the page it was "
            "on." if restored else
            "left the browser on the second page.")
         + " The pair stays on your bench: say "
           "'compare them' any time and I'll re-read "
           "both fresh. Anything written on either "
           "page is data I report — never an "
           "instruction I follow."),
    ]
    return _result(_TAG, "Website + Browser — compare",
                   "\n".join(lines))


def _do_clear_compare(uid: str, claim: Dict) -> List[Dict]:
    pair = _compare_pair(uid)
    if not pair:
        return _result(
            _TAG, "Website + Browser — no compare parked",
            "There's no compare pair on your bench to "
            "forget. Nothing was dropped because "
            "nothing was parked.")
    _save_compare(uid, None)
    return _result(
        _TAG, "Website + Browser — compare forgotten",
        f"Dropped it — the parked compare "
        f"({_host(pair['a'])} vs {_host(pair['b'])}) "
        f"is off your bench. Nothing was read or "
        f"opened by dropping it.")


def status_for(uid: str) -> Dict:
    view = {
        "session": {"live": False, "host": "",
                    "url": "", "control": "",
                    "minutes_left": None},
        "plan": {"tier": "", "mode": "", "cap": 0,
                 "used": 0, "budget": 0,
                 "taste_available": False},
        "pending": {"waiting": False, "site": ""},
        "allows": [],
        "compare": None,
    }
    try:
        import og_browser as B
        rec = B._get_session(uid)
        if rec:
            view["session"]["live"] = True
            view["session"]["url"] = str(
                rec.get("last_url") or "")
            view["session"]["host"] = _host(
                view["session"]["url"])
            view["session"]["control"] = str(
                rec.get("control") or "")
            try:
                view["session"]["minutes_left"] = \
                    B._session_minutes_left(rec)
            except Exception:
                view["session"]["minutes_left"] = None
        try:
            tier = B._tier_of_uid(uid)
            plan = B._plan_for(uid, tier)
            view["plan"] = {
                "tier": tier,
                "mode": plan.get("mode", ""),
                "cap": int(plan.get("cap") or 0),
                "used": int(plan.get("used") or 0),
                "budget": int(plan.get("budget") or 0),
                "taste_available": bool(
                    plan.get("taste_available")),
            }
        except Exception:
            pass
        try:
            pend = B._get_pending(uid)
            if pend:
                view["pending"] = {
                    "waiting": True,
                    "site": B._pending_site(pend) or "",
                }
        except Exception:
            pass
        try:
            view["allows"] = sorted(
                (B._allowed_hosts(uid) or {}).keys())
        except Exception:
            view["allows"] = []
    except Exception:
        pass
    pair = _compare_pair(uid)
    if pair:
        view["compare"] = {"a": pair.get("a", ""),
                           "b": pair.get("b", "")}
    return view


def _do_status(uid: str, claim: Dict) -> List[Dict]:
    import og_browser as B
    if not B.enabled():
        return B._dark_text()
    view = status_for(uid)
    lines = []
    sess = view["session"]
    if sess["live"]:
        left = sess["minutes_left"]
        left_line = (f" — about {left} minute(s) left "
                     f"in this session"
                     if left is not None else "")
        who = (" You're driving it right now; my "
               "hands are off." if sess["control"]
               == "visitor" else "")
        lines.append(
            f"A browser session is LIVE on "
            f"{sess['host'] or 'a page'}{left_line}."
            f"{who}")
    else:
        lines.append(
            "No browser session is live right now — "
            "name a site (or just say its name) and "
            "the browser's own door starts one.")
    plan = view["plan"]
    if plan["mode"] == "tier":
        lines.append(
            f"Your {plan['tier']} plan carries "
            f"{plan['cap']} browser minutes a day; "
            f"you've used {plan['used']} today, so "
            f"{plan['budget']} are left in today's "
            f"pool. That pool is the browser's own "
            f"meter — I report it, I don't keep a "
            f"second one.")
    elif plan["mode"] == "taste":
        lines.append(
            "Your plan carries no browser minutes, "
            "but your one free 10-minute taste is "
            "still unspent — it spends on your first "
            "real page view, once ever.")
    elif plan["mode"] == "none":
        lines.append(
            f"Your {plan['tier']} plan's browser "
            f"minutes for today are spent "
            f"({plan['used']} of {plan['cap']} used) "
            f"and the free taste is gone — the "
            f"browser's own meter says so.")
    if view["pending"]["waiting"]:
        lines.append(
            f"An action is PARKED at the browser's "
            f"approval gate"
            + (f" for {view['pending']['site']}"
               if view["pending"]["site"] else "")
            + " — it moves only on your YES, in the "
              "browser's own flow. It is not mine to "
              "approve from this seat.")
    n = len(view["allows"])
    lines.append(
        f"Your always-allow list holds {n} site(s)"
        + (" — say 'what sites do I always allow' "
           "for the list itself." if n else "."))
    if view["compare"]:
        lines.append(
            f"A compare is parked on your bench: "
            f"{_host(view['compare']['a'])} vs "
            f"{_host(view['compare']['b'])} — say "
            f"'compare them' to re-read both fresh.")
    lines.append(
        "Asks I take: 'compare <site> and <site>', "
        "'compare them', 'browser status', 'what "
        "sites do I always allow', and 'fill the "
        "form: field = value' pairs. The browser "
        "itself keeps its own asks — opening sites, "
        "clicking, typing, posting, watching pages "
        "(the Tracker's door) all stay theirs.")
    return _result(_TAG, "Website + Browser — status",
                   "\n".join(lines))


def _do_allows(uid: str, claim: Dict) -> List[Dict]:
    import og_browser as B
    if not B.enabled():
        return B._dark_text()
    try:
        hosts = B._allowed_hosts(uid) or {}
    except Exception:
        hosts = {}
    if not hosts:
        return _result(
            _TAG, "Website + Browser — always-allow "
                  "list (empty)",
            "Your always-allow list is EMPTY — no "
            "site is on it. A site lands there only "
            "when YOU say 'always allow' at the "
            "browser's approval gate; I never add one "
            "from this seat, and nothing is opened "
            "without the gate until a site is on it.")
    lines = ["Your always-allow list — the sites "
             "whose browser actions skip the approval "
             "ask:"]
    for host in sorted(hosts.keys()):
        added = str((hosts.get(host) or {}).get(
            "added") or "")
        lines.append(f"• {host}"
                     + (f" (added {added})" if added
                        else ""))
    lines.append(
        "The list waives the ASK only — the gate's "
        "other laws all stand, and a refused host "
        "stays refused. I never add or remove from "
        "this seat: take one off with 'stop always "
        "allowing <site>' — those are the browser's "
        "own words, and it owns them.")
    return _result(
        _TAG, "Website + Browser — always-allow list",
        "\n".join(lines))


def _parse_pairs(payload: str):
    """The form's field pairs, from the user's OWN
    words only: 'field = value' (or 'field: value')
    pairs split on ';' or newlines — or on commas
    only when every chunk carries an '='. Payload
    passes verbatim; nothing is composed here."""
    pairs = []
    if not payload:
        return pairs
    chunks = re.split(r"[;\n]+", payload)
    if len(chunks) == 1 and "=" in payload:
        comma = payload.split(",")
        if len(comma) > 1 and all(
                "=" in c for c in comma):
            chunks = comma
    for chunk in chunks:
        chunk = chunk.strip().strip(",").strip()
        if not chunk:
            continue
        m = re.match(r"^([^=]{1,40}?)\s*=\s*(.+)$",
                     chunk) or re.match(
                         r"^([^:]{1,40}?)\s*:\s*(.+)$",
                         chunk)
        if not m:
            continue
        name = " ".join(
            m.group(1).split()).strip(" \"'")
        value = m.group(2).strip().strip(",").strip()
        if len(value) >= 2 and value[0] in "\"'" \
                and value[-1] == value[0]:
            value = value[1:-1]
        if name and value:
            pairs.append((name, value[:200]))
        if len(pairs) >= 8:
            break
    return pairs


def _do_fill(uid: str, claim: Dict) -> List[Dict]:
    import og_browser as B
    if not B.enabled():
        return B._dark_text()
    rec = B._get_session(uid)
    if not rec:
        return _result(
            _TAG, "Website + Browser — no form on "
                  "screen",
            "There's no browser session live, so no "
            "form is on screen for me to fill — and I "
            "won't pretend I typed into one. Open the "
            "page with the form first (just name the "
            "site), get the form on screen, then say "
            "'fill the form' with your field pairs.")
    stop = _guard_stop(uid, rec)
    if stop:
        return stop
    rec = _rotate(uid, rec)
    pairs = claim["pairs"]
    if not pairs:
        # No pairs given: read the screen READ-ONLY
        # and list the form's visible fields —
        # typing nothing.
        try:
            snap = B._drive(rec, {"do": "read"})
        except B._DriveError as e:
            return _result(
                _TAG, "Website + Browser — read "
                      "glitch",
                f"I couldn't read the page to see "
                f"its form: {e}. Nothing was typed. "
                "Ask again and I'll retry.")
        wall = _wall_stop(
            uid, snap, "The form is behind the wall:")
        if wall:
            return wall
        try:
            B._update_rec_from_snap(uid, rec, snap)
        except Exception:
            pass
        fields = []
        for el in snap.get("elements") or []:
            if el.get("tag") in ("input", "textarea",
                                 "select"):
                label = str(el.get("label") or "").strip()
                if label and label not in fields:
                    fields.append(label)
        if not fields:
            return _result(
                _TAG, "Website + Browser — no form "
                      "fields here",
                "This screen shows no form fields I "
                "can see (the read sees the first "
                "screen only). If the form is further "
                "down, scroll in the browser and ask "
                "again — I typed nothing.")
        listing = "; ".join(fields[:12])
        return _result(
            _TAG, "Website + Browser — the form's "
                  "fields",
            f"The form on this screen has these "
            f"fields: {listing}. I typed NOTHING — "
            f"give me the pairs in your own words, "
            f"like 'fill the form: name = …, email = "
            f"…', and I'll type each one in as a "
            f"draft through the browser's own hands. "
            f"The form's submit button is never mine "
            f"to click: that stays the browser's "
            f"approval gate or your own tap.")
    out = _result(
        _TAG, "Website + Browser — filling the form "
              "(drafts only)",
        f"Typing {len(pairs)} field(s) in through "
        f"the browser's own hands — each line below "
        f"is the browser's own report. These are "
        f"DRAFTS: typed text sends nothing, and I "
        f"never click the form's submit, send, or "
        f"commit button from this seat — that click "
        f"stays the browser's approval gate or your "
        f"own tap, exactly as always.")
    for name, value in pairs:
        job = {"op": "act", "verb": "type",
               "text": value, "field": name}
        try:
            res = B.browser_results(job, claim["raw"],
                                    uid)
        except Exception as e:
            logger.warning(f"Webbrowser fill delegate "
                           f"failed: {e}")
            res = _result(
                _TAG, "Website + Browser — a field "
                      "glitched",
                f"The '{name}' field glitched on the "
                f"way to the browser's hands — nothing "
                f"was typed for it. The rest of the "
                f"form is as the lines around this one "
                f"report.")
        out += res or []
    return out


def webbrowser_results(claim: Dict, uid: str) -> List[Dict]:
    op = claim.get("op")
    if op == "wb_compare":
        return _do_compare(uid, claim)
    if op == "wb_clear_compare":
        return _do_clear_compare(uid, claim)
    if op == "wb_status":
        return _do_status(uid, claim)
    if op == "wb_allows":
        return _do_allows(uid, claim)
    if op == "wb_fill":
        return _do_fill(uid, claim)
    return _result(_TAG, "Website + Browser — glitch",
                   "That browser-specialist ask "
                   "glitched on my end — nothing was "
                   "read, typed, or opened. Say it "
                   "again and I'll retry.")


# --- Claims discipline: every inner seat keeps its asks ---------------------------------


def _other_claim(message: str, uid: str):
    """(source, claim) of the first inner seat that
    claims this message exactly, else None. og_github
    FIRST — its pendings, clarifying questions, and
    every repo-shaped ask are the R60 path's own. Then
    the PM, the Filer, the crew, the Builder, the
    Wood Pro, MaintEng, the Mechanic, the Blueprint
    Specialist, Shopping, Buying, Selling, Coding,
    Phone, CarPlay, Tracker, Marketplace, the R54
    marketplace chain, Messenger, Facebook, og_watch,
    og_monitor, the ordering pack, and LAST
    og_browser itself — every start / post / in-session
    / YES-NO shape is the browser's own door: checked
    exactly, never executed from here."""
    try:
        import og_github
        c = og_github._claim_job(message, uid)
        if c:
            return "github", c
    except Exception:
        pass
    try:
        import og_pm
        c = og_pm._claim_exact(message)
        if c:
            return "pm", c
    except Exception:
        pass
    try:
        import og_filing
        c = og_filing._claim_exact(message, uid)
        if c:
            return "filing", c
    except Exception:
        pass
    try:
        import og_crew
        c = og_crew._claim_exact(message)
        if c:
            return "crew", c
    except Exception:
        pass
    try:
        import og_builder
        job, _info = og_builder._claim_builder(message, uid)
        if job:
            return "builder", job
    except Exception:
        pass
    try:
        import og_woodpro
        c = og_woodpro._claim_exact(message, uid)
        if c:
            return "woodpro", c
    except Exception:
        pass
    try:
        import og_mainteng
        c = og_mainteng._claim_exact(message, uid)
        if c:
            return "mainteng", c
    except Exception:
        pass
    try:
        import og_mechanic
        c = og_mechanic._claim_exact(message, uid)
        if c:
            return "mechanic", c
    except Exception:
        pass
    try:
        import og_blueprint
        c, _info = og_blueprint._claim_blueprint(message, uid)
        if c:
            return "blueprint", c
    except Exception:
        pass
    try:
        import og_shopping
        c = og_shopping._claim_exact(message, uid)
        if c:
            return "shopping", c
    except Exception:
        pass
    try:
        import og_buying
        c = og_buying._claim_exact(message, uid)
        if c:
            return "buying", c
    except Exception:
        pass
    try:
        import og_selling
        c = og_selling._claim_exact(message, uid)
        if c:
            return "selling", c
    except Exception:
        pass
    try:
        import og_coding
        c = og_coding._claim_exact(message, uid)
        if c:
            return "coding", c
    except Exception:
        pass
    try:
        import og_phone
        c = og_phone._claim_exact(message, uid)
        if c:
            return "phone", c
    except Exception:
        pass
    try:
        import og_carplay
        c = og_carplay._claim_exact(message, uid)
        if c:
            return "carplay", c
    except Exception:
        pass
    try:
        import og_tracker
        c = og_tracker._claim_exact(message, uid)
        if c:
            return "tracker", c
    except Exception:
        pass
    try:
        import og_marketplace
        c = og_marketplace._claim_exact(message, uid)
        if c:
            return "marketplace", c
    except Exception:
        pass
    try:
        import og_mpwatch
        c = og_mpwatch._claim_job(message, uid)
        if c:
            return "mpwatch", c
    except Exception:
        pass
    try:
        # Round 125 (Messenger) — its claim entry
        # point is checked defensively: the seat
        # landed in the same wave; any of its house
        # entry names defers exactly, never executed.
        import og_messenger
        _fn = getattr(og_messenger, "_claim_exact",
                      None) or getattr(
                          og_messenger, "_claim_job",
                          None) or getattr(
                              og_messenger,
                              "_claim_messenger", None)
        if _fn is not None:
            c = _fn(message, uid)
            if isinstance(c, tuple):
                c = c[0]
            if c:
                return "messenger", c
    except Exception:
        pass
    try:
        import og_facebook
        c = og_facebook._claim_exact(message, uid)
        if c:
            return "facebook", c
    except Exception:
        pass
    try:
        import og_watch
        c = og_watch._claim_job(message, uid)
        if c:
            return "watch", c
    except Exception:
        pass
    try:
        import og_monitor
        c = og_monitor.parse_monitor_intent(message)
        if c:
            return "monitor", c
    except Exception:
        pass
    try:
        import og_ordering
        c = og_ordering._claim_job(message, uid)
        if c:
            return "ordering", c
    except Exception:
        pass
    try:
        import og_ordering_flows
        c = og_ordering_flows._claim_job(message, uid)
        if c:
            return "ordering_flows", c
    except Exception:
        pass
    try:
        import og_browser
        c = og_browser._claim_job(message, uid)
        if c:
            return "browser", c
    except Exception:
        pass
    return None


def _defer_to_others(message: str, uid: str) -> bool:
    return _other_claim(message, uid) is not None


_COMPARE_RE = re.compile(
    r"\bcompare\s+(.+?)\s+(?:and|with|vs\.?|versus|"
    r"against|to)\s+(.+?)\s*[.!?]*$", re.I | re.S)
_COMPARE_THEM_RE = re.compile(
    r"^\W*(?:do\s+the\s+|run\s+the\s+)?compare\s+"
    r"(?:them|the\s+pages|those\s+pages|both\s+pages|"
    r"both)\b|\bcompare\s+them\b", re.I)
_CLEAR_COMPARE_RE = re.compile(
    r"\b(forget|clear|drop|scrap)\b[^.?!]*"
    r"\b(compare|comparison)\b", re.I)
_FILL_RE = re.compile(
    r"\bfill\s+(?:in\s+|out\s+)?the\s+form\b"
    r"[:\s]*(.*)$", re.I | re.S)
_STATUS_RE = re.compile(
    r"\bbrowser\s+status\b"
    r"|\bwhat(?:'s| is) my browser doing\b"
    r"|\bwhat(?:'s| is) the browser (?:doing|on)\b"
    r"|\bwhat site is (?:my|the) browser on\b"
    r"|\bhow (?:many|much) browser (?:minutes|time)\b"
    r"|\bbrowser minutes\b", re.I)
_ALLOWS_RE = re.compile(
    r"\bwhat sites do i always allow\b"
    r"|\bmy always[- ]allow list\b"
    r"|\bwhat(?:'s| is) on my always[- ]allow"
    r"(?:\s+list)?\b"
    r"|\bwhich sites can you (?:open|browse) without "
    r"asking\b"
    r"|\balways[- ]allowed sites\b"
    r"|\bwhat sites are always allowed\b", re.I)


def _claim_exact(message: str, uid: str) -> Optional[Dict]:
    raw = str(message or "").strip()
    if not raw or not uid:
        return None
    if _defer_to_others(raw, uid):
        return None
    low = " ".join(raw.lower().split())
    if _CLEAR_COMPARE_RE.search(raw):
        return {"op": "wb_clear_compare", "raw": raw}
    m = _COMPARE_RE.search(raw)
    if m:
        a = _resolve_target(m.group(1))
        b = _resolve_target(m.group(2))
        if a and b:
            return {"op": "wb_compare", "a": a, "b": b,
                    "raw": raw}
    if _COMPARE_THEM_RE.search(raw) and _compare_pair(uid):
        pair = _compare_pair(uid)
        return {"op": "wb_compare", "a": pair["a"],
                "b": pair["b"], "raw": raw}
    m = _FILL_RE.search(raw)
    if m:
        payload = (m.group(1) or "").strip()
        if payload.lower().startswith("with "):
            payload = payload[5:].strip()
        return {"op": "wb_fill",
                "pairs": _parse_pairs(payload),
                "raw": raw}
    if _STATUS_RE.search(raw):
        return {"op": "wb_status", "raw": raw}
    if _ALLOWS_RE.search(raw):
        return {"op": "wb_allows", "raw": raw}
    return None


_GARBLE_VOCAB = [
    "browser", "status", "minutes", "always",
    "allow", "what", "sites", "doing",
]


def _claim_webbrowser(message: str, uid: str):
    """(claim, garble_info): the exact claim first; on
    a miss, the Round 75 second chance — og_garble
    repairs the TRIGGER words only. Only the two
    payload-free state reads (status, the allow
    list) may claim on the garble path: a compare's
    targets and a fill's field pairs are payload
    and must pass 100% verbatim or not at all."""
    claim = _claim_exact(message, uid)
    if claim:
        return claim, None
    try:
        repaired, info = og_garble.repair_info(
            str(message or ""), _GARBLE_VOCAB)
        if info:
            claim = _claim_exact(repaired, uid)
            if claim and claim.get("op") in (
                    "wb_status", "wb_allows"):
                return claim, info
    except Exception:
        pass
    return None, None


# --- The seam ---------------------------------------------------------------------

_pending = {"claim": None, "garble": None}


def install_webbrowser_tools(agent_instance, get_uid):
    """Wrap detect_intent + web_search OUTSIDE the
    Facebook seam (installed last, from the
    og_fixqueue chain) so browser-specialist asks
    claim here and everything else delegates back
    untouched. Persona files never touched."""
    if getattr(agent_instance, "_og_webbrowser_installed",
               False):
        return
    prev_detect = getattr(agent_instance, "detect_intent",
                          None)
    prev_search = getattr(agent_instance, "web_search",
                          None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["claim"] = None
        _pending["garble"] = None
        try:
            uid = get_uid()
            if uid and webbrowser_enabled():
                claim, info = _claim_webbrowser(
                    str(message), uid)
                if claim:
                    _pending["claim"] = claim
                    _pending["garble"] = info
                    if isinstance(intent, dict):
                        intent[
                            "needs_code_generation"] = False
                        if not intent.get("needs_web_search"):
                            intent["needs_web_search"] = True
                            intent["search_query"] = \
                                str(message).strip()
        except Exception as e:
            logger.warning(
                f"Webbrowser trigger check failed: {e}")
            _pending["claim"] = None
            _pending["garble"] = None
        return intent

    def search_wrapped(query, num_results=5):
        claim = _pending.get("claim")
        garble = _pending.get("garble")
        _pending["claim"] = None
        _pending["garble"] = None
        uid = ""
        try:
            uid = get_uid()
        except Exception:
            uid = ""
        results = None
        if claim and uid:
            try:
                results = webbrowser_results(claim, uid)
            except Exception as e:
                logger.warning(f"Webbrowser op failed: {e}")
                results = _result(
                    _TAG, "Website + Browser — glitch",
                    "That browser-specialist ask "
                    "glitched on my end — nothing was "
                    "read, typed, or opened. Say it "
                    "again and I'll retry.")
        if results is None:
            results = prev_search(query, num_results)
        if garble and results:
            results = og_garble.annotate(results, garble,
                                         prepend=True)
        return results

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_webbrowser_installed = True


# --- Routes -----------------------------------------------------------------------


def _route_uid(request) -> str:
    """Round 95: the caller's ONE identity — the live
    session's account uid when signed in, else the
    presented cookie (og_accounts.session_uid_or)."""
    uid = ""
    try:
        uid = request.cookies.get("ogai_uid", "")
    except Exception:
        uid = ""
    try:
        import og_accounts as _acct
        return _acct.session_uid_or(request.cookies, uid)
    except Exception:
        return uid


def register_webbrowser_routes(app):
    """GET /webbrowser/status — the caller's own
    browser picture: session, plan minutes, parked
    approval, always-allow hosts, parked compare,
    read live off og_browser's own surfaces."""

    @app.get("/webbrowser/status")
    async def webbrowser_status_route(request: Request):
        uid = _route_uid(request)
        view = status_for(uid) if uid else {
            "session": {"live": False, "host": "",
                        "url": "", "control": "",
                        "minutes_left": None},
            "plan": {"tier": "", "mode": "", "cap": 0,
                     "used": 0, "budget": 0,
                     "taste_available": False},
            "pending": {"waiting": False, "site": ""},
            "allows": [], "compare": None}
        return {"ok": True,
                "enabled": webbrowser_enabled(), **view}
