"""
OG AI monitoring pack (Round 18) — read-and-report tools that ride
the connects and OG's own stores. Three live pieces plus the digest:

1. CALORIE LOG (OG-native, no connect needed): "log 2 eggs and
   toast" / "I ate a Whopper combo" stores a per-visitor daily food
   log. Calorie/protein numbers are ESTIMATES — produced by a small
   estimator call on the same OpenAI key (strict JSON out), or taken
   verbatim when the visitor states them ("log 500 cal chicken").
   Every stored entry and every total is labeled an estimate.
   "What did I eat today" answers ONLY from the visitor's own log;
   "undo last" removes the newest entry. Per-tier daily entry cap
   (og_tiers kind "calorie") enforced by app.py's counters.
2. PRICE WATCH: "watch BTC" (default: a 10% move from the set
   price), "alert me if AAPL goes above 250", "watch ETH for a 10%
   move" — per-visitor watchlist priced by the same keyless feeds
   the Round 4 data pack uses (Coinbase Exchange stats for crypto,
   Nasdaq API with Yahoo fallback for stocks). A background asyncio
   loop (started at app startup; OG_ALERT_POLL_SECONDS, default
   300) evaluates watches; a fired watch fires ONCE. HONEST
   DELIVERY MODEL: OG has no push channel to a web visitor, so a
   fired alert is stored and delivered at the TOP of the visitor's
   next chat exchange, and "my alerts" reports state any time.
   Per-tier ACTIVE-watch cap (og_tiers kind "watch").
3. MY DIGEST: "give me my digest" assembles one grounded briefing
   from whatever the visitor has connected — email highlights
   (Round 12 Google hands, last 24 h), MyChart notification
   pointers surfaced FIRST as health notices (the emails are
   content-free by MyChart's design; OG never claims their content,
   and an appointment-looking notice prompts the Round 12
   calendar-add offer — nothing is added without the visitor's
   explicit yes in that flow), Twitch followed channels live now,
   newest uploads from YouTube subscriptions, recent Reddit
   activity. Unconnected sources are simply absent — never faked.
   A visitor with nothing connected gets the honest connect-first
   answer. One lookup unit per real digest.

Banking rides og_plaid.py (shipped dark). Per-visitor data is only
ever read with that visitor's own tokens; nothing is logged beyond
counts. Persona files are never touched.
"""

import asyncio
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# --- Config -------------------------------------------------------------------

POLL_SECONDS = max(30, int(os.getenv("OG_ALERT_POLL_SECONDS", "300")))
CAL_MODEL = os.getenv("OG_CALORIE_MODEL", "gpt-4o-mini")
MONITOR_STORE_FILE = "monitor_store.json"
_monitor_lock = threading.Lock()

MEMORY_DB_URL = os.getenv("OG_MEMORY_DB_URL", "").strip()
try:
    import psycopg
    from psycopg.types.json import Jsonb as _Jsonb
except Exception:  # psycopg missing — file backend
    psycopg = None
    _Jsonb = None

_CRYPTO = {"BTC", "ETH", "SOL", "DOGE", "XRP", "ADA", "LTC", "LINK",
           "MATIC", "DOT", "AVAX", "ATOM", "UNI", "NEAR", "APT"}


# --- Per-visitor store (calories + watches + alerts in one blob) ---------------

def _db_connect():
    conn = psycopg.connect(MEMORY_DB_URL, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS og_monitor_data ("
            "uid TEXT, kind TEXT, data JSONB, PRIMARY KEY (uid, kind))")
    conn.commit()
    return conn


def _load_file_store() -> Dict:
    if os.path.exists(MONITOR_STORE_FILE):
        try:
            with open(MONITOR_STORE_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as e:
            logger.warning(f"Monitor store load failed: {e}")
    return {}


def _save_file_store(store: Dict):
    try:
        with open(MONITOR_STORE_FILE, "w") as f:
            json.dump(store, f)
    except Exception as e:
        logger.warning(f"Monitor store save failed: {e}")


def _blob(uid: str, kind: str, default):
    """One visitor's blob of one kind ('calories'|'watches'|'alerts')."""
    if not uid:
        return default
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT data FROM og_monitor_data "
                        "WHERE uid=%s AND kind=%s", (uid, kind))
                    row = cur.fetchone()
            return row[0] if row else default
        except Exception as e:
            logger.warning(f"Monitor DB load failed, using file: {e}")
    with _monitor_lock:
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
                        "INSERT INTO og_monitor_data (uid, kind, data) "
                        "VALUES (%s, %s, %s) ON CONFLICT (uid, kind) "
                        "DO UPDATE SET data=EXCLUDED.data",
                        (uid, kind, _Jsonb(value)))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"Monitor DB save failed, using file: {e}")
    with _monitor_lock:
        store = _load_file_store()
        mine = store.get(uid) or {}
        mine[kind] = value
        store[uid] = mine
        _save_file_store(store)


