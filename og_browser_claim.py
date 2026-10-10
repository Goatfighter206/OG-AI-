"""OG browser — chat-claim parsing (Round 46 code motion).

Byte-identical move out of og_browser.py: that file's push
payload hit the wrapper's argv ceiling in Round 45 (130,452 of
~131,072 bytes), so the pure claim-parsing block — the nav/site
regexes and tables, _parse_post, _extract_url, _is_start_request
— lives here now, and og_browser imports every name back, so
callers and suites see the same attributes on og_browser.

Everything here is PURE LOGIC: the claim parsing over the
visitor's own words, plus (Round 49) the approval-gate and
element-matching cluster over page-snapshot dicts. No session
state, no network, no Steel anywhere in this module.
_claim_job — which reads live session state — stayed in
og_browser.py.
"""

import re
import urllib.parse
from typing import Dict, List, Optional

# --- Chat parsing ---------------------------------------------------------------

_APPROVE_RE = re.compile(
    r"^\W*(yes|yeah|yep|yup|approve|approved|go ahead|do it"
    r"|confirm|ok|okay|sounds good|let'?s go)\b", re.I)
_DECLINE_RE = re.compile(
    r"^\W*(no|nope|nah|cancel|scrap|discard|never ?mind|stop"
    r"|don'?t|do not)\b", re.I)
_URL_TOKEN_RE = re.compile(
    # Round 26 fix: the host part must allow MULTIPLE labels — the
    # old single-dot pattern truncated "en.wikipedia.org/wiki/..."
    # to "en.wikipedia", a bogus host the gate then refused, so
    # full-URL asks with subdomains never reached the browser.
    r"(?:https?://)?(?:[a-z0-9][a-z0-9-]*\.)+[a-z]{2,}"
    r"(?:/[^\s<>\"']*)?", re.I)
# Round 26: the natural start-verb set (see _is_start_request).
# Round 45 added the "take me to" family (r45 NOTES).
_NAV_START_RE = re.compile(
    r"\b(open|visit|browse|navigate|load|pull up|bring up|pop up|"
    r"show( me)?|display|go to|start|launch|fire up|use|get on|"
    r"hop on|take me( over)? to|head( over)? to|go over to|"
    r"jump over to|swing by)\b|\bput\b(?:\s+\w+){0,3}?\s+up\b", re.I)
# Content words turn a site mention into a LOOKUP ask, not a
# browsing ask ("show me the headlines on bbc.com" = fetch the
# headlines as text) — unless the visitor said "browser" out loud,
# which always means the browser.
_CONTENT_MARKER_RE = re.compile(
    r"\b(headlines?|news|articles?|weather|forecast|scores?|prices?|"
    r"story|stories|lyrics|song|recipes?|directions|traffic)\b", re.I)

# Well-known site names a visitor names instead of a domain
# ("pull up Facebook", "show me ESPN"). Every target is on the
# allowlist; the hard blocklist has no names here by construction.
_SITE_NAMES = {
    "facebook": "facebook.com", "instagram": "instagram.com",
    "twitter": "twitter.com", "youtube": "youtube.com",
    "espn": "espn.com", "reddit": "reddit.com",
    "amazon": "amazon.com", "ebay": "ebay.com",
    "walmart": "walmart.com", "target": "target.com",
    "best buy": "bestbuy.com", "netflix": "netflix.com",
    "twitch": "twitch.tv", "github": "github.com",
    "wikipedia": "wikipedia.org", "google": "google.com",
    "bbc": "bbc.com", "cnn": "cnn.com", "zillow": "zillow.com",
    "craigslist": "craigslist.org", "yelp": "yelp.com",
    "spotify": "spotify.com", "tiktok": "tiktok.com",
    "pinterest": "pinterest.com", "linkedin": "linkedin.com",
    "weather channel": "weather.com", "home depot": "homedepot.com",
    "lowes": "lowes.com", "lowe's": "lowes.com",
    "duckduckgo": "duckduckgo.com",
    # Round 45 (r45 NOTES): audit gaps + "coinbase". All targets
    # allowlisted EXCEPT coinbase.com (claimed; still gate-refused).
    "soundcloud": "soundcloud.com", "vimeo": "vimeo.com",
    "hulu": "hulu.com", "nytimes": "nytimes.com",
    "new york times": "nytimes.com",
    "weather site": "weather.com", "weather website": "weather.com",
    "coinbase": "coinbase.com",
}
_SITE_NAME_RE = re.compile(
    r"\b(" + "|".join(
        re.escape(k) for k in sorted(_SITE_NAMES, key=len,
                                     reverse=True)) + r")\b", re.I)

