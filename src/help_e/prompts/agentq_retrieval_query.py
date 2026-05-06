"""V8 Agent Q — retrieval-query generator.

ONE call per turn (V8 only; not used by V7). Produces a single
``retrieval_query`` string that the dense retriever (MiniLM cosine over
the graph corpus) embeds and matches against attribute audits and
problem-problem connections.

Why a separate agent
--------------------
Agent Q operates on the *structured signals* the other agents have
already produced — user_intent + MI for the user, system_intent + MI
per active problem, and the current_problem cluster — rather than the
raw user message. It projects "what is the user expressing, and what
is the chatbot trying to do" into a search-friendly bag of theme
tokens, not a paraphrase of what the user said.

Why a separate agent (vs. extending Agent 1)
--------------------------------------------
Agent 1 produces ``user_intent_phrase`` for classification + MI choice.
Agent Q's job is different — search expansion. Distinct prompt, distinct
optimization target, different read of the same source. Could be folded
into Agent 1 (saves an LLM call) but kept separate here for cleaner
iteration.

Inputs (structured, no raw message)
-----------------------------------
  - user_intent + user_intent_phrase + mi_for_user_intent  (from Agent 1)
  - main_problem + main.system_intent + main.mi_for_system_intent
                                                            (from graph)
  - other_current_problems with their system_intent + mi_for_system_intent
                                                            (from graph)

Output
------
  { "retrieval_query": "<10-30 token bag-of-words>" }
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from typing import Any, Optional

from ..llm_client import CallContext, LLMClient, LLMStructuredError


# ---------------------------------------------------------------------------
# JSON schema
# ---------------------------------------------------------------------------


AGENTQ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["retrieval_query"],
    "properties": {
        "retrieval_query": {
            "type": "string",
            "minLength": 1,
            "maxLength": 300,
        },
    },
}


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def build_system_prompt() -> str:
    return textwrap.dedent("""\
        You are the QueryAgent of the v8 HELP-E pipeline.

        # YOUR ONE JOB
        Given the structured signals from the other agents — the user's
        intent + chosen MI technique, the chatbot's system_intent +
        chosen MI per active problem, and the current_problem cluster —
        produce a SINGLE retrieval query string that a dense retriever
        (MiniLM cosine over the user's graph corpus) will use to surface
        the most relevant past audits and problem-problem connections.

        # WHY THIS QUERY MATTERS
        The retriever indexes every audit entry and every connection
        entry from the user's chronological history. Your query is the
        only thing that decides which slices of that history land in
        front of the response model. A good query lets the retriever
        pull evidence that supports BOTH:
          - the user's expressed intent (e.g., "venting" → past
            venting moments, severity peaks, what helped before),
          - the system's nudge intent (e.g., "evoke" → past motivation
            statements, considered changes, near-decisions).

        # HOW TO COMPOSE THE QUERY
        - 10-30 tokens. Bag-of-words is fine; ordering doesn't affect
          dense scoring after pooling.
        - Include emotional/cognitive themes implied by the user_intent
          (venting → severity, exhaustion, frustration; deliberate →
          weighing, options, ambivalence; request_plan → coping,
          past attempts, capacity).
        - Include theme tokens aligned with the MI techniques chosen
          (complex_reflection → underlying themes; evoke → motivation,
          change-talk; affirmation → effort, strengths; advise →
          options, considerations).
        - Include the active problem names verbatim (these are graph
          keys — exact string matches help even with dense retrieval).
        - DO NOT paraphrase a user message; you don't have one. Work
          from the structured signals only.

        # EXAMPLES

        INPUTS:
          user_intent: express_emotion
          user_intent_phrase: "venting about exam-driven sleep loss"
          mi_for_user_intent: complex_reflection
          main_problem: sleep_problems
          main.system_intent: "explore what's keeping you awake"
          main.mi_for_system_intent: complex_reflection
          other_current_problems:
            - academic_pressure (system_intent_1line:
              "name the deadline weight before pushing")
        OUTPUT:
          retrieval_query: "sleep_problems academic_pressure exam stress
                            rumination triggers severity peaks
                            past venting moments insomnia onset
                            underlying exhaustion themes"

        INPUTS:
          user_intent: request_plan
          user_intent_phrase: "asking how to talk to manager"
          mi_for_user_intent: advise_with_permission
          main_problem: academic_pressure
          main.system_intent: "affirm agency in considering an ask"
          main.mi_for_system_intent: support
          other_current_problems: []
        OUTPUT:
          retrieval_query: "academic_pressure manager interaction
                            coping strategies past accommodation
                            self_efficacy agency moments effort
                            support evidence considered options"

        INPUTS:
          user_intent: deliberate_decision
          user_intent_phrase: "weighing whether to keep dating partner"
          mi_for_user_intent: complex_reflection
          main_problem: relationship_anxiety
          main.system_intent: "reflect both sides without nudging"
          main.mi_for_system_intent: complex_reflection
          other_current_problems: []
        OUTPUT:
          retrieval_query: "relationship_anxiety partner ambivalence
                            both sides moments perceived_severity
                            perceived_barriers self_efficacy decision
                            weighing past relational episodes"

        # OUTPUT (JSON only, nothing else)
        { "retrieval_query": "<a single bag-of-tokens query string>" }
    """)


def _format_other_block(others: list[dict]) -> str:
    if not others:
        return "  (none)"
    lines: list[str] = []
    for o in others:
        si = (o.get("system_intent_1line") or "(no system_intent set yet)").strip()
        mi = o.get("mi_for_system_intent") or "(none)"
        lines.append(
            f"  - {o['name']}: system_intent_1line=\"{si}\"  mi={mi}"
        )
    return "\n".join(lines)


def build_user_prompt(
    *,
    user_intent: str,
    user_intent_phrase: str,
    mi_for_user_intent: str,
    main_problem: Optional[str],
    main_system_intent: Optional[str],
    main_mi_for_system_intent: Optional[str],
    other_current_problems: list[dict],
) -> str:
    main_si_render = (
        main_system_intent.strip() if main_system_intent
        else "(no system_intent set yet)"
    )
    main_mi_render = main_mi_for_system_intent or "(none)"
    main_name_render = main_problem or "(none yet — fresh conversation)"
    return textwrap.dedent(f"""\
        # USER SIDE (Agent 1)
        user_intent: {user_intent}
        user_intent_phrase: "{user_intent_phrase}"
        mi_for_user_intent: {mi_for_user_intent}

        # MAIN PROBLEM (graph state)
        main_problem: {main_name_render}
        main.system_intent: "{main_si_render}"
        main.mi_for_system_intent: {main_mi_render}

        # OTHER CURRENT PROBLEMS
        {_format_other_block(other_current_problems)}

        Return the JSON object now.
    """)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def validate_agentq(out: dict) -> None:
    q = (out.get("retrieval_query") or "").strip()
    if not q:
        raise ValueError("retrieval_query must be non-empty")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@dataclass
class AgentQInputs:
    user_intent: str
    user_intent_phrase: str
    mi_for_user_intent: str
    main_problem: Optional[str]
    main_system_intent: Optional[str]
    main_mi_for_system_intent: Optional[str]
    other_current_problems: list[dict] = field(default_factory=list)
    # each: {name, system_intent_1line, mi_for_system_intent}


def _safe_fallback(*, inputs: AgentQInputs) -> dict:
    """Fallback used when Agent Q's LLM call fails. Builds a
    deterministic theme query from whatever structured signals are
    present, so retrieval never goes out empty.
    """
    parts: list[str] = []
    if inputs.user_intent_phrase:
        parts.append(inputs.user_intent_phrase.strip())
    if inputs.main_problem:
        parts.append(inputs.main_problem)
    for o in inputs.other_current_problems:
        if o.get("name"):
            parts.append(o["name"])
    if inputs.main_system_intent:
        parts.append(inputs.main_system_intent.strip())
    return {
        "retrieval_query": " ".join(p for p in parts if p) or "user evidence",
        "_fallback_default": True,
    }


def run_agentq(
    *, client: LLMClient, ctx: CallContext, inputs: AgentQInputs,
) -> dict:
    assert ctx.call_role == "agentq_retrieval_query"
    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(**inputs.__dict__),
            schema=AGENTQ_SCHEMA,
            validator_extras=lambda o: validate_agentq(o),
        )
    except LLMStructuredError:
        return _safe_fallback(inputs=inputs)


# ---------------------------------------------------------------------------
# Self-test (no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    # Schema validator
    valid = {"retrieval_query": "exam-driven sleep loss; insomnia coping"}
    validate_agentq(valid)
    try:
        validate_agentq({"retrieval_query": "   "})
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    try:
        validate_agentq({"retrieval_query": ""})
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # Prompts render and contain the expected scaffolding.
    sp = build_system_prompt()
    assert "QueryAgent" in sp
    assert "retrieval_query" in sp
    assert "user_intent" in sp
    assert "system_intent" in sp
    assert "EXAMPLES" in sp

    inputs = AgentQInputs(
        user_intent="express_emotion",
        user_intent_phrase="venting about exam-driven sleep loss",
        mi_for_user_intent="complex_reflection",
        main_problem="sleep_problems",
        main_system_intent="explore what's keeping you awake",
        main_mi_for_system_intent="complex_reflection",
        other_current_problems=[
            {"name": "academic_pressure",
             "system_intent_1line": "name the deadline weight before pushing",
             "mi_for_system_intent": "support"},
        ],
    )
    up = build_user_prompt(**inputs.__dict__)
    assert "express_emotion" in up
    assert "sleep_problems" in up
    assert "academic_pressure" in up
    assert "complex_reflection" in up
    assert "what's keeping you awake" in up

    # Cold-start: main_problem is None, no other current_problems.
    cold = AgentQInputs(
        user_intent="small_talk",
        user_intent_phrase="hi",
        mi_for_user_intent="support",
        main_problem=None,
        main_system_intent=None,
        main_mi_for_system_intent=None,
        other_current_problems=[],
    )
    up_cold = build_user_prompt(**cold.__dict__)
    assert "(none yet" in up_cold
    assert "(no system_intent set yet)" in up_cold

    # Fallback is well-shaped.
    fb = _safe_fallback(inputs=inputs)
    assert "sleep_problems" in fb["retrieval_query"]
    assert "academic_pressure" in fb["retrieval_query"]
    assert fb["_fallback_default"] is True
    fb_cold = _safe_fallback(inputs=cold)
    assert fb_cold["retrieval_query"]  # non-empty even when nothing to project

    print("agentq_retrieval_query self-test PASSED")


if __name__ == "__main__":
    _self_test()
