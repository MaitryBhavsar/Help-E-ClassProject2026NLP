"""§1.c v6 (REDESIGN) — ESConv 6-dim session-level judge.

ONE LLM call per session, mirrors `eval.judge` (MITI). Reads the full
session transcript (user messages + assistant `final_response`s) and
scores the ASSISTANT on the 6 ESConv dimensions on a 1–5 scale each:

  - empathy
  - understanding
  - helpfulness
  - autonomy_respect
  - non_judgment
  - willingness_to_continue

Replaces the old multi-session `simulator/mind3.py` which output 4
sessions × 6 dims at once and consistently truncated mid-JSON, falling
back to a constant 3.0 across the board (the bug that made ESC
non-informative in the v6 matrix). Per-session output is small enough
to fit comfortably under any token cap.

Per-profile aggregation lives in `eval.metrics.esc_per_profile_from_sessions`.
"""
from __future__ import annotations

import re
import textwrap
from typing import Any

from ..config import ESC_DIMENSIONS
from ..llm_client import CallContext, LLMClient


_DIM_ENUM = list(ESC_DIMENSIONS)


ESC_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["dimensions"],
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
    },
}


# ---------------------------------------------------------------------------
# Rubric — inline in the prompt
# ---------------------------------------------------------------------------


_RUBRIC: dict[str, str] = {
    "empathy": (
        "The assistant recognized and validated what the user was feeling. "
        "1=ignored/denied feelings; 3=acknowledged but generic; "
        "5=named the feeling precisely and stayed with it."
    ),
    "understanding": (
        "The assistant tracked the user's specific situation, not a generic "
        "one. 1=boilerplate; 3=partial grasp; 5=clearly tracked details "
        "across turns."
    ),
    "helpfulness": (
        "What the assistant said moved the user forward — a useful angle, "
        "question, or next step. 1=nothing useful; 3=neutral; 5=clearly "
        "shifted something."
    ),
    "autonomy_respect": (
        "The assistant respected the user's right to make their own "
        "choices; didn't push or lecture. 1=lectured/commanded; 3=neutral; "
        "5=explicitly asked permission and honored the user's pace."
    ),
    "non_judgment": (
        "The user could say what they actually thought or felt without "
        "being judged. 1=judgmental tone; 3=neutral; 5=fully safe."
    ),
    "willingness_to_continue": (
        "Would the user keep talking to this assistant? "
        "1=no; 3=maybe; 5=yes."
    ),
}


def _rubric_block() -> str:
    lines = ["6-dimensional ESConv rubric (score 1–5 each):"]
    for dim in ESC_DIMENSIONS:
        lines.append(f"  - {dim}: {_RUBRIC[dim]}")
    return "\n".join(lines)


def build_system_prompt() -> str:
    return textwrap.dedent(f"""\
        You are an expert annotator rating an emotional-support
        conversation against the ESConv 6-dimensional rubric. You will see
        ONE complete session as a back-and-forth transcript between a user
        and an assistant. Score the ASSISTANT's overall behavior in that
        session on the 6 dimensions on a 1–5 scale each.

        These are SESSION-LEVEL ratings, not per-turn. Form an overall
        judgment from the assistant's behavior across the whole session;
        a single great or bad turn should not dominate. Use the FULL 1–5
        range — do NOT default to 3 when the assistant was clearly great
        or clearly poor. Score what the assistant ACTUALLY did across the
        session, not what it could have done.

        # DIMENSIONS

        {_rubric_block()}

        # OUTPUT FORMAT

        Return a JSON object with one key, `dimensions`, whose value is an
        array of EXACTLY 6 objects — one per dimension, in any order — each with:
          * `name`         (one of: {", ".join(_DIM_ENUM)})
          * `score`        (integer 1..5)
          * `justification` (1 sentence anchored to specific assistant
                            moves observed in the transcript)

        Hard rules:
          - EXACTLY 6 entries — one per dimension; no extras, no omissions.
          - `score` is an INTEGER 1..5 — not a string, not a float.
          - Use specific transcript evidence in `justification`.

        Example:
        {{
          "dimensions": [
            {{"name": "empathy", "score": 5,
              "justification": "Named the feeling underneath the words ('the body keeps a tally') instead of generic validation."}},
            {{"name": "understanding", "score": 4,
              "justification": "Recalled the user's earlier mention of three sleepless nights when offering a next step."}},
            {{"name": "helpfulness", "score": 4,
              "justification": "Offered one small concrete experiment for the week without overloading."}},
            {{"name": "autonomy_respect", "score": 5,
              "justification": "Explicitly asked permission before suggesting the breathing technique."}},
            {{"name": "non_judgment", "score": 5,
              "justification": "Held space for the 'I should be fine by now' line without correcting it."}},
            {{"name": "willingness_to_continue", "score": 4,
              "justification": "Closed with an open invitation rather than a wrap-up."}}
          ]
        }}

        Return ONLY valid JSON matching the schema. No prose.
    """)


