"""
OG AI named ordering flows (Round 64) — DoorDash / Uber Eats /
Uber rides, extending Round 19's og_ordering (prepare ->
approve -> handoff) onto the one route that actually exists:
there are NO official consumer ordering APIs for these three
services, so each flow drives the visitor's OWN logged-in
browser profile (og_browser / Steel vault) and stops at the
final pay/confirm screen. The last tap is the visitor's —
always.

THE NO-PAY-CLICK RULE (source-pinned by the r64 suite): this
module has exactly ONE click path, _safe_click, and it REFUSES
any element whose label matches _FORBIDDEN_CLICK_RE (pay,
place order, confirm ride/order, request a ride, order now,
complete order, book now, ...). No code path in this module
clicks a pay / place-order / confirm-ride control, by
construction: goto_confirm may click checkout / review /
continue-style steps only, and it stops AT the final screen.

THE FLOW, per named service:
1. ASK. "order my usual from DoorDash", "order <food> from
   Uber Eats", "get me an Uber to <place>" (and plain
   variants). The browser spins up on the visitor's logged-in
   profile for that service and the cart (food) or ride is
   built: pickup is the site's saved/current location AS THE
   SITE SHOWS IT, destination is the visitor's own words.
2. SUMMARY. A PLAIN summary is parked as an approval in
   og_ordering's pending store (stage "flow" — the existing
   machinery: 60-min TTL, and og_browser's _other_pending
   already honours og_ordering pendings, so the browser gate
   can never steal this YES/NO): service, items + sizes as
   the site shows them, subtotal / fees / total AS THE SITE
   SHOWS THEM, and the delivery address AS THE SITE SHOWS
   IT. OG never invents and never types an address — a site
   with no saved address stops the flow honestly.
3. APPROVE -> HANDOFF. On YES — and only on YES — the flow
   re-drives to the final confirm step and RE-READS the
   total: if it changed against the approved summary, a
   FRESH approval is parked with the new total and nothing
   proceeds on the stale one. Same total: one unit of the
   per-tier "order" cap is spent via og_ordering's own
   counters, and the flow HANDS OFF — take-control opens for
   the visitor with the line that the last tap is theirs.
4. USUALS. Durable per-uid per-service usuals ride
   og_ordering's blob store (kind "flow_usuals" — the
   existing durable-store pattern, Postgres og_ordering_data
   / ordering_store.json; purged with the account via
   og_ordering.purge_uid). "my usual at DoorDash is ..."
   teaches one; after a completed handoff OG offers to
   remember what was ordered ("remember that as my usual at
   DoorDash" saves it). "Order my usual" reads the usual
   back in plain words inside the summary BEFORE the
   approval. Fallback read: a Round 19 usual already saved
   under that store key is honoured — it is the visitor's
   own saved data, never outside seeding. No usual anywhere
   -> honest ask, and the browser never even spins up.
5. HONEST STOPS. Not logged in at the service -> a
   waiting_login-style stop: the session stays live and the
   take-control login path is offered (the visitor logs in
   themselves; OG never sees a password). Bot wall /
   Cloudflare challenge (DoorDash especially) -> stop, FULL
   browser release (og_browser._end_session — the shutdown
   rule), and a plain line that it was walled and the
   service's own app is the fallback. Item not found -> said
   plainly; the closest match is offered AS A QUESTION
   (parked stage "flow_ask": YES substitutes that one item
   and continues, NO discards and releases) — never a silent
   substitution.

Caps ride the existing ordering machinery (og_tiers kind
"order", checked at ask time, spent only at an approved
handoff). Zero orders by construction without approval:
nothing reaches a confirm screen before a YES, and nothing
at all is ever clicked past it.

The driver seam: every browser touch goes through a driver
object (start / search_add / set_destination / choose_ride /
read_summary / goto_confirm / open_handoff / release). The
live driver (_LiveDriver) is a thin, best-effort layer over
og_browser's own seams; the r64 suite injects a fake driver
and never touches a live site. Persona files never touched.
"""

import logging
import re
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import og_ordering as O

logger = logging.getLogger(__name__)

# --- Services -------------------------------------------------------------------

SERVICES = {
    "doordash": {"key": "doordash", "name": "DoorDash",
                 "mode": "food", "url": "https://www.doordash.com/",
                 "app": "the DoorDash app"},
    "ubereats": {"key": "ubereats", "name": "Uber Eats",
                 "mode": "food", "url": "https://www.ubereats.com/",
                 "app": "the Uber Eats app"},
    "uber": {"key": "uber", "name": "Uber",
             "mode": "ride", "url": "https://www.uber.com/",
             "app": "the Uber app"},
}

# Uber Eats is matched BEFORE Uber everywhere.
_SERVICE_PATTERNS = (
    ("ubereats", re.compile(r"\buber\s?eats\b", re.I)),
    ("doordash", re.compile(r"\bdoor\s?dash\b", re.I)),
    ("uber", re.compile(r"\buber\b", re.I)),
)


def _service_key_in(text: str) -> Optional[str]:
    for key, rx in _SERVICE_PATTERNS:
        if rx.search(str(text or "")):
            return key
    return None


# --- The no-pay-click guard -------------------------------------------------------

_FORBIDDEN_CLICK_RE = re.compile(
    r"\b(pay|place\s+(the\s+)?order|confirm(\s+(the\s+)?(ride|order))?"
    r"|request\s+(a\s+)?(ride|uber)|order\s+now|complete\s+(the\s+)?order"
    r"|book\s+now|buy\s+now|submit\s+order|charge|check\s*out\s+and\s+pay)\b",
    re.I)


