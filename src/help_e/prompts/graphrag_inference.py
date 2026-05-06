"""GraphRAG baseline — entity + relationship extraction prompt.

One LLM call per turn. Reads the current user message + recent dialogue,
emits free-text entities and labeled relationships (no vocabulary
constraint — that is the whole point of the baseline).

Schema is intentionally permissive: any string, no enum, no HBM types,
no TTM stages, no problem vocabulary. The output is consumed by
``baselines.graphrag_baseline.graphrag_turn_fn`` and applied to a
``graphrag_state.GraphRAGState``.

This file is purely additive: nothing in the v1/v3/v4/v6 pipelines
imports from here.
"""
from __future__ import annotations

import logging
import textwrap
from typing import Any

from ..llm_client import CallContext, LLMClient


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


GRAPHRAG_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["entities", "relationships"],
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "supporting_utterance_span"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 80},
                    "supporting_utterance_span": {"type": "string"},
                },
            },
        },
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["entity_a", "label", "entity_b",
                             "supporting_utterance_span"],
                "properties": {
                    "entity_a": {"type": "string", "minLength": 1, "maxLength": 80},
                    "label":    {"type": "string", "minLength": 1, "maxLength": 60},
                    "entity_b": {"type": "string", "minLength": 1, "maxLength": 80},
                    "supporting_utterance_span": {"type": "string"},
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def build_system_prompt() -> str:
    return textwrap.dedent("""\
        You are an extraction agent for a GraphRAG-style knowledge graph
        of an emotional-support conversation. Read the user's CURRENT
        TURN and the RECENT DIALOGUE preceding it, and emit a JSON object
        with two fields: `entities` and `relationships`.

        # GUIDELINES

        ENTITIES are concrete things, situations, feelings, or events the
        user has mentioned. Use the user's own language, lightly cleaned
        up. Do NOT normalize to any vocabulary — write them as you see
        them in the transcript.

        Examples of valid entity names (illustrative only):
          "weight gain"
          "thyroid condition"
          "the gym"
          "comments from coworkers"
          "feeling unrecognizable"
          "running"
          "shame at the gym mirror"

        RELATIONSHIPS are directed edges between two entities, with a
        free-text label that captures how they relate. The label is
        whatever phrase fits — write it like you would describe it to
        another person. Common labels you might use: "caused", "is
        triggered by", "make worse", "reminds of", "happens when",
        "since", "interferes with". Do NOT pick from a closed list — use
        whatever phrase is most natural.

        Examples (entity_a, label, entity_b):
          ("thyroid condition", "caused", "weight gain")
          ("comments from coworkers", "make worse", "shame at the gym mirror")
          ("weight gain", "interferes with", "running")

        Each entity and each relationship MUST include a
        `supporting_utterance_span`: a short verbatim quote from the
        user's CURRENT TURN that anchors it. Quote, don't paraphrase.

        # WHAT NOT TO DO

        - Do NOT invent entities the user has not actually mentioned in
          this turn or the recent dialogue.
        - Do NOT label entities with categorical types like
          "perceived_severity" or "TTM stage" — leave them as the
          user's own phrasing.
        - Do NOT generate connections the user did not make. If two
          things came up but the user did not link them, omit the
          relationship.
        - Do NOT emit a relationship where `entity_a == entity_b`.

        # WHAT TO DO IF THERE IS NOTHING TO EXTRACT

        If the current turn is small-talk, a thank-you, or otherwise has
        no extractable content, return:
            {"entities": [], "relationships": []}

        # OUTPUT FORMAT

        Return ONLY valid JSON matching the schema. No prose.

        {
          "entities": [
            {"name": "<string>",
             "supporting_utterance_span": "<verbatim quote>"}
          ],
          "relationships": [
            {"entity_a": "<string>",
             "label":    "<free-text relation>",
             "entity_b": "<string>",
             "supporting_utterance_span": "<verbatim quote>"}
          ]
        }
    """)


def _format_recent_turns(recent_turns: list[dict]) -> str:
    if not recent_turns:
        return "(none — first turn of the conversation)"
    lines: list[str] = []
    for t in recent_turns:
        role = (t.get("role") or "").upper()
        tid = t.get("turn_id")
        text = (t.get("text") or "").strip()
        if not role or not text:
            continue
        prefix = f"[t{tid} {role}]" if tid is not None else f"[{role}]"
        lines.append(f"{prefix}: {text}")
    return "\n".join(lines) if lines else "(empty)"


def build_user_prompt(*, current_message: str, recent_turns: list[dict]) -> str:
    return textwrap.dedent(f"""\
        # RECENT DIALOGUE
        {_format_recent_turns(recent_turns)}

        # CURRENT TURN (user)
        {(current_message or '').strip()}

        Extract entities + relationships from the CURRENT TURN, with
        light context from the RECENT DIALOGUE for disambiguation.
        Return ONLY the JSON object — no other text.
    """).rstrip()


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def _validate_extraction(out: dict) -> None:
    """Light business-rule validation beyond the JSON schema.

    The schema already constrains shape. This function rejects the easy
    correctness mistakes: self-edges and references to entities whose
    name is empty after stripping.
    """
    entities = out.get("entities") or []
    relationships = out.get("relationships") or []

    # Self-edges.
    for r in relationships:
        a = (r.get("entity_a") or "").strip().lower()
        b = (r.get("entity_b") or "").strip().lower()
        if not a or not b:
            raise ValueError("relationship endpoint is empty")
        if a == b:
            raise ValueError(f"self-edge rejected: {a!r} → {b!r}")

    # Names must be non-empty after strip.
    for e in entities:
        if not (e.get("name") or "").strip():
            raise ValueError("entity name is empty after strip")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_graphrag_extraction(
    *,
    client: LLMClient,
    ctx: CallContext,
    current_message: str,
    recent_turns: list[dict],
) -> dict:
    """Execute the GraphRAG extraction call.

    Returns a dict with ``entities: list[dict]`` and
    ``relationships: list[dict]``. On failure (schema mismatch after all
    retries), returns an empty extraction with ``_fallback_default: True``
    so the pipeline can continue without the new entities for this turn.
    """
    assert ctx.call_role == "graphrag_inference", (
        f"run_graphrag_extraction expects call_role='graphrag_inference', "
        f"got {ctx.call_role!r}"
    )
    try:
        out = client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(
                current_message=current_message,
                recent_turns=recent_turns,
            ),
            schema=GRAPHRAG_EXTRACTION_SCHEMA,
            validator_extras=_validate_extraction,
        )
        # Defensive defaults — even if the schema lets a field be missing
        # (it doesn't, but belt + braces), normalize to empty lists so the
        # caller's add_* methods don't NPE.
        out.setdefault("entities", [])
        out.setdefault("relationships", [])
        return out
    except Exception as e:
        log.warning("graphrag_inference failed (%s); using empty extraction", e)
        return {"entities": [], "relationships": [], "_fallback_default": True}


