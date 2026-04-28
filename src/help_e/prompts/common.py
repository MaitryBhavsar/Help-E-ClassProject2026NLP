"""Shared prompt building blocks used by every §18 call.

Most pieces — project identity, style rules, enum reminders — appear verbatim
in multiple prompts. Pulling them into one module keeps the copies in sync and
lets us version the "shared system prompt" independently of any individual
§18 spec.
"""

from __future__ import annotations

import textwrap

from ..config import (
    ATTR_TYPES,
    MI_TECHNIQUES,
    PERSONA_FIELDS,
    PROBLEM_VOCAB,
    TTM_STAGES,
    USER_INTENTS,
)

# ---------------------------------------------------------------------------
# Identity + style
# ---------------------------------------------------------------------------

PROJECT_IDENTITY = (
    "HELP-E is a motivational-interviewing (MI) emotional-support companion. "
    "It helps users work through everyday, non-crisis well-being problems "
    "across multiple conversations."
)

MI_STYLE_RULES = textwrap.dedent("""\
    Style rules you must follow when writing to the user:
    - Reflect what the user said before moving forward.
    - Support the user's autonomy; never moralize, lecture, or command.
    - Do not give unsolicited advice — if advice is needed, ask permission or
      offer an options menu first.
    - At most one question per response.
    - At most five sentences per response.
    - Match the user's emotional register; don't over-enthusiasm.
""").strip()

# ---------------------------------------------------------------------------
# Enum paste-ins
# ---------------------------------------------------------------------------


def problem_vocab_block() -> str:
    """Paste-in block for the 20-problem vocabulary with one-line glosses."""
    glosses = {
        "academic_pressure": "school/university workload and performance expectations",
        "work_stress": "job demands, burnout, work-life balance strain",
        "sleep_problems": "difficulty falling/staying asleep; non-restorative sleep",
        "procrastination": "chronic delay of tasks the user intends to do",
        "general_anxiety": "persistent worry not tied to a single domain",
        "low_self_esteem": "persistent negative view of self, self-worth",
        "perfectionism": "rigid standards that drive distress or inaction",
        "social_anxiety": "fear/avoidance of social situations",
        "loneliness": "felt lack of meaningful social connection",
        "conflicts_with_partner": "recurring tension with romantic partner",
        "breakup_aftermath": "coping with ended romantic relationship",
        "conflicts_with_parents": "recurring tension with parents",
        "conflicts_with_friends": "recurring tension with friends",
        "financial_stress": "money worries, debt, budget pressure",
        "career_uncertainty": "job searching, direction, role transitions",
        "caregiver_stress": "providing care to an ill/aging family member",
        "grief_of_loved_one": "mourning loss (non-acute; bereavement ongoing)",
        "health_anxiety": "persistent worry about physical health",
        "body_image_concerns": "distress about appearance, weight, body",
        "life_transition": "moving, graduation, new role, big life change",
    }
    lines = ["20-problem vocabulary (use these exact strings, no others):"]
    for name in PROBLEM_VOCAB:
        lines.append(f"  - {name} — {glosses[name]}")
    return "\n".join(lines)


def attribute_inventory_block() -> str:
    meanings = {
        "perceived_severity": "how serious the user thinks the problem is",
        "perceived_susceptibility": "how likely the problem is to keep affecting them",
        "perceived_benefits": "what good the user thinks change would bring",
        "perceived_barriers": "what the user thinks is in the way of change",
        "self_efficacy": "how capable the user feels of making the change",
        "cues_to_action": "events/reminders pushing them to act",
        "motivation": "desire/energy to work on this (change-talk intensity)",
        "coping_strategies": "what they're currently trying",
        "past_attempts": "what they've tried before and how it went",
        "triggers": "what sets the problem off or makes it worse",
        "goal": "what 'solved' looks like for the user",
    }
    lines = ["Behavioral attribute types (attr_type enum):"]
    for name in ATTR_TYPES:
        lines.append(f"  - {name} — {meanings[name]}")
    return "\n".join(lines)


def ttm_stage_rubric_block() -> str:
    anchors = {
        "precontemplation": "user does not see a problem or does not want to change",
        "contemplation": "acknowledges the problem; ambivalent about change",
        "preparation": "intending to act soon; planning, small steps",
        "action": "actively making changes; concrete recent steps",
        "maintenance": "sustaining change; preventing relapse",
    }
    lines = ["TTM stage rubric:"]
    for stage in TTM_STAGES:
        lines.append(f"  - {stage}: {anchors[stage]}")
    return "\n".join(lines)


