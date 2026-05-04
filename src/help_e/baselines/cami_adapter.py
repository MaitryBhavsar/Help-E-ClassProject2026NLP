"""CAMI external baseline adapter for the v6-aligned HELP-E driver.

CAMI is used only as the counselor policy. HELP-E still owns the
profile/session loop, simulator, transcript persistence, and evaluation.
"""
from __future__ import annotations

import os
import re
import sys
import importlib.util
from pathlib import Path
from typing import Any, Optional

from .. import config
from ..graph_v6 import ProblemGraphV6
from ..profile_spec import load_profile


DEFAULT_CAMI_MODEL = "meta-llama/Llama-3.3-70B-Instruct"
_CAMI_SESSION_CACHE: dict[tuple[str, int], "CamiSession"] = {}


def _problem_to_text(problem_name: str) -> str:
    return (problem_name or "general_anxiety").replace("_", " ")


def _parse_cami_output(raw: str) -> tuple[dict, str]:
    """Split CAMI's bracketed trace from the user-facing counselor reply."""
    text = str(raw or "").strip()
    cami_trace: dict[str, Any] = {}

    m = re.match(r"^\s*\[(?P<trace>.*?)\]\s*(?P<reply>.*)$", text, re.DOTALL)
    if m:
        trace_text = m.group("trace").strip()
        text = m.group("reply").strip()
        cami_trace["raw"] = trace_text
        for part in re.split(r"\s*\|\|\s*", trace_text):
            if ":" not in part:
                continue
            key, value = part.split(":", 1)
            norm_key = re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")
            if norm_key:
                cami_trace[norm_key] = value.strip()

    text = re.sub(r"^\s*-?\s*Counselor\s*:\s*", "", text, flags=re.IGNORECASE)
    return cami_trace, text.strip()


def _profile_get(profile: Any, field: str, default: Any = None) -> Any:
    if isinstance(profile, dict):
        return profile.get(field, default)
    return getattr(profile, field, default)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_cami_root() -> Path:
    raw = os.environ.get("HELPE_CAMI_ROOT", "external/CAMI")
    root = Path(raw)
    if root.is_absolute():
        return root
    cwd_root = (Path.cwd() / root).resolve()
    if cwd_root.exists():
        return cwd_root
    return (_repo_root() / root).resolve()


def _import_cami_class():
    cami_root = _resolve_cami_root()
    cami_root_s = str(cami_root)
    if cami_root_s not in sys.path:
        sys.path.insert(0, cami_root_s)
    try:
        from agents import CAMI  # type: ignore
    except ImportError as first_error:
        try:
            from agents.counselor import CAMI  # type: ignore
        except ImportError:
            counselor_path = cami_root / "agents" / "counselor.py"
            spec = importlib.util.spec_from_file_location(
                "_helpe_external_cami_counselor", counselor_path,
            )
            if spec is None or spec.loader is None:
                raise first_error
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            CAMI = module.CAMI
    return CAMI


class CamiSession:
    def __init__(self, profile: Any, model: str) -> None:
        primary = _profile_get(profile, "primary_problem", None) or "general_anxiety"
        self.primary_problem = str(primary)
        self.behavior = _problem_to_text(self.primary_problem)
        self.goal = f"make progress on {self.behavior}"
        self.model = model

        CAMI = _import_cami_class()
        self.counselor = CAMI(goal=self.goal, behavior=self.behavior, model=model)

    def step(self, user_message: str) -> dict:
        self.counselor.receive(f"Client: {user_message}")
        raw = self.counselor.reply()
        cami_trace, final_response = _parse_cami_output(raw)
        reasoning = ""
        if cami_trace:
            state = cami_trace.get("inferred_state")
            strategies = cami_trace.get("strategies") or cami_trace.get("final_strategy")
            topic = cami_trace.get("topic")
            bits = []
            if state:
                bits.append(f"CAMI inferred state: {state}")
            if strategies:
                bits.append(f"strategies: {strategies}")
            if topic:
                bits.append(f"topic: {topic}")
            reasoning = "; ".join(bits)

        return {
            "response": {
                "reasoning": reasoning,
                "evidence_used": [],
                "final_response": final_response,
            },
            "trace": {
                "system": "cami",
                "main_problem": self.primary_problem,
                "current_problems": [self.primary_problem],
                "user_intent": "small_talk",
                "ttm_stage": None,
                "transition_target": None,
                "all_candidate_codes": [],
                "chosen_misc_codes": [],
                "turn_scope_level_attrs": [],
                "level_updates": [],
                "ttm_updates": [],
                "cooc_added": 0,
                "attr_conn_added": 0,
                "behavior_given_to_cami": self.behavior,
                "goal_given_to_cami": self.goal,
                "cami_trace": cami_trace,
            },
        }