def _all_uids() -> list:
    """Every visitor with any monitor data (poll loop driver)."""
    if MEMORY_DB_URL and psycopg is not None:
        try:
            with _db_connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT DISTINCT uid FROM og_monitor_data")
                    return [r[0] for r in cur.fetchall()]
        except Exception as e:
            logger.warning(f"Monitor DB uid scan failed, using file: {e}")
    with _monitor_lock:
        store = _load_file_store()
    return list(store.keys())


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Calorie log ----------------------------------------------------------------

def _cal_entries(uid: str, date: str = None) -> list:
    days = _blob(uid, "calories", {})
    if not isinstance(days, dict):
        return []
    return list(days.get(date or _today()) or [])


def _cal_add(uid: str, entry: Dict):
    days = _blob(uid, "calories", {})
    if not isinstance(days, dict):
        days = {}
    day = days.get(_today()) or []
    day.append(entry)
    days[_today()] = day
    _put_blob(uid, "calories", days)


def _cal_pop_last(uid: str) -> Optional[Dict]:
    """Remove and return the visitor's newest entry (any date)."""
    days = _blob(uid, "calories", {})
    if not isinstance(days, dict) or not days:
        return None
    best_date, best = None, None
    for date, entries in days.items():
        for e in entries or []:
            if best is None or str(e.get("ts", "")) > str(best.get("ts", "")):
                best, best_date = e, date
    if best is None:
        return None
    days[best_date] = [e for e in days[best_date] if e is not best]
    if not days[best_date]:
        del days[best_date]
    _put_blob(uid, "calories", days)
    return best


def _cal_totals(uid: str, date: str = None):
    entries = _cal_entries(uid, date)
    cal = sum(int(e.get("cal", 0)) for e in entries)
    pro = sum(float(e.get("protein", 0)) for e in entries)
    return entries, cal, pro


_EXPLICIT_CAL = re.compile(
    r"(\d{1,5}(?:\.\d+)?)\s*(?:kcal|calories|cal)\b", re.I)
_EXPLICIT_PRO = re.compile(
    r"(\d{1,4}(?:\.\d+)?)\s*g(?:rams)?\s+(?:of\s+)?protein", re.I)


