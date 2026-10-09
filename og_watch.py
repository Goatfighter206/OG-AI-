"""Round 29: OG WATCH + LOGIN VAULT (browser-plan phase B4).

Two halves, one rule set from Brent (2026-10-09):

PERSISTENCE RULE. Log-ins in OG's browser persist BY DEFAULT.
The vault is built on Steel's real Profiles API (verified in
Steel's docs at build time): a session created with
persistProfile=True snapshots the browser's user-data directory
when it is released and returns a profileId; later sessions pass
that profileId and start with the same cookies/auth. OG's server
stores ONLY the Steel profile id per visitor — never a password
(visitors type their own log-ins via Take control, as always),
never the profile contents. A saved log-in ends when the visitor
says forget/log out (per-site or "forget my logins", which DELETEs
the Steel profile), the site expires it, or it sits unused ~30
days (Steel itself auto-deletes profiles unused 30 days).

SHUTDOWN RULE. No browser is ever left running between checks.
Every watch check spins a Steel session up on the profile, reads,
and RELEASES it in full. Closing the browser is not logging out.

OG WATCH. "Watch Facebook Marketplace for <item> under $<max>
near <place>" stores a watch; an asyncio scheduler (the Round 18
poll-loop pattern) runs due watches read-only — navigate + read,
NOTHING else: this module contains no click/type/gate/send code
path at all, so the watch can never message anyone on its own
(asserted by the r29 suite at the source level). New listings and
price drops become stored alerts delivered at the TOP of the
visitor's next chat exchange (the Round 18 delivery seam — there
is no push channel). "Message the seller" from an alert re-enters
the Round 27 browser flow (session -> draft -> approval card ->
send) through the visitor's own words; this module never claims
those messages.

COST CONTROLS. Slots ride og_tiers kind "fbwatch" (free 0 /
standard 1 / pro 3 / blue 5 / blackout 10); cadence by tier
(~6h / ~3h / ~1h / ~30min); background minutes ride the SEPARATE
kind "watch_min" (never the interactive browser_min) — checks
stop when the daily watch_min budget is spent. Every check has a
hard budget (one Steel session capped at creation, a wall-clock
deadline inside). Kill switch: OG_WATCH_ENABLED=false stops the
scheduler and watch creation cold; the vault rides the browser's
own switch (it is a browser feature).

CHECKPOINT HONESTY. If Facebook walls a check (log-in page or an
identity checkpoint), the watch pauses ITSELF and the visitor is
told to log in again in the browser. OG never solves identity
checkpoints, never enters credentials, never retries around a
wall.

GATING. watch_enabled() = the browser is live (OG_BROWSER_ENABLED
+ Steel key) AND OG_WATCH_ENABLED is not set false — the watch
needs no credentials of its own beyond the already-live browser
(the og_video capability-gate convention). While the browser is
dark, /watch/status answers {"enabled": false} and watch claims
answer with the browser's own not-switched-on text.

STATE. Own durable store, the Round 18 pattern: Postgres table
og_watch_data when OG_MEMORY_DB_URL is set, else a JSON file.
Per-visitor blobs: 'watches' (each carrying its seen-store),
'alerts' (matches + notices, delivered-flagged), 'vault',
'minusage'. Persona files are never touched.
"""

import asyncio
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

import og_browser as B

logger = logging.getLogger(__name__)

# --- Config -------------------------------------------------------------------

TICK_SECONDS = max(15, int(os.getenv("OG_WATCH_TICK_SECONDS", "60")))
CHECK_BUDGET_S = 110          # wall-clock deadline inside one visitor run
SESSION_BUDGET_MIN = 3        # Steel creation cap for a check session
SEEN_CAP = 300                # seen listing ids kept per watch
SITE_IDLE_DAYS = 30           # Brent's house rule for unused log-ins

CADENCE_MIN = {"standard": 360, "pro": 180, "blue": 60, "blackout": 30}

WATCH_STORE_FILE = "watch_store.json"
_watch_lock = threading.Lock()

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_deps: Dict = {}


def bind_app(deps):
    _deps.update(deps)


def _pro_url() -> str:
    base = os.getenv("OG_PUBLIC_URL", "https://og-ai-service.onrender.com")
    return base.rstrip("/") + "/pro"


# --- Switches -------------------------------------------------------------------


def watch_enabled() -> bool:
    """The watch rides the live browser; OG_WATCH_ENABLED=false is
    the kill switch (any other value, or unset, leaves it on)."""
    if not B.enabled():
        return False
    return os.environ.get(
        "OG_WATCH_ENABLED", "true").strip().lower() not in (
            "0", "false", "no", "off")


# --- Durable store (Round 18 pattern) -------------------------------------------