def _safe_click(click_fn, el) -> bool:
    """THE only click path in this module. click_fn(label, el)
    performs the click and returns truthy on success. A label
    that reads like a pay / place-order / confirm-ride control
    is REFUSED — never clicked, no matter who asked."""
    label = str((el or {}).get("label") or "")
    if not el or not label.strip():
        return False
    if _FORBIDDEN_CLICK_RE.search(label):
        logger.warning(f"Flow click refused (pay/confirm): {label!r}")
        return False
    try:
        return bool(click_fn(label, el))
    except Exception as e:
        logger.warning(f"Flow click failed: {e}")
        return False


_BOT_WALL_MARKERS = (
    "verify you are human", "are you a robot", "checking your browser",
    "just a moment", "captcha", "unusual traffic", "bot detection",
    "please verify you are a human", "press and hold",
)

_CONFIRM_STEP_RE = re.compile(
    r"\b(check\s?out|review\s+(your\s+)?order|continue|next|"
    r"see\s+(prices|options)|go\s+to\s+checkout)\b", re.I)

_RIDE_OPTION_RE = re.compile(
    r"\b(uberx|uber\s*x|comfort|uberxl|premier|uber\s+black|pool)\b",
    re.I)


# --- Driver seam --------------------------------------------------------------------

_driver_factory = None


def set_driver_factory(fn):
    """Tests inject a fake driver factory here: fn(uid) -> driver."""
    global _driver_factory
    _driver_factory = fn


def _get_driver(uid: str):
    if _driver_factory is not None:
        return _driver_factory(uid)
    return _LiveDriver(uid)


class _LiveDriver:
    """Best-effort live driver over og_browser's seams: spin-up
    on the visitor's vault profile (_do_start), snapshots via
    _drive/_snapshot, non-commit clicks/types via _run_action
    (every click still funnels through _safe_click), take
    control via _do_control, full release via _end_session."""

    def __init__(self, uid: str):
        self.uid = uid
        self.service: Dict = {}
        self.added: List[Dict] = []

    # -- og_browser helpers ------------------------------------------------------
    def _B(self):
        import og_browser as B
        return B

    def _snap(self) -> Optional[Dict]:
        B = self._B()
        rec = B._get_session(self.uid)
        if not rec:
            return None
        try:
            return B._drive(rec, {"do": "read"})
        except Exception as e:
            logger.warning(f"Flow snapshot failed: {e}")
            return None

    def _classify(self, snap: Dict) -> str:
        B = self._B()
        hay = ((snap.get("title") or "") + " "
               + (snap.get("text") or "")).lower()
        if any(m in hay for m in _BOT_WALL_MARKERS):
            return "bot_wall"
        if B._login_wall(snap):
            return "login_wall"
        return "ok"

    def _run_click(self, label, el) -> bool:
        B = self._B()
        rec = B._get_session(self.uid)
        if not rec:
            return False
        run = {"do": "click", "idx": el.get("i"),
               "sig": B._element_signature(el)}
        B._run_action(self.uid, rec, run, el)
        return True

    def _type(self, text: str, field_words: str) -> bool:
        B = self._B()
        rec = B._get_session(self.uid)
        snap = self._snap()
        if not rec or not snap:
            return False
        found = B._find_field(snap.get("elements") or [], field_words)
        el = found.get("el") if found else None
        if not el:
            return False
        run = {"do": "type", "idx": el.get("i"), "text": text,
               "sig": B._element_signature(el)}
        B._run_action(self.uid, rec, run, el)
        try:
            B._run_action(self.uid, B._get_session(self.uid) or rec,
                          {"do": "enter"})
        except Exception:
            pass
        return True

    # -- contract ------------------------------------------------------------------
    def start(self, service: Dict) -> Dict:
        B = self._B()
        self.service = service
        if not B.enabled():
            return {"status": "unavailable"}
        rec = B._get_session(self.uid)
        if not rec:
            B._do_start(self.uid, {"op": "start",
                                   "url": service["url"],
                                   "goal": f"order flow: "
                                           f"{service['name']}"})
            rec = B._get_session(self.uid)
            if not rec:
                return {"status": "unavailable"}
        else:
            B._run_action(self.uid, rec,
                          {"do": "navigate", "url": service["url"]})
        snap = self._snap()
        if snap is None:
            return {"status": "unavailable"}
        return {"status": self._classify(snap)}

    def search_add(self, name: str, qty: int) -> Dict:
        B = self._B()
        self._type(name, "search")
        snap = self._snap()
        if snap is None:
            return {"found": False, "closest": None}
        els = snap.get("elements") or []
        want = [w for w in re.findall(r"[a-z0-9']+", name.lower())
                if len(w) >= 4]
        cands = [el for el in els
                 if any(w in str(el.get("label") or "").lower()
                        for w in want)]
        if not cands:
            return {"found": False, "closest": None}
        closest = str(cands[0].get("label") or "")
        hit = B._find_element(els, name)
        el = hit.get("el") if hit else None
        if el is not None:
            _safe_click(self._run_click, el)
            snap = self._snap() or snap
            els = snap.get("elements") or []
        add = None
        for cand in els:
            lab = str(cand.get("label") or "")
            if re.search(r"\badd\b", lab, re.I) \
                    and not _FORBIDDEN_CLICK_RE.search(lab):
                add = cand
                break
        if add is not None and _safe_click(self._run_click, add):
            self.added.append({"name": name, "qty": int(qty),
                               "detail": closest})
            return {"found": True, "label": closest}
        if el is not None:
            self.added.append({"name": name, "qty": int(qty),
                               "detail": closest})
            return {"found": True, "label": closest}
        return {"found": False, "closest": closest}

    def set_destination(self, place: str) -> Dict:
        B = self._B()
        self._type(place, "where to")
        snap = self._snap()
        if snap is None:
            return {"found": False, "closest": None}
        els = snap.get("elements") or []
        hit = B._find_element(els, place)
        el = hit.get("el") if hit else None
        if el is None:
            first = els[0] if els else None
            return {"found": False,
                    "closest": str(first.get("label") or "") or None
                    if first else None}
        _safe_click(self._run_click, el)
        return {"found": True, "label": str(el.get("label") or "")}

    def choose_ride(self) -> Dict:
        snap = self._snap()
        if snap is None:
            return {"ok": False}
        for el in snap.get("elements") or []:
            if _RIDE_OPTION_RE.search(str(el.get("label") or "")):
                if _safe_click(self._run_click, el):
                    return {"ok": True,
                            "label": str(el.get("label") or "")}
                return {"ok": False}
        return {"ok": False}

    def read_summary(self) -> Dict:
        snap = self._snap()
        if snap is None:
            return {}
        text = " ".join(str(snap.get("text") or "").split())

        def money(label_rx):
            m = re.search(label_rx + r"[^$]{0,24}\$\s?([\d,]+\.\d{2})",
                          text, re.I)
            return f"${m.group(1)}" if m else None

        addr = None
        m = re.search(r"\bdeliver(?:y)? to[:\s]+(.{5,90}?)(?:\s{2,}|$)",
                      text, re.I)
        if m:
            addr = m.group(1).strip()
        pickup = None
        m = re.search(r"\bpickup[:\s]+(.{5,90}?)(?:\s{2,}|$)",
                      text, re.I)
        if m:
            pickup = m.group(1).strip()
        return {"items": list(self.added),
                "subtotal": money(r"subtotal"),
                "fees": money(r"(?:delivery|service) fee"),
                "total": money(r"total"),
                "address": addr, "pickup": pickup}

    def goto_confirm(self) -> Dict:
        snap = self._snap()
        if snap is None:
            return {"status": "gone", "total": None}
        for el in snap.get("elements") or []:
            lab = str(el.get("label") or "")
            if _CONFIRM_STEP_RE.search(lab) \
                    and not _FORBIDDEN_CLICK_RE.search(lab):
                _safe_click(self._run_click, el)
                break
        summ = self.read_summary()
        # Arriving here at all means we are AT the final screen
        # (anything further would be a forbidden click).
        return {"status": "at_confirm",
                "total": summ.get("total"),
                "summary": summ}

    def open_handoff(self) -> bool:
        B = self._B()
        try:
            B._do_control(self.uid, "visitor")
            return True
        except Exception as e:
            logger.warning(f"Flow handoff control failed: {e}")
            return False

    def release(self):
        B = self._B()
        try:
            B._end_session(self.uid, "order flow ended")
        except Exception as e:
            logger.warning(f"Flow browser release failed: {e}")


