"""v7 graph — extends v6's problem-centric graph with running summaries,
per-attribute level_reasoning, system_intent per problem, and a
single stack of structured problem-problem connection entries.

Differences from v6 (`graph_v6.py`):
  - Per (problem, attribute), the graph stores `summary_text` (chronological
    NL maintained by Agent 3a) + `current_level` + `level_reasoning` +
    `level_change_confidence`. Raw evidence stack kept for audit only.
  - Each problem has `system_intent`, `mi_for_system_intent`, `ttm_reasoning`,
    `previous_main_for_session`.
  - Edges hold a single `connection_entries` stack with structured per-entry
    fields (no separate cooccurrence + attr_connection split, no
    LLM-maintained running summary).
  - `rolling_summary_5turns` lives on the graph (maintained by Agent X).
  - `assemble_evidence_pack(main_problem, current_problems)` returns the
    dict consumed by Agent 5.
  - `select_neighbors_by_weighted_degree(seeds, tau)` is the V7
    retrieval method: aggregates per-candidate connection strength to
    the seed set, keeps candidates whose score ≥ tau × max_score. The
    legacy per-edge τ-method is archived under
    ``_archive/retrieval_legacy/v7_tau_retrieval.py``.

See plan at /Users/maitry/.claude/plans/i-want-u-to-lovely-boot.md.
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
    TTM_STAGES_V6,
)
from .graph_v6 import (
    AttributeEvidenceEntry,
    PersonaState,
    _recency_sum,
    global_turn_idx,
)


# ---------------------------------------------------------------------------
# Per-attribute state (v7 — adds running summary + level_reasoning)
# ---------------------------------------------------------------------------

LEVEL_CONFIDENCES: tuple[str, ...] = ("low", "medium", "high")


@dataclass
class LevelAttributeStateV7:
    current_level: str = "unknown"  # one of LEVELS_V6
    summary_text: str = ""           # chronological NL, maintained by Agent 3a
    level_reasoning: str = ""        # why current_level
    level_change_confidence: str = "low"  # one of LEVEL_CONFIDENCES
    audit_stack: list[AttributeEvidenceEntry] = field(default_factory=list)


@dataclass
class NonLevelAttributeStateV7:
    summary_text: str = ""           # chronological NL
    audit_stack: list[AttributeEvidenceEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Problem node (v7)
# ---------------------------------------------------------------------------


@dataclass
class ProblemNodeV7:
    problem_name: str
    first_mentioned: tuple[int, int]
    last_mentioned: tuple[int, int]
    current_ttm_stage: str = "precontemplation"  # one of TTM_STAGES_V6
    ttm_reasoning: str = ""
    system_intent: str = ""
    mi_for_system_intent: Optional[str] = None  # MISC code or None
    goal: Optional[str] = None
    level_attributes: dict[str, LevelAttributeStateV7] = field(default_factory=dict)
    non_level_attributes: dict[str, NonLevelAttributeStateV7] = field(default_factory=dict)
    previous_main_for_session: bool = False

    def has_any_evidence(self) -> bool:
        return bool(self.level_attributes) or bool(self.non_level_attributes)


# ---------------------------------------------------------------------------
# Problem-problem edge (single structured entry stack)
# ---------------------------------------------------------------------------


@dataclass
class ConnectionEntryV7:
    turn_id: int
    session_id: int
    attribute_a: str
    attribute_b: str
    relation_type: str  # one of RELATION_TYPES
    why: str
    supporting_quote: Optional[str]
    useful: int = 0  # 1 if (attr_a, attr_b, relation_type) is new for this edge

    def global_idx(self) -> int:
        return global_turn_idx(self.session_id, self.turn_id)


@dataclass
class ProblemEdgeV7:
    problem_1: str  # alphabetically first
    problem_2: str  # alphabetically second
    connection_entries: list[ConnectionEntryV7] = field(default_factory=list)
    weight: float = 0.0
    summary_text: str = ""  # chronological NL maintained by Agent 3c

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

    def has_relation_type(self, attr_a: str, attr_b: str, relation_type: str) -> bool:
        """True if any prior entry on this edge had the same
        (attr_a, attr_b, relation_type) tuple. Treats (a, b) and (b, a)
        as identical.
        """
        canonical = tuple(sorted([attr_a, attr_b]))
        for e in self.connection_entries:
            if (
                tuple(sorted([e.attribute_a, e.attribute_b])) == canonical
                and e.relation_type == relation_type
            ):
                return True
        return False


# ---------------------------------------------------------------------------
# The graph
# ---------------------------------------------------------------------------


@dataclass
class ProblemGraphV7:
    profile_id: str
    persona: PersonaState = field(default_factory=PersonaState)
    problems: dict[str, ProblemNodeV7] = field(default_factory=dict)
    edges: dict[tuple[str, str], ProblemEdgeV7] = field(default_factory=dict)
    rolling_summary_5turns: str = ""  # maintained by Agent X end-of-turn

    # --- problem + edge creation -----------------------------------------

    def get_or_create_problem(
        self, name: str, *, first_mentioned: tuple[int, int]
    ) -> ProblemNodeV7:
        if name not in PROBLEM_VOCAB:
            raise ValueError(f"unknown problem {name!r} (not in PROBLEM_VOCAB)")
        if name not in self.problems:
            self.problems[name] = ProblemNodeV7(
                problem_name=name,
                first_mentioned=first_mentioned,
                last_mentioned=first_mentioned,
            )
        return self.problems[name]

    def get_or_create_edge(self, a: str, b: str) -> ProblemEdgeV7:
        if a == b:
            raise ValueError(f"self-edge not allowed ({a})")
        if a not in self.problems or b not in self.problems:
            raise ValueError(
                f"both problems must exist before creating an edge: {a!r}, {b!r}"
            )
        key = ProblemEdgeV7.canonical_pair(a, b)
        if key not in self.edges:
            self.edges[key] = ProblemEdgeV7(problem_1=key[0], problem_2=key[1])
        return self.edges[key]

    # --- writes ----------------------------------------------------------

    def append_audit_entry(
        self, *, problem_name: str, attr_name: str, entry: AttributeEvidenceEntry,
    ) -> None:
        """Append the raw extraction entry to the audit stack. The summary
        + level are set separately by Agent 3a via update_level_attribute /
        update_non_level_attribute.
        """
        prob = self.problems.get(problem_name)
        if prob is None:
            raise ValueError(f"problem {problem_name!r} does not exist")
        if attr_name in LEVEL_ATTR_TYPES:
            state = prob.level_attributes.setdefault(attr_name, LevelAttributeStateV7())
            state.audit_stack.append(entry)
        elif attr_name in NON_LEVEL_ATTR_TYPES:
            state = prob.non_level_attributes.setdefault(attr_name, NonLevelAttributeStateV7())
            state.audit_stack.append(entry)
            if attr_name == "goal":
                prob.goal = entry.inferred_information
        else:
            raise ValueError(f"unknown attribute {attr_name!r}")
        prob.last_mentioned = (entry.session_id, entry.turn_id)

    def update_level_attribute(
        self,
        *,
        problem_name: str,
        attr_name: str,
        summary_text: str,
        current_level: str,
        level_reasoning: str,
        level_change_confidence: str,
    ) -> None:
        if current_level not in LEVELS_V6:
            raise ValueError(f"invalid level {current_level!r}")
        if level_change_confidence not in LEVEL_CONFIDENCES:
            raise ValueError(f"invalid level_change_confidence {level_change_confidence!r}")
        if attr_name not in LEVEL_ATTR_TYPES:
            raise ValueError(f"{attr_name!r} is not a level attribute")
        prob = self.problems[problem_name]
        state = prob.level_attributes.setdefault(attr_name, LevelAttributeStateV7())
        state.summary_text = summary_text
        state.current_level = current_level
        state.level_reasoning = level_reasoning
        state.level_change_confidence = level_change_confidence

    def update_non_level_attribute(
        self, *, problem_name: str, attr_name: str, summary_text: str,
    ) -> None:
        if attr_name not in NON_LEVEL_ATTR_TYPES:
            raise ValueError(f"{attr_name!r} is not a non-level attribute")
        prob = self.problems[problem_name]
        state = prob.non_level_attributes.setdefault(attr_name, NonLevelAttributeStateV7())
        state.summary_text = summary_text

    def append_connection_entry(
        self,
        *,
        problem_a: str,
        problem_b: str,
        entry: ConnectionEntryV7,
    ) -> int:
        """Append a structured connection entry. Returns the `useful` flag
        (1 if (attr_a, attr_b, relation_type) was new for this edge, else 0).
        Sets entry.useful in place.
        """
        if entry.relation_type not in RELATION_TYPES:
            raise ValueError(f"unknown relation_type {entry.relation_type!r}")
        edge = self.get_or_create_edge(problem_a, problem_b)
        useful = 0 if edge.has_relation_type(
            entry.attribute_a, entry.attribute_b, entry.relation_type,
        ) else 1
        entry.useful = useful
        edge.connection_entries.append(entry)
        return useful

    def update_edge_summary(
        self, *, problem_a: str, problem_b: str, summary_text: str,
    ) -> None:
        """Set the running NL summary maintained by Agent 3c for this edge.

        Both endpoints must already exist as problems in the graph and the
        edge must already exist (typically created by a prior
        `append_connection_entry` call).
        """
        key = ProblemEdgeV7.canonical_pair(problem_a, problem_b)
        if key not in self.edges:
            raise ValueError(
                f"edge {key!r} does not exist; append a connection entry first"
            )
        self.edges[key].summary_text = summary_text

    def set_ttm(
        self,
        *,
        problem_name: str,
        new_stage: str,
        ttm_reasoning: str,
        system_intent: str,
        mi_for_system_intent: Optional[str],
    ) -> None:
        if new_stage not in TTM_STAGES_V6:
            raise ValueError(f"invalid TTM stage {new_stage!r}")
        prob = self.problems[problem_name]
        prob.current_ttm_stage = new_stage
        prob.ttm_reasoning = ttm_reasoning
        prob.system_intent = system_intent
        prob.mi_for_system_intent = mi_for_system_intent

    def set_rolling_summary(self, text: str) -> None:
        self.rolling_summary_5turns = text

    # --- edge weight recompute -------------------------------------------

    def recompute_all_edge_weights(self, current_global_turn_idx: int) -> None:
        """v7 has a single stack of structured connection entries per edge.
        Weight = α_M * norm(count, half-life-decayed) + α_A * norm(conf-weighted).
        Reuses v6's exponent shape so values are roughly comparable.
        """
        if not self.edges:
            return
        m_scores: dict[tuple[str, str], float] = {}
        a_scores: dict[tuple[str, str], float] = {}
        for key, edge in self.edges.items():
            indices = [e.global_idx() for e in edge.connection_entries]
            # M = recency-decayed count (treat each entry as one cooccurrence)
            m_scores[key] = _recency_sum(
                indices, current_global_turn_idx, confidences=None,
            )
            # A = recency-decayed weighted by relation strength. Use a
            # uniform 1.0 weight per entry since v7 doesn't carry a
            # per-entry confidence (entry quality is gated upstream by the
            # inference LLM via supporting_quote requirement).
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

    # --- weighted-degree neighbor selection (Agent 4 step 2) -------------

    def select_neighbors_by_weighted_degree(
        self, seeds: list[str], tau: float = 0.5,
    ) -> list[tuple[str, str]]:
        """Weighted-degree-centrality retrieval (current V7 default).

        For each non-seed candidate node ``X``, compute its **aggregate
        connection strength** to the seed set:

            score(X) = Σ over seeds s with edge(s, X):  weight(s, X)

        Keep candidate ``X`` iff
        ``score(X) ≥ tau × max(score over all non-seed candidates)``.
        Then surface **every** edge from a kept candidate to a seed,
        regardless of that individual edge's weight. Two sub-threshold
        edges from ``X`` to two different seeds therefore aggregate and
        survive if their sum is high enough — exactly the case the
        per-edge τ-filter misses.

        Returns canonical edge keys ``(a, b)``. Seed-seed edges are NOT
        returned by this method (the cluster fallback in
        ``assemble_evidence_pack`` handles those separately).

        Network-science name: weighted degree centrality restricted to
        the seed set, with relative threshold (Newman 2004; Freeman 1979
        for the unweighted ancestor).
        """
        if not self.edges or not seeds:
            return []
        seed_set = set(seeds)

        # Aggregate score per non-seed candidate (sum of edge weights to
        # any seed).
        aggregate: dict[str, float] = {}
        for (a, b), edge in self.edges.items():
            if a in seed_set and b not in seed_set:
                aggregate[b] = aggregate.get(b, 0.0) + edge.weight
            elif b in seed_set and a not in seed_set:
                aggregate[a] = aggregate.get(a, 0.0) + edge.weight
            # seed-seed and non-seed-non-seed edges contribute nothing.

        if not aggregate:
            return []
        max_score = max(aggregate.values())
        if max_score <= 0:
            return []
        threshold = tau * max_score

        kept_candidates = {n for n, s in aggregate.items() if s >= threshold}
        if not kept_candidates:
            return []

        out: list[tuple[str, str]] = []
        for key, edge in self.edges.items():
            a, b = key
            if (a in kept_candidates and b in seed_set) or (
                b in kept_candidates and a in seed_set
            ):
                out.append(key)
        return out

    # --- audit-anchor helpers --------------------------------------------

    @staticmethod
    def _dedup_audit_anchors(audit_stack) -> list[str]:
        """Return chronological, de-duplicated ``sN.tM`` anchors from an
        audit_stack. Each (session, turn) pair appears at most once.
        Used by V8 (which surfaces audit *content* via RAG and only
        needs anchors in the main_problem block).
        """
        seen: set[tuple[int, int]] = set()
        out: list[str] = []
        for e in audit_stack:
            key = (e.session_id, e.turn_id)
            if key in seen:
                continue
            seen.add(key)
            out.append(f"s{e.session_id}.t{e.turn_id}")
        return out

    @staticmethod
    def _dedup_attribute_quotes(audit_stack) -> list[dict]:
        """Return chronological, de-duplicated *quote records* per
        attribute audit_stack. Each (session, turn) pair appears at
        most once; if multiple entries share the same anchor (e.g.,
        the attribute extracted twice from one user message), only
        the first is kept.

        Each record:
            {anchor: "sN.tM", quote: str, inferred: str}

        Used by V7 to surface the actual user words that grounded each
        attribute, in addition to the running summary_text.
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
        """Same as ``_dedup_attribute_quotes`` but for an edge's
        connection_entries. Each record:
            {anchor, attribute_a, attribute_b, relation_type, quote}

        ``why`` is intentionally omitted — the edge's running
        ``summary_text`` already captures the relational story; the per-
        anchor records carry only the typed pair, relation_type, and
        verbatim user quote.
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
                "attribute_a": ce.attribute_a,
                "attribute_b": ce.attribute_b,
                "relation_type": ce.relation_type,
                "quote": (ce.supporting_quote or "").strip(),
            })
        return out

    # --- evidence pack assembly (Agent 4) --------------------------------

    def assemble_evidence_pack(
        self,
        *,
        main_problem: Optional[str],
        current_problems: list[str],
        tau: float = 0.5,
    ) -> dict[str, Any]:
        """Assemble the dict consumed by Agent 5.

        Layout:
          - main_problem: full level + non-level attribute detail
          - other_current_problems: 1-line each, no attribute detail
          - problem_problem_connections: edges among current_problems
            (cluster fallback) + neighbor edges retained by
            weighted-degree centrality, each rendered as its running
            ``summary_text`` (maintained by Agent 3c).
          - persona: full 9 fields
          - rolling_summary_5turns

        Retrieval is weighted-degree centrality: each non-seed candidate
        is scored as the sum of its edge weights to seeds; candidates
        whose score ≥ tau × max(score) are kept, and every edge from a
        kept candidate to a seed is surfaced. The cluster fallback
        (edges fully inside ``current_problems``) is added
        unconditionally on top of that.
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
                "level_attributes": {
                    name: {
                        "summary_text": s.summary_text,
                        "current_level": s.current_level,
                        "level_reasoning": s.level_reasoning,
                        "quotes": self._dedup_attribute_quotes(s.audit_stack),
                    }
                    for name, s in mp.level_attributes.items()
                    if s.summary_text or s.current_level != "unknown"
                },
                "non_level_attributes": {
                    name: {
                        "summary_text": s.summary_text,
                        "quotes": self._dedup_attribute_quotes(s.audit_stack),
                    }
                    for name, s in mp.non_level_attributes.items()
                    if s.summary_text
                },
            }

        # Other current problems (1-line each)
        others_block: list[dict[str, Any]] = []
        for name in current_problems:
            if name == main_problem:
                continue
            if name not in self.problems:
                continue
            p = self.problems[name]
            others_block.append({
                "name": name,
                "ttm_stage": p.current_ttm_stage,
                "system_intent_1line": p.system_intent,
            })

        # Problem-problem connections — weighted-degree retrieval plus
        # the cluster fallback below.
        edge_keys = self.select_neighbors_by_weighted_degree(
            seeds=current_problems, tau=tau,
        )
        # Also include all edges fully inside current_problems (regardless
        # of weight), because those are part of "the cluster".
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
                "quotes": self._dedup_connection_quotes(
                    edge.connection_entries
                ),
            })

        return {
            "main_problem": main_block,
            "other_current_problems": others_block,
            "problem_problem_connections": connections_block,
            "persona": asdict(self.persona),
            "rolling_summary_5turns": self.rolling_summary_5turns,
        }

    # --- persistence -----------------------------------------------------

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
                    "current_ttm_stage": p.current_ttm_stage,
                    "ttm_reasoning": p.ttm_reasoning,
                    "system_intent": p.system_intent,
                    "mi_for_system_intent": p.mi_for_system_intent,
                    "goal": p.goal,
                    "previous_main_for_session": p.previous_main_for_session,
                    "level_attributes": {
                        k: {
                            "current_level": v.current_level,
                            "summary_text": v.summary_text,
                            "level_reasoning": v.level_reasoning,
                            "level_change_confidence": v.level_change_confidence,
                            "audit_stack": [asdict(e) for e in v.audit_stack],
                        }
                        for k, v in p.level_attributes.items()
                    },
                    "non_level_attributes": {
                        k: {
                            "summary_text": v.summary_text,
                            "audit_stack": [asdict(e) for e in v.audit_stack],
                        }
                        for k, v in p.non_level_attributes.items()
                    },
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
    def from_json_dict(cls, d: dict[str, Any]) -> "ProblemGraphV7":
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

        for name, pd2 in d.get("problems", {}).items():
            p = ProblemNodeV7(
                problem_name=pd2["problem_name"],
                first_mentioned=tuple(pd2["first_mentioned"]),
                last_mentioned=tuple(pd2["last_mentioned"]),
                current_ttm_stage=pd2.get("current_ttm_stage", "precontemplation"),
                ttm_reasoning=pd2.get("ttm_reasoning", ""),
                system_intent=pd2.get("system_intent", ""),
                mi_for_system_intent=pd2.get("mi_for_system_intent"),
                goal=pd2.get("goal"),
                previous_main_for_session=pd2.get("previous_main_for_session", False),
            )
            for k, v in pd2.get("level_attributes", {}).items():
                p.level_attributes[k] = LevelAttributeStateV7(
                    current_level=v.get("current_level", "unknown"),
                    summary_text=v.get("summary_text", ""),
                    level_reasoning=v.get("level_reasoning", ""),
                    level_change_confidence=v.get("level_change_confidence", "low"),
                    audit_stack=[
                        AttributeEvidenceEntry(**e)
                        for e in v.get("audit_stack", [])
                    ],
                )
            for k, v in pd2.get("non_level_attributes", {}).items():
                p.non_level_attributes[k] = NonLevelAttributeStateV7(
                    summary_text=v.get("summary_text", ""),
                    audit_stack=[
                        AttributeEvidenceEntry(**e)
                        for e in v.get("audit_stack", [])
                    ],
                )
            g.problems[name] = p

        for ed in d.get("edges", []):
            edge = ProblemEdgeV7(
                problem_1=ed["problem_1"],
                problem_2=ed["problem_2"],
                weight=ed.get("weight", 0.0),
                summary_text=ed.get("summary_text", ""),
                connection_entries=[
                    ConnectionEntryV7(**c)
                    for c in ed.get("connection_entries", [])
                ],
            )
            g.edges[edge.key()] = edge
        return g

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json_dict(), indent=2))

    @classmethod
    def load(cls, path: Path) -> "ProblemGraphV7":
        return cls.from_json_dict(json.loads(path.read_text()))


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _self_test() -> None:
    from .config import PROBLEM_VOCAB

    p1 = next(iter(PROBLEM_VOCAB))  # "academic_pressure"
    # Pick two distinct problems for edge tests.
    p2 = None
    for cand in PROBLEM_VOCAB:
        if cand != p1:
            p2 = cand
            break
    assert p2 is not None

    # 1. Cold-start invariant.
    g = ProblemGraphV7(profile_id="T")
    assert not g.problems
    assert not g.edges
    assert g.rolling_summary_5turns == ""
    assert g.persona.is_empty()

    # 2. Create problems, append audit entries, update summaries.
    g.get_or_create_problem(p1, first_mentioned=(1, 1))
    g.get_or_create_problem(p2, first_mentioned=(1, 1))

    g.append_audit_entry(
        problem_name=p1, attr_name="perceived_severity",
        entry=AttributeEvidenceEntry(
            session_id=1, turn_id=1,
            inferred_information="user said pressure is overwhelming",
            concise_explanation="severity high from t1",
            supporting_utterance_span="the pressure is getting to me",
        ),
    )
    g.update_level_attribute(
        problem_name=p1, attr_name="perceived_severity",
        summary_text="t1: user named pressure as overwhelming → severity high.",
        current_level="high", level_reasoning="single unambiguous statement",
        level_change_confidence="high",
    )
    state = g.problems[p1].level_attributes["perceived_severity"]
    assert state.current_level == "high"
    assert state.summary_text.startswith("t1:")
    assert state.level_change_confidence == "high"
    assert len(state.audit_stack) == 1

    # 3. Conservative confidence rule — invalid level_change_confidence.
    try:
        g.update_level_attribute(
            problem_name=p1, attr_name="perceived_severity",
            summary_text="x", current_level="medium",
            level_reasoning="y", level_change_confidence="bogus",
        )
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # 4. Connection entry — usefulness flag.
    e1 = ConnectionEntryV7(
        turn_id=2, session_id=1,
        attribute_a="perceived_severity", attribute_b="perceived_barriers",
        relation_type="causal",
        why="severity drives barriers",
        supporting_quote="every revision feels like a judgment",
    )
    u1 = g.append_connection_entry(problem_a=p1, problem_b=p2, entry=e1)
    assert u1 == 1, "first occurrence should be useful"
    assert e1.useful == 1

    # Same (attr pair, relation_type) → useful=0
    e2 = ConnectionEntryV7(
        turn_id=3, session_id=1,
        attribute_a="perceived_severity", attribute_b="perceived_barriers",
        relation_type="causal",
        why="restated",
        supporting_quote=None,
    )
    u2 = g.append_connection_entry(problem_a=p1, problem_b=p2, entry=e2)
    assert u2 == 0, "duplicate pair+relation → not useful"

    # Different relation_type → useful=1 again
    e3 = ConnectionEntryV7(
        turn_id=4, session_id=1,
        attribute_a="perceived_severity", attribute_b="perceived_barriers",
        relation_type="reinforcing",
        why="new dimension",
        supporting_quote=None,
    )
    u3 = g.append_connection_entry(problem_a=p1, problem_b=p2, entry=e3)
    assert u3 == 1

    # 5. Edge weight recompute + threshold neighbor selection.
    g.recompute_all_edge_weights(current_global_turn_idx=global_turn_idx(1, 4))
    assert g.edges[ProblemEdgeV7.canonical_pair(p1, p2)].weight > 0.0

    # 5a. Weighted-degree retrieval — single seed, single candidate.
    wd_keys_single = g.select_neighbors_by_weighted_degree(seeds=[p1], tau=0.5)
    assert wd_keys_single, "single candidate p2 should be kept under weighted_degree"
    # τ above max → empty
    assert g.select_neighbors_by_weighted_degree(seeds=[p1], tau=1.5) == []

    # 5b. Two-sub-threshold-edges-to-common-neighbor scenario.
    #     A 4th problem X is linked to two seeds via individually-weak
    #     edges. Aggregate score is high, so weighted_degree keeps both
    #     edges (the case the legacy per-edge τ-method missed).
    q3_iter = iter(PROBLEM_VOCAB)
    A = next(q3_iter); B = next(q3_iter); X = next(q3_iter); other = next(q3_iter)
    g_q3 = ProblemGraphV7(profile_id="Q3")
    for name in (A, B, X, other):
        g_q3.get_or_create_problem(name, first_mentioned=(1, 1))

    # 1 entry on the strong A-B edge → fresh, full recency weight 1.0.
    g_q3.append_connection_entry(
        problem_a=A, problem_b=B,
        entry=ConnectionEntryV7(
            session_id=1, turn_id=1, attribute_a="perceived_severity",
            attribute_b="perceived_severity", relation_type="reinforcing",
            why="strong shared severity", supporting_quote="x",
        ),
    )
    # 1 entry each on the weak A-X and B-X edges. Both at the same age,
    # so identical raw_strength → identical weight after log-norm.
    for partner in (A, B):
        g_q3.append_connection_entry(
            problem_a=partner, problem_b=X,
            entry=ConnectionEntryV7(
                session_id=1, turn_id=1, attribute_a="triggers",
                attribute_b="triggers", relation_type="shared_trigger",
                why="minor trigger overlap", supporting_quote="y",
            ),
        )

    # Force the A-B edge to look stronger by stacking two more entries on
    # it (so its raw_strength clearly exceeds the singletons).
    for tt in (2, 3):
        g_q3.append_connection_entry(
            problem_a=A, problem_b=B,
            entry=ConnectionEntryV7(
                session_id=1, turn_id=tt, attribute_a="perceived_severity",
                attribute_b="perceived_severity", relation_type="reinforcing",
                why="repeated", supporting_quote="z",
            ),
        )

    g_q3.recompute_all_edge_weights(global_turn_idx(1, 3))
    w_AB = g_q3.edges[ProblemEdgeV7.canonical_pair(A, B)].weight
    w_AX = g_q3.edges[ProblemEdgeV7.canonical_pair(A, X)].weight
    w_BX = g_q3.edges[ProblemEdgeV7.canonical_pair(B, X)].weight
    assert w_AB > w_AX and w_AB > w_BX, "A-B should be strongest"
    # Each of A-X and B-X should be individually below 0.5 × w_AB so the
    # per-edge τ-filter would drop them.
    assert w_AX < 0.5 * w_AB and w_BX < 0.5 * w_AB, (
        "test setup: A-X / B-X must each be < τ × max_w to demonstrate the bug"
    )

    seeds_q3 = [A, B]

    # Weighted-degree retrieval aggregates X's connections to both seeds
    # and keeps both edges (even though each individual edge weight
    # would fall below τ × max_w).
    wd_edges = g_q3.select_neighbors_by_weighted_degree(
        seeds=seeds_q3, tau=0.5,
    )
    flat_wd = {tuple(sorted(k)) for k in wd_edges}
    assert ProblemEdgeV7.canonical_pair(A, X) in flat_wd, (
        "weighted_degree should keep A-X (X aggregates strongly)"
    )
    assert ProblemEdgeV7.canonical_pair(B, X) in flat_wd, (
        "weighted_degree should keep B-X (X aggregates strongly)"
    )

    # The unrelated 'other' problem (no edges) is not kept.
    other_keys = [k for k in wd_edges if other in (k[0], k[1])]
    assert other_keys == [], "unrelated problem should not appear"

    # 5c. assemble_evidence_pack surfaces the same edges via cluster
    #     fallback (A-B) + weighted-degree retrieval (A-X, B-X).
    pack_wd = g_q3.assemble_evidence_pack(
        main_problem=A, current_problems=[A, B],
    )
    edges_wd = {tuple(sorted([c["a"], c["b"]]))
                for c in pack_wd["problem_problem_connections"]}
    assert ProblemEdgeV7.canonical_pair(A, X) in edges_wd
    assert ProblemEdgeV7.canonical_pair(B, X) in edges_wd
    assert ProblemEdgeV7.canonical_pair(A, B) in edges_wd  # cluster fallback

    # 6. TTM + system_intent set.
    g.set_ttm(
        problem_name=p1, new_stage="contemplation",
        ttm_reasoning="severity high, motivation unknown",
        system_intent="reflect both sides; do not plan yet.",
        mi_for_system_intent="evoke",
    )
    assert g.problems[p1].current_ttm_stage == "contemplation"
    assert g.problems[p1].mi_for_system_intent == "evoke"

    # 7. Evidence pack assembly.
    g.update_edge_summary(
        problem_a=p1, problem_b=p2,
        summary_text=(
            "t1.2: severity drives barriers; user said 'every revision feels "
            "like a judgment'. t1.3: same point, restated. t1.4: new dimension"
            " — barriers also reinforce severity (mutual amplification)."
        ),
    )
    pack = g.assemble_evidence_pack(
        main_problem=p1, current_problems=[p1, p2],
    )
    assert pack["main_problem"] is not None
    assert pack["main_problem"]["name"] == p1
    assert "perceived_severity" in pack["main_problem"]["level_attributes"]
    assert pack["main_problem"]["system_intent"] == "reflect both sides; do not plan yet."
    # other_current_problems excludes the main one
    assert any(o["name"] == p2 for o in pack["other_current_problems"])
    assert all(o["name"] != p1 for o in pack["other_current_problems"])
    # connection block present and uses summary_text (no raw entries slice)
    assert pack["problem_problem_connections"], "should include p1-p2 edge"
    conn = pack["problem_problem_connections"][0]
    assert {conn["a"], conn["b"]} == {p1, p2}
    assert "summary_text" in conn and "mutual amplification" in conn["summary_text"]
    assert conn["n_entries"] == 3, "all 3 entries persist; summary represents them"
    assert "entries" not in conn, "raw entries no longer surfaced — summary only"
    # quotes deduplicated by (session, turn); entries 2/3/4 → 3 records.
    conn_quotes = conn["quotes"]
    assert [r["anchor"] for r in conn_quotes] == ["s1.t2", "s1.t3", "s1.t4"], (
        f"expected deduped chronological anchors, got "
        f"{[r['anchor'] for r in conn_quotes]!r}"
    )
    # First record carries the per-entry connection content.
    assert conn_quotes[0]["relation_type"] == "causal"
    assert conn_quotes[0]["attribute_a"] in ("perceived_severity", "perceived_barriers")
    # `why` should NOT appear in the rendered quote records — the
    # edge's running summary_text already covers the relational story.
    assert "why" not in conn_quotes[0]
    assert "session_id" not in conn_quotes[0]  # anchor encodes both
    # Main attribute also surfaces verbatim user quotes.
    main_attr_quotes = (
        pack["main_problem"]["level_attributes"]["perceived_severity"]["quotes"]
    )
    assert main_attr_quotes and main_attr_quotes[0].get("anchor")
    assert "quote" in main_attr_quotes[0]
    assert "concise" not in main_attr_quotes[0]

    # 8. Rolling summary set/get.
    g.set_rolling_summary("user vented about deadline pressure across 3 turns")
    assert "deadline" in g.rolling_summary_5turns

    # 9. Round-trip JSON.
    d = g.to_json_dict()
    g2 = ProblemGraphV7.from_json_dict(d)
    assert g2.profile_id == g.profile_id
    assert g2.problems[p1].current_ttm_stage == "contemplation"
    assert g2.problems[p1].level_attributes["perceived_severity"].current_level == "high"
    edge2 = g2.edges[ProblemEdgeV7.canonical_pair(p1, p2)]
    assert len(edge2.connection_entries) == 3
    assert "mutual amplification" in edge2.summary_text
    assert g2.rolling_summary_5turns.startswith("user vented")

    # 10. Save/load.
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = Path(f.name)
    try:
        g.save(path)
        g3 = ProblemGraphV7.load(path)
        assert g3.problems[p1].system_intent == "reflect both sides; do not plan yet."
    finally:
        path.unlink(missing_ok=True)

    # 11. Cold-start guard — main_problem=None returns sane pack.
    g_cold = ProblemGraphV7(profile_id="cold")
    pack_cold = g_cold.assemble_evidence_pack(
        main_problem=None, current_problems=[],
    )
    assert pack_cold["main_problem"] is None
    assert pack_cold["other_current_problems"] == []
    assert pack_cold["problem_problem_connections"] == []

    print("graph_v7 self-test PASSED")


if __name__ == "__main__":
    _self_test()
