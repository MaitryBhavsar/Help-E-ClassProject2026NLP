"""End-to-end smoke test for the V7 + V8 turn functions using a mock LLM.

Both turn functions share Agents 1, 2, 3a, 3b, X. V7 also uses Agent 3c
(edge summary). V8 swaps Agent 4's graph-walk for MiniLM dense
retrieval. This smoke test exercises **one full turn through each**
without any network or LLM calls:

  - A ``_MockClient`` returns canned, schema-valid JSON for every
    ``call_role`` either pipeline issues.
  - The V8 dense backend's encoder is monkey-patched with a
    deterministic 32-dim hash so MiniLM (and ``sentence-transformers``)
    is not required.

Verifies for V7:
  - All seven phase types fire (Agents 1, 2, 3a, 3c, 3b, 5, X).
  - The graph state mutates as expected: problems registered, audit
    stacks grown, edge created with summary_text.
  - ``evidence_pack`` carries the V7 shape: main_problem with attribute
    summaries, other_current_problems 1-line, problem_problem_connections
    with summary_text + n_entries.

Verifies for V8:
  - Agents 1, 2, 3a, 3b, 5, X fire — Agent 3c does NOT.
  - ``evidence_pack`` carries the V8 shape: main_problem with
    current_levels (no summary_text), other_current_problems 1-line,
    rag_retrieved_chunks (with score), rag_query, rag_corpus_size.
  - The retrieval ran against a non-empty corpus.

Usage:
    PYTHONPATH=src python3 -m help_e.eval.smoke_v7_v8
"""
from __future__ import annotations

import sys
import traceback
from typing import Any


# ---------------------------------------------------------------------------
# Canned LLM outputs — all schema-valid for the relevant agents
# ---------------------------------------------------------------------------


_AGENT1_OUT = {
    "user_intent": "express_emotion",
    "user_intent_phrase": "venting about exam-driven sleep loss",
    "mi_for_user_intent": "complex_reflection",
}


def _agent2_out_main(main_name: str) -> dict:
    return {
        "current_problems": [
            {"problem_name": "academic_pressure", "explanation": "exam stress",
             "supporting_utterance_span": "finals"},
            {"problem_name": "sleep_problems", "explanation": "two days no sleep",
             "supporting_utterance_span": "haven't slept"},
        ],
        "main_problem": {
            "problem_name": main_name,
            "explanation": "user named it as the immediate concern",
            "supporting_utterance_span": main_name,
        },
        "problem_attribute_entries": [
            {"problem_name": "sleep_problems",
             "attribute_name": "perceived_severity",
             "inferred_information": "two days without sleep",
             "concise_explanation": "severity high — duration",
             "supporting_utterance_span": "haven't slept in two days"},
            {"problem_name": "academic_pressure",
             "attribute_name": "triggers",
             "inferred_information": "finals approaching",
             "concise_explanation": "exam deadline",
             "supporting_utterance_span": "finals"},
        ],
        "problem_attribute_connections": [
            {"problem_1": "academic_pressure", "problem_2": "sleep_problems",
             "attribute_1": "triggers", "attribute_2": "perceived_severity",
             "relation_type": "causal",
             "connection_explanation": "exam stress disrupts sleep onset",
             "supporting_utterance_span": "finals"},
        ],
    }


def _agent3a_out(problem_name: str) -> dict:
    if problem_name == "sleep_problems":
        return {
            "problem_name": "sleep_problems",
            "attribute_updates": [
                {"attribute_name": "perceived_severity",
                 "summary_text": "s1.t1: two days without sleep — severity high.",
                 "current_level": "high",
                 "level_reasoning": "duration claim is unambiguous",
                 "level_change_confidence": "high",
                 "new_info_useful": 1},
            ],
        }
    if problem_name == "academic_pressure":
        return {
            "problem_name": "academic_pressure",
            "attribute_updates": [
                {"attribute_name": "triggers",
                 "summary_text": "s1.t1: finals approaching as proximal trigger.",
                 "current_level": "unknown",
                 "level_reasoning": "",
                 "level_change_confidence": "low",
                 "new_info_useful": 1},
            ],
        }
    raise KeyError(problem_name)


