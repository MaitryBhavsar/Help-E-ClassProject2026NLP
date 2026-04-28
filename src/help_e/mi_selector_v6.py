"""§6.6 v6 (REDESIGN) — MI strategy candidate selection.

Pure Python, no LLM. Single source of MI strategy candidates: the main
problem's TTM stage. Rule = COMMON + STAGE_SPECIFIC[stage].

Replaces the prior dual-stream (intent + TTM) version. user_intent now
informs only the response *entry style* (passed through unchanged), not
the candidate pool. Attribute values inform the response *content* via
the bundle, not the candidate pool. See plan §3, §4 and
`knowledge/mi_mapping_v6.md`.
"""
from __future__ import annotations

from typing import Optional

from .config import (
    INTENT_ENTRY_STYLE_V6,
    MISC_CODES,
    TTM_STAGES_V6,
    TTM_TO_MISC_COMMON,
    TTM_TO_MISC_STAGE_SPECIFIC,
    TTM_TRANSITION_TARGET_V6,
    USER_INTENTS_V6,
)
from .graph_v6 import ProblemGraphV6


# Safety fallback: if no main problem (cold-start), still emit something
# the response prompt can pick from. These are the two COMMON codes that
# work at any stage.
_SAFETY_FALLBACK: tuple[str, ...] = TTM_TO_MISC_COMMON


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candidate_dict(code: str) -> dict:
    """Render a single MISC code as the dict shape the response prompt
    consumes: code, label, what (definition), transition_fn.
    """
    spec = MISC_CODES.get(code)
    if spec is None:
        # defensively skip bogus codes
        return {"code": code, "label": code, "what": "", "transition_fn": ""}
    return {
        "code": code,
        "label": spec["label"],
        "what": spec["what"],
        "transition_fn": spec["transition_fn"],
    }


def _candidates_for(ttm_stage: Optional[str]) -> tuple[list[dict], list[dict]]:
    """Return (common, stage_specific) candidate dict lists for the stage.

    `ttm_stage` may be None (cold-start, no main problem) → returns
    (safety_fallback as common, []).
    """
    common = [_candidate_dict(c) for c in TTM_TO_MISC_COMMON]
    if ttm_stage is None or ttm_stage not in TTM_TO_MISC_STAGE_SPECIFIC:
        return common, []
    stage_specific = [
        _candidate_dict(c) for c in TTM_TO_MISC_STAGE_SPECIFIC[ttm_stage]
    ]
    return common, stage_specific


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def select_candidates_v6(
    *,
    graph: ProblemGraphV6,
    main_problem_name: Optional[str],
    user_intent: str,
    # Kept for backwards-compat with v6_full's signature; not used.
    current_session: int = 0,
    current_turn: int = 0,
    last_n: int = 5,
) -> dict:
    """Return the per-turn candidate bundle the response prompt consumes.

    Output shape:
        {
          "main_problem":       str | None,
          "ttm_stage":          str | None,
          "transition_target":  str | None,   # e.g. "contemplation → preparation"
          "user_intent":        str,
          "intent_entry_style": str,          # the entry-style guidance string

          "common_candidates":         [{code, label, what, transition_fn}, ...],
          "stage_specific_candidates": [{code, label, what, transition_fn}, ...],
          "all_candidate_codes":       [code, ...],   # union, for validators
        }

    Cold start (`main_problem_name` is None or not in graph): TTM stage is
    None; stage_specific_candidates is []; common_candidates still emitted
    (the two-code COMMON fallback).
    """
    # Resolve main problem and TTM stage.
    ttm_stage: Optional[str] = None
    main_name: Optional[str] = None
    if main_problem_name and main_problem_name in graph.problems:
        main_name = main_problem_name
        prob = graph.problems[main_problem_name]
        ttm_stage = prob.current_ttm_stage
        # Guard: only use stages defined in the v6 4-stage enum.
        if ttm_stage not in TTM_STAGES_V6:
            # If the graph still carries a legacy "maintenance" or unknown
            # stage, treat as None for candidate-selection purposes.
            ttm_stage = None

    common, stage_specific = _candidates_for(ttm_stage)

    transition_target: Optional[str] = (
        TTM_TRANSITION_TARGET_V6.get(ttm_stage) if ttm_stage else None
    )

    # Resolve entry style for user_intent. Unknown intents fall back to the
    # safest open-but-respectful entry: small_talk's style.
    if user_intent not in USER_INTENTS_V6:
        intent_entry_style = INTENT_ENTRY_STYLE_V6["small_talk"]
    else:
        intent_entry_style = INTENT_ENTRY_STYLE_V6[user_intent]

    all_codes = [c["code"] for c in common] + [c["code"] for c in stage_specific]

    return {
        "main_problem": main_name,
        "ttm_stage": ttm_stage,
        "transition_target": transition_target,
        "user_intent": user_intent,
        "intent_entry_style": intent_entry_style,
        "common_candidates": common,
        "stage_specific_candidates": stage_specific,
        "all_candidate_codes": all_codes,
    }


