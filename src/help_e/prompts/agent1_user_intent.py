"""v7 Agent 1 — user_intent + MI shortlist pick.

One small-model LLM call per turn. Reads:
  - rolling_summary_5turns  (from Agent X at t-1; "" on cold start)
  - current user message

Emits:
  - user_intent             (enum from USER_INTENTS_V6)
  - user_intent_phrase      (1-line NL: "User wants X right now")
  - mi_for_user_intent      (MISC code chosen from shortlist for this intent)

This call is intentionally narrow: it does NOT see the graph, does NOT
plan a response. It's a fast classifier-plus-MI-picker that hands off
to Agent 5.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Any

from ..config import USER_INTENTS_V6, USER_INTENTS_V6_DEF
from ..llm_client import CallContext, LLMClient, LLMStructuredError
from ..mi_picker_v7 import shortlist_for_user_intent

# Enum of valid MISC codes the LLM can pick. Schema validation catches
# anything outside the canonical 10.
from ..mi_picker_v7 import all_misc_codes as _all_misc_codes


# ---------------------------------------------------------------------------
# JSON schema (Draft 2020-12)
# ---------------------------------------------------------------------------

AGENT1_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "user_intent",
        "user_intent_phrase",
        "mi_for_user_intent",
    ],
    "properties": {
        "user_intent": {
            "type": "string",
            "enum": list(USER_INTENTS_V6),
        },
        "user_intent_phrase": {
            "type": "string",
            "minLength": 1,
            "maxLength": 240,
        },
        "mi_for_user_intent": {
            "type": "string",
            "enum": list(_all_misc_codes()),
        },
    },
}


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _user_intent_block() -> str:
    lines = ["The 8 user_intent values and what each means:"]
    for k, v in USER_INTENTS_V6_DEF.items():
        lines.append(f"  - {k}: {v}")
    return "\n".join(lines)


def _shortlist_block(intent: str | None) -> str:
    """Render the MI shortlist for a given intent.
    Used inside the user prompt AFTER the LLM picks intent — but the
    LLM picks both intent AND mi in one shot, so we render shortlists
    for ALL intents in the system prompt.
    """
    out: list[str] = ["Per-intent MI shortlists. Pick mi_for_user_intent FROM the row matching the user_intent you chose:"]
    for k in USER_INTENTS_V6:
        codes = [c["code"] for c in shortlist_for_user_intent(k)]
        out.append(f"  - {k}: {', '.join(codes)}")
    return "\n".join(out)


def build_system_prompt() -> str:
    return textwrap.dedent(f"""\
        You are the IntentAgent of the v7/v8 HELP-E pipeline.

        # YOUR ONE JOB
        From the rolling summary of the past few turns, the chatbot's
        last reply, and the user's current message, classify what the
        user wants from the chatbot right now AND pick the MI technique
        that best answers that need.

        Output ONE JSON object with three fields. No talk, no explanation.

        # FIELDS

        1. `user_intent` — exactly one of these enum values:
        {_user_intent_block()}

        2. `user_intent_phrase` — a single short sentence in plain English
           naming what the user wants from YOU (the chatbot) right now.
           Form: "User wants ..." OR "User is ...". Concrete, specific,
           ≤25 words. Do NOT just paraphrase the user's message.

        3. `mi_for_user_intent` — pick ONE MISC code from the shortlist
           for the intent you chose. The MISC code names HOW you'll
           answer the user's intent (the OPENING / FIRST move of the
           reply, before any system-side nudge).

        {_shortlist_block(None)}

        # MID-THREAD RULE (CRITICAL — read before anything else)

        If ROLLING_SUMMARY_LAST_5_TURNS is non-empty AND describes any
        ongoing topic, OR if LAST_ASSISTANT_MESSAGE is anything other
        than an opening greeting, then the user is MID-THREAD.

        When mid-thread, short / vague messages such as:
            "yeah", "ok", "sure", "right", "uh huh", "i guess",
            "what do i do", "what should i do", "what now",
            "i don't know", "idk", "what to do", "any ideas"
        are NOT `small_talk`. They are acknowledgments of, or follow-ups
        to, the active topic. NEVER classify them as `small_talk`
        mid-thread. Classify by what the user is doing relative to the
        ongoing topic:
          - "yeah" / "right" / "uh huh" after the assistant named or
            reflected a feeling → `seek_validation`
          - "what should I do" / "what now" while distressed about the
            ongoing topic → `request_plan`
          - "i don't know" / "idk" → `express_emotion` (resignation /
            stuckness) or `seek_validation` if the assistant just asked
            "what do you think"
          - silence-fillers with no content but emotion still active →
            `express_emotion`

        Use `small_talk` ONLY when:
          (a) the rolling summary is empty (true cold start), AND
          (b) the message is a greeting / sign-off / pleasantry.

        # HOW TO CHOOSE (after the mid-thread rule has been applied)

        - express_emotion / seek_validation → support / acknowledge first;
          reflect only when you can name what's UNDERNEATH the user's words
          (don't just rephrase them). Don't plan.
        - seek_information / request_plan → answer concretely; don't
          deflect with a question.
        - deliberate_decision → reflect both sides; do NOT push.
        - report_action → affirm specifics; respond to content.
        - resistance → roll with it; do NOT argue.
        - small_talk → keep it light. (Cold-start only — see mid-thread rule.)

        # OUTPUT
        Return ONLY valid JSON matching the schema. No prose.
    """)


def build_user_prompt(
    *,
    rolling_summary_5turns: str,
    current_message: str,
    last_assistant_message: str = "",
) -> str:
    summary_block = (
        rolling_summary_5turns.strip()
        if rolling_summary_5turns and rolling_summary_5turns.strip()
        else "(no prior context — this is the start of the conversation)"
    )
    last_assistant_block = (
        last_assistant_message.strip()
        if last_assistant_message and last_assistant_message.strip()
        else "(none — chatbot has not spoken yet this session)"
    )
    return textwrap.dedent(f"""\
        ROLLING_SUMMARY_LAST_5_TURNS:
        {summary_block}

        LAST_ASSISTANT_MESSAGE:
        {last_assistant_block}

        CURRENT_USER_MESSAGE:
        {current_message.strip()}

        Return the JSON object now.
    """)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def validate_agent1(out: dict) -> None:
    """Cross-field constraint: mi_for_user_intent must be in the shortlist
    for the chosen user_intent.
    """
    intent = out.get("user_intent")
    mi = out.get("mi_for_user_intent")
    allowed = {c["code"] for c in shortlist_for_user_intent(intent)}
    if mi not in allowed:
        raise ValueError(
            f"mi_for_user_intent {mi!r} not in shortlist for "
            f"user_intent {intent!r}: allowed = {sorted(allowed)}"
        )
    phrase = (out.get("user_intent_phrase") or "").strip()
    if not phrase:
        raise ValueError("user_intent_phrase must be non-empty")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@dataclass
class Agent1Inputs:
    rolling_summary_5turns: str
    current_message: str
    # The chatbot's last reply this session, fed to the mid-thread rule
    # in the system prompt so short user replies ("yeah", "what now")
    # don't get misclassified as small_talk. Empty = cold start.
    last_assistant_message: str = ""


def _safe_fallback() -> dict:
    """small_talk + support is the safest cold-start guess."""
    return {
        "user_intent": "small_talk",
        "user_intent_phrase": "User opened the conversation; warm acknowledgment.",
        "mi_for_user_intent": "support",
        "_fallback_default": True,
    }


def run_agent1(
    *, client: LLMClient, ctx: CallContext, inputs: Agent1Inputs,
) -> dict:
    """Execute Agent 1. On total failure returns a safe fallback."""
    assert ctx.call_role == "agent1_user_intent"
    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(**inputs.__dict__),
            schema=AGENT1_SCHEMA,
            validator_extras=validate_agent1,
        )
    except LLMStructuredError:
        return _safe_fallback()


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _self_test() -> None:
    # Schema-valid record passes.
    valid = {
        "user_intent": "express_emotion",
        "user_intent_phrase": "User is venting about deadline pressure and wants to feel heard.",
        "mi_for_user_intent": "complex_reflection",
    }
    validate_agent1(valid)

    # mi outside shortlist → reject.
    bad = dict(valid, mi_for_user_intent="advise_with_permission")
    try:
        validate_agent1(bad)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "not in shortlist" in str(e)

    # request_plan + advise_with_permission → ok (it IS in that shortlist)
    plan_ok = {
        "user_intent": "request_plan",
        "user_intent_phrase": "User wants a concrete next step.",
        "mi_for_user_intent": "advise_with_permission",
    }
    validate_agent1(plan_ok)

    # request_plan + evoke → reject (evoke is NOT in request_plan's shortlist)
    plan_bad = dict(plan_ok, mi_for_user_intent="evoke")
    try:
        validate_agent1(plan_bad)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # Empty phrase rejected
    empty_phrase = dict(valid, user_intent_phrase="   ")
    try:
        validate_agent1(empty_phrase)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # Prompts render without errors
    sys_p = build_system_prompt()
    assert "IntentAgent" in sys_p
    assert "express_emotion" in sys_p
    assert "complex_reflection" in sys_p

    user_p = build_user_prompt(
        rolling_summary_5turns="user vented about deadline pressure",
        current_message="I can't focus today.",
        last_assistant_message="That sounds really hard.",
    )
    assert "deadline pressure" in user_p
    assert "I can't focus" in user_p
    assert "That sounds really hard" in user_p

    # Cold-start prompt renders gracefully
    cold_p = build_user_prompt(
        rolling_summary_5turns="", current_message="hi",
    )
    assert "no prior context" in cold_p
    assert "chatbot has not spoken yet" in cold_p

    # Mid-thread rule is in the system prompt
    sys_p2 = build_system_prompt()
    assert "MID-THREAD RULE" in sys_p2
    assert "yeah" in sys_p2 and "small_talk" in sys_p2

    # Fallback is schema-valid
    fb = _safe_fallback()
    validate_agent1({k: v for k, v in fb.items() if not k.startswith("_")})

    print("agent1_user_intent self-test PASSED")


if __name__ == "__main__":
    _self_test()
