"""Aggregate reporter for a v6 (REDESIGN) matrix run.

Scans every profile on disk that has v6 artifacts, computes the per-profile
metric bundle (the three v6-redesign criteria — MITI 4.2 globals, TTM
transition rate, ESC adherence) plus a few diagnostic counters
(fallbacks, MISC code usage, graph shape), and prints a compact
comparison table plus summary statistics.

Usage:
    PYTHONPATH=src python -m help_e.eval.matrix_report
    PYTHONPATH=src python -m help_e.eval.matrix_report --profiles P01 P02
    PYTHONPATH=src python -m help_e.eval.matrix_report --json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from typing import Any, Optional

from .judge import extract_misc_codes
from .metrics import (
    esc_per_profile,
    esc_per_profile_from_sessions,
    miti_across_profiles,
    miti_per_profile,
    transition_rate_per_profile,
    transition_rate_across_profiles,
)
from .v6_loader import (
    list_v6_profiles,
    load_v6_run_artifacts,
    load_v6_session_esc,
    load_v6_session_files,
    load_v6_session_miti,
    load_v6_turn_traces,
)


def _count_fallbacks(sessions: list[dict]) -> dict:
    inf_fb = rc_fb = rsp_fb = 0
    total_turns = 0
    for s in sessions:
        for tr in s.get("turn_traces") or []:
            total_turns += 1
            if tr.get("inference", {}).get("_fallback_default"):
                inf_fb += 1
            if tr.get("recompute", {}).get("_fallback_default"):
                rc_fb += 1
            if tr.get("response", {}).get("_fallback_default"):
                rsp_fb += 1
    return {
        "turns": total_turns,
        "inference_fb": inf_fb,
        "recompute_fb": rc_fb,
        "response_fb": rsp_fb,
    }


def _misc_code_histogram(sessions: list[dict]) -> dict[str, int]:
    """Count MISC codes named in each turn's `response.reasoning`."""
    hist: dict[str, int] = {}
    for s in sessions:
        for tr in s.get("turn_traces") or []:
            reasoning = (tr.get("response") or {}).get("reasoning", "")
            for code in extract_misc_codes(reasoning):
                hist[code] = hist.get(code, 0) + 1
    return hist


def _graph_shape(sessions: list[dict]) -> dict:
    """Problems, edges, cooccurrences, attribute-connections summed
    across the session traces.
    """
    problems: set[str] = set()
    cooc = 0
    attr_conn = 0
    for s in sessions:
        for tr in s.get("turn_traces") or []:
            problems.update((tr.get("trace") or {}).get("current_problems") or [])
            cooc += (tr.get("trace") or {}).get("cooc_added") or 0
            attr_conn += (tr.get("trace") or {}).get("attr_conn_added") or 0
    return {
        "n_problems_ever_active": len(problems),
        "cooc_entries_written": cooc,
        "attr_conn_entries_written": attr_conn,
        "attr_conn_ratio": (attr_conn / cooc) if cooc else None,
    }


def per_profile_report(profile_id: str, system: str = "v6") -> Optional[dict]:
    sessions = load_v6_session_files(profile_id, system)
    if not sessions:
        return None
    traces = load_v6_turn_traces(profile_id, system)
    miti_sessions = load_v6_session_miti(profile_id, system)
    # Prefer the new per-session ESC judge files; fall back to the old
    # all-sessions Mind-3 only if no per-session files exist (legacy
    # transcripts that predate the §1.c session-level judge refactor).
    esc_sessions = load_v6_session_esc(profile_id, system)
    if esc_sessions:
        esc_block = esc_per_profile_from_sessions(esc_sessions)
    else:
        run_art = load_v6_run_artifacts(profile_id, system) or {}
        mind3 = run_art.get("mind3_out") or {}
        esc_block = esc_per_profile(mind3)

    out: dict[str, Any] = {
        "profile_id": profile_id,
        "system": system,
        "n_sessions": len(sessions),
        "n_turns": sum(len(s.get("turn_traces") or []) for s in sessions),
        "fallbacks": _count_fallbacks(sessions),
        "graph": _graph_shape(sessions),
        "misc_code_usage": _misc_code_histogram(sessions),
        "MITI": miti_per_profile(miti_sessions),
        "TTM_TRANSITION_RATE": transition_rate_per_profile(traces),
        "ESC": esc_block,
    }
    return out


