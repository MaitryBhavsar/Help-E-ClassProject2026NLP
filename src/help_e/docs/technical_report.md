# HELP-E: A Technical Report

*Base document for the forthcoming paper.*

All file paths in this document are relative to `HELP-E/src/help_e/` (the project root). External assets that feed the pipeline but live outside `help_e/` are marked with `[external]` and cited by path.

---

## Abstract *(draft placeholder)*

Emotional-support chatbots are typically trained and evaluated on short, single-concern exchanges, but real users raise multiple, evolving concerns across sessions, and different concerns call for different counseling moves. HELP-E is a multi-turn emotional-support system that maintains a per-user **Problem Graph** — a bipartite structure linking a stable persona to any number of concurrent problems through behavioral-attribute edges grounded in the Health Belief Model (HBM). Each problem carries a Transtheoretical Model (TTM) stage inferred from the chronological evidence stream on its edges, and a rule-based TTM × HBM table selects a small candidate set of Motivational-Interviewing (MI) techniques for every turn. A single merged chain-of-thought call then commits to a subset of candidates and writes a short, style-constrained reply. We implement five ablation systems (v1 history-only → v5 full graph + merged CoT), drive them against a three-agent LLM user simulator (Mind-1 per-turn, Mind-2 retrospective labels, Mind-3 satisfaction), and evaluate on MITI adherence, TTM progression, extraction agreement with silver labels, maintenance-reach ratio, and six-dimensional ESC satisfaction. *(Results: TBD — pending full matrix run.)*

---

## 1. Introduction

Most emotional-support-conversation (ESC) systems assume a single concern per dialogue and use flat dialogue history as state. Two limitations of that assumption motivate this work:

1. **Concurrent problems are the norm, not the exception.** A user venting about academic pressure frequently surfaces co-occurring sleep disruption, perfectionism, or family conflict. Treating these as one flat narrative loses the structure a human counselor uses to decide *which* concern to work with right now and *which* to carry forward.
2. **The right counseling move depends on where the user is on each concern.** The same user message can warrant a pure reflection, a permission-gated suggestion, or a concrete action plan depending on the user's stage of change and current beliefs about that specific problem. A policy that ignores stage and belief state cannot pick this well.

HELP-E addresses both limitations by maintaining a structured **Problem Graph** per user and by grounding MI-technique selection in two behavior-change theories — the Health Belief Model (HBM) for belief-level attributes and the Transtheoretical Model (TTM) for stage-of-change tracking. Contributions:

- A bipartite Problem Graph with append-only HBM attribute evidence and per-problem TTM stages, updated after every turn.
- A rule-based TTM × HBM → MI candidate selector that narrows a 12-technique vocabulary to a per-turn shortlist without an LLM call.
- A merged instruction+response chain-of-thought call that commits to a technique subset from the shortlist and emits a length-capped, MI-style user-facing reply in a single constrained-decoding step.
- A five-system ablation (v1–v5) isolating the contribution of per-problem summaries, summary-based TTM, evidence-based TTM + HBM-modulated MI, and the merged CoT wrapper.
- A three-agent LLM user simulator (Mind-1/2/3) that enables reproducible, cost-controlled evaluation on five metrics (MITI adherence, TTM progression, extraction agreement, maintenance-reach, 6-dim ESC satisfaction) over a 30-profile × 5-system matrix.

---

## 2. Background and Theoretical Grounding

### 2.1 Motivational Interviewing

Motivational Interviewing (MI) is a counseling style that elicits change from the user's own reasoning rather than prescribing it. HELP-E operationalises MI through a 12-technique taxonomy (T1–T12) combining the OARS core (Open questions, Affirmations, Reflections, Summaries) with five change-oriented techniques and three structural moves:

| id | label | purpose |
|---|---|---|
| T1 | Reflection | paraphrase content + feeling; go a hair deeper |
| T2 | Affirmation | name a specific strength or effort the user showed |
| T3 | Summary | gather the thread of what has been said so far |
| T4 | Open Question | invite elaboration, not yes/no |
| T5 | Ask Permission | check before offering a suggestion |
| T6 | Options Menu | offer 2–3 alternatives side-by-side |
| T7 | EPE (Elicit–Provide–Elicit) | find out what the user knows, add one fact, elicit reaction |
| T8 | Change Talk | invite the user's own reasons for change |
| T9 | Confidence Builder | reflect past capability and small wins |
| T10 | Action Planning | co-design a concrete next step |
| T11 | Coping / Relapse Plan | plan for a likely trigger or setback |
| T12 | Normalize / Reframe | normalize experience; widen a stuck thought |

Files: `config.py` (MI_TECHNIQUES dict), `prompts/common.py` (technique reference block), `knowledge/mi_mapping.md`.

### 2.2 Health Belief Model

HBM predicts health-related behavior from the user's beliefs about the problem. HELP-E encodes these beliefs as typed **AttributeNodes** on the Problem Graph. The 11 attribute types, with the first six mirroring classical HBM constructs and the remainder added to capture MI-relevant change talk, are:

| attr_type | meaning |
|---|---|
| perceived_severity | how serious the user thinks the problem is |
| perceived_susceptibility | how likely the problem is to keep affecting them |
| perceived_benefits | what good the user thinks change would bring |
| perceived_barriers | what the user thinks is in the way of change |
| self_efficacy | how capable the user feels of making the change |
| cues_to_action | events/reminders pushing them to act |
| motivation | desire/energy to work on this (change-talk intensity) |
| coping_strategies | what they are currently trying |
| past_attempts | what they have tried before and how it went |
| triggers | what sets the problem off or makes it worse |
| goal | what "solved" looks like for the user (main problem only → system_intent) |

Each AttributeNode is shared across the graph by `(attr_type, value)` identity; evidence for a specific `(attribute, problem)` pair lives on the edge. Each edge carries a rolling `current_level ∈ {low, moderate, high}` updated from its stack.

Files: `data/attributes.md`, `config.py` (ATTR_TYPES, LEVELS).

### 2.3 Transtheoretical Model

TTM views behavior change as a progression through five stages:

