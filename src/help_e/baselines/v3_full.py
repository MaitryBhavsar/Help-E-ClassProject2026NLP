"""V3 multi-agent turn function — V7's structure minus HBM.

Agent roster (meaningful names; legacy "Agent N" labels in parens):

  - IntentAgent          (Agent 1)  — user_intent + MI for user
  - InferenceAgent       (Agent 2)  — graph mutation (no attributes)
  - ProblemAgent         (Agent 3)  — per-problem summary + TTM +
                                       system_intent in ONE call
  - EdgeSummaryAgent     (Agent 3c) — per-edge running summary (reused
                                       from V7)
  - ResponseAgent        (Agent 5)  — R1→R4 final response
  - RollingSummaryAgent  (Agent X)  — end-of-turn 5-turn summary

Per-turn pipeline:

  Phase 1 (parallel):    IntentAgent (small) + InferenceAgent (BIG)
  Phase 2 (Python):      Apply inference → register problems, append
                         per-problem audit entries, stack connection_entries.
  Phase 3+3c (parallel):
     ProblemAgent × N current_problems (small)
     + EdgeSummaryAgent × E edges-with-new-entries (small)
  Phase 4 (Python):      Recompute edge weights + assemble V3
                         evidence_pack via weighted-degree centrality.
  Phase 5 (sync, BIG):   ResponseAgent — R1 → R2 → R3 → R4.
  Phase 6 (sync):        RollingSummaryAgent.

Total LLM calls per turn: 1 (Intent) + 1 (Inference, BIG) + N
(ProblemAgent, small, parallel) + E (EdgeSummary, small, parallel) +
1 (Response, BIG) + 1 (Rolling) = ``3 + N + E``. Two BIG calls; rest
small. Same shape as V7's per-turn cost; V3 collapses V7's 3a×N + 3b×M
into a single ProblemAgent×N pass.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from typing import Any, Optional

from ..config import (
    AGENT2_RECENT_TURNS_N,
    AGENT5_PAST_TURNS_HINT_N,
    EDGE_THRESHOLD_TAU_V7,
)
from ..graph_v3 import (
    ConnectionEntryV3,
    ProblemAuditEntryV3,
    ProblemEdgeV3,
    ProblemGraphV3,
)
from ..graph_v6 import global_turn_idx
from ..instruction_response_v3 import (
    ResponseV3Inputs,
    run_response_v3,
)
from ..llm_client import CallContext, LLMClient
from ..prompts.agent1_user_intent import Agent1Inputs, run_agent1
from ..prompts.agent2_inference_v3 import Agent2V3Inputs, run_agent2_v3
from ..prompts.agent3_problem_v3 import (
    Agent3ProblemV3Inputs,
    run_agent3_problem_v3,
)
from ..prompts.agent3c_edge_summary import Agent3cInputs, run_agent3c
from ..prompts.agentX_rolling_summary import AgentXInputs, run_agentx


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# past_two_turns hint (same shape as V7/V8)
# ---------------------------------------------------------------------------


def _collect_past_two_turns_v3(prev_traces: list[dict]) -> list[dict]:
    if not prev_traces:
        return []
    last = prev_traces[-AGENT5_PAST_TURNS_HINT_N:]
    out: list[dict] = []
    for offset, tr in enumerate(reversed(last), start=1):
        resp = tr.get("response") or {}
        strategies = [
            c for c in (
                resp.get("mi_for_user_intent_used"),
                resp.get("mi_for_system_intent_used"),
            ) if c
        ]
        out.append({
            "turn_offset": -offset,
            "main_problem": (tr.get("trace") or {}).get("main_problem"),
            "strategies": strategies,
        })
    out.reverse()
    return out


# ---------------------------------------------------------------------------
# Phase 2 — apply inference to graph (Python only)
# ---------------------------------------------------------------------------


def _apply_inference_to_graph_v3(
    graph: ProblemGraphV3,
    inference_out: dict,
    *,
    session_id: int,
    turn_id: int,
) -> tuple[
    Optional[str],
    int,
    int,
    dict[tuple[str, str], list[dict]],
]:
    """Apply InferenceAgent's output to the V3 graph. Returns:
      (new_main_name,
       n_connection_entries_added,
       n_useful_connection_entries_added,
       new_entries_per_edge)  ← used by Phase 3c
    """
    cps = inference_out.get("current_problems") or []
    main_obj = inference_out.get("main_problem")

    # Reset previous_main_for_session flags.
    for p in graph.problems.values():
        p.previous_main_for_session = False

    new_main = main_obj["problem_name"] if main_obj else None
    for cp in cps:
        name = cp["problem_name"]
        graph.get_or_create_problem(name, first_mentioned=(session_id, turn_id))
        graph.problems[name].last_mentioned = (session_id, turn_id)
    if new_main and new_main in graph.problems:
        graph.problems[new_main].previous_main_for_session = True

    # Append per-problem audit entries.
    for entry in inference_out.get("problem_evidence") or []:
        graph.append_problem_audit(
            problem_name=entry["problem_name"],
            entry=ProblemAuditEntryV3(
                session_id=session_id, turn_id=turn_id,
                inferred_information=entry["inferred_information"],
                why=entry["why"],
                supporting_utterance_span=entry.get("supporting_utterance_span") or "",
            ),
        )

    # Stack connection entries + bucket by edge for Phase 3c.
    n_added = 0
    n_useful = 0
    new_entries_per_edge: dict[tuple[str, str], list[dict]] = {}
    for c in inference_out.get("problem_problem_connections") or []:
        ce = ConnectionEntryV3(
            session_id=session_id, turn_id=turn_id,
            relation_type=c["relation_type"],
            why=c["why"],
            supporting_quote=c.get("supporting_utterance_span"),
        )
        useful = graph.append_connection_entry(
            problem_a=c["problem_1"], problem_b=c["problem_2"],
            entry=ce,
        )
        n_added += 1
        n_useful += useful
        key = ProblemEdgeV3.canonical_pair(c["problem_1"], c["problem_2"])
        # EdgeSummaryAgent reuses the V7 prompt which expects
        # attribute_a / attribute_b fields; V3 has no attributes, so we
        # synthesize "(problem-level)" placeholders so the prompt
        # renders cleanly without confusing the agent.
        new_entries_per_edge.setdefault(key, []).append({
            "session_id": session_id,
            "turn_id": turn_id,
            "attribute_a": "(problem-level)",
            "attribute_b": "(problem-level)",
            "relation_type": c["relation_type"],
            "why": c["why"],
            "supporting_quote": c.get("supporting_utterance_span"),
        })

    return new_main, n_added, n_useful, new_entries_per_edge


# ---------------------------------------------------------------------------
# Per-problem ProblemAgent input builder
# ---------------------------------------------------------------------------


def _build_problem_agent_inputs(
    graph: ProblemGraphV3,
    *,
    problem_name: str,
    is_main: bool,
    inference_evidence: list[dict],
    session_id: int,
    turn_id: int,
) -> Agent3ProblemV3Inputs:
    p = graph.problems[problem_name]
    new_evidence: list[dict] = []
    for e in inference_evidence:
        if e["problem_name"] != problem_name:
            continue
        new_evidence.append({
            "session_id": session_id,
            "turn_id": turn_id,
            "inferred_information": e["inferred_information"],
            "why": e["why"],
            "supporting_utterance_span": e.get("supporting_utterance_span") or "",
        })
    return Agent3ProblemV3Inputs(
        problem_name=problem_name,
        is_main_problem=is_main,
        current_session=session_id,
        current_turn=turn_id,
        existing_state={
            "summary_text": p.summary_text,
            "current_ttm_stage": p.current_ttm_stage,
            "ttm_reasoning": p.ttm_reasoning,
            "system_intent": p.system_intent,
            "mi_for_system_intent": p.mi_for_system_intent,
        },
        new_evidence=new_evidence,
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
    prior_session_summary: Optional[str] = None,
    graph: ProblemGraphV3,
    last_n_turns: int = 5,
    previous_turn_traces: Optional[list[dict]] = None,
) -> dict:
    """Execute one V3 turn against `graph` (a ProblemGraphV3)."""
    t0 = time.monotonic()
    timings: dict[str, float] = {}

    previous_main: Optional[str] = next(
        (n for n, p in graph.problems.items()
         if p.previous_main_for_session),
        None,
    )
    previous_active_problems: list[str] = list(graph.problems.keys())

    # ------------------------------------------------------------------ #
    # Phase 1 — IntentAgent + InferenceAgent in parallel                 #
    # ------------------------------------------------------------------ #
    a1_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="agent1_user_intent",
    )
    a2_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="agent2_inference_v3",
    )

    def _run_a1():
        s = time.monotonic()
        out = run_agent1(
            client=client, ctx=a1_ctx,
            inputs=Agent1Inputs(
                rolling_summary_5turns=graph.rolling_summary_5turns,
                current_message=user_message,
            ),
        )
        timings["agent_intent"] = time.monotonic() - s
        return out

    def _run_a2():
        s = time.monotonic()
        a2_recent_turns = recent_turns[-(AGENT2_RECENT_TURNS_N * 2):]
        out = run_agent2_v3(
            client=client, ctx=a2_ctx,
            inputs=Agent2V3Inputs(
                current_message=user_message,
                recent_turns=a2_recent_turns,
                previous_active_problems=previous_active_problems,
                previous_main_problem=previous_main,
            ),
        )
        timings["agent_inference"] = time.monotonic() - s
        return out

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="v3_p1") as ex:
        f_a1 = ex.submit(_run_a1)
        f_a2 = ex.submit(_run_a2)
        agent1_out = f_a1.result()
        agent2_out = f_a2.result()

    # ------------------------------------------------------------------ #
    # Phase 2 — Apply inference to the graph                             #
    # ------------------------------------------------------------------ #
    (
        new_main,
        n_conn_added,
        n_conn_useful,
        new_entries_per_edge,
    ) = _apply_inference_to_graph_v3(
        graph, agent2_out, session_id=session_id, turn_id=turn_id,
    )
    current_problem_names = [
        cp["problem_name"] for cp in agent2_out.get("current_problems") or []
    ]

    # ------------------------------------------------------------------ #
    # Phase 3 + 3c — ProblemAgent per current_problem +                  #
    # EdgeSummaryAgent per edge with new entries, all in one pool.       #
    # ------------------------------------------------------------------ #
    inference_evidence = agent2_out.get("problem_evidence") or []

    problem_agent_outs: dict[str, dict] = {}
    edge_summary_outs: dict[tuple[str, str], dict] = {}

    s_phase = time.monotonic()
    n_problems = len(current_problem_names)
    n_edges = len(new_entries_per_edge)
    n_workers = max(1, min(6, n_problems + n_edges))

    if n_problems > 0 or n_edges > 0:
        with ThreadPoolExecutor(
            max_workers=n_workers, thread_name_prefix="v3_p3",
        ) as ex:
            # ProblemAgent × N
            problem_futures: dict[str, Any] = {}
            for name in current_problem_names:
                ap_ctx = CallContext(
                    profile_id=profile_id, session_id=session_id, system=system,
                    turn_id=turn_id, call_role="agent3_problem_v3",
                )
                inputs = _build_problem_agent_inputs(
                    graph, problem_name=name,
                    is_main=(name == new_main),
                    inference_evidence=inference_evidence,
                    session_id=session_id, turn_id=turn_id,
                )
                problem_futures[name] = ex.submit(
                    run_agent3_problem_v3,
                    client=client, ctx=ap_ctx, inputs=inputs,
                )

            # EdgeSummaryAgent × E (reused V7 prompt)
            edge_futures: dict[tuple[str, str], Any] = {}
            for edge_key, entries in new_entries_per_edge.items():
                p_a, p_b = edge_key
                a3c_ctx = CallContext(
                    profile_id=profile_id, session_id=session_id, system=system,
                    turn_id=turn_id, call_role="agent3c_edge_summary",
                )
                edge = graph.edges.get(edge_key)
                existing_summary = edge.summary_text if edge is not None else ""
                edge_futures[edge_key] = ex.submit(
                    run_agent3c, client=client, ctx=a3c_ctx,
                    inputs=Agent3cInputs(
                        problem_1=p_a, problem_2=p_b,
                        current_session=session_id, current_turn=turn_id,
                        existing_summary_text=existing_summary,
                        new_entries=entries,
                    ),
                )

            for name, fut in problem_futures.items():
                problem_agent_outs[name] = fut.result()
            for edge_key, fut in edge_futures.items():
                edge_summary_outs[edge_key] = fut.result()
    timings["agent_3_max"] = time.monotonic() - s_phase

    # Apply ProblemAgent outputs to the graph.
    n_ttm_changes = 0
    n_useful_problem_updates = 0
    for name, out in problem_agent_outs.items():
        prior_stage = graph.problems[name].current_ttm_stage
        # Conservative gate: if confidence is "low", DO NOT change the
        # stage even if the agent emitted a new one. Spec from the
        # problem-update rules in the prompt; enforced here for safety.
        new_stage = out.get("current_ttm_stage", prior_stage)
        confidence = out.get("ttm_change_confidence", "low")
        if confidence == "low":
            new_stage = prior_stage
        graph.update_problem(
            problem_name=name,
            summary_text=out.get("summary_text", graph.problems[name].summary_text),
            new_ttm_stage=new_stage,
            ttm_reasoning=out.get("ttm_reasoning", ""),
            ttm_change_confidence=confidence,
            system_intent=out.get(
                "system_intent", graph.problems[name].system_intent or "",
            ),
            mi_for_system_intent=out.get("mi_for_system_intent"),
        )
        if new_stage != prior_stage:
            n_ttm_changes += 1
        if int(out.get("useful", 0)) == 1:
            n_useful_problem_updates += 1

    # Apply EdgeSummaryAgent outputs.
    n_edge_summaries_updated = 0
    n_edge_summaries_useful = 0
    for edge_key, out in edge_summary_outs.items():
        p_a, p_b = edge_key
        graph.update_edge_summary(
            problem_a=p_a, problem_b=p_b,
            summary_text=out.get("summary_text", ""),
        )
        n_edge_summaries_updated += 1
        if int(out.get("useful", 0)) == 1:
            n_edge_summaries_useful += 1

    # ------------------------------------------------------------------ #
    # Phase 4 — recompute edge weights + assemble V3 evidence_pack       #
    # ------------------------------------------------------------------ #
    s_phase = time.monotonic()
    graph.recompute_all_edge_weights(global_turn_idx(session_id, turn_id))
    evidence_pack = graph.assemble_evidence_pack(
        main_problem=new_main,
        current_problems=current_problem_names,
        tau=EDGE_THRESHOLD_TAU_V7,
    )
    timings["agent_4_python"] = time.monotonic() - s_phase

    n_connections_touching_main = 0
    if new_main:
        for c in evidence_pack.get("problem_problem_connections") or []:
            if new_main in (c["a"], c["b"]):
                n_connections_touching_main += 1

    # ------------------------------------------------------------------ #
    # Phase 5 — ResponseAgent (BIG)                                      #
    # ------------------------------------------------------------------ #
    past_two_turns = _collect_past_two_turns_v3(previous_turn_traces or [])
    a5_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="agent5_response_v3",
    )
    s_phase = time.monotonic()
    response_out = run_response_v3(
        client=client, ctx=a5_ctx,
        inputs=ResponseV3Inputs(
            user_intent=agent1_out.get("user_intent", "small_talk"),
            user_intent_phrase=agent1_out.get("user_intent_phrase", ""),
            mi_for_user_intent=agent1_out.get("mi_for_user_intent", "support"),
            evidence_pack=evidence_pack,
            past_two_turns=past_two_turns,
            current_user_message=user_message,
        ),
    )
    timings["agent_response"] = time.monotonic() - s_phase

    # Tally evidence_used types.
    ev_used = response_out.get("evidence_used") or []
    n_ev_by_type: dict[str, int] = {
        "problem": 0,
        "problem_problem_connection": 0,
        "persona": 0,
        "recent_turn": 0,
        # legacy keys ResponseAgent might still emit if it leaked V7 vocab:
        "attribute": 0,
        "attribute_connection": 0,
    }
    for e in ev_used:
        t = (e or {}).get("type")
        if t in n_ev_by_type:
            n_ev_by_type[t] += 1

    # ------------------------------------------------------------------ #
    # Phase 6 — RollingSummaryAgent                                      #
    # ------------------------------------------------------------------ #
    final_response = response_out.get("final_response", "") or ""
    ax_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="agentX_rolling_summary",
    )
    s_phase = time.monotonic()
    try:
        agentx_out = run_agentx(
            client=client, ctx=ax_ctx,
            inputs=AgentXInputs(
                previous_summary=graph.rolling_summary_5turns,
                new_user_message=user_message,
                new_bot_message=final_response,
                current_session=session_id,
                current_turn=turn_id,
            ),
        )
        graph.set_rolling_summary(agentx_out.get("rolling_summary_5turns", ""))
    except Exception as e:
        log.exception("agentX_rolling_summary failed: %s", e)
    timings["agent_X"] = time.monotonic() - s_phase

    # ------------------------------------------------------------------ #
    # Trace + diagnostics                                                #
    # ------------------------------------------------------------------ #
    trace = {
        "main_problem": new_main,
        "current_problems": current_problem_names,
        "user_intent": agent1_out.get("user_intent"),
        "user_intent_phrase": agent1_out.get("user_intent_phrase"),
        "mi_for_user_intent": agent1_out.get("mi_for_user_intent"),
        "ttm_stage": (
            graph.problems[new_main].current_ttm_stage if new_main else None
        ),
        "system_intent": (
            graph.problems[new_main].system_intent if new_main else None
        ),
        "mi_for_system_intent": (
            graph.problems[new_main].mi_for_system_intent if new_main else None
        ),
        "agent3_problem_outputs": problem_agent_outs,
        "agent3c_outputs_per_edge": {
            f"{a}|{b}": out for (a, b), out in edge_summary_outs.items()
        },
        "n_problems_with_new_evidence": len(
            {e["problem_name"] for e in inference_evidence}
        ),
        "n_problem_summaries_with_useful_new_info": n_useful_problem_updates,
        "n_ttm_stages_changed": n_ttm_changes,
        "n_connection_entries_added": n_conn_added,
        "n_useful_connection_entries_added": n_conn_useful,
        "n_edge_summaries_updated": n_edge_summaries_updated,
        "n_edge_summaries_with_useful_new_info": n_edge_summaries_useful,
        "n_connections_touching_main": n_connections_touching_main,
        "used_evidence": bool(response_out.get("used_evidence", False)),
        "n_evidence_used_total": len(ev_used),
        "n_evidence_used_by_type": n_ev_by_type,
        # V3 has no attribute audits — count "real audits" as the
        # response's references to per-problem and per-connection
        # evidence (closest analog to V7/V8's attribute citations).
        "n_audits_used_in_response": (
            n_ev_by_type["problem"]
            + n_ev_by_type["problem_problem_connection"]
            + n_ev_by_type["attribute"]
            + n_ev_by_type["attribute_connection"]
        ),
    }

    diagnostics = {
        "n_llm_calls_total": (
            2  # IntentAgent + InferenceAgent
            + len(problem_agent_outs)   # ProblemAgent × N
            + len(edge_summary_outs)    # EdgeSummaryAgent × E
            + 1  # ResponseAgent
            + 1  # RollingSummaryAgent
        ),
        "n_llm_calls_big": 2,  # InferenceAgent + ResponseAgent
        "n_llm_calls_small": (
            1  # IntentAgent
            + len(problem_agent_outs)
            + len(edge_summary_outs)
            + 1  # RollingSummaryAgent
        ),
        "agent_wall_times": {k: round(v, 3) for k, v in timings.items()},
        "turn_total_wall_time_s": round(time.monotonic() - t0, 3),
    }

    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "user_message": user_message,
        "agent1": agent1_out,
        "inference": agent2_out,
        "agent2": agent2_out,
        "evidence_pack": evidence_pack,
        "past_two_turns": past_two_turns,
        "response": response_out,
        "trace": trace,
        "diagnostics": diagnostics,
    }


__all__ = ["v3_turn_fn"]
