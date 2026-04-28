"""§13 v6 (REDESIGN) — Mind-1 user simulator, per-turn.

ONE LLM call per user turn. Speaks as the simulated user; emits
`simulated_user_message` (what the chatbot sees) plus a hidden reasoning
sidecar (what we keep for analysis, never fed back to the chatbot).

Per §9.5/§9.6 — replaces the prior v6 prompts:
  - SYSTEM prompt is the canonical block in §9.5
  - USER prompt is the canonical block in §9.6
  - `user_intent` enum is the new 8-value taxonomy (USER_INTENTS_V6)
  - No references to seed_situation_paragraph / primary_problem
    (those fields are gone from SimulatorProfile)
  - No prior-session summaries (sessions are independent for the
    simulator — §9.2)
"""
from __future__ import annotations

import textwrap
from typing import Any, Optional

from ..config import PROBLEM_VOCAB
from ..llm_client import CallContext, LLMClient
from ..prompts.common import format_dialog_turns, problem_vocab_block
from ..prompts.common_v6 import problem_name_mapping_block
from .session_context import (
    SimulatorProfile,
    format_session_context,
)


# Max past turns the simulator reads (J in the spec).
MAX_PAST_J_TURNS: int = 20


# `hidden_reasoning_summary` was removed; the only retained bookkeeping
# field is `problems_referred` — the simulator's claim of which problems
# (subset of the 20-vocab) the user message touched. Useful for
# simulator-vs-chatbot agreement analysis (does Mind-1's claim match
# what the chatbot's inference call extracted?). Other dropped fields:
# active_session_context_used, mood, resistance_level, user_intent,
# why_this_message_is_consistent_with_profile.
MIND1_V6_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["simulated_user_message", "problems_referred"],
    "properties": {
        "simulated_user_message": {"type": "string", "minLength": 1},
        "problems_referred": {
            "type": "array",
            "items": {"type": "string", "enum": list(PROBLEM_VOCAB)},
            "uniqueItems": True,
        },
    },
}


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _format_list(items: list[str]) -> str:
    return ", ".join(items) if items else "(none)"


def _format_demographics(d: dict[str, str]) -> str:
    if not d:
        return "(unspecified)"
    parts = [f"{k}={v}" for k, v in d.items() if v]
    return ", ".join(parts) if parts else "(unspecified)"


def _format_profile(p: SimulatorProfile) -> str:
    history = p.relevant_history.strip() if p.relevant_history else "(none)"
    return textwrap.dedent(f"""\
        PROFILE — who you are:
          demographics: {_format_demographics(p.demographics)}
          personality_traits: {_format_list(p.personality_traits)}
          communication_style: {_format_list(p.communication_style)}
          core_beliefs: {_format_list(p.core_beliefs)}
          hobbies_interests: {_format_list(p.hobbies_interests)}
          relevant_history: {history}
    """).rstrip()


def build_system_prompt() -> str:
    return textwrap.dedent(f"""\
        You are a human user talking to an emotional-support chatbot about
        everyday, non-crisis wellbeing problems. You are NOT the chatbot.
        You are NOT an assistant. You are the person who wants help (or is
        ambivalent about it).

        # HARD RULES
        - Stay in your persona, defined by your PROFILE below.
        - Speak naturally in your own voice. Match your COMMUNICATION STYLE
          (length, emotion-density, structure, vocabulary, mood pattern,
          openness — all captured in the list of descriptors you're given).
        - The only problems you reference are from the 20-problem vocabulary,
          and specifically those listed in this session's
          `currently_active_problems` from your SESSION CONTEXT.
        - Pace yourself. Not every message has to advance the problem.
          Some turns are small, ambivalent, or tangential.
        - Real change is hard. Don't suddenly "see the light".
        - Cooperation vs. resistance follows your `resistance_cooperation_level`
          in this session's SESSION CONTEXT.
        - Respond to what the chatbot just said — directly, indirectly, or
          partially. You may also keep your own thread.
        - Never use therapy jargon ("TTM", "motivational interviewing", "MI",
          "stage of change", "Health Belief Model").
        - Never reveal you are an AI or a simulator.

        # YOUR INPUTS EACH TURN
        1. PROFILE — who you are as a person (stable across sessions).
        2. SESSION CONTEXT — what's happening in your life THIS session
           (current life events, mental state, mood, emotions, resistance,
           active problems from the 20-vocab, why these problems matter
           today). Use this to ground every message.
        3. PAST TURNS this session (up to 20, oldest → newest).
        4. LAST CHATBOT MESSAGE (if any).
        5. The 20-problem vocabulary, for reference.

        # WHAT YOU PRODUCE

        A JSON object with TWO top-level fields:

        1. `simulated_user_message` — the actual message the chatbot sees.
           1–4 sentences typical. Some turns are a single short sentence;
           occasional longer turns are fine when venting or processing.

        2. `problems_referred` — the subset of the 20-problem vocabulary
           that THIS message touches on. May be empty (e.g. a small_talk
           turn). MUST be valid problem names from the vocabulary above.

        # REQUIRED JSON SHAPE

        {{
          "simulated_user_message": "I don't even know where to start. This whole week has been a blur — I keep telling myself I'll catch up on sleep after finals but honestly at this point I'm not sure there's a version of me that comes out the other side.",
          "problems_referred": ["academic_pressure", "sleep_problems"]
        }}

        # HARD RULES — VIOLATIONS WASTE A FULL LLM ROUND-TRIP

        1. OUTPUT JSON ONLY. Begin your response IMMEDIATELY with `{{`.
           No prose before the JSON. No markdown code fences. No
           commentary after.

        2. The JSON has EXACTLY TWO fields: `simulated_user_message` and
           `problems_referred`. No other top-level keys.

        3. `simulated_user_message` is 1–4 sentences. Stay in
           character — no therapy jargon, no breaking the fourth wall.

        4. `problems_referred` MUST be a subset of the 20-problem
           vocabulary AND should be drawn from this session's
           `currently_active_problems`.
           {problem_name_mapping_block()}

        Return ONLY valid JSON matching the schema. Begin with `{{`.
    """)


