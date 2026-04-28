"""§5 + §6 v6 (REDESIGN) — Response generation.

ONE LLM call per turn. Produces three fields:

  - `reasoning`           — 4 short sentences walking the reasoning chain
                             (Where? Which strategies? Evidence? Entry?)
  - `evidence_used`       — list of {source, content} pointers to every
                             evidence piece used in `final_response`
  - `final_response`      — the user-facing reply

SYSTEM prompt: stable per turn (cacheable); covers IDENTITY, PURPOSE,
SKILLS, HOW YOU USE INFORMATION, REASONING CHAIN, CONSTRAINTS, OUTPUT.

USER prompt: per-turn data (MAIN PROBLEM, USER_INTENT, CANDIDATE
STRATEGIES, EVIDENCE, PAST TWO TURNS, CURRENT USER MESSAGE). Section
names mirror the SYSTEM prompt's HOW-YOU-USE block 1:1.

See the canonical plan at
/Users/maitry/.claude/plans/i-want-u-to-lovely-boot.md (§5, §6, §8).
"""
from __future__ import annotations

import logging
import re
import textwrap
from typing import Any, Optional

log = logging.getLogger(__name__)

from .config import (
    MISC_CODES,
    MISC_INCONSISTENT_CODES,
    USER_INTENTS_V6,
)
from .graph_v6 import ProblemGraphV6
from .llm_client import CallContext, LLMClient, LLMStructuredError
from .prompts.common_v6 import (
    format_candidate_strategies_block,
    format_main_problem_block_v6,
    format_past_two_turns_block_v6,
    format_persona_block_v6,
    format_recent_turns_block_v6,
    format_relevant_problems_block_v6,
    format_user_intent_block_v6,
    misc_inconsistent_codes_block_v6,
    mi_principles_block_v6,
    oars_skills_block_v6,
)


# Caps consistent with §5 CONSTRAINTS.
MAX_V6_RESPONSE_SENTENCES: int = 6
MAX_V6_RESPONSE_QUESTIONS: int = 1
MAX_V6_REASONING_WORDS: int = 200       # ~4 short sentences

# Banned opening patterns (compared case-insensitively against the
# first 40 characters of `final_response` after stripping leading
# whitespace). Restored after the prompt-strengthening rewrite — the
# SYSTEM prompt now lists these as MUST-NOT openers so the LLM
# obeys, and the validator stays as a safety net.
_BANNED_OPENERS: tuple[str, ...] = (
    "it sounds like",
    "it seems like",
    "it's like",
    "that sounds like",
    "that sounds really",
    "that must be really",
    "i can only imagine",
    "that can be a really tough",
    "that can be really tough",
    "that's a really tough place",
)
_BANNED_PHRASES: tuple[str, ...] = (
    "tough to navigate",
    "tough thing to navigate",
    "hard thing to swallow",
    "i'm here for you",
    "i'm here to listen",
)

# Intents that demand a declarative answer (not just an open question).
_DECLARATIVE_REQUIRED_INTENTS: tuple[str, ...] = ("request_plan", "seek_information")


# ---------------------------------------------------------------------------
# JSON schema
# ---------------------------------------------------------------------------


