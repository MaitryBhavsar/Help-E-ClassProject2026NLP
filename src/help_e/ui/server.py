"""FastAPI backend for the interactive demo UI.

Each conversation is a per-profile, per-system run with an in-memory
graph and transcript. The UI currently supports v1/v3/v7/v8 using the
same v6-aligned turn_fn signature/response shape.

Run:
    ./scripts/run_ui.sh
then open http://127.0.0.1:8765 (override with HELPE_UI_PORT).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


# Per-browser identity used to scope conversations on the public demo.
# The frontend generates a UUID, stores it in localStorage, and sends
# it on every API call as this header. Two visitors with different IDs
# cannot see, list, open, or delete each other's conversations.
CLIENT_ID_HEADER = "x-client-id"


def _require_client_id(request: Request) -> str:
    cid = (request.headers.get(CLIENT_ID_HEADER) or "").strip()
    if not cid:
        raise HTTPException(400, f"missing {CLIENT_ID_HEADER} header")
    return cid


def _get_owned(cid: str, client_id: str) -> "ConversationState":
    state = _conversations.get(cid)
    # Return 404 (not 403) so cross-client probes can't enumerate IDs.
    if state is None or state.owner_client_id != client_id:
        raise HTTPException(404, "conversation not found")
    return state

from .. import config
from ..baselines.v1_full import v1_turn_fn
from ..baselines.v3_full import v3_turn_fn
from ..baselines.v7_full import v7_turn_fn
from ..baselines.v8_full import v8_turn_fn
from ..graph_v3 import ProblemGraphV3
from ..graph_v6 import ProblemGraphV6
from ..graph_v7 import ProblemGraphV7
from ..llm_client import CallContext, get_client
from ..session_driver import ProfileSpec, list_profiles, load_profile
from ..session_driver_v6 import _to_simulator_profile
from ..simulator.mind1_v6 import run_mind1_v6
from ..simulator.session_context import SimulatorProfile, run_session_context


log = logging.getLogger(__name__)

# Cached chat probes — GET /v1/models can be green while chat returns 401.
_CHAT_PROBE_CACHE: Optional[tuple[float, dict[str, Any]]] = None
_CHAT_PROBE_TTL_S: float = 120.0


SYSTEMS: dict[str, Any] = {
    "v3": {
        "label": "v3 — summary + TTM inference",
        "turn_fn": v3_turn_fn,
        "description": "Per-problem running summaries plus per-problem TTM stage inferred from the summary. No attribute graph.",
    },
    "v1": {
        "label": "v1 — history-only (baseline)",
        "turn_fn": v1_turn_fn,
        "description": "No graph, no TTM. Simple MI rule on the last user message.",
    },
    "v7": {
        "label": "v7 — multi-agent graph pipeline",
        "turn_fn": v7_turn_fn,
        "description": "Multi-agent graph pipeline with per-problem attributes, TTM, and edge summaries.",
    },
    "v8": {
        "label": "v8 — multi-agent + dense retrieval (latest)",
        "turn_fn": v8_turn_fn,
        "description": "V7-style graph pipeline with dense retrieval over graph evidence.",
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
    variant: str = "v6"
    extras: dict = field(default_factory=dict)


@dataclass
class ConversationState:
    """Multi-session conversation state.

    `turns_by_session[N]` holds the ordered turns of session N. Sessions
    are 1-indexed; new sessions are added by `start_new_session()`. The
    `turns` property exposes a flat list for any caller that wants the
    full history regardless of session boundary (kept for compatibility
    with the existing renderer).
    """

    id: str
    system: str
    profile_id: str
    graph: Any
    profile: ProfileSpec
    owner_client_id: str = ""  # X-Client-ID of the browser that created this convo
    mode: str = "human"  # "human": user types; "agent": Mind-1 generates user turns
    transcript: list[dict] = field(default_factory=list)
    traces: list = field(default_factory=list)
    previous_main_problem: Optional[str] = None
    turns_by_session: dict[int, list[TurnRecord]] = field(default_factory=dict)
    session_started_ts: dict[int, float] = field(default_factory=dict)
    session_ended_ts: dict[int, Optional[float]] = field(default_factory=dict)
    session_id: int = 1
    prior_session_summary: Optional[str] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    created_ts: float = field(default_factory=time.time)
    updated_ts: float = field(default_factory=time.time)
    simulator_profile: Optional[SimulatorProfile] = None
    session_context: Optional[dict] = None
    last_system_message: Optional[str] = None

    @property
    def turns(self) -> list[TurnRecord]:
        """Flat list across sessions in session-then-turn order."""
        out: list[TurnRecord] = []
        for sid in sorted(self.turns_by_session.keys()):
            out.extend(self.turns_by_session[sid])
        return out

    def current_turns(self) -> list[TurnRecord]:
        return self.turns_by_session.setdefault(self.session_id, [])


_conversations: dict[str, ConversationState] = {}
_persistence_lock = asyncio.Lock()


def _new_graph_for_system(system: str, profile_id: str) -> Any:
    if system in ("v7", "v8"):
        return ProblemGraphV7(profile_id=profile_id)
    # v1 reuses ProblemGraphV3 for plumbing (problems/edges stay empty;
    # only persona + rolling_summary_5turns are populated).
    if system in ("v1", "v3"):
        return ProblemGraphV3(profile_id=profile_id)
    return ProblemGraphV6(profile_id=profile_id)


def _graph_from_json_for_system(system: str, payload: dict, profile_id: str) -> Any:
    if system in ("v7", "v8"):
        return ProblemGraphV7.from_json_dict(payload or {"profile_id": profile_id})
    if system in ("v1", "v3"):
        return ProblemGraphV3.from_json_dict(payload or {"profile_id": profile_id})
    return ProblemGraphV6.from_json_dict(payload or {"profile_id": profile_id})


# ---------------------------------------------------------------------------
# Persistence — one JSON file per conversation under UI_CONVERSATIONS_DIR.
# ---------------------------------------------------------------------------


def _state_dir() -> Path:
    config.UI_CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)
    return config.UI_CONVERSATIONS_DIR


def _state_path(cid: str) -> Path:
    return _state_dir() / f"{cid}.json"


def _record_to_dict(record: TurnRecord) -> dict:
    return {
        "turn_id": record.turn_id,
        "user_message": record.user_message,
        "assistant_response": record.assistant_response,
        "trace": record.trace,
        "bundle": record.bundle,
        "candidates": list(record.candidates),
        "merged": record.merged,
        "graph_snapshot": record.graph_snapshot,
        "elapsed_s": record.elapsed_s,
        "variant": record.variant,
        "extras": record.extras,
    }


def _record_from_dict(d: dict) -> TurnRecord:
    return TurnRecord(
        turn_id=int(d["turn_id"]),
        user_message=d.get("user_message", ""),
        assistant_response=d.get("assistant_response", ""),
        trace=d.get("trace") or {},
        bundle=d.get("bundle"),
        candidates=list(d.get("candidates") or []),
        merged=d.get("merged") or {},
        graph_snapshot=d.get("graph_snapshot") or {},
        elapsed_s=float(d.get("elapsed_s") or 0.0),
        variant=d.get("variant") or "v6",
        extras=d.get("extras") or {},
    )


def _serialize_state(state: ConversationState) -> dict:
    sessions: list[dict] = []
    for sid in sorted(state.turns_by_session.keys()):
        sessions.append({
            "session_id": sid,
            "started_ts": state.session_started_ts.get(sid),
            "ended_ts": state.session_ended_ts.get(sid),
            "turns": [_record_to_dict(t) for t in state.turns_by_session[sid]],
        })
    sim_profile = (
        asdict(state.simulator_profile) if state.simulator_profile is not None
        else None
    )
    return {
        "schema_version": 2,
        "conversation_id": state.id,
        "system": state.system,
        "profile_id": state.profile_id,
        "owner_client_id": state.owner_client_id,
        "mode": state.mode,
        "created_ts": state.created_ts,
        "updated_ts": state.updated_ts,
        "current_session_id": state.session_id,
        "previous_main_problem": state.previous_main_problem,
        "last_system_message": state.last_system_message,
        "session_context": state.session_context,
        "simulator_profile": sim_profile,
        "graph": state.graph.to_json_dict(),
        "transcript": list(state.transcript),
        "sessions": sessions,
    }


async def _save_state(state: ConversationState) -> None:
    """Atomic write: temp file in same dir, then os.replace."""
    state.updated_ts = time.time()
    payload = _serialize_state(state)
    path = _state_path(state.id)
    async with _persistence_lock:
        await asyncio.to_thread(_atomic_write_json, path, payload)


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _delete_state_file(cid: str) -> None:
    path = _state_path(cid)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("failed to delete UI state file %s: %s", path, e)


def _hydrate_state(d: dict) -> Optional[ConversationState]:
    """Best-effort rebuild of a ConversationState from a persisted dict.

    Returns None if the file is too damaged to be useful (e.g. missing
    profile, broken graph). The caller logs the skip and continues.
    """
    cid = d.get("conversation_id")
    system = d.get("system")
    profile_id = d.get("profile_id")
    if not cid or system not in SYSTEMS or not profile_id:
        return None
    try:
        profile = load_profile(profile_id)
    except Exception as e:
        log.warning("hydration: profile %r missing for cid=%s: %s", profile_id, cid, e)
        return None
    try:
        graph = _graph_from_json_for_system(system, d.get("graph") or {}, profile_id)
    except Exception as e:
        log.warning("hydration: graph rebuild failed for cid=%s: %s", cid, e)
        graph = _new_graph_for_system(system, profile_id)

    sim_profile_d = d.get("simulator_profile")
    sim_profile: Optional[SimulatorProfile] = None
    if isinstance(sim_profile_d, dict):
        try:
            sim_profile = SimulatorProfile(**sim_profile_d)
        except TypeError as e:
            log.warning("hydration: simulator_profile malformed for cid=%s: %s", cid, e)

    state = ConversationState(
        id=cid,
        system=system,
        profile_id=profile_id,
        graph=graph,
        profile=profile,
        owner_client_id=d.get("owner_client_id") or "",
        mode=d.get("mode") or "human",
        previous_main_problem=d.get("previous_main_problem"),
        simulator_profile=sim_profile,
        session_context=d.get("session_context"),
        last_system_message=d.get("last_system_message"),
        session_id=int(d.get("current_session_id") or 1),
        created_ts=float(d.get("created_ts") or time.time()),
        updated_ts=float(d.get("updated_ts") or time.time()),
    )
    state.transcript = list(d.get("transcript") or [])
    sessions = d.get("sessions") or []
    for sess in sessions:
        sid = int(sess.get("session_id") or 1)
        state.turns_by_session[sid] = [
            _record_from_dict(t) for t in (sess.get("turns") or [])
        ]
        started = sess.get("started_ts")
        if started is not None:
            state.session_started_ts[sid] = float(started)
        ended = sess.get("ended_ts")
        state.session_ended_ts[sid] = float(ended) if ended is not None else None
    # Make sure the current session always has a turns list, even if empty.
    state.turns_by_session.setdefault(state.session_id, [])
    state.session_started_ts.setdefault(state.session_id, state.created_ts)
    return state


def _load_all_states() -> int:
    """Load every {cid}.json on startup. Returns count loaded.

    Skips unreadable / corrupt files with a warning rather than crashing
    — the UI should still come up if one conversation file is bad.
    """
    d = _state_dir()
    loaded = 0
    for path in sorted(d.glob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            log.warning("ui-persistence: skipping unreadable %s: %s", path, e)
            continue
        state = _hydrate_state(payload)
        if state is None:
            log.warning("ui-persistence: skipping unhydratable %s", path)
            continue
        _conversations[state.id] = state
        loaded += 1
    return loaded


# ---------------------------------------------------------------------------
# FastAPI app + static
# ---------------------------------------------------------------------------


app = FastAPI(title="HELP-E demo")

_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.on_event("startup")
def _hydrate_on_startup() -> None:
    try:
        n = _load_all_states()
    except Exception as e:
        log.exception("ui-persistence: hydration failed: %s", e)
        return
    log.info(
        "ui-persistence: loaded %d conversation(s) from %s", n,
        config.UI_CONVERSATIONS_DIR,
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        str(_STATIC_DIR / "index.html"),
        headers={"Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health(verify_chat: bool = Query(False)) -> dict:
    client = get_client()
    out: dict[str, Any] = {
        "ok": True,
        "main_url": client.main_url,
        "main_model": client.main_model,
        "sim_url": client.sim_url,
        "sim_model": client.sim_model,
        "ollama_reachable": client.ping(),
    }
    if verify_chat:
        global _CHAT_PROBE_CACHE
        now = time.time()
        if (
            _CHAT_PROBE_CACHE is not None
            and (now - _CHAT_PROBE_CACHE[0]) < _CHAT_PROBE_TTL_S
        ):
            out["chat_completion"] = _CHAT_PROBE_CACHE[1]
        else:
            probes = client.probe_all_chat_endpoints()
            _CHAT_PROBE_CACHE = (now, probes)
            out["chat_completion"] = probes
            main_ok = probes.get("main", {}).get("ok")
            sim_ok = probes.get("sim", {}).get("ok")
            if not main_ok or not sim_ok:
                log.warning(
                    "ui health: chat probe failed main=%s sim=%s",
                    probes.get("main"),
                    probes.get("sim"),
                )
    return out


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
async def api_new_conversation(req: NewConversationRequest, request: Request) -> dict:
    client_id = _require_client_id(request)
    if req.system not in SYSTEMS:
        raise HTTPException(400, f"unknown system {req.system!r}")
    if req.mode not in ("human", "agent"):
        raise HTTPException(400, f"unknown mode {req.mode!r}")
    try:
        profile = load_profile(req.profile_id)
    except FileNotFoundError:
        raise HTTPException(404, f"profile {req.profile_id!r} not found")

    log.info(
        "ui: new conversation system=%s profile=%s mode=%s client=%s",
        req.system, req.profile_id, req.mode, client_id[:8],
    )

    cid = uuid.uuid4().hex[:12]
    graph_state = _new_graph_for_system(req.system, profile.profile_id)
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
        graph=graph_state,
        profile=profile,
        owner_client_id=client_id,
        mode=req.mode,
        previous_main_problem=None,  # v6 cold-start: no pre-seeded main
        simulator_profile=sim_profile,
        session_context=session_context,
        last_system_message=None,
    )
    # Initialize session 1 bookkeeping.
    state.turns_by_session[1] = []
    state.session_started_ts[1] = state.created_ts
    state.session_ended_ts[1] = None
    _conversations[cid] = state
    await _save_state(state)
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
        "initial_graph": graph_state.to_json_dict(),
        "variant": req.system,
        "session_context": session_context,
    }


class MessageRequest(BaseModel):
    message: str


async def _run_turn(state: ConversationState, user_message: str,
                    *, agent_generated: bool) -> dict:
    """Append a user turn and run the per-turn pipeline. Caller must hold state.lock.

    All four systems (v1, v3, v7, v8) share the v6-aligned turn_fn signature
    and the v6 response shape, so this dispatch is uniform.
    """
    current = state.current_turns()
    turn_id = len(current) + 1
    state.transcript.append({
        "role": "user", "turn_id": turn_id, "text": user_message,
        "session_id": state.session_id,
    })

    client = get_client()
    recent_turns = state.transcript[-(config.LAST_N_TURNS * 2):-1]
    turn_fn = SYSTEMS[state.system]["turn_fn"]

    t0 = time.monotonic()
    def _call() -> dict:
        return turn_fn(
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
        log.exception("%s turn_fn failed: %s", state.system, e)
        state.transcript.pop()
        raise HTTPException(500, f"{state.system} turn_fn failed: {e}")
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
        "session_id": state.session_id,
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
        variant=state.system,
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
    current.append(record)
    payload = _turn_payload(record)
    payload["agent_generated"] = agent_generated
    payload["session_id"] = state.session_id
    await _save_state(state)
    return payload


@app.post("/api/conversations/{cid}/messages")
async def api_post_message(cid: str, req: MessageRequest, request: Request) -> dict:
    state = _get_owned(cid, _require_client_id(request))
    user_message = req.message.strip()
    if not user_message:
        raise HTTPException(400, "empty message")

    async with state.lock:
        return await _run_turn(state, user_message, agent_generated=False)


@app.post("/api/conversations/{cid}/agent_reply")
async def api_agent_reply(cid: str, request: Request) -> dict:
    """Generate the next user utterance via Mind-1, then run the turn pipeline."""
    state = _get_owned(cid, _require_client_id(request))

    async with state.lock:
        turn_id = len(state.current_turns()) + 1
        client = get_client()
        if state.simulator_profile is None or state.session_context is None:
            raise HTTPException(500, "conversation missing simulator state")
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


@app.post("/api/conversations/{cid}/sessions")
async def api_start_new_session(cid: str, request: Request) -> dict:
    """Start a new session inside an existing conversation.

    Mirrors the v6 matrix pipeline: bumps `session_id`, re-runs
    `run_session_context` for the new arc cue, but keeps the same
    `ProblemGraphV6` in memory and does NOT compute a session summary
    (matrix sets `prior_session_summary=None` between sessions —
    `session_driver_v6.py:327`). The graph is the cross-session memory.
    """
    state = _get_owned(cid, _require_client_id(request))

    async with state.lock:
        prior_sid = state.session_id
        # Don't allow opening a fresh empty session on top of an empty one.
        if not state.turns_by_session.get(prior_sid):
            raise HTTPException(
                400,
                f"session {prior_sid} has no turns yet — send at least one "
                "message before starting a new session.",
            )
        state.session_ended_ts[prior_sid] = time.time()

        new_sid = prior_sid + 1
        state.session_id = new_sid
        state.turns_by_session[new_sid] = []
        state.session_started_ts[new_sid] = time.time()
        state.session_ended_ts[new_sid] = None
        # Cross-session: graph carries forward (matrix contract). No summary.
        state.prior_session_summary = None
        # Reset per-session continuity hint; the new session starts fresh.
        state.last_system_message = None

        if state.simulator_profile is None:
            log.warning("cid=%s missing simulator_profile; skipping session_context refresh", cid)
            new_session_context = state.session_context
        else:
            sc_ctx = CallContext(
                profile_id=state.profile_id, session_id=new_sid,
                system=state.system, turn_id=0, call_role="session_context",
            )
            client = get_client()
            try:
                new_session_context = await asyncio.to_thread(
                    run_session_context,
                    client=client, ctx=sc_ctx,
                    profile=state.simulator_profile,
                )
            except Exception as e:
                log.exception("session_context failed for new session: %s", e)
                raise HTTPException(500, f"session_context failed: {e}")
            state.session_context = new_session_context

        await _save_state(state)
        arc_cue = ""
        if isinstance(new_session_context, dict):
            arc_cue = (
                new_session_context.get("why_bringing_these_up_now")
                or new_session_context.get("current_life_events", "")
            )
        return {
            "conversation_id": cid,
            "session_id": new_sid,
            "session_arc_cue": arc_cue,
            "session_context": new_session_context,
        }


def _sessions_payload(state: ConversationState) -> list[dict]:
    out: list[dict] = []
    for sid in sorted(state.turns_by_session.keys()):
        out.append({
            "session_id": sid,
            "started_ts": state.session_started_ts.get(sid),
            "ended_ts": state.session_ended_ts.get(sid),
            "turns": [_turn_payload(t) for t in state.turns_by_session[sid]],
        })
    return out


def _last_user_message(state: ConversationState) -> str:
    for entry in reversed(state.transcript):
        if entry.get("role") == "user":
            text = entry.get("text") or ""
            return text.strip().splitlines()[0][:140] if text else ""
    return ""


@app.get("/api/conversations/{cid}")
def api_get_conversation(cid: str, request: Request) -> dict:
    state = _get_owned(cid, _require_client_id(request))
    return {
        "conversation_id": cid,
        "system": state.system,
        "system_label": SYSTEMS[state.system]["label"],
        "profile_id": state.profile_id,
        "profile_blurb": state.profile.blurb or state.profile.seed_situation_paragraph,
        "primary_problem": state.profile.primary_problem,
        "mode": state.mode,
        "session_id": state.session_id,
        "variant": state.system,
        "created_ts": state.created_ts,
        "updated_ts": state.updated_ts,
        "session_context": state.session_context,
        "turns": [_turn_payload(t) for t in state.turns],
        "sessions": _sessions_payload(state),
        "graph_snapshot": state.graph.to_json_dict(),
    }


@app.delete("/api/conversations/{cid}")
async def api_delete_conversation(cid: str, request: Request) -> dict:
    client_id = _require_client_id(request)
    state = _conversations.get(cid)
    if state is None or state.owner_client_id != client_id:
        # 404 (not 403) so cross-client probes can't enumerate IDs.
        raise HTTPException(404, "conversation not found")
    del _conversations[cid]
    async with _persistence_lock:
        await asyncio.to_thread(_delete_state_file, cid)
    return {"deleted": True}


@app.get("/api/conversations")
def api_list_conversations(request: Request) -> dict:
    client_id = _require_client_id(request)
    items: list[dict] = []
    for c in _conversations.values():
        if c.owner_client_id != client_id:
            continue
        total_turns = sum(len(ts) for ts in c.turns_by_session.values())
        items.append({
            "conversation_id": c.id,
            "system": c.system,
            "system_label": SYSTEMS[c.system]["label"],
            "profile_id": c.profile_id,
            "primary_problem": c.profile.primary_problem,
            "mode": c.mode,
            "current_session_id": c.session_id,
            "session_count": len(c.turns_by_session),
            "total_turns": total_turns,
            "turn_count": total_turns,  # legacy field name
            "last_user_message_excerpt": _last_user_message(c),
            "created_ts": c.created_ts,
            "updated_ts": c.updated_ts,
        })
    items.sort(key=lambda x: x.get("updated_ts") or 0.0, reverse=True)
    return {"conversations": items}


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _turn_payload(record: TurnRecord) -> dict:
    trace = record.trace
    extraction = trace.get("extraction", {}) or {}
    snap = record.graph_snapshot or {}
    problems_field = snap.get("problems")
    ttm_map: dict[str, Optional[str]] = {}
    if isinstance(problems_field, dict):
        ttm_map = {
            name: p.get("current_ttm_stage")
            for name, p in problems_field.items()
        }
    # v3 exposes inferred TTM on trace.extraction (ttm_stages_inferred).
    if "ttm_stages_inferred" in extraction:
        ttm_map.update(extraction["ttm_stages_inferred"])
    return {
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
        "v6": record.extras,
    }


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

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run(
        "help_e.ui.server:app",
        host=args.host, port=args.port, reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