def _estimate_food(food: str) -> Optional[Dict]:
    """Estimate calories + protein for a food description via one
    small strict-JSON call on the app's OpenAI key. None on any
    failure (no key, upstream down, unparseable) — the caller then
    asks the visitor for numbers instead of inventing any."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key or not food:
        return None
    import httpx
    try:
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": CAL_MODEL,
                    "messages": [
                        {"role": "system", "content":
                         "You estimate nutrition for a typical single "
                         "serving of the described food. Reply with "
                         "STRICT JSON only: {\"calories\": <int>, "
                         "\"protein_g\": <number>}. No other text."},
                        {"role": "user", "content": food[:300]},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2,
                })
        if resp.status_code != 200:
            logger.warning(
                f"Calorie estimator status: {resp.status_code}")
            return None
        data = resp.json()
        content = ((data.get("choices") or [{}])[0].get("message")
                   or {}).get("content", "")
        parsed = json.loads(content)
        cal = int(float(parsed.get("calories", 0)))
        pro = float(parsed.get("protein_g", 0))
        if cal <= 0 or cal > 10000:
            return None
        return {"cal": cal, "protein": max(0.0, min(pro, 500.0))}
    except Exception as e:
        logger.warning(f"Calorie estimator failed: {e}")
        return None


# --- Price feeds (Round 4's keyless endpoints, numeric) -------------------------

# Nasdaq/Yahoo wall non-browser user agents (proven live
# 2026-10-08: the Round 4 data pack, which sends this browser UA,
# quoted AAPL from the same host while an OG-AI UA got nothing) —
# so the quote fetches send the data pack's UA.
_FEED_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; "
                          "x64) AppleWebKit/537.36 (KHTML, like "
                          "Gecko) Chrome/124.0 Safari/537.36"}


def _fetch_json(url: str, params: Dict = None) -> Optional[Dict]:
    import httpx
    try:
        with httpx.Client(timeout=12) as client:
            resp = client.get(
                url, params=params or {}, headers=_FEED_UA)
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _crypto_price(symbol: str) -> Optional[float]:
    data = _fetch_json(
        f"https://api.exchange.coinbase.com/products/"
        f"{symbol}-USD/stats")
    if not data:
        return None
    try:
        return float(data.get("last"))
    except (TypeError, ValueError):
        return None


def _stock_price(symbol: str) -> Optional[float]:
    data = _fetch_json(
        "https://api.nasdaq.com/api/quote/"
        f"{symbol.replace('.', '-')}/info",
        params={"assetclass": "stocks"})
    primary = ((data or {}).get("data") or {}).get("primaryData") or {}
    raw = str(primary.get("lastSalePrice") or "").replace("$", "") \
        .replace(",", "").strip()
    try:
        price = float(raw)
        if price > 0:
            return price
    except ValueError:
        pass
    chart = _fetch_json(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}")
    try:
        result = ((chart or {}).get("chart") or {}).get("result") or []
        meta = (result[0] or {}).get("meta") or {}
        price = float(meta.get("regularMarketPrice"))
        return price if price > 0 else None
    except (TypeError, ValueError, IndexError):
        return None


def get_price(symbol: str, kind: str) -> Optional[float]:
    """Current USD price for one watch symbol, or None."""
    if kind == "crypto":
        return _crypto_price(symbol)
    return _stock_price(symbol)


def _fmt_price(p) -> str:
    try:
        p = float(p)
    except (TypeError, ValueError):
        return "?"
    if p >= 1000:
        return f"${p:,.2f}"
    if p >= 1:
        return f"${p:.2f}"
    return f"${p:.4f}"


# --- Watches + alerts -----------------------------------------------------------

def _watches(uid: str) -> list:
    w = _blob(uid, "watches", [])
    return list(w) if isinstance(w, list) else []


def _active_watches(uid: str) -> list:
    return [w for w in _watches(uid) if w.get("status") == "active"]


def _alerts(uid: str) -> list:
    a = _blob(uid, "alerts", [])
    return list(a) if isinstance(a, list) else []


def _pending_alerts(uid: str) -> list:
    return [a for a in _alerts(uid) if not a.get("delivered")]


def _mark_delivered(uid: str, alert_ids: set):
    alerts = _alerts(uid)
    changed = False
    for a in alerts:
        if a.get("id") in alert_ids and not a.get("delivered"):
            a["delivered"] = True
            changed = True
    if changed:
        _put_blob(uid, "alerts", alerts)


def _watch_terms(w: Dict) -> str:
    cond = w.get("cond") or {}
    if cond.get("type") == "threshold":
        arrow = "above" if cond.get("dir") == "above" else "below"
        return f"{arrow} {_fmt_price(cond.get('value'))}"
    return f"a {cond.get('value', 10):g}% move from {_fmt_price(w.get('base'))}"


def evaluate_all(price_fn=None) -> int:
    """One poll cycle: price every active watch (per-symbol cache),
    fire the ones whose condition is met — each fires ONCE and
    leaves a stored alert for in-chat delivery. Failure-quiet:
    a dead feed just skips its symbols. Returns alerts fired."""
    price_fn = price_fn or get_price
    fired = 0
    cache: Dict = {}
    for uid in _all_uids():
        watches = _watches(uid)
        if not any(w.get("status") == "active" for w in watches):
            continue
        alerts = _alerts(uid)
        changed = False
        for w in watches:
            if w.get("status") != "active":
                continue
            key = (w.get("kind"), w.get("symbol"))
            if key not in cache:
                try:
                    cache[key] = price_fn(w.get("symbol"), w.get("kind"))
                except Exception:
                    cache[key] = None
            price = cache[key]
            if not price:
                continue
            cond = w.get("cond") or {}
            hit = False
            if cond.get("type") == "threshold":
                if cond.get("dir") == "above":
                    hit = price >= float(cond.get("value", 0))
                else:
                    hit = price <= float(cond.get("value", 0))
            else:  # pct move from the set price
                base = float(w.get("base") or 0)
                if base > 0:
                    hit = abs(price - base) / base * 100.0 >= \
                        float(cond.get("value", 10))
            if hit:
                w["status"] = "fired"
                w["fired_price"] = price
                w["fired_at"] = _now_iso()
                alerts.append({
                    "id": uuid.uuid4().hex[:12],
                    "ts": _now_iso(),
                    "delivered": False,
                    "text": (f"{w.get('symbol')} hit your watch: "
                             f"now {_fmt_price(price)} "
                             f"({_watch_terms(w)}; set at "
                             f"{_fmt_price(w.get('base'))})."),
                })
                fired += 1
                changed = True
                # Notifications (optional layer): the fire
                # also lands in the visitor's notification
                # center (+ opt-in channels). Fail-safe.
                try:
                    import og_notify as _notify
                    _notify.record(
                        uid, "price_alert",
                        f"Price alert: {w.get('symbol')}",
                        alerts[-1]["text"])
                except Exception:
                    pass
        if changed:
            _put_blob(uid, "watches", watches)
            _put_blob(uid, "alerts", alerts)
    return fired


# --- Digest sources (raw helpers of the connect modules; no spends) -------------

def _digest_google(uid: str):
    """(mychart_lines, mail_lines) or None when Google hands mail
    isn't available for this visitor."""
    try:
        import og_google_hands as hands
        if not (hands.HANDS_ENABLED and hands._google_enabled()):
            return None
        entry = hands._live_entry(uid)
        if not entry or not hands._scope_ok(entry, "gmail"):
            return None
        token = entry.get("access_token", "")
        if not token:
            return None
    except Exception:
        return None
    mychart, mail = [], []
    try:
        notices = hands._gmail_list(
            token, "mychart newer_than:7d", 5) or []
        for m in notices:
            mychart.append(
                f"- {m.get('date', '')} — {m.get('subject', '')} "
                f"(from {m.get('from', '')})")
    except Exception:
        pass
    try:
        recent = hands._gmail_list(token, "newer_than:1d", 7) or []
        for m in recent:
            mail.append(
                f"- {m.get('from', '')} — \"{m.get('subject', '')}\" "
                f"({m.get('date', '')})")
    except Exception:
        pass
    return mychart, mail


