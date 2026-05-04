# HELP-E

**A graph-backed, MI-grounded multi-turn emotional-support chatbot — with a 4-tier ablation matrix (v1 / v3 / v4 / v6), three-criteria evaluation (MITI 4.2 + TTM transition rate + ESC), and a live LightningAI deployment.**

HELP-E is a research system that combines **Motivational Interviewing**, the **Health Belief Model**, and the **Transtheoretical Model** with a persistent typed problem-graph to produce psychologically principled, context-aware support across multiple sessions. It ships with 30 synthetic user profiles, an interactive UI, batch runners, and example output for all four active systems.

> **Active matrix:** `v1 (floor)` · `v3 (mid-tier)` · `v4 (graph-only)` · `v6 (full system)` — all run through a unified v6 driver so their MITI / TTM-transition / ESC metrics are directly comparable.
>
> **External baseline:** CAMI can be run as a counselor comparison via `--system cami`; see [`CAMI_BASELINE_README.md`](./CAMI_BASELINE_README.md).

---

## Table of contents

- [Innovation — what's new here](#innovation--whats-new-here)
- [Try the live demo](#try-the-live-demo)
- [Technical pipeline](#technical-pipeline)
- [System ablations](#system-ablations)
- [Evaluation — three criteria](#evaluation--three-criteria)
- [Repository structure](#repository-structure)
- [Graph schema (v6)](#graph-schema-v6)
- [Installation](#installation)
- [LLM backend setup (LightningAI / vLLM / Ollama)](#llm-backend-setup-lightningai--vllm--ollama)
- [Configuration reference](#configuration-reference)
- [Running HELP-E (step by step)](#running-help-e-step-by-step)
- [Data assets](#data-assets)
- [License](#license)
- [Acknowledgements](#acknowledgements)

---

## Innovation — what's new here

HELP-E goes beyond stateless emotional-support chatbots and beyond single-turn MI-prompted LLMs by combining four design moves that, to our knowledge, have not been brought together before in a single emotional-support system:

1. **A persistent, typed problem-graph as the chatbot's memory.** Instead of dumping conversation history into a prompt, HELP-E maintains a structured graph: persona node, problem nodes (drawn from a fixed 20-problem vocabulary), attribute nodes (HBM-aligned: perceived severity, perceived barriers, self-efficacy, …), and **typed problem-problem edges** (causal, reinforcing, conflicting, shared trigger / barrier / goal, …). The graph is updated turn-by-turn with append-only evidence stacks and recency-weighted level inference, then queried rule-based for retrieval — no vector search, no hallucinated continuity.

2. **MISC-aligned, TTM-gated MI strategy selection — without an LLM in the loop.** A pure-Python rule table maps `(TTM stage)` to a candidate set of MISC counselor strategies (Moyers et al. 2014). The 10 selectable codes — Support, Facilitate, Complex Reflection, Reframe, Inform/Raise Concern/Advise (with permission), Evoke, Closed Question, Structure — sit alongside always-on OARS skills (Open question, Affirmation, Simple Reflection, Summary) and an always-on autonomy-support principle. MI-inconsistent behaviors (Direct, Confront, Warn, advise/inform without permission) are explicitly catalogued and banned in the system prompt.

3. **A four-tier ablation that controls *exactly* what the graph adds.** Because v1 / v3 / v4 / v6 all run through the same driver, simulator, response prompt, and judge, the only thing that varies across systems is **how much graph-derived structure the prompt sees**:
   - **v1** — no graph at all, history-only.
   - **v3** — per-problem free-text running summaries + TTM-from-summary.
   - **v4** — observed attributes + TTM (no problem-problem edges, no merged instruction step).
   - **v6** — full system: typed problem-problem edges + recency-weighted edge weights + the merged instruction-and-response chain-of-thought.
   This isolates the contribution of each layer of structure cleanly.

4. **Three orthogonal evaluation axes that catch different failure modes.** Most MI chatbot work reports one number (often a single LLM-judge MITI score). HELP-E reports **MITI 4.2** (counselor adherence — does the model *speak* like an MI counselor?), **TTM state-transition rate** (clinical effectiveness — does the user actually *progress through stages*?), and **ESC** (subjective support quality — does the user *feel* supported?). The middle one is pure-Python, deterministic, and unbiased by judge-model choice. They disagree often enough to be interesting.

The implementation also supports **three-tier LLM routing** (main response model, user-simulator model, dedicated judge model — each independently configurable), with a default LightningAI deployment, and optional fallback to local Ollama or vLLM.

---

## Try the live demo

> 🌐 **Live UI:** _**TODO** — paste the deployed URL here._
>
> The interactive UI is a FastAPI app with a static frontend; see [Running HELP-E §1](#1-interactive-ui-chat-demo) for how to run it locally on `http://localhost:8000`. To share it publicly, deploy the FastAPI app behind any HTTPS-capable host (e.g., Lightning AI Studio, Render, Fly, Cloud Run) and point the hostname at the same `HELPE_*` env vars used for batch runs.

In the UI you can:
- pick any of the 30 synthetic profiles + any of the four systems (v1, v3, v4, v6),
- chat turn-by-turn with the bot,
- inspect the per-turn artifacts in real time — retrieval bundle, selected MISC strategies, candidate list, raw response trace, post-turn graph state.

---

## Technical pipeline

Each turn flows through a deterministic pipeline. v1 / v3 / v4 / v6 share the same skeleton; the only difference is *how much* graph machinery is wired in.

```
                          ┌─────────────────────────────────────────────────┐
                          │  PER-TURN PIPELINE (one user message in,        │
                          │   one assistant response out, all artifacts     │
                          │   logged + serialized)                          │
                          └─────────────────────────────────────────────────┘

  user message
       │
       ▼
┌───────────────────┐
│ §1 INFERENCE      │  LLM call (call_role: "inference"). Reads recent N turns
│  prompts/         │  + persona + current graph. Outputs:
│  inference.py     │     • current_problems       (subset of 20-problem vocab)
│                   │     • main_problem            (one of current_problems)
│                   │     • user_intent            (1 of 8: express_emotion, …)
│                   │     • observed_attributes    (typed: severity, barriers, …)
│                   │     • problem_problem_relations (typed edges, v6 only)
└───────────────────┘
       │
       ▼
┌───────────────────┐
│ §2 GRAPH UPDATE   │  Pure-Python (no LLM). Applies inference output to the
│  graph_v6.py      │  graph: appends evidence to attribute stacks, increments
│                   │  problem mention counts, adds/strengthens problem-problem
│                   │  edges (v4/v6), advances or holds TTM stage on the main
│                   │  problem.
└───────────────────┘
       │
       ▼
┌───────────────────┐
│ §3 RECOMPUTE      │  LLM call (call_role: "recompute"). Recomputes per-attribute
│  prompts/         │  level (low / medium / high / unknown) from the top-K
│  recompute.py     │  recent stack entries with recency weighting.
└───────────────────┘
       │
       ▼
┌───────────────────┐
│ §4 RETRIEVAL      │  Pure-Python rule-based bundle. Pulls:
│  retrieval_v6.py  │   • PersonaState (8 fields)
│                   │   • Prior-session summary
│                   │   • Main problem node + level summaries
│                   │   • Top-S graph-close problems (edge-weight ranked, v6)
│                   │   • Recent-active problems
│                   │   • Last-N turns (raw dialogue)
└───────────────────┘
       │
       ▼
┌───────────────────┐
│ §5 STRATEGY       │  Pure-Python (no LLM). Rule table maps TTM stage →
│  SELECTION        │  MISC candidate set:
│  mi_selector_v6   │     precontemplation → complex_reflection, reframe,
│                   │                         inform/raise_concern (w/ permission)
│                   │     contemplation    → evoke, complex_reflection, inform
│                   │     preparation      → advise (w/ permission), closed_q,
│                   │                         structure
│                   │     action           → structure, advise, raise_concern
│                   │  Plus always-on: support, facilitate.
└───────────────────┘
       │
       ▼
┌───────────────────┐
│ §6 RESPONSE       │  LLM call.
│                   │   v6 → instruction_response_v6 (merged CoT: chosen_strategies,
│                   │         instruction, response — single call)
│                   │   v1/v3/v4 → instruction_response_simple (fewer fields; same
│                   │         OARS / MISC / autonomy framing)
│                   │  Hard caps: ≤ 5 sentences, ≤ 1 question per response.
│                   │  Banned-opener / candidate-subset / sentence-cap validators
│                   │  retry the call up to 3 times with the previous attempt's
│                   │  error hint as feedback.
└───────────────────┘
       │
       ▼
   final response
       │
       ▼
┌───────────────────┐
│ §7 USER REPLY     │  LLM call (call_role: "mind1_v6"). Generates the synthetic
│  simulator/       │  user's next message conditional on the persona + session
│  mind1_v6.py      │  arc + a resistance level. The same simulator is used for
│                   │  all four systems → ablation contract preserved.
└───────────────────┘
       │
       ▼
   (loop to next turn until session ends)


    SESSION-END:
       │
       ▼
┌───────────────────┐
│ PERSONA UPDATE    │  Refines persona fields based on what we learned this
│ prompts/          │  session (used to seed the next session's context).
│ persona_update_v6 │
└───────────────────┘
       │
       ▼
┌───────────────────┐    ┌───────────────────┐
│ MITI 4.2 JUDGE    │    │ ESC JUDGE         │  Both run automatically at
│ eval/judge.py     │    │ eval/esc_judge.py │  session end against the JUDGE
│ (4 globals × 1-5) │    │ (6 dims × 1-5)    │  endpoint (separate from main
└───────────────────┘    └───────────────────┘  to avoid self-rater bias).
       │                          │
       └──────────┬───────────────┘
                  ▼
        ┌───────────────────┐
        │ TTM TRANSITION    │  Pure-Python (no LLM). Replays per-turn TTM
        │ RATE              │  snapshots → counts forward transitions
        │ eval/metrics.py   │  (precontempl→contempl→prep→action), regressions,
        │                   │  turns_to_<src>_to_<dst>.
        └───────────────────┘
```

**Reproducibility.** Every LLM call is seeded from `hash((profile_id, session_id, system, turn_id, call_role))`. Every call is appended as a JSONL record (request, response, parsed output, latency, attempt index, seed) under `src/help_e/logs/{profile}/session_{n}/turn_{NNN}.jsonl`. The same `(profile, system, sessions, turns, seed_salt)` always produces the same transcripts.

---

## System ablations

All four active systems share the **same** v6 driver (`session_driver_v6`), the **same** simulator (`mind1_v6` + `session_context`), the **same** judges, and the **same** 4-stage TTM and 8-value user-intent enums. The only thing that varies is what each `turn_fn` does with the graph.

| System | turn_fn | Graph used | What it sees | LLM calls / turn |
|---|---|---|---|---|
| **v1** — floor | `baselines/v1_history.py` | none — no inference, no recompute | Last N user turns + persona only | 1 (response) |
| **v3** — mid-tier | `baselines/v3_ttm_from_summary.py` | per-problem free-text summaries + TTM | v1 + per-problem summaries + TTM stage | 2 (combined extract+summary+TTM, response) |
| **v4** — graph-only | `baselines/v4_obs_ttm.py` | full attribute graph + TTM, **no** problem-problem edges, **no** merged instruction | v3 + per-attribute observed values + recomputed levels | 3 (inference, recompute, response) |
| **v6** — full system | `baselines/v6_full.py` | full attribute graph + **typed problem-problem edges** + merged instruction CoT | v4 + top-S graph-close problems + edge-weight context | 3 (inference, recompute, merged response) |

The ablation answers: *what does each layer of structure (summary → graph → typed edges) actually buy you in MITI / TTM-transition / ESC?*

---

## Evaluation — three criteria

The v6 redesign collapses evaluation to **three orthogonal criteria**. All three run automatically at session end as part of `python -m help_e.run` — no separate `--run-judge` step. Aggregation helpers + statistical tests (Wilcoxon, Holm–Bonferroni, mixed-effects) live in `eval/metrics.py`.

### §1.a — MITI 4.2 adherence (counselor speaks like an MI counselor?)

`eval/judge.py` (call_role `miti_judge`). One LLM call per session against the JUDGE endpoint. Reads the full session transcript and scores the **MITI 4.2 standard 4 globals** on a 1–5 scale:

| Global | What it measures |
|---|---|
| **cultivating_change_talk** | How well the chatbot encourages the client's own arguments for change |
| **softening_sustain_talk** | How well the chatbot avoids arguing against the client's resistance |
| **partnership** | Collaborative stance vs. expert-on-pedestal |
| **empathy** | Accurate understanding of the client's perspective |

### §1.b — TTM state-transition rate (user actually progresses?)

`eval/metrics.py` — pure-Python, no LLM, deterministic. Replays per-turn TTM snapshots from v6's `turn_traces`. Per problem the trace touched, it reports `first_seen_stage`, `last_seen_stage`, `reached_action`, `regressions` (count of consecutive snapshots where stage went backwards), and `turns_to_<src>_to_<dst>` for each forward transition.

Forward transitions tracked: **precontemplation → contemplation**, **contemplation → preparation**, **preparation → action**.

Per-profile aggregates: `n_problems_reached_action`, `pct_reached_action`, `mean_turns_to_action`, mean turns per transition, total regressions. **This is the headline metric for cross-system comparison** — v1/v3/v4/v6 all share the same trace format so it's directly comparable.

### §1.c — ESC adherence (user *feels* supported?)

`eval/esc_judge.py` (call_role `esc_judge`). One LLM call per `(profile, system)` after the run. Scores each session 1–5 on six Emotional Support Conversation dimensions: **empathy**, **understanding**, **helpfulness**, **autonomy_respect**, **non_judgment**, **willingness_to_continue**. Per dimension, the judge cites 2–3 specific turns with short excerpts and one-sentence reasoning before assigning the score.

### Inspection helpers

```bash
python -m help_e.eval.matrix_report      # profile × system × session matrix (MITI / TTM / ESC)
python -m help_e.eval.ablation_report    # cross-system comparison
python -m help_e.eval.view_profile P01   # inspect one profile incl. stage_transitions
python -m help_e.eval.smoke_v6           # mini end-to-end smoke (asserts ≥1 TTM transition)
```

---

## Repository structure

```
src/help_e/
├── run.py                          # CLI entrypoint — `python -m help_e.run --system {v1,v3,v4,v6}`
├── session_driver_v6.py            # Active driver (used by ALL four systems)
├── session_driver.py               # Legacy driver (kept only for the v1-v5 UI demo path)
├── profile_spec.py                 # ProfileSpec / RunConfig dataclasses + profile loader
├── config.py                       # Runtime config: env vars, enums, temp / retry / max-token tables
├── llm_client.py                   # OpenAI-compat client, JSON-schema enforcement, JSONL logging
├── curriculum.py                   # Pre-baked curriculum (eligible problems + session contexts per profile)
│
├── graph_v6.py                     # Active problem-centric graph schema
├── graph.py                        # Legacy bipartite graph (kept for the legacy UI path)
├── retrieval_v6.py                 # Active rule-based retrieval bundle
├── mi_selector_v6.py               # Active MISC strategy selector (TTM → candidate set)
├── instruction_response_simple.py  # Response prompt for v1/v3/v4 (single-stage)
├── instruction_response_v6.py      # Merged instruction+response CoT for v6
│
├── baselines/                      # Per-system turn functions
│   ├── v1_history.py               # v1 — history-only
│   ├── v3_ttm_from_summary.py      # v3 — summaries + TTM
│   ├── v4_obs_ttm.py               # v4 — full graph + TTM, no problem-problem edges
│   └── v6_full.py                  # v6 — full system
│
├── prompts/                        # Modular prompt templates
│   ├── inference.py                # §1 inference prompt (v6 + v4)
│   ├── recompute.py                # §3 attribute level recomputation (v6 + v4)
│   ├── persona_update_v6.py        # session-end persona refinement
│   ├── common_v6.py                # MISC vocabulary / OARS / banned-opener constants
│   └── common.py                   # PROJECT_IDENTITY, MI_STYLE_RULES, dialog formatting
│
├── simulator/                      # User simulator stack (shared across all systems)
│   ├── mind1_v6.py                 # Per-turn user responder (8-intent taxonomy)
│   ├── session_context.py          # Session-opening context (used by mind1_v6)
│   └── mind1.py                    # Legacy v1-v5 simulator (kept for legacy UI demo)
│
├── eval/                           # Evaluation
│   ├── judge.py                    # MITI 4.2 session-level judge (4 globals × 1-5)
│   ├── esc_judge.py                # ESC session-level judge (6 dims × 1-5)
│   ├── metrics.py                  # MITI / TTM / ESC aggregation + statistical tests
│   ├── matrix_report.py            # profile × system × session matrix report
│   ├── ablation_report.py          # cross-system comparison report
│   ├── view_profile.py             # per-profile inspection helper
│   ├── v6_loader.py                # Load v6 transcripts / runs into Python objects
│   ├── smoke_v6.py                 # Mini end-to-end smoke test
│   └── backfill_esc.py             # Backfill ESC scores onto existing transcripts
│
├── knowledge/
│   ├── mi_mapping.md               # legacy v1-v5 (TTM, HBM) → MI candidates
│   └── mi_mapping_v6.md            # active TTM → MISC strategy candidate rules
│
├── data/
│   ├── profiles/                   # 30 synthetic personas P01.yaml … P30.yaml + _manifest.json
│   ├── attributes.md               # 11-type attribute inventory + persona field defs
│   ├── problems.md                 # 20-problem vocabulary
│   └── seed_profiles.py            # Offline pipeline: EmoCare → persona YAML
│
├── ui/                             # FastAPI demo UI + static frontend
│   ├── server.py                   # `python -m help_e.ui.server` (defaults :8000)
│   └── static/
│       └── index.html
│
├── docs/
│   └── technical_report.md         # Internal technical write-up
│
├── graphs/, graphs_v6/             # Output: per-session graph snapshots
├── transcripts/                    # Output: per-session JSONL transcripts
└── logs/                           # Output: per-call LLM request/response logs (one JSONL per turn)

scripts/                            # Operational scripts
├── run_all.sh                      # Orchestrate the full v1×v3×v4×v6 sweep
├── run_v6_local.sh                 # Local Ollama+vLLM run
├── run_v6_p8888.sh                 # LightningAI run on a specific port
├── run_v6_fireworks.sh             # Fireworks AI alternative deploy
├── check_endpoints.py              # Pre-flight check that all 3 endpoints respond
└── generate_curriculum.py          # One-time pre-experiment curriculum generation
```

---

## Graph schema (v6)

The active graph (used by v4 and v6, with v1/v3 ignoring it) is a **problem-centric typed graph**:

- **PersonaNode** — 8 fields: demographics, personality_traits, core_values, core_beliefs, support_system, hobbies_interests, communication_style, relevant_history.

- **ProblemNode** — one per active problem (drawn from the fixed 20-problem vocabulary). Carries:
  - `ttm_stage` ∈ {precontemplation, contemplation, preparation, action}
  - `mention_count` and `last_mentioned_turn`
  - **7 *level* attributes** with `current_level` ∈ {low, medium, high, unknown}: perceived_severity, perceived_susceptibility, perceived_benefits, perceived_barriers, self_efficacy, cues_to_action, motivation
  - **4 *non-level* attributes** (free-text observation lists): coping_strategies, past_attempts, triggers, goal
  - per-attribute `information_stack` of append-only observations with timestamps

- **Problem-problem edges** (v6 only) — typed relations, each carrying confidence ∈ {low, medium, high} (weights 0.5 / 0.75 / 1.0) and recency-weighted edge weight using a half-life on a global turn index. Relation types:
  - `causal`, `effect`, `reinforcing`, `conflicting`, `shared_trigger`, `shared_barrier`, `shared_goal`, `unclear_but_related`

---

## Installation

### Prerequisites

- **Python ≥ 3.10**
- One or more OpenAI-compatible LLM endpoints (LightningAI / vLLM / Ollama / SGLang / TGI / cloud — anything that speaks `/v1/chat/completions`).

### Install

```bash
git clone https://github.com/MaitryBhavsar/Help-E-ClassProject2026NLP.git
cd Help-E-ClassProject2026NLP

python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

Set the import path so Python picks up the package under `src/`:

```bash
export PYTHONPATH="$PWD/src:$PYTHONPATH"
```

---

## LLM backend setup (LightningAI / vLLM / Ollama)

HELP-E uses up to **three** OpenAI-compatible endpoints, all configurable independently:

| Tier | Default | Used for |
|---|---|---|
| **MAIN**   | `https://lightning.ai/api` (lightning-ai/llama-3.3-70b) | Per-turn response, persona update, curriculum |
| **SIM**    | `https://lightning.ai/api` (lightning-ai/gpt-oss-20b) | User simulator (mind1_v6, session_context) |
| **JUDGE**  | `https://lightning.ai/api` (lightning-ai/llama-3.3-70b) | MITI 4.2 + ESC judges (kept separate to avoid self-rater bias when chatbot model is swapped) |

### Option A — LightningAI (default, no local GPU needed)

The repo defaults are already pointed at LightningAI. You only need to provide API keys:

```bash
# Chatbot side (response / curriculum / persona update)
export HELPE_VLLM_API_KEY=<lightning-ai key for the llama-3.3-70b account>

# (Optional) override the per-tier API keys if your judge / sim tenants differ
export HELPE_MAIN_API_KEY=<lightning-ai key for chatbot>
export HELPE_SIM_API_KEY=<lightning-ai key for gpt-oss-20b>
```

Sanity-check both endpoints respond:

```bash
python scripts/check_endpoints.py
```

### Option B — Local vLLM (high throughput; needs a GPU)

```bash
# MAIN model on :11436
python -m vllm.entrypoints.openai.api_server \
    --model meta-llama/Llama-3.3-70B-Instruct --port 11436

# SIM model on :11438
python -m vllm.entrypoints.openai.api_server \
    --model openai/gpt-oss-20b --port 11438

export HELPE_MAIN_OLLAMA_URL=http://127.0.0.1:11436
export HELPE_MAIN_MODEL=meta-llama/Llama-3.3-70B-Instruct
export HELPE_SIM_OLLAMA_URL=http://127.0.0.1:11438
export HELPE_SIM_MODEL=openai/gpt-oss-20b
export HELPE_JUDGE_OLLAMA_URL=http://127.0.0.1:11436     # share the main server
export HELPE_JUDGE_MODEL=meta-llama/Llama-3.3-70B-Instruct
export HELPE_VLLM_API_KEY=EMPTY                          # any non-empty string ok
```

### Option C — Local Ollama (CPU / Apple silicon friendly)

```bash
ollama pull llama3.3:70b
ollama pull gpt-oss:20b

# Two parallel Ollama instances
OLLAMA_HOST=127.0.0.1:11436 ollama serve   # MAIN + JUDGE
OLLAMA_HOST=127.0.0.1:11438 ollama serve   # SIM

export HELPE_MAIN_OLLAMA_URL=http://127.0.0.1:11436
export HELPE_MAIN_MODEL=llama3.3:70b
export HELPE_SIM_OLLAMA_URL=http://127.0.0.1:11438
export HELPE_SIM_MODEL=gpt-oss:20b
export HELPE_JUDGE_OLLAMA_URL=http://127.0.0.1:11436
export HELPE_JUDGE_MODEL=llama3.3:70b
```

For Ollama at concurrency, set `OLLAMA_NUM_PARALLEL=4` (or higher) before `ollama serve`.

### Mixed setups

The shipped `scripts/run_v6_p8888.sh` and `scripts/run_v6_local.sh` document the reference mixed setups (LightningAI for response + judge, local Ollama for simulator, etc.). They expect a `.env.local` at the repo root with the API keys.

### Pre-flight check

```bash
python -c "from help_e.llm_client import get_client; print(get_client().ping())"
# → {'main': True, 'sim': True}
```

---

## Configuration reference

All configuration is via environment variables. Defaults live in `src/help_e/config.py`.

### LLM endpoints

| Variable | Default | Purpose |
|---|---|---|
| `HELPE_MAIN_OLLAMA_URL` | `https://lightning.ai/api` | MAIN response endpoint |
| `HELPE_MAIN_MODEL` | `lightning-ai/llama-3.3-70b` | MAIN model identifier |
| `HELPE_SIM_OLLAMA_URL` | `https://lightning.ai/api` | SIM (user simulator) endpoint |
| `HELPE_SIM_MODEL` | `lightning-ai/gpt-oss-20b` | SIM model identifier |
| `HELPE_JUDGE_OLLAMA_URL` | `https://lightning.ai/api` | JUDGE endpoint (MITI + ESC) |
| `HELPE_JUDGE_MODEL` | `lightning-ai/llama-3.3-70b` | JUDGE model identifier |
| `HELPE_VLLM_API_KEY` | `EMPTY` | Bearer token (Ollama ignores; vLLM enforces if `--api-key` set; LightningAI requires) |
| `HELPE_MAIN_API_KEY` / `HELPE_SIM_API_KEY` | (falls back to `HELPE_VLLM_API_KEY`) | Per-tier override when tenants differ |
| `HELPE_TIMEOUT_S` | `600` | Per-request timeout in seconds |

### Retrieval / selection knobs

| Variable | Default | Purpose |
|---|---|---|
| `HELPE_N` | `5` | Last-N-turn window |
| `HELPE_K` | `3` | Top-K evidence entries per attribute edge |
| `HELPE_TOP_S` | `2` | v6: top-S graph-close problems surfaced in retrieval bundle |
| `HELPE_EDGE_ALPHA_M` | `0.4` | v6: mention-count weight in edge-weight formula |
| `HELPE_EDGE_ALPHA_A` | `0.6` | v6: attribute-evidence weight in edge-weight formula |
| `HELPE_RECENCY_HL` | `20` | v6: recency half-life in global-turn index |
| `HELPE_SEED_SALT` | `helpe-v1` | Reproducibility salt for per-call seeds |

### Output paths

| Variable | Default | Purpose |
|---|---|---|
| `HELPE_LOG_ROOT` | `src/help_e/logs` | Per-call LLM JSONL logs |
| `HELPE_GRAPH_DIR` | `src/help_e/graphs` | Legacy (v1-v5) graph snapshots |
| `HELPE_GRAPH_V6_DIR` | `src/help_e/graphs_v6` | Active graph snapshots |
| `HELPE_TRANSCRIPT_DIR` | `src/help_e/transcripts` | Session transcripts |

---

## Running HELP-E (step by step)

All commands assume the install + `PYTHONPATH` setup above and that your three endpoints are reachable.

### 1. Interactive UI (chat demo)

```bash
python -m help_e.ui.server
# → opens http://127.0.0.1:8000
```

In the browser: pick a profile + system, chat. The UI shows the retrieval bundle, candidate strategies, chosen strategies, and response trace per turn.

Optional flags:
```bash
python -m help_e.ui.server --host 0.0.0.0 --port 8001 --reload
```

### 2. v6 — full system (primary)

```bash
# Single profile, 2 sessions × 6 turns
python -m help_e.run --system v6 --profile P01 --sessions 2 --turns 6

# Multiple profiles
python -m help_e.run --system v6 --profile P01 --profile P05 --profile P12

# All 30 profiles, with profile-level parallelism (default 4 concurrent)
python -m help_e.run --system v6 --all-profiles --sessions 2 --turns 6
```

The MITI 4.2 + ESC judges + TTM transition rate computation all run automatically at session end — no `--run-judge` needed.

### 3. v4 — full graph + TTM (no problem-problem edges, no merged instruction)

```bash
python -m help_e.run --system v4 --profile P01 --sessions 2 --turns 6
```

### 4. v3 — summary + TTM (mid-tier baseline)

```bash
python -m help_e.run --system v3 --profile P01 --sessions 2 --turns 6
```

### 5. v1 — history-only (floor baseline)

```bash
python -m help_e.run --system v1 --profile P01 --sessions 2 --turns 6
```

### 6. Full sweep across all four systems

The shipped `scripts/run_all.sh` orchestrates the full v1 × v3 × v4 × v6 × all-profiles sweep:

```bash
bash scripts/run_all.sh
```

Or do it inline:
```bash
for sys in v1 v3 v4 v6; do
    python -m help_e.run --system "$sys" --all-profiles --sessions 2 --turns 6
done
```

### 7. Profile-level parallelism

By default 4 profiles run concurrently. vLLM and LightningAI batch requests natively, so `N>1` yields near-linear speedup until the GPU saturates.

```bash
# Sequential (slowest, easiest to debug)
python -m help_e.run --system v6 --all-profiles --max-parallel-profiles 1

# More aggressive (for a beefy server)
python -m help_e.run --system v6 --all-profiles --max-parallel-profiles 8
```

### 8. Inspecting / re-aggregating evaluation

```bash
python -m help_e.eval.matrix_report      # profile × system × session matrix (MITI / TTM / ESC)
python -m help_e.eval.ablation_report    # cross-system comparison
python -m help_e.eval.view_profile P01   # one-profile inspection incl. stage_transitions
python -m help_e.eval.smoke_v6           # mini end-to-end smoke (asserts ≥1 TTM transition)
python -m help_e.eval.backfill_esc       # backfill ESC scores onto existing transcripts
```

### `help_e.run` flag reference

| Flag | Purpose |
|---|---|
| `--system {v1,v3,v4,v6}` | **Required.** Active ablation tier. |
| `--profile PID` | Profile id (e.g., `P01`). Repeat for multiple. |
| `--all-profiles` | Use every profile under `data/profiles/`. |
| `--sessions N` | Sessions per profile (default `4`). |
| `--turns N` | Turns per session (default `10`). |
| `--max-parallel-profiles N` | Profiles concurrently (default `4`; pass `1` for sequential). |
| `--run-judge` | Legacy no-op (judges always run at session end now). |
| `--log-level {DEBUG,INFO,WARNING,ERROR}` | Logging verbosity (default `INFO`). |
| `--fail-fast` | Abort the whole run on the first profile error. |

---

## Data assets

- **`data/profiles/`** — 30 synthetic user personas (`P01.yaml` … `P30.yaml`) + `_manifest.json`. Each YAML contains profile id, seed situation, primary problem, session arc, and persona draft.
- **`data/attributes.md`** — the 11-type attribute inventory + persona field definitions.
- **`data/problems.md`** — the 20-problem vocabulary used by inference and the graph.
- **`data/seed_profiles.py`** — the offline pipeline that generated the profiles (EmoCare → persona YAML).
- **`knowledge/mi_mapping_v6.md`** — `(TTM stage) → MISC strategy candidate set` rule table consumed by `mi_selector_v6.py`.

### Enumerations

| Enum | Values |
|---|---|
| **TTM stages (4)** | precontemplation, contemplation, preparation, action |
| **User intents (8)** | express_emotion, seek_validation, seek_information, deliberate_decision, request_plan, report_action, resistance, small_talk |
| **Levels (4)** | low, medium, high, unknown |
| **OARS skills** (always-on) | open_question, affirm, simple_reflection, summary |
| **MISC selectable codes (10)** | support, facilitate, complex_reflection, reframe, inform_with_permission, raise_concern_with_permission, evoke, closed_question, advise_with_permission, structure |
| **MISC anti-patterns** (banned) | direct, confront, warn, advise/inform/raise_concern_without_permission |
| **Attribute types — *level* (7)** | perceived_severity, perceived_susceptibility, perceived_benefits, perceived_barriers, self_efficacy, cues_to_action, motivation |
| **Attribute types — *non-level* (4)** | coping_strategies, past_attempts, triggers, goal |
| **Persona fields (8)** | demographics, personality_traits, core_values, core_beliefs, support_system, hobbies_interests, communication_style, relevant_history |
| **Problem-problem relations (8)** | causal, effect, reinforcing, conflicting, shared_trigger, shared_barrier, shared_goal, unclear_but_related |
| **Problem vocabulary (20)** | academic_pressure, work_stress, sleep_problems, procrastination, general_anxiety, low_self_esteem, perfectionism, social_anxiety, loneliness, conflicts_with_partner, breakup_aftermath, conflicts_with_parents, conflicts_with_friends, financial_stress, career_uncertainty, caregiver_stress, grief_of_loved_one, health_anxiety, body_image_concerns, life_transition |

---

## License

This project is licensed under the GNU General Public License v3.0 — see [`LICENSE`](./LICENSE) for the full text.

## Acknowledgements

HELP-E is developed as part of PhD research on multi-turn emotional-support dialogue. The MISC vocabulary is derived from Moyers, Manuel, & Ernst (2014); the MITI 4.2 globals follow the same authors; the TTM is from Prochaska & DiClemente (1983); MI principles follow Miller & Rollnick (2013); the Health Belief Model is the classic Hochbaum/Rosenstock formulation. The synthetic profiles are seeded from the EmoCare conversations dataset. Citation details for the accompanying paper will be added when released.
