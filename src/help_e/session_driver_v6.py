"""§6.10 v6 — multi-session driver for the v6 problem-centric pipeline.

Separate from `session_driver.py` so v1–v5 stay frozen. For one
`(profile, v6)` pair runs `run_cfg.sessions_per_profile` sessions:

    at session start:
        generate session_context (simulator-only hidden framing)

    per turn:
        mind1_v6 → user utterance + hidden reasoning sidecar
        v6_turn_fn → inference → graph writes → recompute →
                     edge weight recompute → bundle → MI candidates →
                     response
        append to transcript, log trace

    at session end:
        persona_update_v6 + apply to graph.persona
        session_summary (reuses v5's prompt — graph-agnostic)

    after all sessions:
        Mind-2 + Mind-3 (reused from v5; adapted inputs for v6 transcripts)

Persistence layout (under config.PACKAGE_ROOT unless overridden):

    transcripts/{profile}/v6/session_{NN}.json
    transcripts/{profile}/v6/mind1_reasoning_s{NN}.jsonl   (sidecar; never fed back)
    transcripts/{profile}/v6/session_context_s{NN}.json    (sidecar)
    transcripts/{profile}/v6/run_artifacts.json
    graphs_v6/{profile}_after_s{NN}.json                    (config.GRAPH_V6_DIR)

Cold-start: a fresh ProblemGraphV6 is created on session 1 with no
pre-seeding. Persona starts empty; it fills over sessions via
persona_update_v6.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import config
from .baselines.v6_full import v6_turn_fn
from .graph_v6 import ProblemGraphV6, PersonaState
from .llm_client import CallContext, LLMClient, get_client
from .eval.judge import run_miti_judge
from .eval.esc_judge import run_esc_judge
from .prompts.persona_update_v6 import apply_updates_to_persona, run_persona_update_v6
# v6 redesign (§9.2): no session-summary call. Sessions are independent
# for the simulator; the chatbot still carries graph + persona between
# sessions. `prompts/session_summary.py` and `simulator/mind2.py` were
# deleted as part of Phase D.
from .profile_spec import (
    ProfileSpec,
    RunConfig,
    list_profiles,
    load_profile,
)
from .simulator.mind1_v6 import run_mind1_v6
from .simulator.session_context import (
    SimulatorProfile,
    run_session_context,
)


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# v6 artifacts
# ---------------------------------------------------------------------------


@dataclass
class SessionArtifactsV6:
    session_id: int
    transcript: list[dict] = field(default_factory=list)
    turn_traces: list[dict] = field(default_factory=list)
    session_context: Optional[dict] = None
    mind1_hidden: list[dict] = field(default_factory=list)  # per-turn reasoning sidecar
    # v6 redesign: session_summary dropped (§9.2). MITI 4.2 4-globals
    # judge runs at session end and is stored here as a sidecar that the
    # main pipeline never reads.
    miti_judge: Optional[dict] = None
    esc_judge: Optional[dict] = None
    session_summary: str = ""  # kept for compat with old readers; always ""
    persona_updates: list[dict] = field(default_factory=list)
    stage_transitions: list[dict] = field(default_factory=list)


@dataclass
class RunArtifactsV6:
    profile_id: str
    system: str = "v6"
    sessions: list[SessionArtifactsV6] = field(default_factory=list)
    mind2_out: Optional[dict] = None
    # mind3_out kept for back-compat with old transcripts that still
    # have it; the new ESC judge writes per-session esc_judge_s{NN}.json
    # files instead.
    mind3_out: Optional[dict] = None

    def transcripts_for_minds(self) -> list[dict]:
        return [
            {"session_id": s.session_id, "turns": s.transcript}
            for s in self.sessions
        ]

    def all_user_turn_ids(self) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        for s in self.sessions:
            for t in s.transcript:
                if t["role"] == "user":
                    out.append((s.session_id, t["turn_id"]))
        return out


# ---------------------------------------------------------------------------
# Profile → SimulatorProfile
# ---------------------------------------------------------------------------


def _to_simulator_profile(profile: ProfileSpec) -> SimulatorProfile:
    """Shim: pull simulator-relevant fields from the shared ProfileSpec
    into the new (v6 redesign §9.3) `SimulatorProfile` shape.

    Tolerates both the new YAML schema (Phase G re-seeded profiles —
    `demographics`, `core_beliefs`, `hobbies_interests`,
    `communication_style` as a list) and the legacy schema (single
    string `communication_style`, missing demographics/beliefs/hobbies).
    Phase G is the operational re-seed step; until then, this shim
    fills sensible defaults from whatever fields are present so the
    simulator can still run.
    """
    pd = profile.persona_draft or {}

    raw_style = pd.get("communication_style", "")
    if isinstance(raw_style, list):
        style = [s for s in raw_style if s]
    elif isinstance(raw_style, str) and raw_style.strip():
        style = [raw_style.strip()]
    else:
        style = []

    demo = pd.get("demographics") or {}
    if not isinstance(demo, dict):
        demo = {}

    return SimulatorProfile(
        profile_id=profile.profile_id,
        demographics=dict(demo),
        personality_traits=list(pd.get("personality_traits") or []),
        communication_style=style,
        core_beliefs=list(pd.get("core_beliefs") or []),
        hobbies_interests=list(pd.get("hobbies_interests") or []),
        relevant_history=pd.get("relevant_history", "") or "",
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


SYSTEM_V6: str = "v6"


def run_profile_v6(
    *,
    profile: ProfileSpec,
    run_cfg: RunConfig = RunConfig(),
    client: Optional[LLMClient] = None,
    system: str = SYSTEM_V6,
    turn_fn: Optional[Any] = None,
) -> RunArtifactsV6:
    """Run `run_cfg.sessions_per_profile` sessions of `system` on this profile.

    The driver itself is system-agnostic: it always uses the v6
    simulator stack (mind1_v6 + session_context + persona_update_v6 +
    miti_judge_v6 + mind3) so ablation systems share the same user
    profile, session context, MISC vocabulary, TTM enum, and 3-field
    response output. The per-turn assistant call is what differs:
    `turn_fn` defaults to `v6_turn_fn` (full graph pipeline) but can be
    swapped for `v1_turn_fn` (history-only) or `v3_turn_fn` (text
    summary + TTM-from-summary). Each turn_fn must match v6_turn_fn's
    signature and return the same trace shape.

    Persistence happens after each session and after the full run.
    """
    client = client or get_client()
    artifacts = RunArtifactsV6(profile_id=profile.profile_id, system=system)

    # Cold-start graph: no pre-seeding, empty persona. v1 leaves it
    # empty; v3 attaches text-summary state to it; v6 fills it via the
    # inference + recompute calls.
    graph = ProblemGraphV6(profile_id=profile.profile_id)
    sim_profile = _to_simulator_profile(profile)

    effective_turn_fn = turn_fn or v6_turn_fn

    # v6 redesign (§9.2): no session-summary carryforward. Sessions are
    # independent for the simulator; the chatbot still carries graph +
    # persona between sessions.
    #
    # Cross-session judge parallelization: we launch session N+1's first
    # turns immediately while session N's miti_judge_v6 + esc_judge_v6
    # finish in this background pool. Each judge writes its own
    # `{miti,esc}_judge_s{NN}.json` file in its callback. The `with`
    # block at the end waits for any still-pending futures before
    # returning so the run report sees a complete set of judge files.
    from concurrent.futures import ThreadPoolExecutor, wait as _wait
    pending_futures: list = []
    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="judge") as judge_pool:
        for session_id in range(1, run_cfg.sessions_per_profile + 1):
            session_art = _run_session_v6(
                profile=profile, sim_profile=sim_profile,
                session_id=session_id, run_cfg=run_cfg,
                client=client, graph=graph,
                system=system, turn_fn=effective_turn_fn,
                judge_pool=judge_pool,
                pending_futures=pending_futures,
            )
            artifacts.sessions.append(session_art)

            _save_session_artifacts_v6(profile.profile_id, session_art, system=system)
            _save_graph_v6(profile.profile_id, session_id, graph, system=system)

        # End-of-all-sessions evaluators. Wait for any in-flight judges
        # first so the report has the complete set of session-level
        # files. The all-sessions Mind-3 path is no longer called —
        # esc_judge replaces it with per-session calls that don't
        # truncate.
        if pending_futures:
            _wait(pending_futures)
    _save_run_artifacts_v6(artifacts)
    return artifacts


# ---------------------------------------------------------------------------
# Session loop
# ---------------------------------------------------------------------------


def _run_session_v6(
    *,
    profile: ProfileSpec,
    sim_profile: SimulatorProfile,
    session_id: int,
    run_cfg: RunConfig,
    client: LLMClient,
    graph: ProblemGraphV6,
    system: str = SYSTEM_V6,
    turn_fn: Any = None,
    judge_pool: Optional["ThreadPoolExecutor"] = None,  # type: ignore[name-defined]
    pending_futures: Optional[list] = None,
) -> SessionArtifactsV6:
    """Run one session under `system` using `turn_fn` for the per-turn
    assistant call. The simulator stack is shared across all systems so
    the only thing that varies is what the assistant does with each
    user turn.
    """
    if turn_fn is None:
        turn_fn = v6_turn_fn
    art = SessionArtifactsV6(session_id=session_id)

    # Snapshot pre-session TTM stages for stage-transition reporting.
    pre_stages = {
        name: p.current_ttm_stage for name, p in graph.problems.items()
    }

    # 1. Generate the hidden per-session framing (simulator-only).
    sc_ctx = CallContext(
        profile_id=profile.profile_id, session_id=session_id, system=system,
        turn_id=0, call_role="session_context",
    )
    session_context = run_session_context(
        client=client, ctx=sc_ctx,
        profile=sim_profile,
    )
    art.session_context = session_context

    # 2. Per-turn loop.
    last_system_message: Optional[str] = None
    turns_this_session = run_cfg.turns_for_session(session_id)
    for turn_id in range(1, turns_this_session + 1):
        # Window the transcript for the per-turn prompts. Keep it
        # modest — mind1_v6 enforces J=20 internally, recent_turns is
        # intended to be a smaller window for the response prompt.
        recent_turns = art.transcript[-(run_cfg.last_n_turns * 2):]
        past_turns_for_mind1 = list(art.transcript)  # whole session so far

        # --- Mind-1 v6 user utterance + hidden reasoning sidecar ---
        m1_ctx = CallContext(
            profile_id=profile.profile_id, session_id=session_id, system=system,
            turn_id=turn_id, call_role="mind1_v6",
        )
        mind1_out = run_mind1_v6(
            client=client, ctx=m1_ctx,
            profile=sim_profile,
            session_context=session_context,
            past_turns=past_turns_for_mind1,
            last_system_message=last_system_message,
        )
        user_message = mind1_out["simulated_user_message"]
        art.transcript.append({
            "role": "user", "turn_id": turn_id, "text": user_message,
        })
        # Sidecar — turn_id, fallback flag, and the simulator's claim
        # of which problems this message touches (the only retained
        # bookkeeping field). Useful for simulator-vs-chatbot agreement
        # analysis without the full hidden_reasoning_summary payload.
        art.mind1_hidden.append({
            "turn_id": turn_id,
            "problems_referred": mind1_out.get("problems_referred", []),
            "_fallback_default": mind1_out.get("_fallback_default", False),
        })

        # --- per-turn pipeline (system-specific turn_fn) ---
        # v6 redesign (§9.2): no `prior_session_summary` carryforward.
        turn_result = turn_fn(
            client=client,
            profile_id=profile.profile_id,
            system=system,
            session_id=session_id, turn_id=turn_id,
            user_message=user_message,
            recent_turns=recent_turns,
            last_system_message=last_system_message,
            prior_session_summary=None,
            graph=graph,
            last_n_turns=run_cfg.last_n_turns,
            # Previous turn traces from THIS session — used by the response
            # prompt to pick a natural next MI move.
            previous_turn_traces=list(art.turn_traces),
        )
        art.turn_traces.append(turn_result)

        assistant_text = (turn_result.get("response") or {}).get("final_response", "")
        art.transcript.append({
            "role": "assistant", "turn_id": turn_id, "text": assistant_text,
        })
        last_system_message = assistant_text or None

    # 3 & 6. Session-end LLM calls — persona_update_v6 and miti_judge_v6
    # are independent (both read art.transcript, neither writes anything
    # the other reads), so we fire them in parallel via ThreadPoolExecutor.
    # On Lightning each takes ~6-15s; sequential = up to 30s/session,
    # parallel = max of the two ≈ 15s. Saves ~10s/session × 4 sessions
    # × 30 profiles = ~20 min over a full matrix.
    pu_ctx = CallContext(
        profile_id=profile.profile_id, session_id=session_id, system=system,
        turn_id=-1, call_role="persona_update_v6",
    )
    miti_ctx = CallContext(
        profile_id=profile.profile_id, session_id=session_id, system=system,
        turn_id=-1, call_role="miti_judge",
    )
    esc_ctx = CallContext(
        profile_id=profile.profile_id, session_id=session_id, system=system,
        turn_id=-1, call_role="esc_judge",
    )
    persona_before = _persona_to_dict(graph.persona)

    def _do_persona() -> list[dict]:
        try:
            out = run_persona_update_v6(
                client=client, ctx=pu_ctx,
                transcript=art.transcript,
                current_persona=persona_before,
            )
            return out.get("updates", [])
        except Exception as e:
            log.exception("persona_update_v6 failed for %s s%d: %s",
                          profile.profile_id, session_id, e)
            return []

    def _do_miti_and_save() -> dict:
        try:
            out = run_miti_judge(
                client=client, ctx=miti_ctx,
                transcript=art.transcript,
            )
        except Exception as e:
            log.exception("miti_judge_v6 failed for %s s%d: %s",
                          profile.profile_id, session_id, e)
            out = {"_error": str(e), "_fallback_default": True}
        _save_judge_file(profile.profile_id, system, session_id,
                         "miti_judge", out)
        # Back-fill the in-memory artifact too so downstream callers
        # (smoke tests, in-process aggregations) see the result after
        # `_wait(pending_futures)` resolves at end of profile.
        art.miti_judge = out
        return out

    def _do_esc_and_save() -> dict:
        try:
            out = run_esc_judge(
                client=client, ctx=esc_ctx,
                transcript=art.transcript,
            )
        except Exception as e:
            log.exception("esc_judge_v6 failed for %s s%d: %s",
                          profile.profile_id, session_id, e)
            out = {"_error": str(e), "_fallback_default": True}
        _save_judge_file(profile.profile_id, system, session_id,
                         "esc_judge", out)
        art.esc_judge = out
        return out

    if judge_pool is not None:
        # Non-blocking: judges run in parallel with the next session's
        # first turns. Each callback writes its own file on completion.
        # Persona update still blocks (next session needs the updated
        # persona on the graph).
        f_miti = judge_pool.submit(_do_miti_and_save)
        f_esc = judge_pool.submit(_do_esc_and_save)
        pending_futures.extend([f_miti, f_esc])
        updates = _do_persona()
        # art.miti_judge / art.esc_judge stay None — they get written to
        # disk via the callbacks above and read back at report time.
    else:
        # Legacy synchronous path (kept for tests / single-session calls).
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=3) as ex:
            f_persona = ex.submit(_do_persona)
            f_miti = ex.submit(_do_miti_and_save)
            f_esc = ex.submit(_do_esc_and_save)
            updates = f_persona.result()
            art.miti_judge = f_miti.result()
            art.esc_judge = f_esc.result()

    art.persona_updates = updates

    # Apply persona updates to the in-memory graph (must happen on the
    # main thread — graph object isn't synchronized).
    persona_after = apply_updates_to_persona(persona_before, updates)
    graph.persona = _persona_from_dict(persona_after)

    # 4. Stage transitions across the session.
    post_stages = {
        name: p.current_ttm_stage for name, p in graph.problems.items()
    }
    art.stage_transitions = _compute_stage_transitions(pre_stages, post_stages)

    # 5. v6 redesign (§9.2): no session-summary call. The field stays
    # empty for backward-compatible artifact serialization.
    art.session_summary = ""

    return art


# ---------------------------------------------------------------------------
# Persona <-> dict helpers
# ---------------------------------------------------------------------------


def _persona_to_dict(p: PersonaState) -> dict:
    return {
        "demographics": p.demographics,
        "personality_traits": list(p.personality_traits),
        "core_values": list(p.core_values),
        "core_beliefs": list(p.core_beliefs),
        "support_system": p.support_system,
        "hobbies_interests": list(p.hobbies_interests),
        "communication_style": p.communication_style,
        "relevant_history": p.relevant_history,
        "general_behavioral_traits": list(p.general_behavioral_traits),
    }


def _persona_from_dict(d: dict) -> PersonaState:
    return PersonaState(
        demographics=d.get("demographics"),
        personality_traits=list(d.get("personality_traits") or []),
        core_values=list(d.get("core_values") or []),
        core_beliefs=list(d.get("core_beliefs") or []),
        support_system=d.get("support_system"),
        hobbies_interests=list(d.get("hobbies_interests") or []),
        communication_style=d.get("communication_style"),
        relevant_history=d.get("relevant_history"),
        general_behavioral_traits=list(d.get("general_behavioral_traits") or []),
    )


def _compute_stage_transitions(
    pre: dict[str, str], post: dict[str, str]
) -> list[dict]:
    out = []
    all_problems = set(pre) | set(post)
    for pname in sorted(all_problems):
        old = pre.get(pname, "(absent)")
        new = post.get(pname, "(absent)")
        if old != new:
            out.append({"problem": pname, "from": old, "to": new})
    return out


def _collect_active_problems(turn_traces: list[dict]) -> list[str]:
    seen: list[str] = []
    for tr in turn_traces:
        for p in (tr.get("trace") or {}).get("current_problems") or []:
            if p not in seen:
                seen.append(p)
    return seen


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _v6_transcript_dir(profile_id: str, system: str = SYSTEM_V6) -> Path:
    d = config.TRANSCRIPT_DIR / profile_id / system
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_judge_file(
    profile_id: str, system: str, session_id: int, prefix: str, payload: dict,
) -> None:
    """Write a single per-session judge artifact (`{prefix}_s{NN}.json`).

    Used as the on-completion callback for the non-blocking
    `miti_judge_v6` / `esc_judge_v6` futures so judges can run in
    parallel with the next session's first turns and persist their own
    output without going through the session-end save path.
    """
    d = _v6_transcript_dir(profile_id, system)
    fp = d / f"{prefix}_s{session_id:02d}.json"
    with fp.open("w") as f:
        json.dump(payload, f, indent=2, default=str)


def _save_session_artifacts_v6(
    profile_id: str, art: SessionArtifactsV6, system: str = SYSTEM_V6,
) -> None:
    d = _v6_transcript_dir(profile_id, system)

    # Main transcript + traces (does NOT include hidden mind1 reasoning).
    payload = {
        "session_id": art.session_id,
        "session_summary": art.session_summary,
        "stage_transitions": art.stage_transitions,
        "persona_updates": art.persona_updates,
        "transcript": art.transcript,
        "turn_traces": art.turn_traces,
    }
    with (d / f"session_{art.session_id:02d}.json").open("w") as f:
        json.dump(payload, f, indent=2, default=str)

    # Hidden reasoning sidecar — isolated so it can be deleted without
    # affecting the main transcript.
    with (d / f"mind1_reasoning_s{art.session_id:02d}.jsonl").open("w") as f:
        for entry in art.mind1_hidden:
            f.write(json.dumps(entry) + "\n")

    # Session context sidecar.
    with (d / f"session_context_s{art.session_id:02d}.json").open("w") as f:
        json.dump(art.session_context, f, indent=2, default=str)

    # MITI 4.2 session-level judge sidecar (§1.a).
    if art.miti_judge is not None:
        with (d / f"miti_judge_s{art.session_id:02d}.json").open("w") as f:
            json.dump(art.miti_judge, f, indent=2, default=str)

    # ESConv 6-dim session-level judge sidecar (§1.c). Replaces the old
    # all-sessions Mind-3 design which truncated and fell back to 3.0.
    if art.esc_judge is not None:
        with (d / f"esc_judge_s{art.session_id:02d}.json").open("w") as f:
            json.dump(art.esc_judge, f, indent=2, default=str)


def _save_graph_v6(
    profile_id: str, session_id: int, graph: ProblemGraphV6,
    system: str = SYSTEM_V6,
) -> None:
    # Per-system subdirectory so v1/v3 graph snapshots don't clobber v6's.
    d = config.GRAPH_V6_DIR / system
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{profile_id}_after_s{session_id:02d}.json"
    graph.save(path)


def _save_run_artifacts_v6(artifacts: RunArtifactsV6) -> None:
    d = _v6_transcript_dir(artifacts.profile_id, artifacts.system)
    payload = {
        "profile_id": artifacts.profile_id,
        "system": artifacts.system,
        "session_ids": [s.session_id for s in artifacts.sessions],
        "mind2_out": artifacts.mind2_out,
        "mind3_out": artifacts.mind3_out,
    }
    with (d / "run_artifacts.json").open("w") as f:
        json.dump(payload, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# End-of-all-sessions evaluation — REMOVED.
#
# Old design: ran Mind-2 (silver TTM labels) + Mind-3 (all-sessions ESC)
# at end of run. Mind-2 was dropped in the v6 redesign (no consumer
# after E3a/E3b/E4 were retired). Mind-3 was dropped after the per-
# session esc_judge replaced it (the all-sessions design truncated and
# fell back to constant 3.0 on every call).
#
# Per-session MITI + ESC judges now fire from inside _run_session_v6
# via the judge_pool, so end-of-run evaluators are no longer needed.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Re-exports for run.py
# ---------------------------------------------------------------------------


__all__ = [
    "SYSTEM_V6",
    "RunArtifactsV6", "SessionArtifactsV6",
    "run_profile_v6",
    "load_profile", "list_profiles",
]