# ---------------------------------------------------------------------------
# Self-test (no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    g_cold = ProblemGraphV6(profile_id="T")

    # 1. Cold start — no main problem.
    out = select_candidates_v6(
        graph=g_cold, main_problem_name=None, user_intent="express_emotion",
    )
    assert out["main_problem"] is None
    assert out["ttm_stage"] is None
    assert out["transition_target"] is None
    assert [c["code"] for c in out["common_candidates"]] == ["support", "facilitate"]
    assert out["stage_specific_candidates"] == []
    assert out["intent_entry_style"].startswith("Reflect first")
    assert out["all_candidate_codes"] == ["support", "facilitate"]

    # 2. Main problem in contemplation.
    g = ProblemGraphV6(profile_id="T")
    g.get_or_create_problem("academic_pressure", first_mentioned=(1, 1))
    g.set_ttm_stage("academic_pressure", "contemplation")
    out = select_candidates_v6(
        graph=g, main_problem_name="academic_pressure",
        user_intent="deliberate_decision",
    )
    assert out["main_problem"] == "academic_pressure"
    assert out["ttm_stage"] == "contemplation"
    assert out["transition_target"] == "contemplation → preparation"
    common_ids = [c["code"] for c in out["common_candidates"]]
    spec_ids = [c["code"] for c in out["stage_specific_candidates"]]
    assert common_ids == ["support", "facilitate"]
    assert spec_ids == ["evoke", "complex_reflection", "inform_with_permission"]
    assert out["all_candidate_codes"] == common_ids + spec_ids
    assert "Reflect both sides" in out["intent_entry_style"]

    # 3. Each candidate has the rich dict shape.
    sample = out["stage_specific_candidates"][0]
    assert set(sample.keys()) == {"code", "label", "what", "transition_fn"}
    assert sample["code"] == "evoke"
    assert sample["label"] == "Evoke"
    assert "ambivalence" in sample["transition_fn"].lower()

    # 4. Each TTM stage produces a non-empty stage_specific stream.
    for stage in TTM_STAGES_V6:
        g4 = ProblemGraphV6(profile_id="T")
        g4.get_or_create_problem("work_stress", first_mentioned=(1, 1))
        g4.set_ttm_stage("work_stress", stage)
        out4 = select_candidates_v6(
            graph=g4, main_problem_name="work_stress",
            user_intent="express_emotion",
        )
        assert out4["stage_specific_candidates"], f"empty stream for {stage}"
        assert out4["transition_target"] is not None

    # 5. Unknown user_intent falls back to small_talk entry style.
    out5 = select_candidates_v6(
        graph=g_cold, main_problem_name=None, user_intent="not_a_real_intent",
    )
    assert out5["intent_entry_style"] == INTENT_ENTRY_STYLE_V6["small_talk"]

    # 6. Legacy "maintenance" stage on the graph → TTM treated as None.
    g_legacy = ProblemGraphV6(profile_id="T")
    g_legacy.get_or_create_problem("academic_pressure", first_mentioned=(1, 1))
    # Bypass the v6 enum guard by writing directly to the field.
    g_legacy.problems["academic_pressure"].current_ttm_stage = "maintenance"
    out6 = select_candidates_v6(
        graph=g_legacy, main_problem_name="academic_pressure",
        user_intent="express_emotion",
    )
    assert out6["ttm_stage"] is None  # legacy stage filtered out
    assert out6["stage_specific_candidates"] == []  # nothing stage-specific
    assert [c["code"] for c in out6["common_candidates"]] == ["support", "facilitate"]

    # 7. All 8 user_intents have a non-empty entry style.
    for intent in USER_INTENTS_V6:
        out_i = select_candidates_v6(
            graph=g_cold, main_problem_name=None, user_intent=intent,
        )
        assert out_i["intent_entry_style"], f"empty entry style for {intent}"

    print("mi_selector_v6 (redesign) self-test PASSED")


if __name__ == "__main__":
    _self_test()
