"""Perceived Value of Session (PVS-6) — user-satisfaction judge.

ONE LLM call per session. Mirrors `eval.esc_judge` (same shape — a 6-dim
1–5 Likert with named justifications) but evaluates SESSION VALUE FROM
THE USER'S POINT OF VIEW rather than rater-side process quality (MITI)
or counselor-quality (ESC).

The six dimensions, anchored to established constructs:

  - responsiveness        — did the assistant reply to what the user
                             actually wanted in this session? (close to
                             WAI-SR Goal/Task agreement)
  - engagement            — was the conversation alive — questions
                             landing, the user leaning in, not
                             flat-lining? (close to SEQ Arousal)
  - insight_and_movement  — did the user end the session with more
                             clarity / a small step / a perspective
                             shift than they had at the start?
                             (close to SEQ Depth + ORS movement)
  - felt_affirmed         — was the assistant a positive presence —
                             specific affirmations, no judgement, the
                             user can feel something good about
                             themselves after? (close to SEQ Positivity
                             + MITI Affirm)
  - continuity            — did the assistant USE what was said in
                             prior sessions (or earlier in this session)
                             to make THIS session land — names, prior
                             coping moves, prior plans, prior fears?
                             (no clinical analog; LLM-chatbot-specific)
  - hope_evocation        — did the assistant draw POSITIVE content out
                             of the user — past wins, strengths, brief
                             moments of relief, future possibilities —
                             instead of only sitting with the negative?
                             (close to MI's "evoking change talk" but
                             scoped to positivity/hope)

Continuity REQUIRES seeing prior-session context. The runner passes a
`prior_sessions_summary` list (one short paragraph or last-exchange
snippet per prior session, OR `None` for the first session) so the
judge can reason about cross-session memory use without the prompt
ballooning.

Output: same shape as ESC judge — `{"dimensions":[...], }` plus a
session takeaway. Per-profile aggregation is left to a thin helper that
mirrors `esc_per_profile_from_sessions`.
"""
from __future__ import annotations

import textwrap
from typing import Any, Optional

from ..llm_client import CallContext, LLMClient


PVS_DIMENSIONS: tuple[str, ...] = (
    "responsiveness",
    "engagement",
    "insight_and_movement",
    "felt_affirmed",
    "continuity",
    "hope_evocation",
)
_DIM_ENUM = list(PVS_DIMENSIONS)


PVS_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["dimensions", "session_takeaway"],
    "properties": {
        "dimensions": {
            "type": "array",
            "minItems": len(_DIM_ENUM),
            "maxItems": len(_DIM_ENUM),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "score", "justification"],
                "properties": {
                    "name": {"type": "string", "enum": _DIM_ENUM},
                    "score": {"type": "integer", "minimum": 1, "maximum": 5},
                    "justification": {"type": "string", "minLength": 1},
                },
            },
        },
        "session_takeaway": {"type": "string", "minLength": 1},
    },
}


# ---------------------------------------------------------------------------
# Rubric — inline in the prompt
# ---------------------------------------------------------------------------


_RUBRIC: dict[str, str] = {
    "responsiveness": (
        "Did the assistant address what the user actually asked for or "
        "needed in THIS session? 1=missed the user's point repeatedly; "
        "3=replied but somewhat off-target; 5=consistently met the user "
        "where they were and answered what they were really asking."
    ),
    "engagement": (
        "Was the conversation alive — the user leaning in, questions "
        "landing, the assistant pulling things forward — not flat or "
        "robotic? 1=user disengaged or short replies all the way through; "
        "3=mixed; 5=the user is opening up and the assistant keeps the "
        "exchange feeling like a real dialogue."
    ),
    "insight_and_movement": (
        "Did the user end this session with something they didn't have at "
        "the start — a small clarity, a reframe, a useful question they "
        "hadn't sat with, a tiny next step? 1=they left exactly where "
        "they came in (or worse); 3=marginal; 5=clear shift in how they "
        "are seeing the situation OR a concrete small step taken/named."
    ),
    "felt_affirmed": (
        "Did the assistant leave the user with something positive about "
        "themselves — specific affirmations of effort or capacity, no "
        "judgement, the user can take the session as a positive "
        "reinforcement rather than a re-soaking in distress? 1=session "
        "left them feeling worse / no positive reinforcement; 3=neutral; "
        "5=specific, grounded affirmations the user could carry forward."
    ),
    "continuity": (
        "Did the assistant USE memory of past content to make THIS session "
        "land harder — referencing prior coping moves, prior plans, prior "
        "fears, names, hobbies, support people, or earlier moments in "
        "THIS session? 1=behaved as if it had no memory; 3=light callback "
        "but generic; 5=specific, well-timed callbacks that made the user "
        "feel known. If this is session 1 and there is no prior content "
        "to leverage, score 5 ONLY when the assistant uses earlier moments "
        "from THIS session well; otherwise score 3 (no fault — nothing to "
        "carry from)."
    ),
    "hope_evocation": (
        "Did the assistant draw POSITIVE content out of the user — past "
        "wins, strengths, brief moments of relief, glimpses of what they "
        "want, possibilities — rather than only sitting with the "
        "negative? 1=stayed entirely in problem-talk; 3=acknowledged "
        "positives only when the user volunteered them; 5=actively "
        "elicited strengths/hopes/future possibilities and the user "
        "responded with at least some change-talk or positive content."
    ),
}


