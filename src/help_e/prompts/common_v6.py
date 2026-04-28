"""Shared prompt building blocks specific to the v6 pipeline.

v6 uses a problem-centric graph with inline attributes, typed
problem-problem edges, and a long-form system_intent. This module adds
the blocks and formatters needed by inference.py, recompute.py,
instruction_response_v6.py, and persona_update_v6.py.

v5 blocks in common.py still apply where the shape has not changed
(PROJECT_IDENTITY, format_dialog_turns, problem_vocab_block,
user_intent_block, ttm_stage_rubric_block, mi_technique_reference_block).
"""

from __future__ import annotations

import textwrap
from typing import Any, Optional

from ..config import (
    LEVEL_ATTR_TYPES,
    LEVELS_V6,
    NON_LEVEL_ATTR_TYPES,
    RELATION_TYPES,
)


# ---------------------------------------------------------------------------
# Style rules (v6: tighter than v5 — ≤4 sentences, not ≤5).
# These rules describe WHAT a good emotionally-supportive response looks
# like in the spirit of MI, not just the mechanical constraints.
# ---------------------------------------------------------------------------

MI_STYLE_RULES_V6 = textwrap.dedent("""\
    # WHAT AN EMOTIONALLY SUPPORTIVE RESPONSE IS (THE SPIRIT)

    A good response sounds like a trusted friend who genuinely gets this
    person — NOT a therapist reading a script, NOT a chatbot running
    through a checklist, NOT a parrot playing back what was said. Spirit
    first, technique second.

    The job of a response is to MOVE the user — a little — in a
    positive, motivating direction, consistent with where they are on
    the change ladder. Not to re-describe their situation. Not to
    confirm how heavy things are. You are here to help them navigate
    BETTER than they could alone.

    # THE HARD RULES (enforced)

    - At most SIX sentences. Often far fewer — shorter is usually
      better. Length depends on the moment: a single sentence can be
      right if the user just needs to feel heard; a few sentences fit
      when you're delivering a small step or an EPE exchange. Do not
      pad.
    - At most ONE question.
    - Never moralize, lecture, or command. Preserve autonomy in
      language and tone.
    - Do not overload with multiple suggestions in one turn.

    # REFLECTION ≠ PARROTING  ·  DO NOT RE-HIGHLIGHT THE PROBLEM

    - DO NOT restate what the user just said in different words.
    - DO NOT describe the problem back to them in the same frame they
      used. "It sounds like work is overwhelming" when they said "work
      is overwhelming" is a repetition, not a response. Naming the
      problem the way they did is not progress.
    - DO name what's UNDERNEATH the user's words: the feeling they
      haven't labeled, the fear shaping the statement, the connection
      they haven't made out loud. Go ONE LAYER DEEPER.
    - Bad:   "It sounds like this week has been heavy."         ← parrot
      Good:  "That kind of week has a way of wearing down even
              the people who usually hold it together."         ← deeper

    # POSITIVE, MOTIVATING, ALIVE

    - The response should feel alive — not boring, not flat. A friend
      brings energy when a friend answers you.
    - Where the chosen MI technique invites it, use creative elements:
      a small anecdote, a fitting metaphor, a gentle piece of humor
      (only if tone allows), an image the user can picture. Do NOT
      force these — they must serve the technique and match the user's
      emotional register. Never joke about the user's pain.
    - Speak AS a consistent person: warm, observant, honest. Contractions.
      Occasional "yeah" or "that makes sense." Drop clinical /
      counseling jargon.
    - Positive does not mean minimizing. It means orienting the user
      toward something — a small step, a reframe, a piece of their own
      strength — instead of staying stuck describing the problem.

    # ACKNOWLEDGE THE USER'S INTENT

    Match what they are actually asking for THIS turn:
    - Venting → reflect + sit with them; do NOT pivot to solutions.
    - Seeking validation → name the reasonableness of their reaction.
    - Seeking advice → permission is ALREADY implied by the ask. Do not
      hide behind "would it help if I suggested…". Deliver: use EPE
      (T7) or a small action step (T10) — something concrete, not
      another question back. Returning another open question when they
      just asked you IS a failure mode.
    - Reporting progress → affirm a SPECIFIC thing, not the generic
      "you're doing great."
    - Exploring options → offer 2–3 alternatives side-by-side (T6).
    - Checking in → stay light; don't over-weight it.

    # EVIDENCE IS CONTENT, NOT LABELS

    Evidence means the specific material (from past turns, the persona,
    the attribute observations in the bundle, the prior-session
    summary, the connection-evidence between problems) that tells you
    something USEFUL about this person and what might help them now.
    Use it to enrich the chosen technique, not to describe their state.
    - DO: "the all-nighters you mentioned last week"
    - DO: "you said earlier that running used to be your way of
           resetting"
    - DO: connection evidence — "the sleep piece and the workload
           piece seem to be feeding each other"
    - DO NOT surface attribute labels to the user: never say
      "perceived_barriers", "self_efficacy", "TTM stage".
    - Evidence should feel like a friend who remembers things about
      you, not a transcript replay.

    # VOICE + PERSONALIZATION

    - Match the user's register. Casual → casual. If they reach for a
      metaphor ("I feel like I'm drowning"), stay inside it; don't
      swap it for yours.
    - Personalize using the persona when it's known:
        - communication_style (guarded, intellectualizing, etc.) —
          adjust directness.
        - general_behavioral_traits — rigid users need slower reframes;
          self-critical users need SPECIFIC affirmations; emotionally
          reactive users need regulation before advice; overthinkers
          need LESS cognitive load (fewer options, one small step).
    - Use the prior-session summary (if any) to set the emotional
      starting point. If last session ended on a small win, nod to it
      before moving on.
    - Session 1 / turn 1 has no persona and no summary — default to
      warmth without inventing detail.

    # THE POSITIVE OPENING RULE

    Your FIRST sentence must NAME something specific from the user's
    turn: an image they used, a phrase they said, a feeling they
    carried, a particular detail. Start FROM that specific thing, not
    from a template scaffold.

    Good openings lead with the specific:
      - "Pulling all-nighters only works for so long before the system
         pushes back, and you've hit that point."
      - "The card feeling like a knife again — that's what caught me
         in what you said."
      - "Yeah — and the part I keep noticing is the word 'alone' in
         that sentence."

    Templates to drop (never use these as the OPENING):
      - "It sounds like …"
      - "It seems like …"
      - "It's like …"
      - "That sounds like …"
      - "That sounds really …"
      - "That must be really …"
      - "I can only imagine how …"
      - "That's a really tough place to be"
      - "That can be a really tough feeling"
      - "That can be (a) really tough to navigate"

    Mid-response templates to avoid (these read as empty filler):
      - "really tough to navigate" / "tough thing to navigate"
      - "hard thing to swallow"
      - "a really tough reminder / a really tough place to be / really
        scary place to be / really tough gap to fill"
      - "I'm here for you" / "I'm here to listen" (empty closers)
      - "Take your time" (unless the user is venting and it's the only
        thing that fits)

    If you notice yourself about to write any of these — stop and pick
    the specific thing instead. The validator hard-rejects the worst
    offenders; the rest you should avoid on your own by picking
    specific, concrete language over vague "that's really tough" lines.

    # WHEN THE USER IS ASKING FOR ADVICE (intent = seek_advice)

    READ THIS BLOCK TWICE. This is the highest-frequency failure mode.

    If the user has asked you a direct question like "should I …",
    "what can I do …", "how do I …", "help me decide …" — they have
    already given you permission. They are asking for SUBSTANCE.

    Your `final_response` MUST contain:
      (a) a declarative sentence (no "?") that offers SOMETHING — a
          small concrete suggestion, a reframe they haven't heard
          before, a specific piece of perspective, a named option, or
          an EPE "elicit one thing → provide one thing → elicit" turn;
      AND
      (b) at MOST one question (optional; EPE's closing "does that
          fit?" counts).

    Returning ONLY an open question ("what would it mean for you if…")
    when they asked for advice IS THE FAILURE. A friend who was just
    asked a direct question does not answer with another open question.

    Examples of valid deliveries for seek_advice:
      - T7 EPE: "Before I throw anything out — when you picture [X],
        what's the piece you're most worried about losing? One thing
        that often helps in this kind of spot is [one concrete
        suggestion]. Does that feel like it could fit?"
      - T10 small step: "One thing you could try this week is [one
        specific step]. Not a solution — just a way to learn something
        about [X] without committing to anything yet."
      - T6 options menu: "Two directions that usually show up in this
        kind of tension are [option A] and [option B]. Either one is
        legit; the one that fits you is the one that doesn't cost you
        your [core value]."
""").strip()


