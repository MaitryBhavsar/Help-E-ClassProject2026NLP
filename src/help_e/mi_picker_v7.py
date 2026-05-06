"""v7 MI candidate-shortlist helpers.

Two callers:
  - Agent 1 picks `mi_for_user_intent` from a shortlist conditioned on
    the user_intent enum.
  - Agent 3b picks `mi_for_system_intent` from a shortlist conditioned
    on the new TTM stage (mirrors v6's stage→MISC table).

Each helper returns a list of dicts with `code / label / what /
transition_fn` so the prompt can surface the full menu. The actual
choice is made by the LLM (small model is fine for a shortlist pick).
"""
from __future__ import annotations

from typing import Optional

from .config import (
    MISC_CODES,
    TTM_STAGES_V6,
    TTM_TO_MISC_COMMON,
    TTM_TO_MISC_STAGE_SPECIFIC,
    USER_INTENTS_V6,
)


# -- USER_INTENT → MISC shortlist --------------------------------------------
# Maps the 8 user_intent values to MISC codes that fit "answer this user's
# IMMEDIATE need". Curated from MI literature: e.g., venting → support
# (acknowledge first), reflection only if it adds something underneath the
# words; resistance → roll-with-it via support/reflect; request_plan →
# advise/structure with permission.
USER_INTENT_TO_MISC: dict[str, tuple[str, ...]] = {
    "express_emotion":      ("support", "facilitate", "complex_reflection"),
    "seek_validation":      ("support", "complex_reflection"),
    "seek_information":     ("inform_with_permission", "complex_reflection"),
    "deliberate_decision":  ("complex_reflection", "evoke", "reframe"),
    "request_plan":         ("advise_with_permission", "structure", "closed_question"),
    "report_action":        ("support", "complex_reflection", "evoke"),
    "resistance":           ("support", "complex_reflection", "reframe"),
    "small_talk":           ("support", "facilitate"),
}


def _candidate_dict(code: str) -> dict:
    spec = MISC_CODES.get(code)
    if spec is None:
        return {"code": code, "label": code, "what": "", "transition_fn": ""}
    return {
        "code": code,
        "label": spec["label"],
        "what": spec["what"],
        "transition_fn": spec["transition_fn"],
    }


def shortlist_for_user_intent(user_intent: Optional[str]) -> list[dict]:
    """Return the MISC shortlist for Agent 1 to pick from.
    Falls back to (support, facilitate) if user_intent is unknown.
    """
    if user_intent in USER_INTENT_TO_MISC:
        codes = USER_INTENT_TO_MISC[user_intent]
    else:
        codes = TTM_TO_MISC_COMMON
    return [_candidate_dict(c) for c in codes]


def shortlist_for_ttm_stage(ttm_stage: Optional[str]) -> list[dict]:
    """Return the MISC shortlist for Agent 3b to pick `mi_for_system_intent` from.
    `ttm_stage` is the NEWLY-RECOMPUTED stage. Returns COMMON + stage-specific
    union. Mirrors v6's mi_selector logic but expressed as a flat shortlist.
    """
    common = list(TTM_TO_MISC_COMMON)
    if ttm_stage and ttm_stage in TTM_TO_MISC_STAGE_SPECIFIC:
        stage_specific = list(TTM_TO_MISC_STAGE_SPECIFIC[ttm_stage])
    else:
        stage_specific = []
    seen: set[str] = set()
    out: list[dict] = []
    for c in common + stage_specific:
        if c not in seen:
            seen.add(c)
            out.append(_candidate_dict(c))
    return out


def all_misc_codes() -> tuple[str, ...]:
    """All canonical MISC codes; used by validators in Agent 5."""
    return tuple(MISC_CODES.keys())


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _self_test() -> None:
    # 1. Every USER_INTENTS_V6 maps somewhere.
    for intent in USER_INTENTS_V6:
        assert intent in USER_INTENT_TO_MISC, f"missing mapping for {intent!r}"
        out = shortlist_for_user_intent(intent)
        assert out, f"empty shortlist for {intent!r}"
        for c in out:
            assert set(c.keys()) == {"code", "label", "what", "transition_fn"}
            assert c["code"] in MISC_CODES, f"unknown code {c['code']!r}"

    # 2. Unknown intent falls back gracefully.
    out_unknown = shortlist_for_user_intent("not_a_real_intent")
    assert [c["code"] for c in out_unknown] == list(TTM_TO_MISC_COMMON)
    out_none = shortlist_for_user_intent(None)
    assert [c["code"] for c in out_none] == list(TTM_TO_MISC_COMMON)

    # 3. Each TTM stage produces a non-empty shortlist with COMMON included.
    for stage in TTM_STAGES_V6:
        out = shortlist_for_ttm_stage(stage)
        codes = [c["code"] for c in out]
        for cc in TTM_TO_MISC_COMMON:
            assert cc in codes, f"COMMON {cc} missing for stage {stage}"
        # No duplicates
        assert len(codes) == len(set(codes))

    # 4. Unknown stage → just COMMON.
    out_bogus = shortlist_for_ttm_stage("maintenance")
    assert [c["code"] for c in out_bogus] == list(TTM_TO_MISC_COMMON)

    # 5. all_misc_codes returns the full vocab.
    codes = all_misc_codes()
    assert len(codes) == len(MISC_CODES)
    assert "evoke" in codes and "complex_reflection" in codes

    # 6. Sanity on a couple specific picks.
    venting_codes = [c["code"] for c in shortlist_for_user_intent("express_emotion")]
    assert "complex_reflection" in venting_codes, "venting should allow reflection"
    request_plan_codes = [c["code"] for c in shortlist_for_user_intent("request_plan")]
    assert "advise_with_permission" in request_plan_codes, "request_plan should offer advise"
    assert "evoke" not in request_plan_codes, (
        "request_plan should NOT default to evoke — that's deflection"
    )

    print("mi_picker_v7 self-test PASSED")


if __name__ == "__main__":
    _self_test()
