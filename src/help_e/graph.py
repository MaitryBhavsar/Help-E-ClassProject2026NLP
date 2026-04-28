"""HELP-E v1 graph schema (§6.3).

Bipartite NetworkX graph with three node types:

- ``PersonaNode`` — one per user; the 8 fields from §8.4.
- ``ProblemNode`` — one per problem; ``current_ttm_stage`` is the only
  change-trajectory signal. ``problem_state`` is dropped (§2 scope row).
- ``AttributeNode`` — one per ``(attr_type, value)`` per user. No canonical
  value vocabulary; two phrasings create two nodes.

Edges:

- ``PersonaNode ↔ ProblemNode`` — implicit ownership.
- ``AttributeNode ↔ ProblemNode`` — carries an append-only
  ``information_stack`` plus a recency-weighted ``current_level``. The same
  attribute may have different evidence across different problems.

Goals are modelled as AttributeNodes with ``attr_type='goal'`` (§6.3).
"""

from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Optional

import networkx as nx

from .config import PROBLEM_VOCAB, TTM_STAGES


# ---------------------------------------------------------------------------
# Node dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PersonaNode:
    demographics: Optional[str] = None
    personality_traits: list[str] = field(default_factory=list)
    core_values: list[str] = field(default_factory=list)
    core_beliefs: list[str] = field(default_factory=list)
    support_system: list[str] = field(default_factory=list)
    hobbies_interests: list[str] = field(default_factory=list)
    communication_style: Optional[str] = None
    relevant_history: Optional[str] = None

    def populated_fields(self) -> dict[str, Any]:
        """Compact view used in extraction/retrieval prompts."""
        out: dict[str, Any] = {}
        for k, v in asdict(self).items():
            if v in (None, "", [], {}):
                continue
            out[k] = v
        return out


@dataclass
class ProblemNode:
    problem_name: str = ""  # one of PROBLEM_VOCAB
    current_ttm_stage: str = "precontemplation"
    first_mentioned: tuple[int, int] = (0, 0)  # (session_id, turn_id)
    last_mentioned: tuple[int, int] = (0, 0)

    @property
    def id(self) -> str:
        return f"prob::{self.problem_name}"


@dataclass
class AttributeNode:
    attr_type: str = ""  # one of ATTR_TYPES
    value: str = ""      # free text

    @property
    def id(self) -> str:
        # Stable id derived from (attr_type, value) — normalized whitespace + lowercase.
        key = f"{self.attr_type}::{self.value.strip().lower()}"
        return f"attr::{key}"


@dataclass
class StackEntry:
    """One line of evidence on an AttributeNode↔ProblemNode edge (§6.3)."""

    session_id: int
    turn_id: int
    user_message: str
    information: str


@dataclass
class AttributeEdge:
    attr_id: str
    problem_id: str
    information_stack: list[StackEntry] = field(default_factory=list)
    current_level: str = "moderate"  # one of LEVELS

    @property
    def weight(self) -> int:
        # Number of distinct (session_id, turn_id) pairs.
        seen: set[tuple[int, int]] = set()
        for e in self.information_stack:
            seen.add((e.session_id, e.turn_id))
        return len(seen)


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