def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_watch_data ("
            "uid TEXT, kind TEXT, data JSONB, PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(WATCH_STORE_FILE):
        try:
            with open(WATCH_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Watch store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(WATCH_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Watch store save failed: {e}")


def _blob(uid: str, kind: str, default):
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_watch_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"Watch DB load failed, using file: {e}")
    with _watch_lock:
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
                        "INSERT INTO og_watch_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Watch DB save failed, using file: {e}")
    with _watch_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


def _all_uids() -> list:
    """Every visitor with any watch data (scheduler driver)."""
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT DISTINCT uid FROM og_watch_data")
                    return [r[0] for r in cur.fetchall()]
        except Exception as e:
            logger.warning(f"Watch DB uid scan failed, using file: {e}")
    with _watch_lock:
        store = _load_file_store()
    return list(store.keys())


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result(tag: str, title: str, body: str, href: str = "") -> List[Dict]:
    return [{"title": title, "body": f"[{tag}] " + body, "href": href}]


# --- Alerts + notices (one queue, delivered at the top of next chat) ------------


def _alerts(uid: str) -> list:
    a = _blob(uid, "alerts", [])
    return a if isinstance(a, list) else []


def _pending_alerts(uid: str) -> list:
    return [a for a in _alerts(uid) if not a.get("delivered")]


def _queue_alert(uid: str, text: str, kind: str = "notice",
                 watch_id: str = "") -> None:
    alerts = _alerts(uid)
    alerts.append({"id": uuid.uuid4().hex[:12], "ts": _now_iso(),
                   "delivered": False, "kind": kind,
                   "watch_id": watch_id, "text": text})
    _put_blob(uid, "alerts", alerts[-60:])


def _mark_delivered(uid: str, alert_ids: set):
    alerts = _alerts(uid)
    changed = False
    for a in alerts:
        if a.get("id") in alert_ids and not a.get("delivered"):
            a["delivered"] = True
            changed = True
    if changed:
        _put_blob(uid, "alerts", alerts)


# --- The vault -------------------------------------------------------------------


def _vault(uid: str) -> Dict:
    v = _blob(uid, "vault", None)
    if not isinstance(v, dict):
        v = {"profile_id": None, "sites": {}, "profile_gone": False,
             "created": _now_iso()}
    v.setdefault("profile_id", None)
    v.setdefault("sites", {})
    v.setdefault("profile_gone", False)
    return v


def _save_vault(uid: str, v: Dict) -> None:
    _put_blob(uid, "vault", v)


def _live_sites(v: Dict):
    """(live, dropped): sites whose last confirmed log-in is inside
    the 30-day house rule. Dropped ones are removed by the caller
    when it saves — Steel deletes the whole profile at 30 days
    unused anyway; this is the per-site bookkeeping on top."""
    cutoff = time.time() - SITE_IDLE_DAYS * 86400
    live, dropped = {}, []
    for host, info in (v.get("sites") or {}).items():
        last = float((info or {}).get("last_ts") or 0)
        if last and last < cutoff:
            dropped.append(host)
        else:
            live[host] = info
    return live, dropped


def _site_info() -> Dict:
    return {"first": _now_iso(), "last": _now_iso(),
            "last_ts": time.time()}


def _hook_get_profile(uid: str) -> Optional[str]:
    return _vault(uid).get("profile_id") or None


def _hook_note_profile(uid: str, profile_id: str) -> None:
    v = _vault(uid)
    if v.get("profile_id") != profile_id or v.get("profile_gone"):
        v["profile_id"] = profile_id
        v["profile_gone"] = False
        _save_vault(uid, v)


def _hook_profile_gone(uid: str) -> None:
    v = _vault(uid)
    if not v.get("profile_id") and not v.get("sites"):
        return
    v["profile_id"] = None
    v["sites"] = {}
    v["profile_gone"] = True
    _save_vault(uid, v)
    watches = _watches(uid)
    changed = False
    for w in watches:
        if w.get("login_notice_sent"):
            w["login_notice_sent"] = False
            changed = True
    if changed:
        _put_blob(uid, "watches", watches)
    _queue_alert(
        uid,
        "Heads-up: your saved browser profile expired, so the "
        "log-ins saved in it were dropped. Log into a site once "
        "more in my browser and I'll keep it saved again from "
        "there — nothing else changed.")


def _hook_note_site(uid: str, host: str) -> None:
    """A log-in wall cleared for host in a live session: record the
    kept log-in. First sighting queues the stated-once notice and
    switches any waiting/paused Marketplace watches back on."""
    v = _vault(uid)
    live, _dropped = _live_sites(v)
    is_new = host not in live
    info = live.get(host) or _site_info()
    info["last"] = _now_iso()
    info["last_ts"] = time.time()
    live[host] = info
    v["sites"] = live
    _save_vault(uid, v)
    if not is_new:
        return
    watches = _watches(uid)
    woke = 0
    for w in watches:
        if host == "facebook.com" and w.get("status") in (
                "waiting_login", "paused_checkpoint"):
            w["status"] = "active"
            w["next_due"] = time.time()
            woke += 1
    if woke:
        _put_blob(uid, "watches", watches)
    text = (f"You're logged into {host} — I've got it saved now, so "
            "you won't have to sign in again next time. It stays "
            "saved until you say 'forget my logins', log out of "
            f"{host}, or it sits unused about 30 days. Closing the "
            "browser doesn't log you out.")
    if woke:
        text += (" Your Marketplace watch is ON now — the first "
                 "check runs within about a minute.")
    _queue_alert(uid, text)


def _hook_vault_summary(uid: str) -> str:
    v = _vault(uid)
    live, _dropped = _live_sites(v)
    if live:
        hosts = ", ".join(sorted(live.keys()))
        return (f"Your saved log-in(s) — {hosts} — stay in your "
                "vault, so next time opens already logged in. "
                "Closing the browser is NOT logging out; say "
                "'forget my logins' any time to wipe them.")
    if v.get("profile_id"):
        return ("Your browser profile is saved for next time — "
                "log-ins you make in it get kept until you say "
                "'forget my logins'.")
    return "No log-ins are saved right now."


def ensure_hooks() -> None:
    B.register_profile_hooks(get=_hook_get_profile,
                             note=_hook_note_profile,
                             gone=_hook_profile_gone,
                             site=_hook_note_site,
                             summary=_hook_vault_summary)


def _forget_all(uid: str) -> List[Dict]:
    """'Forget my logins': DELETE the Steel profile, drop the
    vault record, stop every watch (they depended on the log-ins).
    Honest about a failed Steel delete — never claim Steel-side
    deletion that did not happen."""
    v = _vault(uid)
    pid = v.get("profile_id")
    watches = _watches(uid)
    n = len(watches)
    steel_state = "none"
    if pid:
        try:
            B._steel_api("DELETE", f"/profiles/{pid}")
            steel_state = "deleted"
        except Exception as e:
            logger.warning(f"Steel profile delete failed: {e}")
            steel_state = "failed"
    _save_vault(uid, {"profile_id": None, "sites": {},
                      "profile_gone": False, "created": _now_iso()})
    if n:
        _put_blob(uid, "watches", [])
    watch_line = (f" I also stopped all {n} watch(es) — they "
                  "depended on those log-ins.") if n else ""
    if steel_state == "deleted":
        body = ("Done — your saved browser profile is deleted at "
                "Steel and every log-in in it is gone." + watch_line
                + " The next browser session starts completely "
                "clean, and nothing can use those log-ins again.")
    elif steel_state == "failed":
        body = ("I dropped the profile from my records and forgot "
                "every saved log-in here." + watch_line + " One "
                "honest catch: Steel's delete call failed, so the "
                "profile may still exist on Steel's side — nothing "
                "here can reach it anymore, and Steel auto-deletes "
                "profiles unused for 30 days.")
    else:
        body = ("You had no saved log-ins to forget — the vault "
                "was already empty." + watch_line)
    return _result("OG WATCH", "OG Watch — log-ins forgotten", body)


def _forget_site(uid: str, host: str) -> List[Dict]:
    """Per-site log-out: open ONE short session on the visitor's
    own profile, clear that origin's cookies + site storage over
    CDP, release. No clicks on the site, nothing typed. If the
    surgical clear fails, say so and offer the forget-all wipe
    instead of pretending."""
    v = _vault(uid)
    live, _dropped = _live_sites(v)
    pid = v.get("profile_id")
    if host not in live or not pid:
        return _result(
            "OG WATCH", "OG Watch — not logged in there",
            f"You're not logged into {host} in my browser — "
            "there's nothing to drop for that site.")
    ok = False
    sess = None
    try:
        sess = B._steel_create_session(2, profile_id=pid,
                                       persist=True)
        rec = {"steel_id": sess["id"], "ws": sess["websocketUrl"]}
        cdp = B._cdp_connect(rec)
        try:
            B._attach_page(cdp)
            cdp.call("Network.enable")
            cdp.call("Page.enable")
            cdp.call("Page.navigate", {"url": f"https://{host}/"})
            B._wait_ready(cdp)
            got = cdp.call("Network.getAllCookies")
            for ck in got.get("cookies") or []:
                dom = str(ck.get("domain") or "").lstrip(".")
                if dom == host or dom.endswith("." + host):
                    cdp.call("Network.deleteCookies", {
                        "name": ck.get("name", ""),
                        "domain": ck.get("domain", ""),
                        "path": ck.get("path", "/")})
            cdp.call("Storage.clearDataForOrigin", {
                "origin": f"https://{host}", "storageTypes": "all"})
            ok = True
        finally:
            cdp.close()
    except Exception as e:
        logger.warning(f"Per-site logout failed for {host}: {e}")
        ok = False
    finally:
        if sess:
            try:
                B._steel_release(sess["id"])
            except Exception:
                pass
    if not ok:
        return _result(
            "OG WATCH", "OG Watch — couldn't wipe just that one",
            f"I couldn't wipe just {host} cleanly — the session "
            "didn't cooperate. The sure way is 'forget my logins', "
            "which deletes the whole saved profile and every "
            "log-in in it. Say the word and I'll do that instead.")
    live.pop(host, None)
    v["sites"] = live
    _save_vault(uid, v)
    extra = ""
    if host == "facebook.com":
        watches = _watches(uid)
        flipped = 0
        for w in watches:
            if w.get("status") == "active":
                w["status"] = "waiting_login"
                w["login_notice_sent"] = False
                flipped += 1
        if flipped:
            _put_blob(uid, "watches", watches)
            extra = (" Your Marketplace watches are paused until "
                     "you log into Facebook again — they need that "
                     "log-in to check.")
    return _result(
        "OG WATCH", "OG Watch — logged out of one site",
        f"Done — you're logged out of {host} in my browser and "
        f"its saved log-in is wiped. Your other saved log-ins are "
        f"untouched.{extra}")


def _logins_text(uid: str) -> List[Dict]:
    v = _vault(uid)
    live, dropped = _live_sites(v)
    if dropped:
        v["sites"] = live
        _save_vault(uid, v)
    lines = []
    if live:
        rows = []
        for host in sorted(live.keys()):
            info = live[host] or {}
            since = str(info.get("first") or "")[:10]
            last = str(info.get("last") or "")[:10]
            rows.append(f"{host} (saved since {since}, last "
                        f"confirmed {last})")
        lines.append("You're logged into: " + "; ".join(rows) + ".")
    else:
        lines.append("No saved log-ins right now.")
    if dropped:
        lines.append("Dropped for sitting unused 30 days: "
                     + ", ".join(sorted(dropped)) + ".")
    lines.append(
        "Those log-ins live in your saved browser profile (held "
        "by Steel — I keep only its ID, never a password). Say "
        "'log me out of <site>' to drop one, or 'forget my logins' "
        "to wipe them all at once.")
    return _result("OG WATCH", "OG Watch — your saved log-ins",
                   " ".join(lines))


# --- Watches + background-minute budget ------------------------------------------


def _watches(uid: str) -> list:
    w = _blob(uid, "watches", [])
    return w if isinstance(w, list) else []


def _minutes_used(uid: str) -> float:
    e = _blob(uid, "minusage", None)
    if not isinstance(e, dict) or e.get("date") != _today():
        return 0.0
    return float(e.get("minutes") or 0)


def _add_minutes(uid: str, minutes: float) -> None:
    if minutes <= 0:
        return
    e = _blob(uid, "minusage", None)
    if not isinstance(e, dict) or e.get("date") != _today():
        e = {"date": _today(), "minutes": 0.0}
    e["minutes"] = round(float(e.get("minutes") or 0) + minutes, 2)
    _put_blob(uid, "minusage", e)


def _cadence_min(tier: str) -> int:
    return int(CADENCE_MIN.get(tier, 360))


def _search_url(item: str) -> str:
    return ("https://www.facebook.com/marketplace/search/?query="
            + urllib.parse.quote_plus(item))


def _criteria_text(w: Dict) -> str:
    out = f"'{w.get('item')}'"
    if w.get("max_price"):
        out += f" under ${_fmt_price(w['max_price'])}"
    if w.get("place"):
        out += f" near {w['place']}"
    return out


def _fmt_price(value) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "$?"
    if f == int(f):
        return f"${int(f):,}"
    return f"${f:,.2f}"


def _create_watch(uid: str, tier: str, item: str,
                  max_price: Optional[float],
                  place: str) -> List[Dict]:
    if not watch_enabled():
        if not B.enabled():
            return B._dark_text()
        return _result(
            "OG WATCH", "OG Watch — switched off",
            "The Watch is switched off at the source right now — "
            "I can still browse with you live, but background "
            "checks aren't running. Nothing was set up.")
    import og_tiers as _tiers
    slots = int(_tiers.cap(tier, "fbwatch"))
    if slots <= 0:
        return _result(
            "OG WATCH", "OG Watch — paid plans",
            "Marketplace watches ride on the paid plans: Standard "
            "carries 1 watch (checked about every 6 hours), Pro 3, "
            f"Blue 5, Blackout 10. See {_pro_url()} — on Free I "
            "can still shop Marketplace WITH you live in the "
            "browser, I just can't keep checking while you're away.")
    watches = _watches(uid)
    for w in watches:
        if (str(w.get("item") or "").lower() == item.lower()
                and w.get("max_price") == max_price
                and str(w.get("place") or "").lower() == place.lower()):
            return _result(
                "OG WATCH", "OG Watch — already on it",
                f"Already watching {_criteria_text(w)} for you — "
                f"checks about every {_cadence_min(tier)} minutes, "
                "matches land at the top of your next chat.")
    if len(watches) >= slots:
        return _result(
            "OG WATCH", "OG Watch — slots full",
            f"You're using all {slots} watch slot(s) on your "
            f"{tier} plan. Stop one first ('stop watching <item>') "
            f"or upgrade for more: {_pro_url()}")
    v = _vault(uid)
    live, _d = _live_sites(v)
    has_fb = "facebook.com" in live
    w = {"id": uuid.uuid4().hex[:12], "item": item,
         "max_price": max_price, "place": place,
         "url": _search_url(item), "tier": tier,
         "cadence_min": _cadence_min(tier), "created": _now_iso(),
         "last_check": None, "next_due": time.time(),
         "status": "active" if has_fb else "waiting_login",
         "seen": {}, "checks": 0, "login_notice_sent": False}
    watches.append(w)
    _put_blob(uid, "watches", watches)
    crit = _criteria_text(w)
    if has_fb:
        body = (f"Watching {crit} on Facebook Marketplace — first "
                f"check within about a minute, then about every "
                f"{w['cadence_min']} minutes. New listings and "
                "price drops land at the top of your next chat "
                "with the title, price, town, and link. I never "
                "message a seller on my own — if you want one "
                "messaged, you approve the exact words first.")
    else:
        body = (f"Watch is set for {crit} on Facebook Marketplace, "
                "but it needs your Facebook log-in first — once. "
                "Say 'open Facebook', tap Take control in the "
                "panel, log in yourself (your password never "
                "touches me), and say 'hand back'. I'll keep that "
                "log-in saved, the watch switches on, and the "
                "first check runs within about a minute. After "
                "that I check on my own — about every "
                f"{w['cadence_min']} minutes — and matches land "
                "at the top of your next chat.")
    return _result("OG WATCH", "OG Watch — watching", body)


def _list_watches(uid: str, tier: str) -> List[Dict]:
    import og_tiers as _tiers
    watches = _watches(uid)
    if not watches:
        return _result(
            "OG WATCH", "OG Watch — nothing watched",
            "I'm not watching anything for you right now. Say "
            "'watch Facebook Marketplace for <item> under $<max> "
            "near <place>' and I'll keep an eye on it while "
            "you're away.")
    pending = _pending_alerts(uid)
    by_watch: Dict[str, int] = {}
    for a in pending:
        if a.get("kind") in ("match", "drop"):
            by_watch[a.get("watch_id") or ""] = \
                by_watch.get(a.get("watch_id") or "", 0) + 1
    status_words = {
        "active": "watching",
        "waiting_login": "WAITING for your Facebook log-in",
        "paused_checkpoint": "PAUSED — Facebook wants a fresh "
                             "log-in",
    }
    rows = []
    for w in watches:
        last = w.get("last_check")
        when = ("never yet" if not last else
                str(last)[:16].replace("T", " ") + " UTC")
        n = by_watch.get(w.get("id") or "", 0)
        rows.append(
            f"• {_criteria_text(w)} — "
            f"{status_words.get(w.get('status'), w.get('status'))}, "
            f"checks about every {w.get('cadence_min')} min, "
            f"last check {when}"
            + (f", {n} match(es) waiting for you" if n else ""))
    used = _minutes_used(uid)
    cap = int(_tiers.cap(tier, "watch_min"))
    rows.append(
        f"Background check time today: {used:g} of {cap} minutes "
        f"on your {tier} plan (separate from your live browser "
        "minutes — checks stop when that budget is spent).")
    return _result("OG WATCH", "OG Watch — your watches",
                   "\n".join(rows))


def _stop_watch(uid: str, words: str) -> List[Dict]:
    watches = _watches(uid)
    if not watches:
        return _result("OG WATCH", "OG Watch — nothing to stop",
                       "You're not watching anything right now.")
    want = " ".join(str(words or "").lower().split())
    hits = [w for w in watches
            if want and want in str(w.get("item") or "").lower()]
    if len(hits) != 1:
        names = "; ".join(_criteria_text(w) for w in watches)
        return _result(
            "OG WATCH", "OG Watch — which one?",
            f"Which one? I've got these watches: {names}. Say "
            "'stop watching <item>' with the item name, or 'stop "
            "all watches'. Nothing was stopped.")
    w = hits[0]
    watches = [x for x in watches if x.get("id") != w.get("id")]
    _put_blob(uid, "watches", watches)
    return _result(
        "OG WATCH", "OG Watch — stopped",
        f"Stopped watching {_criteria_text(w)}. Its seen-list is "
        "gone too — set it up again any time and it starts fresh.")


def _stop_all(uid: str) -> List[Dict]:
    watches = _watches(uid)
    if not watches:
        return _result("OG WATCH", "OG Watch — nothing to stop",
                       "You're not watching anything right now.")
    _put_blob(uid, "watches", [])
    return _result(
        "OG WATCH", "OG Watch — all stopped",
        f"Stopped all {len(watches)} watch(es). Your saved "
        "log-ins are untouched — say 'forget my logins' if you "
        "want those wiped too.")


def _resume_watches(uid: str) -> List[Dict]:
    v = _vault(uid)
    live, _d = _live_sites(v)
    has_fb = "facebook.com" in live
    watches = _watches(uid)
    changed = 0
    for w in watches:
        if w.get("status") == "paused_checkpoint":
            if has_fb:
                w["status"] = "active"
            else:
                w["status"] = "waiting_login"
                w["login_notice_sent"] = False
            w["next_due"] = time.time()
            changed += 1
        elif w.get("status") == "waiting_login" and has_fb:
            w["status"] = "active"
            w["next_due"] = time.time()
            changed += 1
    if changed:
        _put_blob(uid, "watches", watches)
    if not watches:
        return _result("OG WATCH", "OG Watch — nothing to resume",
                       "You're not watching anything right now.")
    if not has_fb:
        return _result(
            "OG WATCH", "OG Watch — needs your log-in first",
            "Your watches are queued, but they can't check until "
            "you're logged into Facebook in my browser: say 'open "
            "Facebook', Take control, log in, hand back — then "
            "they start on their own.")
    return _result(
        "OG WATCH", "OG Watch — resumed",
        f"{changed or len(watches)} watch(es) live again — next "
        "check within about a minute.")


# --- The check itself: read-only by construction ---------------------------------

_ITEM_HREF_RE = re.compile(r"/marketplace/item/(\d+)")
_PRICE_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)")
_TOWN_RE = re.compile(r"([A-Za-z][A-Za-z .]+,\s?[A-Z]{2})\s*$")
_CHECKPOINT_MARKERS = ("checkpoint", "confirm your identity",
                       "we need to verify", "security check",
                       "verify your identity", "unusual activity")