def _format_transcript(transcript: list[dict]) -> str:
    """Render a session transcript as `[t<turn> role]: text` lines.

    Same formatter as the MITI judge — the input shape is identical (the
    chatbot-side alternating user/assistant transcript).
    """
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


def build_user_prompt(
    *,
    session_id: int,
    transcript: list[dict],
) -> str:
    return textwrap.dedent(f"""\
        SESSION: #{session_id}

        FULL SESSION TRANSCRIPT (chronological — score the ASSISTANT's
        overall behavior across this session):
        {_format_transcript(transcript)}

        Score the assistant on the 6 ESConv dimensions now.
    """)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def _validate_esc_judge(out: dict) -> None:
    names = [d["name"] for d in out["dimensions"]]
    if sorted(names) != sorted(_DIM_ENUM):
        raise ValueError(
            f"esc_judge dimensions {sorted(names)} != expected {sorted(_DIM_ENUM)}"
        )
    seen = set()
    for d in out["dimensions"]:
        if d["name"] in seen:
            raise ValueError(f"duplicate ESC dimension: {d['name']}")
        seen.add(d["name"])


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
             "justification": "ESC judge call failed; neutral default."}
            for name in ESC_DIMENSIONS
        ],
        "_fallback_default": True,
    }


def run_esc_judge(
    *,
    client: LLMClient,
    ctx: CallContext,
    transcript: list[dict],
) -> dict:
    """Execute one ESConv 6-dim session-level judge call.

    `ctx.call_role` must be `"esc_judge"`. Returns a dict matching
    `ESC_JUDGE_SCHEMA`, with `_fallback_default=True` on any LLM
    failure.
    """
    assert ctx.call_role == "esc_judge"
    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(
                session_id=ctx.session_id,
                transcript=transcript,
            ),
            schema=ESC_JUDGE_SCHEMA,
            validator_extras=_validate_esc_judge,
        )
    except Exception:
        return _safe_fallback()


# ---------------------------------------------------------------------------
# Self-test (no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    sp = build_system_prompt()
    assert "ESConv 6-dimensional rubric" in sp
    for dim in ESC_DIMENSIONS:
        assert dim in sp, f"missing {dim} in system prompt"

    up = build_user_prompt(session_id=1, transcript=[
        {"role": "user", "turn_id": 1, "text": "I'm exhausted."},
        {"role": "assistant", "turn_id": 1, "text": "That's a lot to carry."},
    ])
    assert "[t1 USER]" in up and "[t1 ASSISTANT]" in up

    valid = {
        "dimensions": [
            {"name": d, "score": 4, "justification": "x"} for d in ESC_DIMENSIONS
        ],
    }
    _validate_esc_judge(valid)

    # missing dim
    bad = {"dimensions": valid["dimensions"][:-1]}
    try:
        _validate_esc_judge(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("expected validator rejection on missing dim")

    fb = _safe_fallback()
    assert fb["_fallback_default"] is True
    _validate_esc_judge(fb)
    assert all(d["score"] == 3 for d in fb["dimensions"])

    print("esc_judge (v6) self-test PASSED")


if __name__ == "__main__":
    _self_test()
