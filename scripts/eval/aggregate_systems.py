"""Aggregate ``compare_v3_v7_v8 --json`` across multiple profiles for any set
of systems, with per-system transcript-root overrides.

Usage::

    PYTHONPATH=src python scripts/eval/aggregate_systems.py \\
        --systems v1 v7 v8 \\
        --root v1:output/lightning_v1_70b/transcripts \\
        --root v7:output/lightning_v7_70b/transcripts \\
        --root v8:output/lightning_v8_70b/transcripts \\
        --profiles P01 P03 P05 P07 P09 P11 P13 P15 P17 P19 P21 P23 P25 P27 P29

Drop ``--profiles`` to default to the 15 odd profiles P01..P29.
"""
import argparse
import json
import subprocess
import sys
from statistics import mean

ODD_PROFILES = [f"P{i:02d}" for i in range(1, 30, 2)]


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--systems", nargs="+", required=True,
                    help="System names, e.g. v1 v7 v8")
    ap.add_argument("--root", action="append", default=[],
                    help="SYSTEM:PATH transcripts-root override (repeatable)")
    ap.add_argument("--profiles", nargs="+", default=ODD_PROFILES,
                    help="Profile IDs (default: 15 odd profiles)")
    ap.add_argument("--save", default=None,
                    help="Optional path to dump full aggregated JSON")
    return ap.parse_args()


def main():
    args = parse_args()
    systems = args.systems

    per_profile = {}
    for p in args.profiles:
        cmd = ["/usr/bin/python3", "-m", "help_e.eval.compare_v3_v7_v8",
               "--profile", p, "--systems", *systems, "--json"]
        for r in args.root:
            cmd.extend(["--root", r])
        out = subprocess.run(
            cmd, capture_output=True, text=True,
            env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"},
        )
        if out.returncode != 0:
            print(f"  {p}: FAILED -> {out.stderr[:200]}", file=sys.stderr)
            continue
        per_profile[p] = json.loads(out.stdout)

    miti_globals = ["cultivating_change_talk", "softening_sustain_talk",
                    "partnership", "empathy"]
    esc_dims = ["empathy", "understanding", "helpfulness",
                "autonomy_respect", "non_judgment", "willingness_to_continue"]

    agg = {s: {} for s in systems}
    for p, d in per_profile.items():
        for entry in d["systems"]:
            s = entry["system"]
            if s not in agg:
                continue
            for k in ("n_problems_detected", "n_evidences_extracted_total",
                      "n_evidences_used_total", "n_audits_used_in_response",
                      "n_forward_transitions", "regressions_total",
                      "n_problems_reached_action"):
                agg[s].setdefault(k, []).append(entry.get(k) or 0)
            for k in ("progression_rate", "pct_reached_action",
                      "mean_turns_to_action"):
                v = entry.get(k)
                if v is not None:
                    agg[s].setdefault(k, []).append(v)
            agg[s].setdefault("assigned_overlap", []).append(
                1 if entry.get("assigned_overlap") else 0)
            miti = (entry.get("miti") or {}).get("per_global_mean") or {}
            for g in miti_globals:
                if miti.get(g) is not None:
                    agg[s].setdefault(f"miti.{g}", []).append(miti[g])
            if miti:
                vals = [miti[g] for g in miti_globals if miti.get(g) is not None]
                if vals:
                    agg[s].setdefault("miti.overall", []).append(mean(vals))
            esc = entry.get("esc") or {}
            for dim in esc_dims:
                if esc.get(dim) is not None:
                    agg[s].setdefault(f"esc.{dim}", []).append(esc[dim])
            if esc:
                vals = [esc[d] for d in esc_dims if esc.get(d) is not None]
                if vals:
                    agg[s].setdefault("esc.overall", []).append(mean(vals))

    def fmt(vals, dp=2):
        if not vals:
            return "n/a"
        return f"{mean(vals):.{dp}f}"

    width = 42 + 12 * len(systems)
    print()
    print("=" * width)
    print(f"  Aggregate across {len(per_profile)} profiles  ({' vs '.join(systems)})")
    print("=" * width)
    header = f"{'metric':<42}"
    for s in systems:
        header += f"  {s:>10}"
    print(header)
    print("-" * width)

    def row(label, key, dp=2, scale=1.0):
        line = f"  {label:<40}"
        for s in systems:
            vals = [v * scale for v in agg[s].get(key, [])]
            line += f"  {fmt(vals, dp):>10}"
        print(line)

    print(f"\n  Detection / graph load (mean per profile)")
    row("problems detected", "n_problems_detected", 2)
    row("primary-problem hit rate (%)", "assigned_overlap", 1, scale=100)
    row("evidences extracted (attr+conn)", "n_evidences_extracted_total", 1)
    row("evidences used (response)", "n_evidences_used_total", 1)
    row("audits used by response", "n_audits_used_in_response", 1)
    row("forward TTM transitions", "n_forward_transitions", 2)
    row("regressions", "regressions_total", 2)
    row("problems reached action", "n_problems_reached_action", 2)
    row("progression rate (mean of non-null)", "progression_rate", 3)

    print(f"\n  MITI 4-globals (mean across profiles)")
    for g in miti_globals:
        row(f"miti.{g}", f"miti.{g}")
    row("miti.OVERALL", "miti.overall", 3)

    print(f"\n  ESC 6-dim (mean across profiles)")
    for d in esc_dims:
        row(f"esc.{d}", f"esc.{d}")
    row("esc.OVERALL", "esc.overall", 3)

    if args.save:
        with open(args.save, "w") as f:
            json.dump({"per_profile": per_profile, "agg": agg}, f,
                      indent=2, default=str)
        print(f"\n[saved {args.save}]")


if __name__ == "__main__":
    main()