def _parse_listings(snap: Dict) -> Dict:
    """Listing cards out of a Marketplace search snapshot: anchors
    whose href carries /marketplace/item/<id>; title + price come
    from the anchor's own text. Nothing else on the page is data
    we act on."""
    out: Dict[str, Dict] = {}
    for el in snap.get("elements") or []:
        href = str(el.get("href") or "")
        m = _ITEM_HREF_RE.search(href)
        if not m:
            continue
        lid = m.group(1)
        if lid in out:
            continue
        label = " ".join(str(el.get("label") or "").split())
        price = None
        pm = _PRICE_RE.search(label)
        if pm:
            try:
                price = float(pm.group(1).replace(",", ""))
            except ValueError:
                price = None
        title = _PRICE_RE.sub("", label).strip(" ·-|") or \
            "Marketplace listing"
        town = ""
        tm = _TOWN_RE.search(label)
        if tm:
            town = tm.group(1).strip()
        out[lid] = {"id": lid, "title": title[:90],
                    "price": price, "town": town,
                    "url": "https://www.facebook.com/marketplace/"
                           f"item/{lid}/"}
    return out


def _relevant(item: str, title: str) -> bool:
    toks = [t for t in re.split(r"[^a-z0-9]+", item.lower())
            if len(t) >= 3]
    low = title.lower()
    if not toks:
        return True
    return all(t in low for t in toks)


