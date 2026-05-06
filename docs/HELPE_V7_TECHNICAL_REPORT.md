# HELP-E v7 — Complete Technical Report

*A full, plain-language walkthrough of what v7 of the HELP-E system does, why it does it, how every piece works, what changed from v6, what is genuinely novel, and what an input and output actually look like.*

*Written so a reader without a computer-science or behavioral-science background can read it from start to finish.*

> Companion to `HELPE_COMPLETE_TECHNICAL_REPORT.md` (the v6 deep-dive). Where v6 already explained a concept (MI, HBM, TTM, the simulator, the judges), this document recaps it briefly and points back. Everything that is **new in v7** is documented here in full. This revision (2026-05-05) reflects the current code at `src/help_e/baselines/v7_full.py` and the weighted-degree-centrality retrieval that replaced the original per-edge τ filter.

---

## Contents

1. [What HELP-E v7 Is — In One Page](#1-what-help-e-v7-is--in-one-page)
2. [Recap of the Three Theories We Use](#2-recap-of-the-three-theories-we-use)
3. [What Changed from v6 to v7 — The 30-Second Version](#3-what-changed-from-v6-to-v7--the-30-second-version)
4. [System Architecture — Bird's-Eye View](#4-system-architecture--birds-eye-view)
5. [The Multi-Agent Pipeline — Seven Phases of One Turn](#5-the-multi-agent-pipeline--seven-phases-of-one-turn)
6. [Agent 1 — User Intent + Immediate MI Move](#6-agent-1--user-intent--immediate-mi-move)
7. [Agent 2 — Inference (the Big Read)](#7-agent-2--inference-the-big-read)
8. [Agent 3a — Per-Attribute Summary + Level Update](#8-agent-3a--per-attribute-summary--level-update)
9. [Agent 3b — TTM Stage + System Intent + Strategy](#9-agent-3b--ttm-stage--system-intent--strategy)
10. [Agent 3c — Per-Edge Running Summary](#10-agent-3c--per-edge-running-summary)
11. [Agent 4 — The Pure-Python Core (no LLM)](#11-agent-4--the-pure-python-core-no-llm)
12. [Agent 5 — Response Generation (R1 → R2 → R3 → R4)](#12-agent-5--response-generation-r1--r2--r3--r4)
13. [Agent X — Rolling 5-Turn Summary](#13-agent-x--rolling-5-turn-summary)
14. [Agent P — End-of-Session Persona Update](#14-agent-p--end-of-session-persona-update)
15. [The v7 Graph — How Memory Is Organized](#15-the-v7-graph--how-memory-is-organized)
16. [Strategy Selection — The Two MI Picks Per Turn](#16-strategy-selection--the-two-mi-picks-per-turn)
17. [Reproducibility, Logging, and the Three-Tier LLM Routing](#17-reproducibility-logging-and-the-three-tier-llm-routing)
18. [Parallelism — What Runs at the Same Time, and Why](#18-parallelism--what-runs-at-the-same-time-and-why)
19. [End of Session — Judges and Persistence](#19-end-of-session--judges-and-persistence)
20. [The Fix-1 Venting Policy Change — A Worked Walkthrough](#20-the-fix-1-venting-policy-change--a-worked-walkthrough)
21. [Evaluation — Matrix, Criteria, Current State](#21-evaluation--matrix-criteria-current-state)
22. [Worked End-to-End Example — One Turn from the Smoke](#22-worked-end-to-end-example--one-turn-from-the-smoke)
23. [What Is Genuinely Novel in v7 (Contributions and Research Gap Filled)](#23-what-is-genuinely-novel-in-v7-contributions-and-research-gap-filled)
24. [Limitations and Honest Caveats](#24-limitations-and-honest-caveats)
25. [Glossary](#25-glossary)

---

## 1. What HELP-E v7 Is — In One Page

**HELP-E** is a research chatbot that has supportive conversations with people about everyday wellbeing problems — work stress, sleep, body image, breakups, grief, financial worries — across multiple sessions. It is **not** a therapist, **not** a crisis helpline, and **not** for clinical use. Think "thoughtful friend over coffee."

**v7** is the current production version of the HELP-E pipeline. The chatbot's job is unchanged from v6: read what the user says, remember it across sessions, talk back in a way that respects the user's autonomy and meets them where they are. **What changed is *how* it thinks** between reading a message and writing a reply.

The two ideas at the core of v7:

1. **Decompose the per-turn thought process into specialized small agents** that each do one job, instead of one big "inference" call that does everything. Think of it as the difference between a single doctor doing your whole exam vs. a small clinic where one person does triage, another reads your file, another writes the order, another writes the note. Specialization gives each agent simpler instructions, simpler outputs, and simpler validators — and the small agents can run in parallel on a small model, which is fast and cheap.

2. **Keep a structured, evidence-anchored memory of the user that grows turn by turn**, with each piece of new information traceable back to a specific user utterance, and with a separation between *what the user actually said* (the audit stack) and *what we currently believe* (the per-attribute summary + level + reasoning). When something has to be revised, we do it through transparent rules — not by overwriting old beliefs invisibly.

The novelty is **not** that v7 talks supportively. There are many supportive chatbots. The novelty is:

- **What v7 remembers** — a problem-graph with typed attributes, typed cross-problem connections, a chronological per-attribute summary, a per-problem TTM stage and system intent, a 9-field persona;
- **How it decides what to do** — two MI picks per turn, one for the user's immediate need, one for the bot's longer-arc nudge, both gated by shortlists tied to user intent and TTM stage;
- **How it grounds every claim** — every attribute entry must point at a substring of the actual user message; every level change must be justified in writing; every connection between two problems must be argued;
- **How it retrieves** — weighted-degree-centrality over the whole edge graph, not a fixed top-S list, so the cluster surrounding the current problems shows up naturally;
- **How rigorously it is measured** — three independent judges (MITI 4.2 globals, ESC dimensions, TTM transition rate) across simulated profiles × multiple sessions × multiple ablations.

---

## 2. Recap of the Three Theories We Use

These are explained in full in the v6 report (sections 3.1–3.4). Here is the one-paragraph recap.

**Motivational Interviewing (MI)** is a counseling style developed by William Miller and Stephen Rollnick. Its core insight is that arguing for change *backfires*. Instead, the counselor evokes the user's own reasons for change. Four principles: empathy, develop discrepancy, roll with resistance, support self-efficacy. Four basic skills (OARS): Open questions, Affirmations, Reflections, Summaries. **MISC** (Motivational Interviewing Skill Code) is a more granular vocabulary; v7 uses 10 selectable MISC codes (`support`, `facilitate`, `complex_reflection`, `reframe`, `inform_with_permission`, `raise_concern_with_permission`, `evoke`, `closed_question`, `advise_with_permission`, `structure`) and explicitly bans 6 anti-patterns (`direct`, `confront`, `warn`, `advise_without_permission`, `inform_without_permission`, `raise_concern_without_permission`).

**Health Belief Model (HBM)** says behavior change is driven by a small set of beliefs: perceived severity, perceived susceptibility, perceived benefits, perceived barriers, self-efficacy. HELP-E extends this to **11 attributes**. The split in code (`config.LEVEL_ATTR_TYPES`, `NON_LEVEL_ATTR_TYPES`) is **7 leveled + 4 non-leveled**:

- **Leveled (carry `low | medium | high | unknown`)**: `perceived_severity`, `perceived_susceptibility`, `perceived_benefits`, `perceived_barriers`, `self_efficacy`, `cues_to_action`, `motivation`.
- **Non-leveled (free-text only)**: `coping_strategies`, `past_attempts`, `triggers`, `goal`.

HBM is **what to track** about each problem.

**Transtheoretical Model (TTM)** says people moving through behavior change pass through stages — precontemplation, contemplation, preparation, action — and each stage calls for a different style of counseling. v7 uses the same 4 stages as v6 (we drop "maintenance" because a multi-session conversational study can't observe it). TTM is **which strategy** to use right now.

The three theories are orthogonal: HBM tells you *what to talk about*, TTM tells you *which move is appropriate*, MI/MISC tells you *how to phrase that move*. Combining all three with explicit separation of concerns is one of HELP-E's contributions — see §22.

The **20-problem v6 vocabulary was extended in v7 to 22 problems** (`config.PROBLEM_VOCAB`, lines 389–398): the v7 additions are `friendship_changes` and `role_loss`, added after a P05 substitution-failure analysis where users describing a lost role in a friend group were forced into the closest existing bucket (`social_anxiety`), polluting the audit stacks of an unrelated problem.

---

## 3. What Changed from v6 to v7 — The 30-Second Version

| Dimension | v6 | **v7** |
|---|---|---|
| Per-turn LLM calls | 2–3 large calls (inference, recompute, response) | **6–9 small + 2 large calls** (Agents 1, 2, 3a × N, 3b × M, 3c × E, 5, X) |
| User-intent classification | Inside the inference call | **Separate small agent (Agent 1)** + per-intent MI shortlist |
| Per-attribute state | `current_level` + flat evidence list | **`current_level` + `level_reasoning` + `level_change_confidence` + chronological `summary_text`** + audit_stack |
| Level updating | Recompute call rebuilds levels from evidence | **Agent 3a** updates one problem at a time, only for attributes touched THIS turn, with explicit conservative rules |
| TTM updating | Inside the recompute call | **Agent 3b** runs only for problems whose 3a actually changed at least one level (sparse, conditional) |
| Cross-problem connections | Two stacks per edge (cooccurrence + attribute_connections) | **One** structured `connection_entries` stack per edge, every entry typed at the *attribute pair* level, **plus a per-edge `summary_text`** maintained by Agent 3c |
| Edge running summary | None | **Agent 3c (EdgeSummaryAgent)** writes a chronological NL summary per edge that received new entries this turn; surfaced in the evidence_pack for Agent 5; deterministic Python-concat fallback if the SMALL LLM call fails |
| `system_intent` per problem | Implicit (recomputed each turn) | **Explicit field on the problem node**, carried forward unchanged when 3b doesn't fire |
| Rolling summary | None — past turns scraped from the transcript window | **Agent X** maintains a rolling 5-turn summary on the graph (read by Agent 1 + Agent 5) |
| Evidence retrieval | Top-S fixed retrieval per seed | **Weighted-degree centrality** over the full edge graph: each non-seed candidate's score = sum of its edge weights to seeds; keep candidates whose score ≥ τ × max_score; surface every edge from a kept candidate to a seed |
| Response generation | 2-pass (R1, R2) free-form CoT | **R1 → R2 → R3 → R4** progressive rewrites with one JSON object covering all four drafts; banned-opener and banned-phrase validators; ESC-first contract |
| Persona update | Per-field free-text | **Agent P**: per-field `useful` flag + integrated `updated_value` + `evidence_quote` |
| Sticky main problem | Implicit | **Explicit "STICKY MAIN PROBLEM RULE"** with positive and negative examples in Agent 2's prompt |
| Conservative levels | Soft suggestion | **Hard rules** in Agent 3a: change level only when unambiguous OR pattern of ≥3 converging instances |
| Inference robustness | Bare retry → empty trace | **Carry-forward fallback** (`build_agent2_carry_forward_output`): if Agent 2's retries are exhausted, build a synthetic Agent-2 output from the previous turn's main_problem + active problems, with empty new entries — keeps the pipeline alive |
| Smoke / unit testing | Mock-LLM smoke (`smoke_v6.py`) | Same v6 mock smoke; for v7, the supported test path is `eval/smoke_v7_v8.py` — a wet 3-turn smoke against the real backends |
| Problem vocabulary | 20 problems | **22 problems** (added `friendship_changes`, `role_loss`) |

The headline: v7 trades one big "do everything" inference call for a small constellation of single-purpose agents. Each agent has narrower instructions, simpler JSON outputs, and a dedicated validator. The graph stores richer per-attribute state (with `level_reasoning` and `level_change_confidence`) so the response generator can read evidence-grade information directly without re-deriving it. Retrieval moved from a fixed top-S to weighted-degree centrality so the *cluster of meaningfully connected* problems surfaces, not an artificially-fixed-size list.

---

## 4. System Architecture — Bird's-Eye View

The three layers from v6 are unchanged:

```
┌─────────────────────────────┐
│   USER SIMULATOR (Mind-1)   │   ← what the user "says" (v6 simulator stack reused)
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│      HELP-E CHATBOT v7      │   ← the 7-phase per-turn pipeline below
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│    EVALUATOR (judges)       │   ← MITI 4.2 + ESC + TTM transition rate
└─────────────────────────────┘
```

Each layer is independent: the simulator does not see what the chatbot is thinking; the chatbot does not see the simulator's hidden reasoning; the judges only read finished transcripts. This is what makes the system **auto-evaluable** — we don't need real human users.

### 4.1 Three-tier LLM routing

v7 uses an explicit **three-tier model routing** so each call goes to the cheapest model that can do the job well. The current production roster (after the May-2026 model swap):

| Tier | Used by | Current model | Why this tier |
|---|---|---|---|
| **MAIN (BIG)** | Agent 2 (inference), Agent 5 (response), Agent P (persona update) | **`meta-llama/Llama-3.3-70B-Instruct`** on Lightning AI | Agents that produce structured cross-domain inferences or write the user-facing reply; need the strongest model. The Lightning gpt-oss-120b previously used here proved unstable (HTTP 500 spikes during the April validation run); we switched to llama-3.3-70b on the same Lightning tenant. |
| **JUDGE (BIG)** | MITI 4.2 judge, ESC judge | Same `llama-3.3-70b` on a separate Lightning tenant | Judging needs the same caliber as the responder; the separate tenant prevents judge load from blocking next-session response calls. |
| **SMALL** | Agent 1, Agent 3a, Agent 3b, Agent 3c, Agent X | `gpt-oss-20b` on Lightning AI | Small, scoped jobs (one problem, one attribute, one summary) where 20B is plenty and parallel calls amortize. |
| **SIM** | Mind-1 simulator, session-context generator | Same `gpt-oss-20b` on Lightning | Believable user behavior, not clinical reasoning. SIM and SMALL share a model but route to different env vars so we can swap one without the other. |

The routing is per-`call_role` (e.g., `agent2_inference_v7` → MAIN, `agent3a_attr_update` → SMALL). Each run script sets the env vars; the LLM client (`llm_client.py`) reads them and routes. See §17 for the full env-var contract.

> Same-family judging (responder = llama-3.3-70b, judge = llama-3.3-70b) creates a self-evaluation bias risk. We accept it for now in exchange for stability. The ablation paper plan (Plan E in `plans/`) revisits this if Lightning's gpt-oss-120b returns to stable service.

---

## 5. The Multi-Agent Pipeline — Seven Phases of One Turn

When the user sends a message, v7's `v7_turn_fn` (in `src/help_e/baselines/v7_full.py:388`) runs seven phases. Phases marked **‖** run in parallel within themselves.

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
         │                              (carry-forward fallback if retries exhausted)
         └─────────────────────┬─────────────────────┘
                               ▼
                PHASE 2 — Pure Python (no LLM, ~1 ms)
                    Apply Agent 2 to graph: register problems,
                    append audit entries, stack typed connection
                    entries with usefulness flags. Bucket new
                    connection entries by edge_key for Phase 3c.
                               ▼
        ┌──────────────────────┼──────────────────────┐
PHASE 3 ‖▼ Agent 3a × N        │                      │
   for each problem with NEW   │   (parallel SMALL)   │
   info this turn:             │                      │
   per-attribute summary,      │                      │
   level + level_reasoning +   │                      │
   level_change_confidence,    │                      │
   new_info_useful flag.       │                      │
        └──────────────────────┴──────────────────────┘
                               ▼
                PHASE 3c ‖ Agent 3c × E (parallel SMALL, conditional)
                    For every edge that received NEW connection entries
                    this turn: refresh the per-edge `summary_text`
                    chronologically. Append-only — never rewrites
                    earlier sentences. Falls back to a deterministic
                    Python concat on LLM failure (the "summarize, never
                    drop" guarantee).
                               ▼
                PHASE 4 ‖ Agent 3b × M, where M = problems whose level
                actually changed in 3a (often 0 or 1 of N)
                    new TTM stage + ttm_reasoning +
                    system_intent + mi_for_system_intent
                    (MISC code from TTM-stage shortlist)
                               ▼
                PHASE 5 — Pure Python (no LLM, ~10 ms)
                    Recompute all edge weights with recency decay.
                    Assemble evidence_pack via weighted-degree
                    centrality (see §11): full main + 1-line others +
                    cluster-fallback edges + above-τ neighbor edges +
                    persona + rolling_summary_5turns.
                               ▼
                PHASE 6 — Agent 5 (BIG, ~15–30 s)
                    R1 → R2 → R3 → R4 in ONE structured output.
                    Validators: banned openers, banned phrases,
                    MISC code membership, schema.
                               ▼
                PHASE 7 — Agent X (SMALL, fire-and-forget, ~1 s)
                    Refresh rolling_summary_5turns from
                    (previous_summary, new_user_msg, new_bot_reply).
                               ▼
                       BOT RESPONSE → SIMULATOR
```

Each phase is documented in the next sections. Engineering details (parallelism, retries, hash-seeded sampling, logging) are covered in §17 and §18.

---

## 6. Agent 1 — User Intent + Immediate MI Move

**File**: `src/help_e/prompts/agent1_user_intent.py`. **Tier**: SMALL (gpt-oss-20b). **Inputs**: `rolling_summary_5turns` (from Agent X of the previous turn) + `current_user_message`. **Output**: 3 fields.

**Why a separate agent?** v6 baked intent classification into the big inference call. This caused two problems: (a) it bloated the inference prompt, and (b) when the inference call timed out or returned malformed JSON, you also lost the intent signal for the response generator. Splitting Agent 1 out lets the small model do this in ~1 second, in parallel with the big inference. If Agent 1 fails, we have a safe fallback (`small_talk` + `support`) and the rest of the pipeline still runs.

### 6.1 The 8 user_intent values

| value | meaning |
|---|---|
| `express_emotion` | venting, sharing how they feel — **the "supportive" lane** |
| `seek_validation` | asking for reassurance about a feeling, decision, or experience |
| `seek_information` | asking for facts, comparisons, definitions |
| `deliberate_decision` | weighing options, ambivalent, asking the bot to help them think |
| `request_plan` | wants the bot to help them make a concrete plan |
| `report_action` | reporting something they did since last turn |
| `resistance` | pushing back at the bot, dismissing reflections |
| `small_talk` | greetings, off-topic chatter, social maintenance |

### 6.2 The MI shortlist per intent (the gate that prevents misuse)

Agent 1's third output is `mi_for_user_intent` — the MISC code that names *how the bot will open its reply*. The LLM cannot pick freely from all 10 MISC codes; it must pick from a shortlist tied to the chosen intent. This is a **rule, not a soft preference**: a post-parse validator rejects out-of-shortlist picks.

The current shortlists (`mi_picker_v7.USER_INTENT_TO_MISC`):

| user_intent | shortlist (in this order) |
|---|---|
| `express_emotion` | **`support`**, `facilitate`, `complex_reflection` |
| `seek_validation` | `support`, `complex_reflection` |
| `seek_information` | `inform_with_permission`, `complex_reflection` |
| `deliberate_decision` | `complex_reflection`, `evoke`, `reframe` |
| `request_plan` | `advise_with_permission`, `structure`, `closed_question` |
| `report_action` | `support`, `complex_reflection`, `evoke` |
| `resistance` | `support`, `complex_reflection`, `reframe` |
| `small_talk` | `support`, `facilitate` |

The order matters because LLMs are anchor-biased to the first option. Leading with `support` for `express_emotion` produces "I hear you / that's a lot" rather than reflexive paraphrase. **The Fix-1 change in §20 was exactly this re-ordering** for `express_emotion`; the order shown above is the post-fix order, in the code today.

### 6.3 Example output

For the smoke turn 1 user message *"I'm staring at this deadline and everything feels so tight…"*:

```json
{
  "user_intent": "express_emotion",
  "user_intent_phrase": "User wants the chatbot to acknowledge the deadline pressure and self-doubt and sit with the feelings",
  "mi_for_user_intent": "support"
}
```

`user_intent_phrase` (free-text, ≤25 words) is a small affordance that helps Agent 5 understand *what specifically the user wants* — it's not just the enum value, it's a one-line elaboration in plain English.

---

## 7. Agent 2 — Inference (the Big Read)

**File**: `src/help_e/prompts/agent2_inference_v7.py`. **Tier**: MAIN (llama-3.3-70b). **Inputs**: current user message + last 4 raw turns (`AGENT2_RECENT_TURNS_N = 2` user-bot pairs, for coreference only) + names of previously-active problems with the previous main problem flagged. **Outputs**: 4 structured fields.

### 7.1 The four output fields

| field | what it is |
|---|---|
| `current_problems` | which problems from the 22-problem vocabulary are active in THIS turn |
| `main_problem` | the one problem this turn most centers on (subject to the **STICKY MAIN PROBLEM RULE** — see §7.3) |
| `problem_attribute_entries` | every NEW piece of information about a (problem, attribute) pair, drawn from the current message — each entry must include a substring of the actual user utterance as `supporting_utterance_span` (or null if only implied) |
| `problem_attribute_connections` | typed cross-problem links — every connection runs between an attribute on problem A and an attribute on problem B, with a `relation_type` from `config.RELATION_TYPES` (`causal`, `effect`, `reinforcing`, `conflicting`, `shared_trigger`, `shared_barrier`, `shared_goal`, `unclear_but_related`) |

### 7.2 The "primary evidence" rule

Agent 2 is told that the **current** user utterance is the primary source. Past turns are coreference context only. *"Do NOT extract attributes from past turns; they were already extracted on their turn."* This prevents double-counting and keeps the audit trail clean — every audit entry on the graph corresponds to exactly one (user message, attribute) discovery moment.

### 7.3 The STICKY MAIN PROBLEM rule

This is one of v7's most important behavioral rules. v6 had a tendency to flip `main_problem` mid-conversation whenever a secondary problem was mentioned, which fragmented the user's narrative. v7 spells out the rule explicitly:

> If a previous main_problem exists, **KEEP IT** as main_problem unless the current message contains STRONG evidence of focus shift. Strong evidence = (a) explicit mention of a different problem AND no mention of the previous main, OR (b) explicit redirect ("let's talk about X instead"). Mere co-mention or a brief tangent is NOT a focus shift.

Two examples are given inline:

```
Sticky example (KEEP previous main):
  previous_main_problem = work_stress
  user message: "the deadline is hitting harder, and my sleep is suffering
                  too — I lay awake replaying it"
  → main stays work_stress (sleep is brought in secondary).

Switch example (CHANGE main):
  previous_main_problem = work_stress
  user message: "actually let me talk about my dad — I keep arguing with
                  him every weekend"
  → main = conflicts_with_parents (explicit redirect).
```

### 7.4 Connections are not automatic

Another v7-explicit rule: an attribute connection is **NOT** justified by two problems sharing the same attribute type. The utterance itself must say the attributes are linked. Example given inline:

> *"I feel unable to cope with work, and I can't cope with my breakup either."*
> Correct: TWO `self_efficacy` entries (one per problem). NO connection — the utterance says low coping on both, it doesn't link them.

> *"The late-night cramming for finals is what's keeping me up at 3am — I keep thinking about the workload."*
> Correct: ONE connection (`academic_pressure.triggers shared_trigger sleep_problems.triggers`) because the utterance names a single driver firing both.

### 7.5 Validator and carry-forward fallback

A post-parse validator (`validate_agent2`) enforces structural rules: no duplicate (problem, attribute) entries; main_problem must be in current_problems; attribute connections require two distinct problems and reference attributes that exist for each side.

Failed validation triggers one retry. If the retry also fails (`LLMStructuredError`), v7 calls **`build_agent2_carry_forward_output`** instead of giving up: it synthesizes a "no new evidence" Agent-2 output that re-asserts the previous turn's `main_problem` and active problems with empty `problem_attribute_entries` and `problem_attribute_connections`. The pipeline keeps running; the turn just doesn't contribute new graph state. Without this, an Agent-2 hiccup would crater the entire turn's response.

---

## 8. Agent 3a — Per-Attribute Summary + Level Update

**File**: `src/help_e/prompts/agent3a_attr_update.py`. **Tier**: SMALL. **Concurrency**: one parallel call per problem with new info this turn (capped at 4 by a thread pool). **Skipped** for problems Agent 2 didn't write any new attribute or connection info about.

Agent 3a turns the raw audit entries Agent 2 emitted into **structured per-attribute beliefs** about the user. It is the v7 replacement for v6's recompute call.

### 8.1 What it sees and what it writes

For one problem, Agent 3a sees:
- The existing per-attribute records on the graph for *only the attributes that received new info this turn* (level, level_reasoning, summary_text). This keeps context small.
- The new attribute info Agent 2 emitted for this problem this turn.
- New connection info that touches this problem.

For each touched attribute, Agent 3a writes one record with:

| field | meaning |
|---|---|
| `summary_text` | the chronological NL paragraph for this attribute, with this turn appended as a new sentence (e.g., "t3: pressure spiked again in the afternoon") |
| `current_level` | one of `low`, `medium`, `high`, `unknown` (only for the 7 leveled attributes) |
| `level_reasoning` | a short justification for the level |
| `level_change_confidence` | `high`, `medium`, or `low` |
| `new_info_useful` | 0 or 1 — whether this turn added something genuinely new |

### 8.2 Conservative level-update rules (the prompt's hard rules)

This is where v7 puts most of the engineering. v6 sometimes flipped `current_level` from a single ambiguous instance, producing wobbly levels that confused downstream TTM logic. v7's prompt encodes 5 conservative rules:

1. **Only change `current_level` when evidence is unambiguous** OR a pattern of ≥3 converging instances exists. *"I'm so anxious I can't function"* → severity HIGH is unambiguous (single turn). But *"I asked a senior designer for input on the draft"* does NOT mean self_efficacy is low.
2. **Pattern overrides single instance.** Two prior moments + one converging this turn = set level with confidence=high.
3. **When uncertain, keep the prior level.** Default behavior under ambiguity is to extend `summary_text` and leave `current_level` untouched. confidence=low signals "we extended the narrative without committing."
4. **Counter-evidence matters.** If the user gives a healthy explanation for behavior that *could* look like a deficit, that's counter-evidence — the level should NOT move toward the deficit reading.
5. **`level_change_confidence` semantics:**
   - `high` = unambiguous single-turn signal OR pattern of ≥3 converging.
   - `medium` = pattern of 2 OR strong-but-debatable single-turn signal.
   - `low` = ambiguous or weak single instance — level should NOT change.

These rules let downstream Agent 3b (TTM) trust the level signal it reads.

### 8.3 The new_info_useful flag

A small but important contribution: Agent 3a explicitly labels each turn's contribution as either *redundant restatement* (useful=0, the summary gets a one-line "tN: restated — same point from another angle") or *genuinely new* (useful=1, the summary gets a new sentence describing the new dimension). This metric shows up in the per-turn diagnostics as `n_useful_summary_updates_total` — it's a window on whether the conversation is genuinely advancing or going in circles.

---

## 9. Agent 3b — TTM Stage + System Intent + Strategy

**File**: `src/help_e/prompts/agent3b_ttm_intent.py`. **Tier**: SMALL. **Conditional**: runs only for problems where Agent 3a actually changed at least one level (often 0 or 1 of N current problems). When 3b doesn't run, the previous TTM stage and `system_intent` carry forward unchanged.

### 9.1 What 3b sees and writes

Per problem, Agent 3b sees:
- The current attribute *levels* and their `level_reasoning` (no `summary_text` — keeps context tight).
- The previous TTM stage and reasoning.
- Which attributes Agent 3a actually changed this turn (so the prompt can ask "did this change push you across a stage boundary?").

It writes:
- `new_ttm_stage` — one of the 4 stages. **Regression is allowed**: if a setback shows up, 3b can step the stage back.
- `ttm_reasoning` — a short justification.
- `system_intent` — a one-line directive for the bot's nudge on the **next** turn (e.g., *"Reflect both sides of the ambivalence; do NOT plan yet"*). No HBM labels in the directive.
- `mi_for_system_intent` — a MISC code from the shortlist for the new TTM stage.

### 9.2 The MISC shortlist by TTM stage

Like Agent 1, Agent 3b picks from a stage-conditioned shortlist. Each stage has a "common" set (`support`, `facilitate`) plus stage-specific options, defined in `config.TTM_TO_MISC_COMMON` and `config.TTM_TO_MISC_STAGE_SPECIFIC`:

| stage | common | stage-specific |
|---|---|---|
| precontemplation | `support`, `facilitate` | `complex_reflection`, `reframe`, `inform_with_permission`, `raise_concern_with_permission` |
| contemplation | `support`, `facilitate` | `evoke`, `complex_reflection`, `inform_with_permission` |
| preparation | `support`, `facilitate` | `advise_with_permission`, `closed_question`, `structure` |
| action | `support`, `facilitate` | `structure`, `advise_with_permission`, `raise_concern_with_permission` |

`mi_picker_v7.shortlist_for_ttm_stage(stage)` returns the union with deduplication.

### 9.3 Why 3b is conditional

If Agent 3a didn't change any levels this turn, the user state didn't materially shift, so the TTM stage and system_intent stay where they were. Skipping 3b in that common case saves one full SMALL call per stable problem and avoids LLM-introduced noise in the stage signal.

---

## 10. Agent 3c — Per-Edge Running Summary

**File**: `src/help_e/prompts/agent3c_edge_summary.py`. **Tier**: SMALL. **Concurrency**: one parallel call per edge that received NEW connection entries this turn (capped at a small thread pool). **When**: only fires for edges with at least one new entry — most turns touch zero or one edge.

Agent 3c is the EDGE analog of Agent 3a. Where Agent 3a maintains a chronological NL summary per (problem, attribute), Agent 3c maintains a chronological NL summary per **edge** (per problem-problem pair). Same principle: **the audit stack** (`edge.connection_entries`) is append-only and never rewritten; **the summary** is the readable narrative the response generator actually consumes.

### 10.1 Why a per-edge summary at all

After a few turns, a single edge can accumulate 5, 10, 20+ structured connection entries. Surfacing all of them in Agent 5's prompt is wasteful — most are redundant restatements ("severity drives the felt barriers" said three different ways across three turns). And surfacing only the most recent N drops earlier mechanism evidence the responder may need.

The compromise: keep the full audit stack on the graph (lossless, never rewritten) and **also** maintain a compact running NL summary. The summary fits in ~150 words even after many turns, so Agent 5 reads it instead of paging through raw entries. This is the same "summarize, don't drop" pattern Agent 3a uses for attributes.

### 10.2 What Agent 3c sees and writes

For one edge, Agent 3c sees:
- The edge's existing `summary_text` (or empty string on first call).
- The list of new connection entries this turn (each with attribute_a, attribute_b, relation_type, why, supporting_quote).

It writes:
- `summary_text` — the updated full chronological summary. This turn appears as one new sentence anchored at `sS.tT`. Earlier sentences are NOT rewritten.
- `useful` — 0 or 1, mirroring Agent 3a's flag. 1 if today's entries added a genuinely new dimension; 0 if it's restatement.

### 10.3 Conservative summary rules (in the prompt)

- **Append, don't rewrite.** Every prior turn's sentence stays.
- **Skip redundancy.** If today's entries restate what's already in the summary, append a single short note (e.g., `s2.t7: restated — same link from another angle.`) and set `useful = 0`.
- **One new sentence for genuine novelty.** A new attribute pairing, a new relation_type, a clearer mechanism, an escalation, or an observable change in interaction → write a sentence describing the new dimension. `useful = 1`.
- **Quote sparingly.** Embed at most one short verbatim quote per turn-sentence, only when it adds something the surrounding sentence doesn't already capture.
- **No HBM diagnostic labels.** Use natural language ("severity drives the felt barriers"), not labeled jargon ("perceived_severity → perceived_barriers").
- **Aim for ~150 words.** Compression is the goal — but never drop a distinct mechanism the user voiced.

### 10.4 Deterministic fallback on LLM failure

If the SMALL-tier LLM call fails (timeout, schema error, exhausted retries), `run_agent3c` returns a Python-built fallback summary that prepends the new entries chronologically to the existing summary. This guarantees the "summarize, never drop" principle holds even when the LLM is unhappy. The fallback summary is plainer than the LLM's output but never loses information.

### 10.5 Why 3c is conditional, not always-on

Agent 3c only fires when `edge.connection_entries` got new entries this turn. In practice:
- Most turns add zero connection entries (the user said one thing about one problem).
- Turns that *do* add entries usually only touch one edge.
- So 3c's per-turn cost is typically zero or one extra SMALL call.

Skipping unaffected edges saves time and avoids LLM noise on stable connections. The summary stays exactly where it was until new evidence arrives.

### 10.6 Where the summary surfaces

`edge.summary_text` flows into the evidence_pack (§11.3) inside each problem-problem connection block. Agent 5 sees it as part of the "this is how these two problems relate, in plain language" surface — alongside the per-anchor connection_quotes (typed attribute pair, relation_type, verbatim quote) for citation.

---

## 11. Agent 4 — The Pure-Python Core (no LLM)

There is no Agent 4 LLM call. "Agent 4" is the conventional name for the **pure-Python phase** that turns the now-updated graph into the **evidence_pack** that Agent 5 will read. It does three things, all deterministic.

### 11.1 Recompute edge weights with recency decay

For each problem-problem edge, compute (in `graph.recompute_all_edge_weights`):
- `M_score` = recency-decayed count of connection entries (each entry contributes 1.0; older entries decay by the half-life `RECENCY_HALF_LIFE_TURNS = 20`).
- `A_score` = same shape, but treating each entry's contribution as 1.0 (v7 doesn't carry per-entry confidence — quality is gated upstream by the `supporting_quote` requirement).
- Normalize each by `log(1 + max_*)` so both are in [0, 1].
- `weight = α_M · norm_M + α_A · norm_A` with `EDGE_WEIGHT_ALPHA_M = 0.4`, `EDGE_WEIGHT_ALPHA_A = 0.6`.

This is the same shape as v6's edge weight, kept on purpose so the values are comparable across versions.

### 11.2 Weighted-degree-centrality retrieval (v7 default)

Replaces v6's "always take top-S edges" *and* the earlier v7 prototype's per-edge τ filter. The current method (`graph.select_neighbors_by_weighted_degree`, `graph_v7.py:349`):

For each non-seed candidate node `X`, compute its **aggregate** connection strength to the seed set (the current_problems this turn):

```
score(X) = Σ over seeds s with edge(s, X):  weight(s, X)
```

Keep candidate `X` iff `score(X) ≥ τ × max(score over all non-seed candidates)`, with τ = `EDGE_THRESHOLD_TAU_V7 = 0.5` by default. Then surface **every** edge from a kept candidate to a seed, regardless of that individual edge's weight.

The structural difference from a per-edge τ filter: two **sub-threshold** edges from the same candidate to two different seeds aggregate and survive if their sum is high enough — exactly the case the per-edge filter misses (a problem mechanistically connected to two current problems via two weak-but-real links). The network-science name is *weighted degree centrality restricted to the seed set with relative threshold* (Newman 2004; Freeman 1979 for the unweighted ancestor). Self-test Q3 in `graph_v7.py:822–889` is the unit test that proves the change matters.

### 11.3 Assemble the evidence_pack

`graph.assemble_evidence_pack(...)` returns a single dict with:

```
{
  "main_problem": {                             # FULL detail
    "name", "ttm_stage", "ttm_reasoning",
    "system_intent", "mi_for_system_intent",
    "level_attributes": {
      <attr>: {summary_text, current_level, level_reasoning,
               level_change_confidence, quotes: [{anchor, quote, inferred}]},
      ...
    },
    "non_level_attributes": {
      <attr>: {summary_text, quotes: [{anchor, quote, inferred}]}, ...
    }
  },
  "other_current_problems": [                   # 1-line each
    {"name", "ttm_stage", "system_intent_1line"}, ...
  ],
  "problem_problem_connections": [              # cluster fallback + WDC neighbors
    {"a", "b", "weight",
     "summary_text": "<Agent 3c's running NL summary for this edge>",
     "connection_quotes": [{anchor, attribute_a, attribute_b,
                            relation_type, quote}, ...]},
    ...
  ],
  "persona": <9-field persona>,
  "rolling_summary_5turns": "..."               # from Agent X
}
```

The connections block always includes the **cluster fallback** (every edge whose two endpoints are *both* in `current_problems`) plus the WDC neighbors. So if two of this turn's current problems are directly linked, the responder always sees that link, regardless of edge weight.

This is what Agent 5 reads. **The shape is deliberately asymmetric**: deep detail on the main problem, shallow on others. This mirrors how a thoughtful counselor holds the conversation: one problem in foreground, others in the background ready to be brought forward only if relevant.

---

## 12. Agent 5 — Response Generation (R1 → R2 → R3 → R4)

**File**: `src/help_e/instruction_response_v7.py` (the function `run_response_v7` at line 737). **Tier**: MAIN. The hardest single call in the pipeline.

### 12.1 The four progressive drafts in one JSON output

Agent 5 produces R1, R2, R3, R4 as a single JSON object. The drafts are **progressive rewrites**, not append-only steps — at each step the model may add, edit, restructure, or rephrase whatever was there before. The goal is a single coherent response, not four sentences glued together.

| draft | purpose |
|---|---|
| **R1** | Empathic answer using `mi_for_user_intent` + the rolling summary. Reflects what's underneath without diagnosing. No graph awareness yet. |
| **R2** | Integrate the system_intent nudge using `mi_for_system_intent`. Edit R1 — replace a phrase, restructure a sentence, change the entry point — whatever makes R2 read as one coherent reply with both moves integrated. **R2 must NOT contradict R1's empathic answer.** If user_intent is venting and system_intent wants to plan, hold off on the nudge — keep R2 = R1 and set `mi_for_system_intent_used = null`. |
| **R3** | Decide `used_evidence: true | false`. Evidence is for *strengthening*, not for *proving you remember*. Use only POSITIVE solution evidence (past_attempts that worked, hobbies, strengths, cues_to_action, persona anchors). PROBLEM evidence stays private — it shaped the choice; it does not become a sentence the user reads. |
| **R4** | Final refinement against IDENTITY ("friend" vs "therapist"), PURPOSE (ESC + MI), and `persona.communication_style` (blunt vs introspective, brief vs layered). If anything is off, rewrite. R4 is the actual user-facing response. |

The prompt explicitly distinguishes **PROBLEM evidence** (severity, barriers, low self_efficacy, regression signals — what the person is up against) from **POSITIVE SOLUTION evidence** (past_attempts that worked, coping_strategies, hobbies, strengths, persona anchors — what they have going for them). The rule:

> Problem evidence shapes your INTERPRETATION; solution evidence shapes what you SAY.

### 12.2 ESC-first contract

The response prompt has two grounded principles inscribed at the top:

> - **ESC**: always begin by acknowledging what the user is in — briefly, grounded in specific evidence — before any further move.
> - **MI**: draw out the user's own reasoning rather than impose yours; the user owns their choices. Empathy is the through-line.

This ESC-first stance is what makes v7 a *supportive* responder and not just an MI-correct one.

### 12.3 Hard validators

A post-parse validator (`_validate_factory`) enforces:

- **Banned openers** (lowercased prefix match): `"It sounds like"`, `"It seems like"`, `"It's like"`, `"That sounds like"`, `"That sounds really"`, `"That must be really"`, `"I can only imagine"`, `"That can be a really tough"`, `"That can be really tough"`, `"That's a really tough place"`. These are the chatbot tells that scream "AI counselor."
- **Banned phrases anywhere**: `"tough to navigate"`, `"tough thing to navigate"`, `"hard thing to swallow"`, `"I'm here for you"`, `"I'm here to listen"`. These are the empty-empathy fillers.
- **MISC code membership**: `mi_for_user_intent_used` and `mi_for_system_intent_used` (when non-null) must come from the canonical MISC vocabulary (`all_misc_codes()`).
- **Schema**: all 8 required fields present (`reasoning`, `mi_for_user_intent_used`, `mi_for_system_intent_used`, `r1`, `r2`, `r3`, `final_response`, `used_evidence`, `evidence_used`).

A validator failure triggers one retry. If the retry also fails, a safe fallback ("Hearing you. Take a breath; we can pick up wherever you want.") is emitted via `_safe_fallback`.

### 12.4 Reasoning field and `evidence_used` schema

`reasoning` (≤200 words) walks the four moves explicitly: (1) what they said + what's underneath, (2) what we already know that connects, (3) what will help (MI in mind), (4) how to say it. This is the audit trail.

`evidence_used` is a list of typed entries from `_EVIDENCE_TYPES`:
- `attribute` — `{problem, attribute, summary_excerpt}`
- `attribute_connection` — `{problem_a, attribute_a, problem_b, attribute_b, relation_type, why}`
- `problem_problem_connection` — `{problem_a, problem_b, weight, key_entries}`
- `persona` — `{field, content_excerpt}`
- `recent_turn` — `{turn_id, role}`

Each entry carries the `user_utterance` quote when applicable. If `used_evidence: false`, `evidence_used` must be empty. This lets us measure, post hoc, *what kinds of evidence the responder actually leans on* in a given turn.

---

## 13. Agent X — Rolling 5-Turn Summary

**File**: `src/help_e/prompts/agentX_rolling_summary.py`. **Tier**: SMALL. **When**: end of each turn, fire-and-forget.

Agent X reads `(previous_rolling_summary, new_user_message, new_bot_message, session, turn)` and writes a refreshed `rolling_summary_5turns` (≤150 words). This summary is stored on the graph (`graph.rolling_summary_5turns`) and read by next turn's Agent 1 (so Agent 1 doesn't need raw past turns) and by Agent 5 (inside the evidence_pack).

**Cold start**: previous_summary is `""`. Agent X writes the first summary based on the current turn alone.

**The point of Agent X** is to make the small agents stateless with respect to dialogue history. Agent 1 doesn't need to read 30 turns of transcript to know what the user has been on — it reads 150 words of clean narrative summary instead. This keeps small-tier prompt size predictable.

---

## 14. Agent P — End-of-Session Persona Update

**File**: `src/help_e/prompts/agent_p_persona_update.py`. **Tier**: MAIN (BIG). **When**: once per session, after all turns and judges have fired.

Agent P reads the existing 9-field persona, the full session transcript, and the final graph state. For each of the 9 fields it returns:

```
{
  "field": "<one of demographics, personality_traits, core_values,
              core_beliefs, support_system, hobbies_interests,
              communication_style, relevant_history,
              general_behavioral_traits>",
  "useful": 0 | 1,
  "updated_value": "<whole new value, integrated>" | null,
  "evidence_quote": "<the single transcript quote justifying the update>" | null
}
```

The simpler v7 shape (per-field useful + integrated updated_value + single quote) replaces v6's per-field free-text update. Two effects:
1. **Verifiability** — every field update is anchored to one specific quote.
2. **Auditability** — `useful=0` is a first-class signal that the field stayed unchanged. We can compute "how many fields actually moved this session?" directly.

For list-valued fields (`personality_traits`, `core_values`, `core_beliefs`, `hobbies_interests`, `general_behavioral_traits`), `updated_value` is a comma-separated string; the caller splits it.

---

## 15. The v7 Graph — How Memory Is Organized

**File**: `src/help_e/graph_v7.py`.

The graph holds everything we know about the user. v7's structure is a refinement of v6's: same top-level shape (a graph of problem nodes connected by typed edges, plus a persona), but each piece carries more structured state.

### 15.1 Top-level container

```
ProblemGraphV7
  profile_id: str
  persona: PersonaState                       (9 fields, see §14)
  problems: dict[str, ProblemNodeV7]          (keyed by problem name)
  edges: dict[(p1,p2), ProblemEdgeV7]         (keyed canonically alphabetically)
  rolling_summary_5turns: str                 (Agent X)
```

### 15.2 Problem node

```
ProblemNodeV7
  problem_name: str                           (one of 22)
  first_mentioned: (session_id, turn_id)
  last_mentioned: (session_id, turn_id)
  current_ttm_stage: str                      (default precontemplation)
  ttm_reasoning: str                          (Agent 3b's justification)
  system_intent: str                          (Agent 3b's nudge directive)
  mi_for_system_intent: str | None            (MISC code or None)
  goal: str | None
  level_attributes: dict[str, LevelAttributeStateV7]
  non_level_attributes: dict[str, NonLevelAttributeStateV7]
  previous_main_for_session: bool
```

`previous_main_for_session` is the flag that makes the **STICKY MAIN PROBLEM** rule possible: each turn, exactly one problem (or zero) carries this flag, and Agent 2 is told which it is.

### 15.3 Per-attribute state (the v7 expansion)

```
LevelAttributeStateV7                          NonLevelAttributeStateV7
  current_level: str  (low|med|high|unknown)    summary_text: str
  summary_text: str                             audit_stack: list[AuditEntry]
  level_reasoning: str
  level_change_confidence: low|medium|high
  audit_stack: list[AuditEntry]
```

The audit_stack is the **append-only list of every (turn, evidence span) discovery** for this attribute. Nothing is ever rewritten there. The summary_text + level + reasoning are the **interpreted view** maintained by Agent 3a. This separation is critical — it means we can always reconstruct *what the user actually said* even when our interpretation evolves.

The 7 leveled + 4 non-leveled attribute split lives in `config.LEVEL_ATTR_TYPES` and `config.NON_LEVEL_ATTR_TYPES`. Leveled attributes also surface a **deduplicated quote list** (`_dedup_attribute_quotes`) into the evidence_pack so Agent 5 can cite the actual user words, not just the current level.

### 15.4 Connection entries + edge summary

```
ProblemEdgeV7
  problem_1, problem_2: str        (canonical alphabetical order)
  connection_entries: list[ConnectionEntryV7]   (append-only audit stack)
  summary_text: str                 (chronological NL — maintained by Agent 3c)
  weight: float                     (recency-decayed strength)

ConnectionEntryV7
  turn_id, session_id
  attribute_a, attribute_b: str
  relation_type: str                          (one of 8 from RELATION_TYPES)
  why: str
  supporting_quote: str | None
  useful: int                                 (1 if (attr_a, attr_b,
                                                relation_type) is new for
                                                this edge)
```

Each problem-problem edge carries one stack of these structured entries **plus** one running `summary_text` that Agent 3c keeps up to date (see §10). The `useful` flag is computed in Python (`has_relation_type`) when the entry is appended — it's 1 the first time we see a given (attribute pair, relation type) on this edge, 0 every time after. This gives us a turn-by-turn signal of "did the conversation reveal a new dimension of this connection?" The summary_text is the readable surface Agent 5 actually consumes; the entries are the lossless audit trail for citation and replay (`_dedup_connection_quotes`).

### 15.5 Cold start

A fresh `ProblemGraphV7(profile_id="P05")` has empty persona, empty problems, empty edges, empty rolling summary. **There is no pre-seeding.** v7 builds the graph from scratch, turn by turn, so the chatbot's behavior reflects only what the user actually said.

### 15.6 Persistence

`graph.save(path)` writes a JSON snapshot via `to_json_dict`. `ProblemGraphV7.load(path)` round-trips through `from_json_dict`. Snapshots are saved at end-of-session under `config.GRAPH_V6_DIR / v7 / <profile>_after_s<NN>.json` (the directory name is legacy from v6 — v7 reuses it for storage).

---

## 16. Strategy Selection — The Two MI Picks Per Turn

v7 makes **two MI picks per turn**, not one. This is one of v7's clearest contributions and worth a section.

### 16.1 Why two picks?

The previous turn's analogy was "a counselor decides one MI move per turn." But careful counselors actually balance two concerns simultaneously:

1. **What does the user need from me right now?** (immediate, surface-level — "are they venting? asking? deciding?")
2. **What is the right longer-arc move for this person on this problem?** (TTM stage — "are they in contemplation, so I should evoke? in preparation, so I should structure?")

These can disagree. A user in TTM contemplation might still need pure venting space *this turn*. A user in TTM action might still ask for information first. v7 lets the response generator hold both:

- `mi_for_user_intent` (Agent 1) — the **opening / first move** of the reply.
- `mi_for_system_intent` (Agent 3b) — the **longer-arc nudge** to integrate.

Agent 5's R1 uses the user-intent move; R2 integrates the system-intent nudge. **R2 has explicit permission to skip the nudge** if the two moves conflict (e.g., venting + plan → hold off on the plan). When that happens, `mi_for_system_intent_used = null` and R2 = R1.

### 16.2 The shortlists are not optional

Both Agent 1 and Agent 3b pick from **shortlists** — closed sets of MISC codes tied to (intent / stage). Both validators reject out-of-shortlist picks. This is the structural reason v7 cannot accidentally direct or confront — those codes (`direct`, `confront`, `warn`, the three `*_without_permission` codes) are in `MISC_INCONSISTENT_CODES` and are **never** in any shortlist.

### 16.3 The Fix-1 venting reorder

See §20 for the full story. The short version: the order of codes in the shortlist matters because the LLM is anchor-biased to the first option. Fix-1 reordered the `express_emotion` shortlist from `(complex_reflection, support, facilitate)` to `(support, facilitate, complex_reflection)` so that venting turns now lead with `support` ("I hear you / that's heavy") rather than reflexive paraphrase.

---

## 17. Reproducibility, Logging, and the Three-Tier LLM Routing

### 17.1 Hash-seeded sampling

Every LLM call has a `CallContext` containing `(profile_id, session_id, system, turn_id, call_role)`. The seed is computed deterministically from these fields:

```
seed = SHA-256("HELP-E_seed_v1 | profile | session | system | turn | call_role") % 2³¹
```

So a re-run of the same profile in the same system at the same turn for the same call role gets the same seed → the LLM (with temperature ≥ 0) produces a comparable output. Different agents at the same turn get different seeds. This is what makes v7 results reproducible across machines (modulo backend version changes).

### 17.2 Per-call audit log

Every LLM call writes one JSONL line to:
```
output/<run_id>/logs/<profile>/session_<N>/turn_<NNN>.jsonl
```

with fields including: `ts`, `profile_id`, `session_id`, `system`, `turn_id`, `call_role`, `attempt`, `model`, `temperature`, `seed`, `latency_s`, `error`, `raw_response`. Multiple lines per turn (one per agent call). This is the audit trail — every word the LLM produced, on what call, at what time, with what seed.

### 17.3 The three-tier env-var contract

| Env var | Purpose |
|---|---|
| `HELPE_MAIN_OLLAMA_URL`, `HELPE_MAIN_MODEL`, `HELPE_MAIN_API_KEY` | Where MAIN-tier calls go (Agents 2, 5, P). Default model is now `lightning-ai/llama-3.3-70b-instruct`. |
| `HELPE_JUDGE_OLLAMA_URL`, `HELPE_JUDGE_MODEL`, `HELPE_JUDGE_API_KEY` | Where the MITI/ESC judges go. Same model family as MAIN, separate Lightning tenant by default. |
| `HELPE_SIM_OLLAMA_URL`, `HELPE_SIM_MODEL`, `HELPE_SIM_API_KEY` | Where the simulator goes (Mind-1, session_context). gpt-oss-20b. |
| `HELPE_SMALL_URL`, `HELPE_SMALL_MODEL` | Where SMALL calls go (Agents 1, 3a, 3b, 3c, X). Defaults to SIM if unset. |
| `HELPE_REASONING_EFFORT` | `low` / `medium` / `high` for gpt-oss models — `low` trims internal CoT for extraction tasks. Default `low`. |
| `HELPE_MAX_TOKENS_*` | Per-call-role token caps. |
| `HELPE_TRANSCRIPT_DIR`, `HELPE_GRAPH_V6_DIR`, `HELPE_LOG_ROOT` | Output paths (isolated per run). |

Per-role temperature (`config.TEMPERATURE_BY_ROLE`): `agent5_response_v7 = 0.4` (the warmest, because it writes prose); all extraction agents (`agent1_user_intent`, `agent2_inference_v7`, `agent3a_attr_update`, `agent3b_ttm_intent`, `agent3c_edge_summary`, `agent_p_persona_update`) at 0.2; `agentX_rolling_summary` at 0.3.

### 17.4 Output token rate limiter

v7's LLM client (`llm_client.py`) wraps every call in a per-tenant token bucket (~200 tok/sec, 4-sec burst) AND a global semaphore that caps concurrent calls per tenant. The cap is what enforces "max 2 parallel calls per tenant" no matter what the per-profile parallelism is set to — it prevents a 4-profile parallel run from saturating Lightning into queueing.

### 17.5 Slow-call alarm

Any successful call > 90 s logs a WARNING line (`slow LLM call`). Grep-able by an operator who wants early warning of Lightning-AI latency drift mid-run.

### 17.6 Dual-tenant Lightning split

Because the Lightning gpt-oss-20b SIM/SMALL endpoint has a per-tenant rate limit, the v7 and v8 launch scripts intentionally point at *different sub-accounts* on the same provider so that running v7 and v8 in sequence (or in parallel) doesn't compound 429 backoff into one tenant. See `scripts/run_v7_lightning_70b.sh` for the assignments.

---

## 18. Parallelism — What Runs at the Same Time, and Why

This is one of the things v7 explicitly engineers for.

### 18.1 Within a turn

v7 has three parallel-fan-out points within a single turn:

| Phase | What runs in parallel | Bound | Why |
|---|---|---|---|
| Phase 1 | Agent 1 (SMALL) ‖ Agent 2 (MAIN) | 2 threads (`thread_name_prefix="v7_p1"`) | Agent 1's classification doesn't depend on Agent 2's inference and vice versa — both read the user message. Running them in parallel saves ~1 s per turn. |
| Phase 3 | Agent 3a × N (SMALL) | min(4, N) threads (`v7_3a`) | Per-problem attribute updates are independent — different problems, different attribute records. N is usually 1–3. |
| Phase 3c | Agent 3c × E (SMALL) | min(4, E) threads (`v7_3c`) | Per-edge running-summary updates are independent. E = edges with new connection entries this turn; usually 0 or 1. Skipped entirely on turns with no new connections. |
| Phase 4 | Agent 3b × M (SMALL) | min(4, M) threads (`v7_3b`) | Per-problem TTM updates are independent. M ≤ N and is usually 0–1 (only problems with level changes). |

All inside ThreadPoolExecutor blocks. The per-tenant cap in `llm_client.py` then restricts total concurrent LLM calls per tenant — so the *actual* concurrency seen at the backend is whichever is smaller (per-phase max, tenant cap).

### 18.2 Across turns within a profile

**Cross-session judge parallelization**: when session N finishes, the MITI judge and ESC judge for session N are submitted to a `judge_pool` (8-worker thread pool) and the driver immediately starts session N+1's first turns. The judges run *in parallel with the next session*, so the user-facing latency of "running 3 sessions" is not *(session_time + judge_time) × 3* but *session_time × 3 + judge_time*. At the very end of the profile, the driver `wait()`s on any pending futures so all judge files exist by the time `run_artifacts.json` is written.

### 18.3 Across profiles

`scripts/run_v7_lightning_70b.sh ... --max-parallel-profiles N` runs N profiles concurrently against the LLM backends. `python -m help_e.run` uses a ThreadPoolExecutor of size N, and inside each profile worker, the in-turn fan-out described above still happens. With N=2, the backend sees up to 2 profiles × 2 in-turn = 4 candidate concurrent calls, capped by the per-tenant cap. So N=2 is the sweet spot for our Lightning tenants: enough to keep the backend busy while one profile is in a Python phase, not so many that calls queue.

### 18.4 Why this matters for the run plan

For the current evaluation matrix (P05–P29 odd profiles, 3 sessions × turns 30/20/20):
- A single turn averages ~30 s wall time.
- A session = 30 (or 20) turns ≈ 15–18 min.
- A profile = 3 sessions ≈ 50 min wall time.
- 13 odd profiles ÷ parallel 2 ≈ 13 hours wall clock.

The judge parallelization saves an additional 10–15% by overlapping judges with the next session's work.

---

## 19. End of Session — Judges and Persistence

### 19.1 MITI 4.2 judge

**File**: `src/help_e/eval/judge.py` (the `miti_judge_v6` entry point, reused by v7). **Tier**: JUDGE (BIG).

Reads the full session transcript (user+bot turns) and rates 4 MITI globals:

| Global | What it measures |
|---|---|
| `cultivating_change_talk` | Did the bot evoke the user's own arguments for change? |
| `softening_sustain_talk` | Did the bot roll with sustain talk rather than argue? |
| `partnership` | Was the bot collaborative or directive? |
| `empathy` | Did the bot accurately mirror the user's feelings? |

Scale: 1 (lowest) to 5 (highest). One justification sentence per global. Output: `output/<run>/<profile>/v7/miti_judge_s<NN>.json`.

### 19.2 ESC judge

**File**: `src/help_e/eval/esc_judge.py`. **Tier**: JUDGE.

Rates 6 ESC dimensions: empathy, understanding, helpfulness, autonomy_respect, non_judgment, willingness_to_continue. Same 1–5 scale. Output: `output/<run>/<profile>/v7/esc_judge_s<NN>.json`. (This is the per-session judge that replaced the old multi-session "mind3" pass that suffered JSON truncation on long inputs.)

ESC and MITI are *independent* judges — they read the same transcript but apply different rubrics. They sometimes disagree; when they do, it's informative. (See §11.5 of the v6 doc on the LLM-judging-LLM concern.)

### 19.3 TTM transition rate

Computed deterministically from the graph snapshots at session end. For each (profile, problem) pair, count the number of stage transitions across the multi-session arc. Aggregate as `n_transitions / n_problem_sessions`. This is the "did real progress happen?" signal. Computed by `eval/metrics.py`.

### 19.4 Persona update

Agent P fires once at session end (see §14).

### 19.5 What persists between sessions

- The **graph**: problems, edges, all per-attribute state, all audit stacks, persona, rolling summary.
- The **per-session JSON transcript** (with turn_traces — the full Agent 1/2/3a/3b/3c/5 outputs per turn).
- The **judge files** (one MITI, one ESC per session).
- The **graph snapshot** (one per session: `<profile>_after_s<NN>.json`).
- The **per-call jsonl logs** (one file per turn).

The simulator does NOT persist anything between sessions. Mind-1 is reseeded each session from the YAML profile + session arc.

---

## 20. The Fix-1 Venting Policy Change — A Worked Walkthrough

This change happened during evaluation. It's a useful concrete example of what "tuning v7" actually looks like.

### 20.1 The complaint

From a careful read of the P01 transcripts: every turn where the user vented, the bot's reply opened with a paraphrase of what the user just said, then asked a probing question. Reading several turns in a row, this pattern felt **parrot-y** rather than supportive.

### 20.2 The mechanism that produced it

Two code paths combined:

**Path A — `mi_for_user_intent` shortlist**: the venting shortlist was `(complex_reflection, support, facilitate)`. The LLM is anchor-biased to the first option, so Agent 1 picked `complex_reflection` for almost every venting turn. By definition, complex_reflection is "say what they said in your own words, going one step underneath." That produces paraphrase.

**Path B — `mi_for_system_intent` nudge**: every turn pairs R1 (the user-intent move) with R2 (the system-intent nudge). For users in contemplation, the system_intent MISC code is almost always `evoke`. So every R2 layered an evoking question on top of the paraphrase.

Result: paraphrase → question. Forever.

### 20.3 The fix

A 1-line change in `mi_picker_v7.py`:
```python
# Before
"express_emotion":  ("complex_reflection", "support", "facilitate"),
# After
"express_emotion":  ("support", "facilitate", "complex_reflection"),
```

Plus a one-bullet edit in `prompts/agent1_user_intent.py`'s "HOW TO CHOOSE" block:

> express_emotion / seek_validation → support / acknowledge first; reflect only when you can name what's UNDERNEATH the user's words (don't just rephrase them). Don't plan.

### 20.4 The effect

Before Fix-1 (P01 turn 1):

> *"Sounds like the pressure is really weighing on you, and each client comment is feeding a growing doubt about your own abilities. What do you think might happen if you could find a way to ease some of that stress…"*

`mi_for_user_intent_used: complex_reflection`.

After Fix-1 (P01 turn 1, fresh profile message):

> *"You're feeling the deadline closing in while the client's feedback has sparked a wave of self-doubt. You notice a push to work harder, yet you're also questioning whether that pressure will truly help you feel more confident. You're caught between the urge to keep going and the worry that more effort might just amplify the doubt."*

`mi_for_user_intent_used: support` for all 3 turns. **The picker shifted as intended.** The response writer (Agent 5) still produced reflective wording — its R1 contract describes "Empathic answer... reflects what's underneath" without distinguishing what `support` should sound like vs `complex_reflection`. That deeper fix (Fix-2: broaden the venting bypass in R2 so high-distress turns drop the mandatory question) was deliberately deferred — see §24.

### 20.5 What this episode shows about v7

The single-line picker change had a clean, observable effect on Agent 1's MI selection — *because* the strategy-selection layer is decoupled from the writer. v6 would have required editing the response prompt directly. v7's separation of concerns is what makes a one-line change behaviorally meaningful and observable.

---

## 21. Evaluation — Matrix, Criteria, Current State

### 21.1 The matrix

We evaluate v7 across simulated profiles (currently the 13 odd profiles P05–P29) × 3 sessions per profile × turns 30/20/20 per session. Each session produces:
- `session_<NN>.json` (full transcript with turn_traces).
- `mind1_reasoning_s<NN>.jsonl` (the simulator's hidden CoT — for understanding the user, not for the chatbot).
- `session_context_s<NN>.json` (the per-session arc context the simulator was given).
- `miti_judge_s<NN>.json` (4 MITI globals).
- `esc_judge_s<NN>.json` (6 ESC dimensions).
- `<profile>_after_s<NN>.json` (graph snapshot).

### 21.2 The three criteria

| Criterion | Lens | Source | What it measures |
|---|---|---|---|
| MITI 4.2 globals | Technique | LLM judge | Was the MI correct? |
| ESC dimensions | Experience | LLM judge | How did the user feel? |
| TTM transition rate | Outcome | Pure computation from graph | Did real progress happen? |

These three are deliberately independent — one can be high while another is low. A bot can have great MITI and still feel cold on ESC; a bot can score high on ESC and never produce TTM transitions because it's all pure validation.

### 21.3 Aggregation and ablation

`eval/matrix_report.py` reads all session-level judge files and produces the per-system summary table. `eval/ablation_report.py` does the same comparison across systems (the default set is `("v1", "v3", "v6")` plus v7 and v8 as they complete) using paired Wilcoxon signed-rank tests on the headline scalars. `eval/compare_v3_v7_v8.py` produces side-by-side per-profile comparisons including v7 / v8 specific diagnostics (`n_problems_detected`, `n_audits_used_in_response`, `state-progression rate`, `turns_to_action`).

---

## 22. Worked End-to-End Example — One Turn from the Smoke

Concrete trace from a v7 smoke run, turn 1.

### 22.1 What the user said

> *"I'm staring at this deadline and everything feels so tight. The client's critique just made me doubt whether I'm even good enough at this. I keep thinking I should just push harder, but I'm not sure that's the right move."*

### 22.2 Phase 1 — Agent 1 + Agent 2 (parallel)

**Agent 1** (~1 s, SMALL):
```json
{
  "user_intent": "express_emotion",
  "user_intent_phrase": "User wants the chatbot to acknowledge the deadline pressure and self-doubt and sit with the feelings",
  "mi_for_user_intent": "support"
}
```
Picked `support` (Fix-1 active — first item in the express_emotion shortlist).

**Agent 2** (~15 s, MAIN):
```
current_problems = [
  {"problem_name": "work_stress", "explanation": "deadline pressure",
   "supporting_utterance_span": "I'm staring at this deadline"},
  {"problem_name": "low_self_esteem", "explanation": "doubt of competence",
   "supporting_utterance_span": "doubt whether I'm even good enough"},
  {"problem_name": "general_anxiety", "explanation": "tightness, push-harder loop",
   "supporting_utterance_span": "everything feels so tight"}
]
main_problem = work_stress  (sticky main: cold start, deadline frames the message)
problem_attribute_entries = [
  (work_stress, perceived_severity, "user describes overwhelming tightness", "everything feels so tight"),
  (work_stress, self_efficacy, "user questions whether harder effort will help", "I'm not sure that's the right move"),
  (low_self_esteem, perceived_severity, "competence doubt is acute", "good enough at this"),
]
problem_attribute_connections = [
  (work_stress.self_efficacy, low_self_esteem.perceived_severity, reinforcing,
   "client critique drives both work-stress severity and self-doubt", "doubt whether I'm even good enough at this")
]
```

### 22.3 Phase 2 — Apply to graph (Python)

Three problems registered with `first_mentioned=(1,1)`. work_stress flagged as previous_main. Three audit entries appended. One connection entry stacked on the work_stress↔low_self_esteem edge with `useful=1`.

### 22.4 Phase 3 — Agent 3a × 3 (parallel SMALL)

Three calls, one per problem with new info. For work_stress, the Agent 3a output for `perceived_severity`:
```json
{
  "attribute_name": "perceived_severity",
  "summary_text": "t1: User describes the deadline as 'staring at' them with everything feeling tight — first signal of high severity.",
  "current_level": "high",
  "level_reasoning": "Single unambiguous statement of overwhelming tightness around the deadline.",
  "level_change_confidence": "high",
  "new_info_useful": 1
}
```

For `self_efficacy` (work_stress), the level stays `unknown` (single ambiguous instance — the user is *questioning* whether to push harder, not stating low competence).

### 22.5 Phase 3c — Agent 3c × 1 (parallel SMALL)

The work_stress↔low_self_esteem edge received one new connection entry, so Agent 3c fires once and writes:
```
"s1.t1: Client critique reinforces both the felt severity of the work pressure and the self-doubt — same trigger fires both responses."
```

### 22.6 Phase 4 — Agent 3b × M (parallel SMALL)

Two problems had levels actually change: work_stress (severity → high) and low_self_esteem (severity → high). For work_stress, Agent 3b emits:
```json
{
  "new_ttm_stage": "contemplation",
  "ttm_reasoning": "Severity is high; user is weighing whether harder effort helps — ambivalence is present.",
  "system_intent": "Reflect both sides of the push-harder vs. ease-up tension; do not propose a plan yet.",
  "mi_for_system_intent": "evoke"
}
```

### 22.7 Phase 5 — Pure-Python evidence pack assembly

Edge weights recomputed (the new work_stress↔low_self_esteem edge gets a non-zero weight). WDC retrieval surfaces the cluster (work_stress↔low_self_esteem is fully inside current_problems, so it's in the cluster fallback). The evidence_pack now contains:
- Full work_stress block (TTM=contemplation, system_intent="reflect both sides…", level_attributes for severity and self_efficacy with quotes).
- 1-line entries for low_self_esteem and general_anxiety.
- One problem_problem_connection (work_stress↔low_self_esteem) with Agent 3c's NL summary.
- Persona = empty (cold start).
- rolling_summary_5turns = "" (cold start).

### 22.8 Phase 6 — Agent 5 (BIG)

Reads:
- `mi_for_user_intent: support` (Agent 1)
- `mi_for_system_intent: evoke` (Agent 3b)
- the full evidence_pack
- the user's current message

Produces R1 → R2 → R3 → R4. Final R4:

> *"You're feeling the deadline closing in while the client's feedback has sparked a wave of self-doubt. You notice a push to work harder, yet you're also questioning whether that pressure will truly help you feel more confident. You're caught between the urge to keep going and the worry that more effort might just amplify the doubt."*

`mi_for_user_intent_used: support`. `mi_for_system_intent_used: complex_reflection`. (Note: the system-intent move actually executed was `complex_reflection`, not `evoke` — Agent 5 chose the safer reflective move on this turn, allowed because the shortlist for contemplation includes complex_reflection.)

### 22.9 Phase 7 — Agent X

Reads `(previous_summary="", new_user_msg, new_bot_msg)`. Writes a ~80-word rolling summary that captures the deadline pressure + self-doubt theme. Stored on the graph for next turn's Agent 1 / Agent 5 to read.

### 22.10 The audit trail

Every step produced a JSONL log line under `output/.../logs/P01/session_1/turn_001.jsonl`. The bot's reasoning, the MISC codes used, the seed, the latency — all captured. We can replay the turn by re-reading that file.

---

## 23. What Is Genuinely Novel in v7 (Contributions and Research Gap Filled)

Below, the gap is stated against the cross-category synthesis from `Related_Works_Report.md` and the closest prior systems are named.

### 23.1 Multi-agent decomposition with role-typed call routing

Most prior emotional-support / MI dialogue systems use **one** model for everything (MISC, MultiESC, EmoDynamiX, CauESC) or **one model per major step** (CAMI: separate state-tracker + topic-explorer). v7 explicitly types every call by role (`call_role`) and routes calls to one of three model tiers (MAIN / SMALL / SIM-or-JUDGE). Each tier has independent token caps, retry policies, and validators.

**Gap filled**: prior MI/ESC systems do not separate "small classification jobs" from "large generation jobs" on a per-call-role basis. v7's `llm_client.py` routing makes this explicit and overridable.

### 23.2 Two-track MI selection per turn (immediate need + longer-arc nudge)

Prior strategy-conditioned response generators (MISC, MultiESC, EmoDynamiX) pick **one** strategy per turn, conditioned on the user's emotion or recent dialogue. CAMI conditions on inferred state. None of them split the *immediate user-intent reply* from the *longer-arc TTM-stage nudge*.

**Gap filled**: v7's `mi_for_user_intent` (Agent 1, opening move) + `mi_for_system_intent` (Agent 3b, R2 nudge) is, to our knowledge, the first explicit decomposition of these two concerns inside a single supportive-conversation system. R2's permission to skip the nudge ("hold off if it conflicts with venting") is the structural rule that prevents the bot from interrogating someone mid-vent — a known failure mode of MI-trained bots.

### 23.3 Per-attribute summary + level + reasoning + confidence

The closest analog is CAMI's per-state belief tracker. CAMI does not record (a) a chronological narrative summary per attribute, (b) an explicit `level_reasoning` field, (c) an explicit `level_change_confidence`, or (d) an audit stack of supporting utterances. v7 records all four, with hard rules in Agent 3a's prompt for when a level may change.

**Gap filled**: behavior-change literature (Chalaguine et al. on HBM persuasion, the GPTCoach barrier-tactic framework) tracks beliefs but doesn't anchor every level change to a specific user utterance. v7 does — every level transition in the audit trail can be reconstructed from the source quote.

### 23.4 Typed cross-problem connections with attribute-pair specificity

Prior multi-problem systems (FeedbackESConv, the multi-problem extension proposals in EmoDynamiX) use cooccurrence-based or text-summary problem linking. They do not type the connection at the *attribute pair* level (e.g., "work_stress.triggers shared_trigger sleep_problems.triggers").

**Gap filled**: v7's `ConnectionEntryV7` is the first explicit per-edge structure that ties the connection to specific attributes on each side, with a typed `relation_type` and a supporting quote. This makes it possible to reason about *which* aspect of one problem connects to *which* aspect of another — important for the response generator when deciding whether to bring up an adjacent problem.

### 23.5 Conservative level-update rules with counter-evidence

Most belief-tracking dialogue systems update beliefs whenever new evidence arrives. v7's Agent 3a explicitly resists belief change: levels only move on unambiguous evidence OR ≥3-converging-instance patterns; counter-evidence (a healthy explanation for behavior that *could* look like a deficit) prevents the level from drifting toward the deficit reading.

**Gap filled**: avoids the "negative-belief drift" failure mode where every turn nudges the user toward more pathology because every observation is read in the worst light. This is a documented issue with LLM-based mental-health labellers.

### 23.6 Closed-MISC-vocabulary shortlist gating

EmoDynamiX uses a strategy classifier; it does not constrain to a closed MISC vocabulary. v7 constrains both MI picks (Agent 1 and Agent 3b) to fixed shortlists tied to (intent / TTM stage), with **post-parse validators that reject out-of-shortlist picks**. The MI-inconsistent codes (`direct`, `confront`, `warn`, the three `*_without_permission` codes) are not in any shortlist, so v7 cannot accidentally produce them.

**Gap filled**: the structural guarantee that the bot cannot violate MI integrity. Other systems achieve MI fidelity via training data; v7 achieves it via a small grammar in the prompt + a Python validator.

### 23.7 R1→R2→R3→R4 progressive rewrite + ESC-first contract

Prior response generators are either single-shot (most ESC chatbots) or use Self-Refine-style "draft + critique" pairs. v7's four-pass rewrite has explicit contracts at each step: R1 = empathic answer, R2 = integrate the nudge or skip if conflicting, R3 = decide whether to use evidence, R4 = match identity / persona / register.

**Gap filled**: the "use evidence, never echo" rule (in v6 and v7) plus the "POSITIVE solution evidence is what you SAY, PROBLEM evidence is what shapes your INTERPRETATION" distinction is, to our knowledge, novel. It addresses the failure mode where bots prove they remember by listing back the user's problems.

### 23.8 Per-field persona update with single-quote anchoring

Persona-grounded dialogue work (PAL, PersonaChat, persona-from-dialogue extraction) maintains per-field updates but does not anchor each update to a single transcript quote. v7's Agent P emits `(field, useful, updated_value, evidence_quote)` per field — every persona movement is verifiable.

**Gap filled**: persona drift is auditable, not silent.

### 23.9 Weighted-degree-centrality retrieval over the problem graph

Prior retrieval-conditioned dialogue systems use either text-similarity over a flat memory (MemoryBank, RAG-style) or a fixed top-S edge walk over a structured graph. v7 uses **weighted degree centrality restricted to the seed set with a relative threshold** — a candidate node's score is the sum of its edge weights to the current problems, and we keep candidates above τ × max_score.

**Gap filled**: avoids the failure mode of per-edge τ filters, where a problem mechanistically connected to two current problems by two weak-but-real edges falls below threshold on both. v7 surfaces it because the *aggregate* connection strength clears the bar. v8 (the dense-RAG sibling) is the experimental fork that asks "do we still need any of this if we have lossless RAG?" — see the v8 doc.

### 23.10 Cross-category synthesis (against the Related Works survey)

| Dimension | State of the Art | **HELP-E v7** |
|---|---|---|
| Problem tracking | Single problem per dialogue | Multiple concurrent, interacting problems with explicit STICKY MAIN |
| State representation | Flat text or emergent KG | Predefined schema with HBM (7+4 attrs) + TTM (4 stages) per problem |
| Strategy selection | Emotion-reactive, single-turn pick | Two-track MI: immediate intent + TTM-stage nudge, both shortlist-gated |
| Persona | Static or session-scoped | Dynamic 9-field, per-session updated, single-quote anchored |
| Memory | Text summaries or generic RAG | Structured problem graph with typed attribute connections + per-attribute audit stacks + per-edge running summary |
| Evaluation | Single-session, single-problem | Multi-session × multi-problem × triple-criterion (MITI / ESC / TTM transition) |
| Belief updates | Always update on new evidence | Conservative rules: unambiguous OR pattern-of-3 + counter-evidence override |
| MI fidelity | Trained-in via data | Structurally enforced via shortlists + post-parse validators + banned phrases |
| Routing | One model | Three-tier (MAIN / SMALL / SIM-or-JUDGE) per call_role |
| Retrieval | Top-S or per-edge τ | Weighted-degree centrality with relative threshold |

---

## 24. Limitations and Honest Caveats

### 24.1 Simulated users are not real users

The user simulator (Mind-1, gpt-oss-20b) generates believable text but is *aligned-by-construction* to the profile YAML. It does not have the genuine ambivalence, the mid-turn shifts, the "I don't know" pauses of real users. v7's evaluation is thus a *necessary but not sufficient* check.

### 24.2 LLM judges have biases — and now share a model family with the responder

MITI 4.2 and ESC are evaluated by `llama-3.3-70b` reading the transcript. Same-family judging (the responder is also llama-3.3-70b after the May-2026 model swap) creates a self-evaluation bias. We accept the risk for stability; future work re-runs judges on a different family (e.g., Claude or gpt-oss-120b once stable) to bound it.

### 24.3 Fix-1 is partial

The Fix-1 reorder shifted Agent 1's pick to `support` for venting turns, but the response prompt's R1 contract still describes "Empathic answer... reflects what's underneath" without distinguishing what `support` should sound like vs `complex_reflection`. The result is that responses *look like* paraphrase even when the chosen MI code is `support`. The deeper Fix-2 (broaden the venting bypass in R2 so high-distress turns drop the obligatory question) was deliberately deferred. See §20.

### 24.4 No mock-LLM smoke for v7

v6 has `eval/smoke_v6.py` — a fast mock-LLM smoke that catches schema breaks without burning real LLM budget. v7's supported test path is `eval/smoke_v7_v8.py`, which is a wet smoke against the real backends. Schema regressions can only be caught by actual runs.

### 24.5 The 4-stage TTM is a simplification

We dropped maintenance because a multi-session study can't observe it, but a real change-tracking system probably should observe early indicators of maintenance (e.g., self-reported persistence over weeks). v7 doesn't try.

### 24.6 The 22-problem vocabulary is not exhaustive

Crisis problems (suicidality, abuse, acute self-harm) are deliberately excluded from the vocabulary. v7 has no special handling if a simulator user introduces them — the inference call may pick the closest match (e.g., `general_anxiety`), which is wrong for that case. The April additions (`friendship_changes`, `role_loss`) closed the most common substitution failures observed in P05 logs but the vocabulary is still finite.

### 24.7 The current evaluation matrix is small

13 odd profiles × 3 sessions × ~70 turns ≈ 2700 turns total per system. For statistical significance comparing systems, this is on the small side. Larger evaluation requires either more profile diversity or more sessions per profile.

### 24.8 Carry-forward fallback can mask a broken Agent 2

The new `build_agent2_carry_forward_output` keeps the pipeline alive when Agent 2's retries are exhausted, but a long string of carry-forward turns means *no new attribute or connection evidence is being added to the graph*. Operators should grep the per-call jsonl logs for `agent2_inference_v7 exhausted` to spot this.

---

## 25. Glossary

| Term | Plain meaning |
|---|---|
| **Agent N** | One of the v7 specialized LLM calls (Agent 1, 2, 3a, 3b, 3c, 5, X, P). Each has its own prompt, schema, validator. The "name + (Agent N)" double-naming in the source — `IntentAgent (Agent 1)`, `EdgeSummaryAgent (Agent 3c)`, etc. — is intentional: the meaningful name describes the role, the legacy "Agent N" label preserves the index used in older docs and call_role strings. |
| **EdgeSummaryAgent / Agent 3c** | The SMALL-tier agent that maintains the per-edge running NL summary. Append-only — never rewrites earlier sentences. Falls back to a deterministic Python concat on LLM failure. |
| **edge.summary_text** | Per-edge chronological NL paragraph maintained by Agent 3c. This is the readable surface that flows into Agent 5's evidence_pack; the raw `connection_entries` are kept as the lossless audit trail. |
| **audit_stack** | Append-only list of every (turn, evidence span) discovery for an attribute. Never rewritten. |
| **call_role** | A string like `agent2_inference_v7` that names *which kind* of call this is. Drives tier routing, token caps, retry policy, and seed derivation. |
| **CallContext** | The bundle `(profile_id, session_id, system, turn_id, call_role)` used to derive the seed and route the call. |
| **carry-forward fallback** | The synthetic Agent-2 output (`build_agent2_carry_forward_output`) generated when `agent2_inference_v7` exhausts retries. Re-asserts the previous turn's main_problem and active problems with empty new entries; keeps the rest of the turn's pipeline running without injecting noise. |
| **complex_reflection** | A MISC code: paraphrase that goes one level beneath what the user said. Useful but, when overused, feels like restatement. |
| **connection_entry** | One typed link between an attribute on problem A and an attribute on problem B, with relation_type (causal / shared_trigger / reinforcing / …) + supporting quote. |
| **CoT** | Chain of thought — the model's free-text reasoning before producing the structured output. |
| **dual-tenant Lightning split** | The decision to route v7 and v8 small-tier calls through different Lightning sub-accounts so 429 backoff in one doesn't stall the other. |
| **ESC** | Emotional Support Conversation. Also the name of the 6-dimension judge (empathy, understanding, helpfulness, autonomy_respect, non_judgment, willingness_to_continue). |
| **evidence_pack** | The dict Agent 5 reads. Full main + 1-line others + cluster-fallback edges + WDC neighbor edges + persona + rolling_summary_5turns. |
| **HBM** | Health Belief Model. Tells us *what to track* (severity, susceptibility, benefits, barriers, self_efficacy, cues_to_action, motivation, plus 4 non-leveled). |
| **JSON schema** | The strict structure each agent's output must satisfy. Used by both the prompt (as a template) and the post-parse validator (as a check). |
| **leveled attribute** | One of 7 attributes that carry a `low | medium | high | unknown` level. The remaining 4 are non-leveled (free text only). |
| **MAIN tier** | The big-model tier (Agents 2, 5, P). llama-3.3-70b on Lightning in our current runs (was gpt-oss-120b before the May-2026 swap). |
| **main_problem** | The one problem this turn most centers on. Subject to STICKY MAIN PROBLEM. |
| **MI** | Motivational Interviewing. The counseling style. |
| **MISC** | Motivational Interviewing Skill Code. The 10-code vocabulary v7 picks from per turn. |
| **MITI 4.2** | The clinical rubric for MI quality. v7 evaluates with the 4 globals. |
| **mi_for_user_intent** | The MISC code Agent 1 picks for the *opening* of the reply (R1 in Agent 5). |
| **mi_for_system_intent** | The MISC code Agent 3b picks for the *longer-arc nudge* (R2 in Agent 5). May be skipped by Agent 5 if it conflicts with R1. |
| **persona** | The 9-field profile of the user. Updated by Agent P at session end. |
| **rolling_summary_5turns** | Agent X's running ~150-word narrative of the last few turns. Lives on the graph. |
| **shortlist** | The closed set of MISC codes Agent 1 / Agent 3b can pick from, conditioned on (intent / TTM stage). Out-of-shortlist picks are rejected by validators. |
| **SIM tier** | The simulator-side tier. gpt-oss-20b. Same model family as SMALL, separate env vars. |
| **SMALL tier** | The small-model tier (Agents 1, 3a, 3b, 3c, X). gpt-oss-20b. |
| **STICKY MAIN PROBLEM** | The rule that keeps `main_problem` stable unless a strong focus shift happens. |
| **system_intent** | The one-line nudge directive Agent 3b writes per problem. Stored on the problem node. Read by Agent 5 in R2. |
| **τ (tau)** | The relative threshold for WDC neighbor selection. A non-seed candidate is kept if its aggregate score ≥ τ × max(aggregate scores). Default 0.5 (`EDGE_THRESHOLD_TAU_V7`). |
| **TTM** | Transtheoretical Model. Tells us *which strategy is appropriate* (precontemplation → contemplation → preparation → action). |
| **turn_trace** | The full per-turn record (Agent 1, 2, 3a, 3b, 3c outputs + evidence_pack + Agent 5 output + diagnostics) saved into `session_<NN>.json`. |
| **useful flag** | Per-update or per-connection-entry signal: 1 = something genuinely new for this attribute / edge, 0 = redundant restatement. Used in diagnostics and in the response prompt's evidence rules. |
| **WDC retrieval** | Weighted-Degree-Centrality retrieval. The current default in `select_neighbors_by_weighted_degree`. |
| **wet smoke** | A small live LLM run (vs. mock LLM). Currently the only smoke path for v7 (`eval/smoke_v7_v8.py`). |

---

## End

This document covers v7 as of 2026-05-05 and reflects the May-2026 model swap (MAIN now llama-3.3-70b on Lightning), the weighted-degree-centrality retrieval that replaced the original per-edge τ filter, the carry-forward fallback for Agent 2 outages, and the 22-problem vocabulary (added `friendship_changes`, `role_loss`).
