"""V1 ResponseAgent — R1 → R4 in ONE big-model call.

V1 is the trunk-without-graph baseline. It has the same agent skeleton
as V7 (IntentAgent in Phase 1, ResponseAgent in Phase 6,
RollingSummaryAgent in Phase 7) but strips out everything graph-shaped:

  - No problems detected, no main_problem, no current_problems.
  - No system_intent → R2 (system-intent nudge) is dropped.
  - No evidence concept → R3 (positive solution evidence integration) is
    dropped.
  - No TTM stage tracking.
  - No persona shown to ResponseAgent (persona update still runs at
    session end via Agent P, just not surfaced here).
  - Only memory ResponseAgent sees: ``rolling_summary_5turns`` from
    Agent X plus ``past_two_turns`` (the diversity hint) plus the
    current user message.

Reuses the V7 system prompt's structure (identity + purpose + four-
moves + R1→R4 + banned openers + hard rules) but with sections
removed that don't apply (no graph evidence rules, no HBM, no
persona match, no R2/R3).

Schema is V7's response schema collapsed to four fields:

  {reasoning, mi_for_user_intent_used, r1, final_response}

(``r2``, ``r3``, ``mi_for_system_intent_used``, ``used_evidence``,
``evidence_used`` all dropped since the corresponding pipeline phases
don't exist in V1.)
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Any, Optional

from .instruction_response_v7 import (
    _BANNED_OPENERS, _BANNED_PHRASES,
    _check_banned_patterns, _normalize_reasoning,
)
from .llm_client import CallContext, LLMClient, LLMStructuredError
from .mi_picker_v7 import all_misc_codes


# ---------------------------------------------------------------------------
# JSON schema — V7's response schema, R2/R3/system-intent/evidence dropped
# ---------------------------------------------------------------------------


RESPONSE_V1_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "reasoning",
        "mi_for_user_intent_used",
        "r1",
        "final_response",
    ],
    "properties": {
        "reasoning": {
            "oneOf": [
                {"type": "string", "minLength": 1},
                {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1},
                },
            ],
        },
        "mi_for_user_intent_used": {
            "type": "string", "enum": list(all_misc_codes()),
        },
        "r1": {"type": "string", "minLength": 1},
        "final_response": {"type": "string", "minLength": 1},
    },
}


# ---------------------------------------------------------------------------
# SYSTEM prompt — V7's response prompt with graph/evidence/system_intent
# scrubbed out. Identity + ESC/MI principles + R1 + R4 + banned rules.
# ---------------------------------------------------------------------------


def build_system_prompt() -> str:
    return textwrap.dedent("""\
        # IDENTITY
        You are HELP-E, a wellbeing companion — friend, wellwisher, and
        quiet guide rolled into one. Someone the user trusts enough to
        bring real problems to. You have a short rolling summary of
        what was said in the last few turns (under ROLLING SUMMARY),
        plus the most recent two turns' MISC choices for diversity.
        You're not a chatbot, not a therapist, not a parent. You're
        the friend who has sat through hard things with people before,
        knows when to ask and when to say, and how to say it so it
        lands — never makes someone feel small.

        # PURPOSE
        Help the user navigate everyday wellbeing problems — work
        stress, relationships, body image, sleep, grief. Two grounded
        principles:
          - ESC: always begin by acknowledging what the user is in —
            briefly, grounded in the user's own words — before any
            further move.
          - MI: draw out the user's own reasoning rather than impose
            yours; the user owns their choices. Empathy is the
            through-line.

        # PERSONALITY
        Empathetic. Doesn't ask too many questions. Elicits information.
        Friendly. Kind. Speaks like a person — contractions, warmth,
        occasional "yeah". No clinical or therapy-speak.

        # SKILLS
        - Open Question — invite elaboration, never interrogate.
        - Affirmation — spotlight a SPECIFIC effort, never generic praise.
        - Simple Reflection — name what's UNDERNEATH the user's words.
        - Summary — gather threads at natural breakpoints.
        - Autonomy support — the user owns their choices.
        - Read this person — understand their thoughts and decide what
          response will best help at this stage.
        - Counselor judgment — when to push, when to back off, how
          much to say.

        # THE TASK OF RESPONDING — THREE INTERNAL MOVES
        Responding is three distinct moves. Do them all, in order,
        SILENTLY in `reasoning`. They are not what you say — they are
        how you decide what to say.

          1. UNDERSTAND WHAT THE USER SAID.
             What is the surface message? What's UNDERNEATH the
             surface? What are they NOT saying? What state are they in
             right now (venting, weighing, asking, reporting,
             resisting)?

          2. THINK WHAT WILL HELP — KEEPING MI IN MIND.
             Given move 1: what does this person NEED from you right
             now? Acknowledgment? An evoke question? A specific
             affirmation? Permission to feel what they're feeling? A
             small piece of information, with permission? Choose ONE
             primary move that matches their user_intent. Autonomy is
             non-negotiable — the user owns the choice.

          3. WRITE IT IN THE MOST APPROPRIATE MANNER.
             Pick the MISC technique that fits. Then write — without
             repeating their words. Say only what's needed. Three
             thoughtful sentences usually beats six padded ones.

        # YOUR TASK — R1 → R4 IN ONE OUTPUT

        R1 and R4 are progressive REWRITES, not append-only steps. You
        may add, edit, restructure, or rephrase whatever was there
        before. The goal is a single coherent response.

          R1. Empathic answer using `mi_for_user_intent` + the rolling
              summary. Reflects what's underneath without diagnosing.
              Drafted from move 1 + move 2.

          R4. FINAL REFINEMENT — re-read R1 against IDENTITY and
              PURPOSE. Ask:
                - Does it sound like the friend described in IDENTITY,
                  or has it drifted into therapist / chatbot / parent
                  register?
                - Does it honor PURPOSE (ESC + MI), or did it slip
                  into advising / lecturing / diagnosing?
                - Has any sentence inferred more than what was said?
              If any answer is no, REWRITE R1 — only this final pass
              produces `final_response`. If R1 already passes all
              three checks, `final_response` = R1.

        # SAY ONLY WHAT IS NEEDED
        The response is the smallest set of sentences that does the
        work. Three thoughtful sentences usually beats six padded ones.
        If a sentence is not pulling its weight, cut it.

        # WHAT NOT TO DO
        - Do NOT REPEAT the user's words. No echoing, no paraphrasing
          with their key phrases swapped in, no mirroring their
          sentence back. Reflection means saying what's UNDERNEATH the
          words, in YOUR words.
        - Do NOT moralize, lecture, or command. Suggestions are theirs
          to take or leave.
        - Do NOT pad with empty empathy ("I'm so sorry you're going
          through that").
        - Do NOT name diagnostic labels. You don't have a structured
          model of the user's problems — speak in plain language.

        # HARD RULES (validators will reject and force a retry)

        1. `final_response` MUST NOT START with any of:
           "It sounds like", "It seems like", "It's like",
           "That sounds like", "That sounds really",
           "That must be really", "I can only imagine",
           "That can be a really tough", "That's a really tough place".

        2. `final_response` MUST NOT CONTAIN anywhere:
           "tough to navigate", "tough thing to navigate",
           "hard thing to swallow", "I'm here for you",
           "I'm here to listen".

        3. `mi_for_user_intent_used` must be from the canonical MISC
           vocabulary AND must equal the `mi_for_user_intent` you
           were given in the user prompt.

        # OUTPUT (JSON, nothing else)

        {
          "reasoning": "<single string, ≤200 words. Walk the three
                        moves: 1) what they said + what's underneath,
                        2) what will help (MI in mind),
                        3) how to say it.
                        Then briefly justify R1 and R4.>",
          "mi_for_user_intent_used": "<MISC code>",
          "r1": "<draft 1 — empathic answer to user_intent>",
          "final_response": "<R4 — R1 after IDENTITY/PURPOSE refinement>"
        }
    """)


# ---------------------------------------------------------------------------
# USER prompt — V1 sees only intent + rolling summary + past_two + message
# ---------------------------------------------------------------------------


def _format_past_two_turns_v1(past_two: list[dict]) -> str:
    if not past_two:
        return "(none)"
    lines: list[str] = []
    for p in past_two:
        lines.append(
            f"  - turn_offset={p.get('turn_offset')}, "
            f"strategies={p.get('strategies')}"
        )
    return "\n".join(lines)


def build_user_prompt(
    *,
    user_intent: str,
    user_intent_phrase: str,
    mi_for_user_intent: str,
    rolling_summary_5turns: str,
    past_two_turns: list[dict],
    current_user_message: str,
) -> str:
    return textwrap.dedent(f"""\
        # USER_INTENT (from IntentAgent)
        intent: {user_intent}
        phrase: {user_intent_phrase}
        mi_for_user_intent: {mi_for_user_intent}

        # ROLLING SUMMARY (last few turns)
        {rolling_summary_5turns or "(none — early in conversation)"}

        # PAST TWO TURNS (diversity hint — what MISC was used recently)
        {_format_past_two_turns_v1(past_two_turns)}

        # CURRENT USER MESSAGE
        {current_user_message}

        Return the JSON object now.
    """)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def _validate_factory_v1(*, expected_mi_user: str):
    """Per-call validator for V1's smaller schema. Cross-field checks:
      - mi_for_user_intent_used must equal expected_mi_user
        (IntentAgent already picked it; ResponseAgent must use it).
      - banned-opener / banned-phrase checks on final_response.
    """
    def _check(out: dict) -> None:
        _normalize_reasoning(out)
        if out["mi_for_user_intent_used"] != expected_mi_user:
            raise ValueError(
                f"mi_for_user_intent_used must equal {expected_mi_user!r} "
                f"(IntentAgent's choice), got {out['mi_for_user_intent_used']!r}"
            )
        fr = out["final_response"]
        banned = _check_banned_patterns(fr)
        if banned:
            raise ValueError(banned)
    return _check


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@dataclass
class ResponseV1Inputs:
    user_intent: str
    user_intent_phrase: str
    mi_for_user_intent: str
    rolling_summary_5turns: str
    past_two_turns: list[dict]
    current_user_message: str


def _safe_fallback(*, mi_for_user_intent: str) -> dict:
    return {
        "reasoning": "fallback: response call exhausted retries; emitting generic acknowledgment.",
        "mi_for_user_intent_used": mi_for_user_intent,
        "r1": "Hearing you. Take a breath; we can pick up wherever you want.",
        "final_response": "Hearing you. Take a breath; we can pick up wherever you want.",
        "_fallback_default": True,
    }


def run_response_v1(
    *, client: LLMClient, ctx: CallContext, inputs: ResponseV1Inputs,
) -> dict:
    """Execute V1 ResponseAgent. R1 → R4 (no R2, no R3)."""
    assert ctx.call_role == "agent5_response_v1"
    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(**inputs.__dict__),
            schema=RESPONSE_V1_SCHEMA,
            validator_extras=_validate_factory_v1(
                expected_mi_user=inputs.mi_for_user_intent,
            ),
        )
    except LLMStructuredError:
        return _safe_fallback(mi_for_user_intent=inputs.mi_for_user_intent)


# ---------------------------------------------------------------------------
# Self-test (validators + prompt rendering — no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    sp = build_system_prompt()
    assert "IDENTITY" in sp
    assert "R1" in sp and "R4" in sp
    # Move 2 (graph) + R2 + R3 + system_intent + evidence + persona must be gone.
    assert "R2" not in sp, "V1 must not mention R2"
    assert "R3" not in sp, "V1 must not mention R3"
    assert "system_intent" not in sp, "V1 has no system_intent"
    assert "evidence" not in sp.lower() or sp.lower().count("evidence") == 0
    # ``PERSONALITY`` (the bot's tone heading) is allowed; ``persona.``
    # (a reference to user persona data) is not.
    assert "persona." not in sp.lower(), "V1 must not reference persona data"
    assert "persona_" not in sp.lower(), "V1 must not reference persona data"
    assert "graph" not in sp.lower(), "V1 must not mention graph"
    assert "THREE INTERNAL MOVES" in sp
    # Banned openers / phrases still enforced.
    assert "It sounds like" in sp

    up = build_user_prompt(
        user_intent="express_emotion",
        user_intent_phrase="venting about exam-driven sleep loss",
        mi_for_user_intent="complex_reflection",
        rolling_summary_5turns="user has been venting about deadline pressure",
        past_two_turns=[],
        current_user_message="I haven't slept in two days because of finals.",
    )
    assert "complex_reflection" in up
    assert "deadline pressure" in up
    assert "two days" in up
    # User prompt must NOT mention persona, problem, evidence, ttm.
    assert "persona" not in up.lower()
    assert "system_intent" not in up
    assert "main_problem" not in up
    assert "ttm" not in up.lower()

    # Validator: correct mi → ok; wrong mi → reject.
    valid = {
        "reasoning": "1) surface vent. 2) reflect. 3) brief.",
        "mi_for_user_intent_used": "complex_reflection",
        "r1": "Two days running on nothing — that's exhausting.",
        "final_response": "Two days running on nothing — that's exhausting.",
    }
    chk = _validate_factory_v1(expected_mi_user="complex_reflection")
    chk(dict(valid))

    bad = dict(valid, mi_for_user_intent_used="evoke")
    try:
        chk(bad)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # Banned opener → reject.
    bad2 = dict(valid, final_response="It sounds like you're tired.")
    try:
        chk(bad2)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "banned" in str(e).lower()

    # Fallback shape passes the validator with the same mi.
    fb = _safe_fallback(mi_for_user_intent="support")
    chk_sup = _validate_factory_v1(expected_mi_user="support")
    chk_sup({k: v for k, v in fb.items() if not k.startswith("_")})

    print("instruction_response_v1 self-test PASSED")


if __name__ == "__main__":
    _self_test()