RESPONSE_V6_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reasoning", "evidence_used", "final_response"],
    "properties": {
        # Accept reasoning as a single string (preferred) OR an array of
        # strings (LLMs love bulleting one-per-question). The validator
        # normalizes arrays to a joined string before downstream checks.
        "reasoning": {
            "oneOf": [
                {"type": "string", "minLength": 1},
                {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1},
                },
            ],
        },
        "evidence_used": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source", "content"],
                "properties": {
                    "source": {"type": "string", "minLength": 1},
                    "content": {"type": "string", "minLength": 1},
                },
            },
        },
        "final_response": {"type": "string", "minLength": 1},
    },
}


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def build_system_prompt() -> str:
    """Canonical SYSTEM prompt — stable per turn."""
    return textwrap.dedent(f"""\
        # IDENTITY
        You are HELP-E, a wellbeing companion — friend, wellwisher, and
        quiet guide rolled into one. Someone the user trusts enough to
        bring real problems to. You have memory of what they've told you
        (given each turn under EVIDENCE) and emotional awareness. You're
        not a chatbot, not a therapist, not a parent. You're the friend
        who has sat through hard things with people before, knows when
        to ask and when to say, and how to say it so it lands for THIS
        person — never makes someone feel small.

        # PURPOSE
        Help the user navigate everyday wellbeing problems — work stress,
        grief, relationships, body image, sleep. Two grounded principles:
          - ESC (Emotional Support Conversation): always begin by
            acknowledging what the user is in — briefly, grounded in
            specific evidence — before any further move. Listening alone
            isn't enough; the response has to show that you saw them.
          - MI (Motivational Interviewing): draw out the user's own
            reasoning rather than impose yours; the user owns their
            choices. Empathy is the through-line.

        # SKILLS
        Two layers, both yours every turn:

        {oars_skills_block_v6()}

        {mi_principles_block_v6()}

        MISC strategies are explicit MI moves you pick from the per-turn
        CANDIDATE STRATEGIES list (described below). Each carries its
        definition + transition function inline.

        # HOW YOU USE INFORMATION
        Each turn you receive:

          - MAIN PROBLEM (with TTM stage) — the problem to focus on and
            where the user is on the change journey for it.

          - USER_INTENT (with entry note) — what the user is asking
            from THIS turn (vent → reflect, ask → answer, ambivalent →
            reflect both sides). Honor it FIRST; the strategic MISC
            move comes after.

          - EVIDENCE — your memory of this person, used as the source
            of HELP and UNDERSTANDING (not diagnosis):
              * Relevant problems with full graph data — every HBM
                attribute, stacked entries (the user's actual words by
                session/turn), and typed problem-problem connections.
                Read these to find EITHER:
                  (a) WHAT MIGHT HELP this person right now — a coping
                      strategy they've tried, a past_attempt that
                      worked, a hobby/interest, their stated goal,
                      strengths implied by their language, or a POSITIVE
                      relation between problems (e.g. one problem's
                      coping that lifts another, a hobby that bridges
                      domains).
                  (b) WHAT TO ACKNOWLEDGE so they feel SEEN — a barrier
                      they've named, a trigger pattern, or a NEGATIVE
                      relation they're carrying (one problem feeding
                      another).
                Cite specific entries by content, not by label.
              * Persona — communication style + behavioral traits;
                anchors (hobbies, strengths) you can lean on.
              * Recent turns — for continuity and callback.

          - CANDIDATE STRATEGIES — the MISC strategies appropriate for
            the user's current TTM stage on the main problem (computed
            by rule). Pick one or more, layered on the OARS baseline.

          - PAST TWO TURNS — main problem + MISC strategies used in
            the last 2 turns. Use for diversity: pick a different
            strategy from the candidate list unless the moment clearly
            demands the same one.

        # REASONING CHAIN
        Your `reasoning` field is a SINGLE STRING with 4 short sentences
        IN ORDER (NOT a JSON list, NOT bullets):

          1. Who am I to this person right now, and what do they need? —
             combine your role with USER_INTENT (priority signal).
             Empathy first.
          2. Which strategies? — one or more MISC codes from the
             candidate list, tied to intent AND stage. Layered on OARS.
          3. Evidence? — 1–2 specific pieces BY CONTENT chosen for
             EITHER (a) something that HELPS them right now, or (b)
             something that ACKNOWLEDGES so they feel understood.
          4. Entry? — your first sentence(s): a brief, empathic,
             evidence-grounded acknowledgment of what they're in. The
             strategic move comes after.

        Priority when signals conflict:
          user_intent + empathy  >  TTM stage  >  evidence  >  style

        # CONSTRAINTS

        Use evidence, never echo. The user's words are evidence for your
        reasoning, not material for your response. Don't quote,
        paraphrase, or reflect with their key words. Acknowledgment ≠
        generic ("that sounds tough") and ≠ quoting them — it's naming
        the situation/pattern, anchored in SPECIFIC evidence. Reflection
        means saying what's UNDERNEATH, in your own words.

        Graph-relation policy.
          - POSITIVE relations can be named in the reply when relevant —
            they reinforce something already working.
          - NEGATIVE relations stay PRIVATE — never told to the user as
            a diagnostic statement; use them silently to choose what to
            offer or acknowledge.
          - Whether to OFFER a coping suggestion at all depends on the
            MISC strategy you picked: `advise_with_permission`,
            `inform_with_permission`, `raise_concern_with_permission`,
            and `structure` allow offering. Pure listening codes —
            `complex_reflection`, `support`, `facilitate`, `evoke` —
            mean hold the suggestion and just acknowledge or evoke
            their own thinking.
          - Never name HBM labels ("perceived_severity is high")
            regardless of strategy.

        {misc_inconsistent_codes_block_v6()}

        Answer direct asks. When intent is `request_plan` or
        `seek_information`, answer the question. Don't deflect with
        another open question.

        Speak like a person. Contractions, warmth, occasional "yeah".
        No clinical or therapy-speak. No commands, lectures, or
        moralizing — suggestions are theirs to take or leave.

        # OUTPUT FORMAT
        Three fields:
          - `reasoning`: SINGLE STRING, 4 short sentences walking the
            chain above. NOT a JSON list, NOT bullets — one plain
            string with sentence-level "Who/Which/Evidence/Entry"
            structure.
          - `evidence_used`: a list of {{source, content}} pointers to
            every piece of evidence you actually used in `final_response`.
            Each entry is EITHER help-oriented (coping strategy, past
            attempt, hobby, goal, strength, positive relation) OR
            acknowledgment-oriented (a specific barrier, trigger, or
            moment they've named). Avoid pure-diagnostic citations.

            `source` MUST start with EXACTLY ONE of these prefixes
            (no exceptions; bare problem names like `career_uncertainty`
            or bare turn anchors like `s1t4` are INVALID):
              * `hbm_attribute.<attr_name> (main_problem=<problem>, s<S>t<T>)`
                — for any HBM attribute evidence (perceived_severity,
                triggers, coping_strategies, goal, etc.) drawn from a
                problem in the EVIDENCE block. Include the originating
                problem in parentheses.
              * `problem_connection.<a>↔<b> (s<S>t<T>)` — for typed
                cross-problem edges. Use this whenever you reference a
                relationship between two problems (positive connections
                may also surface in the reply; negative connections
                must stay private and only inform your reasoning).
              * `persona.<field>` — e.g. `persona.hobbies_interests`,
                `persona.communication_style`.
              * `recent_turn.t<T>.<role>` — e.g. `recent_turn.t3.user`,
                `recent_turn.t2.assistant`.

            Examples (these prefix-formats are the ONLY ones accepted):
              * `hbm_attribute.coping_strategies (main_problem=academic_pressure, s2t3)`
              * `hbm_attribute.triggers (main_problem=sleep_problems, s1t4)`
              * `problem_connection.academic_pressure↔sleep_problems (s2t1)`
              * `persona.hobbies_interests`
              * `recent_turn.t5.user`
          - `final_response`: the user-facing reply. ≤ {MAX_V6_RESPONSE_SENTENCES}
            sentences. ≤ {MAX_V6_RESPONSE_QUESTIONS} question total.

        # HARD RULES — VIOLATIONS ARE REJECTED, FORCING A RETRY
        These rules are enforced by an automatic post-validator. Every
        violation costs an extra LLM round-trip. Read them before you
        write `final_response`.

        1. `final_response` MUST NOT START with ANY of these phrases:
              - "It sounds like"
              - "It seems like"
              - "It's like"
              - "That sounds like"
              - "That sounds really"
              - "That must be really"
              - "I can only imagine"
              - "That can be a really tough"
              - "That can be really tough"
              - "That's a really tough place"
           Open with a SPECIFIC concrete detail from THIS turn instead
           (a thing they did, a moment they named, a trigger you saw).

        2. `final_response` MUST NOT CONTAIN these phrases ANYWHERE:
              - "tough to navigate"
              - "tough thing to navigate"
              - "hard thing to swallow"
              - "I'm here for you"
              - "I'm here to listen"

        3. `final_response` MUST be ≤ {MAX_V6_RESPONSE_SENTENCES} sentences.
           Count your sentences before answering. Cut a sentence if you're
           at the limit. Three short sentences usually beats six long ones.

        4. `final_response` MUST contain ≤ {MAX_V6_RESPONSE_QUESTIONS}
           question. ONE question max. If you're tempted to ask two,
           pick the one that matters most and turn the other into an
           observation.

        5. If `user_intent` is `request_plan` or `seek_information`,
           `final_response` MUST contain at least one DECLARATIVE
           sentence (something concrete: a step, a piece of info, a
           named option). Don't deflect a direct ask with another
           open question.

        6. `reasoning` MUST be a SINGLE STRING (not a JSON list, not
           bullets), ≤ 200 words.

        7. The MISC strategies you name in `reasoning` MUST be drawn
           from the candidate list given to you under
           "CANDIDATE STRATEGIES" this turn. Naming a strategy that
           is NOT in that list is rejected. The candidates are
           computed by rule from the user's TTM stage — pick from
           those, do not invent codes.

        Return ONLY valid JSON matching the schema. No prose before or
        after.
    """)


