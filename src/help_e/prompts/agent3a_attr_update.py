"""v7 Agent 3a — per-problem attribute summary + level update.

ONE call per current problem (parallel across problems). Reads the
problem's existing per-attribute records (only for attributes touched
by NEW info this turn) plus the new attribute info from Agent 2 plus
new connection info touching this problem.

Emits, for each touched attribute:
  - updated `summary_text` — chronological NL with new turn appended.
    Prompt-instructed to skip redundant restatement (no separate
    classification step).
  - new `current_level`               (LEVELS_V6 or unchanged)
  - new `level_reasoning`             (why current_level)
  - `level_change_confidence`         ("high" | "medium" | "low")
  - `new_info_useful: 0|1`            (1 if this turn added something
                                       not already in summary, else 0)

Conservative level-update rules are encoded in the prompt:
  - ONLY change a level when evidence is unambiguous OR when ≥3
    converging instances form a pattern.
  - When in doubt, KEEP the prior level and just extend the summary.
  - Counter-evidence (a healthy explanation for behavior that could
    look like a deficit) means the level should NOT move toward the
    deficit reading.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Any, Optional

from ..config import (
    LEVEL_ATTR_TYPES,
    LEVELS_V6,
    NON_LEVEL_ATTR_TYPES,
    PROBLEM_VOCAB,
)
from ..graph_v7 import LEVEL_CONFIDENCES
from ..llm_client import CallContext, LLMClient, LLMStructuredError

_ALL_ATTR_TYPES: tuple[str, ...] = tuple(
    list(LEVEL_ATTR_TYPES) + list(NON_LEVEL_ATTR_TYPES)
)


# ---------------------------------------------------------------------------
# JSON schema
# ---------------------------------------------------------------------------

# One attribute update record. For non-level attrs, level fields are
# permitted-but-ignored (prompt asks the LLM to set them to "unknown" /
# "low" — we don't need to plumb a separate sub-schema).
_UPDATE_ITEM: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "attribute_name",
        "summary_text",
        "current_level",
        "level_reasoning",
        "level_change_confidence",
        "new_info_useful",
    ],
    "properties": {
        "attribute_name": {"type": "string", "enum": list(_ALL_ATTR_TYPES)},
        "summary_text": {"type": "string", "minLength": 1},
        "current_level": {
            "type": "string", "enum": list(LEVELS_V6),
        },
        "level_reasoning": {"type": "string"},
        "level_change_confidence": {
            "type": "string", "enum": list(LEVEL_CONFIDENCES),
        },
        "new_info_useful": {"type": "integer", "enum": [0, 1]},
    },
}

AGENT3A_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["problem_name", "attribute_updates"],
    "properties": {
        "problem_name": {"type": "string", "enum": list(PROBLEM_VOCAB)},
        "attribute_updates": {
            "type": "array",
            "items": _UPDATE_ITEM,
        },
    },
}


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _conservative_rules_block() -> str:
    return textwrap.dedent("""
        # CONSERVATIVE LEVEL-UPDATE RULES

        These rules govern when to CHANGE current_level and how to set
        level_change_confidence. The level should be a high-confidence
        claim about THIS person, not a running guess.

        1. ONLY change current_level when the evidence makes the new level
           UNAMBIGUOUS. A single instance of behavior is rarely
           unambiguous.
           - "I'm so anxious I can't function" → severity HIGH is
             unambiguous from this one turn. confidence = high.
           - "I asked a senior designer for input on the draft" → does
             NOT mean self_efficacy is low. Could be diligence or normal
             collaboration. KEEP prior current_level. Extend summary.
             confidence = low.
           - "I just couldn't do it" → ambiguous (situational freeze vs.
             stable competence belief). KEEP prior level. confidence =
             low. Extend summary noting the moment.

        2. PATTERN OVERRIDES SINGLE INSTANCE. If the existing summary_text
           shows two or more prior moments pointing at the same level
           direction AND this turn adds a third converging instance, set
           the level. confidence = high.

        3. WHEN UNCERTAIN, KEEP THE PRIOR LEVEL. Default behavior under
           ambiguity is to extend the summary_text but leave
           current_level untouched. Setting confidence = low is the
           signal that we extended the narrative without committing to a
           level shift.

        4. COUNTER-EVIDENCE MATTERS. If the user gives a reasoned, healthy
           explanation for behavior that COULD look like a deficit, treat
           that as counter-evidence: the level should NOT move toward
           the deficit reading.

        5. level_change_confidence values:
           - "high"   = unambiguous single-turn signal OR pattern of ≥3
                        converging instances.
           - "medium" = pattern of 2 instances or strong-but-debatable
                        single-turn signal.
           - "low"    = ambiguous or single weak instance — level should
                        NOT change at this confidence level (only
                        summary_text updates).

        # SUMMARY_TEXT CONSTRUCTION

        - Chronological NL paragraph. Each level change or new dimension
          gets a turn-anchored sentence (e.g., "t3: ...").
        - Append THIS turn as a new sentence. Do NOT rewrite earlier
          turns' sentences.
        - SKIP REDUNDANCY. If this turn merely restates what's already
          in the summary, append a single short note like
          "tN: restated — same point from another angle." Do NOT repeat
          the content. Set new_info_useful = 0.
        - If this turn adds a genuinely new dimension or shifts the
          level, write a short sentence describing the new dimension.
          Set new_info_useful = 1.

        # NON-LEVEL ATTRIBUTES

        For attributes in {coping_strategies, past_attempts, triggers, goal}:
        - Update summary_text the same way (chronological).
        - Set current_level = "unknown" and level_change_confidence =
          "low" (these attributes don't carry levels — those fields are
          ignored downstream). level_reasoning may be empty string.
    """)


def build_system_prompt() -> str:
    return textwrap.dedent(f"""\
        You are the AttributeAgent of the v7/v8 HELP-E pipeline. You handle ONE problem
        per call. Your job is to update the per-attribute records — the
        chronological summary, current_level, level_reasoning,
        level_change_confidence — for every attribute that received NEW
        info this turn.

        You do NOT touch attributes that didn't receive new info this
        turn. You do NOT compute TTM stage (Agent 3b does, only if any
        level actually changed). You do NOT talk to the user.

        {_conservative_rules_block()}

        # OUTPUT

        Return ONE JSON object:

        {{
          "problem_name": "<the problem you're updating>",
          "attribute_updates": [
            {{
              "attribute_name": "<one of the touched attributes>",
              "summary_text": "<full chronological NL paragraph after this turn's update>",
              "current_level": "<low | medium | high | unknown>",
              "level_reasoning": "<why current_level — short>",
              "level_change_confidence": "<high | medium | low>",
              "new_info_useful": <0 or 1>
            }},
            ...
          ]
        }}

        Emit exactly one entry per attribute that received new info this
        turn. Do not add entries for attributes that weren't touched.

        Return ONLY JSON. No prose.
    """)


def _format_existing_record(rec: dict) -> str:
    return textwrap.dedent(f"""\
          attribute_name: {rec["attribute_name"]}
          current_level: {rec.get("current_level", "unknown")}
          level_reasoning: {rec.get("level_reasoning") or "(none)"}
          summary_text:
            {rec.get("summary_text") or "(none — first time this attribute appears)"}
    """).rstrip()


def _format_new_attribute_info(items: list[dict]) -> str:
    if not items:
        return "(none — no new attribute info this turn)"
    out: list[str] = []
    for it in items:
        out.append(textwrap.dedent(f"""\
              attribute_name: {it["attribute_name"]}
              new_info: {it["inferred_information"]}
              concise: {it["concise_explanation"]}
              span: {it.get("supporting_utterance_span") or "(implied)"}
        """).rstrip())
    return "\n\n".join(out)


def _format_new_connections(items: list[dict]) -> str:
    if not items:
        return "(none)"
    out: list[str] = []
    for c in items:
        out.append(textwrap.dedent(f"""\
              {c["attribute_a"]} ↔ {c["attribute_b"]} ({c["relation_type"]})
              with: {c["other_problem"]}
              why: {c["why"]}
              span: {c.get("supporting_quote") or "(implied)"}
        """).rstrip())
    return "\n\n".join(out)


def build_user_prompt(
    *,
    problem_name: str,
    current_session: int,
    current_turn: int,
    existing_records: list[dict],
    new_attribute_info: list[dict],
    new_connections_touching_this_problem: list[dict],
) -> str:
    """Assemble Agent 3a's user prompt for ONE problem.

    `existing_records` items have shape:
        {attribute_name, current_level, level_reasoning, summary_text}

    `new_attribute_info` items (from Agent 2):
        {attribute_name, inferred_information, concise_explanation,
         supporting_utterance_span}

    `new_connections_touching_this_problem` items (from Agent 2,
     filtered to those that involve `problem_name` and rewritten with
     attribute_a = the side belonging to THIS problem):
        {attribute_a, attribute_b, relation_type, why,
         supporting_quote, other_problem}
    """
    existing_block = (
        "\n\n".join(_format_existing_record(r) for r in existing_records)
        if existing_records else "(none — fresh attributes)"
    )
    return textwrap.dedent(f"""\
        PROBLEM: {problem_name}
        TURN: session {current_session}, turn {current_turn}

        EXISTING_RECORDS (only attributes touched by new info this turn):
        {existing_block}

        NEW_ATTRIBUTE_INFO (extracted by Agent 2 this turn):
        {_format_new_attribute_info(new_attribute_info)}

        NEW_CONNECTION_INFO touching {problem_name} (use as context for
        attribute summaries; the connection itself is stacked separately):
        {_format_new_connections(new_connections_touching_this_problem)}

        Return the JSON object now.
    """)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


_LEVEL_SET = frozenset(LEVEL_ATTR_TYPES)
_NON_LEVEL_SET = frozenset(NON_LEVEL_ATTR_TYPES)


def validate_agent3a(
    out: dict,
    *,
    expected_problem: Optional[str] = None,
    expected_attrs: Optional[set[str]] = None,
) -> None:
    """Cross-field constraints. expected_problem and expected_attrs are
    optional (only enforced when provided by the caller).
    """
    if expected_problem and out["problem_name"] != expected_problem:
        raise ValueError(
            f"expected problem_name {expected_problem!r}, got {out['problem_name']!r}"
        )
    seen: set[str] = set()
    for u in out["attribute_updates"]:
        attr = u["attribute_name"]
        if attr in seen:
            raise ValueError(f"duplicate attribute_updates entry for {attr!r}")
        seen.add(attr)
        if attr not in _LEVEL_SET and attr not in _NON_LEVEL_SET:
            raise ValueError(f"unknown attribute {attr!r}")
        # Non-level attrs must use unknown / low
        if attr in _NON_LEVEL_SET:
            if u["current_level"] != "unknown":
                raise ValueError(
                    f"non-level attribute {attr!r} must have current_level "
                    f"= 'unknown', got {u['current_level']!r}"
                )
        if not u["summary_text"].strip():
            raise ValueError(f"empty summary_text for {attr!r}")
    if expected_attrs is not None:
        if seen != expected_attrs:
            raise ValueError(
                f"attribute_updates set {seen} != expected {expected_attrs}"
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@dataclass
class Agent3aInputs:
    problem_name: str
    current_session: int
    current_turn: int
    existing_records: list[dict]
    new_attribute_info: list[dict]
    new_connections_touching_this_problem: list[dict]


def _safe_fallback(problem_name: str) -> dict:
    return {
        "problem_name": problem_name,
        "attribute_updates": [],
        "_fallback_default": True,
    }


def run_agent3a(
    *, client: LLMClient, ctx: CallContext, inputs: Agent3aInputs,
) -> dict:
    assert ctx.call_role == "agent3a_attr_update"
    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(**inputs.__dict__),
            schema=AGENT3A_SCHEMA,
            validator_extras=lambda o: validate_agent3a(
                o, expected_problem=inputs.problem_name,
            ),
        )
    except LLMStructuredError:
        return _safe_fallback(inputs.problem_name)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _self_test() -> None:
    valid = {
        "problem_name": "academic_pressure",
        "attribute_updates": [
            {
                "attribute_name": "perceived_severity",
                "summary_text": "t1: pressure overwhelming → severity high.",
                "current_level": "high",
                "level_reasoning": "single unambiguous statement",
                "level_change_confidence": "high",
                "new_info_useful": 1,
            },
            {
                "attribute_name": "triggers",
                "summary_text": "t1: late-night cramming for finals.",
                "current_level": "unknown",
                "level_reasoning": "",
                "level_change_confidence": "low",
                "new_info_useful": 1,
            },
        ],
    }
    validate_agent3a(valid, expected_problem="academic_pressure")
    validate_agent3a(
        valid,
        expected_problem="academic_pressure",
        expected_attrs={"perceived_severity", "triggers"},
    )

    # Non-level attr with level != "unknown" → reject.
    bad1 = {
        "problem_name": "academic_pressure",
        "attribute_updates": [
            {
                "attribute_name": "triggers",
                "summary_text": "x",
                "current_level": "high",  # invalid for non-level
                "level_reasoning": "",
                "level_change_confidence": "low",
                "new_info_useful": 1,
            },
        ],
    }
    try:
        validate_agent3a(bad1)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "non-level attribute" in str(e)

    # Wrong problem_name → reject.
    try:
        validate_agent3a(valid, expected_problem="sleep_problems")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # Duplicate attribute → reject.
    bad2 = {
        "problem_name": "academic_pressure",
        "attribute_updates": [
            valid["attribute_updates"][0],
            valid["attribute_updates"][0],
        ],
    }
    try:
        validate_agent3a(bad2)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # Empty summary → reject.
    bad3 = {
        "problem_name": "academic_pressure",
        "attribute_updates": [
            {
                "attribute_name": "perceived_severity",
                "summary_text": "  ",
                "current_level": "high",
                "level_reasoning": "x",
                "level_change_confidence": "high",
                "new_info_useful": 1,
            },
        ],
    }
    try:
        validate_agent3a(bad3)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # Unexpected attrs set → reject.
    try:
        validate_agent3a(
            valid, expected_problem="academic_pressure",
            expected_attrs={"perceived_severity"},
        )
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # Prompts render
    sp = build_system_prompt()
    assert "AttributeAgent" in sp
    assert "PATTERN OVERRIDES" in sp
    up = build_user_prompt(
        problem_name="academic_pressure", current_session=1, current_turn=2,
        existing_records=[
            {"attribute_name": "perceived_severity", "current_level": "high",
             "level_reasoning": "x", "summary_text": "t1: ..."},
        ],
        new_attribute_info=[
            {"attribute_name": "perceived_severity",
             "inferred_information": "y", "concise_explanation": "z",
             "supporting_utterance_span": "I can't keep this up"},
        ],
        new_connections_touching_this_problem=[
            {"attribute_a": "perceived_severity", "attribute_b": "triggers",
             "relation_type": "causal", "why": "x",
             "supporting_quote": "y", "other_problem": "sleep_problems"},
        ],
    )
    assert "academic_pressure" in up
    assert "perceived_severity" in up
    assert "sleep_problems" in up

    print("agent3a_attr_update self-test PASSED")


if __name__ == "__main__":
    _self_test()