# ---------------------------------------------------------------------------
# Enum paste-ins for v6 prompts
# ---------------------------------------------------------------------------


def level_attribute_block() -> str:
    meanings = {
        "perceived_severity": "how serious the user thinks the problem is",
        "perceived_susceptibility": "how likely the problem is to keep affecting them",
        "perceived_benefits": "what good the user thinks change would bring",
        "perceived_barriers": "what the user thinks is in the way of change",
        "self_efficacy": "how capable the user feels of making the change",
        "cues_to_action": "events or reminders pushing them to act",
        "motivation": "desire or energy to work on this (change-talk intensity)",
    }
    lines = ["LEVEL attributes (attr_type values carrying a current level):"]
    for name in LEVEL_ATTR_TYPES:
        lines.append(f"  - {name} — {meanings[name]}")
    return "\n".join(lines)


def non_level_attribute_block() -> str:
    meanings = {
        "coping_strategies": "what the user is currently trying",
        "past_attempts": "what the user tried before and how it went",
        "triggers": "what sets the problem off or makes it worse",
        "goal": "what 'solved' looks like for the user (main problem only)",
    }
    lines = ["NON-LEVEL attributes (attr_type values without a level):"]
    for name in NON_LEVEL_ATTR_TYPES:
        lines.append(f"  - {name} — {meanings[name]}")
    return "\n".join(lines)


