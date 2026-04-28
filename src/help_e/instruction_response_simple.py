"""§5+§6 (REDESIGN) — Response generation for v1 / v3 / v4.

Mirror of `instruction_response_v6` with all HBM-related instructions
removed from the SYSTEM and USER prompts. The schema, validators,
banned-opener checks, sentence/question caps, and entry-point logic
are reused so the wins/losses across systems track to "what evidence
the chatbot has", NOT "how the response prompt is tuned for it".

Differences from `instruction_response_v6`:
  - SYSTEM prompt: no "Health Belief Model", no "HBM attribute" mentions,
    no "perceived_severity is high" example
  - USER prompt: EVIDENCE renders per-problem TEXT SUMMARIES (v3/v4) and
    CONNECTIONS (v4 only), never HBM attribute stacks
  - `evidence_used.source` format examples reference `problem_summary.*`
    and `problem_connection.*` instead of `hbm_attribute.*`

Used by:
  - v1 (history-only) — graph is empty, EVIDENCE renders nothing
  - v3 (text summary + TTM-from-summary) — renders per-problem summaries
  - v4 (text summary + TTM-from-summary + free-form connections) — adds
    a Connections sub-block under EVIDENCE
"""
from __future__ import annotations

import logging
import textwrap
from typing import Any, Optional