def build_user_prompt(
    *,
    profile: SimulatorProfile,
    session_id: int,
    turn_id: int,
    session_context: dict,
    past_turns: list[dict],
    last_system_message: Optional[str],
) -> str:
    ctx_str = format_session_context(session_context)
    last_sys = last_system_message.strip() if last_system_message else "(no prior system message this session)"
    capped = past_turns[-MAX_PAST_J_TURNS:] if past_turns else []
    return textwrap.dedent(f"""\
        {_format_profile(profile)}

        SESSION CONTEXT (THIS session's hidden framing):
        {ctx_str}

        20-PROBLEM VOCABULARY:
        {problem_vocab_block()}

        PAST TURNS THIS SESSION (oldest → newest):
        {format_dialog_turns(capped)}

        LAST CHATBOT MESSAGE: {last_sys}

        SESSION: #{session_id}, TURN about to produce: t{turn_id}

        Produce your next user message now as JSON with one top-level
        key: `simulated_user_message`. Stay in persona. Reply to the
        last chatbot message if the moment calls for it; otherwise
        keep your own thread.
    """)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def _validate_mind1_v6(out: dict) -> None:
    msg = out["simulated_user_message"].strip()
    if not msg:
        raise ValueError("simulated_user_message must be non-empty")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _fallback(profile: SimulatorProfile, session_id: int) -> dict:
    """Soft placeholder so the turn can proceed when the LLM call fails."""
    return {
        "simulated_user_message": "I don't know. I guess I'm just tired.",
        "problems_referred": [],
        "_fallback_default": True,
    }


def run_mind1_v6(
    *,
    client: LLMClient,
    ctx: CallContext,
    profile: SimulatorProfile,
    session_context: dict,
    past_turns: list[dict],
    last_system_message: Optional[str],
) -> dict:
    """Execute §13 v6 Mind-1. Returns dict matching MIND1_V6_SCHEMA plus
    optional `_fallback_default`. The previous bookkeeping payload
    (`hidden_reasoning_summary`) was removed to cut per-call tokens.
    """
    assert ctx.call_role == "mind1_v6"
    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(
                profile=profile,
                session_id=ctx.session_id,
                turn_id=ctx.turn_id,
                session_context=session_context,
                past_turns=past_turns,
                last_system_message=last_system_message,
            ),
            schema=MIND1_V6_SCHEMA,
            validator_extras=_validate_mind1_v6,
        )
    except Exception:
        return _fallback(profile, ctx.session_id)


# ---------------------------------------------------------------------------
# Self-test (no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    p = SimulatorProfile(
        profile_id="P01",
        demographics={
            "age_range": "20-25", "gender": "non-binary",
            "occupation": "grad student",
        },
        personality_traits=["analytical", "self-critical"],
        communication_style=["terse", "guarded", "intellectualizing"],
        core_beliefs=["I have to earn rest"],
        hobbies_interests=["nonfiction (likes deep dives)"],
        relevant_history="first-gen college; prior burnout 2 years ago",
    )
    ctx_sum = {
        "current_life_events": "Finals week; three nights of almost no sleep.",
        "mental_state": "Running on fumes.",
        "mood": "exhausted",
        "emotions": ["anxious", "defeated"],
        "resistance_cooperation_level": "medium",
        "currently_active_problems": ["academic_pressure", "sleep_problems"],
        "why_bringing_these_up_now": "Finals compounded by a job shift.",
    }

    # Cold-start turn 1.
    sp = build_system_prompt()
    up = build_user_prompt(
        profile=p, session_id=1, turn_id=1,
        session_context=ctx_sum, past_turns=[],
        last_system_message=None,
    )
    assert "emotional-support chatbot" in sp.lower()
    assert "SESSION CONTEXT" in up
    assert "PROFILE — who you are" in up
    assert "no prior turns" in up
    assert "no prior system message" in up
    assert "TURN about to produce: t1" in up

    # Mid-session turn: J=20 cap.
    many_turns = [
        {"role": "user", "turn_id": i, "text": f"u msg {i}"}
        for i in range(1, 40)
    ]
    up2 = build_user_prompt(
        profile=p, session_id=1, turn_id=40,
        session_context=ctx_sum, past_turns=many_turns,
        last_system_message="Take a breath — what would help right now?",
    )
    assert "u msg 20" in up2
    assert "u msg 1]" not in up2  # the first 19 should be dropped
    assert "Take a breath" in up2

    # Valid record passes validator (two-field schema).
    valid = {
        "simulated_user_message": "I don't know. It's been a rough week.",
        "problems_referred": ["academic_pressure"],
    }
    _validate_mind1_v6(valid)

    # Empty message — rejected.
    bad = dict(valid)
    bad["simulated_user_message"] = "   "
    try:
        _validate_mind1_v6(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("expected rejection: empty message")

    # Fallback is a valid (single-field) record shape.
    fb = _fallback(p, session_id=1)
    _validate_mind1_v6(fb)
    assert fb["_fallback_default"] is True

    print("mind1_v6 (redesign) self-test PASSED")


if __name__ == "__main__":
    _self_test()
