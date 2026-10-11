"""Round 54: MARKETPLACE WATCH + HAGGLE — an EXTENSION of
Round 29's Watch, not a new system (Brent approved the queue,
2026-10-10: "Get them all going don't ask just do").

WHAT ALREADY EXISTED (reused, not rebuilt). og_watch's fbwatch
watches ARE Marketplace watches: the Marketplace search URL, the
listing extraction (_parse_listings: /marketplace/item/<id>
anchors, title/price/town from the anchor label), the durable
seen-store, the scheduler that spins ONE Steel session up on the
visitor's vault profile, reads, and RELEASES it in full, the
active / waiting_login / paused_checkpoint states, the fbwatch
slot ladder (free 0 / red 1 / standard 1 / pro 3 / blue 5 /
blackout 10) and the SEPARATE watch_min daily budget. All of
that is untouched in shape; this module adds, on watches whose
record carries kind="mpwatch":

1. BEST-DEAL MATCHES. Each check filters the extracted listings
   through og_watch's relevance/place rules PLUS a hard price
   rule (a listing only counts when its price is known and at or
   under the watch ceiling), dedupes against the same seen-store,
   and keeps the watch's matches list ranked BEST DEAL FIRST
   (lowest price; ties go to the earlier-seen listing). A cheaper
   match later RE-ALERTS even when earlier matches were already
   reported: a brand-new cheaper listing alerts as a match, and a
   known listing whose price drops alerts as a drop — the same
   alert kinds og_watch already delivers at the top of the next
   chat, where delivery records the NEW og_notify kind
   "mp_match" with target {"view": "watch", "id": watch_id,
   "kind": "mpwatch"} (the Round 59 target shape).

2. ASK BEFORE MESSAGING. "Message this seller" parks its OWN
   approval (this module's durable per-visitor approval store,
   og_watch blob kind "mpapprovals"). Nothing sends on the ask.
   On approval, OG drafts a plain inquiry (the item/price
   context only) and sends it ONLY through the Round 27
   approval-gated browser flow on the visitor's logged-in
   profile: og_browser._attempt_post_draft types the exact draft
   and parks the send click at the browser's own gate, and
   og_browser._do_approve re-verifies and executes exactly that
   click, once. This module contains no direct page-driving send
   path of its own — the Round 27 functions are the only way a
   seller message ever leaves.

3. ASK BEFORE HAGGLING — a SEPARATE approval, never implied by
   a message approval (and vice versa; the suite pins both
   directions). The visitor sets a haggle ceiling; the offer
   draft is built at or below it (enforced when the draft is
   generated, when the approval is requested, and again at send
   time — an offer above the ceiling cannot be drafted, parked,
   or sent). If the seller agrees or proposes a meetup/payment,
   that becomes a FRESH approval item of type "deal" for the
   visitor: approving it sends NOTHING (there is nothing OG may
   send — OG never pays, never shares an address, never commits
   to a meetup); it only acknowledges the visitor will handle
   the deal themselves.

DRAFT GUARDS, STRUCTURAL. Both draft generators are fixed
templates interpolating only a sanitized title, the listed
price, and the offer amount. Titles are stripped of every
forbidden word before interpolation, and every finished draft
must pass _guard_draft (which rejects address / payment /
meetup language) before it can be parked or sent — a draft
that fails the guard is never created. The suite pins the
templates against the forbidden list directly.

STATE. Approvals ride og_watch's durable store (same Postgres
table / JSON file as watches), blob kind "mpapprovals":
{id -> record}. Watches themselves live in og_watch's watches
blob, so slots, minutes, states, /watch/status and the Goals
seat all stay one system.
"""

import logging
import re
import time
import uuid
from typing import Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse

import og_watch as W

logger = logging.getLogger(__name__)

KIND = "mpwatch"
APPROVALS_BLOB = "mpapprovals"
MATCH_CAP = 50