def aggregate(reports: list[dict]) -> dict:
    """Cross-profile rollup of the three metrics + diagnostics."""
    if not reports:
        return {"n_profiles": 0}

    total_turns = sum(r["n_turns"] for r in reports)
    total_inf_fb = sum(r["fallbacks"]["inference_fb"] for r in reports)
    total_rc_fb = sum(r["fallbacks"]["recompute_fb"] for r in reports)
    total_rsp_fb = sum(r["fallbacks"]["response_fb"] for r in reports)

    miti_aggs = [r["MITI"] for r in reports if r.get("MITI")]
    ttm_aggs = [r["TTM_TRANSITION_RATE"] for r in reports if r.get("TTM_TRANSITION_RATE")]
    esc_aggs = [r["ESC"] for r in reports if r.get("ESC")]

    # Cross-profile MITI.
    miti_summary = miti_across_profiles(miti_aggs)

    # Cross-profile TTM transition rate.
    ttm_summary = transition_rate_across_profiles(ttm_aggs)

    # Cross-profile ESC overall mean.
    esc_overalls = [
        a.get("overall_mean") for a in esc_aggs
        if a.get("overall_mean") is not None
    ]

    summary: dict[str, Any] = {
        "n_profiles": len(reports),
        "total_turns": total_turns,
        "fallback_rates": {
            "inference": total_inf_fb / total_turns if total_turns else 0.0,
            "recompute": total_rc_fb / total_turns if total_turns else 0.0,
            "response": total_rsp_fb / total_turns if total_turns else 0.0,
        },
        "MITI_overall_mean": {
            "mean": miti_summary["overall_mean_mean"],
            "median": miti_summary["overall_mean_median"],
            "per_global_mean": miti_summary["per_global_mean"],
            "n_profiles": miti_summary["n_profiles"],
        },
        "TTM_TRANSITION_RATE": ttm_summary,
        "ESC_overall_mean": {
            "mean": statistics.mean(esc_overalls) if esc_overalls else None,
            "median": statistics.median(esc_overalls) if esc_overalls else None,
            "min": min(esc_overalls) if esc_overalls else None,
            "max": max(esc_overalls) if esc_overalls else None,
            "n_profiles": len(esc_overalls),
        },
    }

    # MISC code histogram across all profiles.
    code_hist: dict[str, int] = {}
    for r in reports:
        for code, n in (r.get("misc_code_usage") or {}).items():
            code_hist[code] = code_hist.get(code, 0) + n
    summary["misc_code_usage"] = dict(sorted(code_hist.items(), key=lambda kv: -kv[1]))

    # Graph-shape distribution.
    attr_conn_ratios = [
        r["graph"]["attr_conn_ratio"] for r in reports
        if r["graph"]["attr_conn_ratio"] is not None
    ]
    if attr_conn_ratios:
        summary["attr_conn_per_cooc"] = {
            "mean": statistics.mean(attr_conn_ratios),
            "median": statistics.median(attr_conn_ratios),
        }
    return summary