1. **precontemplation** — not aware of / not seeking change
2. **contemplation** — aware, ambivalent
3. **preparation** — intending to act soon; planning small steps
4. **action** — actively making changes in the last weeks
5. **maintenance** — sustaining change, preventing relapse

Each `ProblemNode` stores a `current_ttm_stage`. The stage is re-inferred after every turn by an LLM that reads the **chronological observation stream** of attribute evidence for that problem (not the free-text summary and not the current user message in isolation). Regression is permitted only when the evidence includes an explicit setback signal (`past_attempts`, `triggers`, negative-motivation content).

Files: `config.py` (TTM_STAGES), `prompts/ttm_infer.py`.

### 2.4 TTM × HBM → MI Mapping

A per-problem TTM stage defines a **base set** of MI techniques; HBM attribute `(attr_type, level)` pairs extend the base set; user intent restricts it. The full mapping is pure Python rules in `mi_selector.py` and codifies the therapist heuristic that, e.g., *a precontemplation user should not be advised* and *a high-barriers user needs options + relapse planning, not more reflection alone*. The rule set is frozen in `knowledge/mi_mapping.md` so the paper's §6.6 matches implementation exactly.

Base set by TTM stage (from `mi_selector.TTM_BASE`):

| stage | base techniques |
|---|---|
| precontemplation | T1, T3, T12 |
| contemplation | T1, T5, T8 |
| preparation | T5, T6, T10 |
| action | T2, T10, T11 |
| maintenance | T2, T11, T12 |

HBM modifiers — for every attribute edge whose `information_stack` has an entry within the last N turns, add the listed techniques (from `mi_selector.HBM_MODIFIERS`):

| attr_type | level | add |
|---|---|---|
| perceived_severity | low | T4 |
| perceived_susceptibility | low | T4 |
| perceived_benefits | low | T8 |
| perceived_barriers | high | T6, T11 |
| self_efficacy | low | T2, T9 |
| cues_to_action | high | T10 |
| motivation | low | T8 |
| motivation | high | T10 |
| coping_strategies | low | T6 |
| past_attempts | high | T11, T12 |
| triggers | high | T11 |

Intent gate: when `user_intent ∈ {vent, seek_validation}`, the final candidate set is restricted to the reflective/affirming subset `{T1, T2, T3, T12}` regardless of TTM/HBM additions. Safety fallback: empty candidate list falls back to `{T1, T4}`.

Files: `mi_selector.py`, `knowledge/mi_mapping.md`.

---

## 3. System Architecture

### 3.1 Module Map

| module | responsibility | LLM? |
|---|---|---|
| `session_driver.py` | multi-profile × multi-session × multi-turn orchestration | no |
| `run.py` | CLI entry point; routes `--system v1..v5` to the right `turn_fn` | no |
| `llm_client.py` | OpenAI-compatible client with schema-constrained JSON + deterministic seed | infra |
| `config.py` | endpoints, models, temperature schedule, enums, paths | no |
| `graph.py` | bipartite NetworkX graph (Persona, Problem, Attribute) + mutation API | no |
| `graph_update.py` | per-turn graph writes + session-end finalisation | orchestrator |
| `retrieval.py` | build `RetrievalBundle` from graph (main + close + recent) | no |
| `mi_selector.py` | rule-based MI candidate selector | no |
| `instruction_response.py` | merged CoT instruction+response (v5) | yes |
| `prompts/extraction.py` | §18.1 per-turn extraction | yes |
| `prompts/level_recompute.py` | §18.2 edge-level re-judge | yes |
| `prompts/ttm_infer.py` | §18.3 per-problem stage inference | yes |
| `prompts/persona_update.py` | §18.5 session-end persona update | yes |
| `prompts/session_summary.py` | §18.6 session-end summary | yes |
| `prompts/common.py` | shared prompt blocks + `PROJECT_IDENTITY` | no |
| `baselines/v1_history.py` | v1 flat-history turn_fn | yes |
| `baselines/v2_summary.py` | v2 per-problem summary turn_fn | yes |
| `baselines/v3_ttm_from_summary.py` | v3 summary + TTM turn_fn | yes |
| `baselines/common.py` | shared baseline helpers (intent heuristic, MI hints) | no |
| `simulator/mind1.py` | per-turn user utterance + session-end drift check | yes |
| `simulator/mind2.py` | retrospective per-turn silver labels | yes |
| `simulator/mind3.py` | 6-dim ESC satisfaction | yes |
| `eval/judge.py` | MITI judge (LLM, T=0) | yes |
| `eval/metrics.py` | E1/E3a/E3b/E4/E5 aggregation + statistical tests | no |
| `ui/server.py` | FastAPI interactive demo | orchestrator |
| `data/profiles/*.yaml` | 30 seeded personas (P01–P30) | no |
| `data/problems.md`, `data/attributes.md` | canonical vocabularies | no |
| `knowledge/mi_mapping.md` | frozen TTM×HBM→MI rule document | no |

### 3.2 LLM Infrastructure

HELP-E uses a **dual-endpoint** setup accessed through one unified client:

- **Main endpoint** (port 11436): vLLM serving `meta-llama/Llama-3.3-70B-Instruct`. Handles all pipeline calls — extraction, level recompute, TTM inference, persona update, session summary, merged response, judge.
- **Simulator endpoint** (port 11438): Ollama serving `gpt-oss:20b`. Handles Mind-1 / Mind-2 / Mind-3 so the "user" and the "system" are different models (avoids single-model self-collusion).

Both endpoints expose the OpenAI-compatible `/v1/chat/completions` API; routing is by `call_role`. The client (`LLMClient` in `llm_client.py`) enforces structured output via Outlines-style constrained decoding when available, falling back to JSON mode + `json_repair` + post-parse validators. Every call has:

- **Deterministic seed:** `hash(SEED_SALT, profile_id, session_id, system, turn_id, call_role) mod 2^31`. Two runs with identical inputs produce identical outputs (up to backend non-determinism).
- **Role-specific temperature** from `TEMPERATURE_BY_ROLE` in `config.py`:

| role | T |
|---|---|
| extraction, level_recompute, ttm_infer, persona_update, session_summary, v2_summary_update, v3_ttm_from_summary, mind2, mind3 | 0.2 |
| merged_response, v1_response, v2_response, v3_response, mind1 | 0.4 |
| e1_judge | 0.0 |

