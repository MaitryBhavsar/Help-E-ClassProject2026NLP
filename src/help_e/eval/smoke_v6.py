"""End-to-end smoke test for the v6 (REDESIGN) pipeline using a mock LLM.

Runs `run_profile_v6` against a MockClient that serves canned JSON for
every call_role touched by one full v6 redesign session. After the run,
verifies:

  - session_{NN}.json + sidecar files (mind1_reasoning_s, session_context_s,
    miti_judge_s) exist
  - run_artifacts.json is written (Mind-3 reused; Mind-2 deleted)
  - v6 graph snapshot is valid JSON and loads
  - new metrics (MITI per-profile, TTM transition rate, ESC) compute on
    the reloaded artifacts
  - extract_misc_codes pulls the right codes from the new `reasoning`
    field

Outputs are written under a temporary directory. No network, no live
LLM.

Usage:
    PYTHONPATH=src python -m help_e.eval.smoke_v6
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Canned LLM outputs
# ---------------------------------------------------------------------------


def _canned_inference_turn(session_id: int, turn_id: int) -> dict:
    if turn_id == 1:
        return {
            "user_intent": {
                "intent": "express_emotion",
                "supporting_utterance_span": "keep this up",
            },
            "current_problems": [
                {"problem_name": "academic_pressure",
                 "explanation": "x", "supporting_utterance_span": "finals"},
                {"problem_name": "sleep_problems",
                 "explanation": "x", "supporting_utterance_span": "can't sleep"},
            ],
            "main_problem": {
                "problem_name": "academic_pressure",
                "explanation": "x", "supporting_utterance_span": "finals",
            },
            "problem_attribute_entries": [
                {"problem_name": "academic_pressure",
                 "attribute_name": "perceived_severity",
                 "inferred_information": "workload unsustainable",
                 "concise_explanation": "x", "supporting_utterance_span": "y"},
                {"problem_name": "academic_pressure",
                 "attribute_name": "goal",
                 "inferred_information": "get through finals without crashing",
                 "concise_explanation": "stated goal",
                 "supporting_utterance_span": None},
                {"problem_name": "sleep_problems",
                 "attribute_name": "triggers",
                 "inferred_information": "late cramming prevents sleep",
                 "concise_explanation": "x", "supporting_utterance_span": "y"},
            ],
            "problem_cooccurrence_connections": [
                {"problem_1": "academic_pressure", "problem_2": "sleep_problems",
                 "concise_explanation": "same turn",
                 "supporting_utterance_span": "y"},
            ],
            "problem_attribute_connections": [
                {"problem_1": "academic_pressure", "attribute_1": "triggers",
                 "problem_2": "sleep_problems", "attribute_2": "triggers",
                 "relation_type": "shared_trigger",
                 "connection_explanation": "cramming drives both",
                 "supporting_utterance_span": "y", "confidence": "high"},
            ],
        }
    if turn_id == 2:
        return {
            "user_intent": {
                "intent": "seek_validation",
                "supporting_utterance_span": "right?",
            },
            "current_problems": [
                {"problem_name": "academic_pressure",
                 "explanation": "continuing", "supporting_utterance_span": "y"},
                {"problem_name": "sleep_problems",
                 "explanation": "continuing", "supporting_utterance_span": "y"},
            ],
            "main_problem": {
                "problem_name": "academic_pressure",
                "explanation": "still primary", "supporting_utterance_span": "y",
            },
            "problem_attribute_entries": [
                {"problem_name": "academic_pressure",
                 "attribute_name": "self_efficacy",
                 "inferred_information": "user feels unable to finish",
                 "concise_explanation": "x", "supporting_utterance_span": "y"},
            ],
            "problem_cooccurrence_connections": [
                {"problem_1": "academic_pressure", "problem_2": "sleep_problems",
                 "concise_explanation": "both mentioned again",
                 "supporting_utterance_span": None},
            ],
            "problem_attribute_connections": [],
        }
    # Turn 3 — report_action; no new HBM evidence.
    return {
        "user_intent": {
            "intent": "report_action",
            "supporting_utterance_span": "I tried",
        },
        "current_problems": [
            {"problem_name": "academic_pressure",
             "explanation": "still focus", "supporting_utterance_span": "y"},
        ],
        "main_problem": {
            "problem_name": "academic_pressure",
            "explanation": "x", "supporting_utterance_span": "y",
        },
        "problem_attribute_entries": [
            {"problem_name": "academic_pressure",
             "attribute_name": "past_attempts",
             "inferred_information": "tried a 25-minute study block yesterday",
             "concise_explanation": "mentioned attempt",
             "supporting_utterance_span": "y"},
        ],
        "problem_cooccurrence_connections": [],
        "problem_attribute_connections": [],
    }


def _canned_recompute_turn(session_id: int, turn_id: int) -> dict:
    if turn_id == 1:
        return {
            "attribute_level_updates": [
                {"problem_name": "academic_pressure",
                 "attribute_name": "perceived_severity",
                 "new_level": "high",
                 "reasoning": "crushing language"},
            ],
            "ttm_stage_updates": [
                {"problem_name": "academic_pressure",
                 "new_ttm_stage": "contemplation",
                 "reasoning": "x"},
                {"problem_name": "sleep_problems",
                 "new_ttm_stage": "precontemplation",
                 "reasoning": "no movement"},
            ],
        }
    if turn_id == 2:
        return {
            "attribute_level_updates": [
                {"problem_name": "academic_pressure",
                 "attribute_name": "self_efficacy",
                 "new_level": "low",
                 "reasoning": "stated inability"},
            ],
            "ttm_stage_updates": [
                {"problem_name": "academic_pressure",
                 "new_ttm_stage": "contemplation",
                 "reasoning": "still weighing; no preparation yet"},
                {"problem_name": "sleep_problems",
                 "new_ttm_stage": "precontemplation",
                 "reasoning": "x"},
            ],
        }
    return {
        "attribute_level_updates": [],
        "ttm_stage_updates": [
            {"problem_name": "academic_pressure",
             "new_ttm_stage": "preparation",
             "reasoning": "user tried a study block — preparation markers"},
        ],
    }


def _canned_response_turn(turn_id: int) -> dict:
    """3-field schema. Reasoning names MISC codes from the candidate set
    for the current TTM stage.

    Turn 1 (contemplation): COMMON support/facilitate + STAGE evoke,
        complex_reflection, inform_with_permission. Pick complex_reflection
        + support.
    Turn 2 (contemplation, same): pick evoke + facilitate.
    Turn 3 (preparation after recompute): COMMON + STAGE
        advise_with_permission, closed_question, structure. Pick
        advise_with_permission + support.
    """
    if turn_id == 1:
        return {
            "reasoning": (
                "Where: contemplation, express_emotion, needs space. "
                "Which: complex_reflection to develop discrepancy, support to anchor. "
                "Evidence: triggers (all-nighters), goal (finish without crash). "
                "Entry: name the body's signal, then sit with it."
            ),
            "evidence_used": [
                {"source": "hbm_attribute.triggers (main_problem=academic_pressure, s1t1)",
                 "content": "late cramming prevents sleep"},
                {"source": "hbm_attribute.goal (main_problem=academic_pressure, s1t1)",
                 "content": "get through finals without crashing"},
            ],
            "final_response": (
                "Pulling all-nighters has a way of wearing down even the people "
                "who usually hold it together. Take a breath."
            ),
        }
    if turn_id == 2:
        return {
            "reasoning": (
                "Where: still contemplation; user wants validation. "
                "Which: evoke their own framing, facilitate to keep room open. "
                "Evidence: self_efficacy=low this turn; perceived_severity=high. "
                "Entry: normalize the wall without minimizing it."
            ),
            "evidence_used": [
                {"source": "hbm_attribute.self_efficacy (main_problem=academic_pressure, s1t2)",
                 "content": "user feels unable to finish"},
            ],
            "final_response": (
                "Finals at this pace would feel impossible to most people. "
                "Hitting this wall doesn't mean you can't finish."
            ),
        }
    return {
        "reasoning": (
            "Where: preparation now; user reported a small attempt. "
            "Which: advise_with_permission to scaffold next step, support to honor effort. "
            "Evidence: past_attempts (25-min study block helped). "
            "Entry: name the concrete win first."
        ),
        "evidence_used": [
            {"source": "hbm_attribute.past_attempts (main_problem=academic_pressure, s1t3)",
             "content": "tried a 25-minute study block yesterday"},
        ],
        "final_response": (
            "Protecting a short block took some doing — that's a real step. "
            "Want me to suggest one tiny tweak to make it sturdier?"
        ),
    }


def _canned_session_context(session_id: int) -> dict:
    return {
        "current_life_events": "Finals week; minimal sleep; extra shifts at work.",
        "mental_state": "Exhausted and raw.",
        "mood": "exhausted",
        "emotions": ["anxious", "defeated"],
        "resistance_cooperation_level": "medium",
        "currently_active_problems": ["academic_pressure", "sleep_problems"],
        "why_bringing_these_up_now": "Finals + sleep loss reached a tipping point.",
    }


def _canned_mind1_turn(turn_id: int) -> dict:
    msgs = {
        1: "I'm pulling all-nighters for finals and I can't sleep. I don't think I can keep this up.",
        2: "Is it normal to feel like I literally can't finish, right?",
        3: "I tried a 25-minute study block yesterday. It kind of helped.",
    }
    return {
        "simulated_user_message": msgs.get(turn_id, "I'm not sure."),
        "problems_referred": (
            ["academic_pressure", "sleep_problems"] if turn_id <= 2
            else ["academic_pressure"]
        ),
    }


def _canned_persona_update() -> dict:
    return {
        "updates": [
            {"field": "demographics", "action": "keep",
             "new_value": None, "evidence_citations": []},
            {"field": "personality_traits", "action": "update",
             "new_value": "analytical, self-critical",
             "evidence_citations": [
                 {"turn_id": 1, "excerpt": "I don't think I can keep this up"},
                 {"turn_id": 2, "excerpt": "literally can't finish"},
             ]},
            {"field": "core_values", "action": "keep",
             "new_value": None, "evidence_citations": []},
            {"field": "core_beliefs", "action": "keep",
             "new_value": None, "evidence_citations": []},
            {"field": "support_system", "action": "keep",
             "new_value": None, "evidence_citations": []},
            {"field": "hobbies_interests", "action": "keep",
             "new_value": None, "evidence_citations": []},
            {"field": "communication_style", "action": "update",
             "new_value": "guarded, short",
             "evidence_citations": [{"turn_id": 1, "excerpt": "keep this up"}]},
            {"field": "relevant_history", "action": "keep",
             "new_value": None, "evidence_citations": []},
            {"field": "general_behavioral_traits", "action": "update",
             "new_value": "self-critical, overthinker",
             "evidence_citations": [
                 {"turn_id": 1, "excerpt": "I don't think I can keep this up"},
                 {"turn_id": 2, "excerpt": "literally can't finish, right"},
             ]},
        ],
    }


def _canned_miti_judge() -> dict:
    return {
        "globals": [
            {"name": "cultivating_change_talk", "score": 4,
             "justification": "Drew out user's own reasoning around the study block."},
            {"name": "softening_sustain_talk", "score": 5,
             "justification": "Reflected 'crushing' / 'can't' lines without arguing back."},
            {"name": "partnership", "score": 4,
             "justification": "Honored autonomy in suggestion to tweak the block."},
            {"name": "empathy", "score": 5,
             "justification": "Named the body's tally underneath the deadline pressure."},
        ],
    }


def _canned_esc_judge() -> dict:
    """Per-session ESC judge canned output. Same shape `esc_judge` writes
    to `esc_judge_s{NN}.json`. Replaces the obsolete all-sessions Mind-3
    mock.
    """
    return {
        "dimensions": [
            {"name": "empathy", "score": 4, "justification": "ack"},
            {"name": "understanding", "score": 4, "justification": "ack"},
            {"name": "helpfulness", "score": 3, "justification": "ack"},
            {"name": "autonomy_respect", "score": 5, "justification": "ack"},
            {"name": "non_judgment", "score": 5, "justification": "ack"},
            {"name": "willingness_to_continue", "score": 4, "justification": "ack"},
        ],
    }


# ---------------------------------------------------------------------------
# MockClient
# ---------------------------------------------------------------------------


class _MockClient:
    """LLMClient stand-in serving canned JSON per call_role.

    Per-turn varying roles (inference, recompute, response_v6, mind1_v6)
    are looked up by turn_id via ctx.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []  # (role, session, turn)

    def generate_structured(
        self, *, ctx, system_prompt, user_prompt, schema,
        validator_extras=None,
    ) -> dict:
        role = ctx.call_role
        self.calls.append((role, ctx.session_id, ctx.turn_id))

        if role == "session_context":
            out = _canned_session_context(ctx.session_id)
        elif role == "mind1_v6":
            out = _canned_mind1_turn(ctx.turn_id)
        elif role == "inference":
            out = _canned_inference_turn(ctx.session_id, ctx.turn_id)
        elif role == "recompute":
            out = _canned_recompute_turn(ctx.session_id, ctx.turn_id)
        elif role == "response_v6":
            out = _canned_response_turn(ctx.turn_id)
        elif role == "persona_update_v6":
            out = _canned_persona_update()
        elif role == "miti_judge":
            out = _canned_miti_judge()
        elif role == "esc_judge":
            out = _canned_esc_judge()
        else:
            raise KeyError(f"MockClient has no canned response for role {role!r}")

        if validator_extras is not None:
            validator_extras(out)
        return out


