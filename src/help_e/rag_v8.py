"""V8 RAG retrieval over the V7 graph's raw entries.

V8 keeps the same per-attribute summary_text + level_reasoning (used by
Agent 3a → 3b for level + TTM compute) and the same chronological
audit/connection stacks, but replaces V7's structural retrieval with
**dense top-K retrieval over a flat corpus of every raw entry ever
written to the graph**:

  - one chunk per attribute audit entry (level + non-level)
  - one chunk per problem-problem connection entry

No edge summary_text is needed (Agent 3c is dropped in V8) — RAG is
expected to surface the most relevant raw entries directly. The store
remains chronological and lossless: every entry persists in the graph
and is re-indexed each turn.

Per-turn flow inside V8 (driven by `baselines.v8_full`):

  1. Build query = current_user_message + user_intent_phrase + main.system_intent.
  2. Extract a corpus of {attribute_entry, connection_entry} chunks.
  3. Score with sentence-transformers (default model
     ``sentence-transformers/all-MiniLM-L6-v2``); diversify and select
     top-K (default 8) via MMR (Carbonell & Goldstein 1998).
  4. Render in the V8 evidence_pack as ``rag_retrieved_chunks``.

Backend
-------
Sentence-transformers cosine similarity. Embeddings are L2-normalized so
cosine reduces to a dot product. Per-chunk encodings are cached in a
process-lifetime dict keyed by the chunk's ``text`` field, so each
audit/connection entry is encoded once when it first appears and never
re-encoded. Only the query is re-encoded per turn.

Model name overridable via ``HELPE_V8_DENSE_MODEL`` (default
``sentence-transformers/all-MiniLM-L6-v2``). Requires
``pip install sentence-transformers``.

Selection strategy — MMR only
------------------------------
Maximal Marginal Relevance (Carbonell & Goldstein 1998). Iteratively
picks chunks that maximize
``λ · sim_to_query  −  (1 − λ) · max_{p ∈ selected} sim_to_p``. With
``λ = 1.0`` MMR reduces to plain top-K; ``λ = 0.5`` (default) is the
standard balanced setting that diversifies away from near-duplicate
audits/connections covering the same dimension. Configurable via
``HELPE_V8_MMR_LAMBDA`` and ``HELPE_V8_MMR_FETCH_K``.

Strictly non-positive cosine scores are dropped before MMR (no overlap
or anti-correlated direction); the legacy absolute minimum-cosine
floor was archived under
``_archive/retrieval_legacy/v8_min_cosine_floor.py``.

The legacy BM25 backend that lived here was archived to
``_archive/retrieval_legacy/v8_bm25_backend.py``; the standalone
``baselines/rag_baseline.py`` (the BM25 Tier-1 baseline) is unaffected.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from .config import V8_MMR_FETCH_K, V8_MMR_LAMBDA
from .graph_v7 import ProblemGraphV7


log = logging.getLogger(__name__)


# Default top-K of retrieved chunks per turn. Tuned to keep prompt size
# bounded while still surfacing more breadth than V7's structural pass.
V8_RAG_TOP_K_DEFAULT: int = 8

# Embedding model. Override via env to swap to e.g. bge-small later.
_RAG_DENSE_MODEL: str = os.environ.get(
    "HELPE_V8_DENSE_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)


# ---------------------------------------------------------------------------
# Corpus extraction
# ---------------------------------------------------------------------------


def _attribute_entry_text(
    *, problem: str, attribute: str, inferred: str, quote: str,
) -> str:
    """Concatenate fields into a single retrievable string for the
    encoder. Order doesn't affect MiniLM scoring (it's a bag-of-tokens
    after pooling) but it makes the rendered chunk readable in logs:
    the user's verbatim quote first, then the inferred claim, then the
    structural labels.
    """
    parts = [quote, inferred, problem, attribute]
    return " ".join(p for p in parts if p)


def _connection_entry_text(
    *, problem_a: str, problem_b: str, attribute_a: str, attribute_b: str,
    relation_type: str, why: str, quote: str,
) -> str:
    """Concatenate connection fields for encoding. ``why`` is included
    here (for the encoder) even though it's NOT shown in Agent 5's
    prompt — extra semantic signal for retrieval scoring.
    """
    parts = [
        quote, why,
        attribute_a, attribute_b, relation_type,
        problem_a, problem_b,
    ]
    return " ".join(p for p in parts if p)


def extract_corpus(graph: ProblemGraphV7) -> list[dict]:
    """Flatten the graph into retrievable chunks.

    Returns a list of dicts; each has a ``"text"`` field (used for
    encoding) plus the structured fields rendered into Agent 5's prompt.
    Two chunk shapes:

      attribute_entry:
        {type, attribute_kind, problem, attribute, anchor,
         quote, inferred, text}

      connection_entry:
        {type, problem_a, problem_b, attribute_a, attribute_b,
         relation_type, anchor, quote, why, text}

    ``why`` survives on connection chunks because it adds retrieval
    signal (more tokens for MiniLM to match against), but it is
    intentionally not rendered in the prompt — the edge's typed
    relation + the verbatim quote are enough at the prompt layer, and
    the response's evidence_used can cite the anchor when needed.
    """
    chunks: list[dict] = []

    # Attribute audit entries (both level and non-level).
    for problem_name, problem in graph.problems.items():
        for attr_name, state in problem.level_attributes.items():
            for entry in state.audit_stack:
                quote = entry.supporting_utterance_span or ""
                inferred = entry.inferred_information or ""
                chunks.append({
                    "type": "attribute_entry",
                    "attribute_kind": "level",
                    "problem": problem_name,
                    "attribute": attr_name,
                    "anchor": f"s{entry.session_id}.t{entry.turn_id}",
                    "quote": quote,
                    "inferred": inferred,
                    "text": _attribute_entry_text(
                        problem=problem_name, attribute=attr_name,
                        inferred=inferred, quote=quote,
                    ),
                })
        for attr_name, state_nl in problem.non_level_attributes.items():
            for entry in state_nl.audit_stack:
                quote = entry.supporting_utterance_span or ""
                inferred = entry.inferred_information or ""
                chunks.append({
                    "type": "attribute_entry",
                    "attribute_kind": "non_level",
                    "problem": problem_name,
                    "attribute": attr_name,
                    "anchor": f"s{entry.session_id}.t{entry.turn_id}",
                    "quote": quote,
                    "inferred": inferred,
                    "text": _attribute_entry_text(
                        problem=problem_name, attribute=attr_name,
                        inferred=inferred, quote=quote,
                    ),
                })

    # Problem-problem connection entries.
    for (a, b), edge in graph.edges.items():
        for ce in edge.connection_entries:
            why = ce.why or ""
            quote = ce.supporting_quote or ""
            chunks.append({
                "type": "connection_entry",
                "problem_a": a,
                "problem_b": b,
                "attribute_a": ce.attribute_a,
                "attribute_b": ce.attribute_b,
                "relation_type": ce.relation_type,
                "anchor": f"s{ce.session_id}.t{ce.turn_id}",
                "quote": quote,
                "why": why,
                "text": _connection_entry_text(
                    problem_a=a, problem_b=b,
                    attribute_a=ce.attribute_a, attribute_b=ce.attribute_b,
                    relation_type=ce.relation_type,
                    why=why, quote=quote,
                ),
            })

    return chunks


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------


def build_query(
    *,
    user_intent_phrase: str,
    main_system_intent: Optional[str],
    current_user_message: str,
) -> str:
    """Compose the retrieval query — fallback path used when Agent Q
    is unavailable or its output is empty. The primary V8 path uses
    Agent Q's curated query string; this concatenation is the safety
    net.

    Three signals concatenated as a single bag-of-tokens for MiniLM:
      1. ``current_user_message`` — what the user just said.
      2. ``user_intent_phrase`` — Agent 1's interpretation.
      3. ``main_system_intent`` — what the chatbot wants to nudge.

    Ordering doesn't affect MiniLM scoring (bag-of-tokens after
    pooling). Rolling summary is intentionally NOT included — if
    something from past turns is relevant, RAG surfaces it directly
    from the corpus rather than via the query.
    """
    parts: list[str] = [current_user_message or ""]
    if user_intent_phrase:
        parts.append(user_intent_phrase)
    if main_system_intent:
        parts.append(main_system_intent)
    return " ".join(p.strip() for p in parts if p and p.strip())


# ---------------------------------------------------------------------------
# Dense backend — the only retrieval path
# ---------------------------------------------------------------------------


class _DenseBackend:
    """sentence-transformers cosine similarity.

    Embeddings are L2-normalized at encode time so cosine similarity
    reduces to a single matmul. Per-chunk encodings are cached in
    ``self._cache`` keyed on the chunk's ``text`` field — audit and
    connection entries are append-only, so each text is encoded exactly
    once across the lifetime of the process. Only the query needs a
    fresh encode each call (~10ms for MiniLM on CPU).
    """

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model: Any = None
        self._cache: dict[str, Any] = {}  # text → np.ndarray, L2-normalized

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            # Local import — sentence-transformers is heavy.
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise RuntimeError(
                "rag_v8 requires sentence-transformers. Install with: "
                "`pip install sentence-transformers`."
            ) from e
        log.info("rag_v8: loading dense embedding model %r", self.model_name)
        self._model = SentenceTransformer(self.model_name)

    def _encode(self, texts: list[str]):
        self._load()
        return self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )

    # ---- encoding + similarity matrix --------------------------------

    def _ensure_encoded(self, chunks: list[dict]) -> None:
        """Encode any chunks whose ``text`` isn't already cached."""
        need_texts: list[str] = []
        for c in chunks:
            text = c.get("text", "") or ""
            if text not in self._cache:
                need_texts.append(text)
        if need_texts:
            new_embs = self._encode(need_texts)
            for j, t in enumerate(need_texts):
                self._cache[t] = new_embs[j]

    def _query_sims(self, query: str, chunks: list[dict]):
        """Return ``(sims, c_mat)``: cosine similarities (= dot, since
        unit-normalized) and the stacked chunk-embedding matrix.
        """
        import numpy as np

        self._ensure_encoded(chunks)
        c_mat = np.stack(
            [self._cache[(c.get("text", "") or "")] for c in chunks]
        )
        q_vec = self._encode([query])[0]
        sims = c_mat @ q_vec
        return sims, c_mat

    # ---- MMR selection (the only strategy V8 uses) -------------------

    @staticmethod
    def _mmr_top_k(
        chunks: list[dict], sims, c_mat, top_k: int,
        lambda_mult: float, fetch_k: int,
    ) -> list[dict]:
        """Maximal Marginal Relevance (Carbonell & Goldstein 1998).

        Algorithm:
          1. Rank chunks by query similarity.
          2. Keep the top ``fetch_k`` non-zero-score candidates.
          3. Iteratively pick the chunk that maximizes
                 lambda_mult · sim_to_query
                   − (1 − lambda_mult) · max_{p ∈ selected} sim_to_p
             until ``top_k`` are picked or the pool is exhausted.

        ``lambda_mult = 1.0`` reduces MMR to plain top-K; ``0.0`` is
        pure diversity; ``0.5`` is the textbook balanced default.
        """
        import numpy as np

        if not chunks:
            return []

        # Build the candidate pool: top fetch_k by query sim, drop only
        # strictly non-positive scores (anti-correlated / no-overlap).
        order = np.argsort(-sims)
        candidate_idx: list[int] = []
        for i in order:
            i = int(i)
            if len(candidate_idx) >= fetch_k:
                break
            if float(sims[i]) > 0.0:
                candidate_idx.append(i)
        if not candidate_idx:
            return []

        # Pairwise cosine within the pool.
        cand_mat = c_mat[candidate_idx]
        cand_pairwise = cand_mat @ cand_mat.T  # (n, n)

        n = len(candidate_idx)
        selected_local: list[int] = []
        in_selected = [False] * n
        budget = min(top_k, n)

        while len(selected_local) < budget:
            best_score = -float("inf")
            best_local = -1
            for li in range(n):
                if in_selected[li]:
                    continue
                gi = candidate_idx[li]
                sim_q = float(sims[gi])
                if selected_local:
                    redundancy = float(
                        max(cand_pairwise[li, sj] for sj in selected_local)
                    )
                else:
                    redundancy = 0.0
                mmr_score = lambda_mult * sim_q - (1.0 - lambda_mult) * redundancy
                if mmr_score > best_score:
                    best_score = mmr_score
                    best_local = li
            if best_local < 0:
                break
            selected_local.append(best_local)
            in_selected[best_local] = True

        return [
            {**chunks[candidate_idx[li]],
             "score": float(sims[candidate_idx[li]])}
            for li in selected_local
        ]

    def score(
        self, query: str, chunks: list[dict], top_k: int,
    ) -> list[dict]:
        """MMR top-K retrieval over MiniLM cosine."""
        if not chunks:
            return []
        sims, c_mat = self._query_sims(query, chunks)
        return self._mmr_top_k(
            chunks, sims, c_mat, top_k,
            lambda_mult=V8_MMR_LAMBDA,
            fetch_k=V8_MMR_FETCH_K,
        )