def levels_block() -> str:
    anchors = {
        "low": "clearly mild or minimal",
        "medium": "noticeable but not overwhelming",
        "high": "prominent, strongly present",
        "unknown": "evidence insufficient to place",
    }
    lines = ["Level scale (for LEVEL attributes):"]
    for lv in LEVELS_V6:
        lines.append(f"  - {lv} — {anchors[lv]}")
    return "\n".join(lines)


def relation_types_block() -> str:
    anchors = {
        "causal": "an attribute of problem A causes an attribute of problem B",
        "effect": "an attribute of problem A is caused by an attribute of problem B",
        "reinforcing": "attributes of the two problems amplify each other",
        "conflicting": "attributes of the two problems pull in opposite directions",
        "shared_trigger": "the same trigger fires both problems",
        "shared_barrier": "the same barrier blocks progress on both",
        "shared_goal": "the same desired outcome covers both",
        "unclear_but_related": "the utterance links them but the mechanism is vague",
    }
    lines = ["Problem-attribute-connection relation types:"]
    for r in RELATION_TYPES:
        lines.append(f"  - {r} — {anchors[r]}")
    return "\n".join(lines)


def persona_v6_field_block() -> str:
    anchors = {
        "demographics": "age_range, gender, occupation, education, life_stage",
        "personality_traits": "~3 dominant traits",
        "core_values": "what matters to the user",
        "core_beliefs": "stable self/world beliefs",
        "support_system": "who is around (family, friends, partner, community, none)",
        "hobbies_interests": "activities for meaning or fun",
        "communication_style": "brief, emotional, intellectualizing, guarded, etc.",
        "relevant_history": "past major events or prior therapy that shape approach",
        "general_behavioral_traits": (
            "free-text traits that shape how the user engages with help — "
            "rigid, inquisitive, overthinker, emotionally reactive, avoidant, "
            "impulsive, reflective, concrete thinker, self-critical, etc. "
            "Not a closed vocabulary; infer from the transcript."
        ),
    }
    lines = ["Persona fields (9 total — start empty, fill over sessions):"]
    for name in _PERSONA_V6_FIELDS:
        lines.append(f"  - {name} — {anchors[name]}")
    return "\n".join(lines)


# Ordered list of the 9 v6 persona fields (matches PersonaState dataclass).
_PERSONA_V6_FIELDS: tuple[str, ...] = (
    "demographics", "personality_traits", "core_values", "core_beliefs",
    "support_system", "hobbies_interests", "communication_style",
    "relevant_history", "general_behavioral_traits",
)


def persona_v6_field_names() -> tuple[str, ...]:
    return _PERSONA_V6_FIELDS


# ---------------------------------------------------------------------------
# Formatters for v6 inputs
# ---------------------------------------------------------------------------


def format_persona_v6(persona_dict: dict) -> str:
    """Compact rendering of PersonaState.asdict() for prompt context.

    Empty persona → "(no persona info yet — system has not inferred anything
    about this user)".
    Populated fields are shown one per line; list values joined by ", ".
    """
    populated = []
    for name in _PERSONA_V6_FIELDS:
        v = persona_dict.get(name)
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        populated.append(f"  - {name}: {v}")
    if not populated:
        return "(no persona info yet — system has not inferred anything about this user)"
    return "\n".join(populated)


def format_active_problems_v6(problems: list[dict]) -> str:
    """Render the active problem list for inference input.

    Each entry: {problem_name, current_ttm_stage, goal, last_mentioned}.
    """
    if not problems:
        return "(no active problems yet — fresh conversation)"
    lines = []
    for p in problems:
        goal = p.get("goal")
        goal_str = f", goal={goal!r}" if goal else ""
        last = p.get("last_mentioned")
        last_str = f", last={last}" if last else ""
        lines.append(
            f"  - {p['problem_name']} "
            f"(stage={p.get('current_ttm_stage', 'precontemplation')}"
            f"{goal_str}{last_str})"
        )
    return "\n".join(lines)