# A start ask that NAMES a hard-blocked target gets the honest
# refusal at proposal time (no session, no minutes): banking,
# money and password targets are never opened, by name or by URL.
_BLOCKED_TARGET_RE = re.compile(
    r"\b(bank|banking|chase|wells fargo|bank of america|"
    r"capital one|us bank|truist|citi ?bank|credit union|paypal|"
    r"venmo|cash ?app|fidelity|vanguard|schwab|brokerage|"
    r"1 ?password|lastpass|bitwarden|dashlane|password manager|"
    r"password vault)\b", re.I)


def _resolve_site_url(low: str) -> str:
    """'pull up facebook' -> 'https://facebook.com'; '' if the
    message names no well-known site."""
    m = _SITE_NAME_RE.search(low)
    if m:
        return "https://" + _SITE_NAMES[m.group(1).lower()]
    # "on X" / "to X": the single-letter name only counts glued to
    # a preposition — a bare "x" anywhere else is not a site ask.
    if re.search(r"\b(?:on|onto|to|via|open|check)\s+x\b", low) \
            or "x.com" in low:
        return "https://twitter.com"
    return ""


# Round 27 (auto-open): account-ish phrasing aimed at a named site
# is a browsing ask by itself — the task inherently needs the live
# site ("check my Facebook", "look at my messages on X", "go on
# Facebook"). Requires a resolved site; never claims on its own.
_ACCOUNT_RE = re.compile(
    r"\b(check|look at|open|view|see|read)\s+my\b|\bmy\s+(feed|"
    r"profile|account|page|timeline|messages|notifications|posts)\b"
    r"|\b(go|get|hop|jump|log|sign)\s+(on|onto|in|into)\b"
    r"|\bmessages on\b|\bnotifications on\b", re.I)

# Filler a visitor wraps around a bare site name ("og facebook",
# "facebook please") — stripped before the bare-mention test.
_BARE_FILLER = {"og", "yo", "hey", "hi", "please", "pls", "the",
                "a", "an", "um", "uh", "ok", "okay"}


def _is_bare_mention(raw: str, low: str) -> bool:
    """The message IS a site mention and nothing else: 'Facebook',
    'facebook.com', 'og — Facebook!' -> True. Any extra content
    word ('facebook news', 'facebook stock') defeats it."""
    words = [w.strip(".,!?\"'—-") for w in low.split()]
    words = [w for w in words if w and w not in _BARE_FILLER]
    if not words:
        return False
    joined = " ".join(words)
    if joined in _SITE_NAMES:
        return True
    token = _extract_url(raw)
    if token and joined == token.lower().replace(
            "https://", "").replace("http://", "").rstrip("/"):
        return True
    return _resolve_site_url(low) != "" and len(words) == 1


# Round 27: posting asks. A post/publish verb plus a site (or a
# live session to post into) claims the browser: the task needs
# the live site by construction. "share" counts only with quoted
# text or an explicit on/to target — plain "share" is too loose.
_POST_VERB_RE = re.compile(r"\b(post|publish|share)\b", re.I)
_POST_QUOTED_RE = re.compile(
    r"(?:post|publish|share)\s+['\"“](.+?)['\"”]", re.I | re.S)
_POST_ON_RE = re.compile(
    r"\b(?:post|publish|share)\s+(.+?)\s+(?:on|onto|to)\s+"
    r"(?:my\s+)?(?:the\s+)?\S+\s*$", re.I | re.S)


def _parse_post(raw: str, low: str) -> Optional[Dict]:
    """{'url', 'text'} for a posting ask, else None. Text is the
    EXACT words to post: a quoted span, or the words between the
    verb and 'on/to <site>'. 'post this on my facebook' yields
    text '' — a placeholder the visitor must fill in word-for-word
    before anything is drafted."""
    m = _POST_VERB_RE.search(low)
    if not m:
        return None
    verb = m.group(1).lower()
    url = _extract_url(raw) or _resolve_site_url(low)
    if verb == "share" and not url and not _POST_QUOTED_RE.search(raw):
        return None
    if not url and not _BLOCKED_TARGET_RE.search(low):
        return None
    text = ""
    q = _POST_QUOTED_RE.search(raw)
    if q:
        text = q.group(1).strip()
    else:
        o = _POST_ON_RE.search(raw)
        if o:
            cand = o.group(1).strip().strip("'\"")
            if cand.lower() not in ("this", "that", "it",
                                    "something", "a post", "a status"):
                text = cand
    return {"url": url, "text": text}
