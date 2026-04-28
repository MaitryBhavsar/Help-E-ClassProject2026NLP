"""§18.1 v6 — Per-turn inference (replaces v5 extraction).

One LLM call per user turn. Emits SIX structured fields, not four:

  1. user_intent          — single-object record with confidence + span
  2. current_problems     — list of problems in play this turn, flagged
                             new-vs-existing
  3. main_problem         — single dominant problem or null
  4. problem_attribute_entries — per (problem, attr) evidence, level or
                             non-level, with confidence + span
  5. problem_cooccurrence_connections — weak signal: both problems were
                             discussed in the same turn
  6. problem_attribute_connections — strong, typed signal: an attribute of
                             problem A is meaningfully related to an
                             attribute of problem B (causal / effect /
                             shared_trigger / ...)

Key separation from v5:
  - Attribute entries no longer share an AttributeNode pool — each entry
    is per-problem.
  - Co-occurrence is recorded as its own evidence type (weak by design).
  - Attribute connections are typed and require linking evidence in the
    utterance; merely sharing an attr_type across two problems is NOT
    sufficient.

Cold-start (session 1, turn 1): `active_problem_list` may be empty. The
prompt handles this gracefully — every problem is new and there is no
prior graph state to reconcile.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Any, Optional

from ..config import (
    CONFIDENCE_LEVELS,
    LEVEL_ATTR_TYPES,
    NON_LEVEL_ATTR_TYPES,
    PROBLEM_VOCAB,
    RELATION_TYPES,
    USER_INTENTS_V6,
)
from ..llm_client import CallContext, LLMClient, LLMStructuredError
from .common import (
    PROJECT_IDENTITY,
    format_dialog_turns,
    problem_vocab_block,
)
from .common_v6 import (
    format_active_problems_v6,
    level_attribute_block,
    non_level_attribute_block,
    problem_name_mapping_block,
    relation_types_block,
    user_intents_v6_block,
)

# All 11 attribute types (level ∪ non-level) — used as the enum for
# attribute_name fields in the schema.
_ALL_ATTR_TYPES: tuple[str, ...] = tuple(list(LEVEL_ATTR_TYPES) + list(NON_LEVEL_ATTR_TYPES))


# ---------------------------------------------------------------------------
# JSON schema (Draft 2020-12)
# ---------------------------------------------------------------------------

INFERENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "user_intent",
        "current_problems",
        "main_problem",
        "problem_attribute_entries",
        "problem_cooccurrence_connections",
        "problem_attribute_connections",
    ],
    "properties": {
        "user_intent": {
            "type": "object",
            "additionalProperties": False,
            "required": ["intent", "supporting_utterance_span"],
            "properties": {
                "intent": {"type": "string", "enum": list(USER_INTENTS_V6)},
                "supporting_utterance_span": {"type": ["string", "null"]},
            },
        },
        "current_problems": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "problem_name",
                    "explanation", "supporting_utterance_span",
                ],
                "properties": {
                    "problem_name": {"type": "string", "enum": list(PROBLEM_VOCAB)},
                    "explanation": {"type": "string", "minLength": 1},
                    "supporting_utterance_span": {"type": ["string", "null"]},
                },
            },
        },
        "main_problem": {
            "anyOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["problem_name", "explanation", "supporting_utterance_span"],
                    "properties": {
                        "problem_name": {"type": "string", "enum": list(PROBLEM_VOCAB)},
                        "explanation": {"type": "string", "minLength": 1},
                        "supporting_utterance_span": {"type": ["string", "null"]},
                    },
                },
                {"type": "null"},
            ],
        },
        "problem_attribute_entries": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "problem_name", "attribute_name",
                    "inferred_information", "concise_explanation",
                    "supporting_utterance_span",
                ],
                "properties": {
                    "problem_name": {"type": "string", "enum": list(PROBLEM_VOCAB)},
                    "attribute_name": {"type": "string", "enum": list(_ALL_ATTR_TYPES)},
                    "inferred_information": {"type": "string", "minLength": 1},
                    "concise_explanation": {"type": "string", "minLength": 1},
                    "supporting_utterance_span": {"type": ["string", "null"]},
                },
            },
        },
        "problem_cooccurrence_connections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "problem_1", "problem_2",
                    "concise_explanation", "supporting_utterance_span",
                ],
                "properties": {
                    "problem_1": {"type": "string", "enum": list(PROBLEM_VOCAB)},
                    "problem_2": {"type": "string", "enum": list(PROBLEM_VOCAB)},
                    "concise_explanation": {"type": "string", "minLength": 1},
                    "supporting_utterance_span": {"type": ["string", "null"]},
                },
            },
        },
        "problem_attribute_connections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "problem_1", "attribute_1", "problem_2", "attribute_2",
                    "relation_type", "connection_explanation",
                    "supporting_utterance_span", "confidence",
                ],
                "properties": {
                    "problem_1": {"type": "string", "enum": list(PROBLEM_VOCAB)},
                    "attribute_1": {"type": "string", "enum": list(_ALL_ATTR_TYPES)},
                    "problem_2": {"type": "string", "enum": list(PROBLEM_VOCAB)},
                    "attribute_2": {"type": "string", "enum": list(_ALL_ATTR_TYPES)},
                    "relation_type": {"type": "string", "enum": list(RELATION_TYPES)},
                    "connection_explanation": {"type": "string", "minLength": 1},
                    "supporting_utterance_span": {"type": ["string", "null"]},
                    "confidence": {"type": "string", "enum": list(CONFIDENCE_LEVELS)},
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

        You are the INFERENCE agent (§18.1 v6). On every user turn you read
        the current user utterance, a window of recent dialogue, and the
        current list of the user's active problems. You then emit a single
        JSON record with SIX fields: user_intent, current_problems,
        main_problem, problem_attribute_entries,
        problem_cooccurrence_connections, and problem_attribute_connections.

        You do NOT talk to the user. You do NOT decide TTM stage or
        attribute levels — those are recomputed separately from the
        chronological evidence stack after you run.

        # VOCABULARIES (use these exact strings — no others)

        {problem_vocab_block()}

        # PROBLEM-NAME MAPPING

        {problem_name_mapping_block()}

        {level_attribute_block()}

        {non_level_attribute_block()}

        {relation_types_block()}

        {user_intents_v6_block()}

        # DEFINITIONS

        - Current problem: a problem from the 20-vocabulary that is active
          in THIS turn. You may carry a previously-active problem forward
          ONLY if the user is clearly still on it.
        - Main problem: the single most dominant problem in THIS turn, or
          null if no problem clearly dominates or the turn has no problem
          content at all.
        - Level attribute: an attribute that carries a low/medium/high
          level. There are 7: perceived_severity, perceived_susceptibility,
          perceived_benefits, perceived_barriers, self_efficacy,
          cues_to_action, motivation.
        - Non-level attribute: an attribute without a level. There are 4:
          coping_strategies, past_attempts, triggers, goal.
        - Co-occurrence connection: two problems were both discussed in
          THIS turn — nothing more. Do not over-interpret.
        - Attribute connection: the utterance gives evidence that an
          attribute of problem A is meaningfully related to an attribute
          of problem B. The relation type must be one of the 8 listed
          above.

        # PRIMARY EVIDENCE RULE

        - The CURRENT user utterance is the primary source.
        - Past turns are context only — for coreference, topic continuity,
          and disambiguation.
        - Do NOT over-infer. Silence on an attribute means no entry for it.
        - Every inferred item must include a concise explanation AND a
          supporting utterance span (a substring of the CURRENT user
          message, or null if only implied).

        # CRITICAL: ATTRIBUTE CONNECTIONS ARE NOT AUTOMATIC

        An attribute connection is NOT justified by two problems sharing
        the same attr_type. You must see evidence in the utterance itself
        that the two attributes are meaningfully related.

        Counter-example:
          User: "I feel unable to cope with work, and I can't cope with
                 my breakup either."
          Correct: TWO self_efficacy entries, ONE per problem. NO
                   attribute connection, because the utterance only
                   expresses low self_efficacy on both — it does not link
                   one to the other.

        Positive example:
          User: "The late-night cramming for finals is what's keeping me
                 up at 3am — I can't sleep because I keep thinking about
                 the workload."
          Correct: ONE attribute connection (academic_pressure.triggers
                   shared_trigger sleep_problems.triggers), because the
                   utterance states the SAME driver fires both.

        # CONFIDENCE SCALE

        Use `confidence` at each inferred item:
          - low:    utterance only hints at it; you are interpolating.
          - medium: utterance implies it clearly.
          - high:   utterance states it directly.

        # STRUCTURAL RULES (the post-parse validator enforces these)

        - If `current_problems` is empty → `main_problem` MUST be null,
          all three entry arrays MUST be empty.
        - If `main_problem` is non-null → its `problem_name` must appear
          in `current_problems`.
        - Every `problem_attribute_entries[i].problem_name` must appear in
          `current_problems`.
        - No duplicate (problem_name, attribute_name) pairs in
          `problem_attribute_entries` — merge evidence into one entry.
        - Co-occurrence and attribute connections require two DIFFERENT
          problems (problem_1 != problem_2), both present in
          `current_problems`.
        - No duplicate unordered problem pairs in
          `problem_cooccurrence_connections`.

        # REQUIRED JSON SHAPE

        {{
          "user_intent": {{
            "intent": "express_emotion",
            "supporting_utterance_span": "I don't think I can keep this up"
          }},
          "current_problems": [
            {{
              "problem_name": "academic_pressure",
              "explanation": "finals / all-nighters",
              "supporting_utterance_span": "pulling all-nighters for finals"
            }},
            {{
              "problem_name": "sleep_problems",
              "explanation": "rumination-driven insomnia",
              "supporting_utterance_span": "I lie there replaying everything"
            }}
          ],
          "main_problem": {{
            "problem_name": "academic_pressure",
            "explanation": "finals is the framing driver of both threads",
            "supporting_utterance_span": "pulling all-nighters for finals"
          }},
          "problem_attribute_entries": [
            {{
              "problem_name": "academic_pressure",
              "attribute_name": "perceived_severity",
              "inferred_information": "user sees this week's workload as unsustainable",
              "concise_explanation": "'can't keep this up' language",
              "supporting_utterance_span": "I don't think I can keep this up"
            }},
            {{
              "problem_name": "academic_pressure",
              "attribute_name": "triggers",
              "inferred_information": "late-night cramming drives the stress escalation",
              "concise_explanation": "all-nighters cited as driver",
              "supporting_utterance_span": "pulling all-nighters for finals"
            }},
            {{
              "problem_name": "sleep_problems",
              "attribute_name": "triggers",
              "inferred_information": "rumination about academics prevents sleep onset",
              "concise_explanation": "lies awake replaying",
              "supporting_utterance_span": "I lie there replaying everything"
            }}
          ],
          "problem_cooccurrence_connections": [
            {{
              "problem_1": "academic_pressure",
              "problem_2": "sleep_problems",
              "concise_explanation": "both problems discussed in the same turn",
              "supporting_utterance_span": "pulling all-nighters for finals and honestly I can't sleep anyway"
            }}
          ],
          "problem_attribute_connections": [
            {{
              "problem_1": "academic_pressure",
              "attribute_1": "triggers",
              "problem_2": "sleep_problems",
              "attribute_2": "triggers",
              "relation_type": "shared_trigger",
              "connection_explanation": "late-night cramming is the same mechanism driving both the academic stress escalation and the sleep-onset failure.",
              "supporting_utterance_span": "pulling all-nighters for finals and honestly I can't sleep anyway — I lie there replaying everything",
              "confidence": "high"
            }}
          ]
        }}

        # EMPTY-TURN EXAMPLE (no problem content)

        {{
          "user_intent": {{
            "intent": "small_talk",
            "supporting_utterance_span": "hey how are you"
          }},
          "current_problems": [],
          "main_problem": null,
          "problem_attribute_entries": [],
          "problem_cooccurrence_connections": [],
          "problem_attribute_connections": []
        }}

        Return ONLY JSON matching the schema. No prose before or after.
    """)


