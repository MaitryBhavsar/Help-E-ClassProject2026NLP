"""Slim UI shim — only the helpers `ui/server.py` still needs.

The original v1–v5 orchestrator (run_profile, _default_turn_fn,
_run_session, _run_end_of_all, etc.) lives at
`_archive/legacy_session_driver.py` for reference. The active matrix
runs through `session_driver_v6.run_profile_v6`.

This shim exists so the demo UI (which still seeds a legacy
`ProblemGraph` from a profile YAML and picks a session-arc cue) keeps
working without forcing a full UI rewrite. Once the UI is migrated to
v6-only, this whole file can be deleted.

Re-exports from `profile_spec` are kept for back-compat with any
external script that imports `ProfileSpec` / `RunConfig` from
`session_driver` directly.
"""
from __future__ import annotations

from . import config
from .graph import PersonaNode, ProblemGraph
from .profile_spec import (
    ProfileSpec,
    RunConfig,
    list_profiles,
    load_profile,
)


<<<<<<< HEAD
__all__ = [
    # Re-exports for back-compat.
    "ProfileSpec", "RunConfig", "list_profiles", "load_profile",
    # UI helpers.
    "_load_or_seed_graph", "_pick_arc_cue",
]
=======
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------


@dataclass
class ProfileSpec:
    """Mirror of the YAML files produced by §8.1 profile seeding."""

    profile_id: str
    source_emocare_id: Optional[str]
    seed_situation_paragraph: str
    primary_problem: str
    session_arc: list[str]
    persona_draft: dict
    initial_graph: Optional[dict] = None  # pre-seeded graph snapshot
    blurb: str = ""

    @classmethod
    def from_yaml(cls, path: Path) -> "ProfileSpec":
        with path.open() as f:
            d = yaml.safe_load(f)
        arc = d.get("session_arc") or []
        if isinstance(arc, list) and arc and isinstance(arc[0], dict):
            # YAML schema allows list-of-dicts like ``- session 1: ...``.
            flat = []
            for item in arc:
                flat.extend(f"{k}: {v}" for k, v in item.items())
            arc = flat
        return cls(
            profile_id=d["profile_id"],
            source_emocare_id=d.get("source_emocare_id"),
            seed_situation_paragraph=d["seed_situation_paragraph"],
            primary_problem=d["primary_problem"],
            session_arc=arc,
            persona_draft=d.get("persona_draft", {}),
            initial_graph=d.get("initial_graph"),
            blurb=d.get("blurb", d["seed_situation_paragraph"]),
        )

    def to_mind1_persona(self) -> Mind1Persona:
        pd = self.persona_draft or {}
        return Mind1Persona(
            profile_id=self.profile_id,
            seed_situation_paragraph=self.seed_situation_paragraph,
            primary_problem=self.primary_problem,
            personality_traits=list(pd.get("personality_traits", [])),
            communication_style=pd.get("communication_style", ""),
            relevant_history=pd.get("relevant_history", ""),
        )


# ---------------------------------------------------------------------------
# Run config + artifacts
# ---------------------------------------------------------------------------


@dataclass
class RunConfig:
    sessions_per_profile: int = 4
    turns_per_session: int = 10
    turns_by_session: Optional[list[int]] = None
    last_n_turns: int = config.LAST_N_TURNS
    run_judge_inline: bool = False  # judge usually runs post-hoc

    def __post_init__(self) -> None:
        if self.turns_by_session is None:
            return
        if len(self.turns_by_session) != self.sessions_per_profile:
            raise ValueError(
                "turns_by_session length must equal sessions_per_profile "
                f"({len(self.turns_by_session)} != {self.sessions_per_profile})"
            )
        for idx, turns in enumerate(self.turns_by_session, start=1):
            if not isinstance(turns, int) or turns <= 0:
                raise ValueError(
                    "turns_by_session values must be positive integers; "
                    f"session {idx} got {turns!r}"
                )

    def turns_for_session(self, session_id: int) -> int:
        if self.turns_by_session is None:
            return self.turns_per_session
        return self.turns_by_session[session_id - 1]