_BROWSER_WORD_RE = re.compile(r"\bbrowser\b", re.I)
_END_RE = re.compile(
    r"^(stop|end|close|kill|shut down)\b.*\b(browser|session|browsing)\b"
    r"|^(end|close|stop) (the )?(browser )?session\b", re.I)
_TAKEOVER_RE = re.compile(
    r"\b(take control|take over|let me drive|my turn|i'?ll drive|"
    r"i want to drive)\b", re.I)
_HANDBACK_RE = re.compile(
    r"\b(hand (it )?back|give (it )?back|you drive|your turn|"
    r"take it back|you take over)\b", re.I)
_CLICK_RE = re.compile(
    r"\b(?:click|tap|press)(?: on)?\s+(?:the\s+)?[\"']?(.+?)[\"']?\s*$",
    re.I)
_TYPE_QUOTED_RE = re.compile(
    r"\b(?:type|write|enter|put)\s+[\"'](.+?)[\"']\s+"
    r"(?:in|into)\s+(?:the\s+)?(.+?)\s*$", re.I)
_TYPE_RE = re.compile(
    r"\b(?:type|write|put)\s+(.+?)\s+(?:in|into)\s+(?:the\s+)?(.+?)\s*$",
    re.I)
_SEARCH_RE = re.compile(r"\bsearch(?: the page)? for\s+(.+?)\s*$", re.I)
_ENTER_RE = re.compile(r"\b(press|hit|push) enter\b|^enter$", re.I)
_SCROLL_RE = re.compile(r"\bscroll\s+(down|up)\b|\bscroll\b", re.I)
_BACK_RE = re.compile(r"^(go back|back|previous page)\b", re.I)
_READ_RE = re.compile(
    r"\b(what do you see|read (the |this )?page|what'?s on (the |this )"
    r"page|describe the page|look at the page|what is on the page)\b",
    re.I)
# Round 45: _NAV_START_RE's additions mirrored for in-session nav.
_NAV_VERB_RE = re.compile(
    r"\b(go to|open|visit|navigate|load|pull up|"
    r"take me( over)? to|head( over)? to|go over to|"
    r"jump over to|swing by)\b", re.I)

# Round 49: always-allow removal asks ("stop always allowing
# facebook.com", "remove facebook from always allow", "take X
# off my always-allow list"). Pure text like everything here;
# _claim_job checks this BEFORE its YES/NO block — the ask
# starts with "stop"/"don't", which _DECLINE_RE would otherwise
# eat as a NO on a parked approval. The capture is the site
# WORDS; og_browser resolves them to a host.
_DISALLOW_RE = re.compile(
    r"\b(?:stop|don'?t|do not) always[- ]allow(?:ing)?\s+"
    r"(.+?)\s*[.!?]*$"
    r"|\bremove\s+(.+?)\s+from\s+(?:my\s+)?always[- ]allow"
    r"(?:\s+list)?\b"
    r"|\btake\s+(.+?)\s+off\s+(?:my\s+)?always[- ]allow"
    r"(?:\s+list)?\b", re.I)


def _parse_disallow(raw: str) -> Optional[str]:
    """The site words of an always-allow removal ask, else None."""
    m = _DISALLOW_RE.search(str(raw or ""))
    if not m:
        return None
    words = next((g for g in m.groups() if g), "")
    return words.strip() or None


def _extract_url(message: str) -> str:
    m = _URL_TOKEN_RE.search(str(message))
    if not m:
        return ""
    token = m.group(0).rstrip(".,!?)\"'")
    if not token.lower().startswith(("http://", "https://")):
        token = "https://" + token.lstrip("/")
    return token


