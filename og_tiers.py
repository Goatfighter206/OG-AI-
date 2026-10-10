"""
OG AI pricing tiers (Round 7) — the ladder that replaced the single
$9.99 "OG AI Pro" plan.

Tier model (v1 cookie scheme, same trust level as the old ogai_pro
cookie): after Stripe hands the buyer back to
/pro/success?tier=<tier>, the app sets an `ogai_tier` cookie whose
value is "<tier>.<token>", where <token> is OG_TIER_TOKEN when set,
else the legacy OG_PRO_TOKEN. A cookie is honored only when the token
matches (constant-time compare). Legacy holders of the old `ogai_pro`
cookie (the $9.99 plan) are treated as **standard** tier. The dark
Stripe-webhook entitlement (v2) also maps to **standard** — it was the
old single plan. Webhook price/product -> tier mapping for when v2 is
enabled (see .env.example):

    price_1UOJxjJgRg4PhjAqIBzwLEoo  (OG AI Standard $10)  -> standard
    price_1UOJxtJgRg4PhjAqKFUAuzKz  (OG AI Pro $25)      -> pro
    price_1UOJy0JgRg4PhjAqAJ5PXD2g  (OG AI Blue $50)      -> blue
    price_1UOJy8JgRg4PhjAq5uCiCfSu  (OG AI Blackout $100)-> blackout
    price_1UMaXZJgRg4PhjAqsEJqmSeA  (legacy OG AI Pro $9.99) -> standard

Round 58 adds the RED tier ($5.99/mo, "the chat tier") between
free and standard. No Stripe price/link exists for it yet — the
link is a dashboard step (OG_LINK_RED env once created), so
metadata tier=red resolves the moment a link lands; until then
the /pro Red card honestly shows "Not open yet" and tier_link
never falls back to the legacy link for red.

Caps are per UTC day and env-overridable per tier/kind:
OG_CAP_<TIER>_<KIND>, e.g. OG_CAP_PRO_IMAGES=50. Kinds: images,
uploads, lookup, tts, upload_mb, transcribe (Round 8 voice notes),
unity (Round 16 Unity-maker packages per day), song (Round 30
sung tracks per day, charged only on a completed generation),
shortlink (Round 17
short-link creations per day), calorie (Round 18 food-log entries
per day), watch (Round 18 ACTIVE price watches, a count not a
daily meter), order (Round 19 approved order handoffs per day),
trade (Round 20 approved trades per day — an execution on the
visitor's connected Coinbase or a trade-sheet handoff; drafts and
previews are free), browser_min (Round 23 OG-browser minutes per
day: Blue 60, Blackout 300, every other tier 0 — plus the module's
own once-ever 10-minute free taste). Round 20 also caps the SIZE
of one trade:
TRADE_CEILINGS below (env OG_TRADE_CEIL_<TIER> overrides).
Chat tokens: free was metered by
OG_FREE_DAILY_TOKENS in app.py. Round 55 (owner ruling
2026-10-10 14:19: "Change daily cap to weekly cap"): for the
priced kinds — chat tokens, images, video, song, tts — the
caps are WEEKLY, full stop; there is no daily enforcement layer
for them anymore. Every tier carries a weekly chat-token pool
(_WEEKLY_CHAT_TOKENS; free's pool was the old 25,000/day x7 =
175,000/week in Round 55, then Round 57 trimmed free to a
25,000/week minimal taste — below every paid tier, even the
coming Red tier's tiny generator taste)
and weekly ceilings
for images/video/song/tts (_WEEKLY_CAPS). The weekly window is
trailing 7 UTC days, read from a per-day map the existing
writers maintain on the same usage-store entries
(note_day/week_used). Kinds outside the priced grid (uploads,
lookup, browser minutes, ...) keep their daily caps — no weekly
numbers were set for them.

Payment links default to the live Stripe links created 2026-10-08;
OG_LINK_STANDARD / OG_LINK_PRO / OG_LINK_BLUE / OG_LINK_BLACKOUT
override them. Standard falls back to the legacy OG_PRO_LINK first so
an existing deployment keeps its configured checkout until the new
env vars land.
"""

import hmac
import os
from datetime import date, datetime, timedelta, timezone

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

TIER_ORDER = ("free", "red", "standard", "pro", "blue", "blackout")
PAID_TIERS = TIER_ORDER[1:]

TIER_TOKEN = os.getenv("OG_TIER_" "TOKEN", "").strip()

_DEFAULT_LINKS = {
    "standard": "https://buy.stripe.com/00wfZi8GB3kHaLEft4dby04",
    "pro": "https://buy.stripe.com/00wcN63mh2gD4ng6Wydby05",
    "blue": "https://buy.stripe.com/3cIeVeg936wT9HAbcOdby06",
    "blackout": "https://buy.stripe.com/6oU9AUf4Zg7t6vo94Gdby07",
}

