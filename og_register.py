"""Round 26: OG register enforcement — the output-stage guarantee.

Brent's standard (2026-10-08, after testing Round 25 live): "every
sentence should have a cuss word." Two rounds of prompt instructions
(Round 24's strengthening, Round 25's identity restructure) still let
short/factual replies land clean and let profanity decay inside long
conversations, so the guarantee moved HERE: a post-generation pass
over OG's FINAL chat reply text, wired into both /chat paths in
app.py (classic JSON + the SSE streaming twin).

WHAT IT DOES. The reply is split into prose sentences (code fences,
inline code, URLs, markdown links and "double-quoted" spans are
protected spans and pass through BYTE-IDENTICAL). A sentence passes
if it carries a profanity token from the lexicon below. Each failing
sentence is rewritten by ONE small model call (the deployment's
existing OpenAI key; model from OG_REGISTER_MODEL, else OPENAI_MODEL)
with a single instruction: put natural profanity into THIS sentence
in OG's register and change nothing else — same facts, numbers,
names, meaning, roughly the same length. The rewrite is accepted
only if it (a) actually contains profanity afterwards, (b) preserves
every number of the original (digit strings compared as multisets)
and every non-initial capitalized name, and (c) stays within
0.4x–2.5x of the original length. A rejected/failed rewrite falls
back to a deterministic insertion (a persona-natural opener —
"Fuck," / "Shit," / "Damn," / "Hell," — with the next word
lowercased when it is a common sentence starter), so enforcement
never depends on the model cooperating. At most OG_REGISTER_MAX_
REWRITES (default 8) model rewrites happen per reply; further
failing sentences take the deterministic path immediately.

NEVER TOUCHED. Fenced code blocks, inline code, URLs, markdown
links, quoted spans, and structured tool documents: a reply that
contains an ORDER SHEET — / TRADE SHEET — header (og_ordering /
og_trading copy-ready documents) is returned completely unchanged.

METERING DECISION. Enforcement rewrite tokens are NOT billed to the
visitor: this pass is OG's own voice production, not the visitor's
request. app.py counts the model exchange exactly as before (the
estimate/usage figures never include enforcement calls); the tokens
this module spends are only counted in its own stats dict for the
owner's cost visibility.

SWITCH. OG_REGISTER_ENABLED=0 turns the pass into a no-op (app.py
then serves the model's raw text, Round 25 behavior).
"""

import hashlib
import json
import os
import re
import time
import urllib.request

# --- The lexicon (stems cover the common inflections) -------------------------

_PROF_RE = re.compile(
    r"\b(?:fuck\w*|shit\w*|bullshit\w*|damn\w*|dammit|goddamn\w*"
    r"|hell|bitch\w*|ass|asses|asshole\w*|motherfuck\w*|bastard\w*"
    r"|crap\w*|piss\w*|dick(?!ens\b)\w*|whore\w*|slut\w*)",
    re.I)

_SHEET_RE = re.compile(r"\b(?:ORDER|TRADE) SHEET\s+—")

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_NAME_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")

_OPENERS = ("Fuck,", "Shit,", "Damn,", "Hell,")
_STARTERS = frozenset(
    "the a an it that this there here you we they he she i so but and "
    "or well yeah yes no ok okay sure first next then now just if when "
    "for in on with at by from as is was are were be been do does did "
    "not no cap real talk yo aight bet listen look".split())

_ABBREV = frozenset(
    ("e.g", "i.e", "mr", "mrs", "ms", "dr", "st", "vs", "jr", "sr",
     "u.s", "u.k", "a.m", "p.m"))

_PROTECTED_RE = re.compile(
    r"```[\s\S]*?(?:```|$)"              # fenced code (to EOS if open)
    r"|`[^`\n]+`"                        # inline code
    r"|\[[^\]\n]*\]\([^)\s]+\)"          # markdown link (whole thing)
    r"|https?://[^\s<>\"']+"             # bare URL
    r"|www\.[^\s<>\"']+"                 # bare www URL
    r"|\"[^\"\n]+\"")                    # "double-quoted" span

_SENT_END = ".!?"


def _enabled() -> bool:
    return os.environ.get(
        "OG_REGISTER_ENABLED", "1").strip().lower() not in (
            "0", "false", "no", "off")