# Words/phrases a seller draft may NEVER contain: no address, no
# payment arrangements, no meetup commitment. Draft templates are
# fixed strings (below); titles are sanitized against this list
# before interpolation and finished drafts must pass the guard.
_FORBIDDEN_RE = re.compile(
    r"\b(address|meet|meetup|meet-up|pickup|pick\s+up|cash|venmo|"
    r"zelle|paypal|payment|pay\s+you|wire|deposit|come\s+to|"
    r"my\s+place|my\s+house|my\s+home|drop\s+off|deliver\s+to)\b",
    re.I)
_FORBIDDEN_WORDS = {
    "address", "meet", "meetup", "pickup", "cash", "venmo",
    "zelle", "paypal", "payment", "wire", "deposit", "deliver",
}


def _guard_draft(text: str) -> str:
    """Return the draft, or raise ValueError — a draft carrying
    address/payment/meetup language can never be parked or sent."""
    if _FORBIDDEN_RE.search(str(text or "")):
        raise ValueError("draft carries forbidden language")
    return str(text)


def _safe_title(title: str) -> str:
    words = [w for w in re.split(r"\s+", str(title or "").strip())
             if w.strip(".,!?").lower() not in _FORBIDDEN_WORDS]
    out = " ".join(words).strip(" .,")[:70]
    return out or "your listing"


# --- Draft generators (fixed templates, guarded) -------------------------------


def draft_inquiry(listing: Dict) -> str:
    """The plain first message: is-it-available, with the listing's
    own title/price as context. Nothing else can enter it."""
    title = _safe_title(listing.get("title") or "")
    price = W._fmt_price(listing.get("price"))
    return _guard_draft(
        f"Hi! Is this still available? I saw your listing "
        f"'{title}' listed at {price} and I'm interested. "
        f"Thanks!")


def draft_offer(listing: Dict, offer: float, ceiling: float) -> str:
    """The haggle offer. The ceiling is enforced HERE, at draft
    generation: an offer above the ceiling cannot be drafted."""
    offer = float(offer)
    ceiling = float(ceiling)
    if offer <= 0:
        raise ValueError("offer must be positive")
    if offer > ceiling:
        raise ValueError("offer is above the haggle ceiling")
    title = _safe_title(listing.get("title") or "")
    price = W._fmt_price(listing.get("price"))
    return _guard_draft(
        f"Hi! Would you take {W._fmt_price(offer)} for the "
        f"'{title}'? It's listed at {price}. I completely "
        f"understand if not — thanks for considering it!")


# --- The check diff: filtering, dedupe, best-deal ranking -----------------------


def _rank_key(match: Dict):
    price = match.get("price")
    return (price if price is not None else float("inf"),
            float(match.get("seq") or 0))


