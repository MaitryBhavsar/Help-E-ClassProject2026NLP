"""v7 Agent P — session-end persona update on the BIG model.

ONE call per session, AFTER all turns + judges have fired. Reads the
existing persona (9 fields) + the full session transcript + the final
graph state. Returns one record per persona field with the simplified
shape:

    {
      "field": "<one of the 9 names>",
      "useful": 0 | 1,
      "updated_value": "<whole new value, integrated>" | null,
      "evidence_quote": "<the single transcript quote that justifies>" | null
    }

If useful = 0, the field stays unchanged. Otherwise updated_value
overwrites the prior value entirely (no diff, no append). For
list-valued fields the value is a comma-separated string; the caller
splits it.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Any

from ..llm_client import CallContext, LLMClient, LLMStructuredError
from .common_v6 import (
    format_persona_v6,
    persona_v6_field_block,
    persona_v6_field_names,
)


_PERSONA_FIELDS: tuple[str, ...] = persona_v6_field_names()

_LIST_FIELDS: frozenset[str] = frozenset({
    "personality_traits", "core_values", "core_beliefs",
    "hobbies_interests", "general_behavioral_traits",
})


# ---------------------------------------------------------------------------
# JSON schema
# ---------------------------------------------------------------------------

AGENT_P_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["updates"],
    "properties": {
        "updates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["field", "useful", "updated_value", "evidence_quote"],
                "properties": {
                    "field": {"type": "string", "enum": list(_PERSONA_FIELDS)},
                    "useful": {"type": "integer", "enum": [0, 1]},
                    "updated_value": {"type": ["string", "null"]},
                    "evidence_quote": {"type": ["string", "null"]},
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def build_system_prompt() -> str:
    return textwrap.dedent(f"""\
        You are AGENT P (PERSONA UPDATE) of the v7 HELP-E pipeline. Once
        per session, after the full transcript has unfolded, decide for
        each of the 9 persona fields whether the session produced new
        evidence that materially refines what we know about this person.

        # PERSONA FIELDS (use these exact field names)

        {persona_v6_field_block()}

        # WHAT TO DO

        For EACH of the 9 fields, return ONE record with:

          - useful: 1 if this session produced evidence that materially
                       changes or refines the existing value (or
                       populates an empty field with a grounded value).
                    0 if there is no new evidence worth writing — the
                       field stays exactly as it was.

          - updated_value (when useful=1): the WHOLE NEW VALUE for the
                       field, integrating prior content and new
                       evidence. Plain text. No diff format. For
                       list-valued fields (personality_traits,
                       core_values, core_beliefs, hobbies_interests,
                       general_behavioral_traits) write a single
                       comma-separated string of items. When useful=0,
                       updated_value is null.

          - evidence_quote (when useful=1): a SINGLE concise quote (≤25
                       words) from the user's transcript that justifies
                       the update. When useful=0, evidence_quote is null.

        # CONSERVATIVE DEFAULT

        Default to useful=0 unless this session genuinely added or
        sharpened the field. Single passing references that don't change
        the picture get useful=0. The persona is meant to stabilize over
        time, not chase every utterance.

        # WHAT NOT TO DO

        - Do NOT invent disclosures the transcript doesn't support.
        - Do NOT use clinical labels (anxious-attachment, perfectionist
          schema, etc.) — write in plain language.
        - Do NOT diagnose. Describe behavior + style as the user expressed
          it.

        # OUTPUT

        Return ONE JSON object:

        {{
          "updates": [
            {{
              "field": "demographics",
              "useful": 0,
              "updated_value": null,
              "evidence_quote": null
            }},
            {{
              "field": "communication_style",
              "useful": 1,
              "updated_value": "calm but newly self-deprecating under pressure",
              "evidence_quote": "I'm doubting if I can keep doing this job"
            }},
            ...
          ]
        }}

        Emit EXACTLY one record for each of the 9 fields, in any order.

        Return ONLY JSON.
    """)


def build_user_prompt(
    *, transcript: list[dict], current_persona: dict,
) -> str:
    persona_block = format_persona_v6(current_persona).strip() or "(empty — first session)"
    # Render the transcript inline (turn-by-turn).
    lines: list[str] = []
    for t in transcript:
        role = t.get("role", "?")
        turn_id = t.get("turn_id", "?")
        text = (t.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"  [t{turn_id} {role.upper()}] {text}")
    transcript_block = "\n".join(lines) if lines else "(empty)"

    return textwrap.dedent(f"""\
        CURRENT_PERSONA:
        {persona_block}

        SESSION_TRANSCRIPT:
        {transcript_block}

        Return the JSON object now. Emit one record per persona field.
    """)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def validate_agent_p(out: dict) -> None:
    updates = out["updates"]
    if len(updates) != len(_PERSONA_FIELDS):
        raise ValueError(
            f"updates must contain exactly {len(_PERSONA_FIELDS)} records "
            f"(one per field), got {len(updates)}"
        )
    seen: set[str] = set()
    for u in updates:
        f = u["field"]
        if f in seen:
            raise ValueError(f"duplicate update for field {f!r}")
        seen.add(f)
        if u["useful"] == 1:
            if not u.get("updated_value") or not u["updated_value"].strip():
                raise ValueError(
                    f"useful=1 for {f!r} but updated_value is empty"
                )
            if not u.get("evidence_quote") or not u["evidence_quote"].strip():
                raise ValueError(
                    f"useful=1 for {f!r} but evidence_quote is empty"
                )
        else:
            # useful=0 → both null
            if u.get("updated_value") is not None:
                raise ValueError(
                    f"useful=0 for {f!r} but updated_value is non-null"
                )
            if u.get("evidence_quote") is not None:
                raise ValueError(
                    f"useful=0 for {f!r} but evidence_quote is non-null"
                )
    if seen != set(_PERSONA_FIELDS):
        missing = set(_PERSONA_FIELDS) - seen
        raise ValueError(f"missing field records: {sorted(missing)}")


# ---------------------------------------------------------------------------
# Apply helper
# ---------------------------------------------------------------------------


def apply_updates_to_persona(
    persona_dict: dict, updates: list[dict],
) -> dict:
    """Apply Agent P's updates. Only useful=1 fields overwrite.

    List-valued fields get their comma-separated `updated_value` split
    on commas + trimmed. Scalar fields take `updated_value` verbatim.
    """
    out = dict(persona_dict)
    for u in updates:
        if u["useful"] != 1:
            continue
        field = u["field"]
        new_value = u["updated_value"]
        if not new_value:
            continue
        if field in _LIST_FIELDS:
            items = [s.strip() for s in new_value.split(",") if s.strip()]
            out[field] = items
        else:
            out[field] = new_value
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@dataclass
class AgentPInputs:
    transcript: list[dict]
    current_persona: dict


def _safe_fallback() -> dict:
    """Keep all fields unchanged on total failure."""
    return {
        "updates": [
            {"field": f, "useful": 0, "updated_value": None,
             "evidence_quote": None}
            for f in _PERSONA_FIELDS
        ],
        "_fallback_default": True,
    }


def run_agent_p(
    *, client: LLMClient, ctx: CallContext, inputs: AgentPInputs,
) -> dict:
    assert ctx.call_role == "agent_p_persona_update"
    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(**inputs.__dict__),
            schema=AGENT_P_SCHEMA,
            validator_extras=validate_agent_p,
        )
    except LLMStructuredError:
        return _safe_fallback()


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _self_test() -> None:
    all_keep = {
        "updates": [
            {"field": f, "useful": 0, "updated_value": None,
             "evidence_quote": None}
            for f in _PERSONA_FIELDS
        ],
    }
    validate_agent_p(all_keep)

    # Missing a field → reject.
    short = {"updates": all_keep["updates"][:-1]}
    try:
        validate_agent_p(short)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "exactly" in str(e)

    # Duplicate field → reject.
    dup = {"updates": all_keep["updates"] + [all_keep["updates"][0]]}
    try:
        validate_agent_p(dup)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "duplicate" in str(e) or "exactly" in str(e)

    # useful=1 with null updated_value → reject.
    bad_use1 = {
        "updates": [
            {"field": f, "useful": 0, "updated_value": None, "evidence_quote": None}
            for f in _PERSONA_FIELDS[1:]
        ] + [
            {"field": _PERSONA_FIELDS[0], "useful": 1,
             "updated_value": None, "evidence_quote": "x"},
        ],
    }
    try:
        validate_agent_p(bad_use1)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "updated_value is empty" in str(e)

    # useful=0 with non-null updated_value → reject.
    bad_use0 = {
        "updates": [
            {"field": f, "useful": 0, "updated_value": None, "evidence_quote": None}
            for f in _PERSONA_FIELDS[1:]
        ] + [
            {"field": _PERSONA_FIELDS[0], "useful": 0,
             "updated_value": "x", "evidence_quote": None},
        ],
    }
    try:
        validate_agent_p(bad_use0)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "non-null" in str(e)

    # Valid mixed
    mixed_updates = []
    for i, f in enumerate(_PERSONA_FIELDS):
        if i == 0:
            mixed_updates.append({
                "field": f, "useful": 1,
                "updated_value": "calm and methodical",
                "evidence_quote": "I always like to think before I act.",
            })
        else:
            mixed_updates.append({
                "field": f, "useful": 0,
                "updated_value": None, "evidence_quote": None,
            })
    validate_agent_p({"updates": mixed_updates})

    # Apply helper
    persona = {f: ("" if f not in _LIST_FIELDS else []) for f in _PERSONA_FIELDS}
    updates = [
        {"field": "communication_style", "useful": 1,
         "updated_value": "calm and methodical",
         "evidence_quote": "x"},
        {"field": "hobbies_interests", "useful": 1,
         "updated_value": "running, drawing, podcasts",
         "evidence_quote": "y"},
        {"field": "demographics", "useful": 0,
         "updated_value": None, "evidence_quote": None},
    ]
    after = apply_updates_to_persona(persona, updates)
    assert after["communication_style"] == "calm and methodical"
    assert after["hobbies_interests"] == ["running", "drawing", "podcasts"]
    assert after["demographics"] == ""

    # Prompts render
    sp = build_system_prompt()
    assert "AGENT P" in sp
    assert "communication_style" in sp
    up = build_user_prompt(
        transcript=[
            {"role": "user", "turn_id": 1, "text": "I keep doubting myself."},
            {"role": "assistant", "turn_id": 1, "text": "That's heavy."},
        ],
        current_persona={
            "demographics": "30s, designer",
            "personality_traits": ["conscientious"],
            "core_values": [], "core_beliefs": [],
            "support_system": "lives alone",
            "hobbies_interests": [], "communication_style": "calm",
            "relevant_history": "",
            "general_behavioral_traits": [],
        },
    )
    assert "30s, designer" in up
    assert "I keep doubting myself" in up

    # Fallback is valid
    fb = _safe_fallback()
    validate_agent_p({k: v for k, v in fb.items() if not k.startswith("_")})

    print("agent_p_persona_update self-test PASSED")


if __name__ == "__main__":
    _self_test()