_DEFAULT_CAPS = {
    # kind:      free  red   standard  pro   blue  blackout
    "images":  {"free": 2,    "red": 5,   "standard": 10,  "pro": 50,  "blue": 300,  "blackout": 1000},
    "uploads": {"free": 3,    "red": 10,  "standard": 25,  "pro": 30,  "blue": 100,  "blackout": 300},
    "lookup":  {"free": 25,   "red": 100, "standard": 250, "pro": 250, "blue": 250,  "blackout": 1000},
    "tts":     {"free": 60,   "red": 60,  "standard": 60,  "pro": 300, "blue": 600,  "blackout": 2000},
    "transcribe": {"free": 3, "red": 8,   "standard": 15,  "pro": 50,  "blue": 150,  "blackout": 500},
    "unity":   {"free": 1,    "red": 2,   "standard": 3,   "pro": 10,  "blue": 25,   "blackout": 100},
    "video":   {"free": 1,    "red": 2,   "standard": 3,   "pro": 10,  "blue": 25,   "blackout": 100},
    # Round 30 (OG Songs): sung tracks per day. At the studio's
    # $0.15/generated-minute and a ~2-3 min song (~$0.38), the
    # ladder stays cheaper per unit than story videos above.
    "song":    {"free": 1,    "red": 1,   "standard": 2,   "pro": 5,   "blue": 15,   "blackout": 50},
    "shortlink": {"free": 5,  "red": 10,  "standard": 25,  "pro": 50,  "blue": 100,  "blackout": 500},
    "calorie": {"free": 10,   "red": 25,  "standard": 50,  "pro": 100, "blue": 200,  "blackout": 500},
    "watch":   {"free": 3,    "red": 5,   "standard": 10,  "pro": 25,  "blue": 50,   "blackout": 200},
    "order":   {"free": 1,    "red": 2,   "standard": 3,   "pro": 10,  "blue": 25,   "blackout": 100},
    "trade":   {"free": 1,    "red": 2,   "standard": 3,   "pro": 10,  "blue": 25,   "blackout": 100},
    "browser_min": {"free": 0, "red": 0,  "standard": 0,  "pro": 0,   "blue": 60,   "blackout": 300},
    # Round 29 (OG Watch): marketplace watch slots per visitor, and
    # the separate daily BACKGROUND-minute budget those scheduled
    # checks draw from (never the interactive browser_min above).
    "fbwatch":   {"free": 0,    "red": 1,   "standard": 1,   "pro": 3,   "blue": 5,    "blackout": 10},
    "watch_min": {"free": 0,    "red": 4,   "standard": 8,   "pro": 32,  "blue": 60,   "blackout": 96},
    "upload_mb": {"free": 8,  "red": 8,   "standard": 8,   "pro": 25,  "blue": 25,   "blackout": 25},
}

# Round 20: the largest single trade (USD value) a tier may approve.
# OG refuses the preview above the ceiling and states the limit.
_DEFAULT_TRADE_CEILINGS = {
    "free": 100, "red": 250, "standard": 500, "pro": 2500,
    "blue": 10000, "blackout": 50000,
}

# Round 21: the file-locker quota per tier — the storage ladder
# Brent locked 2026-10-08 (Free: NO locker, uploads stay temporary;
# Standard 5 GB, Pro 25 GB, Blue 50 GB, Blackout 100 GB; Round 58
# slots Red in between at 1 GB). Env
# OG_STORAGE_GB_<TIER> (a GB number) overrides a tier's quota.
_GB = 1024 ** 3
STORAGE_BYTES = {
    "free": 0, "red": 1 * _GB, "standard": 5 * _GB, "pro": 25 * _GB,
    "blue": 50 * _GB, "blackout": 100 * _GB,
}


def storage_bytes(tier: str) -> int:
    """Locker quota in bytes for a tier (0 = no locker)."""
    env = os.getenv(f"OG_STORAGE_GB_{str(tier).upper()}")
    if env is not None:
        try:
            return max(0, int(float(env) * _GB))
        except ValueError:
            pass
    return int(STORAGE_BYTES.get(tier, 0))


def trade_ceiling(tier: str) -> float:
    """Per-trade dollar ceiling for a tier; env OG_TRADE_CEIL_<TIER>
    wins. Unknown tiers get the free ceiling."""
    env = os.getenv(f"OG_TRADE_CEIL_{str(tier).upper()}")
    if env is not None:
        try:
            return max(0.0, float(env))
        except ValueError:
            pass
    return float(_DEFAULT_TRADE_CEILINGS.get(tier, 100))

BADGES = {
    "free": "✦ Upgrade",
    "red": "🔴 Red",
    "standard": "⭐ Standard",
    "pro": "💎 Pro",
    "blue": "💙 Blue",
    "blackout": "🖤 Blackout",
}

NAMES = {
    "free": "Free",
    "red": "Red",
    "standard": "Standard",
    "pro": "Pro",
    "blue": "Blue",
    "blackout": "Blackout",
}

PRICES = {"red": 5.99, "standard": 10, "pro": 25, "blue": 50,
          "blackout": 100}


def _token(pro_token: str) -> str:
    return TIER_TOKEN or pro_token or ""


def cookie_value(tier: str, pro_token: str) -> str:
    """The ogai_tier cookie value granting `tier`."""
    return f"{tier}.{_token(pro_token)}"


def valid_tier_cookie(raw, pro_token: str):
    """Tier granted by an ogai_tier cookie value, or None."""
    if not raw or "." not in str(raw):
        return None
    tier, _, tok = str(raw).partition(".")
    if tier not in PAID_TIERS:
        return None
    expected = _token(pro_token)
    if not expected or not hmac.compare_digest(tok, expected):
        return None
    return tier