def format_problem_view_v6(pv: dict, *, is_main: bool) -> str:
    """Render one problem's evidence block for the response prompt.

    Expected shape (dict from retrieval_v6):
        {
            "problem_name": str,
            "current_ttm_stage": str,
            "goal": str | None,
            "level_attributes": {attr: {"current_level", "recent_evidence": [...]}},
            "non_level_attributes": {attr: {"recent_evidence": [...]}},
            "edge_weight_to_main_problem": float,    # only for non-main
            "connection_evidence": [...],            # only for non-main
        }
    Each recent_evidence entry is a dict with at least
    "inferred_information" and optionally "session_id"/"turn_id".
    """
    tag = "MAIN" if is_main else "RELATED"
    header = f"[{tag}] {pv['problem_name']} (stage={pv.get('current_ttm_stage', '?')}"
    if pv.get("goal"):
        header += f", goal={pv['goal']!r}"
    if not is_main and "edge_weight_to_main_problem" in pv:
        header += f", edge_weight_to_main={pv['edge_weight_to_main_problem']:.3f}"
    header += ")"

    lines = [header]
    level_attrs = pv.get("level_attributes") or {}
    if level_attrs:
        lines.append("  LEVEL ATTRIBUTES:")
        for attr, state in level_attrs.items():
            lines.append(
                f"    - {attr} = {state.get('current_level', 'unknown')}"
            )
            for e in state.get("recent_evidence", []):
                _append_evidence_line(lines, e)
    non_level_attrs = pv.get("non_level_attributes") or {}
    if non_level_attrs:
        lines.append("  NON-LEVEL ATTRIBUTES:")
        for attr, state in non_level_attrs.items():
            lines.append(f"    - {attr}")
            for e in state.get("recent_evidence", []):
                _append_evidence_line(lines, e)
    if not level_attrs and not non_level_attrs:
        lines.append("  (no attribute evidence yet)")
    if not is_main and pv.get("connection_evidence"):
        lines.append("  CONNECTION TO MAIN:")
        for c in pv["connection_evidence"]:
            s = c.get("session_id")
            t = c.get("turn_id")
            span = c.get("supporting_utterance_span")
            span_str = f' :: "{span}"' if span else ""
            lines.append(
                f"    - [{c.get('relation_type', 'unclear_but_related')}] "
                f"{c.get('attribute_1')}<->{c.get('attribute_2')} "
                f"(conf={c.get('confidence', '?')}, s{s}t{t}): "
                f"{c.get('explanation', '')}{span_str}"
            )
    return "\n".join(lines)


def _append_evidence_line(lines: list[str], e: dict) -> None:
    s = e.get("session_id")
    t = e.get("turn_id")
    conf = e.get("confidence", "?")
    info = e.get("inferred_information", "")
    lines.append(f"        · [s{s}t{t}, conf={conf}] {info}")


def format_candidate_list(techniques: list[dict]) -> str:
    """Format a list of {technique_id, technique_label} candidate dicts."""
    if not techniques:
        return "(none)"
    return ", ".join(f"{t['technique_id']} {t['technique_label']}" for t in techniques)


# ---------------------------------------------------------------------------
# MI technique reference — v6, with SPIRIT of each technique
# ---------------------------------------------------------------------------