def _get_cami_session(*, profile_id: str, session_id: int, turn_id: int) -> CamiSession:
    key = (profile_id, session_id)
    if turn_id == 1:
        _CAMI_SESSION_CACHE.pop(key, None)
    session = _CAMI_SESSION_CACHE.get(key)
    if session is None:
        try:
            profile = load_profile(profile_id)
        except Exception:
            profile = {"profile_id": profile_id, "primary_problem": "general_anxiety"}
        model = os.environ.get("HELPE_MAIN_MODEL", config.MAIN_MODEL_NAME) or DEFAULT_CAMI_MODEL
        session = CamiSession(profile=profile, model=model)
        _CAMI_SESSION_CACHE[key] = session
    return session


def cami_turn_fn(
    *,
    client: Any,
    profile_id: str,
    system: str = "cami",
    session_id: int,
    turn_id: int,
    user_message: str,
    recent_turns: list[dict],
    last_system_message: Optional[str] = None,
    prior_session_summary: Optional[str] = None,
    graph: ProblemGraphV6,
    last_n_turns: int = 5,
    previous_turn_traces: Optional[list[dict]] = None,
) -> dict:
    session = _get_cami_session(
        profile_id=profile_id, session_id=session_id, turn_id=turn_id,
    )
    step_out = session.step(user_message)
    trace = step_out["trace"]
    response = step_out["response"]

    return {
        "session_id": session_id,
        "turn_id": turn_id,
        "user_message": user_message,
        "inference": {
            "user_intent": {
                "intent": "small_talk",
                "confidence": "low",
                "explanation": "CAMI baseline does not run HELP-E inference.",
                "supporting_utterance_span": None,
            },
            "current_problems": [
                {
                    "problem_name": session.primary_problem,
                    "is_new_problem": False,
                    "matched_existing_problem_name": session.primary_problem,
                    "explanation": "Pinned to HELP-E profile primary_problem.",
                    "supporting_utterance_span": None,
                }
            ],
            "main_problem": {
                "problem_name": session.primary_problem,
                "explanation": "Pinned to HELP-E profile primary_problem.",
                "supporting_utterance_span": None,
            },
            "problem_attribute_entries": [],
            "problem_cooccurrence_connections": [],
            "problem_attribute_connections": [],
            "_cami_no_helpe_inference": True,
        },
        "recompute": {
            "attribute_level_updates": [],
            "ttm_stage_updates": [],
            "_cami_no_recompute": True,
        },
        "bundle": None,
        "candidate_bundle": {
            "main_problem": session.primary_problem,
            "ttm_stage": None,
            "transition_target": None,
            "user_intent": "small_talk",
            "intent_entry_style": "",
            "common_candidates": [],
            "stage_specific_candidates": [],
            "all_candidate_codes": [],
        },
        "past_two_turns": [],
        "response": response,
        "trace": trace,
    }


__all__ = [
    "CamiSession",
    "_parse_cami_output",
    "_problem_to_text",
    "cami_turn_fn",
]
