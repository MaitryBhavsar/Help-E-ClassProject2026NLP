"""v4 — text summary + TTM-from-summary + free-form problem connections.

Step UP from v3 (text summary + per-problem TTM): adds free-form
cross-problem CONNECTIONS describing how the user's problems link to
each other in their own narrative. NOT bound to HBM attribute types
(that's v6's job); just `{what, why, supporting_quote}` strings.

Per-turn pipeline (1 LLM call, same as v3):
  1. ONE `v4_extract` LLM call — produces {user_intent, problems[name+
     summary+ttm_stage], main_problem, problem_connections}.
  2. Apply to per-problem state attached to the graph (`graph._v4_state`).
  3. Compute MISC candidate bundle from the inferred TTM stage — same
     selector logic as v3/v6.
  4. Call `run_response_simple` with the populated state. The summary +
     connections show up in the EVIDENCE block; v4 does NOT add HBM
     attributes (that's v6).

TTM stage source: PER-PROBLEM SUMMARY ONLY. Connections do NOT
inform TTM. This keeps v3's TTM logic intact — v4's only addition
relative to v3 is the connections layer.

Differences from v3:
  - v4 emits + tracks `problem_connections` (v3 does not)
  - v4 surfaces connections in the response prompt's EVIDENCE block
    (under "Connections" sub-block; rendered by response_simple)
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import (
    PROBLEM_VOCAB,
    TTM_STAGES_V6,
    USER_INTENTS_V6,
)
from ..graph_v6 import ProblemGraphV6
from ..instruction_response_simple import run_response_simple
from ..llm_client import CallContext, LLMClient
from ..mi_selector_v6 import select_candidates_v6
from ..prompts.common import PROJECT_IDENTITY, format_dialog_turns
from ..prompts.common_v6 import (
    problem_name_mapping_block,
    ttm_guidance_block,
)


# ---------------------------------------------------------------------------
# Per-problem state (attached to the graph object)
# ---------------------------------------------------------------------------


@dataclass
class _ProblemSummary:
    name: str                         # one of PROBLEM_VOCAB
    summary: str                      # 2–4 sentence rolling description
    ttm_stage: str                    # one of TTM_STAGES_V6
    last_active_turn: int = 0
    history: list[tuple[int, str]] = field(default_factory=list)  # (turn_id, stage)


@dataclass
class _Connection:
    """Free-form cross-problem link emitted by the LLM. No closed enum;
    `what` and `why` carry the connection's description.
    """
    problem_a: str
    problem_b: str
    what: str
    why: str
    supporting_quote: Optional[str] = None
    session_id: Optional[int] = None
    turn_id: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "problem_a": self.problem_a,
            "problem_b": self.problem_b,
            "what": self.what,
            "why": self.why,
            "supporting_quote": self.supporting_quote,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
        }


@dataclass
class _V4State:
    summaries: dict[str, _ProblemSummary] = field(default_factory=dict)
    connections: list[_Connection] = field(default_factory=list)
    main_problem: Optional[str] = None


def _get_state(graph: ProblemGraphV6) -> _V4State:
    state = getattr(graph, "_v4_state", None)
    if state is None:
        state = _V4State()
        graph._v4_state = state  # type: ignore[attr-defined]
    return state


# ---------------------------------------------------------------------------
# Combined extract + summary + TTM + connections call (one LLM call per turn)
# ---------------------------------------------------------------------------


_V4_EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["user_intent", "problems", "main_problem", "problem_connections"],
    "properties": {
        "user_intent": {"type": "string", "enum": list(USER_INTENTS_V6)},
        "problems": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["problem_name", "summary", "ttm_stage"],
                "properties": {
                    "problem_name": {"type": "string", "enum": list(PROBLEM_VOCAB)},
                    "summary": {"type": "string", "minLength": 1},
                    "ttm_stage": {"type": "string", "enum": list(TTM_STAGES_V6)},
                },
            },
        },
        "main_problem": {
            "oneOf": [
                {"type": "null"},
                {"type": "string", "enum": list(PROBLEM_VOCAB)},
            ],
        },
        "problem_connections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["problem_a", "problem_b", "what", "why"],
                "properties": {
                    "problem_a": {"type": "string", "enum": list(PROBLEM_VOCAB)},
                    "problem_b": {"type": "string", "enum": list(PROBLEM_VOCAB)},
                    "what": {"type": "string", "minLength": 1},
                    "why":  {"type": "string", "minLength": 1},
                    "supporting_quote": {
                        "oneOf": [
                            {"type": "null"},
                            {"type": "string", "minLength": 1},
                        ],
                    },
                },
            },
        },
    },
}


def _v4_extract_system() -> str:
    return textwrap.dedent(f"""\
        {PROJECT_IDENTITY}

        You are the v4 extraction agent. On every user turn you produce:
          1. user_intent (one of {list(USER_INTENTS_V6)})
          2. A list of problems active in this conversation (subset of
             the 20-vocabulary).
          3. For each active problem: a 2–4 sentence rolling SUMMARY
             that captures what the user has said about it so far, and
             a TTM stage (one of {list(TTM_STAGES_V6)}) inferred from
             that summary's behavioral cues.
          4. A single main_problem (one of those problem names, or null
             if no clear focus).
          5. problem_connections — a (possibly empty) list of free-form
             links between any TWO problems in the active list. Each
             connection has:
               - problem_a, problem_b: the two problems linked
               - what: a short free-form description of WHAT the
                 connection is (e.g. "the late-night studying is the
                 same activity that's keeping the user awake")
               - why: WHY you think this connection exists (e.g.
                 "the user explicitly mentioned 'all-nighters keep me
                 up replaying' tying both"). The "why" should anchor
                 in something the user actually said.
               - supporting_quote: the user's actual phrase that
                 supports this connection (or null if you're inferring
                 from a pattern across turns)

             Connections are FREE-FORM. Don't constrain yourself to a
             closed enum (no "shared_trigger" / "causal" tags) — just
             describe the link in your own words. Only emit a
             connection when you genuinely see one in the conversation;
             do NOT invent connections to fill the field.

        # PROBLEM-NAME MAPPING
        {problem_name_mapping_block()}

        # TTM STAGE GUIDANCE (from the rolling summary, NOT from connections)
        {ttm_guidance_block()}

        Note: TTM stage is inferred from EACH problem's summary in
        isolation. Connections do NOT change a problem's TTM stage.

        # SUMMARY UPDATE RULE
        Carry the prior summary forward when a problem is still active
        and incorporate new content from this turn. Drop a problem
        from the list when it has clearly faded.

        # CONNECTION UPDATE RULE
        - Re-emit existing connections on every turn IF they're still
          relevant. Drop them when one of the linked problems fades.
        - Add new connections only when the current turn introduces a
          link that wasn't present before.

        # OUTPUT SHAPE
        {{
          "user_intent": "express_emotion",
          "problems": [
            {{
              "problem_name": "academic_pressure",
              "summary": "User is in finals week, has had three nights of poor sleep, feels the workload is crushing.",
              "ttm_stage": "contemplation"
            }},
            {{
              "problem_name": "sleep_problems",
              "summary": "User has been lying awake replaying the day's studying.",
              "ttm_stage": "contemplation"
            }}
          ],
          "main_problem": "academic_pressure",
          "problem_connections": [
            {{
              "problem_a": "academic_pressure",
              "problem_b": "sleep_problems",
              "what": "the late-night studying is the same activity that's keeping the user awake",
              "why":  "the user explicitly mentioned 'all-nighters keep me up replaying' tying both problems",
              "supporting_quote": "all-nighters keep me up replaying"
            }}
          ]
        }}

        Return ONLY valid JSON matching the schema. No prose.
    """)


def _v4_extract_user(
    *,
    user_message: str,
    recent_turns: list[dict],
    prior_summaries: dict[str, _ProblemSummary],
    prior_connections: list[_Connection],
) -> str:
    if prior_summaries:
        prior_block = "\n".join(
            f"  - {p.name} (current_ttm_stage={p.ttm_stage}): {p.summary}"
            for p in prior_summaries.values()
        )
    else:
        prior_block = "  (none yet)"

    if prior_connections:
        conn_block = "\n".join(
            f"  - {c.problem_a} ↔ {c.problem_b}: {c.what} (why: {c.why})"
            for c in prior_connections
        )
    else:
        conn_block = "  (none yet)"

    return textwrap.dedent(f"""\
        PRIOR PROBLEM SUMMARIES (from earlier turns this conversation):
        {prior_block}

        PRIOR PROBLEM CONNECTIONS (from earlier turns this conversation):
        {conn_block}

        RECENT DIALOGUE:
        {format_dialog_turns(recent_turns)}

        CURRENT USER MESSAGE:
        {user_message}

        Update the problem summaries, TTM stages, and problem
        connections now.
    """)


def _safe_v4_fallback(prior_state: _V4State) -> dict:
    """Neutral fallback so the turn can proceed. Carries forward any
    existing summaries + connections unchanged."""
    return {
        "user_intent": "small_talk",
        "problems": [
            {
                "problem_name": p.name,
                "summary": p.summary,
                "ttm_stage": p.ttm_stage,
            }
            for p in prior_state.summaries.values()
        ],
        "main_problem": prior_state.main_problem,
        "problem_connections": [
            {
                "problem_a": c.problem_a,
                "problem_b": c.problem_b,
                "what": c.what,
                "why": c.why,
                "supporting_quote": c.supporting_quote,
            }
            for c in prior_state.connections
        ],
        "_fallback_default": True,
    }


def _run_v4_extract(
    *,
    client: LLMClient,
    ctx: CallContext,
    user_message: str,
    recent_turns: list[dict],
    state: _V4State,
) -> dict:
    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=_v4_extract_system(),
            user_prompt=_v4_extract_user(
                user_message=user_message,
                recent_turns=recent_turns,
                prior_summaries=state.summaries,
                prior_connections=state.connections,
            ),
            schema=_V4_EXTRACT_SCHEMA,
        )
    except Exception:
        return _safe_v4_fallback(state)


# ---------------------------------------------------------------------------
# State application + candidate selection
# ---------------------------------------------------------------------------


def _apply_extract_to_state(
    state: _V4State, extract_out: dict, *, session_id: int, turn_id: int,
    global_turn_id: int,
) -> tuple[Optional[str], Optional[str], list[dict]]:
    """Update state from extract output. Returns
    `(main_name, main_ttm_stage, ttm_updates)`. Connection list is
    REPLACED each turn with the LLM's output (so the LLM can drop
    stale connections by omitting them).
    """
    ttm_updates: list[dict] = []
    new_problem_names: set[str] = set()
    for prob in extract_out.get("problems") or []:
        name = prob["problem_name"]
        new_stage = prob["ttm_stage"]
        new_summary = prob["summary"]
        new_problem_names.add(name)
        existing = state.summaries.get(name)
        old_stage = existing.ttm_stage if existing else None
        if existing is None:
            state.summaries[name] = _ProblemSummary(
                name=name, summary=new_summary, ttm_stage=new_stage,
                last_active_turn=global_turn_id,
                history=[(global_turn_id, new_stage)],
            )
        else:
            existing.summary = new_summary
            existing.ttm_stage = new_stage
            existing.last_active_turn = global_turn_id
            existing.history.append((global_turn_id, new_stage))
        ttm_updates.append({
            "problem_name": name,
            "old_ttm_stage": old_stage,
            "new_ttm_stage": new_stage,
            "reasoning": "v4 inferred from rolling summary",
        })

    main_name = extract_out.get("main_problem")
    if main_name and main_name not in state.summaries:
        main_name = None
    state.main_problem = main_name
    main_ttm_stage = (
        state.summaries[main_name].ttm_stage if main_name else None
    )

    # Replace the connection list with the LLM's current view, tagged
    # with the turn that produced it. Both endpoints must be in active
    # summaries (drop dangling references).
    state.connections = []
    for c in extract_out.get("problem_connections") or []:
        a = c["problem_a"]
        b = c["problem_b"]
        if a not in state.summaries or b not in state.summaries or a == b:
            continue
        state.connections.append(_Connection(
            problem_a=a, problem_b=b,
            what=c["what"], why=c["why"],
            supporting_quote=c.get("supporting_quote"),
            session_id=session_id, turn_id=turn_id,
        ))

    return main_name, main_ttm_stage, ttm_updates


def _candidate_bundle_for_v4(
    *, main_name: Optional[str], main_ttm_stage: Optional[str],
    user_intent: str,
) -> dict:
    """Reuse the v6 selector by constructing a minimal in-memory graph
    that carries just the main problem + its TTM stage. Same approach
    as v3.
    """
    g = ProblemGraphV6(profile_id="_v4_selector_only")
    if main_name:
        g.get_or_create_problem(main_name, first_mentioned=(1, 1))
        if main_ttm_stage in TTM_STAGES_V6:
            g.set_ttm_stage(main_name, main_ttm_stage)
    return select_candidates_v6(
        graph=g,
        main_problem_name=main_name,
        user_intent=user_intent if user_intent in USER_INTENTS_V6 else "small_talk",
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def v4_turn_fn(
    *,
    client: LLMClient,
    profile_id: str,
    system: str = "v4",
    session_id: int,
    turn_id: int,
    user_message: str,
    recent_turns: list[dict],
    last_system_message: Optional[str] = None,
    prior_session_summary: Optional[str] = None,    # unused (§9.2)
    graph: ProblemGraphV6,                           # state attaches here
    last_n_turns: int = 5,
    previous_turn_traces: Optional[list[dict]] = None,
) -> dict:
    """One v4 turn. Same signature as `v6_turn_fn` so the v6 driver
    can call either interchangeably.

    Pipeline:
      1. v4_extract LLM call: per-problem summary update + TTM
         inference + intent + main_problem + free-form connections.
      2. Apply to per-problem state attached to the graph.
      3. Select MISC candidates from inferred main TTM stage.
      4. Call run_response_simple with empty graph + the populated
         state (response_simple reads `graph._v4_state` for summaries +
         connections).
    """
    state = _get_state(graph)
    global_turn_id = (session_id - 1) * 1000 + turn_id

    # --- Step 1: combined extract+summary+TTM+connections call ---
    ext_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="v4_extract",
    )
    extract_out = _run_v4_extract(
        client=client, ctx=ext_ctx,
        user_message=user_message,
        recent_turns=recent_turns,
        state=state,
    )

    # --- Step 2: apply to state ---
    main_name, main_ttm_stage, ttm_updates = _apply_extract_to_state(
        state, extract_out,
        session_id=session_id, turn_id=turn_id,
        global_turn_id=global_turn_id,
    )
    user_intent = extract_out.get("user_intent", "small_talk")

    # --- Step 3: candidate bundle from inferred TTM stage ---
    candidate_bundle = _candidate_bundle_for_v4(
        main_name=main_name, main_ttm_stage=main_ttm_stage,
        user_intent=user_intent,
    )

    # --- Step 4: response (3-field schema) ---
    from .v6_full import (
        _collect_past_two_turns,
        _extract_misc_codes_from_reasoning,
    )
    past_two_turns = _collect_past_two_turns(previous_turn_traces or [])

    rsp_ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=turn_id, call_role="response_simple",
    )
    response_out = run_response_simple(
        client=client, ctx=rsp_ctx,
        graph=graph,                           # carries _v4_state
        candidate_bundle=candidate_bundle,
        past_two_turns=past_two_turns,
        recent_turns=recent_turns,
        current_user_message=user_message,
    )

    chosen_misc_codes = _extract_misc_codes_from_reasoning(
        response_out.get("reasoning", "") or ""
    )

    trace = {
        "main_problem": main_name,
        "current_problems": [p["problem_name"] for p in extract_out.get("problems") or []],
        "user_intent": user_intent,
        "ttm_stage": main_ttm_stage,
        "transition_target": candidate_bundle.get("transition_target"),
        "all_candidate_codes": candidate_bundle.get("all_candidate_codes") or [],
        "chosen_misc_codes": chosen_misc_codes,
        "turn_scope_level_attrs": [],
        "level_updates": [],
        "ttm_updates": ttm_updates,
        # v4 emits cooccurrence-style edges (free-form), not HBM
        # attribute connections.
        "cooc_added": len([
            c for c in extract_out.get("problem_connections") or []
            if c["problem_a"] != c["problem_b"]
        ]),
        "attr_conn_added": 0,
    }

    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "user_message": user_message,
        # v4 doesn't run the v6 inference/recompute calls; emit
        # v4-shaped stubs so downstream loaders/metrics still find
        # what they expect. Connections live under
        # `inference.problem_cooccurrence_connections` (free-form
        # variant — `relation_type` is omitted, replaced with `what`/
        # `why`).
        "inference": {
            "user_intent": {"intent": user_intent, "confidence": "medium",
                            "explanation": "v4 combined extract", "supporting_utterance_span": None},
            "current_problems": [
                {"problem_name": p["problem_name"], "is_new_problem": False,
                 "matched_existing_problem_name": p["problem_name"],
                 "explanation": "v4 summary-based", "supporting_utterance_span": None}
                for p in extract_out.get("problems") or []
            ],
            "main_problem": (
                {"problem_name": main_name, "explanation": "v4 main pick",
                 "supporting_utterance_span": None} if main_name else None
            ),
            "problem_attribute_entries": [],
            "problem_cooccurrence_connections": [
                {
                    "problem_a": c["problem_a"],
                    "problem_b": c["problem_b"],
                    "what": c["what"],
                    "why": c["why"],
                    "supporting_quote": c.get("supporting_quote"),
                }
                for c in extract_out.get("problem_connections") or []
            ],
            "problem_attribute_connections": [],
            "_v4_extract": True,
            "_fallback_default": extract_out.get("_fallback_default", False),
        },
        "recompute": {
            "attribute_level_updates": [],
            "ttm_stage_updates": ttm_updates,
            "_v4_no_recompute": True,
        },
        "bundle": None,
        "candidate_bundle": candidate_bundle,
        "past_two_turns": past_two_turns,
        "response": response_out,
        "trace": trace,
    }


# ---------------------------------------------------------------------------
# Self-test (no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    # State attaches to the graph and survives across turns.
    g = ProblemGraphV6(profile_id="T")
    s1 = _get_state(g)
    s2 = _get_state(g)
    assert s1 is s2

    # Apply an extract with one problem + one connection (well-formed).
    state = _get_state(ProblemGraphV6(profile_id="T2"))
    extract = {
        "user_intent": "express_emotion",
        "problems": [
            {"problem_name": "academic_pressure",
             "summary": "Finals week, three nights of poor sleep.",
             "ttm_stage": "contemplation"},
            {"problem_name": "sleep_problems",
             "summary": "Lying awake replaying the day.",
             "ttm_stage": "contemplation"},
        ],
        "main_problem": "academic_pressure",
        "problem_connections": [
            {
                "problem_a": "academic_pressure",
                "problem_b": "sleep_problems",
                "what": "late-night studying is what's keeping user awake",
                "why":  "user mentioned 'all-nighters keep me up replaying'",
                "supporting_quote": "all-nighters keep me up replaying",
            }
        ],
    }
    main, main_stage, updates = _apply_extract_to_state(
        state, extract, session_id=1, turn_id=1, global_turn_id=1,
    )
    assert main == "academic_pressure"
    assert main_stage == "contemplation"
    assert len(updates) == 2
    assert "academic_pressure" in state.summaries
    assert "sleep_problems" in state.summaries
    assert len(state.connections) == 1
    c = state.connections[0]
    assert c.problem_a == "academic_pressure"
    assert c.problem_b == "sleep_problems"
    assert "late-night studying" in c.what
    assert "all-nighters" in c.why

    # Connection with one dangling endpoint (problem not in summaries) is dropped.
    extract_dangling = {
        "user_intent": "express_emotion",
        "problems": [
            {"problem_name": "academic_pressure",
             "summary": "Updated.", "ttm_stage": "contemplation"},
        ],
        "main_problem": "academic_pressure",
        "problem_connections": [
            {
                "problem_a": "academic_pressure",
                "problem_b": "sleep_problems",  # dropped from summaries this turn
                "what": "stale link", "why": "stale", "supporting_quote": None,
            }
        ],
    }
    state.connections = []  # reset
    state.summaries.pop("sleep_problems", None)  # simulate fade
    _apply_extract_to_state(
        state, extract_dangling, session_id=1, turn_id=2, global_turn_id=2,
    )
    # The dangling connection (sleep_problems no longer active) is dropped.
    assert state.connections == []

    # Self-loop connection is dropped.
    extract_self = {
        "user_intent": "express_emotion",
        "problems": [
            {"problem_name": "academic_pressure",
             "summary": "Still finals.", "ttm_stage": "preparation"},
        ],
        "main_problem": "academic_pressure",
        "problem_connections": [
            {
                "problem_a": "academic_pressure",
                "problem_b": "academic_pressure",
                "what": "self-loop", "why": "test", "supporting_quote": None,
            }
        ],
    }
    state.connections = []
    _apply_extract_to_state(
        state, extract_self, session_id=1, turn_id=3, global_turn_id=3,
    )
    assert state.connections == []

    # Candidate bundle for contemplation gives common + stage_specific.
    cb = _candidate_bundle_for_v4(
        main_name="academic_pressure", main_ttm_stage="contemplation",
        user_intent="express_emotion",
    )
    assert cb["main_problem"] == "academic_pressure"
    assert cb["ttm_stage"] == "contemplation"
    common_codes = {c["code"] for c in cb["common_candidates"]}
    stage_codes = {c["code"] for c in cb["stage_specific_candidates"]}
    assert "support" in common_codes and "facilitate" in common_codes
    assert "evoke" in stage_codes

    # Cold-start: no main → only common candidates.
    cb_cold = _candidate_bundle_for_v4(
        main_name=None, main_ttm_stage=None, user_intent="small_talk",
    )
    assert cb_cold["main_problem"] is None
    assert cb_cold["stage_specific_candidates"] == []

    # Fallback shape carries forward summaries + connections.
    state.connections = [_Connection(
        problem_a="academic_pressure", problem_b="sleep_problems",
        what="x", why="y", supporting_quote=None,
    )]
    state.summaries["sleep_problems"] = _ProblemSummary(
        name="sleep_problems", summary="back", ttm_stage="contemplation",
    )
    fb = _safe_v4_fallback(state)
    assert fb["_fallback_default"] is True
    assert any(p["problem_name"] == "academic_pressure" for p in fb["problems"])
    assert len(fb["problem_connections"]) == 1

    # Schema validates a well-formed example.
    from jsonschema import Draft202012Validator
    Draft202012Validator(_V4_EXTRACT_SCHEMA).validate(extract)
    Draft202012Validator(_V4_EXTRACT_SCHEMA).validate({
        "user_intent": "small_talk", "problems": [], "main_problem": None,
        "problem_connections": [],
    })

    print("v4_obs_ttm self-test PASSED")


if __name__ == "__main__":
    _self_test()