def _diff_mp(w: Dict, listings: Dict, alerts: list) -> None:
    """og_watch._diff_watch's mpwatch sibling. Called by
    og_watch._check_one ONLY for kind="mpwatch" watches, after
    the checkpoint/login-wall handling, with the listings
    og_watch._parse_listings extracted from the first results
    screen. Mutates w (seen-store + ranked matches) and appends
    match/drop alerts in og_watch's alert shape."""
    seen = w.get("seen") or {}
    matches = w.get("matches") or []
    by_id = {m.get("id"): m for m in matches}
    max_price = w.get("max_price")
    place = str(w.get("place") or "").lower()
    best_alerted = w.get("best_alerted")
    seq = int(w.get("seq") or 0)
    for lid, lst in listings.items():
        price = lst.get("price")
        prev = seen.get(lid)
        entry = {"price": price,
                 "alerted": (prev or {}).get("alerted"),
                 "title": lst.get("title", "")}
        # The price rule is HARD for mpwatch: with a ceiling set,
        # a listing counts only at a known price at/under it.
        in_range = (max_price is None
                    or (price is not None
                        and price <= float(max_price)))
        place_ok = True
        if place and lst.get("town"):
            place_ok = place in lst["town"].lower()
        match = in_range and place_ok and W._relevant(
            str(w.get("item") or ""), lst.get("title", ""))
        if match:
            rec = by_id.get(lid)
            if rec is None:
                seq += 1
                rec = {"id": lid, "title": lst.get("title", ""),
                       "price": price, "town": lst.get("town", ""),
                       "url": lst.get("url", ""), "seq": seq}
                by_id[lid] = rec
                matches.append(rec)
            else:
                rec["price"] = price
                rec["title"] = lst.get("title", "")
                rec["town"] = lst.get("town", "")
        if prev is None:
            if match:
                alerts.append({
                    "id": uuid.uuid4().hex[:12], "ts": W._now_iso(),
                    "delivered": False, "kind": "match",
                    "watch_id": w.get("id"),
                    "text": W._match_text(w, lst, drop_from=None)})
                entry["alerted"] = price
        else:
            if match and entry["alerted"] is not None \
                    and price is not None \
                    and price < float(entry["alerted"]):
                alerts.append({
                    "id": uuid.uuid4().hex[:12], "ts": W._now_iso(),
                    "delivered": False, "kind": "drop",
                    "watch_id": w.get("id"),
                    "text": W._match_text(
                        w, lst, drop_from=entry["alerted"])})
                entry["alerted"] = price
            elif match and entry["alerted"] is None:
                # Seen before but never in range (e.g. it dropped
                # under the ceiling): a NEW match — and when it is
                # cheaper than anything alerted before, it still
                # alerts even though matches were already reported.
                alerts.append({
                    "id": uuid.uuid4().hex[:12], "ts": W._now_iso(),
                    "delivered": False, "kind": "match",
                    "watch_id": w.get("id"),
                    "text": W._match_text(w, lst, drop_from=None)})
                entry["alerted"] = price
        if entry.get("alerted") is not None:
            if best_alerted is None or float(entry["alerted"]) < \
                    float(best_alerted):
                best_alerted = entry["alerted"]
        seen[lid] = entry
    while len(seen) > W.SEEN_CAP:
        seen.pop(next(iter(seen)))
    w["seen"] = seen
    w["seq"] = seq
    w["best_alerted"] = best_alerted
    matches.sort(key=_rank_key)
    w["matches"] = matches[:MATCH_CAP]


def find_match(uid: str, watch_id: str, listing_id: str):
    """(watch, match) for one stored match, or (None, None)."""
    for w in W._watches(uid):
        if w.get("id") == watch_id:
            for m in w.get("matches") or []:
                if m.get("id") == listing_id:
                    return w, m
            return w, None
    return None, None


def ranked_matches(uid: str, watch_id: str) -> list:
    w, _m = find_match(uid, watch_id, "")
    if w is None:
        for cand in W._watches(uid):
            if cand.get("id") == watch_id:
                w = cand
                break
    if not w:
        return []
    return sorted(w.get("matches") or [], key=_rank_key)


# --- Approvals: message and haggle are SEPARATE records --------------------------


def _approvals(uid: str) -> Dict:
    a = W._blob(uid, APPROVALS_BLOB, {})
    return a if isinstance(a, dict) else {}


def _save_approvals(uid: str, approvals: Dict) -> None:
    W._put_blob(uid, APPROVALS_BLOB, approvals)


def _notify_approval(uid: str, rec: Dict) -> None:
    """The parked approval also lands in the notification center
    (approval_needed, the alert family) with the Round 59 target
    shape. Fail-safe, like every notify producer."""
    try:
        import og_notify as _notify
        if not _notify.alerts_enabled(uid):
            return
        _notify.record(
            uid, "approval_needed", "OG needs your OK",
            rec.get("summary", ""),
            target={"view": "approvals", "id": rec.get("id", ""),
                    "kind": "mp_" + str(rec.get("type") or "")})
    except Exception:
        pass