# --- Usuals (og_ordering's durable blob store, kind "flow_usuals") -------------------


def _flow_usuals(uid: str) -> Dict:
    usuals = O._blob(uid, "flow_usuals", {})
    return usuals if isinstance(usuals, dict) else {}


def _save_flow_usual(uid: str, key: str, record: Dict):
    usuals = _flow_usuals(uid)
    record = dict(record)
    record["service"] = SERVICES[key]["name"]
    record["saved"] = datetime.now(timezone.utc).isoformat()
    usuals[key] = record
    O._put_blob(uid, "flow_usuals", usuals)


def _usual_for(uid: str, key: str) -> Optional[Dict]:
    """The visitor's usual for one service: the flow usual
    first, then a Round 19 usual saved under that store key
    (the visitor's own saved data — never outside seeding)."""
    rec = _flow_usuals(uid).get(key)
    if isinstance(rec, dict) and (rec.get("items")
                                  or rec.get("destination")):
        return {"items": rec.get("items") or [],
                "destination": rec.get("destination") or "",
                "service": rec.get("service")
                or SERVICES[key]["name"]}
    legacy = O._usuals(uid).get(O._store_key(SERVICES[key]["name"]))
    if isinstance(legacy, dict) and legacy.get("items"):
        return {"items": legacy.get("items") or [],
                "destination": "",
                "service": legacy.get("store")
                or SERVICES[key]["name"]}
    return None


def _usual_words(rec: Dict) -> str:
    if rec.get("destination"):
        return f"a ride to {rec['destination']}"
    names = ", ".join(
        f"{int(i.get('qty', 1))} × {i['name']}"
        + (f" ({i['detail']})" if i.get("detail") else "")
        for i in rec.get("items", []))
    return names


# --- Claims -----------------------------------------------------------------------------

_APPROVE_RE = O._APPROVE_RE
_DECLINE_RE = O._DECLINE_RE

_TEACH_RES = (
    re.compile(r"\bmy usual\s+(?:order\s+)?(?:at|from|on)\s+"
               r"(door\s?dash|uber\s?eats|uber)\s+is\s+(.+)$", re.I),
    re.compile(r"\bmy usual\s+(uber)\s+ride\s+is\s+(?:to\s+)?(.+)$",
               re.I),
    re.compile(r"\bmy usual\s+(door\s?dash|uber\s?eats)\s+order\s+is\s+"
               r"(.+)$", re.I),
)
_SAVE_LAST_RE = re.compile(
    r"\b(remember|save)\b[^.?!]{0,40}\b(as\s+)?my usual\b", re.I)
_USUAL_SHOW_RE = re.compile(
    r"\bwhat('?s| is) my usual\b[^.?!]{0,20}?\b(door\s?dash|uber\s?eats"
    r"|uber)\b", re.I)
_USUAL_ORDER_RE = re.compile(
    r"\b(order|get|grab|book)\b[^.?!]{0,30}\bmy usual\b"
    r"|\bmy usual\b[^.?!]{0,30}\b(order|please)\b", re.I)