class ProblemGraph:
    """NetworkX-backed v1 graph. Stacks live on edges, not nodes."""

    def __init__(self) -> None:
        self.G = nx.Graph()  # undirected; direction semantics live on edge.attr dict
        self.persona: PersonaNode = PersonaNode()
        self._problems: dict[str, ProblemNode] = {}
        self._attributes: dict[str, AttributeNode] = {}
        self._edges: dict[tuple[str, str], AttributeEdge] = {}
        # session/turn cursor for write paths
        self._session_id: int = 0
        self._turn_id: int = 0

    # -- cursor ------------------------------------------------------------

    def set_cursor(self, session_id: int, turn_id: int) -> None:
        self._session_id = session_id
        self._turn_id = turn_id

    # -- persona -----------------------------------------------------------

    def set_persona(self, persona: PersonaNode) -> None:
        self.persona = persona
        self.G.add_node("persona", node_type="persona", data=asdict(persona))

    def update_persona(self, updates: dict[str, Any]) -> None:
        for k, v in updates.items():
            if v is None:
                continue
            if hasattr(self.persona, k):
                setattr(self.persona, k, v)
        self.G.add_node("persona", node_type="persona", data=asdict(self.persona))

    # -- problems ----------------------------------------------------------

    def get_or_create_problem(self, name: str) -> ProblemNode:
        if name not in PROBLEM_VOCAB:
            raise ValueError(f"problem_name {name!r} not in 20-vocabulary")
        node = next((p for p in self._problems.values() if p.problem_name == name), None)
        if node is not None:
            node.last_mentioned = (self._session_id, self._turn_id)
            self.G.nodes[node.id]["data"] = asdict(node)
            return node
        node = ProblemNode(
            problem_name=name,
            current_ttm_stage="precontemplation",
            first_mentioned=(self._session_id, self._turn_id),
            last_mentioned=(self._session_id, self._turn_id),
        )
        self._problems[node.id] = node
        self.G.add_node(node.id, node_type="problem", data=asdict(node))
        self.G.add_edge("persona", node.id, edge_type="has_problem")
        return node

    def get_problem(self, name: str) -> Optional[ProblemNode]:
        return next(
            (p for p in self._problems.values() if p.problem_name == name), None
        )

    def set_ttm_stage(self, problem_name: str, new_stage: str) -> None:
        if new_stage not in TTM_STAGES:
            raise ValueError(f"ttm_stage {new_stage!r} not in enum")
        p = self.get_problem(problem_name)
        if p is None:
            return
        p.current_ttm_stage = new_stage
        self.G.nodes[p.id]["data"] = asdict(p)

    # -- attributes + edges -------------------------------------------------

    def get_or_create_attribute(self, attr_type: str, value: str) -> AttributeNode:
        node = AttributeNode(attr_type=attr_type, value=value)
        existing = self._attributes.get(node.id)
        if existing is not None:
            return existing
        self._attributes[node.id] = node
        self.G.add_node(node.id, node_type="attribute", data=asdict(node))
        return node

    def append_evidence(
        self,
        *,
        attr_type: str,
        value: str,
        problem_name: str,
        information: str,
        user_message: str,
    ) -> AttributeEdge:
        """Append-only (§6.3). Creates nodes and edge if missing."""
        attr = self.get_or_create_attribute(attr_type, value)
        problem = self.get_or_create_problem(problem_name)
        key = (attr.id, problem.id)
        edge = self._edges.get(key)
        if edge is None:
            edge = AttributeEdge(
                attr_id=attr.id, problem_id=problem.id,
                information_stack=[], current_level="moderate",
            )
            self._edges[key] = edge
            self.G.add_edge(
                attr.id, problem.id,
                edge_type="has_attribute",
                current_level=edge.current_level,
            )
        entry = StackEntry(
            session_id=self._session_id,
            turn_id=self._turn_id,
            user_message=user_message,
            information=information,
        )
        edge.information_stack.append(entry)
        self.G.edges[attr.id, problem.id]["weight"] = edge.weight
        return edge

    def set_level(self, attr_id: str, problem_id: str, new_level: str) -> None:
        edge = self._edges.get((attr_id, problem_id))
        if edge is None:
            return
        edge.current_level = new_level
        self.G.edges[attr_id, problem_id]["current_level"] = new_level

    # -- queries -----------------------------------------------------------

    def get_edges_for_problem(self, problem_name: str) -> list[AttributeEdge]:
        p = self.get_problem(problem_name)
        if p is None:
            return []
        return [e for (_, pid), e in self._edges.items() if pid == p.id]

    def get_edges_with_new_evidence(
        self, since_session: int, since_turn: int
    ) -> list[AttributeEdge]:
        """Edges that had a stack entry appended at (session, turn) >= cursor."""
        out: list[AttributeEdge] = []
        for edge in self._edges.values():
            for e in edge.information_stack:
                if (e.session_id, e.turn_id) >= (since_session, since_turn):
                    out.append(edge)
                    break
        return out

    def get_attributes_detected_in_last_n(
        self, *, current_session: int, current_turn: int, n: int
    ) -> list[AttributeEdge]:
        """Edges whose information_stack has an entry within last n turns.

        "Last n turns" is computed within the current session only — turns in
        the prior session are not counted. If ``current_turn - n < 0`` we
        include from turn 0 of the current session.
        """
        min_turn = max(0, current_turn - n + 1)
        out: list[AttributeEdge] = []
        for edge in self._edges.values():
            for e in edge.information_stack:
                if e.session_id == current_session and e.turn_id >= min_turn:
                    out.append(edge)
                    break
        return out

    def get_related_problems(self, problem_name: str) -> list[tuple[ProblemNode, float]]:
        """Closeness (§6.5 Step B):

            closeness(main, p) =
                sum over shared attrs a of min(weight(main,a), weight(p,a))
              + alpha * (count of shared attrs a)

        Returns ``(problem, score)`` pairs for all problems other than ``main``,
        sorted high-to-low.
        """
        from .config import CLOSENESS_ALPHA

        main = self.get_problem(problem_name)
        if main is None:
            return []
        main_edges = {e.attr_id: e.weight for e in self.get_edges_for_problem(problem_name)}
        out: list[tuple[ProblemNode, float]] = []
        for other in self._problems.values():
            if other.problem_name == problem_name:
                continue
            other_edges = {e.attr_id: e.weight for e in self.get_edges_for_problem(other.problem_name)}
            shared = set(main_edges) & set(other_edges)
            if not shared:
                continue
            score = sum(min(main_edges[a], other_edges[a]) for a in shared) \
                + CLOSENESS_ALPHA * len(shared)
            out.append((other, score))
        out.sort(key=lambda x: x[1], reverse=True)
        return out

    # -- serialization -----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "persona": asdict(self.persona),
            "problems": [asdict(p) for p in self._problems.values()],
            "attributes": [asdict(a) for a in self._attributes.values()],
            "edges": [
                {
                    "attr_id": e.attr_id,
                    "problem_id": e.problem_id,
                    "current_level": e.current_level,
                    "weight": e.weight,
                    "information_stack": [asdict(s) for s in e.information_stack],
                }
                for e in self._edges.values()
            ],
            "stats": {
                "num_problems": len(self._problems),
                "num_attributes": len(self._attributes),
                "num_edges": len(self._edges),
            },
        }

    def snapshot(self) -> dict:
        return copy.deepcopy(self.to_dict())

    @classmethod
    def from_dict(cls, d: dict) -> "ProblemGraph":
        g = cls()
        p = d.get("persona", {})
        g.set_persona(PersonaNode(
            demographics=p.get("demographics"),
            personality_traits=list(p.get("personality_traits", [])),
            core_values=list(p.get("core_values", [])),
            core_beliefs=list(p.get("core_beliefs", [])),
            support_system=list(p.get("support_system", [])),
            hobbies_interests=list(p.get("hobbies_interests", [])),
            communication_style=p.get("communication_style"),
            relevant_history=p.get("relevant_history"),
        ))
        for prob in d.get("problems", []):
            node = ProblemNode(
                problem_name=prob["problem_name"],
                current_ttm_stage=prob.get("current_ttm_stage", "precontemplation"),
                first_mentioned=tuple(prob.get("first_mentioned", (0, 0))),
                last_mentioned=tuple(prob.get("last_mentioned", (0, 0))),
            )
            g._problems[node.id] = node
            g.G.add_node(node.id, node_type="problem", data=asdict(node))
            g.G.add_edge("persona", node.id, edge_type="has_problem")
        for attr in d.get("attributes", []):
            node = AttributeNode(attr_type=attr["attr_type"], value=attr["value"])
            g._attributes[node.id] = node
            g.G.add_node(node.id, node_type="attribute", data=asdict(node))
        for edge in d.get("edges", []):
            stack = [StackEntry(**s) for s in edge.get("information_stack", [])]
            e = AttributeEdge(
                attr_id=edge["attr_id"],
                problem_id=edge["problem_id"],
                information_stack=stack,
                current_level=edge.get("current_level", "moderate"),
            )
            g._edges[(e.attr_id, e.problem_id)] = e
            g.G.add_edge(
                e.attr_id, e.problem_id,
                edge_type="has_attribute",
                current_level=e.current_level,
                weight=e.weight,
            )
        return g

    def save_json(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)

    @classmethod
    def load_json(cls, path: str) -> "ProblemGraph":
        with open(path) as f:
            return cls.from_dict(json.load(f))