def _new_approval(uid: str, rtype: str, watch: Dict, match: Dict,
                  draft: str, ceiling: Optional[float] = None,
                  offer: Optional[float] = None,
                  note: str = "") -> Dict:
    rec = {
        "id": uuid.uuid4().hex[:12], "type": rtype,
        "watch_id": watch.get("id"), "listing_id": match.get("id"),
        "listing_title": match.get("title", ""),
        "listing_url": match.get("url", ""),
        "price": match.get("price"),
        "draft": draft, "ceiling": ceiling, "offer": offer,
        "note": note, "status": "pending",
        "created": W._now_iso(), "decided": None,
    }
    rec["summary"] = _approval_summary(rec)
    approvals = _approvals(uid)
    approvals[rec["id"]] = rec
    _save_approvals(uid, approvals)
    _notify_approval(uid, rec)
    return rec


def _approval_summary(rec: Dict) -> str:
    title = rec.get("listing_title") or "the listing"
    if rec.get("type") == "message":
        return (f"Message the seller of '{title}' "
                f"({W._fmt_price(rec.get('price'))})? Draft: "
                f"\"{rec.get('draft', '')}\" Approving sends "
                f"exactly those words through the browser approval "
                f"flow — nothing else.")
    if rec.get("type") == "haggle":
        return (f"Haggle on '{title}' "
                f"({W._fmt_price(rec.get('price'))})? Offer draft "
                f"at {W._fmt_price(rec.get('offer'))} — at or "
                f"under your ceiling of "
                f"{W._fmt_price(rec.get('ceiling'))}: "
                f"\"{rec.get('draft', '')}\" This is a SEPARATE "
                f"approval from messaging the seller.")
    return (f"The seller of '{title}' responded: "
            f"{rec.get('note', '')} This is YOUR call — OG never "
            f"pays, never shares an address, and never agrees to "
            f"a meetup. Approving only marks that you've seen it; "
            f"nothing is sent.")


def pending_approvals(uid: str, rtype: str = "") -> list:
    out = [r for r in _approvals(uid).values()
           if r.get("status") == "pending"
           and (not rtype or r.get("type") == rtype)]
    out.sort(key=lambda r: str(r.get("created") or ""))
    return out


def request_message(uid: str, watch_id: str,
                    listing_id: str) -> List[Dict]:
    """'Message this seller': parks the message approval. Sends
    NOTHING — the draft leaves only on decide(approve), through
    the Round 27 flow."""
    watch, match = find_match(uid, watch_id, listing_id)
    if watch is None or match is None:
        return W._result(
            "OG WATCH", "Marketplace — no such match",
            "I don't have that listing in a Marketplace watch's "
            "matches — ask me what matched first, then say "
            "'message this seller' about one of those.")
    draft = draft_inquiry(match)
    rec = _new_approval(uid, "message", watch, match, draft)
    return W._result(
        "OG WATCH", "Marketplace — message needs your OK",
        rec["summary"] + " Say 'approve marketplace message' to "
        "send it, or 'decline marketplace message' to drop it. "
        "Nothing has been sent.")


