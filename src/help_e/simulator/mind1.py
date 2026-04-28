"""§18.7 — Mind-1: per-turn user-simulator responder.

One call per user turn. Mind-1 speaks AS the user. It has no access to
internal labels (TTM stage, HBM attributes, next_turn_intent); Mind-2 tags
those retrospectively after all sessions complete.

The persona paragraph is re-injected at the start of every session to fight
drift (§8.5). A tiny persona-consistency check runs at session end and can
trigger a session regeneration if drift is severe.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Any, Optional

from ..llm_client import CallContext, LLMClient
from ..prompts.common import format_dialog_turns


MIND1_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["utterance"],
    "properties": {
        "utterance": {"type": "string", "minLength": 1},
    },
}


DRIFT_CHECK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["consistent", "severity", "reason"],
    "properties": {
        "consistent": {"type": "boolean"},
        "severity": {"type": "string", "enum": ["none", "minor", "major"]},
        "reason": {"type": "string"},
    },
}


# ---------------------------------------------------------------------------
# Persona spec
# ---------------------------------------------------------------------------


@dataclass
class Mind1Persona:
    """The user-side spec Mind-1 reads at every turn.

    ``seed_situation_paragraph`` is the verbatim EmoCare paragraph that
    seeded this profile (§8.1). ``persona_draft`` carries the three drafted
    fields that give the persona texture beyond the situation facts.
    """

    profile_id: str
    seed_situation_paragraph: str
    primary_problem: str
    personality_traits: list[str]
    communication_style: str
    relevant_history: str


def _format_persona(p: Mind1Persona) -> str:
    traits = ", ".join(p.personality_traits) if p.personality_traits else "(unspecified)"
    return textwrap.dedent(f"""\
        YOU ARE: profile {p.profile_id}
        SITUATION (this is your life — speak from inside it):
        {p.seed_situation_paragraph}

        PERSONALITY TRAITS: {traits}
        COMMUNICATION STYLE: {p.communication_style}
        RELEVANT HISTORY: {p.relevant_history}
        PRIMARY PROBLEM (the thing you'd keep coming back to): {p.primary_problem}
    """).rstrip()


# ---------------------------------------------------------------------------
# Responder prompt
# ---------------------------------------------------------------------------


def build_system_prompt() -> str:
    return textwrap.dedent("""\
        You are a human user talking to an emotional-support chatbot about
        everyday, non-crisis well-being problems.

        Rules:
        - Stay in your persona. Do not reveal that you are an AI or a
          simulator.
        - Do not name therapy concepts. Never say "TTM stage",
          "motivational interviewing", "health belief model", "action
          stage", "change talk", or similar.
        - Speak naturally in your own voice. Match your communication style.
        - Pace yourself: not every message has to advance the problem. Some
          turns are small, ambivalent, or tangential.
        - Real change is hard — don't suddenly "see the light". If the
          chatbot does help, let it land gradually.
        - Stay mostly on ONE topic per session unless the arc cue suggests
          otherwise.
        - Keep utterances conversational. Usually 1–4 sentences.

        Return ONLY valid JSON matching the schema:
          { "utterance": "<your next message>" }
    """)


def build_user_prompt(
    *,
    persona: Mind1Persona,
    session_id: int,
    turn_id: int,
    session_arc_cue: str,
    prior_session_summary: Optional[str],
    recent_turns: list[dict],
) -> str:
    prior = prior_session_summary or "(no prior session — first session)"
    arc = session_arc_cue or "(no specific arc cue for this session)"
    dialog = format_dialog_turns(recent_turns)
    return textwrap.dedent(f"""\
        {_format_persona(persona)}

        SESSION: #{session_id}    TURN (about to produce): t{turn_id}
        SESSION ARC CUE: {arc}

        PRIOR-SESSION SUMMARY: {prior}

        RECENT DIALOGUE (this session):
        {dialog}

        Produce your next user utterance now as JSON with a single key
        `utterance`. Stay in persona.
    """)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_mind1(
    *,
    client: LLMClient,
    ctx: CallContext,
    persona: Mind1Persona,
    session_arc_cue: str,
    prior_session_summary: Optional[str],
    recent_turns: list[dict],
) -> dict:
    """Returns ``{'utterance': str, 'long_venting': bool}`` on success.

    Mind-1 is the only call where we swallow total failure with a soft
    placeholder — without a user utterance the turn can't proceed, and
    Mind-1 drift is an eval-side concern rather than a pipeline-blocker.
    """
    assert ctx.call_role == "mind1"

    try:
        out = client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(
                persona=persona,
                session_id=ctx.session_id,
                turn_id=ctx.turn_id,
                session_arc_cue=session_arc_cue,
                prior_session_summary=prior_session_summary,
                recent_turns=recent_turns,
            ),
            schema=MIND1_SCHEMA,
        )
    except Exception:
        return {
            "utterance": "I don't know. I guess I'm just tired.",
            "long_venting": False,
            "_fallback_default": True,
        }

    text = out["utterance"].strip()
    sentence_count = sum(text.count(c) for c in ".!?")
    return {"utterance": text, "long_venting": sentence_count > 5}


# ---------------------------------------------------------------------------
# Session-end drift check
# ---------------------------------------------------------------------------


def _drift_system_prompt() -> str:
    return textwrap.dedent("""\
        You are a quality-check reader. Given a user-persona spec and every
        user utterance from one session, decide whether the user stayed in
        persona and on-topic.

        Flag as "major" only when the persona clearly broke (spoke as an
        AI, named therapy concepts, changed occupation/demographics,
        switched communication style dramatically). Mild topic drift is
        "minor". Consistent => "none".

        Return JSON: { "consistent": bool, "severity": "none"|"minor"|"major",
        "reason": "<one sentence>" }.
    """)


def run_drift_check(
    *,
    client: LLMClient,
    ctx: CallContext,
    persona: Mind1Persona,
    user_utterances: list[str],
) -> dict:
    """Post-session persona-consistency pass.

    Intended to run with ``ctx.call_role='mind1'`` but at session end; the
    session driver decides whether to regenerate the session when severity
    is ``major`` (§13 Mind-1 row).
    """
    uttr_block = "\n".join(f"  [t{i}] {u}" for i, u in enumerate(user_utterances))
    user_prompt = textwrap.dedent(f"""\
        {_format_persona(persona)}

        USER UTTERANCES FROM SESSION {ctx.session_id}:
        {uttr_block}

        Judge consistency.
    """)
    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=_drift_system_prompt(),
            user_prompt=user_prompt,
            schema=DRIFT_CHECK_SCHEMA,
        )
    except Exception:
        # Drift check failing shouldn't break the pipeline; assume OK.
        return {"consistent": True, "severity": "none", "reason": "(drift check failed)"}