def _digest_twitch(uid: str) -> Optional[list]:
    try:
        import og_twitch as tw
        entry = tw._live_connection(uid)
        if not entry:
            return None
        user_id = entry.get("twitch_id", "")
        token = entry.get("access_token", "")
        if not user_id or not token:
            return None
        status, data = tw._request(
            "GET", tw._API_BASE + "/streams/followed", token,
            params={"user_id": user_id, "first": 3})
        if status != 200:
            return None
        lines = []
        for it in tw._data_list(data)[:3]:
            name = it.get("user_name") or it.get("user_login")
            if not name:
                continue
            line = f"- {name} is LIVE"
            if it.get("title"):
                line += f": \"{it['title']}\""
            if it.get("game_name"):
                line += f" [{it['game_name']}]"
            lines.append(line)
        return lines
    except Exception:
        return None


def _digest_youtube(uid: str) -> Optional[list]:
    """Newest upload from each of up to 3 subscribed channels."""
    try:
        import og_youtube as yt
        entry = yt._live_connection(uid)
        if not entry:
            return None
        token = entry.get("access_token", "")
        if not token:
            return None
        status, data = yt._request(
            "GET", yt._API_BASE + "/subscriptions", token,
            params={"part": "snippet", "mine": "true", "maxResults": 3})
        if status != 200:
            return None
        lines = []
        for it in yt._items(data)[:3]:
            snip = it.get("snippet") or {}
            chan_id = ((snip.get("resourceId") or {}).get("channelId")
                       or "")
            chan_name = snip.get("title", "")
            if not chan_id:
                continue
            st2, ch = yt._request(
                "GET", yt._API_BASE + "/channels", token,
                params={"part": "contentDetails", "id": chan_id})
            if st2 != 200:
                continue
            ch_items = yt._items(ch)
            uploads = (((ch_items[0].get("contentDetails") or {})
                        .get("relatedPlaylists") or {}).get("uploads")
                       if ch_items else "")
            if not uploads:
                continue
            st3, pl = yt._request(
                "GET", yt._API_BASE + "/playlistItems", token,
                params={"part": "snippet", "playlistId": uploads,
                        "maxResults": 1})
            if st3 != 200:
                continue
            pl_items = yt._items(pl)
            if not pl_items:
                continue
            vid = pl_items[0].get("snippet") or {}
            title = vid.get("title", "")
            when = yt._fmt_date(vid.get("publishedAt", ""))
            if title:
                lines.append(
                    f"- {chan_name}: \"{title}\""
                    + (f" ({when})" if when else ""))
        return lines
    except Exception:
        return None


def _digest_reddit(uid: str) -> Optional[list]:
    try:
        import og_reddit as rd
        entry = rd._live_connection(uid)
        if not entry:
            return None
        who = entry.get("username") or entry.get("name") or ""
        token = entry.get("access_token", "")
        if not who or not token:
            return None
        status, data = rd._request(
            "GET", rd._API_BASE + f"/user/{who}/overview", token,
            params={"limit": 3, "sort": "new"})
        if status != 200:
            return None
        lines = []
        for kind, item in rd._children(data)[:3]:
            sub = item.get("subreddit", "")
            if kind == "t3" and item.get("title"):
                lines.append(f"- POST in r/{sub}: \"{item['title']}\"")
            elif kind == "t1":
                text = re.sub(r"\s+", " ",
                              str(item.get("body", ""))).strip()
                if text:
                    if len(text) > 120:
                        text = text[:117] + "..."
                    lines.append(f"- COMMENT in r/{sub}: \"{text}\"")
        return lines
    except Exception:
        return None


# --- Intent parsing ---------------------------------------------------------------

_DIGEST_RES = (
    r"\bmy digest\b", r"\bgive me my digest\b", r"\bwhat'?s new for me\b",
    r"\bcatch me up\b", r"\bmy (morning )?briefing\b",
    r"\bwhat'?s new with me\b",
)
_UNDO_RE = re.compile(
    r"\bundo\b|\bremove (the )?last\b|\bdelete (the )?last\b", re.I)
