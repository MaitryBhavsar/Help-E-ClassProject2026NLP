"""GraphRAG baseline (v6-aligned).

Per-turn pipeline:

  1. LLM extraction call (`graphrag_inference`) — read the user's current
     turn + recent dialogue, emit free-text `entities` and labeled
     `relationships`. No HBM types, no TTM stages, no problem vocabulary.
  2. Append the extraction to the per-profile `GraphRAGState` (in-memory,
     persists across sessions of the same profile within one process).
  3. Local-search retrieval — pick the entities most lexically similar to
     the current user message, fetch their 1-hop neighborhood and the
     associated relationship records.
  4. LLM response call (`response_graphrag`) — generate the user-facing
     reply, with the retrieved neighborhood rendered as evidence.

Per-turn cost: 2 LLM calls (extraction + response). One fewer than v6
(which has inference + recompute + response). The extraction is
unconstrained — that is the comparison point against v6's HBM/TTM-typed
inference.

This file is purely additive and does NOT modify any existing baseline,
prompt, graph, simulator, evaluator, driver, UI, or config code.
"""
from __future__ import annotations

import logging
import textwrap
from typing import Optional

from ..config import (
    INTENT_ENTRY_STYLE_V6,
    LAST_N_TURNS,
    MISC_CODES,
    USER_INTENTS_V6,
)
from ..graph_v6 import ProblemGraphV6
from ..graphrag_state import get_or_create_state
from ..llm_client import CallContext, LLMClient
from ..prompts.graphrag_inference import run_graphrag_extraction


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Reused: same response system prompt + schema + validators as v1/v3/v4
# ---------------------------------------------------------------------------


from ..instruction_response_simple import (   # noqa: E402
    RESPONSE_SIMPLE_SCHEMA,
    _fallback_response,
    _validate_factory,
    build_system_prompt as _build_simple_system_prompt,
)
from ..instruction_response_v6 import _persona_to_dict   # noqa: E402
from ..prompts.common_v6 import (   # noqa: E402
    format_candidate_strategies_block,
    format_past_two_turns_block_v6,
    format_persona_block_v6,
    format_recent_turns_block_v6,
    format_user_intent_block_v6,
)


# ---------------------------------------------------------------------------
# Render the local-search result as a prompt block
# ---------------------------------------------------------------------------


