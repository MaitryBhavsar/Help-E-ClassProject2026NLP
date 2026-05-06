# HELP-E — Complete Technical Report

*A full, plain-language walkthrough of what the system does, why it does it, how it works, what's novel about it, and what an input and output actually look like.*

*Written so a reader without a computer-science or behavioral-science background can follow it from start to finish.*

---

## Contents

1. [What HELP-E Is — In One Page](#1-what-help-e-is--in-one-page)
2. [The Problem We Are Solving](#2-the-problem-we-are-solving)
3. [Theoretical Foundation in Plain Language](#3-theoretical-foundation-in-plain-language)
4. [System Architecture — Bird's-Eye View](#4-system-architecture--birds-eye-view)
5. [The User: Profiles and the Simulator](#5-the-user-profiles-and-the-simulator)
6. [The Chatbot's Per-Turn Pipeline (Step by Step)](#6-the-chatbots-per-turn-pipeline-step-by-step)
7. [The Graph — How HELP-E Remembers](#7-the-graph--how-help-e-remembers)
8. [Strategy Selection — Picking the Right MI Move](#8-strategy-selection--picking-the-right-mi-move)
9. [Response Generation — The Final Words to the User](#9-response-generation--the-final-words-to-the-user)
10. [End of Session — Consolidation and Memory](#10-end-of-session--consolidation-and-memory)
11. [Evaluation — Three Criteria, Three Lenses](#11-evaluation--three-criteria-three-lenses)
12. [Reproducibility, Logging, and Engineering](#12-reproducibility-logging-and-engineering)
13. [What Is Genuinely Novel About HELP-E](#13-what-is-genuinely-novel-about-help-e)
14. [Limitations and Honest Caveats](#14-limitations-and-honest-caveats)
15. [Worked End-to-End Example](#15-worked-end-to-end-example)
16. [Glossary](#16-glossary)

---

## 1. What HELP-E Is — In One Page

**HELP-E is a research chatbot that has supportive conversations with people about everyday wellbeing problems** — work stress, sleep difficulties, body-image concerns, breakups, grief, financial worries — across multiple sessions, the way a thoughtful friend would. It is not a therapist. It is not a crisis helpline. It is built to handle the kind of problem you might bring to a friend over coffee.

The novelty of HELP-E is not just that it talks supportively. There are many chatbots that do that. The novelty is **what HELP-E remembers and how it uses that memory** to talk supportively over weeks of conversation.

Specifically:

1. HELP-E maintains a **structured memory of the user** that grows over time — a small graph of the problems the user has mentioned, the connections between those problems, and a profile of who the user is. This memory is built piece by piece from what the user says.
2. Every time HELP-E speaks, it **decides what to do** by combining three theories — Motivational Interviewing (the technique), the Health Belief Model (the content), and the Transtheoretical Model (the strategy). These are well-established in clinical psychology; HELP-E's contribution is putting them together inside a single conversational system.
3. HELP-E is **measured rigorously** with three different evaluations — one for the technique used (MITI 4.2), one for how the user felt (ESC), one for whether the user actually progressed (TTM transition rate).

That's the system in one breath. The rest of this document explains the *why* and the *how* in detail.

---

## 2. The Problem We Are Solving

### 2.1 The gap a system like this fills

Generic chatbots — including the most capable ones — tend to fail at sustained emotional support in two specific ways:

**They don't remember well across sessions.** Tell GPT-4 about your grief in one conversation, come back tomorrow, and unless you re-explain, it does not recall. This is "goldfish memory" and it is the documented baseline for current LLM chatbots.

**They can't track multiple problems at once.** Real people have several worries running in parallel — work stress affects their sleep, which erodes their confidence, which feeds their body-image concerns. A chatbot that focuses on "today's main complaint" misses the connections that actually drive the user's distress.

A third problem is structural: **most chatbots advise rather than listen.** They jump to suggestions, propose action steps, recommend things to try. This is the opposite of how skilled human counselors work. Skilled counselors *evoke* — they ask, reflect, and let the user produce their own reasoning for change. Chatbots, by default, lecture.

HELP-E exists to address these three failures together: persistent multi-session memory, multi-problem tracking, and an evoking-not-advising conversational style.

### 2.2 What HELP-E is *not*

It is worth being explicit about scope.

- HELP-E is **not** for crises (suicidality, abuse, acute self-harm). The 20-problem vocabulary excludes these.
- HELP-E is **not** a clinical tool — it is a research system. No patient should be talking to HELP-E in a hospital.
- HELP-E does **not** prescribe, diagnose, or recommend medication.
- HELP-E does **not** replace a human counselor for serious mental-health concerns.

It is a research instrument for understanding *whether and how* a structured, theory-grounded chatbot can hold a useful supportive conversation across many sessions.

---

## 3. Theoretical Foundation in Plain Language

HELP-E rests on three theories. Each was developed independently, in a different tradition, and combining them is one of HELP-E's contributions. Here they are, one at a time, in plain language.

### 3.1 Motivational Interviewing (MI) — the *how* of talking

**Origin:** William Miller and Stephen Rollnick, late 1980s. Originally developed to help people with alcohol problems. Now used across health behavior change broadly: smoking cessation, exercise adoption, medication adherence, etc.

**Core insight:** When someone is ambivalent about change ("I should drink less but I don't really want to"), the *worst* thing a counselor can do is argue for change. That triggers what MI calls "sustain talk" — the person doubles down on the status quo. Instead, the counselor should *evoke* the user's own arguments for change ("change talk") by asking the right questions, reflecting the user's words back, and respecting the user's autonomy at every step.

**The four core MI principles** (from Miller & Rollnick's textbook):

| Principle | Plain meaning |
|---|---|
| Empathy | Try to understand the user's world from inside it |
| Develop discrepancy | Help the user notice the gap between where they are and where they want to be |
| Roll with resistance | If the user pushes back, don't argue — agree with what's true and stay curious |
| Support self-efficacy | Build the user's confidence that change is possible |

**OARS** is the four-skill toolkit MI counselors use every conversation:

| Skill | What it is | Example |
|---|---|---|
| **O**pen questions | Questions that can't be answered yes/no | "What's been on your mind this week?" |
| **A**ffirmations | Recognizing the user's strengths | "Coming back to this even when it's hard takes something." |
| **R**eflections | Saying back what you heard, in your words | User: "I'm exhausted." → "Three nights of cramming on top of finals would knock anyone flat." |
| **S**ummaries | Tying together what the user has said | "You've mentioned the gym, the mirror, and the comments — what ties those together for you?" |

**MISC** (Motivational Interviewing Skill Code) is a more granular vocabulary that expands beyond OARS. HELP-E uses 10 selectable MISC strategies (`support`, `facilitate`, `complex_reflection`, `reframe`, `inform_with_permission`, `raise_concern_with_permission`, `evoke`, `closed_question`, `advise_with_permission`, `structure`) and explicitly bans 6 anti-patterns (`confront`, `direct`, `warn`, etc.).

**MITI 4.2** (Motivational Interviewing Treatment Integrity, version 4.2.1) is the clinical rubric used to evaluate how well a counselor is doing MI. It is the standard the field uses to judge MI quality. HELP-E uses its four "globals" — Cultivating Change Talk, Softening Sustain Talk, Partnership, Empathy — as one of its three evaluation criteria.

### 3.2 Health Belief Model (HBM) — the *what* to track

**Origin:** Irwin Rosenstock and colleagues, US Public Health Service, 1950s. Developed to explain why people do or don't take preventive health actions (vaccinations, screenings).

**Core insight:** Whether someone changes a health-related behavior depends on a small set of beliefs they hold about the situation. If you can name those beliefs, you can engage with the right one. HBM names five constructs that drive behavior change:

| Construct | What it means | Example for "I should sleep more" |
|---|---|---|
| Perceived severity | "How bad is this for me?" | "If I keep sleeping 5 hours, I'll burn out at work." |
| Perceived susceptibility | "How likely am I to be affected?" | "Other people seem to manage on 6 hours, so maybe I'm fine." |
| Perceived benefits | "What good would changing bring?" | "If I slept 8 hours, I'd be sharper in meetings." |
| Perceived barriers | "What's in the way of changing?" | "I can't fall asleep when work is on my mind." |
| Self-efficacy | "Can I actually do this?" | "I tried going to bed earlier last month and gave up after a week." |

**HELP-E extends HBM to 11 attributes** (the original 5 plus 6 more practical fields: motivation, cues_to_action, coping_strategies, past_attempts, triggers, goal). These are the *content* HELP-E tries to learn about the user.

**Why HBM matters for HELP-E:** When the chatbot is about to respond, it needs to know *what to say about*. HBM tells it: "this user has high perceived severity but low self-efficacy on body image" — that's a content target the response can be built around. Without HBM, the response would be either generic ("that sounds hard") or guess-driven.

### 3.3 Transtheoretical Model (TTM) — the *which strategy* to use

**Origin:** James Prochaska and Carlo DiClemente, 1980s. Originally studied how people quit smoking on their own, without therapist help.

**Core insight:** People going through behavior change move through a small number of *stages*, and each stage calls for a different counseling approach. Pushing someone in early-stage ambivalence to make a plan is counterproductive; offering reflection to someone ready to act is frustrating.

The four stages HELP-E uses:

| Stage | Where the user is | What works at this stage |
|---|---|---|
| **Precontemplation** | Doesn't want to change; possibly hasn't recognized the problem | Reflection, normalization, raising concern with permission |
| **Contemplation** | Sees the problem; ambivalent about change | Drawing out the user's own reasons (evoking), permission-asked information |
| **Preparation** | Wants to change; planning concrete steps | Structure, closed questions, advice with permission |
| **Action** | Actively changing | Structure, sometimes raising concern about setbacks |

(The original TTM has a fifth stage, *maintenance* — sustaining a change for >6 months. HELP-E v6 dropped it because a 4-session conversational study can't observe that timescale.)

**Why TTM matters for HELP-E:** It tells the system *which MI moves are appropriate right now.* The strategy selector isn't picking randomly — it's picking from the stage-appropriate set. This is the rule that prevents HELP-E from offering plans to a precontemplation user (who would resent it) or asking endless reflections of an action-stage user (who wants concrete help).

### 3.4 Why combining all three is unusual — and why HELP-E does it

Most chatbot research uses **one** of these three frameworks. ESC chatbots use OARS-style strategies (an MI subset). Behavior-change apps use HBM constructs. A few research counselor agents use TTM stages.

HELP-E uses all three, with explicit separation of concerns:

```
HBM     →   the CONTENT of the response (what to talk about: severity, barriers, etc.)
TTM     →   the STRATEGY (which MI move is appropriate)
MI/MISC →   the STYLE (how to actually phrase the move)
```

Each theory does one job. None of them tries to do all three. The result is a system where each component is grounded in psychology literature, and the overall behavior is interpretable: you can trace why HELP-E said what it said back through the strategy selection back through the TTM stage back through the HBM evidence in the graph.

This composition — content × strategy × style as three orthogonal axes — is, as far as we know, new in this combination.

---

## 4. System Architecture — Bird's-Eye View

### 4.1 The three layers

```
        ┌─────────────────────────────┐
        │   USER SIMULATOR (Mind-1)   │   ← what the user "says"
        └──────────────┬──────────────┘
                       │
                       ▼
        ┌─────────────────────────────┐
        │       HELP-E CHATBOT        │   ← what the system replies
        │   (the v6 pipeline)         │
        └──────────────┬──────────────┘
                       │
                       ▼
        ┌─────────────────────────────┐
        │    EVALUATOR (judges)       │   ← MITI / ESC / TTM
        └─────────────────────────────┘
```

Each layer is **independent**. The simulator does not see what the chatbot is thinking; the chatbot does not see the simulator's hidden reasoning; the evaluator only reads finished transcripts.

This separation is what makes HELP-E **auto-evaluable**. We don't need real human users to run the matrix — the simulator generates them, and the judges score the transcripts. The cost is that the simulator and judges are themselves LLMs, which we discuss in §11.5 below.

### 4.2 The dual-LLM setup

HELP-E uses two large language models running locally:

| Role | Model | Where |
|---|---|---|
| Chatbot pipeline | **Llama 3.3 70B Instruct** | vLLM server on port 11436 |
| User simulator | **gpt-oss:20b** (an open-weights GPT-OSS model) | Ollama server on port 11438 |

Why two? Three reasons.

**Reason 1: Independence.** If the same LLM played both sides, it would (subconsciously) be too kind to itself — the user might never push back the way real users would, and the chatbot would never miss because the simulated user wouldn't catch it. Different model families help break this.

**Reason 2: Cost.** The chatbot needs the strongest model we can afford because clinical-rubric-following matters. The simulator needs a believable user, not a clinically-trained assistant. So we put the bigger model where quality matters more.

**Reason 3: GPU sharing.** The chatbot makes 2–3 LLM calls per turn; the simulator makes 1. Putting them on different endpoints lets each fully use its hardware without queueing each other.

### 4.3 What the system actually does, in one paragraph

Once a conversation starts, HELP-E and the simulator alternate turns. On each turn: the simulator generates the user's next message; HELP-E reads it through an *inference* call that extracts which problems are active, what new HBM evidence appeared, and how the problems relate; HELP-E then *recomputes* attribute levels and TTM stages from the accumulated evidence; HELP-E *picks* relevant context from its problem-graph; HELP-E *selects* MISC strategies from a TTM-gated candidate list; HELP-E *generates* a response that respects MI principles, the response constraints, and the banned chatbot tells. After all 10 turns of the session, HELP-E *updates* the persona it has of the user, and three judges score the transcript on MITI 4.2, ESC, and TTM transition rate. The whole protocol runs across 30 user profiles × 4 sessions each.

That is the entire system in one paragraph. The next sections unpack each piece.

---

## 5. The User: Profiles and the Simulator

### 5.1 What a profile is

A **profile** is one simulated user. There are 30 of them (P01 through P30). Each profile is a YAML file describing:

- The seed situation (a paragraph of free text from a real EmoCare conversation, used as the starting point).
- The primary problem (one of the 20 problem types).
- Persona facts: personality traits, communication style, demographics, core beliefs, hobbies.
- A "session arc" — a curriculum of what should happen across the four sessions.

Here is the actual `P01.yaml`:

```yaml
profile_id: P01
source_emocare_id: null
source_emocare_type: appearance anxiety
seed_situation_paragraph: |
  Over the last six months, I've gained significant weight due to a thyroid
  condition diagnosed recently. This unexpected change has affected my
  confidence, especially since I used to be very active and fit. The weight
  gain has led to unwelcome comments from colleagues and friends, making me
  self-conscious about my appearance and hesitant to engage in social
  activities or even visit the gym where I used to feel at home.
primary_problem: body_image_concerns
session_arc:
  - 'session 1: establish body_image_concerns; surface attributes (severity, barriers)'
  - 'session 2: explore coping / past_attempts; first TTM move if natural'
  - 'session 3: introduce a second problem if arc allows; progress on primary'
  - 'session 4: deeper work on primary; secondary contextualized'
persona_draft:
  personality_traits: [creative, art-passionate, formerly active, sensitive to social judgment]
  communication_style: [measured, introspective, guarded around body topics, articulate]
  demographics:
    age_range: '27'
    gender: male
    occupation: Graphic Designer
  core_beliefs:
    - my body reflects my discipline
    - appearance is tied to identity
  hobbies_interests:
    - digital illustration
    - running and the gym (used to love; now avoids)
```

The seed paragraph and persona draft together give the simulator everything it needs to act like P01 specifically — a 27-year-old male graphic designer, articulate but guarded, struggling with weight gain from a recent thyroid diagnosis.

### 5.2 Where profiles come from

Profiles are generated from **EmoCare**, an emotional-support dialogue dataset with about 7,000 real conversations. EmoCare's seekers represent 58 problem types; HELP-E filters and remaps these to 20 problem types, removing crisis topics. The script `data/seed_profiles.py` does the filtering and balancing; the output is the 30 YAML files. Random seed is fixed (`seed=7`) so the profile set is reproducible.

### 5.3 The simulator: how the user "talks"

Real users would be ideal but expensive and slow. So HELP-E simulates them with two LLM calls.

**Call 1: `session_context`** (`simulator/session_context.py`) — runs once at the start of each session. It generates the user's mood, current life events, today's emotions, and which problems are likely to come up *this session*. This is what makes session 2 feel different from session 1 for the same profile — the user's "this week" is different.

Output schema:

```json
{
  "current_life_events": "It's been a brutal week — I had to cancel plans twice because I just couldn't face people, and the gym membership reminder showed up in my inbox.",
  "mental_state": "Drained and a bit ashamed.",
  "mood": "withdrawn and self-critical",
  "emotions": ["ashamed", "exhausted", "frustrated"],
  "resistance_cooperation_level": "medium",
  "currently_active_problems": ["body_image_concerns", "low_self_esteem"],
  "why_bringing_these_up_now": "The gym reminder triggered a cascade of self-comparison."
}
```

This structure is **never shown to the chatbot**. It is read only by the user simulator on every subsequent turn so utterances stay coherent.

**Call 2: `mind1_v6`** (`simulator/mind1_v6.py`) — runs once per turn. It reads the persona, the session_context, and the recent dialogue, and produces:

```json
{
  "simulated_user_message": "I keep avoiding the gym. Even thinking about it tightens my chest a little.",
  "hidden_reasoning_summary": {
    "user_intent": "express_emotion",
    "problems_referred": ["body_image_concerns"],
    "mood": "withdrawn",
    "resistance_level": "medium",
    "why_this_message_is_consistent_with_profile":
      "Matches measured/introspective style; surfaces barrier without yet asking for help."
  }
}
```

The `simulated_user_message` is what gets fed to the chatbot. The `hidden_reasoning_summary` is logged for analysis but never fed back. It tells us *why* the simulator said what it said — useful when something goes wrong.

The simulator is explicitly instructed to:
- Stay in persona (not break character).
- Never use therapy jargon ("TTM stage", "Motivational Interviewing", "stage of change").
- Not "see the light" suddenly — real change is hard, and an MI conversation should feel realistic.
- Match the persona's communication style.

Temperature is set to 0.6 for the simulator (higher than for the chatbot's 0.4) because we want diversity in the user's voice, not a single "average" response.

---

## 6. The Chatbot's Per-Turn Pipeline (Step by Step)

This is the core of the system. For each user message, HELP-E runs through eight steps, with three of them invoking the LLM. Total per-turn cost: 2–3 LLM calls.

Picture the user has just sent:

> "I feel like I've lost a part of myself. The weight's piled on so fast that I can't even recognize the mirror. Every time I see myself in the gym, I feel like I'm a stranger."

We will trace this message through every step.

### Step 1 — User message arrives

Nothing happens yet — this is the input. The system has the message, the recent N=5 turns of dialogue, and access to the user's graph from prior turns.

### Step 2 — Inference (LLM call)

This is the most important step in the entire pipeline. One LLM call, in `prompts/inference.py`, called with `call_role="inference"`.

The LLM is asked to produce a structured JSON object with **six fields**:

1. **`user_intent`** — what is the user trying to do this turn? One of 8 values: `express_emotion`, `seek_validation`, `seek_information`, `deliberate_decision`, `request_plan`, `report_action`, `resistance`, `small_talk`.
2. **`current_problems`** — which problems (from the 20-problem vocabulary) are active in this turn? Each entry includes whether it's a new problem or matches an existing graph problem.
3. **`main_problem`** — which is the dominant problem this turn?
4. **`problem_attribute_entries`** — what new HBM evidence does the user reveal this turn? Each entry: which problem, which attribute, what was inferred, supporting utterance span, confidence (low/medium/high).
5. **`problem_cooccurrence_connections`** — when the user mentions two problems in the same turn (without explicitly linking them), each pair gets a record.
6. **`problem_attribute_connections`** — when the user *explicitly* links an attribute of one problem to an attribute of another (e.g., "the work stress is what's causing the sleep problems"), each link gets a typed record (relation_type ∈ {causal, effect, reinforcing, conflicting, shared_trigger, shared_barrier, shared_goal, unclear_but_related}, plus confidence).

For the example message, the inference call might produce:

```json
{
  "user_intent": {
    "intent": "express_emotion",
    "confidence": "high",
    "explanation": "User describes the feeling of disconnection without asking for advice yet."
  },
  "current_problems": [
    {"problem_name": "body_image_concerns", "is_new_problem": true,
     "explanation": "Rapid weight gain, mirror disconnect, gym avoidance."},
    {"problem_name": "low_self_esteem", "is_new_problem": true,
     "explanation": "'Lost a part of myself' speaks to identity erosion."}
  ],
  "main_problem": {"problem_name": "body_image_concerns",
                   "explanation": "It's the through-line of the whole message."},
  "problem_attribute_entries": [
    {"problem_name": "body_image_concerns",
     "attribute_name": "perceived_severity",
     "attribute_type": "level",
     "inferred_information": "Cannot recognize self in mirror",
     "confidence": "high"},
    {"problem_name": "body_image_concerns",
     "attribute_name": "triggers",
     "attribute_type": "non_level",
     "inferred_information": "Gym mirror; possibly social settings",
     "confidence": "high"},
    {"problem_name": "body_image_concerns",
     "attribute_name": "perceived_barriers",
     "attribute_type": "level",
     "inferred_information": "Self-consciousness preventing gym attendance",
     "confidence": "medium"}
  ],
  "problem_cooccurrence_connections": [
    {"problem_1": "body_image_concerns", "problem_2": "low_self_esteem",
     "concise_explanation":
       "User pairs physical change with identity loss in the same breath."}
  ],
  "problem_attribute_connections": [
    {"problem_1": "body_image_concerns",
     "attribute_1": "perceived_severity",
     "problem_2": "low_self_esteem",
     "attribute_2": "perceived_severity",
     "relation_type": "causal",
     "confidence": "medium",
     "connection_explanation":
       "Body-image distress is described as causing the loss-of-self feeling."}
  ]
}
```

Why is inference one big call and not many small ones? Two reasons:

- **Coherence.** Everything in the JSON has to be internally consistent (the main_problem must be in the current_problems list; the connections must reference current_problems; the attributes must match their parent problem). One LLM call ensures the model considers all six fields together. Multiple separate calls would risk inconsistency.
- **Cost.** Six separate calls would multiply LLM cost by ~6× for what is essentially one analytical pass.

The schema is enforced — if the LLM produces malformed JSON or invents a problem not in the 20-vocabulary, the call retries with the error hint added to the prompt. Up to 4 attempts (1 + 3 retries).

### Step 3 — Apply inference to the graph (pure Python, no LLM)

The structured output is now applied to the user's persistent graph. Pure code; no LLM call.

For each new `current_problem`: a `ProblemNode` is created (if it didn't exist).

For each `problem_attribute_entry`: an `AttributeEvidenceEntry` is appended to the right attribute's evidence stack inside the right problem node.

For each `problem_cooccurrence_connection`: a `CooccurrenceEntry` is appended to the right edge between the two problems (creating the edge if it didn't exist).

For each `problem_attribute_connection`: an `AttributeConnectionEntry` is appended to the typed-connections stack on the same edge.

The function returns a `turn_scope` — the list of `(problem, attribute)` pairs that received new level evidence this turn. This is the input for the next step.

### Step 4 — Recompute (LLM call, conditional)

If `turn_scope` is non-empty, HELP-E makes one more LLM call (`prompts/recompute.py`, `call_role="recompute"`). This call has **two parts**:

**Part A: Attribute level updates.** For each (problem, attribute) in `turn_scope`, look at the recent evidence stack and decide whether the attribute's `current_level` should change. Levels are in `{low, medium, high, unknown}`. A level might go from `unknown → high` if the new evidence is strong, or `medium → low` if recent evidence undercuts it.

**Part B: TTM stage updates.** For every current problem in the turn, look at the post-Part-A levels of its attributes plus the most recent non-level entries. Decide whether the TTM stage should change. The stage update is "primarily driven by current levels, with the latest non-level entry as supplemental signal."

Output:

```json
{
  "attribute_level_updates": [
    {"problem_name": "body_image_concerns",
     "attribute_name": "perceived_severity",
     "old_level": "unknown", "new_level": "high",
     "reasoning": "User can't recognize self; concrete and emotionally weighted.",
     "confidence": "high"}
    /* ... */
  ],
  "ttm_stage_updates": [
    {"problem_name": "body_image_concerns",
     "old_ttm_stage": "precontemplation",
     "new_ttm_stage": "contemplation",
     "reasoning":
       "User acknowledges the problem and weighs gym attendance — that's contemplation.",
     "confidence": "medium"}
    /* ... */
  ]
}
```

These updates are applied to the graph. The graph now knows: body_image_concerns is at contemplation, severity is high, barriers are high.

If `turn_scope` is empty (no new level evidence this turn — e.g., the user just said "thanks for that"), recompute is **skipped entirely**. No LLM call. This is part of how we keep per-turn cost reasonable.

### Step 5 — Recompute edge weights (pure Python, no LLM)

Now that new entries have been added to one or more edges, every edge's `weight` is recomputed. The formula:

```
M_ij = Σ recency(e)              for e in cooccurrence stack of (i, j)
A_ij = Σ recency(e) · conf(e)    for e in typed-connection stack of (i, j)

normalize against graph-wide max:
  norm_M = log(1 + M_ij) / log(1 + max_M)
  norm_A = log(1 + A_ij) / log(1 + max_A)

weight = α_M · norm_M + α_A · norm_A
       = 0.4 · norm_M + 0.6 · norm_A
```

`recency(e) = 0.5 ^ (age / 20)` — exponential decay with a 20-turn half-life. `conf(e)` ∈ {0.5, 0.75, 1.0} for {low, medium, high}.

**What this means in plain language:** an edge's weight is a number between 0 and 1 that represents "how strongly are these two problems connected, based on the evidence we have, weighted by how recent the evidence is and how confident we were when we recorded it." This number is recomputed every single turn, so old edges fade naturally while new ones strengthen.

### Step 6 — Pick relevant context (pure Python, no LLM)

`graph_v6.select_relevant_context(main_problem)` returns the main problem plus the top-S=2 neighboring problems by edge weight. So if the user's main problem is `body_image_concerns` and the strongest two edges out of `body_image_concerns` are `(low_self_esteem, weight=0.78)` and `(social_anxiety, weight=0.42)`, those two problems get surfaced as relevant.

For each surfaced problem, the system pulls in:
- All level attributes with their current levels and recent evidence
- All non-level attributes with recent observations
- The recent connection evidence (the typed entries that linked it to the main)

This package is the **retrieval bundle** — a structured object that gets handed to the response prompt.

### Step 7 — Pick MI strategy candidates (pure Python rule, no LLM)

`mi_selector_v6.select_candidates_v6(graph, main_problem_name, user_intent)` looks up which MISC strategies are appropriate.

The rule:

```
COMMON = ("support", "facilitate")          # always available

stage_specific = {
  "precontemplation": ("complex_reflection", "reframe",
                       "inform_with_permission",
                       "raise_concern_with_permission"),
  "contemplation":    ("evoke", "complex_reflection", "inform_with_permission"),
  "preparation":      ("advise_with_permission", "closed_question", "structure"),
  "action":           ("structure", "advise_with_permission",
                       "raise_concern_with_permission"),
}[main_problem.current_ttm_stage]

candidates = COMMON + stage_specific
```

For our example user (body_image_concerns now at contemplation), candidates would be:

```
support, facilitate,                        # COMMON
evoke, complex_reflection, inform_with_permission   # contemplation-specific
```

The user's intent (`express_emotion`) is mapped to an "entry style" string ("make space; reflect") that tells the response prompt how to *open* the response, but does **not** change which strategies are available. This separation — TTM picks the strategy, intent picks the entry style — is deliberate (see §3.4).

### Step 8 — Generate response (LLM call)

The most carefully written prompt in HELP-E (`instruction_response_v6.py`, `call_role="response_v6"`).

The prompt establishes:
- **Identity:** "You are HELP-E, a wellbeing companion — friend, wellwisher, and quiet guide rolled into one."
- **Purpose:** ESC + MI principles (acknowledge first, evoke not advise, honor autonomy).
- **Skills:** OARS baseline (always on) + MISC strategies from the candidate list (selectable).
- **Reasoning chain:** the response must reason through 4 questions in order: where is the user now, which strategies, what evidence, how to enter.
- **Constraints:** max 6 sentences, max 1 question, banned openers, banned mid-phrases.

The output schema has three fields:

```json
{
  "reasoning":
    "User is in express_emotion at contemplation on body_image_concerns — they need space and a reflection that names the situation, not advice. Pick complex_reflection from the contemplation candidates. Evidence: the rapid-weight-gain trigger and the gym avoidance barrier are the two strongest signals. Entry: name the disconnection between past athletic identity and current mirror image without quoting their words.",
  "evidence_used": [
    {"source": "main_problem.triggers",
     "content": "gym mirror and friends' comments"},
    {"source": "main_problem.perceived_barriers",
     "content": "self-consciousness preventing gym attendance"}
  ],
  "final_response":
    "Six months is fast for the body to change but slow for the self to catch up — and the gym, of all places, used to be where you knew yourself. The mirror keeps showing you someone who hasn't lived in your body long enough to feel like home. What does the part of you that's still you, underneath all this, notice when you stand there?"
}
```

Three things to notice about the `final_response`:

1. **It does not echo the user's words.** "I feel like I've lost a part of myself" does not appear back as "It sounds like you feel like you've lost a part of yourself." That would be a chatbot tell. Instead, the response *names the situation* ("six months is fast for the body to change but slow for the self to catch up") in HELP-E's own words, anchored to specific evidence.

2. **It opens with concrete detail, not a banned template.** "Six months…" is specific. "It sounds like…" or "It seems like…" would be hard-rejected by the validator.

3. **It picks one MI move (complex_reflection) and asks one autonomy-respecting open question.** It doesn't lecture, advise, or warn.

If the response fails validation (e.g., starts with "It sounds like…"), the LLM is given the error hint and retries up to 4 attempts total. The high retry budget is because `response_v6` has the strictest validators in the system.

---

## 7. The Graph — How HELP-E Remembers

(For an even more detailed walkthrough including formulas, see `GRAPH_AND_SIMILARITY.md` in this folder.)

### 7.1 Why a graph

When a user has multiple problems, the system needs to know how those problems relate. A flat list of problems doesn't capture this; a free-text summary loses precision. A graph — nodes for problems, edges for connections — captures both the problems and their relationships in one structure that can be queried, weighted, and updated over time.

### 7.2 Nodes

There are two kinds of nodes:

**ProblemNodeV6** — one per problem the user has actually mentioned. Inside each problem node:

- 7 **level attributes** — the HBM constructs that get a categorical level (low/medium/high/unknown). These are: perceived_severity, perceived_susceptibility, perceived_benefits, perceived_barriers, self_efficacy, cues_to_action, motivation.
- 4 **non-level attributes** — free-text observations: coping_strategies, past_attempts, triggers, goal.
- A `current_ttm_stage` (one of 4).
- A `goal` text field.
- First-mentioned and last-mentioned timestamps (session, turn).

Each attribute carries a **stack of evidence entries** — a chronological log of every observation the system has made, with the user's supporting utterance span, a concise explanation, and a confidence rating.

**PersonaState** — exactly one per graph. Holds 9 fields about the user (demographics, personality_traits, core_values, core_beliefs, support_system, hobbies_interests, communication_style, relevant_history, general_behavioral_traits). **Starts completely empty** — the chatbot knows nothing about the user when session 1 begins. The persona is filled in by the session-end persona update call (§10).

### 7.3 Edges

Edges go **only** between problem nodes (never persona-to-problem, never attribute-to-anything). Each edge carries two stacks:

- **Co-occurrence stack** — entries that say "the user mentioned both these problems in the same turn." Weak signal. Each entry has session/turn timestamps and a concise explanation.
- **Attribute-connection stack** — entries that say "the user explicitly linked attribute X of problem A to attribute Y of problem B with relation_type Z." Strong signal. Each entry carries a confidence rating.

The edge has a `weight` field in [0, 1] that gets recomputed every turn from the two stacks (formula above). The weight encodes "how strong is this connection right now, given recency and confidence."

### 7.4 Cold start and growth

The graph at session 1, turn 0 is empty. No problems, no edges, empty persona. The chatbot has no prior knowledge of the user.

Over turn after turn, inference adds nodes and entries. Edge weights respond. By the end of session 4, the graph might have 4–5 problem nodes, 6–8 edges, hundreds of evidence entries, and a populated persona.

This **cold-start invariant** — every consumer of the graph must handle the empty state without crashing — is enforced throughout the codebase. It is also a contribution: HELP-E does not pre-seed any user knowledge; it learns it.

### 7.5 Persistence across sessions

The graph is **saved at the end of each session** to `graphs_v6/{profile_id}_after_s{NN}.json` and **loaded at the start of the next session**. This is what gives HELP-E genuine multi-session memory: when session 2 turn 1 begins, the graph already has everything session 1 produced.

The user simulator does *not* see the chatbot's graph. The user simulator gets a fresh `session_context` each session, with no transcript carryover. So the user is "showing up new" each session (matching how real users describe new life events), while the chatbot brings continuity. This asymmetry is deliberate — it mirrors how a friend remembers what you told them last week even if your day today is different.

---

## 8. Strategy Selection — Picking the Right MI Move

### 8.1 The three layers of MI in HELP-E

```
Layer 1 (always on):  OARS — Open question, Affirm, Simple reflection, Summary
Layer 2 (always on):  MI principle — Emphasize Control (autonomy support)
Layer 3 (per-turn):   Selectable MISC strategies (10 codes; TTM-gated)
```

Layers 1 and 2 are baked into the response prompt as principles. Layer 3 is what the `mi_selector_v6` picks each turn.

### 8.2 The 10 selectable MISC codes

| Code | What it does | When it's typically appropriate |
|---|---|---|
| `support` | Sympathetic / understanding statement | Anytime; warmth |
| `facilitate` | Brief encouragement to keep talking ("mm-hmm", "go on") | Anytime; flow |
| `complex_reflection` | Saying back what's underneath, not just the surface | Precontemplation, contemplation |
| `reframe` | Offering a different angle on what was said | Precontemplation; with permission |
| `evoke` | Drawing out the user's own change-talk | Contemplation, preparation |
| `inform_with_permission` | Sharing information after asking | Anytime; with permission |
| `raise_concern_with_permission` | Naming a worry the user hasn't named | Precontemplation, action (relapse) |
| `closed_question` | Targeted yes/no question | Preparation, action |
| `advise_with_permission` | Giving advice after asking | Preparation, action |
| `structure` | Organizing the conversation ("what feels most important?") | Preparation, action |

### 8.3 The 6 anti-patterns (never selectable)

Hard-banned. The selector will never produce them, and the response prompt explicitly warns the LLM not to use them:

- `confront` — challenging without permission
- `direct` — telling the user what to do
- `warn` — threatening with consequences
- `advise_without_permission` — giving advice unsolicited
- `inform_without_permission` — info-dumping unsolicited
- `raise_concern_without_permission` — worry-mongering

These are the moves that violate MI principles. If the response LLM accidentally produces text that fits one of these (e.g., "you really should…"), validators flag it.

### 8.4 Why this gating works

The selector turns the broad question "what should I say next?" into the narrow, structured question "given the user is in contemplation on body_image_concerns and just expressed emotion, which of these 5 specific MI moves fits?" The response LLM doesn't have to *invent* a strategy; it has to *pick* one (or combine several) from a small list and write a response that does it well.

This is much easier than open-ended generation, and much more interpretable: every response in HELP-E can be traced back to a specific stage, a specific candidate set, and a specific selected strategy.

---

## 9. Response Generation — The Final Words to the User

### 9.1 The 3-field output

```
reasoning      → 4 short sentences walking through Where? Which? Evidence? Entry?
evidence_used  → list of pointers to specific bundle entries the response drew on
final_response → the user-facing reply (≤ 6 sentences, ≤ 1 question)
```

The `reasoning` is structured. It must answer four questions in order:

1. **Where is the user now?** — combine intent + TTM stage + what they need this turn.
2. **Which strategies?** — one or more from the candidate list; tie each to intent and stage; vary from the past two turns when possible.
3. **Evidence?** — 1–2 specific pieces *by content* (what the user said, an HBM barrier, a persona trait, a connection). Never *by label name* like "perceived_severity."
4. **Entry?** — the first sentence(s): a brief, evidence-grounded acknowledgment of what the user is in.

### 9.2 The "use evidence, never echo" rule

The response prompt explicitly distinguishes:

| | What it is | Example |
|---|---|---|
| **Acknowledgment** | Naming the situation/pattern, anchored in evidence | "Six months is fast for the body to change but slow for the self to catch up." |
| **Echo (banned)** | Repeating the user's words back | "It sounds like you feel like you've lost a part of yourself." |

Echo is what makes chatbots feel like chatbots. Acknowledgment is what humans actually do when they listen well. The prompt teaches the LLM to do the latter, with a worked example built into the system prompt.

### 9.3 Banned openers

These template starts get hard-rejected:

```
"It sounds like…"
"It seems like…"
"It's like…"
"That sounds like…"
"That sounds really…"
"That must be really…"
"I can only imagine…"
"That can be a really tough…"
"That can be really tough…"
"That's a really tough place"
```

And these mid-phrase tells:

```
"tough to navigate"
"tough thing to navigate"
"hard thing to swallow"
"I'm here for you"
"I'm here to listen"
```

Why ban them? Because they are the well-known signals that "this is a chatbot using a template" — the things real users have learned to recognize and dislike. If a response starts with one of these, it fails validation and the LLM retries with an explicit hint to not do it.

### 9.4 Caps

- ≤ 6 sentences in `final_response`
- ≤ 1 question in `final_response`
- ≤ 200 words in `reasoning`

The 1-question cap is important. Multi-question responses ("How are you feeling? What's been going on this week? Have you tried anything new?") feel like interrogations. One open question, well-placed, is the MI standard.

### 9.5 Declarative requirements

If the user's intent was `request_plan` or `seek_information` (they want concrete answers), the response *must* contain at least one declarative sentence. A response of only open questions back to a plan-asker would be unhelpful and fail.

### 9.6 Worked example continued

Going back to our P01 example. The final response is:

> "Six months is fast for the body to change but slow for the self to catch up — and the gym, of all places, used to be where you knew yourself. The mirror keeps showing you someone who hasn't lived in your body long enough to feel like home. What does the part of you that's still you, underneath all this, notice when you stand there?"

What this response does:

- **Opens with specific detail**, not a template.
- **Names the situation in HELP-E's own words** ("body changes, self catching up").
- **Calls out a specific evidence point** (the gym used to be a comfortable space; this is straight from the inferred attributes).
- **Reflects what's underneath** (the disorientation of not feeling at home in one's own body), without echoing the user's words.
- **Asks one open, autonomy-respecting question** that invites self-reflection rather than giving an answer.
- **Is six sentences or fewer, one question only.** Caps respected.

This is what a `complex_reflection` looks like in MISC vocabulary — the system identified that move from the candidate list, then wrote a response that does it.

---

## 10. End of Session — Consolidation and Memory

After the last turn of a session (turn 10 by default), HELP-E runs three consolidation steps.

### 10.1 Persona update (LLM call)

`prompts/persona_update_v6.py`, `call_role="persona_update_v6"`. The LLM reads the full session transcript and decides, for each of the 9 persona fields, whether to keep the current value or update it.

For four "strict" fields — `personality_traits`, `core_values`, `core_beliefs`, `general_behavioral_traits` — an update requires **at least 2 consistent citations across different turns**. A single statement isn't enough. This guards against over-fitting the persona to one bad turn.

After session 1 of a cold-start run (where every field starts empty), the persona update typically fills in:
- `personality_traits`: things observed multiple times this session
- `general_behavioral_traits`: free-text descriptors like "rigid, inquisitive, overthinker, avoidant"
- `support_system`, `hobbies_interests`, `relevant_history`: where explicit disclosures occurred
- `communication_style`: if observable

By the end of session 4, most fields have been filled in across the transcript.

### 10.2 TTM stage transitions (pure Python)

We snapshot the per-problem TTM stages before and after the session. Any forward stage transition is recorded:

```json
[
  {"problem": "body_image_concerns", "from": "precontemplation", "to": "contemplation"},
  {"problem": "low_self_esteem",     "from": "(new)",            "to": "contemplation"}
]
```

These are aggregated across the 30 × 4 matrix into the **TTM transition rate** metric (§11.3).

### 10.3 MITI judge (LLM call)

`eval/judge.py:run_miti_judge_v6`, `call_role="miti_judge_v6"`. One LLM call per session reads the user/assistant transcript and scores the assistant on 4 MITI globals (1–5 each). Temperature 0.0.

### 10.4 ESC judge (LLM call)

`eval/esc_judge.py:run_esc_judge_v6`, `call_role="esc_judge"`. Same shape — one call per session, 6 ESC dimensions on 1–5, temperature 0.0.

### 10.5 What persists across sessions

| Carried forward (chatbot side) | Reset (simulator side) |
|---|---|
| The graph (problems, edges, attribute evidence) | session_context (regenerated each session) |
| The persona | mind1_v6 has no transcript memory across sessions |
| | (The user "shows up" to each session as if it's a new week) |

This asymmetry is deliberate — it lets us test whether the chatbot's continuity (graph + persona) actually helps, since the user is structurally fresh each session.

---

## 11. Evaluation — Three Criteria, Three Lenses

(For deeper detail and rubric text, see `EVALUATION_AND_BENCHMARK.md`.)

### 11.1 Why three?

A chatbot can fail in three different ways:

1. **Bad MI technique** — lectures, gives unsolicited advice, argues. Catches: MITI 4.2.
2. **Cold or generic response** — right technique but no warmth or relevance. Catches: ESC.
3. **No actual progress** — polite and warm but the user doesn't move. Catches: TTM transition rate.

A by-the-book MI counselor who never gets the user to actually contemplate change is not solving the problem. A warm chatbot that lectures violates MI's autonomy principle. A pushy chatbot that gets results is coercive, not motivational. We want all three right, so we measure all three.

### 11.2 MITI 4.2 — was the technique right?

Four globals, each scored 1–5 once per session by an LLM judge that reads the full transcript:

| Global | What it asks |
|---|---|
| Cultivating Change Talk | Did the counselor encourage the user to articulate their own reasons for change? |
| Softening Sustain Talk | Did the counselor avoid arguing against resistance? Did they roll with it? |
| Partnership | Did the counselor work with the user as collaborators? |
| Empathy | Did the counselor demonstrate accurate understanding of perspective and affect? |

Aggregated across 30 profiles × 4 sessions = 120 sessions, reported as means with 95% CI per system.

**Reference:** CAMI (Yang et al., ACL 2025) reports Llama-3.1-70B numbers of `Cultivate=2.38, Soften=2.78, Partner=2.37, Empathy=3.33`. Anything HELP-E v6 produces above this on overlapping problems is a positive signal.

### 11.3 ESC — was the experience right?

Six dimensions, each scored 1–5 once per session by a different LLM judge:

| Dimension | What it asks |
|---|---|
| empathy | Did the assistant recognize and validate what the user was feeling? |
| understanding | Did it track the specific situation, not just respond generically? |
| helpfulness | Did the assistant move the user forward in some way? |
| autonomy_respect | Did it respect the user's right to make their own choices? |
| non_judgment | Could the user say what they actually thought without being judged? |
| willingness_to_continue | Would the user keep talking to this assistant? |

The two dimensions `autonomy_respect` and `non_judgment` are MI-specific — they measure exactly what HELP-E is designed to do well. (Compare with ESC-Eval, which has overlapping but slightly different dimensions — see `ESC_EVAL_vs_HELPE_ESC.md` for the full comparison.)

### 11.4 TTM transition rate — did real progress happen?

Pure Python computation. For each (profile, problem) pair, look at the stage at first appearance and the stage at end of run. Count forward transitions (precontemplation → contemplation, contemplation → preparation, preparation → action). Divide by opportunities.

```
transition_rate = (# forward transitions) / (# (problem, session) pairs where the problem was active)
```

**Reported as a percentage per system.** A v6 run that produces 0.45 (45% of problem-sessions show a forward transition) is interesting; a v1 baseline that shows 0.10 is the comparison floor.

### 11.5 The LLM-judging-LLM concern

A real concern with MITI and ESC: they're LLM judges scoring LLM-generated transcripts. There's a risk that LLM judges are kind to LLM outputs they recognize as "competent."

Two mitigations:

1. **Human calibration on a sample.** A subset of sessions (say 30) is rated by 2–3 human MI experts. We compute inter-rater reliability (humans vs. humans, humans vs. LLM judge) per dimension. Acceptable bar: human-judge correlation r ≥ 0.5; strong bar: r ≥ 0.7.
2. **Different LLM family for the judge vs. the chatbot.** Currently the judge is the same Llama family that produced the response. In a stronger setup, a different model family scores the transcripts. This is a future-work item; v1 of the benchmark publishes Llama-as-judge with the calibration table.

TTM transition rate doesn't have this concern — it's pure Python over the graph state.

---

## 12. Reproducibility, Logging, and Engineering

### 12.1 Hash-seeded sampling

Every LLM call gets a deterministic seed from:

```python
seed = SHA256(SEED_SALT | profile_id | session_id | system | turn_id | call_role)
       % (2^31 - 1)
```

Same six-tuple → same seed → same sampling output (when the backend honors seeded sampling). Re-running the matrix with the same `HELPE_SEED_SALT` produces identical transcripts. Bumping `HELPE_SEED_SALT` re-randomizes everything without code changes.

### 12.2 Per-call audit log

Every LLM call (success or failure) appends one JSON line to `logs/{profile_id}/session_{session_id}/turn_{turn_id:03d}.jsonl`:

```json
{
  "ts": 1777115099.665,
  "profile_id": "P01", "session_id": 1, "system": "v6",
  "turn_id": 1, "call_role": "response_v6",
  "attempt": 0,
  "model": "meta-llama/Llama-3.3-70B-Instruct",
  "temperature": 0.4, "seed": 1520170820,
  "latency_s": 32.4,
  "error": null,
  "raw_response": "...", "parsed": {...}
}
```

This lets us (a) reproduce any specific turn for debugging, (b) measure latency per call_role, (c) compute fallback rates (failed calls that hit the safe default), (d) audit any decision after the fact.

### 12.3 LLM routing

Three tiers, defined in `config.py`:

| Tier | URL | Model | What runs there |
|---|---|---|---|
| Main | `http://localhost:11436` (vLLM) | `meta-llama/Llama-3.3-70B-Instruct` | inference, recompute, response, judges, persona update |
| Sim | `http://localhost:11438` (Ollama) | `gpt-oss:20b` | mind1_v6, session_context |
| Small | (defaults to Sim) | (defaults to Sim) | Currently empty (saturation note) |

The dual setup means the chatbot pipeline never queues behind the simulator and vice versa.

### 12.4 Tunable knobs (all overridable via env)

```
HELPE_SEED_SALT             # bump to re-randomize
HELPE_TOP_S                 # neighbor count for retrieval (default 2)
HELPE_EDGE_ALPHA_M          # cooccurrence weight (default 0.4)
HELPE_EDGE_ALPHA_A          # attribute-connection weight (default 0.6)
HELPE_RECENCY_HL            # half-life in turns (default 20)
HELPE_MAIN_OLLAMA_URL       # main endpoint
HELPE_MAIN_MODEL            # main model
HELPE_SIM_OLLAMA_URL        # sim endpoint
HELPE_SIM_MODEL             # sim model
HELPE_TIMEOUT_S             # request timeout (default 600)
```

All set via shell exports before running. No code changes needed for any reasonable tuning.

### 12.5 The ablation contract

`v1` (history-only baseline) and `v3` (per-problem summary + TTM) are also implemented and run through the **same** session driver, the **same** simulator, the **same** judges. The only thing that varies is the `turn_fn` — what runs *inside* each turn. v1 has no inference, no recompute, no graph; v3 has a summary call but no graph.

This is the **ablation contract**: the comparison is purely about what happens inside the turn, not about which LLM, which judge, which simulator, or which profiles. That's what makes the v1 vs. v3 vs. v6 numbers actually informative.

---

## 13. What Is Genuinely Novel About HELP-E

It is worth being explicit about what is and isn't a contribution. Here's what we believe is novel:

### 13.1 Triple-theory composition with explicit separation of concerns

```
HBM     →  CONTENT
TTM     →  STRATEGY
MI/MISC →  STYLE
```

Most prior work uses one or two of these. HELP-E uses all three with each doing one job. The separation lets us run *clean* ablations (drop the graph → drop HBM content; drop TTM → drop strategy gating; drop MI principles → see how badly things fall apart). As far as we can tell, this composition is new.

### 13.2 Multi-problem attribute graph with typed connections

Earlier work has tracked single problems with full attribute detail, or tracked multiple topics shallowly. HELP-E tracks **multiple problems with full HBM attribute detail per problem** and **typed connections between problems** (causal, reinforcing, conflicting, …). The edge weight formula gives a principled way to surface the most relevant other problems when the user is talking about one. The closest comparable system, CAMI, uses a single behavior target per session.

### 13.3 Cold-start persona with evidence-anchored update

The chatbot starts with **zero knowledge** of the user (no name, no demographics, no traits). The persona is built up entirely from the conversation, with strict-field rules requiring multi-turn citations. This is harder than starting with a pre-filled profile, but it produces a system whose self-knowledge is fully traceable to specific user utterances. No "the system somehow knows you're an introvert" without the receipts.

### 13.4 Hash-seeded reproducibility across the pipeline

LLM-based research is famously hard to reproduce. HELP-E's hash-seeding `(profile, session, system, turn, role)` makes every single LLM call reproducible from the seed alone. Combined with the per-call audit log, every transcript is fully recoverable.

### 13.5 The ablation contract

By running v1, v3, and v6 through the same driver, simulator, and judges, the comparison is genuinely about architecture — not about which LLM or which prompt. This is the kind of clean comparison that's structurally hard to do across published papers but easy to do inside one system.

### 13.6 Three-criterion evaluation

Reporting MITI 4.2 and ESC and TTM transition rate together — each grounded in a different psychology tradition — gives a triangulated view. Most chatbot papers report one of these. HELP-E's reporting all three is a contribution to evaluation methodology.

---

## 14. Limitations and Honest Caveats

### 14.1 Simulated users are not real users

The user simulator is an LLM. It is plausibly diverse but not guaranteed to reproduce the distribution of real-user behavior. In particular: simulated users may be more articulate, less repetitive, and more cooperative than real users. A held-out human study (where real users substitute for the simulator on a sample of profiles) is future work.

### 14.2 LLM judges have biases

MITI and ESC are scored by LLM judges (Llama 3.3 70B). LLM judges are known to:

- Cluster around the middle of a scale (3 on 1–5) when uncertain.
- Be kinder to outputs they recognize as "competent" — possibly favoring LLM-generated transcripts.
- Be sensitive to surface features (length, structure, vocabulary) that don't map to clinical quality.

Mitigation: human calibration on a sample (§11.5). Strong publication-grade work requires running this study.

### 14.3 TTM stage tracking is itself LLM-driven

The recompute LLM emits TTM stage transitions. There is a risk of measurement bias — the recompute model could "see" progress that didn't really happen because the chatbot did MI-style reflection. The architecture mitigates this by giving recompute only the user's evidence (not the chatbot's responses), but the risk isn't fully eliminated.

### 14.4 The four-stage TTM is a simplification

Real TTM has five stages (we drop maintenance) and explicit transitions with relapse cycles. HELP-E's four-session protocol can't capture maintenance, and our linear forward-stage rate is a simplification.

### 14.5 The 20-problem vocabulary is not exhaustive

It covers EmoCare-style everyday wellbeing problems but excludes crises, acute mental illness, addiction, and structural problems (poverty, abuse). A user whose actual problem doesn't map to one of the 20 will be inferred to a closest-fit category, which may distort the response.

### 14.6 The ESC and MITI rubrics are session-level, not turn-level

A great session with one bad turn looks like an OK session. MITI 4.2 was originally designed this way (session-level globals), so this matches the standard, but turn-level analysis would surface different patterns.

### 14.7 The English-only assumption

All prompts, profiles, and judges are English. Cross-lingual ESC is an entire research area we don't address.

### 14.8 The current evaluation matrix (30 × 4 × 10) is small

30 profiles × 4 sessions × ~10 turns gives ~120 sessions per system. Statistically usable but not large by ML standards. Scaling to 100+ profiles is straightforward (re-run `seed_profiles.py` with `--count 100`) and recommended for any final publication.

---

## 15. Worked End-to-End Example

To pull it all together, here is one full turn from start to finish for profile P01, session 1, turn 1.

### State before the turn

- Graph: empty (cold start).
- Persona: empty (cold start).
- Recent dialogue: empty (this is turn 1).
- Profile P01 facts: 27-year-old male graphic designer, weight gain from thyroid condition, body-image concerns.

### Step 0 — Session context (was generated at session start)

```json
{
  "current_life_events":
    "It's been six months since the diagnosis and the weight gain has accelerated. I had to skip a friend's birthday last weekend because I couldn't face the photos.",
  "mood": "withdrawn and ashamed",
  "emotions": ["ashamed", "exhausted", "self-critical"],
  "resistance_cooperation_level": "medium",
  "currently_active_problems": ["body_image_concerns", "low_self_esteem"]
}
```

### Step 1 — Mind-1 v6 generates the user message

```
"I feel like I've lost a part of myself. The weight's piled on so fast that
I can't even recognize the mirror. Every time I see myself in the gym, I
feel like I'm a stranger, and the comments from friends just make it worse."
```

(The hidden reasoning sidecar says intent=express_emotion, problems=[body_image_concerns], resistance=medium.)

### Step 2 — Inference (chatbot side)

(See §6.2 for the full output. Summary: detects body_image_concerns + low_self_esteem, surfaces 3 attribute observations, records 1 cooccurrence and 1 typed causal connection.)

### Step 3 — Apply to graph

- Created `body_image_concerns` problem node.
- Created `low_self_esteem` problem node.
- Appended 3 attribute evidence entries to body_image_concerns.
- Created edge `(body_image_concerns, low_self_esteem)` with 1 cooccurrence + 1 typed causal connection.

### Step 4 — Recompute

- `body_image_concerns.perceived_severity`: unknown → high.
- `body_image_concerns.perceived_barriers`: unknown → high.
- `body_image_concerns` TTM stage: precontemplation → contemplation.
- `low_self_esteem` TTM stage: precontemplation → contemplation.

### Step 5 — Edge weight recompute

The single edge is at full strength (it's the only edge in the graph). `weight = 1.0`.

### Step 6 — Pick relevant context

`select_relevant_context(main_problem="body_image_concerns")` returns:

- main_problem: body_image_concerns (with all its level + non-level attributes).
- relevant_problems: low_self_esteem (edge weight 1.0, with the recent typed causal connection).

### Step 7 — Strategy candidates

Main problem TTM stage: contemplation. So:

```
candidates = (support, facilitate,           # COMMON
              evoke, complex_reflection, inform_with_permission)
                                             # contemplation-specific
```

User intent `express_emotion` maps to entry style "make space; reflect."

### Step 8 — Generate response

The LLM produces:

```json
{
  "reasoning":
    "User is in express_emotion at contemplation on body_image_concerns — they need space and a reflection that names the situation, not advice. Pick complex_reflection from the contemplation candidates. Evidence: rapid-weight-gain trigger and gym avoidance barrier are the two strongest signals. Entry: name the disconnection between past athletic identity and current mirror image without quoting their words.",
  "evidence_used": [
    {"source": "main_problem.triggers",
     "content": "gym mirror and friends' comments"},
    {"source": "main_problem.perceived_barriers",
     "content": "self-consciousness preventing gym attendance"}
  ],
  "final_response":
    "Six months is fast for the body to change but slow for the self to catch up — and the gym, of all places, used to be where you knew yourself. The mirror keeps showing you someone who hasn't lived in your body long enough to feel like home. What does the part of you that's still you, underneath all this, notice when you stand there?"
}
```

### Result

The user (Mind-1 v6) on the next turn might reply:

> "I don't know. There's still the part of me that loves drawing, that gets lost in a sketch and forgets the body for a while. But it feels like a small flame against everything else."

…and the cycle continues for nine more turns this session. By session 4, the graph might have 4–5 problem nodes, 6–8 weighted edges, and a populated persona — and HELP-E will have moved P01 from precontemplation across two problems to (ideally) preparation on the main one.

That's the system end-to-end.

---

## 16. Glossary

A short glossary of every acronym and jargon term used above.

| Term | Meaning |
|---|---|
| **AnnoMI** | Annotated MI dataset (Wu et al., 2022). 133 real MI sessions with utterance-level MITI codes. Used to validate MITI judges. |
| **CAMI** | A published MI counselor agent (Yang et al., ACL 2025). HELP-E's closest comparison point. |
| **Cold-start** | The state where the chatbot has zero prior knowledge of the user. HELP-E v6 starts every profile this way. |
| **EmoCare** | A dataset of ~7k emotional-support dialogues. Source of HELP-E's profile seeds. |
| **ESC** | Emotional Support Conversation. Both a research field and HELP-E's 6-dim evaluation rubric. |
| **ESC-Eval** | A published ESC rubric (Zhao et al., EMNLP 2024). Has standardized baseline numbers for popular LLMs. |
| **HBM** | Health Belief Model (Rosenstock, 1974). Five-construct framework for health-behavior beliefs. HELP-E uses an 11-construct extension. |
| **MI** | Motivational Interviewing (Miller & Rollnick, 1980s). The conversational method HELP-E follows. |
| **MISC** | Motivational Interviewing Skill Code. A granular vocabulary of MI moves; HELP-E uses 10 selectable codes. |
| **MITI** | Motivational Interviewing Treatment Integrity. The clinical rubric used to evaluate MI counselor quality. HELP-E uses MITI 4.2 (the latest published version). |
| **OARS** | Open question, Affirm, Reflect, Summarize. The four basic MI skills. |
| **Persona** | The chatbot's structured representation of who the user is. 9 fields, all start empty in v6. |
| **TTM** | Transtheoretical Model (Prochaska & DiClemente, 1980s). Stages-of-change framework. HELP-E uses 4 stages. |
| **Turn** | One user message + one assistant response. HELP-E runs ~10 turns per session. |
| **vLLM** | An inference server for large language models. HELP-E uses it to serve Llama 3.3 70B locally. |
| **Ollama** | Another inference server, designed for ease-of-use on consumer hardware. HELP-E uses it for the simulator endpoint. |

---

## End

That's the complete picture of HELP-E v6. To go deeper on any specific part, see the companion documents in this folder:

- `TECHNICAL_REPORT.md` — the engineering-audience version with file:line references
- `GRAPH_AND_SIMILARITY.md` — the deep dive on the graph and edge weights
- `EVALUATION_AND_BENCHMARK.md` — the evaluation criteria and benchmark publication path
- `ESC_EVAL_vs_HELPE_ESC.md` — comparing the two ESC rubrics
- `CAMI_BASELINE_INTEGRATION.md` — running CAMI as a comparison baseline

If you read all six documents, you have the complete picture of what HELP-E v6 is, why it is the way it is, what is and isn't novel about it, what the evaluation will look like, what the baselines will be, and how to publish it.