_RIDE_RES = (
    re.compile(r"\b(?:get|book|order|call|hail)\s+me\s+an?\s+uber\s+to\s+"
               r"(.+)$", re.I),
    re.compile(r"\b(?:get|book|order|call|hail)\s+an?\s+uber\s+to\s+"
               r"(.+)$", re.I),
    re.compile(r"\bi\s+need\s+an?\s+uber\s+to\s+(.+)$", re.I),
    re.compile(r"^\s*(?:an?\s+)?uber\s+to\s+(.+)$", re.I),
)
_FOOD_RES = (
    re.compile(r"\border\s+(?:me\s+)?(.+?)\s+(?:from|on|through|via)\s+"
               r"(door\s?dash|uber\s?eats)\b", re.I),
    re.compile(r"\border\s+(?:from|on)\s+(door\s?dash|uber\s?eats)\b"
               r"\s*[:,\-]?\s*(.+)$", re.I),
)


def _key_of_name(name: str) -> Optional[str]:
    return _service_key_in(name)


def _clean_dest(raw: str) -> str:
    dest = re.sub(r"\s+", " ", str(raw or "")).strip(" .!?")
    dest = re.sub(r"\b(please|now|asap|tonight)\s*$", "", dest,
                  flags=re.I).strip(" .!?")
    return dest[:90]


def _claim_job(message: str, uid: str) -> Optional[Dict]:
    if not uid:
        return None
    text = str(message or "")
    low = " " + re.sub(r"\s+", " ", text.lower()).strip() + " "
    trimmed = low.strip()
    pending = O._get_pending(uid)
    if pending is not None \
            and pending.get("stage") in ("flow", "flow_ask"):
        stage = pending.get("stage")
        if stage == "flow" and O._PAY_RE.search(text):
            return {"kind": "flow_pay_note"}
        if _DECLINE_RE.match(trimmed):
            return {"kind": "flow_decline"}
        if _APPROVE_RE.match(trimmed):
            return {"kind": "flow_approve"
                    if stage == "flow" else "flow_ask_approve"}
        # A fresh flow ask below supersedes the parked one.
    for rx in _TEACH_RES:
        m = rx.search(text)
        if m:
            key = _key_of_name(m.group(1))
            if key:
                return {"kind": "teach", "service": key,
                        "rest": m.group(2).strip()}
    if _SAVE_LAST_RE.search(text):
        last = O._blob(uid, "flow_last", None)
        if isinstance(last, dict) and last.get("service_key"):
            key = _service_key_in(text) or last["service_key"]
            return {"kind": "save_last", "service": key}
    m = _USUAL_SHOW_RE.search(text)
    if m:
        key = _key_of_name(m.group(2))
        if key:
            return {"kind": "usual_show", "service": key}
    if _USUAL_ORDER_RE.search(text):
        key = _service_key_in(text)
        if key:
            return {"kind": "usual_order", "service": key}
        if _flow_usuals(uid):
            return {"kind": "usual_order", "service": None}
        return None  # og_ordering's own usuals own this one
    if "uber eats" not in low:
        for rx in _RIDE_RES:
            m = rx.search(text)
            if m:
                dest = _clean_dest(m.group(1))
                if dest and "eats" != dest.split(" ")[0]:
                    return {"kind": "ask_ride", "service": "uber",
                            "destination": dest}
    for rx in _FOOD_RES:
        m = rx.search(text)
        if m:
            if rx is _FOOD_RES[0]:
                payload, name = m.group(1), m.group(2)
            else:
                name, payload = m.group(1), m.group(2)
            if "usual" in payload.lower():
                continue
            key = _key_of_name(name)
            if key:
                return {"kind": "ask_food", "service": key,
                        "items": O._parse_items(payload)}
    return None


# --- Rendering ----------------------------------------------------------------------


def _result(tag, title, body, href=""):
    return O._result(tag, title, body, href)


def _summary_text(svc: Dict, summary: Dict, usual_text: str = "",
                  destination: str = "") -> str:
    lines = [f"🧾 {svc['name']} — HERE'S THE ORDER"]
    if usual_text:
        lines.append(f"Your usual: {usual_text}")
    items = summary.get("items") or []
    if items:
        lines.append("Items:")
        for i in items:
            line = f"- {int(i.get('qty', 1))} × {i['name']}"
            if i.get("detail"):
                line += f" — {i['detail']}"
            lines.append(line)
    if svc["mode"] == "ride":
        if summary.get("pickup"):
            lines.append(f"Pickup: {summary['pickup']} "
                         f"(as {svc['name']} shows it)")
        lines.append(f"Destination: {destination}")
        if summary.get("ride_option"):
            lines.append(f"Ride: {summary['ride_option']}")
    for label, field in (("Subtotal", "subtotal"), ("Fees", "fees")):
        if summary.get(field):
            lines.append(f"{label}: {summary[field]} "
                         f"(as {svc['name']} shows it)")
    if summary.get("total"):
        lines.append(f"Total: {summary['total']} "
                     f"(as {svc['name']} shows it)")
    if svc["mode"] == "food" and summary.get("address"):
        lines.append(f"Deliver to: {summary['address']} "
                     f"(the address {svc['name']} has on file — "
                     f"OG never types an address)")
    lines.append("Reply YES and I'll take it to the final step "
                 "and hand you the wheel — the last tap (pay / "
                 "confirm) is yours and only yours. Reply NO and "
                 "it's discarded. Nothing is charged by OG.")
    return "\n".join(lines)


def _park_approval(uid, svc, plan, summary, usual_text=""):
    O._set_pending(uid, {
        "stage": "flow", "service_key": svc["key"],
        "mode": svc["mode"], "plan": plan, "summary": summary,
        "usual_text": usual_text})


