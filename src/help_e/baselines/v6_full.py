"""§6.10 v6 (REDESIGN) — per-turn pipeline.

One call (`v6_turn_fn`) takes a user message and a mutable `ProblemGraphV6`
and drives every step of the v6 per-turn pipeline:

    1. inference            (§18.1 v6)   LLM
    2. graph writes          (pure Python — problems, evidence, cooc,
                              attr-connections)
    3. recompute             (§18.2 + §18.3 v6)  LLM — level + TTM
    4. edge weight recompute (pure Python)
    5. bundle (analysis snapshot) (pure Python)
    6. MI candidate select   (pure Python — TTM-stage rule)
    7. response              (§5/§6/§8 v6 redesign)   LLM

Cold-start path (no problems in graph, inference returns empty
current_problems): skips recompute and edge recompute; still calls
response_v6 with an empty graph and the COMMON-only candidate stream,
so the chatbot emits a sensible reply instead of crashing.

v6 redesign integration points (§4, §5, §6):
  - `select_candidates_v6` returns the new single-stream
    `{common_candidates, stage_specific_candidates, all_candidate_codes,
      intent_entry_style, ...}` bundle.
  - `run_response_v6` consumes that bundle plus the graph and renders
    the canonical 3-field output `{reasoning, evidence_used,
    final_response}`.
  - Past-turn diversity is fed in as a tiny `past_two_turns` list
    `[{turn_offset, main_problem, strategies}, ...]` derived from
    earlier turn traces.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from ..config import LEVEL_ATTR_TYPES, MISC_CODES, MISC_INCONSISTENT_CODES
from ..graph_v6 import (
    AttributeConnectionEntry,
    AttributeEvidenceEntry,
    CooccurrenceEntry,
    ProblemGraphV6,
    global_turn_idx,
)
from ..instruction_response_v6 import run_response_v6
from ..llm_client import CallContext, LLMClient
from ..mi_selector_v6 import select_candidates_v6
from ..prompts.inference import InferenceInputs, run_inference
from ..prompts.recompute import RecomputeInputs, run_recompute
from ..retrieval_v6 import build_bundle_v6


# Default K for past-K evidence entries read by the recompute prompt.
DEFAULT_RECOMPUTE_K: int = 5

# How many past turns the response prompt sees in the diversity hint
# (§6 PAST TWO TURNS block).
PAST_TWO_TURNS_N: int = 2


# Match snake_case identifiers; we filter for known MISC code names below.
_WORD_RE = re.compile(r"\b[a-z_]{3,}\b")


def _extract_misc_codes_from_reasoning(reasoning: str) -> list[str]:
    """Extract MISC code names mentioned in a reasoning string.

    The response LLM names its picks inline (e.g. "use complex_reflection
    to develop discrepancy"). We pull every snake_case word and intersect
    with both the selectable MISC codes and the MI-inconsistent
    anti-pattern codes — so downstream metrics can spot anti-patterns
    too. Order is preserved as first-seen.
    """
    if not reasoning:
        return []
    known = set(MISC_CODES.keys()) | set(MISC_INCONSISTENT_CODES.keys())
    found: list[str] = []
    seen: set[str] = set()
    for m in _WORD_RE.finditer(reasoning.lower()):
        w = m.group(0)
        if w in known and w not in seen:
            seen.add(w)
            found.append(w)
    return found


def _collect_past_two_turns(prev_traces: list[dict]) -> list[dict]:
    """Build the PAST TWO TURNS diversity hint for the response prompt.

    Reads the last `PAST_TWO_TURNS_N` traces from the current session
    and pulls `(main_problem, strategies)` from each. `strategies` is
    extracted from that turn's `response.reasoning` text.

    Output entries: `{turn_offset, main_problem, strategies}` —
    `turn_offset` is `-1` for the most recent prior turn, `-2` for
    the one before that, etc.
    """
    if not prev_traces:
        return []
    last_n = prev_traces[-PAST_TWO_TURNS_N:]
    out: list[dict] = []
    for offset, tr in enumerate(reversed(last_n), start=1):
        reasoning = (tr.get("response") or {}).get("reasoning", "") or ""
        strategies = _extract_misc_codes_from_reasoning(reasoning)
        out.append({
            "turn_offset": -offset,
            "main_problem": (tr.get("trace") or {}).get("main_problem"),
            "strategies": strategies,
        })
    # Keep them in oldest-to-newest order so the prompt reads "t-2, t-1".
    out.reverse()
    return out


# ---------------------------------------------------------------------------
# Graph-mutation helpers (pure Python, no LLM)
# ---------------------------------------------------------------------------


def _apply_inference_to_graph(
    graph: ProblemGraphV6,
    inference_out: dict,
    *,
    session_id: int,
    turn_id: int,
) -> list[dict]:
    """Apply the 6-field inference output to the graph in place.

    Returns `turn_scope` — the list of `(problem_name, attribute_name)`
    pairs that received NEW level-attribute evidence this turn. Used as
    Part A scope for `recompute`.
    """
    for cp in inference_out.get("current_problems") or []:
        try:
            graph.get_or_create_problem(
                cp["problem_name"], first_mentioned=(session_id, turn_id),
            )
        except ValueError:
            continue

    turn_scope: list[dict] = []
    seen_scope: set[tuple[str, str]] = set()
    for entry in inference_out.get("problem_attribute_entries") or []:
        pname = entry["problem_name"]
        attr_name = entry["attribute_name"]
        if pname not in graph.problems:
            continue
        ev = AttributeEvidenceEntry(
            session_id=session_id,
            turn_id=turn_id,
            inferred_information=entry["inferred_information"],
            concise_explanation=entry["concise_explanation"],
            supporting_utterance_span=entry.get("supporting_utterance_span"),
            # `confidence` was removed from inference output; default
            # is set on the dataclass.
        )
        try:
            graph.append_evidence(
                problem_name=pname, attr_name=attr_name, entry=ev,
            )
        except ValueError:
            continue
        if attr_name in LEVEL_ATTR_TYPES:
            key = (pname, attr_name)
            if key not in seen_scope:
                seen_scope.add(key)
                turn_scope.append({
                    "problem_name": pname,
                    "attribute_name": attr_name,
                })

    for coc in inference_out.get("problem_cooccurrence_connections") or []:
        p1, p2 = coc["problem_1"], coc["problem_2"]
        if p1 == p2:
            continue
        if p1 not in graph.problems or p2 not in graph.problems:
            continue
        try:
            graph.append_cooccurrence(
                p1, p2,
                CooccurrenceEntry(
                    session_id=session_id, turn_id=turn_id,
                    concise_explanation=coc["concise_explanation"],
                    supporting_utterance_span=coc.get("supporting_utterance_span"),
                ),
            )
        except ValueError:
            continue

    for conn in inference_out.get("problem_attribute_connections") or []:
        p1, p2 = conn["problem_1"], conn["problem_2"]
        if p1 == p2:
            continue
        if p1 not in graph.problems or p2 not in graph.problems:
            continue
        try:
            graph.append_attribute_connection(
                p1, p2,
                AttributeConnectionEntry(
                    session_id=session_id, turn_id=turn_id,
                    attribute_1=conn["attribute_1"],
                    attribute_2=conn["attribute_2"],
                    relation_type=conn["relation_type"],
                    connection_explanation=conn["connection_explanation"],
                    supporting_utterance_span=conn.get("supporting_utterance_span"),
                    confidence=conn["confidence"],
                ),
            )
        except ValueError:
            continue

    return turn_scope


def _build_recompute_inputs(
    graph: ProblemGraphV6,
    *,
    current_problems: list[str],
    turn_scope: list[dict],
    session_id: int,
    turn_id: int,
    k: int = DEFAULT_RECOMPUTE_K,
) -> RecomputeInputs:
    """Assemble the state + past-K context that `recompute` consumes."""
    current_problem_state: list[dict] = []
    past_k_level: dict[tuple[str, str], list[dict]] = {}
    past_k_all: dict[str, list[dict]] = {}

    for pname in current_problems:
        if pname not in graph.problems:
            continue
        p = graph.problems[pname]

        level_attrs_state = {
            aname: st.current_level
            for aname, st in p.level_attributes.items()
        }
        current_problem_state.append({
            "problem_name": pname,
            "current_ttm_stage": p.current_ttm_stage,
            "goal": p.goal,
            "level_attributes": level_attrs_state,
        })

        for scope in turn_scope:
            if scope["problem_name"] != pname:
                continue
            aname = scope["attribute_name"]
            state = p.level_attributes.get(aname)
            if state is None or not state.evidence_stack:
                continue
            past_k_level[(pname, aname)] = [
                {
                    "session_id": e.session_id,
                    "turn_id": e.turn_id,
                    "attr_name": aname,
                    "inferred_information": e.inferred_information,
                    "confidence": e.confidence,
                }
                for e in state.evidence_stack[-k:]
            ]

        merged: list[dict] = []
        for aname, st in p.level_attributes.items():
            for e in st.evidence_stack:
                merged.append({
                    "session_id": e.session_id, "turn_id": e.turn_id,
                    "attr_name": aname,
                    "inferred_information": e.inferred_information,
                    "confidence": e.confidence,
                })
        for aname, st in p.non_level_attributes.items():
            for e in st.evidence_stack:
                merged.append({
                    "session_id": e.session_id, "turn_id": e.turn_id,
                    "attr_name": aname,
                    "inferred_information": e.inferred_information,
                    "confidence": e.confidence,
                })
        merged.sort(key=lambda x: (x["session_id"], x["turn_id"]))
        past_k_all[pname] = merged[-k:]

    return RecomputeInputs(
        session_id=session_id,
        turn_id=turn_id,
        turn_scope=turn_scope,
        current_problem_state=current_problem_state,
        past_k_level_attribute_entries=past_k_level,
        past_k_all_attribute_entries=past_k_all,
    )


def _apply_recompute_to_graph(graph: ProblemGraphV6, recompute_out: dict) -> None:
    """Apply level + TTM updates. Skip silently on individual errors."""
    for u in recompute_out.get("attribute_level_updates") or []:
        try:
            graph.set_level(
                u["problem_name"], u["attribute_name"], u["new_level"],
            )
        except (KeyError, ValueError):
            continue
    for u in recompute_out.get("ttm_stage_updates") or []:
        try:
            graph.set_ttm_stage(u["problem_name"], u["new_ttm_stage"])
        except (KeyError, ValueError):
            continue


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def v6_turn_fn(
    *,
    client: LLMClient,
    profile_id: str,
    system: str = "v6",
    session_id: int,
    turn_id: int,
    user_message: str,
    recent_turns: list[dict],
    last_system_message: Optional[str] = None,
    prior_session_summary: Optional[str] = None,  # accepted but ignored (§9.2)
    graph: ProblemGraphV6,
    last_n_turns: int = 5,
    previous_turn_traces: Optional[list[dict]] = None,
) -> dict:
    """Execute one v6 turn against `graph`. Returns a structured trace.

    The graph is MUTATED in place with new evidence, level updates, TTM
    updates, and edge-weight recomputes. Caller is responsible for
    persisting the graph after the turn completes.

    Output dict (all JSON-serializable):

        {
          "session_id", "turn_id", "user_message",
          "inference":          ...,   # full inference_out
          "recompute":          ...,   # full recompute_out (or skipped marker)
          "bundle":             ...,   # RetrievalBundleV6.to_json_dict()
                                       # (analysis snapshot; new response
                                       # prompt reads the graph directly)
          "candidate_bundle":   ...,   # mi_selector_v6 single-stream output
          "past_two_turns":     ...,   # diversity hint passed to response
          "response":           ...,   # {reasoning, evidence_used,
                                       #  final_response}
          "trace": {
              "main_problem", "current_problems", "user_intent",
              "ttm_stage", "transition_target",
              "all_candidate_codes",
              "chosen_misc_codes",   # extracted from response.reasoning
              "turn_scope_level_attrs", "level_updates", "ttm_updates",
              "cooc_added", "attr_conn_added",
          },
        }
    """
    # --- Step 1: Inference ----------------------------------------------
    active_problems_for_prompt = [
        {
            "problem_name": name,
            "current_ttm_stage": p.current_ttm_stage,
            "goal": p.goal,
            "last_mentioned": list(p.last_mentioned),
        }
        for name, p in graph.problems.items()
    ]
    inf_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="inference",
    )
    inference_out = run_inference(
        client=client, ctx=inf_ctx,
        inputs=InferenceInputs(
            current_message=user_message,
            recent_turns=recent_turns,
            active_problems=active_problems_for_prompt,
        ),
    )

    current_problem_names = [
        cp["problem_name"] for cp in (inference_out.get("current_problems") or [])
    ]
    main_obj = inference_out.get("main_problem")
    main_name = main_obj["problem_name"] if main_obj else None
    user_intent = (
        (inference_out.get("user_intent") or {}).get("intent", "small_talk")
    )

    # --- Step 2: Apply inference to graph -------------------------------
    turn_scope = _apply_inference_to_graph(
        graph, inference_out, session_id=session_id, turn_id=turn_id,
    )
    cooc_added = len(inference_out.get("problem_cooccurrence_connections") or [])
    attr_conn_added = len(inference_out.get("problem_attribute_connections") or [])

    # --- Step 3: Recompute ---------------------------------------------
    if current_problem_names:
        recompute_inputs = _build_recompute_inputs(
            graph,
            current_problems=current_problem_names,
            turn_scope=turn_scope,
            session_id=session_id, turn_id=turn_id,
        )
        rc_ctx = CallContext(
            profile_id=profile_id, session_id=session_id, system=system,
            turn_id=turn_id, call_role="recompute",
        )
        recompute_out = run_recompute(
            client=client, ctx=rc_ctx, inputs=recompute_inputs,
        )
        _apply_recompute_to_graph(graph, recompute_out)
    else:
        recompute_out = {
            "attribute_level_updates": [],
            "ttm_stage_updates": [],
            "_skipped_no_current_problems": True,
        }

    # --- Step 4: Edge weight recompute ----------------------------------
    graph.recompute_all_edge_weights(global_turn_idx(session_id, turn_id))

    # --- Step 5: Bundle (skipped) --------------------------------------
    # `build_bundle_v6` was kept as an analysis snapshot but is never
    # consumed by metrics/eval — only by the demo UI (ui/server.py) for
    # debug rendering. Skipping it on the matrix path saves ~50ms/turn
    # of pure-Python build cost + JSON serialization. The UI continues
    # to work because it gracefully handles `bundle=None`.
    bundle = None

    # --- Step 6: MI candidate selection (single-stream) -----------------
    candidate_bundle = select_candidates_v6(
        graph=graph,
        main_problem_name=main_name,
        user_intent=user_intent,
        current_session=session_id,
        current_turn=turn_id,
        last_n=last_n_turns,
    )

    # --- Step 7: Response (3-field schema) -----------------------------
    past_two_turns = _collect_past_two_turns(previous_turn_traces or [])
    rsp_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="response_v6",
    )
    response_out = run_response_v6(
        client=client, ctx=rsp_ctx,
        graph=graph,
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
        "current_problems": current_problem_names,
        "user_intent": user_intent,
        "ttm_stage": candidate_bundle.get("ttm_stage"),
        "transition_target": candidate_bundle.get("transition_target"),
        "all_candidate_codes": candidate_bundle.get("all_candidate_codes") or [],
        "chosen_misc_codes": chosen_misc_codes,
        "turn_scope_level_attrs": turn_scope,
        "level_updates": recompute_out.get("attribute_level_updates") or [],
        "ttm_updates": recompute_out.get("ttm_stage_updates") or [],
        "cooc_added": cooc_added,
        "attr_conn_added": attr_conn_added,
    }

    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "user_message": user_message,
        "inference": inference_out,
        "recompute": recompute_out,
        "bundle": bundle,  # None on the matrix path; bundle build skipped
        "candidate_bundle": candidate_bundle,
        "past_two_turns": past_two_turns,
        "response": response_out,
        "trace": trace,
    }


# ---------------------------------------------------------------------------
# Self-test with a mock LLM client
# ---------------------------------------------------------------------------


class _MockClient:
    """Minimal LLMClient stand-in for unit testing `v6_turn_fn`."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, str]] = []  # (role, sys, user)

    def generate_structured(
        self, *, ctx, system_prompt, user_prompt, schema,
        validator_extras=None,
    ) -> dict:
        role = ctx.call_role
        self.calls.append((role, system_prompt, user_prompt))
        resp = self._responses.get(role)
        if resp is None:
            raise KeyError(f"mock has no response for call_role {role!r}")
        if callable(resp):
            resp = resp(system_prompt, user_prompt)
        if validator_extras is not None:
            validator_extras(resp)
        return resp


def _self_test() -> None:
    # Canned inference output — uses v6 user_intent (express_emotion).
    inference_resp = {
        "user_intent": {
            "intent": "express_emotion",
            "supporting_utterance_span": "I can't keep this up",
        },
        "current_problems": [
            {"problem_name": "academic_pressure",
             "explanation": "x", "supporting_utterance_span": "finals"},
            {"problem_name": "sleep_problems",
             "explanation": "x", "supporting_utterance_span": "can't sleep"},
        ],
        "main_problem": {
            "problem_name": "academic_pressure",
            "explanation": "x", "supporting_utterance_span": "finals",
        },
        "problem_attribute_entries": [
            {"problem_name": "academic_pressure",
             "attribute_name": "perceived_severity",
             "inferred_information": "workload unsustainable",
             "concise_explanation": "x", "supporting_utterance_span": "y"},
            {"problem_name": "academic_pressure",
             "attribute_name": "goal",
             "inferred_information": "get through finals without a crash",
             "concise_explanation": "stated goal",
             "supporting_utterance_span": None},
            {"problem_name": "sleep_problems",
             "attribute_name": "triggers",
             "inferred_information": "late cramming prevents sleep",
             "concise_explanation": "x", "supporting_utterance_span": "y"},
        ],
        "problem_cooccurrence_connections": [
            {"problem_1": "academic_pressure", "problem_2": "sleep_problems",
             "concise_explanation": "same turn", "supporting_utterance_span": "y"},
        ],
        "problem_attribute_connections": [
            {"problem_1": "academic_pressure", "attribute_1": "triggers",
             "problem_2": "sleep_problems", "attribute_2": "triggers",
             "relation_type": "shared_trigger",
             "connection_explanation": "cramming drives both",
             "supporting_utterance_span": "all-nighters", "confidence": "high"},
        ],
    }
    recompute_resp = {
        "attribute_level_updates": [
            {"problem_name": "academic_pressure",
             "attribute_name": "perceived_severity",
             "new_level": "high",
             "reasoning": "escalated language"},
        ],
        "ttm_stage_updates": [
            {"problem_name": "academic_pressure",
             "new_ttm_stage": "contemplation",
             "reasoning": "user acknowledging problem"},
            {"problem_name": "sleep_problems",
             "new_ttm_stage": "precontemplation",
             "reasoning": "no movement yet"},
        ],
    }
    # 3-field response schema — picks complex_reflection (a contemplation
    # candidate) and support (a COMMON candidate).
    response_resp = {
        "reasoning": (
            "Where: contemplation, express_emotion, needs space. "
            "Which: complex_reflection to develop discrepancy, support to anchor. "
            "Evidence: triggers (all-nighters), goal (finish without crash). "
            "Entry: name the body's signal, then sit with it."
        ),
        "evidence_used": [
            {"source": "hbm_attribute.triggers (main_problem=academic_pressure, s1t1)",
             "content": "late cramming prevents sleep"},
            {"source": "hbm_attribute.goal (main_problem=academic_pressure, s1t1)",
             "content": "get through finals without a crash"},
        ],
        "final_response": (
            "Pulling all-nighters only works for so long before the system "
            "pushes back, and you've hit that point. Take a breath."
        ),
    }

    client = _MockClient({
        "inference": inference_resp,
        "recompute": recompute_resp,
        "response_v6": response_resp,
    })

    graph = ProblemGraphV6(profile_id="TEST")
    out = v6_turn_fn(
        client=client, profile_id="TEST",
        session_id=1, turn_id=1,
        user_message="I'm pulling all-nighters for finals and can't sleep. I can't keep this up.",
        recent_turns=[],
        last_system_message=None,
        prior_session_summary=None,  # ignored under v6 redesign
        graph=graph,
        last_n_turns=5,
    )

    # Three LLM calls fired in order.
    roles = [c[0] for c in client.calls]
    assert roles == ["inference", "recompute", "response_v6"], roles

    # Graph mutated correctly.
    assert set(graph.problems) == {"academic_pressure", "sleep_problems"}
    ap = graph.problems["academic_pressure"]
    assert ap.level_attributes["perceived_severity"].current_level == "high"
    assert ap.current_ttm_stage == "contemplation"
    assert ap.goal == "get through finals without a crash"
    assert "goal" in ap.non_level_attributes
    sp_ = graph.problems["sleep_problems"]
    assert "triggers" in sp_.non_level_attributes
    assert sp_.current_ttm_stage == "precontemplation"

    # Edge with both cooc + attr-connection; weight > 0.
    assert len(graph.edges) == 1
    edge = next(iter(graph.edges.values()))
    assert len(edge.cooccurrence_entries) == 1
    assert len(edge.attribute_connection_entries) == 1
    assert edge.weight > 0

    # New v6-redesign output contract.
    assert out["trace"]["main_problem"] == "academic_pressure"
    assert out["trace"]["user_intent"] == "express_emotion"
    assert out["trace"]["ttm_stage"] == "contemplation"
    assert out["trace"]["transition_target"] == "contemplation → preparation"
    # candidate codes ⊆ chosen MISC codes; chosen pulled from `reasoning`
    cand = set(out["trace"]["all_candidate_codes"])
    chosen = set(out["trace"]["chosen_misc_codes"])
    assert chosen <= cand, f"chosen {chosen} not in candidates {cand}"
    assert "complex_reflection" in chosen
    assert "support" in chosen
    # Response object carries the 3 redesign fields.
    rsp = out["response"]
    assert "reasoning" in rsp and "evidence_used" in rsp and "final_response" in rsp
    assert rsp["final_response"].endswith("Take a breath.")
    # Past-two-turns diversity hint defaults to empty when no prev traces.
    assert out["past_two_turns"] == []
    # Bundle is now skipped on the matrix path (analysis-only artifact);
    # only the UI calls it directly when needed.
    assert out["bundle"] is None
    # Candidate bundle is the new single-stream shape.
    cb = out["candidate_bundle"]
    assert cb["main_problem"] == "academic_pressure"
    assert cb["common_candidates"] and cb["stage_specific_candidates"]

    # --- Past-two-turns extraction from prev traces ---
    prev = [
        # t1: chose complex_reflection
        {"turn_id": 1, "trace": {"main_problem": "academic_pressure"},
         "response": {"reasoning": "use complex_reflection now"}},
        # t2: chose evoke + support
        {"turn_id": 2, "trace": {"main_problem": "academic_pressure"},
         "response": {"reasoning": "evoke change talk; support throughout"}},
    ]
    p2 = _collect_past_two_turns(prev)
    # Oldest first: t-2 (turn 1) then t-1 (turn 2)
    assert [e["turn_offset"] for e in p2] == [-2, -1]
    assert p2[0]["strategies"] == ["complex_reflection"]
    assert set(p2[1]["strategies"]) == {"evoke", "support"}

    # Anti-pattern names ARE picked up too (so analysis can flag them).
    bad_prev = [
        {"turn_id": 1, "trace": {"main_problem": "x"},
         "response": {"reasoning": "use confront aggressively"}},
    ]
    p_bad = _collect_past_two_turns(bad_prev)
    assert p_bad[0]["strategies"] == ["confront"]

    # --- Cold-start path: empty inference → no recompute call fired. ---
    empty_inference = {
        "user_intent": {"intent": "small_talk", "supporting_utterance_span": None},
        "current_problems": [], "main_problem": None,
        "problem_attribute_entries": [],
        "problem_cooccurrence_connections": [],
        "problem_attribute_connections": [],
    }
    cold_response = {
        "reasoning": (
            "Where: cold start, small_talk. "
            "Which: support, facilitate to stay present. "
            "Evidence: nothing yet from this user. "
            "Entry: warm acknowledgment without questions."
        ),
        "evidence_used": [],
        "final_response": "I'm here whenever you want to talk.",
    }
    client2 = _MockClient({
        "inference": empty_inference,
        "response_v6": cold_response,
    })
    g2 = ProblemGraphV6(profile_id="TEST2")
    out2 = v6_turn_fn(
        client=client2, profile_id="TEST2",
        session_id=1, turn_id=1,
        user_message="hi",
        recent_turns=[], last_system_message=None,
        prior_session_summary=None, graph=g2, last_n_turns=5,
    )
    roles2 = [c[0] for c in client2.calls]
    assert "recompute" not in roles2, \
        f"recompute should be skipped on empty inference, got roles={roles2}"
    assert out2["recompute"].get("_skipped_no_current_problems") is True
    assert not g2.problems
    assert not g2.edges
    assert out2["trace"]["main_problem"] is None
    # Cold-start: stage is None, transition_target None, but COMMON
    # candidates still emitted (support, facilitate).
    assert out2["trace"]["ttm_stage"] is None
    assert out2["trace"]["transition_target"] is None
    assert set(out2["trace"]["all_candidate_codes"]) == {"support", "facilitate"}
    assert set(out2["trace"]["chosen_misc_codes"]) == {"support", "facilitate"}

    print("v6_turn_fn (redesign) self-test PASSED")


if __name__ == "__main__":
    _self_test()
