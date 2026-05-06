"""v7 Agent 2 — INFERENCE on the big model.

Reads the current user utterance + a small dialogue window + the names
of previously-active problems (with the previous main_problem flagged
for sticky-main reasoning).

Emits FOUR structured fields:
  - current_problems              (which problems are active this turn)
  - main_problem                  (sticky unless strong focus shift)
  - problem_attribute_entries     (NEW per-attribute info from THIS turn)
  - problem_attribute_connections (typed cross-problem links, always
                                   through attributes)

Differences from v6 inference:
  - No `user_intent` (Agent 1 owns it).
  - No `problem_cooccurrence_connections` — v7 stores one structured
    connection-entry stack per edge; cooccurrence-only links are
    derived in Python (Agent 4) from co-membership in current_problems.
  - No level inference (Agent 3a owns levels).
  - No `confidence` per attribute_connection (v7 graph doesn't weight
    entries by per-entry confidence).
  - Sticky-main rule is explicit in the prompt with examples.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Any, Optional

from ..config import (
    LEVEL_ATTR_TYPES,
    NON_LEVEL_ATTR_TYPES,
    PROBLEM_VOCAB,
    RELATION_TYPES,
)
from ..graph_v6 import global_turn_idx
from ..graph_v7 import ProblemGraphV7
from ..llm_client import CallContext, LLMClient, LLMStructuredError
from .common import PROJECT_IDENTITY, format_dialog_turns, problem_vocab_block
from .common_v6 import (
    level_attribute_block,
    non_level_attribute_block,
    problem_name_mapping_block,
    relation_types_block,
)

_ALL_ATTR_TYPES: tuple[str, ...] = tuple(
    list(LEVEL_ATTR_TYPES) + list(NON_LEVEL_ATTR_TYPES)
)


# ---------------------------------------------------------------------------
# JSON schema (Draft 2020-12)
# ---------------------------------------------------------------------------

AGENT2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "current_problems",
        "main_problem",
        "problem_attribute_entries",
        "problem_attribute_connections",
    ],
    "properties": {
        "current_problems": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "problem_name", "explanation", "supporting_utterance_span",
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
                    "required": [
                        "problem_name", "explanation", "supporting_utterance_span",
                    ],
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
        "problem_attribute_connections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "problem_1", "attribute_1", "problem_2", "attribute_2",
                    "relation_type", "connection_explanation",
                    "supporting_utterance_span",
                ],
                "properties": {
                    "problem_1": {"type": "string", "enum": list(PROBLEM_VOCAB)},
                    "attribute_1": {"type": "string", "enum": list(_ALL_ATTR_TYPES)},
                    "problem_2": {"type": "string", "enum": list(PROBLEM_VOCAB)},
                    "attribute_2": {"type": "string", "enum": list(_ALL_ATTR_TYPES)},
                    "relation_type": {"type": "string", "enum": list(RELATION_TYPES)},
                    "connection_explanation": {"type": "string", "minLength": 1},
                    "supporting_utterance_span": {"type": ["string", "null"]},
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _sticky_main_block() -> str:
    return textwrap.dedent("""
        # MAIN PROBLEM — chosen from the WHOLE arc, not just this turn

        Choosing `main_problem` does NOT mean you shrink `current_problems`.
        `current_problems` lists every active thread; `main_problem` names the
        single thread that is most central to the user RIGHT NOW.

        Decide `main_problem` from THREE sources together — none alone is
        decisive:
          1. CURRENT_USER_MESSAGE — what is the user pulling toward in this
             turn, which problem carries the heaviest concrete language /
             stakes / specifics?
          2. RECENT_DIALOGUE (the last several turns shown to you) — which
             problem has been the gravitational center of the conversation
             arc, even when this single turn is brief or ambiguous?
          3. PREVIOUSLY_ACTIVE_PROBLEMS + the problem vocabulary — does the
             vocabulary's framing for any listed problem fit what the user
             is doing better than the previous main? When two candidates
             feel close, prefer the one whose vocabulary entry matches the
             user's wording / stakes / target of distress.

        Stickiness is a TIE-BREAKER, not a default. Keep `previous_main_problem`
        as `main_problem` only when (1)+(2)+(3) above leave it as the best
        match. Switch when another current problem clearly fits better under
        any of:
          - the user's current message centers stakes / specificity / emotion
            on a different listed problem,
          - the recent dialogue arc has migrated to another problem,
          - the previous main is no longer the closest vocabulary match for
            what the user is naming or implying,
          - the user explicitly redirects ("actually I need to talk about X").

        Do NOT carry the previous main forward just because it was previous.
        A reasonable shift each time the center drifts is GOOD; clinging to a
        stale main is the failure mode this rule guards against.

        Dialogue continuation: minimal replies ("yeah", "I don't know") may
        not name problems by themselves; in that case lean harder on
        RECENT_DIALOGUE + PREVIOUSLY_ACTIVE_PROBLEMS to keep a sensible main.

        Cold start (no previous_main_problem): pick the strongest center
        among this turn's live problems for `main_problem`; `main_problem`
        may be null only when `current_problems` is empty.
    """)


def build_system_prompt() -> str:
    return textwrap.dedent(f"""\
        {PROJECT_IDENTITY}

        You are the InferenceAgent of the v7/v8 HELP-E pipeline. On every
        user turn you read the current user utterance, a small window of
        recent dialogue (for coreference only), and the names of the
        user's previously-active problems with one of them flagged as
        the previous main_problem.

        You emit ONE JSON record with FOUR fields: current_problems,
        main_problem, problem_attribute_entries, and
        problem_attribute_connections.

        # THREE SEPARATE JOBS (do not conflate them)

        1) Problem detection — `current_problems` + `main_problem`
           (what problems are live this turn, and which is central).
        2) Attribute extraction — `problem_attribute_entries`
           (NEW attribute-level facts from THIS user utterance only).
        3) Cross-problem links — `problem_attribute_connections`
           (NEW evidence in THIS utterance that a specific attribute on
           problem A meaningfully relates to a specific attribute on B).

        # CURRENT_PROBLEMS — list ALL of them, no cap

        Always include EVERY problem from the vocabulary that is active or
        clearly continuing this turn. There is NO ceiling on how many you may
        list — if the user surfaces five threads at once, list five. Better to
        list a problem and have the attribute arrays stay empty than to drop
        the problem because no new attribute detail showed up. Problem
        detection lives in `current_problems`; attribute extraction lives in
        the other two arrays. They are independent jobs.

        # CURRENT_PROBLEMS WITHOUT NEW ATTRIBUTES IS NORMAL

        It is NORMAL and VALID for `current_problems` / `main_problem` to be
        non-empty while BOTH `problem_attribute_entries` AND
        `problem_attribute_connections` are empty. This happens when the user:
          - is just venting / acknowledging an existing problem,
          - gives a brief continuation ("yeah", "still that"),
          - shifts focus among already-named problems without adding new
            severity / barrier / trigger / coping detail,
          - restates the same pain you've already captured.
        In all of these cases: KEEP the problem in `current_problems`, leave
        the attribute arrays empty. Do NOT drop the problem just because this
        turn carries no new attribute-level information.

        You do NOT classify user_intent (Agent 1 does). You do NOT decide
        attribute LEVELS or TTM stages (Agent 3a/3b do). You do NOT talk
        to the user.

        # VOCABULARIES (use these exact strings)

        {problem_vocab_block()}

        # PROBLEM-NAME MAPPING

        {problem_name_mapping_block()}

        {level_attribute_block()}

        {non_level_attribute_block()}

        {relation_types_block()}

        # DEFINITIONS

        - Current problem: a problem from the vocabulary that is active
          in THIS turn (mentioned, alluded to, or carried forward
          because the user is clearly still on it).
        - Main problem: the problem this turn most centers on. Subject
          to the MAIN PROBLEM (sticky, but NOT over-sticky) rules above.
        - Problem-attribute entry: a single piece of NEW information
          about ONE attribute of ONE problem, drawn from THIS turn.
        - Problem-attribute connection: typed evidence in the utterance
          that an attribute of problem A is meaningfully related to an
          attribute of problem B.

        # PRIMARY EVIDENCE RULE (problem vs attributes)

        Problem detection (`current_problems`, `main_problem`):
          - Use the CURRENT user utterance PLUS RECENT_DIALOGUE + the
            PREVIOUSLY_ACTIVE_PROBLEMS hints to decide what problems stay
            in play and where the center is.
          - Supporting spans for problems may cite the current message OR be
            null when continuity is purely from recent dialogue — in that case
            the problem's `explanation` must cite that continuity plainly.

        Attribute / connection extraction (`problem_attribute_entries`,
        `problem_attribute_connections`):
          - ONLY emit NEW structured attribute facts or typed cross-problem
            links justified by THIS user utterance. Do NOT re-scrape historical
            events from RECENT_DIALOGUE for these two arrays — if the user adds
            no new attributable detail here, leave them empty arrays.

        For those two arrays: do NOT over-infer — silence means no row.
        Every row must include concise explanation; `supporting_utterance_span`
        is preferably a substring of the CURRENT user message but may be null
        when implication is unmistakable ONLY from wording in THIS message.

        # CRITICAL: ATTRIBUTE CONNECTIONS ARE NOT AUTOMATIC

        An attribute connection is NOT justified by two problems sharing
        the same attribute type. You must see evidence in the utterance
        itself that the two attributes are meaningfully related.

        Counter-example:
          User: "I feel unable to cope with work, and I can't cope with
                 my breakup either."
          Correct: TWO self_efficacy entries, ONE per problem. NO
                   attribute connection — the utterance only expresses
                   low self_efficacy on both, it does not link them.

        Positive example:
          User: "The late-night cramming for finals is what's keeping me
                 up at 3am — I keep thinking about the workload."
          Correct: ONE attribute connection
                   (academic_pressure.triggers shared_trigger
                    sleep_problems.triggers), because the utterance
                   states the SAME driver fires both.

        {_sticky_main_block()}

        # STRUCTURAL RULES (the post-parse validator enforces these)

        - If `current_problems` is empty → `main_problem` MUST be null,
          all entry arrays MUST be empty.
        - If `main_problem` is non-null → its `problem_name` must appear
          in `current_problems`.
        - Every `problem_attribute_entries[i].problem_name` must appear
          in `current_problems`.
        - No duplicate (problem_name, attribute_name) pairs in
          `problem_attribute_entries` — merge evidence into one entry.
        - Attribute connections require two DIFFERENT problems
          (problem_1 != problem_2), both present in `current_problems`.
        - No duplicate unordered (problem_1, attribute_1, problem_2,
          attribute_2, relation_type) tuples.

        # REQUIRED JSON SHAPE

        {{
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
              "problem_name": "sleep_problems",
              "attribute_name": "triggers",
              "inferred_information": "rumination about academics prevents sleep onset",
              "concise_explanation": "lies awake replaying",
              "supporting_utterance_span": "I lie there replaying everything"
            }}
          ],
          "problem_attribute_connections": [
            {{
              "problem_1": "academic_pressure",
              "attribute_1": "triggers",
              "problem_2": "sleep_problems",
              "attribute_2": "triggers",
              "relation_type": "shared_trigger",
              "connection_explanation": "late-night cramming is the same driver firing both the academic stress escalation and the sleep-onset failure.",
              "supporting_utterance_span": "pulling all-nighters for finals — I lie there replaying everything"
            }}
          ]
        }}

        # EMPTY-TURN EXAMPLE (no problem content)

        {{
          "current_problems": [],
          "main_problem": null,
          "problem_attribute_entries": [],
          "problem_attribute_connections": []
        }}

        # FOLLOW-UP EXAMPLE — problems continue, attributes empty

        {{
          "current_problems": [
            {{
              "problem_name": "work_stress",
              "explanation": "continuation of burnout thread already live",
              "supporting_utterance_span": null
            }},
            {{
              "problem_name": "sleep_problems",
              "explanation": "still bundled in user's ongoing juggling theme",
              "supporting_utterance_span": null
            }}
          ],
          "main_problem": {{
            "problem_name": "work_stress",
            "explanation": "center still framing pressure from prior beats",
            "supporting_utterance_span": null
          }},
          "problem_attribute_entries": [],
          "problem_attribute_connections": []
        }}

        Return ONLY JSON matching the schema. No prose before or after.
    """)


def _format_active_problems_v7(
    previous_active: list[str], previous_main: Optional[str],
) -> str:
    if not previous_active:
        return "(none — this is the cold-start of the user's profile)"
    lines = []
    for name in previous_active:
        flag = " ← PREVIOUS MAIN" if name == previous_main else ""
        lines.append(f"  - {name}{flag}")
    return "\n".join(lines)


def build_user_prompt(
    *,
    current_message: str,
    recent_turns: list[dict],
    previous_active_problems: list[str],
    previous_main_problem: Optional[str],
) -> str:
    return textwrap.dedent(f"""\
        RECENT_DIALOGUE (for problem continuity — attribute rows still only from CURRENT message text):
        {format_dialog_turns(recent_turns) if recent_turns else "(no prior turns)"}

        PREVIOUSLY_ACTIVE_PROBLEMS:
        {_format_active_problems_v7(previous_active_problems, previous_main_problem)}

        CURRENT_USER_MESSAGE:
        {current_message.strip()}

        Return the JSON object now.
    """)


# ---------------------------------------------------------------------------
# Post-parse validator
# ---------------------------------------------------------------------------


_LEVEL_SET = frozenset(LEVEL_ATTR_TYPES)
_NON_LEVEL_SET = frozenset(NON_LEVEL_ATTR_TYPES)


def validate_agent2(out: dict) -> None:
    current = [c["problem_name"] for c in out["current_problems"]]
    current_set = set(current)
    if len(current) != len(current_set):
        raise ValueError(f"duplicate problem in current_problems: {current!r}")

    main = out["main_problem"]
    if not current:
        if main is not None:
            raise ValueError(
                "main_problem must be null when current_problems is empty"
            )
        for f in (
            "problem_attribute_entries",
            "problem_attribute_connections",
        ):
            if out[f]:
                raise ValueError(f"{f} must be empty when current_problems is empty")
        return

    if main is not None and main["problem_name"] not in current_set:
        raise ValueError(
            f"main_problem {main['problem_name']!r} not in current_problems"
        )

    seen_pairs: set[tuple[str, str]] = set()
    for e in out["problem_attribute_entries"]:
        if e["problem_name"] not in current_set:
            raise ValueError(
                f"attribute entry references non-current problem {e['problem_name']!r}"
            )
        attr = e["attribute_name"]
        if attr not in _LEVEL_SET and attr not in _NON_LEVEL_SET:
            raise ValueError(f"attribute {attr!r} not in level/non-level lists")
        key = (e["problem_name"], attr)
        if key in seen_pairs:
            raise ValueError(
                f"duplicate problem_attribute_entries entry for {key} — merge"
            )
        seen_pairs.add(key)

    seen_connections: set[tuple] = set()
    for c in out["problem_attribute_connections"]:
        if c["problem_1"] == c["problem_2"]:
            raise ValueError(
                f"attribute connection requires two distinct problems, "
                f"got {c['problem_1']!r} twice"
            )
        if (
            c["problem_1"] not in current_set
            or c["problem_2"] not in current_set
        ):
            raise ValueError(
                f"attribute connection references problem not in "
                f"current_problems: {c['problem_1']}, {c['problem_2']}"
            )
        # Canonicalize to dedupe order-invariant duplicates.
        canonical = tuple(sorted([
            (c["problem_1"], c["attribute_1"]),
            (c["problem_2"], c["attribute_2"]),
        ])) + (c["relation_type"],)
        if canonical in seen_connections:
            raise ValueError(
                f"duplicate attribute connection {canonical} — merge"
            )
        seen_connections.add(canonical)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@dataclass
class Agent2Inputs:
    current_message: str
    recent_turns: list[dict]
    previous_active_problems: list[str]
    previous_main_problem: Optional[str]


def build_agent2_carry_forward_output(
    *,
    graph: ProblemGraphV7,
    previous_main_problem: Optional[str],
    previous_turn_traces: list[dict],
    error_summary: str,
) -> dict:
    """When structured inference retries are exhausted: reuse the last trace's
    active problem list (or minimal graph-based fallback) instead of emitting
    an empty inference object. Attribute/connection arrays are always empty."""

    reasons = (error_summary or "unknown").strip()

    prev_names: list[str] = []
    trace_main: Optional[str] = None
    if previous_turn_traces:
        lt = previous_turn_traces[-1].get("trace") or {}
        raw = lt.get("current_problems") or []
        for item in raw:
            if isinstance(item, str) and item.strip():
                prev_names.append(item.strip())
            elif isinstance(item, dict):
                pn = item.get("problem_name")
                if isinstance(pn, str) and pn.strip():
                    prev_names.append(pn.strip())
        seen_ns: set[str] = set()
        prev_names = [n for n in prev_names if not (n in seen_ns or seen_ns.add(n))]
        tm = lt.get("main_problem")
        if isinstance(tm, str) and tm.strip():
            trace_main = tm.strip()

    if not prev_names and previous_main_problem:
        prev_names = [previous_main_problem]
        trace_main = trace_main or previous_main_problem

    if not prev_names and graph.problems:
        probs = sorted(
            graph.problems.values(),
            key=lambda pr: global_turn_idx(pr.last_mentioned[0], pr.last_mentioned[1]),
            reverse=True,
        )
        prev_names = [probs[0].problem_name][:1]
        trace_main = trace_main or prev_names[0]

    if not prev_names:
        out = {
            "current_problems": [],
            "main_problem": None,
            "problem_attribute_entries": [],
            "problem_attribute_connections": [],
            "_agent2_carry_forward": False,
            "_agent2_carry_forward_reason": reasons,
        }
        validate_agent2(out)
        return out

    def _explain(pn: str) -> str:
        return (
            f"carry_forward:inference exhaustion — {reasons[:180]}"
            if pn == (trace_main or previous_main_problem or prev_names[0])
            else f"carry_forward:active context includes {pn} — {reasons[:120]}"
        )

    current_problems = [
        {
            "problem_name": pn,
            "explanation": (_explain(pn))[:400],
            "supporting_utterance_span": None,
        }
        for pn in prev_names[:12]
    ]
    name_set = {cp["problem_name"] for cp in current_problems}
    picked = trace_main if trace_main in name_set else None
    if picked is None and previous_main_problem in name_set:
        picked = previous_main_problem
    if picked is None:
        picked = current_problems[0]["problem_name"]
    main_obj = {
        "problem_name": picked,
        "explanation": (
            f"carry_forward: center held on {picked} — {reasons[:220]}"
        )[:800],
        "supporting_utterance_span": None,
    }

    out = {
        "current_problems": current_problems,
        "main_problem": main_obj,
        "problem_attribute_entries": [],
        "problem_attribute_connections": [],
        "_agent2_carry_forward": True,
        "_agent2_carry_forward_reason": reasons[:2000],
    }
    validate_agent2(out)
    return out


def run_agent2(
    *, client: LLMClient, ctx: CallContext, inputs: Agent2Inputs,
) -> dict:
    assert ctx.call_role == "agent2_inference_v7"
    return client.generate_structured(
        ctx=ctx,
        system_prompt=build_system_prompt(),
        user_prompt=build_user_prompt(**inputs.__dict__),
        schema=AGENT2_SCHEMA,
        validator_extras=validate_agent2,
    )


# ---------------------------------------------------------------------------
# Self-test (validator only — no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    # 1. Empty turn passes.
    empty = {
        "current_problems": [],
        "main_problem": None,
        "problem_attribute_entries": [],
        "problem_attribute_connections": [],
    }
    validate_agent2(empty)

    # 2. Non-empty + valid passes.
    valid = {
        "current_problems": [
            {"problem_name": "academic_pressure", "explanation": "x",
             "supporting_utterance_span": None},
            {"problem_name": "sleep_problems", "explanation": "y",
             "supporting_utterance_span": None},
        ],
        "main_problem": {
            "problem_name": "academic_pressure",
            "explanation": "x", "supporting_utterance_span": None,
        },
        "problem_attribute_entries": [
            {"problem_name": "academic_pressure", "attribute_name": "perceived_severity",
             "inferred_information": "a", "concise_explanation": "b",
             "supporting_utterance_span": None},
            {"problem_name": "sleep_problems", "attribute_name": "triggers",
             "inferred_information": "c", "concise_explanation": "d",
             "supporting_utterance_span": None},
        ],
        "problem_attribute_connections": [
            {"problem_1": "academic_pressure", "attribute_1": "triggers",
             "problem_2": "sleep_problems", "attribute_2": "triggers",
             "relation_type": "shared_trigger",
             "connection_explanation": "shared driver",
             "supporting_utterance_span": None},
        ],
    }
    validate_agent2(valid)

    # 3. Empty current_problems with a non-empty entry → reject.
    bad1 = dict(empty, problem_attribute_entries=[{"problem_name": "x", "attribute_name": "y", "inferred_information": "z", "concise_explanation": "w", "supporting_utterance_span": None}])
    try:
        validate_agent2(bad1)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # 4. main_problem not in current_problems → reject.
    bad2 = {
        "current_problems": [
            {"problem_name": "academic_pressure", "explanation": "x",
             "supporting_utterance_span": None},
        ],
        "main_problem": {
            "problem_name": "sleep_problems",
            "explanation": "x", "supporting_utterance_span": None,
        },
        "problem_attribute_entries": [],
        "problem_attribute_connections": [],
    }
    try:
        validate_agent2(bad2)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # 5. Self-edge in connection → reject.
    bad3 = dict(valid, problem_attribute_connections=[
        {"problem_1": "academic_pressure", "attribute_1": "triggers",
         "problem_2": "academic_pressure", "attribute_2": "perceived_severity",
         "relation_type": "causal", "connection_explanation": "x",
         "supporting_utterance_span": None},
    ])
    try:
        validate_agent2(bad3)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # 6. Duplicate (problem, attr) → reject.
    bad4 = dict(valid, problem_attribute_entries=valid["problem_attribute_entries"] + [valid["problem_attribute_entries"][0]])
    try:
        validate_agent2(bad4)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # 7. Duplicate canonical connection → reject.
    bad5 = dict(valid, problem_attribute_connections=valid["problem_attribute_connections"] + [
        {"problem_1": "sleep_problems", "attribute_1": "triggers",
         "problem_2": "academic_pressure", "attribute_2": "triggers",
         "relation_type": "shared_trigger", "connection_explanation": "y",
         "supporting_utterance_span": None},
    ])
    try:
        validate_agent2(bad5)
        raise AssertionError("expected ValueError on duplicate canonical")
    except ValueError:
        pass

    # 8. Prompts render
    sp = build_system_prompt()
    assert "InferenceAgent" in sp
    assert "MAIN PROBLEM" in sp and "NOT over-sticky" in sp
    assert "academic_pressure" in sp
    up = build_user_prompt(
        current_message="hi", recent_turns=[],
        previous_active_problems=["work_stress", "general_anxiety"],
        previous_main_problem="work_stress",
    )
    assert "work_stress" in up
    assert "PREVIOUS MAIN" in up

    # Cold-start prompt
    up_cold = build_user_prompt(
        current_message="hi", recent_turns=[],
        previous_active_problems=[], previous_main_problem=None,
    )
    assert "cold-start" in up_cold

    print("agent2_inference_v7 self-test PASSED")


if __name__ == "__main__":
    _self_test()