from .config import USER_INTENTS_V6
from .graph_v6 import ProblemGraphV6
from .llm_client import CallContext, LLMClient
from .prompts.common_v6 import (
    format_candidate_strategies_block,
    format_past_two_turns_block_v6,
    format_persona_block_v6,
    format_recent_turns_block_v6,
    format_user_intent_block_v6,
    misc_inconsistent_codes_block_v6,
    mi_principles_block_v6,
    oars_skills_block_v6,
)
# Reuse: schema, validators, banned-opener tables, fallback, helpers.
from .instruction_response_v6 import (
    MAX_V6_RESPONSE_QUESTIONS,
    MAX_V6_RESPONSE_SENTENCES,
    RESPONSE_V6_SCHEMA,
    _fallback_response,
    _persona_to_dict,
    _validate_factory,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema (reuse v6's; same 3 fields)
# ---------------------------------------------------------------------------


RESPONSE_SIMPLE_SCHEMA: dict[str, Any] = RESPONSE_V6_SCHEMA


# ---------------------------------------------------------------------------
# EVIDENCE rendering — summaries + connections (no HBM)
# ---------------------------------------------------------------------------


def _format_summary_problem(name: str, *, is_main: bool, ttm_stage: Optional[str],
                            summary: str) -> str:
    main_tag = "MAIN, " if is_main else ""
    stage_tag = f"ttm={ttm_stage}" if ttm_stage else "ttm=unknown"
    head = f"### {name} ({main_tag}{stage_tag})"
    body = f"  summary: {summary}" if summary else "  summary: (none yet)"
    return f"{head}\n{body}"


def _format_connections_block(connections: list[dict]) -> str:
    """Render free-form `{problem_a, problem_b, what, why, supporting_quote,
    turn_id}` connection records (v4) or HBM-typed connections that v6
    might emit. The renderer is shape-tolerant so v3 (no connections),
    v4 (free-form), and any future v6-style typed connections all work.
    """
    if not connections:
        return "  (none yet)"

    def _as_dict(c) -> dict:
        """Normalize a connection record to a dict regardless of source.
        v4 stores `_Connection` dataclass instances; future formats may
        emit plain dicts. Both work."""
        if isinstance(c, dict):
            return c
        if hasattr(c, "to_dict"):
            return c.to_dict()
        try:
            from dataclasses import asdict, is_dataclass
            if is_dataclass(c):
                return asdict(c)
        except Exception:
            pass
        # Last resort: pull common attrs by name.
        return {
            "problem_a": getattr(c, "problem_a", None),
            "problem_b": getattr(c, "problem_b", None),
            "what": getattr(c, "what", None),
            "why": getattr(c, "why", None),
            "supporting_quote": getattr(c, "supporting_quote", None),
            "session_id": getattr(c, "session_id", None),
            "turn_id": getattr(c, "turn_id", None),
        }

    lines: list[str] = []
    for raw in connections:
        c = _as_dict(raw)
        a = c.get("problem_a") or "?"
        b = c.get("problem_b") or "?"
        tid = c.get("turn_id")
        sid = c.get("session_id")
        loc = (
            f" (s{sid}t{tid})" if sid is not None and tid is not None
            else f" (t{tid})" if tid is not None else ""
        )
        what = c.get("what") or c.get("description") or ""
        why = c.get("why") or ""
        quote = c.get("supporting_quote") or c.get("supporting_utterance_span") or ""
        lines.append(f"    {a} ↔ {b}{loc}:")
        if what:
            lines.append(f"      what: {what}")
        if why:
            lines.append(f"      why:  {why}")
        if quote:
            lines.append(f"      quote: {quote!r}")
    return "\n".join(lines)


def format_relevant_problems_block_simple(graph: ProblemGraphV6,
                                          main_name: Optional[str]) -> str:
    """Render `## Relevant problems` for v1/v3/v4.

    Reads per-problem summaries + connections from the lightweight state
    objects v3/v4 attach to the graph (`graph._v3_state` /
    `graph._v4_state`). v1 attaches no state → returns "(none yet)".

    Output shape mirrors v6's relevant-problems block but with
    `summary:` instead of HBM attribute stacks.
    """
    # v3/v4 state shapes (defined in their respective baselines).
    v3_state = getattr(graph, "_v3_state", None)
    v4_state = getattr(graph, "_v4_state", None)
    state = v4_state or v3_state
    if state is None or not getattr(state, "summaries", None):
        return "    (none yet)"

    summaries = state.summaries
    main_problem = main_name if main_name in summaries else None

    blocks: list[str] = []
    if main_problem is not None:
        s = summaries[main_problem]
        blocks.append(_format_summary_problem(
            main_problem, is_main=True, ttm_stage=getattr(s, "ttm_stage", None),
            summary=getattr(s, "summary", ""),
        ))

    # Show up to 2 other connected/active problems.
    others = [n for n in summaries.keys() if n != main_problem][:2]
    for name in others:
        s = summaries[name]
        blocks.append(_format_summary_problem(
            name, is_main=False, ttm_stage=getattr(s, "ttm_stage", None),
            summary=getattr(s, "summary", ""),
        ))

    # Connections section — only for v4 (v3 has no connections).
    connections = list(getattr(state, "connections", []) or [])
    if connections:
        blocks.append("    ### Connections")
        blocks.append(_format_connections_block(connections))

    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def build_system_prompt() -> str:
    """Canonical SYSTEM prompt for v1/v3/v4. Same shape as response_v6 but
    with all HBM language stripped.
    """
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
              * Relevant problems with TEXT SUMMARIES — a few sentences
                per problem capturing what the user has shared so far,
                plus their TTM stage. For each problem, read the summary
                to find EITHER:
                  (a) WHAT MIGHT HELP this person right now — a coping
                      strategy mentioned, an attempt that worked, a
                      strength implied by their language, a stated goal.
                  (b) WHAT TO ACKNOWLEDGE so they feel SEEN — a barrier
                      named, a trigger pattern, an emotion underneath.
                Some turns may also include a "Connections" sub-block
                describing how two problems link (e.g. a shared trigger
                or one feeding another). When present, use a positive
                connection to reinforce something working; keep negative
                connections PRIVATE (use silently to choose what to
                acknowledge — don't tell the user "X is feeding Y" as a
                diagnostic statement).
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
             something that ACKNOWLEDGES so they feel understood. Cite
             from the per-problem summary or a connection — by what the
             user said, not by label.
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

        Connection policy.
          - POSITIVE connections (a hobby that lifts mood across
            problems, a coping that helps two domains) can be named in
            the reply when relevant — they reinforce what's working.
          - NEGATIVE connections (one problem driving another) stay
            PRIVATE — never told to the user as a diagnostic statement;
            use silently to choose what to offer or acknowledge.
          - Whether to OFFER a coping suggestion at all depends on the
            MISC strategy you picked: `advise_with_permission`,
            `inform_with_permission`, `raise_concern_with_permission`,
            and `structure` allow offering. Pure listening codes —
            `complex_reflection`, `support`, `facilitate`, `evoke` —
            mean hold the suggestion and just acknowledge or evoke
            their own thinking.

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
            Each entry is EITHER help-oriented (coping mentioned, past
            attempt, hobby, goal, strength, positive connection) OR
            acknowledgment-oriented (a specific barrier, trigger, or
            moment they've named). Avoid pure-diagnostic citations.
            Source format examples: `problem_summary.<problem_name>`,
            `problem_connection.<a>↔<b> (s<S>t<T>)`,
            `persona.<field>` (e.g. `persona.hobbies_interests`),
            `recent_turn.t<T>.<role>`.
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


def _format_main_problem_simple(graph: ProblemGraphV6,
                                main_name: Optional[str]) -> str:
    """v3/v4-friendly main-problem block. Reads TTM stage + goal (if any)
    from the lightweight v3/v4 state instead of the full graph node.
    """
    if main_name is None:
        return "  (none — cold start or no clear focus)"
    state = getattr(graph, "_v4_state", None) or getattr(graph, "_v3_state", None)
    if state is None or main_name not in getattr(state, "summaries", {}):
        return f"  name: {main_name}\n  current_ttm_stage: unknown"
    s = state.summaries[main_name]
    ttm = getattr(s, "ttm_stage", "unknown")
    return f"  name: {main_name}\n  current_ttm_stage: {ttm}"


def build_user_prompt(
    *,
    graph: ProblemGraphV6,
    candidate_bundle: dict,
    past_two_turns: list[dict],
    recent_turns: list[dict],
    current_user_message: str,
) -> str:
    """v1/v3/v4 USER prompt — same shape as response_v6 but EVIDENCE
    renders per-problem text summaries + (v4 only) connections, never
    HBM attribute stacks.
    """
    main_name = candidate_bundle.get("main_problem")
    user_intent = candidate_bundle.get("user_intent", "small_talk")
    intent_entry = candidate_bundle.get("intent_entry_style", "")
    persona_dict = _persona_to_dict(graph.persona)

    return textwrap.dedent(f"""\
        # MAIN PROBLEM
        {_format_main_problem_simple(graph, main_name)}

        # USER_INTENT
        {format_user_intent_block_v6(user_intent, intent_entry)}

        # CANDIDATE STRATEGIES
        {format_candidate_strategies_block(candidate_bundle)}

        # EVIDENCE

          ## Relevant problems
        {format_relevant_problems_block_simple(graph, main_name)}

          ## Persona
        {format_persona_block_v6(persona_dict)}

          ## Recent turns this session
        {format_recent_turns_block_v6(recent_turns)}

        # PAST TWO TURNS
        {format_past_two_turns_block_v6(past_two_turns)}

        # CURRENT USER MESSAGE
          {current_user_message!r}
    """).rstrip()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_response_simple(
    *,
    client: LLMClient,
    ctx: CallContext,
    graph: ProblemGraphV6,
    candidate_bundle: dict,
    past_two_turns: list[dict],
    recent_turns: list[dict],
    current_user_message: str,
) -> dict:
    """Execute the v1/v3/v4 response call. On failure returns a safe
    fallback (the same `_fallback_response` v6 uses) so the pipeline
    can continue.
    """
    assert ctx.call_role == "response_simple", (
        f"run_response_simple expects call_role='response_simple', got {ctx.call_role!r}"
    )

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
            ),
            schema=RESPONSE_SIMPLE_SCHEMA,
            validator_extras=_validate_factory(candidate_codes, user_intent),
        )
    except Exception as e:
        log.warning("response_simple failed (%s); using fallback", e)
        return _fallback_response(user_intent)


# ---------------------------------------------------------------------------
# Self-test (no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    sp = build_system_prompt()
    # NO HBM language anywhere.
    assert "HBM" not in sp, "response_simple SYSTEM prompt mentions HBM"
    assert "Health Belief" not in sp
    assert "perceived_severity" not in sp
    assert "hbm_attribute" not in sp
    # KEEPS the rest.
    assert "MISC" in sp
    assert "OARS" in sp
    assert "USER_INTENT" in sp
    assert "CANDIDATE STRATEGIES" in sp
    assert "evidence_used" in sp
    assert "problem_summary." in sp  # source-format hint for v3/v4

    # USER prompt with empty graph → "(none yet)".
    g = ProblemGraphV6(profile_id="T")
    cb = {
        "main_problem": None, "user_intent": "small_talk",
        "intent_entry_style": "be brief",
        "common_candidates": [], "stage_specific_candidates": [],
        "all_candidate_codes": [],
    }
    up = build_user_prompt(
        graph=g, candidate_bundle=cb, past_two_turns=[],
        recent_turns=[], current_user_message="hi",
    )
    assert "## Relevant problems" in up
    assert "(none yet)" in up

    # Connection rendering — fake a v4 state with a connection.
    class _FakeSummary:
        def __init__(self, summary, ttm_stage):
            self.summary = summary
            self.ttm_stage = ttm_stage

    class _FakeState:
        def __init__(self):
            self.summaries = {
                "academic_pressure": _FakeSummary(
                    "Finals week, sleep-deprived.", "contemplation"),
                "sleep_problems": _FakeSummary(
                    "Lying awake replaying the day.", "contemplation"),
            }
            self.connections = [{
                "problem_a": "academic_pressure",
                "problem_b": "sleep_problems",
                "what": "the late-night studying is what's keeping the user awake",
                "why":  "user mentioned 'all-nighters keep me up replaying'",
                "supporting_quote": "all-nighters keep me up replaying",
                "session_id": 1, "turn_id": 1,
            }]
    g._v4_state = _FakeState()  # type: ignore[attr-defined]

    block = format_relevant_problems_block_simple(g, "academic_pressure")
    assert "academic_pressure (MAIN" in block
    assert "summary: Finals week" in block
    assert "sleep_problems" in block
    assert "Connections" in block
    assert "academic_pressure ↔ sleep_problems" in block
    assert "what: the late-night studying" in block
    assert "all-nighters keep me up replaying" in block

    # Schema is the same shape as v6.
    assert RESPONSE_SIMPLE_SCHEMA is RESPONSE_V6_SCHEMA

    print("response_simple self-test PASSED")


if __name__ == "__main__":
    _self_test()
