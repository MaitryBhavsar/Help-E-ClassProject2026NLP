"""RAG baseline (v6-aligned).

Plain text-retrieval RAG over the multi-session transcript. The chatbot
side has no problem graph, no HBM attribute tracking, no TTM stage
inference, no MISC strategy gating. It only has:

  1. A BM25 index built from every prior turn in this profile's run
     (across sessions in the current process).
  2. The standard v6 response prompt (reused via the same schema and
     validators as `instruction_response_simple`), augmented with a
     RETRIEVED EVIDENCE block that holds the top-K BM25 hits.

Per-turn cost: 1 LLM call (response generation only). The BM25 index is
pure Python — no LLM call.

This file is purely additive and does NOT modify any existing baseline,
prompt, graph, simulator, evaluator, driver, UI, or config code.
"""
from __future__ import annotations

import logging
import math
import re
import textwrap
from typing import Optional

from ..config import (
    INTENT_ENTRY_STYLE_V6,
    LAST_N_TURNS,
    MISC_CODES,
    USER_INTENTS_V6,
)
from ..graph_v6 import ProblemGraphV6
from ..llm_client import CallContext, LLMClient


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level multi-session transcript cache
# ---------------------------------------------------------------------------
#
# The session driver gives us per-call `recent_turns` (a small recency
# window). For RAG to retrieve content from EARLIER sessions, we also
# need the full prior transcript. We accumulate it here, keyed by
# profile_id. This is process-local: a single `python -m help_e.run`
# invocation that runs all 4 sessions for a profile in sequence will
# build up the cache as turns happen, and the cache disappears when the
# process exits — the same lifecycle as v6's in-memory graph.
# ---------------------------------------------------------------------------

_TRANSCRIPTS: dict[str, list[dict]] = {}


def _record_turn(profile_id: str, role: str, session_id: int, turn_id: int,
                 text: str) -> None:
    if not text:
        return
    _TRANSCRIPTS.setdefault(profile_id, []).append({
        "role": role, "session_id": session_id, "turn_id": turn_id, "text": text,
    })


def _full_prior_transcript(profile_id: str) -> list[dict]:
    """Snapshot of every turn this profile has produced so far in the
    current process, oldest first. Returns a copy so callers cannot
    accidentally mutate the cache.
    """
    return list(_TRANSCRIPTS.get(profile_id, []))


def reset_transcript_cache(profile_id: Optional[str] = None) -> None:
    """Clear the cached transcript. Useful for tests; not used by the main
    pipeline.
    """
    global _TRANSCRIPTS
    if profile_id is None:
        _TRANSCRIPTS = {}
    else:
        _TRANSCRIPTS.pop(profile_id, None)


# ---------------------------------------------------------------------------
# BM25 (lightweight in-house implementation; no new dependency)
# ---------------------------------------------------------------------------
#
# Standard BM25 with Okapi defaults (k1=1.5, b=0.75). Implemented inline
# to avoid adding `rank_bm25` to the project's dependency surface.
# ---------------------------------------------------------------------------


_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")
_STOPWORDS = frozenset({
    "a", "an", "and", "the", "am", "is", "are", "was", "were", "be", "been",
    "being", "to", "of", "in", "on", "at", "by", "for", "with", "from",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us",
    "them", "my", "your", "his", "its", "our", "their", "this", "that",
    "these", "those", "do", "does", "did", "have", "has", "had", "will",
    "would", "could", "should", "may", "might", "can", "but", "or",
    "if", "then", "so", "as", "than", "not", "no", "yes",
})


def _tokenize(text: str) -> list[str]:
    return [t for t in (m.group(0).lower() for m in _TOKEN_RE.finditer(text or ""))
            if t not in _STOPWORDS and len(t) > 1]