- **One retry** on validation failure with the validator's error string appended as a hint.
- **Safe fallback** per call site (e.g. extraction returns an empty-turn record; merged response falls back to a T1 Reflection) so the pipeline never crashes on a single bad JSON.

Files: `llm_client.py`, `config.py`.

### 3.3 Per-Turn Pipeline

The v4/v5 per-turn pipeline (in `session_driver._default_turn_fn`, with the graph mutations delegated to `graph_update.apply_turn`) proceeds as follows:

```
 ┌───────────────────────── one turn ─────────────────────────┐
 │                                                            │
 │   Mind-1 (sim endpoint)       ← user utterance             │
 │       │                                                    │
 │       ▼                                                    │
 │   §18.1 extraction (main)     → active_problems,           │
 │       │                         main_problem, user_intent, │
 │       │                         observed_attributes        │
 │       ▼                                                    │
 │   graph.append_evidence()     (pure Python; per attr)      │
 │       │                                                    │
 │       ▼                                                    │
 │   §18.2 level_recompute       batched over edges with      │
 │       │                        new evidence                │
 │       ▼                                                    │
 │   §18.3 ttm_infer             per active problem, from     │
 │       │                        chronological obs stream    │
 │       ▼                                                    │
 │   retrieval.build_bundle()   main view + graph-close       │
 │       │                        neighbors + last-N actives, │
 │       │                        capped at 50 edges total    │
 │       ▼                                                    │
 │   mi_selector.select_candidates()   TTM base + HBM mods    │
 │       │                              + intent gate         │
 │       ▼                                                    │
 │   §18.4 merged response (v5)  one call → system_intent +   │
 │       │                        instruction +               │
 │       │                        chosen_techniques +         │
 │       │                        response                    │
 │       ▼                                                    │
 │   append assistant turn to transcript, log trace + bundle  │
 │                                                            │
 └────────────────────────────────────────────────────────────┘

 At session end:
   §18.5 persona_update   (reads full transcript + traces)
   §18.6 session_summary  (reads full transcript + stage transitions)
   Mind-1 drift_check     (is the user utterance stream consistent
                            with the original persona?)

 After all sessions for (profile, system):
   Mind-2  → per-turn silver labels (active_problems, ttm_stages,
              next_turn_intent) for E3a / E3b / E4
   Mind-3  → 6-dim ESC satisfaction (per session, per dimension)
   E1 judge (optional inline) → MITI scores per assistant turn
```

Retrieval knobs (from `config.py`):
`LAST_N_TURNS=5`, `TOP_K_STACK=3`, `CLOSENESS_ALPHA=1.0`, `CLOSENESS_THRESHOLD=2.0`, `MAX_RESPONSE_SENTENCES=5`, `MAX_RESPONSE_QUESTIONS=1`.

---

## 4. Problem Graph

### 4.1 Bipartite Schema

The graph is bipartite between problems and attributes, with a single persona node hanging off the user:

```
              ┌────────────┐
              │ PersonaNode│  (1 per user; session-end update)
              └─────┬──────┘
                    │ HAS_PROBLEM (implicit ownership)
          ┌─────────┼──────────────┐
          ▼         ▼              ▼
  ┌──────────┐ ┌──────────┐   ┌──────────┐
  │ ProblemA │ │ ProblemB │   │ ProblemC │
  │  stage:  │ │  stage:  │   │  stage:  │
  │  prep    │ │  contempl│   │  action  │
  └────┬─────┘ └────┬─────┘   └────┬─────┘
       │            │              │
       │HAS_ATTRIBUTE (w/ evidence stack, current_level)
       │            │              │
       ▼            ▼              ▼
  ┌─────────────────────────────────────┐
  │ AttributeNode pool (shared)         │
  │  (attr_type, value) — deduped       │
  │  e.g. (perceived_barriers,          │
  │        "difficulty asking for help")│
  └─────────────────────────────────────┘
```

### 4.2 Edge Structure

Each `ProblemNode ↔ AttributeNode` edge (`AttributeEdge` in `graph.py`) carries:

- `information_stack: list[StackEntry]` — append-only. Each `StackEntry` holds `session_id`, `turn_id`, `user_message`, `information` (an observer-style phrasing emitted by extraction).
- `current_level: "low" | "moderate" | "high"` — recomputed by §18.2 after new evidence is added.
- `weight: int` — count of distinct `(session_id, turn_id)` pairs in the stack; used by retrieval's closeness score.

### 4.3 Implicit Problem–Problem Links

No explicit `Problem → Problem` edges. Two problems are *related* exactly when they share one or more AttributeNodes. Closeness between problem `p_main` and candidate neighbor `p_n` is:

```
closeness(p_main, p_n) = Σ_{a ∈ shared attrs} min(weight_e1, weight_e2)
                         + CLOSENESS_ALPHA * |shared attrs|
```

Retrieval (step B) returns neighbors whose closeness ≥ `CLOSENESS_THRESHOLD` (default 2.0). This lets the system surface, e.g., `sleep_problems` when the main is `academic_pressure` if both share the `triggers: "late-night cramming"` attribute.

### 4.4 Mutation API

All graph writes funnel through three methods exposed by `graph.ProblemGraph`:

- `get_or_create_problem(name)` — idempotent; updates `last_mentioned`.
- `append_evidence(attr_type, value, problem_name, information, session_id, turn_id, user_message)` — creates/finds the shared AttributeNode, ensures the edge exists, appends a `StackEntry`, and marks the edge as having new evidence for the next `level_recompute` batch.
- `set_level(attr_id, problem_id, level)` — writes `current_level` on the edge.
- `set_ttm_stage(problem_name, new_stage)` — writes on the ProblemNode.

All mutations are pure Python (no LLM). Graphs are persisted as JSON after every session to `graphs/{system}/{profile_id}_after_s{N:02d}.json`.

Files: `graph.py`, `graph_update.py`.

---

## 5. Per-Turn Pipeline in Detail

*(Numbered after the internal `§18` spec for traceability back to prompts.)*