def request_haggle(uid: str, watch_id: str, listing_id: str,
                   offer: float, ceiling: float) -> List[Dict]:
    """'Haggle': parks the SEPARATE haggle approval. The ceiling
    is the visitor's; the offer must sit at or under it — checked
    here and again inside the draft generator."""
    watch, match = find_match(uid, watch_id, listing_id)
    if watch is None or match is None:
        return W._result(
            "OG WATCH", "Marketplace — no such match",
            "I don't have that listing in a Marketplace watch's "
            "matches — haggling only rides on a watched match.")
    try:
        offer = float(offer)
        ceiling = float(ceiling)
    except (TypeError, ValueError):
        return W._result(
            "OG WATCH", "Marketplace — haggle needs numbers",
            "Tell me the offer and your ceiling, like 'offer "
            "$120, my ceiling is $130' — the offer can never go "
            "above the ceiling you set.")
    if offer > ceiling:
        return W._result(
            "OG WATCH", "Marketplace — above your ceiling",
            f"That offer ({W._fmt_price(offer)}) is above the "
            f"ceiling you set ({W._fmt_price(ceiling)}), so I "
            "won't even draft it. Lower the offer or raise the "
            "ceiling — your call, never mine.")
    draft = draft_offer(match, offer, ceiling)
    rec = _new_approval(uid, "haggle", watch, match, draft,
                        ceiling=ceiling, offer=offer)
    return W._result(
        "OG WATCH", "Marketplace — haggle needs your OK",
        rec["summary"] + " Say 'approve marketplace haggle' to "
        "send it, or 'decline marketplace haggle' to drop it. "
        "Messaging the seller is a different approval — this one "
        "covers only this offer.")


# --- The send: Round 27 ONLY ------------------------------------------------------


def _round27_send(uid: str, rec: Dict) -> List[Dict]:
    """Send one approved draft through the existing Round 27
    approval-gated browser flow on the visitor's logged-in
    profile: start (or reuse) the session ON the listing, let
    _attempt_post_draft type the EXACT draft and park the send
    click at the browser gate, then resolve that gate with
    _do_approve (which re-verifies the page and executes exactly
    that click, once). If no gated click gets parked (no message
    box on the page, a login wall), nothing sends and the blocks
    say so honestly."""
    import og_browser as B
    draft = _guard_draft(rec.get("draft") or "")
    url = rec.get("listing_url") or ""
    out: List[Dict] = []
    if not B._get_session(uid):
        out += B.browser_results(
            {"op": "start", "url": url,
             "goal": "message the seller",
             "post": {"url": url, "text": draft}},
            "message the seller", uid)
    else:
        sess = dict(B._get_session(uid) or {})
        sess["post_intent"] = {"text": draft, "url": url}
        B._set_session(uid, sess)
        out += B._attempt_post_draft(uid)
    pend = B._get_pending(uid)
    if pend and pend.get("kind") == "action":
        out += B._do_approve(uid)
        return out
    return out + W._result(
        "OG WATCH", "Marketplace — approved but not sent",
        "Your approval stands, but the browser flow never "
        "parked a send click (no message box on that page, or a "
        "log-in wall), so NOTHING was sent. Open the listing in "
        "my browser and I can draft it there for your YES.")


def decide(uid: str, approval_id: str, decision: str,
           sender=None) -> List[Dict]:
    """Resolve ONE approval. Approving a message approval sends
    ONLY that record's inquiry draft; approving a haggle approval
    sends ONLY that record's offer draft (after re-checking the
    offer against the ceiling); approving a deal approval sends
    nothing ever. One approval can never authorize another."""
    approvals = _approvals(uid)
    rec = approvals.get(approval_id)
    if rec is None or rec.get("status") != "pending":
        return W._result(
            "OG WATCH", "Marketplace — nothing to decide",
            "That Marketplace approval isn't waiting anymore — "
            "nothing was sent.")
    if decision != "approve":
        rec["status"] = "declined"
        rec["decided"] = W._now_iso()
        approvals[approval_id] = rec
        _save_approvals(uid, approvals)
        return W._result(
            "OG WATCH", "Marketplace — dropped",
            "Dropped. Nothing was sent to the seller.")
    if rec.get("type") == "deal":
        rec["status"] = "acknowledged"
        rec["decided"] = W._now_iso()
        approvals[approval_id] = rec
        _save_approvals(uid, approvals)
        return W._result(
            "OG WATCH", "Marketplace — your deal, your move",
            "Marked as seen. OG sends nothing on a deal — no "
            "payment, no address, no meetup commitment. Whatever "
            "happens next with the seller is yours to arrange.")
    if rec.get("type") == "haggle":
        # Send-time ceiling re-check: even a tampered record
        # cannot send an offer above its ceiling.
        try:
            offer = float(rec.get("offer"))
            ceiling = float(rec.get("ceiling"))
        except (TypeError, ValueError):
            offer, ceiling = 1.0, 0.0
        if offer > ceiling:
            rec["status"] = "refused"
            rec["decided"] = W._now_iso()
            approvals[approval_id] = rec
            _save_approvals(uid, approvals)
            return W._result(
                "OG WATCH", "Marketplace — refused at the ceiling",
                "That offer sits above its haggle ceiling, so it "
                "was NOT sent. Set a fresh haggle with numbers "
                "that line up.")
        _guard_draft(rec.get("draft") or "")
    send = sender or _round27_send
    out = send(uid, rec)
    rec["status"] = "approved_sent"
    rec["decided"] = W._now_iso()
    approvals[approval_id] = rec
    _save_approvals(uid, approvals)
    return out