def mi_technique_reference_block_v6() -> str:
    """Richer MI reference than v5's one-liners.

    For each technique: what it looks like in practice, what it is NOT
    (the common failure mode), and when it's the right move. This lives
    in the response prompt so the LLM can pick techniques in SPIRIT, not
    by label.
    """
    from ..config import MI_TECHNIQUES

    spirit = {
        "T1": (
            "Catch the feeling or concern UNDERNEATH the user's words. "
            "Name what they haven't yet named out loud — the guilt under "
            "the anger, the exhaustion under the anxiety. Simple reflection "
            "= content + feeling; complex reflection = what is implied but "
            "not said. NEVER restate the user's sentence in different "
            "words — that reads as a machine going through motions."
        ),
        "T2": (
            "Spotlight a SPECIFIC strength, effort, or resilience the user "
            "has shown. Tie it to something concrete. Not generic praise "
            '("that\'s great", "you\'re doing great") — that lands as '
            "hollow. For self-critical users, specificity matters even "
            "more: name the exact thing they did."
        ),
        "T3": (
            "Weave together several threads the user has raised so far "
            "into one short recap. Best at session transitions or when "
            "the conversation has sprawled; used to move things forward, "
            "not to burn a turn restating. Closes with a small question "
            "or nudge toward what's next."
        ),
        "T4": (
            "Invite elaboration — a question the user could answer many "
            "different ways. Genuinely curious, not a leading question in "
            "disguise. NOT yes/no. NOT 'have you considered' (that's a "
            "veiled suggestion). Open questions expand the space; they "
            "don't narrow it."
        ),
        "T5": (
            "Ask permission BEFORE introducing something the user has not "
            "asked for (a fact, a reframe, a suggestion). If the user has "
            "explicitly requested advice or input, permission is IMPLIED "
            "by their request — skip this and use T7 or T10 directly. "
            "Permission-gating for something already asked for reads as "
            "evasive."
        ),
        "T6": (
            "Offer 2–3 distinct alternatives side-by-side; let the user "
            "pick which one to explore. Use when the user is exploring or "
            "ambivalent, not when they need one concrete thing. Each "
            "option should feel different (not three versions of the same "
            "idea)."
        ),
        "T7": (
            "Elicit-Provide-Elicit. First ask what the user already knows "
            "or has thought about — make space for their own reasoning. "
            "Then provide ONE concrete piece of information or "
            "perspective. Then ask what they make of it. This is the "
            "polite, autonomy-preserving way to deliver advice when it "
            "has been requested."
        ),
        "T8": (
            "Invite the user's OWN reasons for change, in THEIR own "
            'words. Questions like "what would be different for you if '
            'this wasn\'t hanging over you?" or "what makes you think '
            'about this now?" Not your reasons, not generic ones.'
        ),
        "T9": (
            "Name a past capability the user has already shown, drawn "
            "from the transcript or persona — not from imagination. Used "
            "to build self-efficacy when the user doubts their ability to "
            "act. For a user who used to run marathons, mention the "
            "running; for a user who finished a brutal term last year, "
            "mention that term."
        ),
        "T10": (
            "Co-design ONE concrete next step — specific, small, time-"
            "bound. 'Take care of yourself' is NOT a step. 'Try ten "
            "minutes of one thing you used to enjoy, any time this week' "
            "is a step. When self_efficacy is low, scope the step even "
            "smaller (a MICRO-step)."
        ),
        "T11": (
            "Anticipate what could go wrong — a predictable trigger, a "
            "likely setback — and plan how the user will handle it ahead "
            "of time. For users already moving (action / maintenance), "
            "not for precontemplation."
        ),
        "T12": (
            "Widen a stuck thought without minimizing it. Normalize the "
            "EXPERIENCE, not the pain. 'This is one of the shapes grief "
            "takes for a lot of people in the first months' — yes. "
            "'Everyone feels that way' — no; that flattens it. Reframing "
            "is inviting a wider view, not correcting the user's view."
        ),
    }
    lines = ["MI techniques (use in SPIRIT, not by label):"]
    for tid, label in MI_TECHNIQUES.items():
        lines.append(f"  - {tid} {label}: {spirit[tid]}")
    return "\n".join(lines)


def format_three_source_candidates(
    from_user_intent: list[dict],
    from_ttm_stage: list[dict],
    from_attribute_levels: Optional[list[dict]] = None,
) -> str:
    """Labeled candidate streams for the response prompt.

    v6 uses TWO streams (intent + ttm). The `from_attribute_levels`
    parameter is retained for backwards compatibility with older callers
    and is IGNORED if non-empty — attribute values drive response content
    via the bundle, not technique selection.
    """
    return textwrap.dedent(f"""\
        - from user_intent:          {format_candidate_list(from_user_intent)}
        - from ttm_stage:            {format_candidate_list(from_ttm_stage)}
    """).strip()


def format_two_source_candidates(
    from_user_intent: list[dict],
    from_ttm_stage: list[dict],
) -> str:
    """Canonical v6 formatter — two streams only."""
    return format_three_source_candidates(from_user_intent, from_ttm_stage)


def natural_mi_progression_block() -> str:
    """Reference — common next-step pairings between MI techniques.

    The response prompt consults this when picking which technique to
    lean into GIVEN what has already been used. It is a guide, not a
    hard rule: the LLM can (and should) break the sequence when the
    user's signal calls for something else.
    """
    return textwrap.dedent("""\
        Natural MI-technique progressions (reference — not a hard rule):
          - T1 Reflection  →  T4 Open Question  /  T8 Change Talk
              (after catching the feeling, invite the user's own reasons)
          - T4 Open Question  →  T8 Change Talk  /  T10 Action Planning
              (open the space, then harvest the answer into something)
          - T5 Ask Permission  →  T6 Options Menu  /  T7 EPE
              (once you have permission, DELIVER — don't re-ask)
          - T7 EPE  →  T10 Action Planning  /  T11 Coping Plan
              (information landed — turn it into one concrete move)
          - T8 Change Talk  →  T10 Action Planning  /  T9 Confidence Builder
              (user's own reasons → a small specific step, or a lift in belief)
          - T10 Action Planning  →  T11 Coping / Relapse Plan
              (step set → anticipate what might derail it)
          - T12 Normalize / Reframe  →  T1 Reflection  /  T4 Open Question
              (after widening the frame, slow down and check in)
          - T2 Affirmation  →  T1 Reflection  /  T8 Change Talk
              (after spotlighting a strength, make room for what's next)

        When a technique has just been used, consider whether the user's
        current signal makes its natural successor the right move — or
        whether to stay on the same move one more turn. Do NOT treat
        this as a script; use it as a cue for fluency.
    """).strip()


