"""v6 problem-centric graph with typed problem-problem edges.

Changes from v1–v5 (`graph.py`):
  - Attributes live INSIDE problem nodes; there is no shared AttributeNode
    pool.
  - Edges are ONLY between problems, with TWO separate evidence stacks:
    `cooccurrence_entries` (weak, binary per turn) and
    `attribute_connection_entries` (typed, stronger, confidence-weighted).
  - Edge weight = EDGE_WEIGHT_ALPHA_M * norm_M + EDGE_WEIGHT_ALPHA_A * norm_A
    with recency decay and per-entry confidence weighting (see
    `recompute_all_edge_weights`).
  - PersonaState starts empty. The main pipeline infers all persona fields
    from inference at session end; the profile YAML is simulator-only.

Cold-start invariant: a ProblemGraphV6 constructed with only `profile_id`
has no problems, no edges, and an empty persona. Every downstream consumer
must handle this state without crashing.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import (
    CONFIDENCE_WEIGHT,
    EDGE_WEIGHT_ALPHA_A,
    EDGE_WEIGHT_ALPHA_M,
    LEVEL_ATTR_TYPES,
    LEVELS_V6,
    NON_LEVEL_ATTR_TYPES,
    PROBLEM_VOCAB,
    RECENCY_HALF_LIFE_TURNS,
    RELATION_TYPES,
    SESSION_TURN_STRIDE,
    TOP_S_NEIGHBORS,
    TTM_STAGES_V6,
)


# ---------------------------------------------------------------------------
# Evidence entry types (append-only)
# ---------------------------------------------------------------------------


@dataclass
class AttributeEvidenceEntry:
    session_id: int
    turn_id: int
    inferred_information: str
    concise_explanation: str
    supporting_utterance_span: Optional[str]
    # `confidence` was removed from the inference output schema (not
    # consumed downstream). Default to "high" for back-compat with old
    # transcript files / loaders that still expect the field.
    confidence: str = "high"

    def global_idx(self) -> int:
        return global_turn_idx(self.session_id, self.turn_id)


@dataclass
class CooccurrenceEntry:
    session_id: int
    turn_id: int
    concise_explanation: str
    supporting_utterance_span: Optional[str]

    def global_idx(self) -> int:
        return global_turn_idx(self.session_id, self.turn_id)


@dataclass
class AttributeConnectionEntry:
    session_id: int
    turn_id: int
    attribute_1: str
    attribute_2: str
    relation_type: str  # one of RELATION_TYPES
    connection_explanation: str
    supporting_utterance_span: Optional[str]
    confidence: str

    def global_idx(self) -> int:
        return global_turn_idx(self.session_id, self.turn_id)


# ---------------------------------------------------------------------------
# Per-problem attribute state
# ---------------------------------------------------------------------------


@dataclass
class LevelAttributeState:
    current_level: str = "unknown"  # one of LEVELS_V6
    evidence_stack: list[AttributeEvidenceEntry] = field(default_factory=list)


@dataclass
class NonLevelAttributeState:
    evidence_stack: list[AttributeEvidenceEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Problem node
# ---------------------------------------------------------------------------


@dataclass
class ProblemNode:
    problem_name: str
    first_mentioned: tuple[int, int]
    last_mentioned: tuple[int, int]
    current_ttm_stage: str = "precontemplation"  # one of TTM_STAGES_V6
    goal: Optional[str] = None  # latest inferred goal; also mirrored on the goal evidence stack
    level_attributes: dict[str, LevelAttributeState] = field(default_factory=dict)
    non_level_attributes: dict[str, NonLevelAttributeState] = field(default_factory=dict)

    def has_any_evidence(self) -> bool:
        return bool(self.level_attributes) or bool(self.non_level_attributes)


# ---------------------------------------------------------------------------
# Problem-problem edge (canonicalized alphabetical pair)
# ---------------------------------------------------------------------------


@dataclass
class ProblemEdge:
    problem_1: str  # alphabetically first
    problem_2: str  # alphabetically second
    cooccurrence_entries: list[CooccurrenceEntry] = field(default_factory=list)
    attribute_connection_entries: list[AttributeConnectionEntry] = field(default_factory=list)
    weight: float = 0.0

    @staticmethod
    def canonical_pair(a: str, b: str) -> tuple[str, str]:
        return (a, b) if a < b else (b, a)

    def key(self) -> tuple[str, str]:
        return (self.problem_1, self.problem_2)

    def other(self, name: str) -> str:
        if name == self.problem_1:
            return self.problem_2
        if name == self.problem_2:
            return self.problem_1
        raise ValueError(f"{name!r} is not an endpoint of this edge")


# ---------------------------------------------------------------------------
# Persona state (empty on first session; inferred session-end)
# ---------------------------------------------------------------------------


@dataclass
class PersonaState:
    demographics: Optional[str] = None
    personality_traits: list[str] = field(default_factory=list)
    core_values: list[str] = field(default_factory=list)
    core_beliefs: list[str] = field(default_factory=list)
    support_system: Optional[str] = None
    hobbies_interests: list[str] = field(default_factory=list)
    communication_style: Optional[str] = None
    relevant_history: Optional[str] = None
    general_behavioral_traits: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any([
            self.demographics, self.personality_traits, self.core_values,
            self.core_beliefs, self.support_system, self.hobbies_interests,
            self.communication_style, self.relevant_history,
            self.general_behavioral_traits,
        ])


# ---------------------------------------------------------------------------
# The graph
# ---------------------------------------------------------------------------


@dataclass
class ProblemGraphV6:
    profile_id: str
    persona: PersonaState = field(default_factory=PersonaState)
    problems: dict[str, ProblemNode] = field(default_factory=dict)
    edges: dict[tuple[str, str], ProblemEdge] = field(default_factory=dict)

    # --- problem + edge creation -----------------------------------------

    def get_or_create_problem(
        self, name: str, *, first_mentioned: tuple[int, int]
    ) -> ProblemNode:
        if name not in PROBLEM_VOCAB:
            raise ValueError(f"unknown problem {name!r} (not in PROBLEM_VOCAB)")
        if name not in self.problems:
            self.problems[name] = ProblemNode(
                problem_name=name,
                first_mentioned=first_mentioned,
                last_mentioned=first_mentioned,
            )
        return self.problems[name]

    def get_or_create_edge(self, a: str, b: str) -> ProblemEdge:
        if a == b:
            raise ValueError(f"self-edge not allowed ({a})")
        if a not in self.problems or b not in self.problems:
            raise ValueError(
                f"both problems must exist before creating an edge: {a!r}, {b!r}"
            )
        key = ProblemEdge.canonical_pair(a, b)
        if key not in self.edges:
            self.edges[key] = ProblemEdge(problem_1=key[0], problem_2=key[1])
        return self.edges[key]

    # --- evidence writes --------------------------------------------------

    def append_evidence(
        self,
        *,
        problem_name: str,
        attr_name: str,
        entry: AttributeEvidenceEntry,
    ) -> None:
        if entry.confidence not in CONFIDENCE_WEIGHT:
            raise ValueError(f"invalid confidence {entry.confidence!r}")
        prob = self.problems.get(problem_name)
        if prob is None:
            raise ValueError(f"problem {problem_name!r} does not exist")
        if attr_name in LEVEL_ATTR_TYPES:
            state = prob.level_attributes.setdefault(attr_name, LevelAttributeState())
            state.evidence_stack.append(entry)
        elif attr_name in NON_LEVEL_ATTR_TYPES:
            state = prob.non_level_attributes.setdefault(attr_name, NonLevelAttributeState())
            state.evidence_stack.append(entry)
            if attr_name == "goal":
                # Goal is singular per problem; the stack keeps provenance.
                prob.goal = entry.inferred_information
        else:
            raise ValueError(
                f"unknown attribute {attr_name!r}; "
                f"expected one of LEVEL_ATTR_TYPES or NON_LEVEL_ATTR_TYPES"
            )
        prob.last_mentioned = (entry.session_id, entry.turn_id)

    def append_cooccurrence(
        self, problem_1: str, problem_2: str, entry: CooccurrenceEntry
    ) -> None:
        edge = self.get_or_create_edge(problem_1, problem_2)
        edge.cooccurrence_entries.append(entry)

    def append_attribute_connection(
        self, problem_1: str, problem_2: str, entry: AttributeConnectionEntry
    ) -> None:
        if entry.relation_type not in RELATION_TYPES:
            raise ValueError(f"unknown relation_type {entry.relation_type!r}")
        if entry.confidence not in CONFIDENCE_WEIGHT:
            raise ValueError(f"invalid confidence {entry.confidence!r}")
        edge = self.get_or_create_edge(problem_1, problem_2)
        edge.attribute_connection_entries.append(entry)

    # --- updates ----------------------------------------------------------

    def set_level(self, problem_name: str, attr_name: str, new_level: str) -> None:
        if new_level not in LEVELS_V6:
            raise ValueError(f"invalid level {new_level!r}")
        prob = self.problems[problem_name]
        if attr_name not in prob.level_attributes:
            raise ValueError(
                f"cannot set level on {problem_name}.{attr_name}: no evidence yet"
            )
        prob.level_attributes[attr_name].current_level = new_level

    def set_ttm_stage(self, problem_name: str, new_stage: str) -> None:
        if new_stage not in TTM_STAGES_V6:
            raise ValueError(f"invalid TTM stage {new_stage!r}")
        self.problems[problem_name].current_ttm_stage = new_stage

    # --- edge weight recompute -------------------------------------------

    def recompute_all_edge_weights(self, current_global_turn_idx: int) -> None:
        """Recompute every edge's weight:

            M_ij = Σ recency(e)                     for e in cooccurrence
            A_ij = Σ recency(e) * conf_weight(e)    for e in attr-connection
            weight = α_M * log(1+M)/log(1+max_M) + α_A * log(1+A)/log(1+max_A)

        Recency decays with RECENCY_HALF_LIFE_TURNS turn half-life. Normalizing
        against the graph-wide max keeps weight ∈ [0, 1].
        """
        if not self.edges:
            return
        m_scores: dict[tuple[str, str], float] = {}
        a_scores: dict[tuple[str, str], float] = {}
        for key, edge in self.edges.items():
            m_scores[key] = _recency_sum(
                (e.global_idx() for e in edge.cooccurrence_entries),
                current_global_turn_idx,
                confidences=None,
            )
            a_scores[key] = _recency_sum(
                (e.global_idx() for e in edge.attribute_connection_entries),
                current_global_turn_idx,
                confidences=[
                    CONFIDENCE_WEIGHT[e.confidence]
                    for e in edge.attribute_connection_entries
                ],
            )
        max_m = max(m_scores.values()) if m_scores else 0.0
        max_a = max(a_scores.values()) if a_scores else 0.0
        for key, edge in self.edges.items():
            norm_m = (
                math.log(1 + m_scores[key]) / math.log(1 + max_m)
                if max_m > 0 else 0.0
            )
            norm_a = (
                math.log(1 + a_scores[key]) / math.log(1 + max_a)
                if max_a > 0 else 0.0
            )
            edge.weight = EDGE_WEIGHT_ALPHA_M * norm_m + EDGE_WEIGHT_ALPHA_A * norm_a

    # --- relevant-context selection --------------------------------------

    def select_relevant_context(
        self,
        main_problem_name: Optional[str],
        *,
        top_s: int = TOP_S_NEIGHBORS,
        recent_connection_entries: int = 3,
    ) -> dict[str, Any]:
        """Pick the main problem + top-S neighbors by current edge weight.

        Returns a shape consumed directly by the v6 response prompt. If the
        graph is cold (no main problem or main is unknown), returns an empty
        container.
        """
        if main_problem_name is None or main_problem_name not in self.problems:
            return {"main_problem": None, "relevant_problems": []}

        main_edges = [
            e for e in self.edges.values()
            if main_problem_name in (e.problem_1, e.problem_2)
        ]
        main_edges.sort(key=lambda e: e.weight, reverse=True)

        relevant: list[dict[str, Any]] = []
        for edge in main_edges[:top_s]:
            other_name = edge.other(main_problem_name)
            relevant.append({
                "problem_name": other_name,
                "edge_weight_to_main_problem": edge.weight,
                "cooccurrence_count": len(edge.cooccurrence_entries),
                "connection_evidence": [
                    {
                        "relation_type": c.relation_type,
                        "attribute_1": c.attribute_1,
                        "attribute_2": c.attribute_2,
                        "explanation": c.connection_explanation,
                        "supporting_utterance_span": c.supporting_utterance_span,
                        "confidence": c.confidence,
                        "session_id": c.session_id,
                        "turn_id": c.turn_id,
                    }
                    for c in edge.attribute_connection_entries[-recent_connection_entries:]
                ],
            })
        return {"main_problem": main_problem_name, "relevant_problems": relevant}

    # --- persistence -----------------------------------------------------

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "persona": asdict(self.persona),
            "problems": {
                name: {
                    "problem_name": p.problem_name,
                    "first_mentioned": list(p.first_mentioned),
                    "last_mentioned": list(p.last_mentioned),
                    "current_ttm_stage": p.current_ttm_stage,
                    "goal": p.goal,
                    "level_attributes": {
                        k: {
                            "current_level": v.current_level,
                            "evidence_stack": [asdict(e) for e in v.evidence_stack],
                        } for k, v in p.level_attributes.items()
                    },
                    "non_level_attributes": {
                        k: {
                            "evidence_stack": [asdict(e) for e in v.evidence_stack],
                        } for k, v in p.non_level_attributes.items()
                    },
                } for name, p in self.problems.items()
            },
            "edges": [
                {
                    "problem_1": e.problem_1,
                    "problem_2": e.problem_2,
                    "cooccurrence_entries": [asdict(c) for c in e.cooccurrence_entries],
                    "attribute_connection_entries": [asdict(c) for c in e.attribute_connection_entries],
                    "weight": e.weight,
                } for e in self.edges.values()
            ],
        }

    @classmethod
    def from_json_dict(cls, d: dict[str, Any]) -> "ProblemGraphV6":
        g = cls(profile_id=d["profile_id"])
        pd = d.get("persona") or {}
        known = {
            k: pd.get(k) for k in (
                "demographics", "personality_traits", "core_values",
                "core_beliefs", "support_system", "hobbies_interests",
                "communication_style", "relevant_history",
                "general_behavioral_traits",
            )
        }
        for lf in (
            "personality_traits", "core_values", "core_beliefs",
            "hobbies_interests", "general_behavioral_traits",
        ):
            known[lf] = known[lf] or []
        g.persona = PersonaState(**known)

        for name, pd2 in d.get("problems", {}).items():
            p = ProblemNode(
                problem_name=pd2["problem_name"],
                first_mentioned=tuple(pd2["first_mentioned"]),
                last_mentioned=tuple(pd2["last_mentioned"]),
                current_ttm_stage=pd2.get("current_ttm_stage", "precontemplation"),
                goal=pd2.get("goal"),
            )
            for k, v in pd2.get("level_attributes", {}).items():
                p.level_attributes[k] = LevelAttributeState(
                    current_level=v.get("current_level", "unknown"),
                    evidence_stack=[
                        AttributeEvidenceEntry(**e)
                        for e in v.get("evidence_stack", [])
                    ],
                )
            for k, v in pd2.get("non_level_attributes", {}).items():
                p.non_level_attributes[k] = NonLevelAttributeState(
                    evidence_stack=[
                        AttributeEvidenceEntry(**e)
                        for e in v.get("evidence_stack", [])
                    ],
                )
            g.problems[name] = p

        for ed in d.get("edges", []):
            edge = ProblemEdge(
                problem_1=ed["problem_1"],
                problem_2=ed["problem_2"],
                cooccurrence_entries=[
                    CooccurrenceEntry(**c) for c in ed.get("cooccurrence_entries", [])
                ],
                attribute_connection_entries=[
                    AttributeConnectionEntry(**c)
                    for c in ed.get("attribute_connection_entries", [])
                ],
                weight=ed.get("weight", 0.0),
            )
            g.edges[edge.key()] = edge
        return g

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json_dict(), indent=2))

    @classmethod
    def load(cls, path: Path) -> "ProblemGraphV6":
        return cls.from_json_dict(json.loads(path.read_text()))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def global_turn_idx(session_id: int, turn_id: int) -> int:
    """Encode (session_id, turn_id) into a monotone integer for recency math.

    Relies on SESSION_TURN_STRIDE being larger than any plausible turn count
    per session (current runs use 10 turns).
    """
    return session_id * SESSION_TURN_STRIDE + turn_id


def _recency_weight(entry_global_idx: int, current_global_idx: int) -> float:
    age = max(0, current_global_idx - entry_global_idx)
    return 0.5 ** (age / RECENCY_HALF_LIFE_TURNS)


def _recency_sum(
    global_indices: Iterable[int],
    current_global_idx: int,
    confidences: Optional[list[float]],
) -> float:
    total = 0.0
    idxs = list(global_indices)
    if confidences is None:
        for idx in idxs:
            total += _recency_weight(idx, current_global_idx)
    else:
        for idx, conf in zip(idxs, confidences):
            total += _recency_weight(idx, current_global_idx) * conf
    return total


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _self_test() -> None:
    # Cold-start invariant.
    g_cold = ProblemGraphV6(profile_id="TEST_COLD")
    assert g_cold.persona.is_empty()
    assert not g_cold.problems and not g_cold.edges
    assert g_cold.select_relevant_context(None) == {
        "main_problem": None, "relevant_problems": []
    }
    assert g_cold.select_relevant_context("academic_pressure") == {
        "main_problem": None, "relevant_problems": []
    }, "unknown main should yield empty context"

    # Build a small graph.
    g = ProblemGraphV6(profile_id="TEST")
    g.get_or_create_problem("academic_pressure", first_mentioned=(1, 1))
    g.get_or_create_problem("sleep_problems", first_mentioned=(1, 1))

    # Level attribute + set_level.
    g.append_evidence(
        problem_name="academic_pressure",
        attr_name="perceived_severity",
        entry=AttributeEvidenceEntry(
            session_id=1, turn_id=1,
            inferred_information="workload feels unsustainable this week",
            concise_explanation="can't-keep-this-up language",
            supporting_utterance_span="I don't think I can keep this up",
            confidence="high",
        ),
    )
    g.set_level("academic_pressure", "perceived_severity", "high")
    assert g.problems["academic_pressure"].level_attributes["perceived_severity"].current_level == "high"

    # Goal (non-level, singular mirror on ProblemNode.goal).
    g.append_evidence(
        problem_name="academic_pressure",
        attr_name="goal",
        entry=AttributeEvidenceEntry(
            session_id=1, turn_id=2,
            inferred_information="finish finals without burning out",
            concise_explanation="stated goal",
            supporting_utterance_span=None,
            confidence="medium",
        ),
    )
    assert g.problems["academic_pressure"].goal == "finish finals without burning out"

    # Unknown attribute should reject.
    try:
        g.append_evidence(
            problem_name="academic_pressure",
            attr_name="not_a_real_attr",
            entry=AttributeEvidenceEntry(
                session_id=1, turn_id=2,
                inferred_information="x", concise_explanation="y",
                supporting_utterance_span=None, confidence="low",
            ),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError on unknown attribute")

    # Co-occurrence: order-independent (canonical pair).
    g.append_cooccurrence(
        "academic_pressure", "sleep_problems",
        CooccurrenceEntry(
            session_id=1, turn_id=1,
            concise_explanation="both discussed same turn",
            supporting_utterance_span="pulling all-nighters and can't sleep",
        ),
    )
    g.append_cooccurrence(
        "sleep_problems", "academic_pressure",  # reversed order
        CooccurrenceEntry(
            session_id=1, turn_id=2,
            concise_explanation="again mentioned together",
            supporting_utterance_span=None,
        ),
    )
    assert ("academic_pressure", "sleep_problems") in g.edges
    assert len(g.edges) == 1, f"canonical pair should dedupe, got {len(g.edges)}"

    # Attribute connection.
    g.append_attribute_connection(
        "academic_pressure", "sleep_problems",
        AttributeConnectionEntry(
            session_id=1, turn_id=1,
            attribute_1="triggers", attribute_2="triggers",
            relation_type="shared_trigger",
            connection_explanation="late-night cramming drives both",
            supporting_utterance_span="pulling all-nighters for finals and honestly I can't sleep",
            confidence="high",
        ),
    )

    # Self-edge rejected.
    try:
        g.get_or_create_edge("academic_pressure", "academic_pressure")
    except ValueError:
        pass
    else:
        raise AssertionError("expected self-edge to raise")

    # First weight recompute (current turn = 1,2).
    g.recompute_all_edge_weights(global_turn_idx(1, 2))
    edge_strong = g.edges[("academic_pressure", "sleep_problems")]
    assert 0.0 < edge_strong.weight <= 1.0, f"bad weight {edge_strong.weight}"

    # Add a second edge (co-occurrence only) and recompute.
    g.get_or_create_problem("general_anxiety", first_mentioned=(1, 3))
    g.append_cooccurrence(
        "academic_pressure", "general_anxiety",
        CooccurrenceEntry(
            session_id=1, turn_id=3,
            concise_explanation="brief mention",
            supporting_utterance_span=None,
        ),
    )
    g.recompute_all_edge_weights(global_turn_idx(1, 3))
    e_strong = g.edges[("academic_pressure", "sleep_problems")]
    e_weak = g.edges[("academic_pressure", "general_anxiety")]
    assert e_strong.weight > e_weak.weight, (
        f"attr-connection edge should outweigh cooc-only: "
        f"strong={e_strong.weight:.3f} vs weak={e_weak.weight:.3f}"
    )

    # Relevant context: strongest first, S=2 returns both.
    ctx = g.select_relevant_context("academic_pressure", top_s=2)
    assert ctx["main_problem"] == "academic_pressure"
    assert len(ctx["relevant_problems"]) == 2
    assert ctx["relevant_problems"][0]["problem_name"] == "sleep_problems"
    assert ctx["relevant_problems"][1]["problem_name"] == "general_anxiety"
    # The strong edge should carry its connection evidence.
    assert ctx["relevant_problems"][0]["connection_evidence"], "missing connection evidence on strong edge"
    assert not ctx["relevant_problems"][1]["connection_evidence"], "weak edge should have no connection evidence"

    # Top-S=1 filters to strongest only.
    ctx1 = g.select_relevant_context("academic_pressure", top_s=1)
    assert len(ctx1["relevant_problems"]) == 1
    assert ctx1["relevant_problems"][0]["problem_name"] == "sleep_problems"

    # JSON round-trip preserves state.
    d = g.to_json_dict()
    g2 = ProblemGraphV6.from_json_dict(d)
    assert g2.profile_id == g.profile_id
    assert set(g2.problems) == set(g.problems)
    assert len(g2.edges) == len(g.edges)
    assert g2.problems["academic_pressure"].goal == g.problems["academic_pressure"].goal
    assert (
        g2.problems["academic_pressure"]
         .level_attributes["perceived_severity"].current_level == "high"
    )
    edge2 = g2.edges[("academic_pressure", "sleep_problems")]
    assert len(edge2.cooccurrence_entries) == 2
    assert len(edge2.attribute_connection_entries) == 1
    assert abs(edge2.weight - e_strong.weight) < 1e-9

    # File round-trip.
    from tempfile import NamedTemporaryFile
    with NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        tmp_path = Path(f.name)
    try:
        g.save(tmp_path)
        g3 = ProblemGraphV6.load(tmp_path)
        assert g3.to_json_dict() == g.to_json_dict()
    finally:
        tmp_path.unlink(missing_ok=True)

    print("graph_v6 self-test PASSED "
          f"(strong_weight={e_strong.weight:.3f}, weak_weight={e_weak.weight:.3f})")


if __name__ == "__main__":
    _self_test()