_TODAY_RES = (
    r"\bwhat did i eat\b", r"\bwhat have i eaten\b",
    r"\bmy calories\b", r"\bcalories today\b", r"\bcalorie count\b",
    r"\bmy food log\b", r"\bfood log\b", r"\bmy calorie log\b",
)
_LOG_RES = (
    re.compile(r"^\s*log\s*[:\-]?\s+(.+)$", re.I),
    re.compile(r"\bi (?:just )?ate\s+(.+)$", re.I),
    re.compile(r"\badd\s+(.+?)\s+to my (?:food|calorie) log\b", re.I),
)
_HAD_RE = re.compile(
    r"\bi had\s+(.+?)(?:\s+for\s+(?:breakfast|lunch|dinner|snack))?\s*$",
    re.I)
_HAD_REJECT = ("question", "dream", "feeling", "thought", "idea",
               "chance", "look", "problem", "bad", "good", "great",
               "rough", "weird", "fever", "cold", "test", "meeting",
               "call", "idea", "plan", "feeling")
_WATCHLIST_RES = (
    r"\bmy watch ?list\b", r"\bmy alerts\b", r"\bwhat am i watching\b",
    r"\bmy price alerts\b", r"\bmy watches\b",
)
_CANCEL_RE = re.compile(
    r"\b(?:stop watching|unwatch|cancel (?:my )?watch (?:on|for)?|"
    r"remove (?:my )?watch (?:on|for)?)\s*\$?([A-Za-z][A-Za-z0-9.\-]{0,9})\b",
    re.I)
_ALERT_IF_RE = re.compile(
    r"\balert me (?:if|when)\s+\$?([A-Za-z][A-Za-z0-9.\-]{0,9})\s+"
    r"(?:goes|drops|falls|rises|hits|crosses|moves)?\s*"
    r"(above|below|over|under|past|up|down)?\s*\$?"
    r"([\d,]+(?:\.\d+)?)?\s*(%)?", re.I)
_WATCH_RE = re.compile(
    r"\bwatch\s+\$?([A-Za-z][A-Za-z0-9.\-]{0,9})\b"
    r"(?:\s+for\s+(?:a\s+)?([\d.]+)\s*%\s*(?:move)?)?"
    r"(?:\s+(above|below|over|under)\s+\$?([\d,]+(?:\.\d+)?))?", re.I)


def _clean(text: str) -> str:
    return " " + re.sub(r"\s+", " ", str(text).lower()).strip() + " "


_SYMBOL_REJECT = {"THE", "IT", "IS", "IF", "WHEN", "PRICE", "STOCK",
                  "CRYPTO", "A", "OUT", "THIS", "THAT", "MY", "TV",
                  "ME", "YOU", "MOVIE", "GAME", "SHOW", "NEWS"}


def _symbol(raw: str):
    sym = str(raw or "").upper().strip().strip("$")
    if sym in _SYMBOL_REJECT:
        return None, None
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", sym):
        return None, None
    return sym, ("crypto" if sym in _CRYPTO else "stock")


def parse_monitor_intent(message: str) -> Optional[Dict]:
    """Parse one monitoring ask. Returns a job dict or None."""
    text = str(message)
    low = _clean(text)
    for pattern in _DIGEST_RES:
        if re.search(pattern, low):
            return {"kind": "digest"}
    if _UNDO_RE.search(low):
        return {"kind": "cal_undo"}
    for pattern in _TODAY_RES:
        if re.search(pattern, low):
            return {"kind": "cal_today"}
    m = _CANCEL_RE.search(text)
    if m:
        sym, kind = _symbol(m.group(1))
        if sym:
            return {"kind": "watch_cancel", "symbol": sym, "skind": kind}
    m = _ALERT_IF_RE.search(text)
    if m:
        sym, kind = _symbol(m.group(1))
        if sym:
            direction = (m.group(2) or "").lower()
            number = m.group(3)
            job = {"kind": "watch_set", "symbol": sym, "skind": kind}
            if number and m.group(4):  # e.g. "moves 10%"
                job["cond"] = {"type": "pct",
                               "value": float(number.replace(",", ""))}
            elif number:
                job["cond"] = {
                    "type": "threshold",
                    "dir": "below" if direction in
                    ("below", "under", "down") else "above",
                    "value": float(number.replace(",", ""))}
            else:
                job["cond"] = {"type": "pct", "value": 10.0}
            return job
    m = _WATCH_RE.search(text)
    if m:
        sym, kind = _symbol(m.group(1))
        if sym:
            job = {"kind": "watch_set", "symbol": sym, "skind": kind}
            if m.group(3) and m.group(4):
                job["cond"] = {
                    "type": "threshold",
                    "dir": "below" if m.group(3).lower() in
                    ("below", "under") else "above",
                    "value": float(m.group(4).replace(",", ""))}
            elif m.group(2):
                job["cond"] = {"type": "pct",
                               "value": float(m.group(2))}
            else:
                job["cond"] = {"type": "pct", "value": 10.0}
            return job
    for pattern in _WATCHLIST_RES:
        if re.search(pattern, low):
            return {"kind": "watch_list"}
    for rx in _LOG_RES:
        m = rx.search(text)
        if m:
            food = m.group(1).strip().strip(".")
            first = food.lower().split(" ")[0] if food else ""
            if first in ("in", "into", "on", "off", "out", "back"):
                continue  # "log in to my account" is not food
            if food and len(food) <= 300:
                return {"kind": "log_cal", "food": food,
                        "message": text}
    m = _HAD_RE.search(text)
    if m:
        food = m.group(1).strip()
        words = food.lower().split(" ")
        probe = [w for w in words if w not in ("a", "an", "the")]
        bad = any(w in _HAD_REJECT for w in probe[:2])
        if food and not bad and "?" not in food \
                and len(food) <= 300:
            return {"kind": "log_cal", "food": food, "message": text}
    return None