def build_user_prompt(
    *,
    current_message: str,
    recent_turns: list[dict],
    active_problems: list[dict],
) -> str:
    return textwrap.dedent(f"""\
        RECENT_DIALOGUE:
        {format_dialog_turns(recent_turns)}

        ACTIVE_PROBLEMS (already tracked for this user):
        {format_active_problems_v6(active_problems)}

        CURRENT_USER_MESSAGE:
        {current_message}
    """)


# ---------------------------------------------------------------------------
# Post-parse validator
# ---------------------------------------------------------------------------


_LEVEL_SET = frozenset(LEVEL_ATTR_TYPES)
_NON_LEVEL_SET = frozenset(NON_LEVEL_ATTR_TYPES)


def validate_inference(out: dict) -> None:
    """Enforce structural invariants the JSON schema can't express."""
    current = [c["problem_name"] for c in out["current_problems"]]
    current_set = set(current)

    # Duplicate current_problems check.
    if len(current) != len(current_set):
        raise ValueError(
            f"duplicate problem in current_problems: {current!r}"
        )

    # main_problem ⊆ current_problems.
    main = out["main_problem"]
    if current:
        if main is not None and main["problem_name"] not in current_set:
            raise ValueError(
                f"main_problem {main['problem_name']!r} not in current_problems"
            )
    else:
        # No current problems → everything else must be empty.
        if main is not None:
            raise ValueError(
                "main_problem must be null when current_problems is empty"
            )
        for field_name in (
            "problem_attribute_entries",
            "problem_cooccurrence_connections",
            "problem_attribute_connections",
        ):
            if out[field_name]:
                raise ValueError(
                    f"{field_name} must be empty when current_problems is empty"
                )
        return

    # problem_attribute_entries: enforce membership + dedupe.
    # attribute_type was removed from the schema — it's derivable from
    # attribute_name (LEVEL_ATTR_TYPES vs NON_LEVEL_ATTR_TYPES).
    seen_pairs: set[tuple[str, str]] = set()
    for e in out["problem_attribute_entries"]:
        if e["problem_name"] not in current_set:
            raise ValueError(
                f"attribute entry references non-current problem {e['problem_name']!r}"
            )
        attr = e["attribute_name"]
        if attr not in _LEVEL_SET and attr not in _NON_LEVEL_SET:
            raise ValueError(
                f"attribute {attr!r} not in level or non-level attribute lists"
            )
        key = (e["problem_name"], attr)
        if key in seen_pairs:
            raise ValueError(
                f"duplicate problem_attribute_entries entry for {key} — "
                f"merge evidence into a single record"
            )
        seen_pairs.add(key)

    # Co-occurrence: distinct problems, both in current, no duplicate unordered pair.
    seen_cooc: set[frozenset[str]] = set()
    for c in out["problem_cooccurrence_connections"]:
        if c["problem_1"] == c["problem_2"]:
            raise ValueError(
                f"co-occurrence requires two distinct problems, got {c['problem_1']!r} twice"
            )
        if c["problem_1"] not in current_set or c["problem_2"] not in current_set:
            raise ValueError(
                f"co-occurrence references problem not in current_problems: "
                f"{c['problem_1']}, {c['problem_2']}"
            )
        pair = frozenset((c["problem_1"], c["problem_2"]))
        if pair in seen_cooc:
            raise ValueError(
                f"duplicate co-occurrence pair {set(pair)} — merge into one entry"
            )
        seen_cooc.add(pair)

    # Attribute connections: distinct problems, attrs valid, both in current.
    for c in out["problem_attribute_connections"]:
        if c["problem_1"] == c["problem_2"]:
            raise ValueError(
                f"attribute connection requires two distinct problems, "
                f"got {c['problem_1']!r} twice"
            )
        if c["problem_1"] not in current_set or c["problem_2"] not in current_set:
            raise ValueError(
                f"attribute connection references problem not in current_problems: "
                f"{c['problem_1']}, {c['problem_2']}"
            )
        # Attribute names are already enum-enforced at schema level. We
        # don't enforce attribute_1/2 to appear in problem_attribute_entries
        # because a connection can be inferred from language even when no
        # separate attribute_entry was warranted.


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@dataclass
class InferenceInputs:
    current_message: str
    recent_turns: list[dict]
    active_problems: list[dict]  # [{problem_name, current_ttm_stage, goal, last_mentioned}, ...]


