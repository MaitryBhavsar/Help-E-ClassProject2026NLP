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


__all__ = [
    # Re-exports for back-compat.
    "ProfileSpec", "RunConfig", "list_profiles", "load_profile",
    # UI helpers.
    "_load_or_seed_graph", "_pick_arc_cue",
]


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


def _pick_arc_cue(profile: ProfileSpec, session_id: int) -> str:
    """Return the session-arc cue string for `session_id` from the
    profile YAML's `session_arc`. Empty string if not defined.
    """
    arc = profile.session_arc or []
    if not arc:
        return ""
    idx = min(session_id - 1, len(arc) - 1)
    return arc[idx]