# --- Job runners ------------------------------------------------------------------

def _result(title: str, body: str) -> list:
    return [{"title": title, "body": body, "href": ""}]


def _log_cal(job, uid, consume_calorie, calorie_left) -> Optional[list]:
    food = job.get("food", "")
    message = job.get("message", food)
    if calorie_left() <= 0:
        body = ("The visitor tried to log food but has hit today's "
                "food-log entry cap for their plan. Tell them plainly "
                "in persona — the entry was NOT stored — and that the "
                "cap resets tomorrow (UTC).")
        return _result("🍔 Food log — daily cap reached", body)
    explicit_cal = _EXPLICIT_CAL.search(message)
    explicit_pro = _EXPLICIT_PRO.search(message)
    cal = int(float(explicit_cal.group(1))) if explicit_cal else None
    protein = float(explicit_pro.group(1)) if explicit_pro else None
    estimated = cal is None
    if cal is None or protein is None:
        est = _estimate_food(food)
        if est is None:
            if cal is None:
                body = ("The visitor asked to log this food: "
                        f"\"{food}\". OG could NOT estimate its "
                        "calories automatically right now, and nothing "
                        "was stored. Tell them plainly, in persona, "
                        "and ask them to give the numbers directly — "
                        "like 'log 500 cal chicken sandwich' — and "
                        "OG will store exactly those.")
                return _result("🍔 Food log — needs numbers", body)
            protein = 0.0
        else:
            if cal is None:
                cal = est["cal"]
            if protein is None:
                protein = est["protein"]
    entry = {"ts": _now_iso(), "name": food[:120], "cal": cal,
             "protein": round(float(protein or 0), 1),
             "estimated": bool(estimated)}
    _cal_add(uid, entry)
    consume_calorie(uid)
    entries, tcal, tpro = _cal_totals(uid)
    body = (f"The visitor logged food and it IS stored in their "
            f"personal calorie log: \"{entry['name']}\" — "
            f"{cal} calories, {entry['protein']:g}g protein"
            + (" (an ESTIMATE — say so)" if entry["estimated"]
               else " (their own stated numbers)")
            + f". Their totals for today are now {tcal} calories "
            f"and {tpro:g}g protein across {len(entries)} "
            "entries. Confirm it in persona with those exact "
            "numbers — do not change them or add foods.")
    return _result("🍔 Logged to the visitor's calorie log", body)


def _cal_today(uid) -> list:
    entries, tcal, tpro = _cal_totals(uid)
    if not entries:
        body = ("The visitor asked what they ate today. Their "
                "personal calorie log has NOTHING logged today. Tell "
                "them plainly in persona — do not invent foods — and "
                "remind them they can say 'log …' with what they ate.")
        return _result("🍔 The visitor's calorie log — empty today",
                       body)
    lines = [f"- {e.get('name', '')} — {int(e.get('cal', 0))} cal, "
             f"{float(e.get('protein', 0)):g}g protein"
             + (" (est.)" if e.get("estimated") else "")
             for e in entries]
    body = ("The visitor asked what they ate today. Answer ONLY "
            "from their own calorie log below — these are estimates "
            "unless marked as their stated numbers:\n\n"
            + "\n".join(lines)
            + f"\n\nTotals today: {tcal} calories, {tpro:g}g protein "
            f"across {len(entries)} entries.")
    return _result("🍔 The visitor's calorie log — today", body)


def _cal_undo(uid) -> list:
    popped = _cal_pop_last(uid)
    if not popped:
        body = ("The visitor asked to undo their last food entry, "
                "but their calorie log is EMPTY — nothing was "
                "removed. Tell them plainly in persona.")
        return _result("🍔 Food log — nothing to undo", body)
    entries, tcal, tpro = _cal_totals(uid)
    body = (f"The visitor's newest food entry WAS removed from "
            f"their calorie log: \"{popped.get('name', '')}\" "
            f"({int(popped.get('cal', 0))} cal). Today's remaining "
            f"totals: {tcal} calories, {tpro:g}g protein across "
            f"{len(entries)} entries. Confirm it in persona.")
    return _result("🍔 Food log — entry removed", body)


