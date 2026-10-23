"""The user's algorithm — the panels leg (Brent's rules,
2026-10-16, from the panel audit of the same day).

WHAT THIS IS. Round 70 gave the Feed an ORDERING (the
caller's own library items + notifications, scored) and
gave Ideas four hand-placed rules. Brent's panel rules
complete both seats:

  FEED (Brent 2026-10-16 09:26): "the Feed runs on the
  user's algorithm (personalized first); if nothing
  personal, fill with 'random stuff of the day' — never
  blank." Until this module the Feed held ONLY the
  mailbox (og_notify) plus the caller's library rows —
  a user with neither got an empty seat, and there was
  no content algorithm and no daily fallback at all.
  Brent's "we never added anything" was correct.

  IDEAS (Brent 2026-10-16 09:32): "Ideas also runs on
  the user's algorithm — personalized ideas for the
  user," with the user's OG Watch list/activity as an
  input signal.

This module is the ONE signal reader both seats consume:

  signals_for(uid, request) — per-user signals derived
  ONLY from records the server already holds for THIS
  caller: what they make (og_library's aggregate over
  the generator stores + og_artifacts), what they watch
  (og_watch's list + og_monitor's price watches), what
  they save (the locker, inside the library aggregate).
  No signal store is invented, nothing is learned across
  users, and chat topics are NOT mined — the honest
  rules-only stance og_ideas set in Round 70 stands.

  personal_rows(uid, items, notify_items) — the Feed's
  personalized CONTENT rows (a most-made-kind resurface
  and a watch digest), built from the rows the Feed
  already collected plus one guarded watch pass. They
  ride the Feed's existing score order via their score
  field; the notifications mailbox rows are untouched
  and keep their own order semantics.

  daily_rows() — the "random stuff of the day": a
  deterministic pick from a fixed pool of general
  entries (real OG capabilities and starters — no dated
  facts are claimed, because no source here could back
  one). NO JUNK DRAWER: the pick is a pure function of
  the calendar date — sha256("og-day:" + date) seeding a
  sample — so the day's selection IS a written, dated
  record: every row carries its date in its id
  ("day:YYYY-MM-DD:<key>"), any process recomputes the
  identical selection all day, and it rotates when the
  date turns. Nothing is stored, so nothing can pile
  up, go stale, or disagree between workers.

IDEAS BOUNDARY (Brent's law, 2026-10-18, restated
2026-10-11): Ideas holds USER stuff ONLY. signals_for
reads only the user's own world; nothing here can see
og_fixqueue, fix proposals, or OG's system needs, and
og_ideas' new candidates draw exclusively from this
dict. The fix-proposal section of the Ideas seat is a
separate surface (og_fixqueue via /approvals/proposals)
— Ticket C's fix-wave territory, untouched here.

MAIN THREAD: consumers are the /feed and /ideas GET
handlers — sync def handlers (FastAPI threadpool) that
already pay these same store reads today. Nothing in
this module is called from the chat path.

FAIL-SOFT: every leg is guarded on its own, in the house
pattern — a broken signal contributes nothing and never
sinks a seat.
"""

import hashlib
import logging
import random
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# The Feed tops up with daily rows until it holds at
# least this many rows — the never-blank guarantee, with
# a little substance. A user whose own rows already fill
# the seat gets no filler at all.
FEED_MIN_ROWS = 6
# How many of the day's picks may be used as filler.
DAILY_PICK = 6
# Personalized content rows score at their signal's
# timestamp plus this boost: below an unread notification
# (+3d) and an alert kind (+2d) in og_library.feed_score's
# economy, above a bare old item — they sit near the top
# without ever shouting over the mailbox.
PERSONAL_BOOST = 12 * 3600.0

_CREATION_KINDS = ("video", "song", "podcast", "book",
                   "motion", "model3d", "sfx", "project",
                   "page", "document", "table")
_PLURALS = {"video": "videos", "song": "songs",
            "podcast": "podcasts", "book": "books",
            "motion": "motion clips",
            "model3d": "3D models", "sfx": "sound effects",
            "project": "projects", "page": "pages",
            "document": "documents", "table": "tables"}
_SINGULARS = {"video": "story video", "song": "song",
              "podcast": "podcast episode", "book": "book",
              "motion": "motion clip",
              "model3d": "3D model", "sfx": "sound effect"}