_AGENT3B_OUT_SLEEP = {
    "problem_name": "sleep_problems",
    "new_ttm_stage": "contemplation",
    "ttm_reasoning": "user reports the problem; no plan yet",
    "system_intent": "explore what's keeping you awake before pushing for a plan",
    "mi_for_system_intent": "complex_reflection",
}


def _agent3c_out(problem_1: str, problem_2: str) -> dict:
    # Canonical (alphabetical) ordering — academic_pressure < sleep_problems
    a, b = sorted([problem_1, problem_2])
    return {
        "problem_1": a,
        "problem_2": b,
        "summary_text": (
            "s1.t1: exam stress (academic) disrupts sleep onset — "
            'user said "finals" and "haven\'t slept in two days".'
        ),
        "useful": 1,
    }


def _agent5_v7_out() -> dict:
    return {
        "reasoning": "User vented about exam-driven sleep loss. Reflect underneath; do not problem-solve. Hold the nudge for now.",
        "mi_for_user_intent_used": "complex_reflection",
        "mi_for_system_intent_used": "complex_reflection",
        "r1": "Two days running on nothing — that's exhausting.",
        "r2": "Two days running on nothing — that's exhausting. What's the loop in your head when you lie down?",
        "r3": "Two days running on nothing — that's exhausting. What's the loop in your head when you lie down?",
        "final_response": "Two days running on nothing — that's exhausting. What's the loop in your head when you lie down?",
        "used_evidence": False,
        "evidence_used": [],
    }


def _agent5_v8_out() -> dict:
    # Same shape as v7 — V8 reuses the V7 schema.
    return _agent5_v7_out()


_AGENTX_OUT = {
    "rolling_summary_5turns":
        "s1.t1: user vented about not having slept in two days because of finals; "
        "bot reflected exhaustion and asked an open question about bedtime rumination.",
}

# V3-specific canned outputs ----------------------------------------


def _agent2_v3_out_main(main_name: str) -> dict:
    """V3 InferenceAgent canned output — no attributes; per-problem
    evidence + relation_type-only connections.
    """
    return {
        "current_problems": [
            {"problem_name": "academic_pressure", "explanation": "exam stress",
             "supporting_utterance_span": "finals"},
            {"problem_name": "sleep_problems", "explanation": "two days no sleep",
             "supporting_utterance_span": "haven't slept"},
        ],
        "main_problem": {
            "problem_name": main_name,
            "explanation": "user named it as the immediate concern",
            "supporting_utterance_span": main_name,
        },
        "problem_evidence": [
            {"problem_name": "sleep_problems",
             "inferred_information": "user reports two days without sleep",
             "why": "duration claim implies severity",
             "supporting_utterance_span": "haven't slept in two days"},
            {"problem_name": "academic_pressure",
             "inferred_information": "finals approaching as proximal trigger",
             "why": "the deadline is the named pressure",
             "supporting_utterance_span": "finals"},
        ],
        "problem_problem_connections": [
            {"problem_1": "academic_pressure", "problem_2": "sleep_problems",
             "relation_type": "causal",
             "why": "exam stress disrupts sleep onset",
             "supporting_utterance_span": "finals"},
        ],
    }


def _agent3_problem_v3_out(problem_name: str) -> dict:
    if problem_name == "sleep_problems":
        return {
            "problem_name": "sleep_problems",
            "summary_text": "s1.t1: user reports two days without sleep — clear "
                            "pressure on the body; not yet weighing options.",
            "current_ttm_stage": "contemplation",
            "ttm_reasoning": "user named the problem; not planning yet",
            "ttm_change_confidence": "high",
            "system_intent": "explore what's keeping you awake without pushing",
            "mi_for_system_intent": "complex_reflection",
            "useful": 1,
        }
    if problem_name == "academic_pressure":
        return {
            "problem_name": "academic_pressure",
            "summary_text": "s1.t1: finals approaching is the proximal trigger.",
            "current_ttm_stage": "precontemplation",
            "ttm_reasoning": "user mentioned finals only as context",
            "ttm_change_confidence": "low",
            "system_intent": "name the deadline weight without pushing for a plan",
            "mi_for_system_intent": "support",
            "useful": 1,
        }
    raise KeyError(problem_name)


