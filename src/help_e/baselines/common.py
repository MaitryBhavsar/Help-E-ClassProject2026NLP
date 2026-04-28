"""Shared pieces for v1/v2/v3 ablation baselines.

Each baseline provides a ``turn_fn`` compatible with
``session_driver.run_profile``. The ``turn_fn`` returns a dict with keys
``{trace, bundle, candidates, merged}`` — same contract as the v4/v5
default, so downstream session-end orchestration, Mind-2/3, and the E1
judge don't need to know which baseline produced the turn.

Baselines don't touch the attribute graph (§7): v1 has no graph at all,
v2 maintains per-problem running text summaries, v3 adds an LLM-inferred
TTM stage per problem. To keep the dataflow uniform we still materialize
a ``TurnTrace`` object so session_summary has a coherent
``active_problems_over_session`` list.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from typing import Any, Optional

from ..graph_update import TurnTrace
from ..llm_client import CallContext, LLMClient
from ..prompts.common import MI_STYLE_RULES, PROJECT_IDENTITY, format_dialog_turns


# ---------------------------------------------------------------------------
# Response-shape validation (shared by every baseline response call)
# ---------------------------------------------------------------------------


RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["response"],
    "properties": {
        "response": {"type": "string", "minLength": 1},
    },
}


_SENT_SPLIT = re.compile(r'(?<=[.!?])\s+')


def _count_sentences(s: str) -> int:
    return len([x for x in _SENT_SPLIT.split(s.strip()) if x.strip()])


def validate_response_shape(out: dict) -> None:
    r = out["response"]
    if _count_sentences(r) > 5:
        raise ValueError("response exceeds 5 sentences")
    if r.count("?") > 1:
        raise ValueError("response has more than 1 question")


def base_system_prompt(extra: str = "") -> str:
    return textwrap.dedent(f"""\
        {PROJECT_IDENTITY}

        You are writing the ASSISTANT's reply to the user.

        {MI_STYLE_RULES}
        {extra}

        Return ONLY valid JSON:  {{ "response": "<your reply>" }}
    """)


# ---------------------------------------------------------------------------
# Minimal trace builder
# ---------------------------------------------------------------------------


def make_minimal_trace(
    *,
    session_id: int,
    turn_id: int,
    user_message: str,
    main_problem: Optional[str],
    active_problems: list[str],
) -> TurnTrace:
    return TurnTrace(
        session_id=session_id,
        turn_id=turn_id,
        user_message=user_message,
        extraction={
            "active_problems": active_problems,
            "main_problem": main_problem,
            "user_intent": "other",
            "observed_attributes": [],
            "_baseline_placeholder": True,
        },
        main_problem=main_problem,
        active_problems=active_problems,
        empty_turn=main_problem is None,
        carried_forward_main=False,
    )


# ---------------------------------------------------------------------------
# Per-problem running summary state (v2 + v3)
# ---------------------------------------------------------------------------


@dataclass
class ProblemSummaryState:
    """Per-problem running summary used by v2 and v3.

    Kept in memory per (profile, session) — written to disk alongside the
    transcript when the session ends so later tools can inspect it. v3
    adds a TTM stage field updated each turn.
    """

    summaries: dict[str, str] = field(default_factory=dict)
    ttm_stages: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"summaries": self.summaries, "ttm_stages": self.ttm_stages}


# ---------------------------------------------------------------------------
# Simple intent heuristic (v1 MI rule)
# ---------------------------------------------------------------------------


_VENT_WORDS = ("hate", "tired", "angry", "alone", "stressed", "overwhelmed", "sad")


def guess_intent(text: str) -> str:
    lower = text.lower()
    if "?" in text and any(w in lower for w in ("should i", "what do i", "how do i")):
        return "seek_advice"
    if any(w in lower for w in _VENT_WORDS):
        return "vent"
    if any(w in lower for w in ("i tried", "i did", "i started", "i've been")):
        return "report_progress"
    return "other"


def reflect_affirm_hint(intent: str) -> str:
    """Tiny MI rule for v1/v2 when no richer state exists. Picked based on
    the last user message alone.
    """
    if intent == "vent":
        return "Start with a reflection of the feeling. Do not advise."
    if intent == "report_progress":
        return "Affirm the concrete effort. Ask one open question if natural."
    if intent == "seek_advice":
        return "Reflect first; ask permission before offering options."
    return "Reflect briefly; ask one open question at most."
