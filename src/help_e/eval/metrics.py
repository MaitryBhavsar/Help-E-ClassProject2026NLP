"""§1 v6 (REDESIGN) — Evaluation metrics.

The redesign collapses evaluation to THREE criteria (§1.a / §1.b / §1.c):

  - MITI 4.2 adherence — session-level LLM judge, 4 globals × 1–5
    (`miti_per_session`, `miti_per_profile`, `miti_across_profiles`).
  - TTM state-transition rate — pure-Python compute over per-turn TTM
    snapshots from the v6 `turn_traces` (`transition_rate_per_problem`,
    `transition_rate_per_profile`, `transition_rate_across_profiles`).
  - ESC adherence — Mind-3 6-dim 1–5 (kept from v5; helpers
    `esc_per_profile`, `esc_across_profiles`).

E2 (AnnoMI) and per-turn evidence-relevance are out of scope (§1.d).
E3a/E3b (Mind-2 silver labels), E4 (maintenance reach), and the E1 4-dim
0–3 rubric were removed when Mind-2 and the `maintenance` stage were
dropped from v6.

Statistical helpers (Wilcoxon, Holm–Bonferroni, mixed-effects) live at
the bottom and are unchanged.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import mean, median
from typing import Any, Iterable, Optional

from ..config import (
    ESC_DIMENSIONS,
    MITI_42_GLOBALS,
    TTM_STAGES_V6,
)


# Stage ordering for advancement / regression direction. Higher index =
# more advanced. v6 uses the 4-stage enum; `maintenance` is gone.
_STAGE_INDEX = {s: i for i, s in enumerate(TTM_STAGES_V6)}


# ---------------------------------------------------------------------------
# §1.a — MITI 4.2 adherence (session-level)
# ---------------------------------------------------------------------------


def miti_per_session(judge_out: dict) -> dict:
    """Flatten one MITI judge output into per-global scores + overall mean.

    Input shape: `{"globals": [{"name", "score", "justification"}, ...]}`
    """
    globals_ = {g["name"]: int(g["score"]) for g in judge_out.get("globals", [])}
    overall = mean(globals_.values()) if globals_ else None
    return {
        "globals": globals_,
        "overall_mean": overall,
        "_fallback_default": bool(judge_out.get("_fallback_default")),
    }


def miti_per_profile(session_judge_outputs: list[dict]) -> dict:
    """Aggregate a profile's session-level MITI judges.

    Returns mean per global across this profile's sessions, plus overall
    mean across globals (averaged after per-session aggregation).
    """
    n = len(session_judge_outputs)
    if n == 0:
        return {
            "per_global_mean": {g: None for g in MITI_42_GLOBALS},
            "overall_mean": None,
            "n_sessions": 0,
        }
    per_global: dict[str, list[float]] = {g: [] for g in MITI_42_GLOBALS}
    for out in session_judge_outputs:
        for g in out.get("globals", []):
            if g["name"] in per_global:
                per_global[g["name"]].append(float(g["score"]))
    per_global_mean = {
        g: (mean(v) if v else None) for g, v in per_global.items()
    }
    overalls = [v for v in per_global_mean.values() if v is not None]
    return {
        "per_global_mean": per_global_mean,
        "overall_mean": mean(overalls) if overalls else None,
        "n_sessions": n,
    }


def miti_across_profiles(profile_aggregates: list[dict]) -> dict:
    """Mean / median of `overall_mean` and per-global means across
    profiles. Each input is a `miti_per_profile` output dict.
    """
    if not profile_aggregates:
        return {
            "per_global_mean": {g: None for g in MITI_42_GLOBALS},
            "overall_mean_mean": None,
            "overall_mean_median": None,
            "n_profiles": 0,
        }
    per_global: dict[str, list[float]] = {g: [] for g in MITI_42_GLOBALS}
    overalls: list[float] = []
    for agg in profile_aggregates:
        for g, v in (agg.get("per_global_mean") or {}).items():
            if g in per_global and v is not None:
                per_global[g].append(float(v))
        if agg.get("overall_mean") is not None:
            overalls.append(float(agg["overall_mean"]))
    return {
        "per_global_mean": {g: (mean(v) if v else None) for g, v in per_global.items()},
        "overall_mean_mean": mean(overalls) if overalls else None,
        "overall_mean_median": median(overalls) if overalls else None,
        "n_profiles": len(profile_aggregates),
    }


# ---------------------------------------------------------------------------
# §1.b — TTM state-transition rate (per-turn TTM, pure compute)
# ---------------------------------------------------------------------------


def _replay_per_problem_ttm(turn_traces: list[dict]) -> dict[str, list[tuple[int, str]]]:
    """Replay the v6 `turn_traces` to produce, per problem, the chronological
    list of `(global_turn_idx, stage)` snapshots — one entry per turn the
    problem appeared as current.

    `global_turn_idx` is `(session_id - 1) * 1000 + turn_id` so transitions
    span sessions naturally (small per-session turn counts).
    """
    per_problem_stage: dict[str, str] = {}
    history: dict[str, list[tuple[int, str]]] = defaultdict(list)

    ordered = sorted(
        turn_traces,
        key=lambda t: (t.get("session_id", 0), t.get("turn_id", 0)),
    )
    for tr in ordered:
        sid = int(tr.get("session_id") or 0)
        tid = int(tr.get("turn_id") or 0)
        gidx = sid * 1000 + tid
        trace = tr.get("trace") or {}
        # Apply ttm_updates first so the snapshot reflects post-update stage.
        for u in trace.get("ttm_updates") or []:
            pname = u.get("problem_name")
            new_stage = u.get("new_ttm_stage")
            if pname and new_stage in _STAGE_INDEX:
                per_problem_stage[pname] = new_stage
        for p in trace.get("current_problems") or []:
            # Default new problems whose stage hasn't been set yet.
            stage = per_problem_stage.get(p)
            if stage is None or stage not in _STAGE_INDEX:
                continue
            history[p].append((gidx, stage))
    return history


def _first_appearance_at_each_stage(
    history: list[tuple[int, str]],
) -> dict[str, int]:
    """Return `{stage: global_turn_idx}` for the FIRST turn the problem
    was observed in that stage, in chronological order.
    """
    out: dict[str, int] = {}
    for gidx, stage in history:
        if stage not in out:
            out[stage] = gidx
    return out


# Forward-only TTM transitions of interest (§1.b). Edge cases:
#   - regression (e.g. contemplation → precontemplation) is logged
#     separately as `regressions` count.
#   - transitions that did not happen: don't contribute to per-transition
#     turn counts.
_FORWARD_TRANSITIONS: tuple[tuple[str, str], ...] = (
    ("precontemplation", "contemplation"),
    ("contemplation", "preparation"),
    ("preparation", "action"),
)


def transition_rate_per_problem(turn_traces: list[dict]) -> dict[str, dict]:
    """For each problem the v6 trace touched, report when (if) it
    transitioned forward through TTM stages.

    Returned shape per problem:
      {
        "first_seen_stage":      str,
        "last_seen_stage":       str,
        "reached_action":        bool,
        "regressions":           int,
        "first_idx_per_stage":   {stage: global_turn_idx},
        # Only populated for transitions that actually occurred:
        "turns_to_<from>_to_<to>": int,    # absolute turn distance
      }
    """
    history = _replay_per_problem_ttm(turn_traces)
    out: dict[str, dict] = {}

    for pname, hist in history.items():
        if not hist:
            continue
        first_stage = hist[0][1]
        last_stage = hist[-1][1]
        first_idx_per_stage = _first_appearance_at_each_stage(hist)

        # Count regressions across consecutive snapshots.
        regressions = 0
        for (_, prev_stage), (_, cur_stage) in zip(hist, hist[1:]):
            if prev_stage in _STAGE_INDEX and cur_stage in _STAGE_INDEX:
                if _STAGE_INDEX[cur_stage] < _STAGE_INDEX[prev_stage]:
                    regressions += 1

        rec: dict[str, Any] = {
            "first_seen_stage": first_stage,
            "last_seen_stage": last_stage,
            "reached_action": "action" in first_idx_per_stage,
            "regressions": regressions,
            "first_idx_per_stage": dict(first_idx_per_stage),
        }
        for src, dst in _FORWARD_TRANSITIONS:
            if src in first_idx_per_stage and dst in first_idx_per_stage:
                # Transition counted only when DST first appears AFTER SRC
                # first appeared. (If the problem was observed at DST
                # before SRC — e.g. inferred straight into contemplation —
                # that is a "born advanced" path, not a transition.)
                src_idx = first_idx_per_stage[src]
                dst_idx = first_idx_per_stage[dst]
                if dst_idx > src_idx:
                    rec[f"turns_to_{src}_to_{dst}"] = dst_idx - src_idx
        out[pname] = rec
    return out


def transition_rate_per_profile(turn_traces: list[dict]) -> dict:
    """Aggregate per-problem transition stats into per-profile metrics.

    Output:
      {
        "per_problem":          {pname: ...},   # raw stats
        "n_problems":           int,
        "n_problems_reached_action":      int,
        "pct_reached_action":             float | None,    # 0–1
        "mean_turns_to_action":           float | None,
        "mean_turns_to_<from>_to_<to>":   float | None,    # per transition
        "regressions_total":              int,
      }
    """
    per_problem = transition_rate_per_problem(turn_traces)
    n = len(per_problem)
    if n == 0:
        out: dict[str, Any] = {
            "per_problem": {}, "n_problems": 0,
            "n_problems_reached_action": 0,
            "pct_reached_action": None,
            "mean_turns_to_action": None,
            "regressions_total": 0,
        }
        for src, dst in _FORWARD_TRANSITIONS:
            out[f"mean_turns_to_{src}_to_{dst}"] = None
        return out

    reached = [r for r in per_problem.values() if r["reached_action"]]
    out: dict[str, Any] = {
        "per_problem": per_problem,
        "n_problems": n,
        "n_problems_reached_action": len(reached),
        "pct_reached_action": len(reached) / n,
        "regressions_total": sum(r["regressions"] for r in per_problem.values()),
    }

    # Mean turns per transition kind, across problems where it occurred.
    for src, dst in _FORWARD_TRANSITIONS:
        key = f"turns_to_{src}_to_{dst}"
        vals = [r[key] for r in per_problem.values() if key in r]
        out[f"mean_{key}"] = mean(vals) if vals else None

    # Mean turns to action (chained pre→...→action span if available;
    # else just first_idx_at_action - first_idx_at_first_seen).
    turns_to_action: list[int] = []
    for r in reached:
        first_seen_idx = min(r["first_idx_per_stage"].values())
        action_idx = r["first_idx_per_stage"]["action"]
        if action_idx > first_seen_idx:
            turns_to_action.append(action_idx - first_seen_idx)
    out["mean_turns_to_action"] = (
        mean(turns_to_action) if turns_to_action else None
    )
    return out


def transition_rate_across_profiles(profile_aggregates: list[dict]) -> dict:
    """Mean per-transition turn counts + distribution of % reached action,
    across profiles. Each input is a `transition_rate_per_profile` output.
    """
    if not profile_aggregates:
        out: dict[str, Any] = {
            "n_profiles": 0,
            "pct_reached_action": {"mean": None, "median": None, "values": []},
            "mean_turns_to_action": None,
        }
        for src, dst in _FORWARD_TRANSITIONS:
            out[f"mean_turns_to_{src}_to_{dst}"] = None
        return out

    pct_vals = [
        agg["pct_reached_action"] for agg in profile_aggregates
        if agg["pct_reached_action"] is not None
    ]
    tta_vals = [
        agg["mean_turns_to_action"] for agg in profile_aggregates
        if agg["mean_turns_to_action"] is not None
    ]
    out: dict[str, Any] = {
        "n_profiles": len(profile_aggregates),
        "pct_reached_action": {
            "mean": mean(pct_vals) if pct_vals else None,
            "median": median(pct_vals) if pct_vals else None,
            "values": pct_vals,
        },
        "mean_turns_to_action": mean(tta_vals) if tta_vals else None,
    }
    for src, dst in _FORWARD_TRANSITIONS:
        key = f"mean_turns_to_{src}_to_{dst}"
        vals = [
            agg[key] for agg in profile_aggregates if agg.get(key) is not None
        ]
        out[key] = mean(vals) if vals else None
    return out


# ---------------------------------------------------------------------------
# §1.c — ESC adherence (Mind-3, kept from v5)
# ---------------------------------------------------------------------------


def esc_per_profile(mind3_out: dict) -> dict:
    """Per-dimension mean + overall mean across this profile's sessions.

    `mind3_out` shape: `{"sessions": [{"session_id", "dimensions": [
        {"dimension", "score"}, ...]}, ...]}`.
    """
    sessions = mind3_out.get("sessions", []) if mind3_out else []
    if not sessions:
        return {
            "per_dim_mean": {d: None for d in ESC_DIMENSIONS},
            "overall_mean": None,
            "n_sessions": 0,
        }
    per_dim: dict[str, list[float]] = {d: [] for d in ESC_DIMENSIONS}
    for s in sessions:
        for d in s.get("dimensions") or []:
            if d["dimension"] in per_dim:
                per_dim[d["dimension"]].append(float(d["score"]))
    per_dim_mean = {d: (mean(v) if v else None) for d, v in per_dim.items()}
    overalls = [v for v in per_dim_mean.values() if v is not None]
    return {
        "per_dim_mean": per_dim_mean,
        "overall_mean": mean(overalls) if overalls else None,
        "n_sessions": len(sessions),
    }


def esc_per_profile_from_sessions(esc_session_outputs: list[dict]) -> dict:
    """Per-dimension mean + overall mean across this profile's sessions
    using the new session-level ESC judge output (one file per session).

    Each `esc_session_outputs[i]` shape: `{"dimensions": [{"name",
    "score", "justification"}, ...]}` — note the key is `name`, NOT
    `dimension` (mirrors `miti_judge_v6` shape, not the old `mind3`).
    """
    if not esc_session_outputs:
        return {
            "per_dim_mean": {d: None for d in ESC_DIMENSIONS},
            "overall_mean": None,
            "n_sessions": 0,
            "n_fallbacks": 0,
        }
    per_dim: dict[str, list[float]] = {d: [] for d in ESC_DIMENSIONS}
    n_fallbacks = 0
    for s in esc_session_outputs:
        if s.get("_fallback_default"):
            n_fallbacks += 1
        for d in s.get("dimensions") or []:
            name = d.get("name") or d.get("dimension")
            if name in per_dim:
                per_dim[name].append(float(d["score"]))
    per_dim_mean = {d: (mean(v) if v else None) for d, v in per_dim.items()}
    overalls = [v for v in per_dim_mean.values() if v is not None]
    return {
        "per_dim_mean": per_dim_mean,
        "overall_mean": mean(overalls) if overalls else None,
        "n_sessions": len(esc_session_outputs),
        "n_fallbacks": n_fallbacks,
    }


def esc_across_profiles(profile_aggregates: list[dict]) -> dict:
    if not profile_aggregates:
        return {
            "per_dim_mean": {d: None for d in ESC_DIMENSIONS},
            "overall_mean_mean": None,
            "overall_mean_median": None,
            "n_profiles": 0,
        }
    per_dim: dict[str, list[float]] = {d: [] for d in ESC_DIMENSIONS}
    overalls: list[float] = []
    for agg in profile_aggregates:
        for d, v in (agg.get("per_dim_mean") or {}).items():
            if d in per_dim and v is not None:
                per_dim[d].append(float(v))
        if agg.get("overall_mean") is not None:
            overalls.append(float(agg["overall_mean"]))
    return {
        "per_dim_mean": {d: (mean(v) if v else None) for d, v in per_dim.items()},
        "overall_mean_mean": mean(overalls) if overalls else None,
        "overall_mean_median": median(overalls) if overalls else None,
        "n_profiles": len(profile_aggregates),
    }


# ---------------------------------------------------------------------------
# Top-level aggregator
# ---------------------------------------------------------------------------


def compute_all_metrics_v6(
    *,
    profile_id: str,
    miti_session_outputs: list[dict],
    mind3_out: Optional[dict],
    turn_traces: list[dict],
) -> dict:
    """Per-profile bundle with the three v6-redesign metrics."""
    return {
        "profile_id": profile_id,
        "MITI": miti_per_profile(miti_session_outputs),
        "TTM_TRANSITION_RATE": transition_rate_per_profile(turn_traces),
        "ESC": esc_per_profile(mind3_out or {}),
    }


# ---------------------------------------------------------------------------
# Statistical tests (§11.7) — optional (scipy / statsmodels)
# ---------------------------------------------------------------------------


def wilcoxon_signed_rank(
    paired_a: Iterable[float],
    paired_b: Iterable[float],
) -> dict:
    a = list(paired_a)
    b = list(paired_b)
    if len(a) != len(b) or not a:
        return {"_unavailable": True, "reason": "mismatched or empty inputs"}
    try:
        from scipy.stats import wilcoxon  # type: ignore
    except ImportError:
        return {"_unavailable": True, "reason": "scipy not installed"}
    diffs = [x - y for x, y in zip(a, b)]
    nonzero = [d for d in diffs if d != 0]
    if not nonzero:
        return {"statistic": 0.0, "pvalue": 1.0, "n": len(a), "median_diff": 0.0}
    stat, p = wilcoxon(a, b, zero_method="wilcox")
    diffs_sorted = sorted(diffs)
    mid = len(diffs_sorted) // 2
    median_diff = (
        diffs_sorted[mid]
        if len(diffs_sorted) % 2 else
        (diffs_sorted[mid - 1] + diffs_sorted[mid]) / 2
    )
    return {
        "statistic": float(stat),
        "pvalue": float(p),
        "n": len(a),
        "median_diff": median_diff,
    }


def holm_bonferroni(pvalues: list[float], alpha: float = 0.05) -> list[bool]:
    indexed = sorted(enumerate(pvalues), key=lambda kv: kv[1])
    m = len(pvalues)
    rejected = [False] * m
    for rank, (orig_i, p) in enumerate(indexed):
        threshold = alpha / (m - rank)
        if p <= threshold:
            rejected[orig_i] = True
        else:
            break
    return rejected


def mixed_effects_system_vs_metric(
    records: list[dict],
    metric_col: str,
) -> dict:
    try:
        import statsmodels.formula.api as smf  # type: ignore
        import pandas as pd  # type: ignore
    except ImportError:
        return {"_unavailable": True, "reason": "statsmodels/pandas not installed"}
    if not records:
        return {"_unavailable": True, "reason": "no records"}
    df = pd.DataFrame(records)
    model = smf.mixedlm(
        f"{metric_col} ~ C(system)",
        df,
        groups=df["persona"],
    ).fit(method="lbfgs")
    return {
        "params": model.params.to_dict(),
        "pvalues": model.pvalues.to_dict(),
        "converged": bool(model.converged),
        "nobs": int(model.nobs),
    }


# ---------------------------------------------------------------------------
# Self-test (no LLM)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    # MITI per-session.
    j = {
        "globals": [
            {"name": "cultivating_change_talk", "score": 4, "justification": "x"},
            {"name": "softening_sustain_talk", "score": 5, "justification": "x"},
            {"name": "partnership", "score": 3, "justification": "x"},
            {"name": "empathy", "score": 4, "justification": "x"},
        ],
    }
    s = miti_per_session(j)
    assert s["globals"]["cultivating_change_talk"] == 4
    assert s["overall_mean"] == 4.0  # (4+5+3+4)/4 = 4.0
    assert s["_fallback_default"] is False

    # MITI per-profile across two sessions.
    j2 = {
        "globals": [
            {"name": "cultivating_change_talk", "score": 2, "justification": "x"},
            {"name": "softening_sustain_talk", "score": 3, "justification": "x"},
            {"name": "partnership", "score": 1, "justification": "x"},
            {"name": "empathy", "score": 2, "justification": "x"},
        ],
    }
    prof = miti_per_profile([j, j2])
    assert prof["n_sessions"] == 2
    assert prof["per_global_mean"]["cultivating_change_talk"] == 3.0
    assert prof["overall_mean"] == (3.0 + 4.0 + 2.0 + 3.0) / 4

    # MITI across profiles.
    across = miti_across_profiles([prof, prof])
    assert across["n_profiles"] == 2
    assert across["overall_mean_mean"] == prof["overall_mean"]

    # TTM transition rate — synthetic 1-session trace where one problem
    # walks pre → contempl → preparation → action over four turns.
    traces = []
    stages = ["precontemplation", "contemplation", "preparation", "action"]
    for tid, st in enumerate(stages, start=1):
        traces.append({
            "session_id": 1, "turn_id": tid,
            "trace": {
                "current_problems": ["academic_pressure"],
                "ttm_updates": [
                    {"problem_name": "academic_pressure",
                     "new_ttm_stage": st},
                ],
            },
        })
    per_problem = transition_rate_per_problem(traces)
    rec = per_problem["academic_pressure"]
    assert rec["first_seen_stage"] == "precontemplation"
    assert rec["last_seen_stage"] == "action"
    assert rec["reached_action"] is True
    assert rec["regressions"] == 0
    # Each consecutive turn is +1 in the global-idx chain.
    assert rec["turns_to_precontemplation_to_contemplation"] == 1
    assert rec["turns_to_contemplation_to_preparation"] == 1
    assert rec["turns_to_preparation_to_action"] == 1

    prof_tr = transition_rate_per_profile(traces)
    assert prof_tr["pct_reached_action"] == 1.0
    assert prof_tr["mean_turns_to_action"] == 3.0  # pre → action = +3
    assert prof_tr["mean_turns_to_precontemplation_to_contemplation"] == 1.0
    assert prof_tr["regressions_total"] == 0

    # Regression case: contemplation → precontemplation should count.
    reg_traces = [
        {"session_id": 1, "turn_id": 1,
         "trace": {"current_problems": ["x"],
                   "ttm_updates": [{"problem_name": "x",
                                    "new_ttm_stage": "contemplation"}]}},
        {"session_id": 1, "turn_id": 2,
         "trace": {"current_problems": ["x"],
                   "ttm_updates": [{"problem_name": "x",
                                    "new_ttm_stage": "precontemplation"}]}},
    ]
    pp_reg = transition_rate_per_problem(reg_traces)
    assert pp_reg["x"]["regressions"] == 1
    assert pp_reg["x"]["reached_action"] is False
    # No forward transition counted (contempl appeared first, then went back).
    assert "turns_to_precontemplation_to_contemplation" not in pp_reg["x"]

    # Empty input.
    assert transition_rate_per_problem([]) == {}
    pp_empty = transition_rate_per_profile([])
    assert pp_empty["n_problems"] == 0
    assert pp_empty["pct_reached_action"] is None

    # ESC.
    m3 = {
        "sessions": [
            {"session_id": 1,
             "dimensions": [
                 {"dimension": d, "score": 4} for d in ESC_DIMENSIONS
             ]},
            {"session_id": 2,
             "dimensions": [
                 {"dimension": d, "score": 5} for d in ESC_DIMENSIONS
             ]},
        ],
    }
    esc = esc_per_profile(m3)
    assert esc["n_sessions"] == 2
    for d in ESC_DIMENSIONS:
        assert esc["per_dim_mean"][d] == 4.5
    assert esc["overall_mean"] == 4.5

    # compute_all_metrics_v6 plumbing.
    bundle = compute_all_metrics_v6(
        profile_id="P01",
        miti_session_outputs=[j, j2],
        mind3_out=m3,
        turn_traces=traces,
    )
    assert bundle["profile_id"] == "P01"
    assert "MITI" in bundle and "TTM_TRANSITION_RATE" in bundle and "ESC" in bundle

    # Cross-profile.
    multi = transition_rate_across_profiles([prof_tr, prof_tr])
    assert multi["pct_reached_action"]["mean"] == 1.0
    assert multi["mean_turns_to_action"] == 3.0

    print("metrics (redesign) self-test PASSED")


if __name__ == "__main__":
    _self_test()
