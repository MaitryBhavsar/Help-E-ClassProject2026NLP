"""§18.2 + §18.3 v6 — Combined attribute-level + TTM-stage recompute.

Runs ONCE per turn after inference writes evidence into the graph. Emits
two arrays in a single call:

  Part A — attribute_level_updates:
    For every (problem, level_attribute) pair that received new evidence
    this turn AND the problem is a current problem, re-judge the level
    ∈ {low, medium, high, unknown} using up to past K=5 evidence entries
    for that pair.

  Part B — ttm_stage_updates:
    For every current problem, re-judge its TTM stage using up to past
    K=5 evidence entries across ALL behavioral attributes (level and
    non-level combined).

Input context carries the prior state so the LLM can compute old→new
transitions and justify regression when needed.

Cold-start safe: if `turn_scope` is empty or the graph has no matching
evidence, the prompt instructs the model to return empty arrays and the
pipeline skips the call.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Any, Optional

from ..config import (
    CONFIDENCE_LEVELS,
    LEVEL_ATTR_TYPES,
    LEVELS_V6,
    NON_LEVEL_ATTR_TYPES,
    PROBLEM_VOCAB,
    TTM_STAGES_V6,
)
from ..llm_client import CallContext, LLMClient, LLMStructuredError
from .common import PROJECT_IDENTITY, ttm_stage_rubric_block
from .common_v6 import (
    format_chronology_entries,
    level_attribute_block,
    levels_block,
    non_level_attribute_block,
)


# ---------------------------------------------------------------------------
# JSON schema
# ---------------------------------------------------------------------------


RECOMPUTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["attribute_level_updates", "ttm_stage_updates"],
    "properties": {
        "attribute_level_updates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "problem_name", "attribute_name",
                    "new_level", "reasoning",
                ],
                "properties": {
                    "problem_name": {"type": "string", "enum": list(PROBLEM_VOCAB)},
                    "attribute_name": {"type": "string", "enum": list(LEVEL_ATTR_TYPES)},
                    "new_level": {"type": "string", "enum": list(LEVELS_V6)},
                    "reasoning": {"type": "string", "minLength": 1},
                },
            },
        },
        "ttm_stage_updates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "problem_name",
                    "new_ttm_stage", "reasoning",
                ],
                "properties": {
                    "problem_name": {"type": "string", "enum": list(PROBLEM_VOCAB)},
                    "new_ttm_stage": {"type": "string", "enum": list(TTM_STAGES_V6)},
                    "reasoning": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def build_system_prompt() -> str:
    return textwrap.dedent(f"""\
        {PROJECT_IDENTITY}

        You are the RECOMPUTE agent (§18.2 + §18.3 v6). After inference
        writes new evidence this turn, you re-judge:

          Part A: the CURRENT LEVEL of every level-attribute that received
                  new evidence this turn, for current problems only.

          Part B: the TTM STAGE of every current problem, based primarily
                  on the CURRENT LEVELS of its level attributes (after
                  Part A's updates), with the latest non-level entry per
                  attribute as supplemental signal.

        You do NOT infer new attributes, create problems, or write
        attribute-connections — that is inference's job. You only update
        levels and stages.

        IMPORTANT scope: the entries you see come from the CURRENT
        session only. Past sessions' attribute values are not surfaced
        for re-judgement. Recency K = 5 entries cap per (problem,
        attribute) pair.

        # VOCABULARIES

        {level_attribute_block()}

        {non_level_attribute_block()}

        {levels_block()}

        {ttm_stage_rubric_block()}

        # PART A RULES (attribute_level_updates)

        - Only emit an update when BOTH:
            (a) the attribute is a LEVEL attribute, AND
            (b) the problem is a current problem, AND
            (c) that (problem, attribute) has at least one entry FROM
                THIS TURN in the scope you are given.
        - Weight the most recent entries heaviest. Older entries fill in
          background.
        - Output `new_level` ∈ {{low, medium, high, unknown}}. Use
          `unknown` only if evidence is genuinely insufficient even after
          this turn's new entry.
        - If the level has not changed, still emit an update with
          new_level equal to the existing level and a one-line reasoning
          describing why it stayed the same.

        # PART B RULES (ttm_stage_updates)

        - Emit one update per CURRENT problem (regardless of whether
          attributes changed this turn).
        - Stage decision is PRIMARILY a function of the CURRENT LEVELS
          of the problem's level attributes (after Part A's updates).
          Use the level pattern to place the user on the 4-stage ladder:
            * precontemplation → contemplation: severity rising,
              awareness building, motivation low-medium
            * contemplation → preparation: motivation rising,
              self-efficacy moving up, perceived_benefits acknowledged
            * preparation → action: cues_to_action high, concrete
              recent steps reported (via past_attempts non-level)
            * action sustained: continued effort, no major setback signals
        - Use the LATEST entry per non-level attribute (goal, triggers,
          coping_strategies, past_attempts) as supplemental signal for
          stage placement — especially past_attempts (action evidence)
          and goal (preparation evidence).
        - Do NOT review every K=5 entry's raw text in Part B; the levels
          themselves carry the summary signal Part A computed.
        - Regression (e.g. action → preparation, or preparation →
          contemplation) is allowed when the evidence contains setback
          signals: negative past_attempts outcomes, new triggers
          resurfacing, motivation dropping, or the user explicitly
          stepping back.
        - If the stage has not changed, emit an update with new_ttm_stage
          equal to the prior stage and reasoning describing why it
          stayed the same.
        - For a brand-new problem (just appeared this turn): the prior
          stage is the graph's default `precontemplation`, but if the
          new evidence supports a more advanced stage immediately
          (e.g. user already shows preparation-level signals), set
          new_ttm_stage accordingly without hesitation. Do NOT assume
          new problems must start in precontemplation.
        - `reasoning` MUST cite which level pattern (and non-level
          signals) drove the stage decision.

        # REQUIRED JSON SHAPE

        {{
          "attribute_level_updates": [
            {{
              "problem_name": "academic_pressure",
              "attribute_name": "perceived_severity",
              "new_level": "high",
              "reasoning": "User now describes the workload as 'crushing' and unsustainable; earlier entries showed moderate stress."
            }}
          ],
          "ttm_stage_updates": [
            {{
              "problem_name": "academic_pressure",
              "new_ttm_stage": "contemplation",
              "reasoning": "User acknowledged the problem and is weighing options to reduce load."
            }}
          ]
        }}

        Return ONLY valid JSON matching the schema. Empty arrays are
        fine when nothing qualifies.
    """)


def build_user_prompt(
    *,
    turn_scope: list[dict],
    current_problem_state: list[dict],
    past_k_level_attribute_entries: dict,
    past_k_all_attribute_entries: dict,
    session_id: int,
    turn_id: int,
) -> str:
    """Assemble the per-turn context.

    Arg shapes:

      turn_scope:
        [{problem_name, attribute_name}, ...] — (problem, level_attr) pairs
        that received a new entry THIS turn. Part A updates must cover
        exactly this list.

      current_problem_state:
        [{problem_name, current_ttm_stage, goal,
          level_attributes: {name: current_level}}, ...]
        — the pre-recompute state. Used to supply old_level / old_ttm_stage
        and to filter which problems are "current".

      past_k_level_attribute_entries:
        {(problem_name, attribute_name): [entry, ...]} — up to K=5 entries,
        oldest first, per pair appearing in turn_scope.

      past_k_all_attribute_entries:
        {problem_name: [entry, ...]} — up to K=5 entries across ALL
        behavioral attributes for each current problem, oldest first.
        Each entry has attr_name, session_id, turn_id,
        inferred_information, confidence.
    """
    scope_lines = (
        "\n".join(
            f"  - ({s['problem_name']}, {s['attribute_name']})"
            for s in turn_scope
        )
        if turn_scope else "  (none — Part A should return an empty array)"
    )

    state_lines = []
    for p in current_problem_state:
        parts = [
            f"  - {p['problem_name']} (stage={p.get('current_ttm_stage', 'precontemplation')}"
        ]
        goal = p.get("goal")
        if goal:
            parts.append(f", goal={goal!r}")
        parts.append(")")
        state_lines.append("".join(parts))
        for attr_name, lvl in (p.get("level_attributes") or {}).items():
            state_lines.append(f"      * {attr_name} = {lvl}")
    state_block = "\n".join(state_lines) if state_lines else "  (no current problems)"

    level_entries_lines = []
    if past_k_level_attribute_entries:
        for (pname, aname), entries in past_k_level_attribute_entries.items():
            level_entries_lines.append(f"  ({pname}, {aname}):")
            for e in entries:
                level_entries_lines.append(
                    f"    [s{e['session_id']} t{e['turn_id']}, conf={e.get('confidence', '?')}] "
                    f"{e.get('inferred_information', '')}"
                )
    else:
        level_entries_lines.append("  (none)")
    level_entries_block = "\n".join(level_entries_lines)

    all_entries_lines = []
    if past_k_all_attribute_entries:
        for pname, entries in past_k_all_attribute_entries.items():
            all_entries_lines.append(f"  {pname}:")
            for e in entries:
                all_entries_lines.append(
                    f"    [s{e['session_id']} t{e['turn_id']}, conf={e.get('confidence', '?')}] "
                    f"{e.get('attr_name', '?')}: {e.get('inferred_information', '')}"
                )
    else:
        all_entries_lines.append("  (none)")
    all_entries_block = "\n".join(all_entries_lines)

    return textwrap.dedent(f"""\
        CURRENT (session={session_id}, turn={turn_id}).

        PART A SCOPE — (problem, level_attribute) pairs with new evidence this turn:
        {scope_lines}

        CURRENT PROBLEM STATE (before recompute):
        {state_block}

        PART A CONTEXT — past K=5 entries per scope pair (oldest → newest):
        {level_entries_block}

        PART B CONTEXT — past K=5 entries across all behavioral attributes per current problem (oldest → newest):
        {all_entries_block}

        Produce attribute_level_updates (Part A) and ttm_stage_updates (Part B).
    """)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def _validate_factory(
    turn_scope: list[dict],
    current_problems: set[str],
):
    scope_set = {(s["problem_name"], s["attribute_name"]) for s in turn_scope}

    def _check(out: dict) -> None:
        # Part A must cover exactly scope_set — no extras, no missing.
        part_a_set: set[tuple[str, str]] = set()
        for u in out["attribute_level_updates"]:
            key = (u["problem_name"], u["attribute_name"])
            if key in part_a_set:
                raise ValueError(
                    f"duplicate attribute_level_update for {key}"
                )
            part_a_set.add(key)
            if key not in scope_set:
                raise ValueError(
                    f"attribute_level_update for {key} not in turn_scope"
                )
            if u["attribute_name"] not in LEVEL_ATTR_TYPES:
                raise ValueError(
                    f"attribute_level_update.attribute_name {u['attribute_name']!r} "
                    "is not a level attribute"
                )
        if part_a_set != scope_set:
            missing = scope_set - part_a_set
            raise ValueError(
                f"attribute_level_updates missing entries for {missing}"
            )

        # Part B must cover exactly current_problems — one update per.
        part_b_set: set[str] = set()
        for u in out["ttm_stage_updates"]:
            if u["problem_name"] in part_b_set:
                raise ValueError(
                    f"duplicate ttm_stage_update for {u['problem_name']!r}"
                )
            part_b_set.add(u["problem_name"])
            if u["problem_name"] not in current_problems:
                raise ValueError(
                    f"ttm_stage_update for non-current problem "
                    f"{u['problem_name']!r}"
                )
        if part_b_set != current_problems:
            missing = current_problems - part_b_set
            raise ValueError(
                f"ttm_stage_updates missing entries for {missing}"
            )

    return _check


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@dataclass
class RecomputeInputs:
    session_id: int
    turn_id: int
    turn_scope: list[dict]               # [{problem_name, attribute_name}, ...]
    current_problem_state: list[dict]    # see build_user_prompt docstring
    past_k_level_attribute_entries: dict # {(problem, attr): [entries]}
    past_k_all_attribute_entries: dict   # {problem: [entries]}


def _empty_out() -> dict:
    return {
        "attribute_level_updates": [],
        "ttm_stage_updates": [],
        "_skipped": True,
    }


def run_recompute(
    *,
    client: LLMClient,
    ctx: CallContext,
    inputs: RecomputeInputs,
) -> dict:
    """Execute §18.2+18.3 v6. Returns parsed dict.

    Skips the LLM call entirely when there is nothing to do (no current
    problems AND empty scope). On validation failure returns an empty
    record so the pipeline can continue without applying any updates.
    """
    assert ctx.call_role == "recompute"

    current_problems = {p["problem_name"] for p in inputs.current_problem_state}
    if not current_problems and not inputs.turn_scope:
        return _empty_out()

    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(
                turn_scope=inputs.turn_scope,
                current_problem_state=inputs.current_problem_state,
                past_k_level_attribute_entries=inputs.past_k_level_attribute_entries,
                past_k_all_attribute_entries=inputs.past_k_all_attribute_entries,
                session_id=inputs.session_id,
                turn_id=inputs.turn_id,
            ),
            schema=RECOMPUTE_SCHEMA,
            validator_extras=_validate_factory(
                inputs.turn_scope, current_problems
            ),
        )
    except LLMStructuredError:
        return {
            "attribute_level_updates": [],
            "ttm_stage_updates": [],
            "_fallback_default": True,
        }


# ---------------------------------------------------------------------------
# Self-test (validator only)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    scope = [
        {"problem_name": "academic_pressure", "attribute_name": "perceived_severity"},
        {"problem_name": "academic_pressure", "attribute_name": "self_efficacy"},
    ]
    current = {"academic_pressure", "sleep_problems"}

    valid = {
        "attribute_level_updates": [
            {"problem_name": "academic_pressure",
             "attribute_name": "perceived_severity",
             "new_level": "high",
             "reasoning": "escalated language"},
            {"problem_name": "academic_pressure",
             "attribute_name": "self_efficacy",
             "new_level": "low",
             "reasoning": "stated inability"},
        ],
        "ttm_stage_updates": [
            {"problem_name": "academic_pressure",
             "new_ttm_stage": "contemplation",
             "reasoning": "user weighing options"},
            {"problem_name": "sleep_problems",
             "new_ttm_stage": "precontemplation",
             "reasoning": "no movement yet"},
        ],
    }
    _validate_factory(scope, current)(valid)

    # Missing a scope pair — rejected.
    short = dict(valid)
    short["attribute_level_updates"] = valid["attribute_level_updates"][:1]
    try:
        _validate_factory(scope, current)(short)
    except ValueError:
        pass
    else:
        raise AssertionError("expected rejection: Part A missing scope pair")

    # Extra (non-scope) pair — rejected.
    extra = dict(valid)
    extra["attribute_level_updates"] = valid["attribute_level_updates"] + [{
        "problem_name": "sleep_problems",
        "attribute_name": "motivation",
        "new_level": "low",
        "reasoning": "x",
    }]
    try:
        _validate_factory(scope, current)(extra)
    except ValueError:
        pass
    else:
        raise AssertionError("expected rejection: Part A extra pair")

    # Missing a current problem in Part B — rejected.
    miss_b = dict(valid)
    miss_b["ttm_stage_updates"] = valid["ttm_stage_updates"][:1]
    try:
        _validate_factory(scope, current)(miss_b)
    except ValueError:
        pass
    else:
        raise AssertionError("expected rejection: Part B missing current problem")

    # Non-current problem in Part B — rejected.
    bad_b = dict(valid)
    bad_b["ttm_stage_updates"] = valid["ttm_stage_updates"] + [{
        "problem_name": "work_stress",
        "new_ttm_stage": "contemplation",
        "reasoning": "x",
    }]
    try:
        _validate_factory(scope, current)(bad_b)
    except ValueError:
        pass
    else:
        raise AssertionError("expected rejection: Part B non-current problem")

    # Empty scope + no current problems — empty output passes.
    _validate_factory([], set())({
        "attribute_level_updates": [],
        "ttm_stage_updates": [],
    })

    # Prompt assembly does not crash even with minimal input.
    sp = build_system_prompt()
    assert "RECOMPUTE agent" in sp
    up = build_user_prompt(
        turn_scope=[], current_problem_state=[],
        past_k_level_attribute_entries={}, past_k_all_attribute_entries={},
        session_id=1, turn_id=1,
    )
    assert "PART A SCOPE" in up

    print("recompute self-test PASSED")


if __name__ == "__main__":
    _self_test()
