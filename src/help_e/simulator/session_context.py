"""§13 v6 (REDESIGN) — Session-context summary generator (simulator side).

ONE LLM call per session, at session start. Produces a hidden per-session
framing — current life events, mood, emotions, active problems from the
20-vocab, cooperation/resistance — that Mind-1 reads each turn to keep
utterances coherent across the session.

The chatbot NEVER sees this; it stays simulator-side.

Inputs (per redesign §9.4):
  - profile: a `SimulatorProfile` (the new 7-field shape — §9.3)
  - the 20-problem vocabulary (for grounding `currently_active_problems`)

Sessions are independent for the simulator: there is no
`prior_session_summaries` carryforward in v6 redesign (§9.2).

Output shape (see SESSION_CONTEXT_SCHEMA):

    {
      "current_life_events": str,        # 2–4 sentences framing today
      "mental_state": str,               # 1–2 sentences
      "mood": str,                       # short phrase
      "emotions": [str, ...],            # 1–5 short tags
      "resistance_cooperation_level": str,   # low | medium | high
      "currently_active_problems": [str, ...],   # subset of 20-vocab
      "why_bringing_these_up_now": str,  # 1–2 sentences
    }
"""
from __future__ import annotations

import json
import logging
import textwrap
from dataclasses import dataclass, field
from typing import Any, Optional

from .. import config
from ..config import PROBLEM_VOCAB, RESISTANCE_LEVELS
from ..llm_client import CallContext, LLMClient
from ..prompts.common import problem_vocab_block

log = logging.getLogger(__name__)


SESSION_CONTEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "current_life_events", "mental_state", "mood", "emotions",
        "resistance_cooperation_level",
        "currently_active_problems", "why_bringing_these_up_now",
    ],
    "properties": {
        "current_life_events": {"type": "string", "minLength": 1},
        "mental_state": {"type": "string", "minLength": 1},
        "mood": {"type": "string", "minLength": 1},
        "emotions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": {"type": "string", "minLength": 1},
        },
        "resistance_cooperation_level": {
            "type": "string", "enum": list(RESISTANCE_LEVELS),
        },
        "currently_active_problems": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "enum": list(PROBLEM_VOCAB)},
            "uniqueItems": True,
        },
        "why_bringing_these_up_now": {"type": "string", "minLength": 1},
    },
}


# ---------------------------------------------------------------------------
# Profile spec — simulator-only input (v6 redesign §9.3)
# ---------------------------------------------------------------------------


@dataclass
class SimulatorProfile:
    """Profile data available to the simulator ONLY.

    7 fields per redesign §9.3. No `seed_situation_paragraph`, no
    `primary_problem` — the simulator no longer has a fixed problem
    situation; `currently_active_problems` for any given session is
    decided by the per-session `session_context`.

    `communication_style` is a list of descriptors so a single user can
    capture orthogonal axes (length, emotion-density, structure,
    vocabulary, mood pattern, openness) at once — see §9.3.
    """
    profile_id: str
    demographics: dict[str, str] = field(default_factory=dict)
    personality_traits: list[str] = field(default_factory=list)
    communication_style: list[str] = field(default_factory=list)
    core_beliefs: list[str] = field(default_factory=list)
    hobbies_interests: list[str] = field(default_factory=list)
    relevant_history: str = ""


def _format_list(items: list[str]) -> str:
    return ", ".join(items) if items else "(none)"


def _format_demographics(d: dict[str, str]) -> str:
    if not d:
        return "(unspecified)"
    parts = [f"{k}={v}" for k, v in d.items() if v]
    return ", ".join(parts) if parts else "(unspecified)"