def build_user_prompt(
    *,
    graph: ProblemGraphV6,
    candidate_bundle: dict,            # from mi_selector_v6.select_candidates_v6
    past_two_turns: list[dict],        # [{turn_offset, main_problem, strategies}, ...]
    recent_turns: list[dict],          # [{role, turn_id, text}, ...]
    current_user_message: str,
    top_s_neighbors: int = 2,
) -> str:
    """Canonical USER prompt — per-turn data, mirrors SYSTEM HOW-YOU-USE block."""
    main_name = candidate_bundle.get("main_problem")
    user_intent = candidate_bundle.get("user_intent", "small_talk")
    intent_entry = candidate_bundle.get("intent_entry_style", "")
    persona_dict = _persona_to_dict(graph.persona)

    return textwrap.dedent(f"""\
        # MAIN PROBLEM
        {format_main_problem_block_v6(graph, main_name)}

        # USER_INTENT
        {format_user_intent_block_v6(user_intent, intent_entry)}

        # CANDIDATE STRATEGIES
        {format_candidate_strategies_block(candidate_bundle)}

        # EVIDENCE

          ## Relevant problems
        {format_relevant_problems_block_v6(graph, main_name, top_s_neighbors=top_s_neighbors)}

          ## Persona
        {format_persona_block_v6(persona_dict)}

          ## Recent turns this session
        {format_recent_turns_block_v6(recent_turns)}

        # PAST TWO TURNS
        {format_past_two_turns_block_v6(past_two_turns)}

        # CURRENT USER MESSAGE
          {current_user_message!r}
    """).rstrip()


