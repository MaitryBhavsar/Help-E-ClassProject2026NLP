# v7 vs v8 — Simple Comparison

*Plain-language side-by-side, with the numbers that matter. Charts in this directory.*

---

## The two algorithms in one paragraph each

### v7 — Edge-walk over a graph with summarized cluster context

Every turn, v7 reads the user message and updates a structured graph: **problems** (e.g., `work_stress`, `sleep_problems`), each with **typed attributes** (e.g., `perceived_severity`, `self_efficacy`), connected by **typed edges** (e.g., `causal`, `shared_trigger`). Specialized small agents then update each piece in parallel: one agent updates per-attribute beliefs, one updates per-problem TTM stage, one writes a chronological **per-edge running summary** in plain English. When the response generator is about to reply, v7 walks the graph from the current main problem out to whichever edges are *connected enough* (by recency-weighted strength) and hands the responder both the per-edge summary and the most recent few raw entries. The responder reads cluster-level information.

> **Mental model:** "I have a small notebook of this person. The pages are organized by problem, with cross-references. To respond, I read the cluster of pages most connected to what they're saying right now."

### v8 — Dense RAG over a flat audit corpus

v8 keeps **the same graph and the same level/TTM agents** as v7. Two changes only: (a) it drops v7's per-edge running-summary agent, and (b) it adds a small **QueryAgent** that produces a search query from the structured signals (user intent, MI move, system intent, active problems), then runs a **dense MiniLM cosine + MMR diversification** over the union of every audit entry and every connection entry the user has ever produced. The top-K (default 8) raw chunks land directly in the response prompt, each anchored to a specific past turn. No edge summary; the responder reads moment-level information instead of cluster-level information.

> **Mental model:** "I have a searchable archive of every specific thing this person has said. To respond, I write a search query and pull the 8 most relevant past moments — wherever they happen to live."

### What they share (everything else)

Both versions use the **same graph data structure**, the **same small agents** for intent classification, attribute updating, TTM staging, and rolling-summary, the **same response prompt and validators**, the **same MITI/ESC judges**, the **same simulator**, and the **same evaluation matrix**. The only swap is the retrieval layer.

---

## At a glance — 15 odd profiles, gpt-oss-70b on Lightning AI

(Each profile = 3 sessions × turns 30/20/20 = 70 turns. 15 profiles = 1050 turns per system.)

| Signal | v7 | v8 | Δ (v8 − v7) | Read |
|---|---:|---:|---:|---|
| **MITI overall** | 4.61 | 4.58 | −0.03 | Tie. Both excellent. |
| **ESC mean** | 4.84 | 4.86 | +0.02 | Tie. Both excellent. |
| Assigned-primary detected | 60% | **73%** | +13 pp | v8 finds the profile's stated primary problem more reliably |
| Audits cited in response | 91 | **98** | +7 | v8 surfaces ~8% more audit citations |
| **Forward TTM transitions / profile** | **6.33** | 4.87 | −1.46 | v7 progresses TTM stages ~30% more |
| **Progression rate** | **0.24** | 0.19 | −0.05 | v7 progresses more per problem-session |
| Regressions | 0 | 0 | 0 | Neither regresses |

**One-line read**: at this matrix size on 70b, **judge scores are tied; v7 wins on TTM outcome, v8 wins on problem-detection / citation density**.

---

## P18 — three-way comparison @ gpt-oss-120b

Only profile with all three systems run. Single profile, so don't over-read; useful for sanity-checking direction.

| Signal | v3 | v7 | v8 |
|---|---:|---:|---:|
| Problems detected | 6 | 11 | 11 |
| Attribute audit entries (graph) | 0 | 242 | 227 |
| Connection entries (graph) | 77 | 71 | 78 |
| Audits cited in response | n/a | 13 | **36** |
| Forward TTM transitions | 5 | **11** | 10 |
| Progression rate | 0.28 | **0.33** | 0.30 |
| MITI overall | 3.25 | 3.17 | 3.25 |
| ESC mean | 3.22 | **3.44** | 2.67 |

**Read**: v3 (text-summary baseline) detects roughly half the problems v7/v8 find, and produces no attribute audits at all. v7 produces the most TTM forward motion. v8's audit-citation count (36) is **~3× v7's** — its RAG retriever is much more aggressive about putting specific past quotes into the response. On this single profile v8's ESC dropped (helpfulness 2.0, willingness_to_continue 2.0); not seen in the 15-profile aggregate, so likely profile-specific noise.

---

## The eight charts (in this directory)

| File | What it shows |
|---|---|
| `fig1_miti_globals.png` | MITI 4 globals + overall, v7 vs v8 (15-profile mean) |
| `fig2_esc_dims.png` | ESC 6 dimensions + mean, v7 vs v8 (15-profile mean) |
| `fig3_ttm_progression.png` | Forward transitions / progression rate / assigned-primary detection rate |
| `fig4_evidence_behavior.png` | Attribute audits / connection entries / audits-cited-in-response |
| `fig5_per_profile_miti.png` | Per-profile MITI overall — paired bars across all 15 profiles |
| `fig6_per_profile_esc.png` | Per-profile ESC mean — paired bars across all 15 profiles |
| `fig7_per_profile_progression.png` | Per-profile TTM progression rate — paired bars |
| `fig8_p18_three_way.png` | P18 three-way (v3 / v7 / v8 @ 120b) — MITI top, ESC bottom |

Color convention (matches `output/analysis_2026_04_30/`): **gray = v3** (older baseline), **green = v7** (graph family), **red = v8** (RAG variant).

---

## How to regenerate

```bash
# Re-extract numbers from the saved transcripts/judges
PYTHONPATH=src python3 -c "
from help_e.eval import compare_v3_v7_v8
# (see output/analysis_v7_vs_v8/data.json for the materialized form)
"

# Re-render the eight PNGs from data.json
PYTHONPATH=src python3 output/analysis_v7_vs_v8/plot.py
```

`data.json` in this directory is the materialized comparison — every per-profile and aggregated number that the charts read from. If transcripts/judges are added, re-run the data-extraction snippet above (or invoke `compare_v3_v7_v8` directly) and regenerate the PNGs.

---

## When to use which view

- Showing **the algorithm-level comparison** to someone new → use `fig1` (MITI), `fig2` (ESC), `fig3` (TTM), `fig4` (evidence). Four charts, one slide.
- Showing **per-profile variance** (sanity-check that aggregates aren't driven by outliers) → use `fig5`, `fig6`, `fig7`.
- Showing **v3 baseline vs v7 vs v8** on a single profile → use `fig8`. Caveat: P18 only.
- Showing **operational intuition** about what v7 vs v8 emphasizes → cite the assigned-primary-detected (+13 pp) and forward-transitions (+30% for v7) deltas. They are the clearest behavioral signals.