def _watch_set(job, uid, get_tier) -> Optional[list]:
    import og_tiers
    sym, skind = job["symbol"], job["skind"]
    cond = job.get("cond") or {"type": "pct", "value": 10.0}
    active = _active_watches(uid)
    cap = og_tiers.cap(get_tier(), "watch")
    if len(active) >= cap:
        body = (f"The visitor tried to add a price watch but "
                f"already has {len(active)} active watches — their "
                f"plan's cap is {cap}. The new watch was NOT set. "
                "Tell them plainly in persona and suggest removing "
                "one first ('stop watching X').")
        return _result("⏰ Price watch — cap reached", body)
    price = get_price(sym, skind)
    if price is None:
        body = (f"The visitor asked to watch {sym} but OG could "
                "not get a current price for it right now, so NO "
                "watch was set. Tell them plainly in persona — do "
                "not invent a price — and suggest checking the "
                "symbol and trying again.")
        return _result("⏰ Price watch — could not price", body)
    watch = {"id": uuid.uuid4().hex[:12], "symbol": sym,
             "kind": skind, "cond": cond, "base": price,
             "status": "active", "created": _now_iso()}
    watches = _watches(uid)
    watches.append(watch)
    _put_blob(uid, "watches", watches)
    terms = _watch_terms(watch)
    body = (f"The visitor's price watch IS set: {sym} "
            f"({skind}), currently {_fmt_price(price)}. OG will "
            f"alert them here in chat when it is {terms}. The "
            "alert is delivered at the top of their next chat "
            "after it fires (OG has no way to push to their phone "
            "from the web). Confirm it in persona with those exact "
            "numbers.")
    return _result("⏰ Price watch set", body)


def _watch_list(uid) -> list:
    active = _active_watches(uid)
    pending = _pending_alerts(uid)
    if not active and not pending:
        body = ("The visitor asked about their watchlist. They "
                "have NO active price watches and no pending "
                "alerts. Tell them plainly in persona and that they "
                "can say e.g. 'watch BTC' or 'alert me if AAPL goes "
                "above 250'.")
        return _result("⏰ The visitor's watchlist — empty", body)
    lines = []
    for w in active:
        lines.append(f"- {w.get('symbol')} ({w.get('kind')}): "
                     f"{_watch_terms(w)} (set at "
                     f"{_fmt_price(w.get('base'))})")
    for a in pending:
        lines.append(f"- ⏰ FIRED: {a.get('text', '')}")
    body = ("The visitor asked about their watchlist. Answer ONLY "
            "from this list — their actual watches:\n\n"
            + "\n".join(lines))
    return _result("⏰ The visitor's watchlist", body)


def _watch_cancel(job, uid) -> list:
    sym = job["symbol"]
    watches = _watches(uid)
    kept, removed = [], 0
    for w in watches:
        if w.get("symbol") == sym and w.get("status") == "active":
            removed += 1
            continue
        kept.append(w)
    if removed:
        _put_blob(uid, "watches", kept)
        body = (f"The visitor's active {sym} watch WAS removed "
                f"({removed} removed). Confirm it plainly, "
                "in persona.")
    else:
        body = (f"The visitor asked to stop watching {sym} but "
                "has NO active watch on it — nothing was removed. "
                "Tell them plainly in persona.")
    return _result("⏰ Price watch removed" if removed else
                   "⏰ No such watch", body)


def _digest(uid, consume_lookup) -> Optional[list]:
    google = _digest_google(uid)
    twitch = _digest_twitch(uid)
    youtube = _digest_youtube(uid)
    reddit = _digest_reddit(uid)
    if google is None and twitch is None and youtube is None \
            and reddit is None:
        body = ("The visitor asked for their digest but has NO "
                "accounts connected to OG, so there is nothing "
                "personal to brief them on. Tell them plainly, in "
                "persona — do not invent a digest — and explain "
                "that once they connect accounts (Google for email "
                "and health-portal notices, Twitch, YouTube, "
                "Reddit) from the menu, 'give me my digest' pulls "
                "it all together here.")
        return _result("🗞️ Digest — nothing connected", body)
    if not consume_lookup(uid):
        logger.info("Digest skipped: visitor at daily lookup cap")
        return None
    sections = []
    if google is not None:
        mychart, mail = google
        if mychart:
            offer = ""
            if any("appointment" in s.lower()
                   for s in mychart):
                offer = ("\nOne or more of these mention an "
                         "appointment: the visitor can read the "
                         "details inside MyChart, tell OG the date "
                         "and time, and OG will OFFER to add it to "
                         "their Google Calendar — nothing is added "
                         "automatically, and OG cannot see the "
                         "appointment details itself.")
            sections.append(
                "🏥 HEALTH NOTICES (MyChart — these emails are "
                "content-free pointers; the actual messages live "
                "inside the visitor's MyChart login, which OG "
                "cannot see):\n" + "\n".join(mychart) + offer)
        if mail:
            sections.append("📧 EMAIL — last 24 hours:\n"
                            + "\n".join(mail))
        elif not mychart:
            sections.append("📧 EMAIL: nothing new in the last "
                            "24 hours.")
    if twitch is not None:
        sections.append("💜 TWITCH — followed channels live now:\n"
                        + ("\n".join(twitch) if twitch
                           else "Nobody they follow is live."))
    if youtube is not None:
        sections.append("📺 YOUTUBE — newest from subscriptions:\n"
                        + ("\n".join(youtube) if youtube
                           else "No new uploads found."))
    if reddit is not None:
        sections.append("🟠 REDDIT — their recent activity:\n"
                        + ("\n".join(reddit) if reddit
                           else "No recent activity."))
    body = ("The visitor asked for their digest. Brief them in "
            "persona using ONLY the sections below — their actual "
            "connected-account data, pulled just now. Keep the "
            "health notices first. Do not invent anything beyond "
            "these lines:\n\n" + "\n\n".join(sections))
    return _result("🗞️ The visitor's digest", body)