def _bm25_top_k(query: str, candidates: list[dict], k: int,
                k1: float = 1.5, b: float = 0.75) -> list[dict]:
    """Rank ``candidates`` by BM25 similarity to ``query`` and return the top
    K candidates. Each candidate dict needs at least a ``"text"`` field.
    Candidates with score 0 are dropped (BM25 score is 0 iff no query
    tokens appear in the candidate, in which case it's irrelevant).
    """
    if not candidates:
        return []

    tokenized_docs = [_tokenize(c["text"]) for c in candidates]
    doc_lens = [max(1, len(d)) for d in tokenized_docs]
    avgdl = sum(doc_lens) / len(doc_lens)

    # Document frequency: in how many docs each query term appears.
    q_terms = _tokenize(query)
    if not q_terms:
        return []
    df: dict[str, int] = {}
    for term in set(q_terms):
        df[term] = sum(1 for d in tokenized_docs if term in d)

    n_docs = len(tokenized_docs)
    scores: list[float] = []
    for doc, dlen in zip(tokenized_docs, doc_lens):
        # Term frequency in this doc.
        tf: dict[str, int] = {}
        for t in doc:
            if t in df:  # only score query terms
                tf[t] = tf.get(t, 0) + 1
        score = 0.0
        for term, ft in tf.items():
            n_q = df[term]
            # Robertson IDF: log((N - n + 0.5) / (n + 0.5) + 1).
            idf = math.log(((n_docs - n_q + 0.5) / (n_q + 0.5)) + 1.0)
            num = ft * (k1 + 1)
            den = ft + k1 * (1 - b + b * (dlen / avgdl))
            score += idf * (num / den)
        scores.append(score)

    ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
    return [c for c, s in ranked[:k] if s > 0.0]


# ---------------------------------------------------------------------------
# Prompts (reuse the simple system prompt; build a custom user prompt with a
# RETRIEVED EVIDENCE block)
# ---------------------------------------------------------------------------


from ..instruction_response_simple import (   # noqa: E402  (circular-safe)
    RESPONSE_SIMPLE_SCHEMA,
    _fallback_response,
    _validate_factory,
    build_system_prompt as _build_simple_system_prompt,
)
from ..instruction_response_v6 import _persona_to_dict   # noqa: E402
from ..prompts.common_v6 import (   # noqa: E402
    format_candidate_strategies_block,
    format_past_two_turns_block_v6,
    format_persona_block_v6,
    format_recent_turns_block_v6,
    format_user_intent_block_v6,
)


def _format_retrieved_block(retrieved: list[dict]) -> str:
    """Render the BM25 hits as a readable block under EVIDENCE."""
    if not retrieved:
        return "  (no prior turns in memory yet)"
    lines: list[str] = []
    for r in retrieved:
        sid = r.get("session_id")
        tid = r.get("turn_id")
        role = (r.get("role") or "").upper()
        text = (r.get("text") or "").strip().replace("\n", " ")
        loc = f"s{sid}t{tid}" if sid is not None and tid is not None else "?"
        lines.append(f"  [{loc} {role}]: {text}")
    return "\n".join(lines)


def _build_rag_user_prompt(
    *,
    graph: ProblemGraphV6,
    candidate_bundle: dict,
    past_two_turns: list[dict],
    recent_turns: list[dict],
    retrieved: list[dict],
    current_user_message: str,
) -> str:
    """User prompt that mirrors response_simple's, but adds a RETRIEVED
    EVIDENCE block. The simple system prompt already contains the schema
    and the banned-opener instructions; we just hand it a bundle that
    looks shape-compatible.
    """
    persona_dict = _persona_to_dict(graph.persona)
    persona_block = format_persona_block_v6(persona_dict)
    intent_block = format_user_intent_block_v6(
        candidate_bundle.get("user_intent"),
        candidate_bundle.get("intent_entry_style"),
    )
    candidate_block = format_candidate_strategies_block(
        common=candidate_bundle.get("common_candidates") or [],
        stage_specific=candidate_bundle.get("stage_specific_candidates") or [],
        ttm_stage=candidate_bundle.get("ttm_stage"),
        transition_target=candidate_bundle.get("transition_target"),
    )
    recent_block = format_recent_turns_block_v6(recent_turns)
    past_two_block = format_past_two_turns_block_v6(past_two_turns)

    return textwrap.dedent(f"""\
        # PERSONA
        {persona_block}

        # USER_INTENT
        {intent_block}

        # CANDIDATE STRATEGIES
        {candidate_block}

        # EVIDENCE

        ## Relevant problems
          (no graph in this baseline — RAG retrieves prior text instead)

        ## Retrieved excerpts (top BM25 hits over the multi-session transcript)
        {_format_retrieved_block(retrieved)}

        # RECENT TURNS
        {recent_block}

        # PAST TWO TURNS
        {past_two_block}

        # CURRENT USER MESSAGE
          {current_user_message!r}
    """).rstrip()


# ---------------------------------------------------------------------------
# Helpers shared with v1 (free MISC vocabulary, lightweight intent guess)
# ---------------------------------------------------------------------------


