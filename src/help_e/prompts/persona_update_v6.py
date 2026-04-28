"""§18.5 v6 — Session-end persona update.

Extends v5 persona update with one new field, `general_behavioral_traits`
(free-text traits that shape how the user engages with help — rigid,
inquisitive, overthinker, emotionally reactive, etc.). Nine fields total.

Two principles matter here:

  1. Cold-start aware. The system has NO initial profile; on session 1
     the current persona is entirely empty. The prompt explicitly lists
     the empty state and instructs the agent to fill only what the
     transcript justifies.

  2. Conservative inference. Four fields require ≥2 consistent evidence
     citations before an update is accepted: personality_traits,
     core_values, core_beliefs, and general_behavioral_traits. Single
     strong statements for these four are demoted to "keep" in
     post-processing. Lifestyle/disclosure fields (demographics,
     support_system, hobbies_interests, relevant_history) update on a
     single explicit disclosure. Communication_style changes only on
     marked shift.

List-valued fields (personality_traits, core_values, core_beliefs,
hobbies_interests, general_behavioral_traits) are sent back as a single
comma-separated string in `new_value`; the caller parses.
"""
from __future__ import annotations

import textwrap
from typing import Any

from ..llm_client import CallContext, LLMClient
from .common import PROJECT_IDENTITY, format_dialog_turns
from .common_v6 import (
    format_persona_v6,
    persona_v6_field_block,
    persona_v6_field_names,
)

_PERSONA_V6_FIELDS = persona_v6_field_names()


PERSONA_UPDATE_V6_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["updates"],
    "properties": {
        "updates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["field", "action", "new_value", "evidence_citations"],
                "properties": {
                    "field": {"type": "string", "enum": list(_PERSONA_V6_FIELDS)},
                    "action": {"type": "string", "enum": ["keep", "update"]},
                    "new_value": {"type": ["string", "null"]},
                    "evidence_citations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["turn_id", "excerpt"],
                            "properties": {
                                "turn_id": {"type": "integer", "minimum": 0},
                                "excerpt": {"type": "string", "minLength": 1},
                            },
                        },
                    },
                },
            },
        },
    },
}


_STRICT_FIELDS: frozenset[str] = frozenset({
    "personality_traits",
    "core_values",
    "core_beliefs",
    "general_behavioral_traits",
})


def build_system_prompt() -> str:
    return textwrap.dedent(f"""\
        {PROJECT_IDENTITY}

        You are the PERSONA-UPDATE agent (§18.5 v6). Once per session, you
        review the full transcript and decide whether to KEEP or UPDATE
        each of the 9 persona fields. The system had NO initial knowledge
        of this user — every field starts empty and is filled only from
        what the conversation reveals.

        {persona_v6_field_block()}

        # WEIGHTING GUIDANCE

        - Stable markers override temporary markers.
        - A single strong statement is weaker than multiple consistent
          statements.
        - Session-specific moods are temporary; lifestyle patterns are
          stable.
        - `demographics` updates only on explicit self-disclosure.
        - `support_system`, `hobbies_interests`, `relevant_history` update
          on explicit disclosure.
        - `communication_style` changes gradually; only change if this
          session differs markedly from the prior value (or is the first
          time a distinct style is clearly observable).
        - STRICT fields (personality_traits, core_values, core_beliefs,
          general_behavioral_traits) require ≥2 consistent citations
          across different turns before updating. A single statement is
          not enough.
        - `general_behavioral_traits` is FREE-TEXT — do NOT pick from a
          closed vocabulary. Examples of traits: rigid, inquisitive,
          overthinker, emotionally reactive, avoidant, impulsive,
          reflective, concrete thinker, self-critical. Use other words
          when they fit.

        # COLD-START HANDLING

        If the CURRENT PERSONA block says "no persona info yet", every
        field starts null/empty. The same rules apply — strict fields
        still require ≥2 citations — but an update from empty → something
        is expected wherever the transcript supports it.

        # OUTPUT CONTRACT (per field)

        - `action`: "keep" or "update".
        - If "update": `new_value` is a non-empty string AND
          `evidence_citations` has at least one citation (at least TWO
          for STRICT fields).
        - If "keep": `new_value` is null and `evidence_citations` may be
          [].
        - For list-valued fields (personality_traits, core_values,
          core_beliefs, hobbies_interests, general_behavioral_traits),
          `new_value` is a SINGLE COMMA-SEPARATED STRING like
          "analytical, self-critical, avoidant" — the caller parses.
        - Return exactly 9 entries, one per field.

        # REQUIRED JSON SHAPE

        {{
          "updates": [
            {{
              "field": "personality_traits",
              "action": "update",
              "new_value": "analytical, self-critical, introverted",
              "evidence_citations": [
                {{"turn_id": 2, "excerpt": "I always overthink everything before I act."}},
                {{"turn_id": 5, "excerpt": "I'd rather sit alone than go to a party."}}
              ]
            }},
            {{
              "field": "general_behavioral_traits",
              "action": "update",
              "new_value": "overthinker, emotionally reactive",
              "evidence_citations": [
                {{"turn_id": 2, "excerpt": "I keep replaying it for hours"}},
                {{"turn_id": 4, "excerpt": "I snapped and regretted it immediately"}}
              ]
            }},
            {{
              "field": "demographics",
              "action": "keep",
              "new_value": null,
              "evidence_citations": []
            }}
          ]
        }}

        Hard rules:
        - `turn_id` MUST be an integer.
        - Each evidence citation is an OBJECT with `turn_id` and
          `excerpt` — never a [turn_id, excerpt] tuple.
        - Only `updates` at the top level. Every field record goes inside
          the array.
        - Return ONLY valid JSON matching the schema.
    """)