def _persona_to_dict(persona) -> dict:
    """Convert PersonaState to a plain dict for the persona block formatter.

    Lazy import to avoid cycle.
    """
    from dataclasses import asdict
    return asdict(persona) if persona is not None else {}


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
# MISC strategy code names are snake_case; match identifier-style words
# of length ≥ 3 against the known set.
_WORD_RE = re.compile(r"\b[a-z_][a-z0-9_]+\b")


def _count_sentences(text: str) -> int:
    t = text.strip()
    if not t:
        return 0
    return len([s for s in _SENTENCE_SPLIT.split(t) if s.strip()])


def _count_questions(text: str) -> int:
    return text.count("?")


def _count_words(text: str) -> int:
    return len(text.split())


def _has_declarative_sentence(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    for s in _SENTENCE_SPLIT.split(t):
        s = s.strip()
        if not s:
            continue
        if not s.endswith("?"):
            return True
    return False


def _check_banned_patterns(text: str) -> Optional[str]:
    stripped = text.lstrip()
    opener = stripped[:40].lower()
    for bad in _BANNED_OPENERS:
        if opener.startswith(bad):
            return (
                f"final_response opens with banned template pattern {bad!r}; "
                "vary your opening (name the specific thing, drop the template)"
            )
    low = text.lower()
    for bad in _BANNED_PHRASES:
        if bad in low:
            return (
                f"final_response contains banned template phrase {bad!r}; "
                "reword in your own voice"
            )
    return None


def _extract_misc_codes_from_reasoning(reasoning: str) -> set[str]:
    """Find MISC code names mentioned in reasoning text.

    Matches snake_case identifiers and intersects with the union of
    selectable MISC codes AND MI-inconsistent anti-pattern codes — so the
    validator catches both off-list selectable codes AND any anti-pattern
    code (which is never in any candidate set).
    """
    known = set(MISC_CODES.keys()) | set(MISC_INCONSISTENT_CODES.keys())
    found: set[str] = set()
    for m in _WORD_RE.finditer(reasoning.lower()):
        word = m.group(0)
        if word in known:
            found.add(word)
    return found


def _normalize_reasoning(out: dict) -> None:
    """If `reasoning` came back as a list of strings, join them into one
    string in place — downstream code (validators + analysis) expects a
    string. The schema's oneOf already accepted both shapes; this just
    canonicalizes.
    """
    r = out.get("reasoning")
    if isinstance(r, list):
        out["reasoning"] = " ".join(s.strip() for s in r if isinstance(s, str) and s.strip())


def _validate_factory(candidate_codes: set[str], user_intent: Optional[str] = None):
    def _check(out: dict) -> None:
        # Normalize array-shaped reasoning into a single string before
        # any string-based check below.
        _normalize_reasoning(out)

        # `reasoning` must be present and within word cap.
        reasoning = out["reasoning"]
        if _count_words(reasoning) > MAX_V6_REASONING_WORDS:
            raise ValueError(
                f"reasoning is {_count_words(reasoning)} words "
                f"(> {MAX_V6_REASONING_WORDS})"
            )

        # If candidate_codes is non-empty, every MISC code named in
        # reasoning must be in the candidate set (prevents picking
        # off-list strategies). Empty candidate_codes (cold-start fallback)
        # skips this check.
        if candidate_codes:
            named = _extract_misc_codes_from_reasoning(reasoning)
            extras = named - candidate_codes
            if extras:
                raise ValueError(
                    f"reasoning names MISC strategies not in candidate set: "
                    f"{sorted(extras)} (candidates: {sorted(candidate_codes)})"
                )

        # final_response: length, question, banned patterns.
        fr = out["final_response"]
        s = _count_sentences(fr)
        if s > MAX_V6_RESPONSE_SENTENCES:
            raise ValueError(
                f"final_response has {s} sentences (> {MAX_V6_RESPONSE_SENTENCES})"
            )
        q = _count_questions(fr)
        if q > MAX_V6_RESPONSE_QUESTIONS:
            raise ValueError(
                f"final_response has {q} questions (> {MAX_V6_RESPONSE_QUESTIONS})"
            )
        banned_err = _check_banned_patterns(fr)
        if banned_err is not None:
            raise ValueError(banned_err)

        # Direct-ask intents demand a declarative sentence.
        if user_intent in _DECLARATIVE_REQUIRED_INTENTS and not _has_declarative_sentence(fr):
            raise ValueError(
                f"intent is {user_intent!r} but final_response contains no "
                "declarative sentence — the user asked; deliver SOMETHING "
                "(small suggestion, EPE, named option) instead of just a "
                "question back"
            )

        if not isinstance(out["evidence_used"], list):
            raise ValueError("evidence_used must be a list")
    return _check


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _fallback_response(user_intent: Optional[str]) -> dict:
    """Safe fallback when the response call exhausts retries."""
    return {
        "reasoning": (
            "Where: user state unclear. Which: T1-equivalent reflection only. "
            "Evidence: none surfaced this turn. Entry: stay present without "
            "advancing any agenda."
        ),
        "evidence_used": [],
        "final_response": "Whatever you want to say next, I'm listening.",
        "_fallback_default": True,
    }


def run_response_v6(
    *,
    client: LLMClient,
    ctx: CallContext,
    graph: ProblemGraphV6,
    candidate_bundle: dict,        # from mi_selector_v6.select_candidates_v6
    past_two_turns: list[dict],    # [{turn_offset, main_problem, strategies}, ...]
    recent_turns: list[dict],
    current_user_message: str,
    top_s_neighbors: int = 2,
) -> dict:
    """Execute the v6 redesign response call. On failure returns a safe
    fallback so the pipeline can continue.
    """
    assert ctx.call_role == "response_v6"

    candidate_codes = set(candidate_bundle.get("all_candidate_codes") or [])
    user_intent = candidate_bundle.get("user_intent")

    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(
                graph=graph,
                candidate_bundle=candidate_bundle,
                past_two_turns=past_two_turns,
                recent_turns=recent_turns,
                current_user_message=current_user_message,
                top_s_neighbors=top_s_neighbors,
            ),
            schema=RESPONSE_V6_SCHEMA,
            validator_extras=_validate_factory(candidate_codes, user_intent),
        )
    except LLMStructuredError:
        return _fallback_response(user_intent)