def _flat_candidate_bundle(user_intent: str) -> dict:
    """Same shape as v1's: ALL 10 MISC codes available, no TTM stage."""
    if user_intent not in USER_INTENTS_V6:
        user_intent = "small_talk"
    common = [
        {
            "code": code,
            "label": spec["label"],
            "what": spec["what"],
            "transition_fn": spec["transition_fn"],
        }
        for code, spec in MISC_CODES.items()
    ]
    return {
        "main_problem": None,
        "ttm_stage": None,
        "transition_target": None,
        "user_intent": user_intent,
        "intent_entry_style": INTENT_ENTRY_STYLE_V6[user_intent],
        "common_candidates": common,
        "stage_specific_candidates": [],
        "all_candidate_codes": [c["code"] for c in common],
    }


def _guess_intent_from_message(msg: str) -> str:
    """Same lightweight heuristic v1 uses; no LLM call."""
    if not msg:
        return "small_talk"
    low = msg.lower()
    if low.strip() in ("hi", "hey", "hello", "thanks", "thank you", "bye"):
        return "small_talk"
    if any(t in low for t in ("is that ok", "is it normal", "am i wrong", "right?")):
        return "seek_validation"
    if any(t in low for t in ("don't want", "won't", "not going to", "drop it", "stop")):
        return "resistance"
    if any(t in low for t in ("on one hand", "on the other", "torn", "i can't decide")):
        return "deliberate_decision"
    if any(t in low for t in ("i tried", "yesterday i", "last week i", "i started")):
        return "report_action"
    if any(t in low for t in ("plan", "next step", "what should i do", "should i")):
        return "request_plan"
    if any(t in low for t in ("?", "what do you", "how do i", "any tips")):
        return "seek_information"
    return "express_emotion"


# ---------------------------------------------------------------------------
# Turn function
# ---------------------------------------------------------------------------


# Number of older items to retrieve in addition to the recent-N window.
RAG_TOP_K: int = 3


