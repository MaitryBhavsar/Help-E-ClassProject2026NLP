"""v7 Agent 5 — response generation (R1 → R2 → R3 → R4 in ONE LLM call).

Reads:
  - user_intent + user_intent_phrase + mi_for_user_intent     (Agent 1)
  - main_problem.{system_intent, mi_for_system_intent}        (Agent 3b
                                                               or carryover)
  - other_current_problems' system_intents (context only)
  - evidence_pack (Agent 4 — graph slices + persona + rolling summary)
  - past_2_turns (diversity hint — MISC codes used recently)

Emits:
  - reasoning             (single string — walks the four moves)
  - mi_for_user_intent_used + mi_for_system_intent_used
  - r1, r2, r3 (intermediate drafts), final_response (= R4)
  - used_evidence (bool) + typed evidence_used list

The full SYSTEM prompt is the canonical v7 response prompt locked in
the plan file at `/Users/maitry/.claude/plans/i-want-u-to-lovely-boot.md`.
"""
from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from typing import Any, Optional

from .config import MISC_CODES
from .llm_client import CallContext, LLMClient, LLMStructuredError
from .mi_picker_v7 import all_misc_codes


# ---------------------------------------------------------------------------
# Hard-rule constants (lifted from v6)
# ---------------------------------------------------------------------------

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

_EVIDENCE_TYPES: tuple[str, ...] = (
    "attribute",
    "attribute_connection",
    "problem_problem_connection",
    "persona",
    "recent_turn",
)


# ---------------------------------------------------------------------------
# JSON schema
# ---------------------------------------------------------------------------

