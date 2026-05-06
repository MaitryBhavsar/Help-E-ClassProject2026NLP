"""Generate every asset needed for the HELP-E v7/v8 presentation.

Outputs all artifacts under ``docs/presentation_assets/``:

  - results_per_session.csv     per-(system, profile, session) MITI + ESC means
  - results_per_profile.csv     per-(system, profile) means
  - results_per_system.csv      per-system aggregate means + std
  - results_summary.md          markdown table of per-system summary
  - results_bar_chart.png       MITI vs ESC bar chart per system
  - results_per_profile_heatmap.png  per-profile ESC scores per system
  - pipeline_diagram.png        7-phase pipeline with v7/v8 fork
  - architecture_matrix.png     v1/v3/CAMI/v7/v8 capability matrix
  - retrieval_comparison.png    side-by-side v7 graph-walk vs v8 dense RAG

Reads judge artifacts from ``output/<sysroot>/transcripts/<profile>/<system>/``.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.lines import Line2D
import numpy as np


REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "presentation_assets"
OUT.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Data sources — system to (root_dir, system_label_in_path)
# ---------------------------------------------------------------------------
SYSTEM_ROOTS = {
    "v1":   ("output/lightning_v1_70b/transcripts", "v1"),
    "v3":   ("output/lightning_v3_70b/transcripts", "v3"),
    "v7":   ("output/lightning_v7_70b/transcripts", "v7"),
    "v8":   ("output/lightning_v8_70b/transcripts", "v8"),
    "cami": ("output/local_cami_11436_70b/transcripts", "cami"),
}

SYSTEM_COLORS = {
    "v1":   "#9CA3AF",   # grey
    "v3":   "#94A3B8",   # slate-grey
    "cami": "#A78BFA",   # purple
    "v7":   "#2563EB",   # blue
    "v8":   "#EA580C",   # orange
}


def load_judge_files() -> tuple[list[dict], list[dict]]:
    """Walk every system root and return per-session MITI + ESC records."""
    miti_rows: list[dict] = []
    esc_rows: list[dict] = []
    for system, (root_rel, sys_label) in SYSTEM_ROOTS.items():
        root = REPO / root_rel
        if not root.exists():
            continue
        for prof_dir in sorted(root.iterdir()):
            if not prof_dir.is_dir():
                continue
            sys_dir = prof_dir / sys_label
            if not sys_dir.exists():
                continue
            for jf in sorted(sys_dir.glob("miti_judge_*.json")):
                try:
                    d = json.loads(jf.read_text())
                    globals_ = d.get("globals") or []
                    for g in globals_:
                        miti_rows.append({
                            "system": system,
                            "profile": prof_dir.name,
                            "session_file": jf.name,
                            "global": g.get("name"),
                            "score": float(g.get("score", 0)),
                        })
                except Exception:
                    pass
            for jf in sorted(sys_dir.glob("esc_judge_*.json")):
                try:
                    d = json.loads(jf.read_text())
                    dims = d.get("dimensions") or []
                    for dim in dims:
                        esc_rows.append({
                            "system": system,
                            "profile": prof_dir.name,
                            "session_file": jf.name,
                            "dimension": dim.get("name"),
                            "score": float(dim.get("score", 0)),
                        })
                except Exception:
                    pass
    return miti_rows, esc_rows


def aggregate(miti_rows: list[dict], esc_rows: list[dict]) -> dict:
    """Compute per-(system, profile, session), per-(system, profile),
    and per-system aggregates.
    """
    # Per (system, profile, session) means
    per_session_miti: dict[tuple, list[float]] = defaultdict(list)
    per_session_esc: dict[tuple, list[float]] = defaultdict(list)
    for r in miti_rows:
        per_session_miti[(r["system"], r["profile"], r["session_file"])].append(r["score"])
    for r in esc_rows:
        per_session_esc[(r["system"], r["profile"], r["session_file"])].append(r["score"])

    sess_records = []
    keys = sorted(set(per_session_miti) | set(per_session_esc))
    for k in keys:
        miti_scores = per_session_miti.get(k, [])
        esc_scores = per_session_esc.get(k, [])
        sess_records.append({
            "system": k[0],
            "profile": k[1],
            "session_file": k[2],
            "miti_mean": mean(miti_scores) if miti_scores else None,
            "esc_mean": mean(esc_scores) if esc_scores else None,
            "n_miti": len(miti_scores),
            "n_esc": len(esc_scores),
        })

    # Per (system, profile)
    per_profile_miti: dict[tuple, list[float]] = defaultdict(list)
    per_profile_esc: dict[tuple, list[float]] = defaultdict(list)
    for r in sess_records:
        if r["miti_mean"] is not None:
            per_profile_miti[(r["system"], r["profile"])].append(r["miti_mean"])
        if r["esc_mean"] is not None:
            per_profile_esc[(r["system"], r["profile"])].append(r["esc_mean"])

    prof_records = []
    keys = sorted(set(per_profile_miti) | set(per_profile_esc))
    for k in keys:
        miti = per_profile_miti.get(k, [])
        esc = per_profile_esc.get(k, [])
        prof_records.append({
            "system": k[0],
            "profile": k[1],
            "miti_mean": mean(miti) if miti else None,
            "esc_mean": mean(esc) if esc else None,
            "n_sessions": max(len(miti), len(esc)),
        })

    # Per system
    per_system_miti: dict[str, list[float]] = defaultdict(list)
    per_system_esc: dict[str, list[float]] = defaultdict(list)
    sys_n_profiles: dict[str, set] = defaultdict(set)
    sys_n_sessions: dict[str, int] = defaultdict(int)
    for r in prof_records:
        if r["miti_mean"] is not None:
            per_system_miti[r["system"]].append(r["miti_mean"])
        if r["esc_mean"] is not None:
            per_system_esc[r["system"]].append(r["esc_mean"])
        sys_n_profiles[r["system"]].add(r["profile"])
        sys_n_sessions[r["system"]] += r["n_sessions"]

    sys_records = []
    for sys in SYSTEM_ROOTS:
        miti = per_system_miti.get(sys, [])
        esc = per_system_esc.get(sys, [])
        sys_records.append({
            "system": sys,
            "n_profiles": len(sys_n_profiles.get(sys, set())),
            "n_sessions": sys_n_sessions.get(sys, 0),
            "miti_mean": mean(miti) if miti else None,
            "miti_std": stdev(miti) if len(miti) > 1 else None,
            "esc_mean": mean(esc) if esc else None,
            "esc_std": stdev(esc) if len(esc) > 1 else None,
        })

    return {
        "per_session": sess_records,
        "per_profile": prof_records,
        "per_system": sys_records,
        "per_profile_esc_dict": dict(per_profile_esc),
        "per_profile_miti_dict": dict(per_profile_miti),
    }


# ---------------------------------------------------------------------------
# CSV writers
# ---------------------------------------------------------------------------
def write_csv(rows: list[dict], path: Path):
    if not rows:
        path.write_text("(no rows)\n")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_results_summary_md(sys_records: list[dict], path: Path):
    lines = [
        "# Results Summary — Aggregate per System",
        "",
        "*Means computed per (system, profile, session) → averaged per profile → averaged per system. Std is across-profile std of profile-level means.*",
        "",
        "| System | # Profiles | # Sessions | MITI mean (1–5) | MITI std | ESC mean (1–5) | ESC std |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    label_map = {"v1": "v1 (baseline)", "v3": "v3 (graph, no HBM)", "cami": "CAMI (external)",
                 "v7": "**v7 (graph-walk)**", "v8": "**v8 (dense RAG)**"}
    for r in sys_records:
        sys = r["system"]
        miti_mean = f"{r['miti_mean']:.2f}" if r["miti_mean"] is not None else "—"
        miti_std = f"{r['miti_std']:.2f}" if r["miti_std"] is not None else "—"
        esc_mean = f"{r['esc_mean']:.2f}" if r["esc_mean"] is not None else "—"
        esc_std = f"{r['esc_std']:.2f}" if r["esc_std"] is not None else "—"
        lines.append(
            f"| {label_map.get(sys, sys)} | {r['n_profiles']} | {r['n_sessions']} | {miti_mean} | {miti_std} | {esc_mean} | {esc_std} |"
        )
    lines.extend(["", "*Generated by `scripts/build_presentation_assets.py`*"])
    path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Bar chart: MITI + ESC per system
# ---------------------------------------------------------------------------
def make_bar_chart(sys_records: list[dict], path: Path):
    systems = ["v1", "v3", "cami", "v7", "v8"]
    miti = [next((r["miti_mean"] for r in sys_records if r["system"] == s), None) for s in systems]
    esc = [next((r["esc_mean"] for r in sys_records if r["system"] == s), None) for s in systems]
    miti_std = [next((r["miti_std"] for r in sys_records if r["system"] == s), None) for s in systems]
    esc_std = [next((r["esc_std"] for r in sys_records if r["system"] == s), None) for s in systems]
    n_p = [next((r["n_profiles"] for r in sys_records if r["system"] == s), 0) for s in systems]

    label_map = {"v1": "v1\nbaseline", "v3": "v3\ngraph,\nno HBM", "cami": "CAMI\nexternal",
                 "v7": "v7\ngraph-walk", "v8": "v8\ndense RAG"}
    labels = [f"{label_map[s]}\n(n={n_p[i]})" for i, s in enumerate(systems)]

    x = np.arange(len(systems))
    width = 0.36
    fig, ax = plt.subplots(figsize=(11, 6.2))
    miti_vals = [v if v is not None else 0 for v in miti]
    esc_vals = [v if v is not None else 0 for v in esc]
    miti_errs = [v if v is not None else 0 for v in miti_std]
    esc_errs = [v if v is not None else 0 for v in esc_std]

    bars1 = ax.bar(x - width/2, miti_vals, width,
                   yerr=miti_errs, capsize=4,
                   label="MITI overall mean (technique)",
                   color="#94A3B8", edgecolor="#475569")
    bars2 = ax.bar(x + width/2, esc_vals, width,
                   yerr=esc_errs, capsize=4,
                   label="ESC overall mean (experience)",
                   color=[SYSTEM_COLORS[s] for s in systems], edgecolor="#1F2937")

    # Annotate values on top of bars; offset above error bar
    for i, (m, e, me, ee) in enumerate(zip(miti_vals, esc_vals, miti_errs, esc_errs)):
        if m > 0:
            ax.text(x[i] - width/2, m + (me or 0) + 0.10, f"{m:.2f}",
                    ha="center", va="bottom",
                    fontsize=10, color="#475569")
        if e > 0:
            ax.text(x[i] + width/2, e + (ee or 0) + 0.10, f"{e:.2f}",
                    ha="center", va="bottom",
                    fontsize=11, fontweight="bold", color="#111827")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=10)
    ax.set_ylabel("Judge score (1–5)", fontsize=12)
    ax.set_title("HELP-E systems — MITI (technique) vs ESC (experience) on full odd-profile matrix\n"
                 "Means across profile-level means; error bars = std across profiles",
                 fontsize=13, pad=16)
    ax.set_ylim(0, 6.0)
    ax.set_yticks([1, 2, 3, 4, 5])
    ax.axhline(3.0, color="#9CA3AF", linestyle=":", linewidth=0.8, alpha=0.5)
    # Move legend to bottom-right OUTSIDE the plot area to avoid overlap
    ax.legend(loc="lower right", fontsize=10, frameon=True,
              facecolor="white", edgecolor="#9CA3AF")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Heatmap: per-profile ESC across systems (shows variance)
# ---------------------------------------------------------------------------
def make_per_profile_heatmap(prof_records: list[dict], path: Path):
    systems = ["v1", "v3", "cami", "v7", "v8"]
    profiles_set = sorted({r["profile"] for r in prof_records})
    matrix = np.full((len(systems), len(profiles_set)), np.nan)
    for r in prof_records:
        if r["esc_mean"] is None:
            continue
        try:
            i = systems.index(r["system"])
            j = profiles_set.index(r["profile"])
            matrix[i, j] = r["esc_mean"]
        except ValueError:
            continue

    fig, ax = plt.subplots(figsize=(max(10, 0.55 * len(profiles_set)), 4.0))
    im = ax.imshow(matrix, aspect="auto", cmap="RdYlGn", vmin=1.5, vmax=5.0)
    ax.set_xticks(range(len(profiles_set)))
    ax.set_xticklabels(profiles_set, rotation=45, ha="right", fontsize=9)
    ax.set_yticks(range(len(systems)))
    ax.set_yticklabels([s.upper() for s in systems], fontsize=11)
    ax.set_title("ESC mean per (system × profile) — green = good, red = poor, white = no data",
                 fontsize=12, pad=12)
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            v = matrix[i, j]
            if not np.isnan(v):
                ax.text(j, i, f"{v:.1f}", ha="center", va="center",
                        fontsize=8, color="black" if 2.5 < v < 4.3 else "white")
    fig.colorbar(im, ax=ax, label="ESC mean (1–5)", shrink=0.7)
    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Pipeline diagram
# ---------------------------------------------------------------------------
def make_pipeline_diagram(path: Path):
    """Three-band pipeline diagram. Clean layout, no crossing arrows.

    Coordinate system: 0..100 wide, 0..50 tall. Three horizontal bands:
      - Shared upstream  (left)  : Agent 1 → Agent 2 → Apply → Agent 3a×N
      - Two parallel fork lanes  : v7 lane (top) and v8 lane (bottom)
      - Shared downstream (right): Agent 5 → Agent X → user
    """
    fig, ax = plt.subplots(figsize=(18, 8))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 50)
    ax.axis("off")

    SHARED = "#E5E7EB"
    SHARED_EDGE = "#6B7280"
    V7_FILL = "#DBEAFE"
    V7_EDGE = "#2563EB"
    V8_FILL = "#FED7AA"
    V8_EDGE = "#EA580C"

    def box(x, y, w, h, label, sub=None, fill=SHARED, edge=SHARED_EDGE,
            text_color="#111827", lw=1.8):
        b = FancyBboxPatch((x, y), w, h,
                           boxstyle="round,pad=0.25,rounding_size=0.5",
                           linewidth=lw, facecolor=fill, edgecolor=edge,
                           zorder=10)
        ax.add_patch(b)
        ax.text(x + w/2, y + h*0.65, label, ha="center", va="center",
                fontsize=10.5, fontweight="bold", color=text_color, zorder=11)
        if sub:
            ax.text(x + w/2, y + h*0.30, sub, ha="center", va="center",
                    fontsize=8.5, color="#475569", style="italic", zorder=11)

    def arrow(x0, y0, x1, y1, color="#6B7280", lw=1.6, pad=0.5):
        """Draw an arrow with a small pad on each end so the head/tail
        rendering doesn't visibly bleed into target/source boxes.
        """
        # Shrink along the line direction by `pad` on each end
        dx = x1 - x0
        dy = y1 - y0
        length = (dx * dx + dy * dy) ** 0.5
        if length > 2 * pad:
            ux = dx / length
            uy = dy / length
            x0 = x0 + ux * pad
            y0 = y0 + uy * pad
            x1 = x1 - ux * pad
            y1 = y1 - uy * pad
        a = FancyArrowPatch((x0, y0), (x1, y1),
                            arrowstyle="->,head_width=5,head_length=6",
                            color=color, linewidth=lw, mutation_scale=14,
                            zorder=5, shrinkA=0, shrinkB=0)
        ax.add_patch(a)

    # --- Title (above plot area) ---
    ax.text(50, 47.5, "HELP-E pipeline  —  six phases shared, only retrieval forks",
            ha="center", va="center", fontsize=16, fontweight="bold", color="#111827")
    ax.text(50, 44.8,
            "Same input → same inference → same graph state. Retrieval (Phase 5) is the only divergence.",
            ha="center", va="center", fontsize=11, color="#4B5563", style="italic")

    # --- Strict left-to-right flow:
    #     Upstream → Fork → v7 lane (top) / v8 lane (bottom) → Merge into Agent 5 → Agent X → user
    #     All arrows flow LEFT to RIGHT only (no diagonals across boxes).
    Y_UP = 28        # Upstream row centre
    Y_V7 = 33        # v7 lane top
    Y_V8 = 14        # v8 lane bottom
    Y_DOWN = 24      # Agent 5 / Agent X centred between the two lanes
    H = 6
    H_DOWN = 7

    # --- Upstream shared row (positioned slightly left and tall) ---
    boxes_upstream = [
        ("Agent 1",      "intent + MI",        1,  10),
        ("Agent 2",      "graph mutation",     12, 11),
        ("Apply",        "to graph",           24, 8),
        ("Agent 3a × N", "attribute summary",  33, 12),
    ]
    for i, (label, sub, x, w) in enumerate(boxes_upstream):
        box(x, Y_UP - H/2, w, H, label, sub)
        if i > 0:
            prev = boxes_upstream[i-1]
            arrow(prev[2] + prev[3], Y_UP, x, Y_UP)

    # End of upstream
    end_up_x = boxes_upstream[-1][2] + boxes_upstream[-1][3]   # = 45

    # --- v7 lane (TOP, y = 33-H/2 .. 33+H/2) ---
    boxes_v7 = [
        ("v7: Agent 3c", "per-edge LLM summary", 49, 13),
        ("Agent 3b × M", "TTM + system_intent",  64, 11),
        ("v7: Phase 5",  "WDC graph-walk",       77, 11),
    ]
    for i, (label, sub, x, w) in enumerate(boxes_v7):
        is_v7_specific = label.startswith("v7:")
        box(x, Y_V7 - H/2, w, H, label, sub,
            fill=V7_FILL if is_v7_specific else SHARED,
            edge=V7_EDGE if is_v7_specific else SHARED_EDGE,
            text_color="#1E3A8A" if is_v7_specific else "#111827",
            lw=2.0 if is_v7_specific else 1.5)
        if i > 0:
            prev = boxes_v7[i-1]
            arrow(prev[2] + prev[3], Y_V7, x, Y_V7, color=V7_EDGE)

    # --- v8 lane (BOTTOM) ---
    boxes_v8 = [
        ("v8: Agent Q",  "retrieval query (LLM)", 49, 13),
        ("Agent 3b × M", "TTM + system_intent",   64, 11),
        ("v8: Phase 5",  "dense RAG (MiniLM+MMR)",77, 11),
    ]
    for i, (label, sub, x, w) in enumerate(boxes_v8):
        is_v8_specific = label.startswith("v8:")
        box(x, Y_V8 - H/2, w, H, label, sub,
            fill=V8_FILL if is_v8_specific else SHARED,
            edge=V8_EDGE if is_v8_specific else SHARED_EDGE,
            text_color="#9A3412" if is_v8_specific else "#111827",
            lw=2.0 if is_v8_specific else 1.5)
        if i > 0:
            prev = boxes_v8[i-1]
            arrow(prev[2] + prev[3], Y_V8, x, Y_V8, color=V8_EDGE)

    # --- Diagonal-but-clean fork arrows from upstream end to v7/v8 first boxes ---
    # These are the only diagonal arrows; they don't cross any box.
    arrow(end_up_x, Y_UP, boxes_v7[0][2], Y_V7, color=V7_EDGE, lw=2.0)
    arrow(end_up_x, Y_UP, boxes_v8[0][2], Y_V8, color=V8_EDGE, lw=2.0)

    # --- Downstream shared row (Agent 5 + Agent X) — placed RIGHT of the lanes,
    # vertically centred between them at Y_DOWN, no crossing ---
    end_v7_x = boxes_v7[-1][2] + boxes_v7[-1][3]   # = 88
    end_v8_x = boxes_v8[-1][2] + boxes_v8[-1][3]   # = 88
    a5_x = max(end_v7_x, end_v8_x) + 1.5
    a5_w = 9.5
    box(a5_x, Y_DOWN - H_DOWN/2, a5_w, H_DOWN,
        "Agent 5", "R1→R2→R3→R4")
    # Merge arrows from v7 + v8 last boxes into Agent 5 (clean diagonals,
    # no crossing because Agent 5 is to the RIGHT of both lanes)
    arrow(end_v7_x, Y_V7, a5_x, Y_DOWN + 1.5, color=V7_EDGE, lw=2.0)
    arrow(end_v8_x, Y_V8, a5_x, Y_DOWN - 1.5, color=V8_EDGE, lw=2.0)

    # --- Lane band labels on far left of each lane ---
    ax.text(47, Y_V7, "v7", ha="right", va="center",
            fontsize=14, fontweight="bold", color=V7_EDGE)
    ax.text(47, Y_V8, "v8", ha="right", va="center",
            fontsize=14, fontweight="bold", color=V8_EDGE)

    # --- Final → user note at far right ---
    ax.text(a5_x + a5_w + 0.5, Y_DOWN, "→ user",
            ha="left", va="center", fontsize=11, color="#1F2937", fontweight="bold")
    ax.text(a5_x + a5_w/2, Y_DOWN - H_DOWN/2 - 1.5, "+ Agent X",
            ha="center", va="top", fontsize=8.5, color="#475569", style="italic")

    # --- Legend (bottom-left, doesn't cover anything) ---
    legend_handles = [
        mpatches.Patch(facecolor=SHARED, edgecolor=SHARED_EDGE,
                       label="Shared phases (v7 = v8)"),
        mpatches.Patch(facecolor=V7_FILL, edgecolor=V7_EDGE,
                       label="v7 lane: graph-walk + per-edge LLM summary"),
        mpatches.Patch(facecolor=V8_FILL, edgecolor=V8_EDGE,
                       label="v8 lane: Agent Q + dense MiniLM RAG"),
    ]
    ax.legend(handles=legend_handles, loc="lower left",
              fontsize=10, frameon=True, bbox_to_anchor=(0.0, 0.0))

    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Architecture matrix table image
# ---------------------------------------------------------------------------
def make_architecture_matrix(sys_records: list[dict], path: Path):
    capabilities = [
        ("Problems tracked across turns",        "✗", "✓", "◐", "✓", "✓"),
        ("Per-attribute belief state (HBM)",     "✗", "✗", "✗", "✓", "✓"),
        ("TTM stage per problem",                "✗", "✓", "✗", "✓", "✓"),
        ("Two-track MI (immediate + longer-arc)","✗", "✓", "✗", "✓", "✓"),
        ("Cross-problem connections",            "✗", "✓ (problem)", "✗", "✓ (attr-pair)", "✓ (attr-pair)"),
        ("Evidence-grounded R3",                 "✗", "✓", "◐", "✓", "✓"),
        ("Retrieval mechanism",                  "—", "WDC", "proprietary", "WDC graph-walk", "dense RAG"),
    ]
    systems_order = ["v1", "v3", "cami", "v7", "v8"]
    miti_row = ["MITI mean (1–5)"]
    esc_row = ["ESC mean (1–5)"]
    for s in systems_order:
        rec = next((r for r in sys_records if r["system"] == s), None)
        if rec and rec["miti_mean"] is not None:
            miti_row.append(f"{rec['miti_mean']:.2f}")
        else:
            miti_row.append("—")
        if rec and rec["esc_mean"] is not None:
            esc_row.append(f"{rec['esc_mean']:.2f}")
        else:
            esc_row.append("—")

    headers = ["Capability", "v1", "v3", "CAMI", "v7", "v8"]

    fig, ax = plt.subplots(figsize=(13.5, 6.8))
    ax.axis("off")

    cell_text = [[cap] + list(vals) for (cap, *vals) in capabilities]
    cell_text.append(miti_row)
    cell_text.append(esc_row)

    n_rows = len(cell_text)
    n_cols = len(headers)

    # Use a Table via matplotlib
    table = ax.table(cellText=cell_text, colLabels=headers, cellLoc="center", loc="center",
                     colColours=["#F3F4F6", SYSTEM_COLORS["v1"], SYSTEM_COLORS["v3"],
                                 SYSTEM_COLORS["cami"], SYSTEM_COLORS["v7"], SYSTEM_COLORS["v8"]])
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1, 1.7)

    # Color cells: ✓ green, ✗ red, ◐ yellow
    for r in range(n_rows):
        for c in range(n_cols):
            cell = table[r + 1, c]
            txt = str(cell_text[r][c])
            if c == 0:
                cell.set_facecolor("#F9FAFB")
                cell.get_text().set_horizontalalignment("left")
                cell.get_text().set_fontweight("bold")
            elif "✓" in txt:
                cell.set_facecolor("#D1FAE5")
            elif "✗" in txt:
                cell.set_facecolor("#FECACA")
            elif "◐" in txt:
                cell.set_facecolor("#FEF3C7")
            elif r >= len(capabilities):  # numeric rows
                cell.set_facecolor("#EFF6FF")
                cell.get_text().set_fontweight("bold")
        # Highlight v7 column on numeric rows visually if it's max
        if r >= len(capabilities):
            try:
                vals = [float(cell_text[r][i]) for i in range(1, n_cols) if cell_text[r][i] != "—"]
                if vals:
                    mx = max(vals)
                    for c in range(1, n_cols):
                        if cell_text[r][c] != "—" and abs(float(cell_text[r][c]) - mx) < 1e-6:
                            table[r + 1, c].set_facecolor("#BBF7D0")
            except ValueError:
                pass

    # Style header row
    for c in range(n_cols):
        table[0, c].get_text().set_fontweight("bold")
        table[0, c].get_text().set_color("white")

    ax.set_title("HELP-E v7/v8 vs baselines — capabilities + headline numbers",
                 fontsize=14, fontweight="bold", pad=20)
    fig.text(0.5, 0.04,
             "✓ supported   ✗ not supported   ◐ partial.   "
             "MITI/ESC means are aggregate per-system across all profiles run.   "
             "Green numeric cell = highest in that row.",
             ha="center", va="bottom", fontsize=9, style="italic", color="#4B5563")

    fig.tight_layout(rect=[0, 0.06, 1, 0.97])
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Side-by-side retrieval comparison diagram (Slide 4)
# ---------------------------------------------------------------------------
def make_retrieval_comparison(path: Path):
    fig, ax = plt.subplots(figsize=(15, 7.5))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 60)
    ax.axis("off")

    V7_FILL = "#DBEAFE"
    V7_EDGE = "#2563EB"
    V8_FILL = "#FED7AA"
    V8_EDGE = "#EA580C"

    # Two columns
    def col(x0, w, header, header_fill, header_edge, rows):
        # Header
        b = FancyBboxPatch((x0, 50), w, 6.5,
                           boxstyle="round,pad=0.3,rounding_size=0.5",
                           linewidth=1.8, facecolor=header_fill, edgecolor=header_edge)
        ax.add_patch(b)
        ax.text(x0 + w/2, 53.3, header, ha="center", va="center",
                fontsize=12, fontweight="bold", color="#111827")
        # Rows
        y = 47
        row_h = 6.2
        for label, val in rows:
            # Row background subtle
            row_box = FancyBboxPatch((x0, y - row_h + 0.6), w, row_h - 1.0,
                                     boxstyle="round,pad=0.1,rounding_size=0.3",
                                     linewidth=0.6, facecolor="#FFFFFF", edgecolor="#E5E7EB")
            ax.add_patch(row_box)
            ax.text(x0 + 0.6, y - 1.2, label, ha="left", va="center",
                    fontsize=9, fontweight="bold", color="#374151")
            ax.text(x0 + 0.6, y - 3.2, val, ha="left", va="center",
                    fontsize=8.5, color="#1F2937")
            y -= row_h

    v7_rows = [
        ("What it indexes",  "The problem graph itself — edges with weights"),
        ("The query",        "The set of currently-active problems (seeds)"),
        ("Scoring",          "score(X) = Σ weight(seed, X) for each non-seed"),
        ("Keep rule",        "score(X) ≥ τ × max(score),  τ = 0.5"),
        ("Surfaced to R3",   "Per-edge LLM summary (Agent 3c)"),
        ("Strength",         "Cluster-aware — sees how problems relate"),
        ("Weakness",         "Old isolated moments fade over time"),
    ]
    v8_rows = [
        ("What it indexes",  "Every audit-stack entry as a flat chunk"),
        ("The query",        "Bag-of-words string written by Agent Q"),
        ("Scoring",          "cosine(query_emb, chunk_emb), MiniLM L2-norm"),
        ("Keep rule",        "MMR: λ·sim − (1−λ)·max_redundancy, λ = 0.5, K = 8"),
        ("Surfaced to R3",   "Up to 8 raw chunks with sN.tM anchors"),
        ("Strength",         "Moment-aware — finds specific past quotes"),
        ("Weakness",         "Cluster context implicit (top-K can cluster)"),
    ]
    col(2, 46, "v7  —  Graph-walked retrieval", V7_FILL, V7_EDGE, v7_rows)
    col(52, 46, "v8  —  Dense RAG retrieval", V8_FILL, V8_EDGE, v8_rows)

    # Title
    ax.text(50, 58.5, "Memory retrieval — same graph, two ways of asking 'what's relevant now?'",
            ha="center", va="center", fontsize=14, fontweight="bold", color="#111827")

    # Bottom tagline
    ax.text(50, 2.5,
            'Same graph. Two retrievals. v7 is "tell me the network." v8 is "tell me the moments."',
            ha="center", va="center", fontsize=12, fontweight="bold",
            color="#111827",
            bbox=dict(boxstyle="round,pad=0.6", facecolor="#FEF3C7", edgecolor="#D97706", linewidth=1.5))

    fig.tight_layout()
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Evidence usage over time (Slide 6 supplement)
# ---------------------------------------------------------------------------
EVIDENCE_TYPES = (
    "attribute",
    "attribute_connection",
    "problem_problem_connection",
    "persona",
    "recent_turn",
)
EVIDENCE_COLORS = {
    "attribute":                  "#2563EB",   # blue
    "attribute_connection":       "#7C3AED",   # purple
    "problem_problem_connection": "#DB2777",   # pink
    "persona":                    "#059669",   # green
    "recent_turn":                "#F59E0B",   # amber
}


def collect_evidence_per_turn(system: str, sysroot_rel: str, sys_label: str):
    """Return list of dicts:
        [{profile, session, turn, global_turn, type_counts: {type: count}}, ...]
    Where global_turn = cumulative turn index across earlier sessions of the
    same profile (so session 1 turns are 1..N1; session 2 turns are N1+1..),
    so we can bucket by 10-turn windows that span session boundaries.
    """
    root = REPO / sysroot_rel
    if not root.exists():
        return []
    out: list[dict] = []
    for prof_dir in sorted(root.iterdir()):
        if not prof_dir.is_dir():
            continue
        sys_dir = prof_dir / sys_label
        if not sys_dir.exists():
            continue
        # Sort sessions by session id
        session_files = sorted(sys_dir.glob("session_*.json"))
        # We need to compute cumulative turn offset per session. Loop in order.
        cumulative = 0
        for sf in session_files:
            try:
                d = json.loads(sf.read_text())
            except Exception:
                continue
            tts = d.get("turn_traces") or []
            n_turns_this_session = len(tts)
            for t in tts:
                tid = t.get("turn_id", 0)
                global_turn = cumulative + tid
                r = t.get("response") or {}
                eu = r.get("evidence_used") or []
                type_counts: dict[str, int] = {tp: 0 for tp in EVIDENCE_TYPES}
                for e in eu:
                    tp = e.get("type")
                    if tp in type_counts:
                        type_counts[tp] += 1
                out.append({
                    "profile": prof_dir.name,
                    "session": d.get("session_id"),
                    "turn": tid,
                    "global_turn": global_turn,
                    "n_evidence_total": len(eu),
                    "type_counts": type_counts,
                })
            cumulative += n_turns_this_session
    return out


def aggregate_evidence_by_window(records: list[dict],
                                  windows: list[tuple[int, int]] | None = None,
                                  window: int = 10):
    """Bucket records by explicit (low, high) windows OR by uniform window
    size. Return per-window means of (total evidence, per-type counts).

    If `windows` is provided, use those exact windows (turns outside any
    window are dropped — useful to skip the cold-start turns 1-10).
    """
    from collections import defaultdict
    bucket_records: dict[int, list[dict]] = defaultdict(list)
    if windows is not None:
        for r in records:
            for i, (low, high) in enumerate(windows):
                if low <= r["global_turn"] <= high:
                    bucket_records[i].append(r)
                    break
    else:
        for r in records:
            b = (r["global_turn"] - 1) // window
            bucket_records[b].append(r)

    summary = {}
    for b, recs in bucket_records.items():
        n = len(recs)
        if n == 0:
            continue
        per_type = {tp: 0 for tp in EVIDENCE_TYPES}
        total = 0
        for r in recs:
            total += r["n_evidence_total"]
            for tp in EVIDENCE_TYPES:
                per_type[tp] += r["type_counts"].get(tp, 0)
        if windows is not None:
            low, high = windows[b]
        else:
            low, high = b * window + 1, (b + 1) * window
        summary[b] = {
            "n_turns": n,
            "mean_total": total / n,
            "mean_per_type": {tp: per_type[tp] / n for tp in EVIDENCE_TYPES},
            "low_turn": low,
            "high_turn": high,
        }
    return summary


def make_evidence_usage_chart(path: Path):
    """Produce a 2-panel chart: v7 on top, v8 on bottom. Each panel:
       - X = turn-window (1-10, 11-20, ..., 61-70)
       - Y = mean evidence citations per turn in that window
       - 5 lines: one per evidence type
       - Vertical dotted lines at session boundaries (turn 30, 50)
    """
    v7_root, v7_label = SYSTEM_ROOTS["v7"]
    v8_root, v8_label = SYSTEM_ROOTS["v8"]
    v7 = collect_evidence_per_turn("v7", v7_root, v7_label)
    v8 = collect_evidence_per_turn("v8", v8_root, v8_label)
    # Skip first 10 turns (cold-start noise) and bucket the remaining 60
    # turns into 3 windows of 20: 11-30, 31-50, 51-70. Three points per
    # line is enough to show the trajectory cleanly without noise.
    WINDOWS = [(11, 30), (31, 50), (51, 70)]
    v7_buckets = aggregate_evidence_by_window(v7, windows=WINDOWS)
    v8_buckets = aggregate_evidence_by_window(v8, windows=WINDOWS)

    # Two side-by-side panels (v7 left, v8 right), each with the same
    # 3-window x-axis. Discrete category x-axis ("Late S1", "S2", "S3")
    # because we have only 3 well-defined points.
    fig, (ax_v7, ax_v8) = plt.subplots(1, 2, figsize=(14, 6.5), sharey=True)

    PERIOD_LABELS = ["Late S1\n(turns 11–30)", "S2\n(turns 31–50)", "S3\n(turns 51–70)"]

    for ax, buckets, label, color_accent in [
        (ax_v7, v7_buckets, "v7  —  graph-walked retrieval", "#2563EB"),
        (ax_v8, v8_buckets, "v8  —  dense MiniLM RAG", "#EA580C"),
    ]:
        if not buckets or len(buckets) < 3:
            ax.text(0.5, 0.5, f"Insufficient {label} data", transform=ax.transAxes,
                    ha="center", va="center", fontsize=14)
            continue
        bs = sorted(buckets.keys())[:3]
        x_pos = list(range(len(bs)))
        n_turns_per_bucket = [buckets[b]["n_turns"] for b in bs]

        # Compute max for ylim
        max_y = 0
        for tp in EVIDENCE_TYPES:
            ys = [buckets[b]["mean_per_type"].get(tp, 0) for b in bs]
            max_y = max(max_y, max(ys) if ys else 0)
        ax.set_ylim(0, max_y * 1.30)

        # Plot one line per evidence type
        for tp in EVIDENCE_TYPES:
            ys = [buckets[b]["mean_per_type"].get(tp, 0) for b in bs]
            line, = ax.plot(x_pos, ys, marker="o", linewidth=2.6, markersize=10,
                            label=tp.replace("_", " "),
                            color=EVIDENCE_COLORS[tp])
            # Annotate every point with its value
            for xp, y in zip(x_pos, ys):
                offset = (0, 12) if tp != "attribute" else (0, -16)
                ax.annotate(f"{y:.2f}", (xp, y),
                            textcoords="offset points", xytext=offset,
                            fontsize=9, color=EVIDENCE_COLORS[tp],
                            fontweight="bold", ha="center", va="center")

        # n_turns annotation just below x-axis
        for xp, n in zip(x_pos, n_turns_per_bucket):
            ax.text(xp, -0.08, f"n={n} turns", ha="center", va="top",
                    fontsize=8.5, color="#6B7280",
                    transform=ax.get_xaxis_transform(), style="italic")

        ax.set_title(label, fontsize=13.5, fontweight="bold",
                     color=color_accent, loc="left", pad=10)
        ax.set_xticks(x_pos)
        ax.set_xticklabels(PERIOD_LABELS, fontsize=10.5)
        ax.set_xlim(-0.3, len(bs) - 0.7)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.grid(axis="y", linestyle=":", alpha=0.5)
        if ax is ax_v7:
            ax.set_ylabel("Mean evidence citations / turn", fontsize=11.5)

    # Single shared legend below both plots
    handles = [plt.Line2D([0], [0], marker="o", linewidth=2.5, markersize=9,
                          color=EVIDENCE_COLORS[tp],
                          label=tp.replace("_", " "))
               for tp in EVIDENCE_TYPES]
    fig.legend(handles=handles, loc="lower center",
               bbox_to_anchor=(0.5, -0.02), ncol=5,
               fontsize=10.5, frameon=True, title="Evidence category cited in `evidence_used`",
               title_fontsize=11)

    fig.suptitle(
        "Evidence diversity grows over the conversation",
        fontsize=15, fontweight="bold", y=0.995,
    )
    fig.text(0.5, 0.94,
             "First 10 turns omitted (cold start). "
             "Persona citations rise after each session boundary "
             "(Agent P updates persona end-of-session). "
             "v8 surfaces typed attribute_connections more reliably than v7.",
             ha="center", va="top", fontsize=10, style="italic", color="#4B5563")
    fig.tight_layout(rect=[0, 0.06, 1, 0.92])
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


# Helper for collect_evidence_per_turn — adapted SYSTEM_ROOTS access
def _system_root_path(system: str) -> tuple[str, str]:
    return SYSTEM_ROOTS[system]


# ---------------------------------------------------------------------------
# Problem detection statistics
# ---------------------------------------------------------------------------
TTM_STAGES = ("precontemplation", "contemplation", "preparation", "action")
TTM_COLORS = {
    "precontemplation": "#FCA5A5",   # red
    "contemplation":    "#FCD34D",   # yellow
    "preparation":      "#86EFAC",   # light green
    "action":           "#10B981",   # green
}


def collect_graph_stats_per_turn(sysroot_rel: str, sys_label: str) -> list[dict]:
    """Walk every transcript for one system and return per-turn graph stats.

    Each record has:
        profile, session, turn, global_turn,
        n_current_problems, main_problem,
        n_attribute_entries_laid_down, n_connection_entries_laid_down,
        ttm_stage_main, n_evidence_used_total, n_evidence_used_by_type
    """
    root = REPO / sysroot_rel
    if not root.exists():
        return []
    out: list[dict] = []
    for prof_dir in sorted(root.iterdir()):
        if not prof_dir.is_dir():
            continue
        sys_dir = prof_dir / sys_label
        if not sys_dir.exists():
            continue
        cumulative = 0
        for sf in sorted(sys_dir.glob("session_*.json")):
            try:
                d = json.loads(sf.read_text())
            except Exception:
                continue
            tts = d.get("turn_traces") or []
            for t in tts:
                tid = t.get("turn_id", 0)
                global_turn = cumulative + tid
                a2 = t.get("agent2") or {}
                tr = t.get("trace") or {}
                r = t.get("response") or {}
                eu = r.get("evidence_used") or []
                type_counts: dict[str, int] = {tp: 0 for tp in EVIDENCE_TYPES}
                for e in eu:
                    tp = e.get("type")
                    if tp in type_counts:
                        type_counts[tp] += 1
                out.append({
                    "profile": prof_dir.name,
                    "session": d.get("session_id"),
                    "turn": tid,
                    "global_turn": global_turn,
                    "n_current_problems": len(a2.get("current_problems") or []),
                    "main_problem": (a2.get("main_problem") or {}).get("problem_name") if isinstance(a2.get("main_problem"), dict) else a2.get("main_problem") or tr.get("main_problem"),
                    "n_attribute_entries_laid_down": len(a2.get("problem_attribute_entries") or []),
                    "n_connection_entries_laid_down": len(a2.get("problem_attribute_connections") or []),
                    "ttm_stage_main": tr.get("ttm_stage"),
                    "n_evidence_used_total": len(eu),
                    "n_evidence_used_by_type": type_counts,
                })
            cumulative += len(tts)
    return out


def make_problems_detected_chart(path: Path):
    """Two-panel chart:
       - LEFT: bar chart of mean #current_problems per turn for each system.
       - RIGHT: horizontal bar of the top problems most often picked as
         main_problem across all turns of v7 (the system with most data).
    """
    systems = ["v1", "v3", "v7", "v8"]
    means: dict[str, float] = {}
    means_p25: dict[str, float] = {}
    means_p75: dict[str, float] = {}
    n_records: dict[str, int] = {}
    for s in systems:
        sysroot, label = SYSTEM_ROOTS[s]
        recs = collect_graph_stats_per_turn(sysroot, label)
        if not recs:
            continue
        n_problems = [r["n_current_problems"] for r in recs]
        n_records[s] = len(recs)
        if n_problems:
            ns = np.array(n_problems)
            means[s] = float(ns.mean())
            means_p25[s] = float(np.percentile(ns, 25))
            means_p75[s] = float(np.percentile(ns, 75))

    # Right panel: distribution of main_problem (use v7)
    v7_recs = collect_graph_stats_per_turn(*SYSTEM_ROOTS["v7"])
    from collections import Counter
    main_counter = Counter(r["main_problem"] for r in v7_recs if r["main_problem"])
    top_problems = main_counter.most_common(15)

    fig, (ax_left, ax_right) = plt.subplots(1, 2, figsize=(15, 6.5),
                                            gridspec_kw={"width_ratios": [1, 1.4]})

    # LEFT panel: mean current_problems per turn
    sys_labels = []
    sys_means = []
    sys_errs_low = []
    sys_errs_high = []
    sys_colors = []
    for s in systems:
        if s in means:
            sys_labels.append(f"{s}\n(n={n_records.get(s, 0)})")
            sys_means.append(means[s])
            sys_errs_low.append(means[s] - means_p25[s])
            sys_errs_high.append(means_p75[s] - means[s])
            sys_colors.append(SYSTEM_COLORS[s])

    x = np.arange(len(sys_labels))
    bars = ax_left.bar(x, sys_means, yerr=[sys_errs_low, sys_errs_high],
                       capsize=5, color=sys_colors, edgecolor="#1F2937", linewidth=1.2)
    for i, (m, lab) in enumerate(zip(sys_means, sys_labels)):
        ax_left.text(i, m + 0.08, f"{m:.2f}", ha="center", va="bottom",
                     fontsize=11, fontweight="bold", color="#111827")
    ax_left.set_xticks(x)
    ax_left.set_xticklabels(sys_labels, fontsize=10)
    ax_left.set_ylabel("Mean # active problems / turn", fontsize=11)
    ax_left.set_title("Active problems tracked per turn",
                      fontsize=12, fontweight="bold", color="#111827")
    ax_left.set_ylim(0, max(sys_means) * 1.45 if sys_means else 1)
    ax_left.spines["top"].set_visible(False)
    ax_left.spines["right"].set_visible(False)
    ax_left.grid(axis="y", linestyle=":", alpha=0.4)
    ax_left.text(0.5, -0.18, "(error bars = 25-75th percentile across turns)",
                 transform=ax_left.transAxes, ha="center", va="top",
                 fontsize=9, color="#6B7280", style="italic")

    # RIGHT panel: top-15 main_problem distribution (v7)
    if top_problems:
        labels = [p[0].replace("_", " ") for p in top_problems][::-1]
        counts = [p[1] for p in top_problems][::-1]
        colors_seq = plt.cm.viridis(np.linspace(0.15, 0.85, len(counts)))
        ax_right.barh(labels, counts, color=colors_seq, edgecolor="#1F2937", linewidth=0.8)
        for i, (lab, c) in enumerate(zip(labels, counts)):
            ax_right.text(c + max(counts) * 0.012, i, str(c),
                          va="center", fontsize=9, color="#1F2937")
        ax_right.set_xlabel("# turns where this is the main_problem", fontsize=11)
        ax_right.set_title("Top 15 main_problems detected (v7, all profiles)",
                           fontsize=12, fontweight="bold", color="#2563EB")
        ax_right.spines["top"].set_visible(False)
        ax_right.spines["right"].set_visible(False)
        ax_right.tick_params(axis="y", labelsize=9.5)

    fig.suptitle("Problem-detection statistics — what does HELP-E actually track?",
                 fontsize=14, fontweight="bold", y=0.995)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def make_ttm_progression_chart(path: Path):
    """Two-panel chart per system using SAME 3-window x-axis as the
    evidence-over-time chart (Late S1, S2, S3).

    Per system (v7 / v8 stacked vertically):
      - LEFT panel: stacked-bar distribution of TTM stages (% per window)
      - RIGHT panel: line chart of "% in preparation + action" — the
        meaningful behavior-change movement, which is otherwise invisible
        when the stacked area is dominated by contemplation.
    """
    WINDOWS = [(11, 30), (31, 50), (51, 70)]
    PERIOD_LABELS = ["Late S1\n(11–30)", "S2\n(31–50)", "S3\n(51–70)"]

    fig, axes = plt.subplots(2, 2, figsize=(15, 9.5),
                             gridspec_kw={"width_ratios": [1.3, 1]})
    for row_idx, (system, color_accent) in enumerate(
        [("v7", "#2563EB"), ("v8", "#EA580C")]
    ):
        ax_stack = axes[row_idx, 0]
        ax_line = axes[row_idx, 1]
        recs = collect_graph_stats_per_turn(*SYSTEM_ROOTS[system])

        from collections import defaultdict
        bucket_stage: dict[int, dict[str, int]] = defaultdict(
            lambda: {st: 0 for st in TTM_STAGES})
        bucket_n: dict[int, int] = defaultdict(int)
        for r in recs:
            stage = r["ttm_stage_main"]
            if stage not in TTM_STAGES:
                continue
            for i, (low, high) in enumerate(WINDOWS):
                if low <= r["global_turn"] <= high:
                    bucket_stage[i][stage] += 1
                    bucket_n[i] += 1
                    break

        bs = sorted(bucket_stage.keys())[:3]
        if not bs:
            ax_stack.text(0.5, 0.5, f"No {system} data",
                          transform=ax_stack.transAxes, ha="center", va="center")
            continue

        # Compute per-stage percentages per bucket
        pct_per_stage = {}
        for st in TTM_STAGES:
            pct_per_stage[st] = [bucket_stage[b][st] / max(bucket_n[b], 1) * 100
                                  for b in bs]

        # ---- LEFT panel: stacked bar (cleaner than stacked area for 3 windows)
        x_pos = list(range(len(bs)))
        bottoms = [0.0] * len(bs)
        for st in TTM_STAGES:
            ys = pct_per_stage[st]
            ax_stack.bar(x_pos, ys, bottom=bottoms, width=0.62,
                         color=TTM_COLORS[st], edgecolor="white", linewidth=1.5,
                         label=st.replace("_", " "))
            # Annotate inside each segment if it's tall enough to fit
            for xp, y, b in zip(x_pos, ys, bottoms):
                if y >= 4:
                    ax_stack.text(xp, b + y/2, f"{y:.0f}%",
                                  ha="center", va="center",
                                  fontsize=9.5,
                                  fontweight="bold" if y >= 8 else "normal",
                                  color="#1F2937" if y < 30 else "#1F2937")
            bottoms = [b + y for b, y in zip(bottoms, ys)]

        ax_stack.set_title(f"{system}  —  TTM stage distribution per window",
                           fontsize=12, fontweight="bold", color=color_accent,
                           loc="left", pad=8)
        ax_stack.set_xticks(x_pos)
        ax_stack.set_xticklabels(PERIOD_LABELS, fontsize=10.5)
        ax_stack.set_yticks([0, 25, 50, 75, 100])
        ax_stack.set_ylim(0, 105)
        ax_stack.set_ylabel("% of turns", fontsize=11)
        ax_stack.spines["top"].set_visible(False)
        ax_stack.spines["right"].set_visible(False)
        if row_idx == 0:
            ax_stack.legend(loc="upper left", bbox_to_anchor=(0.0, -0.14),
                            ncol=4, fontsize=9.5, frameon=True,
                            title="TTM stage", title_fontsize=10)

        # ---- RIGHT panel: line of "% in preparation OR action"
        prep_pct = pct_per_stage["preparation"]
        action_pct = pct_per_stage["action"]
        moved_pct = [p + a for p, a in zip(prep_pct, action_pct)]

        # Two lines: preparation alone, and action alone
        ax_line.plot(x_pos, prep_pct, marker="o", linewidth=2.6, markersize=10,
                     color=TTM_COLORS["preparation"], label="preparation")
        ax_line.plot(x_pos, action_pct, marker="s", linewidth=2.6, markersize=10,
                     color=TTM_COLORS["action"], label="action")
        ax_line.plot(x_pos, moved_pct, marker="^", linewidth=2.6, markersize=10,
                     color="#047857", linestyle="--", label="prep + action")

        for xp, y in zip(x_pos, prep_pct):
            ax_line.annotate(f"{y:.1f}%", (xp, y), xytext=(0, -16),
                             textcoords="offset points", ha="center",
                             fontsize=9, fontweight="bold",
                             color=TTM_COLORS["preparation"])
        for xp, y in zip(x_pos, action_pct):
            ax_line.annotate(f"{y:.1f}%", (xp, y), xytext=(0, -16),
                             textcoords="offset points", ha="center",
                             fontsize=9, fontweight="bold",
                             color=TTM_COLORS["action"])
        for xp, y in zip(x_pos, moved_pct):
            ax_line.annotate(f"{y:.1f}%", (xp, y), xytext=(8, 8),
                             textcoords="offset points", ha="left",
                             fontsize=10, fontweight="bold", color="#047857")

        ax_line.set_title(f"{system}  —  % moved beyond contemplation",
                          fontsize=12, fontweight="bold", color=color_accent,
                          loc="left", pad=8)
        ax_line.set_xticks(x_pos)
        ax_line.set_xticklabels(PERIOD_LABELS, fontsize=10.5)
        ax_line.set_xlim(-0.3, len(bs) - 0.5)
        ax_line.set_ylabel("% of turns", fontsize=11)
        ax_line.set_ylim(bottom=0)
        ax_line.spines["top"].set_visible(False)
        ax_line.spines["right"].set_visible(False)
        ax_line.grid(axis="y", linestyle=":", alpha=0.4)
        ax_line.legend(loc="upper left", fontsize=9.5, frameon=True)

    fig.suptitle(
        "TTM progression — does the conversation move users beyond contemplation?",
        fontsize=15, fontweight="bold", y=0.995,
    )
    fig.text(0.5, 0.955,
             "LEFT: full TTM distribution per window. "
             "RIGHT: zoom on preparation + action — small but non-zero movement, "
             "growing window-over-window.",
             ha="center", va="top", fontsize=10, style="italic", color="#4B5563")
    fig.tight_layout(rect=[0, 0.04, 1, 0.93])
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def make_evidence_laid_vs_used_chart(path: Path):
    """Grouped bar chart per system showing total evidence laid down by Agent 2
    vs total cited in evidence_used by Agent 5.
    """
    systems = ["v7", "v8"]
    # Categories: (laid-down field, used type label)
    laid_total = {s: {"attribute": 0, "connection": 0} for s in systems}
    used_total = {s: {"attribute": 0, "attribute_connection": 0,
                      "problem_problem_connection": 0, "persona": 0,
                      "recent_turn": 0} for s in systems}

    for s in systems:
        recs = collect_graph_stats_per_turn(*SYSTEM_ROOTS[s])
        for r in recs:
            laid_total[s]["attribute"] += r["n_attribute_entries_laid_down"]
            laid_total[s]["connection"] += r["n_connection_entries_laid_down"]
            for tp in used_total[s]:
                used_total[s][tp] += r["n_evidence_used_by_type"].get(tp, 0)

    fig, axes = plt.subplots(1, 2, figsize=(14, 6.2), gridspec_kw={"width_ratios": [1, 1.2]})

    # LEFT — laid-down counts per system
    ax = axes[0]
    cats = ["attribute", "connection"]
    cat_labels = ["attribute_entry\n(per attribute, per turn)",
                  "connection_entry\n(per problem-pair, per turn)"]
    x = np.arange(len(cats))
    w = 0.35
    for i, s in enumerate(systems):
        vals = [laid_total[s][c] for c in cats]
        ax.bar(x + (i - 0.5) * w, vals, w, label=s,
               color=SYSTEM_COLORS[s], edgecolor="#1F2937", linewidth=1.0)
        for j, v in enumerate(vals):
            ax.text(x[j] + (i - 0.5) * w, v + max(vals) * 0.02, str(v),
                    ha="center", va="bottom", fontsize=10,
                    fontweight="bold", color="#1F2937")
    ax.set_xticks(x)
    ax.set_xticklabels(cat_labels, fontsize=10)
    ax.set_ylabel("Total entries written to graph", fontsize=11)
    ax.set_title("Evidence LAID DOWN by Agent 2 (cumulative)",
                 fontsize=12, fontweight="bold", color="#111827")
    ax.legend(fontsize=10, frameon=True, loc="upper right")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", linestyle=":", alpha=0.4)

    # RIGHT — used-by-type per system
    ax2 = axes[1]
    types = list(EVIDENCE_TYPES)
    x2 = np.arange(len(types))
    for i, s in enumerate(systems):
        vals = [used_total[s][t] for t in types]
        ax2.bar(x2 + (i - 0.5) * w, vals, w, label=s,
                color=SYSTEM_COLORS[s], edgecolor="#1F2937", linewidth=1.0)
        for j, v in enumerate(vals):
            ax2.text(x2[j] + (i - 0.5) * w, v + max(vals) * 0.015, str(v),
                     ha="center", va="bottom", fontsize=9,
                     fontweight="bold", color="#1F2937")
    ax2.set_xticks(x2)
    ax2.set_xticklabels([t.replace("_", "\n") for t in types], fontsize=9.5)
    ax2.set_ylabel("Total cited in `evidence_used`", fontsize=11)
    ax2.set_title("Evidence USED by Agent 5 (cumulative)",
                  fontsize=12, fontweight="bold", color="#111827")
    ax2.legend(fontsize=10, frameon=True, loc="upper right")
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)
    ax2.grid(axis="y", linestyle=":", alpha=0.4)

    fig.suptitle("Evidence economy — how much does each system collect, and how much does it cite?",
                 fontsize=14, fontweight="bold", y=0.995)
    fig.text(0.5, 0.955,
             "Same audit-stack mechanics in v7 and v8; the difference shows up at the citation step.",
             ha="center", va="top", fontsize=10, style="italic", color="#4B5563")
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Coverage summary
# ---------------------------------------------------------------------------
def write_coverage_summary(sys_records: list[dict], prof_records: list[dict], path: Path):
    lines = [
        "# Data Coverage — what's been run so far",
        "",
        "Snapshot of which (system, profile) cells have judge artifacts.",
        "",
    ]
    profiles_set = sorted({r["profile"] for r in prof_records})
    systems = ["v1", "v3", "cami", "v7", "v8"]
    lines.append("| Profile | " + " | ".join(s.upper() for s in systems) + " |")
    lines.append("|---|" + "|".join(":---:" for _ in systems) + "|")
    cells = {(r["system"], r["profile"]): r for r in prof_records}
    for prof in profiles_set:
        row = [prof]
        for s in systems:
            r = cells.get((s, prof))
            if r is None:
                row.append("—")
            else:
                miti = f"{r['miti_mean']:.2f}" if r["miti_mean"] is not None else "?"
                esc = f"{r['esc_mean']:.2f}" if r["esc_mean"] is not None else "?"
                row.append(f"M={miti} / E={esc}")
        lines.append("| " + " | ".join(row) + " |")
    lines.extend(["", "Per-system aggregate:", ""])
    label_map = {"v1": "v1 (baseline)", "v3": "v3 (graph, no HBM)", "cami": "CAMI (external)",
                 "v7": "**v7 (graph-walk)**", "v8": "**v8 (dense RAG)**"}
    for r in sys_records:
        n_p = r["n_profiles"]
        n_s = r["n_sessions"]
        lines.append(f"- **{label_map.get(r['system'], r['system'])}**: {n_p} profiles, {n_s} sessions evaluated")
    lines.extend(["", "*Generated by `scripts/build_presentation_assets.py`*"])
    path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("Loading judge files...")
    miti_rows, esc_rows = load_judge_files()
    print(f"  MITI scores: {len(miti_rows)} rows ({len({(r['system'], r['profile'], r['session_file']) for r in miti_rows})} sessions)")
    print(f"  ESC scores: {len(esc_rows)} rows ({len({(r['system'], r['profile'], r['session_file']) for r in esc_rows})} sessions)")

    agg = aggregate(miti_rows, esc_rows)

    print("\nWriting CSVs...")
    write_csv(agg["per_session"], OUT / "results_per_session.csv")
    write_csv(agg["per_profile"], OUT / "results_per_profile.csv")
    write_csv(agg["per_system"], OUT / "results_per_system.csv")

    print("Writing markdown summaries...")
    write_results_summary_md(agg["per_system"], OUT / "results_summary.md")
    write_coverage_summary(agg["per_system"], agg["per_profile"], OUT / "data_coverage.md")

    print("Generating bar chart...")
    make_bar_chart(agg["per_system"], OUT / "results_bar_chart.png")

    print("Generating per-profile heatmap...")
    make_per_profile_heatmap(agg["per_profile"], OUT / "results_per_profile_heatmap.png")

    print("Generating pipeline diagram...")
    make_pipeline_diagram(OUT / "pipeline_diagram.png")

    print("Generating architecture matrix...")
    make_architecture_matrix(agg["per_system"], OUT / "architecture_matrix.png")

    print("Generating retrieval comparison...")
    make_retrieval_comparison(OUT / "retrieval_comparison.png")

    print("Generating evidence-usage-over-time chart...")
    make_evidence_usage_chart(OUT / "evidence_usage_over_time.png")

    print("Generating problem-detection chart...")
    make_problems_detected_chart(OUT / "problems_detected.png")

    print("Generating TTM progression chart...")
    make_ttm_progression_chart(OUT / "ttm_progression.png")

    print("Generating evidence laid-down vs used chart...")
    make_evidence_laid_vs_used_chart(OUT / "evidence_laid_vs_used.png")

    print("\nAll assets written to:", OUT)
    for p in sorted(OUT.iterdir()):
        size_kb = p.stat().st_size / 1024
        print(f"  {p.name}  ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