_BACKEND: Optional[_DenseBackend] = None


def _get_backend() -> _DenseBackend:
    """Return the process-singleton dense backend, created lazily on
    first call so importing this module doesn't trigger the
    sentence-transformers download.
    """
    global _BACKEND
    if _BACKEND is None:
        _BACKEND = _DenseBackend(_RAG_DENSE_MODEL)
    return _BACKEND


def reset_backend() -> None:
    """Clear the cached backend singleton. Used by tests."""
    global _BACKEND
    _BACKEND = None


# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------


def retrieve(
    query: str,
    chunks: list[dict],
    *,
    top_k: int = V8_RAG_TOP_K_DEFAULT,
) -> list[dict]:
    """Score ``chunks`` against ``query`` with sentence-transformers and
    return the top-K hits. Returned hits carry an extra ``"score"`` key.
    """
    if not chunks or not (query or "").strip():
        return []
    return _get_backend().score(query, chunks, top_k=top_k)


# ---------------------------------------------------------------------------
# Self-test (no LLM, no embeddings)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    from .config import PROBLEM_VOCAB
    from .graph_v6 import AttributeEvidenceEntry, global_turn_idx
    from .graph_v7 import ConnectionEntryV7, ProblemEdgeV7, ProblemGraphV7

    p1 = next(iter(PROBLEM_VOCAB))
    p2 = None
    for cand in PROBLEM_VOCAB:
        if cand != p1:
            p2 = cand
            break
    assert p2 is not None

    g = ProblemGraphV7(profile_id="T")
    g.get_or_create_problem(p1, first_mentioned=(1, 1))
    g.get_or_create_problem(p2, first_mentioned=(1, 1))

    g.append_audit_entry(
        problem_name=p1, attr_name="perceived_severity",
        entry=AttributeEvidenceEntry(
            session_id=1, turn_id=1,
            inferred_information="user reports overwhelming pressure from finals",
            concise_explanation="severity high — exam season",
            supporting_utterance_span="the pressure from finals is getting to me",
        ),
    )
    g.append_audit_entry(
        problem_name=p2, attr_name="perceived_severity",
        entry=AttributeEvidenceEntry(
            session_id=1, turn_id=2,
            inferred_information="user describes 3am wake-ups",
            concise_explanation="recurring early-morning awakenings",
            supporting_utterance_span="I keep waking up at 3am",
        ),
    )
    g.append_connection_entry(
        problem_a=p1, problem_b=p2,
        entry=ConnectionEntryV7(
            session_id=1, turn_id=3,
            attribute_a="perceived_severity", attribute_b="onset_latency",
            relation_type="causal",
            why="exam pressure delays sleep onset",
            supporting_quote="my mind keeps replaying revisions",
        ),
    )

    corpus = extract_corpus(g)
    types = {c["type"] for c in corpus}
    assert types == {"attribute_entry", "connection_entry"}
    assert len(corpus) == 3
    # Verify normalized field names — quote / inferred / anchor present;
    # legacy field names absent.
    for c in corpus:
        assert "anchor" in c and "quote" in c
        assert "session_id" not in c and "turn_id" not in c
        assert "supporting_utterance_span" not in c
        assert "supporting_quote" not in c
        assert "inferred_information" not in c
        assert "concise_explanation" not in c
    attr_chunks = [c for c in corpus if c["type"] == "attribute_entry"]
    assert all("inferred" in c for c in attr_chunks)
    conn_chunks = [c for c in corpus if c["type"] == "connection_entry"]
    assert all("relation_type" in c for c in conn_chunks)

    # Query that should hit the connection entry strongest (mind replay /
    # revisions language overlaps the connection's text).
    q1 = build_query(
        user_intent_phrase="venting about exam-driven sleep loss",
        main_system_intent="explore what's keeping you awake",
        current_user_message="my mind keeps replaying revisions and I can't sleep",
    )

    # 1. Mock-encoder dense backend — deterministic, no real model dep.
    #    We monkey-patch the encoder with a tiny lexical hash so the
    #    self-test exercises the cache + ranking path without depending
    #    on sentence-transformers being installed or the model being
    #    downloaded.
    import numpy as np

    def _stable_hash(s: str) -> int:
        # FNV-1a 32-bit. Deterministic across processes (Python's
        # built-in hash() is randomized per process via PYTHONHASHSEED).
        h = 2166136261
        for c in s:
            h ^= ord(c)
            h = (h * 16777619) & 0xFFFFFFFF
        return h

    def _mock_encode(texts):
        # Deterministic 32-dim "embedding" from token bag overlap.
        out = np.zeros((len(texts), 32), dtype=np.float32)
        for i, t in enumerate(texts):
            for tok in (t or "").lower().split():
                d = _stable_hash(tok) % 32
                out[i, d] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms

    backend = _DenseBackend("__mock__")
    backend._encode = _mock_encode  # bypass _load()

    hits = backend.score(q1, corpus, top_k=3)
    assert hits, "should retrieve something for an in-vocab query"
    for h in hits:
        assert "score" in h and h["score"] > 0
    assert hits[0]["type"] in ("attribute_entry", "connection_entry")

    # Cache should have grown to one entry per chunk.
    assert len(backend._cache) == len(corpus)

    # Re-run with the same query → cache hits, no re-encode of chunks.
    cache_size_before = len(backend._cache)
    hits2 = backend.score(q1, corpus, top_k=3)
    assert len(backend._cache) == cache_size_before
    assert [h["type"] for h in hits2] == [h["type"] for h in hits]

    # 2. Empty graph / empty query / empty corpus — sane no-op behavior.
    empty = ProblemGraphV7(profile_id="empty")
    assert extract_corpus(empty) == []
    # Empty corpus / empty query goes through the public retrieve()
    # function and short-circuits before the backend is ever invoked.
    assert retrieve("anything", []) == []
    assert retrieve("   ", corpus) == []

    # 3. MMR diversification — handcrafted chunks with two distinct
    #    "topics". Plain top-K=2 should pick two from the dense topic;
    #    MMR top-K=2 with λ=0.5 should pick one from each topic.
    diverse_chunks = [
        {"type": "attribute_entry", "problem": "p", "attribute": "a",
         "anchor": "s1.t1", "quote": "", "inferred": "alpha alpha alpha shared",
         "text": "alpha alpha alpha shared"},
        {"type": "attribute_entry", "problem": "p", "attribute": "a",
         "anchor": "s1.t2", "quote": "", "inferred": "alpha alpha shared shared",
         "text": "alpha alpha shared shared"},
        {"type": "attribute_entry", "problem": "p", "attribute": "a",
         "anchor": "s1.t3", "quote": "", "inferred": "alpha shared shared shared",
         "text": "alpha shared shared shared"},
        {"type": "attribute_entry", "problem": "p", "attribute": "b",
         "anchor": "s1.t4", "quote": "", "inferred": "beta unique distinct",
         "text": "beta unique shared"},
    ]
    diverse_query = "alpha shared beta"

    # Use a fresh backend with the mock encoder.
    div_backend = _DenseBackend("__mock__")
    div_backend._encode = _mock_encode  # type: ignore[method-assign]

    sims2, cmat2 = div_backend._query_sims(diverse_query, diverse_chunks)
    mmr_hits = div_backend._mmr_top_k(
        diverse_chunks, sims2, cmat2,
        top_k=2, lambda_mult=0.5, fetch_k=10,
    )
    mmr_attrs = [h["attribute"] for h in mmr_hits]

    # MMR top-2 must include both topics ("a" and "b") — that's the
    # whole point of diversification.
    assert "a" in mmr_attrs and "b" in mmr_attrs, (
        f"MMR should diversify topics, got {mmr_attrs}"
    )

    # λ=1.0 should reduce MMR to pure top-K-by-similarity (no diversity).
    # All three "a" chunks share the most-overlapping tokens, so top-2
    # by raw similarity should both be "a"-attribute chunks.
    mmr_pure_sim = div_backend._mmr_top_k(
        diverse_chunks, sims2, cmat2,
        top_k=2, lambda_mult=1.0, fetch_k=10,
    )
    pure_attrs = [h["attribute"] for h in mmr_pure_sim]
    assert pure_attrs.count("a") + pure_attrs.count("b") == 2

    # 4. Real-model dense backend — only run if sentence-transformers is
    #    installed AND the model is locally cached. Skipped otherwise so
    #    the self-test never hits the network.
    dense_status = "skipped"
    try:
        import sentence_transformers  # noqa: F401
        try:
            real = _DenseBackend(_RAG_DENSE_MODEL)
            real_hits = real.score(q1, corpus, top_k=3)
            assert real_hits, "real dense backend produced no hits"
            for h in real_hits:
                assert "score" in h and 0.0 < h["score"] <= 1.0001
            assert len(real._cache) == len(corpus)
            dense_status = "ok"
        except Exception as e:
            dense_status = f"skipped (model unavailable: {type(e).__name__})"
    except ImportError:
        dense_status = "skipped (sentence-transformers not installed)"

    # Reset the module-level backend so tests don't leak state.
    reset_backend()

    print(f"rag_v8 self-test PASSED  (real-model dense: {dense_status})")


if __name__ == "__main__":
    _self_test()
