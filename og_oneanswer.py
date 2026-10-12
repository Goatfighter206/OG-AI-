"""Round 89 — ONE BEST ANSWER, as a runtime directive.

Brent's complaint (2026-10-12): "he is still saying multiple
things — he is supposed to pick the best one and say that."
His oldest persona rule (locked 2026-10-08) lives ONLY inside
the locked persona file, mid-prompt:

    TWO LAWS FOR EVERY ANSWER:
    1. ONE ANSWER — THE BEST ONE. When there's a call to
    make, YOU make it. … People come to OG for the call,
    not a fucking catalog.

Nothing restates it at the END of the assembled system
prompt — the slot every other law that needed teeth (R65
language, the R84 clean register) rides. On a plain English
advice turn og_language.round65_directives() returns
nothing, so the model sees the law once, buried. This
module is the missing end-of-prompt reinforcement: a
directive block appended LAST in the assembled system
prompt, every turn, on both prompt seams
(_generate_ai_response and detect_intent — the house
install_*_tools pattern from og_language /
swearing_on_switch_off_switch).

The persona file is never touched; og_language is never
touched (its byte-identity for directive-less turns is
r65-pinned). Installation is piggybacked fail-safe from
og_fixqueue.install_fixqueue_tools, BETWEEN the swearing
switch and R65: wrappers run outermost-first, so at
assembly time R65's wrapper appends its block first and
this wrapper appends the R89 block after it — final word
before the conversation, on the generate seam (restored
after the call) and on the detect seam (left in place,
like R65's own detect wrap, for app.py's streaming
branches that read agent.system_prompt after
detect_intent).

NO POST-GUARD: this module deliberately exposes no
function that touches a reply. A pattern trimmer cannot
tell an answer-menu from "you could also…" inside
step-by-step instructions, an "or" inside directions, an
asked-for list, or options inside an approval ask — and a
trim that eats real content is worse than a menu that
slips. The protected shapes are protected in the directive
text itself instead.
"""

import logging

logger = logging.getLogger(__name__)

MARKER = "\n\n--- OG RUNTIME (R89) ---\n"

DIRECTIVE = (
    "ONE ANSWER — FINAL WORD, EVERY REPLY: when this turn "
    "asks for a call — a recommendation, a 'best' anything, "
    "an either/or, advice with more than one possible "
    "answer — YOU make the call. Open with the single best "
    "answer and commit to it like it's yours. Never close "
    "by handing the choice back: no 'let me know which "
    "one', no 'either works', no 'it depends on you'. Do "
    "not parade the runners-up as a menu — another option "
    "gets named only in one line on why it loses. 'It "
    "depends' is banned as an ending: if the answer truly "
    "turns on something, name the thing, then STILL give "
    "your pick for the usual case. NOT menus, and they stay "
    "in full: step-by-step instructions, directions, lists "
    "the user asked for, and facts laid out as facts. One "
    "sharp clarifying question when you genuinely don't "
    "have what you need, and asking approval before you "
    "act, are not menus either — ask, then move.")


def _strip_block(prompt: str) -> str:
    if prompt and MARKER in prompt:
        return prompt.split(MARKER, 1)[0]
    return prompt


def oneanswer_prompt(base):
    """The system prompt for one chat call with the Round 89
    directive applied: the base with any stale R89 block
    stripped and exactly one fresh block appended LAST.
    A missing/empty base passes through unchanged (there is
    no persona to reinforce). Idempotent."""
    if not base:
        return base
    clean = _strip_block(str(base))
    return clean + MARKER + DIRECTIVE


def install_oneanswer_tools(agent_instance, get_uid=None) -> bool:
    """Wrap the agent's prompt-assembly seams so the R89
    block is the last text of the system prompt on every
    call. The law is universal — no uid is consulted
    (get_uid is accepted only for chain-signature
    uniformity with the neighboring installers).
    Idempotent; returns True when installed (or already
    installed). Never raises on a missing seam."""
    if getattr(agent_instance, "_og_r89_installed", False):
        return True
    installed = False

    prev_generate = getattr(agent_instance, "_generate_ai_response", None)
    if callable(prev_generate):

        def generate_wrapped(message, context=""):
            original = getattr(agent_instance, "system_prompt", None)
            try:
                if hasattr(agent_instance, "system_prompt"):
                    agent_instance.system_prompt = oneanswer_prompt(
                        agent_instance.system_prompt)
            except Exception as e:  # assembly never eats a turn
                logger.warning(f"R89 assemble failed: {e}")
            try:
                return prev_generate(message, context)
            finally:
                try:
                    if hasattr(agent_instance, "system_prompt"):
                        agent_instance.system_prompt = original
                except Exception:
                    pass

        agent_instance._generate_ai_response = generate_wrapped
        installed = True

    prev_detect = getattr(agent_instance, "detect_intent", None)
    if callable(prev_detect):

        def detect_wrapped(message):
            try:
                if hasattr(agent_instance, "system_prompt"):
                    agent_instance.system_prompt = oneanswer_prompt(
                        agent_instance.system_prompt)
            except Exception as e:  # normalization never eats a turn
                logger.warning(f"R89 normalize failed: {e}")
            return prev_detect(message)

        agent_instance.detect_intent = detect_wrapped
        installed = True

    if installed:
        agent_instance._og_r89_installed = True
    return installed
