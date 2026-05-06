"""V8 multi-agent turn function — V7 with dense RAG instead of
structural edge-walk retrieval, EdgeSummaryAgent dropped, and a new
QueryAgent that produces the retrieval query.

Agent roster (meaningful names; legacy "Agent N" labels in parens):

  - IntentAgent          (Agent 1)  — user_intent + MI for user
  - InferenceAgent       (Agent 2)  — graph mutation extraction
  - QueryAgent           (Agent Q)  — retrieval query (V8 ONLY)
  - AttributeAgent       (Agent 3a) — per-attribute summary + level
  - StageAgent           (Agent 3b) — TTM + system_intent + MI for system
  - ResponseAgent        (Agent 5)  — R1→R4 final response
  - RollingSummaryAgent  (Agent X)  — end-of-turn 5-turn summary
  - (EdgeSummaryAgent / Agent 3c is NOT used in V8)

What's the same as V7
---------------------
  - IntentAgent, InferenceAgent, AttributeAgent, StageAgent,
    RollingSummaryAgent — identical roles, identical prompts. The
    level + TTM compute pipeline is unchanged.
  - The graph data structure (`ProblemGraphV7`). Audit stacks per
    attribute and connection_entries per edge are stored exactly the
    same way and are still chronological + lossless.
  - ResponseAgent's SYSTEM prompt and response schema (reused via
    `instruction_response_v8`).

What's different in V8
----------------------
  - **No EdgeSummaryAgent.** Per-edge running summaries are not
    maintained. Connection history reaches the response via RAG over
    the raw entries.
  - **QueryAgent (new).** Reads structured signals from IntentAgent +
    StageAgent (per-problem) and emits a retrieval query string used
    by the dense retriever in Phase 5.
  - **Phase 5 changes.** Instead of the τ-weighted-degree edge walk
    (`graph.assemble_evidence_pack`), V8 builds an evidence_pack with:
      * graph state for the main problem (TTM + system_intent +
        current_levels — NO attribute summary_text)
      * 1-line "other current problems" block
      * `rag_retrieved_chunks`: top-K MMR-diversified MiniLM cosine
        hits over the union of every attribute audit entry and every
        connection entry, ranked against QueryAgent's output.
  - Edge weights are still recomputed (cheap; useful for diagnostics)
    but they no longer gate retrieval.
  - Attribute `summary_text` is still maintained by AttributeAgent
    (used by StageAgent to compute TTM) but is NOT surfaced in
    ResponseAgent's prompt.

Per-turn LLM calls
------------------
    Phase 1 (parallel):    Agent 1 (small) + Agent 2 (BIG)
    Phase 2 (Python):      Apply inference to graph
    Phase 3+Q (parallel):  Agent 3a × N (small) + Agent Q (small)
    Phase 4 (parallel):    Agent 3b × M ≤ N (small, conditional)
    Phase 5 (Python):      Recompute weights + assemble V8 evidence pack
                           (RAG retrieval using Agent Q's query)
    Phase 6 (sync, BIG):   Agent 5 (response)
    Phase 7 (sync):        Agent X (rolling summary, end-of-turn)

  Total: Agent 1 + Agent 2 + Agent Q + Agent 3a×N + Agent 3b×M
         + Agent 5 + Agent X.
  Same call count as V7 (V7's Agent 3c is replaced by Agent Q).

  Agent Q runs in parallel with Agent 3a and reads system_intent +
  mi_for_system_intent from the graph at Phase 2 end — pre-Phase-4
  values. When Agent 3b fires this turn (only when a level changed),
  the system_intent Q saw is one turn stale; otherwise it's correct.
  Trade-off accepted in exchange for hiding Q's wallclock behind 3a.
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
    LEVEL_ATTR_TYPES,
    MISC_CODES,
    MISC_INCONSISTENT_CODES,
)
from ..graph_v6 import AttributeEvidenceEntry, global_turn_idx
from ..graph_v7 import ConnectionEntryV7, ProblemEdgeV7, ProblemGraphV7
from ..instruction_response_v8 import (
    ResponseV8Inputs,
    run_response_v8,
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
from ..prompts.agentq_retrieval_query import AgentQInputs, run_agentq
from ..prompts.agentX_rolling_summary import AgentXInputs, run_agentx
from ..config import V8_MMR_LAMBDA
from ..rag_v8 import (
    V8_RAG_TOP_K_DEFAULT,
    build_query,
    extract_corpus,
    retrieve,
)


log = logging.getLogger(__name__)

_LEVEL_SET = frozenset(LEVEL_ATTR_TYPES)


# ---------------------------------------------------------------------------
# past_two_turns hint for Agent 5 (identical to v7)
# ---------------------------------------------------------------------------


def _collect_past_two_turns_v8(prev_traces: list[dict]) -> list[dict]:
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
# Graph-mutation helpers (Python, no LLM) — identical to v7 except no
# per-edge bucketing for Agent 3c.
# ---------------------------------------------------------------------------


def _apply_inference_to_graph_v8(
    graph: ProblemGraphV7,
    inference_out: dict,
    *,
    session_id: int,
    turn_id: int,
) -> tuple[Optional[str], int, int]:
    """Apply Agent 2's output to the v7 graph. Returns
    (new_main_name, n_connection_entries_added,
     n_useful_connection_entries_added).
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

    n_added = 0
    n_useful = 0
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

    return new_main, n_added, n_useful