def _is_checkpoint(snap: Dict) -> bool:
    url = str(snap.get("url") or "").lower()
    if "/checkpoint" in url:
        return True
    text = (str(snap.get("title") or "") + " "
            + str(snap.get("text") or "")).lower()
    return any(m in text for m in _CHECKPOINT_MARKERS)


def _diff_watch(w: Dict, listings: Dict, alerts: list) -> None:
    """Diff one search result against the watch's seen-store and
    append match / price-drop alerts. Seen is recorded for every
    listing (matching or not) so a listing that later drops INTO
    range alerts then — and each drop alerts once."""
    seen = w.get("seen") or {}
    max_price = w.get("max_price")
    place = str(w.get("place") or "").lower()
    for lid, lst in listings.items():
        price = lst.get("price")
        prev = seen.get(lid)
        entry = {"price": price,
                 "alerted": (prev or {}).get("alerted"),
                 "title": lst.get("title", "")}
        in_range = (max_price is None or price is None
                    or price <= float(max_price))
        place_ok = True
        if place and lst.get("town"):
            place_ok = place in lst["town"].lower()
        match = in_range and place_ok and _relevant(
            str(w.get("item") or ""), lst.get("title", ""))
        if prev is None:
            if match:
                alerts.append({
                    "id": uuid.uuid4().hex[:12], "ts": _now_iso(),
                    "delivered": False, "kind": "match",
                    "watch_id": w.get("id"),
                    "text": _match_text(w, lst, drop_from=None)})
                entry["alerted"] = price
        else:
            if match and entry["alerted"] is not None \
                    and price is not None \
                    and price < float(entry["alerted"]):
                alerts.append({
                    "id": uuid.uuid4().hex[:12], "ts": _now_iso(),
                    "delivered": False, "kind": "drop",
                    "watch_id": w.get("id"),
                    "text": _match_text(w, lst,
                                        drop_from=entry["alerted"])})
                entry["alerted"] = price
            elif match and entry["alerted"] is None:
                alerts.append({
                    "id": uuid.uuid4().hex[:12], "ts": _now_iso(),
                    "delivered": False, "kind": "match",
                    "watch_id": w.get("id"),
                    "text": _match_text(w, lst, drop_from=None)})
                entry["alerted"] = price
        seen[lid] = entry
    # Keep the seen-store bounded: oldest sightings fall off.
    while len(seen) > SEEN_CAP:
        seen.pop(next(iter(seen)))
    w["seen"] = seen


