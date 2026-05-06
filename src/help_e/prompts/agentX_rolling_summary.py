"""v7 Agent X — rolling 5-turn dialogue summary updater.

Runs at end of each turn (fire-and-forget, async). Reads:
  - the previous rolling_summary_5turns
  - the just-completed user+bot exchange (one or both messages)

Emits a refreshed rolling summary (≤150 words). Used by next turn's
Agent 1 (so Agent 1 doesn't need raw past turns) and Agent 5 (which
includes it inside the evidence pack).

Cold start: previous_summary is "". Agent X writes the first summary
based on this turn alone.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Any

from ..llm_client import CallContext, LLMClient, LLMStructuredError


# ---------------------------------------------------------------------------
# JSON schema
# ---------------------------------------------------------------------------

AGENTX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rolling_summary_5turns"],
    "properties": {
        "rolling_summary_5turns": {
            "type": "string",
            "minLength": 1,
            "maxLength": 1200,  # ~150-200 words at ~6 chars/word
        },
    },
}


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def build_system_prompt() -> str:
    return textwrap.dedent("""\
        You are the RollingSummaryAgent of the v7/v8 HELP-E pipeline.
        Your one job is to maintain a rolling 5-turn summary of the
        conversation between the user and the chatbot.

        # WHAT THE SUMMARY IS FOR

        The summary is read by other agents next turn. It needs to give
        them, in plain English, what's been going on for the user across
        the most recent few turns: their themes, their state, any
        movement, anything they explicitly named that matters.

        # RULES

        - Keep it short. Aim for 80-150 words.
        - Plain narrative, no labels, no diagnosis.
        - Cover the LAST UP-TO-FIVE turns only. As new turns arrive,
          older content fades naturally — drop the oldest detail when
          space gets tight.
        - DO NOT echo the user's exact words back. Paraphrase.
        - DO NOT add interpretations the user didn't make themselves.
        - If this is the first turn (no previous summary), write the
          summary from scratch based on this turn alone.

        # OUTPUT

        Return ONE JSON object:

        { "rolling_summary_5turns": "..." }

        Return ONLY JSON.
    """)


def build_user_prompt(
    *,
    previous_summary: str,
    new_user_message: str,
    new_bot_message: str,
    current_session: int,
    current_turn: int,
) -> str:
    prev = previous_summary.strip() or "(none — first turn of the conversation)"
    return textwrap.dedent(f"""\
        PREVIOUS_ROLLING_SUMMARY:
        {prev}

        NEW_TURN (session {current_session}, turn {current_turn}):
          USER: {new_user_message.strip()}
          BOT:  {new_bot_message.strip()}

        Refresh the rolling summary now. Return the JSON object.
    """)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def validate_agentx(out: dict) -> None:
    text = (out.get("rolling_summary_5turns") or "").strip()
    if not text:
        raise ValueError("rolling_summary_5turns must be non-empty")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@dataclass
class AgentXInputs:
    previous_summary: str
    new_user_message: str
    new_bot_message: str
    current_session: int
    current_turn: int


def _safe_fallback(previous_summary: str) -> dict:
    """If the call fails, keep the previous summary unchanged."""
    return {
        "rolling_summary_5turns": (
            previous_summary.strip()
            or "(no rolling summary yet — Agent X failed at cold-start)"
        ),
        "_fallback_default": True,
    }


def run_agentx(
    *, client: LLMClient, ctx: CallContext, inputs: AgentXInputs,
) -> dict:
    assert ctx.call_role == "agentX_rolling_summary"
    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(**inputs.__dict__),
            schema=AGENTX_SCHEMA,
            validator_extras=validate_agentx,
        )
    except LLMStructuredError:
        return _safe_fallback(inputs.previous_summary)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _self_test() -> None:
    valid = {
        "rolling_summary_5turns": "User opened by venting about deadline pressure.",
    }
    validate_agentx(valid)

    bad = {"rolling_summary_5turns": "  "}
    try:
        validate_agentx(bad)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    sp = build_system_prompt()
    assert "RollingSummaryAgent" in sp

    up = build_user_prompt(
        previous_summary="user vented about work",
        new_user_message="I'm so tired",
        new_bot_message="That fatigue is real.",
        current_session=1, current_turn=2,
    )
    assert "I'm so tired" in up
    assert "user vented about work" in up

    cold = build_user_prompt(
        previous_summary="",
        new_user_message="hi",
        new_bot_message="hey",
        current_session=1, current_turn=1,
    )
    assert "first turn" in cold

    fb = _safe_fallback("prior summary text")
    validate_agentx({k: v for k, v in fb.items() if not k.startswith("_")})
    fb_cold = _safe_fallback("")
    validate_agentx({k: v for k, v in fb_cold.items() if not k.startswith("_")})

    print("agentX_rolling_summary self-test PASSED")


if __name__ == "__main__":
    _self_test()