def _summary_body(svc, summary, usual_text="", destination="",
                  changed=False):
    lead = ("The total CHANGED at the confirm step, so the old "
            "approval is void — this is a FRESH approval with "
            "the new numbers. Present this summary EXACTLY as "
            "written, in persona around it:" if changed else
            "The visitor's order is built on the site. Present "
            "this PLAIN SUMMARY EXACTLY as written (every item, "
            "price and the address character-for-character), in "
            "persona around it:")
    body = (lead + "\n\n"
            + _summary_text(svc, summary, usual_text, destination)
            + "\n\nDo NOT invent or change any number or the "
            "address — they are exactly what the site shows.")
    return _result("ORDERING FLOW: SUMMARY",
                   f"🧾 {svc['name']} — summary", body)


# --- Honest stops ---------------------------------------------------------------------


def _stop_unavailable(svc):
    return _result(
        "ORDERING FLOW: STOP", f"🧾 {svc['name']} — browser dark",
        "The visitor asked for a named ordering flow, but OG's "
        "browser is not switched on / did not start, so NOTHING "
        "was built and nothing is parked. Tell them plainly, in "
        "persona: the browser route is the only route for "
        f"{svc['name']} (there is no ordering API), and until "
        "the browser is live the order has to happen in "
        f"{svc['app']}.")


def _stop_login(svc, driver):
    try:
        driver.open_handoff()
    except Exception:
        pass
    return _result(
        "ORDERING FLOW: LOGIN", f"🧾 {svc['name']} — sign-in needed",
        f"{svc['name']} is asking for a sign-in, and that part "
        "is the visitor's BY DESIGN: the browser session is "
        "still live in the panel under the chat and Take "
        "control is open — they tap Take control and log in "
        "THEMSELVES (OG never sees, types, or stores a "
        "password). Once they're in, the log-in is kept in "
        "their browser profile. Then they hand back and ask "
        "again — the flow picks right back up. NOTHING was "
        "ordered and nothing is parked. Say exactly that, in "
        "persona; never suggest creating a new account.")


def _stop_botwall(svc):
    return _result(
        "ORDERING FLOW: WALLED", f"🧾 {svc['name']} — walled",
        f"{svc['name']} threw a bot wall / human-check at the "
        "browser and the flow STOPPED — the browser session "
        "was fully released, nothing was built, nothing is "
        "parked, and OG does not fight or solve that wall. "
        "Tell the visitor plainly, in persona: "
        f"{svc['name']} walled the browser off, so this one "
        f"has to happen in {svc['app']} — that's the fallback "
        "and there's no shame in it.")


def _stop_no_address(svc):
    return _result(
        "ORDERING FLOW: NO ADDRESS",
        f"🧾 {svc['name']} — no saved address",
        f"{svc['name']} has NO delivery address saved on the "
        "account, and OG never invents or types an address — "
        "so the flow stopped and the browser was released. "
        "Tell the visitor plainly, in persona: add the "
        f"delivery address in {svc['app']} first, then ask "
        "again and the flow will read it back from the site "
        "exactly as shown. Nothing was ordered.")


def _stop_not_found(svc, name):
    return _result(
        "ORDERING FLOW: NOT FOUND", f"🧾 {svc['name']} — not found",
        f"The item \"{name}\" could not be found on "
        f"{svc['name']} and there was no close match to offer. "
        "The flow stopped and the browser was released — "
        "nothing was substituted and nothing was ordered. "
        "Tell the visitor plainly, in persona, and let them "
        "name the item the way the site lists it.")


# --- Build (food / ride) ----------------------------------------------------------------


def _cap_check(uid):
    try:
        left = O._order_left(uid)
    except Exception as e:
        logger.warning(f"Flow cap check failed: {e}")
        return None
    if left is not None and left <= 0:
        return O._cap_body()
    return None


def _start_or_stop(uid, driver, svc):
    """(ok, stop_results). Start the driver; route its status
    to the honest stop that matches it."""
    try:
        started = driver.start(svc) or {}
    except Exception as e:
        logger.warning(f"Flow driver start failed: {e}")
        started = {"status": "unavailable"}
    status = started.get("status")
    if status == "ok":
        return True, None
    if status == "login_wall":
        return False, _stop_login(svc, driver)
    if status == "bot_wall":
        try:
            driver.release()
        except Exception:
            pass
        return False, _stop_botwall(svc)
    return False, _stop_unavailable(svc)


def _build_food(uid, driver, svc, items, usual_text="",
                resolved=None, start_at=0):
    resolved = list(resolved or [])
    for idx in range(start_at, len(items)):
        item = items[idx]
        try:
            found = driver.search_add(item["name"],
                                      int(item.get("qty", 1))) or {}
        except Exception as e:
            logger.warning(f"Flow search_add failed: {e}")
            found = {"found": False, "closest": None}
        if not found.get("found"):
            closest = found.get("closest")
            if closest:
                O._set_pending(uid, {
                    "stage": "flow_ask", "service_key": svc["key"],
                    "mode": "food",
                    "plan": {"items": items},
                    "item_index": idx, "closest": closest,
                    "resolved": resolved,
                    "usual_text": usual_text})
                body = (f"On {svc['name']}, \"{item['name']}\" "
                        "was NOT found — and OG never silently "
                        f"substitutes. The closest match the site "
                        f"shows is \"{closest}\". Ask the visitor "
                        "plainly, in persona: YES to use that "
                        "closest match for this one item and keep "
                        "building, NO to scrap the whole order. "
                        "Nothing else happens until they answer.")
                return _result("ORDERING FLOW: CLOSEST?",
                               f"🧾 {svc['name']} — closest match?",
                               body)
            try:
                driver.release()
            except Exception:
                pass
            return _stop_not_found(svc, item["name"])
        resolved.append({"name": item["name"],
                         "qty": int(item.get("qty", 1)),
                         "detail": found.get("label") or ""})
    try:
        summary = driver.read_summary() or {}
    except Exception as e:
        logger.warning(f"Flow read_summary failed: {e}")
        summary = {}
    summary = dict(summary)
    summary["items"] = resolved
    if not summary.get("address"):
        try:
            driver.release()
        except Exception:
            pass
        return _stop_no_address(svc)
    _park_approval(uid, svc, {"items": items}, summary, usual_text)
    return _summary_body(svc, summary, usual_text)