def _format_profile(p: SimulatorProfile) -> str:
    history = p.relevant_history.strip() if p.relevant_history else "(none)"
    return textwrap.dedent(f"""\
        Profile {p.profile_id}
        Demographics: {_format_demographics(p.demographics)}
        Personality traits: {_format_list(p.personality_traits)}
        Communication style: {_format_list(p.communication_style)}
        Core beliefs: {_format_list(p.core_beliefs)}
        Hobbies / interests (with likes & dislikes): {_format_list(p.hobbies_interests)}
        Relevant history: {history}
    """).rstrip()


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def build_system_prompt() -> str:
    return textwrap.dedent(f"""\
        You are the SESSION-CONTEXT generator for a user simulator.

        Your job: before the user starts talking to an emotional-support
        chatbot, produce a hidden one-session framing that describes WHY
        the user is walking in TODAY — what is happening in their life
        this week, how they feel, what they might bring up, how
        cooperative they are likely to be.

        This framing is NEVER shown to the chatbot. It is read only by
        the user simulator to keep its per-turn utterances coherent
        across the session.

        # WHAT TO PRODUCE

        Imagine the user is coming to talk to the chatbot at DIFFERENT
        time intervals across multiple sessions. Each session should feel
        DIFFERENT — a different week, a different trigger, a slightly
        different mood — but consistent with their profile.

        Fields:
          - current_life_events: 2–4 sentences describing what is
            happening in the user's life this session. Concrete, not
            abstract.
          - mental_state: 1–2 sentences on overall mental state
            this session.
          - mood: a short phrase (e.g. "anxious and tired", "cautiously
            hopeful", "irritable"). Not a paragraph.
          - emotions: 1–5 short tags (e.g. ["anxious", "guilty",
            "defeated"]).
          - resistance_cooperation_level: one of {list(RESISTANCE_LEVELS)}.
              * low: user is open, willing to explore, cooperative
              * medium: user is somewhat guarded, mixed signals
              * high: user is resistant, minimizing, short-answered,
                reluctant to engage
          - currently_active_problems: subset of the 20-vocabulary below
            that the user is likely to bring up TODAY. Usually 1–3 items.
            Must include at least one. Choose problems consistent with
            the user's life and personality, and let what's "active" vary
            across sessions.
          - why_bringing_these_up_now: 1–2 sentences explaining why THIS
            session's life events make THESE problems salient today.

        # VOCABULARY (only these strings count for currently_active_problems)

        {problem_vocab_block()}

        # CONSTRAINTS

        - Be grounded in the profile — life events, mood, and active
          problems must be plausible for THIS person.
        - Keep currently_active_problems small (1–3 items typical).
        - resistance_cooperation_level should vary across sessions for
          the same user — do not default everyone to "low".
        - Sessions are independent — do not assume narrative
          continuity from prior sessions.

        # REQUIRED JSON SHAPE

        {{
          "current_life_events": "It's the middle of finals week; three nights of almost no sleep; supervisor asked for two extra shifts on top of studying.",
          "mental_state": "Running on fumes; emotionally raw; doubting they can finish the term.",
          "mood": "exhausted and overwhelmed",
          "emotions": ["anxious", "defeated", "irritable"],
          "resistance_cooperation_level": "medium",
          "currently_active_problems": ["academic_pressure", "sleep_problems", "financial_stress"],
          "why_bringing_these_up_now": "Finals + job pressure is the trigger; sleep loss pushed them to reach out."
        }}

        # HARD RULES — VIOLATIONS WASTE A FULL LLM ROUND-TRIP

        1. OUTPUT JSON ONLY. Begin your response IMMEDIATELY with `{{`.
           No prose before the JSON. No markdown code fences. No
           commentary after.

        2. Keep field values BRIEF — 1-2 sentences each, never more.
           Verbose values eat your token budget and cause truncation.

        3. `currently_active_problems` MUST be a non-empty subset of
           the 20-vocabulary above. Do NOT invent close-but-off-list
           strings (e.g. `conflicts_with_sister` is invalid; use
           `conflicts_with_partner` for sibling/in-law/business-partner
           conflict, `conflicts_with_parents` for parent conflict,
           `conflicts_with_friends` for peer conflict).

        4. `resistance_cooperation_level` MUST be exactly one of:
           {list(RESISTANCE_LEVELS)}.

        Return ONLY valid JSON matching the schema. Begin with `{{`.
    """)


def _format_prev_session_context(prev: Optional[dict]) -> str:
    if not prev:
        return "(none — this is the very first session for this user)"
    emotions = ", ".join(prev.get("emotions", []))
    active = ", ".join(prev.get("currently_active_problems", []))
    return textwrap.dedent(f"""\
        Last session's life events: {prev.get('current_life_events', '')}
        Last session's mood: {prev.get('mood', '')}
        Last session's emotions: {emotions}
        Last session's active problems: {active}
        Why those problems were up: {prev.get('why_bringing_these_up_now', '')}
    """).rstrip()


