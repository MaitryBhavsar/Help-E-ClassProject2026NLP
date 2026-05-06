"""v7 Agent 3c — per-edge problem-problem connection summary updater.

ONE call per edge that received NEW connection entries this turn (parallel
across edges). Reads the edge's existing ``summary_text`` plus the new
entries from Agent 2 and emits an updated ``summary_text`` — chronological
NL with this turn appended, prompt-pruned for redundancy.

This mirrors Agent 3a's role for attribute summaries: nothing is dropped
from the connection history. Older entries persist in the audit stack
(``edge.connection_entries``) for diagnostics, but the running NL summary
is the surface that flows into Agent 4's evidence_pack and onward to
Agent 5. As the audit stack grows, the summary stays readable.

Conservative summary rules (encoded in the prompt):
  - Append THIS turn as one chronological sentence anchored at "tS.T".
  - Do NOT rewrite earlier sentences.
  - SKIP redundancy. If today's entries restate what's already in
    summary_text, append a single short note (e.g., "tS.T: restated").
  - Embed a short verbatim ``supporting_quote`` ONLY when it adds
    something the summary doesn't already capture (Agent 5 may quote it).
  - Set ``useful = 1`` if today's entries added genuinely new content,
    else 0.

Fallback: if the LLM call fails, ``run_agent3c`` returns a deterministic
Python-built summary that prepends the new entries chronologically to the
existing summary_text. This guarantees the "summarize, don't drop"
principle holds even on LLM failure.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Any, Optional

from ..config import (
    LEVEL_ATTR_TYPES,
    NON_LEVEL_ATTR_TYPES,
    PROBLEM_VOCAB,
    RELATION_TYPES,
)
from ..llm_client import CallContext, LLMClient, LLMStructuredError


_ALL_ATTR_TYPES: tuple[str, ...] = tuple(
    list(LEVEL_ATTR_TYPES) + list(NON_LEVEL_ATTR_TYPES)
)


# ---------------------------------------------------------------------------
# JSON schema
# ---------------------------------------------------------------------------


AGENT3C_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["problem_1", "problem_2", "summary_text", "useful"],
    "properties": {
        "problem_1": {"type": "string", "enum": list(PROBLEM_VOCAB)},
        "problem_2": {"type": "string", "enum": list(PROBLEM_VOCAB)},
        "summary_text": {"type": "string", "minLength": 1},
        "useful": {"type": "integer", "enum": [0, 1]},
    },
}


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _rules_block() -> str:
    return textwrap.dedent("""
        # SUMMARY_TEXT CONSTRUCTION

        - One chronological NL paragraph spanning this edge's whole
          history. Each turn that added something gets a turn-anchored
          sentence (e.g., "s1.t4: ...").
        - Append THIS turn as a NEW sentence at the end. Do NOT rewrite
          earlier sentences.
        - SKIP REDUNDANCY. If today's entries merely restate what's
          already in the summary, append a single short note like
          "sS.tT: restated — same link from another angle." Do NOT
          repeat the content. Set useful = 0.
        - If today adds a genuinely new dimension (a new attribute
          pairing, a new relation_type, a clearer mechanism, an
          escalation, or an observable change in how the two problems
          interact), write a short sentence that captures the new
          dimension. Set useful = 1.
        - Embed a verbatim supporting_quote ONLY when it adds something
          the surrounding sentence doesn't already capture. Quote
          sparingly — at most one short quote per turn-sentence — and
          render it inside double quotes inline.
        - Stay tight. Aim for a summary that fits in ~150 words even
          after many turns. Compression is the goal — but never at the
          cost of losing a distinct mechanism the user voiced.

        # WHAT NOT TO DO

        - Do NOT drop or omit prior content. The summary is the only
          surface Agent 5 sees for this edge — older entries beyond the
          most recent are not separately rendered.
        - Do NOT name HBM labels diagnostically ("perceived_severity is
          high causes perceived_barriers"). Use the natural-language
          shape of the link (e.g., "severity drives the felt barriers").
        - Do NOT speculate beyond the entries. Only summarize what was
          actually extracted by Agent 2 in earlier turns and today.
    """)


def build_system_prompt() -> str:
    return textwrap.dedent(f"""\
        You are the EdgeSummaryAgent of the v7 HELP-E pipeline. You handle ONE
        problem-problem edge per call. Your job is to maintain the edge's
        running NL summary — a chronological record of every connection
        entry that has ever been written for this edge, compressed to
        readable prose.

        You read the edge's existing summary_text plus the new connection
        entries appended this turn, and you return the updated
        summary_text. You also return a useful flag (0|1) indicating
        whether today's entries added genuinely new content.

        You do NOT touch attribute summaries (Agent 3a does), TTM stage
        (Agent 3b does), or the user. You only update one edge.

        {_rules_block()}

        # OUTPUT

        Return ONE JSON object:

        {{
          "problem_1": "<endpoint 1, alphabetically first>",
          "problem_2": "<endpoint 2, alphabetically second>",
          "summary_text": "<full chronological NL paragraph after this turn's update>",
          "useful": <0 or 1>
        }}

        Return ONLY JSON. No prose.
    """)


def _format_new_entries(items: list[dict]) -> str:
    if not items:
        return "(none — should not happen; Agent 3c is only called when there are new entries)"
    out: list[str] = []
    for it in items:
        quote = it.get("supporting_quote") or "(no quote)"
        out.append(textwrap.dedent(f"""\
              s{it["session_id"]}.t{it["turn_id"]}:
                {it["attribute_a"]} ↔ {it["attribute_b"]} ({it["relation_type"]})
                why: {it["why"]}
                quote: "{quote}"
        """).rstrip())
    return "\n\n".join(out)


def build_user_prompt(
    *,
    problem_1: str,
    problem_2: str,
    current_session: int,
    current_turn: int,
    existing_summary_text: str,
    new_entries: list[dict],
) -> str:
    """Assemble Agent 3c's user prompt for ONE edge.

    `existing_summary_text` is the edge's prior running summary (may be
    empty on the very first call for this edge).

    `new_entries` items have shape:
        {session_id, turn_id, attribute_a, attribute_b, relation_type,
         why, supporting_quote}
    """
    existing = (
        existing_summary_text.strip()
        or "(none — this is the first time this edge is being summarized)"
    )
    return textwrap.dedent(f"""\
        EDGE: {problem_1} ↔ {problem_2}
        TURN: session {current_session}, turn {current_turn}

        EXISTING SUMMARY_TEXT (the full running history of this edge prior
        to this turn — do NOT rewrite it; only extend it):
        {existing}

        NEW CONNECTION ENTRIES THIS TURN (one or more):
        {_format_new_entries(new_entries)}

        Return the JSON object now.
    """)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


_RELATION_SET = frozenset(RELATION_TYPES)
_ATTR_SET = frozenset(_ALL_ATTR_TYPES)


def validate_agent3c(
    out: dict,
    *,
    expected_problem_1: Optional[str] = None,
    expected_problem_2: Optional[str] = None,
) -> None:
    """Cross-field constraints. Endpoints, when provided, must match the
    edge the agent was called for (canonical alphabetical order).
    """
    if expected_problem_1 and out["problem_1"] != expected_problem_1:
        raise ValueError(
            f"expected problem_1 {expected_problem_1!r}, got {out['problem_1']!r}"
        )
    if expected_problem_2 and out["problem_2"] != expected_problem_2:
        raise ValueError(
            f"expected problem_2 {expected_problem_2!r}, got {out['problem_2']!r}"
        )
    if out["problem_1"] == out["problem_2"]:
        raise ValueError("self-edge not allowed")
    if not out["summary_text"].strip():
        raise ValueError("empty summary_text")


# ---------------------------------------------------------------------------
# Deterministic Python fallback
# ---------------------------------------------------------------------------


def _python_fallback_summary(
    *, existing_summary_text: str, new_entries: list[dict],
) -> str:
    """Deterministic NL append used when the LLM call fails.

    Guarantees that no information is lost: every new entry is appended as
    a "sS.tT: <attr_a> ↔ <attr_b> (<relation>) — <why>" sentence, with the
    supporting quote in double quotes when present.
    """
    pieces: list[str] = []
    base = existing_summary_text.strip()
    if base:
        pieces.append(base)
    for e in new_entries:
        anchor = f"s{e['session_id']}.t{e['turn_id']}"
        body = (
            f"{anchor}: {e['attribute_a']} ↔ {e['attribute_b']} "
            f"({e['relation_type']}) — {e['why']}"
        )
        q = (e.get("supporting_quote") or "").strip()
        if q:
            body += f' "{q}"'
        pieces.append(body)
    return " ".join(pieces).strip() or "(empty)"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@dataclass
class Agent3cInputs:
    problem_1: str
    problem_2: str
    current_session: int
    current_turn: int
    existing_summary_text: str
    new_entries: list[dict]


def _safe_fallback(inputs: Agent3cInputs) -> dict:
    return {
        "problem_1": inputs.problem_1,
        "problem_2": inputs.problem_2,
        "summary_text": _python_fallback_summary(
            existing_summary_text=inputs.existing_summary_text,
            new_entries=inputs.new_entries,
        ),
        "useful": 1 if inputs.new_entries else 0,
        "_fallback_default": True,
    }


def run_agent3c(
    *, client: LLMClient, ctx: CallContext, inputs: Agent3cInputs,
) -> dict:
    assert ctx.call_role == "agent3c_edge_summary"
    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(
                problem_1=inputs.problem_1,
                problem_2=inputs.problem_2,
                current_session=inputs.current_session,
                current_turn=inputs.current_turn,
                existing_summary_text=inputs.existing_summary_text,
                new_entries=inputs.new_entries,
            ),
            schema=AGENT3C_SCHEMA,
            validator_extras=lambda o: validate_agent3c(
                o,
                expected_problem_1=inputs.problem_1,
                expected_problem_2=inputs.problem_2,
            ),
        )
    except LLMStructuredError:
        return _safe_fallback(inputs)


# ---------------------------------------------------------------------------
# Self-test (no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    p1 = next(iter(PROBLEM_VOCAB))
    p2 = None
    for cand in PROBLEM_VOCAB:
        if cand != p1:
            p2 = cand
            break
    assert p2 is not None
    a, b = (p1, p2) if p1 < p2 else (p2, p1)

    # Schema-valid output
    valid = {
        "problem_1": a,
        "problem_2": b,
        "summary_text": (
            "s1.t2: severity drives the felt barriers. "
            "s1.t4: new dimension — barriers also reinforce severity."
        ),
        "useful": 1,
    }
    validate_agent3c(valid, expected_problem_1=a, expected_problem_2=b)

    # Endpoint mismatch → reject
    try:
        validate_agent3c(valid, expected_problem_1="not_a_problem")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # Self-edge → reject
    bad = dict(valid)
    bad["problem_2"] = a
    try:
        validate_agent3c(bad)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # Empty summary → reject
    bad2 = dict(valid)
    bad2["summary_text"] = "   "
    try:
        validate_agent3c(bad2)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # Fallback summary preserves all entries
    fb = _python_fallback_summary(
        existing_summary_text="s1.t1: initial link.",
        new_entries=[
            {"session_id": 1, "turn_id": 2,
             "attribute_a": "perceived_severity",
             "attribute_b": "perceived_barriers",
             "relation_type": "causal",
             "why": "severity drives barriers",
             "supporting_quote": "every revision feels like a judgment"},
            {"session_id": 1, "turn_id": 4,
             "attribute_a": "perceived_severity",
             "attribute_b": "perceived_barriers",
             "relation_type": "reinforcing",
             "why": "barriers also reinforce severity"},
        ],
    )
    assert "s1.t1: initial link" in fb, "old content preserved"
    assert "s1.t2:" in fb and "s1.t4:" in fb, "both new entries present"
    assert "every revision feels like a judgment" in fb, "quote present"
    assert "(reinforcing)" in fb, "relation type present"

    # Fallback when no existing summary
    fb_cold = _python_fallback_summary(
        existing_summary_text="",
        new_entries=[
            {"session_id": 1, "turn_id": 1,
             "attribute_a": "x", "attribute_b": "y",
             "relation_type": "causal", "why": "z"},
        ],
    )
    assert fb_cold.startswith("s1.t1:")

    # Prompts render
    sp = build_system_prompt()
    assert "EdgeSummaryAgent" in sp
    assert "SKIP REDUNDANCY" in sp
    up = build_user_prompt(
        problem_1=a, problem_2=b,
        current_session=1, current_turn=4,
        existing_summary_text="s1.t2: severity drives barriers.",
        new_entries=[
            {"session_id": 1, "turn_id": 4,
             "attribute_a": "perceived_severity",
             "attribute_b": "perceived_barriers",
             "relation_type": "reinforcing",
             "why": "barriers also reinforce severity",
             "supporting_quote": "the harder it gets the worse it feels"},
        ],
    )
    assert a in up and b in up
    assert "severity drives barriers" in up
    assert "reinforcing" in up

    print("agent3c_edge_summary self-test PASSED")


if __name__ == "__main__":
    _self_test()