### 5.1 §18.1 Extraction

One LLM call per user turn. Input: current user message + last 5 turns of dialogue. Output (JSON, enum-enforced):

```json
{
  "active_problems": ["academic_pressure", "sleep_problems"],
  "main_problem":    "academic_pressure",
  "user_intent":     "vent",
  "observed_attributes": [
    {"attr_type": "perceived_severity", "problem": "academic_pressure",
     "value": "user experiences this week's workload as crushing and unmanageable"},
    {"attr_type": "triggers", "problem": "academic_pressure",
     "value": "late-night cramming sessions amplify the academic stress"},
    {"attr_type": "triggers", "problem": "sleep_problems",
     "value": "late-night cramming is pushing bedtime past 2am"}
  ]
}
```

Key design choices:

- `main_problem` is *single* (or `null`). A turn cannot have two "main" problems — the response agent must pick one thread.
- `observed_attributes` has one entry per `(attr_type, problem)` pair. The same attribute type can appear under different problems in one turn (e.g. `triggers` above covers both academic_pressure and sleep_problems).
- `value` is observer-style, not a quote. This decouples the evidence stack from surface text and lets level-recompute reason over semantics.
- Empty turns are allowed: `active_problems=[]`, `main_problem=null`, `observed_attributes=[]`.
- Post-parse validators enforce the structural invariants JSON Schema cannot express (main ⊆ active; no duplicate `(attr_type, problem)` pairs; every observed_attribute's problem appears in active_problems).

Fallback: on validation failure the pipeline treats the turn as empty and continues.

Files: `prompts/extraction.py`, `prompts/common.py` (shared vocab blocks).

### 5.2 §18.2 Level Recompute

After §18.1 appends new `StackEntry`s, `graph_update.apply_turn` collects all edges that received evidence this turn and sends them to one batched LLM call. For each edge the model reads: attribute type, attribute value, problem name, prior `current_level`, and the most recent entries of the information stack. It returns one of `{low, moderate, high}`. Batching keeps the per-turn LLM cost at O(1) instead of O(edges).

Files: `prompts/level_recompute.py`, `graph_update.py`.

### 5.3 §18.3 TTM Inference

Run once per active problem. Input: a flat, chronologically sorted stream of observations for that problem pulled across all sessions:

```
[{"session_id": s, "turn_id": t, "attr_type": a, "value": v}, ...]
```

No free-text summary, no persona. The model places the problem on the five-stage ladder, citing the observations that drove the decision. Regressions are allowed but constrained — the model is instructed to treat regression as a meaningful signal (e.g. a setback keyword in `past_attempts` or new `triggers`).

This is the key departure from v3: in v3, TTM is inferred from a free-text running summary; here it is inferred from **structured evidence stacks**.

Files: `prompts/ttm_infer.py`, `graph_update.py`.

### 5.4 Retrieval Bundle

Pure Python. `retrieval.build_bundle` constructs a `RetrievalBundle` in three steps, then budgets the total:

- **Step A — Main problem view.** All edges for `main_problem`, sorted by `weight` desc, each edge surfacing its top `TOP_K_STACK=3` most recent stack entries.
- **Step B — Graph-close neighbors.** `graph.get_related_problems(main)` returns problems ranked by closeness; those above `CLOSENESS_THRESHOLD` are added with the same top-K edge formatting.
- **Step C — Recent-active problems.** Any `active_problem` from the last 5 turns not already in A or B, included minimally (name, stage, zero or one edge) so the response agent knows the user has recently touched them.

A 50-edge cap is applied across A+B+C to keep the merged response prompt tractable.

Output dataclass fields (`RetrievalBundle`):
`persona`, `prior_session_summary`, `main_problem: ProblemView | None`, `graph_close_problems: list[ProblemView]`, `recent_active_problems: list[ProblemView]`, `current_user_message`, `recent_turns`.

Files: `retrieval.py`.

### 5.5 MI Candidate Selection

Pure Python (no LLM). Inputs: the `RetrievalBundle` + `user_intent` from extraction + `(current_session, current_turn, last_n)`. Logic:

1. Start from the TTM base set for `main_problem.current_ttm_stage`.
2. For every edge in `main + graph_close + recent_active` whose stack has an entry within the last N turns of the *current* session, look up `HBM_MODIFIERS[(attr_type, current_level)]` and add those techniques.
3. Apply the intent gate: if `user_intent ∈ {vent, seek_validation}`, intersect with the reflective/affirming subset `{T1, T2, T3, T12}`.
4. Safety fallback: if the set is empty, return `{T1, T4}`.

Output is an ordered, de-duplicated list of `{technique_id, technique_label}` dicts. The module does *not* pre-pick; it emits the candidate list and lets the merged response call commit.

Special note: if `self_efficacy=low` and T10 (Action Planning) is in the candidate list, the instruction layer must scope T10 to a **micro-step**. The selector flags this; the merged prompt enforces the wording.

Files: `mi_selector.py`, `knowledge/mi_mapping.md`.

### 5.6 §18.4 Merged Chain-of-Thought Response

One LLM call that emits four fields in order (schema in `instruction_response.MERGED_SCHEMA`):

```json
{
  "system_intent":      "≤20 words — derived ONLY from main problem + HBM + TTM",
  "instruction":        "≤150 words — names technique ids, evidence to surface, don'ts",
  "chosen_techniques":  ["T1", "T5", "T6"],
  "response":           "≤5 sentences, ≤1 question"
}
```

The system prompt walks the model through four reasoning steps:

1. Derive `system_intent` from the main problem's HBM attributes + goal + current TTM stage. User intent and secondary problems are *not* used here — this keeps the intent grounded in where the user stands on the main thread.
2. Write an `instruction` up to 150 words that: (a) picks one or a combination of candidates and names them by id; (b) says which evidence to surface; (c) restates length/question caps and any don'ts (e.g. "don't pivot to the secondary problem").
3. Bookkeep the technique ids used into `chosen_techniques`. Validator enforces: `chosen_techniques ⊆ candidates` and every chosen id appears as a substring in `instruction`.
4. Write the user-facing `response` following the instruction. Validator enforces ≤5 sentences and ≤1 question (regex).

Advantages of the merged form over a separate "instruction then response" pipeline:

- One round-trip instead of two; fewer tokens.
- The `instruction` is auditable — it sits next to the response it produced and names the techniques, enabling post-hoc analysis of whether the response honored its own plan.
- The CoT is explicit and the technique commitment is structured, so E1 judging can cross-reference.

Fallback: on repeated validation failure the pipeline emits a safe T1 Reflection: `"I'm here. Take your time."`

Files: `instruction_response.py`, `prompts/common.py`.

### 5.7 Session-End Calls

- **§18.5 persona update.** Reads the full transcript + all per-turn `TurnTrace`s, emits updates to any of the eight persona fields (`demographics`, `personality_traits`, `core_values`, `core_beliefs`, `support_system`, `hobbies_interests`, `communication_style`, `relevant_history`). Conservative: only updates fields with clear new evidence.
- **§18.6 session summary.** Reads the full transcript + stage transitions, emits a short summary used as `prior_session_summary` for the next session.
- **Drift check** (Mind-1). Given the original persona and the user utterance stream, Mind-1 emits `{consistent: bool, severity: "none|minor|major", reason: str}` — a sanity check that the simulator stayed in persona.

Files: `prompts/persona_update.py`, `prompts/session_summary.py`, `simulator/mind1.py`.

---

## 6. User Simulator (Mind-1 / Mind-2 / Mind-3)

All three simulator roles run on the sim endpoint (`gpt-oss:20b`), kept deliberately different from the main endpoint to reduce single-model self-collusion in evaluation.

### 6.1 Mind-1 — Per-Turn User Utterance

Runs once per turn before the system acts. Reads: the Mind-1 persona (profile_id, situation paragraph, primary problem, traits, communication style, history), session arc cue, prior-session summary, and last 5 turns. Emits `{"utterance": "<user message>"}`. System instructions enforce: stay in persona, no therapy jargon, pace naturally (no info dumping on turn 1), don't solve the user's own problem.

At the end of each session, `run_drift_check` reads the full utterance stream and the original persona and returns a drift judgement. A `major` drift is flagged in the session artifact but does not abort the run.

Files: `simulator/mind1.py`.

### 6.2 Mind-2 — Retrospective Silver Labels

Runs once per `(profile, system)` after all sessions. Reads every transcript. Emits:

- `arc_summary` — one summary per session.
- `per_problem_trajectories` — per problem, the list of `(session_id, start_stage, end_stage, notes)`.
- `turn_labels` — per user turn: `active_problems`, `per_problem_ttm`, `next_turn_intent`.

Mind-2 is **not in the response loop**. Its outputs function as silver labels for:

- E3a (TTM progression) — read from `per_problem_trajectories`.
- E3b (extraction agreement) — Mind-2's `turn_labels` are compared against the system's own extraction via Cohen's κ.
- E4 (maintenance-reach ratio) — read from end-of-final-session stages.

Mind-2 looks at the full transcript at once, not turn-by-turn online, so it can assess trajectories a turn-local extractor cannot.

Files: `simulator/mind2.py`.

### 6.3 Mind-3 — 6-Dim ESC Satisfaction

Runs after Mind-2 (using Mind-2's labels as CoT scaffolding). Reads every session transcript. Emits per session, per dimension: two-to-three cited turn excerpts, reasoning, and a 1–5 score.

Dimensions (`ESC_DIMENSIONS` in `config.py`):

1. empathy
2. understanding
3. helpfulness
4. autonomy_respect
5. non_judgment
6. willingness_to_continue

Temperature 0.2. Outputs feed E5.

Files: `simulator/mind3.py`.

---

## 7. Ablation Systems v1–v5

### 7.1 Overview

| system | MI calls / turn* | state beyond transcript | TTM tracking | HBM evidence | MI selection |
|---|---|---|---|---|---|
| **v1** | 1 (response) | none | none | none | heuristic intent → hint string |
| **v2** | 2 (summary + response) | per-problem running summary | none | none | heuristic intent → hint string |
| **v3** | 2–3 (summary + per-problem TTM + response) | per-problem summary + TTM stage | summary-only | none | TTM base set + intent gate |
| **v4** | 3–4 (extraction + level + per-problem TTM + response) | full bipartite graph | evidence-stream | yes | TTM base + HBM modifiers + intent gate |
| **v5** | 3–4 (extraction + level + per-problem TTM + merged) | full bipartite graph | evidence-stream | yes | TTM base + HBM modifiers + intent gate, selected inside merged CoT |

*Mind-1 is always +1 call/turn; session-end adds persona update + session summary regardless of system. The counts above exclude these.*

### 7.2 v1 — History-Only Baseline

`baselines/v1_history.py::v1_turn_fn`. No graph operations. `guess_intent()` classifies the user message heuristically (keyword match) into `{vent, seek_advice, report_progress, other}`; `reflect_affirm_hint(intent)` returns a fixed MI hint ("reflect before advising", "ask permission before options", etc.). One LLM call with a system prompt that lists MI style rules and the hint; input is the prior-session summary + the last few turns. Output is `{"response": "<reply>"}` only. Validators enforce ≤5 sentences and ≤1 question.

Purpose in the ablation: establish what a strong prompt-only baseline looks like.

### 7.3 v2 — Per-Problem Running Summaries

`baselines/v2_summary.py::v2_turn_fn`. Two LLM calls per turn:

1. **Summary update.** Reads the 20-problem vocab, existing summaries dict, last N turns, current message. Emits `active_problems`, `main_problem`, and a list of `(problem_name, summary)` entries (≤3 sentences each). Post-parse: `main_problem ∈ active_problems`; every summary's `problem_name ∈ active_problems`.
2. **Response.** Reads the main problem's running summary + last N turns + MI hint (from the same heuristic as v1).

Per-problem state lives on `graph._v2_summary_state.summaries: dict[problem_name, summary]`. No TTM, no HBM, no graph edges. The summary is a free-text rolling digest.

Purpose in the ablation: isolate the contribution of structured per-problem tracking without stage-of-change.

### 7.4 v3 — Summary + TTM-From-Summary

`baselines/v3_ttm_from_summary.py::v3_turn_fn`. Three-to-four LLM calls per turn:

1. Summary update (same as v2).
2. **Per-active-problem TTM inference from the summary.** For each active problem with an existing summary, infer `ttm_stage`. Regression is gated by `_SETBACK_KEYWORDS`.
3. Response call reads the main problem's summary + inferred TTM stage + a TTM-narrowed MI hint (base set from TTM, clipped by intent gate).

State adds `state.ttm_stages: dict[problem_name, stage]` on the graph. Still no attribute-level state.

Purpose in the ablation: isolate the contribution of TTM tracking when the underlying state is still a free-text summary.

### 7.5 v4 — Full Attribute Graph + HBM-Modulated MI

Uses `session_driver._default_turn_fn` (no baseline file). Four LLM calls per turn: extraction (§18.1) → batched level recompute (§18.2) → per-active-problem TTM inference from the observation stream (§18.3) → response. MI candidate selection uses the full TTM × HBM table + intent gate + safety fallback. In v4 the response is generated by a single-field call (just `{"response": ...}`) that receives the bundle + candidates.

Purpose in the ablation: isolate the contribution of structured HBM evidence (attributes with levels and stacks) plus evidence-driven TTM over v3's summary-driven TTM.

### 7.6 v5 — v4 + Merged CoT Instruction+Response

Identical state and identical first three LLM calls as v4. The fourth call is replaced by §18.4's merged CoT: one call produces `system_intent`, `instruction`, `chosen_techniques`, `response` together, with the technique commitment constrained to the candidate list and echoed in the instruction text.

Purpose in the ablation: isolate the contribution of explicit, technique-grounded chain-of-thought over a vanilla response call with the same state.

Files: `baselines/v1_history.py`, `baselines/v2_summary.py`, `baselines/v3_ttm_from_summary.py`, `baselines/common.py`, `session_driver.py`, `instruction_response.py`.

---

## 8. Data and Profiles

### 8.1 Source Dataset

HELP-E seeds user profiles from **EmoCare** — 3,743 multi-turn English emotional-support dialogues with per-utterance ESConv-style strategy labels and a seeker-profile blurb per dialogue. EmoCare lives at `EmoCare.jsonl` in the project root `[external]`; `help_e/` does not include the raw dialogues. HELP-E does **not** use EmoCare turn-by-turn — it only samples situation paragraphs and seeker profiles to seed Mind-1 personas.

### 8.2 20-Problem Vocabulary

`data/problems.md` defines the canonical 20-problem vocabulary used by `config.PROBLEM_VOCAB`. These are deliberately **non-crisis everyday concerns**; crisis topics (suicidality, abuse, acute self-harm) are out of scope for v1 and would trigger a scripted safety hand-off outside the pipeline. The 20 problems and their one-line glosses:

| id | gloss |
|---|---|
| academic_pressure | school/university workload and performance expectations |
| work_stress | job demands, burnout, work-life balance strain |
| sleep_problems | difficulty falling/staying asleep; non-restorative sleep |
| procrastination | chronic delay of tasks the user intends to do |
| general_anxiety | persistent worry not tied to a single domain |
| low_self_esteem | persistent negative view of self, self-worth |
| perfectionism | rigid standards that drive distress or inaction |
| social_anxiety | fear/avoidance of social situations |
| loneliness | felt lack of meaningful social connection |
| conflicts_with_partner | recurring tension with romantic partner |
| breakup_aftermath | coping with ended romantic relationship |
| conflicts_with_parents | recurring tension with parents |
| conflicts_with_friends | recurring tension with friends |
| financial_stress | money worries, debt, budget pressure |
| career_uncertainty | job searching, direction, or role transitions |
| caregiver_stress | providing care to an ill/aging family member |
| grief_of_loved_one | mourning loss (non-acute; bereavement ongoing) |
| health_anxiety | persistent worry about physical health |
| body_image_concerns | distress about appearance, weight, body |
| life_transition | moving, graduation, new role, big life change |

Rationale: these 20 cover most common non-clinical everyday concerns and keep MI-style reflection safely applicable — they do not require clinical intervention or crisis routing.

### 8.3 30 Seeded Profiles

`data/profiles/P01.yaml` … `P30.yaml` — 30 user personas, one per primary problem from the 20-vocab (with 10 additional diversity samples). Each YAML contains:

- `profile_id` (`P01` … `P30`)
- `seed_situation_paragraph` — verbatim from an EmoCare dialogue's `situation` field.
- `primary_problem` (one of the 20).
- `session_arc` — a narrative description of expected 3–4 session progression (e.g. "S1: venting; S2: ambivalence + one barrier; S3: small action planned; S4: maintenance check-in").
- `persona_draft` — structured persona covering personality_traits, communication_style, relevant_history, plus the `_raw_seeker_profile` pulled from EmoCare.

Profiles are deliberately diverse in primary problem, demographic signals, and communication style so the 30-profile × 5-system matrix exercises the system's breadth.

Files: `data/problems.md`, `data/attributes.md`, `data/profiles/*.yaml`, `data/seed_profiles.py`, `config.py` (PROBLEM_VOCAB).

---

## 9. Evaluation Design

HELP-E evaluates five metrics across systems v1–v5. All evaluation is fully simulated — no human raters in the v1 cycle. Statistical tests on top of per-profile metrics handle the sampling noise.

### 9.1 E1 — MITI Adherence (Judge-Based)

A separate LLM judge (main endpoint, temperature 0, `call_role=e1_judge`) scores every assistant turn on four dimensions with a 0–3 rubric plus five boolean flags. Dimensions (`JUDGE_DIMENSIONS` in `config.py`):

| dimension | 0 | 3 |
|---|---|---|
| reflect_vs_direct | ignored user's content/feeling | complex reflection of content + feeling |
| autonomy_support | lectured/moralized | explicit autonomy support |
| no_unsolicited_advice | full unprompted advice | no or earned advice |
| open_vs_closed_questions | multiple closed in a row | one thoughtful open or none |

Flags: `unsolicited_advice`, `moralizing`, `hallucinated_fact`, `too_long`, `too_many_questions`.

Aggregation (`eval/metrics.py::e1_aggregate`): per-dimension means, overall mean, flag rates, n scored. Judge identity is separated from the system prompt identity so the judge cannot simply echo the system's own view.

Files: `eval/judge.py`, `eval/metrics.py`.

### 9.2 E3a — TTM Progression

For each `(profile, problem)` pair, `stage_end_session - stage_start_session` indexed by `TTM_STAGES` is averaged across sessions, then averaged across problems to give a per-profile score. Wilcoxon signed-rank across profile pairs tests per-system pair. Direct measure of whether a system moves users forward on the change ladder.

Files: `eval/metrics.py::e3a_problem_advancement`, `e3a_profile_score`.

### 9.3 E3b — Extraction Agreement

Cohen's κ on paired `(problem, ttm_stage)` labels per user turn between the system's own extraction (from the main pipeline) and Mind-2's retrospective label. Only defined for systems that extract TTM — v3, v4, v5. Tests whether the system's online TTM reading agrees with a full-transcript retrospective reader. κ near 0 indicates noisy extraction; κ approaching 1 indicates robust agreement. By-stage breakdown flags which stages are hardest to extract reliably.

Files: `eval/metrics.py::e3b_cohen_kappa`.

### 9.4 E4 — Maintenance-Reach Ratio

Fraction of problems introduced at any point that reach `ttm_stage=maintenance` by the end of the final session, per Mind-2 labels. Per-profile ratio in `[0,1]`. A coarse but direct measure of problem-solving efficacy — how many of the concerns the user raised did the system help sustain change on.

Files: `eval/metrics.py::e4_maintenance_reach_ratio`.

### 9.5 E5 — 6-Dimensional ESC Satisfaction

Mean per-dimension and overall-mean scores from Mind-3, per profile. Dimensions listed in §6.3. Reports per-dim means so dimension-level regressions (e.g. an autonomy_respect drop) are visible.

Files: `eval/metrics.py::e5_profile_scores`.

### 9.6 Statistical Framework (§11.7)

- **Paired comparison per system pair:** Wilcoxon signed-rank test over the 30 matched personas. Reports statistic, p-value, n, and median difference.
- **Effect-size aggregation:** mixed-effects linear model `metric ~ system + (1 | persona)` treating the persona as a random intercept (captures persona-level variability).
- **Multiple-comparison correction:** Holm–Bonferroni within each metric, controlling the family-wise error rate across the five system pairs per metric.

All tests are optional imports (`scipy`, `statsmodels`). If a library is missing the helper returns `{'_unavailable': True}` instead of crashing.

### 9.7 What is *not* in v1 evaluation

- **E2 (AnnoMI ground-truth MITI).** Deferred — will use expert-annotated MITI labels from AnnoMI as an external, non-circular check on E1.
- **Human eval.** Deferred to a post-v1 user study.
- **External baselines** (B-hist style, CAMI). Current v1 cycle compares only the five internal ablations.

Files: `eval/metrics.py`, `eval/judge.py`, `simulator/mind2.py`, `simulator/mind3.py`.

---

## 10. Implementation Details

### 10.1 Entry Points

```bash
# single profile, single system
python -m help_e.run --system v5 --profile P01 --sessions 4 --turns 10

# all 30 profiles for one system
python -m help_e.run --system v4 --all-profiles --sessions 4 --turns 10

# inline judge
python -m help_e.run --system v5 --profile P01 --run-judge

# full matrix launcher (shell)
scripts/run_all.sh                                    # [external — lives above src/]
scripts/run_all.sh --smoke                            # 1×1×3, v3 only
scripts/run_all.sh --systems v4 v5 --parallel 2 --run-judge

# interactive demo
python -m help_e.ui.server    # http://localhost:8000
```

### 10.2 Reproducibility

Every LLM call derives its seed from `hash(SEED_SALT, profile_id, session_id, system, turn_id, call_role) mod 2^31`. Given the same profile YAML and the same `SEED_SALT`, every call under the pipeline is re-playable. Bumping `HELPE_SEED_SALT` (env var) is the supported way to re-seed an entire matrix.

### 10.3 Artifact Layout

All per-system artifacts land inside `help_e/`:

```
help_e/
  transcripts/{profile_id}/{system}/session_{NN}.json    # full dialogue + traces + merged outputs
  transcripts/{profile_id}/{system}/run_artifacts.json    # Mind-2, Mind-3, optional judge output
  graphs/{system}/{profile_id}_after_s{NN}.json           # graph snapshot per session
  logs/{profile_id}/session_{N}/turn_{NNN}.jsonl          # per-turn structured log
```

### 10.4 Configuration Knobs

Everything at the top of `config.py` is environment-overridable:

| env var | default | effect |
|---|---|---|
| `HELPE_MAIN_OLLAMA_URL` | `http://localhost:11436` | vLLM endpoint |
| `HELPE_MAIN_MODEL` | `meta-llama/Llama-3.3-70B-Instruct` | main model |
| `HELPE_SIM_OLLAMA_URL` | `http://localhost:11438` | Ollama endpoint |
| `HELPE_SIM_MODEL` | `gpt-oss:20b` | sim model |
| `HELPE_N` | 5 | last-N-turn window (retrieval, HBM modifiers) |
| `HELPE_K` | 3 | top-K stack entries per edge in bundle |
| `HELPE_ALPHA` | 1.0 | closeness α |
| `HELPE_THRESHOLD` | 2.0 | closeness threshold |
| `HELPE_TIMEOUT_S` | 600 | per-call HTTP timeout |
| `HELPE_SEED_SALT` | `helpe-v1` | seed salt prefix |

Files: `config.py`.

---

## 11. Current Status, Limitations, Future Work

### 11.1 Status

Implemented and smoke-tested: v1, v2, v3, v5. v4 shares the graph pipeline with v5 but replaces the merged call with a single-field response call. Full 30-profile × 5-system × 4-session matrix is the target for the next run cycle; early runs surfaced validation failures on `persona_update` that have been addressed.

### 11.2 Limitations

- **Simulated users only.** The entire evaluation pipeline runs against Mind-1. No human raters in v1; external validation is deferred.
- **LLM-as-judge for E1.** A separate model (same family as the main system but a different `PROJECT_IDENTITY`) judges MITI adherence. Inter-model correlation with human MITI coders has not been measured yet; E2 (AnnoMI) is the planned external check.
- **No external baselines.** The ablation compares internal systems only. A subsequent paper cycle should include CAMI or another MI-capable reference.
- **20-problem vocab.** Deliberately curated and non-crisis; generalisation to broader or clinical vocabularies is future work.
- **Single-language (English).** Multi-lingual MI is out of scope for v1.
- **No learned selection.** MI candidate selection is rule-based by design (for interpretability); a learned policy trained on AnnoMI is a natural extension.

### 11.3 Future Work

- **E2 — AnnoMI ground-truth MITI adherence.** Label a slice of HELP-E responses with an expert coder (or AnnoMI-trained classifier) to calibrate E1.
- **Fine-tuning.** Instruct-tune the main model on AnnoMI + AugESC + EmoCare to see if the structured graph still helps on top of a stronger base.
- **Real-user study.** Replace Mind-1 with consenting human users in a restricted well-being-companion setting.
- **Learned MI selector.** Replace the TTM × HBM rule table with a trained ranker; compare against the rule-based version with the same graph state.
- **Cross-dataset transfer.** Evaluate on ESConv / AnnoMI / AugESC with the same graph pipeline to test whether the Problem Graph transfers.

---

## 12. References *(stub — fill when expanding into the paper)*

- Motivational Interviewing — Miller & Rollnick [AUTHOR YEAR]
- Health Belief Model — Rosenstock [AUTHOR YEAR]
- Transtheoretical Model — Prochaska & DiClemente [AUTHOR YEAR]
- OARS (Open questions, Affirmations, Reflections, Summaries) — MI literature [AUTHOR YEAR]
- MITI coding scheme — Moyers et al. [AUTHOR YEAR]
- ESConv — Liu et al. [AUTHOR YEAR]
- AugESC — [AUTHOR YEAR]
- AnnoMI — [AUTHOR YEAR]
- EmoCare — [AUTHOR YEAR] *(source dataset; lives at project root as `EmoCare.jsonl`, external to `help_e/`)*
- Llama 3.3 70B Instruct — Meta, model card [AUTHOR YEAR]
- gpt-oss:20b (simulator model) — [AUTHOR YEAR]
- Outlines (constrained decoding) — [AUTHOR YEAR]
- json_repair — [AUTHOR YEAR]
- Cohen's κ — Cohen [AUTHOR YEAR]
- Wilcoxon signed-rank test — Wilcoxon [AUTHOR YEAR]
- Holm–Bonferroni correction — Holm [AUTHOR YEAR]
- Mixed-effects models — statsmodels [AUTHOR YEAR]

---

## Appendix A — Enum Source of Truth

All enums below are defined in `config.py` and enforced by the JSON schemas of every prompt. If a prompt references a value not in its enum, Outlines / the post-parse validator will reject it.

- **TTM_STAGES** (5): `precontemplation, contemplation, preparation, action, maintenance`
- **USER_INTENTS** (7): `vent, seek_validation, seek_advice, explore_option, report_progress, check_in, other`
- **ATTR_TYPES** (11): `perceived_severity, perceived_susceptibility, perceived_benefits, perceived_barriers, self_efficacy, cues_to_action, motivation, coping_strategies, past_attempts, triggers, goal`
- **PERSONA_FIELDS** (8): `demographics, personality_traits, core_values, core_beliefs, support_system, hobbies_interests, communication_style, relevant_history`
- **LEVELS** (3): `low, moderate, high`
- **ESC_DIMENSIONS** (6): `empathy, understanding, helpfulness, autonomy_respect, non_judgment, willingness_to_continue`
- **JUDGE_DIMENSIONS** (4): `reflect_vs_direct, autonomy_support, no_unsolicited_advice, open_vs_closed_questions`
- **MI_TECHNIQUES** (12): `T1..T12` (see §2.1 table)
- **PROBLEM_VOCAB** (20): see §8.2 table
- **CALL_ROLES**: every key of `TEMPERATURE_BY_ROLE` is a valid call role; unknown roles will refuse routing.

## Appendix B — Call Role → LLM Temperature

From `TEMPERATURE_BY_ROLE` (`config.py`):

| call_role | T | endpoint |
|---|---|---|
| extraction | 0.2 | main |
| level_recompute | 0.2 | main |
| ttm_infer | 0.2 | main |
| merged_response | 0.4 | main |
| persona_update | 0.2 | main |
| session_summary | 0.2 | main |
| mind1 | 0.4 | sim |
| mind2 | 0.2 | sim |
| mind3 | 0.2 | sim |
| e1_judge | 0.0 | main |
| v1_response | 0.4 | main |
| v2_summary_update | 0.2 | main |
| v2_response | 0.4 | main |
| v3_ttm_from_summary | 0.2 | main |
| v3_response | 0.4 | main |

## Appendix C — Key Files at a Glance

| concern | file(s) |
|---|---|
| orchestration | `session_driver.py`, `run.py` |
| graph state | `graph.py`, `graph_update.py` |
| per-turn prompts | `prompts/extraction.py`, `prompts/level_recompute.py`, `prompts/ttm_infer.py`, `prompts/common.py` |
| session-end prompts | `prompts/persona_update.py`, `prompts/session_summary.py` |
| retrieval | `retrieval.py` |
| MI selection | `mi_selector.py`, `knowledge/mi_mapping.md` |
| response | `instruction_response.py` |
| v1–v3 baselines | `baselines/v1_history.py`, `baselines/v2_summary.py`, `baselines/v3_ttm_from_summary.py`, `baselines/common.py` |
| simulator | `simulator/mind1.py`, `simulator/mind2.py`, `simulator/mind3.py` |
| evaluation | `eval/judge.py`, `eval/metrics.py` |
| config / enums | `config.py` |
| data / profiles | `data/problems.md`, `data/attributes.md`, `data/profiles/*.yaml`, `data/seed_profiles.py` |
| infra | `llm_client.py` |
| demo UI | `ui/server.py` |