def build_user_prompt(
    *,
    transcript: list[dict],
    current_persona: dict,
) -> str:
    return textwrap.dedent(f"""\
        CURRENT PERSONA (before this session):
        {format_persona_v6(current_persona)}

        FULL SESSION TRANSCRIPT:
        {format_dialog_turns(transcript)}

        Decide the update for each of the 9 persona fields now. Remember
        the ≥2-citation requirement for personality_traits, core_values,
        core_beliefs, and general_behavioral_traits.
    """)


def _validate_updates(out: dict) -> None:
    fields = [u["field"] for u in out["updates"]]
    if len(fields) != len(_PERSONA_V6_FIELDS):
        raise ValueError(
            f"expected {len(_PERSONA_V6_FIELDS)} updates, got {len(fields)}"
        )
    if set(fields) != set(_PERSONA_V6_FIELDS):
        raise ValueError(
            "updates must cover each persona field exactly once"
        )
    for u in out["updates"]:
        if u["action"] == "update":
            if not u.get("new_value"):
                raise ValueError(f"{u['field']}: update requires new_value")
            if not u.get("evidence_citations"):
                raise ValueError(
                    f"{u['field']}: update requires at least one evidence_citation"
                )


def run_persona_update_v6(
    *,
    client: LLMClient,
    ctx: CallContext,
    transcript: list[dict],
    current_persona: dict,
) -> dict:
    assert ctx.call_role == "persona_update_v6"
    out = client.generate_structured(
        ctx=ctx,
        system_prompt=build_system_prompt(),
        user_prompt=build_user_prompt(
            transcript=transcript, current_persona=current_persona,
        ),
        schema=PERSONA_UPDATE_V6_SCHEMA,
        validator_extras=_validate_updates,
    )

    # Enforce the ≥2-citation rule for strict fields — demote to keep.
    for u in out["updates"]:
        if (u["field"] in _STRICT_FIELDS
                and u["action"] == "update"
                and len(u["evidence_citations"]) < 2):
            u["action"] = "keep"
            u["new_value"] = None
    return out


def apply_updates_to_persona(
    persona_dict: dict, updates: list[dict]
) -> dict:
    """Apply an `updates` list to a persona dict in place-safe fashion.

    List-valued fields get their comma-separated new_value split on ", "
    and trimmed. Scalar fields take new_value verbatim. `keep` actions
    are no-ops.

    Caller is responsible for writing the returned dict back into
    `ProblemGraphV6.persona` via `PersonaState(**dict)` or field-by-field.
    """
    out = dict(persona_dict)
    _LIST_FIELDS = {
        "personality_traits", "core_values", "core_beliefs",
        "hobbies_interests", "general_behavioral_traits",
    }
    for u in updates:
        if u["action"] != "update":
            continue
        field = u["field"]
        new_value = u["new_value"]
        if new_value is None:
            continue
        if field in _LIST_FIELDS:
            items = [s.strip() for s in new_value.split(",") if s.strip()]
            out[field] = items
        else:
            out[field] = new_value
    return out


