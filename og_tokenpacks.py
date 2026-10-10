"""
OG token packs (Round 56) — Brent's design, 2026-10-10 14:36:
"add another one like this right next to it and it will be to
buy more tokens... these token stay with the user until the
user uses them all and they only start using them when they
run out of there weekly cap and they can't just buy tokens
they have to have a subscription to buy tokens."

Model:
- One balance unit = one chat token. The balance NEVER expires
  and is never transferable or cash-redeemable.
- BURN ORDER: the weekly pool always spends first (Round 55's
  choke points decide). Only when a weekly check would refuse
  does the balance get checked; past the wall, image/video/
  song/tts burn their posted token rate instead of refusing,
  and chat draws actual token usage from the balance. Burns
  land at the same success points as weekly charges — failed
  generations never burn.
- BUYING is subscribers-only: you need an active paid plan
  (tier_of() != "free" — the same entitlement resolution the
  tiers use) to be handed a buy link. Burning is NOT gated: a
  lapsed subscriber keeps the tokens they bought.
- Purchases ride the EXISTING Stripe Payment Link + webhook
  rails (no Stripe API key exists or is added). Each pack is
  its own Payment Link (config: OG_TOKEN_LINK_<PRICE>, like
  OG_LINK_<TIER>); the webhook credits the buyer
  (client_reference_id) exactly once per Stripe event when
  the session carries metadata pack=<price> (set on the link
  in the Stripe dashboard) and amount_total == price*100.

Exchange (flat 10,000 tokens per $1 — one per-token sale
price, $0.0001, so the margin guard is identical at every
pack and binds hardest at $20 via Stripe's fixed 30c):
    $20=200,000  $30=300,000  $40=400,000  $50=500,000
    $60=600,000  $70=700,000  $80=800,000  $90=900,000
    $100=1,000,000  $150=1,500,000  $200=2,000,000
    $300=3,000,000

Round 58 (owner ruling): the exchange is TIER-AWARE. Red and
Standard buyers credit at a leaner 6,500 tokens per $1 — the
60% table, sized with the same guard method (worst case =
the whole pack redeemed in songs, discrete whole units,
Stripe 2.9% + 30c): at $20, 130,000 tokens buy 18 whole
songs = $6.84 of redemption against $19.12 net, a 61.4% net
margin, and every pack clears 60% (pinned in r58tests).
Pro / Blue / Blackout buyers keep the flat 10,000/$ table
(the 40% table). The rate is fixed AT PURCHASE TIME by the
buyer's tier (balances are just numbers — nothing already
bought is ever repriced). Crediting resolves the tier
uid-only from the webhook entitlement store (the webhook-on
resolution; with the webhook off no pack credits at all).
An entitlement-less uid falls back to the flat table — the
shipped Round 56 behavior.
Burn rates (tokens): chat 1/token, image 750, video 7,000,
song 7,000, tts 175. Redemption cost vs sale value (unit
costs: chat $0.60/1M out worst case, image $0.04, video
$0.375, song ~$0.38, tts ~$0.009): chat 0.6%, image 53.3%,
video 53.6%, song 54.3%, tts 51.4% — every kind <= 55% of
the token's sale price, >= 40% margin after Stripe fees at
the worst pack. Pinned by r56tests (R55's cost-guard pattern).

Storage: the bound app usage store — "tokenpack:<uid>" ->
{"balance": int, "ledger": [last 100 entries]}; processed
pack events under "__token_events__" (pruned to 500).
"""

import os
from datetime import datetime, timezone
from typing import Dict

from fastapi import Request

PACK_PRICES = (20, 30, 40, 50, 60, 70, 80, 90, 100, 150, 200, 300)
TOKENS_PER_DOLLAR = 10_000
PACK_TOKENS = {p: p * TOKENS_PER_DOLLAR for p in PACK_PRICES}
TOKEN_SALE_PRICE = 1.0 / TOKENS_PER_DOLLAR  # $0.0001 per token

# Round 58: the 60%-margin table for Red + Standard buyers.
TOKENS_PER_DOLLAR_60 = 6_500
PACK_TOKENS_60 = {p: p * TOKENS_PER_DOLLAR_60 for p in PACK_PRICES}
TOKEN_SALE_PRICE_60 = 1.0 / TOKENS_PER_DOLLAR_60
MARGIN_60_TIERS = ("red", "standard")


def tokens_per_dollar(tier=None) -> int:
    """The exchange a buyer at `tier` gets (60% table for
    red/standard, the flat table for everyone else)."""
    if tier in MARGIN_60_TIERS:
        return TOKENS_PER_DOLLAR_60
    return TOKENS_PER_DOLLAR


def pack_tokens(price, tier=None) -> int:
    """Tokens one pack of `price` credits a buyer at `tier`."""
    table = PACK_TOKENS_60 if tier in MARGIN_60_TIERS \
        else PACK_TOKENS
    return table[int(price)]

RATES = {"chat": 1, "image": 750, "video": 7000, "song": 7000,
         "tts": 175}