# --- Seller replies: agreement / meetup / payment = a FRESH approval --------------


_SELLER_TERMS_RE = re.compile(
    r"\b(sounds good|deal\b|agreed|i accept|yes\b|ok\b|okay|"
    r"meet|meetup|pickup|pick up|address|cash|venmo|zelle|"
    r"paypal|pay\b|payment|come by|come get|drop off)\b", re.I)


def detect_seller_terms(text: str) -> bool:
    """True when a seller reply agrees to a deal or proposes a
    meetup/payment — the trigger for a fresh deal approval."""
    return bool(_SELLER_TERMS_RE.search(str(text or "")))


def record_seller_reply(uid: str, approval_id: str,
                        text: str) -> Optional[Dict]:
    """A reply to an approved message/haggle: when it carries
    agreement or meetup/payment terms, park a FRESH 'deal'
    approval for the visitor. OG acts on nothing itself."""
    base = _approvals(uid).get(approval_id)
    if base is None:
        return None
    if not detect_seller_terms(text):
        return None
    watch = {"id": base.get("watch_id")}
    match = {"id": base.get("listing_id"),
             "title": base.get("listing_title"),
             "url": base.get("listing_url"),
             "price": base.get("price")}
    note = " ".join(str(text).split())[:200]
    return _new_approval(uid, "deal", watch, match, draft="",
                         note=note)


# --- Chat claims --------------------------------------------------------------------

_CREATE_HINT_RE = re.compile(r"marketplace", re.I)
_MESSAGE_RE = re.compile(
    r"\bmessage (?:this|the) seller\b\s*(.*)$", re.I)
_HAGGLE_RE = re.compile(r"\bhaggle\b\s*(.*)$", re.I)
_DECIDE_RE = re.compile(
    r"\b(approve|decline)\s+(?:the\s+)?marketplace\s+"
    r"(message|haggle|deal)\b", re.I)
_OFFER_RE = re.compile(
    r"(?:offer|at)\s*\$\s*([\d,]+(?:\.\d{1,2})?)", re.I)
_CEILING_RE = re.compile(
    r"ceiling\s*(?:is\s*)?\$\s*([\d,]+(?:\.\d{1,2})?)", re.I)


def _pick_match(uid: str, words: str):
    """Resolve a seller ask to (watch, match): words naming a
    listing title/id win; otherwise the single best match across
    mpwatch watches — and ambiguity (several watches with
    matches, no words) resolves to nothing, honestly."""
    words = " ".join(str(words or "").lower().split())
    cands = []
    for w in W._watches(uid):
        if w.get("kind") != KIND:
            continue
        for m in w.get("matches") or []:
            cands.append((w, m))
    if words:
        hits = [(w, m) for w, m in cands
                if words in str(m.get("title") or "").lower()
                or words == str(m.get("id") or "")]
        if len(hits) == 1:
            return hits[0]
        if hits:
            return None, None
    ranked = sorted(cands, key=lambda wm: _rank_key(wm[1]))
    watches_with = {w.get("id") for w, _m in ranked}
    if len(watches_with) == 1 and ranked:
        return ranked[0]
    if len(ranked) == 1:
        return ranked[0]
    return None, None