def assistant_persona_block() -> str:
    """Fixed voice the assistant inhabits across turns and sessions.

    This is a CHARACTER the LLM is asked to be, not a set of rules —
    aimed at producing a consistent, warm, alive voice rather than a
    generic assistant tone.
    """
    return textwrap.dedent("""\
        THE VOICE YOU ARE:
          A friend who has sat through hard things with people before.
          You listen more than you talk. You remember small details and
          bring them back when it matters. You have warmth and a quiet,
          grounded humor — you'll use it when the moment invites it,
          never at the user's pain. You don't lecture. You don't
          diagnose. You say things like "yeah" and "that makes sense"
          the way a person does. When you offer something, you offer it
          small — one thing, not five. When the user just needs to be
          heard, you stay with them. When they ask you something
          directly, you answer — you don't deflect with another
          question.

          This is one person the user is talking to — the same person
          each turn, each session. Keep the voice consistent.
    """).strip()


def format_past_turn_techniques(past: list[dict]) -> str:
    """Render the list of techniques used on previous turns of this
    session. `past` is a list of
    `{turn_id, user_intent, technique_ids}` dicts, oldest first.
    """
    if not past:
        return "(no prior turns this session)"
    lines = []
    for p in past:
        tids = p.get("technique_ids") or []
        tid_str = " ".join(tids) if tids else "(none)"
        intent = p.get("user_intent") or "?"
        lines.append(
            f"  - turn {p.get('turn_id')} (intent={intent}): {tid_str}"
        )
    return "\n".join(lines)


def format_chronology_entries(entries: list[dict]) -> str:
    """Render past-K evidence entries for the recompute prompt.

    Each entry: {session_id, turn_id, attr_name, inferred_information, confidence}.
    Oldest first.
    """
    if not entries:
        return "(no prior entries)"
    lines = []
    for e in entries:
        lines.append(
            f"  [s{e['session_id']} t{e['turn_id']}, conf={e.get('confidence', '?')}] "
            f"{e.get('attr_name', '?')}: {e.get('inferred_information', '')}"
        )
    return "\n".join(lines)


# ===========================================================================
# v6 REDESIGN helpers (plan §3, §5, §6, §9 — MISC-aligned vocabulary)
# ===========================================================================


def oars_skills_block_v6() -> str:
    """OARS basic MI skills as the always-on baseline.

    Used in the SYSTEM prompt's SKILLS section. Citation removed
    (M&R 2013 ch. 6) — academic refs are noise to the LLM.
    """
    from ..config import OARS_SKILLS
    lines = ["OARS — always-on conversational baseline:"]
    for code, blurb in OARS_SKILLS.items():
        lines.append(f"  - {blurb}")
    return "\n".join(lines)


def mi_principles_block_v6() -> str:
    """MI principles always-on baseline. Citation removed."""
    from ..config import MI_PRINCIPLES_V6
    lines = ["MI principles (always on):"]
    for code, blurb in MI_PRINCIPLES_V6.items():
        lines.append(f"  - {code}: {blurb}")
    return "\n".join(lines)


def misc_inconsistent_codes_block_v6() -> str:
    """Anti-pattern MISC codes — listed in SYSTEM prompt CONSTRAINTS as
    things NOT to use (Moyers et al. 2014 MI-non-adherent codes).
    """
    from ..config import MISC_INCONSISTENT_CODES
    lines = [
        "Anti-patterns — these MISC strategies are MI-inconsistent and you",
        "MUST NOT use them:",
    ]
    for code, why in MISC_INCONSISTENT_CODES.items():
        lines.append(f"  - `{code}` — {why}")
    return "\n".join(lines)


def problem_name_mapping_block() -> str:
    """Compressed instruction telling the LLM how to map off-vocabulary
    relationship-conflict mentions onto the 3 official `conflicts_with_*`
    labels in the 20-problem vocabulary.

    Reused by every prompt that emits problem names: `inference`,
    `v3_extract`, `v4_extract`, `mind1_v6`. Eliminates duplicate text
    drift across systems and ensures all four call_roles map identical
    edge cases (sibling, in-law, business co-owner) the same way.
    """
    return textwrap.dedent("""\
        - Pick problem names EXACTLY from the 20-vocabulary above. Never
          invent off-list strings. For relationship conflicts that don't
          fit, map to the closest of: conflicts_with_partner (any
          close-adult conflict — sibling, in-law, business co-owner),
          conflicts_with_parents, conflicts_with_friends.
    """).rstrip()


