"""FastAPI backend for the interactive demo UI.

Each conversation is a per-profile, per-system run with an in-memory graph
and transcript. A single user-message POST drives one full per-turn
pipeline (extraction → graph update → retrieval → MI candidates → merged
response for v4/v5; the baseline turn_fns for v1/v2/v3). The turn's raw
artifacts (trace, bundle, candidates, merged) are returned to the UI.

Run:
    python -m help_e.ui.server
then open http://localhost:8000.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import config
from ..baselines.v1_history import v1_turn_fn
from ..baselines.v3_ttm_from_summary import v3_turn_fn
from ..baselines.v6_full import v6_turn_fn
from ..graph import ProblemGraph
from ..graph_v6 import ProblemGraphV6
from ..llm_client import CallContext, LLMClient, get_client
from ..session_driver import (
    ProfileSpec,
    _load_or_seed_graph,
    _pick_arc_cue,
    list_profiles,
    load_profile,
)
from ..session_driver_v6 import _to_simulator_profile
from ..simulator.mind1 import run_mind1
from ..simulator.mind1_v6 import run_mind1_v6
from ..simulator.session_context import (
    SimulatorProfile,
    format_session_context,
    run_session_context,
)


log = logging.getLogger(__name__)


SYSTEMS: dict[str, Any] = {
    "v1": {
        "label": "v1 — history-only (baseline)",
        "turn_fn": v1_turn_fn,
        "description": "No graph, no TTM. Simple MI rule on the last user message.",
        "variant": "v5",
    },
    "v3": {
        "label": "v3 — summary + TTM inference",
        "turn_fn": v3_turn_fn,
        "description": "Per-problem running summaries plus per-problem TTM stage inferred from the summary. No attribute graph.",
        "variant": "v5",
    },
    "v6": {
        "label": "v6 — redesigned MI/HBM/TTM with full graph",
        "turn_fn": v6_turn_fn,
        "description": "MISC-aligned MI moves, HBM attributes inside problem nodes, typed problem-problem edges, session-context simulator framing. Cold-start: no pre-seeding.",
        "variant": "v6",
    },
}


# ---------------------------------------------------------------------------
# Conversation state
# ---------------------------------------------------------------------------


@dataclass
class TurnRecord:
    turn_id: int
    user_message: str
    assistant_response: str
    trace: dict
    bundle: Optional[dict]
    candidates: list
    merged: dict
    graph_snapshot: dict
    elapsed_s: float
    variant: str = "v5"
    extras: dict = field(default_factory=dict)


@dataclass
class ConversationState:
    id: str
    system: str
    profile_id: str
    graph: Union[ProblemGraph, ProblemGraphV6]
    profile: ProfileSpec
    mode: str = "human"  # "human": user types; "agent": Mind-1 generates user turns
    transcript: list[dict] = field(default_factory=list)
    traces: list = field(default_factory=list)
    previous_main_problem: Optional[str] = None
    turns: list[TurnRecord] = field(default_factory=list)
    session_id: int = 1
    prior_session_summary: Optional[str] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    created_ts: float = field(default_factory=time.time)
    # v6-only state (None for v1–v5)
    simulator_profile: Optional[SimulatorProfile] = None
    session_context: Optional[dict] = None
    last_system_message: Optional[str] = None


_conversations: dict[str, ConversationState] = {}


# ---------------------------------------------------------------------------
# FastAPI app + static
# ---------------------------------------------------------------------------


app = FastAPI(title="HELP-E demo")

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "index.html"))


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    client = get_client()
    return {
        "ok": True,
        "main_url": client.main_url,
        "main_model": client.main_model,
        "sim_url": client.sim_url,
        "sim_model": client.sim_model,
        "ollama_reachable": client.ping(),
    }


# ---------------------------------------------------------------------------
# Catalog: systems + profiles
# ---------------------------------------------------------------------------


@app.get("/api/systems")
def api_systems() -> dict:
    return {
        "systems": [
            {"id": sid, "label": s["label"], "description": s["description"]}
            for sid, s in SYSTEMS.items()
        ]
    }


@app.get("/api/profiles")
def api_profiles() -> dict:
    out = []
    for pid in list_profiles():
        try:
            p = load_profile(pid)
        except Exception as e:  # pragma: no cover
            log.warning("failed to load profile %s: %s", pid, e)
            continue
        out.append({
            "profile_id": p.profile_id,
            "primary_problem": p.primary_problem,
            "blurb": (p.blurb or p.seed_situation_paragraph)[:220],
        })
    return {"profiles": out}


# ---------------------------------------------------------------------------
# Conversation lifecycle
# ---------------------------------------------------------------------------


class NewConversationRequest(BaseModel):
    system: str
    profile_id: str
    mode: str = "human"


@app.post("/api/conversations")
async def api_new_conversation(req: NewConversationRequest) -> dict:
    if req.system not in SYSTEMS:
        raise HTTPException(400, f"unknown system {req.system!r}")
    if req.mode not in ("human", "agent"):
        raise HTTPException(400, f"unknown mode {req.mode!r}")
    try:
        profile = load_profile(req.profile_id)
    except FileNotFoundError:
        raise HTTPException(404, f"profile {req.profile_id!r} not found")

    variant = SYSTEMS[req.system]["variant"]
    cid = uuid.uuid4().hex[:12]

    if variant == "v6":
        graph_v6 = ProblemGraphV6(profile_id=profile.profile_id)
        sim_profile = _to_simulator_profile(profile)
        sc_ctx = CallContext(
            profile_id=profile.profile_id, session_id=1, system=req.system,
            turn_id=0, call_role="session_context",
        )
        client = get_client()
        try:
            session_context = await asyncio.to_thread(
                run_session_context,
                client=client, ctx=sc_ctx,
                profile=sim_profile,
            )
        except Exception as e:
            log.exception("session_context failed: %s", e)
            raise HTTPException(500, f"session_context failed: {e}")

        state = ConversationState(
            id=cid,
            system=req.system,
            profile_id=req.profile_id,
            graph=graph_v6,
            profile=profile,
            mode=req.mode,
            previous_main_problem=None,  # v6 cold-start: no pre-seeded main
            simulator_profile=sim_profile,
            session_context=session_context,
            last_system_message=None,
        )
        _conversations[cid] = state
        arc_cue = (
            session_context.get("why_bringing_these_up_now")
            or session_context.get("current_life_events", "")
        )
        return {
            "conversation_id": cid,
            "system": req.system,
            "system_label": SYSTEMS[req.system]["label"],
            "profile_id": profile.profile_id,
            "profile_blurb": profile.blurb or profile.seed_situation_paragraph,
            "primary_problem": profile.primary_problem,
            "mode": state.mode,
            "session_arc_cue": arc_cue,
            "initial_graph": graph_v6.to_json_dict(),
            "variant": "v6",
            "session_context": session_context,
        }

    # v1–v5 path
    graph = _load_or_seed_graph(profile)
    state = ConversationState(
        id=cid,
        system=req.system,
        profile_id=req.profile_id,
        graph=graph,
        profile=profile,
        mode=req.mode,
        previous_main_problem=profile.primary_problem,
    )
    # Snapshot traces list onto graph for the retrieval helper used by v4/v5
    # (bundle builder reads `graph._session_traces` if present).
    state.graph._session_traces = state.traces  # type: ignore[attr-defined]
    _conversations[cid] = state
    return {
        "conversation_id": cid,
        "system": req.system,
        "system_label": SYSTEMS[req.system]["label"],
        "profile_id": profile.profile_id,
        "profile_blurb": profile.blurb or profile.seed_situation_paragraph,
        "primary_problem": profile.primary_problem,
        "mode": state.mode,
        "session_arc_cue": _pick_arc_cue(profile, state.session_id),
        "initial_graph": graph.to_dict(),
        "variant": "v5",
    }


class MessageRequest(BaseModel):
    message: str


async def _run_turn(state: ConversationState, user_message: str,
                    *, agent_generated: bool) -> dict:
    """Append a user turn and run the per-turn pipeline. Caller must hold state.lock."""
    variant = SYSTEMS[state.system]["variant"]
    if variant == "v6":
        return await _run_turn_v6(
            state, user_message, agent_generated=agent_generated,
        )
    return await _run_turn_v5(
        state, user_message, agent_generated=agent_generated,
    )


async def _run_turn_v5(state: ConversationState, user_message: str,
                       *, agent_generated: bool) -> dict:
    turn_id = len(state.turns) + 1
    state.transcript.append({
        "role": "user", "turn_id": turn_id, "text": user_message,
    })

    turn_fn = SYSTEMS[state.system]["turn_fn"]
    client = get_client()
    recent_turns = state.transcript[-(config.LAST_N_TURNS * 2):-1]  # exclude just-added user turn

    t0 = time.monotonic()
    def _call() -> dict:
        return turn_fn(
            client=client,
            ctx_profile=state.profile_id,
            system=state.system,
            session_id=state.session_id,
            turn_id=turn_id,
            user_message=user_message,
            recent_turns=recent_turns,
            prior_session_summary=state.prior_session_summary,
            previous_main_problem=state.previous_main_problem,
            graph=state.graph,
            last_n_turns=config.LAST_N_TURNS,
        )

    try:
        result = await asyncio.to_thread(_call)
    except Exception as e:
        log.exception("turn_fn failed: %s", e)
        state.transcript.pop()
        raise HTTPException(500, f"turn_fn failed: {e}")
    elapsed = time.monotonic() - t0

    trace = result["trace"]
    state.traces.append(trace)
    if trace.main_problem:
        state.previous_main_problem = trace.main_problem

    merged = result.get("merged") or {}
    assistant_text = merged.get("response", "")
    state.transcript.append({
        "role": "assistant", "turn_id": turn_id, "text": assistant_text,
    })

    record = TurnRecord(
        turn_id=turn_id,
        user_message=user_message,
        assistant_response=assistant_text,
        trace=_trace_to_dict(trace),
        bundle=result.get("bundle"),
        candidates=result.get("candidates") or [],
        merged=merged,
        graph_snapshot=state.graph.to_dict(),
        elapsed_s=round(elapsed, 2),
        variant="v5",
    )
    state.turns.append(record)
    payload = _turn_payload(record)
    payload["agent_generated"] = agent_generated
    return payload


# v6 redesign: response trace already carries `chosen_misc_codes`
# (extracted by `baselines.v6_full._extract_misc_codes_from_reasoning`),
# so the UI no longer needs its own MISC-code regex.


async def _run_turn_v6(state: ConversationState, user_message: str,
                       *, agent_generated: bool) -> dict:
    turn_id = len(state.turns) + 1
    state.transcript.append({
        "role": "user", "turn_id": turn_id, "text": user_message,
    })

    client = get_client()
    recent_turns = state.transcript[-(config.LAST_N_TURNS * 2):-1]

    t0 = time.monotonic()
    def _call() -> dict:
        return v6_turn_fn(
            client=client,
            profile_id=state.profile_id,
            system=state.system,
            session_id=state.session_id,
            turn_id=turn_id,
            user_message=user_message,
            recent_turns=recent_turns,
            last_system_message=state.last_system_message,
            prior_session_summary=state.prior_session_summary,
            graph=state.graph,  # ProblemGraphV6
            last_n_turns=config.LAST_N_TURNS,
        )

    try:
        result = await asyncio.to_thread(_call)
    except Exception as e:
        log.exception("v6 turn_fn failed: %s", e)
        state.transcript.pop()
        raise HTTPException(500, f"v6 turn_fn failed: {e}")
    elapsed = time.monotonic() - t0

    raw_trace = result.get("trace") or {}
    response_obj = result.get("response") or {}
    candidate_bundle = result.get("candidate_bundle") or {}
    candidates = (
        list(candidate_bundle.get("common_candidates") or [])
        + list(candidate_bundle.get("stage_specific_candidates") or [])
    )

    reasoning = response_obj.get("reasoning", "")
    evidence_used = response_obj.get("evidence_used", []) or []
    final_response = response_obj.get("final_response", "")

    # v6 redesign: chosen MISC codes are extracted into the trace by
    # baselines.v6_full; UI just consumes it.
    chosen = list(raw_trace.get("chosen_misc_codes") or [])

    # Normalize v6 trace/ttm updates into the UI's v5-shape vocabulary so
    # the existing summary renderer keeps working.
    ttm_updates_norm: list[dict] = []
    for u in raw_trace.get("ttm_updates") or []:
        ttm_updates_norm.append({
            "problem_name": u.get("problem_name"),
            "current_ttm_stage": u.get("old_ttm_stage"),
            "new_ttm_stage": u.get("new_ttm_stage"),
            "is_change": u.get("old_ttm_stage") != u.get("new_ttm_stage"),
            "rationale": u.get("reasoning", ""),
        })

    level_updates_norm: list[dict] = []
    for u in raw_trace.get("level_updates") or []:
        level_updates_norm.append({
            "attr_type": u.get("attribute_name"),
            "problem_name": u.get("problem_name"),
            "current_level": u.get("old_level"),
            "new_level": u.get("new_level"),
            "rationale": u.get("reasoning", ""),
        })

    trace_dict = {
        "main_problem": raw_trace.get("main_problem"),
        "active_problems": raw_trace.get("current_problems") or [],
        "extraction": {
            "user_intent": raw_trace.get("user_intent"),
            "observed_attributes": [
                {
                    "attr_type": e["attribute_name"],
                    "value": e.get("inferred_information", ""),
                    "problem": e.get("problem_name"),
                }
                for e in (result.get("inference") or {}).get(
                    "problem_attribute_entries"
                ) or []
            ],
        },
        "ttm_updates": ttm_updates_norm,
        "level_updates": level_updates_norm,
        "empty_turn": not (raw_trace.get("current_problems") or []),
        "carried_forward_main": False,
    }

    # Merged-call compatibility shape for the UI. v6 redesign collapses
    # `system_intent` + `response_reasoning` into the single `reasoning`
    # field; we surface it under both legacy keys so existing UI panels
    # keep rendering without further frontend changes.
    merged = {
        "system_intent": reasoning,
        "instruction": reasoning,
        "chosen_techniques": chosen,
        "response": final_response,
        "evidence_used": evidence_used,
    }

    main_name = raw_trace.get("main_problem")
    if main_name:
        state.previous_main_problem = main_name

    state.transcript.append({
        "role": "assistant", "turn_id": turn_id, "text": final_response,
    })
    state.last_system_message = final_response or None

    record = TurnRecord(
        turn_id=turn_id,
        user_message=user_message,
        assistant_response=final_response,
        trace=trace_dict,
        bundle=result.get("bundle"),
        candidates=candidates,
        merged=merged,
        graph_snapshot=state.graph.to_json_dict(),
        elapsed_s=round(elapsed, 2),
        variant="v6",
        extras={
            "inference": result.get("inference"),
            "recompute": result.get("recompute"),
            "candidate_bundle": candidate_bundle,
            "past_two_turns": result.get("past_two_turns") or [],
            "reasoning": reasoning,
            "evidence_used": evidence_used,
            "session_context": state.session_context,
            "turn_scope_level_attrs": raw_trace.get("turn_scope_level_attrs") or [],
            "cooc_added": raw_trace.get("cooc_added", 0),
            "attr_conn_added": raw_trace.get("attr_conn_added", 0),
        },
    )
    state.turns.append(record)
    payload = _turn_payload(record)
    payload["agent_generated"] = agent_generated
    return payload


@app.post("/api/conversations/{cid}/messages")
async def api_post_message(cid: str, req: MessageRequest) -> dict:
    state = _conversations.get(cid)
    if state is None:
        raise HTTPException(404, "conversation not found")
    user_message = req.message.strip()
    if not user_message:
        raise HTTPException(400, "empty message")

    async with state.lock:
        return await _run_turn(state, user_message, agent_generated=False)


@app.post("/api/conversations/{cid}/agent_reply")
async def api_agent_reply(cid: str) -> dict:
    """Generate the next user utterance via Mind-1, then run the turn pipeline."""
    state = _conversations.get(cid)
    if state is None:
        raise HTTPException(404, "conversation not found")

    async with state.lock:
        turn_id = len(state.turns) + 1
        client = get_client()
        variant = SYSTEMS[state.system]["variant"]

        if variant == "v6":
            if state.simulator_profile is None or state.session_context is None:
                raise HTTPException(500, "v6 conversation missing simulator state")
            m1_ctx = CallContext(
                profile_id=state.profile_id,
                session_id=state.session_id,
                system=state.system,
                turn_id=turn_id,
                call_role="mind1_v6",
            )
            past_turns_for_mind1 = list(state.transcript)

            def _mind1_v6() -> dict:
                return run_mind1_v6(
                    client=client,
                    ctx=m1_ctx,
                    profile=state.simulator_profile,
                    session_context=state.session_context,
                    past_turns=past_turns_for_mind1,
                    last_system_message=state.last_system_message,
                )

            try:
                mind1_out = await asyncio.to_thread(_mind1_v6)
            except Exception as e:
                log.exception("mind1_v6 failed: %s", e)
                raise HTTPException(500, f"mind1_v6 failed: {e}")

            user_message = (mind1_out.get("simulated_user_message") or "").strip()
            if not user_message:
                raise HTTPException(500, "mind1_v6 returned empty utterance")
            return await _run_turn(state, user_message, agent_generated=True)

        # v1–v5 path
        mind1_ctx = CallContext(
            profile_id=state.profile_id,
            session_id=state.session_id,
            system=state.system,
            turn_id=turn_id,
            call_role="mind1",
        )
        recent_turns = state.transcript[-(config.LAST_N_TURNS * 2):]

        def _mind1() -> dict:
            return run_mind1(
                client=client,
                ctx=mind1_ctx,
                persona=state.profile.to_mind1_persona(),
                session_arc_cue=_pick_arc_cue(state.profile, state.session_id),
                prior_session_summary=state.prior_session_summary,
                recent_turns=recent_turns,
            )

        try:
            mind1_out = await asyncio.to_thread(_mind1)
        except Exception as e:
            log.exception("mind1 failed: %s", e)
            raise HTTPException(500, f"mind1 failed: {e}")

        user_message = (mind1_out.get("utterance") or "").strip()
        if not user_message:
            raise HTTPException(500, "mind1 returned empty utterance")

        return await _run_turn(state, user_message, agent_generated=True)


@app.get("/api/conversations/{cid}")
def api_get_conversation(cid: str) -> dict:
    state = _conversations.get(cid)
    if state is None:
        raise HTTPException(404, "conversation not found")
    variant = SYSTEMS[state.system]["variant"]
    graph_snapshot = (
        state.graph.to_json_dict() if variant == "v6" else state.graph.to_dict()
    )
    return {
        "conversation_id": cid,
        "system": state.system,
        "system_label": SYSTEMS[state.system]["label"],
        "profile_id": state.profile_id,
        "profile_blurb": state.profile.blurb or state.profile.seed_situation_paragraph,
        "primary_problem": state.profile.primary_problem,
        "mode": state.mode,
        "session_id": state.session_id,
        "variant": variant,
        "turns": [_turn_payload(t) for t in state.turns],
        "graph_snapshot": graph_snapshot,
    }


@app.delete("/api/conversations/{cid}")
def api_delete_conversation(cid: str) -> dict:
    if cid in _conversations:
        del _conversations[cid]
        return {"deleted": True}
    raise HTTPException(404, "conversation not found")


@app.get("/api/conversations")
def api_list_conversations() -> dict:
    return {
        "conversations": [
            {
                "conversation_id": c.id,
                "system": c.system,
                "profile_id": c.profile_id,
                "turn_count": len(c.turns),
                "created_ts": c.created_ts,
            }
            for c in _conversations.values()
        ]
    }


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _trace_to_dict(trace) -> dict:
    # TurnTrace is a dataclass in graph_update; asdict works.
    if hasattr(trace, "__dataclass_fields__"):
        return asdict(trace)
    return dict(trace)


def _turn_payload(record: TurnRecord) -> dict:
    trace = record.trace
    extraction = trace.get("extraction", {}) or {}
    snap = record.graph_snapshot or {}
    # v6 stores problems as a dict; v1–v5 store them as a list.
    problems_field = snap.get("problems")
    if isinstance(problems_field, dict):
        ttm_map = {
            name: p.get("current_ttm_stage")
            for name, p in problems_field.items()
        }
    else:
        ttm_map = {
            p["problem_name"]: p.get("current_ttm_stage")
            for p in (problems_field or [])
        }
    # v3 exposes inferred TTM on trace.extraction (ttm_stages_inferred).
    if "ttm_stages_inferred" in extraction:
        ttm_map.update(extraction["ttm_stages_inferred"])
    payload = {
        "turn_id": record.turn_id,
        "user_message": record.user_message,
        "assistant_response": record.assistant_response,
        "elapsed_s": record.elapsed_s,
        "user_intent": extraction.get("user_intent"),
        "main_problem": trace.get("main_problem"),
        "active_problems": trace.get("active_problems") or [],
        "ttm_stages": ttm_map,
        "ttm_updates": trace.get("ttm_updates") or [],
        "level_updates": trace.get("level_updates") or [],
        "empty_turn": trace.get("empty_turn"),
        "carried_forward_main": trace.get("carried_forward_main"),
        "system_intent": record.merged.get("system_intent"),
        "instruction": record.merged.get("instruction"),
        "chosen_techniques": record.merged.get("chosen_techniques") or [],
        "candidates": record.candidates,
        "extraction": extraction,
        "bundle": record.bundle,
        "graph_stats": snap.get("stats", {}),
        "graph_snapshot": record.graph_snapshot,
        "fallback_default": record.merged.get("_fallback_default", False),
        "variant": record.variant,
    }
    if record.variant == "v6":
        payload["v6"] = record.extras
    return payload


# ---------------------------------------------------------------------------
# Error handler — don't leak raw 500 HTML
# ---------------------------------------------------------------------------


@app.exception_handler(HTTPException)
def _http_handler(_request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code, content={"error": exc.detail}
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run(
        "help_e.ui.server:app",
        host=args.host, port=args.port, reload=args.reload,
    )


if __name__ == "__main__":
    main()
