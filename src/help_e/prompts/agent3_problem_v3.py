"""V3 ProblemAgent (Phase 3) — per-problem summary + TTM + system_intent
in ONE small-model call.

Combines what V7 splits across AttributeAgent (3a) + StageAgent (3b)
into a single per-problem update call. Operates at the WHOLE-PROBLEM
level — V3 has no HBM attributes — so each call updates one problem's:

  - ``summary_text``  — chronological NL maintained turn-by-turn.
                        Conservative redundancy filtering (same spirit
                        as V7's AttributeAgent on attribute summaries).
  - ``current_ttm_stage`` + ``ttm_reasoning`` + ``ttm_change_confidence``
    Conservative TTM-update rules (analogous to V7's
    level_change_confidence): low-confidence updates DO NOT advance
    the stage; high or strong-medium updates do.
  - ``system_intent`` + ``mi_for_system_intent``
    Computed for every current_problem. Only the main_problem's flows
    downstream into the response prompt (other current_problems carry
    their previous turn's value forward into the 1-line stub block).

ONE call per current_problem (parallel × N). Inputs are scoped to the
problem the agent is updating: prior summary + TTM + system_intent on
the graph + the new audit entries this turn for THIS problem. Cross-
problem connections are handled separately by the EdgeSummaryAgent.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Any, Optional

from ..config import (
    MISC_CODES,
    PROBLEM_VOCAB,
    TTM_STAGES_V6,
)
from ..graph_v7 import LEVEL_CONFIDENCES
from ..llm_client import CallContext, LLMClient, LLMStructuredError
from ..mi_picker_v7 import shortlist_for_ttm_stage


# ---------------------------------------------------------------------------
# JSON schema
# ---------------------------------------------------------------------------


def _all_misc_codes() -> tuple[str, ...]:
    return tuple(MISC_CODES.keys())


AGENT3_PROBLEM_V3_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "problem_name",
        "summary_text",
        "current_ttm_stage",
        "ttm_reasoning",
        "ttm_change_confidence",
        "system_intent",
        "mi_for_system_intent",
        "useful",
    ],
    "properties": {
        "problem_name":      {"type": "string", "enum": list(PROBLEM_VOCAB)},
        "summary_text":      {"type": "string", "minLength": 1},
        "current_ttm_stage": {"type": "string", "enum": list(TTM_STAGES_V6)},
        "ttm_reasoning":     {"type": "string"},
        "ttm_change_confidence": {
            "type": "string", "enum": list(LEVEL_CONFIDENCES),
        },
        "system_intent":     {"type": "string", "minLength": 1},
        "mi_for_system_intent": {
            "anyOf": [
                {"type": "string", "enum": list(_all_misc_codes())},
                {"type": "null"},
            ],
        },
        "useful": {"type": "integer", "enum": [0, 1]},
    },
}


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _conservative_rules_block() -> str:
    return textwrap.dedent("""
        # CONSERVATIVE TTM-UPDATE RULES

        These rules govern when to ADVANCE current_ttm_stage and how to
        set ttm_change_confidence. The TTM stage should be a high-
        confidence claim about THIS person, not a running guess.

        TTM stage definitions:
          - precontemplation: user does not name a problem or is not
            considering change. Vague gripes count as
            precontemplation, not contemplation.
          - contemplation:    user explicitly names the problem AND
            shows ambivalence or weighing-up about doing something.
            Naming alone without weighing is still precontemplation.
          - preparation:      user has a SPECIFIC, CONCRETE plan or
            commits to a near-term action. "I should probably exercise
            more" is not preparation. "I'm going to ask my manager
            tomorrow" is preparation.
          - action:           user reports having taken a concrete step
            recently (within the last week or two), not merely "tried
            once and gave up." Reports of effort that didn't land
            count as preparation, not action.

        1. ADVANCE the stage only when the evidence makes the new
           stage UNAMBIGUOUS. A single ambiguous statement is not
           enough.
           - "I keep thinking about it" → CAN move precontemplation
             → contemplation if the user previously didn't name the
             problem and now does. confidence = high.
           - "Maybe I should ask for help" → DOES NOT move stage by
             itself. ttm_change_confidence = low; current_ttm_stage =
             prior stage.
           - "I'm going to talk to my manager on Monday" → CAN move
             contemplation → preparation. confidence = high if the
             plan is specific, medium if it's hedged.
           - "I asked my manager last week and we agreed on a
             timeline" → CAN move preparation → action.
             confidence = high.

        2. PATTERN OVERRIDES SINGLE INSTANCE. If the existing
           summary_text shows two or more prior turns pointing at the
           same direction AND this turn adds a third converging
           instance, advance the stage. confidence = high.

        3. WHEN UNCERTAIN, KEEP THE PRIOR STAGE. Default behavior under
           ambiguity: extend summary_text but leave current_ttm_stage
           untouched. Set ttm_change_confidence = low to record that
           you held the stage despite new content.

        4. REGRESSION IS ALLOWED but only with strong evidence. A user
           who reaches preparation, then gives up and won't talk about
           it, can return to contemplation. ttm_change_confidence = high
           for that move only when the user explicitly says they're
           backing off.

        5. ttm_change_confidence values:
           - "high"   = unambiguous turn signal OR pattern of ≥3
                        converging instances.
           - "medium" = pattern of 2 instances or strong-but-debatable
                        signal.
           - "low"    = ambiguous or single weak instance — DO NOT
                        advance the stage; keep prior current_ttm_stage.

        # SUMMARY_TEXT CONSTRUCTION

        - Chronological NL paragraph spanning this problem's whole
          history. Each turn that added something gets a turn-anchored
          sentence (e.g., "s1.t4: ...").
        - Append THIS turn as a new sentence. Do NOT rewrite earlier
          sentences.
        - SKIP REDUNDANCY. If today's evidence merely restates what's
          already in the summary, append a single short note like
          "sS.tT: restated — same point from another angle." Do NOT
          repeat the content. Set useful = 0.
        - If today adds a genuinely new dimension (a new aspect of
          the problem, a new trigger, a new attempt, a clearer
          mechanism, etc.), write a short sentence capturing that
          dimension. Set useful = 1.
        - Stay tight (~150 words even after many turns). Compress —
          but never at the cost of losing a distinct mechanism the
          user voiced.
        - You may quote 1 short verbatim user phrase per turn-sentence
          when the literal words add something.
        - Speak in plain language about what's happening for the user.
          Do not introduce typed-attribute jargon; describe the
          problem the way the user would talk about it.

        # SYSTEM_INTENT + MI_FOR_SYSTEM_INTENT

        Write a one-sentence ``system_intent`` describing what the bot
        wants to nudge for this problem GIVEN the new TTM stage:

          - precontemplation: build awareness; reflect what the user is
            in; do not push for plans. system_intent ≈ "name the weight
            of this without pushing for a plan yet."
          - contemplation:    surface trade-offs, evoke the user's own
            reasons; do not impose. system_intent ≈ "evoke both sides;
            keep the choice the user's."
          - preparation:      affirm specificity of the plan; check on
            the next concrete step. system_intent ≈ "affirm the
            specificity of the plan and ask about the next step."
          - action:           reinforce effort; check fit; resist over-
            advising. system_intent ≈ "reinforce the effort and ask
            what made it land for them."

        Pick ``mi_for_system_intent`` from the shortlist for the new
        TTM stage (provided in the user prompt). Use ``null`` only if
        no shortlist code fits.
    """)


def build_system_prompt() -> str:
    return textwrap.dedent(f"""\
        You are the ProblemAgent of the v3 HELP-E pipeline. You handle
        ONE problem per call. Your job is to update — in a single
        small-model JSON output — that problem's running summary, its
        TTM stage (with explicit confidence), and a one-line
        system_intent + chosen MI technique.

        You do NOT touch other problems. You do NOT update edge
        summaries (the EdgeSummaryAgent does, in a parallel call). You
        do NOT classify user_intent (the IntentAgent does). You do NOT
        write the user-facing response (the ResponseAgent does).

        {_conservative_rules_block()}

        # OUTPUT (JSON, nothing else)

        {{
          "problem_name": "<the problem you are updating>",
          "summary_text": "<full chronological NL paragraph after this turn>",
          "current_ttm_stage": "<precontemplation | contemplation | preparation | action>",
          "ttm_reasoning": "<short — why this stage>",
          "ttm_change_confidence": "<high | medium | low>",
          "system_intent": "<one sentence — what to nudge for this problem this turn>",
          "mi_for_system_intent": "<MISC code from the shortlist for the new TTM stage, or null>",
          "useful": <0 or 1>
        }}

        Return ONLY JSON. No prose.
    """)


def _format_existing_state(rec: dict) -> str:
    return textwrap.dedent(f"""\
          summary_text:
            {rec.get("summary_text") or "(none — first time this problem appears)"}
          current_ttm_stage: {rec.get("current_ttm_stage", "precontemplation")}
          ttm_reasoning:    {rec.get("ttm_reasoning") or "(none)"}
          system_intent:    {rec.get("system_intent") or "(none)"}
    """).rstrip()


def _format_new_evidence(items: list[dict]) -> str:
    if not items:
        return "(none — no new evidence about this problem this turn)"
    out: list[str] = []
    for it in items:
        out.append(textwrap.dedent(f"""\
              s{it["session_id"]}.t{it["turn_id"]}:
                inferred: {it["inferred_information"]}
                why:      {it["why"]}
                quote:    "{it.get("supporting_utterance_span") or "(implied)"}"
        """).rstrip())
    return "\n\n".join(out)


def _format_misc_shortlist(stage_specific: list[dict]) -> str:
    """Render the MISC shortlist for a given TTM stage. Stage may shift
    inside the agent's reasoning; we include shortlists for ALL stages
    so the agent can pick after deciding the new stage.
    """
    out: list[str] = ["Per-stage MI shortlists. Pick mi_for_system_intent FROM the row matching the NEW current_ttm_stage you decide on:"]
    for stage in TTM_STAGES_V6:
        codes = [c["code"] for c in shortlist_for_ttm_stage(stage)]
        out.append(f"  - {stage}: {', '.join(codes)}")
    return "\n".join(out)


def build_user_prompt(
    *,
    problem_name: str,
    is_main_problem: bool,
    current_session: int,
    current_turn: int,
    existing_state: dict,
    new_evidence: list[dict],
) -> str:
    main_marker = "MAIN PROBLEM" if is_main_problem else "ACTIVE (not main)"
    return textwrap.dedent(f"""\
        PROBLEM: {problem_name}  ({main_marker})
        TURN: session {current_session}, turn {current_turn}

        EXISTING STATE FOR THIS PROBLEM:
        {_format_existing_state(existing_state)}

        NEW EVIDENCE THIS TURN (from the InferenceAgent):
        {_format_new_evidence(new_evidence)}

        {_format_misc_shortlist([])}

        Return the JSON object now.
    """)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def validate_agent3_problem_v3(
    out: dict,
    *,
    expected_problem: Optional[str] = None,
) -> None:
    if expected_problem and out["problem_name"] != expected_problem:
        raise ValueError(
            f"expected problem_name {expected_problem!r}, got {out['problem_name']!r}"
        )
    if not out["summary_text"].strip():
        raise ValueError("empty summary_text")
    if not out["system_intent"].strip():
        raise ValueError("empty system_intent")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@dataclass
class Agent3ProblemV3Inputs:
    problem_name: str
    is_main_problem: bool
    current_session: int
    current_turn: int
    existing_state: dict   # {summary_text, current_ttm_stage,
                            #  ttm_reasoning, system_intent}
    new_evidence: list[dict]  # [{session_id, turn_id,
                               #  inferred_information, why,
                               #  supporting_utterance_span}, ...]


def _safe_fallback(inputs: Agent3ProblemV3Inputs) -> dict:
    """Carry the prior state forward if the LLM call fails. Append a
    deterministic note to summary_text so we never lose the new turn's
    evidence chronology. Confidence = low so we never accidentally
    advance TTM via the fallback.
    """
    prior_summary = (inputs.existing_state.get("summary_text") or "").strip()
    prior_stage = inputs.existing_state.get("current_ttm_stage", "precontemplation")
    prior_reason = inputs.existing_state.get("ttm_reasoning", "")
    prior_si = inputs.existing_state.get("system_intent") or "explore what's underneath without pushing"
    prior_mi = inputs.existing_state.get("mi_for_system_intent") or "complex_reflection"

    appended: list[str] = []
    for e in inputs.new_evidence:
        anchor = f"s{e['session_id']}.t{e['turn_id']}"
        body = (e.get("inferred_information") or "").strip()
        appended.append(f"{anchor}: {body}" if body else f"{anchor}: (no inference)")
    new_summary = (prior_summary + " " + " ".join(appended)).strip()
    if not new_summary:
        new_summary = "(no content)"

    return {
        "problem_name": inputs.problem_name,
        "summary_text": new_summary,
        "current_ttm_stage": prior_stage,
        "ttm_reasoning": prior_reason or "fallback: prior reasoning carried forward",
        "ttm_change_confidence": "low",
        "system_intent": prior_si,
        "mi_for_system_intent": prior_mi,
        "useful": 0,
        "_fallback_default": True,
    }


def run_agent3_problem_v3(
    *, client: LLMClient, ctx: CallContext, inputs: Agent3ProblemV3Inputs,
) -> dict:
    assert ctx.call_role == "agent3_problem_v3"
    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(
                problem_name=inputs.problem_name,
                is_main_problem=inputs.is_main_problem,
                current_session=inputs.current_session,
                current_turn=inputs.current_turn,
                existing_state=inputs.existing_state,
                new_evidence=inputs.new_evidence,
            ),
            schema=AGENT3_PROBLEM_V3_SCHEMA,
            validator_extras=lambda o: validate_agent3_problem_v3(
                o, expected_problem=inputs.problem_name,
            ),
        )
    except LLMStructuredError:
        return _safe_fallback(inputs)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _self_test() -> None:
    p1 = next(iter(PROBLEM_VOCAB))

    valid = {
        "problem_name": p1,
        "summary_text": "s1.t1: user named the problem; severity weight is high",
        "current_ttm_stage": "contemplation",
        "ttm_reasoning": "user weighed both sides without committing",
        "ttm_change_confidence": "medium",
        "system_intent": "evoke both sides; keep the choice with the user",
        "mi_for_system_intent": "complex_reflection",
        "useful": 1,
    }
    validate_agent3_problem_v3(valid, expected_problem=p1)

    # Wrong problem_name → reject
    try:
        validate_agent3_problem_v3(valid, expected_problem="wrong_problem")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # Empty summary → reject
    bad = dict(valid, summary_text="   ")
    try:
        validate_agent3_problem_v3(bad)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # Empty system_intent → reject
    bad2 = dict(valid, system_intent="")
    try:
        validate_agent3_problem_v3(bad2)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # Prompts render
    sp = build_system_prompt()
    assert "ProblemAgent" in sp
    assert "v3" in sp
    assert "ttm_change_confidence" in sp
    # No HBM language
    assert "perceived_severity" not in sp
    assert "level_attributes" not in sp

    up = build_user_prompt(
        problem_name=p1, is_main_problem=True,
        current_session=1, current_turn=2,
        existing_state={
            "summary_text": "s1.t1: user named the problem.",
            "current_ttm_stage": "precontemplation",
            "ttm_reasoning": "first mention",
            "system_intent": "name the weight without pushing",
        },
        new_evidence=[
            {"session_id": 1, "turn_id": 2,
             "inferred_information": "user weighed pros/cons of acting",
             "why": "deliberate-language",
             "supporting_utterance_span": "I'm not sure if it'd help"},
        ],
    )
    assert p1 in up
    assert "MAIN PROBLEM" in up
    assert "I'm not sure" in up
    assert "precontemplation" in up
    assert "Per-stage MI shortlists" in up

    # Fallback
    inputs = Agent3ProblemV3Inputs(
        problem_name=p1, is_main_problem=True,
        current_session=1, current_turn=2,
        existing_state={
            "summary_text": "s1.t1: prior.",
            "current_ttm_stage": "contemplation",
            "ttm_reasoning": "prior",
            "system_intent": "prior intent",
            "mi_for_system_intent": "support",
        },
        new_evidence=[
            {"session_id": 1, "turn_id": 2,
             "inferred_information": "more on this", "why": "z",
             "supporting_utterance_span": None},
        ],
    )
    fb = _safe_fallback(inputs)
    assert fb["current_ttm_stage"] == "contemplation"     # carried forward
    assert fb["ttm_change_confidence"] == "low"            # never advance via fallback
    assert "s1.t1: prior" in fb["summary_text"]
    assert "s1.t2: more on this" in fb["summary_text"]

    print("agent3_problem_v3 self-test PASSED")


if __name__ == "__main__":
    _self_test()