def build_user_prompt(
    *,
    profile: SimulatorProfile,
    session_id: int,
    seed_problems: Optional[list[str]] = None,
    prev_session_context: Optional[dict] = None,
) -> str:
    seed_block = (
        ", ".join(seed_problems)
        if seed_problems else "(generator picks freely from the 20-vocab)"
    )
    return textwrap.dedent(f"""\
        SESSION NUMBER: {session_id} (1 = very first time this user talks
        to the chatbot)

        USER PROFILE (simulator-only; the chatbot does NOT see this):
        {_format_profile(profile)}

        PREVIOUS SESSION CONTEXT (for chronological continuity — the user
        is the SAME person coming back at a later point):
        {_format_prev_session_context(prev_session_context)}

        SEED PROBLEMS for THIS session (set by the curriculum builder —
        `currently_active_problems` MUST be exactly this list):
        {seed_block}

        Produce a hidden session-context framing for THIS session. If
        SEED PROBLEMS are listed, your `currently_active_problems` MUST
        match them exactly (no additions, no omissions, no reordering of
        meaning). Build `current_life_events`, `mental_state`, `mood`,
        `emotions`, `resistance_cooperation_level`, and
        `why_bringing_these_up_now` AROUND those problems and consistent
        with the profile + previous session context.
    """)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def _validate_session_context(out: dict) -> None:
    probs = out["currently_active_problems"]
    if len(set(probs)) != len(probs):
        raise ValueError(
            f"currently_active_problems must be unique: {probs}"
        )
    if len(probs) > 7:
        raise ValueError(
            f"currently_active_problems should be ≤ 7 items; got {len(probs)}"
        )
    if not out["emotions"]:
        raise ValueError("emotions must be non-empty")


def _validate_session_context_factory(seed_problems: Optional[list[str]]):
    """Build a validator that ALSO enforces `currently_active_problems`
    matches `seed_problems` exactly when seed_problems is provided
    (curriculum pre-bake mode).
    """
    def _check(out: dict) -> None:
        _validate_session_context(out)
        if seed_problems is not None:
            got = set(out["currently_active_problems"])
            want = set(seed_problems)
            if got != want:
                raise ValueError(
                    f"seed_problems mismatch — wanted {sorted(want)} "
                    f"but generator produced {sorted(got)}"
                )
    return _check


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _safe_fallback(profile: SimulatorProfile) -> dict:
    """If the LLM call fails, emit a minimal framing so the simulator
    can still start. Picks the first vocabulary problem deterministically
    rather than relying on a (no-longer-existing) primary_problem field.
    """
    return {
        "current_life_events": "Ordinary week; no specific events to flag.",
        "mental_state": "Baseline mood; nothing acutely wrong.",
        "mood": "neutral",
        "emotions": ["uncertain"],
        "resistance_cooperation_level": "medium",
        "currently_active_problems": [PROBLEM_VOCAB[0]],
        "why_bringing_these_up_now": "Ongoing concern; user decided to check in today.",
        "_fallback_default": True,
    }


def _curriculum_session_context_path(
    profile_id: str, session_id: int,
) -> "config.Path":  # type: ignore[name-defined]
    """Disk path for a pre-baked session_context curriculum file."""
    from pathlib import Path
    return (
        config.CURRICULUM_DIR / profile_id
        / f"session_context_s{session_id:02d}.json"
    )


def _load_curriculum_session_context(
    profile_id: str, session_id: int,
) -> Optional[dict]:
    """Read a pre-baked session_context from the curriculum dir, or
    None if it doesn't exist. Used by the runtime path so every
    system reads the same hidden user state per (profile, session).
    """
    fp = _curriculum_session_context_path(profile_id, session_id)
    if not fp.exists():
        return None
    try:
        with fp.open() as f:
            return json.load(f)
    except Exception as e:
        log.warning(
            "curriculum session_context exists at %s but failed to load: %s",
            fp, e,
        )
        return None


def run_session_context(
    *,
    client: LLMClient,
    ctx: CallContext,
    profile: SimulatorProfile,
    seed_problems: Optional[list[str]] = None,
    prev_session_context: Optional[dict] = None,
    use_curriculum_cache: bool = True,
) -> dict:
    """Execute §13 session-context generation.

    Called ONCE at session start. Returns a dict matching
    SESSION_CONTEXT_SCHEMA.

    Curriculum integration (Phase B): if a pre-baked
    `data/curricula/<profile>/session_context_s<NN>.json` exists,
    return it directly without an LLM call — every system in the
    ablation reads the same frozen hidden state per (profile, session).
    Pass `use_curriculum_cache=False` to force a fresh LLM call (used by
    the curriculum-generation script itself + tests).

    `seed_problems` constrains the LLM to use exactly that list of
    problems for `currently_active_problems`. Used by the curriculum
    pre-bake to enforce the stratified-scenario assignment.

    `prev_session_context` lets the LLM ground session N+1 in what
    happened in session N (chronological continuity).

    Returns dict with `_fallback_default=True` on LLM failure (when no
    curriculum cache hit).
    """
    assert ctx.call_role in ("session_context", "curriculum_session_context"), (
        f"unexpected call_role for session_context: {ctx.call_role!r}"
    )
    # Curriculum cache short-circuit (runtime path).
    if use_curriculum_cache:
        cached = _load_curriculum_session_context(
            ctx.profile_id, ctx.session_id,
        )
        if cached is not None:
            return cached
        log.warning(
            "no curriculum session_context for profile=%s session=%d; "
            "falling back to live LLM generation",
            ctx.profile_id, ctx.session_id,
        )
    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(
                profile=profile,
                session_id=ctx.session_id,
                seed_problems=seed_problems,
                prev_session_context=prev_session_context,
            ),
            schema=SESSION_CONTEXT_SCHEMA,
            validator_extras=_validate_session_context_factory(seed_problems),
        )
    except Exception:
        return _safe_fallback(profile)