def _is_start_request(message: str, low: str) -> bool:
    """THE RULE (Round 27, supersedes Round 26's gate).

    A start is claimed when the visitor's ask inherently needs
    the LIVE site — Brent's auto-open rule: no magic phrasing,
    no proposal step. Claims:
    - "browser" said out loud + any start verb;
    - a navigation verb (open / pull up / put ... up / bring up /
      show / visit / go to / load / browse / start / use ...) aimed
      at a URL or a well-known site NAME;
    - a bare site mention that IS the whole message ("Facebook",
      "facebook.com", "og, facebook please");
    - an account-ish ask aimed at a named site (check/look at/open
      MY ..., go/get/log on..., my feed/profile/messages/...):
      checking your own account cannot be done from a text lookup.

    A start is NOT claimed when the visitor asks for CONTENT from
    a site: lookup verbs (check / read / watch / search / what's)
    with no account marker never claim, and neither does a
    navigation verb aimed at content words (headlines, news,
    weather, scores, prices ... — _CONTENT_MARKER_RE) unless
    "browser" was said out loud. Those asks belong to the text
    lookup and must answer in text with no session.

    History: Round 25 narrowed the gate after a lookup-verb gate
    hijacked text lookups into live sessions; Round 26 restored
    natural go-somewhere asks but still made the visitor say YES
    to a proposal before starting, and Brent's bare/account asks
    ("Facebook", "check my Facebook") claimed nothing. Round 27
    keeps the anti-hijack half (content asks stay text) and makes
    every go-somewhere/account ask START in the same turn.

    Round 45: "take me to" verbs; blocked-name asks claim
    url-less so the refusal fires; "site/website" exempts the veto."""
    has_browser = bool(_BROWSER_WORD_RE.search(low))
    if has_browser and _NAV_START_RE.search(low):
        return True
    site = _extract_url(message) or _resolve_site_url(low)
    if not site:
        # Round 45: a nav/account ask NAMING a hard-blocked target
        # claims a url-less start so _do_start's refusal fires
        # (Rounds 27-44 fell silently through to plain chat).
        if _BLOCKED_TARGET_RE.search(low) and (
                _NAV_START_RE.search(low)
                or _ACCOUNT_RE.search(low)):
            return True
        return False
    token = _extract_url(message)
    if token and str(message).strip().rstrip(".,!?)\"'") == token:
        return True  # a bare URL pasted alone means "open this"
    if _ACCOUNT_RE.search(low):
        return True
    if _is_bare_mention(message, low):
        return True
    if not _NAV_START_RE.search(low):
        return False
    # Round 45: an explicit site/website word exempts the veto —
    # naming the SITE itself is a visit ask, not a content ask.
    if _CONTENT_MARKER_RE.search(low) and not has_browser \
            and not re.search(r"\b(?:site|website)\b", low):
        return False
    return True




# --- The approval gate + element matching (Round 49 move) ----------------
# Byte-identical move out of og_browser.py, same reason as the
# Round 46 motion above: that file's push payload hit the
# wrapper's argv ceiling again in Round 49 (135,330 of
# ~131,072 bytes). These are PURE functions over page-snapshot
# dicts — no session state, no network, no Steel — and
# og_browser imports every name back, so callers and suites
# see the same attributes on og_browser.

# --- The approval gate --------------------------------------------------------

# A click on anything whose label reads like a consequential verb is
# never taken silently — it is parked for a YES (plan 6.2.2).
_GATE_RE = re.compile(
    r"\b(submit|post|publish|send|buy|checkout|check\s?out|pay|payment|"
    r"purchase|order\s+now|place\s+(the\s+)?order|delete|remove|comment|"
    r"reply|follow|subscribe|sign\s?up|register|book\s+now|reserve|"
    r"confirm|donate|apply\s+now|join\s+now)\b", re.I)


def _gate_reason(el: Dict, action: Dict) -> str:
    """'' when the action is free to run, else WHY it needs a YES.
    Typing is a draft (visitor can still hand it back); Enter inside
    a form submits the form; submit/consequential clicks commit."""
    do = action.get("do")
    if do == "enter":
        if el.get("in_form"):
            return "pressing Enter in that form field submits the form"
        return ""
    if do == "click":
        if el.get("type") == "submit":
            return "that button submits a form"
        if el.get("tag") == "button" and el.get("in_form") \
                and el.get("type") in ("", "submit"):
            return "that button submits a form"
        label = str(el.get("label") or "")
        if _GATE_RE.search(label):
            return f"'{label}' is a commit-style action, not a read"
    return ""