_AGENT5_V3_OUT = {
    "reasoning": "User vented about exam-driven sleep loss. Reflect underneath; "
                 "do not problem-solve.",
    "mi_for_user_intent_used": "complex_reflection",
    "mi_for_system_intent_used": "complex_reflection",
    "r1": "Two days running on nothing — that's exhausting.",
    "r2": "Two days running on nothing — that's exhausting. What's the loop "
          "in your head when you lie down?",
    "r3": "Two days running on nothing — that's exhausting. What's the loop "
          "in your head when you lie down?",
    "final_response": "Two days running on nothing — that's exhausting. What's "
                      "the loop in your head when you lie down?",
    "used_evidence": False,
    "evidence_used": [],
}


_AGENTQ_OUT = {
    # Mock-encoder-friendly: shares enough tokens with the canned corpus
    # to produce non-zero cosine scores under the 32-dim hash mock.
    # Real QueryAgent output will be more expansive (see its prompt's
    # examples) but this is what we test against deterministically.
    "retrieval_query": (
        "exam stress sleep finals two days severity triggers "
        "academic_pressure sleep_problems"
    ),
}


# ---------------------------------------------------------------------------
# MockClient
# ---------------------------------------------------------------------------


class _MockClient:
    """Returns canned schema-valid JSON for every V7/V8 call_role."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []

    def generate_structured(
        self, *, ctx, system_prompt, user_prompt, schema, validator_extras=None,
    ) -> dict:
        role = ctx.call_role
        self.calls.append((role, ctx.session_id, ctx.turn_id))

        if role == "agent1_user_intent":
            out = dict(_AGENT1_OUT)
        elif role == "agent2_inference_v7":
            # Pick main from the user's recent message context — for
            # this smoke we hardcode sleep_problems as main.
            out = _agent2_out_main("sleep_problems")
        elif role == "agent2_inference_v3":
            out = _agent2_v3_out_main("sleep_problems")
        elif role == "agent3_problem_v3":
            problem_name = "sleep_problems"
            for line in user_prompt.splitlines():
                line = line.strip()
                if line.startswith("PROBLEM:"):
                    parts = line.split(":", 1)[1].strip()
                    # PROBLEM: sleep_problems  (MAIN PROBLEM)
                    problem_name = parts.split("(", 1)[0].strip()
                    break
            out = _agent3_problem_v3_out(problem_name)
        elif role == "agent3a_attr_update":
            # Per-problem call; the mock infers the problem from the
            # user_prompt header. We look for the "PROBLEM:" line.
            problem_name = "sleep_problems"
            for line in user_prompt.splitlines():
                line = line.strip()
                if line.startswith("PROBLEM:"):
                    problem_name = line.split(":", 1)[1].strip()
                    break
            out = _agent3a_out(problem_name)
        elif role == "agent3b_ttm_intent":
            # Only sleep_problems triggers 3b in this smoke (severity
            # changed unknown → high).
            out = dict(_AGENT3B_OUT_SLEEP)
        elif role == "agent3c_edge_summary":
            # Pull endpoints from the EDGE: header in the user prompt.
            p1, p2 = "academic_pressure", "sleep_problems"
            for line in user_prompt.splitlines():
                line = line.strip()
                if line.startswith("EDGE:"):
                    parts = line.split(":", 1)[1].strip()
                    if "↔" in parts:
                        a, b = [s.strip() for s in parts.split("↔", 1)]
                        p1, p2 = a, b
                    break
            out = _agent3c_out(p1, p2)
        elif role == "agent5_response_v3":
            out = dict(_AGENT5_V3_OUT)
        elif role == "agent5_response_v7":
            out = _agent5_v7_out()
        elif role == "agent5_response_v8":
            out = _agent5_v8_out()
        elif role == "agent5_response_v1":
            # V1 schema is V7's collapsed to {reasoning,
            # mi_for_user_intent_used, r1, final_response}.
            out = {
                "reasoning": "1) surface vent. 2) reflect. 3) brief.",
                "mi_for_user_intent_used": "complex_reflection",
                "r1": "Two days running on nothing — that's exhausting.",
                "final_response": "Two days running on nothing — that's exhausting.",
            }
        elif role == "agentX_rolling_summary":
            out = dict(_AGENTX_OUT)
        elif role == "agentq_retrieval_query":
            out = dict(_AGENTQ_OUT)
        else:
            raise KeyError(f"MockClient: no canned response for role {role!r}")

        if validator_extras is not None:
            validator_extras(out)
        return out


# ---------------------------------------------------------------------------
# Mock dense backend for V8 (no sentence-transformers / no network)
# ---------------------------------------------------------------------------


def _install_mock_dense_backend() -> None:
    """Replace the V8 dense backend with a deterministic 32-dim hash
    encoder. Lets ``v8_turn_fn`` exercise the full retrieval path
    without ``sentence-transformers``.
    """
    import numpy as np

    from help_e import rag_v8

    rag_v8.reset_backend()
    backend = rag_v8._DenseBackend("__smoke_mock__")

    def _stable_hash(s: str) -> int:
        # FNV-1a 32-bit; deterministic across processes (Python's
        # built-in hash() randomizes via PYTHONHASHSEED).
        h = 2166136261
        for c in s:
            h ^= ord(c)
            h = (h * 16777619) & 0xFFFFFFFF
        return h

    def _mock_encode(texts):
        out = np.zeros((len(texts), 32), dtype=np.float32)
        for i, t in enumerate(texts):
            for tok in (t or "").lower().split():
                d = _stable_hash(tok) % 32
                out[i, d] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms

    backend._encode = _mock_encode  # bypass _load()
    rag_v8._BACKEND = backend


# ---------------------------------------------------------------------------
# Per-system smoke runners
# ---------------------------------------------------------------------------


def _run_v1_smoke() -> dict[str, Any]:
    from help_e.baselines.v1_full import v1_turn_fn
    from help_e.graph_v3 import ProblemGraphV3

    graph = ProblemGraphV3(profile_id="SMOKE_V1")
    client = _MockClient()
    result = v1_turn_fn(
        client=client, profile_id="SMOKE_V1", system="v1",
        session_id=1, turn_id=1,
        user_message="I haven't slept in two days because of finals. I just lie there.",
        recent_turns=[],
        previous_turn_traces=None,
        graph=graph,
    )

    trace = result["trace"]
    # V1 has no problems, no graph mutation.
    assert trace["main_problem"] is None
    assert trace["current_problems"] == []
    assert trace["ttm_stage"] is None
    assert trace["system_intent"] is None

    # Graph stays empty.
    assert graph.problems == {}
    assert graph.edges == {}

    # Response is well-shaped (R1 + final_response only — no R2/R3).
    response = result["response"]
    assert "r1" in response and "final_response" in response
    assert "r2" not in response
    assert "r3" not in response
    assert "evidence_used" not in response

    # Rolling summary was updated by Agent X.
    assert graph.rolling_summary_5turns != ""

    # Roles called: only Intent + Response + RollingSummary.
    roles_called = {r for (r, _, _) in client.calls}
    assert roles_called == {
        "agent1_user_intent",
        "agent5_response_v1",
        "agentX_rolling_summary",
    }, f"V1: unexpected roles called: {roles_called}"

    return {
        "system": "v1",
        "n_llm_calls": len(client.calls),
        "diagnostics": result["diagnostics"],
        "rolling_summary_set": bool(graph.rolling_summary_5turns),
    }


def _run_v3_smoke() -> dict[str, Any]:
    from help_e.baselines.v3_full import v3_turn_fn
    from help_e.graph_v3 import ProblemGraphV3

    graph = ProblemGraphV3(profile_id="SMOKE_V3")
    client = _MockClient()
    result = v3_turn_fn(
        client=client, profile_id="SMOKE_V3", system="v3",
        session_id=1, turn_id=1,
        user_message="I haven't slept in two days because of finals. I just lie there.",
        recent_turns=[],
        previous_turn_traces=None,
        graph=graph,
    )

    trace = result["trace"]
    assert trace["main_problem"] == "sleep_problems"
    assert "academic_pressure" in trace["current_problems"]
    assert "sleep_problems" in trace["current_problems"]
    assert trace["ttm_stage"] == "contemplation", \
        f"V3: expected sleep_problems → contemplation, got {trace['ttm_stage']}"

    # V3 graph populated correctly.
    assert "sleep_problems" in graph.problems
    assert "academic_pressure" in graph.problems
    sp = graph.problems["sleep_problems"]
    assert sp.summary_text.startswith("s1.t1:")
    assert sp.current_ttm_stage == "contemplation"
    assert len(sp.audit_stack) == 1
    # academic_pressure stayed precontemplation (low confidence → no advance)
    ap = graph.problems["academic_pressure"]
    assert ap.current_ttm_stage == "precontemplation"
    # Edge created with summary_text from EdgeSummaryAgent.
    assert len(graph.edges) == 1
    edge = next(iter(graph.edges.values()))
    assert edge.summary_text != ""
    assert len(edge.connection_entries) == 1

    pack = result["evidence_pack"]
    assert pack["main_problem"]["name"] == "sleep_problems"
    assert "summary_text" in pack["main_problem"]
    assert "quotes" in pack["main_problem"]
    main_quotes = pack["main_problem"]["quotes"]
    assert main_quotes and main_quotes[0]["anchor"] == "s1.t1"
    # No HBM in pack.
    assert "level_attributes" not in pack["main_problem"]
    assert "non_level_attributes" not in pack["main_problem"]

    # Connection block surfaces summary + relation_type-only quotes.
    assert pack["problem_problem_connections"]
    conn = pack["problem_problem_connections"][0]
    assert "summary_text" in conn and "quotes" in conn
    assert "relation_type" in conn["quotes"][0]

    roles_called = {r for (r, _, _) in client.calls}
    assert {
        "agent1_user_intent", "agent2_inference_v3",
        "agent3_problem_v3", "agent3c_edge_summary",
        "agent5_response_v3", "agentX_rolling_summary",
    }.issubset(roles_called)
    # V3 must NOT call HBM agents or RAG-query agent.
    assert "agent3a_attr_update" not in roles_called
    assert "agent3b_ttm_intent" not in roles_called
    assert "agentq_retrieval_query" not in roles_called

    return {
        "system": "v3",
        "n_llm_calls": len(client.calls),
        "diagnostics": result["diagnostics"],
        "main_problem": trace["main_problem"],
        "ttm_stage_after": trace["ttm_stage"],
        "edge_count": len(graph.edges),
        "edge_summary_present": bool(edge.summary_text),
    }


def _run_v7_smoke() -> dict[str, Any]:
    from help_e.baselines.v7_full import v7_turn_fn
    from help_e.graph_v7 import ProblemGraphV7

    graph = ProblemGraphV7(profile_id="SMOKE_V7")
    client = _MockClient()

    result = v7_turn_fn(
        client=client,
        profile_id="SMOKE_V7",
        system="v7",
        session_id=1,
        turn_id=1,
        user_message="I haven't slept in two days because of finals. I just lie there.",
        recent_turns=[],
        previous_turn_traces=None,
        graph=graph,
    )

    # ---- assertions -------------------------------------------------------
    trace = result["trace"]
    assert trace["main_problem"] == "sleep_problems", \
        f"V7: expected main_problem=sleep_problems, got {trace['main_problem']!r}"
    assert "academic_pressure" in trace["current_problems"]
    assert "sleep_problems" in trace["current_problems"]

    # Agent 3c fired exactly once (one new edge with new entries this turn).
    assert trace["n_edge_summaries_updated"] == 1, \
        f"V7: expected 1 edge summary update, got {trace['n_edge_summaries_updated']}"

    # Agent 3b fired once for sleep_problems (severity went unknown → high).
    assert trace["n_ttm_calls_made"] == 1, \
        f"V7: expected 1 TTM call, got {trace['n_ttm_calls_made']}"

    # evidence_pack shape — V7
    pack = result["evidence_pack"]
    assert pack["main_problem"]["name"] == "sleep_problems"
    assert "level_attributes" in pack["main_problem"]
    sev = pack["main_problem"]["level_attributes"]["perceived_severity"]
    # V7 surfaces verbatim user quotes on each main attribute.
    sev_quotes = sev.get("quotes") or []
    assert sev_quotes and sev_quotes[0]["anchor"] == "s1.t1", (
        f"V7: expected first quote anchor=s1.t1, got {sev_quotes!r}"
    )
    assert "quote" in sev_quotes[0], "V7 quotes must include verbatim quote"
    # Legacy field names must not leak through.
    assert "span" not in sev_quotes[0]
    assert "session_id" not in sev_quotes[0]
    assert "concise" not in sev_quotes[0]
    # Per-edge summary_text (Agent 3c product) — not raw entries.
    assert pack["problem_problem_connections"], "should have at least one edge in pack"
    edge_block = pack["problem_problem_connections"][0]
    assert "summary_text" in edge_block
    assert "n_entries" in edge_block
    assert edge_block["n_entries"] == 1
    edge_quotes = edge_block.get("quotes") or []
    assert edge_quotes and edge_quotes[0]["anchor"] == "s1.t1", (
        f"V7: expected edge quote anchor=s1.t1, got {edge_quotes!r}"
    )
    assert "relation_type" in edge_quotes[0]
    # `why` is intentionally omitted from connection quote records.
    assert "why" not in edge_quotes[0]
    # Other current problems get only a 1-line stub.
    assert any(o["name"] == "academic_pressure" for o in pack["other_current_problems"])

    # Audit-usage diagnostic in trace — canned response cited 0 evidence
    # entries, so n_audits_used_in_response == 0.
    assert "n_audits_used_in_response" in trace
    assert trace["n_audits_used_in_response"] == 0

    # Graph state actually mutated as expected.
    assert "academic_pressure" in graph.problems
    assert "sleep_problems" in graph.problems
    assert len(graph.edges) == 1
    edge = next(iter(graph.edges.values()))
    assert edge.summary_text, "Agent 3c should have set the edge summary_text"
    assert len(edge.connection_entries) == 1

    # Verify the role roster — 7 distinct roles (Agents 1, 2, 3a×2, 3b, 3c, 5, X).
    roles_called = {r for (r, _, _) in client.calls}
    assert {"agent1_user_intent", "agent2_inference_v7",
            "agent3a_attr_update", "agent3b_ttm_intent",
            "agent3c_edge_summary",
            "agent5_response_v7", "agentX_rolling_summary"}.issubset(roles_called), \
        f"V7: missing expected roles, got {roles_called}"

    return {
        "system": "v7",
        "n_llm_calls": len(client.calls),
        "diagnostics": result["diagnostics"],
        "trace_keys": list(trace.keys()),
        "main_problem": trace["main_problem"],
        "ttm_stage_after": trace["ttm_stage"],
        "edge_count": len(graph.edges),
        "edge_summary_present": bool(edge.summary_text),
    }


def _run_v8_smoke() -> dict[str, Any]:
    _install_mock_dense_backend()  # before v8_full imports anything from rag_v8

    from help_e.baselines.v8_full import v8_turn_fn
    from help_e.graph_v7 import ProblemGraphV7

    graph = ProblemGraphV7(profile_id="SMOKE_V8")
    client = _MockClient()

    result = v8_turn_fn(
        client=client,
        profile_id="SMOKE_V8",
        system="v8",
        session_id=1,
        turn_id=1,
        user_message="I haven't slept in two days because of finals. I just lie there.",
        recent_turns=[],
        previous_turn_traces=None,
        graph=graph,
    )

    # ---- assertions -------------------------------------------------------
    trace = result["trace"]
    assert trace["main_problem"] == "sleep_problems"
    assert "academic_pressure" in trace["current_problems"]
    assert "sleep_problems" in trace["current_problems"]
    assert trace["n_ttm_calls_made"] == 1

    # V8 must NOT have fired Agent 3c — graph.edges[*].summary_text stays empty.
    assert all(not e.summary_text for e in graph.edges.values()), \
        "V8 should not maintain edge.summary_text"

    # evidence_pack shape — V8
    pack = result["evidence_pack"]
    assert pack["main_problem"]["name"] == "sleep_problems"
    assert "current_levels" in pack["main_problem"], \
        "V8 main_problem block must carry current_levels (no summary_text)"
    assert "level_attributes" not in pack["main_problem"], \
        "V8 must NOT surface attribute summary_text to Agent 5"
    # Audit anchors per attribute.
    sev = pack["main_problem"]["current_levels"].get("perceived_severity")
    assert sev and sev.get("audit_anchors") == ["s1.t1"], (
        f"V8: expected audit_anchors=['s1.t1'], got {sev}"
    )
    # rag_retrieved_chunks present and well-shaped.
    assert "rag_retrieved_chunks" in pack
    assert isinstance(pack["rag_retrieved_chunks"], list)
    assert pack["rag_corpus_size"] >= 2, \
        f"V8 corpus should have ≥2 chunks (audit + connection); got {pack['rag_corpus_size']}"

    # The mock encoder is deterministic — at least one hit should come back
    # for an in-vocab query.
    assert pack["rag_retrieved_chunks"], "V8 retrieval returned nothing"
    for h in pack["rag_retrieved_chunks"]:
        assert "score" in h
        assert h["score"] > 0.0, f"V8 score must be > 0; got {h['score']:.3f}"
        assert h["type"] in ("attribute_entry", "connection_entry")
        assert "anchor" in h, "V8 chunks must use 'anchor', not session/turn IDs"
        # New normalized field names — old ones must be gone.
        assert "supporting_utterance_span" not in h
        assert "supporting_quote" not in h
        assert "concise_explanation" not in h
        assert "session_id" not in h
        assert "turn_id" not in h

    # Audit-usage diagnostic.
    assert trace["n_audits_used_in_response"] == 0

    # No agent3c_edge_summary in V8's role roster.
    roles_called = {r for (r, _, _) in client.calls}
    assert "agent3c_edge_summary" not in roles_called, \
        "V8 must NOT call Agent 3c"
    assert "agent5_response_v8" in roles_called
    # Agent Q must have fired and its query must be the one retrieval used.
    assert "agentq_retrieval_query" in roles_called, "V8 must call Agent Q"
    assert pack.get("rag_query_source") == "agent_q", (
        f"V8 should source query from Agent Q, got {pack.get('rag_query_source')!r}"
    )
    assert pack.get("rag_query") == _AGENTQ_OUT["retrieval_query"], (
        f"V8 retrieval query should be Agent Q's output verbatim; "
        f"got {pack.get('rag_query')!r}"
    )

    return {
        "system": "v8",
        "n_llm_calls": len(client.calls),
        "diagnostics": result["diagnostics"],
        "main_problem": trace["main_problem"],
        "ttm_stage_after": trace["ttm_stage"],
        "rag_corpus_size": pack["rag_corpus_size"],
        "rag_retrieved_count": len(pack["rag_retrieved_chunks"]),
        "rag_top_score": pack["rag_retrieved_chunks"][0]["score"]
                         if pack["rag_retrieved_chunks"] else None,
        "rag_query_source": pack.get("rag_query_source"),
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 70)
    print("V1 + V3 + V7 + V8 smoke test (mock LLM, mock dense encoder)")
    print("=" * 70)

    failures: list[str] = []

    print("\n--- V1 ---")
    try:
        v1 = _run_v1_smoke()
        print(f"PASS  · LLM calls: {v1['n_llm_calls']}")
        print(f"      · rolling_summary set after turn: {v1['rolling_summary_set']}")
        diag = v1["diagnostics"]
        print(f"      · diagnostics: total={diag['n_llm_calls_total']}  "
              f"big={diag['n_llm_calls_big']}  small={diag['n_llm_calls_small']}")
    except Exception as e:
        print(f"FAIL  · {type(e).__name__}: {e}")
        traceback.print_exc()
        failures.append("v1")

    print("\n--- V3 ---")
    try:
        v3 = _run_v3_smoke()
        print(f"PASS  · LLM calls: {v3['n_llm_calls']}")
        print(f"      · main_problem: {v3['main_problem']}")
        print(f"      · ttm after turn: {v3['ttm_stage_after']}")
        print(f"      · edges in graph: {v3['edge_count']}")
        print(f"      · edge.summary_text populated: {v3['edge_summary_present']}")
        diag = v3["diagnostics"]
        print(f"      · diagnostics: total={diag['n_llm_calls_total']}  "
              f"big={diag['n_llm_calls_big']}  small={diag['n_llm_calls_small']}")
    except Exception as e:
        print(f"FAIL  · {type(e).__name__}: {e}")
        traceback.print_exc()
        failures.append("v3")

    print("\n--- V7 ---")
    try:
        v7 = _run_v7_smoke()
        print(f"PASS  · LLM calls: {v7['n_llm_calls']}")
        print(f"      · main_problem: {v7['main_problem']}")
        print(f"      · ttm after turn: {v7['ttm_stage_after']}")
        print(f"      · edges in graph: {v7['edge_count']}")
        print(f"      · edge.summary_text populated: {v7['edge_summary_present']}")
        diag = v7["diagnostics"]
        print(f"      · diagnostics: total={diag['n_llm_calls_total']}  "
              f"big={diag['n_llm_calls_big']}  small={diag['n_llm_calls_small']}")
    except Exception as e:
        print(f"FAIL  · {type(e).__name__}: {e}")
        traceback.print_exc()
        failures.append("v7")

    print("\n--- V8 ---")
    try:
        v8 = _run_v8_smoke()
        print(f"PASS  · LLM calls: {v8['n_llm_calls']}")
        print(f"      · main_problem: {v8['main_problem']}")
        print(f"      · ttm after turn: {v8['ttm_stage_after']}")
        print(f"      · rag corpus size: {v8['rag_corpus_size']}")
        print(f"      · rag retrieved: {v8['rag_retrieved_count']}")
        print(f"      · rag query source: {v8['rag_query_source']}")
        print(f"      · top score: {v8['rag_top_score']:.3f}"
              if v8["rag_top_score"] is not None else "      · top score: None")
        diag = v8["diagnostics"]
        print(f"      · diagnostics: total={diag['n_llm_calls_total']}  "
              f"big={diag['n_llm_calls_big']}  small={diag['n_llm_calls_small']}")
    except Exception as e:
        print(f"FAIL  · {type(e).__name__}: {e}")
        traceback.print_exc()
        failures.append("v8")

    print("\n" + "=" * 70)
    if failures:
        print(f"SMOKE FAILED  ({', '.join(failures)})")
        return 1
    print("SMOKE PASSED  (V1 + V3 + V7 + V8)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
