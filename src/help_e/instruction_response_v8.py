"""V8 Agent 5 — response generation with RAG-retrieved evidence.

Reuses the V7 SYSTEM prompt, response schema, validators, and fallback
verbatim. The only difference between V7 and V8 at this layer is the
USER prompt:

  - V7's ``# MAIN PROBLEM`` block surfaced each level/non-level
    attribute's full ``summary_text`` and the per-edge running
    ``summary_text`` from Agent 3c.
  - V8's ``# MAIN PROBLEM`` block surfaces only graph state that is NOT
    a chronological summary (TTM, system_intent, current_levels +
    level_reasoning), and replaces the per-edge summaries with a single
    ``# RETRIEVED EVIDENCE`` block listing the BM25-retrieved raw entries
    chosen by ``rag_v8.retrieve``.

Why: V8's principle is "let RAG surface the relevant entries directly
from the chronological store; no separate edge summary is needed."
Attribute summary_text is still maintained by Agent 3a (so Agent 3b can
compute TTM), but it does not flow to Agent 5 — RAG does.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Any, Optional

from .instruction_response_v7 import (
    RESPONSE_V7_SCHEMA,
    _safe_fallback,
    _validate_factory,
    build_system_prompt,
)
from .llm_client import CallContext, LLMClient, LLMStructuredError


# Re-export the v7 schema under a v8 name so call sites and tests can be
# version-explicit without forcing them to know v8 = v7 schema.
RESPONSE_V8_SCHEMA: dict[str, Any] = RESPONSE_V7_SCHEMA


# ---------------------------------------------------------------------------
# USER prompt — v8 shape
# ---------------------------------------------------------------------------


def _format_main_block_v8(main: Optional[dict]) -> str:
    """V8 main_problem block: graph state only, no chronological summary.

    Includes ``current_levels`` (a label dict per level attribute, with a
    short level_reasoning) so Agent 5 has the same level signal V7 did
    without re-feeding the running summary. The full chronological
    history reaches Agent 5 via the # RETRIEVED EVIDENCE block.
    """
    if not main:
        return "(no main problem yet — cold start)"
    lines = [
        f"name: {main['name']}",
        f"ttm_stage: {main['ttm_stage']}",
        f"ttm_reasoning: {main.get('ttm_reasoning') or '(none)'}",
        f"system_intent: {main.get('system_intent') or '(none)'}",
        f"mi_for_system_intent: {main.get('mi_for_system_intent') or '(none)'}",
    ]
    levels = main.get("current_levels") or {}
    if levels:
        lines.append("current_levels:")
        for attr, rec in levels.items():
            if isinstance(rec, dict):
                lvl = rec.get("level")
                why = rec.get("level_reasoning") or ""
                anchors = rec.get("audit_anchors") or []
            else:
                lvl, why, anchors = rec, "", []
            row = f"  - {attr}: {lvl}"
            if why:
                row += f"  (why: {why})"
            if anchors:
                row += f"\n    audits: {', '.join(anchors)}"
            lines.append(row)
    nl_anchors = main.get("non_level_attribute_anchors") or {}
    if nl_anchors:
        lines.append("non_level_attribute_audits:")
        for attr, rec in nl_anchors.items():
            anchors = rec.get("audit_anchors") or []
            lines.append(
                f"  - {attr}: {', '.join(anchors) if anchors else '(none)'}"
            )
    return "\n".join(lines)


def _format_others_block_v8(others: list[dict]) -> str:
    if not others:
        return "(none)"
    return "\n".join(
        f"  - {o['name']} (ttm: {o['ttm_stage']}; "
        f"nudge: {o.get('system_intent_1line') or '(none)'})"
        for o in others
    )


def _format_retrieved_block_v8(chunks: list[dict]) -> str:
    """Render the BM25-retrieved raw entries as the chronologically-anchored
    evidence block that replaces V7's main_problem.summary_text and
    per-edge summary.

    Each chunk gets one or two lines:
      - attribute_entry: "[s1.t4 attribute • severity@academic_pressure (level)]:
                          inferred_information — concise — \"quote\""
      - connection_entry: "[s1.t12 connection • academic_pressure ↔ sleep_problems
                           (causal: severity↔onset_latency)]: why — \"quote\""
    """
    if not chunks:
        return "(no relevant entries retrieved this turn)"

    lines: list[str] = []
    for c in chunks:
        anchor = c.get("anchor") or "?"
        if c["type"] == "attribute_entry":
            kind = c.get("attribute_kind") or "attribute"
            head = (
                f"  [{anchor} • {c['attribute']}@{c['problem']} ({kind})]"
            )
            quote = (c.get("quote") or "").strip()
            inferred = (c.get("inferred") or "").strip()
            if quote and inferred:
                lines.append(f'{head}: "{quote}"  ·  {inferred}')
            elif quote:
                lines.append(f'{head}: "{quote}"')
            elif inferred:
                lines.append(f"{head}: {inferred}")
            else:
                lines.append(f"{head}: (no content)")
        elif c["type"] == "connection_entry":
            head = (
                f"  [{anchor} • {c['problem_a']} ↔ {c['problem_b']} "
                f"({c['relation_type']}: {c['attribute_a']}↔{c['attribute_b']})]"
            )
            quote = (c.get("quote") or "").strip()
            if quote:
                lines.append(f'{head}  "{quote}"')
            else:
                lines.append(head)
        else:
            lines.append(f"  [{anchor} unknown]: {c.get('text', '')}")
    return "\n".join(lines)


def _format_persona_block_v8(persona: dict) -> str:
    rows: list[str] = []
    for k, v in persona.items():
        if not v:
            continue
        v_str = ", ".join(str(x) for x in v) if isinstance(v, list) else str(v)
        rows.append(f"  {k}: {v_str}")
    return "\n".join(rows) if rows else "(empty)"


def _format_past_two_turns_v8(past_two: list[dict]) -> str:
    if not past_two:
        return "(none)"
    lines: list[str] = []
    for p in past_two:
        lines.append(
            f"  - turn_offset={p.get('turn_offset')}, "
            f"main_problem={p.get('main_problem')}, "
            f"strategies={p.get('strategies')}"
        )
    return "\n".join(lines)


def build_user_prompt_v8(
    *,
    user_intent: str,
    user_intent_phrase: str,
    mi_for_user_intent: str,
    evidence_pack: dict,
    past_two_turns: list[dict],
    current_user_message: str,
) -> str:
    main = evidence_pack.get("main_problem")
    others = evidence_pack.get("other_current_problems", []) or []
    retrieved = evidence_pack.get("rag_retrieved_chunks", []) or []
    persona = evidence_pack.get("persona", {}) or {}
    rolling = evidence_pack.get("rolling_summary_5turns", "") or ""

    return textwrap.dedent(f"""\
        # USER_INTENT (from Agent 1)
        intent: {user_intent}
        phrase: {user_intent_phrase}
        mi_for_user_intent: {mi_for_user_intent}

        # MAIN PROBLEM (graph state only — chronological history is in RETRIEVED EVIDENCE)
        {_format_main_block_v8(main)}

        # OTHER CURRENT PROBLEMS (context only)
        {_format_others_block_v8(others)}

        # RETRIEVED EVIDENCE (MMR-diversified MiniLM cosine over your full
        # memory of past turns; both attribute audits and problem-problem
        # connections; chronologically anchored as sS.tT)
        {_format_retrieved_block_v8(retrieved)}

        # PERSONA
        {_format_persona_block_v8(persona)}

        # ROLLING_SUMMARY_5TURNS (Agent X)
        {rolling or "(none)"}

        # PAST_TWO_TURNS (diversity hint — what MISC was used recently)
        {_format_past_two_turns_v8(past_two_turns)}

        # CURRENT USER MESSAGE
        {current_user_message}

        Return the JSON object now.
    """)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@dataclass
class ResponseV8Inputs:
    user_intent: str
    user_intent_phrase: str
    mi_for_user_intent: str
    evidence_pack: dict
    past_two_turns: list[dict]
    current_user_message: str


def run_response_v8(
    *,
    client: LLMClient,
    ctx: CallContext,
    inputs: ResponseV8Inputs,
) -> dict:
    """Execute V8 Agent 5. Reuses V7's system prompt, schema, validator,
    and fallback. The only thing that differs is the user prompt shape.
    """
    assert ctx.call_role == "agent5_response_v8"
    main = inputs.evidence_pack.get("main_problem") or {}
    expected_mi_system = main.get("mi_for_system_intent") or None

    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt_v8(**inputs.__dict__),
            schema=RESPONSE_V8_SCHEMA,
            validator_extras=_validate_factory(
                expected_mi_user=inputs.mi_for_user_intent,
                expected_mi_system=expected_mi_system,
            ),
        )
    except LLMStructuredError:
        return _safe_fallback(mi_for_user_intent=inputs.mi_for_user_intent)


# ---------------------------------------------------------------------------
# Self-test (validators + prompt rendering — no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    pack = {
        "main_problem": {
            "name": "academic_pressure",
            "ttm_stage": "contemplation",
            "ttm_reasoning": "severity high but ambivalent on action",
            "system_intent": "evoke trade-offs without nudging a plan yet",
            "mi_for_system_intent": "evoke",
            "current_levels": {
                "perceived_severity": {
                    "level": "high",
                    "level_reasoning": "user said the pressure is unbearable",
                    "audit_anchors": ["s1.t1", "s1.t2"],
                },
                "self_efficacy": {
                    "level": "medium", "level_reasoning": "",
                    "audit_anchors": ["s1.t5"],
                },
            },
            "non_level_attribute_anchors": {
                "triggers": {"audit_anchors": ["s1.t1"]},
            },
        },
        "other_current_problems": [
            {"name": "sleep_problems", "ttm_stage": "precontemplation",
             "system_intent_1line": "Build awareness"},
        ],
        "rag_retrieved_chunks": [
            {"type": "attribute_entry", "attribute_kind": "level",
             "problem": "academic_pressure", "attribute": "perceived_severity",
             "anchor": "s1.t4",
             "quote": "the pressure is getting to me",
             "inferred": "severity high — exam season",
             "text": "irrelevant for prompt rendering"},
            {"type": "connection_entry",
             "problem_a": "academic_pressure", "problem_b": "sleep_problems",
             "attribute_a": "perceived_severity", "attribute_b": "onset_latency",
             "relation_type": "causal",
             "anchor": "s1.t12",
             "quote": "my mind keeps replaying revisions",
             "why": "exam pressure delays sleep onset",
             "text": "irrelevant for prompt rendering"},
        ],
        "persona": {"communication_style": "calm, methodical"},
        "rolling_summary_5turns": "user vented about deadline pressure",
    }

    sp = build_system_prompt()
    assert "IDENTITY" in sp and "R1" in sp
    up = build_user_prompt_v8(
        user_intent="express_emotion",
        user_intent_phrase="user wants to feel heard about exam-induced 3am rumination",
        mi_for_user_intent="complex_reflection",
        evidence_pack=pack,
        past_two_turns=[],
        current_user_message="my mind keeps replaying revisions and I can't sleep",
    )
    # Main-problem block: levels but no summary_text; audits surfaced.
    assert "academic_pressure" in up
    assert "perceived_severity: high" in up
    assert "audits: s1.t1, s1.t2" in up, "audit anchors must render"
    assert "summary_text" not in up, (
        "v8 must NOT surface attribute summary_text in the user prompt"
    )
    # RAG block carries both shapes
    assert "RETRIEVED EVIDENCE" in up
    assert "[s1.t4" in up and "perceived_severity@academic_pressure" in up
    assert "[s1.t12" in up and "academic_pressure ↔ sleep_problems" in up
    assert "the pressure is getting to me" in up
    assert "my mind keeps replaying revisions" in up
    # `why` and cosine score must NOT appear in the rendered prompt.
    assert "exam pressure delays sleep onset" not in up
    assert "cos=" not in up
    # Persona + others surface
    assert "calm, methodical" in up
    assert "sleep_problems" in up

    # Fallback is schema-shaped; v7 validators accept it
    fb = _safe_fallback(mi_for_user_intent="support")
    chk = _validate_factory(expected_mi_user="support", expected_mi_system=None)
    chk({k: v for k, v in fb.items() if not k.startswith("_")})

    print("instruction_response_v8 self-test PASSED")


if __name__ == "__main__":
    _self_test()