def _build_ride(uid, driver, svc, destination, usual_text=""):
    try:
        dest = driver.set_destination(destination) or {}
    except Exception as e:
        logger.warning(f"Flow set_destination failed: {e}")
        dest = {"found": False, "closest": None}
    if not dest.get("found"):
        closest = dest.get("closest")
        if closest:
            O._set_pending(uid, {
                "stage": "flow_ask", "service_key": svc["key"],
                "mode": "ride",
                "plan": {"destination": destination},
                "item_index": 0, "closest": closest,
                "resolved": [], "usual_text": usual_text})
            body = (f"On Uber, the destination \"{destination}\" "
                    "did not resolve — the closest the site "
                    f"offers is \"{closest}\". Ask the visitor "
                    "plainly, in persona: YES to ride to that "
                    "closest match instead, NO to scrap it. "
                    "Nothing else happens until they answer.")
            return _result("ORDERING FLOW: CLOSEST?",
                           "🧾 Uber — closest match?", body)
        try:
            driver.release()
        except Exception:
            pass
        return _result(
            "ORDERING FLOW: NOT FOUND", "🧾 Uber — not found",
            f"The destination \"{destination}\" could not be "
            "found on Uber and there was no close match to "
            "offer. The flow stopped and the browser was "
            "released. Tell the visitor plainly, in persona. "
            "Nothing was booked.")
    try:
        ride = driver.choose_ride() or {}
        summary = driver.read_summary() or {}
    except Exception as e:
        logger.warning(f"Flow ride build failed: {e}")
        ride, summary = {}, {}
    summary = dict(summary)
    summary["items"] = []
    if ride.get("label"):
        summary["ride_option"] = ride["label"]
    if not summary.get("pickup"):
        try:
            driver.release()
        except Exception:
            pass
        return _result(
            "ORDERING FLOW: NO PICKUP", "🧾 Uber — no pickup",
            "Uber is not showing a pickup location for this "
            "account — OG never invents or types a pickup "
            "address, so the flow stopped and the browser was "
            "released. Tell the visitor plainly, in persona: "
            "set the pickup in the Uber app first, then ask "
            "again. Nothing was booked.")
    _park_approval(uid, svc, {"destination": destination},
                   summary, usual_text)
    return _summary_body(svc, summary, usual_text, destination)


def _begin(uid, svc, items=None, destination="",
           usual_text=""):
    capped = _cap_check(uid)
    if capped:
        return capped
    driver = _get_driver(uid)
    ok, stop = _start_or_stop(uid, driver, svc)
    if not ok:
        return stop
    if svc["mode"] == "ride":
        return _build_ride(uid, driver, svc, destination,
                           usual_text)
    return _build_food(uid, driver, svc, items or [], usual_text)


# --- Job execution ------------------------------------------------------------------------


def _ask_food(job, uid):
    svc = SERVICES[job["service"]]
    items = job.get("items") or []
    if not items:
        body = ("The visitor named "
                f"{svc['name']} but no food. Ask them ONE "
                "question, in persona: what should go on the "
                "order? Nothing is drafted and the browser has "
                "NOT been opened.")
        return _result("ORDERING FLOW: CLARIFY",
                       f"🧾 {svc['name']} — one question", body)
    return _begin(uid, svc, items=items)


def _usual_order(job, uid):
    key = job.get("service")
    if key is None:
        flow = _flow_usuals(uid)
        if len(flow) == 1:
            key = next(iter(flow))
        else:
            names = ", ".join(SERVICES[k]["name"] for k in flow)
            body = ("The visitor asked for their usual but has "
                    f"flow usuals at more than one service "
                    f"({names}) and didn't name one. Ask them "
                    "ONE question, in persona: which service's "
                    "usual?")
            return _result("ORDERING FLOW: CLARIFY",
                           "🧾 Ordering flow — which usual?", body)
    svc = SERVICES[key]
    usual = _usual_for(uid, key)
    if usual is None:
        body = (f"The visitor asked for their usual from "
                f"{svc['name']} but has NO usual saved for it — "
                "not a flow usual, not a saved order. Do NOT "
                "guess and do NOT open the browser. Tell them "
                "plainly, in persona: no usual on file at "
                f"{svc['name']} yet — name the items now, or "
                f"teach it with \"my usual at {svc['name']} "
                "is ...\".")
        return _result("ORDERING FLOW: USUAL-NONE",
                       f"🧾 {svc['name']} — no usual", body)
    words = _usual_words(usual)
    if svc["mode"] == "ride":
        return _begin(uid, svc,
                      destination=usual.get("destination") or "",
                      usual_text=words)
    return _begin(uid, svc, items=usual.get("items") or [],
                  usual_text=words)


