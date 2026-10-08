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

Caps are per UTC day and env-overridable per tier/kind:
OG_CAP_<TIER>_<KIND>, e.g. OG_CAP_PRO_IMAGES=50. Kinds: images,
uploads, lookup, tts, upload_mb, transcribe (Round 8 voice notes),
unity (Round 16 Unity-maker packages per day), shortlink (Round 17
short-link creations per day).
Chat tokens: free is metered by
OG_FREE_DAILY_TOKENS in app.py; every paid tier is unlimited.

Payment links default to the live Stripe links created 2026-10-08;
OG_LINK_STANDARD / OG_LINK_PRO / OG_LINK_BLUE / OG_LINK_BLACKOUT
override them. Standard falls back to the legacy OG_PRO_LINK first so
an existing deployment keeps its configured checkout until the new
env vars land.
"""

import hmac
import os

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

TIER_ORDER = ("free", "standard", "pro", "blue", "blackout")
PAID_TIERS = TIER_ORDER[1:]

TIER_TOKEN = os.getenv("OG_TIER_" "TOKEN", "").strip()

_DEFAULT_LINKS = {
    "standard": "https://buy.stripe.com/00wfZi8GB3kHaLEft4dby04",
    "pro": "https://buy.stripe.com/00wcN63mh2gD4ng6Wydby05",
    "blue": "https://buy.stripe.com/3cIeVeg936wT9HAbcOdby06",
    "blackout": "https://buy.stripe.com/6oU9AUf4Zg7t6vo94Gdby07",
}

_DEFAULT_CAPS = {
    # kind:      free  standard  pro   blue  blackout
    "images":  {"free": 2,    "standard": 10,  "pro": 50,  "blue": 300,  "blackout": 1000},
    "uploads": {"free": 3,    "standard": 25,  "pro": 30,  "blue": 100,  "blackout": 300},
    "lookup":  {"free": 25,   "standard": 250, "pro": 250, "blue": 250,  "blackout": 1000},
    "tts":     {"free": 60,   "standard": 60,  "pro": 300, "blue": 600,  "blackout": 2000},
    "transcribe": {"free": 3, "standard": 15,  "pro": 50,  "blue": 150,  "blackout": 500},
    "unity":   {"free": 1,    "standard": 3,   "pro": 10,  "blue": 25,   "blackout": 100},
    "shortlink": {"free": 5,  "standard": 25,  "pro": 50,  "blue": 100,  "blackout": 500},
    "upload_mb": {"free": 8,  "standard": 8,   "pro": 25,  "blue": 25,   "blackout": 25},
}

BADGES = {
    "free": "✦ Upgrade",
    "standard": "⭐ Standard",
    "pro": "💎 Pro",
    "blue": "💙 Blue",
    "blackout": "🖤 Blackout",
}

NAMES = {
    "free": "Free",
    "standard": "Standard",
    "pro": "Pro",
    "blue": "Blue",
    "blackout": "Blackout",
}

PRICES = {"standard": 10, "pro": 25, "blue": 50, "blackout": 100}


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


def tier_link(tier: str, legacy_pro_link: str = "") -> str:
    """Checkout URL for a tier: env OG_LINK_<TIER> > the created
    Stripe link > the legacy OG_PRO_LINK (# = unset, never used).
    The created links win over the legacy one on purpose: the old
    $9.99 link must not be reachable from the site anymore."""
    env = os.getenv(f"OG_LINK_{tier.upper()}")
    if env:
        return env
    if tier in _DEFAULT_LINKS:
        return _DEFAULT_LINKS[tier]
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
    "standard": [
        "Unlimited chat — no daily token cap",
        "Web lookup + live data: news, weather, scores, stocks, crypto",
        "10 images a day, drawn by OG",
        "25 file uploads a day (PDFs & photos OG can read)",
        "5 GB file storage",
        "Voice replies + Talk mode",
        "Voice notes — talk instead of typing (15 a day)",
    ],
    "pro": [
        "50 images a day",
        "30 uploads a day — files up to 25 MB",
        "25 GB file storage",
        "250 lookups a day",
        "Higher voice limits (5× the voice)",
        "Early access — new tools land here first",
    ],
    "blue": [
        "300 images a day",
        "100 uploads a day",
        "50 GB file storage",
        "Monitoring pack included when it ships (bank, email, BTC & stock alerts)",
        "Priority speed — your chats jump the line",
    ],
    "blackout": [
        "1,000 images a day",
        "300 uploads a day",
        "100 GB file storage",
        "1,000 lookups a day",
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
    "standard": ("card-std", "⭐ OG AI Standard", "The full OG, uncapped."),
    "pro": ("card-pro", "💎 OG AI Pro", "More art, bigger files, first in line for new tools."),
    "blue": ("card-blue", "💙 OG AI Blue", "The watcher tier — monitoring included, priority speed."),
    "blackout": ("card-black", "🖤 OG AI Blackout", "Everything. Maxed. Forever first."),
}


def ladder_page_html(links, uid=None) -> str:
    """The /pro page: four escalating tier cards with checkout buttons.

    `links` maps tier -> checkout URL.
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
        cards.append(f"""
  <section class="card {cls}">
    <h2>{heading}</h2>
    <div class="price">${PRICES[tier]}<span>/month</span></div>
    <p class="tag">{tagline}</p>
    <ul>{feats}</ul>
    <a class="btn" href="{links[tier]}">Get {NAMES[tier]} →</a>
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
  /* Escalation: every tier is visibly MORE than the one before. */
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


def tier_of(cookies, uid=""):
    """Resolve a visitor's tier: tier cookie > legacy cookie (standard)
    > webhook entitlement > free."""
    return tier_from_cookies(cookies, _DEPS.get("pro_token", ""),
                             entitlement_tier_for(uid) if uid else None)


def images_left(tier, used):
    """Images remaining today for a tier, given today's usage count."""
    return max(0, cap(tier, "images") - used)


def register_tier_routes(app):
    """Mount /pro (the ladder page) and /pro/success on the app."""

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
        response = RedirectResponse(url="/", status_code=302)
        tier = (raw_request.query_params.get("tier") or "").lower()
        if tier not in PAID_TIERS:
            tier = "standard"
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
