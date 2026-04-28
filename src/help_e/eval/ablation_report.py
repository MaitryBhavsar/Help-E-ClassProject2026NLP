"""Cross-system ablation report.

Loads v1, v3, and v6 artifacts for every profile that has all three on
disk and emits a side-by-side table on the three v6-redesign metrics:
  - MITI 4.2 globals (cultivating_change_talk, softening_sustain_talk,
                      partnership, empathy)
  - TTM transition rate (% reached action, mean turns to action,
                         per-transition mean turns)
  - ESC adherence (Mind-3 6-dim mean)

Per-system per-profile metrics come from `eval.matrix_report`. The
cross-system rollup uses the multi-profile aggregators in
`eval.metrics`. Includes paired Wilcoxon between v1↔v6 and v3↔v6 on
the headline scalars.

Usage:
    PYTHONPATH=src python -m help_e.eval.ablation_report
    PYTHONPATH=src python -m help_e.eval.ablation_report --json
    PYTHONPATH=src python -m help_e.eval.ablation_report --systems v1 v6
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from typing import Any, Optional

from .matrix_report import per_profile_report
from .metrics import (
    esc_across_profiles,
    miti_across_profiles,
    transition_rate_across_profiles,
    wilcoxon_signed_rank,
)
from .v6_loader import list_v6_profiles


SYSTEMS_DEFAULT = ("v1", "v3", "v6")


def gather(systems: list[str]) -> dict[str, list[dict]]:
    """Return per-profile reports keyed by system. Only includes profiles
    that have data for ALL requested systems (paired comparison).
    """
    profile_sets = {s: set(list_v6_profiles(s)) for s in systems}
    common = set.intersection(*profile_sets.values()) if profile_sets else set()
    common_sorted = sorted(common)

    out: dict[str, list[dict]] = {}
    for s in systems:
        out[s] = []
        for pid in common_sorted:
            r = per_profile_report(pid, s)
            if r is not None:
                out[s].append(r)
    return out


def summarize_system(reports: list[dict]) -> dict:
    """Aggregate a single system's per-profile reports into headline
    scalars + per-metric rollups.
    """
    miti_aggs = [r["MITI"] for r in reports if r.get("MITI")]
    ttm_aggs = [r["TTM_TRANSITION_RATE"] for r in reports if r.get("TTM_TRANSITION_RATE")]
    esc_aggs = [r["ESC"] for r in reports if r.get("ESC")]

    miti = miti_across_profiles(miti_aggs)
    ttm = transition_rate_across_profiles(ttm_aggs)
    esc = esc_across_profiles(esc_aggs)

    # Per-profile headline scalars (for paired tests).
    miti_per_profile = [
        a.get("overall_mean") for a in miti_aggs if a.get("overall_mean") is not None
    ]
    pct_action_per_profile = [
        a.get("pct_reached_action") for a in ttm_aggs
        if a.get("pct_reached_action") is not None
    ]
    esc_per_profile = [
        a.get("overall_mean") for a in esc_aggs if a.get("overall_mean") is not None
    ]

    total_turns = sum(r["n_turns"] for r in reports)
    fb = {
        "inference": sum(r["fallbacks"]["inference_fb"] for r in reports),
        "recompute": sum(r["fallbacks"]["recompute_fb"] for r in reports),
        "response":  sum(r["fallbacks"]["response_fb"]  for r in reports),
    }
    fb_rates = {k: (v / total_turns) if total_turns else 0.0 for k, v in fb.items()}

    return {
        "n_profiles": len(reports),
        "n_turns_total": total_turns,
        "fallback_rates": fb_rates,
        "MITI_overall_mean": miti["overall_mean_mean"],
        "MITI_overall_median": miti["overall_mean_median"],
        "MITI_per_global_mean": miti["per_global_mean"],
        "MITI_per_profile_overalls": miti_per_profile,
        "TTM_pct_reached_action_mean": (ttm.get("pct_reached_action") or {}).get("mean"),
        "TTM_pct_reached_action_median": (ttm.get("pct_reached_action") or {}).get("median"),
        "TTM_mean_turns_to_action": ttm.get("mean_turns_to_action"),
        "TTM_per_transition": {
            k: ttm.get(f"mean_turns_to_{k}") for k in (
                "precontemplation_to_contemplation",
                "contemplation_to_preparation",
                "preparation_to_action",
            )
        },
        "TTM_pct_action_per_profile": pct_action_per_profile,
        "ESC_overall_mean": esc["overall_mean_mean"],
        "ESC_overall_median": esc["overall_mean_median"],
        "ESC_per_dim_mean": esc["per_dim_mean"],
        "ESC_per_profile_overalls": esc_per_profile,
    }


def paired_tests(by_system: dict[str, dict], baseline: str = "v6") -> dict:
    """Paired Wilcoxon signed-rank for each non-baseline system vs the
    baseline on three headline scalars (MITI overall, TTM pct reached
    action, ESC overall). Each paired list pulls per-profile scalars in
    the SAME profile order, so positions correspond.
    """
    out: dict[str, dict] = {}
    if baseline not in by_system:
        return out
    base = by_system[baseline]
    for sys_name, agg in by_system.items():
        if sys_name == baseline:
            continue
        comp: dict[str, Any] = {}
        for metric_key in (
            "MITI_per_profile_overalls",
            "TTM_pct_action_per_profile",
            "ESC_per_profile_overalls",
        ):
            a = agg.get(metric_key) or []
            b = base.get(metric_key) or []
            if len(a) == len(b) and a:
                comp[metric_key] = wilcoxon_signed_rank(a, b)
            else:
                comp[metric_key] = {
                    "_unavailable": True,
                    "reason": f"len(a)={len(a)} != len(b)={len(b)}",
                }
        out[f"{sys_name}_vs_{baseline}"] = comp
    return out


def print_table(by_system: dict[str, dict]) -> None:
    systems = list(by_system.keys())
    print(f"{'metric':<42} " + " ".join(f"{s:>10}" for s in systems))
    print("-" * (42 + 11 * len(systems)))

    rows = [
        ("n_profiles",                      lambda a: a["n_profiles"]),
        ("n_turns_total",                   lambda a: a["n_turns_total"]),
        ("fallback_rates.inference",        lambda a: a["fallback_rates"]["inference"]),
        ("fallback_rates.recompute",        lambda a: a["fallback_rates"]["recompute"]),
        ("fallback_rates.response",         lambda a: a["fallback_rates"]["response"]),
        ("MITI overall mean",               lambda a: a["MITI_overall_mean"]),
        ("MITI overall median",             lambda a: a["MITI_overall_median"]),
        ("MITI cultivating_change_talk",    lambda a: a["MITI_per_global_mean"]["cultivating_change_talk"]),
        ("MITI softening_sustain_talk",     lambda a: a["MITI_per_global_mean"]["softening_sustain_talk"]),
        ("MITI partnership",                lambda a: a["MITI_per_global_mean"]["partnership"]),
        ("MITI empathy",                    lambda a: a["MITI_per_global_mean"]["empathy"]),
        ("TTM pct_reached_action mean",     lambda a: a["TTM_pct_reached_action_mean"]),
        ("TTM mean_turns_to_action",        lambda a: a["TTM_mean_turns_to_action"]),
        ("TTM pre→contempl mean turns",     lambda a: a["TTM_per_transition"]["precontemplation_to_contemplation"]),
        ("TTM contempl→prep mean turns",    lambda a: a["TTM_per_transition"]["contemplation_to_preparation"]),
        ("TTM prep→action mean turns",      lambda a: a["TTM_per_transition"]["preparation_to_action"]),
        ("ESC overall mean",                lambda a: a["ESC_overall_mean"]),
    ]
    for name, getter in rows:
        cells = []
        for s in systems:
            try:
                v = getter(by_system[s])
            except KeyError:
                v = None
            if v is None:
                cells.append(f"{'—':>10}")
            elif isinstance(v, float):
                cells.append(f"{v:>10.3f}")
            else:
                cells.append(f"{v:>10}")
        print(f"{name:<42} " + " ".join(cells))


def print_paired_tests(tests: dict) -> None:
    if not tests:
        return
    print(f"\n{'=' * 60}")
    print("PAIRED WILCOXON SIGNED-RANK (per-profile scalars vs v6)")
    print(f"{'=' * 60}")
    for pair_label, comp in tests.items():
        print(f"\n  {pair_label}:")
        for metric, res in comp.items():
            if res.get("_unavailable"):
                print(f"    {metric}: unavailable ({res.get('reason')})")
                continue
            stat = res.get("statistic")
            p = res.get("pvalue")
            n = res.get("n")
            md = res.get("median_diff")
            print(f"    {metric}: stat={stat:.3f} p={p:.4f} n={n} median_diff={md:.3f}")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="help_e.eval.ablation_report")
    p.add_argument("--systems", nargs="+", default=list(SYSTEMS_DEFAULT),
                   help="ablation systems to compare (default: v1 v3 v6)")
    p.add_argument("--baseline", default="v6",
                   help="paired tests are computed as <other> vs <baseline> (default v6)")
    p.add_argument("--json", action="store_true", help="emit JSON")
    args = p.parse_args(argv)

    by_profile = gather(args.systems)
    by_system = {s: summarize_system(by_profile[s]) for s in args.systems}
    tests = paired_tests(by_system, baseline=args.baseline)

    if args.json:
        print(json.dumps({
            "by_system": by_system,
            "paired_tests": tests,
        }, indent=2, default=str))
        return 0

    common_n = next(iter(by_system.values()))["n_profiles"] if by_system else 0
    print(f"\n{'=' * 60}")
    print(f"ABLATION REPORT — systems: {args.systems}, paired profiles: {common_n}")
    print(f"{'=' * 60}\n")
    print_table(by_system)
    print_paired_tests(tests)
    return 0


if __name__ == "__main__":
    sys.exit(main())