def _match_text(w: Dict, lst: Dict, drop_from) -> str:
    price = (_fmt_price(lst["price"]) if lst.get("price") is not None
             else "price not shown")
    town = f" — {lst['town']}" if lst.get("town") else ""
    if drop_from is not None:
        head = (f"Price drop on a listing you're watching: "
                f"'{lst['title']}' was {_fmt_price(drop_from)}, "
                f"now {price}{town} — {lst['url']}")
    else:
        head = (f"Watch match for {_criteria_text(w)}: "
                f"'{lst['title']}' — {price}{town} — {lst['url']}")
    return (head + " If you want it messaged, say 'message the "
            "seller' and tell me what to say — I'll open it in the "
            "browser, draft the exact words, and show them to you "
            "for a YES before anything sends. The watch itself "
            "never messages anyone.")


def _check_one(rec: Dict, uid: str, w: Dict, alerts: list,
               has_fb_login: bool) -> None:
    """One watch, one search, read-only: navigate + read over the
    session's CDP (og_browser._drive with read-only actions only).
    Wall handling is the checkpoint contract: pause, tell the
    visitor, never attempt a log-in."""
    snap = B._drive(rec, {"do": "navigate", "url": w["url"]})
    listings = _parse_listings(snap)
    if not listings and not _is_checkpoint(snap) \
            and not B._login_wall(snap):
        time.sleep(2.5)  # Marketplace is a heavy SPA; one re-read
        snap = B._drive(rec, {"do": "read"})
        listings = _parse_listings(snap)
    if _is_checkpoint(snap) or B._login_wall(snap):
        if has_fb_login:
            if w.get("status") == "active":
                w["status"] = "paused_checkpoint"
                alerts.append({
                    "id": uuid.uuid4().hex[:12], "ts": _now_iso(),
                    "delivered": False, "kind": "notice",
                    "watch_id": w.get("id"),
                    "text": (f"Your watch for "
                             f"{_criteria_text(w)} is PAUSED — "
                             "Facebook is asking for a fresh log-in "
                             "or an identity check, and I never "
                             "answer those for you. Open Facebook "
                             "in my browser, Take control, log in, "
                             "hand back, then say 'resume my "
                             "watch' and it picks back up.")})
        else:
            w["status"] = "waiting_login"
            if not w.get("login_notice_sent"):
                w["login_notice_sent"] = True
                alerts.append({
                    "id": uuid.uuid4().hex[:12], "ts": _now_iso(),
                    "delivered": False, "kind": "notice",
                    "watch_id": w.get("id"),
                    "text": (f"Your watch for "
                             f"{_criteria_text(w)} is set, but "
                             "Facebook shows my browser a log-in "
                             "wall, so it can't check yet. Log into "
                             "Facebook once in my browser (Take "
                             "control — your password never touches "
                             "me) and it starts checking on its "
                             "own.")})
        return
    if has_fb_login:
        v = _vault(uid)
        live, _d = _live_sites(v)
        info = live.get("facebook.com")
        if info is not None:
            info["last"] = _now_iso()
            info["last_ts"] = time.time()
            v["sites"] = live
            _save_vault(uid, v)
    _diff_watch(w, listings, alerts)