# Worst-case upstream cost of one unit of each kind (R55 units;
# chat = one token billed at the $0.60/1M output rate).
UNIT_COSTS = {"chat": 0.60 / 1_000_000, "image": 0.04,
              "video": 0.375, "song": 0.38, "tts": 0.009}

_KEY = "tokenpack:"
_EVENTS_KEY = "__token_events__"
_LEDGER_KEEP = 100
_EVENTS_KEEP = 500

# Bound by app.py via bind_app (usage store + tier resolution),
# the og_video/og_songs pattern.
_deps: Dict = {}


def bind_app(deps):
    _deps.update(deps)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _entry(store: Dict, uid: str) -> Dict:
    entry = store.get(_KEY + uid)
    if not isinstance(entry, dict):
        entry = {"balance": 0, "ledger": []}
    entry.setdefault("balance", 0)
    if not isinstance(entry.get("ledger"), list):
        entry["ledger"] = []
    try:
        entry["balance"] = max(0, int(entry["balance"]))
    except (TypeError, ValueError):
        entry["balance"] = 0
    return entry


def _log(entry: Dict, delta: int, reason: str):
    entry["ledger"].append({
        "ts": _now(), "delta": int(delta), "reason": str(reason),
        "balance": int(entry["balance"])})
    entry["ledger"] = entry["ledger"][-_LEDGER_KEEP:]


def balance(uid: str) -> int:
    """The visitor's token balance (0 when unknown/unbound)."""
    if not uid or "load_usage" not in _deps:
        return 0
    try:
        with _deps["usage_lock"]:
            store = _deps["load_usage"]()
        entry = store.get(_KEY + uid)
        if not isinstance(entry, dict):
            return 0
        return max(0, int(entry.get("balance", 0) or 0))
    except Exception:
        return 0


def can_afford(uid: str, amount: int) -> bool:
    return balance(uid) >= int(amount)


def debit(uid: str, amount: int, reason: str) -> int:
    """Burn up to `amount` tokens; returns what actually
    burned (never more than the balance, never negative)."""
    amount = int(amount)
    if not uid or amount <= 0 or "load_usage" not in _deps:
        return 0
    with _deps["usage_lock"]:
        store = _deps["load_usage"]()
        entry = _entry(store, uid)
        burned = min(amount, int(entry["balance"]))
        if burned <= 0:
            return 0
        entry["balance"] = int(entry["balance"]) - burned
        _log(entry, -burned, reason)
        store[_KEY + uid] = entry
        _deps["save_usage"](store)
        return burned


def credit(uid: str, amount: int, reason: str) -> int:
    """Add tokens; returns the new balance."""
    amount = int(amount)
    if not uid or amount <= 0 or "load_usage" not in _deps:
        return balance(uid)
    with _deps["usage_lock"]:
        store = _deps["load_usage"]()
        entry = _entry(store, uid)
        entry["balance"] = int(entry["balance"]) + amount
        _log(entry, amount, reason)
        store[_KEY + uid] = entry
        _deps["save_usage"](store)
        return int(entry["balance"])


def credit_pack(uid: str, price: int, event_key: str = "",
                session_id: str = "", tier: str = None):
    """Credit one purchased pack, exactly once per Stripe
    event. `tier` is the buyer's tier at purchase time and
    picks the exchange table (Round 58); None = the flat
    table. Returns (credited, balance)."""
    price = int(price)
    if not uid or price not in PACK_TOKENS or "load_usage" not in _deps:
        return False, balance(uid)
    amount = pack_tokens(price, tier)
    key = str(event_key or session_id or "")
    with _deps["usage_lock"]:
        store = _deps["load_usage"]()
        events = store.get(_EVENTS_KEY)
        if not isinstance(events, dict):
            events = {}
        if key and key in events:
            entry = store.get(_KEY + uid)
            bal = int(entry.get("balance", 0)) \
                if isinstance(entry, dict) else 0
            return False, bal
        entry = _entry(store, uid)
        entry["balance"] = int(entry["balance"]) + amount
        _log(entry, amount, f"pack:${price}")
        store[_KEY + uid] = entry
        if key:
            events[key] = {"uid": uid, "price": price, "ts": _now(),
                           "session": str(session_id or "")}
            if len(events) > _EVENTS_KEEP:
                newest = sorted(
                    events.items(),
                    key=lambda kv: str(kv[1].get("ts", "")))
                events = dict(newest[-_EVENTS_KEEP:])
            store[_EVENTS_KEY] = events
        _deps["save_usage"](store)
        return True, int(entry["balance"])


import logging

logger = logging.getLogger("og_tokenpacks")


def _buyer_tier(uid: str):
    """The buyer's tier resolved from the uid alone (the
    webhook has no cookies): the entitlement store, via
    og_tiers — the same record tier_of() reads. None when
    the uid holds no entitlement (the credit then falls
    back to the flat table). Lazy import: og_tiers is a
    sibling, never a module-level dependency here."""
    try:
        import og_tiers
        return og_tiers.entitlement_tier_for(uid)
    except Exception:
        return None


