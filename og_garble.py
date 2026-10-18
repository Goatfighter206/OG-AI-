"""OG garble tolerance — the shared second chance (Round 75).

Brent's order (2026-10-11): "like how I mess up words and you
get it right I want og to do that to." In open chat the model
already reads through garble; the brittle parts are the
deterministic seams that pattern-match the visitor's words
(site claims, directions, mail/calendar jobs, watches, orders,
trades). Those seams call this module — and ONLY after their own
exact parser has said no, so a clean ask behaves byte-identically
to before: same job, no extra keys, no extra words in the reply.

What repair() does, and nothing more:

- Tokenizes the visitor's ORIGINAL text and rewrites individual
  tokens, in place, only when a token is one small typo away
  from exactly ONE word in the seam's OWN trigger vocabulary
  (stdlib difflib ratios, a length-scaled floor, and a clear
  margin over the runner-up — a tie rewrites nothing).
- Handles the dictation classics: a dropped/doubled/swapped
  letter ("facebok", "emial", "wacth"), a site name stuck to
  its verb or split in two ("face book", "market place",
  "g mail"), and a glued pair of vocabulary words ("bestbuy").
- Payload is never touched: repair only ever substitutes INTO
  the seam's vocabulary, so place names, items, people,
  amounts and tickers pass through byte-for-byte. Real English
  words that merely resemble a trigger (shell, witch, batch,
  mall, trail, cell…) are on a never-rewrite list, tokens of
  two letters or fewer are never rewritten, and short ALL-CAPS
  tokens (tickers) are never rewritten short of a near-exact
  match.
- A seam may pass a tiny homophone table for its own
  vocabulary (e.g. "too" -> "to" for navigation asks). The
  dangerous pairs are excluded by construction everywhere:
  by/bye -> buy, cell -> sell, male -> mail, red -> read are
  NOT in any seam's table (see round-75/NOTES.md).

A repair that changed something returns the fixes it made; the
claiming seam stamps them on the job, and its answer carries
the grounded read-back note (annotate()) so the visitor can
catch a wrong guess. No fixes -> the caller behaves exactly as
it did before this module existed.

Pure logic: no state, no network, no persona text.
"""

import difflib
import re
from typing import Dict, Iterable, List, Optional, Tuple

_WORD_RE = re.compile(r"[A-Za-z]+")

# Round 321 DBPOOL batch-5: the mid-loop deadline. The
# claim budget's guard consults the deadline at repair
# ENTRY only, so one long repair call in flight at the
# deadline ran its whole token loop (~3,053 repairs per
# plain turn is the fleet's summed reservoir; each call's
# overrun is small but they stack at production prices).
# repair() now also consults the deadline INSIDE its
# loops (every 32 steps): once blown it stops — the token
# loop abandons the repair wholesale, returning the
# original text with no fixes, which is exactly the
# entry guard's blown shape, so no seam can tell the
# difference; the merge loop keeps the repairs already
# made. Fail-soft in both directions: no budget machinery
# importable, or no budget begun on this thread (every
# suite and non-chat caller), means never blown — the
# pre-batch-5 behaviour, byte for byte.
try:
    import og_claimbudget as _claimbudget
except Exception:  # garble must never break on the budget
    _claimbudget = None


def _deadline_blown() -> bool:
    if _claimbudget is None:
        return False
    try:
        if not _claimbudget.claim_budget_open():
            return True
        return bool(_claimbudget.turn_budget_blown())
    except Exception:
        return False

# Real words that sit close to common trigger words. Rewriting
# one of these would manufacture an action out of ordinary
# chat, so they are never substituted (a seam's explicit
# homophone opt-in is the only way past this list, and no seam
# opts in for any of these).
NEVER = frozenset({
    "shell", "witch", "batch", "match", "catch", "patch",
    "latch", "hatch", "mall", "trail", "cell", "male", "red",
    "read", "sell", "buy", "watch", "site", "sight", "sent",
    "cent", "peace", "piece", "weak", "week", "hear", "here",
    "their", "there", "then", "than", "them", "when", "went",
    "want", "were", "west", "what", "with", "word", "work",
    "worn", "warn", "warm", "ward", "wart",
})


def _min_ratio(tok: str, cand: str) -> float:
    """The difflib floor for a rewrite, scaled by the shorter
    word: short words must be nearer-exact than long ones."""
    n = min(len(tok), len(cand))
    if n <= 3:
        return 0.85
    if n == 4:
        return 0.80
    return 0.78


def _transposed(a: str, b: str) -> bool:
    """True when a becomes b by swapping ONE pair of adjacent
    letters — the classic dictation slip ("trun" for "turn")
    that plain difflib scores low (0.75 on a 4-letter word)."""
    if len(a) != len(b) or len(a) < 4:
        return False
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    return len(diff) == 2 and diff[1] == diff[0] + 1 \
        and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]


def _best(tok: str, singles) -> Optional[str]:
    """The one vocabulary word `tok` is a small typo of, or
    None. Unique argmax with a clear margin — a near-tie
    between two readings rewrites nothing."""
    scored = []
    for cand in singles:
        if abs(len(cand) - len(tok)) > 2:
            continue
        r = difflib.SequenceMatcher(None, tok, cand).ratio()
        if _transposed(tok, cand):
            r = max(r, 0.86)
        if r >= _min_ratio(tok, cand):
            scored.append((r, cand))
    if not scored:
        return None
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.03:
        return None
    return scored[0][1]