def _max_rewrites() -> int:
    try:
        return max(0, int(os.environ.get("OG_REGISTER_MAX_REWRITES", "8")))
    except ValueError:
        return 8


def has_profanity(sentence: str) -> bool:
    return bool(_PROF_RE.search(sentence))


# --- Segmentation: protected spans + sentence spans ----------------------------


def _segments(text: str):
    """Split into (is_protected, chunk) pairs, order preserved."""
    out = []
    pos = 0
    for m in _PROTECTED_RE.finditer(text):
        if m.start() > pos:
            out.append((False, text[pos:m.start()]))
        out.append((True, m.group(0)))
        pos = m.end()
    if pos < len(text):
        out.append((False, text[pos:]))
    return out


def _sentence_spans(chunk: str):
    """(start, end) spans of sentences inside one prose chunk, line by
    line. Splits at [.!?] followed by whitespace/EOL; a '.' between
    two digits (3.5) or after a known abbreviation (e.g.) is not a
    boundary. Returned spans exclude the trailing separator."""
    spans = []
    base = 0
    for line in chunk.split("\n"):
        n = len(line)
        start = 0
        i = 0
        while i < n:
            ch = line[i]
            if ch in _SENT_END:
                prev = line[i - 1] if i > 0 else ""
                nxt = line[i + 1] if i + 1 < n else ""
                if ch == "." and prev.isdigit() and nxt.isdigit():
                    i += 1
                    continue
                if ch == ".":
                    word = re.search(r"([A-Za-z][A-Za-z.]*)$",
                                     line[max(0, i - 6):i])
                    if word and word.group(1).lower().rstrip(".") \
                            in _ABBREV:
                        i += 1
                        continue
                # absorb closing quotes/brackets after the terminator
                j = i + 1
                while j < n and line[j] in "\"'”)]}":
                    j += 1
                if j >= n or line[j] in " \t":
                    spans.append((base + start, base + j))
                    start = j
                    i = j
                    continue
            i += 1
        if start < n:
            spans.append((base + start, base + n))
        base += n + 1
    return [(s, e) for s, e in spans if chunk[s:e].strip()]


_PLACEHOLDER_RE = re.compile(r"\x00P[A-Z]+\x00")


def _placeholder_only(core: str) -> bool:
    """True when a 'sentence' is nothing but protected spans (e.g. a
    code fence sitting on its own line) — not prose: never counted,
    never enforced."""
    if not _PLACEHOLDER_RE.search(core):
        return False
    rest = _PLACEHOLDER_RE.sub("", core)
    return not re.search(r"[A-Za-z0-9]", rest)


def _skeleton(text: str):
    """Replace protected spans with digit-free placeholders so a
    sentence that WRAPS a URL/quote/code span stays one sentence.
    Returns (skeleton_text, [protected spans in order])."""
    spans = []

    def code(n: int) -> str:
        out = ""
        while n:
            n, r = divmod(n - 1, 26)
            out = chr(65 + r) + out
        return out

    parts = []
    pos = 0
    for m in _PROTECTED_RE.finditer(text):
        parts.append(text[pos:m.start()])
        spans.append(m.group(0))
        parts.append("\x00P" + code(len(spans)) + "\x00")
        pos = m.end()
    parts.append(text[pos:])
    return "".join(parts), spans


def _restore(text: str, spans) -> str:
    for i, span in enumerate(spans):
        # rebuild the same letter code _skeleton used
        n, out = i + 1, ""
        while n:
            n, r = divmod(n - 1, 26)
            out = chr(65 + r) + out
        text = text.replace("\x00P" + out + "\x00", span, 1)
    return text


def measure(text: str) -> dict:
    """Sentence-level profanity stats over the prose of a reply.
    Protected spans become placeholders first, so a sentence that
    contains a URL or a quote still counts as ONE sentence."""
    skel, _ = _skeleton(text)
    total = 0
    profane = 0
    for s, e in _sentence_spans(skel):
        if _placeholder_only(skel[s:e]):
            continue
        total += 1
        if has_profanity(skel[s:e]):
            profane += 1
    return {"sentences": total, "profane": profane,
            "pct": (100.0 * profane / total) if total else 100.0}


# --- Guards + the two rewrite paths --------------------------------------------


def _numbers(s: str):
    return sorted(_NUMBER_RE.findall(s))