def rag_turn_fn(
    *,
    client: LLMClient,
    profile_id: str,
    system: str = "rag",
    session_id: int,
    turn_id: int,
    user_message: str,
    recent_turns: list[dict],
    last_system_message: Optional[str] = None,
    prior_session_summary: Optional[str] = None,    # unused
    graph: ProblemGraphV6,                           # accepted but unused
    last_n_turns: int = LAST_N_TURNS,
    previous_turn_traces: Optional[list[dict]] = None,
) -> dict:
    """One RAG turn. Same signature as ``v6_turn_fn`` so the v6 driver
    can call either interchangeably.

    Steps:
      1. Append (just-arrived user message) to the module-level transcript
         cache so future turns can retrieve it.
      2. Build BM25 candidates from the prior transcript, EXCLUDING the
         last LAST_N_TURNS (those are injected via the recent-turns block
         to avoid double-counting).
      3. Retrieve top-K hits.
      4. Build the user prompt with a RETRIEVED EVIDENCE block.
      5. Call the response LLM with `call_role="response_rag"`.
      6. Append the assistant's response back into the transcript cache.
      7. Emit a v6-shape turn_result so judges and metrics work uniformly.
    """
    # 1. Cache the user turn for future retrieval (do this BEFORE retrieval
    #    so a later retrieval at this turn can technically see it — though
    #    since we exclude the recent window, that doesn't matter).
    _record_turn(profile_id, "user", session_id, turn_id, user_message)

    # 2 + 3. Retrieve top-K from older transcript.
    full_prior = _full_prior_transcript(profile_id)
    # Exclude the last LAST_N_TURNS items from BM25 candidates so the
    # recent-turns block doesn't get re-surfaced under "Retrieved".
    if len(full_prior) > last_n_turns:
        candidates = full_prior[:-last_n_turns]
    else:
        candidates = []
    retrieved = _bm25_top_k(query=user_message, candidates=candidates, k=RAG_TOP_K)

    # 4. Build prompt.
    user_intent = _guess_intent_from_message(user_message)
    candidate_bundle = _flat_candidate_bundle(user_intent)

    # Past-two-turns hint — reuse v6_full's collector. RAG's prior traces
    # have the same v1-shape (chosen_misc_codes from reasoning), so it works.
    from .v6_full import _collect_past_two_turns
    past_two_turns = _collect_past_two_turns(previous_turn_traces or [])

    rsp_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="response_rag",
    )

    candidate_codes = set(candidate_bundle.get("all_candidate_codes") or [])
    user_prompt = _build_rag_user_prompt(
        graph=graph, candidate_bundle=candidate_bundle,
        past_two_turns=past_two_turns, recent_turns=recent_turns,
        retrieved=retrieved, current_user_message=user_message,
    )

    # 5. Generate response.
    try:
        response_out = client.generate_structured(
            ctx=rsp_ctx,
            system_prompt=_build_simple_system_prompt(),
            user_prompt=user_prompt,
            schema=RESPONSE_SIMPLE_SCHEMA,
            validator_extras=_validate_factory(candidate_codes, user_intent),
        )
    except Exception as e:
        log.warning("response_rag failed (%s); using fallback", e)
        response_out = _fallback_response(user_intent)

    # 6. Cache the assistant's reply for future retrieval.
    final_text = (response_out or {}).get("final_response", "")
    _record_turn(profile_id, "assistant", session_id, turn_id, final_text)

    # 7. v6-shape trace so loaders + metrics work uniformly.
    from .v6_full import _extract_misc_codes_from_reasoning
    chosen_misc_codes = _extract_misc_codes_from_reasoning(
        response_out.get("reasoning", "") or ""
    )
    trace = {
        "main_problem": None,
        "current_problems": [],
        "user_intent": user_intent,
        "ttm_stage": None,
        "transition_target": None,
        "all_candidate_codes": candidate_bundle["all_candidate_codes"],
        "chosen_misc_codes": chosen_misc_codes,
        "turn_scope_level_attrs": [],
        "level_updates": [],
        "ttm_updates": [],
        "cooc_added": 0,
        "attr_conn_added": 0,
        # RAG-specific bookkeeping (extra fields are tolerated by loaders).
        "rag_retrieved_count": len(retrieved),
    }

    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "user_message": user_message,
        "inference": {
            "user_intent": {"intent": user_intent, "confidence": "low",
                            "explanation": "rag heuristic", "supporting_utterance_span": None},
            "current_problems": [], "main_problem": None,
            "problem_attribute_entries": [],
            "problem_cooccurrence_connections": [],
            "problem_attribute_connections": [],
            "_rag_no_extraction": True,
        },
        "recompute": {
            "attribute_level_updates": [],
            "ttm_stage_updates": [],
            "_rag_no_recompute": True,
        },
        "bundle": None,
        "candidate_bundle": candidate_bundle,
        "past_two_turns": past_two_turns,
        "response": response_out,
        "trace": trace,
        "rag_retrieved": retrieved,
    }


# ---------------------------------------------------------------------------
# Self-test (no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    # Tokenizer drops stopwords + short words.
    toks = _tokenize("I am feeling so tired and stressed about exams")
    assert "tired" in toks and "stressed" in toks and "exams" in toks
    assert "i" not in toks and "am" not in toks

    # BM25: a query about exams should retrieve the exam-related candidate.
    cands = [
        {"text": "We talked about your exams last week."},
        {"text": "I bought groceries on Sunday."},
        {"text": "The meeting with your sister went well."},
    ]
    top = _bm25_top_k("how have my exams been going", cands, k=2)
    assert top[0]["text"].startswith("We talked about your exams")

    # Empty candidate list → empty retrieval, no crash.
    assert _bm25_top_k("anything", [], k=3) == []

    # Empty query → no false positives.
    assert _bm25_top_k("", cands, k=3) == []

    # Retrieved-block formatter handles empty + populated cases.
    assert "(no prior turns" in _format_retrieved_block([])
    block = _format_retrieved_block([
        {"role": "user", "session_id": 1, "turn_id": 3,
         "text": "I cannot sleep at all."}
    ])
    assert "[s1t3 USER]" in block

    # Module-level transcript cache.
    reset_transcript_cache()
    _record_turn("T", "user", 1, 1, "hello")
    _record_turn("T", "assistant", 1, 1, "hi back")
    snap = _full_prior_transcript("T")
    assert len(snap) == 2 and snap[0]["text"] == "hello"
    snap.clear()  # snapshot is a copy — must not affect cache
    assert len(_full_prior_transcript("T")) == 2

    # Candidate bundle parity with v1.
    cb = _flat_candidate_bundle("express_emotion")
    assert len(cb["common_candidates"]) == 10
    assert cb["main_problem"] is None and cb["ttm_stage"] is None

    print("rag_baseline self-test PASSED")


if __name__ == "__main__":
    _self_test()
