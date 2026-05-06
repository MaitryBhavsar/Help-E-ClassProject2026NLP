"""V3 ResponseAgent — R1 → R2 → R3 → R4 in ONE big-model call.

Reuses the V7 system prompt's structure (identity + four internal moves
+ R1→R4 progressive rewrite + banned openers + hard rules + output
schema) but renders evidence in V3's HBM-free shape:

  - main_problem block:
        name + ttm_stage + ttm_reasoning + system_intent +
        mi_for_system_intent + summary_text + quotes
        (NO level_attributes, NO non_level_attributes)

  - other_current_problems block:
        1-line each (name + ttm_stage + system_intent_1line)

  - problem_problem_connections block:
        per-edge ``summary_text`` + ``quotes`` ([anchor, relation_type,
        quote] per kept turn — V3 has no attribute_a/b)

  - persona, rolling_summary_5turns, past_two_turns, current_user_message

The ``why`` from connection entries is intentionally NOT surfaced —
it shaped the per-problem summary updates internally; the response
prompt sees the relation_type + quote for the verbatim moment, plus
the edge's running summary for the relational story.

Reuses V7's ``RESPONSE_V7_SCHEMA`` and validators (``_validate_factory``,
``_safe_fallback``). The output schema for evidence_used is identical;
the connection-type ``details`` block in V3 doesn't carry attribute
keys (consistent with V3's data shape) but the schema is permissive
about extra fields.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass
from typing import Any, Optional

from .instruction_response_v7 import (
    RESPONSE_V7_SCHEMA,
    _safe_fallback,
    _validate_factory,
)
from .llm_client import CallContext, LLMClient, LLMStructuredError


# v3-specific evidence_used.type enum. v3 has no HBM attributes, so the
# v7 entries `attribute` / `attribute_connection` aren't applicable —
# v3 evidence references whole problems via `problem`, plus the same
# problem-problem-connection / persona / recent_turn slots that v7 has.
_V3_EVIDENCE_TYPES: tuple[str, ...] = (
    "problem",
    "problem_problem_connection",
    "persona",
    "recent_turn",
)


def _build_v3_response_schema() -> dict[str, Any]:
    """Clone v7's response schema but swap the evidence_used.type enum
    for v3's 4-value enum. Everything else (reasoning, mi codes, r1-r3,
    final_response, used_evidence) is shared with v7.
    """
    import copy
    schema = copy.deepcopy(RESPONSE_V7_SCHEMA)
    schema["properties"]["evidence_used"]["items"]["properties"]["type"]["enum"] = list(
        _V3_EVIDENCE_TYPES
    )
    return schema


RESPONSE_V3_SCHEMA: dict[str, Any] = _build_v3_response_schema()


# ---------------------------------------------------------------------------
# SYSTEM prompt — V3 variant of V7's prompt with HBM language scrubbed
# ---------------------------------------------------------------------------


def build_system_prompt() -> str:
    return textwrap.dedent("""\
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
        Help the user navigate everyday wellbeing problems — work
        stress, relationships, body image, sleep, grief. Two grounded
        principles:
          - ESC: always begin by acknowledging what the user is in —
            briefly, grounded in specific evidence — before any further
            move.
          - MI: draw out the user's own reasoning rather than impose
            yours; the user owns their choices. Empathy is the
            through-line.

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
            * PROBLEM evidence — the running per-problem summary +
              the verbatim user quotes that grounded each turn for that
              problem + the relational summaries between problems.
              This is what the person is up against. You use it
              SILENTLY to understand them and to decide what they need.
              It does NOT become a sentence the user reads back to them.
            * POSITIVE SOLUTION evidence — past attempts that worked,
              hobbies, strengths, a problem's coping move that lifts
              another, persona anchors (support_system,
              communication_style strengths). THIS is what you draw on
              for the response — to motivate, affirm, evoke, or anchor.
              This is what shows up in `evidence_used` when you use
              evidence.
          Rule of thumb: problem evidence shapes your INTERPRETATION;
          solution evidence shapes what you SAY.

        # MATCH THE USER
        Use `persona.communication_style` and `persona.personality_traits`
        to shape register. A blunt, direct user gets a blunt, direct
        response. An introspective user gets a more layered one. Match
        the person you are talking to.

        # THE TASK OF RESPONDING — FOUR INTERNAL MOVES
        Responding is four distinct moves. Do them all, in order,
        SILENTLY in `reasoning`. They are not what you say — they are
        how you decide what to say.

          1. UNDERSTAND WHAT THE USER SAID.
             What is the surface message? What's UNDERNEATH the surface?
             What are they NOT saying? What state are they in right now
             (venting, weighing, asking, reporting, resisting)?

          2. CONNECT IT WITH WHAT YOU ALREADY KNOW.
             Read the graph. POSITIVE evidence — past attempts that
             worked, coping that helped, hobbies, strengths, persona
             anchors, recurring patterns where they got through.
             NEGATIVE evidence — what they're up against, problems
             feeding each other, the heaviness behind the words. Hold
             both sides. Negative evidence shapes your interpretation
             and silently informs what you choose; positive evidence is
             what you actually draw on for the response.

          3. THINK WHAT WILL HELP — KEEPING MI IN MIND.
             Given moves 1 and 2: what does this person NEED from you
             right now? Acknowledgment? An evoke question that pulls
             them toward something that worked before? A specific
             affirmation of an effort they made? Permission to feel
             what they're feeling? A small piece of information, with
             permission? Choose ONE primary move that matches their
             user_intent AND the main problem's system_intent. Autonomy
             is non-negotiable — the user owns the choice.

          4. WRITE IT IN THE MOST APPROPRIATE MANNER.
             Match `persona.communication_style` for register and
             length. Pick the MISC technique that fits. Then write —
             without repeating their words, without diagnosing the
             relational dynamics back to them. Say only what's needed.
             Three thoughtful sentences usually beats six padded ones.

        # YOUR TASK — R1 → R2 → R3 → R4 IN ONE OUTPUT

        R1, R2, R3, R4 are progressive REWRITES, not append-only steps.
        At each step you may add, edit, restructure, or rephrase
        whatever was there before. The goal is a single coherent
        response, not four sentences glued together.

          R1. Empathic answer using `mi_for_user_intent` + the rolling
              summary. Reflects what's underneath without diagnosing.
              No graph awareness yet.

          R2. Integrate the system_intent nudge using
              `mi_for_system_intent`. You may EDIT R1 — replace a
              phrase, restructure a sentence, change the entry point —
              whatever makes R2 read as one coherent reply with both
              the empathic answer and the gentle nudge integrated. R2
              must NOT contradict R1's empathic answer. If user_intent
              is venting and system_intent wants to plan, hold off on
              the nudge — keep R2 = R1 AND set
              mi_for_system_intent_used to null.

          R3. Decide `used_evidence: true | false`. Evidence is for
              strengthening, not for proving you remember. Use only
              POSITIVE SOLUTION evidence (past attempts that worked,
              strengths, persona anchors, positive cross-problem
              connections). PROBLEM evidence stays private — it
              already shaped moves 2–3; it does NOT become a sentence
              the user reads. If you choose to use evidence, you may
              EDIT R2 wholesale to integrate it — do not append a
              tacked-on "by the way you mentioned X" sentence. If R2
              is already complete and there is no positive evidence
              strong enough to make the message land harder, R3 = R2
              and `used_evidence: false`.

          R4. FINAL REFINEMENT — re-read R3 against IDENTITY, PURPOSE,
              and `persona.communication_style`. Ask:
                - Does it sound like the friend described in IDENTITY,
                  or has it drifted into therapist / chatbot / parent
                  register?
                - Does it honor PURPOSE (ESC + MI), or did it slip
                  into advising / lecturing / diagnosing?
                - Does the register match this person's communication
                  style (blunt vs introspective, brief vs layered)?
                - Has any sentence inferred more than the evidence
                  supports?
              If any answer is no, REWRITE R3 — only this fourth pass
              produces `final_response`. If R3 already passes all four
              checks, `final_response` = R3.

        # SAY ONLY WHAT IS NEEDED
        The response is the smallest set of sentences that does the
        work. Three thoughtful sentences usually beats six padded ones.
        If a sentence is not pulling its weight, cut it.

        # WHAT NOT TO DO
        - Do NOT REPEAT the user's words. No echoing, no paraphrasing
          with their key phrases swapped in, no mirroring their
          sentence back. Reflection means saying what's UNDERNEATH the
          words, in YOUR words.
        - Do NOT name typed-relation labels diagnostically ("the work
          stress is amplifying your sleep difficulty"). Hold that
          privately — it shaped your choice of move, that's enough.
        - Do NOT use PROBLEM evidence as a sentence the user reads.
          Problem evidence is for understanding. Solution evidence is
          for speaking.
        - Do NOT moralize, lecture, or command. Suggestions are theirs
          to take or leave.
        - Do NOT pad with empty empathy ("I'm so sorry you're going
          through that").
        - Do NOT tack evidence on at the end ("by the way, you
          mentioned X..."). If you use evidence, integrate it into the
          reply.

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

        3. `mi_for_user_intent_used` and `mi_for_system_intent_used`
           (when non-null) must be from the canonical MISC vocabulary.

        # EVIDENCE_USED SCHEMA

        Each entry in `evidence_used` is:

          {
            "type": "<one of: problem, problem_problem_connection,
                       persona, recent_turn>",
            "details": { ... type-specific ... },
            "user_utterance": "<actual quote when applicable, else null>"
          }

        ## CRITICAL: `type` is ONE OF FOUR FIXED STRINGS — not a problem name

        `type` MUST be exactly one of these four strings (the validator
        rejects anything else and forces a retry):
          - "problem"
          - "problem_problem_connection"
          - "persona"
          - "recent_turn"

        Problem names like `work_stress`, `sleep_problems`,
        `caregiver_stress`, `grief_of_loved_one`, etc. are NOT values of
        `type`. They live INSIDE `details` — in the `problem` field for
        type=problem, or in `problem_a`/`problem_b` for
        type=problem_problem_connection. Likewise, descriptive phrases
        from a problem's running summary (mentions of coping moves,
        past attempts, triggers, support ties) are content you weave
        into `summary_excerpt` — they are NOT values of `type`.

        WRONG (validator will reject):
          {"type": "work_stress",
           "details": {"summary_excerpt": "user mentioned weekly walks help"}}

          {"type": "coping_strategies",
           "details": {"problem": "work_stress",
                       "summary_excerpt": "weekly walks"}}

        RIGHT (same content, correct slot):
          {"type": "problem",
           "details": {"problem": "work_stress",
                       "summary_excerpt": "weekly walks with sister help; one of the few times the pressure eases"},
           "user_utterance": null}

        ## Per-type `details` shape

          - type="problem":
              details = {problem, summary_excerpt}
              (summary_excerpt is a short phrase drawn from the
              problem's running summary — typically a positive coping
              move, past attempt, strength, or persona anchor that you
              are leaning on for the response)

          - type="problem_problem_connection":
              details = {problem_a, problem_b, weight, summary_excerpt}
              (summary_excerpt = short phrase from the edge's running
              summary; weight is the float you saw in the # PROBLEM-
              PROBLEM CONNECTIONS block)

          - type="persona":
              details = {field, content_excerpt}
              (where `field` is one of the persona keys:
              communication_style, support_system, hobbies_interests,
              core_values, personality_traits, etc.)

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
# USER prompt — V3 evidence shape
# ---------------------------------------------------------------------------


def _format_main_block_v3(main: Optional[dict]) -> str:
    if not main:
        return "(no main problem yet — cold start)"
    lines = [
        f"name: {main['name']}",
        f"ttm_stage: {main['ttm_stage']}",
        f"ttm_reasoning: {main.get('ttm_reasoning') or '(none)'}",
        f"system_intent: {main.get('system_intent') or '(none)'}",
        f"mi_for_system_intent: {main.get('mi_for_system_intent') or '(none)'}",
        f"summary: {main.get('summary_text') or '(none)'}",
    ]
    quotes = main.get("quotes") or []
    if quotes:
        lines.append("quotes:")
        for r in quotes:
            q = (r.get("quote") or "").strip()
            inferred = (r.get("inferred") or "").strip()
            if q and inferred:
                lines.append(f'  - {r["anchor"]}: "{q}"  ·  {inferred}')
            elif q:
                lines.append(f'  - {r["anchor"]}: "{q}"')
            elif inferred:
                lines.append(f'  - {r["anchor"]}: {inferred}')
            else:
                lines.append(f'  - {r["anchor"]}: (no content)')
    else:
        lines.append("quotes: (none)")
    return "\n".join(lines)


def _format_others_block_v3(others: list[dict]) -> str:
    if not others:
        return "(none)"
    return "\n".join(
        f"  - {o['name']} (ttm: {o['ttm_stage']}; "
        f"nudge: {o.get('system_intent_1line') or '(none)'})"
        for o in others
    )


def _format_connections_block_v3(conns: list[dict]) -> str:
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
        block_lines = [header, f"    summary: {summary}"]
        quotes = c.get("quotes") or []
        if quotes:
            block_lines.append("    quotes:")
            for r in quotes:
                rel = r.get("relation_type") or "?"
                q = (r.get("quote") or "").strip()
                if q:
                    block_lines.append(
                        f'      - {r["anchor"]}: ({rel})  "{q}"'
                    )
                else:
                    block_lines.append(
                        f'      - {r["anchor"]}: ({rel})'
                    )
        else:
            block_lines.append("    quotes: (none)")
        out.append("\n".join(block_lines))
    return "\n".join(out)


def _format_persona_block_v3(persona: dict) -> str:
    rows: list[str] = []
    for k, v in persona.items():
        if not v:
            continue
        v_str = ", ".join(str(x) for x in v) if isinstance(v, list) else str(v)
        rows.append(f"  {k}: {v_str}")
    return "\n".join(rows) if rows else "(empty)"


def _format_past_two_turns_v3(past_two: list[dict]) -> str:
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
        # USER_INTENT (from IntentAgent)
        intent: {user_intent}
        phrase: {user_intent_phrase}
        mi_for_user_intent: {mi_for_user_intent}

        # MAIN PROBLEM
        {_format_main_block_v3(main)}

        # OTHER CURRENT PROBLEMS (context only)
        {_format_others_block_v3(others)}

        # PROBLEM-PROBLEM CONNECTIONS
        {_format_connections_block_v3(conns)}

        # PERSONA
        {_format_persona_block_v3(persona)}

        # ROLLING SUMMARY (last few turns)
        {rolling or "(none)"}

        # PAST TWO TURNS (diversity hint — vary your move)
        {_format_past_two_turns_v3(past_two_turns)}

        # CURRENT USER MESSAGE
        {current_user_message}

        Return the JSON object now.
    """)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


@dataclass
class ResponseV3Inputs:
    user_intent: str
    user_intent_phrase: str
    mi_for_user_intent: str
    evidence_pack: dict
    past_two_turns: list[dict]
    current_user_message: str


def run_response_v3(
    *, client: LLMClient, ctx: CallContext, inputs: ResponseV3Inputs,
) -> dict:
    """Execute V3 ResponseAgent. Reuses V7's schema, validator, and
    fallback verbatim — only the system prompt and user prompt change.
    """
    assert ctx.call_role == "agent5_response_v3"
    main = inputs.evidence_pack.get("main_problem") or {}
    expected_mi_system = main.get("mi_for_system_intent") or None

    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(**inputs.__dict__),
            schema=RESPONSE_V3_SCHEMA,
            validator_extras=_validate_factory(
                expected_mi_user=inputs.mi_for_user_intent,
                expected_mi_system=expected_mi_system,
            ),
        )
    except LLMStructuredError:
        return _safe_fallback(mi_for_user_intent=inputs.mi_for_user_intent)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _self_test() -> None:
    sp = build_system_prompt()
    assert "IDENTITY" in sp
    assert "R1" in sp and "R4" in sp
    # No HBM language anywhere.
    assert "perceived_severity" not in sp
    assert "level_attributes" not in sp
    assert "non_level_attributes" not in sp
    assert "attribute_connection" not in sp

    pack = {
        "main_problem": {
            "name": "academic_pressure",
            "ttm_stage": "contemplation",
            "ttm_reasoning": "user weighing trade-offs",
            "system_intent": "evoke both sides; keep choice with user",
            "mi_for_system_intent": "complex_reflection",
            "summary_text": "s1.t1: presentation Friday looming. "
                            "s1.t2: weighing whether to ask for an extension.",
            "quotes": [
                {"anchor": "s1.t1",
                 "quote": "I can't seem to get anything done",
                 "inferred": "stuck on the presentation"},
                {"anchor": "s1.t2",
                 "quote": "maybe I should just ask",
                 "inferred": "weighing an action"},
            ],
        },
        "other_current_problems": [
            {"name": "sleep_problems", "ttm_stage": "precontemplation",
             "system_intent_1line": "build awareness without pushing"},
        ],
        "problem_problem_connections": [
            {"a": "academic_pressure", "b": "sleep_problems", "weight": 0.7,
             "summary_text": "s1.t2: presentation rumination drives "
                             "sleep onset.",
             "n_entries": 1,
             "quotes": [
                 {"anchor": "s1.t2", "relation_type": "causal",
                  "quote": "lie there at night thinking about prep"},
             ]},
        ],
        "persona": {"communication_style": "calm, methodical"},
        "rolling_summary_5turns": "user vented about deadline pressure",
    }

    up = build_user_prompt(
        user_intent="deliberate_decision",
        user_intent_phrase="weighing whether to ask for an extension",
        mi_for_user_intent="complex_reflection",
        evidence_pack=pack,
        past_two_turns=[],
        current_user_message="I'm not sure if I should ask.",
    )
    assert "academic_pressure" in up
    assert "evoke both sides" in up
    assert "I can't seem to get anything done" in up   # quote rendered
    assert "lie there at night thinking about prep" in up  # connection quote
    assert "summary: s1.t1: presentation Friday" in up
    # No HBM-typed text.
    assert "level_attributes" not in up
    assert "perceived_severity" not in up

    # Validator path is reused from V7 — confirm fallback shape passes.
    fb = _safe_fallback(mi_for_user_intent="support")
    chk = _validate_factory(expected_mi_user="support", expected_mi_system=None)
    chk({k: v for k, v in fb.items() if not k.startswith("_")})

    print("instruction_response_v3 self-test PASSED")


if __name__ == "__main__":
    _self_test()