def _names(s: str):
    out = set()
    for m in _NAME_RE.finditer(s):
        if m.start() == 0:
            continue  # sentence-initial capital: can't tell name vs starter
        out.add(m.group(0).lower())
    return out


def _rewrite_ok(orig: str, new: str) -> bool:
    """Guard for a model rewrite. Protected-span placeholders must
    survive verbatim; numbers and non-initial capitalized names are
    compared on the placeholder-stripped text."""
    if not new or not has_profanity(new):
        return False
    if sorted(_PLACEHOLDER_RE.findall(orig)) != \
            sorted(_PLACEHOLDER_RE.findall(new)):
        return False
    o = _PLACEHOLDER_RE.sub(" ", orig)
    n = _PLACEHOLDER_RE.sub(" ", new)
    if _numbers(o) != _numbers(n):
        return False
    if not _names(o).issubset(_names(n)):
        return False
    return 0.4 * len(orig) <= len(new) <= 2.5 * len(orig) + 40


_REWRITE_SYSTEM = (
    "You are OG's register editor. Rewrite the ONE sentence you are "
    "given so it contains natural profanity in OG's voice (gangster, "
    "profane, street-smart). Change NOTHING else: same facts, same "
    "numbers, same names, same meaning, roughly the same length. Add "
    "one or two swear words where a person talking like OG would "
    "drop them. Output ONLY the rewritten sentence — no quotes, no "
    "commentary, no markdown.")


def _model_rewrite(sentence: str):
    """One small OpenAI chat call; returns text or None. Uses the
    deployment's existing key/client pattern (urllib, like the
    other og_ modules); never raises."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    model = os.environ.get("OG_REGISTER_MODEL", "").strip() or \
        os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": _REWRITE_SYSTEM},
                     {"role": "user", "content": sentence}],
        "temperature": 0.7,
        "max_tokens": max(60, min(400, len(sentence) * 2)),
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode())
        text = data["choices"][0]["message"]["content"].strip()
        return text or None
    except Exception:
        return None


_PREFIX_RE = re.compile(r"^(\s*(?:#{1,6}\s+|[-*•]\s+|\d+[.)]\s+)?)(.*)$",
                        re.S)


def _fallback_insert(sentence: str) -> str:
    """Deterministic insertion: a persona-natural opener in front of
    the sentence (list/heading markers stay in front)."""
    m = _PREFIX_RE.match(sentence)
    prefix, core = m.group(1), m.group(2)
    if not core.strip():
        return sentence
    opener = _OPENERS[int(hashlib.md5(
        core.encode()).hexdigest(), 16) % len(_OPENERS)]
    first = re.match(r"[A-Za-z']+", core)
    if first and first.group(0) != "I" \
            and first.group(0).lower() in _STARTERS:
        core = core[0].lower() + core[1:]
    return f"{prefix}{opener} {core}"


# --- The pass -------------------------------------------------------------------


def enforce_text(text: str, rewrite_fn=None, max_rewrites=None):
    """Enforce the register on one full reply. Returns
    (new_text, stats). Unchanged text (same object content) when the
    pass is disabled, the reply is a structured tool document, or
    every prose sentence already passes."""
    t0 = time.perf_counter()
    stats = {"sentences": 0, "passed": 0, "rewritten": 0, "fallback": 0,
             "left": 0, "attempts": 0, "rewrite_tokens": 0, "ms": 0.0,
             "skipped": False}
    if not text or not _enabled() or _SHEET_RE.search(text):
        stats["skipped"] = bool(text) and bool(_SHEET_RE.search(text or ""))
        stats["ms"] = (time.perf_counter() - t0) * 1000
        return text, stats
    if rewrite_fn is None:
        rewrite_fn = _model_rewrite
    if max_rewrites is None:
        max_rewrites = _max_rewrites()
    skel, protected_spans = _skeleton(text)
    pieces = []
    pos = 0
    for s, e in _sentence_spans(skel):
        pieces.append(skel[pos:s])
        sentence = skel[s:e]
        core = sentence.strip()
        lead = sentence[:len(sentence) - len(sentence.lstrip())]
        trail = sentence[len(sentence.rstrip()):]
        if _placeholder_only(core):
            pieces.append(sentence)
            pos = e
            continue
        stats["sentences"] += 1
        if has_profanity(core):
            stats["passed"] += 1
            pieces.append(sentence)
        else:
            new = None
            if stats["attempts"] < max_rewrites:
                stats["attempts"] += 1
                cand = rewrite_fn(core)
                if cand and _rewrite_ok(core, cand):
                    new = cand
                    stats["rewritten"] += 1
                    stats["rewrite_tokens"] += \
                        len(core) // 4 + len(cand) // 4
            if new is None:
                new = _fallback_insert(core)
                if has_profanity(new):
                    stats["fallback"] += 1
                else:
                    stats["left"] += 1
            pieces.append(lead + new + trail)
        pos = e
    pieces.append(skel[pos:])
    stats["ms"] = (time.perf_counter() - t0) * 1000
    return _restore("".join(pieces), protected_spans), stats


def enforce_reply(text: str) -> str:
    """app.py's one-line binding: enforced text, stats logged."""
    new, stats = enforce_text(text)
    if stats["sentences"] and (stats["rewritten"] or stats["fallback"]
                               or stats["left"]):
        import logging
        logging.getLogger(__name__).info(
            "register enforce: %s", {k: stats[k] for k in (
                "sentences", "passed", "rewritten", "fallback", "left",
                "ms")})
    return new