def _run_visitor_checks(uid: str, due: list, watches: list) -> int:
    """Spin ONE session up on the visitor's profile, run every due
    watch read-only, release the session in full, charge the real
    elapsed minutes to watch_min. Returns checks completed."""
    v = _vault(uid)
    pid = v.get("profile_id")
    sess = None
    try:
        sess = B._steel_create_session(SESSION_BUDGET_MIN,
                                       profile_id=pid, persist=True)
    except B._SteelError as e:
        if pid:
            logger.warning(f"Watch check: stored profile refused: {e}")
            _hook_profile_gone(uid)
            try:
                sess = B._steel_create_session(SESSION_BUDGET_MIN,
                                               persist=True)
            except B._SteelError:
                sess = None
    if sess is None:
        logger.warning("Watch check: Steel handed no session")
        return 0
    new_pid = str(sess.get("profileId") or "")
    if new_pid:
        _hook_note_profile(uid, new_pid)
    live, _d = _live_sites(_vault(uid))
    has_fb = "facebook.com" in live
    rec = {"steel_id": sess["id"], "ws": sess["websocketUrl"]}
    alerts = _alerts(uid)
    t0 = time.time()
    done = 0
    try:
        for w in due:
            if time.time() - t0 > CHECK_BUDGET_S:
                logger.warning(
                    "Watch check budget hit; remaining watches "
                    "wait for the next cycle")
                break
            try:
                _check_one(rec, uid, w, alerts, has_fb)
            except Exception as e:
                logger.warning(f"Watch check failed for a watch: {e}")
            w["last_check"] = _now_iso()
            w["checks"] = int(w.get("checks") or 0) + 1
            done += 1
    finally:
        try:
            B._steel_release(sess["id"])
        except Exception:
            pass
    # Charge the real elapsed time, floored at 0.1 min — a session
    # spin-up itself is billable Steel time even when the reads
    # come back fast.
    _add_minutes(uid, max(0.1, round((time.time() - t0) / 60.0, 2)))
    _put_blob(uid, "watches", watches)
    _put_blob(uid, "alerts", alerts)
    return done