def tier_from_cookies(cookies, pro_token: str, entitled_tier=None) -> str:
    """Resolve a visitor's tier from their cookies (+ webhook grant).

    Priority: valid ogai_tier cookie > legacy ogai_pro cookie
    (standard) > webhook entitlement tier > free.
    """
    tier = valid_tier_cookie(cookies.get("ogai_tier"), pro_token)
    if tier:
        return tier
    if pro_token and cookies.get("ogai_pro") == pro_token:
        return "standard"
    if entitled_tier in PAID_TIERS:
        return entitled_tier
    return "free"


def cap(tier: str, kind: str) -> int:
    """Daily cap for (tier, kind); env OG_CAP_<TIER>_<KIND> wins."""
    if tier == "free" and kind == "images":
        _legacy = os.getenv("OG_IMAGE_FREE_DAILY")
        if _legacy is not None:
            try:
                return max(0, int(_legacy))
            except ValueError:
                pass
    env = os.getenv(f"OG_CAP_{tier.upper()}_{kind.upper()}")
    if env is not None:
        try:
            return int(env)
        except ValueError:
            pass
    return _DEFAULT_CAPS[kind][tier]


# --- Round 55: weekly ceilings ---------------------------------------
# For the priced kinds (chat tokens, images, video, song, tts)
# the caps ARE weekly — owner ruling 2026-10-10 14:19: "Change
# daily cap to weekly cap." No daily enforcement layer remains
# for them; the daily tables above now serve only the kinds
# outside this grid. The weekly fences exist because the old
# daily ladder's worst case was unprofitable at the top (a
# maxed Blackout burned ~$3,435/mo in generators against
# $100/mo in). Brent's invariant (2026-10-10, profit floor
# after Stripe's 2.9% + 30c): total worst-case cost
# (generators + chat) per user per month <= standard $5.41,
# pro $13.97, blue $28.25, blackout $56.80 — set at a hard
# 40% floor in Round 55, then Round 57 (same day, owner
# ruling 15:04) moved STANDARD and PRO to a 20% floor:
# standard <= $7.41, pro <= $18.98; blue/blackout stay 40%.
# Unit costs: image $0.04, video $0.375, song ~$0.38,
# tts ~$0.009/call. Weekly generator spend by tier:
# free $0.067, standard $1.554, pro $3.625, blue $5.16,
# blackout $11.305. (Round 57 trimmed free from $3.14/wk
# to a minimal taste: 1 image / 0 video / 0 songs /
# 3 voice replies — free is the floor the next round's
# Red tier must clear on every priced kind.)
# Round 58 (owner, same day): RED slots in at a 10% profit
# floor — worst case <= $4.92/mo ($5.99 net of Stripe =
# $5.516, minus 10% of price $0.599). Red is "the chat
# tier": owner set its pool at 125,000/week ("lower than
# standard by half") with tiny generator tastes that still
# clear free on every kind (2 images / 1 video / 1 song /
# 5 tts). Red spend: generators $0.880/wk + chat $0.075/wk
# = $4.14/mo <= $4.92 (r55tests pins the sum).
# No weekly row exceeds its old daily cap x7 (pinned in
# r55tests). Env OG_WCAP_<TIER>_<KIND> overrides a row,
# mirroring cap().
_WEEKLY_CAPS = {
    # kind:      free  red   standard  pro   blue  blackout
    "images":  {"free": 1,    "red": 2,   "standard": 7,   "pro": 25,  "blue": 40,  "blackout": 100},
    "video":   {"free": 0,    "red": 1,   "standard": 2,   "pro": 3,   "blue": 4,   "blackout": 5},
    "song":    {"free": 0,    "red": 1,   "standard": 1,   "pro": 3,   "blue": 4,   "blackout": 6},
    "tts":     {"free": 3,    "red": 5,   "standard": 16,  "pro": 40,  "blue": 60,  "blackout": 350},
}

# Weekly chat-token pools. Free's pool was the old 25,000/day
# allowance x7 = 175,000/week in Round 55; Round 57 trimmed
# it to a 25,000/week minimal taste (owner: free below
# every paid tier on everything, Red included). Free's
# total worst case is now ~$0.36/mo (generators $0.067/wk
# + chat $0.015/wk). The chat model is
# OPENAI_MODEL, default
# gpt-4o-mini; OpenAI's list price for it is $0.15/1M input +
# $0.60/1M output, so the paid pools are sized billing EVERY
# token at the $0.60 output rate (the worst case — no token can
# cost more). Chat spend/week: standard $0.15, pro $0.75,
# blue $1.20, blackout $1.80. TOTAL worst case (generators +
# chat, per month): standard $7.38 <= $7.41 (20% floor,
# Round 57), pro $18.96 <= $18.98 (20% floor, Round 57),
# blue $27.56 <= $28.25, blackout $56.79 <= $56.80.
# The invariant rules (r55tests pins these sums); if a row
# ever has to move, the totals must still fit — trim
# generators before the chat pool.
_WEEKLY_CHAT_TOKENS = {
    "free": 25000, "red": 125000, "standard": 250000, "pro": 1250000,
    "blue": 2000000, "blackout": 3000000,
}


def weekly_cap(tier: str, kind: str):
    """Weekly cap for (tier, kind); None when the kind has no
    weekly row. Env OG_WCAP_<TIER>_<KIND> wins, mirroring cap()."""
    row = _WEEKLY_CAPS.get(kind)
    if row is None:
        return None
    env = os.getenv(f"OG_WCAP_{tier.upper()}_{kind.upper()}")
    if env is not None:
        try:
            return int(env)
        except ValueError:
            pass
    return row.get(tier)


