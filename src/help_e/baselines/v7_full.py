"""v7 multi-agent turn function.

Agent roster (meaningful names; legacy "Agent N" labels in parens):

  - IntentAgent          (Agent 1)  — user_intent + MI for user
  - InferenceAgent       (Agent 2)  — graph mutation extraction
  - AttributeAgent       (Agent 3a) — per-attribute summary + level
  - StageAgent           (Agent 3b) — TTM + system_intent + MI for system
  - EdgeSummaryAgent     (Agent 3c) — per-edge running summary
  - ResponseAgent        (Agent 5)  — R1→R4 final response
  - RollingSummaryAgent  (Agent X)  — end-of-turn 5-turn summary

Per-turn pipeline:

  Phase 1 (parallel):
    - IntentAgent (small):    user_intent + mi_for_user_intent
    - InferenceAgent (BIG):   current_problems + main_problem +
                              problem_attribute_entries +
                              problem_attribute_connections

  Phase 2 (Python):
    - Apply InferenceAgent's output to the v7 graph: register
      problems, append audit entries, stack connection entries
      (with Python usefulness flag).

  Phase 3 (parallel × N current_problems):
    - AttributeAgent (small): per-problem attribute summary + level
      update. Conservative level-update rules + per-update useful flag.

  Phase 3c (parallel × E edges that received new entries this turn):
    - EdgeSummaryAgent (small): per-edge running summary update.
      Nothing is dropped from connection history — older entries
      persist via the running ``edge.summary_text``. Falls back to
      deterministic Python concat on LLM failure so the "summarize,
      never drop" guarantee holds.

  Phase 4 (parallel × M ≤ N where any level changed):
    - StageAgent (small, conditional): TTM + system_intent +
      mi_for_system_intent. Carries previous TTM forward unchanged
      for problems where no level changed in 3a.

  Phase 5 (Python):
    - Recompute edge weights with recency decay.
    - Assemble evidence_pack via graph.assemble_evidence_pack(...).

  Phase 6 (sync, BIG):
    - ResponseAgent: R1 → R2 → R3 → R4 in one call.

  Phase 7 (end-of-turn, sync):
    - RollingSummaryAgent (small): refresh the rolling 5-turn summary
      for next turn's IntentAgent / ResponseAgent input.

The graph is mutated in place; the caller (`session_driver_v6`) is
responsible for persisting it after the turn completes.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from ..config import (
    AGENT2_RECENT_TURNS_N,
    AGENT5_PAST_TURNS_HINT_N,
    EDGE_THRESHOLD_TAU_V7,
    LEVEL_ATTR_TYPES,
    MISC_CODES,
    MISC_INCONSISTENT_CODES,
)
from ..graph_v6 import AttributeEvidenceEntry, global_turn_idx
from ..graph_v7 import ConnectionEntryV7, ProblemEdgeV7, ProblemGraphV7
from ..instruction_response_v7 import (
    ResponseV7Inputs,
    run_response_v7,
)
from ..llm_client import CallContext, LLMClient, LLMStructuredError
from ..prompts.agent1_user_intent import Agent1Inputs, run_agent1
from ..prompts.agent2_inference_v7 import (
    Agent2Inputs,
    build_agent2_carry_forward_output,
    run_agent2,
)
from ..prompts.agent3a_attr_update import Agent3aInputs, run_agent3a
from ..prompts.agent3b_ttm_intent import Agent3bInputs, run_agent3b
from ..prompts.agent3c_edge_summary import Agent3cInputs, run_agent3c
from ..prompts.agentX_rolling_summary import AgentXInputs, run_agentx


log = logging.getLogger(__name__)

_LEVEL_SET = frozenset(LEVEL_ATTR_TYPES)


# ---------------------------------------------------------------------------
# past_two_turns hint for Agent 5
# ---------------------------------------------------------------------------


def _collect_past_two_turns_v7(prev_traces: list[dict]) -> list[dict]:
    """Same shape as v6's hint, but reads v7's chosen MISC codes from the
    response output rather than scraping reasoning text. Returns
    [{turn_offset, main_problem, strategies}, ...] oldest-to-newest.
    """
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
# Graph-mutation helpers (Python, no LLM)
# ---------------------------------------------------------------------------


def _apply_inference_to_graph_v7(
    graph: ProblemGraphV7,
    inference_out: dict,
    *,
    session_id: int,
    turn_id: int,
) -> tuple[Optional[str], int, int, dict[tuple[str, str], list[dict]]]:
    """Apply Agent 2's output to the v7 graph.

    Returns:
      (new_main_name,
       n_connection_entries_added,
       n_useful_connection_entries_added,
       new_entries_per_edge: { canonical_edge_key: [entry_dict, ...] }).

    `new_entries_per_edge` is consumed by Phase 3c (Agent 3c) to refresh
    each affected edge's running summary_text. Each entry_dict has shape
    {session_id, turn_id, attribute_a, attribute_b, relation_type, why,
     supporting_quote}.
    """
    cps = inference_out.get("current_problems") or []
    main_obj = inference_out.get("main_problem")

    # Register problems (clear previous_main_for_session flags first).
    for p in graph.problems.values():
        p.previous_main_for_session = False

    new_main = main_obj["problem_name"] if main_obj else None
    for cp in cps:
        name = cp["problem_name"]
        graph.get_or_create_problem(name, first_mentioned=(session_id, turn_id))
        graph.problems[name].last_mentioned = (session_id, turn_id)
    if new_main and new_main in graph.problems:
        graph.problems[new_main].previous_main_for_session = True

    # Append audit entries for each new attribute info.
    for entry in inference_out.get("problem_attribute_entries") or []:
        problem = entry["problem_name"]
        attr = entry["attribute_name"]
        graph.append_audit_entry(
            problem_name=problem, attr_name=attr,
            entry=AttributeEvidenceEntry(
                session_id=session_id, turn_id=turn_id,
                inferred_information=entry["inferred_information"],
                concise_explanation=entry["concise_explanation"],
                supporting_utterance_span=entry.get("supporting_utterance_span"),
            ),
        )

    # Stack connection entries (Python; has_relation_type computes useful=0/1).
    # Also bucket new entries per canonical edge key so Phase 3c can issue
    # one Agent 3c call per affected edge.
    n_added = 0
    n_useful = 0
    new_entries_per_edge: dict[tuple[str, str], list[dict]] = {}
    for c in inference_out.get("problem_attribute_connections") or []:
        ce = ConnectionEntryV7(
            turn_id=turn_id, session_id=session_id,
            attribute_a=c["attribute_1"], attribute_b=c["attribute_2"],
            relation_type=c["relation_type"],
            why=c["connection_explanation"],
            supporting_quote=c.get("supporting_utterance_span"),
        )
        useful = graph.append_connection_entry(
            problem_a=c["problem_1"], problem_b=c["problem_2"],
            entry=ce,
        )
        n_added += 1
        n_useful += useful
        key = ProblemEdgeV7.canonical_pair(c["problem_1"], c["problem_2"])
        new_entries_per_edge.setdefault(key, []).append({
            "session_id": session_id,
            "turn_id": turn_id,
            "attribute_a": c["attribute_1"],
            "attribute_b": c["attribute_2"],
            "relation_type": c["relation_type"],
            "why": c["connection_explanation"],
            "supporting_quote": c.get("supporting_utterance_span"),
        })

    return new_main, n_added, n_useful, new_entries_per_edge


def _build_agent3a_inputs(
    graph: ProblemGraphV7,
    *,
    problem_name: str,
    turn_attribute_entries: list[dict],
    turn_connections: list[dict],
    session_id: int,
    turn_id: int,
) -> Agent3aInputs:
    """Build the per-problem Agent 3a input bundle."""
    # New attribute info touching THIS problem.
    new_attrs = [
        {
            "attribute_name": e["attribute_name"],
            "inferred_information": e["inferred_information"],
            "concise_explanation": e["concise_explanation"],
            "supporting_utterance_span": e.get("supporting_utterance_span"),
        }
        for e in turn_attribute_entries
        if e["problem_name"] == problem_name
    ]
    touched_attrs = {e["attribute_name"] for e in new_attrs}

    # Existing records for ONLY the touched attributes.
    p = graph.problems[problem_name]
    existing: list[dict] = []
    for attr in touched_attrs:
        if attr in p.level_attributes:
            s = p.level_attributes[attr]
            existing.append({
                "attribute_name": attr,
                "current_level": s.current_level,
                "level_reasoning": s.level_reasoning,
                "summary_text": s.summary_text,
            })
        elif attr in p.non_level_attributes:
            s_nl = p.non_level_attributes[attr]
            existing.append({
                "attribute_name": attr,
                "current_level": "unknown",
                "level_reasoning": "",
                "summary_text": s_nl.summary_text,
            })
        else:
            existing.append({
                "attribute_name": attr,
                "current_level": "unknown",
                "level_reasoning": "",
                "summary_text": "",
            })

    # Connections that TOUCH this problem, rewritten so attribute_a is
    # this problem's side.
    rewritten: list[dict] = []
    for c in turn_connections:
        p1, a1, p2, a2 = (
            c["problem_1"], c["attribute_1"], c["problem_2"], c["attribute_2"]
        )
        if p1 == problem_name:
            rewritten.append({
                "attribute_a": a1, "attribute_b": a2,
                "relation_type": c["relation_type"],
                "why": c["connection_explanation"],
                "supporting_quote": c.get("supporting_utterance_span"),
                "other_problem": p2,
            })
        elif p2 == problem_name:
            rewritten.append({
                "attribute_a": a2, "attribute_b": a1,
                "relation_type": c["relation_type"],
                "why": c["connection_explanation"],
                "supporting_quote": c.get("supporting_utterance_span"),
                "other_problem": p1,
            })

    return Agent3aInputs(
        problem_name=problem_name,
        current_session=session_id,
        current_turn=turn_id,
        existing_records=existing,
        new_attribute_info=new_attrs,
        new_connections_touching_this_problem=rewritten,
    )


def _apply_agent3a_to_graph(
    graph: ProblemGraphV7, problem_name: str, agent3a_out: dict,
) -> tuple[int, int]:
    """Apply 3a updates to the graph. Returns (n_levels_changed,
    n_useful_summary_updates). A "level change" is detected by comparing
    the new current_level against whatever was on the graph before the
    call.
    """
    p = graph.problems[problem_name]
    n_changed = 0
    n_useful = 0
    for u in agent3a_out.get("attribute_updates") or []:
        attr = u["attribute_name"]
        if attr in _LEVEL_SET:
            prior_level = (
                p.level_attributes[attr].current_level
                if attr in p.level_attributes else "unknown"
            )
            graph.update_level_attribute(
                problem_name=problem_name, attr_name=attr,
                summary_text=u["summary_text"],
                current_level=u["current_level"],
                level_reasoning=u["level_reasoning"],
                level_change_confidence=u["level_change_confidence"],
            )
            if u["current_level"] != prior_level:
                n_changed += 1
        else:
            graph.update_non_level_attribute(
                problem_name=problem_name, attr_name=attr,
                summary_text=u["summary_text"],
            )
        if int(u.get("new_info_useful", 0)) == 1:
            n_useful += 1
    return n_changed, n_useful


def _build_agent3b_inputs(
    graph: ProblemGraphV7,
    *,
    problem_name: str,
    changed_attrs: set[str],
) -> Agent3bInputs:
    p = graph.problems[problem_name]
    changed: list[dict] = []
    other: list[dict] = []
    for attr, s in p.level_attributes.items():
        rec = {
            "attribute_name": attr,
            "current_level": s.current_level,
            "level_reasoning": s.level_reasoning,
        }
        if attr in changed_attrs:
            changed.append(rec)
        else:
            other.append(rec)
    return Agent3bInputs(
        problem_name=problem_name,
        previous_ttm_stage=p.current_ttm_stage,
        previous_ttm_reasoning=p.ttm_reasoning,
        changed_attributes=changed,
        other_attributes=other,
    )


def _changed_attrs_in_3a(
    graph_before_levels: dict[str, str], agent3a_out: dict,
) -> set[str]:
    """Return the set of attribute names whose level changed.

    `graph_before_levels` is a snapshot {attr_name: prior_level} captured
    BEFORE Agent 3a's updates were applied to the graph.
    """
    changed: set[str] = set()
    for u in agent3a_out.get("attribute_updates") or []:
        attr = u["attribute_name"]
        if attr not in _LEVEL_SET:
            continue
        prior = graph_before_levels.get(attr, "unknown")
        if u["current_level"] != prior:
            changed.add(attr)
    return changed


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def v7_turn_fn(
    *,
    client: LLMClient,
    profile_id: str,
    system: str = "v7",
    session_id: int,
    turn_id: int,
    user_message: str,
    recent_turns: list[dict],
    last_system_message: Optional[str] = None,
    prior_session_summary: Optional[str] = None,  # accepted but ignored
    graph: ProblemGraphV7,
    last_n_turns: int = 5,
    previous_turn_traces: Optional[list[dict]] = None,
) -> dict:
    """Execute one v7 turn against `graph` (which must be a
    ProblemGraphV7). Returns a structured trace.
    """
    t0 = time.monotonic()
    timings: dict[str, float] = {}

    # Snapshot the previous main_problem (the one carried into this turn).
    previous_main: Optional[str] = next(
        (n for n, p in graph.problems.items()
         if p.previous_main_for_session),
        None,
    )
    previous_active_problems: list[str] = list(graph.problems.keys())

    # ------------------------------------------------------------------ #
    # Phase 1 — Agents 1 + 2 in parallel                                 #
    # ------------------------------------------------------------------ #
    a1_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="agent1_user_intent",
    )
    a2_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="agent2_inference_v7",
    )

    def _run_a1():
        s = time.monotonic()
        out = run_agent1(
            client=client, ctx=a1_ctx,
            inputs=Agent1Inputs(
                rolling_summary_5turns=graph.rolling_summary_5turns,
                current_message=user_message,
                last_assistant_message=last_system_message or "",
            ),
        )
        timings["agent_1"] = time.monotonic() - s
        return out

    def _run_a2():
        s = time.monotonic()
        a2_recent_turns = recent_turns[-(AGENT2_RECENT_TURNS_N * 2):]
        out = run_agent2(
            client=client, ctx=a2_ctx,
            inputs=Agent2Inputs(
                current_message=user_message,
                recent_turns=a2_recent_turns,
                previous_active_problems=previous_active_problems,
                previous_main_problem=previous_main,
            ),
        )
        timings["agent_2"] = time.monotonic() - s
        return out

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="v7_p1") as ex:
        f_a1 = ex.submit(_run_a1)
        f_a2 = ex.submit(_run_a2)
        agent1_out = f_a1.result()
        try:
            agent2_out = f_a2.result()
        except LLMStructuredError as exc:
            log.warning(
                "agent2_inference_v7 exhausted; using carry_forward: %s", exc,
            )
            agent2_out = build_agent2_carry_forward_output(
                graph=graph,
                previous_main_problem=previous_main,
                previous_turn_traces=previous_turn_traces or [],
                error_summary=str(exc),
            )

    # ------------------------------------------------------------------ #
    # Phase 2 — Apply inference to the graph                             #
    # ------------------------------------------------------------------ #
    (
        new_main,
        n_conn_added,
        n_conn_useful,
        new_entries_per_edge,
    ) = _apply_inference_to_graph_v7(
        graph, agent2_out, session_id=session_id, turn_id=turn_id,
    )
    current_problem_names = [
        cp["problem_name"] for cp in agent2_out.get("current_problems") or []
    ]

    # ------------------------------------------------------------------ #
    # Phase 3 — Agent 3a per problem (parallel)                          #
    # ------------------------------------------------------------------ #
    turn_attribute_entries = agent2_out.get("problem_attribute_entries") or []
    turn_connections = agent2_out.get("problem_attribute_connections") or []

    # A problem qualifies for 3a if Agent 2 emitted attribute info OR
    # connections touching it this turn.
    problems_with_new_info: list[str] = []
    for name in current_problem_names:
        has_attr = any(e["problem_name"] == name for e in turn_attribute_entries)
        has_conn = any(
            (c["problem_1"] == name or c["problem_2"] == name)
            for c in turn_connections
        )
        if has_attr or has_conn:
            problems_with_new_info.append(name)

    # Snapshot prior levels per (problem, attr) so we can detect changes.
    prior_levels: dict[str, dict[str, str]] = {}
    for name in problems_with_new_info:
        p = graph.problems[name]
        prior_levels[name] = {
            attr: s.current_level for attr, s in p.level_attributes.items()
        }

    agent3a_outs: dict[str, dict] = {}
    if problems_with_new_info:
        s_phase = time.monotonic()
        with ThreadPoolExecutor(
            max_workers=min(4, len(problems_with_new_info)),
            thread_name_prefix="v7_3a",
        ) as ex:
            futures: dict[str, Any] = {}
            for name in problems_with_new_info:
                a3a_ctx = CallContext(
                    profile_id=profile_id, session_id=session_id, system=system,
                    turn_id=turn_id, call_role="agent3a_attr_update",
                )
                inputs = _build_agent3a_inputs(
                    graph, problem_name=name,
                    turn_attribute_entries=turn_attribute_entries,
                    turn_connections=turn_connections,
                    session_id=session_id, turn_id=turn_id,
                )
                futures[name] = ex.submit(
                    run_agent3a, client=client, ctx=a3a_ctx, inputs=inputs,
                )
            for name, fut in futures.items():
                agent3a_outs[name] = fut.result()
        timings["agent_3a_max"] = time.monotonic() - s_phase

    # Apply each 3a output to the graph and figure out which problems
    # had a level change.
    n_levels_changed_total = 0
    n_useful_summary_updates_total = 0
    problems_needing_3b: dict[str, set[str]] = {}
    n_attrs_with_new_info = 0
    for name, out in agent3a_outs.items():
        n_attrs_with_new_info += len(out.get("attribute_updates") or [])
        # Identify changed attrs BEFORE we apply (use the snapshot).
        snapshot = prior_levels.get(name, {})
        changed = _changed_attrs_in_3a(snapshot, out)
        n_changed_here, n_useful_here = _apply_agent3a_to_graph(
            graph, name, out,
        )
        n_levels_changed_total += n_changed_here
        n_useful_summary_updates_total += n_useful_here
        if changed:
            problems_needing_3b[name] = changed

    # ------------------------------------------------------------------ #
    # Phase 3c — Agent 3c per edge (parallel, conditional)               #
    # One call per edge that received NEW connection entries this turn. #
    # Maintains edge.summary_text so older entries are preserved as a   #
    # running NL summary (no [-N:] truncation in the evidence pack).    #
    # ------------------------------------------------------------------ #
    agent3c_outs: dict[tuple[str, str], dict] = {}
    n_edge_summaries_updated = 0
    n_edge_summaries_useful = 0
    if new_entries_per_edge:
        s_phase = time.monotonic()
        with ThreadPoolExecutor(
            max_workers=min(4, len(new_entries_per_edge)),
            thread_name_prefix="v7_3c",
        ) as ex:
            futures: dict[tuple[str, str], Any] = {}
            for edge_key, entries in new_entries_per_edge.items():
                p_a, p_b = edge_key  # canonical (alphabetical) order
                a3c_ctx = CallContext(
                    profile_id=profile_id, session_id=session_id, system=system,
                    turn_id=turn_id, call_role="agent3c_edge_summary",
                )
                edge = graph.edges.get(edge_key)
                existing_summary = edge.summary_text if edge is not None else ""
                inputs = Agent3cInputs(
                    problem_1=p_a, problem_2=p_b,
                    current_session=session_id, current_turn=turn_id,
                    existing_summary_text=existing_summary,
                    new_entries=entries,
                )
                futures[edge_key] = ex.submit(
                    run_agent3c, client=client, ctx=a3c_ctx, inputs=inputs,
                )
            for edge_key, fut in futures.items():
                agent3c_outs[edge_key] = fut.result()
        timings["agent_3c_max"] = time.monotonic() - s_phase

        # Apply each 3c output to the graph.
        for edge_key, out in agent3c_outs.items():
            p_a, p_b = edge_key
            graph.update_edge_summary(
                problem_a=p_a, problem_b=p_b,
                summary_text=out["summary_text"],
            )
            n_edge_summaries_updated += 1
            if int(out.get("useful", 0)) == 1:
                n_edge_summaries_useful += 1

    # ------------------------------------------------------------------ #
    # Phase 4 — Agent 3b per problem (parallel, conditional)             #
    # ------------------------------------------------------------------ #
    agent3b_outs: dict[str, dict] = {}
    n_ttm_calls_made = 0
    n_ttm_stages_changed = 0
    if problems_needing_3b:
        s_phase = time.monotonic()
        with ThreadPoolExecutor(
            max_workers=min(4, len(problems_needing_3b)),
            thread_name_prefix="v7_3b",
        ) as ex:
            futures: dict[str, Any] = {}
            for name, changed_attrs in problems_needing_3b.items():
                a3b_ctx = CallContext(
                    profile_id=profile_id, session_id=session_id, system=system,
                    turn_id=turn_id, call_role="agent3b_ttm_intent",
                )
                inputs = _build_agent3b_inputs(
                    graph, problem_name=name, changed_attrs=changed_attrs,
                )
                futures[name] = ex.submit(
                    run_agent3b, client=client, ctx=a3b_ctx, inputs=inputs,
                )
            for name, fut in futures.items():
                agent3b_outs[name] = fut.result()
        timings["agent_3b_max"] = time.monotonic() - s_phase

        n_ttm_calls_made = len(agent3b_outs)
        for name, out in agent3b_outs.items():
            prior_stage = graph.problems[name].current_ttm_stage
            graph.set_ttm(
                problem_name=name,
                new_stage=out["new_ttm_stage"],
                ttm_reasoning=out["ttm_reasoning"],
                system_intent=out["system_intent"],
                mi_for_system_intent=out["mi_for_system_intent"],
            )
            if out["new_ttm_stage"] != prior_stage:
                n_ttm_stages_changed += 1

    # ------------------------------------------------------------------ #
    # Phase 5 — recompute edge weights + assemble evidence pack          #
    # ------------------------------------------------------------------ #
    s_phase = time.monotonic()
    graph.recompute_all_edge_weights(global_turn_idx(session_id, turn_id))
    evidence_pack = graph.assemble_evidence_pack(
        main_problem=new_main,
        current_problems=current_problem_names,
        tau=EDGE_THRESHOLD_TAU_V7,
    )
    timings["agent_4_python"] = time.monotonic() - s_phase

    # Count edges involving the main problem (pack no longer carries raw
    # entries; their full history lives in each edge's running summary).
    n_connections_touching_main = 0
    if new_main:
        for c in evidence_pack.get("problem_problem_connections") or []:
            if new_main in (c["a"], c["b"]):
                n_connections_touching_main += 1

    # ------------------------------------------------------------------ #
    # Phase 6 — Agent 5 response (BIG)                                   #
    # ------------------------------------------------------------------ #
    past_two_turns = _collect_past_two_turns_v7(previous_turn_traces or [])
    a5_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="agent5_response_v7",
    )
    s_phase = time.monotonic()
    response_out = run_response_v7(
        client=client, ctx=a5_ctx,
        inputs=ResponseV7Inputs(
            user_intent=agent1_out.get("user_intent", "small_talk"),
            user_intent_phrase=agent1_out.get("user_intent_phrase", ""),
            mi_for_user_intent=agent1_out.get("mi_for_user_intent", "support"),
            evidence_pack=evidence_pack,
            past_two_turns=past_two_turns,
            current_user_message=user_message,
        ),
    )
    timings["agent_5"] = time.monotonic() - s_phase

    # Tally evidence_used by type so we can trace how many audit-style
    # references the response actually cited. Does not interpret
    # references; just counts.
    ev_used = response_out.get("evidence_used") or []
    n_ev_by_type: dict[str, int] = {
        "attribute": 0,
        "attribute_connection": 0,
        "problem_problem_connection": 0,
        "persona": 0,
        "recent_turn": 0,
    }
    for e in ev_used:
        t = (e or {}).get("type")
        if t in n_ev_by_type:
            n_ev_by_type[t] += 1

    # ------------------------------------------------------------------ #
    # Phase 7 — Agent X end-of-turn rolling summary refresh              #
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
    # Diagnostics + trace                                                #
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
        "agent2_carry_forward": bool(agent2_out.get("_agent2_carry_forward")),
        "agent2_carry_forward_reason": agent2_out.get("_agent2_carry_forward_reason"),
        "agent3a_outputs_per_problem": agent3a_outs,
        "agent3b_outputs_per_problem": agent3b_outs,
        "agent3c_outputs_per_edge": {
            f"{a}|{b}": out for (a, b), out in agent3c_outs.items()
        },
        "n_attributes_with_new_info": n_attrs_with_new_info,
        "n_attribute_summaries_with_useful_new_info":
            n_useful_summary_updates_total,
        "n_levels_changed": n_levels_changed_total,
        "n_ttm_calls_made": n_ttm_calls_made,
        "n_ttm_stages_changed": n_ttm_stages_changed,
        "n_connection_entries_added": n_conn_added,
        "n_useful_connection_entries_added": n_conn_useful,
        "n_edge_summaries_updated": n_edge_summaries_updated,
        "n_edge_summaries_with_useful_new_info": n_edge_summaries_useful,
        "n_connections_touching_main": n_connections_touching_main,
        "used_evidence": bool(response_out.get("used_evidence", False)),
        "n_evidence_used_total": len(ev_used),
        "n_evidence_used_by_type": n_ev_by_type,
        "n_audits_used_in_response": (
            n_ev_by_type["attribute"] + n_ev_by_type["attribute_connection"]
        ),
    }

    diagnostics = {
        "n_llm_calls_total": (
            2  # Agent 1 + Agent 2
            + len(agent3a_outs) + len(agent3b_outs) + len(agent3c_outs)
            + 1  # Agent 5
            + 1  # Agent X (end-of-turn)
        ),
        "n_llm_calls_big": 2,  # Agent 2 + Agent 5
        "n_llm_calls_small": (
            1  # Agent 1
            + len(agent3a_outs) + len(agent3b_outs) + len(agent3c_outs)
            + 1  # Agent X
        ),
        "agent_wall_times": {k: round(v, 3) for k, v in timings.items()},
        "turn_total_wall_time_s": round(time.monotonic() - t0, 3),
    }

    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "user_message": user_message,
        "agent1": agent1_out,
        "inference": agent2_out,  # alias 'inference' so v6-style scripts work
        "agent2": agent2_out,
        "evidence_pack": evidence_pack,
        "past_two_turns": past_two_turns,
        "response": response_out,
        "trace": trace,
        "diagnostics": diagnostics,
    }


__all__ = ["v7_turn_fn"]