def _build_agent3a_inputs(
    graph: ProblemGraphV7,
    *,
    problem_name: str,
    turn_attribute_entries: list[dict],
    turn_connections: list[dict],
    session_id: int,
    turn_id: int,
) -> Agent3aInputs:
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


def _build_agentq_inputs(
    *,
    graph: ProblemGraphV7,
    agent1_out: dict,
    main_problem_name: Optional[str],
    current_problem_names: list[str],
) -> AgentQInputs:
    """Project Agent 1's output + graph state into Agent Q's input
    dataclass. Reads system_intent / mi_for_system_intent from the
    graph at the time of the call — this is pre-Phase-4 state, so
    Agent 3b's updates this turn are NOT visible to Q (the staleness
    trade-off for running Q in parallel with Phase 3).
    """
    main_si: Optional[str] = None
    main_mi: Optional[str] = None
    if main_problem_name and main_problem_name in graph.problems:
        mp = graph.problems[main_problem_name]
        main_si = mp.system_intent or None
        main_mi = mp.mi_for_system_intent or None

    others: list[dict] = []
    for name in current_problem_names:
        if name == main_problem_name or name not in graph.problems:
            continue
        p = graph.problems[name]
        others.append({
            "name": name,
            "system_intent_1line": p.system_intent or "",
            "mi_for_system_intent": p.mi_for_system_intent or None,
        })

    return AgentQInputs(
        user_intent=agent1_out.get("user_intent", "small_talk"),
        user_intent_phrase=agent1_out.get("user_intent_phrase", "") or "",
        mi_for_user_intent=agent1_out.get("mi_for_user_intent", "support"),
        main_problem=main_problem_name,
        main_system_intent=main_si,
        main_mi_for_system_intent=main_mi,
        other_current_problems=others,
    )


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
# V8 evidence pack assembly (replaces V7's graph.assemble_evidence_pack)
# ---------------------------------------------------------------------------


def _build_main_block_v8(
    graph: ProblemGraphV7, main_problem_name: Optional[str],
) -> Optional[dict]:
    """Graph state for the main problem — NO chronological summary_text.

    Carries TTM, system_intent, mi_for_system_intent, a ``current_levels``
    dict (each level paired with its short level_reasoning so Agent 5 can
    interpret the label), and de-duplicated ``audit_anchors`` per
    attribute (``["sN.tM", ...]``) so Agent 5 sees *which moments*
    shaped each level even before the RAG-retrieved chunks land.
    Non-level attributes also surface anchors.
    """
    if not main_problem_name or main_problem_name not in graph.problems:
        return None
    p = graph.problems[main_problem_name]

    levels: dict[str, dict] = {}
    for attr, state in p.level_attributes.items():
        if state.current_level == "unknown" and not state.level_reasoning:
            continue
        levels[attr] = {
            "level": state.current_level,
            "level_reasoning": state.level_reasoning,
            "audit_anchors": ProblemGraphV7._dedup_audit_anchors(state.audit_stack),
        }

    non_levels: dict[str, dict] = {}
    for attr, state_nl in p.non_level_attributes.items():
        if not state_nl.audit_stack:
            continue
        non_levels[attr] = {
            "audit_anchors": ProblemGraphV7._dedup_audit_anchors(state_nl.audit_stack),
        }

    return {
        "name": p.problem_name,
        "ttm_stage": p.current_ttm_stage,
        "ttm_reasoning": p.ttm_reasoning,
        "system_intent": p.system_intent,
        "mi_for_system_intent": p.mi_for_system_intent,
        "current_levels": levels,
        "non_level_attribute_anchors": non_levels,
    }


def _build_others_block_v8(
    graph: ProblemGraphV7,
    current_problem_names: list[str],
    main_problem_name: Optional[str],
) -> list[dict]:
    out: list[dict] = []
    for name in current_problem_names:
        if name == main_problem_name:
            continue
        if name not in graph.problems:
            continue
        p = graph.problems[name]
        out.append({
            "name": name,
            "ttm_stage": p.current_ttm_stage,
            "system_intent_1line": p.system_intent,
        })
    return out