@dataclass
class SessionArtifacts:
    session_id: int
    transcript: list[dict] = field(default_factory=list)
    traces: list[TurnTrace] = field(default_factory=list)
    session_summary: str = ""
    stage_transitions: list[dict] = field(default_factory=list)
    drift: Optional[dict] = None
    merged_outputs_by_turn: dict[int, dict] = field(default_factory=dict)
    bundle_snapshots_by_turn: dict[int, dict] = field(default_factory=dict)


@dataclass
class RunArtifacts:
    profile_id: str
    system: str
    sessions: list[SessionArtifacts] = field(default_factory=list)
    mind2_out: Optional[dict] = None
    mind3_out: Optional[dict] = None
    judge_outputs: list[dict] = field(default_factory=list)

    def transcripts_for_minds(self) -> list[dict]:
        return [
            {"session_id": s.session_id, "turns": s.transcript}
            for s in self.sessions
        ]

    def all_expected_turn_ids(self) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        for s in self.sessions:
            user_turn_ids = [
                t["turn_id"] for t in s.transcript if t["role"] == "user"
            ]
            for tid in user_turn_ids:
                out.append((s.session_id, tid))
        return out


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


TurnFn = Callable[..., dict]
"""Signature for the per-turn "generate response" function — lets v1/v2/v3
baselines plug in a simpler flow (no graph, no merged call). For v4/v5 we
use the default ``_default_turn_fn`` below.
"""


def _default_turn_fn(
    *,
    client: LLMClient,
    ctx_profile: str,
    system: str,
    session_id: int,
    turn_id: int,
    user_message: str,
    recent_turns: list[dict],
    prior_session_summary: Optional[str],
    previous_main_problem: Optional[str],
    graph: ProblemGraph,
    last_n_turns: int,
) -> dict:
    """v4/v5 per-turn response pipeline."""
    trace = apply_turn(
        graph=graph, client=client,
        profile_id=ctx_profile, system=system,
        session_id=session_id, turn_id=turn_id,
        user_message=user_message,
        recent_turns=recent_turns,
        previous_main_problem=previous_main_problem,
        last_n_turns=last_n_turns,
    )
    if trace.main_problem is None and not trace.carried_forward_main:
        # still nothing — emit a soft reflection
        return {
            "trace": trace,
            "bundle": None,
            "candidates": [],
            "merged": {
                "system_intent": "stay present with the user",
                "instruction": "Reflect softly; ask nothing.",
                "chosen_techniques": ["T1"],
                "response": "I'm here. Take your time.",
                "_fallback_default": True,
            },
        }

    bundle = build_bundle(
        graph=graph,
        main_problem_name=trace.main_problem,
        recent_active_problem_names=_recent_actives_for_bundle(
            session_id, turn_id, window=last_n_turns,
            session_traces=graph._session_traces  # type: ignore[attr-defined]
            if hasattr(graph, "_session_traces") else [],
        ),
        prior_session_summary=prior_session_summary,
        current_user_message=user_message,
        recent_turns=recent_turns,
    )
    candidates = select_candidates(
        bundle=bundle,
        user_intent=trace.extraction["user_intent"],
        current_session=session_id,
        current_turn=turn_id,
        last_n=last_n_turns,
    )

    merged_ctx = CallContext(
        profile_id=ctx_profile, session_id=session_id, system=system,
        turn_id=turn_id, call_role="merged_response",
    )
    merged = run_merged(
        client=client, ctx=merged_ctx,
        bundle=bundle,
        candidate_techniques=candidates,
        user_intent=trace.extraction["user_intent"],
    )
    return {
        "trace": trace,
        "bundle": bundle.to_json_dict(),
        "candidates": candidates,
        "merged": merged,
    }


def _recent_actives_for_bundle(
    session_id: int, turn_id: int, *, window: int, session_traces: list[TurnTrace],
) -> list[str]:
    min_turn = max(0, turn_id - window + 1)
    seen: list[str] = []
    for t in session_traces:
        if t.session_id != session_id or t.turn_id < min_turn:
            continue
        for p in t.active_problems:
            if p not in seen:
                seen.append(p)
    return seen


