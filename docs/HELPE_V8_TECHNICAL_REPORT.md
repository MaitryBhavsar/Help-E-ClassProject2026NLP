# HELP-E v8 — Complete Technical Report

*A full, plain-language walkthrough of v8 of the HELP-E system: what's the same as v7, what's different, and why the changes matter.*

*Written so a reader without a computer-science or behavioral-science background can read it from start to finish.*

> **This document is a companion to `HELPE_V7_TECHNICAL_REPORT.md`.** v8 is **v7 with two specific changes** to the retrieval layer. Everything else — the graph, the ESC-first response contract, the MITI/ESC judges, the persona update, the simulator, the run scripts, the seed-derivation rule — is **identical to v7 by design**. So this document covers the deltas in depth and points at the v7 doc for everything that hasn't changed. This revision (2026-05-05) reflects the current code at `src/help_e/baselines/v8_full.py` and the May-2026 MAIN-tier swap to llama-3.3-70b.

---

## Contents

1. [What v8 Is — In One Page](#1-what-v8-is--in-one-page)
2. [The Two Things v8 Changes (and Three Things It Keeps)](#2-the-two-things-v8-changes-and-three-things-it-keeps)
3. [The Underlying Question — WDC Edge-Walk vs. Dense RAG](#3-the-underlying-question--wdc-edge-walk-vs-dense-rag)
4. [The v8 Per-Turn Pipeline](#4-the-v8-per-turn-pipeline)
5. [Agent Q — The Retrieval Query Generator](#5-agent-q--the-retrieval-query-generator)
6. [The RAG Layer — Corpus, Encoder, MMR](#6-the-rag-layer--corpus-encoder-mmr)
7. [The v8 Evidence Pack — What Agent 5 Reads](#7-the-v8-evidence-pack--what-agent-5-reads)
8. [What's Identical to v7 (and Why)](#8-whats-identical-to-v7-and-why)
9. [Parallelism — Where Agent Q Hides Its Wallclock](#9-parallelism--where-agent-q-hides-its-wallclock)
10. [Reproducibility, Logging, Three-Tier LLM Routing](#10-reproducibility-logging-three-tier-llm-routing)
11. [Worked End-to-End Example](#11-worked-end-to-end-example)
12. [What Is Genuinely Novel in v8 (vs. v7 and vs. Prior RAG Work)](#12-what-is-genuinely-novel-in-v8-vs-v7-and-vs-prior-rag-work)
13. [Trade-offs and Limitations](#13-trade-offs-and-limitations)
14. [Glossary (v8-specific terms)](#14-glossary-v8-specific-terms)

---

## 1. What v8 Is — In One Page

**v8 is v7 with dense RAG retrieval over the user's lossless evidence corpus, replacing v7's weighted-degree-centrality (WDC) edge-walk + per-edge-summary path.**

Concretely, v8 makes two structural changes to the v7 pipeline:

1. **Drops the EdgeSummaryAgent (Agent 3c).** No per-edge `summary_text` is maintained. The chronological audit stacks (every attribute audit entry, every connection entry) are still kept on the graph — never rewritten, never dropped — but they are not summarized into per-edge prose.

2. **Adds the QueryAgent (Agent Q) + a dense RAG retriever.** Agent Q is a small LLM that produces a single retrieval query string from the structured signals the other agents have already produced (user_intent, mi_for_user_intent, system_intent per active problem). The query is embedded with a sentence-transformers MiniLM model, scored against every chunk in the user's audit corpus by cosine similarity, and the top-K chunks are diversified via MMR. Those chunks are surfaced to the response generator as the `# RETRIEVED EVIDENCE` block.

Everything else — every other agent, the graph data structure, the evaluation criteria, the run scripts, the simulator — is identical to v7. The MAIN-tier ResponseAgent's SYSTEM prompt and JSON schema are reused verbatim; only the USER prompt is reshaped because the evidence_pack itself has changed shape.

**Why these changes?** v7's WDC edge-walk works well for problems whose connection has already been written into the per-edge summary, but it can miss old, mechanism-specific evidence the user voiced once five turns ago and never repeated. Dense retrieval over the raw audit corpus directly surfaces those moments — when they're relevant. v8 is the experiment in: *can RAG over the lossless evidence stack find the right past moments without the LLM-maintained edge summary?*

---

## 2. The Two Things v8 Changes (and Three Things It Keeps)

| Layer | v7 | **v8** |
|---|---|---|
| Per-turn LLM calls | 1 + 2 + 1×N + 1×M + 1×E + 1 + 1  (where E = edges with new entries) | **1 + 2 + 1×N + 1 + 1×M + 1 + 1**  (E term is gone, replaced by exactly one Agent Q call per turn) |
| Phase 3c (edge summary) | **Present** — Agent 3c × E parallel calls | **Removed entirely** |
| Retrieval-query generation | Implicit — the structured signals were used directly to drive WDC over edge weights | **New: Agent Q (SMALL)** produces a curated retrieval query from intent + system_intent + active problems |
| Retrieval method | Weighted-degree centrality over the edge graph (relative-threshold τ on aggregate score) | **Dense MiniLM cosine over a flat corpus** of every audit entry + every connection entry, with MMR (λ=0.5) for diversity |
| Edge weights | Used to gate WDC retrieval | Still computed (cheap diagnostics) but **don't gate retrieval** anymore |
| Edge `summary_text` | Maintained by Agent 3c, surfaced in evidence_pack | **No longer maintained, no longer surfaced** |
| `evidence_pack` shape | main_problem with full attribute summary_text + per-edge summary blocks | **Graph state only on main + RAG-retrieved raw chunks block** |
| Attribute `summary_text` | Maintained by Agent 3a, surfaced to Agent 5 | Still maintained by Agent 3a (Agent 3b needs it for TTM) but **not surfaced to Agent 5** |

| What stays exactly the same as v7 | Why |
|---|---|
| Agents 1, 2, 3a, 3b, 5, X, P | Identical roles, identical prompts, identical schemas. Level-update + TTM-stage logic is unchanged. |
| `ProblemGraphV7` data structure | Same nodes, same edges, same audit stacks. The edge `summary_text` field stays in the dataclass for backward compatibility — v8 just doesn't write it. |
| ResponseAgent's SYSTEM prompt + response schema | `RESPONSE_V8_SCHEMA = RESPONSE_V7_SCHEMA` (literally aliased in `instruction_response_v8.py:38`). Validators (banned openers, banned phrases, MISC code membership) all unchanged. |
| MITI 4.2 + ESC judges | Identical entry points and rubrics. |
| Three-tier LLM routing (MAIN / SMALL / SIM / JUDGE) | Same env-var contract; v8 script is a near-copy of `run_v7_lightning_70b.sh`. MAIN = llama-3.3-70b (after the May-2026 swap, same as v7). |
| Per-call jsonl audit log + hash-seeded sampling | Same shape, same seed-derivation rule. |
| Profile YAMLs, simulator, session driver | Untouched. |
| Carry-forward fallback for Agent 2 | Same `build_agent2_carry_forward_output` safety net used in v7 — when Agent 2 exhausts retries, the v8 turn synthesizes a "no new evidence" Agent-2 output rather than crashing. |

---

## 3. The Underlying Question — WDC Edge-Walk vs. Dense RAG

This is the heart of v7-vs-v8 and the only big design idea you need to hold to read the rest. Skip if you already get it.

**v7's idea:** "When the bot is about to respond, look at the graph and walk to whichever non-seed candidates are *connected enough* to the current main problem (their aggregate edge weight to the seeds is above τ × max). For each such edge, hand the responder both the per-anchor connection records and a running NL summary of the edge's full history. The responder reads structured cluster information."

**v8's idea:** "When the bot is about to respond, *index every raw evidence chunk* (every attribute audit entry, every connection entry) ever written for this user. Compose a search query that captures the user's expressed intent and the bot's nudge intent. Run a dense similarity search. Hand the responder the top-K most relevant chunks, regardless of which edge they live on."

The two approaches answer the same question — *what past evidence is relevant right now?* — with different inductive biases:

| Bias | v7 WDC edge-walk | v8 dense RAG |
|---|---|---|
| Cluster-focused vs. moment-focused | Cluster (you see what aggregates to the current problems) | Moment (you see specific past utterances similar to the current frame) |
| Compression style | Per-edge LLM summary (one sentence per turn that added something) | None — raw chunks land in the prompt with their s.t anchor |
| Cost per turn | Up to E small Agent 3c calls | Exactly 1 small Agent Q call + cheap embedding lookup |
| What's lost | Old turns can fade if no recent reinforcement (recency-decayed weight) | Cluster context is implicit (top-K may all come from one edge) |
| What's gained | Compressed prose that scales as audit stacks grow; aggregation across weak-but-real edges | Direct access to specific past quotes the user said |

Neither is uniformly better. v8 is the experimental fork: same codebase, same evaluation matrix, swap the retrieval layer, see whether the response quality and TTM transition rate move.

---

## 4. The v8 Per-Turn Pipeline

The v8 turn function lives in `src/help_e/baselines/v8_full.py:532` (`v8_turn_fn`).

```
┌────────────────────────────────────────────────────────────────────┐
│                       USER MESSAGE ARRIVES                         │
└──────────────────────────────┬─────────────────────────────────────┘
                               ▼
         ┌─────────────────────┴─────────────────────┐
         │                                           │
PHASE 1 ‖▼ Agent 1 (SMALL, ~1 s)         PHASE 1 ‖▼ Agent 2 (BIG, ~10–20 s)
   user_intent +                            current_problems +
   user_intent_phrase +                     main_problem (sticky) +
   mi_for_user_intent                       problem_attribute_entries +
   (MISC code from shortlist)               problem_attribute_connections
         │                                           │
         │                              (carry-forward fallback if Agent 2 exhausts retries)
         └─────────────────────┬─────────────────────┘
                               ▼
                PHASE 2 — Pure Python (no LLM, ~1 ms)
                    Apply Agent 2 to graph: register problems,
                    append audit entries, stack typed connection
                    entries with usefulness flags. Same as v7.
                               ▼
        ┌──────────────────────┼──────────────────────┐
PHASE 3 ‖▼ Agent 3a × N        │   (parallel SMALL)   │
   for EACH problem with NEW   │                      │
   info this turn:             │                      │
   per-attribute summary,      │                      │
   level + level_reasoning +   │                      │
   level_change_confidence,    │                      │
   new_info_useful flag.       │                      │
        │                      │                      │
PHASE Q ‖▼ Agent Q × 1         │   (parallel SMALL)   │  ← NEW IN v8
   produces:                   │                      │
   { "retrieval_query":        │                      │
     "<10–30 token query>" }   │                      │
        └──────────────────────┴──────────────────────┘
                               ▼
                PHASE 4 ‖ Agent 3b × M  (where M = problems whose
                    level actually changed in 3a, often 0 or 1)
                    new TTM stage + ttm_reasoning +
                    system_intent + mi_for_system_intent.
                    Same as v7.
                               ▼
                PHASE 5 — Pure Python (no LLM, ~30–80 ms)
                    Recompute edge weights (still done, used only
                    for diagnostics in v8 — not for retrieval).
                    Build evidence_pack (v8 shape):
                      - graph state for main problem
                      - 1-line others
                      - rag_retrieved_chunks: top-K MMR-diversified
                        MiniLM cosine hits over the union of all
                        attribute audit entries + connection entries.
                    Query = Agent Q's output (or deterministic
                    concat fallback if Q's call failed).
                               ▼
                PHASE 6 — Agent 5 (BIG, ~15–30 s)
                    R1 → R2 → R3 → R4 in ONE structured output.
                    SAME schema, SAME validators as v7. Only the
                    USER prompt changed (different evidence_pack).
                               ▼
                PHASE 7 — Agent X (SMALL, fire-and-forget, ~1 s)
                    Refresh rolling_summary_5turns. Same as v7.
                               ▼
                       BOT RESPONSE → SIMULATOR
```

**Key shifts vs. v7's diagram:**

- **Phase Q is new** and runs *inside the same parallel pool* as Phase 3a (`thread_name_prefix="v8_p3q"`, `max_workers=min(5, 1+N)`). Agent Q is launched alongside the 3a calls and finishes around the same time, so it adds zero serial wallclock.
- **No Phase 3c.** The edge-summary phase is gone entirely; v8 never calls Agent 3c.
- **Phase 5 is bigger.** Instead of just `assemble_evidence_pack`, it now also runs the embedding lookup + MMR top-K over the full corpus. This is still pure Python, ~30–80 ms thanks to the per-chunk encoding cache (each chunk encoded once across the whole run; only the query is re-encoded each turn).

Total LLM calls per turn: Agent 1 + Agent 2 + Agent 3a × N + Agent Q + Agent 3b × M + Agent 5 + Agent X = same count as v7 (v7 had Agent 3c × E in place of Agent Q).

---

## 5. Agent Q — The Retrieval Query Generator

**File**: `src/help_e/prompts/agentq_retrieval_query.py`. **Tier**: SMALL. **Frequency**: exactly one call per turn.

### 5.1 What Agent Q reads (structured signals only — no raw user message)

This is the deliberate choice. Agent Q does NOT see the user's current utterance. It sees:

- `user_intent` (enum) — what kind of need Agent 1 classified.
- `user_intent_phrase` (≤25 words) — Agent 1's NL elaboration.
- `mi_for_user_intent` (MISC code) — what move Agent 1 picked.
- `main_problem` (one of 22) — the sticky main from Agent 2.
- `main_system_intent` (1-line) — what the bot is nudging *for the main problem* on the next turn.
- `main_mi_for_system_intent` (MISC code).
- `other_current_problems` — for each, name + system_intent_1line + mi_for_system_intent.

Why no raw message? Two reasons:

1. **The structured signals are already "what the user is expressing"** — Agent 1 distilled the user message into a classifier output and a one-line phrase. Agent Q working from those is a clean separation: Agent 1 reads raw text, Agent Q projects abstract intent into search terms.
2. **Agent Q's job is search expansion, not paraphrase.** Its output is a bag-of-tokens dense-retrieval query, not an MI-correct response. Feeding it the user's actual words would invite paraphrase ("user said deadline is on fire"), which is bad for retrieval — it biases the retriever toward duplicates of the current turn instead of *relevant past moments*.

### 5.2 What Agent Q writes

```json
{ "retrieval_query": "<10-30 token bag-of-words string>" }
```

A single short string (1–300 chars by schema). The prompt explicitly tells Agent Q to:
- Include emotional/cognitive themes implied by the user_intent (venting → severity, exhaustion, frustration; deliberate → weighing, options, ambivalence; request_plan → coping, past attempts, capacity).
- Include theme tokens aligned with the chosen MI techniques (complex_reflection → underlying themes; evoke → motivation, change-talk; affirmation → effort, strengths; advise → options, considerations).
- Include the active problem names verbatim (these are graph keys — exact matches help even with dense retrieval).
- Stay in 10–30 tokens (longer hurts dense retrieval signal-to-noise).

### 5.3 Three concrete examples (from the prompt's own training material)

**Example 1 — venting about insomnia:**
```
INPUTS:
  user_intent: express_emotion
  user_intent_phrase: "venting about exam-driven sleep loss"
  mi_for_user_intent: complex_reflection
  main_problem: sleep_problems
  main_system_intent: "explore what's keeping you awake"
  main_mi_for_system_intent: complex_reflection
  other_current_problems:
    - academic_pressure (system_intent_1line: "name the deadline weight")

OUTPUT:
  retrieval_query: "sleep_problems academic_pressure exam stress
                    rumination triggers severity peaks
                    past venting moments insomnia onset
                    underlying exhaustion themes"
```

**Example 2 — request_plan about a manager conversation:**
```
INPUTS:
  user_intent: request_plan
  user_intent_phrase: "asking how to talk to manager"
  mi_for_user_intent: advise_with_permission
  main_problem: academic_pressure
  main_system_intent: "affirm agency in considering an ask"
  main_mi_for_system_intent: support
  other_current_problems: []

OUTPUT:
  retrieval_query: "academic_pressure manager interaction
                    coping strategies past accommodation
                    self_efficacy agency moments effort
                    support evidence considered options"
```

**Example 3 — deliberate_decision about a relationship:**
```
INPUTS:
  user_intent: deliberate_decision
  user_intent_phrase: "weighing whether to keep dating partner"
  mi_for_user_intent: complex_reflection
  main_problem: conflicts_with_partner
  main_system_intent: "reflect both sides without nudging"
  main_mi_for_system_intent: complex_reflection

OUTPUT:
  retrieval_query: "conflicts_with_partner partner ambivalence
                    both sides moments perceived_severity
                    perceived_barriers self_efficacy decision
                    weighing past relational episodes"
```

Notice: each query mixes (a) the literal problem names so MiniLM can token-match exactly, (b) HBM/MI theme words that semantically align with what the responder will need ("ambivalence" for a deliberate user, "severity peaks" for a vent, "agency" for a request_plan), and (c) some general emotional tokens. The result is a compact but theme-rich search target.

### 5.4 Deterministic fallback

If Agent Q's LLM call fails (timeout, schema error, exhausted retries), `_safe_fallback` returns a query built deterministically from the structured signals: `user_intent_phrase + main_problem name + other problem names + main_system_intent`. Less polished than Agent Q's output but never empty (last resort is the literal string `"user evidence"`). The retriever always has *something* to search with, and the trace records `rag_query_source = "fallback_concat"`.

### 5.5 The acceptable staleness trade-off

Agent Q runs in **Phase 3+Q**, parallel with Agent 3a. At that moment, Agent 3b hasn't fired yet — so the `system_intent` Agent Q sees on the graph is the **previous turn's** value. If Agent 3b updates it later this turn, Q's query was based on a now-stale system_intent.

This is documented in the source as an explicit trade-off:
> *"Agent Q reads system_intent + mi_for_system_intent from the graph at Phase 2 end — pre-Phase-4 values. When Agent 3b fires this turn (only when a level changed), the system_intent Q saw is one turn stale; otherwise it's correct. Trade-off accepted in exchange for hiding Q's wallclock behind 3a."*

In practice this matters less than it might sound:
- 3b only fires on turns where 3a actually changed a level — usually 0 or 1 of N current problems.
- Even when 3b fires, the previous turn's system_intent is usually a close themic match to the new one (TTM stages don't usually leap; system_intent stays in the same neighborhood).
- The cost of waiting for 3b would be pure serial latency on every turn, even when 3b doesn't fire.

Net: Q runs in parallel with 3a, occasionally with one-turn-stale signals on TTM-shift turns.

### 5.6 Why a separate agent (vs. extending Agent 1)

Agent 1's job is to classify the user's intent and pick the opening MI move. Agent Q's job is search expansion. Distinct prompt, distinct optimization target, different read of the same source.

Could Q be folded into Agent 1 to save one LLM call? Yes. The deliberate choice to keep them separate is for cleaner iteration: when we want to tune retrieval-query construction, we edit one prompt; we don't have to be careful not to perturb Agent 1's MI behavior. v6 → v7 tightening showed that conflating jobs in one prompt makes both harder to tune.

---

## 6. The RAG Layer — Corpus, Encoder, MMR

**File**: `src/help_e/rag_v8.py`.

### 6.1 The corpus — every audit chunk, period

`extract_corpus(graph)` flattens the graph into a list of dicts. Each dict is a "chunk" — one piece of evidence indexed for retrieval. Two shapes:

**Attribute chunk** (one per audit entry, level or non-level):
```
{
  "type": "attribute_entry",
  "attribute_kind": "level" | "non_level",
  "problem": "academic_pressure",
  "attribute": "perceived_severity",
  "anchor": "s1.t4",                 ← session.turn label
  "quote": "I can't keep this up",   ← user's verbatim span
  "inferred": "user sees workload as unsustainable",
  "text": "<concatenation used by encoder>"
}
```

**Connection chunk** (one per problem-problem connection entry):
```
{
  "type": "connection_entry",
  "problem_a": "academic_pressure",
  "problem_b": "sleep_problems",
  "attribute_a": "triggers",
  "attribute_b": "triggers",
  "relation_type": "shared_trigger",
  "anchor": "s1.t12",
  "quote": "the cramming is what's keeping me up",
  "why": "late-night cramming fires both stress escalation and sleep onset failure",
  "text": "<concatenation used by encoder>"
}
```

The corpus is **never persisted separately** from the graph. It's re-extracted every turn (cheap — it's just a few list comprehensions over the audit stacks). This means the corpus is always exactly synchronized with whatever's on the graph at this moment — no stale index, no rebuild logic.

### 6.2 The encoder — sentence-transformers MiniLM

**Model**: `sentence-transformers/all-MiniLM-L6-v2` (384 dims, ~22M parameters, runs on CPU). Overridable via `HELPE_V8_DENSE_MODEL`.

**Cosine similarity**: embeddings are L2-normalized at encode time, so cosine reduces to a single dot product. Fast.

**Per-chunk encoding cache**: each chunk's `text` field is encoded *once* across the lifetime of the process. The cache is keyed on the text string. Audit and connection entries are append-only, so once a chunk is created, its text never changes — no cache invalidation needed.

**Per-turn cost** (after warmup):
- Query encode: ~10 ms (one short string).
- Chunk encodes: 0 ms (all cached).
- Cosine matmul: ~1–5 ms (corpus is small — typically 30–200 chunks across all problems).
- MMR selection: ~10–50 ms.

Total: ~30–80 ms of pure Python per turn. Negligible vs. the 30s+ Agent 5 LLM call.

### 6.3 MMR — Maximal Marginal Relevance (Carbonell & Goldstein 1998)

Plain top-K retrieval over cosine similarity has a known failure mode: it returns near-duplicates. If five turns ago the user said *"the deadline is on fire"* and three turns ago they said *"I can't keep this up"*, plain top-K against a venting query might return both turns' severity audits plus three more severity audits from earlier sessions — ignoring perceived_barriers, self_efficacy, or persona evidence that would round out the picture.

MMR fixes this. It iteratively picks the chunk that maximizes:

```
λ · sim_to_query  −  (1 − λ) · max_{p ∈ already_selected} sim_to_p
```

- `λ = 1.0` reduces MMR to plain top-K.
- `λ = 0.0` is pure diversity (ignores query).
- `λ = 0.5` (v8 default, `V8_MMR_LAMBDA` = 0.5, configurable via `HELPE_V8_MMR_LAMBDA`) is the textbook balanced setting.

After scoring, v8 keeps the top `V8_MMR_FETCH_K = 20` non-zero candidates by query similarity (overridable via `HELPE_V8_MMR_FETCH_K`), then runs MMR within that pool to pick the final `V8_RAG_TOP_K_DEFAULT = 8` chunks. Strictly non-positive cosine scores are dropped before MMR (no overlap or anti-correlated direction).

### 6.4 Why dense, why MMR, why not BM25 — and why no minimum-cosine floor

The implementation file has documented archive notes. An earlier v8 prototype used BM25 (a classical sparse retriever based on term frequency / inverse document frequency); it was archived to `_archive/retrieval_legacy/v8_bm25_backend.py`. The reasons for moving to dense:

- **Verbatim quotes are short.** The user's `supporting_utterance_span` is usually 5–15 tokens. BM25's IDF weighting struggles when the query and the chunk share only a couple of tokens. Dense embeddings capture meaning beyond exact term overlap.
- **Theme matching matters more than keyword matching.** Agent Q's queries include theme words ("rumination", "agency", "ambivalence") that may not appear in any user utterance verbatim. Dense retrieval can match on meaning.
- **Corpus is small.** With ~30–200 chunks, dense matmul is essentially free. BM25's scaling advantage doesn't matter.

The legacy minimum-cosine floor (`V8_MIN_COSINE_FLOOR_DEFAULT = 0.30`) was also archived (`_archive/retrieval_legacy/v8_min_cosine_floor.py`). The current code drops only strictly non-positive scores before MMR — no absolute threshold. The MMR λ-weighting plus the `fetch_k` cap already handle relevance well enough; an absolute floor was killing recall on early-session turns where every score was modest.

(Note: `src/help_e/graphrag_state.py` is a separate Tier-1 GraphRAG baseline used by `baselines/graphrag_baseline.py`. It is **not** part of the v8 pipeline — v8 operates over the v7 graph via `extract_corpus` only.)

---

## 7. The v8 Evidence Pack — What Agent 5 Reads

**File**: `src/help_e/instruction_response_v8.py`.

### 7.1 The schema is unchanged

`RESPONSE_V8_SCHEMA = RESPONSE_V7_SCHEMA` (literal alias at line 38). Same 8 required fields (`reasoning`, `mi_for_user_intent_used`, `mi_for_system_intent_used`, `r1`, `r2`, `r3`, `final_response`, `used_evidence`, `evidence_used`). Same validators (banned openers, banned phrases, MISC membership). The ResponseAgent's SYSTEM prompt is reused verbatim from v7.

What changed is **the USER prompt** — the block of evidence the responder reads — because the evidence_pack itself has changed shape.

### 7.2 The v8 evidence_pack

```
{
  "main_problem": {                            # graph state ONLY (no chronological summary)
    "name", "ttm_stage", "ttm_reasoning",
    "system_intent", "mi_for_system_intent",
    "current_levels": {
      <attr>: { level, level_reasoning, audit_anchors: ["s1.t4", "s2.t11", ...] },
      ...
    },
    "non_level_attribute_anchors": {
      <attr>: { audit_anchors: ["s1.t6", ...] },
      ...
    }
  },
  "other_current_problems": [
    { "name", "ttm_stage", "system_intent_1line" }, ...
  ],
  "rag_retrieved_chunks": [                    # ← the big change: top-K MMR chunks
    {type, problem, attribute, anchor, quote, inferred, score, ...},
    ...
  ],
  "rag_query": "<the actual query string used>",
  "rag_query_source": "agent_q" | "fallback_concat",
  "rag_corpus_size": <int>,
  "persona": <9-field persona>,
  "rolling_summary_5turns": "..."              # from Agent X
}
```

Key differences from v7's evidence_pack:
- **No `level_attributes.summary_text` on main.** Just current_level + level_reasoning + the list of audit anchors that shaped it (via `_dedup_audit_anchors`).
- **No `non_level_attributes.summary_text` on main.** Just the audit anchors.
- **No `problem_problem_connections` block** (no edge summaries to surface, no WDC neighbor edges).
- **New `rag_retrieved_chunks` block.** Top-K (default 8) chunks chosen by MMR over MiniLM cosine.
- **New diagnostics** (`rag_query`, `rag_query_source`, `rag_corpus_size`) for trace visibility.

### 7.3 How the retrieved evidence renders in Agent 5's prompt

The `# RETRIEVED EVIDENCE` block in the user prompt looks like:

```
# RETRIEVED EVIDENCE (MMR-diversified MiniLM cosine over your full
# memory of past turns; both attribute audits and problem-problem
# connections; chronologically anchored as sS.tT)

  [s1.t4 • perceived_severity@academic_pressure (level)]: "I can't keep this up"  ·  user sees workload as unsustainable
  [s1.t12 • academic_pressure ↔ sleep_problems (shared_trigger: triggers↔triggers)]  "the cramming is what's keeping me up"
  [s2.t3 • coping_strategies@academic_pressure (non_level)]: "splitting the work into 90-minute blocks helped last week"
  [s2.t8 • self_efficacy@academic_pressure (level)]: "I'm starting to think I can do this"  ·  emerging confidence
  ...
```

Each chunk is anchored at `sS.tT` so the responder can cite specific moments. Attribute chunks include both the verbatim `quote` and the `inferred` claim. Connection chunks include the relation_type + the attribute pair + the verbatim quote.

### 7.4 What "POSITIVE solution evidence" rule looks like in v8

The v7 ResponseAgent prompt has a strict rule: **PROBLEM evidence shapes interpretation; POSITIVE solution evidence shapes what you SAY.** This is unchanged in v8 — the SYSTEM prompt is the same. What changes in v8 is *how the responder finds positive evidence*: instead of reading per-edge summaries that bundle the relational story, the responder must spot positive evidence in the RAG chunks (audits of `coping_strategies`, `past_attempts`, `cues_to_action`, `motivation`, persona anchors).

This means v8 puts more pressure on the retriever: if positive evidence exists in the audit corpus but isn't surfaced in the top-K, the responder won't see it. Agent Q's query construction ("affirmation → effort, strengths") is meant to catch this — when the chosen MI is affirmation or evoke, the query explicitly biases toward positive-themed terms.

### 7.5 Why this is the cleanest minimal change

`instruction_response_v8.py` is a thin wrapper around v7's response infrastructure. The actual contract change is two surgical edits:

```python
# v7 main_problem block: full per-attribute summary_text + quotes
def _format_main_block(main):  # v7
    ... renders summary_text per attribute, level, reasoning ...

# v8 main_problem block: graph state only, no summary_text
def _format_main_block_v8(main):
    ... renders level + level_reasoning + audit_anchors ...
```

```python
# v7: assemble_evidence_pack returns connections with summary_text
# v8: _assemble_evidence_pack_v8 returns rag_retrieved_chunks
```

Everything else flows from those two edits.

---

## 8. What's Identical to v7 (and Why)

The v7 doc covers all of these in detail. Each one is **byte-identical** in v8 except where noted.

| Component | v8 status | See v7 doc § |
|---|---|---|
| Theory recap (MI, MISC, HBM, TTM) | unchanged | §2 |
| 22-problem vocabulary | unchanged (`config.PROBLEM_VOCAB`) | §2 |
| 7 + 4 HBM attribute split | unchanged (`LEVEL_ATTR_TYPES`, `NON_LEVEL_ATTR_TYPES`) | §2, §15 |
| Three-layer system architecture (sim / chatbot / judge) | unchanged | §4 |
| Three-tier LLM routing (MAIN llama-3.3-70b post-May-2026 swap) | unchanged (script just sets `--system v8`) | §4.1, §17 |
| Agent 1 — IntentAgent | unchanged (same prompt, same shortlist gating) | §6 |
| Agent 2 — InferenceAgent + carry-forward fallback | unchanged | §7 |
| Agent 3a — AttributeAgent | unchanged. Still maintains attribute summary_text — needed by Agent 3b for TTM. v8 just doesn't surface it to the responder. | §8 |
| Agent 3b — StageAgent | unchanged (same TTM rules, same shortlists) | §9 |
| Agent 5 — ResponseAgent SYSTEM prompt + schema | unchanged | §12 |
| Agent X — RollingSummaryAgent | unchanged | §13 |
| Agent P — End-of-session persona update | unchanged | §14 |
| `ProblemGraphV7` data structure | unchanged (edge.summary_text field exists but stays empty in v8) | §15 |
| Two MI picks per turn (mi_for_user_intent + mi_for_system_intent) | unchanged | §16 |
| Hash-seeded sampling | unchanged (different `system="v8"` produces different seeds; otherwise identical) | §17 |
| Per-call jsonl audit log | unchanged | §17 |
| Cross-session judge parallelization | unchanged | §18 |
| MITI 4.2 + ESC + TTM transition rate | unchanged judges, identical rubrics | §19 |
| Profile YAMLs + simulator | unchanged | (v6 doc §5) |

If any of these need re-reading, the v7 doc is the source.

---

## 9. Parallelism — Where Agent Q Hides Its Wallclock

The within-turn parallelism table for v8:

| Phase | What runs in parallel | Bound | Why |
|---|---|---|---|
| Phase 1 | Agent 1 (SMALL) ‖ Agent 2 (MAIN) | 2 threads (`v8_p1`) | Both read the user message; outputs are independent. |
| Phase 3+Q | Agent 3a × N (SMALL) ‖ **Agent Q × 1 (SMALL)** | min(5, 1+N) threads (`v8_p3q`) | Agent Q is launched in the same thread pool as Agent 3a. Agent Q reads structured signals from Agent 1 + the graph (post-Phase-2); Agent 3a reads its own per-problem inputs. They don't depend on each other. |
| Phase 4 | Agent 3b × M (SMALL) | min(4, M) threads | Per-problem TTM updates. M is usually 0 or 1. |

The key insight: **Agent Q adds zero serial wallclock** because Agent 3a is already blocking on the SMALL tier. Whatever wall-clock time Agent 3a takes for the slowest problem, Agent Q's call lands inside that window.

This is also why Agent Q reads pre-Phase-4 graph state (the staleness trade-off in §5.5).

The cross-session and cross-profile parallelism (judges in parallel with the next session, `--max-parallel-profiles`) is unchanged from v7.

---

## 10. Reproducibility, Logging, Three-Tier LLM Routing

The seed-derivation rule, env-var contract, output-token rate limiter, slow-call alarm, and per-call jsonl audit log are all unchanged. The only new `call_role` strings are:

- `agentq_retrieval_query` — Agent Q. Tier: SMALL. Default temperature 0.2, max_tokens 400, max_retries 2.
- `agent5_response_v8` — Agent 5 in v8. Tier: MAIN. Default temperature 0.4, max_tokens 1600 (the run script can override to 3000 for the local 120b variant via `HELPE_MAX_TOKENS_AGENT5_RESPONSE_V8`). Distinct from `agent5_response_v7` so logs are version-disambiguated and seeds differ.

The v8 run scripts (`scripts/run_v8_lightning_70b.sh`, `scripts/run_v8_local_120b.sh`, `scripts/run_v8_fireworks.sh`) are near-copies of the corresponding v7 scripts with three changes:
- Output path: `output/local_v8_*` / `output/lightning_v8_*` / etc.
- Token cap env var: `HELPE_MAX_TOKENS_AGENT5_RESPONSE_V8` (not `..._V7`).
- Sanity check: ensures `sentence-transformers` is installed for the system Python (RAG dependency).

The Lightning script intentionally points SIM/SMALL at a *different* sub-account from `run_v7_lightning_70b.sh` — that's the dual-tenant split (§17.6 of the v7 doc) that prevents 429 backoff in one version from stalling the other.

The `HELPE_V8_*` config knobs:

| Env var | Default | Meaning |
|---|---|---|
| `HELPE_V8_DENSE_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` | Sentence-transformers model for dense embedding. Override to `bge-small-en-v1.5` etc. |
| `HELPE_V8_MMR_LAMBDA` | `0.5` | MMR balance (1.0 = pure relevance, 0.0 = pure diversity). |
| `HELPE_V8_MMR_FETCH_K` | `20` | How many top-cosine candidates enter the MMR pool before the final K=8 selection. |

---

## 11. Worked End-to-End Example

The user message and Agents 1 + 2 outputs are the same as the v7 worked example (§22 of the v7 doc). Picking up from where v8 diverges:

### 11.1 Phase 3+Q (parallel)

**Agent 3a × 3** (per-problem attribute updates) — same as v7.

**Agent Q** (running in parallel with 3a) sees:
- user_intent: `express_emotion`
- user_intent_phrase: "User wants the chatbot to acknowledge the deadline pressure and self-doubt and sit with the feelings"
- mi_for_user_intent: `support`
- main_problem: `work_stress` (sticky main from cold start)
- main_system_intent: `""` (empty — first turn, Agent 3b hasn't run yet)
- main_mi_for_system_intent: `null`
- other_current_problems: `[{name: low_self_esteem, system_intent_1line: "", mi_for_system_intent: null}, {name: general_anxiety, ...}]`

Agent Q output:
```json
{
  "retrieval_query": "work_stress low_self_esteem general_anxiety
                      deadline pressure self-doubt severity peaks
                      venting moments support themes underlying
                      tightness exhaustion"
}
```

### 11.2 Phase 4 (Agent 3b × 2)

Same as v7: work_stress and low_self_esteem each get their TTM stage set to `contemplation` with appropriate system_intent. (Note: this happens *after* Agent Q ran, so on this first turn the system_intent Q saw was empty. From turn 2 onward, Q sees the previous turn's value.)

### 11.3 Phase 5 (Python)

Edge weights recomputed (cheap, used only for trace diagnostics — no longer driving retrieval).

`extract_corpus(graph)` returns 4 chunks total (3 attribute audits + 1 connection entry, all written this turn by Agent 2).

The retriever scores Q's query against the 4 chunks. With only one turn in the graph, the ranking is essentially "which of these 4 chunks best matches the theme tokens." The top-K=8 is bounded by 4 (the corpus size).

`rag_retrieved_chunks` looks like:
```
[
  {type: attribute_entry, attribute: perceived_severity, problem: work_stress, anchor: s1.t1,
   quote: "everything feels so tight", inferred: "user describes overwhelming tightness",
   score: 0.62},
  {type: attribute_entry, attribute: self_efficacy, problem: work_stress, anchor: s1.t1,
   quote: "I'm not sure that's the right move", inferred: "user questions whether harder effort will help",
   score: 0.51},
  {type: attribute_entry, attribute: perceived_severity, problem: low_self_esteem, anchor: s1.t1,
   quote: "good enough at this", inferred: "competence doubt is acute",
   score: 0.49},
  {type: connection_entry, problem_a: work_stress, problem_b: low_self_esteem, anchor: s1.t1,
   relation_type: reinforcing, attribute_a: self_efficacy, attribute_b: perceived_severity,
   quote: "doubt whether I'm even good enough at this", score: 0.46}
]
```

(All four chunks survive because the corpus is tiny on turn 1. By turn 20+ the corpus has grown enough that MMR meaningfully diversifies.)

### 11.4 Phase 6 (Agent 5)

Reads the v8 evidence_pack:
- USER_INTENT block (intent + phrase + mi_for_user_intent)
- MAIN PROBLEM block: name=work_stress, ttm=contemplation, system_intent="reflect both sides...", mi_for_system_intent=evoke, current_levels={perceived_severity: high (audits: s1.t1), self_efficacy: unknown (audits: s1.t1)}
- OTHER CURRENT PROBLEMS: low_self_esteem, general_anxiety
- RETRIEVED EVIDENCE: the 4 chunks above
- PAST_TWO_TURNS: empty (turn 1)
- PERSONA: empty
- ROLLING_SUMMARY: empty

Same R1→R2→R3→R4 contract, same banned-opener/phrase validators. The reply Agent 5 produces is shaped by *which chunks landed in RETRIEVED EVIDENCE*, not by per-edge summaries.

### 11.5 By turn 20+

Where v8 starts to differ visibly from v7: by turn 20+, the corpus has 30–80 chunks (or more if the user covers many problems). On a venting turn, MMR over Agent Q's query might surface:

- two recent severity audits (the user's current frame),
- one earlier coping_strategies audit from session 1 (a positive solution evidence point),
- one connection entry from a different edge entirely (e.g., sleep_problems ↔ academic_pressure) that the WDC edge-walk in v7 might have pruned because it's not directly attached to the current main,
- one persona-anchored quote ("I tend to grind harder when stressed") that names a behavioral pattern Agent 5 can softly reflect.

This is the qualitative case for v8: positive evidence and adjacent-edge mechanisms surface directly without depending on the recency-decayed weight + per-edge summary path.

---

## 12. What Is Genuinely Novel in v8 (vs. v7 and vs. Prior RAG Work)

### 12.1 RAG over a structured behavior-change corpus, not a text dump

Most RAG-for-dialogue work indexes raw transcripts (LongConv, MemoryBank, MSC-style). v8 indexes the **structured evidence stack** the upstream agents already curated: every attribute audit entry has a typed (problem, attribute) and a verbatim quote; every connection entry has typed attributes and a relation_type. Retrieval matches against curated structured signals, not raw text.

**Gap filled vs. prior RAG-for-ESC**: prior work indexes the conversation; v8 indexes *the inferred structure of the user's situation*. The chunks are smaller, more specific, and already labeled.

### 12.2 LLM-generated retrieval query from agent state, not user message

This is the architectural pattern most RAG systems don't use: the retrieval query is **not** the user's last utterance and **not** a paraphrase of it. It's a separate LLM call (Agent Q) that reads the structured signals from the other agents and projects them into a search-friendly bag of theme tokens.

**Gap filled vs. prior CAMI / CauESC / EmoDynamiX**: those systems condition retrieval (when present) on the user message + an emotion classifier. v8 conditions on the user_intent + chosen MI move + system_intent + active problem cluster — which is what *the chatbot is trying to do*, not just what the user said.

### 12.3 Lossless audit corpus with no separate index store

The corpus is re-extracted from the graph each turn. There's no separate vector store, no indexing pipeline, no consistency problem. The audit stacks ARE the index. Adding a new audit entry next turn means it's automatically searchable next turn.

**Gap filled vs. typical conversational RAG infrastructure**: no Pinecone, no Chroma, no Weaviate, no rebuild cron. The cost is paid in re-extraction (cheap) + per-chunk encoding cache (one-time).

### 12.4 MMR over MiniLM with a dropped LLM-summary layer

The v7 → v8 ablation isolates a specific question: *if we have lossless audit chunks, do we still need an LLM-generated per-edge summary?* v8 says no — RAG can find the relevant entries directly. v7 says yes — the summary is what scales when the audit stack grows. The two together (run side by side on the same matrix) let us actually answer this with measurements rather than intuition.

**Gap filled in research-method terms**: most multi-agent dialogue papers don't ablate the retrieval-vs-summary axis cleanly. v7 and v8 are the same codebase with one knob flipped.

### 12.5 Cross-category synthesis — v7 contributions still hold

All of v7's contributions in §23 of the v7 doc (multi-agent decomposition with role-typed routing, two-track MI selection, per-attribute summary + level + reasoning + confidence, typed cross-problem connections, conservative level-update rules with counter-evidence, closed-MISC-vocabulary shortlist gating, R1→R4 progressive rewrite + ESC-first contract, per-field persona update with single-quote anchoring) carry forward to v8 unchanged. v8 adds dense RAG on top.

---

## 13. Trade-offs and Limitations

### 13.1 Cluster context is implicit

v7's per-edge summary explicitly told the responder *how two problems relate over time*. v8's RAG might surface five chunks that all happen to come from one edge, leaving the responder to reconstruct the cluster picture from chunk-level fragments. MMR helps but doesn't fully solve this.

### 13.2 Embedding-model bias

MiniLM was trained on general English, not on MI/HBM/TTM vocabulary. Theme tokens like "ambivalence" or "self_efficacy" have meaning in this corpus that MiniLM may not perfectly capture. Switching to a domain-tuned embedding model (e.g., a CBT-finetuned encoder if one exists) is a future option.

### 13.3 Q's staleness on TTM-shift turns

When Agent 3b changes the TTM stage this turn, Agent Q saw the previous stage's `system_intent`. In practice rare and the next turn corrects it; in pathological cases (a user who flips stage rapidly) Q's query may consistently lag.

### 13.4 Top-K = 8 is a fixed budget

A turn that genuinely needs more context (or less) gets the same K. Adaptive K based on retrieval-score distribution would be a natural extension; v8 doesn't try.

### 13.5 No guarantee positive evidence is surfaced

If `coping_strategies` audits live in the corpus but Agent Q didn't include theme tokens that match them, the responder won't see them — even when the chosen MI is affirmation. Q's prompt explicitly biases toward positive theme tokens for affirmation/evoke moves; this is a soft mitigation, not a guarantee.

### 13.6 Inherits v7's open limitations

Simulated-users-aren't-real-users, LLM-judges-have-biases (now sharing the model family with the responder after the May-2026 swap), conservative-level-rules-can-still-mis-trigger, the 4-stage TTM is a simplification, the 22-problem vocabulary is closed, the evaluation matrix is small. All apply to v8 unchanged. See v7 doc §24.

### 13.7 The v8 evaluation hasn't fully landed yet

As of writing (2026-05-05), v8 has been running on the odd-profile matrix; final v7-vs-v8 comparison numbers (TTM transition rate, MITI globals, ESC dimensions, per-turn wallclock distribution) will be published once both runs complete on the same matrix. `eval/compare_v3_v7_v8.py` is the side-by-side comparison entry point.

---

## 14. Glossary (v8-specific terms)

| Term | Meaning |
|---|---|
| **Agent Q / QueryAgent** | The new SMALL-tier agent in v8 that produces the retrieval query string from structured signals (Agent 1 output + graph state). One call per turn. Runs in parallel with Agent 3a. |
| **call_role `agentq_retrieval_query`** | The call_role string for Agent Q's LLM call; drives seed derivation and tier routing. |
| **chunk** | One indexed item in the v8 RAG corpus. Two shapes: `attribute_entry` (one per audit entry) and `connection_entry` (one per problem-problem connection entry). |
| **corpus** | The flat list of all chunks extracted from the v7 graph at the start of Phase 5. Re-extracted every turn (cheap). Not stored separately. |
| **dense retrieval** | Cosine similarity over learned vector embeddings (MiniLM). The opposite of sparse retrieval (BM25 / TF-IDF), which scores by exact term overlap. |
| **MMR (Maximal Marginal Relevance)** | Carbonell & Goldstein 1998. Iteratively picks chunks balancing query similarity against redundancy with already-picked chunks. Default λ=0.5. |
| **MiniLM** | The sentence-transformers encoder v8 uses (`all-MiniLM-L6-v2`). 384-dim, ~22M params, runs on CPU. Override via `HELPE_V8_DENSE_MODEL`. |
| **rag_retrieved_chunks** | The top-K (default 8) chunks that survive MMR selection. Surfaced in Agent 5's USER prompt as the `# RETRIEVED EVIDENCE` block. |
| **rag_query_source** | One of `"agent_q"` (Agent Q's LLM-generated query) or `"fallback_concat"` (the deterministic safety-net query if Agent Q failed). Logged in trace diagnostics. |
| **WDC edge-walk** | v7's retrieval method: weighted-degree centrality over the seed set, with relative-threshold τ. **Not used in v8.** |
| **V8_MMR_LAMBDA** | The MMR balance parameter (default 0.5). Higher = more relevance-driven; lower = more diversity-driven. |
| **V8_MMR_FETCH_K** | The number of top-cosine candidates that enter the MMR pool (default 20). The final K=8 is then selected via MMR within that pool. |
| **V8_RAG_TOP_K_DEFAULT** | The number of chunks Agent 5 actually sees (default 8). Constant in `rag_v8.py:73`. |

---

## End

This document covers v8 as of 2026-05-05 and reflects the May-2026 MAIN-tier swap to llama-3.3-70b (shared with v7), the dual-tenant Lightning split for SIM/SMALL calls, and the dropped minimum-cosine-floor in MMR.
