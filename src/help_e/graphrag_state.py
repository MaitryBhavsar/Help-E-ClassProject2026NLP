"""GraphRAG baseline — per-profile entity graph (incremental, in-memory).

Mirrors the spirit of Microsoft GraphRAG (Edge et al., 2024, arXiv:2404.16130)
adapted to a per-turn conversational setting: an LLM extracts entities and
free-text relationships from each user message + recent dialogue, the result
is appended into this lightweight graph, and a local-search retrieval pulls
the 1-hop neighborhood of query-relevant entities into the response prompt.

Differences from `graph_v6.ProblemGraphV6` (the v6 main graph):
  - No 20-problem vocabulary constraint — entities are free-text strings.
  - No HBM attribute typing.
  - No TTM stage tracking.
  - No edge-weight formula — flat adjacency with timestamped provenance.
  - No persona — the chatbot side only carries the entity graph.

This file is purely additive: nothing in the v1/v3/v4/v6 pipelines imports
from here. It is loaded only by `baselines/graphrag_baseline.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class EntityRecord:
    """One canonical entity in the GraphRAG graph.

    `key` is the lowercased / stripped form used for deduplication.
    `display` retains the first-seen surface form for prompt rendering.
    `provenance` is the chronological list of every time the entity was seen.
    """
    key: str
    display: str
    provenance: list[dict] = field(default_factory=list)  # {session_id, turn_id, span}


@dataclass
class RelationshipRecord:
    """One free-text directed relationship between two entities."""
    entity_a_key: str
    entity_b_key: str
    label: str                    # free text, e.g. "caused", "make worse"
    session_id: int
    turn_id: int
    supporting_utterance_span: str = ""


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


def _normalize(name: str) -> str:
    """Stable canonicalization for entity keys."""
    return (name or "").strip().lower()


class GraphRAGState:
    """Free-text entity graph for the GraphRAG baseline.

    Adjacency is stored as ``{key: set[other_key]}`` (undirected union of
    incoming and outgoing edges, since for retrieval we just want the 1-hop
    neighborhood). Per-relationship records are stored separately in
    ``self.relationships`` so the retrieval can surface specific labeled
    edges.
    """

    def __init__(self, profile_id: str) -> None:
        self.profile_id = profile_id
        self.entities: dict[str, EntityRecord] = {}        # key → EntityRecord
        self.relationships: list[RelationshipRecord] = []  # chronological
        self.adjacency: dict[str, set[str]] = {}           # key → set of neighbor keys

    # ----- mutation ------------------------------------------------------

    def add_entities(self, entries: list[dict], session_id: int, turn_id: int) -> None:
        """Append entities from one inference call's output.

        Each ``entries[i]`` is ``{"name": str, "supporting_utterance_span": str}``.
        Deduplication is by lowercased name. The first surface form seen
        becomes the display string for that key.
        """
        for e in entries:
            name = e.get("name") or ""
            key = _normalize(name)
            if not key:
                continue
            if key not in self.entities:
                self.entities[key] = EntityRecord(key=key, display=name)
                self.adjacency[key] = set()
            self.entities[key].provenance.append({
                "session_id": session_id,
                "turn_id": turn_id,
                "supporting_utterance_span": e.get("supporting_utterance_span", ""),
            })

    def add_relationships(self, entries: list[dict], session_id: int, turn_id: int) -> None:
        """Append directed labeled edges between extracted entities.

        Each ``entries[i]`` is ``{"entity_a", "label", "entity_b",
        "supporting_utterance_span"}``. Endpoints are auto-added as entities
        if not already present (so an extraction call that emits a
        relationship without explicitly listing both endpoints in
        ``entities`` still works).
        """
        for r in entries:
            a_name = r.get("entity_a") or ""
            b_name = r.get("entity_b") or ""
            label = (r.get("label") or "").strip()
            a_key, b_key = _normalize(a_name), _normalize(b_name)
            if not a_key or not b_key or a_key == b_key:
                continue
            for k, name in ((a_key, a_name), (b_key, b_name)):
                if k not in self.entities:
                    self.entities[k] = EntityRecord(key=k, display=name)
                    self.adjacency[k] = set()
            self.adjacency[a_key].add(b_key)
            self.adjacency[b_key].add(a_key)
            self.relationships.append(RelationshipRecord(
                entity_a_key=a_key, entity_b_key=b_key, label=label or "(unlabeled)",
                session_id=session_id, turn_id=turn_id,
                supporting_utterance_span=r.get("supporting_utterance_span", ""),
            ))

    # ----- retrieval -----------------------------------------------------

    def _score_entity_for_query(self, ent: EntityRecord, query_tokens: set[str]) -> float:
        """Lexical overlap between an entity's surface form and a query.

        Cheap and deterministic; sufficient for picking the seed entities
        for local-search retrieval. Embeddings would be a future upgrade.
        """
        if not query_tokens:
            return 0.0
        ent_tokens = set(ent.key.split()) | set(ent.display.lower().split())
        if not ent_tokens:
            return 0.0
        overlap = len(query_tokens & ent_tokens)
        # Mild bonus for multi-token entities (more specific match).
        return overlap + 0.1 * len(ent_tokens & query_tokens)

    def local_search(self, query: str, *, top_k_seeds: int = 3,
                     max_neighbors_per_seed: int = 4) -> dict:
        """Return the 1-hop neighborhood of query-relevant entities.

        Algorithm:
          1. Score every entity by lexical overlap with the query tokens.
          2. Keep the top ``top_k_seeds`` non-zero-score entities as seeds.
          3. For each seed, surface up to ``max_neighbors_per_seed`` neighbors
             along with the relationship records that connect them.

        Returns a dict shaped for direct rendering into a prompt block:

            {
                "seeds":         [{"name", "score", "provenance_count"}, ...],
                "neighborhoods": [
                    {
                        "seed": "<display>",
                        "neighbors": [
                            {"name": "<display>", "relationships": [
                                {"a", "label", "b", "session_id", "turn_id",
                                 "supporting_utterance_span"}, ...
                            ]}
                        ]
                    }
                ],
                "is_empty": bool
            }
        """
        if not self.entities:
            return {"seeds": [], "neighborhoods": [], "is_empty": True}

        q_tokens = {t for t in (query or "").lower().split() if len(t) > 2}
        scored = [
            (key, ent, self._score_entity_for_query(ent, q_tokens))
            for key, ent in self.entities.items()
        ]
        scored = [s for s in scored if s[2] > 0]
        scored.sort(key=lambda x: x[2], reverse=True)
        seed_records = scored[:top_k_seeds]
        if not seed_records:
            return {"seeds": [], "neighborhoods": [], "is_empty": True}

        out_seeds = [
            {"name": ent.display, "score": round(score, 2),
             "provenance_count": len(ent.provenance)}
            for _, ent, score in seed_records
        ]

        neighborhoods: list[dict] = []
        for seed_key, seed_ent, _ in seed_records:
            neighbor_keys = list(self.adjacency.get(seed_key, set()))[:max_neighbors_per_seed]
            neighbors = []
            for nk in neighbor_keys:
                nent = self.entities.get(nk)
                if nent is None:
                    continue
                rels = [
                    {
                        "a": self.entities[r.entity_a_key].display
                             if r.entity_a_key in self.entities else r.entity_a_key,
                        "b": self.entities[r.entity_b_key].display
                             if r.entity_b_key in self.entities else r.entity_b_key,
                        "label": r.label,
                        "session_id": r.session_id, "turn_id": r.turn_id,
                        "supporting_utterance_span": r.supporting_utterance_span,
                    }
                    for r in self.relationships
                    if (r.entity_a_key, r.entity_b_key) == (seed_key, nk)
                       or (r.entity_a_key, r.entity_b_key) == (nk, seed_key)
                ]
                neighbors.append({"name": nent.display, "relationships": rels})
            neighborhoods.append({"seed": seed_ent.display, "neighbors": neighbors})

        return {"seeds": out_seeds, "neighborhoods": neighborhoods, "is_empty": False}

    # ----- introspection / serialization --------------------------------

    def stats(self) -> dict:
        return {
            "num_entities":      len(self.entities),
            "num_relationships": len(self.relationships),
            "num_edges":         sum(len(s) for s in self.adjacency.values()) // 2,
        }

    def to_json_dict(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "entities": [
                {"key": e.key, "display": e.display, "provenance": list(e.provenance)}
                for e in self.entities.values()
            ],
            "relationships": [
                {"entity_a_key": r.entity_a_key, "entity_b_key": r.entity_b_key,
                 "label": r.label, "session_id": r.session_id, "turn_id": r.turn_id,
                 "supporting_utterance_span": r.supporting_utterance_span}
                for r in self.relationships
            ],
        }


# ---------------------------------------------------------------------------
# Module-level cache — one GraphRAGState per profile per process
# ---------------------------------------------------------------------------


_STATES: dict[str, GraphRAGState] = {}


def get_or_create_state(profile_id: str) -> GraphRAGState:
    """Return the shared GraphRAGState for ``profile_id``, creating one if
    none exists. The state lives in process memory, so it persists across
    the 4 sessions of a single profile run (which is what we want), but it
    is reset when a new ``python -m help_e.run`` process starts (also what
    we want — matches how v6's graph is reset on a fresh run).
    """
    if profile_id not in _STATES:
        _STATES[profile_id] = GraphRAGState(profile_id=profile_id)
    return _STATES[profile_id]


def reset_state(profile_id: Optional[str] = None) -> None:
    """Clear cached state. If ``profile_id`` is None, clear all. Useful for
    tests; not called from the main pipeline.
    """
    global _STATES
    if profile_id is None:
        _STATES = {}
    else:
        _STATES.pop(profile_id, None)


# ---------------------------------------------------------------------------
# Self-test (no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    g = GraphRAGState(profile_id="T")
    g.add_entities(
        [{"name": "weight gain", "supporting_utterance_span": "I gained weight"},
         {"name": "thyroid condition", "supporting_utterance_span": "thyroid"},
         {"name": "the gym", "supporting_utterance_span": "the gym"}],
        session_id=1, turn_id=1,
    )
    g.add_relationships(
        [{"entity_a": "thyroid condition", "entity_b": "weight gain",
          "label": "caused", "supporting_utterance_span": "thyroid → weight"},
         {"entity_a": "the gym", "entity_b": "weight gain",
          "label": "reminds of", "supporting_utterance_span": "gym mirror"}],
        session_id=1, turn_id=1,
    )

    assert g.stats()["num_entities"] == 3
    assert g.stats()["num_relationships"] == 2
    assert g.stats()["num_edges"] == 2

    # Lexical retrieval picks up the gym query and surfaces its neighbors.
    out = g.local_search("avoiding the gym now", top_k_seeds=2)
    assert not out["is_empty"]
    seed_names = [s["name"] for s in out["seeds"]]
    assert "the gym" in seed_names
    nhood = next(n for n in out["neighborhoods"] if n["seed"] == "the gym")
    assert any(nb["name"] == "weight gain" for nb in nhood["neighbors"])

    # Module-level cache.
    a = get_or_create_state("X")
    b = get_or_create_state("X")
    assert a is b
    reset_state("X")
    c = get_or_create_state("X")
    assert c is not a

    print("graphrag_state self-test PASSED")


if __name__ == "__main__":
    _self_test()