def ttm_guidance_block() -> str:
    """4-stage TTM rubric used by `recompute`, `v3_extract`, `v4_extract`.

    Source of truth for the 4 stage definitions. If you tighten the
    wording (e.g. clarify what counts as "preparation"), edit ONE
    function here, all callers pick it up.
    """
    return textwrap.dedent("""\
        - precontemplation: user is not considering change; minimizes
          or denies the problem.
        - contemplation: user acknowledges the problem and is weighing
          whether to change.
        - preparation: user is planning specific steps or testing tiny
          attempts; intent to act soon.
        - action: user is actively making the behavioral change.
    """).rstrip()


def user_intents_v6_block() -> str:
    """8-value user_intent enum + definitions for the inference prompt."""
    from ..config import USER_INTENTS_V6, USER_INTENTS_V6_DEF
    lines = ["user_intent enum (pick ONE; theory-grounded HELP-E taxonomy):"]
    for intent in USER_INTENTS_V6:
        lines.append(f"  - {intent}: {USER_INTENTS_V6_DEF[intent]}")
    return "\n".join(lines)


def format_candidate_strategies_block(candidate_bundle: dict) -> str:
    """Render the per-turn candidate-strategy bundle from
    `mi_selector_v6.select_candidates_v6` for the USER prompt's
    CANDIDATE STRATEGIES section.

    The bundle has shape:
        {
          ttm_stage, transition_target,
          common_candidates: [{code, label, what, transition_fn}, ...],
          stage_specific_candidates: [...],
          ...
        }
    """
    common = candidate_bundle.get("common_candidates") or []
    spec = candidate_bundle.get("stage_specific_candidates") or []
    target = candidate_bundle.get("transition_target")

    common_codes = ", ".join(c["code"] for c in common) if common else "(none)"

    lines = [f"  COMMON: {common_codes}"]
    if spec:
        if target:
            lines.append(f"  STAGE-SPECIFIC (target: {target}):")
        else:
            lines.append("  STAGE-SPECIFIC:")
        for c in spec:
            lines.append(
                f"    - {c['code']} — {c['transition_fn']}"
            )
    else:
        lines.append("  STAGE-SPECIFIC: (none — cold start, no main problem yet)")
    return "\n".join(lines)


def format_user_intent_block_v6(intent: str, entry_style: str) -> str:
    """Render the USER prompt's USER_INTENT block."""
    return textwrap.dedent(f"""\
          intent: {intent}
          entry: {entry_style}\
        """)


def format_main_problem_block_v6(
    graph,  # ProblemGraphV6
    main_name: Optional[str],
) -> str:
    """Render the USER prompt's MAIN PROBLEM block.

    Cold start (`main_name` is None): "(none yet — chatbot has nothing
    inferred for this turn)".
    """
    if main_name is None or main_name not in graph.problems:
        return "  (none yet — chatbot has nothing inferred for this turn)"
    p = graph.problems[main_name]
    parts = [
        f"  name: {main_name}",
        f"  current_ttm_stage: {p.current_ttm_stage}",
    ]
    if p.goal:
        parts.append(f'  goal: "{p.goal}"')
    return "\n".join(parts)


def _format_problem_attributes_v6(prob) -> list[str]:
    """For one ProblemNode, return lines listing every level + non-level
    attribute with all stacked entries (`s<S>t<T>: "user's words"`).
    Per §6 USER prompt — no observer-style explanations.
    """
    out: list[str] = []
    # Level attributes
    for attr_name, state in prob.level_attributes.items():
        if not state.evidence_stack:
            continue
        out.append(f"      {attr_name} = {state.current_level}")
        for e in state.evidence_stack:
            quote = e.supporting_utterance_span or e.inferred_information
            out.append(f"        s{e.session_id}t{e.turn_id}: \"{quote}\"")
    # Non-level attributes
    for attr_name, state in prob.non_level_attributes.items():
        if not state.evidence_stack:
            continue
        out.append(f"      {attr_name}")
        for e in state.evidence_stack:
            quote = e.supporting_utterance_span or e.inferred_information
            out.append(f"        s{e.session_id}t{e.turn_id}: \"{quote}\"")
    return out