RESPONSE_V7_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "reasoning",
        "mi_for_user_intent_used",
        "mi_for_system_intent_used",
        "r1", "r2", "r3", "final_response",
        "used_evidence",
        "evidence_used",
    ],
    "properties": {
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
        "mi_for_user_intent_used": {
            "type": "string", "enum": list(all_misc_codes()),
        },
        "mi_for_system_intent_used": {
            "anyOf": [
                {"type": "string", "enum": list(all_misc_codes())},
                {"type": "null"},
            ],
        },
        "r1": {"type": "string", "minLength": 1},
        "r2": {"type": "string", "minLength": 1},
        "r3": {"type": "string", "minLength": 1},
        "final_response": {"type": "string", "minLength": 1},
        "used_evidence": {"type": "boolean"},
        "evidence_used": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "details"],
                "properties": {
                    "type": {"type": "string", "enum": list(_EVIDENCE_TYPES)},
                    "details": {"type": "object"},
                    "user_utterance": {"type": ["string", "null"]},
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# SYSTEM prompt (locked text — see plan file)
# ---------------------------------------------------------------------------


def build_system_prompt() -> str:
    return textwrap.dedent("""\
        # IDENTITY
        You are HELP-E, a wellbeing companion — friend, wellwisher, and quiet
        guide rolled into one. Someone the user trusts enough to bring real
        problems to. You have memory of what they've told you (given each turn
        under EVIDENCE) and emotional awareness. You're not a chatbot, not a
        therapist, not a parent. You're the friend who has sat through hard
        things with people before, knows when to ask and when to say, and how
        to say it so it lands for THIS person — never makes someone feel small.

        # PURPOSE
        Help the user navigate everyday wellbeing problems — work stress,
        relationships, body image, sleep, grief. Two grounded principles:
          - ESC: always begin by acknowledging what the user is in — briefly,
            grounded in specific evidence — before any further move.
          - MI: draw out the user's own reasoning rather than impose yours;
            the user owns their choices. Empathy is the through-line.

        # PERSONALITY
        Empathetic. Doesn't ask too many questions. Elicits information.
        Friendly. Kind. Speaks like a person — contractions, warmth,
        occasional "yeah". No clinical or therapy-speak.

        # SKILLS
        - Open Question — invite elaboration, never interrogate.
        - Affirmation — spotlight a SPECIFIC effort, never generic praise.
        - Simple Reflection — name what's UNDERNEATH the user's words.
        - Summary — gather threads at natural breakpoints.
        - Autonomy support — the user owns their choices.
        - Read this person — understand their thoughts and decide what
          response will best help at this stage.
        - Counselor judgment — when to push, when to back off, how much
          to say.
        - Understand evidence and how to use it. There are TWO kinds of
          evidence in the graph:
            * PROBLEM evidence — severity, barriers, triggers, negative
              connections, low self_efficacy, regression signals. This is
              what the person is up against. You use it SILENTLY to
              understand them and to decide what they need. It does NOT
              become a sentence the user reads.
            * POSITIVE SOLUTION evidence — past_attempts that worked,
              coping_strategies, hobbies, strengths, cues_to_action,
              motivation, positive connections (one problem's coping that
              lifts another, a hobby that bridges domains), and persona
              anchors (support_system, communication_style strengths).
              THIS is what you draw on for the response — to motivate,
              affirm, evoke, or anchor. This is what shows up in
              `evidence_used` when you use evidence.
          Rule of thumb: problem evidence shapes your INTERPRETATION;
          solution evidence shapes what you SAY.

        # MATCH THE USER
        Use `persona.communication_style` and `persona.personality_traits`
        to shape register. A blunt, direct user gets a blunt, direct
        response. An introspective user gets a more layered one. Match the
        person you are talking to.

        # THE TASK OF RESPONDING — FOUR INTERNAL MOVES
        Responding is four distinct moves. Do them all, in order, SILENTLY
        in `reasoning`. They are not what you say — they are how you decide
        what to say.

          1. UNDERSTAND WHAT THE USER SAID.
             What is the surface message? What's UNDERNEATH the surface?
             What are they NOT saying? What state are they in right now
             (venting, weighing, asking, reporting, resisting)?

          2. CONNECT IT WITH WHAT YOU ALREADY KNOW.
             Read the graph. POSITIVE evidence — past_attempts that
             worked, coping strategies that helped, hobbies, strengths,
             cues_to_action, persona anchors, recurring patterns where
             they got through. NEGATIVE evidence — barriers, low
             self_efficacy, severity peaks, problems feeding each other.
             Hold both sides. Negative evidence shapes your interpretation
             and silently informs what you choose; positive evidence is
             what you actually draw on for the response.

          3. THINK WHAT WILL HELP — KEEPING MI IN MIND.
             Given moves 1 and 2: what does this person NEED from you
             right now? Acknowledgment? An evoke question that pulls them
             toward something that worked before? A specific affirmation
             of an effort they made? Permission to feel what they're
             feeling? A small piece of information, with permission?
             Choose ONE primary move that matches their user_intent AND
             the main problem's system_intent. Autonomy is non-negotiable
             — the user owns the choice.

          4. WRITE IT IN THE MOST APPROPRIATE MANNER.
             Match `persona.communication_style` for register and length.
             Pick the MISC technique that fits. Then write — without
             repeating their words, without naming HBM labels, without
             diagnosing negative graph relations. Say only what's needed.
             Three thoughtful sentences usually beats six padded ones.

        # YOUR TASK — R1 → R2 → R3 → R4 IN ONE OUTPUT

        R1, R2, R3, R4 are progressive REWRITES, not append-only steps. At
        each step you may add, edit, restructure, or rephrase whatever was
        there before. The goal is a single coherent response, not four
        sentences glued together.

          R1. Empathic answer using `mi_for_user_intent` + the rolling
              summary. Reflects what's underneath without diagnosing.
              No graph awareness yet.

          R2. Integrate the system_intent nudge using
              `mi_for_system_intent`. You may EDIT R1 — replace a phrase,
              restructure a sentence, change the entry point — whatever
              makes R2 read as one coherent reply with both the empathic
              answer and the gentle nudge integrated. R2 must NOT
              contradict R1's empathic answer. If user_intent is venting
              and system_intent wants to plan, hold off on the nudge —
              keep R2 = R1 AND set mi_for_system_intent_used to null.

          R3. SCAN THE CONNECTIONS, THEN DECIDE.
              Before deciding `used_evidence`, do TWO scans, in order:

                (a) Negative-shape scan (silent, shapes interpretation only):
                    read the PROBLEM-PROBLEM CONNECTIONS block AND any
                    attribute_connection rows in RETRIEVED EVIDENCE / per-edge
                    quotes. These tell you HOW the user's problems reinforce
                    each other — which trigger fires which, which barrier on
                    one side becomes a stuck point on another, where the same
                    self-doubt is bleeding across threads. This is private
                    interpretation. Do NOT name the relation to the user
                    ("the work stress is amplifying your sleep issues" is
                    BANNED). Use it to understand the weight they are under.

                (b) Positive-bridge scan: now look at the SAME connection
                    rows AND the POSITIVE SOLUTION evidence (past_attempts
                    that worked, coping_strategies, hobbies, strengths,
                    cues_to_action, motivation, persona anchors, positive
                    edges) and ask: is there a coping move, prior win, hobby,
                    support tie, or strength on ONE side of these connected
                    problems that — given the link you just saw in (a) —
                    could plausibly help the OTHER? Is there a moment the
                    user already got through something similar? Is there a
                    bridge worth surfacing — as an evoke question, a
                    spotlight affirmation, an affirm-then-permission — that
                    would help them take a small step toward the next stage
                    of behavior change for the main problem?

              Then decide `used_evidence: true | false` and choose what to
              say. Rules:
                - Speak the POSITIVE evidence; keep the negative shape
                  silent.
                - If you cite a `problem_problem_connection` or
                  `attribute_connection` in `evidence_used`, your spoken line
                  must be a POSITIVE invitation grounded in that link, NOT
                  a diagnosis of the link. E.g. "the steady walks you
                  mentioned with your sister sound like one of the few times
                  the pressure eases — would you want to lean on that this
                  week?" cites the persona/coping evidence and rides on the
                  silently-known connection. It does NOT say "your work
                  stress and your isolation feed each other."
                - Whatever you surface MUST stay inside the chosen MI moves
                  (`mi_for_user_intent` and, when used, `mi_for_system_intent`)
                  AND honor `user_intent`. If the user is venting, do not
                  pivot to a plan because you spotted a useful bridge —
                  hold the bridge for a later turn or shape it as a soft
                  reflection / affirmation, not a suggestion.
                - You may EDIT R2 wholesale to integrate the evidence — do
                  NOT append a tacked-on "by the way you mentioned X"
                  sentence. The reply still reads as one coherent voice.
                - If neither scan finds anything that lands harder than R2
                  already does, R3 = R2 and `used_evidence: false`. Do not
                  invent evidence to fill the slot.

          R4. FINAL REFINEMENT — re-read R3 against IDENTITY, PURPOSE,
              and `persona.communication_style`. Ask:
                - Does it sound like the friend described in IDENTITY,
                  or has it drifted into therapist / chatbot / parent
                  register?
                - Does it honor PURPOSE (ESC + MI), or did it slip into
                  advising / lecturing / diagnosing?
                - Does the register match this person's communication
                  style (blunt vs introspective, brief vs layered)?
                - Has any sentence inferred more than the evidence
                  supports? (e.g., assuming low self_efficacy from one
                  instance of asking for help)
              If any answer is no, REWRITE R3 — only this fourth pass
              produces `final_response`. If R3 already passes all four
              checks, `final_response` = R3.

        # SAY ONLY WHAT IS NEEDED
        The response is the smallest set of sentences that does the work.
        Three thoughtful sentences usually beats six padded ones. If a
        sentence is not pulling its weight (not acknowledging, not nudging,
        not grounding), cut it. Your job is to land — not to fill space.

        # WHAT NOT TO DO
        - Do NOT REPEAT the user's words. No echoing, no paraphrasing
          with their key phrases swapped in, no mirroring their sentence
          back. If the user said "I'm exhausted", do NOT respond with
          "you're feeling exhausted" — that's a parrot, not a reflection.
          Reflection means saying what's UNDERNEATH the words, in YOUR
          words.
        - Do NOT name HBM labels ("perceived_severity is high",
          "self_efficacy dropped").
        - Do NOT name negative graph relations to the user
          ("the work stress is amplifying your self-doubt"). Hold that
          privately — it shaped your choice of move, that's enough.
        - Do NOT use PROBLEM evidence as a sentence the user reads.
          Problem evidence is for understanding. Solution evidence is
          for speaking.
        - Do NOT moralize, lecture, or command. Suggestions are theirs to
          take or leave.
        - Do NOT pad with empty empathy ("I'm so sorry you're going
          through that").
        - Do NOT tack evidence on at the end ("by the way, you mentioned
          X..."). If you use evidence, integrate it into the reply.

        # HARD RULES (validators will reject and force a retry)

        1. `final_response` MUST NOT START with any of:
           "It sounds like", "It seems like", "It's like",
           "That sounds like", "That sounds really",
           "That must be really", "I can only imagine",
           "That can be a really tough", "That's a really tough place".

        2. `final_response` MUST NOT CONTAIN anywhere:
           "tough to navigate", "tough thing to navigate",
           "hard thing to swallow", "I'm here for you",
           "I'm here to listen".

        3. `mi_for_user_intent_used` and `mi_for_system_intent_used` (when
           non-null) must be from the canonical MISC vocabulary.

        # EVIDENCE_USED SCHEMA

        Each entry in `evidence_used` is:

          {
            "type": "<one of: attribute, attribute_connection,
                       problem_problem_connection, persona, recent_turn>",
            "details": { ... type-specific ... },
            "user_utterance": "<actual quote when applicable, else null>"
          }

        ## CRITICAL: `type` is ONE OF FIVE FIXED STRINGS — not an attribute name

        `type` MUST be exactly one of these five strings (the validator
        rejects anything else and forces a retry):
          - "attribute"
          - "attribute_connection"
          - "problem_problem_connection"
          - "persona"
          - "recent_turn"

        Attribute names like `coping_strategies`, `past_attempts`,
        `triggers`, `barriers`, `self_efficacy`, `perceived_severity`,
        `cues_to_action`, etc. are NOT values of `type`. They live INSIDE
        `details`, in the `attribute` field (for type=attribute) or in
        `attribute_a`/`attribute_b` (for type=attribute_connection).

        WRONG (validator will reject):
          {"type": "coping_strategies",
           "details": {"problem": "work_stress",
                       "summary_excerpt": "weekly walks help"}}

        RIGHT (same content, correct slot):
          {"type": "attribute",
           "details": {"problem": "work_stress",
                       "attribute": "coping_strategies",
                       "summary_excerpt": "weekly walks help"},
           "user_utterance": null}

        ## Per-type `details` shape

          - type="attribute":
              details = {problem, attribute, summary_excerpt}
          - type="attribute_connection":
              details = {problem_a, attribute_a,
                         problem_b, attribute_b,
                         relation_type, why}
          - type="problem_problem_connection":
              details = {problem_a, problem_b, weight, summary_excerpt}
          - type="persona":
              details = {field, content_excerpt}
              (where `field` is one of the persona keys: communication_style,
               support_system, hobbies_interests, etc.)
          - type="recent_turn":
              details = {turn_id, role}

        If `used_evidence` is false, `evidence_used` MUST be [].

        # OUTPUT (JSON, nothing else)

        {
          "reasoning": "<single string, ≤200 words. Walk the four moves:
                        1) what they said + what's underneath,
                        2) what we already know that connects,
                        3) what will help (MI in mind),
                        4) how to say it.
                        Then briefly justify R1, R2, R3, R4.>",
          "mi_for_user_intent_used": "<MISC code>",
          "mi_for_system_intent_used": "<MISC code or null if R2 = R1>",
          "r1": "<draft 1>",
          "r2": "<draft 2>",
          "r3": "<draft 3>",
          "final_response": "<R4>",
          "used_evidence": true | false,
          "evidence_used": [...]
        }
    """)


# ---------------------------------------------------------------------------
# USER prompt
# ---------------------------------------------------------------------------


def _format_main_block(main: Optional[dict]) -> str:
    if not main:
        return "(no main problem yet — cold start)"
    lines = [
        f"name: {main['name']}",
        f"ttm_stage: {main['ttm_stage']}",
        f"system_intent: {main.get('system_intent') or '(none)'}",
        f"mi_for_system_intent: {main.get('mi_for_system_intent') or '(none)'}",
    ]
    la = main.get("level_attributes") or {}
    if la:
        lines.append("level_attributes:")
        for attr, rec in la.items():
            quotes = rec.get("quotes") or []
            quotes_block = _format_attribute_quotes(quotes)
            lines.append(
                f"  - {attr}: level={rec.get('current_level')}  "
                f"(why: {rec.get('level_reasoning') or '(none)'})\n"
                f"    summary: {rec.get('summary_text') or '(none)'}\n"
                f"    quotes:\n"
                f"{quotes_block}"
            )
    nla = main.get("non_level_attributes") or {}
    if nla:
        lines.append("non_level_attributes:")
        for attr, rec in nla.items():
            quotes = rec.get("quotes") or []
            quotes_block = _format_attribute_quotes(quotes)
            lines.append(
                f"  - {attr}:\n"
                f"    summary: {rec.get('summary_text') or '(none)'}\n"
                f"    quotes:\n"
                f"{quotes_block}"
            )
    return "\n".join(lines)


def _format_attribute_quotes(quotes: list[dict]) -> str:
    """Render attribute quote records as a bulleted list. One line per
    deduplicated (session, turn): anchor + verbatim user quote + the
    inferred information that the quote grounded.
    """
    if not quotes:
        return "      (none)"
    lines = []
    for r in quotes:
        quote = (r.get("quote") or "").strip()
        inferred = (r.get("inferred") or "").strip()
        if quote and inferred:
            lines.append(f'      - {r["anchor"]}: "{quote}"  ·  {inferred}')
        elif quote:
            lines.append(f'      - {r["anchor"]}: "{quote}"')
        elif inferred:
            lines.append(f'      - {r["anchor"]}: {inferred}')
        else:
            lines.append(f'      - {r["anchor"]}: (no content)')
    return "\n".join(lines)


def _format_connection_quotes(quotes: list[dict]) -> str:
    """Render edge connection quote records as a bulleted list. One line
    per deduplicated (session, turn): anchor + typed attribute pair +
    relation_type + verbatim user quote. ``why`` is intentionally
    omitted — the edge summary_text already covers the reasoning.
    """
    if not quotes:
        return "      (none)"
    lines = []
    for r in quotes:
        head = (
            f'      - {r["anchor"]}: '
            f'{r["attribute_a"]} ↔ {r["attribute_b"]} ({r["relation_type"]})'
        )
        quote = (r.get("quote") or "").strip()
        if quote:
            head += f'  "{quote}"'
        lines.append(head)
    return "\n".join(lines)


def _format_others_block(others: list[dict]) -> str:
    if not others:
        return "(none)"
    return "\n".join(
        f"  - {o['name']} (ttm: {o['ttm_stage']}; nudge: {o.get('system_intent_1line') or '(none)'})"
        for o in others
    )


def _format_connections_block(conns: list[dict]) -> str:
    if not conns:
        return "(none)"
    out: list[str] = []
    for c in conns:
        n_entries = c.get("n_entries", 0)
        header = (
            f"  {c['a']} ↔ {c['b']}  (weight={c['weight']}, "
            f"{n_entries} entr{'y' if n_entries == 1 else 'ies'} summarized)"
        )
        summary = c.get("summary_text") or "(none)"
        quotes = c.get("quotes") or []
        quotes_block = _format_connection_quotes(quotes)
        out.append(
            f"{header}\n"
            f"    summary: {summary}\n"
            f"    quotes:\n"
            f"{quotes_block}"
        )
    return "\n".join(out)


def _format_persona_block(persona: dict) -> str:
    rows: list[str] = []
    for k, v in persona.items():
        if not v:
            continue
        if isinstance(v, list):
            v_str = ", ".join(str(x) for x in v)
        else:
            v_str = str(v)
        rows.append(f"  {k}: {v_str}")
    return "\n".join(rows) if rows else "(empty)"


def _format_past_two_turns(past_two: list[dict]) -> str:
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


def build_user_prompt(
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
    conns = evidence_pack.get("problem_problem_connections", []) or []
    persona = evidence_pack.get("persona", {}) or {}
    rolling = evidence_pack.get("rolling_summary_5turns", "") or ""

    return textwrap.dedent(f"""\
        # USER_INTENT (from Agent 1)
        intent: {user_intent}
        phrase: {user_intent_phrase}
        mi_for_user_intent: {mi_for_user_intent}

        # MAIN PROBLEM
        {_format_main_block(main)}

        # OTHER CURRENT PROBLEMS (context only)
        {_format_others_block(others)}

        # PROBLEM-PROBLEM CONNECTIONS
        {_format_connections_block(conns)}

        # PERSONA
        {_format_persona_block(persona)}

        # ROLLING SUMMARY (last few turns)
        {rolling.strip() or "(none — early in conversation)"}

        # PAST TWO TURNS (diversity hint — vary your move)
        {_format_past_two_turns(past_two_turns)}

        # CURRENT USER MESSAGE
        {current_user_message.strip()}

        Now produce the JSON response per the schema.
    """)


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def _check_banned_patterns(text: str) -> Optional[str]:
    stripped = text.lstrip()
    opener = stripped[:40].lower()
    for bad in _BANNED_OPENERS:
        if opener.startswith(bad):
            return (
                f"final_response opens with banned pattern {bad!r}; "
                "vary your opening (name a specific thing, drop the template)"
            )
    low = text.lower()
    for bad in _BANNED_PHRASES:
        if bad in low:
            return f"final_response contains banned phrase {bad!r}; reword in your own voice"
    return None


def _normalize_reasoning(out: dict) -> None:
    r = out.get("reasoning")
    if isinstance(r, list):
        out["reasoning"] = " ".join(
            s.strip() for s in r if isinstance(s, str) and s.strip()
        )


def _validate_factory(
    *,
    expected_mi_user: str,
    expected_mi_system: Optional[str],
):
    """Build the per-call validator. Cross-field checks:
      - mi_for_user_intent_used must equal expected_mi_user
        (Agent 1 already picked it; Agent 5 must use it).
      - mi_for_system_intent_used must equal expected_mi_system OR be null
        (the latter is allowed when R2 = R1 because of intent conflict).
      - used_evidence consistency.
      - banned-opener / banned-phrase on final_response.
    """
    def _check(out: dict) -> None:
        _normalize_reasoning(out)

        if out["mi_for_user_intent_used"] != expected_mi_user:
            raise ValueError(
                f"mi_for_user_intent_used must equal {expected_mi_user!r} "
                f"(Agent 1's choice), got {out['mi_for_user_intent_used']!r}"
            )

        used_sys = out["mi_for_system_intent_used"]
        if expected_mi_system is None:
            # Agent 3b didn't fire / no system_intent — must be null.
            if used_sys is not None:
                raise ValueError(
                    "mi_for_system_intent_used must be null when there is no "
                    f"system MI (got {used_sys!r})"
                )
        else:
            if used_sys not in (None, expected_mi_system):
                raise ValueError(
                    f"mi_for_system_intent_used must equal "
                    f"{expected_mi_system!r} or null (got {used_sys!r})"
                )

        # used_evidence consistency
        ev = out["evidence_used"]
        used = out["used_evidence"]
        if used and not ev:
            raise ValueError("used_evidence=true but evidence_used is empty")
        if (not used) and ev:
            raise ValueError("used_evidence=false but evidence_used is non-empty")

        # Banned openers/phrases on final_response
        fr = out["final_response"]
        banned = _check_banned_patterns(fr)
        if banned:
            raise ValueError(banned)

    return _check


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@dataclass
class ResponseV7Inputs:
    user_intent: str
    user_intent_phrase: str
    mi_for_user_intent: str
    evidence_pack: dict
    past_two_turns: list[dict]
    current_user_message: str


def _safe_fallback(*, mi_for_user_intent: str) -> dict:
    return {
        "reasoning": "fallback: response call exhausted retries; emitting generic acknowledgment.",
        "mi_for_user_intent_used": mi_for_user_intent,
        "mi_for_system_intent_used": None,
        "r1": "Hearing you. Take a breath; we can pick up wherever you want.",
        "r2": "Hearing you. Take a breath; we can pick up wherever you want.",
        "r3": "Hearing you. Take a breath; we can pick up wherever you want.",
        "final_response": "Hearing you. Take a breath; we can pick up wherever you want.",
        "used_evidence": False,
        "evidence_used": [],
        "_fallback_default": True,
    }


def run_response_v7(
    *,
    client: LLMClient,
    ctx: CallContext,
    inputs: ResponseV7Inputs,
) -> dict:
    """Execute Agent 5. On total failure returns a safe fallback."""
    assert ctx.call_role == "agent5_response_v7"
    main = inputs.evidence_pack.get("main_problem") or {}
    expected_mi_system = main.get("mi_for_system_intent") or None

    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(**inputs.__dict__),
            schema=RESPONSE_V7_SCHEMA,
            validator_extras=_validate_factory(
                expected_mi_user=inputs.mi_for_user_intent,
                expected_mi_system=expected_mi_system,
            ),
        )
    except LLMStructuredError:
        return _safe_fallback(mi_for_user_intent=inputs.mi_for_user_intent)


# ---------------------------------------------------------------------------
# Self-test (validators only — no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    base = {
        "reasoning": "Move 1: surface vent. Move 2: graph thin. Move 3: reflect. Move 4: brief.",
        "mi_for_user_intent_used": "complex_reflection",
        "mi_for_system_intent_used": "evoke",
        "r1": "X",
        "r2": "X. What helped before?",
        "r3": "X. What helped before?",
        "final_response": "Under the surface, that's a heavier weight. What helped before?",
        "used_evidence": False,
        "evidence_used": [],
    }
    chk = _validate_factory(
        expected_mi_user="complex_reflection",
        expected_mi_system="evoke",
    )
    chk(dict(base))

    # Wrong mi_for_user_intent_used → reject.
    bad1 = dict(base, mi_for_user_intent_used="evoke")
    try:
        chk(bad1)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "mi_for_user_intent_used" in str(e)

    # Wrong mi_for_system_intent_used → reject.
    bad2 = dict(base, mi_for_system_intent_used="advise_with_permission")
    try:
        chk(bad2)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "mi_for_system_intent_used" in str(e)

    # null mi_for_system_intent_used → ok (intent conflict path)
    null_sys = dict(base, mi_for_system_intent_used=None,
                    r2=base["r1"], r3=base["r1"], final_response=base["r1"])
    chk(null_sys)

    # No-system-MI path (expected_mi_system=None) — used_sys must be null.
    chk_no_sys = _validate_factory(
        expected_mi_user="complex_reflection",
        expected_mi_system=None,
    )
    must_null = dict(base, mi_for_system_intent_used=None)
    chk_no_sys(must_null)
    must_null_violation = dict(base, mi_for_system_intent_used="evoke")
    try:
        chk_no_sys(must_null_violation)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # used_evidence inconsistency
    bad_e1 = dict(base, used_evidence=True, evidence_used=[])
    try:
        chk(bad_e1)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "evidence_used is empty" in str(e)
    bad_e2 = dict(base, used_evidence=False, evidence_used=[
        {"type": "attribute", "details": {}, "user_utterance": None},
    ])
    try:
        chk(bad_e2)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "non-empty" in str(e)

    # Banned opener
    bad_open = dict(base, final_response="It sounds like that was rough.")
    try:
        chk(bad_open)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "banned" in str(e).lower()

    # Banned phrase
    bad_phrase = dict(base,
                      final_response="That's tough to navigate honestly.")
    try:
        chk(bad_phrase)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "banned" in str(e).lower()

    # array-shaped reasoning normalizes
    arr = dict(base, reasoning=["a sentence.", "b sentence."])
    chk(arr)
    assert isinstance(arr["reasoning"], str)
    assert "a sentence" in arr["reasoning"]

    # Prompts render
    sp = build_system_prompt()
    assert "HELP-E" in sp
    assert "FOUR INTERNAL MOVES" in sp
    assert "R1" in sp and "R4" in sp

    pack = {
        "main_problem": {
            "name": "academic_pressure", "ttm_stage": "contemplation",
            "system_intent": "Reflect both sides", "mi_for_system_intent": "evoke",
            "level_attributes": {
                "perceived_severity": {
                    "current_level": "high",
                    "level_reasoning": "user said overwhelming",
                    "summary_text": "t1: user said pressure overwhelming",
                    "quotes": [
                        {"anchor": "s1.t1",
                         "quote": "I can't keep this up",
                         "inferred": "severity high — fatigue named"},
                    ],
                },
            },
            "non_level_attributes": {
                "triggers": {
                    "summary_text": "t1: late-night cramming",
                    "quotes": [
                        {"anchor": "s1.t1",
                         "quote": "late-night cramming for finals",
                         "inferred": "deadline-driven late nights"},
                    ],
                },
            },
        },
        "other_current_problems": [
            {"name": "sleep_problems", "ttm_stage": "precontemplation",
             "system_intent_1line": "Build awareness"},
        ],
        "problem_problem_connections": [
            {
                "a": "academic_pressure", "b": "sleep_problems", "weight": 0.7,
                "summary_text": (
                    "s1.t1: shared driver — late-night replay of academic "
                    "pressure spills into sleep onset."
                ),
                "n_entries": 1,
                "quotes": [
                    {"anchor": "s1.t1",
                     "attribute_a": "triggers", "attribute_b": "triggers",
                     "relation_type": "shared_trigger",
                     "quote": "I lie there replaying"},
                ],
            },
        ],
        "persona": {"communication_style": "calm, methodical"},
        "rolling_summary_5turns": "user vented about deadline pressure",
    }
    up = build_user_prompt(
        user_intent="express_emotion",
        user_intent_phrase="User wants to feel heard about deadline pressure.",
        mi_for_user_intent="complex_reflection",
        evidence_pack=pack,
        past_two_turns=[],
        current_user_message="I'm so tired of this.",
    )
    assert "academic_pressure" in up
    assert "sleep_problems" in up
    assert "calm, methodical" in up
    assert "deadline pressure" in up
    assert "complex_reflection" in up

    # Fallback is schema-shaped; key validator passes for it
    fb = _safe_fallback(mi_for_user_intent="support")
    fb_chk = _validate_factory(
        expected_mi_user="support", expected_mi_system=None,
    )
    fb_chk({k: v for k, v in fb.items() if not k.startswith("_")})

    print("instruction_response_v7 self-test PASSED")


if __name__ == "__main__":
    _self_test()