def run_due_checks(now: Optional[float] = None) -> int:
    """One scheduler cycle. Due watches are claimed ahead (their
    next_due moves forward BEFORE any session opens — two app
    workers can never double-run the same check), the daily
    watch_min budget gates every run, and a visitor's due watches
    share one spin-up. Returns the number of checks run."""
    if not watch_enabled():
        return 0
    now = time.time() if now is None else now
    import og_tiers as _tiers
    total = 0
    for uid in _all_uids():
        try:
            watches = _watches(uid)
            due = [w for w in watches
                   if w.get("status") in ("active", "waiting_login")
                   and float(w.get("next_due") or 0) <= now]
            if not due:
                continue
            for w in due:
                w["next_due"] = now + int(
                    w.get("cadence_min") or 360) * 60
            _put_blob(uid, "watches", watches)
            tier = str(due[0].get("tier") or "free")
            cap = int(_tiers.cap(tier, "watch_min"))
            if cap <= 0 or _minutes_used(uid) >= cap:
                continue  # budget spent: cadence slips, nothing runs
            total += _run_visitor_checks(uid, due, watches)
        except Exception as e:
            logger.warning(f"Watch cycle failed for a visitor: {e}")
    return total


# --- Chat claims ------------------------------------------------------------------

_FORGET_ALL_RE = re.compile(
    r"\bforget (?:all )?(?:my )?logins?\b"
    r"|\bforget everything (?:you|u) saved\b"
    r"|\blog me out of everything\b"
    r"|\bdelete my (?:saved )?logins?\b", re.I)
_FORGET_SITE_RE = re.compile(
    r"\b(?:log|sign) me out of ([a-z0-9&' .\-]+?)\s*[.!?]*$"
    r"|\bforget my ([a-z0-9&' .\-]+?) login\b"
    r"|\blog out of ([a-z0-9&' .\-]+?)\s*[.!?]*$", re.I)
_LOGINS_RE = re.compile(
    r"\bwhat am i logged into\b|\bwhere am i logged in\b"
    r"|\bmy (?:saved )?logins\b|\bwhat sites am i logged into\b"
    r"|\bam i logged in anywhere\b", re.I)
_CREATE_RES = (
    re.compile(r"\bwatch (?:facebook |fb )?marketplace for (.+)$",
               re.I),
    re.compile(r"\bwatch for (.+?) on (?:facebook |fb )?"
               r"marketplace\b(.*)$", re.I),
    re.compile(r"\bkeep an eye on (?:facebook |fb )?marketplace "
               r"for (.+)$", re.I),
)
_LIST_RE = re.compile(
    r"\bwhat are you watching\b|\bwatching for me\b"
    r"|\bmy marketplace watches\b|\bmy og watches\b", re.I)
_STOP_ALL_RE = re.compile(
    r"\bstop all (?:my |the )?watches\b"
    r"|\bstop watching everything\b"
    r"|\bcancel all (?:my )?watches\b", re.I)
_STOP_RE = re.compile(
    r"\bstop watching (?:for )?(.+?)\s*[.!?]*$"
    r"|\bstop the watch for (.+?)\s*[.!?]*$"
    r"|\bcancel (?:my |the )?watch for (.+?)\s*[.!?]*$", re.I)
_RESUME_RE = re.compile(r"\bresume (?:my |the )?watch", re.I)
_PRICE_TAIL_RE = re.compile(
    r"\b(?:under|max|below|less than)\s+\$?\s*([\d,]+(?:\.\d{1,2})?)",
    re.I)
_PLACE_TAIL_RE = re.compile(r"\bnear\s+(.+?)\s*$", re.I)


def _site_host(words: str) -> str:
    w = " ".join(str(words or "").lower().split()).strip(" .,!?")
    w = re.sub(r"^the\s+", "", w)
    if not w:
        return ""
    url = B._resolve_site_url(w)
    if url:
        return B._norm_host(urllib.parse.urlparse(url).hostname or "")
    tok = w.split()[0]
    cand = tok if "." in tok else tok + ".com"
    if B._host_allowed(cand):
        return B._norm_host(cand)
    return ""


def _parse_create(message: str) -> Optional[Dict]:
    if "alert me" in message.lower():
        return None  # price-alert phrasing belongs to og_monitor
    tail = None
    for rx in _CREATE_RES:
        m = rx.search(message)
        if m:
            tail = m.group(1)
            if m.lastindex and m.lastindex >= 2 and m.group(2):
                tail = tail + " " + m.group(2)
            break
    if tail is None:
        return None
    max_price = None
    pm = _PRICE_TAIL_RE.search(tail)
    if pm:
        try:
            max_price = float(pm.group(1).replace(",", ""))
        except ValueError:
            max_price = None
        tail = tail[:pm.start()] + tail[pm.end():]
    place = ""
    nm = _PLACE_TAIL_RE.search(tail)
    if nm:
        place = " ".join(nm.group(1).split()).strip(" .,")
        tail = tail[:nm.start()]
    item = " ".join(str(tail).split()).strip(" .,")
    item = re.sub(r"^(?:a|an|the)\s+", "", item, flags=re.I)
    if not item:
        return None
    return {"op": "create", "item": item, "max_price": max_price,
            "place": place}


def _claim_job(message: str, uid: str) -> Optional[Dict]:
    raw = str(message or "")
    low = raw.lower()
    if not uid:
        return None
    if _FORGET_ALL_RE.search(low):
        return {"op": "forget_all"}
    m = _FORGET_SITE_RE.search(raw)
    if m:
        words = next(g for g in m.groups() if g)
        host = _site_host(words)
        if host:
            return {"op": "forget_site", "host": host}
    if _LOGINS_RE.search(low):
        return {"op": "logins"}
    watches = _watches(uid)
    if _STOP_ALL_RE.search(low) and watches:
        return {"op": "stop_all"}
    if _RESUME_RE.search(low) and watches:
        return {"op": "resume"}
    m = _STOP_RE.search(raw)
    if m and watches:
        words = next(g for g in m.groups() if g)
        return {"op": "stop", "words": words}
    if _LIST_RE.search(low):
        return {"op": "list"}
    return _parse_create(raw)