def _rubric_block() -> str:
    lines = ["6-dimensional Perceived-Value-of-Session rubric (score 1–5 each):"]
    for dim in PVS_DIMENSIONS:
        lines.append(f"  - {dim}: {_RUBRIC[dim]}")
    return "\n".join(lines)


def build_system_prompt() -> str:
    return textwrap.dedent(f"""\
        You are an expert annotator rating the VALUE A USER GOT from one
        session of an emotional-support / wellbeing companion chatbot.
        You read the session transcript (and brief context from prior
        sessions if any), and you score what the SESSION delivered FROM
        THE USER'S POINT OF VIEW on six dimensions on a 1–5 Likert scale.

        Anchor your judgments to specific evidence in the transcript. Use
        the FULL 1–5 range — do NOT default to 3 when the session was
        clearly great or clearly poor. Score what THE USER PLAUSIBLY GOT
        from this exchange — not how a counselor would grade the
        assistant's technique (that's MITI / ESC's job, not yours).

        # DIMENSIONS

        {_rubric_block()}

        # OUTPUT FORMAT

        Return a JSON object with two keys:
          * `dimensions` — an array of EXACTLY 6 objects, one per
            dimension, each with:
              - `name`           (one of: {", ".join(_DIM_ENUM)})
              - `score`          (integer 1..5)
              - `justification`  (1 sentence anchored to specific
                                  assistant moves observed in the
                                  transcript)
          * `session_takeaway` — a single sentence summarizing what the
            user likely walked away with from this session.

        Hard rules:
          - EXACTLY 6 entries — one per dimension; no extras, no omissions.
          - `score` is an INTEGER 1..5 — not a string, not a float.
          - Use specific transcript evidence in `justification`.

        Example:
        {{
          "dimensions": [
            {{"name": "responsiveness", "score": 4,
              "justification": "Met the user's vent with reflections rather than redirecting to advice; only one moment slid into a question the user hadn't opened."}},
            {{"name": "engagement", "score": 5,
              "justification": "The user moved from 'I'm just tired' on turn 1 to volunteering memories of their grandmother by turn 6 — clear signs the exchange was pulling them in."}},
            {{"name": "insight_and_movement", "score": 4,
              "justification": "By session end the user named ONE small thing they wanted to try this week (a 10-min walk with sister) — a concrete shift from 'I don't know' at the start."}},
            {{"name": "felt_affirmed", "score": 5,
              "justification": "Spotlighted the user's sustained caregiving as evidence of strength, in plain language the user echoed back."}},
            {{"name": "continuity", "score": 4,
              "justification": "Picked up the 'walks with my sister' thread from session 2 and built on it; missed an opportunity to recall the earlier mention of the funeral aftermath."}},
            {{"name": "hope_evocation", "score": 4,
              "justification": "Asked once 'what would feeling lighter look like' which surfaced a brief positive image from the user."}}
          ],
          "session_takeaway": "User left the session with one named small step (walks with sister) and visibly more energy than they came in with."
        }}

        Return ONLY valid JSON matching the schema. No prose.
    """)


def _format_transcript(transcript: list[dict]) -> str:
    """Render a session transcript as `[t<turn> role]: text` lines."""
    if not transcript:
        return "(empty session)"
    lines: list[str] = []
    for t in transcript:
        role = (t.get("role") or "").upper()
        tid = t.get("turn_id")
        text = (t.get("text") or "").strip()
        if not role or not text:
            continue
        prefix = f"[t{tid} {role}]" if tid is not None else f"[{role}]"
        lines.append(f"{prefix}: {text}")
    return "\n".join(lines) if lines else "(empty session)"


def _format_prior_session_snippets(prior: list[dict]) -> str:
    """Each prior-session entry is a small dict with at least:
      - session_id (int)
      - last_user (str)         — last user message of that session
      - last_assistant (str)    — last assistant message
      - opening_user (str)      — first user message (so the judge sees
                                  the arc from start to end)
    The continuity dimension uses these to assess whether THIS session
    picks up the threads left at the end of those prior sessions.
    """
    if not prior:
        return "(no prior sessions — this is the user's first session.)"
    blocks: list[str] = []
    for snip in prior:
        sid = snip.get("session_id")
        opening = (snip.get("opening_user") or "").strip()
        last_u = (snip.get("last_user") or "").strip()
        last_a = (snip.get("last_assistant") or "").strip()
        block = [f"  -- prior session #{sid} --"]
        if opening:
            block.append(f"     [opening user] {opening}")
        if last_u:
            block.append(f"     [last user]    {last_u}")
        if last_a:
            block.append(f"     [last assist]  {last_a}")
        blocks.append("\n".join(block))
    return "\n".join(blocks)