# ---------------------------------------------------------------------------
# Self-test (validator + apply_updates only)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    good = {
        "updates": [
            {"field": f, "action": "keep", "new_value": None, "evidence_citations": []}
            for f in _PERSONA_V6_FIELDS
        ],
    }
    _validate_updates(good)

    # Wrong count — rejected.
    short = {"updates": good["updates"][:5]}
    try:
        _validate_updates(short)
    except ValueError:
        pass
    else:
        raise AssertionError("expected rejection: wrong update count")

    # Duplicate field — rejected (count is right but set differs).
    dup = {"updates": good["updates"][:-1] + [good["updates"][0]]}
    try:
        _validate_updates(dup)
    except ValueError:
        pass
    else:
        raise AssertionError("expected rejection: duplicate field")

    # update with no new_value — rejected.
    bad_upd = dict(good)
    bad_upd["updates"] = list(good["updates"])
    bad_upd["updates"][0] = {
        "field": "demographics", "action": "update",
        "new_value": None, "evidence_citations": [{"turn_id": 1, "excerpt": "x"}],
    }
    try:
        _validate_updates(bad_upd)
    except ValueError:
        pass
    else:
        raise AssertionError("expected rejection: update without new_value")

    # apply_updates: single citation on strict field does NOT flip to update.
    # (Simulate the post-process step.)
    raw = {
        "updates": [
            {"field": "personality_traits", "action": "update",
             "new_value": "analytical",
             "evidence_citations": [{"turn_id": 1, "excerpt": "I always overthink"}]},
        ],
    }
    # Simulate run_persona_update_v6's post-process demotion.
    for u in raw["updates"]:
        if (u["field"] in _STRICT_FIELDS and u["action"] == "update"
                and len(u["evidence_citations"]) < 2):
            u["action"] = "keep"
            u["new_value"] = None
    assert raw["updates"][0]["action"] == "keep", \
        "single-citation strict update should demote to keep"

    # Strict field with 2 citations: update survives.
    raw2 = {
        "updates": [
            {"field": "general_behavioral_traits", "action": "update",
             "new_value": "overthinker, self-critical",
             "evidence_citations": [
                 {"turn_id": 1, "excerpt": "replayed it for hours"},
                 {"turn_id": 3, "excerpt": "beat myself up over nothing"},
             ]},
        ],
    }
    for u in raw2["updates"]:
        if (u["field"] in _STRICT_FIELDS and u["action"] == "update"
                and len(u["evidence_citations"]) < 2):
            u["action"] = "keep"
            u["new_value"] = None
    assert raw2["updates"][0]["action"] == "update"

    # apply_updates: list field splits correctly; scalar verbatim.
    persona_before = {
        "demographics": None, "personality_traits": [], "core_values": [],
        "core_beliefs": [], "support_system": None, "hobbies_interests": [],
        "communication_style": None, "relevant_history": None,
        "general_behavioral_traits": [],
    }
    updates = [
        {"field": "general_behavioral_traits", "action": "update",
         "new_value": "overthinker, self-critical",
         "evidence_citations": [{"turn_id": 1, "excerpt": "x"}, {"turn_id": 2, "excerpt": "y"}]},
        {"field": "communication_style", "action": "update",
         "new_value": "guarded, intellectualizing",
         "evidence_citations": [{"turn_id": 1, "excerpt": "x"}]},
        {"field": "demographics", "action": "keep",
         "new_value": None, "evidence_citations": []},
    ]
    after = apply_updates_to_persona(persona_before, updates)
    assert after["general_behavioral_traits"] == ["overthinker", "self-critical"]
    assert after["communication_style"] == "guarded, intellectualizing"
    assert after["demographics"] is None

    # Prompt assembles cleanly (including empty persona cold-start).
    sp = build_system_prompt()
    assert "PERSONA-UPDATE agent" in sp and "cold-start" in sp.lower()
    up = build_user_prompt(transcript=[], current_persona={})
    assert "no persona info yet" in up

    print("persona_update_v6 self-test PASSED")


if __name__ == "__main__":
    _self_test()
