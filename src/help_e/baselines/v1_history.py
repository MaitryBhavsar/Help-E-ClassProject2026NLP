"""v1 — history-only baseline (v6-aligned).

The original v1 baseline (§7) only used dialogue history and picked from
a fixed pre-redesign T1/T2 vocabulary. Under the v6 redesign, the
ablation contract requires that ALL systems share the same simulator
(mind1_v6 + SimulatorProfile + session_context), the same MISC code
vocabulary (10 selectable + 6 anti-patterns), the same TTM enum
(4 stages), and the same 3-field response output shape — so MITI 4.2
+ TTM transition rate + ESC metrics apply uniformly.

What v1 keeps from the redesign:
  - shared simulator stack (mind1_v6, session_context, persona_update_v6,
    miti_judge_v6, mind3) via session_driver_v6
  - same MISC vocabulary — but the candidate pool is ALL 10 selectable
    codes flat (no TTM-stage targeting; this is the ablation point)
  - same `run_response_v6` prompt + 3-field {reasoning, evidence_used,
    final_response} output

What v1 strips out:
  - inference (no problem-graph extraction)
  - recompute (no TTM stage tracking)
  - graph evidence (graph stays empty; EVIDENCE block in the prompt is
    just persona + recent_turns)

Per-turn cost: 1 LLM call (response_v6 only) + the shared simulator
overhead (mind1_v6).
"""
from __future__ import annotations

from typing import Optional

from ..config import (
    INTENT_ENTRY_STYLE_V6,
    MISC_CODES,
    USER_INTENTS_V6,
)
from ..graph_v6 import ProblemGraphV6
from ..instruction_response_simple import run_response_simple
from ..llm_client import CallContext, LLMClient


def _flat_candidate_bundle(user_intent: str) -> dict:
    """Build a candidate_bundle as if every MISC code were COMMON.

    No TTM stage → no stage-specific candidates. Mirrors the shape that
    `mi_selector_v6.select_candidates_v6` returns so the response prompt
    + downstream metrics keep working unchanged.
    """
    if user_intent not in USER_INTENTS_V6:
        user_intent = "small_talk"
    common = [
        {
            "code": code,
            "label": spec["label"],
            "what": spec["what"],
            "transition_fn": spec["transition_fn"],
        }
        for code, spec in MISC_CODES.items()
    ]
    return {
        "main_problem": None,
        "ttm_stage": None,
        "transition_target": None,
        "user_intent": user_intent,
        "intent_entry_style": INTENT_ENTRY_STYLE_V6[user_intent],
        "common_candidates": common,
        "stage_specific_candidates": [],
        "all_candidate_codes": [c["code"] for c in common],
    }


def _guess_intent_from_message(msg: str) -> str:
    """Lightweight heuristic intent guesser for v1 — no extraction LLM
    call. Maps lexical cues to the v6 user-intent enum. Defaults to
    `small_talk` so the candidate bundle's entry-style is always defined.
    """
    if not msg:
        return "small_talk"
    low = msg.lower()
    if low.strip() in ("hi", "hey", "hello", "thanks", "thank you", "bye"):
        return "small_talk"
    # Validation cues are checked before generic question handling
    # because "Is that normal?" reads as seeking validation, not info.
    if any(t in low for t in ("is that ok", "is it normal", "am i wrong", "right?")):
        return "seek_validation"
    if any(t in low for t in ("don't want", "won't", "not going to", "drop it", "stop")):
        return "resistance"
    if any(t in low for t in ("on one hand", "on the other", "torn", "i can't decide")):
        return "deliberate_decision"
    if any(t in low for t in ("i tried", "yesterday i", "last week i", "i started")):
        return "report_action"
    if any(t in low for t in ("plan", "next step", "what should i do", "should i")):
        return "request_plan"
    if any(t in low for t in ("?", "what do you", "how do i", "any tips")):
        return "seek_information"
    return "express_emotion"


