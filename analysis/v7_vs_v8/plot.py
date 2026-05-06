"""Generate the v7 vs v8 comparison plot set, plus the P18 three-way
comparison plot. Reads output/analysis_v7_vs_v8/data.json and writes
PNGs into the same directory.

Run:
    PYTHONPATH=src python3 output/analysis_v7_vs_v8/plot.py
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


OUT = Path(__file__).resolve().parent
DATA = json.loads((OUT / "data.json").read_text())

# Colors match the analysis_2026_04_30 palette where possible (v6 green
# becomes the v7/v8 family base; v8 gets a warmer accent).
SYS_COLORS = {
    "v3": "#9aa0a6",   # gray (older baseline)
    "v7": "#34a853",   # green (graph-based v6/v7 family)
    "v8": "#ea4335",   # red (RAG variant)
}


def _bar_label(ax, bars, fmt="{:.2f}", offset=0.02):
    for b in bars:
        h = b.get_height()
        if h is None or np.isnan(h):
            continue
        ax.text(b.get_x() + b.get_width() / 2, h + offset, fmt.format(h),
                ha="center", va="bottom", fontsize=9)


# ---------------------------------------------------------------------
# Fig 1 — MITI 4-globals + overall (v7 vs v8, 15-profile aggregate)
# ---------------------------------------------------------------------
def fig1_miti_globals():
    agg = DATA["matrix_70b"]["aggregated"]
    metrics = ("cultivating_change_talk", "softening_sustain_talk",
               "partnership", "empathy", "overall_mean")
    titles = ("Cultivating\nchange talk", "Softening\nsustain talk",
              "Partnership", "Empathy", "Overall mean")
    x = np.arange(len(metrics))
    w = 0.38
    fig, ax = plt.subplots(figsize=(10, 4.5))
    v7 = [agg["v7"]["miti"][m] for m in metrics]
    v8 = [agg["v8"]["miti"][m] for m in metrics]
    b1 = ax.bar(x - w/2, v7, w, label="v7", color=SYS_COLORS["v7"])
    b2 = ax.bar(x + w/2, v8, w, label="v8", color=SYS_COLORS["v8"])
    _bar_label(ax, b1, offset=0.05)
    _bar_label(ax, b2, offset=0.05)
    ax.set_xticks(x)
    ax.set_xticklabels(titles)
    ax.set_ylabel("Score (1 = lowest, 5 = highest)")
    ax.set_ylim(0, 5.6)
    ax.set_title("MITI 4.2 globals — v7 vs v8 (mean across 15 profiles, gpt-oss-70b)")
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    ax.legend(loc="lower right")
    plt.tight_layout()
    plt.savefig(OUT / "fig1_miti_globals.png", dpi=130, bbox_inches="tight")
    plt.close()


# ---------------------------------------------------------------------
# Fig 2 — ESC 6-dim + mean
# ---------------------------------------------------------------------
def fig2_esc_dims():
    agg = DATA["matrix_70b"]["aggregated"]
    dims = ("empathy", "understanding", "helpfulness", "autonomy_respect",
            "non_judgment", "willingness_to_continue", "mean")
    titles = ("Empathy", "Understanding", "Helpfulness", "Autonomy\nrespect",
              "Non-judgment", "Willingness\nto continue", "Mean")
    x = np.arange(len(dims))
    w = 0.38
    fig, ax = plt.subplots(figsize=(11, 4.5))
    v7 = [agg["v7"]["esc"][d] for d in dims]
    v8 = [agg["v8"]["esc"][d] for d in dims]
    b1 = ax.bar(x - w/2, v7, w, label="v7", color=SYS_COLORS["v7"])
    b2 = ax.bar(x + w/2, v8, w, label="v8", color=SYS_COLORS["v8"])
    _bar_label(ax, b1, offset=0.05)
    _bar_label(ax, b2, offset=0.05)
    ax.set_xticks(x); ax.set_xticklabels(titles)
    ax.set_ylabel("Score (1 = lowest, 5 = highest)")
    ax.set_ylim(0, 5.6)
    ax.set_title("ESC 6-dimension judge — v7 vs v8 (mean across 15 profiles, gpt-oss-70b)")
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    ax.legend(loc="lower right")
    plt.tight_layout()
    plt.savefig(OUT / "fig2_esc_dims.png", dpi=130, bbox_inches="tight")
    plt.close()


# ---------------------------------------------------------------------
# Fig 3 — TTM progression (forward transitions, progression rate, action)
# ---------------------------------------------------------------------
def fig3_ttm_progression():
    agg = DATA["matrix_70b"]["aggregated"]
    metrics = [
        ("forward_transitions", "Forward transitions\n(per profile)", "{:.2f}", 0.5),
        ("progression_rate",    "Progression rate\n(per problem-session)", "{:.2f}", 0.05),
        ("assigned_overlap_rate","Assigned-primary\ndetected (rate)", "{:.2f}", 0.05),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2))
    for ax, (key, title, fmt, off) in zip(axes, metrics):
        v7 = agg["v7"].get(key); v8 = agg["v8"].get(key)
        bars = ax.bar(["v7", "v8"], [v7, v8],
                      color=[SYS_COLORS["v7"], SYS_COLORS["v8"]])
        _bar_label(ax, bars, fmt=fmt, offset=off)
        ax.set_title(title)
        ax.grid(axis="y", linestyle=":", alpha=0.5)
        ax.set_ylim(0, max(filter(None, [v7, v8])) * 1.25)
    fig.suptitle("TTM progression & problem-detection signals (15 profiles, gpt-oss-70b)",
                 fontsize=11)
    plt.tight_layout()
    plt.savefig(OUT / "fig3_ttm_progression.png", dpi=130, bbox_inches="tight")
    plt.close()


# ---------------------------------------------------------------------
# Fig 4 — Evidence behavior (audit corpus size, audits used in response)
# ---------------------------------------------------------------------
def fig4_evidence_behavior():
    agg = DATA["matrix_70b"]["aggregated"]
    metrics = [
        ("n_attr_audits",   "Attribute audit\nentries (mean)"),
        ("n_conn_entries",  "Connection\nentries (mean)"),
        ("n_audits_used",   "Audits cited in\nresponse (mean)"),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2))
    for ax, (key, title) in zip(axes, metrics):
        v7 = agg["v7"].get(key); v8 = agg["v8"].get(key)
        bars = ax.bar(["v7", "v8"], [v7, v8],
                      color=[SYS_COLORS["v7"], SYS_COLORS["v8"]])
        _bar_label(ax, bars, fmt="{:.1f}", offset=max(v7, v8) * 0.02)
        ax.set_title(title)
        ax.grid(axis="y", linestyle=":", alpha=0.5)
        ax.set_ylim(0, max(v7, v8) * 1.2)
    fig.suptitle("Evidence accumulation and citation behavior (15 profiles, gpt-oss-70b)",
                 fontsize=11)
    plt.tight_layout()
    plt.savefig(OUT / "fig4_evidence_behavior.png", dpi=130, bbox_inches="tight")
    plt.close()


# ---------------------------------------------------------------------
# Fig 5 — Per-profile MITI overall (paired bars, 15 profiles)
# ---------------------------------------------------------------------
def fig5_per_profile_miti():
    pp_v7 = {r["profile"]: r["miti_overall"] for r in DATA["matrix_70b"]["per_profile"]["v7"]}
    pp_v8 = {r["profile"]: r["miti_overall"] for r in DATA["matrix_70b"]["per_profile"]["v8"]}
    profiles = sorted(pp_v7.keys(), key=lambda p: int(p[1:]))
    x = np.arange(len(profiles)); w = 0.38
    fig, ax = plt.subplots(figsize=(13, 4.5))
    v7 = [pp_v7[p] for p in profiles]
    v8 = [pp_v8[p] for p in profiles]
    ax.bar(x - w/2, v7, w, label="v7", color=SYS_COLORS["v7"])
    ax.bar(x + w/2, v8, w, label="v8", color=SYS_COLORS["v8"])
    ax.set_xticks(x); ax.set_xticklabels(profiles)
    ax.set_ylabel("MITI overall mean (per-profile)")
    ax.set_ylim(0, 5.6)
    ax.set_title("MITI overall mean — per profile (v7 vs v8, gpt-oss-70b, 3 sessions × 70 turns)")
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    ax.legend(loc="lower right")
    plt.tight_layout()
    plt.savefig(OUT / "fig5_per_profile_miti.png", dpi=130, bbox_inches="tight")
    plt.close()


# ---------------------------------------------------------------------
# Fig 6 — Per-profile ESC mean (paired bars, 15 profiles)
# ---------------------------------------------------------------------
def fig6_per_profile_esc():
    pp_v7 = {r["profile"]: r["esc_mean"] for r in DATA["matrix_70b"]["per_profile"]["v7"]}
    pp_v8 = {r["profile"]: r["esc_mean"] for r in DATA["matrix_70b"]["per_profile"]["v8"]}
    profiles = sorted(pp_v7.keys(), key=lambda p: int(p[1:]))
    x = np.arange(len(profiles)); w = 0.38
    fig, ax = plt.subplots(figsize=(13, 4.5))
    v7 = [pp_v7[p] for p in profiles]
    v8 = [pp_v8[p] for p in profiles]
    ax.bar(x - w/2, v7, w, label="v7", color=SYS_COLORS["v7"])
    ax.bar(x + w/2, v8, w, label="v8", color=SYS_COLORS["v8"])
    ax.set_xticks(x); ax.set_xticklabels(profiles)
    ax.set_ylabel("ESC mean (per-profile)")
    ax.set_ylim(0, 5.6)
    ax.set_title("ESC mean — per profile (v7 vs v8, gpt-oss-70b, 3 sessions × 70 turns)")
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    ax.legend(loc="lower right")
    plt.tight_layout()
    plt.savefig(OUT / "fig6_per_profile_esc.png", dpi=130, bbox_inches="tight")
    plt.close()


# ---------------------------------------------------------------------
# Fig 7 — Per-profile progression rate (paired bars)
# ---------------------------------------------------------------------
def fig7_per_profile_progression():
    pp_v7 = {r["profile"]: r["progression_rate"] for r in DATA["matrix_70b"]["per_profile"]["v7"]}
    pp_v8 = {r["profile"]: r["progression_rate"] for r in DATA["matrix_70b"]["per_profile"]["v8"]}
    profiles = sorted(pp_v7.keys(), key=lambda p: int(p[1:]))
    x = np.arange(len(profiles)); w = 0.38
    fig, ax = plt.subplots(figsize=(13, 4.5))
    v7 = [pp_v7[p] or 0 for p in profiles]
    v8 = [pp_v8[p] or 0 for p in profiles]
    ax.bar(x - w/2, v7, w, label="v7", color=SYS_COLORS["v7"])
    ax.bar(x + w/2, v8, w, label="v8", color=SYS_COLORS["v8"])
    ax.set_xticks(x); ax.set_xticklabels(profiles)
    ax.set_ylabel("TTM progression rate")
    ymax = max(v7 + v8) * 1.2
    ax.set_ylim(0, ymax)
    ax.set_title("TTM progression rate — per profile (v7 vs v8, gpt-oss-70b)")
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    ax.legend(loc="upper right")
    plt.tight_layout()
    plt.savefig(OUT / "fig7_per_profile_progression.png", dpi=130, bbox_inches="tight")
    plt.close()


# ---------------------------------------------------------------------
# Fig 8 — P18 three-way (v3 vs v7 vs v8 @ 120b) — MITI + ESC overlay
# ---------------------------------------------------------------------
def fig8_p18_three_way():
    p18 = DATA["p18_120b"]
    miti_globals = ("cultivating_change_talk", "softening_sustain_talk",
                    "partnership", "empathy", "overall_mean")
    miti_titles = ("Cultivating\nchange talk", "Softening\nsustain talk",
                   "Partnership", "Empathy", "Overall mean")
    esc_dims = ("empathy", "understanding", "helpfulness", "autonomy_respect",
                "non_judgment", "willingness_to_continue", "mean")
    esc_titles = ("Empathy", "Understanding", "Helpfulness", "Autonomy",
                  "Non-judgment", "Willingness", "Mean")

    fig, axes = plt.subplots(2, 1, figsize=(12, 8))
    # MITI
    x = np.arange(len(miti_globals)); w = 0.27
    for off, sysn in zip((-1, 0, 1), ("v3", "v7", "v8")):
        miti = (p18[sysn] or {}).get("miti", {})
        if not miti:
            vals = [None] * len(miti_globals)
        else:
            vals = []
            for g in miti_globals:
                if g == "overall_mean":
                    vals.append(miti.get("overall_mean"))
                else:
                    vals.append((miti.get("per_global_mean") or {}).get(g))
        bars = axes[0].bar(x + off * w, [v if v is not None else 0 for v in vals],
                           w, label=sysn, color=SYS_COLORS[sysn])
        for b, v in zip(bars, vals):
            if v is None: continue
            axes[0].text(b.get_x() + b.get_width()/2, v + 0.05,
                         f"{v:.2f}", ha="center", fontsize=8)
    axes[0].set_xticks(x); axes[0].set_xticklabels(miti_titles)
    axes[0].set_ylabel("MITI score")
    axes[0].set_ylim(0, 5.6)
    axes[0].set_title("P18 three-way — MITI 4 globals (gpt-oss-120b)")
    axes[0].legend(loc="lower right"); axes[0].grid(axis="y", linestyle=":", alpha=0.5)

    # ESC
    x = np.arange(len(esc_dims))
    for off, sysn in zip((-1, 0, 1), ("v3", "v7", "v8")):
        esc = (p18[sysn] or {}).get("esc", {})
        vals = [esc.get(d) for d in esc_dims] if esc else [None]*len(esc_dims)
        bars = axes[1].bar(x + off * w, [v if v is not None else 0 for v in vals],
                           w, label=sysn, color=SYS_COLORS[sysn])
        for b, v in zip(bars, vals):
            if v is None: continue
            axes[1].text(b.get_x() + b.get_width()/2, v + 0.05,
                         f"{v:.2f}", ha="center", fontsize=8)
    axes[1].set_xticks(x); axes[1].set_xticklabels(esc_titles)
    axes[1].set_ylabel("ESC score")
    axes[1].set_ylim(0, 5.6)
    axes[1].set_title("P18 three-way — ESC 6 dimensions (gpt-oss-120b)")
    axes[1].legend(loc="lower right"); axes[1].grid(axis="y", linestyle=":", alpha=0.5)

    plt.tight_layout()
    plt.savefig(OUT / "fig8_p18_three_way.png", dpi=130, bbox_inches="tight")
    plt.close()


def main():
    fig1_miti_globals()
    fig2_esc_dims()
    fig3_ttm_progression()
    fig4_evidence_behavior()
    fig5_per_profile_miti()
    fig6_per_profile_esc()
    fig7_per_profile_progression()
    fig8_p18_three_way()
    print(f"wrote 8 figures to {OUT}/")


if __name__ == "__main__":
    main()