def credit_from_session(session: Dict, event_id: str = "",
                        uid: str = "") -> bool:
    """Webhook helper: credit a completed pack checkout
    exactly once (idempotent on the Stripe event). Returns
    True when tokens landed."""
    price = pack_price_for_session(session)
    if not uid or not price:
        logger.warning("Token pack checkout not credited "
                       "(uid=%r price=%r)", bool(uid), price)
        return False
    credited, bal = credit_pack(uid, price, event_id,
                                session.get("id", ""),
                                tier=_buyer_tier(uid))
    logger.info("Token pack checkout: price=%s credited=%s "
                "balance=%s", price, credited, bal)
    return credited


def settle_chat(uid: str, funded: bool, tokens_used: int) -> int:
    """Chat settlement: a token-funded exchange bills its
    actual tokens to the balance; returns the balance."""
    if funded:
        debit(uid, tokens_used, "chat")
    return balance(uid)


def pack_link(price: int) -> str:
    """Checkout URL for a pack: env OG_TOKEN_LINK_<PRICE>
    (mirrors OG_LINK_<TIER>). "" = not configured yet."""
    return os.getenv(f"OG_TOKEN_LINK_{int(price)}", "").strip()


def session_is_pack(session: Dict) -> bool:
    """True when a checkout session is shaped like a token-pack
    purchase (metadata pack=<known price>, or a payment_link id
    matching OG_TOKEN_PLINK_<PRICE>). Pack-shaped sessions
    never grant a tier entitlement."""
    if not isinstance(session, dict):
        return False
    meta = session.get("metadata") or {}
    raw = str(meta.get("pack") or "").strip()
    if raw:
        try:
            if int(raw) in PACK_TOKENS:
                return True
        except (TypeError, ValueError):
            pass
    plink = str(session.get("payment_link") or "")
    if plink:
        for p in PACK_PRICES:
            if os.getenv(f"OG_TOKEN_PLINK_{p}", "") == plink:
                return True
    return False


def pack_price_for_session(session: Dict):
    """The pack price a completed session actually paid for,
    or None. The amount must match the pack's price exactly
    (integrity check on top of the metadata/plink match)."""
    if not isinstance(session, dict):
        return None
    price = None
    meta = session.get("metadata") or {}
    raw = str(meta.get("pack") or "").strip()
    if raw:
        try:
            if int(raw) in PACK_TOKENS:
                price = int(raw)
        except (TypeError, ValueError):
            price = None
    if price is None:
        plink = str(session.get("payment_link") or "")
        if plink:
            for p in PACK_PRICES:
                if os.getenv(f"OG_TOKEN_PLINK_{p}", "") == plink:
                    price = p
                    break
    if price is None:
        return None
    amount = session.get("amount_total")
    if amount is not None:
        try:
            if int(amount) != price * 100:
                return None
        except (TypeError, ValueError):
            return None
    return price


def purge_uid(uid: str) -> None:
    """Account deletion (R36): wipe the balance + ledger and
    this uid's processed-event records."""
    if not uid or "load_usage" not in _deps:
        return
    with _deps["usage_lock"]:
        store = _deps["load_usage"]()
        changed = False
        if _KEY + uid in store:
            del store[_KEY + uid]
            changed = True
        events = store.get(_EVENTS_KEY)
        if isinstance(events, dict):
            kept = {k: v for k, v in events.items()
                    if not (isinstance(v, dict)
                            and v.get("uid") == uid)}
            if len(kept) != len(events):
                store[_EVENTS_KEY] = kept
                changed = True
        if changed:
            _deps["save_usage"](store)


def register_token_routes(app):
    """GET /tokens — balance, packs, rates for the buy card.

    Posture: NOT a public path, so with the Round 44 sign-in
    gate on (production) a signed-out call gets the gate's
    401. With the gate off it answers for the caller's cookie
    uid (anonymous = balance 0, not a subscriber, no links).
    """

    @app.get("/tokens")
    async def tokens_status(raw_request: Request):
        uid = raw_request.cookies.get("ogai_uid") or ""
        tier = "free"
        if uid and _deps.get("tier_of") is not None:
            try:
                tier = _deps["tier_of"](uid, raw_request) or "free"
            except Exception:
                tier = "free"
        subscriber = bool(uid) and tier != "free"
        packs = []
        for p in PACK_PRICES:
            url = pack_link(p)
            item = {"price": p, "tokens": pack_tokens(p, tier),
                    "available": bool(url), "url": None}
            if url and subscriber:
                sep = "&" if "?" in url else "?"
                item["url"] = f"{url}{sep}client_reference_id={uid}"
            packs.append(item)
        return {
            "balance": balance(uid) if uid else 0,
            "tier": tier,
            "subscriber": subscriber,
            "tokens_per_dollar": tokens_per_dollar(tier),
            "packs": packs,
            "rates": dict(RATES),
            "note": ("Tokens never expire, and they only start "
                     "burning after your weekly pool runs dry. "
                     "Subscribers only — you need an active plan "
                     "to buy packs. Tokens are never transferable "
                     "and can't be cashed out."),
        }
