"""Loaders for v6 (REDESIGN) run artifacts.

v6 writes its run output to several files (all under
`config.TRANSCRIPT_DIR / {profile_id} / v6/`):

  - `session_{NN}.json`              — per-session transcript + full
                                       turn_traces + persona updates +
                                       stage transitions (the MAIN
                                       evaluation file). The redesign
                                       drops `session_summary`.
  - `run_artifacts.json`             — Mind-3 output for this profile
                                       (Mind-2 deleted in v6 redesign).
  - `mind1_reasoning_s{NN}.jsonl`    — sidecar; the chatbot pipeline
                                       never sees it. Do NOT load for
                                       evaluation of the chatbot.
  - `session_context_s{NN}.json`     — sidecar; simulator-side framing.
  - `miti_judge_s{NN}.json`          — per-session MITI 4.2 judge output
                                       (4 globals × 1–5). Only present
                                       once Phase F's MITI judge has run.

plus per-system graph snapshots at
`config.GRAPH_V6_DIR / {system} / {profile_id}_after_s{NN}.json`.

Loaders here return the bits the redesign metrics need. They never read
the hidden reasoning sidecars — those are for debugging.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from .. import config
from ..graph_v6 import ProblemGraphV6


log = logging.getLogger(__name__)


V6_SYSTEM: str = "v6"


def _v6_dir(profile_id: str, system: str = V6_SYSTEM) -> Path:
    return config.TRANSCRIPT_DIR / profile_id / system


# Match `session_NN.json` only, not `session_context_s{NN}.json` or
# `miti_judge_s{NN}.json` sidecars.
_SESSION_GLOB: str = "session_[0-9]*.json"
_MITI_GLOB: str = "miti_judge_s[0-9]*.json"
_ESC_GLOB: str = "esc_judge_s[0-9]*.json"


def list_v6_profiles(system: str = V6_SYSTEM) -> list[str]:
    """Profiles that have at least one session file on disk for `system`."""
    root = config.TRANSCRIPT_DIR
    if not root.exists():
        return []
    out: list[str] = []
    for pdir in sorted(root.iterdir()):
        if not pdir.is_dir() or pdir.name.startswith("_"):
            continue
        sdir = pdir / system
        if not sdir.exists():
            continue
        if any(sdir.glob(_SESSION_GLOB)):
            out.append(pdir.name)
    return out


def load_v6_session_files(profile_id: str, system: str = V6_SYSTEM) -> list[dict]:
    """Return parsed `session_{NN}.json` dicts in session_id order."""
    d = _v6_dir(profile_id, system)
    if not d.exists():
        return []
    files = sorted(d.glob(_SESSION_GLOB))
    sessions: list[dict] = []
    for fp in files:
        try:
            sessions.append(json.loads(fp.read_text()))
        except Exception as e:
            log.warning("failed to read %s: %s", fp, e)
    return sessions


def load_v6_turn_traces(profile_id: str, system: str = V6_SYSTEM) -> list[dict]:
    """Flatten `turn_traces` across all sessions for this profile."""
    traces: list[dict] = []
    for sess in load_v6_session_files(profile_id, system):
        for tt in sess.get("turn_traces") or []:
            traces.append(tt)
    return traces


def load_v6_transcripts_for_minds(profile_id: str, system: str = V6_SYSTEM) -> list[dict]:
    return [
        {"session_id": s["session_id"], "turns": s.get("transcript") or []}
        for s in load_v6_session_files(profile_id, system)
    ]


def load_v6_run_artifacts(profile_id: str, system: str = V6_SYSTEM) -> Optional[dict]:
    fp = _v6_dir(profile_id, system) / "run_artifacts.json"
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text())
    except Exception as e:
        log.warning("failed to read %s: %s", fp, e)
        return None


def load_v6_session_miti(profile_id: str, system: str = V6_SYSTEM) -> list[dict]:
    """Return parsed `miti_judge_s{NN}.json` dicts in session order."""
    d = _v6_dir(profile_id, system)
    if not d.exists():
        return []
    files = sorted(d.glob(_MITI_GLOB))
    out: list[dict] = []
    for fp in files:
        try:
            out.append(json.loads(fp.read_text()))
        except Exception as e:
            log.warning("failed to read %s: %s", fp, e)
    return out


def load_v6_session_esc(profile_id: str, system: str = V6_SYSTEM) -> list[dict]:
    """Return parsed `esc_judge_s{NN}.json` dicts in session order.

    Replaces the all-sessions Mind-3 output (which was constant 3.0 due
    to JSON truncation). Each per-session file has the shape
    `{"dimensions": [{"name", "score", "justification"}, ...]}`.
    """
    d = _v6_dir(profile_id, system)
    if not d.exists():
        return []
    files = sorted(d.glob(_ESC_GLOB))
    out: list[dict] = []
    for fp in files:
        try:
            out.append(json.loads(fp.read_text()))
        except Exception as e:
            log.warning("failed to read %s: %s", fp, e)
    return out


def load_v6_graph(
    profile_id: str, *, session_id: Optional[int] = None,
    system: str = V6_SYSTEM,
) -> Optional[ProblemGraphV6]:
    """Load a graph snapshot after `session_id` (or the latest) for the
    given `system`. Falls back to the legacy flat layout
    `GRAPH_V6_DIR/{profile}_after_sNN.json` so older artifacts still
    load.
    """
    d = config.GRAPH_V6_DIR / system
    if not d.exists():
        # Legacy flat layout (pre-redesign smokes).
        d = config.GRAPH_V6_DIR
        if not d.exists():
            return None
    if session_id is not None:
        fp = d / f"{profile_id}_after_s{session_id:02d}.json"
        return ProblemGraphV6.load(fp) if fp.exists() else None
    snaps = sorted(d.glob(f"{profile_id}_after_s*.json"))
    if not snaps:
        return None
    return ProblemGraphV6.load(snaps[-1])


def iter_v6_assistant_turns(profile_id: str, system: str = V6_SYSTEM):
    """Yield `(session_id, turn_id, user_message, response_v6, bundle,
    recent_turns)` tuples for every assistant turn. Useful for any
    per-turn analysis loop.

    `response_v6` is the new 3-field dict
    `{reasoning, evidence_used, final_response}`.
    """
    for sess in load_v6_session_files(profile_id, system):
        sid = sess["session_id"]
        transcript: list[dict] = sess.get("transcript") or []
        trace_by_turn: dict[int, dict] = {
            tt.get("turn_id"): tt for tt in (sess.get("turn_traces") or [])
        }
        for i, turn in enumerate(transcript):
            if turn.get("role") != "assistant":
                continue
            tid = turn.get("turn_id")
            user_turn = next(
                (t for t in reversed(transcript[:i]) if t.get("role") == "user"),
                None,
            )
            window = transcript[max(0, i - 5):i]
            trace = trace_by_turn.get(tid) or {}
            yield (
                sid,
                tid,
                (user_turn or {}).get("text", ""),
                trace.get("response") or {},
                trace.get("bundle"),
                window,
            )