def watch_results(job: Dict, uid: str, tier: str) -> List[Dict]:
    op = job.get("op")
    if op == "create":
        return _create_watch(uid, tier, job.get("item") or "",
                              job.get("max_price"),
                              job.get("place") or "")
    if op == "list":
        if not B.enabled():
            return B._dark_text()
        return _list_watches(uid, tier)
    if op == "stop":
        return _stop_watch(uid, job.get("words") or "")
    if op == "stop_all":
        return _stop_all(uid)
    if op == "resume":
        return _resume_watches(uid)
    if op == "logins":
        if not B.enabled():
            return B._dark_text()
        return _logins_text(uid)
    if op == "forget_all":
        if not B.enabled():
            return B._dark_text()
        return _forget_all(uid)
    if op == "forget_site":
        if not B.enabled():
            return B._dark_text()
        return _forget_site(uid, job.get("host") or "")
    return _result("OG WATCH", "OG Watch",
                   "That watch ask didn't parse — try 'watch "
                   "Facebook Marketplace for <item> under $<max> "
                   "near <place>'.")


# --- The seam (installed LAST by app.py) + alert delivery -------------------------

_pending: Dict = {"job": None, "alerts": []}


def install_watch_tools(agent_instance, get_uid, get_tier):
    """Wrap the agent's (already fully wrapped) detect_intent +
    web_search hooks so watch/vault asks run here and any stored
    watch alerts + notices are delivered at the TOP of the
    visitor's next exchange (the Round 18 seam). Persona files
    never touched."""
    ensure_hooks()
    if getattr(agent_instance, "_og_watch_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["job"] = None
        _pending["alerts"] = []
        try:
            uid = get_uid()
            if uid and B.enabled():
                _pending["alerts"] = _pending_alerts(uid)
                job = _claim_job(str(message), uid)
                if job:
                    _pending["job"] = job
                if job or _pending["alerts"]:
                    if isinstance(intent, dict):
                        intent["needs_code_generation"] = False
                        if not intent.get("needs_web_search"):
                            intent["needs_web_search"] = True
                            intent["search_query"] = str(message).strip()
        except Exception as e:
            logger.warning(f"Watch trigger check failed: {e}")
            _pending["job"] = None
            _pending["alerts"] = []
        return intent

    def search_wrapped(query, num_results=5):
        job = _pending.get("job")
        alerts = _pending.get("alerts") or []
        _pending["job"] = None
        _pending["alerts"] = []
        results = None
        if job:
            try:
                results = watch_results(job, get_uid(), get_tier())
            except Exception as e:
                logger.warning(f"Watch job failed: {e}")
                results = None
        if results is None:
            results = prev_search(query, num_results)
        if alerts and results:
            texts = "\n".join(f"- {a.get('text', '')}" for a in alerts)
            alert_result = {
                "title": "🔎 OG Watch — while you were away",
                "body": ("FIRST — before answering anything else — "
                         "deliver this from OG Watch, plainly and "
                         "in persona, with the exact titles, "
                         "prices, towns, and links as written, "
                         "THEN answer their message:\n" + texts),
                "href": ""}
            results = [alert_result] + list(results)
            try:
                _mark_delivered(
                    get_uid(), {a.get("id") for a in alerts})
            except Exception as e:
                logger.warning(f"Watch alert delivery mark failed: {e}")
        return results

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_watch_installed = True


# --- Routes + scheduler ------------------------------------------------------------

_loop_started = {"on": False}


def register_watch_routes(app):
    """Mount /watch/status + /watch/forget and start the check
    scheduler. The loop is failure-quiet: a bad cycle is logged
    and skipped, never raised into the app."""
    ensure_hooks()

    @app.get("/watch/status")
    async def watch_status(request: Request):
        if not B.enabled():
            return {"enabled": False}
        uid = ""
        try:
            uid = request.cookies.get("ogai_uid", "") or ""
        except Exception:
            uid = ""
        tier = "free"
        if uid and "tier_of" in _deps:
            try:
                tier = _deps["tier_of"](uid, request)
            except Exception:
                tier = "free"
        import og_tiers as _tiers
        out = {"enabled": True, "watch_enabled": watch_enabled(),
               "tier": tier,
               "slots_cap": int(_tiers.cap(tier, "fbwatch")),
               "slots_used": 0,
               "watch_min_cap": int(_tiers.cap(tier, "watch_min")),
               "watch_min_used": 0.0,
               "vault": {"profile": False, "sites": [],
                          "profile_gone": False},
               "watches": [], "pending_alerts": 0}
        if uid:
            watches = _watches(uid)
            out["slots_used"] = len(watches)
            out["watch_min_used"] = _minutes_used(uid)
            out["pending_alerts"] = len(_pending_alerts(uid))
            out["watches"] = [{
                "item": w.get("item"),
                "max_price": w.get("max_price"),
                "place": w.get("place"),
                "status": w.get("status"),
                "cadence_min": w.get("cadence_min"),
                "last_check": w.get("last_check"),
                "checks": w.get("checks", 0),
            } for w in watches]
            v = _vault(uid)
            live, _d = _live_sites(v)
            out["vault"] = {
                "profile": bool(v.get("profile_id")),
                "sites": sorted(live.keys()),
                "profile_gone": bool(v.get("profile_gone"))}
        return out

    @app.post("/watch/forget")
    async def watch_forget(request: Request):
        if not B.enabled():
            return JSONResponse({"error": "not found"},
                                status_code=404)
        uid = ""
        try:
            uid = request.cookies.get("ogai_uid", "") or ""
        except Exception:
            uid = ""
        if not uid:
            return JSONResponse({"ok": False,
                                 "error": "no visitor cookie"},
                                status_code=400)
        blocks = _forget_all(uid)
        return {"ok": True,
                "title": blocks[0].get("title", "") if blocks else "",
                "body": "\n".join(b.get("body", "") for b in blocks)}

    @app.on_event("startup")
    async def _start_watch_scheduler():
        if _loop_started["on"]:
            return
        _loop_started["on"] = True

        async def _loop():
            while True:
                await asyncio.sleep(TICK_SECONDS)
                try:
                    await asyncio.to_thread(run_due_checks)
                except Exception as e:
                    logger.warning(f"Watch scheduler cycle failed: {e}")

        asyncio.create_task(_loop())