def build_user_prompt(
    *,
    session_id: int,
    transcript: list[dict],
    prior_sessions: Optional[list[dict]] = None,
) -> str:
    return textwrap.dedent(f"""\
        SESSION: #{session_id}

        PRIOR-SESSION SNIPPETS (for the `continuity` dimension only —
        each prior session's first and last exchange is shown):
        {_format_prior_session_snippets(prior_sessions or [])}

        FULL CURRENT SESSION TRANSCRIPT (chronological — score the
        SESSION'S VALUE TO THE USER across this whole session):
        {_format_transcript(transcript)}

        Score the session on the 6 PVS dimensions and write a one-line
        session_takeaway now.
    """)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def _validate_pvs_judge(out: dict) -> None:
    names = [d["name"] for d in out["dimensions"]]
    if sorted(names) != sorted(_DIM_ENUM):
        raise ValueError(
            f"pvs_judge dimensions {sorted(names)} != expected {sorted(_DIM_ENUM)}"
        )
    seen = set()
    for d in out["dimensions"]:
        if d["name"] in seen:
            raise ValueError(f"duplicate PVS dimension: {d['name']}")
        seen.add(d["name"])
    if not (out.get("session_takeaway") or "").strip():
        raise ValueError("session_takeaway must be non-empty")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _safe_fallback() -> dict:
    """Neutral fallback so per-profile aggregation never breaks. Score=3
    is the rubric's "neutral" midpoint; flagged via `_fallback_default`.
    """
    return {
        "dimensions": [
            {"name": name, "score": 3,
             "justification": "PVS judge call failed; neutral default."}
            for name in PVS_DIMENSIONS
        ],
        "session_takeaway": "PVS judge call failed; neutral fallback emitted.",
        "_fallback_default": True,
    }


def run_pvs_judge(
    *,
    client: LLMClient,
    ctx: CallContext,
    transcript: list[dict],
    prior_sessions: Optional[list[dict]] = None,
) -> dict:
    """Execute one PVS-6 session-level judge call.

    `ctx.call_role` must be `"pvs_judge"`. Returns a dict matching
    `PVS_JUDGE_SCHEMA`, with `_fallback_default=True` on any LLM failure.

    `prior_sessions` is a list of small dicts (see
    `_format_prior_session_snippets`); pass an empty list / None for the
    first session of a profile.
    """
    assert ctx.call_role == "pvs_judge"
    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(
                session_id=ctx.session_id,
                transcript=transcript,
                prior_sessions=prior_sessions,
            ),
            schema=PVS_JUDGE_SCHEMA,
            validator_extras=_validate_pvs_judge,
        )
    except Exception:
        return _safe_fallback()


# ---------------------------------------------------------------------------
# Self-test (no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    sp = build_system_prompt()
    for dim in PVS_DIMENSIONS:
        assert dim in sp, f"missing {dim} in system prompt"
    assert "Perceived-Value-of-Session" in sp

    up_no_prior = build_user_prompt(session_id=1, transcript=[
        {"role": "user", "turn_id": 1, "text": "I'm exhausted."},
        {"role": "assistant", "turn_id": 1, "text": "That weight is real."},
    ])
    assert "[t1 USER]" in up_no_prior and "[t1 ASSISTANT]" in up_no_prior
    assert "no prior sessions" in up_no_prior

    up_with_prior = build_user_prompt(
        session_id=2,
        transcript=[
            {"role": "user", "turn_id": 1, "text": "I tried the walks."},
            {"role": "assistant", "turn_id": 1, "text": "Tell me how that felt."},
        ],
        prior_sessions=[{
            "session_id": 1,
            "opening_user": "I'm exhausted.",
            "last_user": "Maybe a walk could help.",
            "last_assistant": "What might that look like?",
        }],
    )
    assert "prior session #1" in up_with_prior
    assert "I'm exhausted." in up_with_prior
    assert "Tell me how that felt" in up_with_prior

    valid = {
        "dimensions": [
            {"name": d, "score": 4, "justification": "x"} for d in PVS_DIMENSIONS
        ],
        "session_takeaway": "user left lighter than they came in.",
    }
    _validate_pvs_judge(valid)

    # missing dim
    bad = {
        "dimensions": valid["dimensions"][:-1],
        "session_takeaway": "x",
    }
    try:
        _validate_pvs_judge(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("expected validator rejection on missing dim")

    # empty takeaway
    bad2 = dict(valid, session_takeaway="")
    try:
        _validate_pvs_judge(bad2)
    except ValueError:
        pass
    else:
        raise AssertionError("expected validator rejection on empty takeaway")

    fb = _safe_fallback()
    assert fb["_fallback_default"] is True
    _validate_pvs_judge(fb)
    assert all(d["score"] == 3 for d in fb["dimensions"])

    print("pvs_judge (PVS-6) self-test PASSED")


if __name__ == "__main__":
    _self_test()
