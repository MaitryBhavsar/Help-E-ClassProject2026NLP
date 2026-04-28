"""§1.a v6 (REDESIGN) — MITI 4.2 session-level judge.

ONE LLM call per session. Reads the full session transcript (user
messages + assistant `final_response`s) and scores the MITI 4.2
standard 4 globals on the 1–5 scale:

  - Cultivating Change Talk
  - Softening Sustain Talk
  - Partnership
  - Empathy

Per the redesign §1.a, this matches MITI's actual standard practice —
globals are rated for a coding segment, not per turn. There are no
derived behavior metrics, and no boolean flags.

Per-profile aggregation lives in `eval.metrics`:
  - mean of each global across sessions
  - overall mean across globals (per session and across the run)
"""
from __future__ import annotations

import re
import textwrap
from typing import Any, Optional

from ..config import (
    MISC_CODES,
    MISC_INCONSISTENT_CODES,
    MITI_42_GLOBALS,
    MITI_42_GLOBALS_DEF,
)
from ..llm_client import CallContext, LLMClient


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


_GLOBAL_ENUM = list(MITI_42_GLOBALS)


MITI_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["globals"],
    "properties": {
        "globals": {
            "type": "array",
            "minItems": len(_GLOBAL_ENUM),
            "maxItems": len(_GLOBAL_ENUM),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "score", "justification"],
                "properties": {
                    "name": {"type": "string", "enum": _GLOBAL_ENUM},
                    "score": {"type": "integer", "minimum": 1, "maximum": 5},
                    "justification": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# MISC-code helper (used by analysis tools — view_profile, matrix_report)
# ---------------------------------------------------------------------------


_WORD_RE = re.compile(r"\b[a-z_]{3,}\b")


def extract_misc_codes(text: str) -> list[str]:
    """Find MISC code names (selectable + anti-pattern) in free text.

    The v6 response LLM names its picks inline (e.g. "use
    complex_reflection to develop discrepancy"). This helper recovers
    the named codes for downstream histograms / fallback flagging.

    Returns code names in order of first appearance, de-duplicated.
    Anti-pattern code names (`confront`, `direct`, ...) are intentionally
    included so analysis can spot them when they slip through.
    """
    if not text:
        return []
    known = set(MISC_CODES.keys()) | set(MISC_INCONSISTENT_CODES.keys())
    seen: set[str] = set()
    out: list[str] = []
    for m in _WORD_RE.finditer(text.lower()):
        w = m.group(0)
        if w in known and w not in seen:
            seen.add(w)
            out.append(w)
    return out


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _rubric_block() -> str:
    """1–5 rubric for the four MITI 4.2 globals.

    Anchors are derived from the MITI 4.2 manual: 1 = clear violation /
    no evidence of the global, 3 = mixed / inconsistent, 5 = consistent
    high-quality demonstration.
    """
    descriptions: dict[str, str] = {
        "cultivating_change_talk": textwrap.dedent("""\
            How well did the assistant encourage the client's OWN arguments
            for change?
              1 — actively discouraged change talk; argued for the status quo.
              2 — passed up clear opportunities to draw out change talk.
              3 — mixed; sometimes drew it out, sometimes missed it.
              4 — consistently invited / amplified the client's own change talk.
              5 — masterfully evoked, deepened, and reinforced change talk
                  throughout.
        """).strip(),
        "softening_sustain_talk": textwrap.dedent("""\
            How well did the assistant avoid arguing against the client's
            sustain talk / resistance?
              1 — argued, lectured, or moralized against the client's
                  position.
              2 — at times pushed back on resistance instead of rolling
                  with it.
              3 — mixed; mostly avoided pushing back but had slips.
              4 — consistently rolled with resistance; honored autonomy.
              5 — skillfully de-escalated sustain talk and surfaced the
                  underlying ambivalence without confrontation.
        """).strip(),
        "partnership": textwrap.dedent("""\
            How collaborative was the assistant's stance? Did it work
            WITH the user (not above)?
              1 — expert-on-pedestal; took over the conversation.
              2 — mostly directive; treated the user as a passive recipient.
              3 — mixed collaborative and directive moves.
              4 — consistently collaborative; invited the user's expertise.
              5 — actively fostered an equal partnership; the user's
                  insight visibly shaped the conversation.
        """).strip(),
        "empathy": textwrap.dedent("""\
            How accurately did the assistant understand the client's
            perspective and inner world?
              1 — little or no evidence of trying to understand.
              2 — surface acknowledgments only; missed deeper meaning.
              3 — accurate on content but inconsistent on feeling /
                  meaning.
              4 — consistently accurate complex understanding of feeling +
                  meaning.
              5 — deep, nuanced understanding of perspective; named
                  feeling and meaning the client had not yet articulated.
        """).strip(),
    }
    lines = []
    for name in MITI_42_GLOBALS:
        meta = MITI_42_GLOBALS_DEF[name]
        lines.append(f"[{name}] — {meta}\n{descriptions[name]}")
    return "\n\n".join(lines)


def build_system_prompt() -> str:
    return textwrap.dedent(f"""\
        You are an expert MITI 4.2 coder rating an emotional-support
        conversation. You will see ONE complete session as a back-and-forth
        transcript between a user and an assistant. Score the ASSISTANT's
        overall behavior in that session on the 4 MITI 4.2 globals on a
        1–5 scale each.

        These are SESSION-LEVEL ratings, not per-turn. Form an overall
        judgment from the assistant's behavior across the whole session;
        a single great or bad turn should not dominate. Use the full 1–5
        range. Score what the assistant ACTUALLY did across the session,
        not what it could have done.

        # GLOBALS

        {_rubric_block()}

        # OUTPUT FORMAT

        Return a JSON object with one key, `globals`, whose value is an
        array of 4 objects — one per global, in any order — each with:
          * `name`         (one of: {", ".join(_GLOBAL_ENUM)})
          * `score`        (integer 1..5)
          * `justification` (1–2 sentences anchored to specific assistant
                            moves observed in the transcript)

        Hard rules:
          - EXACTLY 4 entries — one per global; no extras, no omissions.
          - `score` is an INTEGER 1..5 — not a string, not a float.
          - Use specific transcript evidence in `justification`.

        Example:
        {{
          "globals": [
            {{"name": "cultivating_change_talk", "score": 4,
              "justification": "Repeatedly asked the user to articulate their own reasons for trying study blocks rather than supplying them."}},
            {{"name": "softening_sustain_talk", "score": 5,
              "justification": "Reflected the 'I can't keep this up' line without arguing the opposite case."}},
            {{"name": "partnership", "score": 4,
              "justification": "Invited the user's expertise on what had worked before and let them set pace."}},
            {{"name": "empathy", "score": 5,
              "justification": "Named the body's tally underneath the deadline pressure — went past surface acknowledgement."}}
          ]
        }}

        Return ONLY valid JSON matching the schema. No prose.
    """)


def _format_transcript(transcript: list[dict]) -> str:
    """Render a session transcript as `[t<turn> role]: text` lines.

    `transcript` is the chatbot-side transcript: alternating user /
    assistant entries with `turn_id` and `text` fields. Anything else is
    ignored.
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

        Score the assistant on the 4 MITI 4.2 globals now.
    """)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def _validate_miti_judge(out: dict) -> None:
    names = [g["name"] for g in out["globals"]]
    if sorted(names) != sorted(_GLOBAL_ENUM):
        raise ValueError(
            f"miti_judge globals {sorted(names)} != expected {sorted(_GLOBAL_ENUM)}"
        )
    seen = set()
    for g in out["globals"]:
        if g["name"] in seen:
            raise ValueError(f"duplicate MITI global: {g['name']}")
        seen.add(g["name"])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _safe_fallback() -> dict:
    """Neutral fallback so per-profile aggregation never breaks. Score=3
    is the rubric's "mixed" midpoint; flagged via `_fallback_default`.
    """
    return {
        "globals": [
            {"name": name, "score": 3,
             "justification": "MITI judge call failed; neutral default."}
            for name in MITI_42_GLOBALS
        ],
        "_fallback_default": True,
    }


def run_miti_judge(
    *,
    client: LLMClient,
    ctx: CallContext,
    transcript: list[dict],
) -> dict:
    """Execute one MITI 4.2 session-level judge call.

    `ctx.call_role` must be `"miti_judge"`. Returns a dict matching
    `MITI_JUDGE_SCHEMA`, with `_fallback_default=True` on any LLM
    failure.
    """
    assert ctx.call_role == "miti_judge"
    try:
        return client.generate_structured(
            ctx=ctx,
            system_prompt=build_system_prompt(),
            user_prompt=build_user_prompt(
                session_id=ctx.session_id,
                transcript=transcript,
            ),
            schema=MITI_JUDGE_SCHEMA,
            validator_extras=_validate_miti_judge,
        )
    except Exception:
        return _safe_fallback()


# ---------------------------------------------------------------------------
# Self-test (validator + helper + prompt assembly; no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    sp = build_system_prompt()
    for name in MITI_42_GLOBALS:
        assert name in sp, f"global {name!r} missing from system prompt"
    assert "1–5 scale" in sp or "1..5" in sp

    transcript = [
        {"role": "user", "turn_id": 1, "text": "Finals are crushing me."},
        {"role": "assistant", "turn_id": 1, "text": "Three nights of cramming would knock anyone flat."},
        {"role": "user", "turn_id": 2, "text": "I tried a study block."},
        {"role": "assistant", "turn_id": 2, "text": "Protecting that block took some doing."},
    ]
    up = build_user_prompt(session_id=1, transcript=transcript)
    assert "[t1 USER]" in up and "[t1 ASSISTANT]" in up

    valid = {
        "globals": [
            {"name": "cultivating_change_talk", "score": 4,
             "justification": "Drew out the user's own reasons for trying a study block."},
            {"name": "softening_sustain_talk", "score": 5,
             "justification": "Reflected 'crushing' without arguing back."},
            {"name": "partnership", "score": 4,
             "justification": "Honored the user's autonomy in pacing."},
            {"name": "empathy", "score": 5,
             "justification": "Named the underlying physiological reality."},
        ],
    }
    _validate_miti_judge(valid)

    # Missing a global → rejected.
    bad = {"globals": valid["globals"][:3]}
    try:
        _validate_miti_judge(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("expected rejection: only 3 globals")

    # Duplicate global → rejected.
    bad2 = {"globals": list(valid["globals"])}
    bad2["globals"].append(valid["globals"][0])  # duplicate first
    try:
        _validate_miti_judge(bad2)
    except ValueError:
        pass
    else:
        raise AssertionError("expected rejection: duplicate global")

    # Fallback is a valid record shape.
    fb = _safe_fallback()
    _validate_miti_judge(fb)
    assert fb["_fallback_default"] is True

    # MISC-code extraction — selectable + anti-pattern + ordering.
    assert extract_misc_codes("") == []
    found = extract_misc_codes(
        "use complex_reflection then evoke; avoid confront and direct"
    )
    assert found == ["complex_reflection", "evoke", "confront", "direct"]
    # De-duplication preserves first-seen order.
    assert extract_misc_codes("support facilitate support") == ["support", "facilitate"]

    # Empty transcript path.
    up_empty = build_user_prompt(session_id=2, transcript=[])
    assert "(empty session)" in up_empty

    print("miti_judge self-test PASSED")


if __name__ == "__main__":
    _self_test()