# ---------------------------------------------------------------------------
# Smoke runner
# ---------------------------------------------------------------------------


def _run_smoke(tmpdir: Path) -> dict[str, Any]:
    from .. import config
    orig_transcript = config.TRANSCRIPT_DIR
    orig_graph_v6 = config.GRAPH_V6_DIR
    tmp_transcript = tmpdir / "transcripts"
    tmp_graph_v6 = tmpdir / "graphs_v6"
    config.TRANSCRIPT_DIR = tmp_transcript  # type: ignore[misc]
    config.GRAPH_V6_DIR = tmp_graph_v6  # type: ignore[misc]
    from .. import session_driver_v6 as sdv6
    sdv6.config = config

    try:
        from ..profile_spec import ProfileSpec, RunConfig
        profile = ProfileSpec(
            profile_id="SMOKE01",
            source_emocare_id=None,
            seed_situation_paragraph="A grad student at the end of a hard term.",
            primary_problem="academic_pressure",
            session_arc=["session 1: exhausted but still moving"],
            persona_draft={
                "personality_traits": ["analytical", "self-critical"],
                "communication_style": "guarded",
                "relevant_history": "first-gen college; prior burnout",
            },
            blurb="A grad student venting about finals and sleep.",
        )
        run_cfg = RunConfig(sessions_per_profile=1, turns_per_session=3)
        client = _MockClient()
        artifacts = sdv6.run_profile_v6(
            profile=profile, run_cfg=run_cfg, client=client,
        )

        return {
            "artifacts": artifacts,
            "client_calls": client.calls,
            "tmp_transcript": tmp_transcript,
            "tmp_graph_v6": tmp_graph_v6,
        }
    finally:
        config.TRANSCRIPT_DIR = orig_transcript  # type: ignore[misc]
        config.GRAPH_V6_DIR = orig_graph_v6  # type: ignore[misc]


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="helpe_v6_smoke_"))
    try:
        out = _run_smoke(tmpdir)
        artifacts = out["artifacts"]
        calls = out["client_calls"]
        tmp_transcript: Path = out["tmp_transcript"]
        tmp_graph_v6: Path = out["tmp_graph_v6"]

        # --- Shape of the in-memory artifacts ---
        assert len(artifacts.sessions) == 1
        sess = artifacts.sessions[0]
        assert len(sess.transcript) == 6, f"expected 3 user + 3 assistant turns, got {len(sess.transcript)}"
        assert len(sess.turn_traces) == 3
        # Session summary dropped in redesign — always empty.
        assert sess.session_summary == ""
        assert sess.stage_transitions, "expected at least one TTM transition"
        # Persona update applied.
        actions = {u["field"]: u["action"] for u in sess.persona_updates}
        assert actions["personality_traits"] == "update"
        assert actions["general_behavioral_traits"] == "update"
        assert actions["communication_style"] == "update"
        assert actions["demographics"] == "keep"
        # MITI judge ran.
        assert sess.miti_judge and "globals" in sess.miti_judge
        names = sorted(g["name"] for g in sess.miti_judge["globals"])
        assert names == sorted([
            "cultivating_change_talk", "softening_sustain_talk",
            "partnership", "empathy",
        ])

        # Mind-2 + Mind-3 are both dropped. esc_judge replaces mind3.
        assert artifacts.mind2_out is None
        assert artifacts.mind3_out is None

        # --- Call ordering: session_context, then per-turn block × 3,
        # then persona_update_v6, miti_judge, esc_judge.
        # NO session_summary, NO mind2, NO mind3.
        roles = [c[0] for c in calls]
        assert roles[0] == "session_context"
        turn_blocks = [
            ("mind1_v6", "inference", "recompute", "response_v6"),
        ] * 3
        idx = 1
        for block in turn_blocks:
            for role in block:
                assert roles[idx] == role, f"role mismatch at idx {idx}: {roles}"
                idx += 1
        # Post-turn roles fire from a pool, so submission order doesn't
        # equal completion order. Compare as a multiset.
        post = sorted(roles[idx:])
        assert post == sorted([
            "persona_update_v6", "miti_judge", "esc_judge",
        ]), f"post-turn roles: {roles[idx:]}"
        # And the dropped roles never fire.
        assert "mind2" not in roles
        assert "mind3" not in roles
        assert "session_summary" not in roles

        # --- Persistence layout ---
        p_dir = tmp_transcript / "SMOKE01" / "v6"
        assert (p_dir / "session_01.json").exists()
        assert (p_dir / "mind1_reasoning_s01.jsonl").exists()
        assert (p_dir / "session_context_s01.json").exists()
        assert (p_dir / "miti_judge_s01.json").exists()
        assert (p_dir / "run_artifacts.json").exists()
        # Graph snapshots now live under graphs_v6/{system}/ so v1/v3/v6
        # never overwrite each other.
        assert (tmp_graph_v6 / "v6" / "SMOKE01_after_s01.json").exists()

        sidecar_lines = (p_dir / "mind1_reasoning_s01.jsonl").read_text().strip().splitlines()
        assert len(sidecar_lines) == 3
        main_json = json.loads((p_dir / "session_01.json").read_text())
        assert "mind1_hidden" not in main_json
        for key in ["session_id", "transcript", "turn_traces",
                    "persona_updates", "stage_transitions"]:
            assert key in main_json, f"missing key in main transcript: {key}"

        # --- Loader round-trip + new metrics ---
        from .. import config as cfg
        orig = cfg.TRANSCRIPT_DIR
        orig_g = cfg.GRAPH_V6_DIR
        cfg.TRANSCRIPT_DIR = tmp_transcript  # type: ignore[misc]
        cfg.GRAPH_V6_DIR = tmp_graph_v6  # type: ignore[misc]
        try:
            from .v6_loader import (
                iter_v6_assistant_turns, list_v6_profiles,
                load_v6_graph, load_v6_run_artifacts,
                load_v6_session_files, load_v6_session_miti,
                load_v6_turn_traces, load_v6_transcripts_for_minds,
            )
            assert "SMOKE01" in list_v6_profiles()
            sessions = load_v6_session_files("SMOKE01")
            assert len(sessions) == 1 and sessions[0]["session_id"] == 1
            traces = load_v6_turn_traces("SMOKE01")
            assert len(traces) == 3
            transcripts_for_minds = load_v6_transcripts_for_minds("SMOKE01")
            assert len(transcripts_for_minds[0]["turns"]) == 6
            run_art = load_v6_run_artifacts("SMOKE01")
            assert run_art and run_art["system"] == "v6"

            # Graph loads cleanly.
            g = load_v6_graph("SMOKE01")
            assert g is not None
            assert "academic_pressure" in g.problems
            assert "sleep_problems" in g.problems
            assert g.problems["academic_pressure"].current_ttm_stage == "preparation"
            edge = next(iter(g.edges.values()))
            assert len(edge.cooccurrence_entries) >= 1
            assert len(edge.attribute_connection_entries) >= 1
            assert edge.weight > 0
            assert "analytical" in g.persona.personality_traits

            # MITI loader.
            miti_sessions = load_v6_session_miti("SMOKE01")
            assert len(miti_sessions) == 1
            assert any(g["name"] == "empathy" for g in miti_sessions[0]["globals"])

            # New metrics.
            from .judge import extract_misc_codes
            from .metrics import (
                compute_all_metrics_v6,
                miti_per_profile, transition_rate_per_profile,
                esc_per_profile,
            )
            miti = miti_per_profile(miti_sessions)
            assert miti["overall_mean"] == (4 + 5 + 4 + 5) / 4
            ttm = transition_rate_per_profile(traces)
            # academic_pressure: enters trace at `contemplation` because
            # turn-1 recompute moves it from precontemplation→contemplation
            # before the snapshot is taken. Per §1.b "starting stage is
            # whatever the recompute call infers when the problem enters
            # the graph", so the precontemplation→contemplation transition
            # does NOT count. The subsequent contemplation→preparation
            # (turn 3) DOES count.
            ap = ttm["per_problem"]["academic_pressure"]
            assert ap["first_seen_stage"] == "contemplation"
            assert ap["last_seen_stage"] == "preparation"
            assert ap["reached_action"] is False
            assert ap["regressions"] == 0
            assert "turns_to_precontemplation_to_contemplation" not in ap
            assert ap["turns_to_contemplation_to_preparation"] is not None
            # ESC now reads per-session esc_judge_s{NN}.json files
            # (mind3_out is no longer populated; the v6 driver wrote
            # per-session ESC outputs above).
            from .v6_loader import load_v6_session_esc
            from .metrics import esc_per_profile_from_sessions
            esc_sessions = load_v6_session_esc("SMOKE01")
            esc = esc_per_profile_from_sessions(esc_sessions)
            assert esc["overall_mean"] is not None, (
                f"esc per-session files missing or empty: {esc_sessions}"
            )

            bundle = compute_all_metrics_v6(
                profile_id="SMOKE01",
                miti_session_outputs=miti_sessions,
                mind3_out=None,  # mind3 dropped; pass None
                turn_traces=traces,
            )
            for key in ("MITI", "TTM_TRANSITION_RATE", "ESC"):
                assert key in bundle

            # MISC code extraction from new `reasoning` field.
            any_assistant = list(iter_v6_assistant_turns("SMOKE01"))
            assert len(any_assistant) == 3
            sid, tid, user_msg, resp_v6, bundle_snap, recent = any_assistant[0]
            assert resp_v6["final_response"].startswith("Pulling all-nighters")
            codes_t1 = extract_misc_codes(resp_v6["reasoning"])
            assert codes_t1 == ["complex_reflection", "support"], codes_t1
            _, _, _, resp_t2, _, _ = any_assistant[1]
            codes_t2 = extract_misc_codes(resp_t2["reasoning"])
            assert codes_t2 == ["evoke", "facilitate"], codes_t2
            _, _, _, resp_t3, _, _ = any_assistant[2]
            codes_t3 = extract_misc_codes(resp_t3["reasoning"])
            assert codes_t3 == ["advise_with_permission", "support"], codes_t3
            # evidence_used carried through.
            assert resp_v6["evidence_used"]
            assert all("source" in e and "content" in e for e in resp_v6["evidence_used"])
        finally:
            cfg.TRANSCRIPT_DIR = orig  # type: ignore[misc]
            cfg.GRAPH_V6_DIR = orig_g  # type: ignore[misc]

        print("v6 (redesign) end-to-end smoke test PASSED")
        print(f"  - 1 session × 3 turns; {len(calls)} LLM calls total")
        print(f"  - MITI globals overall mean: {miti['overall_mean']:.2f}")
        print(f"  - MISC codes turn-by-turn: t1={codes_t1}, t2={codes_t2}, t3={codes_t3}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