def monitor_results(job, uid, consume_lookup, consume_calorie,
                    calorie_left, get_tier) -> Optional[list]:
    """Run one parsed monitoring job for this visitor."""
    if not job or not uid:
        return None
    kind = job.get("kind")
    try:
        if kind == "log_cal":
            return _log_cal(job, uid, consume_calorie, calorie_left)
        if kind == "cal_today":
            return _cal_today(uid)
        if kind == "cal_undo":
            return _cal_undo(uid)
        if kind == "watch_set":
            return _watch_set(job, uid, get_tier)
        if kind == "watch_list":
            return _watch_list(uid)
        if kind == "watch_cancel":
            return _watch_cancel(job, uid)
        if kind == "digest":
            return _digest(uid, consume_lookup)
    except Exception as e:
        logger.warning(f"Monitor job {kind} failed: {e}")
        return None
    return None


# ---------------------------------------------------------------------------
# The seam (installed LAST by app.py) + alert delivery
# ---------------------------------------------------------------------------

_pending = {"job": None, "alerts": []}


def install_monitor_tools(agent_instance, get_uid, consume_lookup,
                          consume_calorie, calorie_left, get_tier):
    """Wrap the agent's hooks so monitoring jobs run FIRST and any
    fired price alerts are delivered at the top of the visitor's
    next exchange. Alert delivery forces one search-hook pass whose
    first context block is the alert; the alert is marked delivered
    only when that block actually goes out."""
    if getattr(agent_instance, "_og_monitor_installed", False):
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
            if uid:
                _pending["alerts"] = _pending_alerts(uid)
                job = parse_monitor_intent(str(message))
                if job:
                    _pending["job"] = job
                if job or _pending["alerts"]:
                    if isinstance(intent, dict):
                        intent["needs_code_generation"] = False
                        if not intent.get("needs_web_search"):
                            intent["needs_web_search"] = True
                            intent["search_query"] = \
                                str(message).strip()
        except Exception as e:
            logger.warning(f"Monitor trigger check failed: {e}")
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
                results = monitor_results(
                    job, get_uid(), consume_lookup, consume_calorie,
                    calorie_left, get_tier)
            except Exception as e:
                logger.warning(f"Monitor job failed: {e}")
                results = None
        if results is None:
            results = prev_search(query, num_results)
        if alerts and results:
            texts = "\n".join(f"- {a.get('text', '')}" for a in alerts)
            alert_result = {
                "title": "⏰ Price alert for the visitor",
                "body": ("FIRST — before answering anything else — "
                         "tell the visitor this price alert fired "
                         "while they were away (deliver it plainly, "
                         "in persona, with these exact numbers), "
                         "THEN answer their message:\n" + texts),
                "href": ""}
            results = [alert_result] + list(results)
            try:
                _mark_delivered(
                    get_uid(), {a.get("id") for a in alerts})
            except Exception as e:
                logger.warning(f"Alert delivery mark failed: {e}")
        return results

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_monitor_installed = True


# --- Background poll loop (registered on the FastAPI app) ------------------------

_loop_started = {"on": False}


def register_monitor_routes(app):
    """Attach the price-watch poll loop to app startup. The loop
    is failure-quiet: a dead feed or a bad cycle is logged and
    skipped, never raised into the app."""

    @app.on_event("startup")
    async def _start_watch_loop():
        if _loop_started["on"]:
            return
        _loop_started["on"] = True

        async def _loop():
            while True:
                await asyncio.sleep(POLL_SECONDS)
                try:
                    await asyncio.to_thread(evaluate_all)
                except Exception as e:
                    logger.warning(f"Watch poll cycle failed: {e}")

        asyncio.create_task(_loop())
