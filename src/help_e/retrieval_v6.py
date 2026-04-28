"""§6.5 v6 — Retrieval bundle builder for the response prompt.

Pure Python, no LLM. Wraps `ProblemGraphV6.select_relevant_context` and
attaches the surrounding inputs (persona, recent turns, user message, last
system message) the response prompt needs.

Conceptual steps (§6.5 v6):
  A. Main problem view — inline level + non-level attributes, each with
     up to TOP_K recent evidence entries + the goal string.
  B. Top-S neighbors by current edge weight, each with its connection
     evidence and own attribute view.
  C. No "recent active" step — current-turn active problems are already
     represented in the graph; if they matter, they are neighbors. If they
     are not neighbors, the response prompt does not need them.

Output is `RetrievalBundleV6`, a dataclass mirrored into a JSON-safe dict
by `to_json_dict()` for logging and for the response prompt.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .config import TOP_S_NEIGHBORS
from .graph_v6 import ProblemGraphV6, ProblemNode


# Default number of most-recent evidence entries to surface per attribute.
DEFAULT_TOP_K_EVIDENCE: int = 3


# ---------------------------------------------------------------------------
# Bundle dataclass
# ---------------------------------------------------------------------------


@dataclass
class RetrievalBundleV6:
    persona: dict                         # PersonaState.asdict()
    prior_session_summary: Optional[str]
    current_user_message: str
    recent_turns: list[dict]              # [{role, turn_id, text}, ...]
    last_system_message: Optional[str]
    main_problem: Optional[dict]          # problem view dict, see below
    relevant_problems: list[dict] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "persona": self.persona,
            "prior_session_summary": self.prior_session_summary,
            "current_user_message": self.current_user_message,
            "recent_turns": self.recent_turns,
            "last_system_message": self.last_system_message,
            "main_problem": self.main_problem,
            "relevant_problems": self.relevant_problems,
        }


# ---------------------------------------------------------------------------
# Problem-view builder (per-problem block of attributes + recent evidence)
# ---------------------------------------------------------------------------


def _build_problem_view(
    prob: ProblemNode, *, top_k_evidence: int
) -> dict[str, Any]:
    """Render one ProblemNode into a dict consumed by
    `prompts.common_v6.format_problem_view_v6`.

    Only attributes that have evidence are included. For each level
    attribute, the current_level is surfaced alongside the most recent
    `top_k_evidence` entries. For each non-level attribute, only the recent
    entries are surfaced (no level).
    """
    level_attrs_out: dict[str, dict[str, Any]] = {}
    for attr_name, state in prob.level_attributes.items():
        stack = state.evidence_stack
        if not stack:
            continue
        recent = stack[-top_k_evidence:]
        level_attrs_out[attr_name] = {
            "current_level": state.current_level,
            "recent_evidence": [
                {
                    "session_id": e.session_id,
                    "turn_id": e.turn_id,
                    "inferred_information": e.inferred_information,
                    "concise_explanation": e.concise_explanation,
                    "supporting_utterance_span": e.supporting_utterance_span,
                    "confidence": e.confidence,
                }
                for e in recent
            ],
        }
    non_level_attrs_out: dict[str, dict[str, Any]] = {}
    for attr_name, state in prob.non_level_attributes.items():
        stack = state.evidence_stack
        if not stack:
            continue
        recent = stack[-top_k_evidence:]
        non_level_attrs_out[attr_name] = {
            "recent_evidence": [
                {
                    "session_id": e.session_id,
                    "turn_id": e.turn_id,
                    "inferred_information": e.inferred_information,
                    "concise_explanation": e.concise_explanation,
                    "supporting_utterance_span": e.supporting_utterance_span,
                    "confidence": e.confidence,
                }
                for e in recent
            ],
        }
    return {
        "problem_name": prob.problem_name,
        "current_ttm_stage": prob.current_ttm_stage,
        "goal": prob.goal,
        "first_mentioned": list(prob.first_mentioned),
        "last_mentioned": list(prob.last_mentioned),
        "level_attributes": level_attrs_out,
        "non_level_attributes": non_level_attrs_out,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def build_bundle_v6(
    *,
    graph: ProblemGraphV6,
    main_problem_name: Optional[str],
    current_user_message: str,
    recent_turns: list[dict],
    last_system_message: Optional[str],
    prior_session_summary: Optional[str],
    top_s: int = TOP_S_NEIGHBORS,
    top_k_evidence: int = DEFAULT_TOP_K_EVIDENCE,
) -> RetrievalBundleV6:
    """Assemble the bundle the response prompt consumes.

    Cold-start (no main problem or main not in graph): `main_problem` is
    None and `relevant_problems` is empty. The prompt must handle this.
    """
    persona_dict = asdict(graph.persona)

    main_view: Optional[dict] = None
    relevant_views: list[dict] = []

    if main_problem_name and main_problem_name in graph.problems:
        main_view = _build_problem_view(
            graph.problems[main_problem_name],
            top_k_evidence=top_k_evidence,
        )

        # Neighbor selection via the graph's own top-S helper.
        ctx = graph.select_relevant_context(
            main_problem_name, top_s=top_s,
            recent_connection_entries=top_k_evidence,
        )
        for nb in ctx["relevant_problems"]:
            other_name = nb["problem_name"]
            if other_name not in graph.problems:
                continue
            nb_view = _build_problem_view(
                graph.problems[other_name],
                top_k_evidence=top_k_evidence,
            )
            nb_view["edge_weight_to_main_problem"] = nb["edge_weight_to_main_problem"]
            nb_view["cooccurrence_count"] = nb["cooccurrence_count"]
            nb_view["connection_evidence"] = nb["connection_evidence"]
            relevant_views.append(nb_view)

    return RetrievalBundleV6(
        persona=persona_dict,
        prior_session_summary=prior_session_summary,
        current_user_message=current_user_message,
        recent_turns=list(recent_turns),
        last_system_message=last_system_message,
        main_problem=main_view,
        relevant_problems=relevant_views,
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _self_test() -> None:
    from .graph_v6 import (
        AttributeConnectionEntry,
        AttributeEvidenceEntry,
        CooccurrenceEntry,
        ProblemGraphV6,
        global_turn_idx,
    )

    # 1. Cold start bundle.
    g_cold = ProblemGraphV6(profile_id="T")
    b = build_bundle_v6(
        graph=g_cold, main_problem_name=None,
        current_user_message="hi",
        recent_turns=[],
        last_system_message=None,
        prior_session_summary=None,
    )
    assert b.main_problem is None
    assert b.relevant_problems == []
    assert b.persona.get("personality_traits") == []  # empty default
    assert b.current_user_message == "hi"

    # 2. Build a populated graph: main + 1 strong neighbor + 1 weak.
    g = ProblemGraphV6(profile_id="T")
    g.get_or_create_problem("academic_pressure", first_mentioned=(1, 1))
    g.get_or_create_problem("sleep_problems", first_mentioned=(1, 1))
    g.get_or_create_problem("general_anxiety", first_mentioned=(1, 3))

    for i, (info, turn) in enumerate([
        ("workload unsustainable", 1),
        ("finals loom", 2),
        ("up until 3am again", 3),
    ]):
        g.append_evidence(
            problem_name="academic_pressure",
            attr_name="perceived_severity",
            entry=AttributeEvidenceEntry(
                session_id=1, turn_id=turn,
                inferred_information=info,
                concise_explanation="x",
                supporting_utterance_span=None,
                confidence="high",
            ),
        )
    g.set_level("academic_pressure", "perceived_severity", "high")

    g.append_evidence(
        problem_name="academic_pressure", attr_name="goal",
        entry=AttributeEvidenceEntry(
            session_id=1, turn_id=2,
            inferred_information="get through finals without a crash",
            concise_explanation="stated goal",
            supporting_utterance_span=None, confidence="medium",
        ),
    )
    g.set_ttm_stage("academic_pressure", "contemplation")

    g.append_evidence(
        problem_name="sleep_problems", attr_name="triggers",
        entry=AttributeEvidenceEntry(
            session_id=1, turn_id=1,
            inferred_information="late cramming prevents sleep onset",
            concise_explanation="cited", supporting_utterance_span=None,
            confidence="high",
        ),
    )

    # Strong edge (academic_pressure <-> sleep_problems): cooc + attr conn
    g.append_cooccurrence(
        "academic_pressure", "sleep_problems",
        CooccurrenceEntry(
            session_id=1, turn_id=1,
            concise_explanation="both discussed", supporting_utterance_span=None,
        ),
    )
    g.append_attribute_connection(
        "academic_pressure", "sleep_problems",
        AttributeConnectionEntry(
            session_id=1, turn_id=1,
            attribute_1="triggers", attribute_2="triggers",
            relation_type="shared_trigger",
            connection_explanation="cramming drives both",
            supporting_utterance_span="all-nighters and I can't sleep",
            confidence="high",
        ),
    )
    # Weak edge: cooc only
    g.append_cooccurrence(
        "academic_pressure", "general_anxiety",
        CooccurrenceEntry(
            session_id=1, turn_id=3,
            concise_explanation="brief mention", supporting_utterance_span=None,
        ),
    )
    g.recompute_all_edge_weights(global_turn_idx(1, 3))

    b = build_bundle_v6(
        graph=g, main_problem_name="academic_pressure",
        current_user_message="I can't keep going like this",
        recent_turns=[
            {"role": "user", "turn_id": 2, "text": "finals next week"},
            {"role": "assistant", "turn_id": 2, "text": "that sounds heavy"},
        ],
        last_system_message="that sounds heavy",
        prior_session_summary=None,
        top_s=2,
        top_k_evidence=2,
    )

    # Main problem view: correct name, stage, goal, attrs present.
    assert b.main_problem is not None
    assert b.main_problem["problem_name"] == "academic_pressure"
    assert b.main_problem["current_ttm_stage"] == "contemplation"
    assert b.main_problem["goal"] == "get through finals without a crash"
    assert "perceived_severity" in b.main_problem["level_attributes"]
    assert b.main_problem["level_attributes"]["perceived_severity"]["current_level"] == "high"
    # Top-K=2 enforced on the 3-entry stack:
    assert len(b.main_problem["level_attributes"]["perceived_severity"]["recent_evidence"]) == 2
    assert "goal" in b.main_problem["non_level_attributes"]

    # Relevant problems: 2, strongest first (sleep_problems).
    assert len(b.relevant_problems) == 2
    assert b.relevant_problems[0]["problem_name"] == "sleep_problems"
    assert b.relevant_problems[1]["problem_name"] == "general_anxiety"
    assert b.relevant_problems[0]["edge_weight_to_main_problem"] \
        > b.relevant_problems[1]["edge_weight_to_main_problem"]
    # Strong edge carries connection evidence; weak edge does not.
    assert b.relevant_problems[0]["connection_evidence"]
    assert not b.relevant_problems[1]["connection_evidence"]

    # top_s=1 filters to 1 neighbor.
    b1 = build_bundle_v6(
        graph=g, main_problem_name="academic_pressure",
        current_user_message="x", recent_turns=[],
        last_system_message=None, prior_session_summary=None, top_s=1,
    )
    assert len(b1.relevant_problems) == 1

    # JSON dict round-trip is sane.
    d = b.to_json_dict()
    assert d["main_problem"]["problem_name"] == "academic_pressure"
    assert len(d["relevant_problems"]) == 2

    print("retrieval_v6 self-test PASSED")


if __name__ == "__main__":
    _self_test()
