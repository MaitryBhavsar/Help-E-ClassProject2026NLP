# HELP-E

**A graph-backed, MI-grounded multi-turn emotional-support chatbot — with five ablation systems (v1, v3, v4, v6, v7, v8) plus a CAMI baseline, three-criteria evaluation (MITI 4.2 + ESC + TTM transition rate), and a live LightningAI deployment.**

HELP-E is a research system that combines **Motivational Interviewing**, the **Health Belief Model**, and the **Transtheoretical Model** with a persistent typed problem-graph to produce psychologically principled, context-aware support across multiple sessions. It ships with 30 synthetic user profiles, an interactive UI, batch runners, evaluation tooling, and reproducible v7-vs-v8 comparison plots.

---

## Contents

- [Quick start](#quick-start)
- [The systems](#the-systems)
- [Repository layout](#repository-layout)
- [Setup](#setup)
- [Bring your own keys (`.env.local`)](#bring-your-own-keys-envlocal)
- [Running each system](#running-each-system)
- [Evaluation](#evaluation)
- [Documentation](#documentation)
- [CAMI baseline](#cami-baseline)
- [License & acknowledgements](#license--acknowledgements)

---

## Quick start

```bash
# 1. Clone
git clone https://github.com/MaitryBhavsar/Help-E-ClassProject2026NLP.git
cd Help-E-ClassProject2026NLP

# 2. Python 3.11 venv + deps
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Drop in YOUR API keys
cp .env.example .env.local
# (edit .env.local — see "Bring your own keys" below)

# 4. Smoke a 3-turn run on v7 against your own backend
PYTHONPATH=src python3 -m help_e.run \
    --system v7 --profile P01 --sessions 1 --turns 3
```

Outputs land under `output/<run-tag>/transcripts/<profile>/<system>/`. Transcripts and per-turn logs are git-ignored.

---

## The systems

HELP-E ships **six ablation systems** plus a CAMI baseline. All share the same simulator (Mind-1), the same MITI/ESC judges, and the same 30 profile YAMLs — so every metric is directly comparable across systems.

| System | What it adds over the previous version | Where it lives |
|---|---|---|
| **v1** — history-only floor | Plain dialogue history; no graph, no MI gating. The "what an LLM does without HELP-E's structure" baseline. | `src/help_e/baselines/v1_full.py` |
| **v3** — text-summary + TTM | Per-problem free-text summary + TTM stage inferred from the summary. No structured attribute graph. | `src/help_e/baselines/v3_full.py` |
| **v4** — observation + TTM | v3 plus typed observation extraction; cross-problem connections still free-text. | `src/help_e/baselines/v4_obs_ttm.py` |
| **v6** — full HBM attribute graph | Per-attribute level-tracking, TTM stages per problem, typed cross-problem connections. The "graph" version. | `src/help_e/baselines/v6_full.py` |
| **v7** — multi-agent + edge summaries | Specialized small agents (Intent, Inference, Attribute, Stage, EdgeSummary, RollingSummary, Persona); R1→R4 response contract; per-edge running NL summary; weighted-degree-centrality retrieval. | `src/help_e/baselines/v7_full.py` + `prompts/agent*.py` |
| **v8** — RAG over the audit corpus | v7 minus the EdgeSummaryAgent, plus a QueryAgent (Agent Q) and dense MiniLM retrieval with MMR over every audit/connection chunk. | `src/help_e/baselines/v8_full.py` + `rag_v8.py` + `prompts/agentq_retrieval_query.py` |
| **CAMI** — published-baseline adapter | Wraps the CAMI counselor agent (Sun et al. 2025) as a HELP-E `--system cami` so it runs through the same simulator + judges. | `src/help_e/baselines/cami_adapter.py` (CAMI source under `external/CAMI/`) |

**Three-theory composition** (more in [`docs/HELPE_COMPLETE_TECHNICAL_REPORT.md`](docs/HELPE_COMPLETE_TECHNICAL_REPORT.md)):

- **MI** = how to phrase the move (10 selectable MISC codes, 6 banned anti-patterns).
- **HBM** = what to track per problem (11 attributes: severity, susceptibility, benefits, barriers, self-efficacy + 6 practical fields).
- **TTM** = which strategy is appropriate now (4 stages: pre-contemplation → contemplation → preparation → action).

For a deep dive on v7 and v8 specifically:

- [`docs/HELPE_V7_TECHNICAL_REPORT.md`](docs/HELPE_V7_TECHNICAL_REPORT.md) — every agent, prompt, validator, parallelism point.
- [`docs/HELPE_V8_TECHNICAL_REPORT.md`](docs/HELPE_V8_TECHNICAL_REPORT.md) — the v7-to-v8 deltas (drop EdgeSummaryAgent, add Agent Q + RAG).

---

## Repository layout

```
Help-E-ClassProject2026NLP/
├── README.md                       (this file)
├── requirements.txt
├── .env.example                    template — copy to .env.local
├── .gitignore
├── LICENSE
├── docs/
│   ├── HELPE_COMPLETE_TECHNICAL_REPORT.md   foundations, v1–v6 deep dive
│   ├── HELPE_V7_TECHNICAL_REPORT.md         v7 multi-agent pipeline
│   ├── HELPE_V8_TECHNICAL_REPORT.md         v7 → v8 (RAG) deltas
│   └── EVALUATION_AND_BENCHMARK.md          MITI / ESC / TTM judges
├── src/help_e/
│   ├── run.py                      CLI entry: `python -m help_e.run --system <vN>`
│   ├── config.py                   constants, env-var hooks, MISC vocab
│   ├── llm_client.py               three-tier routing (MAIN/SMALL/SIM/JUDGE)
│   ├── graph_v6.py, graph_v7.py    typed problem graph (v7 = v6 + per-attr summary + level_reasoning)
│   ├── instruction_response_v{1,3,6,7,8}.py   per-system response generators
│   ├── mi_picker_v7.py             MISC shortlist gating (intent + TTM stage)
│   ├── rag_v8.py                   dense MiniLM cosine + MMR (v8 only)
│   ├── session_driver_v6.py        runs a profile through all sessions
│   ├── baselines/
│   │   ├── v1_full.py, v3_full.py, v4_obs_ttm.py, v6_full.py
│   │   ├── v7_full.py, v8_full.py
│   │   ├── cami_adapter.py
│   │   ├── rag_baseline.py, graphrag_baseline.py
│   │   └── common.py
│   ├── prompts/
│   │   ├── agent1_user_intent.py        IntentAgent (v7 + v8)
│   │   ├── agent2_inference_v7.py       InferenceAgent (v7 + v8)
│   │   ├── agent3a_attr_update.py       AttributeAgent
│   │   ├── agent3b_ttm_intent.py        StageAgent
│   │   ├── agent3c_edge_summary.py      EdgeSummaryAgent (v7 only)
│   │   ├── agentq_retrieval_query.py    QueryAgent (v8 only)
│   │   ├── agentX_rolling_summary.py    RollingSummaryAgent
│   │   └── agent_p_persona_update.py    PersonaAgent (end-of-session)
│   ├── eval/
│   │   ├── compare_v3_v7_v8.py     per-profile triple comparison
│   │   ├── matrix_report.py        cross-system × cross-profile matrix
│   │   ├── ablation_report.py      v1/v3/v4/v6 sweep aggregator
│   │   ├── judge.py                MITI 4.2 globals
│   │   ├── esc_judge.py            ESC 6 dimensions
│   │   └── metrics.py              TTM transition rate, forward/regression
│   ├── data/profiles/              30 profile YAMLs (P01–P30)
│   ├── simulator/                  Mind-1 (user simulator)
│   └── ui/                         FastAPI single-file UI server
├── scripts/
│   ├── run_v1_local_120b.sh, run_v3_local_120b.sh
│   ├── run_v6_local.sh, run_v6_fireworks.sh
│   ├── run_v7_local_120b.sh, run_v7_lightning_70b.sh, run_v7_lightning_120b.sh
│   ├── run_v8_local_120b.sh, run_v8_lightning_70b.sh
│   ├── run_cami_local_70b.sh, run_cami_lightning_70b.sh
│   └── ...                         (use these — they set the right env vars)
├── analysis/v7_vs_v8/
│   ├── README.md                   simple v7 vs v8 explainer
│   ├── data.json                   materialized comparison numbers
│   ├── plot.py                     regenerate the 8 PNG charts
│   └── fig{1..8}_*.png             MITI / ESC / TTM / per-profile / P18 three-way
└── external/
    └── CAMI/                       Sun et al. 2025 CAMI source (vendored)
```

---

## Setup

### Python

Tested on **Python 3.11**. macOS, Linux, and Windows (via WSL) all work.

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`sentence-transformers` is **only required for v8** (the dense RAG retriever). The first call will download the MiniLM model (~90 MB) into your HuggingFace cache.

### Backends

HELP-E supports any OpenAI-API-compatible chat endpoint. Tested with:

- **Local vLLM** (e.g., `gpt-oss-120b` on `http://localhost:11436`)
- **Lightning AI** (`gpt-oss-20b`, `llama-3.3-70b`)
- **Fireworks AI** (`gpt-oss-120b`)

The run scripts under `scripts/` pre-fill the URL + model name for common combinations. Switch backends by editing the script's `HELPE_*_OLLAMA_URL` and `HELPE_*_MODEL` lines.

---

## Bring your own keys (`.env.local`)

**No keys are committed to this repository.** You must supply your own.

```bash
cp .env.example .env.local
```

Then edit `.env.local` and fill in **at least one** of:

```bash
# Example for Lightning AI:
HELPE_MAIN_API_KEY="<your-lightning-llama-3.3-70b-key>"
HELPE_SIM_API_KEY="<your-lightning-gpt-oss-20b-key>"
# (HELPE_JUDGE_API_KEY defaults to MAIN if unset)
```

For local vLLM, set the keys to `"EMPTY"` (literal string — vLLM ignores the value):

```bash
HELPE_MAIN_API_KEY="EMPTY"
HELPE_JUDGE_API_KEY="EMPTY"
HELPE_SIM_API_KEY="<your-lightning-gpt-oss-20b-key>"   # or "EMPTY" if SIM is also local
```

Each run script sources `.env.local` at the top and **errors loudly** if a required key isn't present (e.g., `set HELPE_SIM_API_KEY in .env.local`). `.env.local` is git-ignored — your keys never leave your machine.

The four tenant slots:

| Slot | Used by | Typical model |
|---|---|---|
| `HELPE_MAIN_API_KEY` | Agent 2 (inference), Agent 5 (response), Agent P (persona) | Big (gpt-oss-120b / llama-3.3-70b) |
| `HELPE_JUDGE_API_KEY` | MITI 4.2 + ESC judges | Big (often = MAIN) |
| `HELPE_SIM_API_KEY` | Mind-1 user simulator, session_context | Small (gpt-oss-20b) |
| `HELPE_SMALL_API_KEY` (optional) | Agents 1, 3a, 3b, 3c, X, Q in v8 | Small (defaults to SIM endpoint) |

---

## Running each system

### From a run script (the recommended path)

The scripts under `scripts/` preflight the endpoints, set per-call-role token caps, and route output to an isolated tree under `output/`.

```bash
# v1 — history-only baseline, on Lightning AI
./scripts/run_v1_lightning_70b.sh --profile P01 --sessions 3 --turns-list 30,20,20

# v3 — text-summary + TTM, on Lightning AI
./scripts/run_v3_lightning_70b.sh --profile P01 --sessions 3 --turns-list 30,20,20

# v7 — multi-agent + edge summaries, on Lightning AI
./scripts/run_v7_lightning_70b.sh --profile P01 --sessions 3 --turns-list 30,20,20

# v8 — RAG over the audit corpus, on Lightning AI
./scripts/run_v8_lightning_70b.sh --profile P01 --sessions 3 --turns-list 30,20,20

# CAMI baseline (published comparator)
./scripts/run_cami_lightning_70b.sh --profile P01 --sessions 3 --turns-list 30,20,20

# Run all 30 profiles in parallel (subject to global concurrency cap of 2)
./scripts/run_v7_lightning_70b.sh --all-profiles --sessions 3 --turns-list 30,20,20 \
    --max-parallel-profiles 2
```

For local vLLM, swap `lightning_70b` → `local_120b` in the script name.

### Direct CLI (no script)

If you've already exported the env vars yourself:

```bash
PYTHONPATH=src python3 -m help_e.run \
    --system v8 --profile P01 --sessions 3 --turns-list 30,20,20
```

Common flags:

- `--system <v1|v3|v4|v6|v7|v8|cami|rag|graphrag>`
- `--profile P01` (repeatable) **or** `--all-profiles`
- `--sessions N`
- `--turns-list 30,20,20` (per-session turn counts; overrides `--turns`)
- `--max-parallel-profiles 2`

### Interactive UI

```bash
PYTHONPATH=src python3 -m help_e.ui.server --host 127.0.0.1 --port 8765
# open http://127.0.0.1:8765
```

---

## Evaluation

Every session writes a `session_NN.json` transcript plus two LLM-judge sidecars:

- `miti_judge_sNN.json` — MITI 4.2 four globals (cultivating change talk, softening sustain talk, partnership, empathy)
- `esc_judge_sNN.json` — ESC six dimensions (empathy, understanding, helpfulness, autonomy_respect, non_judgment, willingness_to_continue)

A graph snapshot lands at `<profile>_after_sNN.json`. TTM transition rate is computed deterministically from the snapshot.

### Compare three systems on one profile

```bash
PYTHONPATH=src python3 -m help_e.eval.compare_v3_v7_v8 \
    --profile P18 --systems v3 v7 v8 \
    --root v3:output/local_v3_11436_120b/transcripts \
    --root v7:output/fireworks_v7_120b/transcripts \
    --root v8:output/fireworks_v8_120b/transcripts
```

Prints a table: problems detected, evidences in graph, audits cited in response, TTM forward transitions, MITI 4-global means, ESC 6-dim means. See [`analysis/v7_vs_v8/README.md`](analysis/v7_vs_v8/README.md) for an example interpretation.

### Cross-system × cross-profile matrix

```bash
PYTHONPATH=src python3 -m help_e.eval.matrix_report \
    --systems v1 v3 v7 v8 cami --profiles all
```

### v7 vs v8 plot regeneration

```bash
# After new transcripts/judges land, refresh the comparison data and PNGs:
PYTHONPATH=src python3 analysis/v7_vs_v8/plot.py
```

The 8 figures cover MITI globals, ESC dimensions, TTM progression, evidence behavior, per-profile bars, and the P18 three-way (v3 vs v7 vs v8 @ 120b).

---

## Documentation

| Document | What it covers |
|---|---|
| [`docs/HELPE_COMPLETE_TECHNICAL_REPORT.md`](docs/HELPE_COMPLETE_TECHNICAL_REPORT.md) | Full system foundations + v1–v6 deep dive. Plain-language, no prerequisites. |
| [`docs/HELPE_V7_TECHNICAL_REPORT.md`](docs/HELPE_V7_TECHNICAL_REPORT.md) | v7 multi-agent pipeline — every agent, prompt, validator, parallelism point, end-to-end worked example. |
| [`docs/HELPE_V8_TECHNICAL_REPORT.md`](docs/HELPE_V8_TECHNICAL_REPORT.md) | v7 → v8 deltas: drop EdgeSummaryAgent, add Agent Q + dense RAG retrieval, the WDC-edge-walk vs RAG question. |
| [`docs/EVALUATION_AND_BENCHMARK.md`](docs/EVALUATION_AND_BENCHMARK.md) | MITI / ESC / TTM judges, the three-criteria evaluation philosophy. |
| [`analysis/v7_vs_v8/README.md`](analysis/v7_vs_v8/README.md) | Simple v7-vs-v8 side-by-side: one paragraph per algorithm, results table, chart guide. |

---

## CAMI baseline

`external/CAMI/` is a vendored copy of the CAMI counselor-agent implementation:

> Sun, et al. **CAMI: A Counselor Agent Supporting Motivational Interviewing through State Inference and Topic Exploration.** ACL 2025. <https://aclanthology.org/2025.acl-long.1024/>

It is wrapped by `src/help_e/baselines/cami_adapter.py` so it runs as a `--system cami` through the same simulator and judges as v1/v3/v7/v8 — apples-to-apples comparison.

The vendored CAMI source retains its original license (see `external/CAMI/`). All credit for CAMI goes to the original authors.

---

## License & acknowledgements

This project is released under the LICENSE in this repository. The `external/CAMI/` source carries its own license; see that directory.

Built as part of an NLP class project (Spring 2026) by Maitry Bhavsar. Behavioral-science grounding draws on:

- Miller, W. R. & Rollnick, S. *Motivational Interviewing: Helping People Change* (3rd ed., 2013)
- Rosenstock, I. M. *The Health Belief Model and Preventive Health Behavior* (1974)
- Prochaska, J. O. & DiClemente, C. C. *The Transtheoretical Approach* (1984)

Open an issue on this repo for questions, bug reports, or replication problems.