def weekly_hit(tier: str, kind: str, used: int) -> bool:
    """True when `used` has reached the weekly cap for the kind
    (False when the kind has no weekly row)."""
    cap_w = weekly_cap(tier, kind)
    return cap_w is not None and used >= cap_w


def chat_pool_hit(tier: str, used: int) -> bool:
    """True when `used` tokens have reached the tier's weekly
    chat pool (every tier has one; free's is 25,000/week)."""
    pool = weekly_chat_tokens(tier)
    return pool is not None and used >= pool


def weekly_chat_tokens(tier: str):
    """Weekly chat-token pool for a tier. Env
    OG_WCAP_<TIER>_CHAT_TOKENS wins. Free's pool defaults to
    the table's 25,000 but follows OG_FREE_DAILY_TOKENS (or
    the legacy OG_FREE_DAILY_LIMIT) x7 when either is set, so
    the old daily knob still scales the free allowance."""
    env = os.getenv(f"OG_WCAP_{tier.upper()}_CHAT_TOKENS")
    if env is not None:
        try:
            return int(env)
        except ValueError:
            pass
    if tier == "free":
        daily = os.getenv("OG_FREE_DAILY_TOKENS") \
            or os.getenv("OG_FREE_DAILY_LIMIT")
        if daily is not None:
            try:
                return int(daily) * 7
            except ValueError:
                pass
    return _WEEKLY_CHAT_TOKENS.get(tier)


def week_dates(today=None):
    """The trailing 7 UTC dates (ISO strings), oldest first,
    ending today."""
    if today is None:
        today = datetime.now(timezone.utc).date()
    elif isinstance(today, datetime):
        today = today.date()
    return [(today - timedelta(days=n)).isoformat()
            for n in range(6, -1, -1)]