def format_relevant_problems_block_v6(
    graph,  # ProblemGraphV6
    main_name: Optional[str],
    top_s_neighbors: int = 2,
) -> str:
    """Render the USER prompt's EVIDENCE / Relevant problems block.

    Includes main problem + top-S neighbors by current edge weight.
    Per problem: header (name, MAIN/related, ttm, goal) + every HBM
    attribute with all stacked entries. Connections shown inline at the
    end as a `### Connections` sub-block.

    Cold start: returns "(no problems yet)".
    """
    if main_name is None or main_name not in graph.problems:
        return "    (no problems yet)"

    out: list[str] = []

    # Main problem block
    main_prob = graph.problems[main_name]
    main_header = (
        f"    ### {main_name} (MAIN, ttm={main_prob.current_ttm_stage}"
    )
    if main_prob.goal:
        main_header += f', goal="{main_prob.goal}"'
    main_header += ")"
    out.append(main_header)
    main_lines = _format_problem_attributes_v6(main_prob)
    if main_lines:
        out.extend(main_lines)
    else:
        out.append("      (no attribute evidence yet)")
    out.append("")  # blank line between problems

    # Top-S neighbors
    ctx = graph.select_relevant_context(main_name, top_s=top_s_neighbors)
    neighbor_specs = ctx.get("relevant_problems") or []
    rendered_neighbors: list[str] = []
    connection_lines: list[str] = []

    for nb in neighbor_specs:
        other_name = nb["problem_name"]
        if other_name not in graph.problems:
            continue
        other_prob = graph.problems[other_name]
        weight = nb.get("edge_weight_to_main_problem", 0.0)
        nb_header = (
            f"    ### {other_name} (edge_weight={weight:.2f} to {main_name}"
        )
        if other_prob.goal:
            nb_header += f', goal="{other_prob.goal}"'
        nb_header += f", ttm={other_prob.current_ttm_stage})"
        rendered_neighbors.append(nb_header)
        nb_lines = _format_problem_attributes_v6(other_prob)
        if nb_lines:
            rendered_neighbors.extend(nb_lines)
        else:
            rendered_neighbors.append("      (no attribute evidence yet)")
        rendered_neighbors.append("")
        # Collect connection evidence for this neighbor pair
        for ce in nb.get("connection_evidence") or []:
            span = ce.get("supporting_utterance_span") or ""
            connection_lines.append(
                f"      {main_name} ↔ {other_name} via "
                f"{ce.get('relation_type', 'unclear_but_related')} "
                f"(s{ce.get('session_id')}t{ce.get('turn_id')}): \"{span}\""
            )

    out.extend(rendered_neighbors)

    # Connections sub-block (only if there are any)
    if connection_lines:
        out.append("    ### Connections")
        out.extend(connection_lines)

    return "\n".join(out).rstrip()


def format_persona_block_v6(persona_dict: dict) -> str:
    """Render the USER prompt's PERSONA block.

    Cold start (every field empty): "(none yet)".
    """
    populated = []
    for name in _PERSONA_V6_FIELDS:
        v = persona_dict.get(name)
        if v in (None, "", []):
            continue
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        populated.append(f"    {name}: {v}")
    if not populated:
        return "    (none yet)"
    return "\n".join(populated)


def format_recent_turns_block_v6(turns: list[dict]) -> str:
    """Render the USER prompt's RECENT TURNS block.

    Each turn: `[t<T> <role>]: "..."`.
    Cold start (empty): "(no prior turns this session)".
    """
    if not turns:
        return "    (no prior turns this session)"
    lines = []
    for t in turns:
        role = t.get("role", "?")
        tid = t.get("turn_id", "?")
        text = t.get("text", "")
        lines.append(f'    [t{tid} {role}]: "{text}"')
    return "\n".join(lines)


def format_past_two_turns_block_v6(past_turns_data: list[dict]) -> str:
    """Render the USER prompt's PAST TWO TURNS block (for response diversity).

    Each entry shape: {turn_offset: -1|-2, main_problem: str, strategies: [str, ...]}
    where turn_offset = -1 is the most recent prior turn.

    Cold start (empty): "(no prior turns this session)".
    """
    if not past_turns_data:
        return "  (no prior turns this session)"
    lines = []
    for entry in past_turns_data:
        offset = entry.get("turn_offset")
        main = entry.get("main_problem") or "(none)"
        strats = entry.get("strategies") or []
        strats_str = ", ".join(strats) if strats else "(none)"
        lines.append(f"  t{offset}: main={main}, strategies=[{strats_str}]")
    return "\n".join(lines)


def problem_vocab_block_v6() -> str:
    """20-problem vocabulary block (v1–v5 helper exists in common.py;
    this re-exports for v6 callers that want the same content without
    the legacy header text)."""
    from ..config import PROBLEM_VOCAB
    lines = ["20-problem vocabulary (the only problem names you can use):"]
    for name in PROBLEM_VOCAB:
        lines.append(f"  - {name}")
    return "\n".join(lines)
