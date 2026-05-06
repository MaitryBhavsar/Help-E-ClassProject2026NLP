"""V3 graph — V7's structure minus HBM attributes.

V3 is the trunk-without-attributes baseline:
  - Per-problem ``summary_text`` (running NL maintained by the
    ProblemAgent — same conservative-update spirit as V7's
    AttributeAgent + StageAgent, but operating on the whole problem
    instead of per-attribute).
  - Per-problem TTM stage + ttm_reasoning + ttm_change_confidence,
    written by the same ProblemAgent call.
  - Per-problem system_intent + mi_for_system_intent, also written by
    the ProblemAgent. Computed for every current_problem; only the
    main_problem's system_intent flows downstream into the response
    prompt.
  - Per-problem ``audit_stack`` of raw evidence at each turn:
    {session_id, turn_id, inferred_information, why,
     supporting_utterance_span}. Stored chronologically and never
     dropped; surfaced to ResponseAgent as ``{anchor, quote}`` pairs
     (the ``why`` stays internal — used only by the ProblemAgent to
     write summary_text).
  - Per-edge ``connection_entries`` of typed problem-problem links:
    {session_id, turn_id, relation_type, why, supporting_quote}.
    No ``attribute_a``/``attribute_b`` — V3 has no attributes. Per-edge
    ``summary_text`` maintained by the same EdgeSummaryAgent V7 uses.
  - Same retrieval as V7: weighted-degree centrality with τ-relative
    threshold + cluster fallback.
  - Same persona shape as V7 (PersonaState, Agent P updates).

Differences from V7:
  - No level_attributes, no non_level_attributes.
  - One agent (ProblemAgent) per problem instead of two (AttributeAgent
    + StageAgent), since there are no attribute-level summaries to
    maintain.
  - InferenceAgent emits per-problem evidence (one entry per problem
    per turn) instead of per-(problem, attribute) entries.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .config import (
    EDGE_WEIGHT_ALPHA_A,
    EDGE_WEIGHT_ALPHA_M,
    PROBLEM_VOCAB,
    RECENCY_HALF_LIFE_TURNS,
    RELATION_TYPES,
    TTM_STAGES_V6,
)
from .graph_v6 import (
    PersonaState,
    _recency_sum,
    global_turn_idx,
)
from .graph_v7 import LEVEL_CONFIDENCES  # reuse {"low","medium","high"} enum


# ---------------------------------------------------------------------------
# Per-problem audit entry (V3-shape — no attribute key)
# ---------------------------------------------------------------------------


@dataclass
class ProblemAuditEntryV3:
    """One raw extraction entry attached to a problem at a turn.

    ``why`` is what the InferenceAgent (Agent 2) inferred about the
    problem from the user's words; it is read by the ProblemAgent to
    update summary_text but is intentionally NOT rendered in the
    response prompt.
    """
    session_id: int
    turn_id: int
    inferred_information: str
    why: str
    supporting_utterance_span: str = ""

    def global_idx(self) -> int:
        return global_turn_idx(self.session_id, self.turn_id)


# ---------------------------------------------------------------------------
# Per-edge connection entry (V3-shape — no attribute pair)
# ---------------------------------------------------------------------------


@dataclass
class ConnectionEntryV3:
    """One typed problem-problem link at a turn. V3 has no attributes,
    so the connection has only ``relation_type`` (from RELATION_TYPES),
    a free-text ``why``, and the supporting user quote.
    """
    session_id: int
    turn_id: int
    relation_type: str
    why: str
    supporting_quote: Optional[str] = None
    useful: int = 0  # 1 if ``relation_type`` is new for this edge

    def global_idx(self) -> int:
        return global_turn_idx(self.session_id, self.turn_id)


# ---------------------------------------------------------------------------
# Problem node + edge
# ---------------------------------------------------------------------------


@dataclass
class ProblemNodeV3:
    problem_name: str
    first_mentioned: tuple[int, int]
    last_mentioned: tuple[int, int]
    summary_text: str = ""
    audit_stack: list[ProblemAuditEntryV3] = field(default_factory=list)
    current_ttm_stage: str = "precontemplation"
    ttm_reasoning: str = ""
    ttm_change_confidence: str = "low"
    system_intent: str = ""
    mi_for_system_intent: Optional[str] = None
    previous_main_for_session: bool = False


@dataclass
class ProblemEdgeV3:
    problem_1: str
    problem_2: str
    connection_entries: list[ConnectionEntryV3] = field(default_factory=list)
    weight: float = 0.0
    summary_text: str = ""

    @staticmethod
    def canonical_pair(a: str, b: str) -> tuple[str, str]:
        return (a, b) if a < b else (b, a)

    def key(self) -> tuple[str, str]:
        return (self.problem_1, self.problem_2)

    def has_relation_type(self, relation_type: str) -> bool:
        return any(e.relation_type == relation_type
                   for e in self.connection_entries)


# ---------------------------------------------------------------------------
# The graph
# ---------------------------------------------------------------------------


@dataclass
class ProblemGraphV3:
    profile_id: str
    persona: PersonaState = field(default_factory=PersonaState)
    problems: dict[str, ProblemNodeV3] = field(default_factory=dict)
    edges: dict[tuple[str, str], ProblemEdgeV3] = field(default_factory=dict)
    rolling_summary_5turns: str = ""

    # ------------------------------------------------------------------
    # Problem + edge creation
    # ------------------------------------------------------------------

    def get_or_create_problem(
        self, name: str, *, first_mentioned: tuple[int, int],
    ) -> ProblemNodeV3:
        if name not in PROBLEM_VOCAB:
            raise ValueError(f"unknown problem {name!r} (not in PROBLEM_VOCAB)")
        if name not in self.problems:
            self.problems[name] = ProblemNodeV3(
                problem_name=name,
                first_mentioned=first_mentioned,
                last_mentioned=first_mentioned,
            )
        return self.problems[name]

    def get_or_create_edge(self, a: str, b: str) -> ProblemEdgeV3:
        if a == b:
            raise ValueError(f"self-edge not allowed ({a})")
        if a not in self.problems or b not in self.problems:
            raise ValueError(
                f"both problems must exist before creating an edge: {a!r}, {b!r}"
            )
        key = ProblemEdgeV3.canonical_pair(a, b)
        if key not in self.edges:
            self.edges[key] = ProblemEdgeV3(problem_1=key[0], problem_2=key[1])
        return self.edges[key]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def append_problem_audit(
        self, *, problem_name: str, entry: ProblemAuditEntryV3,
    ) -> None:
        prob = self.problems.get(problem_name)
        if prob is None:
            raise ValueError(f"problem {problem_name!r} does not exist")
        prob.audit_stack.append(entry)
        prob.last_mentioned = (entry.session_id, entry.turn_id)

    def update_problem(
        self,
        *,
        problem_name: str,
        summary_text: str,
        new_ttm_stage: str,
        ttm_reasoning: str,
        ttm_change_confidence: str,
        system_intent: str,
        mi_for_system_intent: Optional[str],
    ) -> None:
        if new_ttm_stage not in TTM_STAGES_V6:
            raise ValueError(f"invalid ttm_stage {new_ttm_stage!r}")
        if ttm_change_confidence not in LEVEL_CONFIDENCES:
            raise ValueError(
                f"invalid ttm_change_confidence {ttm_change_confidence!r}"
            )
        prob = self.problems[problem_name]
        prob.summary_text = summary_text
        prob.current_ttm_stage = new_ttm_stage
        prob.ttm_reasoning = ttm_reasoning
        prob.ttm_change_confidence = ttm_change_confidence
        prob.system_intent = system_intent
        prob.mi_for_system_intent = mi_for_system_intent

    def append_connection_entry(
        self, *, problem_a: str, problem_b: str, entry: ConnectionEntryV3,
    ) -> int:
        if entry.relation_type not in RELATION_TYPES:
            raise ValueError(f"unknown relation_type {entry.relation_type!r}")
        edge = self.get_or_create_edge(problem_a, problem_b)
        useful = 0 if edge.has_relation_type(entry.relation_type) else 1
        entry.useful = useful
        edge.connection_entries.append(entry)
        return useful

    def update_edge_summary(
        self, *, problem_a: str, problem_b: str, summary_text: str,
    ) -> None:
        key = ProblemEdgeV3.canonical_pair(problem_a, problem_b)
        if key not in self.edges:
            raise ValueError(f"edge {key!r} does not exist")
        self.edges[key].summary_text = summary_text

    def set_rolling_summary(self, text: str) -> None:
        self.rolling_summary_5turns = text

    # ------------------------------------------------------------------
    # Edge weight + retrieval (same shape as V7)
    # ------------------------------------------------------------------

    def recompute_all_edge_weights(self, current_global_turn_idx: int) -> None:
        if not self.edges:
            return
        m_scores: dict[tuple[str, str], float] = {}
        a_scores: dict[tuple[str, str], float] = {}
        for key, edge in self.edges.items():
            indices = [e.global_idx() for e in edge.connection_entries]
            m_scores[key] = _recency_sum(
                indices, current_global_turn_idx, confidences=None,
            )
            a_scores[key] = _recency_sum(
                indices, current_global_turn_idx,
                confidences=[1.0] * len(indices),
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

    def select_neighbors_by_weighted_degree(
        self, seeds: list[str], tau: float = 0.5,
    ) -> list[tuple[str, str]]:
        """Same algorithm as ProblemGraphV7 — aggregate edge weight per
        non-seed candidate, keep candidates whose aggregate ≥ tau ×
        max_aggregate, return every edge from a kept candidate to a seed.
        """
        if not self.edges or not seeds:
            return []
        seed_set = set(seeds)

        aggregate: dict[str, float] = {}
        for (a, b), edge in self.edges.items():
            if a in seed_set and b not in seed_set:
                aggregate[b] = aggregate.get(b, 0.0) + edge.weight
            elif b in seed_set and a not in seed_set:
                aggregate[a] = aggregate.get(a, 0.0) + edge.weight

        if not aggregate:
            return []
        max_score = max(aggregate.values())
        if max_score <= 0:
            return []
        threshold = tau * max_score
        kept = {n for n, s in aggregate.items() if s >= threshold}
        if not kept:
            return []

        out: list[tuple[str, str]] = []
        for key, edge in self.edges.items():
            a, b = key
            if (a in kept and b in seed_set) or (b in kept and a in seed_set):
                out.append(key)
        return out

    # ------------------------------------------------------------------
    # Audit / connection record helpers — used by assemble_evidence_pack
    # ------------------------------------------------------------------

    @staticmethod
    def _dedup_problem_quotes(audit_stack) -> list[dict]:
        """De-duplicate audit_stack entries by (session, turn) — first
        occurrence wins. Returns ``{anchor, quote, inferred}`` per kept
        entry. ``why`` is intentionally omitted from the rendered shape;
        it's used only internally by the ProblemAgent.
        """
        seen: set[tuple[int, int]] = set()
        out: list[dict] = []
        for e in audit_stack:
            key = (e.session_id, e.turn_id)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "anchor": f"s{e.session_id}.t{e.turn_id}",
                "quote": (e.supporting_utterance_span or "").strip(),
                "inferred": (e.inferred_information or "").strip(),
            })
        return out

    @staticmethod
    def _dedup_connection_quotes(connection_entries) -> list[dict]:
        """De-duplicate connection_entries by (session, turn). Returns
        ``{anchor, relation_type, quote}`` per kept entry. V3 has no
        attribute pairs.
        """
        seen: set[tuple[int, int]] = set()
        out: list[dict] = []
        for ce in connection_entries:
            key = (ce.session_id, ce.turn_id)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "anchor": f"s{ce.session_id}.t{ce.turn_id}",
                "relation_type": ce.relation_type,
                "quote": (ce.supporting_quote or "").strip(),
            })
        return out

    # ------------------------------------------------------------------
    # Evidence pack assembly (Phase 5)
    # ------------------------------------------------------------------

    def assemble_evidence_pack(
        self,
        *,
        main_problem: Optional[str],
        current_problems: list[str],
        tau: float = 0.5,
    ) -> dict[str, Any]:
        """V3 evidence_pack — V7-shape minus HBM. Surface main_problem's
        summary + ttm + system_intent + quotes; other_current_problems
        as 1-line stubs; problem-problem connections via weighted-degree
        retrieval + cluster fallback.
        """
        # Main problem block
        main_block: Optional[dict[str, Any]] = None
        if main_problem and main_problem in self.problems:
            mp = self.problems[main_problem]
            main_block = {
                "name": mp.problem_name,
                "ttm_stage": mp.current_ttm_stage,
                "ttm_reasoning": mp.ttm_reasoning,
                "system_intent": mp.system_intent,
                "mi_for_system_intent": mp.mi_for_system_intent,
                "summary_text": mp.summary_text,
                "quotes": self._dedup_problem_quotes(mp.audit_stack),
            }

        # Other current_problems — 1-line each, no audit detail
        others_block: list[dict[str, Any]] = []
        for name in current_problems:
            if name == main_problem or name not in self.problems:
                continue
            p = self.problems[name]
            others_block.append({
                "name": name,
                "ttm_stage": p.current_ttm_stage,
                "system_intent_1line": p.system_intent,
            })

        # Problem-problem connections — weighted-degree retrieval +
        # cluster fallback (every edge fully inside current_problems is
        # added unconditionally).
        edge_keys = self.select_neighbors_by_weighted_degree(
            seeds=current_problems, tau=tau,
        )
        cur_set = set(current_problems)
        for key, edge in self.edges.items():
            if edge.problem_1 in cur_set and edge.problem_2 in cur_set:
                if key not in edge_keys:
                    edge_keys.append(key)

        connections_block: list[dict[str, Any]] = []
        for key in edge_keys:
            edge = self.edges[key]
            connections_block.append({
                "a": edge.problem_1,
                "b": edge.problem_2,
                "weight": round(edge.weight, 3),
                "summary_text": edge.summary_text,
                "n_entries": len(edge.connection_entries),
                "quotes": self._dedup_connection_quotes(edge.connection_entries),
            })

        return {
            "main_problem": main_block,
            "other_current_problems": others_block,
            "problem_problem_connections": connections_block,
            "persona": asdict(self.persona),
            "rolling_summary_5turns": self.rolling_summary_5turns,
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "persona": asdict(self.persona),
            "rolling_summary_5turns": self.rolling_summary_5turns,
            "problems": {
                name: {
                    "problem_name": p.problem_name,
                    "first_mentioned": list(p.first_mentioned),
                    "last_mentioned": list(p.last_mentioned),
                    "summary_text": p.summary_text,
                    "current_ttm_stage": p.current_ttm_stage,
                    "ttm_reasoning": p.ttm_reasoning,
                    "ttm_change_confidence": p.ttm_change_confidence,
                    "system_intent": p.system_intent,
                    "mi_for_system_intent": p.mi_for_system_intent,
                    "previous_main_for_session": p.previous_main_for_session,
                    "audit_stack": [asdict(e) for e in p.audit_stack],
                }
                for name, p in self.problems.items()
            },
            "edges": [
                {
                    "problem_1": e.problem_1,
                    "problem_2": e.problem_2,
                    "weight": e.weight,
                    "summary_text": e.summary_text,
                    "connection_entries": [asdict(c) for c in e.connection_entries],
                }
                for e in self.edges.values()
            ],
        }

    @classmethod
    def from_json_dict(cls, d: dict[str, Any]) -> "ProblemGraphV3":
        g = cls(profile_id=d["profile_id"])
        g.rolling_summary_5turns = d.get("rolling_summary_5turns", "")
        pd = d.get("persona") or {}
        known = {
            k: pd.get(k)
            for k in (
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
        for name, pd2 in (d.get("problems") or {}).items():
            p = ProblemNodeV3(
                problem_name=pd2["problem_name"],
                first_mentioned=tuple(pd2["first_mentioned"]),
                last_mentioned=tuple(pd2["last_mentioned"]),
                summary_text=pd2.get("summary_text", ""),
                current_ttm_stage=pd2.get("current_ttm_stage", "precontemplation"),
                ttm_reasoning=pd2.get("ttm_reasoning", ""),
                ttm_change_confidence=pd2.get("ttm_change_confidence", "low"),
                system_intent=pd2.get("system_intent", ""),
                mi_for_system_intent=pd2.get("mi_for_system_intent"),
                previous_main_for_session=pd2.get("previous_main_for_session", False),
                audit_stack=[
                    ProblemAuditEntryV3(**e)
                    for e in (pd2.get("audit_stack") or [])
                ],
            )
            g.problems[name] = p
        for ed in d.get("edges") or []:
            edge = ProblemEdgeV3(
                problem_1=ed["problem_1"],
                problem_2=ed["problem_2"],
                weight=ed.get("weight", 0.0),
                summary_text=ed.get("summary_text", ""),
                connection_entries=[
                    ConnectionEntryV3(**c)
                    for c in (ed.get("connection_entries") or [])
                ],
            )
            g.edges[edge.key()] = edge
        return g

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json_dict(), indent=2))

    @classmethod
    def load(cls, path: Path) -> "ProblemGraphV3":
        return cls.from_json_dict(json.loads(path.read_text()))


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _self_test() -> None:
    p1 = next(iter(PROBLEM_VOCAB))
    p2 = None
    for cand in PROBLEM_VOCAB:
        if cand != p1:
            p2 = cand
            break
    assert p2 is not None

    # Cold-start
    g = ProblemGraphV3(profile_id="T")
    assert not g.problems and not g.edges

    # Add problems + audits
    g.get_or_create_problem(p1, first_mentioned=(1, 1))
    g.get_or_create_problem(p2, first_mentioned=(1, 1))

    g.append_problem_audit(
        problem_name=p1,
        entry=ProblemAuditEntryV3(
            session_id=1, turn_id=1,
            inferred_information="user named the problem and its weight",
            why="explicit statement of overwhelm",
            supporting_utterance_span="I just can't keep going like this",
        ),
    )
    g.update_problem(
        problem_name=p1,
        summary_text="s1.t1: user named the problem with a clear overwhelm signal.",
        new_ttm_stage="contemplation",
        ttm_reasoning="user is naming the problem; not yet planning action",
        ttm_change_confidence="high",
        system_intent="reflect what's underneath; do not push for plan",
        mi_for_system_intent="complex_reflection",
    )
    p_node = g.problems[p1]
    assert p_node.current_ttm_stage == "contemplation"
    assert p_node.summary_text.startswith("s1.t1:")
    assert len(p_node.audit_stack) == 1

    # Conservative-confidence guard
    try:
        g.update_problem(
            problem_name=p1, summary_text="x",
            new_ttm_stage="contemplation", ttm_reasoning="y",
            ttm_change_confidence="bogus",
            system_intent="z", mi_for_system_intent=None,
        )
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # Add a connection
    e1 = ConnectionEntryV3(
        session_id=1, turn_id=2, relation_type="causal",
        why="problem A spills into problem B",
        supporting_quote="when one gets bad the other follows",
    )
    u1 = g.append_connection_entry(problem_a=p1, problem_b=p2, entry=e1)
    assert u1 == 1
    assert e1.useful == 1

    # Same relation_type → useful=0
    e2 = ConnectionEntryV3(
        session_id=1, turn_id=3, relation_type="causal",
        why="restated", supporting_quote=None,
    )
    u2 = g.append_connection_entry(problem_a=p1, problem_b=p2, entry=e2)
    assert u2 == 0

    # Different relation_type → useful=1
    e3 = ConnectionEntryV3(
        session_id=1, turn_id=4, relation_type="reinforcing",
        why="new dimension", supporting_quote=None,
    )
    u3 = g.append_connection_entry(problem_a=p1, problem_b=p2, entry=e3)
    assert u3 == 1

    # Edge weight + retrieval
    g.recompute_all_edge_weights(global_turn_idx(1, 4))
    edge = g.edges[ProblemEdgeV3.canonical_pair(p1, p2)]
    assert edge.weight > 0.0

    # Edge summary
    g.update_edge_summary(
        problem_a=p1, problem_b=p2,
        summary_text=(
            "s1.t2: A spills into B. s1.t3: restated. "
            "s1.t4: new dimension — also reinforcing back."
        ),
    )
    assert "reinforcing back" in edge.summary_text

    # Evidence pack — main + 1-line others + connections + quotes
    pack = g.assemble_evidence_pack(
        main_problem=p1, current_problems=[p1, p2],
    )
    assert pack["main_problem"]["name"] == p1
    assert pack["main_problem"]["summary_text"].startswith("s1.t1:")
    main_quotes = pack["main_problem"]["quotes"]
    assert main_quotes and main_quotes[0]["anchor"] == "s1.t1"
    assert "why" not in main_quotes[0]  # why is hidden

    others = pack["other_current_problems"]
    assert any(o["name"] == p2 for o in others)
    assert all(o["name"] != p1 for o in others)

    conns = pack["problem_problem_connections"]
    assert conns and {conns[0]["a"], conns[0]["b"]} == {p1, p2}
    assert "summary_text" in conns[0]
    assert conns[0]["n_entries"] == 3
    conn_quotes = conns[0]["quotes"]
    assert [r["anchor"] for r in conn_quotes] == ["s1.t2", "s1.t3", "s1.t4"]
    assert "why" not in conn_quotes[0]
    assert conn_quotes[0]["relation_type"] == "causal"

    # Round-trip JSON
    d = g.to_json_dict()
    g2 = ProblemGraphV3.from_json_dict(d)
    assert g2.problems[p1].current_ttm_stage == "contemplation"
    assert g2.problems[p1].summary_text.startswith("s1.t1:")
    edge2 = g2.edges[ProblemEdgeV3.canonical_pair(p1, p2)]
    assert len(edge2.connection_entries) == 3
    assert "reinforcing back" in edge2.summary_text

    # Cold-start guard for assemble
    g_cold = ProblemGraphV3(profile_id="cold")
    pack_cold = g_cold.assemble_evidence_pack(
        main_problem=None, current_problems=[],
    )
    assert pack_cold["main_problem"] is None
    assert pack_cold["other_current_problems"] == []
    assert pack_cold["problem_problem_connections"] == []

    print("graph_v3 self-test PASSED")


if __name__ == "__main__":
    _self_test()