def _assemble_evidence_pack_v8(
    graph: ProblemGraphV7,
    *,
    main_problem_name: Optional[str],
    current_problem_names: list[str],
    user_intent_phrase: str,
    current_user_message: str,
    agentq_query: Optional[str] = None,
    top_k: int = V8_RAG_TOP_K_DEFAULT,
) -> tuple[dict, list[dict]]:
    """Build the V8 evidence_pack and return (pack, retrieved_chunks).

    Retrieval query: ``agentq_query`` if non-empty, otherwise the
    deterministic ``build_query`` concatenation as a safety net. Corpus
    is every attribute audit entry + every connection entry across the
    graph. Retrieval is dense MMR over MiniLM embeddings.
    """
    main_block = _build_main_block_v8(graph, main_problem_name)
    others_block = _build_others_block_v8(
        graph, current_problem_names, main_problem_name,
    )
    main_system_intent = (
        main_block.get("system_intent") if main_block else None
    )

    corpus = extract_corpus(graph)

    aq_query = (agentq_query or "").strip()
    if aq_query:
        query = aq_query
        query_source = "agent_q"
    else:
        query = build_query(
            user_intent_phrase=user_intent_phrase,
            main_system_intent=main_system_intent,
            current_user_message=current_user_message,
        )
        query_source = "fallback_concat"
    retrieved = retrieve(query, corpus, top_k=top_k)

    pack = {
        "main_problem": main_block,
        "other_current_problems": others_block,
        "rag_retrieved_chunks": retrieved,
        "rag_query": query,
        "rag_query_source": query_source,
        "rag_corpus_size": len(corpus),
        "persona": asdict(graph.persona),
        "rolling_summary_5turns": graph.rolling_summary_5turns,
    }
    return pack, retrieved


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def v8_turn_fn(
    *,
    client: LLMClient,
    profile_id: str,
    system: str = "v8",
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
    """Execute one V8 turn against `graph` (a ProblemGraphV7). Returns a
    structured trace.
    """
    t0 = time.monotonic()
    timings: dict[str, float] = {}

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

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="v8_p1") as ex:
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
    new_main, n_conn_added, n_conn_useful = _apply_inference_to_graph_v8(
        graph, agent2_out, session_id=session_id, turn_id=turn_id,
    )
    current_problem_names = [
        cp["problem_name"] for cp in agent2_out.get("current_problems") or []
    ]

    # ------------------------------------------------------------------ #
    # Phase 3 + Q — AttributeAgent per problem + QueryAgent, in parallel #
    # in the same thread pool. AttributeAgent maintains attribute        #
    # summaries (consumed by StageAgent in Phase 4 to compute TTM —      #
    # NOT surfaced to ResponseAgent in V8). QueryAgent reads structured  #
    # signals from IntentAgent + the graph state at this point: the      #
    # system_intent values it sees are the *previous turn's* values     #
    # (StageAgent in Phase 4 may update them later this turn).           #
    # Acceptable trade-off for parallelism — most turns don't fire       #
    # StageAgent, and even when it does fire the previous-turn           #
    # system_intent is usually a close enough signal for query           #
    # construction.                                                       #
    # ------------------------------------------------------------------ #
    turn_attribute_entries = agent2_out.get("problem_attribute_entries") or []
    turn_connections = agent2_out.get("problem_attribute_connections") or []

    problems_with_new_info: list[str] = []
    for name in current_problem_names:
        has_attr = any(e["problem_name"] == name for e in turn_attribute_entries)
        has_conn = any(
            (c["problem_1"] == name or c["problem_2"] == name)
            for c in turn_connections
        )
        if has_attr or has_conn:
            problems_with_new_info.append(name)

    prior_levels: dict[str, dict[str, str]] = {}
    for name in problems_with_new_info:
        p = graph.problems[name]
        prior_levels[name] = {
            attr: s.current_level for attr, s in p.level_attributes.items()
        }

    aq_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="agentq_retrieval_query",
    )
    aq_inputs = _build_agentq_inputs(
        graph=graph, agent1_out=agent1_out,
        main_problem_name=new_main,
        current_problem_names=current_problem_names,
    )

    agent3a_outs: dict[str, dict] = {}
    agentq_out: dict = {}
    s_phase = time.monotonic()
    n_workers = min(5, 1 + max(1, len(problems_with_new_info)))
    with ThreadPoolExecutor(
        max_workers=n_workers, thread_name_prefix="v8_p3q",
    ) as ex:
        # Agent Q always runs (one call per turn).
        f_aq = ex.submit(
            run_agentq, client=client, ctx=aq_ctx, inputs=aq_inputs,
        )

        # Agent 3a runs once per problem with new info this turn.
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

        # Collect results.
        agentq_out = f_aq.result()
        for name, fut in futures.items():
            agent3a_outs[name] = fut.result()
    timings["agent_3a_q_max"] = time.monotonic() - s_phase

    n_levels_changed_total = 0
    n_useful_summary_updates_total = 0
    problems_needing_3b: dict[str, set[str]] = {}
    n_attrs_with_new_info = 0
    for name, out in agent3a_outs.items():
        n_attrs_with_new_info += len(out.get("attribute_updates") or [])
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
    # Phase 4 — Agent 3b per problem (parallel, conditional)             #
    # Identical to V7.                                                    #
    # ------------------------------------------------------------------ #
    agent3b_outs: dict[str, dict] = {}
    n_ttm_calls_made = 0
    n_ttm_stages_changed = 0
    if problems_needing_3b:
        s_phase = time.monotonic()
        with ThreadPoolExecutor(
            max_workers=min(4, len(problems_needing_3b)),
            thread_name_prefix="v8_3b",
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
    # Phase 5 — recompute edge weights + assemble V8 evidence pack       #
    # Edge weights are still computed (cheap, useful diagnostics) but    #
    # do NOT gate retrieval — RAG does.                                  #
    # ------------------------------------------------------------------ #
    s_phase = time.monotonic()
    graph.recompute_all_edge_weights(global_turn_idx(session_id, turn_id))
    evidence_pack, rag_retrieved = _assemble_evidence_pack_v8(
        graph,
        main_problem_name=new_main,
        current_problem_names=current_problem_names,
        user_intent_phrase=agent1_out.get("user_intent_phrase", "") or "",
        current_user_message=user_message,
        agentq_query=(agentq_out or {}).get("retrieval_query"),
    )
    timings["agent_4_python"] = time.monotonic() - s_phase

    # Diagnostics: how many retrieved chunks actually involve the main
    # problem (useful as a quick "is the retriever on-target" signal)?
    n_retrieved_touching_main = 0
    if new_main:
        for c in rag_retrieved:
            if c["type"] == "attribute_entry" and c.get("problem") == new_main:
                n_retrieved_touching_main += 1
            elif c["type"] == "connection_entry" and new_main in (
                c.get("problem_a"), c.get("problem_b")
            ):
                n_retrieved_touching_main += 1

    # ------------------------------------------------------------------ #
    # Phase 6 — Agent 5 response (BIG)                                   #
    # ------------------------------------------------------------------ #
    past_two_turns = _collect_past_two_turns_v8(previous_turn_traces or [])
    a5_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="agent5_response_v8",
    )
    s_phase = time.monotonic()
    response_out = run_response_v8(
        client=client, ctx=a5_ctx,
        inputs=ResponseV8Inputs(
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
    # references the response actually cited.
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
        "n_attributes_with_new_info": n_attrs_with_new_info,
        "n_attribute_summaries_with_useful_new_info":
            n_useful_summary_updates_total,
        "n_levels_changed": n_levels_changed_total,
        "n_ttm_calls_made": n_ttm_calls_made,
        "n_ttm_stages_changed": n_ttm_stages_changed,
        "n_connection_entries_added": n_conn_added,
        "n_useful_connection_entries_added": n_conn_useful,
        "rag_corpus_size": evidence_pack.get("rag_corpus_size", 0),
        "rag_retrieved_count": len(rag_retrieved),
        "n_retrieved_chunks_touching_main": n_retrieved_touching_main,
        "rag_retrieval_mode": "mmr",
        "rag_mmr_lambda": V8_MMR_LAMBDA,
        "rag_query_source": evidence_pack.get("rag_query_source"),
        "agentq_output": agentq_out,
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
            + 1  # Agent Q (retrieval query)
            + len(agent3a_outs) + len(agent3b_outs)
            + 1  # Agent 5
            + 1  # Agent X (end-of-turn)
        ),
        "n_llm_calls_big": 2,  # Agent 2 + Agent 5
        "n_llm_calls_small": (
            1  # Agent 1
            + 1  # Agent Q
            + len(agent3a_outs) + len(agent3b_outs)
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
        "agentq": agentq_out,
        "inference": agent2_out,
        "agent2": agent2_out,
        "evidence_pack": evidence_pack,
        "past_two_turns": past_two_turns,
        "response": response_out,
        "trace": trace,
        "diagnostics": diagnostics,
    }


__all__ = ["v8_turn_fn"]
