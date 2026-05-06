"""V3 InferenceAgent (Agent 2) — INFERENCE on the big model, no HBM.

Reads the current user utterance + a small dialogue window + the names
of previously-active problems (with the previous main_problem flagged
for sticky-main reasoning).

Emits FOUR structured fields:
  - current_problems            (which problems are active this turn —
                                 conservatively detected, see prompt)
  - main_problem                (sticky unless strong focus shift)
  - problem_evidence            (per-problem evidence from THIS turn:
                                 inferred_information + why +
                                 supporting_utterance_span)
  - problem_problem_connections (typed cross-problem links —
                                 {problem_1, problem_2, relation_type,
                                  why, supporting_utterance_span};
                                 NO attribute pairs because V3 has no
                                 attributes)

Differences from V7's InferenceAgent:
  - No ``problem_attribute_entries`` — V3 doesn't have HBM attributes;
    each active problem gets a single per-turn evidence entry instead.
  - Connections carry only ``relation_type`` + ``why`` + quote.
  - Sticky-main rule is identical to V7.

Differences from the old V3 (`v3_ttm_from_summary.py`):
  - The old V3 combined inference + summary updates in one big call.
    The new V3 separates them: this agent only extracts; ProblemAgent
    handles per-problem summary + TTM update separately.
  - Adds the same conservative-detection guidance V7 uses.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Any, Optional

from ..config import PROBLEM_VOCAB, RELATION_TYPES
from ..llm_client import CallContext, LLMClient, LLMStructuredError
from .common import format_dialog_turns
from .common_v6 import (
    problem_name_mapping_block,
    relation_types_block,
)


# ---------------------------------------------------------------------------
# JSON schema
# ---------------------------------------------------------------------------


AGENT2_V3_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "current_problems",
        "main_problem",
        "problem_evidence",
        "problem_problem_connections",
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
        "problem_evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "problem_name", "inferred_information", "why",
                    "supporting_utterance_span",
                ],
                "properties": {
                    "problem_name": {"type": "string", "enum": list(PROBLEM_VOCAB)},
                    "inferred_information": {"type": "string", "minLength": 1},
                    "why": {"type": "string", "minLength": 1},
                    "supporting_utterance_span": {"type": ["string", "null"]},
                },
            },
        },
        "problem_problem_connections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "problem_1", "problem_2",
                    "relation_type", "why",
                    "supporting_utterance_span",
                ],
                "properties": {
                    "problem_1": {"type": "string", "enum": list(PROBLEM_VOCAB)},
                    "problem_2": {"type": "string", "enum": list(PROBLEM_VOCAB)},
                    "relation_type": {"type": "string", "enum": list(RELATION_TYPES)},
                    "why": {"type": "string", "minLength": 1},
                    "supporting_utterance_span": {"type": ["string", "null"]},
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _conservative_detection_block() -> str:
    return textwrap.dedent("""
        # CONSERVATIVE PROBLEM DETECTION

        Detect a problem ONLY when the user's words clearly indicate it.
        A passing reference is not enough. Examples:

          - User says "work has been crazy" → not enough on its own to
            mark academic_pressure / work_stress as a current_problem;
            you'd need a description of WHAT's hard, OR a clear effect
            (e.g., "I haven't slept", "I keep snapping at people").
          - User says "I just couldn't focus today and I keep
            replaying everything" → enough to mark academic_pressure
            (or work_stress) as current; the description points at the
            problem clearly.
          - User says "I'm so tired" → not enough alone to mark
            sleep_problems unless the user names sleep specifically OR
            describes a sleep-quality issue ("waking up at 3am",
            "couldn't fall asleep", etc.).

        ``current_problems`` is the active set FOR THIS TURN. A problem
        recorded in earlier turns is NOT automatically still active —
        only include it if this turn's user message also touches it.

        # CURRENT_PROBLEMS — list ALL of them, no cap

        Always include EVERY problem from the vocabulary that is active
        or clearly continuing this turn. There is NO ceiling on how many
        you may list — if the user surfaces five threads at once, list
        five. Better to list a problem and have ``problem_evidence``
        stay empty for that problem than to drop the problem because
        no new evidence detail showed up. Problem detection lives in
        ``current_problems``; evidence extraction lives in
        ``problem_evidence``. They are independent jobs.

        # CURRENT_PROBLEMS WITHOUT NEW EVIDENCE IS NORMAL

        It is NORMAL and VALID for ``current_problems`` / ``main_problem``
        to be non-empty while ``problem_evidence`` is empty. This happens
        when the user:
          - is just venting / acknowledging an existing problem,
          - gives a brief continuation ("yeah", "still that"),
          - shifts focus among already-named problems without adding new
            description / mechanism / consequence detail,
          - restates the same pain you've already captured in earlier turns.
        In all of these cases: KEEP the problem in ``current_problems``;
        leave ``problem_evidence`` empty for that problem. Do NOT drop
        the problem just because this turn carries no new evidence.

        # MAIN PROBLEM — chosen from the WHOLE arc, not just this turn

        Choosing ``main_problem`` does NOT mean you shrink
        ``current_problems``. ``current_problems`` lists every active
        thread; ``main_problem`` names the single thread that is most
        central to the user RIGHT NOW.

        Decide ``main_problem`` from THREE sources together — none alone
        is decisive:
          1. CURRENT_USER_MESSAGE — what is the user pulling toward in
             this turn, which problem carries the heaviest concrete
             language / stakes / specifics?
          2. RECENT_DIALOGUE (the last several turns shown to you) —
             which problem has been the gravitational center of the
             conversation arc, even when this single turn is brief or
             ambiguous?
          3. PREVIOUSLY-ACTIVE PROBLEMS + the problem vocabulary — does
             the vocabulary's framing for any listed problem fit what
             the user is doing better than the previous main? When two
             candidates feel close, prefer the one whose vocabulary
             entry matches the user's wording / stakes / target of
             distress.

        Stickiness is a TIE-BREAKER, not a default. Keep
        ``previous_main_problem`` as ``main_problem`` only when (1)+(2)+(3)
        above leave it as the best match. Switch when another current
        problem clearly fits better under any of:
          - the user's current message centers stakes / specificity /
            emotion on a different listed problem,
          - the recent dialogue arc has migrated to another problem,
          - the previous main is no longer the closest vocabulary match
            for what the user is naming or implying,
          - the user explicitly redirects ("actually I need to talk
            about X").

        Do NOT carry the previous main forward just because it was
        previous. A reasonable shift each time the center drifts is
        GOOD; clinging to a stale main is the failure mode this rule
        guards against.

        Dialogue continuation: minimal replies ("yeah", "I don't know")
        may not name problems by themselves; in that case lean harder on
        RECENT_DIALOGUE + previously-active problems to keep a sensible
        main.

        Cold start (no previous_main_problem): pick the strongest center
        among this turn's live problems for ``main_problem``;
        ``main_problem`` may be null only when ``current_problems`` is
        empty.
    """)


def _per_problem_evidence_rules() -> str:
    return textwrap.dedent("""
        # PER-PROBLEM EVIDENCE (problem_evidence)

        For each ``current_problem`` that the user said something
        substantive about THIS TURN, emit ONE entry:

          - problem_name              (which problem the entry is about)
          - inferred_information       (what we extracted — 1 short
                                        sentence, e.g. "user reports
                                        three nights without sleep")
          - why                         (why we believe it — the
                                        observable signal in the user's
                                        words; used internally by the
                                        ProblemAgent to update the
                                        running summary, NOT shown to
                                        the response model)
          - supporting_utterance_span  (the verbatim user words; null
                                        if not literal)

        At most one entry per (current_problem) per turn — combine
        multiple observations about the same problem into one entry's
        ``inferred_information`` if needed. If the user's message
        doesn't say anything new about a current_problem (just brief
        mention), it's OK to emit zero entries for that problem.
    """)


def _connections_rules() -> str:
    return textwrap.dedent(f"""
        # PROBLEM-PROBLEM CONNECTIONS

        When two ``current_problems`` are linked by today's user words
        — either in a typed way (one drives or reinforces the other,
        they share a trigger, etc.) — emit a connection entry:

          - problem_1, problem_2  (canonical alphabetical order)
          - relation_type         (one of {list(RELATION_TYPES)})
          - why                   (short reasoning — internal,
                                   NOT shown to the response model)
          - supporting_utterance_span  (verbatim quote if any)

        If you cannot tell HOW two current_problems are related from
        the user's words, do not emit a connection — recording an
        unspecified link adds noise.

        {relation_types_block()}
    """)


def build_system_prompt() -> str:
    return textwrap.dedent(f"""\
        You are the InferenceAgent of the v3 HELP-E pipeline.

        # YOUR ONE JOB
        Read the user's current message + recent dialogue + the names
        of previously-active problems, then emit FOUR structured fields:

          1. current_problems            — which problems are active
                                            THIS turn (conservative).
          2. main_problem                — sticky unless strong shift.
          3. problem_evidence            — per-active-problem
                                            evidence: inferred + why +
                                            verbatim quote.
          4. problem_problem_connections — typed cross-problem links
                                            with relation_type + why +
                                            quote (NO attribute pairs;
                                            v3 has no attributes).

        You do NOT classify user_intent (the IntentAgent does).
        You do NOT update problem summaries (the ProblemAgent does,
        per problem, in a separate parallel call).
        You do NOT update edge summaries (the EdgeSummaryAgent does).
        You do NOT compute TTM stages (the ProblemAgent does).

        {_conservative_detection_block()}

        {_per_problem_evidence_rules()}

        {_connections_rules()}

        {problem_name_mapping_block()}

        # OUTPUT (JSON, nothing else)

        {{
          "current_problems": [
            {{"problem_name": "<from problem vocab>",
              "explanation": "<short>",
              "supporting_utterance_span": "<verbatim quote or null>"}},
            ...
          ],
          "main_problem":
            {{"problem_name": "...", "explanation": "...",
              "supporting_utterance_span": "..."}}
            OR null if nothing identifiable yet,
          "problem_evidence": [
            {{"problem_name": "...",
              "inferred_information": "...",
              "why": "...",
              "supporting_utterance_span": "..."}},
            ...
          ],
          "problem_problem_connections": [
            {{"problem_1": "...", "problem_2": "...",
              "relation_type": "<from relation types>",
              "why": "...",
              "supporting_utterance_span": "..."}},
            ...
          ]
        }}

        Return ONLY JSON, no prose.
    """)


def build_user_prompt(
    *,
    current_message: str,
    recent_turns: list[dict],
    previous_active_problems: list[str],
    previous_main_problem: Optional[str],
) -> str:
    prev_active = ", ".join(previous_active_problems) if previous_active_problems else "(none)"
    prev_main = previous_main_problem or "(none)"
    return textwrap.dedent(f"""\
        # PREVIOUSLY-ACTIVE PROBLEMS (for sticky-main reasoning)
        previous_main_problem: {prev_main}
        previously_seen_problems: {prev_active}

        # RECENT DIALOGUE (raw, last few turns)
        {format_dialog_turns(recent_turns)}

        # CURRENT USER MESSAGE
        {current_message}

        Return the JSON object now.
    """)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


_RELATION_SET = frozenset(RELATION_TYPES)
_PROBLEM_SET = frozenset(PROBLEM_VOCAB)


def validate_agent2_v3(out: dict) -> None:
    """Cross-field constraints beyond the schema:
      - main_problem (when present) must be in current_problems.
      - every problem in problem_evidence must be in current_problems.
      - every problem_1/problem_2 in connections must be in
        current_problems and the two must differ.
    """
    cps = {
        cp["problem_name"] for cp in out.get("current_problems") or []
    }
    main = out.get("main_problem")
    if main is not None:
        if main["problem_name"] not in cps:
            raise ValueError(
                f"main_problem {main['problem_name']!r} not in current_problems"
            )
    for e in out.get("problem_evidence") or []:
        if e["problem_name"] not in cps:
            raise ValueError(
                f"problem_evidence references problem "
                f"{e['problem_name']!r} not in current_problems"
            )
    seen_evidence: set[str] = set()
    for e in out.get("problem_evidence") or []:
        if e["problem_name"] in seen_evidence:
            raise ValueError(
                f"duplicate problem_evidence entry for {e['problem_name']!r}"
            )
        seen_evidence.add(e["problem_name"])
    for c in out.get("problem_problem_connections") or []:
        if c["problem_1"] not in cps or c["problem_2"] not in cps:
            raise ValueError(
                f"connection ({c['problem_1']}, {c['problem_2']}) endpoints "
                f"must both be in current_problems"
            )
        if c["problem_1"] == c["problem_2"]:
            raise ValueError(
                f"self-connection not allowed: {c['problem_1']!r}"
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@dataclass
class Agent2V3Inputs:
    current_message: str
    recent_turns: list[dict]
    previous_active_problems: list[str]
    previous_main_problem: Optional[str]


def _safe_fallback() -> dict:
    return {
        "current_problems": [],
        "main_problem": None,
        "problem_evidence": [],
        "problem_problem_connections": [],
        "_fallback_default": True,
    }


def run_agent2_v3(
    *, client: LLMClient, ctx: CallContext, inputs: Agent2V3Inputs,
) -> dict:
    assert ctx.call_role == "agent2_inference_v3"
    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(**inputs.__dict__),
            schema=AGENT2_V3_SCHEMA,
            validator_extras=validate_agent2_v3,
        )
    except LLMStructuredError:
        return _safe_fallback()


# ---------------------------------------------------------------------------
# Self-test (no LLM — schema + validator + prompt rendering)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    p_iter = iter(PROBLEM_VOCAB)
    p1, p2 = next(p_iter), next(p_iter)

    valid = {
        "current_problems": [
            {"problem_name": p1, "explanation": "x",
             "supporting_utterance_span": None},
            {"problem_name": p2, "explanation": "y",
             "supporting_utterance_span": None},
        ],
        "main_problem": {
            "problem_name": p1, "explanation": "x",
            "supporting_utterance_span": None,
        },
        "problem_evidence": [
            {"problem_name": p1,
             "inferred_information": "user named p1 explicitly",
             "why": "verbatim mention",
             "supporting_utterance_span": "p1 is hard"},
        ],
        "problem_problem_connections": [
            {"problem_1": p1, "problem_2": p2,
             "relation_type": "shared_trigger",
             "why": "same driver firing both",
             "supporting_utterance_span": None},
        ],
    }
    validate_agent2_v3(valid)

    # main outside current_problems → reject
    bad1 = dict(valid)
    bad1["main_problem"] = {"problem_name": "academic_pressure"
                            if p1 != "academic_pressure" else "sleep_problems",
                            "explanation": "x",
                            "supporting_utterance_span": None}
    if bad1["main_problem"]["problem_name"] not in {p1, p2}:
        try:
            validate_agent2_v3(bad1)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    # problem_evidence references unknown problem → reject
    bad2 = dict(valid)
    other = next(c for c in PROBLEM_VOCAB if c not in {p1, p2})
    bad2["problem_evidence"] = [
        {"problem_name": other, "inferred_information": "x", "why": "y",
         "supporting_utterance_span": None},
    ]
    try:
        validate_agent2_v3(bad2)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # duplicate problem_evidence entries → reject
    bad3 = dict(valid)
    bad3["problem_evidence"] = valid["problem_evidence"] + valid["problem_evidence"]
    try:
        validate_agent2_v3(bad3)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # self-connection → reject
    bad4 = dict(valid)
    bad4["problem_problem_connections"] = [
        {"problem_1": p1, "problem_2": p1,
         "relation_type": "shared_trigger", "why": "z",
         "supporting_utterance_span": None},
    ]
    try:
        validate_agent2_v3(bad4)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    sp = build_system_prompt()
    assert "InferenceAgent" in sp
    assert "v3" in sp
    assert "problem_evidence" in sp
    assert "problem_problem_connections" in sp
    # Must NOT mention HBM attributes.
    assert "perceived_severity" not in sp
    assert "level_attributes" not in sp

    up = build_user_prompt(
        current_message="I haven't slept in two days because of finals.",
        recent_turns=[],
        previous_active_problems=[p1],
        previous_main_problem=p1,
    )
    assert p1 in up
    assert "two days" in up

    print("agent2_inference_v3 self-test PASSED")


if __name__ == "__main__":
    _self_test()