def _structures(vocab: Iterable[str]):
    """(singles, phrase_joined, token_set) for a seam's
    vocabulary: single words; multi-word phrases keyed by their
    glued form ("best buy" -> key "bestbuy"); and every word a
    token is allowed to be (singles + phrase components), which
    is also the glue-split alphabet."""
    singles = set()
    phrase_joined = {}
    token_set = set()
    for entry in vocab:
        w = " ".join(str(entry).lower().split())
        if not w:
            continue
        if " " in w:
            phrase_joined[w.replace(" ", "")] = w
            token_set.update(w.split())
        else:
            singles.add(w)
            token_set.add(w)
    return singles, phrase_joined, token_set


def repair(text: str, vocab: Iterable[str],
           homophones: Optional[Dict[str, str]] = None
           ) -> Tuple[str, List[Tuple[str, str]]]:
    """(repaired_text, fixes) — fixes is a list of
    (heard, understood) fragments, empty when nothing changed.
    Only trigger-vocabulary tokens are ever rewritten, in place,
    so the rest of the visitor's words — payload — survive
    byte-for-byte, case included."""
    raw = str(text or "")
    if not raw:
        return raw, []
    singles, phrase_joined, token_set = _structures(vocab)
    homophones = {str(k).lower(): str(v).lower()
                  for k, v in (homophones or {}).items()}
    toks = [(m.start(), m.end(), m.group(0))
            for m in _WORD_RE.finditer(raw)]
    if not toks:
        return raw, []
    # edits: (start, end, replacement) over the ORIGINAL string.
    edits: Dict[int, Tuple[int, int, str]] = {}
    fixes: List[Tuple[str, str]] = []
    consumed = set()

    for i, (s, e, surface) in enumerate(toks):
        if i and i % 32 == 0 and _deadline_blown():
            # Blown mid-loop: abandon the repair wholesale
            # (the entry guard's blown shape — original
            # text, no fixes), never a half-repaired string.
            return raw, []
        low = surface.lower()
        if low in token_set:
            continue  # already a vocabulary word — verbatim
        repl = None
        if low in homophones and homophones[low] in token_set:
            repl = homophones[low]
        elif low in phrase_joined:
            repl = phrase_joined[low]
        elif len(low) >= 5 and low not in NEVER:
            splits = set()
            for cut in range(2, len(low) - 1):
                a, b = low[:cut], low[cut:]
                if a in token_set and b in token_set:
                    splits.add((a, b))
            if len(splits) == 1:
                a, b = next(iter(splits))
                repl = f"{a} {b}"
        if repl is None and len(low) >= 3 and low not in NEVER:
            if not (surface.isupper() and len(surface) <= 5):
                repl = _best(low, singles)
            else:
                cand = _best(low, singles)
                if cand is not None and difflib.SequenceMatcher(
                        None, low, cand).ratio() >= 0.92:
                    repl = cand
        if repl is not None:
            edits[i] = (s, e, repl)
            fixes.append((surface, repl))
            consumed.add(i)

    # Merge pass: two untouched neighbours whose glued form is
    # one vocabulary word ("face book" -> "facebook",
    # "g mail" -> "gmail", "market place" -> "marketplace").
    i = 0
    _merge_steps = 0
    while i < len(toks) - 1:
        _merge_steps += 1
        if _merge_steps % 32 == 0 and _deadline_blown():
            break  # keep the repairs already made (below)
        if i in consumed or (i + 1) in consumed:
            i += 1
            continue
        s1, _e1, w1 = toks[i]
        _s2, e2, w2 = toks[i + 1]
        joined = (w1 + w2).lower()
        if (w1.lower() not in NEVER and w2.lower() not in NEVER
                and len(joined) >= 4 and joined in singles):
            edits[i] = (s1, e2, joined)
            fixes.append((f"{w1} {w2}", joined))
            consumed.add(i)
            consumed.add(i + 1)
            i += 2
            continue
        i += 1

    if not edits:
        return raw, []
    out = []
    last = 0
    for i in sorted(edits):
        s, e, repl = edits[i]
        out.append(raw[last:s])
        out.append(repl)
        last = e
    out.append(raw[last:])
    return "".join(out), fixes


def repair_info(text: str, vocab: Iterable[str],
                homophones: Optional[Dict[str, str]] = None
                ) -> Tuple[str, Optional[Dict]]:
    """(repaired_text, info-or-None): the stamp a claiming seam
    puts on its job — the visitor's original words, the reading
    acted on, and the per-word fixes. None when nothing was
    repaired (the clean path: no stamp, no read-back)."""
    repaired, fixes = repair(text, vocab, homophones)
    if not fixes:
        return str(text or ""), None
    return repaired, {"heard": str(text or ""), "read": repaired,
                      "fixes": fixes}


def note_text(info: Dict) -> str:
    """The grounded read-back, in the seams' established
    "(… note for OG: …)" shape (the R73 pattern)."""
    heard = " ".join(str(info.get("heard") or "").split())[:140]
    read = " ".join(str(info.get("read") or "").split())[:140]
    return (f"(Garble note for OG: the visitor's ask came "
            f"through garbled — they wrote \"{heard}\"; you "
            f"understood it as \"{read}\". Say what you "
            f"understood, plainly, early in your answer, so "
            f"they can catch a wrong guess — then answer it.)")


def annotate(results, info: Optional[Dict], prepend: bool = False):
    """Attach the read-back note to the first result's body.
    A no-op for clean asks (info None) and empty results, so
    exact-match answers stay byte-identical."""
    if not results or not info:
        return results
    note = note_text(info)
    first = dict(results[0])
    body = str(first.get("body") or "")
    first["body"] = (note + "\n" + body) if prepend \
        else (body + "\n" + note)
    return [first] + list(results[1:])