def run_profile(
    *,
    profile: ProfileSpec,
    system: str,
    run_cfg: RunConfig = RunConfig(),
    client: Optional[LLMClient] = None,
    turn_fn: Optional[TurnFn] = None,
) -> RunArtifacts:
    """Run ``run_cfg.sessions_per_profile`` sessions for this profile."""
    client = client or get_client()
    turn_fn = turn_fn or _default_turn_fn
    artifacts = RunArtifacts(profile_id=profile.profile_id, system=system)

    # Load or seed graph for this profile.
    graph = _load_or_seed_graph(profile)

    prior_session_summary: Optional[str] = None
    for session_id in range(1, run_cfg.sessions_per_profile + 1):
        session_art = _run_session(
            profile=profile,
            system=system,
            session_id=session_id,
            run_cfg=run_cfg,
            client=client,
            graph=graph,
            turn_fn=turn_fn,
            prior_session_summary=prior_session_summary,
        )
        artifacts.sessions.append(session_art)
        prior_session_summary = session_art.session_summary
        _save_transcript(profile.profile_id, system, session_art)
        _save_graph(profile.profile_id, system, session_id, graph)

    # end-of-all-sessions minds
    _run_end_of_all(
        client=client, profile=profile, system=system, artifacts=artifacts,
    )
    if run_cfg.run_judge_inline:
        _run_judge_all(client=client, profile=profile, system=system,
                       artifacts=artifacts)
    _save_run_artifacts(artifacts)
    return artifacts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
>>>>>>> 657e5d5 (Add CAMI integration + v6 session updates + LLM routing support)


def _load_or_seed_graph(profile: ProfileSpec) -> ProblemGraph:
    """Load a previously-saved legacy graph or seed a fresh one from
    the profile YAML's `persona_draft` + `primary_problem`.

    Used by `ui/server.py` for v1/v3/v4 demo runs that want a legacy
    graph snapshot. v6 builds its own `ProblemGraphV6` via the v6
    driver.
    """
    graph_path = config.GRAPH_DIR / f"{profile.profile_id}.json"
    if graph_path.exists():
        return ProblemGraph.load_json(str(graph_path))
    graph = ProblemGraph()
    if profile.initial_graph:
        graph = ProblemGraph.from_dict(profile.initial_graph)
    else:
        persona = PersonaNode()
        pd = profile.persona_draft or {}
        persona.personality_traits = list(pd.get("personality_traits", []))
        persona.communication_style = pd.get("communication_style")
        persona.relevant_history = pd.get("relevant_history")
        graph.set_persona(persona)
        # Seed the primary problem so the UI's retrieval helper has a
        # target on turn 0 of session 1 even before extraction runs.
        graph.set_cursor(0, 0)
        graph.get_or_create_problem(profile.primary_problem)
    return graph


<<<<<<< HEAD
=======
def _save_transcript(profile_id: str, system: str, art: SessionArtifacts) -> None:
    d = config.TRANSCRIPT_DIR / profile_id / system
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"session_{art.session_id:02d}.json"
    payload = {
        "session_id": art.session_id,
        "session_summary": art.session_summary,
        "stage_transitions": art.stage_transitions,
        "drift": art.drift,
        "transcript": art.transcript,
        "traces": [asdict(t) for t in art.traces],
        "merged_outputs_by_turn": art.merged_outputs_by_turn,
    }
    with path.open("w") as f:
        json.dump(payload, f, indent=2, default=str)


def _save_graph(
    profile_id: str, system: str, session_id: int, graph: ProblemGraph,
) -> None:
    d = config.GRAPH_DIR / system
    d.mkdir(parents=True, exist_ok=True)
    graph.save_json(str(d / f"{profile_id}_after_s{session_id:02d}.json"))


def _save_run_artifacts(art: "RunArtifacts") -> None:
    """Persist end-of-all-sessions outputs (Mind-2, Mind-3, judge)."""
    d = config.TRANSCRIPT_DIR / art.profile_id / art.system
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "profile_id": art.profile_id,
        "system": art.system,
        "session_ids": [s.session_id for s in art.sessions],
        "mind2_out": art.mind2_out,
        "mind3_out": art.mind3_out,
        "judge_outputs": art.judge_outputs,
    }
    with (d / "run_artifacts.json").open("w") as f:
        json.dump(payload, f, indent=2, default=str)