# ---------------------------------------------------------------------------
# Self-test (no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    from .graph_v6 import (
        AttributeConnectionEntry, AttributeEvidenceEntry,
        CooccurrenceEntry, ProblemGraphV6, global_turn_idx,
    )
    from .mi_selector_v6 import select_candidates_v6

    # --- Validator unit checks ---
    assert _count_sentences("One. Two. Three.") == 3
    assert _count_sentences("") == 0
    assert _count_questions("Is it? Hmm.") == 1
    assert _has_declarative_sentence("Why? Yes.") is True
    assert _has_declarative_sentence("Why? Maybe?") is False

    # Banned opener detection.
    assert _check_banned_patterns("It sounds like you are tired.").startswith(
        "final_response opens with banned"
    )
    assert _check_banned_patterns("Pulling all-nighters does that.") is None

    # MISC code extraction.
    found = _extract_misc_codes_from_reasoning(
        "I'll use complex_reflection and evoke this turn."
    )
    assert found == {"complex_reflection", "evoke"}

    # --- Validator factory ---
    cand = {"support", "facilitate", "evoke", "complex_reflection"}

    valid = {
        "reasoning": (
            "Where: user is in contemplation, intent express_emotion, needs space. "
            "Which: complex_reflection to develop discrepancy, support to anchor. "
            "Evidence: the all-nighters they mentioned, the 'finish without crashing' goal. "
            "Entry: name the body's signal, then sit with it."
        ),
        "evidence_used": [
            {"source": "hbm_attribute.triggers (main_problem=academic_pressure, s1t2)",
             "content": "all-nighters keep me at my desk"},
            {"source": "hbm_attribute.goal (main_problem=academic_pressure, s1t2)",
             "content": "finish finals without burning out"},
        ],
        "final_response": (
            "Three nights of cramming on top of finals would knock anyone flat. "
            "And underneath the deadline pressure is a quieter ask — "
            "to finish without burning out, not just to finish at any cost."
        ),
    }
    _validate_factory(cand, "express_emotion")(valid)

    # Banned opener -> rejected.
    bad = dict(valid)
    bad["final_response"] = "It sounds like you are tired and overwhelmed."
    try:
        _validate_factory(cand, "express_emotion")(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("expected rejection: banned opener")

    # Off-list MISC code -> rejected.
    bad2 = dict(valid)
    bad2["reasoning"] = (
        "Where: contemplation. Which: confront the user directly. "
        "Evidence: x. Entry: y."
    )
    try:
        _validate_factory(cand, "express_emotion")(bad2)
    except ValueError:
        pass
    else:
        raise AssertionError("expected rejection: off-list MISC code")

    # request_plan with only-question -> rejected.
    bad3 = dict(valid)
    bad3["final_response"] = "What do you think the next step would be?"
    try:
        _validate_factory(cand, "request_plan")(bad3)
    except ValueError:
        pass
    else:
        raise AssertionError("expected rejection: request_plan with no declarative")

    # Too many sentences -> rejected.
    bad4 = dict(valid)
    bad4["final_response"] = ". ".join(["sentence"] * 7) + "."
    try:
        _validate_factory(cand, "express_emotion")(bad4)
    except ValueError:
        pass
    else:
        raise AssertionError("expected rejection: >6 sentences")

    # Empty candidate_codes -> off-list check is skipped (cold-start path).
    fb = _fallback_response("express_emotion")
    _validate_factory(set(), "express_emotion")(fb)

    # Reasoning as a list (LLMs love bulleting one per question) — schema
    # accepts it; validator normalizes to a single string in place.
    list_reasoning = dict(valid)
    list_reasoning["reasoning"] = [
        "Where: contemplation, express_emotion, needs space.",
        "Which: complex_reflection to develop discrepancy, support to anchor.",
        "Evidence: triggers and goal.",
        "Entry: name the body's signal.",
    ]
    _validate_factory(cand, "express_emotion")(list_reasoning)
    assert isinstance(list_reasoning["reasoning"], str), (
        "validator should have joined list reasoning into a string"
    )
    assert "complex_reflection" in list_reasoning["reasoning"]

    # --- Prompt assembly: cold start ---
    g_cold = ProblemGraphV6(profile_id="T")
    bundle_cold = select_candidates_v6(
        graph=g_cold, main_problem_name=None, user_intent="small_talk",
    )
    sys = build_system_prompt()
    usr_cold = build_user_prompt(
        graph=g_cold,
        candidate_bundle=bundle_cold,
        past_two_turns=[],
        recent_turns=[],
        current_user_message="hi",
    )
    assert "IDENTITY" in sys and "REASONING CHAIN" in sys
    assert "OARS" in sys and "MISC strategies" in sys
    assert "Anti-patterns" in sys
    assert "MAIN PROBLEM" in usr_cold
    assert "USER_INTENT" in usr_cold
    assert "CANDIDATE STRATEGIES" in usr_cold
    assert "EVIDENCE" in usr_cold
    assert "PAST TWO TURNS" in usr_cold
    assert "(none yet" in usr_cold
    # Ensure cold-start COMMON candidates are surfaced.
    assert "support, facilitate" in usr_cold

    # --- Prompt assembly: populated graph ---
    g = ProblemGraphV6(profile_id="T")
    g.get_or_create_problem("academic_pressure", first_mentioned=(1, 1))
    g.set_ttm_stage("academic_pressure", "contemplation")
    g.append_evidence(
        problem_name="academic_pressure", attr_name="perceived_severity",
        entry=AttributeEvidenceEntry(
            session_id=1, turn_id=1,
            inferred_information="workload feels crushing",
            concise_explanation="x",
            supporting_utterance_span="this week is impossible",
            confidence="high",
        ),
    )
    g.set_level("academic_pressure", "perceived_severity", "high")

    bundle = select_candidates_v6(
        graph=g, main_problem_name="academic_pressure",
        user_intent="deliberate_decision",
    )
    past = [
        {"turn_offset": -1, "main_problem": "academic_pressure",
         "strategies": ["simple_reflection"]},
    ]
    recent = [
        {"role": "user", "turn_id": 1, "text": "this week is impossible"},
        {"role": "assistant", "turn_id": 1, "text": "Let me sit with that for a moment."},
    ]
    usr = build_user_prompt(
        graph=g, candidate_bundle=bundle,
        past_two_turns=past, recent_turns=recent,
        current_user_message="should I drop a course or push through?",
    )
    assert "academic_pressure" in usr
    assert "current_ttm_stage: contemplation" in usr
    assert "deliberate_decision" in usr
    assert "evoke" in usr  # contemplation candidate
    assert "this week is impossible" in usr  # evidence by content
    assert "t-1: main=academic_pressure" in usr
    assert "Let me sit with that" in usr
    assert "should I drop a course or push through?" in usr

    print("instruction_response_v6 (redesign) self-test PASSED")


if __name__ == "__main__":
    _self_test()