def _describe_action(action: Dict, el: Optional[Dict], snap: Dict) -> str:
    """The exact action, in plain words, for the approval prompt."""
    host = ""
    try:
        host = urllib.parse.urlparse(snap.get("url", "")).hostname or ""
    except Exception:
        pass
    do = action.get("do")
    label = (el or {}).get("label") or ""
    if do == "click":
        return f"click '{label}' on {host}"
    if do == "enter":
        return (f"press Enter in the '{label}' field on {host} "
                "(that submits the form)")
    return f"{do} on {host}"


def _element_signature(el: Dict) -> Dict:
    return {"label": el.get("label", ""), "href": el.get("href", ""),
            "tag": el.get("tag", ""), "url": ""}


def _find_element(elements: List[Dict], words: str) -> Dict:
    """Resolve a visitor's click target words against the snapshot.
    Returns {"el"} on a unique match, {"ambiguous": [...]}, or {}."""
    want = " ".join(str(words).lower().split())
    if not want:
        return {}
    scored = []
    for el in elements:
        label = " ".join(str(el.get("label") or "").lower().split())
        if not label:
            continue
        if label == want:
            scored.append((3, el))
        elif want in label:
            scored.append((2, el))
        elif label in want:
            scored.append((1, el))
        else:
            overlap = len(set(want.split()) & set(label.split()))
            if overlap >= 2:
                scored.append((0, el))
    if not scored:
        return {}
    top = max(s for s, _ in scored)
    best = [el for s, el in scored if s == top]
    if len(best) > 1:
        return {"ambiguous": best[:4]}
    return {"el": best[0]}


def _find_field(elements: List[Dict], words: str) -> Dict:
    """Resolve a typing target: an input/textarea/select (or a
    role=textbox composer) by words, or the page's obvious
    search/main field when words are empty."""
    fields = [el for el in elements
              if el.get("tag") in ("input", "textarea", "select")
              or el.get("role") == "textbox"]
    if not fields:
        return {}
    if words:
        found = _find_element(fields, words)
        if found:
            return found
    for el in fields:
        hay = (str(el.get("label") or "") + " "
               + str(el.get("type") or "")).lower()
        if any(k in hay for k in ("search", "query", "q", "find")):
            return {"el": el}
    return {"el": fields[0]} if len(fields) == 1 else {}


_COMPOSER_LABEL_RE = re.compile(
    r"what'?s on your mind|write something|write a post|"
    r"create (a )?post|say something|share something|"
    r"composer|status|post", re.I)


def _find_composer(elements: List[Dict]) -> Optional[Dict]:
    """The page's post composer, if one is visible: a field
    (input/textarea/select/role=textbox) whose label reads like a
    post box, else the page's lone role=textbox, else a lone
    textarea. Generic by label — no site-specific selectors."""
    fields = [el for el in elements
              if el.get("tag") in ("input", "textarea", "select")
              or el.get("role") == "textbox"]
    if not fields:
        return None
    for el in fields:
        if _COMPOSER_LABEL_RE.search(str(el.get("label") or "")):
            return el
    boxes = [el for el in fields if el.get("role") == "textbox"]
    if len(boxes) == 1:
        return boxes[0]
    areas = [el for el in fields if el.get("tag") == "textarea"]
    if len(areas) == 1:
        return areas[0]
    return None


_POST_BUTTON_RE = re.compile(
    r"^(post|publish|share|tweet|send)$", re.I)
_POST_BUTTON_LOOSE_RE = re.compile(r"\b(post|publish)\b", re.I)


def _find_post_button(elements: List[Dict]) -> Optional[Dict]:
    """The composer's commit button: an exact 'Post' / 'Publish' /
    'Share' label first, then any button-ish element whose label
    carries post/publish. Clicking it ALWAYS goes through the
    approval gate (its label matches the gate verbs by design)."""
    clickables = [el for el in elements
                  if el.get("tag") in ("button", "a", "input")
                  or el.get("role") in ("button", "")]
    exact = [el for el in clickables
             if _POST_BUTTON_RE.match(
                 str(el.get("label") or "").strip())]
    if len(exact) == 1:
        return exact[0]
    if exact:
        return exact[0]
    loose = [el for el in clickables
             if el.get("tag") == "button"
             and _POST_BUTTON_LOOSE_RE.search(
                 str(el.get("label") or ""))]
    return loose[0] if len(loose) == 1 else None