def _run_session(
    *,
    profile: ProfileSpec,
    system: str,
    session_id: int,
    run_cfg: RunConfig,
    client: LLMClient,
    graph: ProblemGraph,
    turn_fn: TurnFn,
    prior_session_summary: Optional[str],
) -> SessionArtifacts:
    art = SessionArtifacts(session_id=session_id)

    # Pre-session TTM stage snapshot (for session summary transitions).
    pre_stages = {
        p.problem_name: p.current_ttm_stage for p in graph._problems.values()
    }

    # Snapshot traces for bundle helper.
    graph._session_traces = art.traces  # type: ignore[attr-defined]

    session_arc_cue = _pick_arc_cue(profile, session_id)
    previous_main_problem = profile.primary_problem
    mind1_persona = profile.to_mind1_persona()

    turns_this_session = run_cfg.turns_for_session(session_id)
    for turn_id in range(1, turns_this_session + 1):
        recent_turns = art.transcript[-(run_cfg.last_n_turns * 2):]
        # -- Mind-1 user utterance ------------------------------------------
        mind1_ctx = CallContext(
            profile_id=profile.profile_id, session_id=session_id, system=system,
            turn_id=turn_id, call_role="mind1",
        )
        mind1_out = run_mind1(
            client=client, ctx=mind1_ctx,
            persona=mind1_persona,
            session_arc_cue=session_arc_cue,
            prior_session_summary=prior_session_summary,
            recent_turns=recent_turns,
        )
        user_message = mind1_out["utterance"]
        art.transcript.append({
            "role": "user", "turn_id": turn_id, "text": user_message,
        })

        # -- per-turn graph + response --------------------------------------
        turn_result = turn_fn(
            client=client,
            ctx_profile=profile.profile_id,
            system=system,
            session_id=session_id,
            turn_id=turn_id,
            user_message=user_message,
            recent_turns=recent_turns,
            prior_session_summary=prior_session_summary,
            previous_main_problem=previous_main_problem,
            graph=graph,
            last_n_turns=run_cfg.last_n_turns,
        )
        trace: TurnTrace = turn_result["trace"]
        art.traces.append(trace)
        if trace.main_problem:
            previous_main_problem = trace.main_problem

        art.bundle_snapshots_by_turn[turn_id] = turn_result.get("bundle")
        art.merged_outputs_by_turn[turn_id] = turn_result.get("merged", {})

        # -- assistant response --------------------------------------------
        assistant_text = turn_result["merged"].get("response", "")
        art.transcript.append({
            "role": "assistant", "turn_id": turn_id, "text": assistant_text,
        })

    # Session-end processing
    finalize_out = finalize_session(
        graph=graph, client=client,
        profile_id=profile.profile_id, system=system,
        session_id=session_id,
        transcript=art.transcript,
        traces=art.traces,
        pre_session_stages=pre_stages,
        prior_session_summary=prior_session_summary,
    )
    art.session_summary = finalize_out["session_summary"]
    art.stage_transitions = finalize_out["stage_transitions"]

    # Optional persona-drift check at session end.
    user_utterances = [t["text"] for t in art.transcript if t["role"] == "user"]
    drift_ctx = CallContext(
        profile_id=profile.profile_id, session_id=session_id, system=system,
        turn_id=-1, call_role="mind1",
    )
    art.drift = run_drift_check(
        client=client, ctx=drift_ctx,
        persona=mind1_persona, user_utterances=user_utterances,
    )

    return art


>>>>>>> 657e5d5 (Add CAMI integration + v6 session updates + LLM routing support)
def _pick_arc_cue(profile: ProfileSpec, session_id: int) -> str:
    """Return the session-arc cue string for `session_id` from the
    profile YAML's `session_arc`. Empty string if not defined.
    """
    arc = profile.session_arc or []
    if not arc:
        return ""
    idx = min(session_id - 1, len(arc) - 1)
    return arc[idx]