# ---------------------------------------------------------------------------
# Formatter — used by mind1_v6 to inject the context each turn
# ---------------------------------------------------------------------------


def format_session_context(ctx_out: dict) -> str:
    """Render a session_context output as a compact string for prompts."""
    emotions = ", ".join(ctx_out.get("emotions", []))
    active = ", ".join(ctx_out.get("currently_active_problems", []))
    return textwrap.dedent(f"""\
        Current life events: {ctx_out.get('current_life_events', '')}
        Mental state: {ctx_out.get('mental_state', '')}
        Mood: {ctx_out.get('mood', '')}
        Emotions: {emotions}
        Resistance/cooperation level: {ctx_out.get('resistance_cooperation_level', '')}
        Currently active problems (from 20-vocab): {active}
        Why bringing these up now: {ctx_out.get('why_bringing_these_up_now', '')}
    """).rstrip()


# ---------------------------------------------------------------------------
# Self-test (validator + prompt assembly; no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    p = SimulatorProfile(
        profile_id="P01",
        demographics={
            "age_range": "20-25", "gender": "non-binary",
            "occupation": "grad student", "education": "MA in progress",
            "life_stage": "early adulthood", "location": "Seattle",
        },
        personality_traits=["analytical", "self-critical"],
        communication_style=["terse", "guarded", "intellectualizing"],
        core_beliefs=["I have to earn rest", "asking for help is weakness"],
        hobbies_interests=[
            "reading nonfiction (likes deep dives)",
            "cooking new recipes (dislikes sweet desserts)",
        ],
        relevant_history="first-gen college; prior burnout 2 years ago",
    )

    sp = build_system_prompt()
    up = build_user_prompt(profile=p, session_id=1)
    assert "SESSION-CONTEXT generator" in sp
    assert "Profile P01" in up
    assert "Demographics:" in up
    assert "Communication style: terse, guarded, intellectualizing" in up
    assert "Core beliefs:" in up
    assert "Hobbies / interests" in up
    assert "first-gen college" in up

    # Different session id renders the new number.
    up3 = build_user_prompt(profile=p, session_id=3)
    assert "SESSION NUMBER: 3" in up3

    # Valid record passes.
    valid = {
        "current_life_events": "Finals week; three nights of almost no sleep.",
        "mental_state": "Running on fumes.",
        "mood": "exhausted",
        "emotions": ["anxious", "defeated"],
        "resistance_cooperation_level": "medium",
        "currently_active_problems": ["academic_pressure", "sleep_problems"],
        "why_bringing_these_up_now": "Finals compounded by a job shift.",
    }
    _validate_session_context(valid)

    # Duplicate problems — rejected.
    bad = dict(valid)
    bad["currently_active_problems"] = ["academic_pressure", "academic_pressure"]
    try:
        _validate_session_context(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("expected rejection: duplicate problems")

    # Fallback is a valid record shape.
    fb = _safe_fallback(p)
    _validate_session_context(fb)
    assert fb["_fallback_default"] is True

    # Formatter runs cleanly.
    rendered = format_session_context(valid)
    assert "Mood: exhausted" in rendered
    assert "academic_pressure" in rendered

    # Empty profile defaults render without exploding.
    p_min = SimulatorProfile(profile_id="P99")
    up_min = build_user_prompt(profile=p_min, session_id=1)
    assert "Profile P99" in up_min
    assert "(unspecified)" in up_min  # demographics
    assert "(none)" in up_min          # other lists

    print("session_context (redesign) self-test PASSED")


if __name__ == "__main__":
    _self_test()
