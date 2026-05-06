"""v7 Agent 3b — per-problem TTM + system_intent + MI pick.

Triggered ONLY when Agent 3a actually changed at least one level for
this problem. Reads the changed attribute(s) + ALL other attributes'
levels + reasoning (NO summary_text — keeps context small).

Emits:
  - new_ttm_stage          (TTM_STAGES_V6 — regression allowed)
  - ttm_reasoning          (why this stage)
  - system_intent          (1-line directive: what bot wants to nudge)
  - mi_for_system_intent   (MISC code chosen from shortlist for new stage)
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Any, Optional

from ..config import (
    PROBLEM_VOCAB,
    TTM_STAGES_V6,
    TTM_TRANSITION_TARGET_V6,
)
from ..llm_client import CallContext, LLMClient, LLMStructuredError
from ..mi_picker_v7 import shortlist_for_ttm_stage, all_misc_codes


# ---------------------------------------------------------------------------
# JSON schema
# ---------------------------------------------------------------------------

AGENT3B_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "problem_name",
        "new_ttm_stage",
        "ttm_reasoning",
        "system_intent",
        "mi_for_system_intent",
    ],
    "properties": {
        "problem_name": {"type": "string", "enum": list(PROBLEM_VOCAB)},
        "new_ttm_stage": {"type": "string", "enum": list(TTM_STAGES_V6)},
        "ttm_reasoning": {"type": "string", "minLength": 1},
        "system_intent": {"type": "string", "minLength": 1},
        "mi_for_system_intent": {
            "type": "string", "enum": list(all_misc_codes()),
        },
    },
}


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _ttm_rules_block() -> str:
    return textwrap.dedent("""
        # TTM STAGE RULES (4-stage)

        - precontemplation: user is unaware / dismissive / not yet
          recognizing the problem matters to them.
        - contemplation: user is now aware the problem affects them, may
          be weighing whether to do anything; ambivalence common. Move
          here when severity ≥ medium OR susceptibility ≥ medium.
        - preparation: user is leaning toward action, weighing concrete
          steps. Requires motivation ≥ medium AND (perceived_benefits
          ≥ medium OR self_efficacy ≥ medium).
        - action: user has taken or is taking concrete steps. Requires
          cues_to_action = high OR a recent past_attempts entry naming
          a concrete step.

        REGRESSION ALLOWED. If new evidence shows a setback (a problem
        getting heavier, a self-efficacy drop, a barrier surfacing),
        step the stage back. Don't pretend forward motion when the
        evidence reverses.

        BRAND-NEW PROBLEM: not forced to precontemplation. If the user
        enters at "I know X is a problem and I'm trying Y", set the
        stage that fits.
    """)


def _system_intent_rules_block() -> str:
    return textwrap.dedent("""
        # SYSTEM_INTENT — ONE-LINE NUDGE DIRECTIVE

        After picking the new stage, write a single sentence describing
        what the bot wants to nudge for THIS problem on the NEXT turn.
        Concrete and specific. Tied to the stage's transition target.

        Examples (illustrative — write your own per the actual state):
          - precontemplation: "Build awareness of the deadline pressure
            without telling them what to do."
          - contemplation: "Reflect both sides of the ambivalence; do
            NOT plan yet."
          - preparation: "Help them name ONE small step that fits their
            stated strengths."
          - action: "Affirm the specific step they took; ask what felt
            different."

        Do NOT name HBM labels in the directive (no "perceived_severity",
        "self_efficacy"). Use plain language.
    """)


def _mi_rules_block() -> str:
    return textwrap.dedent("""
        # PICK mi_for_system_intent FROM THE SHORTLIST

        After picking the new stage, pick ONE MISC code from the
        shortlist that fits the stage. The shortlist for each stage:
    """) + "\n" + "\n".join(
        f"  - {stage}: {', '.join(c['code'] for c in shortlist_for_ttm_stage(stage))}"
        for stage in TTM_STAGES_V6
    )


def build_system_prompt() -> str:
    return textwrap.dedent(f"""\
        You are the StageAgent of the v7/v8 HELP-E pipeline. You handle
        ONE problem per call and only when the AttributeAgent actually
        changed at least one attribute level for this problem. Your job:

          1. Recompute TTM stage for this problem from current
             attribute levels. Regression allowed.
          2. Write a one-line system_intent describing what the bot
             wants to nudge for THIS problem next turn.
          3. Pick the MISC code from the shortlist that fits the new
             stage.

        You do NOT see attribute summary_text — only current_levels +
        level_reasoning. You do NOT talk to the user.

        {_ttm_rules_block()}

        {_system_intent_rules_block()}

        {_mi_rules_block()}

        # OUTPUT

        Return ONE JSON object:

        {{
          "problem_name": "<this problem>",
          "new_ttm_stage": "<one of precontemplation | contemplation | preparation | action>",
          "ttm_reasoning": "<short — why this stage given the levels>",
          "system_intent": "<one-line nudge directive, no HBM labels>",
          "mi_for_system_intent": "<MISC code from the shortlist for new_ttm_stage>"
        }}

        Return ONLY JSON.
    """)


def _format_levels_block(records: list[dict]) -> str:
    if not records:
        return "(no level attributes set yet)"
    out: list[str] = []
    for r in records:
        out.append(textwrap.dedent(f"""\
              {r["attribute_name"]}: level={r.get("current_level", "unknown")}
              reasoning: {r.get("level_reasoning") or "(none)"}
        """).rstrip())
    return "\n\n".join(out)


def build_user_prompt(
    *,
    problem_name: str,
    previous_ttm_stage: str,
    previous_ttm_reasoning: str,
    changed_attributes: list[dict],
    other_attributes: list[dict],
) -> str:
    """Assemble Agent 3b's user prompt.

    `changed_attributes`: items {attribute_name, current_level,
                                 level_reasoning} for attrs whose level
                                 changed in this turn's 3a.
    `other_attributes`:   items {attribute_name, current_level,
                                 level_reasoning} for attrs whose level
                                 did NOT change but exist on the problem.
    """
    transition_target = TTM_TRANSITION_TARGET_V6.get(previous_ttm_stage, "")
    return textwrap.dedent(f"""\
        PROBLEM: {problem_name}

        PREVIOUS_TTM_STAGE: {previous_ttm_stage}
        PREVIOUS_TTM_REASONING: {previous_ttm_reasoning or "(none)"}
        PREVIOUS_TRANSITION_TARGET: {transition_target}

        CHANGED_ATTRIBUTES (level changed this turn):
        {_format_levels_block(changed_attributes)}

        OTHER_ATTRIBUTES (existing, level unchanged this turn):
        {_format_levels_block(other_attributes)}

        Decide the new TTM stage from the full level picture. Then write
        system_intent and pick mi_for_system_intent.

        Return the JSON object now.
    """)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def validate_agent3b(
    out: dict, *, expected_problem: Optional[str] = None,
) -> None:
    if expected_problem and out["problem_name"] != expected_problem:
        raise ValueError(
            f"expected problem_name {expected_problem!r}, got {out['problem_name']!r}"
        )
    stage = out["new_ttm_stage"]
    mi = out["mi_for_system_intent"]
    allowed = {c["code"] for c in shortlist_for_ttm_stage(stage)}
    if mi not in allowed:
        raise ValueError(
            f"mi_for_system_intent {mi!r} not in shortlist for "
            f"new_ttm_stage {stage!r}: allowed = {sorted(allowed)}"
        )
    if not out["system_intent"].strip():
        raise ValueError("system_intent must be non-empty")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@dataclass
class Agent3bInputs:
    problem_name: str
    previous_ttm_stage: str
    previous_ttm_reasoning: str
    changed_attributes: list[dict]
    other_attributes: list[dict]


def _safe_fallback(problem_name: str, previous_ttm_stage: str) -> dict:
    return {
        "problem_name": problem_name,
        "new_ttm_stage": previous_ttm_stage or "precontemplation",
        "ttm_reasoning": "fallback: keep prior stage",
        "system_intent": "Hold space; reflect what's underneath.",
        "mi_for_system_intent": "support",
        "_fallback_default": True,
    }


def run_agent3b(
    *, client: LLMClient, ctx: CallContext, inputs: Agent3bInputs,
) -> dict:
    assert ctx.call_role == "agent3b_ttm_intent"
    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(**inputs.__dict__),
            schema=AGENT3B_SCHEMA,
            validator_extras=lambda o: validate_agent3b(
                o, expected_problem=inputs.problem_name,
            ),
        )
    except LLMStructuredError:
        return _safe_fallback(inputs.problem_name, inputs.previous_ttm_stage)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _self_test() -> None:
    valid = {
        "problem_name": "academic_pressure",
        "new_ttm_stage": "contemplation",
        "ttm_reasoning": "severity high, motivation unknown",
        "system_intent": "Reflect both sides; do NOT plan yet.",
        "mi_for_system_intent": "evoke",
    }
    validate_agent3b(valid, expected_problem="academic_pressure")

    # mi outside shortlist → reject (advise_with_permission is for preparation+).
    bad1 = dict(valid, mi_for_system_intent="advise_with_permission")
    try:
        validate_agent3b(bad1)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # preparation + advise_with_permission → ok
    prep_ok = dict(valid, new_ttm_stage="preparation",
                   mi_for_system_intent="advise_with_permission")
    validate_agent3b(prep_ok)

    # Empty system_intent → reject
    bad2 = dict(valid, system_intent="  ")
    try:
        validate_agent3b(bad2)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # Wrong problem_name → reject
    try:
        validate_agent3b(valid, expected_problem="sleep_problems")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # Prompts render
    sp = build_system_prompt()
    assert "StageAgent" in sp
    assert "REGRESSION" in sp

    up = build_user_prompt(
        problem_name="academic_pressure",
        previous_ttm_stage="precontemplation",
        previous_ttm_reasoning="(none)",
        changed_attributes=[
            {"attribute_name": "perceived_severity", "current_level": "high",
             "level_reasoning": "user said pressure overwhelming"},
        ],
        other_attributes=[
            {"attribute_name": "motivation", "current_level": "unknown",
             "level_reasoning": ""},
        ],
    )
    assert "academic_pressure" in up
    assert "perceived_severity" in up
    assert "precontemplation" in up

    # Fallback is schema-valid + passes the "any stage shortlist" rule
    fb = _safe_fallback("academic_pressure", "precontemplation")
    validate_agent3b({k: v for k, v in fb.items() if not k.startswith("_")})

    print("agent3b_ttm_intent self-test PASSED")


if __name__ == "__main__":
    _self_test()
