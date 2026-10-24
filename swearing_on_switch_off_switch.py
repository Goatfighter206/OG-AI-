"""swearing on switch off switch

Brent's order (final form, 2026-10-11): "Call the file swearing
on switch off switch." This module IS that switch — the single
gate for OG's profanity register, in both directions:

    OG_PROFANITY_MODE=off  -> swearing OFF
    unset / empty / "on"   -> swearing ON (the ship state;
                              nothing sets the env, so OG talks
                              exactly as he always has)

swearing_on() reads the env PER CALL — no import-time caching,
the OG_OWNER_EMAIL pattern in og_tiers.py — so flipping the
env flips behavior on the very next reply, mid-process, with
no restart and no redeploy of code.

THE GATE HAS TWO HALVES, both wired from this one module:

1. THE ENFORCER (og_register). enforce_text — the shared entry
   every chat path routes through (classic /chat, the SSE
   RegisterSink, the streaming fallback, og_respond drafts) —
   asks swearing_on() first. OFF: it returns the reply text
   unchanged; it inserts and forces NOTHING. ON/unset: the
   original Round 26 pass runs byte-for-byte as before.

2. THE CLEAN-REGISTER DIRECTIVE (prompt assembly). When OFF,
   prompt assembly appends CLEAN_DIRECTIVE to the system
   prompt the model sees for that call: shit-talk mode is
   off, no swearing, same street-smart helpful personality,
   same one-best-answer decisive style — just clean. When
   ON/unset, nothing is appended and assembly is
   byte-identical to before this module existed.

PROMPT SEAM — the locked persona file (ai_agent_enhanced.py)
is never touched. Every provider method in it reads
self.system_prompt at call time inside _generate_ai_response,
and every chat surface reaches the model through that method
(process_message for classic /chat, og_respond's draft path,
the streaming fallback in app.py) — EXCEPT app.py's streaming
OpenAI/Anthropic branches, which build their messages from
agent.system_prompt right after calling agent.detect_intent.
install_swearing_switch therefore wraps both seams on the
agent instance (the house install_*_tools pattern):

  * _generate_ai_response: before the call, agent.system_prompt
    is set to effective_system_prompt for the CURRENT env value
    (so a draft generated without a detect_intent pass — the
    og_respond path — still assembles correctly, and a stale
    directive from a previous OFF turn is stripped the moment
    the env flips back ON).
  * detect_intent: at the top of each turn, agent.system_prompt
    is normalized to the effective prompt for the CURRENT env
    value, so the streaming branches — which read it after
    detect_intent — assemble with the directive when OFF and
    with the byte-original prompt when ON.

effective_system_prompt is idempotent (the directive is never
appended twice) and, when ON, returns the base prompt object
unchanged. Installation itself is piggybacked fail-safe from
og_fixqueue.install_fixqueue_tools (the last install in
app.py's get_agent; app.py is at its push ceiling) — a failure
here can never take chat down, it just leaves the switch
uninstalled and OG talking as before.
"""

import logging
import os

logger = logging.getLogger(__name__)

ENV_VAR = "OG_PROFANITY_MODE"

CLEAN_DIRECTIVE = (
    "CLEAN REGISTER — SHIT-TALK MODE IS OFF: do not swear or use "
    "any profanity in your reply. Keep the exact same street-smart, "
    "genuinely helpful personality and the same one-best-answer, "
    "decisive style — no menus, no hedging — just say it clean.")


def swearing_on() -> bool:
    """True unless OG_PROFANITY_MODE is exactly "off" (any case,
    surrounding whitespace ignored). Read fresh from the env on
    EVERY call — unset, empty, "on", or any other value all mean
    ON: OG's profane register is the default and only an explicit
    "off" turns it off."""
    return (os.environ.get(ENV_VAR) or "").strip().lower() != "off"



def _base_prompt(prompt: str) -> str:
    """Strip our own directive suffix (if present) so the
    effective prompt can be recomputed from the true base."""
    suffix = "\n\n" + CLEAN_DIRECTIVE
    if prompt and prompt.endswith(suffix):
        return prompt[: -len(suffix)]
    if prompt == CLEAN_DIRECTIVE:
        return ""
    return prompt


def effective_system_prompt(base, uid=None) -> str:
    """The system prompt for one chat call. ON/unset: `base`
    returned unchanged (the same object). OFF: base + the clean
    directive, exactly once, however many times this is applied.

    Round 84: OFF also when the resolved account's own
    shit-talking switch is OFF (og_shittalk). `uid` None ->
    og_shittalk's noted current uid; no uid resolvable ->
    the master gate alone decides, exactly the original
    behavior (the same object back when ON)."""
    base = base or ""
    if swearing_on() and not _user_off(uid):
        return _base_prompt(base) if CLEAN_DIRECTIVE in base else base
    base = _base_prompt(base)
    if not base:
        return CLEAN_DIRECTIVE
    return base + "\n\n" + CLEAN_DIRECTIVE


def _user_off(uid=None) -> bool:
    """True only when a uid resolves AND that account stored
    its shit-talking switch OFF. Fail-open: any problem at
    all reads as not-off (OG's shipped default is ON)."""
    try:
        import og_shittalk as _st
        if uid is None:
            uid = _st.current_uid()
        if not uid:
            return False
        return not _st.on_for(uid)
    except Exception:
        return False


def install_swearing_switch(agent_instance) -> bool:
    """Wrap the agent's prompt-assembly seams (see the module
    docstring). Idempotent; returns True when installed (or
    already installed). Never raises on a missing seam — a
    partial agent simply keeps whichever wrap was possible."""
    if getattr(agent_instance, "_og_swearing_switch_installed", False):
        return True
    installed = False

    prev_generate = getattr(agent_instance, "_generate_ai_response", None)
    if callable(prev_generate):

        def generate_wrapped(message, context=""):
            try:
                if hasattr(agent_instance, "system_prompt"):
                    agent_instance.system_prompt = \
                        effective_system_prompt(
                            agent_instance.system_prompt)
            except Exception as e:  # assembly never eats a turn
                logger.warning(f"swearing switch assemble failed: {e}")
            return prev_generate(message, context)

        agent_instance._generate_ai_response = generate_wrapped
        installed = True

    prev_detect = getattr(agent_instance, "detect_intent", None)
    if callable(prev_detect):

        def detect_wrapped(message):
            try:
                if hasattr(agent_instance, "system_prompt"):
                    agent_instance.system_prompt = \
                        effective_system_prompt(
                            agent_instance.system_prompt)
            except Exception as e:  # normalization never eats a turn
                logger.warning(f"swearing switch normalize failed: {e}")
            return prev_detect(message)

        agent_instance.detect_intent = detect_wrapped
        installed = True

    if installed:
        agent_instance._og_swearing_switch_installed = True
    return installed