def _claim_job(message: str, uid: str) -> Optional[Dict]:
    raw = str(message or "")
    if not uid:
        return None
    m = _DECIDE_RE.search(raw)
    if m:
        pend = pending_approvals(uid, m.group(2).lower())
        if pend:
            return {"op": "decide", "id": pend[0].get("id"),
                    "decision": m.group(1).lower()}
        return {"op": "decide_none", "type": m.group(2).lower()}
    m = _MESSAGE_RE.search(raw)
    if m:
        watch, match = _pick_match(uid, m.group(1) or "")
        if watch and match:
            return {"op": "message", "watch_id": watch.get("id"),
                    "listing_id": match.get("id")}
        return {"op": "message_none"}
    m = _HAGGLE_RE.search(raw)
    if m:
        tail = m.group(1) or ""
        om = _OFFER_RE.search(raw)
        cm = _CEILING_RE.search(raw)
        if not om or not cm:
            return {"op": "haggle_numbers"}
        words = tail[:om.start()] if om else tail
        watch, match = _pick_match(uid, words)
        if watch and match:
            return {"op": "haggle", "watch_id": watch.get("id"),
                    "listing_id": match.get("id"),
                    "offer": float(om.group(1).replace(",", "")),
                    "ceiling": float(cm.group(1).replace(",", ""))}
        return {"op": "message_none"}
    # Creation rides og_watch's own parser — the same natural
    # ask — and lands as kind="mpwatch".
    job = W._parse_create(raw)
    if job and _CREATE_HINT_RE.search(raw):
        job = dict(job)
        job["kind"] = KIND
        return job
    return None


def mp_results(job: Dict, uid: str, tier: str) -> List[Dict]:
    op = job.get("op")
    if op == "create":
        return W.watch_results(job, uid, tier)
    if op == "message":
        return request_message(uid, job.get("watch_id") or "",
                               job.get("listing_id") or "")
    if op == "message_none":
        return W._result(
            "OG WATCH", "Marketplace — which listing?",
            "Tell me which match you mean (its title), and I'll "
            "line up the message for that seller only. Nothing "
            "sends without its own approval.")
    if op == "haggle":
        return request_haggle(uid, job.get("watch_id") or "",
                              job.get("listing_id") or "",
                              job.get("offer"), job.get("ceiling"))
    if op == "haggle_numbers":
        return W._result(
            "OG WATCH", "Marketplace — haggle needs numbers",
            "Say it like 'haggle on the leather couch, offer "
            "$120, my ceiling is $130'. The ceiling is yours and "
            "the offer never goes above it.")
    if op == "decide":
        return decide(uid, job.get("id") or "",
                      job.get("decision") or "")
    if op == "decide_none":
        return W._result(
            "OG WATCH", "Marketplace — nothing waiting",
            f"No marketplace {job.get('type', '')} approval is "
            "waiting right now — nothing was sent.")
    return W._result("OG WATCH", "Marketplace",
                     "That Marketplace ask didn't parse.")


# --- The seam (piggybacked from og_watch.install_watch_tools) ------------------------


_pending: Dict = {"job": None}