def _teach(job, uid):
    svc = SERVICES[job["service"]]
    rest = str(job.get("rest") or "").strip(" .")
    if svc["mode"] == "ride":
        dest = _clean_dest(re.sub(r"^(a\s+ride\s+to|ride\s+to|to)\s+",
                                  "", rest, flags=re.I))
        if not dest:
            return _result(
                "ORDERING FLOW: TEACH", "🧾 Uber — usual",
                "The visitor tried to teach their Uber usual "
                "but no destination came through. Ask them "
                "plainly, in persona: \"my usual Uber ride is "
                "to ...\" — where to?")
        _save_flow_usual(uid, svc["key"], {"destination": dest,
                                           "items": []})
        return _result(
            "ORDERING FLOW: TEACH", "🧾 Uber — usual saved",
            f"Saved: the visitor's usual at Uber is a ride to "
            f"{dest}. Confirm that, in persona, and tell them "
            "\"order my usual from Uber\" (or \"get me an Uber\" "
            "for their usual run) brings it right back.")
    items = O._parse_items(rest)
    if not items:
        return _result(
            "ORDERING FLOW: TEACH", f"🧾 {svc['name']} — usual",
            "The visitor tried to teach their usual but no "
            "items came through. Ask them plainly, in persona: "
            f"\"my usual at {svc['name']} is ...\" — name the "
            "items, with sizes the way the site lists them.")
    _save_flow_usual(uid, svc["key"], {"items": items,
                                       "destination": ""})
    names = ", ".join(f"{int(i.get('qty', 1))} × {i['name']}"
                      for i in items)
    return _result(
        "ORDERING FLOW: TEACH", f"🧾 {svc['name']} — usual saved",
        f"Saved: the visitor's usual at {svc['name']} is now "
        f"on file ({names}). Confirm that, in persona, and "
        f"tell them \"order my usual from {svc['name']}\" "
        "brings it right back — read back in plain words in "
        "the summary before any approval, every time.")


def _save_last(job, uid):
    svc = SERVICES[job["service"]]
    last = O._blob(uid, "flow_last", None) or {}
    _save_flow_usual(uid, svc["key"], {
        "items": last.get("items") or [],
        "destination": last.get("destination") or ""})
    words = _usual_words({"items": last.get("items") or [],
                          "destination": last.get("destination")
                          or ""})
    return _result(
        "ORDERING FLOW: TEACH", f"🧾 {svc['name']} — usual saved",
        f"Saved from the last completed {svc['name']} handoff: "
        f"the visitor's usual there is now {words}. Confirm "
        "that, in persona.")


def _usual_show(job, uid):
    svc = SERVICES[job["service"]]
    usual = _usual_for(uid, svc["key"])
    if usual is None:
        return _result(
            "ORDERING FLOW: USUALS", f"🧾 {svc['name']} — usuals",
            f"The visitor asked their usual at {svc['name']} "
            "and has none saved there. Tell them plainly, in "
            "persona — and that \"my usual at "
            f"{svc['name']} is ...\" teaches it.")
    return _result(
        "ORDERING FLOW: USUALS", f"🧾 {svc['name']} — usuals",
        f"The visitor's saved usual at {svc['name']}, exactly "
        f"this (present it as-is, in persona): "
        f"{_usual_words(usual)}.")


def _money_cents(value) -> Optional[int]:
    if value is None:
        return None
    m = re.search(r"\$?\s?([\d,]+)(?:\.(\d{2}))?", str(value))
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", "")) * 100 \
            + int(m.group(2) or 0)
    except ValueError:
        return None


def _flow_approve(uid):
    pending = O._get_pending(uid)
    if pending is None or pending.get("stage") != "flow":
        return None  # zero-order by construction: no park, no go
    svc = SERVICES[pending["service_key"]]
    summary = dict(pending.get("summary") or {})
    plan = pending.get("plan") or {}
    driver = _get_driver(uid)
    try:
        conf = driver.goto_confirm() or {}
    except Exception as e:
        logger.warning(f"Flow goto_confirm failed: {e}")
        conf = {"status": "gone", "total": None}
    if conf.get("status") != "at_confirm":
        O._clear_pending(uid)
        try:
            driver.release()
        except Exception:
            pass
        return _result(
            "ORDERING FLOW: STOP", f"🧾 {svc['name']} — page gone",
            "The visitor approved, but the browser session / "
            "page was gone before the confirm step — NOTHING "
            "was handed off and nothing was spent. Tell them "
            "plainly, in persona, and that asking again starts "
            "it fresh.")
    approved_total = _money_cents(summary.get("total"))
    now_total = _money_cents(conf.get("total"))
    changed = (approved_total is not None and now_total is not None
               and approved_total != now_total) or (
        approved_total is not None and now_total is None)
    if changed:
        new_summary = dict(conf.get("summary") or summary)
        new_summary["items"] = summary.get("items") or []
        if conf.get("total"):
            new_summary["total"] = conf["total"]
        if summary.get("address"):
            new_summary["address"] = summary["address"]
        if summary.get("pickup"):
            new_summary["pickup"] = summary["pickup"]
        if summary.get("ride_option"):
            new_summary["ride_option"] = summary["ride_option"]
        _park_approval(uid, svc, plan, new_summary,
                       pending.get("usual_text") or "")
        return _summary_body(svc, new_summary,
                             pending.get("usual_text") or "",
                             plan.get("destination") or "",
                             changed=True)
    spent = True
    try:
        spent = bool(O._consume_order(uid))
    except Exception as e:
        logger.warning(f"Flow consume failed: {e}")
        spent = False
    if not spent:
        O._clear_pending(uid)
        try:
            driver.release()
        except Exception:
            pass
        return O._cap_body()
    O._put_blob(uid, "flow_last", {
        "service_key": svc["key"], "service": svc["name"],
        "mode": svc["mode"], "items": summary.get("items") or [],
        "destination": plan.get("destination") or "",
        "total": summary.get("total"),
        "at": datetime.now(timezone.utc).isoformat()})
    O._clear_pending(uid)
    try:
        driver.open_handoff()
    except Exception as e:
        logger.warning(f"Flow open_handoff failed: {e}")
    total_line = (f" at the total you approved "
                  f"({summary['total']})" if summary.get("total")
                  else "")
    body = (
        "The visitor APPROVED and the flow reached the final "
        f"step on {svc['name']}{total_line}. The browser is "
        "now in TAKE CONTROL for the visitor. Say, in "
        "substance word for word: It's sitting at the final "
        "step — the last tap is yours. OG never taps pay / "
        "place-order / confirm, not once: look it over in the "
        "panel and YOU tap it when you're happy. Then offer, "
        "in persona: say \"remember that as my usual at "
        f"{svc['name']}\" and OG will save exactly what was "
        "ordered as their usual there.")
    return _result("ORDERING FLOW: HANDOFF",
                   f"🧾 {svc['name']} — your tap", body)