# The day pool: general entries, all of them true of the
# product as shipped (each names a capability with a live
# module behind it). Evergreen by design — the rotation
# comes from the date seed, not from dated claims.
DAILY_POOL = (
    ("song", "OG can write you a song",
     "Hand OG a topic and a style — he writes it and "
     "sings it, and your songs live in your Library."),
    ("video", "Turn a story into a video",
     "OG can narrate a story into a video, scene by "
     "scene. Finished videos land in your Library."),
    ("podcast", "A podcast in two voices",
     "OG can cut a podcast episode with two hosts on "
     "any topic you hand him."),
    ("book", "Start a book, chapter by chapter",
     "OG writes books a chapter at a time — and picks "
     "up right where you left off."),
    ("page", "Ask for a page, OG builds it",
     "Budget page, tracker, checklist — say the words "
     "and OG builds it into your Library."),
    ("document", "Turn talk into a document",
     "Describe it or dictate it — OG turns it into a "
     "clean document in your Library."),
    ("table", "Get it as a table",
     "Lists, prices, schedules — ask and OG lays it "
     "out as a table in your Library."),
    ("watch", "OG can watch for deals",
     "Tell OG what you're hunting and the price — he "
     "watches and pings you when it shows up."),
    ("price", "Watch a price instead of a chart",
     "Stocks and crypto: set the level and OG watches "
     "it, so you don't have to stare at charts."),
    ("browser", "OG can run the browser",
     "Forms, lookups, digging through sites — OG can "
     "drive the browser and report back."),
    ("image", "Pictures save themselves",
     "Pictures OG draws for you land in your Library "
     "under Images — still there when you come back."),
    ("locker", "Your locker keeps your files",
     "Files you save with OG stay in your Library, "
     "counted against your plan's space."),
    ("motion", "Make a motion clip",
     "OG can animate an idea into a short motion clip "
     "for you."),
    ("model3d", "Build a 3D model",
     "Describe the thing — OG generates a 3D model "
     "you can download."),
    ("sfx", "Sound effects on demand",
     "Need a sound? OG's voice lab cooks up effects "
     "and voices."),
    ("voice", "Just talk to OG",
     "Tap the mic and talk — OG listens and answers "
     "out loud."),
    ("project", "OG can run a project",
     "Multi-step jobs get a project board — steps, "
     "files and progress, all in your Progress seat."),
    ("ideas", "Your Ideas are yours",
     "The Ideas seat only ever holds ideas drawn from "
     "your own activity — never OG's homework."),
)


# --- The day's selection (pure function of the date) ---------------------------


def _day_str(when: Optional[float] = None) -> str:
    return time.strftime("%Y-%m-%d",
                         time.localtime(when or time.time()))


def _day_start(when: Optional[float] = None) -> float:
    lt = time.localtime(when or time.time())
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                        0, 0, 0, 0, 0, -1))