def v1_turn_fn(
    *,
    client: LLMClient,
    profile_id: str,
    system: str = "v1",
    session_id: int,
    turn_id: int,
    user_message: str,
    recent_turns: list[dict],
    last_system_message: Optional[str] = None,
    prior_session_summary: Optional[str] = None,    # unused (§9.2)
    graph: ProblemGraphV6,                           # left empty in v1
    last_n_turns: int = 5,
    previous_turn_traces: Optional[list[dict]] = None,
) -> dict:
    """One v1 turn. Same signature as `v6_turn_fn` so the v6 driver
    can call either interchangeably.

    Skips inference + recompute + edge-weight recompute; just calls the
    response prompt with a flat ALL-MISC candidate bundle.
    """
    user_intent = _guess_intent_from_message(user_message)
    candidate_bundle = _flat_candidate_bundle(user_intent)

    # Past-two-turns hint — strategies extracted from prior v1 traces.
    # `v6_full._collect_past_two_turns` shape works fine here since it
    # reads `tr['response']['reasoning']` + `tr['trace']['main_problem']`,
    # both of which v1 also emits.
    from .v6_full import _collect_past_two_turns
    past_two_turns = _collect_past_two_turns(previous_turn_traces or [])

    rsp_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="response_simple",
    )
    response_out = run_response_simple(
        client=client, ctx=rsp_ctx,
        graph=graph,                           # empty in v1 → no graph evidence
        candidate_bundle=candidate_bundle,
        past_two_turns=past_two_turns,
        recent_turns=recent_turns,
        current_user_message=user_message,
    )

    # Trace shape MUST match v6_turn_fn so the loaders, metrics, and
    # matrix_report all work uniformly across v1/v3/v6.
    from .v6_full import _extract_misc_codes_from_reasoning
    chosen_misc_codes = _extract_misc_codes_from_reasoning(
        response_out.get("reasoning", "") or ""
    )

    trace = {
        "main_problem": None,
        "current_problems": [],
        "user_intent": user_intent,
        "ttm_stage": None,
        "transition_target": None,
        "all_candidate_codes": candidate_bundle["all_candidate_codes"],
        "chosen_misc_codes": chosen_misc_codes,
        "turn_scope_level_attrs": [],
        "level_updates": [],
        "ttm_updates": [],
        "cooc_added": 0,
        "attr_conn_added": 0,
    }

    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "user_message": user_message,
        # v1 makes no inference/recompute calls — emit empty stubs so the
        # downstream loaders (which check these dicts) don't NPE.
        "inference": {
            "user_intent": {"intent": user_intent, "confidence": "low",
                            "explanation": "v1 heuristic", "supporting_utterance_span": None},
            "current_problems": [], "main_problem": None,
            "problem_attribute_entries": [],
            "problem_cooccurrence_connections": [],
            "problem_attribute_connections": [],
            "_v1_no_extraction": True,
        },
        "recompute": {
            "attribute_level_updates": [],
            "ttm_stage_updates": [],
            "_v1_no_recompute": True,
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
    # Heuristic intent mapper.
    assert _guess_intent_from_message("hi") == "small_talk"
    assert _guess_intent_from_message("I tried meditating yesterday.") == "report_action"
    assert _guess_intent_from_message("Should I drop the class?") == "request_plan"
    assert _guess_intent_from_message("I don't want to talk about it.") == "resistance"
    assert _guess_intent_from_message("Is that normal? Am I wrong?") == "seek_validation"
    assert _guess_intent_from_message("I'm just tired") == "express_emotion"

    # Candidate bundle is well-formed and contains all 10 MISC codes.
    cb = _flat_candidate_bundle("express_emotion")
    assert cb["main_problem"] is None
    assert cb["ttm_stage"] is None
    assert cb["stage_specific_candidates"] == []
    assert len(cb["common_candidates"]) == 10
    assert set(cb["all_candidate_codes"]) == set(MISC_CODES.keys())
    # Bad intent falls back to small_talk.
    cb_bad = _flat_candidate_bundle("not_a_real_intent")
    assert cb_bad["user_intent"] == "small_talk"

    print("v1_history (v6-aligned) self-test PASSED")


if __name__ == "__main__":
    _self_test()