def user_intent_block() -> str:
    anchors = {
        "vent": "expressing feelings, not asking for anything",
        "seek_validation": "wants to hear that their reaction is understandable",
        "seek_advice": "explicitly asks what to do",
        "explore_option": "turning over possible next steps in conversation",
        "report_progress": "describing what they did since last turn/session",
        "check_in": "light touch — saying hi, small talk, catching up",
        "other": "none of the above",
    }
    lines = ["user_intent enum:"]
    for intent in USER_INTENTS:
        lines.append(f"  - {intent} — {anchors[intent]}")
    return "\n".join(lines)


def persona_field_block() -> str:
    anchors = {
        "demographics": "age_range, gender, occupation, education, life_stage",
        "personality_traits": "~3 dominant traits",
        "core_values": "what matters to the user",
        "core_beliefs": "stable self/world beliefs",
        "support_system": "who's around",
        "hobbies_interests": "activities for meaning or fun",
        "communication_style": "brief, emotional, intellectualizing, guarded, etc.",
        "relevant_history": "past major events or prior therapy",
    }
    lines = ["Persona fields:"]
    for name in PERSONA_FIELDS:
        lines.append(f"  - {name} — {anchors[name]}")
    return "\n".join(lines)


def mi_technique_reference_block() -> str:
    """Full T1–T12 reference with one-line usage guidance (§18.4 system prompt).

    This absorbs what the selector used to carry per-candidate. The merged-call
    LLM reads this block and selects which technique(s) the instruction will
    name from the candidate list it receives.
    """
    guidance = {
        "T1": "paraphrase what the user said, including feeling; go a hair deeper",
        "T2": "affirm a specific strength or effort the user showed",
        "T3": "summarize what's been said so far; gather the thread",
        "T4": "ask an open question that invites elaboration, not yes/no",
        "T5": "ask permission before offering a suggestion or new angle",
        "T6": "offer 2–3 options side-by-side; the user picks",
        "T7": "elicit what the user already knows, provide one fact, elicit reaction",
        "T8": "invite the user's own reasons for change — their words, not yours",
        "T9": "build confidence: reflect past capability, name small wins",
        "T10": "co-design a concrete next step — when, what, how small",
        "T11": "plan how to handle a likely trigger or setback",
        "T12": "normalize the experience; reframe a stuck thought into a wider view",
    }
    lines = ["MI techniques reference (T1–T12):"]
    for tid, label in MI_TECHNIQUES.items():
        lines.append(f"  - {tid} {label}: {guidance[tid]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Transcript / history formatting
# ---------------------------------------------------------------------------


def format_dialog_turns(turns: list[dict]) -> str:
    """Turns are ``[{'role': 'user'|'assistant', 'text': str, 'turn_id': int}]``.

    Returns a role-marked transcript string. Empty input → "(no prior turns)".
    """
    if not turns:
        return "(no prior turns)"
    lines = []
    for t in turns:
        role = "USER" if t.get("role") == "user" else "ASSISTANT"
        tid = t.get("turn_id")
        marker = f"[t{tid}] " if tid is not None else ""
        lines.append(f"  {marker}{role}: {t['text']}")
    return "\n".join(lines)


def format_compact_persona(persona_dict: dict) -> str:
    """One line per populated persona field. Empty fields dropped."""
    lines = []
    for field, value in persona_dict.items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            value = ", ".join(str(x) for x in value)
        lines.append(f"  - {field}: {value}")
    return "\n".join(lines) if lines else "  (no persona info yet)"


def format_existing_problems(problems: list[dict]) -> str:
    if not problems:
        return "  (no existing problems)"
    lines = []
    for p in problems:
        lines.append(
            f"  - {p['problem_name']} "
            f"(stage={p['current_ttm_stage']}, last_mentioned={p['last_mentioned']})"
        )
    return "\n".join(lines)


def format_recent_attribute_summary(
    recent_edges: list[dict],
) -> str:
    """Compact per-problem view consumed by §18.1 extraction.

    ``recent_edges`` is a list of dicts like
    ``{'problem_name', 'attr_type', 'value', 'current_level', 'most_recent_info'}``.
    """
    if not recent_edges:
        return "  (no recent attribute evidence)"
    from collections import defaultdict

    by_problem: dict[str, list[dict]] = defaultdict(list)
    for e in recent_edges:
        by_problem[e["problem_name"]].append(e)
    lines = []
    for problem_name, edges in by_problem.items():
        lines.append(f"  Problem: {problem_name}")
        for e in edges:
            lines.append(
                f"    - {e['attr_type']}: '{e['value']}' "
                f"(level={e['current_level']}) :: {e['most_recent_info']}"
            )
    return "\n".join(lines)