def daily_pick(when: Optional[float] = None) -> List[tuple]:
    """The day's entries, in the day's order. Same date ->
    same pick, in any process, forever; the pick rotates
    when the date turns. This function is the record."""
    day = _day_str(when)
    seed = int(hashlib.sha256(
        ("og-day:" + day).encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    return rng.sample(list(DAILY_POOL),
                      min(DAILY_PICK, len(DAILY_POOL)))


def daily_rows(when: Optional[float] = None,
               limit: int = DAILY_PICK) -> List[Dict]:
    """Feed rows for the day's pick (src "day"). ts/score
    step down by a minute per row so the pick's order is
    the render order among themselves; appended after the
    caller's own rows by the caller, they never outrank
    anything personal."""
    day = _day_str(when)
    start = _day_start(when)
    rows: List[Dict] = []
    for i, (key, title, body) in enumerate(
            daily_pick(when)[:max(0, limit)]):
        ts = start - 60.0 * i
        rows.append({
            "src": "day", "id": f"day:{day}:{key}",
            "kind": "day", "title": title, "sub": body,
            "ts": ts, "read": True, "target": None,
            "action": "", "action_url": "", "status": "",
            "score": ts,
        })
    return rows


# --- Watch signals (shared by the Feed digest + Ideas) --------------------------


def _watch_summaries(uid: str) -> List[Dict]:
    """The caller's OG Watch list, normalized. Guarded by
    the callers; raises nothing itself beyond its imports."""
    import og_watch
    out: List[Dict] = []
    for w in og_watch._watches(uid):
        if not isinstance(w, dict):
            continue
        matches = w.get("matches")
        out.append({
            "id": w.get("id", ""),
            "item": w.get("item") or "",
            "place": w.get("place") or "",
            "status": w.get("status") or "",
            "kind": w.get("kind") or "fbwatch",
            "checks": int(w.get("checks", 0) or 0),
            "matches": (len(matches)
                        if isinstance(matches, list) else 0),
            "state_ts": float(w.get("state_ts", 0) or 0),
            "last_check": w.get("last_check"),
        })
    return out


def _price_watch_summaries(uid: str) -> List[Dict]:
    import og_monitor
    out: List[Dict] = []
    for w in og_monitor._watches(uid):
        if not isinstance(w, dict):
            continue
        out.append({
            "symbol": w.get("symbol") or "",
            "terms": w.get("terms") or "",
            "status": w.get("status") or "",
            "created": float(w.get("created", 0) or 0),
        })
    return out


def watch_digest(uid: str) -> Optional[Dict]:
    """Aggregate watch state for the Feed's digest row, or
    None when the caller watches nothing. Guarded: a dead
    watch store means no digest, never an error."""
    try:
        watches = _watch_summaries(uid)
    except Exception as e:
        logger.warning(f"Algorithm watch leg failed: {e}")
        watches = []
    try:
        prices = _price_watch_summaries(uid)
    except Exception as e:
        logger.warning(f"Algorithm price-watch leg failed: {e}")
        prices = []
    active_prices = [p for p in prices
                     if p["status"] != "fired"]
    if not watches and not active_prices:
        return None
    latest = 0.0
    for w in watches:
        latest = max(latest, w["state_ts"])
        lc = w["last_check"]
        if isinstance(lc, (int, float)):
            latest = max(latest, float(lc))
    for p in active_prices:
        latest = max(latest, p["created"])
    return {
        "watches": watches,
        "active": sum(1 for w in watches
                      if w["status"] == "active"),
        "waiting": sum(1 for w in watches
                       if w["status"] == "waiting_login"),
        "checks": sum(w["checks"] for w in watches),
        "matches": sum(w["matches"] for w in watches),
        "price_active": len(active_prices),
        "latest_ts": latest,
    }


# --- The Feed's personalized content rows ---------------------------------------


def personal_rows(uid: str, items: List[Dict],
                  notify_items: List[Dict]) -> List[Dict]:
    """Personalized CONTENT rows for the Feed, from the
    caller's own signals: (a) a resurface of their
    most-made creation kind, (b) a digest of their watch
    state. The mailbox (notify) rows and the library rows
    themselves are the caller's, unchanged — these are
    additions ABOUT the caller's activity, scored to sit
    under fresh alerts. Each row is guarded on its own."""
    rows: List[Dict] = []

    # (a) Most-made creation kind (two or more of a kind
    # is a habit worth resurfacing; one is just a row the
    # feed already carries).
    try:
        counts: Dict[str, int] = {}
        newest: Dict[str, Dict] = {}
        for it in items or []:
            if not isinstance(it, dict):
                continue
            kind = it.get("kind", "")
            if kind not in _CREATION_KINDS:
                continue
            counts[kind] = counts.get(kind, 0) + 1
            ts = float(it.get("created_at", 0) or 0)
            cur = newest.get(kind)
            if cur is None or ts > float(
                    cur.get("created_at", 0) or 0):
                newest[kind] = it
        best_kind, best_n = "", 0
        for kind in _CREATION_KINDS:
            if counts.get(kind, 0) > best_n:
                best_kind, best_n = kind, counts[kind]
        if best_kind and best_n >= 2:
            top = newest[best_kind]
            ts = float(top.get("created_at", 0) or 0)
            title = top.get("title") or "your latest"
            rows.append({
                "src": "you", "id": f"you:topkind:{best_kind}",
                "kind": "made",
                "title": (f"You've made {best_n} "
                          f"{_PLURALS[best_kind]}"),
                "sub": (f"Newest: “{title}” — it's in your "
                        "Library, and OG can make another "
                        "any time you ask."),
                "ts": ts, "read": True, "target": None,
                "action": "", "action_url": "", "status": "",
                "score": ts + PERSONAL_BOOST,
            })
    except Exception as e:
        logger.warning(f"Algorithm top-kind row failed: {e}")

    # (b) The watch digest — the caller's OG Watch
    # list/activity as one Feed row.
    try:
        dig = watch_digest(uid)
        if dig:
            parts: List[str] = []
            if dig["active"]:
                parts.append(f"{dig['active']} watching now")
            if dig["waiting"]:
                parts.append(
                    f"{dig['waiting']} waiting on your "
                    "sign-in")
            if dig["matches"]:
                parts.append(
                    f"{dig['matches']} match"
                    f"{'es' if dig['matches'] != 1 else ''} "
                    "found so far")
            if dig["checks"]:
                parts.append(
                    f"{dig['checks']} check"
                    f"{'s' if dig['checks'] != 1 else ''} run")
            if dig["price_active"]:
                parts.append(
                    f"{dig['price_active']} price watch"
                    f"{'es' if dig['price_active'] != 1 else ''}"
                    " live")
            ts = dig["latest_ts"] or time.time()
            rows.append({
                "src": "you", "id": "you:watches",
                "kind": "watching",
                "title": "Your watches",
                "sub": " · ".join(parts),
                "ts": ts, "read": True, "target": None,
                "action": "", "action_url": "", "status": "",
                "score": ts + PERSONAL_BOOST,
            })
    except Exception as e:
        logger.warning(f"Algorithm watch row failed: {e}")

    return rows


# --- The shared signal dict (Ideas consumes this) -------------------------------


def signals_for(uid: str, request) -> Dict:
    """The caller's signals for Ideas, each leg guarded:
      artifact     newest READY og_artifacts record
      watch_match  the watch carrying the most matches
      watch_active newest active watch that has run
                   checks (and no match-carrying watch
                   outranked it)
      price_watch  first live og_monitor price watch
      favorite     most-made generator kind at 3+ makes,
                   with its newest title
    USER stuff only — no leg here can see OG's system
    side (Brent's 2026-10-18 law)."""
    sig: Dict = {}

    try:
        import og_artifacts
        ready = [r for r in
                 og_artifacts.list_artifacts_for_owner(uid)
                 if isinstance(r, dict)
                 and r.get("status") == "ready"]
        if ready:
            newest = max(
                ready,
                key=lambda r: float(r.get("updated", 0)
                                    or r.get("created", 0)
                                    or 0))
            sig["artifact"] = {
                "id": newest.get("id", ""),
                "kind": newest.get("kind", "page"),
                "title": newest.get("title") or "",
                "revision": int(newest.get("revision", 1)
                                or 1),
            }
    except Exception as e:
        logger.warning(f"Algorithm artifact signal failed: {e}")

    try:
        watches = _watch_summaries(uid)
        with_matches = [w for w in watches if w["matches"] > 0]
        if with_matches:
            best = max(with_matches,
                       key=lambda w: w["matches"])
            sig["watch_match"] = best
        active = [w for w in watches
                  if w["status"] == "active"
                  and w["checks"] > 0]
        if active:
            sig["watch_active"] = max(
                active, key=lambda w: w["state_ts"])
    except Exception as e:
        logger.warning(f"Algorithm watch signal failed: {e}")

    try:
        for p in _price_watch_summaries(uid):
            if p["status"] != "fired" and p["symbol"]:
                sig["price_watch"] = p
                break
    except Exception as e:
        logger.warning(
            f"Algorithm price-watch signal failed: {e}")

    try:
        import og_library
        items, _line, counts = og_library.collect_library(
            uid, request)
        best_kind, best_n = "", 0
        for kind, key in (("song", "songs"),
                          ("video", "videos"),
                          ("podcast", "podcasts"),
                          ("book", "books"),
                          ("motion", "motions"),
                          ("model3d", "models3d"),
                          ("sfx", "sfx")):
            if int(counts.get(key, 0) or 0) > best_n:
                best_kind = kind
                best_n = int(counts.get(key, 0) or 0)
        if best_kind and best_n >= 3:
            title = ""
            newest_ts = -1.0
            for it in items:
                if isinstance(it, dict) \
                        and it.get("kind") == best_kind:
                    ts = float(it.get("created_at", 0) or 0)
                    if ts > newest_ts:
                        newest_ts = ts
                        title = it.get("title") or ""
            sig["favorite"] = {"kind": best_kind,
                               "count": best_n,
                               "title": title}
    except Exception as e:
        logger.warning(f"Algorithm favorite signal failed: {e}")

    return sig