def _format_graphrag_block(local_search_out: dict) -> str:
    """Turn the local_search dict into a human-readable evidence block.

    Empty graph or no relevant seeds → a clear "(none yet)" stub so the
    response prompt doesn't mention nonexistent retrievals.
    """
    if not local_search_out or local_search_out.get("is_empty"):
        return "  (graph empty or no relevant entities yet)"

    lines: list[str] = []
    lines.append("  Seeds (entities most relevant to the current message):")
    for s in local_search_out.get("seeds", []):
        lines.append(
            f"    • {s['name']}  (score={s['score']}, mentioned×{s['provenance_count']})"
        )

    lines.append("  Neighborhoods:")
    for nhood in local_search_out.get("neighborhoods", []):
        seed = nhood["seed"]
        if not nhood.get("neighbors"):
            lines.append(f"    {seed} → (no related entities yet)")
            continue
        for nb in nhood["neighbors"]:
            head = f"    {seed} ↔ {nb['name']}"
            lines.append(head)
            for rel in nb.get("relationships", []):
                a = rel["a"]; b = rel["b"]
                label = rel["label"]
                sid = rel.get("session_id"); tid = rel.get("turn_id")
                loc = f" (s{sid}t{tid})" if sid is not None and tid is not None else ""
                quote = (rel.get("supporting_utterance_span") or "").strip()
                qstr = f"  — \"{quote}\"" if quote else ""
                lines.append(f"      {a} —[{label}]→ {b}{loc}{qstr}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Build the user prompt (mirrors response_simple's, with a GRAPHRAG block
# replacing the "Relevant problems" block)
# ---------------------------------------------------------------------------


def _build_graphrag_user_prompt(
    *,
    graph: ProblemGraphV6,
    candidate_bundle: dict,
    past_two_turns: list[dict],
    recent_turns: list[dict],
    local_search_out: dict,
    current_user_message: str,
) -> str:
    persona_dict = _persona_to_dict(graph.persona)
    persona_block = format_persona_block_v6(persona_dict)
    intent_block = format_user_intent_block_v6(
        candidate_bundle.get("user_intent"),
        candidate_bundle.get("intent_entry_style"),
    )
    candidate_block = format_candidate_strategies_block(
        common=candidate_bundle.get("common_candidates") or [],
        stage_specific=candidate_bundle.get("stage_specific_candidates") or [],
        ttm_stage=candidate_bundle.get("ttm_stage"),
        transition_target=candidate_bundle.get("transition_target"),
    )
    recent_block = format_recent_turns_block_v6(recent_turns)
    past_two_block = format_past_two_turns_block_v6(past_two_turns)
    graphrag_block = _format_graphrag_block(local_search_out)

    return textwrap.dedent(f"""\
        # PERSONA
        {persona_block}

        # USER_INTENT
        {intent_block}

        # CANDIDATE STRATEGIES
        {candidate_block}

        # EVIDENCE

        ## Auto-extracted entity graph (local search around the current message)
        {graphrag_block}

        # RECENT TURNS
        {recent_block}

        # PAST TWO TURNS
        {past_two_block}

        # CURRENT USER MESSAGE
          {current_user_message!r}
    """).rstrip()


# ---------------------------------------------------------------------------
# Helpers (free MISC vocabulary, intent guess) — same as v1 / RAG
# ---------------------------------------------------------------------------


def _flat_candidate_bundle(user_intent: str) -> dict:
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
    if not msg:
        return "small_talk"
    low = msg.lower()
    if low.strip() in ("hi", "hey", "hello", "thanks", "thank you", "bye"):
        return "small_talk"
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


# ---------------------------------------------------------------------------
# Turn function
# ---------------------------------------------------------------------------


def graphrag_turn_fn(
    *,
    client: LLMClient,
    profile_id: str,
    system: str = "graphrag",
    session_id: int,
    turn_id: int,
    user_message: str,
    recent_turns: list[dict],
    last_system_message: Optional[str] = None,
    prior_session_summary: Optional[str] = None,    # unused
    graph: ProblemGraphV6,                           # accepted but unused
    last_n_turns: int = LAST_N_TURNS,
    previous_turn_traces: Optional[list[dict]] = None,
) -> dict:
    """One GraphRAG turn. Same signature as ``v6_turn_fn``.

    1. LLM extraction → entities + relationships from this turn.
    2. Apply to the per-profile GraphRAGState.
    3. Local search around the current message.
    4. Generate response with the retrieved neighborhood as evidence.
    """
    # 1. Extraction LLM call.
    ext_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="graphrag_inference",
    )
    extraction = run_graphrag_extraction(
        client=client, ctx=ext_ctx,
        current_message=user_message, recent_turns=recent_turns,
    )

    # 2. Apply to graph (pure Python).
    state = get_or_create_state(profile_id)
    cooc_before = state.stats()["num_relationships"]
    state.add_entities(extraction.get("entities") or [],
                       session_id=session_id, turn_id=turn_id)
    state.add_relationships(extraction.get("relationships") or [],
                            session_id=session_id, turn_id=turn_id)
    cooc_after = state.stats()["num_relationships"]

    # 3. Local-search retrieval.
    local_search_out = state.local_search(user_message)

    # 4. Build the response prompt.
    user_intent = _guess_intent_from_message(user_message)
    candidate_bundle = _flat_candidate_bundle(user_intent)

    from .v6_full import _collect_past_two_turns
    past_two_turns = _collect_past_two_turns(previous_turn_traces or [])

    rsp_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="response_graphrag",
    )
    candidate_codes = set(candidate_bundle.get("all_candidate_codes") or [])
    user_prompt = _build_graphrag_user_prompt(
        graph=graph, candidate_bundle=candidate_bundle,
        past_two_turns=past_two_turns, recent_turns=recent_turns,
        local_search_out=local_search_out, current_user_message=user_message,
    )
    try:
        response_out = client.generate_structured(
            ctx=rsp_ctx,
            system_prompt=_build_simple_system_prompt(),
            user_prompt=user_prompt,
            schema=RESPONSE_SIMPLE_SCHEMA,
            validator_extras=_validate_factory(candidate_codes, user_intent),
        )
    except Exception as e:
        log.warning("response_graphrag failed (%s); using fallback", e)
        response_out = _fallback_response(user_intent)

    # 5. v6-shape trace.
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
        "attr_conn_added": cooc_after - cooc_before,
        # GraphRAG-specific bookkeeping.
        "graphrag_entities_total": state.stats()["num_entities"],
        "graphrag_relationships_total": state.stats()["num_relationships"],
        "graphrag_seeds_used": [s["name"] for s in local_search_out.get("seeds", [])],
    }

    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "user_message": user_message,
        # Re-shape the unconstrained extraction into v6's `inference` slot
        # so loaders that look for it don't NPE. We mark it as a graphrag
        # extraction so analysis can distinguish.
        "inference": {
            "user_intent": {"intent": user_intent, "confidence": "low",
                            "explanation": "graphrag heuristic",
                            "supporting_utterance_span": None},
            "current_problems": [], "main_problem": None,
            "problem_attribute_entries": [],
            "problem_cooccurrence_connections": [],
            "problem_attribute_connections": [],
            "_graphrag_extraction": extraction,
        },
        "recompute": {
            "attribute_level_updates": [],
            "ttm_stage_updates": [],
            "_graphrag_no_recompute": True,
        },
        "bundle": None,
        "candidate_bundle": candidate_bundle,
        "past_two_turns": past_two_turns,
        "response": response_out,
        "trace": trace,
        "graphrag_local_search": local_search_out,
    }


# ---------------------------------------------------------------------------
# Self-test (no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    # Renderer handles empty + populated cases.
    assert "graph empty" in _format_graphrag_block({"is_empty": True})
    out = _format_graphrag_block({
        "is_empty": False,
        "seeds": [{"name": "the gym", "score": 1.0, "provenance_count": 2}],
        "neighborhoods": [{
            "seed": "the gym",
            "neighbors": [{
                "name": "weight gain",
                "relationships": [{
                    "a": "the gym", "b": "weight gain", "label": "reminds of",
                    "session_id": 1, "turn_id": 1,
                    "supporting_utterance_span": "the mirror",
                }],
            }],
        }],
    })
    assert "the gym ↔ weight gain" in out
    assert "—[reminds of]→" in out
    assert "(s1t1)" in out

    print("graphrag_baseline self-test PASSED")


if __name__ == "__main__":
    _self_test()