def week_used(entry, field="days", value_key="count") -> int:
    """One meter entry's usage across the trailing 7 UTC days.

    The daily writers keep a per-day map on the entry (note_day);
    this sums it over the window. Entries from before Round 55
    have no map: fall back to today's value when the entry is
    today's (weekly history accrues from deploy day)."""
    if not isinstance(entry, dict):
        return 0
    days = entry.get(field)
    if isinstance(days, dict):
        total = 0
        for d in week_dates():
            try:
                total += int(days.get(d, 0) or 0)
            except (TypeError, ValueError):
                pass
        return total
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if entry.get("date") == today:
        try:
            return int(entry.get(value_key, 0) or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def note_day(entry, today: str, value: int, field="days"):
    """Mirror today's running total into the entry's per-day map
    and prune dates outside the trailing 7-day window. Writers
    call this right after updating the entry's daily total, so
    map[today] always equals that total. `field` is "days" for
    count meters, "token_days" for the chat-token meter."""
    days = entry.get(field)
    if not isinstance(days, dict):
        days = {}
        entry[field] = days
    try:
        days[today] = int(value)
    except (TypeError, ValueError):
        days[today] = 0
    keep = set(week_dates())
    for d in list(days):
        if d not in keep:
            del days[d]


def carry_days(old, entry, field="days"):
    """Carry a stale entry's per-day map onto its fresh same-day
    replacement (writers replace the entry when the date rolls;
    the weekly history must survive the roll)."""
    if isinstance(old, dict) and isinstance(old.get(field), dict):
        entry.setdefault(field, dict(old[field]))


def week_refill_text(entry, field="days", value_key="count") -> str:
    """When space starts opening up again, in plain words. The
    window trails, so there is NO fixed reset day: room frees as
    the oldest day with usage rolls off the week. Says exactly
    that — never a fake reset date."""
    days = entry.get(field) if isinstance(entry, dict) else None
    used_dates = []
    if isinstance(days, dict):
        for d in week_dates():
            try:
                if int(days.get(d, 0) or 0) > 0:
                    used_dates.append(d)
            except (TypeError, ValueError):
                pass
    if not used_dates and isinstance(entry, dict):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            if entry.get("date") == today and \
                    int(entry.get(value_key, 0) or 0) > 0:
                used_dates = [today]
        except (TypeError, ValueError):
            pass
    if not used_dates:
        return "it refills a little every day as your old days roll off"
    oldest = date.fromisoformat(used_dates[0])
    today = datetime.now(timezone.utc).date()
    n = (oldest + timedelta(days=7) - today).days
    if n <= 0:
        return ("space opens back up at midnight tonight, when "
                "today rolls off the week")
    if n == 1:
        return ("space starts opening back up tomorrow, when your "
                "oldest day rolls off the week")
    return (f"space starts opening back up in {n} days, when your "
            f"oldest day rolls off the week")


def weekly_chat_payload(tier: str, agent_name: str):
    """The /chat refusal when a visitor hits their weekly chat
    pool — names the pool and the honest roll-off refill (no
    daily reset exists anymore). Free keeps its own pitch and
    the free_tokens_left field the page reads."""
    pool = weekly_chat_tokens(tier) or 0
    if tier == "free":
        return {
            "response": (
                f"Yo, real talk — you're outta free tokens for "
                f"this week ({pool:,} a week on the free plan), "
                f"and the OG don't work for free forever. Go "
                f"premium for a bigger weekly pool: "
                f"{public_pro_url()} — the free pool refills a "
                f"little every day as your old days roll off "
                f"the week."),
            "agent_name": agent_name,
            "timestamp": datetime.now().isoformat(),
            "upgrade_url": public_pro_url(),
            "free_tokens_left": 0,
        }
    return {
        "response": (
            f"Yo, real talk — you burned through this week's chat "
            f"pool ({pool:,} tokens on the {NAMES.get(tier, tier)} "
            f"plan). The pool ain't daily: it refills a little "
            f"every day as your old days roll off the week. Higher "
            f"plans carry bigger weekly pools: {public_pro_url()}"),
        "agent_name": agent_name,
        "timestamp": datetime.now().isoformat(),
        "upgrade_url": public_pro_url(),
        "weekly_tokens_left": 0,
    }


def weekly_image_line(tier: str) -> str:
    """The /image refusal when the weekly picture wall hits.
    Names the weekly number and the roll-off refill — no fake
    reset date."""
    cap_w = weekly_cap(tier, "images") or 0
    return (
        f"Yo, that's all {cap_w} pics for this week on your plan "
        f"— the weekly pool only refills a little every day as "
        f"your old days roll off it. Higher plans draw more a "
        f"week: {public_pro_url()}")


def weekly_tts_detail(tier: str) -> str:
    """The /tts 429 detail when the weekly voice wall hits."""
    cap_w = weekly_cap(tier, "tts") or 0
    return (f"Weekly voice limit reached ({cap_w} voice replies "
            f"a week on your plan) — it refills as your week "
            f"rolls on")


def tier_link(tier: str, legacy_pro_link: str = "") -> str:
    """Checkout URL for a tier: env OG_LINK_<TIER> > the created
    Stripe link > the legacy OG_PRO_LINK (# = unset, never used).
    The created links win over the legacy one on purpose: the old
    $9.99 link must not be reachable from the site anymore.
    Red has no created link yet (dashboard step): env only, and
    NEVER the legacy fallback — an empty link renders the card's
    honest "Not open yet" state instead of a wrong checkout."""
    env = os.getenv(f"OG_LINK_{tier.upper()}")
    if env:
        return env
    if tier in _DEFAULT_LINKS:
        return _DEFAULT_LINKS[tier]
    if tier == "red":
        return ""
    return legacy_pro_link


def public_pro_url() -> str:
    """Absolute URL of the ladder page, for in-chat upgrade messages."""
    base = os.getenv("OG_PUBLIC_URL", "https://og-ai-service.onrender.com")
    return base.rstrip("/") + "/pro"


# --- Ladder page --------------------------------------------------------------
# Brent's presentation rule: the higher the tier, the more you visibly
# get — cards escalate in size/weight and every card lists the FULL
# cumulative feature set, so each list is longer than the one before.

_FEATURES = {
    "red": [
        "Chat on a weekly pool — 125K tokens a week",
        "Live data pack: news, weather, scores, stocks, crypto",
        "2 images a week, drawn by OG",
        "10 file uploads a day (PDFs & photos OG can read)",
        "1 GB file storage",
        "Voice replies — OG talks back out loud",
        "Voice notes — talk instead of typing (8 a day)",
        "Weekly fences: 1 story video · 1 song · 5 voice replies a week",
        "Token packs unlocked — buy more tokens when the week runs dry",
    ],
    "standard": [
        "Chat on a weekly pool — 250K tokens a week",
        "Web lookup + live data: news, weather, scores, stocks, crypto",
        "7 images a week, drawn by OG",
        "25 file uploads a day (PDFs & photos OG can read)",
        "5 GB file storage",
        "Voice replies + Talk mode",
        "Voice notes — talk instead of typing (15 a day)",
        "Weekly fences: 2 story videos · 1 song · 16 voice replies a week",
    ],
    "pro": [
        "Chat pool grows — 1.25M tokens a week",
        "25 images a week",
        "30 uploads a day — files up to 25 MB",
        "25 GB file storage",
        "250 lookups a day",
        "Higher voice limits (5× the voice)",
        "Weekly fences: 3 story videos · 3 songs · 40 voice replies a week",
        "Early access — new tools land here first",
    ],
    "blue": [
        "Chat pool grows — 2M tokens a week",
        "40 images a week",
        "100 uploads a day",
        "50 GB file storage",
        "OG's own web browser — 60 minutes a day, watch him drive it live",
        "Weekly fences: 4 story videos · 4 songs · 60 voice replies a week",
        "Monitoring pack included when it ships (bank, email, BTC & stock alerts)",
        "Priority speed — your chats jump the line",
    ],
    "blackout": [
        "Chat pool maxed — 3M tokens a week",
        "100 images a week",
        "300 uploads a day",
        "100 GB file storage",
        "1,000 lookups a day",
        "OG's own web browser — 300 minutes a day, five full hours",
        "Weekly fences: 5 story videos · 6 songs · 350 voice replies a week",
        "Online ordering + trading-on-approval included when they ship",
        "Every future tool — day one, no upsells ever",
    ],
}


def _cumulative(tier: str):
    """Full feature list for a tier: everything below it + its own."""
    out = []
    for t in PAID_TIERS:
        out.extend(_FEATURES[t])
        if t == tier:
            break
    return out


_CARD_STYLE = {
    # tier: (card css class, heading, tagline)
    "red": ("card-red", "🔴 OG AI Red", "The chat tier — the cheapest way in, chat for days."),
    "standard": ("card-std", "⭐ OG AI Standard", "The full OG, uncapped."),
    "pro": ("card-pro", "💎 OG AI Pro", "More art, bigger files, first in line for new tools."),
    "blue": ("card-blue", "💙 OG AI Blue", "The watcher tier — monitoring included, priority speed."),
    "blackout": ("card-black", "🖤 OG AI Blackout", "Everything. Maxed. Forever first."),
}


def ladder_page_html(links, uid=None) -> str:
    """The /pro page: five escalating tier cards with checkout buttons.

    `links` maps tier -> checkout URL. A tier whose link is not
    configured yet (Red until its Stripe link lands) renders an
    honest "Not open yet" block instead of a checkout button.
    """
    if uid:
        links = {t: (f"{u}&client_reference_id={uid}" if "?" in u
                     else f"{u}?client_reference_id={uid}")
                 if u.startswith("http") else u
                 for t, u in links.items()}
    cards = []
    for tier in PAID_TIERS:
        cls, heading, tagline = _CARD_STYLE[tier]
        feats = "".join(f"<li>{f}</li>" for f in _cumulative(tier))
        url = links.get(tier, "")
        if url.startswith("http"):
            btn = f'<a class="btn" href="{url}">Get {NAMES[tier]} →</a>'
        else:
            btn = '<span class="btn btn-off">Not open yet</span>'
        cards.append(f"""
  <section class="card {cls}">
    <h2>{heading}</h2>
    <div class="price">${PRICES[tier]}<span>/month</span></div>
    <p class="tag">{tagline}</p>
    <ul>{feats}</ul>
    {btn}
  </section>""")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OG AI — Go Premium</title>
<style>
  body {{ background:#0a0a0a; color:#f2f2f2; font-family: Arial, sans-serif; margin:0; padding:28px 16px 48px; }}
  h1 {{ color:#ffc107; text-align:center; letter-spacing:1px; margin:0 0 4px; }}
  .sub {{ color:#aaa; text-align:center; margin:0 auto 26px; max-width:560px; line-height:1.5; }}
  .ladder {{ display:flex; flex-direction:column; align-items:center; gap:18px; }}
  .card {{ background:#141414; border:1px solid #2c2c2c; border-radius:14px; padding:20px 22px; width:100%; box-sizing:border-box; }}
  .card h2 {{ margin:0 0 2px; font-size:1.15em; }}
  .price {{ font-size:1.9em; font-weight:bold; color:#fff; }}
  .price span {{ font-size:.45em; color:#999; font-weight:normal; }}
  .tag {{ color:#bbb; margin:4px 0 12px; line-height:1.45; }}
  .card ul {{ margin:0 0 16px; padding-left:20px; color:#ddd; line-height:1.65; }}
  .btn {{ display:block; text-align:center; padding:12px; border-radius:999px; border:2px solid #ffc107; color:#ffc107; text-decoration:none; font-weight:bold; background:rgba(255,193,7,.08); }}
  .btn-off {{ display:block; border:2px dashed #444; color:#888; background:transparent; cursor:default; }}
  /* Escalation: every tier is visibly MORE than the one before. */
  .card-red {{ max-width:460px; border-color:rgba(229,57,53,.6); box-shadow:0 0 18px rgba(229,57,53,.22); }}
  .card-red h2 {{ color:#ff7a6b; }}
  .card-red .btn {{ border-color:#e53935; color:#ff7a6b; background:rgba(229,57,53,.08); }}
  .card-std {{ max-width:520px; }}
  .card-pro {{ max-width:580px; border-color:rgba(255,193,7,.55); padding:24px 26px; }}
  .card-pro h2 {{ font-size:1.3em; }}
  .card-blue {{ max-width:640px; border:2px solid #2f7bff; padding:28px 30px; box-shadow:0 0 26px rgba(47,123,255,.35); }}
  .card-blue h2 {{ font-size:1.45em; color:#8dbcff; }}
  .card-blue .btn {{ border-color:#2f7bff; color:#8dbcff; background:rgba(47,123,255,.10); }}
  .card-black {{ max-width:720px; padding:34px 34px; background:linear-gradient(160deg,#050505,#171204); border:2px solid #ffc107; box-shadow:0 0 38px rgba(255,193,7,.30); }}
  .card-black h2 {{ font-size:1.65em; color:#ffc107; letter-spacing:.5px; }}
  .card-black .price {{ font-size:2.3em; color:#ffc107; }}
  .card-black .btn {{ background:#ffc107; color:#111; font-size:1.08em; padding:15px; }}
  .foot {{ color:#777; text-align:center; margin-top:28px; font-size:.85em; line-height:1.6; }}
  a.back {{ color:#ffc107; }}
</style></head><body>
<h1>✦ OG AI — Pick your power</h1>
<p class="sub">Free gets you a taste. Every step up gets you MORE — more chat,
more art, bigger files, and the new tools first. Cancel anytime.</p>
<div class="ladder">{''.join(cards)}
</div>
<p class="foot">Every plan is month-to-month through Stripe. Already paid?
Your plan is live the second you land back here.<br>
<a class="back" href="/">← Back to OG</a></p>
</body></html>"""


# --- App-bound helpers -------------------------------------------------
# app.py binds its usage store / flags once at startup (bind()); the
# resolvers and the /pro routes below run against those bindings.
_DEPS = {}


def bind(deps):
    _DEPS.update(deps)


def entitlement_tier(record):
    """Tier stored on one entitlement record (pre-ladder -> standard)."""
    if not isinstance(record, dict):
        return None
    tier = record.get("tier")
    return tier if tier in PAID_TIERS else "standard"


def entitlement_tier_for(uid):
    """Tier of uid's webhook entitlement, or None (dark when v2 off)."""
    if not uid or not _DEPS.get("webhook_enabled"):
        return None
    with _DEPS["lock"]:
        store = _DEPS["load_store"]()
    entitled = store.get(_DEPS["entitled_key"])
    if not isinstance(entitled, dict) or uid not in entitled:
        return None
    return entitlement_tier(entitled.get(uid))


def _stamp_entitlement(uid, tier):
    """Persist a /pro/success grant into the entitled store (Round 42).

    Mirrors the Stripe webhook's record (app.py _grant_entitlement)
    field-for-field, so tier_of() resolves the tier from the uid alone
    — no ogai_tier cookie needed (e.g. the Unity app client, which
    never carries one). The success path has no Stripe session or
    customer email, so those fields take _grant_entitlement's own
    defaults (""). Re-landing refreshes the same record. No-op when
    there is no uid or no save path is bound.
    """
    save = _DEPS.get("save_store")
    if not uid or save is None:
        return
    with _DEPS["lock"]:
        store = _DEPS["load_store"]()
        entitled = store.get(_DEPS["entitled_key"])
        if not isinstance(entitled, dict):
            entitled = {}
        entitled[uid] = {
            "granted": datetime.now(timezone.utc).isoformat(),
            "session": "",
            "email": "",
            "tier": tier if tier in PAID_TIERS else "standard",
        }
        store[_DEPS["entitled_key"]] = entitled
        save(store)


def migrate_entitlement(old_uid, new_uid, email=""):
    """Round 44 attach step (bound as an og_accounts login hook):
    a guest buyer earns an entitlement under their anonymous uid;
    when they sign in to an existing account instead of signing
    up, move that record onto the account's uid so the purchase
    attaches. No-op when there is no guest record, when the
    account uid already holds one (an account's own record is
    never overwritten), or when no save path is bound."""
    save = _DEPS.get("save_store")
    if not old_uid or not new_uid or old_uid == new_uid or save is None:
        return
    with _DEPS["lock"]:
        store = _DEPS["load_store"]()
        entitled = store.get(_DEPS["entitled_key"])
        if not isinstance(entitled, dict):
            return
        if old_uid not in entitled or new_uid in entitled:
            return
        entitled[new_uid] = entitled.pop(old_uid)
        store[_DEPS["entitled_key"]] = entitled
        save(store)


def tier_of(cookies, uid=""):
    """Resolve a visitor's tier: tier cookie > legacy cookie (standard)
    > webhook entitlement > free."""
    return tier_from_cookies(cookies, _DEPS.get("pro_token", ""),
                             entitlement_tier_for(uid) if uid else None)


def images_left(tier, used):
    """Images remaining today for a tier, given today's usage count."""
    return max(0, cap(tier, "images") - used)


def _buyer_needs_account(raw_request) -> bool:
    """Round 44 buyer flow: True only while the sign-in gate is
    in force AND this request carries no live account session —
    i.e. an entitled guest buyer who still needs the account
    step. With the gate off, /pro/success keeps its exact
    pre-Round-44 behavior (the Round 42 contract). Lazy import:
    og_accounts is the session authority and is never imported
    at module level; any failure falls back to False."""
    try:
        import og_accounts
        if not og_accounts.gate_enabled():
            return False
        return og_accounts._session_for(
            raw_request.cookies.get(og_accounts.SESSION_COOKIE)
        ) is None
    except Exception:
        return False


def commercial_grid() -> dict:
    """Round 58 — the single source of truth, served whole.

    Owner rule (2026-10-10): "automatically update anything
    tied to the thing we changed." Every commercial number a
    client can display — tier prices, weekly caps, chat pools,
    daily caps, browser minutes, storage, and BOTH token-pack
    tables — is computed here straight from this module's
    tables (token tables lazily from og_tokenpacks), so a
    client that renders GET /tiers (the Unity app's plan
    cards are the first customer) can never drift from the
    numbers the server actually enforces. Public pricing
    info only; no per-user data."""
    import og_tokenpacks as _tp  # lazy: no import cycle

    tiers = []
    for t in TIER_ORDER:
        sbytes = storage_bytes(t)
        tiers.append({
            "tier": t,
            "name": NAMES[t],
            "badge": BADGES[t],
            "price": PRICES.get(t, 0),
            "chat_tokens_week": weekly_chat_tokens(t),
            "weekly_caps": {k: weekly_cap(t, k)
                            for k in ("images", "video", "song",
                                      "tts")},
            "daily_caps": {k: cap(t, k)
                           for k in sorted(_DEFAULT_CAPS)},
            "browser_min": cap(t, "browser_min"),
            "storage_bytes": sbytes,
            "storage_gb": round(sbytes / _GB, 2),
            "trade_ceiling": trade_ceiling(t),
            "checkout_open": bool(tier_link(t)),
        })
    return {
        "tiers": tiers,
        "token_packs": {
            "prices": list(_tp.PACK_PRICES),
            "burn_rates": dict(_tp.RATES),
            "tables": {
                "margin_60": {
                    "tiers": list(_tp.MARGIN_60_TIERS),
                    "tokens_per_dollar": _tp.TOKENS_PER_DOLLAR_60,
                    "packs": {str(p): _tp.PACK_TOKENS_60[p]
                              for p in _tp.PACK_PRICES},
                },
                "margin_40": {
                    "tiers": [t for t in PAID_TIERS
                              if t not in _tp.MARGIN_60_TIERS],
                    "tokens_per_dollar": _tp.TOKENS_PER_DOLLAR,
                    "packs": {str(p): _tp.PACK_TOKENS[p]
                              for p in _tp.PACK_PRICES},
                },
            },
        },
    }


def register_tier_routes(app):
    """Mount /pro (the ladder page), /pro/success and the
    Round 58 grid payload (/tiers) on the app."""

    @app.get("/tiers")
    async def tiers_grid():
        return commercial_grid()

    @app.get("/pro", response_class=HTMLResponse)
    async def pro_upgrade(raw_request: Request):
        _DEPS["bump_stat"]("pro_clicks")
        links = {t: tier_link(t, _DEPS["pro_link"]) for t in PAID_TIERS}
        uid = raw_request.cookies.get("ogai_uid") \
            if _DEPS["webhook_enabled"] else None
        return HTMLResponse(content=ladder_page_html(links, uid))

    @app.get("/pro/success")
    async def pro_success(raw_request: Request):
        # v1 (default): the payment link redirects buyers here after
        # checkout; set the ogai_tier cookie for the tier bought
        # (?tier=...; the legacy $9.99 link grants standard).
        # v2 (webhook on): the cookie lands only once the webhook has
        # confirmed payment; until then show the "almost there" page.
        if _DEPS["webhook_enabled"]:
            uid = raw_request.cookies.get("ogai_uid")
            if not uid or not _DEPS["is_entitled"](uid):
                return HTMLResponse(content=_PENDING_HTML, status_code=200)
        _DEPS["bump_stat"]("pro_success")
        tier = (raw_request.query_params.get("tier") or "").lower()
        if tier not in PAID_TIERS:
            tier = "standard"
        # Round 42: stamp the grant onto the account uid as well, so
        # the tier follows the account to clients that never hold the
        # ogai_tier cookie. The cookie flow above/below is unchanged.
        _stamp_entitlement(raw_request.cookies.get("ogai_uid"), tier)
        if _DEPS["webhook_enabled"] and _buyer_needs_account(raw_request):
            # Round 44: a guest buyer — payment confirmed above, but
            # no account session. The grant rides on their visitor
            # uid until they sign up here (signup attaches this uid)
            # or sign in (the login attach hook migrates it). Tier
            # cookies would do nothing while the gate is on, so the
            # landing is this one-step page instead of a redirect.
            return HTMLResponse(content=_PAID_GUEST_HTML,
                                status_code=200)
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(
            "ogai_tier", cookie_value(tier, _DEPS["pro_token"]),
            max_age=_DEPS["cookie_max_age"], path="/", httponly=True,
            samesite="lax")
        # Legacy cookie too, for anything still reading ogai_pro.
        response.set_cookie(
            "ogai_pro", _DEPS["pro_token"],
            max_age=_DEPS["cookie_max_age"], path="/", httponly=True,
            samesite="lax")
        return response


_PENDING_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OG AI — Confirming your Pro</title>
<style>
  body { background:#0a0a0a; color:#f2f2f2; font-family: Arial, sans-serif; margin:0; padding:32px 20px; text-align:center; }
  h1 { color:#ffc107; letter-spacing:1px; }
  p { color:#ccc; line-height:1.6; max-width:520px; margin:12px auto; }
  a.btn { display:inline-block; margin-top:18px; padding:12px 30px; border:2px solid #ffc107; border-radius:999px; color:#ffc107; text-decoration:none; font-weight:bold; background:rgba(255,193,7,0.08); }
</style></head><body>
<h1>💰 Almost there…</h1>
<p>If you just paid, give Stripe a few seconds to confirm it with OG —
then head back and chat, Pro will already be on.</p>
<p>If you didn't finish paying, no charge was made and nothing is unlocked yet.</p>
<a class="btn" href="/">← Back to OG</a>
</body></html>"""


_PAID_GUEST_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OG AI — You're paid</title>
<style>
  body { background:#0a0a0a; color:#f2f2f2; font-family: Arial, sans-serif; margin:0; padding:32px 20px; text-align:center; }
  h1 { color:#ffc107; letter-spacing:1px; }
  p { color:#ccc; line-height:1.6; max-width:520px; margin:12px auto; }
  a.btn { display:inline-block; margin-top:18px; padding:12px 30px; border:2px solid #ffc107; border-radius:999px; color:#ffc107; text-decoration:none; font-weight:bold; background:rgba(255,193,7,0.08); }
</style></head><body>
<h1>💰 You're paid ✓ — one step left</h1>
<p>Stripe confirmed your plan. OG runs on accounts now, so create
your account — or sign in if you already have one — and your plan
switches on for that account.</p>
<p>Do it here, in this browser: your purchase is riding on this
visit, and signing up or signing in here attaches it to your
account. If you sign in to an existing account, the plan moves
onto it automatically.</p>
<a class="btn" href="/">Create account / Sign in →</a>
</body></html>"""