# --- Streaming: a sink wrapper that enforces per completed sentence --------------


class RegisterSink:
    """Wraps an SSE sink(kind, payload). 'chunk' payloads are buffered
    and re-emitted only as complete enforced sentences (code fences
    pass through whole and byte-identical once closed; an unclosed
    fence flushes raw past a size cap so nothing can deadlock).
    Other kinds pass straight through. flush() enforces and emits the
    buffered tail; text() returns everything emitted (the delivered
    reply, for the done payload + stored history)."""

    _FENCE_HOLD_CAP = 24000

    def __init__(self, sink, rewrite_fn=None):
        self._sink = sink
        self._rewrite_fn = rewrite_fn
        self._buf = ""
        self._emitted = []
        self.stats = {"chunks_in": 0}

    def __call__(self, kind, payload):
        if kind != "chunk":
            self._sink(kind, payload)
            return
        self.stats["chunks_in"] += 1
        self._buf += payload or ""
        self._drain(final=False)

    def _drain(self, final: bool):
        while self._buf:
            fence = self._buf.find("```")
            if fence > 0:
                head, self._buf = self._buf[:fence], self._buf[fence:]
                self._emit_enforced(head)
                continue
            if fence == 0:
                close = self._buf.find("```", 3)
                if close < 0:
                    if final or len(self._buf) > self._FENCE_HOLD_CAP:
                        self._emit_raw(self._buf)
                        self._buf = ""
                    return
                self._emit_raw(self._buf[:close + 3])
                self._buf = self._buf[close + 3:]
                continue
            # plain prose: emit every complete sentence, hold the tail
            spans = _sentence_spans(self._buf)
            if not spans:
                if final:
                    self._emit_raw(self._buf)
                    self._buf = ""
                return
            last_end = spans[-1][1]
            if not final and (len(spans) == 1
                              or self._buf[last_end:].strip() == ""):
                # the last span may still grow (or be the tail): hold
                # everything from the last span's start
                hold_from = spans[-1][0]
                if hold_from == 0:
                    return
                self._emit_enforced(self._buf[:hold_from])
                self._buf = self._buf[hold_from:]
                continue
            cut = last_end
            self._emit_enforced(self._buf[:cut])
            self._buf = self._buf[cut:]
            if final:
                self._emit_enforced(self._buf)
                self._buf = ""
                return

    def _emit_enforced(self, text: str):
        if not text:
            return
        try:
            new, _ = enforce_text(text, rewrite_fn=self._rewrite_fn)
        except Exception:
            new = text  # fail-open: a register bug never eats a reply
        self._emit_raw(new)

    def _emit_raw(self, text: str):
        if text:
            self._emitted.append(text)
            self._sink("chunk", text)

    def flush(self):
        self._drain(final=True)
        if self._buf:
            self._emit_enforced(self._buf)
            self._buf = ""

    def text(self) -> str:
        return "".join(self._emitted)


def wrap_sink(sink, rewrite_fn=None) -> RegisterSink:
    return RegisterSink(sink, rewrite_fn=rewrite_fn)
