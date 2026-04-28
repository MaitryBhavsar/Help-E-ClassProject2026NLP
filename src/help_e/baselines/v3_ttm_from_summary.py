"""v3 — text-summary + TTM-from-summary baseline (v6-aligned).

The original v3 used per-problem text summaries plus a separate TTM
inference call, with old MI vocabulary (T1–T12) and old TTM enum
(5 stages). Under the v6 redesign, this rewrite:

  - shares the v6 simulator (mind1_v6 + SimulatorProfile + session_context)
  - uses MISC vocabulary (10 selectable codes, 6 anti-patterns)
  - uses TTM_STAGES_V6 (4 stages, no maintenance)
  - produces the 3-field {reasoning, evidence_used, final_response}
    output so MITI / TTM transition rate / ESC metrics apply uniformly

Per-turn pipeline:
  1. ONE LLM call to detect/update per-problem text summaries +
     infer per-problem TTM stage (combines what the old v3 did across
     three calls — extraction, summary update, TTM inference).
  2. Pick MAIN problem from the summaries (highest-priority active).
  3. Compute MISC candidate bundle from the inferred TTM stage —
     COMMON + STAGE_SPECIFIC, exactly the same selector logic as v6.
  4. Call run_response_v6 with the candidate bundle and the same
     graph object v6 uses (kept empty in v3 — no HBM attributes).

Per-problem state (text summary + TTM stage) is attached to the graph
object as `graph._v3_state` so it persists across turns within a session.
The graph itself stays empty (no problems written into it) so the
shared persistence layout still works.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import (
    MISC_CODES,
    PROBLEM_VOCAB,
    TTM_STAGES_V6,
    USER_INTENTS_V6,
)
from ..graph_v6 import ProblemGraphV6
from ..instruction_response_simple import run_response_simple
from ..llm_client import CallContext, LLMClient
from ..mi_selector_v6 import select_candidates_v6
from ..prompts.common import PROJECT_IDENTITY, format_dialog_turns
from ..prompts.common_v6 import (
    problem_name_mapping_block,
    ttm_guidance_block,
)


# ---------------------------------------------------------------------------
# Per-problem text-summary state (attached to the graph object)
# ---------------------------------------------------------------------------


@dataclass
class _ProblemSummary:
    name: str                         # one of PROBLEM_VOCAB
    summary: str                      # 2–4 sentence rolling description
    ttm_stage: str                    # one of TTM_STAGES_V6
    last_active_turn: int = 0
    history: list[tuple[int, str]] = field(default_factory=list)  # (turn_id, stage)


@dataclass
class _V3State:
    summaries: dict[str, _ProblemSummary] = field(default_factory=dict)
    main_problem: Optional[str] = None


def _get_state(graph: ProblemGraphV6) -> _V3State:
    state = getattr(graph, "_v3_state", None)
    if state is None:
        state = _V3State()
        graph._v3_state = state  # type: ignore[attr-defined]
    return state


# ---------------------------------------------------------------------------
# Combined extraction + summary + TTM call (one LLM call per turn)
# ---------------------------------------------------------------------------


_V3_EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["user_intent", "problems", "main_problem"],
    "properties": {
        "user_intent": {"type": "string", "enum": list(USER_INTENTS_V6)},
        "problems": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["problem_name", "summary", "ttm_stage"],
                "properties": {
                    "problem_name": {"type": "string", "enum": list(PROBLEM_VOCAB)},
                    "summary": {"type": "string", "minLength": 1},
                    "ttm_stage": {"type": "string", "enum": list(TTM_STAGES_V6)},
                },
            },
        },
        "main_problem": {
            "oneOf": [
                {"type": "null"},
                {"type": "string", "enum": list(PROBLEM_VOCAB)},
            ],
        },
    },
}


def _v3_extract_system() -> str:
    return textwrap.dedent(f"""\
        {PROJECT_IDENTITY}

        You are the v3 extraction agent. On every user turn you produce:
          1. user_intent (one of {list(USER_INTENTS_V6)})
          2. A list of problems active in this conversation (subset of
             the 20-vocabulary).
          3. For each active problem: a 2–4 sentence rolling SUMMARY
             that captures what the user has said about it so far, and
             a TTM stage (one of {list(TTM_STAGES_V6)}) inferred from
             that summary's behavioral cues.
          4. A single main_problem (one of those problem names, or null
             if no clear focus).

        # PROBLEM-NAME MAPPING
        {problem_name_mapping_block()}

        # TTM STAGE GUIDANCE (from the rolling summary)
        {ttm_guidance_block()}

        # SUMMARY UPDATE RULE
        Carry the prior summary forward when a problem is still active
        and incorporate new content from this turn. Drop a problem
        from the list when it has clearly faded.

        # OUTPUT SHAPE
        {{
          "user_intent": "express_emotion",
          "problems": [
            {{
              "problem_name": "academic_pressure",
              "summary": "User is in finals week, has had three nights of poor sleep, feels the workload is crushing.",
              "ttm_stage": "contemplation"
            }}
          ],
          "main_problem": "academic_pressure"
        }}

        Return ONLY valid JSON matching the schema. No prose.
    """)


def _v3_extract_user(
    *,
    user_message: str,
    recent_turns: list[dict],
    prior_summaries: dict[str, _ProblemSummary],
) -> str:
    if prior_summaries:
        prior_block = "\n".join(
            f"  - {p.name} (current_ttm_stage={p.ttm_stage}): {p.summary}"
            for p in prior_summaries.values()
        )
    else:
        prior_block = "  (none yet)"
    return textwrap.dedent(f"""\
        PRIOR PROBLEM SUMMARIES (from earlier turns this conversation):
        {prior_block}

        RECENT DIALOGUE:
        {format_dialog_turns(recent_turns)}

        CURRENT USER MESSAGE:
        {user_message}

        Update the problem summaries and TTM stages now.
    """)


def _safe_v3_fallback(prior_state: _V3State) -> dict:
    """Neutral fallback so the turn can proceed. Carries forward any
    existing summaries unchanged."""
    return {
        "user_intent": "small_talk",
        "problems": [
            {
                "problem_name": p.name,
                "summary": p.summary,
                "ttm_stage": p.ttm_stage,
            }
            for p in prior_state.summaries.values()
        ],
        "main_problem": prior_state.main_problem,
        "_fallback_default": True,
    }


def _run_v3_extract(
    *,
    client: LLMClient,
    ctx: CallContext,
    user_message: str,
    recent_turns: list[dict],
    state: _V3State,
) -> dict:
    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=_v3_extract_system(),
            user_prompt=_v3_extract_user(
                user_message=user_message,
                recent_turns=recent_turns,
                prior_summaries=state.summaries,
            ),
            schema=_V3_EXTRACT_SCHEMA,
        )
    except Exception:
        return _safe_v3_fallback(state)


# ---------------------------------------------------------------------------
# State application + candidate selection
# ---------------------------------------------------------------------------


def _apply_extract_to_state(
    state: _V3State, extract_out: dict, *, turn_id: int,
) -> tuple[Optional[str], Optional[str], list[dict]]:
    """Update state from extract output. Returns
    `(main_name, main_ttm_stage, ttm_updates)` where ttm_updates lists
    per-problem stage changes detected this turn (so the v6 metric for
    TTM transition rate works on v3 too).
    """
    ttm_updates: list[dict] = []
    new_problem_names: set[str] = set()
    for prob in extract_out.get("problems") or []:
        name = prob["problem_name"]
        new_stage = prob["ttm_stage"]
        new_summary = prob["summary"]
        new_problem_names.add(name)
        existing = state.summaries.get(name)
        old_stage = existing.ttm_stage if existing else None
        if existing is None:
            state.summaries[name] = _ProblemSummary(
                name=name, summary=new_summary, ttm_stage=new_stage,
                last_active_turn=turn_id,
                history=[(turn_id, new_stage)],
            )
        else:
            existing.summary = new_summary
            existing.ttm_stage = new_stage
            existing.last_active_turn = turn_id
            existing.history.append((turn_id, new_stage))
        ttm_updates.append({
            "problem_name": name,
            "old_ttm_stage": old_stage,
            "new_ttm_stage": new_stage,
            "reasoning": "v3 inferred from rolling summary",
        })

    main_name = extract_out.get("main_problem")
    if main_name and main_name not in state.summaries:
        main_name = None
    state.main_problem = main_name

    main_ttm_stage = (
        state.summaries[main_name].ttm_stage if main_name else None
    )
    return main_name, main_ttm_stage, ttm_updates


def _candidate_bundle_for_v3(
    *, main_name: Optional[str], main_ttm_stage: Optional[str],
    user_intent: str,
) -> dict:
    """Reuse the v6 selector by constructing a minimal in-memory graph
    that carries just the main problem + its TTM stage. v3 doesn't have
    a real graph but the selector only reads stage from the main, so
    this is faithful.
    """
    g = ProblemGraphV6(profile_id="_v3_selector_only")
    if main_name:
        g.get_or_create_problem(main_name, first_mentioned=(1, 1))
        if main_ttm_stage in TTM_STAGES_V6:
            g.set_ttm_stage(main_name, main_ttm_stage)
    return select_candidates_v6(
        graph=g,
        main_problem_name=main_name,
        user_intent=user_intent if user_intent in USER_INTENTS_V6 else "small_talk",
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def v3_turn_fn(
    *,
    client: LLMClient,
    profile_id: str,
    system: str = "v3",
    session_id: int,
    turn_id: int,
    user_message: str,
    recent_turns: list[dict],
    last_system_message: Optional[str] = None,
    prior_session_summary: Optional[str] = None,    # unused (§9.2)
    graph: ProblemGraphV6,                           # used only for v3 state attachment
    last_n_turns: int = 5,
    previous_turn_traces: Optional[list[dict]] = None,
) -> dict:
    """One v3 turn. Same signature as `v6_turn_fn` so the v6 driver
    can call either interchangeably.

    Pipeline:
      1. v3_extract LLM call: per-problem summary update + TTM stage
         inference + intent + main_problem (combined into one call).
      2. Apply to per-problem state attached to the graph.
      3. Select MISC candidates from inferred main TTM stage (same
         selector as v6).
      4. Call run_response_v6 with empty graph (no HBM evidence) +
         the inferred candidate bundle.
    """
    state = _get_state(graph)

    # --- Step 1: combined extract+summary+TTM call ---
    ext_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="v3_extract",
    )
    extract_out = _run_v3_extract(
        client=client, ctx=ext_ctx,
        user_message=user_message,
        recent_turns=recent_turns,
        state=state,
    )

    # --- Step 2: apply to state ---
    main_name, main_ttm_stage, ttm_updates = _apply_extract_to_state(
        state, extract_out, turn_id=(session_id - 1) * 1000 + turn_id,
    )
    user_intent = extract_out.get("user_intent", "small_talk")

    # --- Step 3: candidate bundle from inferred TTM stage ---
    candidate_bundle = _candidate_bundle_for_v3(
        main_name=main_name, main_ttm_stage=main_ttm_stage,
        user_intent=user_intent,
    )

    # --- Step 4: response (3-field schema) ---
    from .v6_full import (
        _collect_past_two_turns,
        _extract_misc_codes_from_reasoning,
    )
    past_two_turns = _collect_past_two_turns(previous_turn_traces or [])

    rsp_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="response_simple",
    )
    response_out = run_response_simple(
        client=client, ctx=rsp_ctx,
        graph=graph,                           # empty in v3 → no HBM evidence
        candidate_bundle=candidate_bundle,
        past_two_turns=past_two_turns,
        recent_turns=recent_turns,
        current_user_message=user_message,
    )

    chosen_misc_codes = _extract_misc_codes_from_reasoning(
        response_out.get("reasoning", "") or ""
    )

    trace = {
        "main_problem": main_name,
        "current_problems": [p["problem_name"] for p in extract_out.get("problems") or []],
        "user_intent": user_intent,
        "ttm_stage": main_ttm_stage,
        "transition_target": candidate_bundle.get("transition_target"),
        "all_candidate_codes": candidate_bundle.get("all_candidate_codes") or [],
        "chosen_misc_codes": chosen_misc_codes,
        "turn_scope_level_attrs": [],
        "level_updates": [],
        "ttm_updates": ttm_updates,
        "cooc_added": 0,
        "attr_conn_added": 0,
    }

    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "user_message": user_message,
        # v3 doesn't run the v6 inference call; emit a v3-shaped stub so
        # downstream loaders/metrics still find what they expect.
        "inference": {
            "user_intent": {"intent": user_intent, "confidence": "medium",
                            "explanation": "v3 combined extract", "supporting_utterance_span": None},
            "current_problems": [
                {"problem_name": p["problem_name"], "is_new_problem": False,
                 "matched_existing_problem_name": p["problem_name"],
                 "explanation": "v3 summary-based", "supporting_utterance_span": None}
                for p in extract_out.get("problems") or []
            ],
            "main_problem": (
                {"problem_name": main_name, "explanation": "v3 main pick",
                 "supporting_utterance_span": None} if main_name else None
            ),
            "problem_attribute_entries": [],
            "problem_cooccurrence_connections": [],
            "problem_attribute_connections": [],
            "_v3_summary_only": True,
            "_fallback_default": extract_out.get("_fallback_default", False),
        },
        "recompute": {
            "attribute_level_updates": [],
            "ttm_stage_updates": ttm_updates,
            "_v3_no_recompute": True,
        },
        "bundle": None,
        "candidate_bundle": candidate_bundle,
        "past_two_turns": past_two_turns,
        "response": response_out,
        "trace": trace,
    }


# ---------------------------------------------------------------------------
# Self-test (no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    # State attaches to the graph and survives across turns.
    g = ProblemGraphV6(profile_id="T")
    s1 = _get_state(g)
    s2 = _get_state(g)
    assert s1 is s2

    # Apply an extract output and check state, ttm_updates.
    state = _get_state(ProblemGraphV6(profile_id="T2"))
    extract = {
        "user_intent": "express_emotion",
        "problems": [
            {"problem_name": "academic_pressure",
             "summary": "Finals week, three nights of poor sleep.",
             "ttm_stage": "contemplation"},
        ],
        "main_problem": "academic_pressure",
    }
    main, main_stage, updates = _apply_extract_to_state(state, extract, turn_id=1)
    assert main == "academic_pressure"
    assert main_stage == "contemplation"
    assert updates == [{
        "problem_name": "academic_pressure",
        "old_ttm_stage": None,
        "new_ttm_stage": "contemplation",
        "reasoning": "v3 inferred from rolling summary",
    }]
    assert "academic_pressure" in state.summaries
    assert state.summaries["academic_pressure"].history == [(1, "contemplation")]

    # Second turn: stage advances to preparation.
    extract2 = {
        "user_intent": "report_action",
        "problems": [
            {"problem_name": "academic_pressure",
             "summary": "Finals week, tried a 25-min study block, helped some.",
             "ttm_stage": "preparation"},
        ],
        "main_problem": "academic_pressure",
    }
    _, stage2, updates2 = _apply_extract_to_state(state, extract2, turn_id=2)
    assert stage2 == "preparation"
    assert updates2[0]["old_ttm_stage"] == "contemplation"
    assert state.summaries["academic_pressure"].history == [(1, "contemplation"), (2, "preparation")]

    # Candidate bundle for contemplation gives common + stage_specific.
    cb = _candidate_bundle_for_v3(
        main_name="academic_pressure", main_ttm_stage="contemplation",
        user_intent="express_emotion",
    )
    assert cb["main_problem"] == "academic_pressure"
    assert cb["ttm_stage"] == "contemplation"
    assert cb["common_candidates"] and cb["stage_specific_candidates"]
    common_codes = {c["code"] for c in cb["common_candidates"]}
    stage_codes = {c["code"] for c in cb["stage_specific_candidates"]}
    assert "support" in common_codes and "facilitate" in common_codes
    assert "evoke" in stage_codes  # contemplation-stage selectable

    # Cold-start (no main): only common candidates.
    cb_cold = _candidate_bundle_for_v3(
        main_name=None, main_ttm_stage=None, user_intent="small_talk",
    )
    assert cb_cold["main_problem"] is None
    assert cb_cold["ttm_stage"] is None
    assert cb_cold["stage_specific_candidates"] == []

    # Fallback shape carries forward summaries.
    fb = _safe_v3_fallback(state)
    assert fb["_fallback_default"] is True
    assert any(p["problem_name"] == "academic_pressure" for p in fb["problems"])

    # Schema validates a well-formed example.
    from jsonschema import Draft202012Validator
    Draft202012Validator(_V3_EXTRACT_SCHEMA).validate(extract)
    Draft202012Validator(_V3_EXTRACT_SCHEMA).validate({
        "user_intent": "small_talk", "problems": [], "main_problem": None,
    })

    print("v3_ttm_from_summary (v6-aligned) self-test PASSED")


if __name__ == "__main__":
    _self_test()