def install_mp_tools(agent_instance, get_uid, get_tier):
    """Wrap the agent hooks AFTER og_watch's wrapper (og_watch
    installs this, fail-safe): mpwatch claims run here, every
    other message delegates to the watch wrapper untouched."""
    if getattr(agent_instance, "_og_mpwatch_installed", False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["job"] = None
        try:
            uid = get_uid()
            if uid:
                job = _claim_job(str(message), uid)
                if job:
                    _pending["job"] = job
                    if isinstance(intent, dict):
                        intent["needs_code_generation"] = False
                        if not intent.get("needs_web_search"):
                            intent["needs_web_search"] = True
                            intent["search_query"] = str(
                                message).strip()
        except Exception as e:
            logger.warning(f"MPWatch trigger check failed: {e}")
            _pending["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _pending.get("job")
        _pending["job"] = None
        if job:
            try:
                return mp_results(job, get_uid(), get_tier())
            except Exception as e:
                logger.warning(f"MPWatch job failed: {e}")
                return W._result(
                    "OG WATCH", "Marketplace — glitch",
                    "That Marketplace ask glitched on my side — "
                    "nothing was set up and nothing was sent.")
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_mpwatch_installed = True


# --- Routes (registered fail-safe from og_library) ------------------------------------


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


def _uid_of(request: Request) -> str:
    try:
        return request.cookies.get("ogai_uid", "") or ""
    except Exception:
        return ""


def _blocks_text(blocks) -> str:
    return "\n".join(str(b.get("body", "")) for b in blocks or [])


def register_mp_routes(app):

    @app.get("/mpwatch/approvals")
    async def mp_approvals(request: Request):
        uid = _uid_of(request)
        if not uid:
            return {"approvals": []}
        items = [r for r in _approvals(uid).values()]
        items.sort(key=lambda r: str(r.get("created") or ""),
                   reverse=True)
        return {"approvals": [{
            "id": r.get("id"), "type": r.get("type"),
            "status": r.get("status"),
            "listing_title": r.get("listing_title"),
            "listing_url": r.get("listing_url"),
            "price": r.get("price"), "draft": r.get("draft"),
            "offer": r.get("offer"), "ceiling": r.get("ceiling"),
            "summary": r.get("summary") or _approval_summary(r),
            "created": r.get("created"),
        } for r in items]}

    @app.post("/mpwatch/message")
    async def mp_message(request: Request):
        uid = _uid_of(request)
        if not uid:
            return JSONResponse({"ok": False,
                                 "error": "no visitor cookie"},
                                status_code=400)
        body = await _json_body(request)
        blocks = request_message(uid, str(body.get("watch_id") or ""),
                                 str(body.get("listing_id") or ""))
        pend = pending_approvals(uid, "message")
        return {"ok": True, "body": _blocks_text(blocks),
                "approval": pend[-1] if pend else None}

    @app.post("/mpwatch/haggle")
    async def mp_haggle(request: Request):
        uid = _uid_of(request)
        if not uid:
            return JSONResponse({"ok": False,
                                 "error": "no visitor cookie"},
                                status_code=400)
        body = await _json_body(request)
        blocks = request_haggle(uid, str(body.get("watch_id") or ""),
                                str(body.get("listing_id") or ""),
                                body.get("offer"), body.get("ceiling"))
        pend = pending_approvals(uid, "haggle")
        return {"ok": True, "body": _blocks_text(blocks),
                "approval": pend[-1] if pend else None}

    @app.post("/mpwatch/decide")
    async def mp_decide(request: Request):
        uid = _uid_of(request)
        if not uid:
            return JSONResponse({"ok": False,
                                 "error": "no visitor cookie"},
                                status_code=400)
        body = await _json_body(request)
        blocks = decide(uid, str(body.get("id") or ""),
                        str(body.get("decision") or ""))
        return {"ok": True, "body": _blocks_text(blocks)}

    @app.post("/mpwatch/seller-reply")
    async def mp_seller_reply(request: Request):
        uid = _uid_of(request)
        if not uid:
            return JSONResponse({"ok": False,
                                 "error": "no visitor cookie"},
                                status_code=400)
        body = await _json_body(request)
        rec = record_seller_reply(uid, str(body.get("id") or ""),
                                  str(body.get("text") or ""))
        return {"ok": True, "deal_approval": rec}