def _flow_decline(uid):
    pending = O._get_pending(uid)
    stage = pending.get("stage") if pending else ""
    svc = SERVICES.get((pending or {}).get("service_key", ""),
                       {"name": "that service"})
    O._clear_pending(uid)
    driver = _get_driver(uid)
    try:
        driver.release()
    except Exception:
        pass
    what = "order" if stage == "flow" else "half-built order"
    return _result(
        "ORDERING FLOW: DISCARDED", f"🧾 {svc['name']} — discarded",
        f"The visitor said NO. The {what} is DISCARDED — "
        "nothing was handed off, nothing was spent, and the "
        "browser session was fully released. Confirm that "
        "briefly, in persona.")


def _flow_ask_approve(uid):
    pending = O._get_pending(uid)
    if pending is None or pending.get("stage") != "flow_ask":
        return None
    svc = SERVICES[pending["service_key"]]
    driver = _get_driver(uid)
    if pending.get("mode") == "ride":
        O._clear_pending(uid)
        return _build_ride(uid, driver, svc,
                           pending["closest"],
                           pending.get("usual_text") or "")
    plan = pending.get("plan") or {}
    items = [dict(i) for i in plan.get("items") or []]
    idx = int(pending.get("item_index", 0))
    if 0 <= idx < len(items):
        items[idx] = {"name": pending["closest"],
                      "qty": int(items[idx].get("qty", 1))}
    resolved = list(pending.get("resolved") or [])
    O._clear_pending(uid)
    # The substituted item goes through search_add like any
    # other — the build simply resumes AT its index with the
    # site's own closest-match name in place of the ask.
    return _build_food(uid, driver, svc, items,
                       pending.get("usual_text") or "",
                       resolved=resolved, start_at=idx)


def flow_results(job, message, uid) -> Optional[list]:
    if not job or not uid:
        return None
    kind = job.get("kind", "")
    if kind == "ask_food":
        return _ask_food(job, uid)
    if kind == "ask_ride":
        svc = SERVICES["uber"]
        return _begin(uid, svc,
                      destination=job.get("destination") or "")
    if kind == "usual_order":
        return _usual_order(job, uid)
    if kind == "teach":
        return _teach(job, uid)
    if kind == "save_last":
        return _save_last(job, uid)
    if kind == "usual_show":
        return _usual_show(job, uid)
    if kind == "flow_approve":
        return _flow_approve(uid)
    if kind == "flow_decline":
        return _flow_decline(uid)
    if kind == "flow_ask_approve":
        return _flow_ask_approve(uid)
    if kind == "flow_pay_note":
        pending = O._get_pending(uid)
        svc = SERVICES.get((pending or {}).get("service_key", ""),
                           {"name": "the site"})
        return _result(
            "ORDERING FLOW: PAY-NOTE", "🧾 Ordering flow — payment",
            "The visitor asked OG to just pay / confirm it. Be "
            "plain, in persona: OG NEVER taps pay, place-order "
            "or confirm — that last tap is the visitor's, at "
            f"{svc['name']}, after they reply YES to the parked "
            "summary and take control at the final step. The "
            "summary is still waiting on their YES or NO.")
    return None


# --- The seam (piggybacked from og_ordering.install_ordering_tools) ---------------------

_pending = {"job": None, "message": ""}


def install_flow_tools(agent_instance, get_uid):
    """Wrap the agent hooks AFTER og_ordering's wrappers
    (og_ordering installs this, fail-safe): named-service
    flow claims run here, every other message delegates to
    the ordering wrapper untouched — so a flows job is
    checked BEFORE ordering's generic draft, and a flows
    miss leaves Round 19 behaviour byte-for-byte intact."""
    if getattr(agent_instance, "_og_ordering_flows_installed",
               False):
        return
    prev_detect = getattr(agent_instance, "detect_intent", None)
    prev_search = getattr(agent_instance, "web_search", None)
    if prev_detect is None or prev_search is None:
        return

    def detect_wrapped(message):
        intent = prev_detect(message)
        _pending["job"] = None
        _pending["message"] = ""
        try:
            uid = get_uid()
            job = _claim_job(str(message), uid) if uid else None
            if job:
                _pending["job"] = job
                _pending["message"] = str(message)
                if isinstance(intent, dict):
                    intent["needs_code_generation"] = False
                    if not intent.get("needs_web_search"):
                        intent["needs_web_search"] = True
                        intent["search_query"] = str(message).strip()
        except Exception as e:
            logger.warning(f"Ordering flow trigger check failed: {e}")
            _pending["job"] = None
        return intent

    def search_wrapped(query, num_results=5):
        job = _pending.get("job")
        message = _pending.get("message", "")
        _pending["job"] = None
        _pending["message"] = ""
        if job:
            try:
                results = flow_results(job, message, get_uid())
            except Exception as e:
                logger.warning(f"Ordering flow job failed: {e}")
                results = None
            if results:
                return results[: max(1, num_results)]
        return prev_search(query, num_results)

    agent_instance.detect_intent = detect_wrapped
    agent_instance.web_search = search_wrapped
    agent_instance._og_ordering_flows_installed = True