# ---------------------------------------------------------------------------
# Self-test (no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    sp = build_system_prompt()
    assert "GraphRAG" in sp
    assert "perceived_severity" in sp     # mentioned only as a "do NOT" example
    assert "Do NOT" in sp
    assert "supporting_utterance_span" in sp

    up = build_user_prompt(
        current_message="The thyroid is what caused all this weight gain.",
        recent_turns=[],
    )
    assert "RECENT DIALOGUE" in up
    assert "thyroid" in up

    # Validator catches self-edges.
    bad = {"entities": [{"name": "x", "supporting_utterance_span": ""}],
           "relationships": [{"entity_a": "x", "entity_b": "x",
                              "label": "is", "supporting_utterance_span": ""}]}
    try:
        _validate_extraction(bad)
        raise AssertionError("expected self-edge rejection")
    except ValueError:
        pass

    # Validator accepts a clean extraction.
    good = {
        "entities": [{"name": "thyroid condition", "supporting_utterance_span": "thyroid"}],
        "relationships": [{"entity_a": "thyroid condition", "label": "caused",
                           "entity_b": "weight gain",
                           "supporting_utterance_span": "what caused all this"}],
    }
    _validate_extraction(good)

    print("graphrag_inference self-test PASSED")


if __name__ == "__main__":
    _self_test()
