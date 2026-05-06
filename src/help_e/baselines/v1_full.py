"""V1 multi-agent turn function — V7's structure stripped to the trunk.

Agent roster (meaningful names; legacy "Agent N" labels in parens):

  - IntentAgent          (Agent 1)  — user_intent + MI for user
                                      (small model; same as V7)
  - ResponseAgent        (Agent 5)  — R1 → R4 (no R2/R3); BIG model
  - RollingSummaryAgent  (Agent X)  — end-of-turn 5-turn summary
                                      (small model; same as V7)

V1 is a deliberately bare baseline: no problems detected, no graph
walked, no evidence concept, no system_intent, no TTM stage. The only
memory the response prompt sees is the rolling 5-turn summary plus a
diversity hint about MISC choices in the past two turns. Agent P
(persona update) still runs at session end for pipeline symmetry with
V3/V7/V8, but the persona is not surfaced to ResponseAgent.

Per-turn pipeline:

  Phase 1 (parallel, 1 worker):  IntentAgent (small)
  Phase 6 (sync, BIG):            ResponseAgent (R1 → R4)
  Phase 7 (sync, small):          RollingSummaryAgent

Total LLM calls per turn: 3. One BIG (Response). Two small (Intent,
RollingSummary).

Graph: V1 reuses ``ProblemGraphV3`` for plumbing compatibility — the
``problems`` and ``edges`` dicts stay empty; only ``persona`` (from
Agent P) and ``rolling_summary_5turns`` (from RollingSummaryAgent)
are populated.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from ..config import AGENT5_PAST_TURNS_HINT_N
from ..graph_v3 import ProblemGraphV3
from ..instruction_response_v1 import (
    ResponseV1Inputs,
    run_response_v1,
)
from ..llm_client import CallContext, LLMClient
from ..prompts.agent1_user_intent import Agent1Inputs, run_agent1
from ..prompts.agentX_rolling_summary import AgentXInputs, run_agentx


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# past_two_turns hint (same shape as V7/V8/V3)
# ---------------------------------------------------------------------------


def _collect_past_two_turns_v1(prev_traces: list[dict]) -> list[dict]:
    if not prev_traces:
        return []
    last = prev_traces[-AGENT5_PAST_TURNS_HINT_N:]
    out: list[dict] = []
    for offset, tr in enumerate(reversed(last), start=1):
        resp = tr.get("response") or {}
        strategies = [
            c for c in (resp.get("mi_for_user_intent_used"),)
            if c
        ]
        out.append({
            "turn_offset": -offset,
            "strategies": strategies,
        })
    out.reverse()
    return out


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


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
    prior_session_summary: Optional[str] = None,
    graph: ProblemGraphV3,
    last_n_turns: int = 5,
    previous_turn_traces: Optional[list[dict]] = None,
) -> dict:
    """Execute one V1 turn against `graph` (a ProblemGraphV3 with empty
    problems/edges; only persona + rolling_summary_5turns are used).
    """
    t0 = time.monotonic()
    timings: dict[str, float] = {}

    # ------------------------------------------------------------------ #
    # Phase 1 — IntentAgent (small)                                      #
    # ------------------------------------------------------------------ #
    a1_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="agent1_user_intent",
    )
    s_phase = time.monotonic()
    agent1_out = run_agent1(
        client=client, ctx=a1_ctx,
        inputs=Agent1Inputs(
            rolling_summary_5turns=graph.rolling_summary_5turns,
            current_message=user_message,
        ),
    )
    timings["agent_intent"] = time.monotonic() - s_phase

    # Phase 2-5 don't exist in V1 (no graph mutation, no per-problem
    # updates, no edge summaries, no evidence assembly).

    # ------------------------------------------------------------------ #
    # Phase 6 — ResponseAgent (BIG)                                      #
    # ------------------------------------------------------------------ #
    past_two_turns = _collect_past_two_turns_v1(previous_turn_traces or [])
    a5_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="agent5_response_v1",
    )
    s_phase = time.monotonic()
    response_out = run_response_v1(
        client=client, ctx=a5_ctx,
        inputs=ResponseV1Inputs(
            user_intent=agent1_out.get("user_intent", "small_talk"),
            user_intent_phrase=agent1_out.get("user_intent_phrase", ""),
            mi_for_user_intent=agent1_out.get("mi_for_user_intent", "support"),
            rolling_summary_5turns=graph.rolling_summary_5turns,
            past_two_turns=past_two_turns,
            current_user_message=user_message,
        ),
    )
    timings["agent_response"] = time.monotonic() - s_phase

    # ------------------------------------------------------------------ #
    # Phase 7 — RollingSummaryAgent (small, end-of-turn)                 #
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
    # Trace + diagnostics — V1-shape, V7-aligned where possible          #
    # ------------------------------------------------------------------ #
    trace = {
        # V1 has no problems → main_problem and current_problems are
        # always None / empty. Kept as keys for downstream tools that
        # iterate trace fields.
        "main_problem": None,
        "current_problems": [],
        "user_intent": agent1_out.get("user_intent"),
        "user_intent_phrase": agent1_out.get("user_intent_phrase"),
        "mi_for_user_intent": agent1_out.get("mi_for_user_intent"),
        "ttm_stage": None,
        "system_intent": None,
        "mi_for_system_intent": None,
        # Used-evidence tally — V1 has no evidence concept, so always 0.
        "used_evidence": False,
        "n_evidence_used_total": 0,
        "n_audits_used_in_response": 0,
    }

    diagnostics = {
        "n_llm_calls_total": 3,        # Intent + Response + RollingSummary
        "n_llm_calls_big": 1,           # Response
        "n_llm_calls_small": 2,         # Intent + RollingSummary
        "agent_wall_times": {k: round(v, 3) for k, v in timings.items()},
        "turn_total_wall_time_s": round(time.monotonic() - t0, 3),
    }

    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "user_message": user_message,
        "agent1": agent1_out,
        # V7-shape fields kept as empty stubs so downstream tools that
        # blindly read ``inference`` don't crash. V1 doesn't run
        # inference.
        "inference": {
            "current_problems": [],
            "main_problem": None,
            "_v1_no_inference": True,
        },
        "evidence_pack": None,
        "past_two_turns": past_two_turns,
        "response": response_out,
        "trace": trace,
        "diagnostics": diagnostics,
    }


__all__ = ["v1_turn_fn"]