def print_table(reports: list[dict]) -> None:
    header = (
        f"{'prof':6} {'turns':>5} {'probs':>5} {'cooc':>4} {'conn':>4} "
        f"{'inf_fb':>6} {'MITI':>5} {'%act':>5} {'TTA':>5} {'ESC':>5}"
    )
    print(header)
    print("-" * len(header))
    for r in reports:
        prof = r["profile_id"]
        turns = r["n_turns"]
        probs = r["graph"]["n_problems_ever_active"]
        cooc = r["graph"]["cooc_entries_written"]
        conn = r["graph"]["attr_conn_entries_written"]
        inf_fb = r["fallbacks"]["inference_fb"]
        miti = (r.get("MITI") or {}).get("overall_mean")
        ttm = r.get("TTM_TRANSITION_RATE") or {}
        pct_action = ttm.get("pct_reached_action")
        tta = ttm.get("mean_turns_to_action")
        esc = (r.get("ESC") or {}).get("overall_mean")

        def _f(v, w=6, prec=2):
            if v is None:
                return "—".rjust(w)
            return f"{v:>{w}.{prec}f}"

        print(
            f"{prof:6} {turns:>5} {probs:>5} {cooc:>4} {conn:>4} "
            f"{inf_fb:>6} {_f(miti, 5, 2)} {_f(pct_action, 5, 2)} "
            f"{_f(tta, 5, 1)} {_f(esc, 5, 2)}"
        )


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="help_e.eval.matrix_report")
    p.add_argument("--profiles", nargs="*",
                   help="restrict to these profile ids (default: all profiles with data on disk)")
    p.add_argument("--system", default="v6",
                   help="which ablation system to report on (v1, v3, or v6). default: v6")
    p.add_argument("--json", action="store_true", help="emit JSON (no pretty-print)")
    args = p.parse_args(argv)

    profiles = args.profiles or list_v6_profiles(args.system)
    if not profiles:
        print(f"no {args.system!r} profiles found on disk", file=sys.stderr)
        return 1

    reports: list[dict] = []
    missing: list[str] = []
    for pid in profiles:
        r = per_profile_report(pid, args.system)
        if r is None:
            missing.append(pid)
            continue
        reports.append(r)

    if args.json:
        print(json.dumps({
            "system": args.system,
            "per_profile": reports,
            "summary": aggregate(reports),
            "missing": missing,
        }, indent=2, default=str))
        return 0

    print(f"\n{'=' * 40}")
    print(f"{args.system} matrix report — {len(reports)} profiles")
    if missing:
        print(f"missing: {missing}")
    print(f"{'=' * 40}\n")
    print_table(reports)
    print()
    agg = aggregate(reports)
    print(f"SUMMARY across {agg['n_profiles']} profiles, {agg['total_turns']} user turns")
    fb = agg["fallback_rates"]
    print(f"  fallback rates: inference={fb['inference']:.3f}, "
          f"recompute={fb['recompute']:.3f}, response={fb['response']:.3f}")

    miti = agg["MITI_overall_mean"]
    if miti.get("mean") is not None:
        print(f"  MITI overall mean (cross-profile): "
              f"mean={miti['mean']:.2f} median={miti['median']:.2f} "
              f"(n={miti['n_profiles']})")
        for g, v in (miti.get("per_global_mean") or {}).items():
            if v is not None:
                print(f"    {g}: {v:.2f}")

    ttm = agg["TTM_TRANSITION_RATE"]
    pct = ttm.get("pct_reached_action") or {}
    if pct.get("mean") is not None:
        print(f"  TTM reached action: mean={pct['mean']:.2f} "
              f"median={pct['median']:.2f}")
    if ttm.get("mean_turns_to_action") is not None:
        print(f"  TTM mean turns to action: {ttm['mean_turns_to_action']:.2f}")
    for k in (
        "mean_turns_to_precontemplation_to_contemplation",
        "mean_turns_to_contemplation_to_preparation",
        "mean_turns_to_preparation_to_action",
    ):
        if ttm.get(k) is not None:
            print(f"  TTM {k}: {ttm[k]:.2f}")

    esc = agg["ESC_overall_mean"]
    if esc.get("mean") is not None:
        print(f"  ESC overall mean: mean={esc['mean']:.2f} "
              f"median={esc['median']:.2f} "
              f"min={esc['min']:.2f} max={esc['max']:.2f}")

    if "attr_conn_per_cooc" in agg:
        v = agg["attr_conn_per_cooc"]
        print(f"  attr-connections per co-occurrence: "
              f"mean={v['mean']:.3f} median={v['median']:.3f}")
    print(f"  MISC code usage: {agg.get('misc_code_usage')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