def _safe_fallback() -> dict:
    # `small_talk` is the safest v6 fallback intent — assumes the user
    # made a low-content utterance without any inferable problem.
    return {
        "user_intent": {
            "intent": "small_talk",
            "supporting_utterance_span": None,
        },
        "current_problems": [],
        "main_problem": None,
        "problem_attribute_entries": [],
        "problem_cooccurrence_connections": [],
        "problem_attribute_connections": [],
        "_fallback_default": True,
    }


def run_inference(
    *,
    client: LLMClient,
    ctx: CallContext,
    inputs: InferenceInputs,
) -> dict:
    """Execute §18.1 v6. On total failure returns a safe empty record so
    the pipeline can continue (the turn is treated as having no problem
    content).
    """
    assert ctx.call_role == "inference"

    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(**inputs.__dict__),
            schema=INFERENCE_SCHEMA,
            validator_extras=validate_inference,
        )
    except LLMStructuredError:
        return _safe_fallback()


# ---------------------------------------------------------------------------
# Self-test (validator only — does not call an LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    # Valid non-empty record passes.
    valid = {
        "user_intent": {
            "intent": "express_emotion", "supporting_utterance_span": "y",
        },
        "current_problems": [
            {"problem_name": "academic_pressure",
             "explanation": "x", "supporting_utterance_span": "y"},
            {"problem_name": "sleep_problems",
             "explanation": "x", "supporting_utterance_span": "y"},
        ],
        "main_problem": {
            "problem_name": "academic_pressure",
            "explanation": "x", "supporting_utterance_span": "y",
        },
        "problem_attribute_entries": [
            {"problem_name": "academic_pressure", "attribute_name": "perceived_severity",
             "inferred_information": "a",
             "concise_explanation": "b", "supporting_utterance_span": "c"},
            {"problem_name": "sleep_problems", "attribute_name": "triggers",
             "inferred_information": "d",
             "concise_explanation": "e", "supporting_utterance_span": "f"},
        ],
        "problem_cooccurrence_connections": [
            {"problem_1": "academic_pressure", "problem_2": "sleep_problems",
             "concise_explanation": "same turn", "supporting_utterance_span": "y"},
        ],
        "problem_attribute_connections": [
            {"problem_1": "academic_pressure", "attribute_1": "triggers",
             "problem_2": "sleep_problems", "attribute_2": "triggers",
             "relation_type": "shared_trigger",
             "connection_explanation": "same driver", "supporting_utterance_span": "y",
             "confidence": "high"},
        ],
    }
    validate_inference(valid)

    # Empty turn passes.
    empty = {
        "user_intent": {"intent": "small_talk",
                        "supporting_utterance_span": None},
        "current_problems": [], "main_problem": None,
        "problem_attribute_entries": [],
        "problem_cooccurrence_connections": [],
        "problem_attribute_connections": [],
    }
    validate_inference(empty)

    # Main not in current: rejected.
    bad = dict(valid)
    bad["main_problem"] = {"problem_name": "work_stress",
                           "explanation": "x", "supporting_utterance_span": None}
    try:
        validate_inference(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("expected rejection: main_problem not in current")

    # Duplicate (problem, attr): rejected.
    bad3 = _deepcopy_dict(valid)
    bad3["problem_attribute_entries"].append(
        dict(bad3["problem_attribute_entries"][0])
    )
    try:
        validate_inference(bad3)
    except ValueError:
        pass
    else:
        raise AssertionError("expected rejection: duplicate (problem, attr)")

    # Duplicate co-occurrence pair (unordered): rejected.
    bad4 = _deepcopy_dict(valid)
    bad4["problem_cooccurrence_connections"].append({
        "problem_1": "sleep_problems", "problem_2": "academic_pressure",
        "concise_explanation": "reversed", "supporting_utterance_span": None,
    })
    try:
        validate_inference(bad4)
    except ValueError:
        pass
    else:
        raise AssertionError("expected rejection: duplicate unordered pair")

    # Self-pair co-occurrence: rejected.
    bad6 = _deepcopy_dict(valid)
    bad6["problem_cooccurrence_connections"] = [{
        "problem_1": "academic_pressure", "problem_2": "academic_pressure",
        "concise_explanation": "bogus", "supporting_utterance_span": None,
    }]
    try:
        validate_inference(bad6)
    except ValueError:
        pass
    else:
        raise AssertionError("expected rejection: self-pair co-occurrence")

    # Prompts assemble without crashing.
    sys = build_system_prompt()
    assert "INFERENCE agent" in sys and "counter-example" in sys.lower()
    usr = build_user_prompt(
        current_message="hi there",
        recent_turns=[],
        active_problems=[],
    )
    assert "CURRENT_USER_MESSAGE" in usr

    print("inference self-test PASSED")


def _deepcopy_dict(d: dict) -> dict:
    import copy
    return copy.deepcopy(d)


if __name__ == "__main__":
    _self_test()
