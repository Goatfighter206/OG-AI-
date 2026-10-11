"""Retaliation-only comebacks for OG (Round 65, Brent's order
2026-10-11): a set of 15 comeback lines in Brent's own words,
fired back ONLY when the user talks shit at OG first — and
only while swearing is ON (the swearing_on_switch_off_switch
gate). OG never opens with these, never uses them on a user
who is swearing about something else, and never uses them at
all when the profanity switch is off.

THE SET IS BRENT'S WORDS VERBATIM. Do NOT normalize, correct,
or "fix" any line — two contain suspected recognizer slips
("writing" / "Formen" in line 12, "Nabi" twice) and they stay
EXACTLY as Brent gave them. The r65 suite pins every line
byte-identical against the spec.

DETECTION IS CONSERVATIVE BY DESIGN. The incoming message
must DIRECTLY insult OG in the user's own voice: second-person
profanity/abuse aimed at the assistant ("fuck you", "you're
stupid / a bitch / an idiot", "shut up bitch", ...). A user
swearing ABOUT something else, quoting someone, or venting at
a third party must NOT trigger — a reporting/quoting guard
runs FIRST and suppresses the whole check, and every trigger
pattern requires an aimed-at-OG shape. When unsure: no fire.

MECHANISM. When (and only when) the detector fires AND
swearing_on() is True, comeback_directive() returns a runtime
directive for that turn quoting ONE line from the set
verbatim; og_language's combined R65 installer appends it to
that turn's system prompt (the agent-seam wrapping pattern
from the swearing switch). When swearing is OFF: no directive,
ever — the set stays locked. When not triggered: nothing —
the normal register is untouched, byte-identical.
"""

import logging
import random
import re

logger = logging.getLogger(__name__)

# --- The set — Brent's words, byte-exact, never edited -------------------------

COMEBACKS = [
    "son of a bitch",
    "fuck you bitch",
    "Go set back in your machine, you fat piece of shit",
    "Go sit on it and spin",
    "what the fuck you damn Nabi",
    "suck my hotdog you damn nabi",
    "you look like you suck hotdog down",
    "oh you're still going",
    "my pants are coming down now",
    "starting to get me hard",
    "I brought you some Chapstick because we all know you were under the bosses table again",
    "Oh, you're writing in the middle in between the Super and the Formen I take it you were skiing again",
    "I bet you had to suck a lot of them to get in that seat and you probably got no more gag reflex",
    "what made you come to work looking like that today?",
    "Look like you were ready to get your ass pounded",
]

# --- Detector -------------------------------------------------------------------

_INSULT = (r"(?:stupid|dumb|idiot|bitch|moron|useless|worthless|trash|"
           r"garbage|pathetic|loser|asshole|dumbass|clown|joke)")

# Reporting / quoting frames: the abuse is someone ELSE's words, or
# aimed at a third party. Any match suppresses the trigger outright.
_REPORTING_RE = re.compile(
    r"\b(?:he|she|they|him|her|them|someone|somebody|my boss|my coworker|"
    r"my co-worker|the driver|this guy|that guy|a guy)\b[^.!?]{0,40}"
    r"\b(?:said|says|told|called|yelled|yelling|texted|wrote|shouted)\b"
    r"|\b(?:said|told me|called me|yelled at me|texted me|wrote)\b"
    r"|\bquote\b|\bquoting\b"
    r"|\bwhat does\b[^.!?]{0,40}\bmean\b"
    r"|\bhow (?:do|would) you say\b|\btranslate\b", re.IGNORECASE)

_TRIGGER_RES = [
    # "fuck you" / "fuck u" / "screw you" / "damn you"
    re.compile(r"\b(?:fuck|screw|damn)\s+(?:you|u)\b", re.IGNORECASE),
    # "shut up" — a direct order to OG in a chat with OG, with or
    # without a trailing insult.
    re.compile(r"\bshut\s+up\b", re.IGNORECASE),
    # "you / you're / you are (+ a/an/so/really/fucking/damn) + insult"
    re.compile(r"\byou(?:'re|\s+are|\s+r)?\s+"
               r"(?:(?:a|an|so|really|fucking|damn|such\s+a)\s+)?"
               + _INSULT + r"\b", re.IGNORECASE),
    # "you piece of shit"
    re.compile(r"\byou\s+(?:fucking\s+|damn\s+)?piece\s+of\s+shit\b",
               re.IGNORECASE),
    # "you suck"
    re.compile(r"\byou\s+suck\b", re.IGNORECASE),
    # vocative aimed at the assistant itself: "stupid bot/og/ai"
    re.compile(r"\b" + _INSULT + r"\s+(?:bot|og|ai|assistant)\b",
               re.IGNORECASE),
    re.compile(r"\b(?:bot|og|ai|assistant)\s+(?:is\s+)?" + _INSULT + r"\b",
               re.IGNORECASE),
]


def is_og_insult(message) -> bool:
    """True only when `message` directly insults OG in the
    user's own voice. Conservative: the reporting/quoting
    guard vetoes first, and every pattern needs an
    aimed-at-OG shape. Anything unsure returns False."""
    text = str(message or "").strip()
    if not text:
        return False
    if _REPORTING_RE.search(text):
        return False
    return any(rx.search(text) for rx in _TRIGGER_RES)


def pick_comeback(message="") -> str:
    """One line from the set, verbatim."""
    return random.choice(COMEBACKS)


def comeback_directive(message) -> str:
    """The runtime directive for this turn, or "" — empty when
    the message does not insult OG, and empty whenever swearing
    is OFF (the set stays locked, no matter the insult)."""
    try:
        import swearing_on_switch_off_switch as _sw
        if not _sw.swearing_on():
            return ""
    except Exception as e:  # gate unreadable -> persona default is ON
        logger.warning(f"Comeback gate check failed: {e}")
    if not is_og_insult(message):
        return ""
    line = pick_comeback(message)
    return (
        "RETALIATION — THE USER JUST TALKED SHIT AT YOU: fire "
        "back with exactly this one comeback line, word for "
        f"word, as your opening: \"{line}\" — then keep helping "
        "as usual, same personality, one best answer.")


def install_comeback_tools(agent_instance, get_uid=None) -> bool:
    """Install the combined R65 prompt seam (language +
    comebacks share ONE wrapper so they can never double-wrap
    or strip each other — see og_language). Delegates there."""
    try:
        import og_language as _lang
        return _lang.install_language_tools(agent_instance, get_uid)
    except Exception as e:
        logger.warning(f"Comeback install skipped: {e}")
        return False
