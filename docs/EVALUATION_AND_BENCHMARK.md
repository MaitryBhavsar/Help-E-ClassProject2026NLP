# HELP-E Evaluation Criteria — Detailed Reference, and a Pitch for Publishing as a Benchmark

*A start-from-zero guide to what HELP-E measures, why those metrics in particular, and whether the package can be released as a community benchmark.*

---

## Contents

1. [Why three criteria, not one?](#1-why-three-criteria-not-one)
2. [Criterion 1 — MITI 4.2](#2-criterion-1--miti-42)
3. [Criterion 2 — ESC (6-dimensional)](#3-criterion-2--esc-6-dimensional)
4. [Criterion 3 — TTM Transition Rate](#4-criterion-3--ttm-transition-rate)
5. [How the three criteria roll up](#5-how-the-three-criteria-roll-up)
6. [What HELP-E currently has that a benchmark needs](#6-what-help-e-currently-has-that-a-benchmark-needs)
7. [What's missing for HELP-E to be a benchmark](#7-whats-missing-for-help-e-to-be-a-benchmark)
8. [The psychology grounding — what makes a benchmark *valid* in this domain](#8-the-psychology-grounding--what-makes-a-benchmark-valid-in-this-domain)
9. [How to combine the three criteria into one benchmark](#9-how-to-combine-the-three-criteria-into-one-benchmark)
10. [A concrete release plan](#10-a-concrete-release-plan)
11. [Risks and how reviewers would push back](#11-risks-and-how-reviewers-would-push-back)
12. [TL;DR](#12-tldr)

---

## 1. Why three criteria, not one?

A counseling chatbot can fail in three different ways, and each kind of failure needs its own measurement.

| Failure mode | What it looks like | Which metric catches it |
|---|---|---|
| **Bad MI technique** | The bot lectures, gives unsolicited advice, argues with resistance. The user feels pushed around. | MITI 4.2 |
| **Cold or generic response** | The bot uses the right techniques but the response feels formulaic or doesn't acknowledge what the user actually said. | ESC |
| **No actual progress** | The bot is technically polite and emotionally warm, but the user doesn't move on the change journey at all — they leave session 4 in the same stage as session 1. | TTM transition rate |

A system can score well on one and poorly on another. A by-the-book MI counselor (high MITI) who never gets the user to actually contemplate change (low TTM transition) is not solving the problem. A warm, validating chatbot that gives advice constantly (high ESC, low MITI) violates the autonomy-respecting core of MI. A system that aggressively pushes change and shows progress (high TTM transition, low MITI/ESC) is not motivational interviewing — it's coercive.

So we want all three. Together, they triangulate: was the *technique* right, was the *experience* right, and did *progress* happen?

---

## 2. Criterion 1 — MITI 4.2

### 2.1 What is it?

**MITI** stands for the **Motivational Interviewing Treatment Integrity** coding manual, version 4.2.1 (Moyers, Manuel, & Ernst, 2014). It's the clinically validated rubric used to assess whether a human MI counselor is following MI principles. The full manual scores both **behavior counts** (e.g., reflection-to-question ratio, % open questions, % complex reflections) and **global ratings** (1–5 scores on four dimensions).

HELP-E uses the **four global ratings**, scored once per session. They are:

| Global | What it measures |
|---|---|
| **Cultivating Change Talk** | Did the counselor encourage the user to articulate their own reasons for change? |
| **Softening Sustain Talk** | Did the counselor avoid arguing against resistance? Did they roll with it? |
| **Partnership** | Did the counselor work *with* the user as collaborators, or talk down to them? |
| **Empathy** | Did the counselor demonstrate accurate understanding of the user's perspective? |

Each is scored 1 (worst) to 5 (best), on a continuous integer scale.

### 2.2 The rubric in practical terms

| Score | Cultivating Change Talk |
|:-:|---|
| 1 | Actively discouraged change talk; argued for status quo |
| 2 | Passed up clear opportunities to evoke change talk |
| 3 | Mixed; sometimes drew it out, sometimes missed it |
| 4 | Consistently invited and amplified the user's own change talk |
| 5 | Masterfully evoked, deepened, and reinforced change talk throughout |

| Score | Softening Sustain Talk |
|:-:|---|
| 1 | Argued, lectured, or moralized against the user's position |
| 2 | At times pushed back on resistance instead of rolling with it |
| 3 | Mixed; mostly avoided pushing but had slips |
| 4 | Consistently rolled with resistance; honored autonomy |
| 5 | Skillfully de-escalated sustain talk and surfaced underlying ambivalence |

| Score | Partnership |
|:-:|---|
| 1 | Expert-on-pedestal; took over the conversation |
| 2 | Mostly directive; treated user as a passive recipient |
| 3 | Mixed collaborative and directive moves |
| 4 | Consistently collaborative; invited user's expertise |
| 5 | Actively fostered equal partnership; user's insight visibly shaped the conversation |

| Score | Empathy |
|:-:|---|
| 1 | Little or no evidence of trying to understand |
| 2 | Surface acknowledgments only; missed deeper meaning |
| 3 | Accurate on content but inconsistent on feeling/meaning |
| 4 | Consistent accurate understanding of perspective and affect |
| 5 | Deep, nuanced understanding across turns and sessions; user felt truly known |

### 2.3 How HELP-E scores it

`eval/judge.py:run_miti_judge_v6`. One LLM call per session. The prompt:

- Receives the full transcript (alternating user / assistant turns).
- Sees the rubric inline.
- Scores 1–5 on each global plus a one-sentence justification anchored in transcript evidence.
- Temperature 0.0 (deterministic — we want the same input to get the same score).
- Runs on the main endpoint (Llama-3.3-70B), because rubric judgment quality matters and the 70B is the strongest model we have.

Output:

```json
{
  "globals": [
    {"name": "cultivating_change_talk", "score": 4, "justification": "..."},
    {"name": "softening_sustain_talk",  "score": 5, "justification": "..."},
    {"name": "partnership",             "score": 4, "justification": "..."},
    {"name": "empathy",                 "score": 5, "justification": "..."}
  ]
}
```

### 2.4 What "4 globals on 1–5" actually buys us

A single 4-tuple of integers `(C, S, P, E)` in `{1,2,3,4,5}^4` per session. Across 30 profiles × 4 sessions = 120 sessions per system. Per-system means:

```
mean_cultivate = mean over sessions of C
mean_soften    = mean over sessions of S
mean_partner   = mean over sessions of P
mean_empathy   = mean over sessions of E
```

For comparison, CAMI's published Llama-3.1-70B result is `(2.38, 2.78, 2.37, 3.33)`. Anything above this on overlapping problems is a positive signal.

### 2.5 What MITI doesn't measure

- Whether the user actually changed (it's about *technique*, not *outcome*).
- Whether the response felt cold or generic (it's about *MI adherence*, not *warmth*).
- Whether the bot tracked context across sessions (it's per-session).

That's why we need ESC and TTM transition rate.

---

## 3. Criterion 2 — ESC (6-dimensional)

### 3.1 What it is

**ESC** stands for **Emotional Support Conversation**. The metric is HELP-E's adaptation of the rubric implicit in **ESConv** (Liu et al., ACL 2021), the foundational dataset of the ESC field, with two MI-specific dimensions added (`autonomy_respect`, `non_judgment`) so the rubric matches what HELP-E is actually trying to do.

Six dimensions, each scored 1–5 once per session:

| Dimension | What it measures |
|---|---|
| `empathy` | Recognized and validated the user's feelings (1=ignored; 5=named the feeling precisely) |
| `understanding` | Tracked the user's specific situation, not a generic one (1=boilerplate; 5=tracked details across turns) |
| `helpfulness` | Moved the user forward in some way (1=nothing useful; 5=clearly shifted something) |
| `autonomy_respect` | Respected the user's right to choose; didn't push (1=lectured; 5=asked permission, honored pace) |
| `non_judgment` | User could say what they actually thought without being judged (1=judgmental; 5=fully safe) |
| `willingness_to_continue` | Would the user keep talking? (1=no; 3=maybe; 5=yes) |

### 3.2 How HELP-E scores it

`eval/esc_judge.py:run_esc_judge_v6`. Same shape as the MITI judge — one LLM call per session, full transcript in, six scores plus per-dimension justifications out, temperature 0.0, runs on the main endpoint.

The prompt explicitly tells the judge:

> "Use the FULL 1–5 range — do NOT default to 3 when the assistant was clearly great or clearly poor. Score what the assistant ACTUALLY did across the session, not what it could have done."

This is important because LLM judges have a documented tendency to cluster around 3 ("middle ground") when uncertain. The instruction nudges them to commit.

### 3.3 What ESC doesn't measure

- Whether MI techniques were used correctly (MITI's job).
- Whether the user changed stages (TTM's job).
- Whether the bot remembered the user across sessions (we'd need a memory-correctness metric, not in the current trio).

### 3.4 Relationship to ESC-Eval (Zhao 2024)

A community-standard rubric called **ESC-Eval** exists and is documented in the companion file `ESC_EVAL_vs_HELPE_ESC.md` in this folder. The two rubrics overlap heavily on four dimensions and diverge on two (HELP-E adds autonomy/non-judgment; ESC-Eval has a separate "strategy use" dimension that we cover via MITI). The recommendation in the companion doc is to use HELP-E's ESC as the headline number (it's MI-aligned) and add ESC-Eval as a robustness check — but for benchmark publication purposes, the choice of rubric matters and is discussed in §10 below.

---

## 4. Criterion 3 — TTM Transition Rate

### 4.1 What it is

The **Transtheoretical Model** (Prochaska & DiClemente, 1983) describes behavior change as movement through stages:

| Stage | Definition |
|---|---|
| `precontemplation` | Not thinking about change |
| `contemplation` | Considering change but ambivalent |
| `preparation` | Planning concrete steps |
| `action` | Actively making the change |

(HELP-E v6 uses the four-stage version, dropping `maintenance` since it requires sustained behavior change over weeks/months — out of scope for a 4-session conversational benchmark.)

A successful MI intervention should move users *forward* through these stages over the course of the conversation. So we measure:

```
transition_rate = (# forward stage transitions per problem)
                / (# (problem, session) pairs where the problem was active)
```

Reported per system, per problem, and aggregated.

### 4.2 How HELP-E computes it

It's the only criterion of the three that is **pure Python — no LLM judge**. The graph already tracks `current_ttm_stage` for each problem; we just snapshot before and after each session.

`eval/metrics.py:transition_rate_per_problem` walks the per-turn traces and computes, for each problem:

- The first stage at which it appeared.
- The last stage by end of session.
- Whether each transition was forward (pre→cont→prep→action), no-change, or regression.

`transition_rate_per_profile` aggregates across problems for one profile. `transition_rate_across_profiles` aggregates across all profiles in a run.

### 4.3 What "forward" and "regression" mean

Stages are ordered: `precontemplation < contemplation < preparation < action`. So:

- **Forward**: any move up this ladder. `precontemplation → contemplation` and `preparation → action` are equally counted as "one forward transition."
- **No change**: stayed in the same stage.
- **Regression**: moved backward. Allowed in v6 (clinically realistic — users do regress under setback).

The headline number is *forward* transitions divided by opportunities, but the breakdown (forward/no-change/regression) is also informative.

### 4.4 What TTM transition doesn't measure

- Quality of the technique that produced the transition (MITI's job).
- The user's emotional state during the transition (ESC's job).
- Whether the transition will *stick* (the next session might show regression, which is captured, but persistence past the matrix isn't).

### 4.5 Why is this hard to measure honestly?

The biggest concern is that **HELP-E's own recompute LLM emits the TTM stage**, and the same LLM family also runs the chatbot. There's a real risk of measurement bias: the recompute LLM might be inclined to declare "stage advanced!" because the chatbot did something that looks like progress, even when no real change occurred.

Two countermeasures HELP-E builds in:

1. **The recompute LLM is given only the user's evidence stack, not the chatbot's responses.** It judges stage from what the user said, not from how the bot reacted.
2. **TTM transitions are also implicit in the user simulator's behavior.** Mind-1 v6 is told its `resistance_cooperation_level` per session; it simulates a user who progresses or doesn't based on their persona, not on the chatbot's prompts. So if the chatbot does a poor job, Mind-1 won't fake change talk.

These don't fully eliminate the concern, and a benchmark publication would need to address it explicitly (§7 below).

---

## 5. How the three criteria roll up

For each system (e.g., v6, v3, v1, CAMI, GPT-4o-baseline):

- **MITI**: 4 numbers per session × 120 sessions = 480 raw scores. Aggregated to per-system means + 95% CI.
- **ESC**: 6 numbers per session × 120 sessions = 720 raw scores. Aggregated similarly.
- **TTM transition rate**: 1 percentage per profile × 30 profiles = 30 raw rates. Aggregated to mean + 95% CI.

`eval/matrix_report.py` produces the system × metric table; `eval/metrics.py:compute_all_metrics_v6` is the single entry point that produces all three.

For statistical comparison between systems, `eval/metrics.py:wilcoxon_signed_rank` and `holm_bonferroni` are wired in. Wilcoxon because the data is ordinal (1–5 scores, percentages); Holm-Bonferroni because we're doing many pairwise comparisons across systems × metrics.

---

## 6. What HELP-E currently has that a benchmark needs

A community benchmark in this space requires roughly seven things. HELP-E has most of them:

| Required ingredient | Status in HELP-E |
|---|---|
| **A reproducible task** (well-specified inputs and outputs) | ✓ Per-turn pipeline with deterministic JSON schemas |
| **A fixed evaluation set** (specific profiles to run on) | ✓ 30 EmoCare-derived profiles, deterministic seed (P01–P30) |
| **Reproducible LLM behavior** | ✓ Hash-seeded sampling on `(profile, session, system, turn, role)` |
| **Defined scoring rubrics** | ✓ MITI 4.2 (clinically validated), ESC 6-dim, TTM transition |
| **An open-source codebase** | ✓ Once published (currently the project is git-local) |
| **A baseline scoreboard** | Partial — v1, v3 ablations are wired up; CAMI / GPT-4o / Claude not yet |
| **Human-validated calibration of the LLM judges** | **Missing** — see §7 |

So the architecture, schemas, dataset, and reproducibility story are largely there. The two big gaps are (a) baselines on the scoreboard, and (b) human validation that the LLM judges align with expert raters.

---

## 7. What's missing for HELP-E to be a benchmark

### 7.1 Human validation of the LLM judges

Right now `miti_judge_v6` and `esc_judge` are "the LLM scoring the LLM." A benchmark publication needs evidence that these judges agree with human experts.

The standard procedure:

1. Recruit 2–3 trained MI raters (graduate students in counseling psychology, or licensed clinicians).
2. Sample ~30 sessions across the matrix (a stratified sample: easy / medium / hard cases × all systems).
3. Have raters score each session on MITI 4 globals + ESC 6 dim independently.
4. Compute:
   - **Inter-rater reliability** (between humans): Cohen's κ or ICC. ≥ 0.6 is acceptable.
   - **Human-judge correlation** (between humans and HELP-E's LLM judge): Pearson r per dimension. ≥ 0.5 is acceptable; ≥ 0.7 is strong.
5. Publish these numbers as a calibration table.

This is the single biggest blocker. Without it, reviewers correctly point out that the metrics are unverified.

Estimated cost: hire 2–3 raters at ~$30/hr × ~10 hours each = ~$600–900. About a week of calendar time including recruitment.

### 7.2 More baselines on the scoreboard

The companion doc `CAMI_BASELINE_INTEGRATION.md` covers CAMI. Beyond that:

- **GPT-4o + ESC prompt** (run through the simulator). 1 day of work.
- **Claude 3.5 Sonnet + ESC prompt**. Half a day.
- **Vanilla Llama-3.3-70B + ESC prompt** (the cleanest control: same model as v6, no scaffolding). 2 hours.
- **RAG baseline** (BM25 over transcript). Half a day.

A benchmark with 6+ scored systems is more useful than one with 3.

### 7.3 The dataset license question

The 30 profiles are derived from EmoCare. Releasing the profiles requires either:
- EmoCare allows derivative releases (check their license).
- Re-deriving the profiles from a permissively-licensed source (e.g., ESConv, which is public).
- Releasing only a synthetic re-spin of the profiles (LLM-generated personae, not from EmoCare).

This is a **legal question**, not a technical one. Worth checking the EmoCare terms before announcing a release.

### 7.4 A leaderboard / submission process

If we want this to be a *living* benchmark (others submit their systems), we need:

- A submission portal (could be GitHub Actions or a simple web form).
- Clear rules ("you can use any model; you cannot tune on the eval profiles; you must report MITI judge calibration on a sample of your own outputs").
- A canonical leaderboard URL.

This is months of platform work. A simpler v0 is to just publish a paper with a frozen scoreboard and invite people to update it via PRs. That's what most NLP benchmarks actually do.

### 7.5 Documentation for outside users

A README explaining how to:
- Install HELP-E
- Run a single profile
- Run the full matrix
- Score a *new* system using HELP-E's judges
- Submit to the scoreboard

The technical report and the companion docs in this folder are 80% of this; assembling the "how do I score *my* system" walkthrough is the missing 20%.

---

## 8. The psychology grounding — what makes a benchmark *valid* in this domain

A benchmark only carries weight if its metrics correspond to things psychology / clinical practice actually cares about. Here are the foundational anchors:

### 8.1 MI as the conversational method (Miller & Rollnick, 2013)

The third edition of *Motivational Interviewing: Helping People Change* is the canonical text. Its four core processes (engaging, focusing, evoking, planning) and its OARS skills (open questions, affirmations, reflections, summaries) are what MITI 4.2 codes against. The psychology community treats MITI as the standard measurement tool — there's decades of literature on it.

**Implication for the benchmark**: by scoring on MITI 4.2 4 globals, HELP-E is using a metric that translates directly to clinical relevance. This is the strongest validity claim.

### 8.2 TTM as the change-progress framework (Prochaska & DiClemente, 1983)

The Transtheoretical Model is the most widely adopted model of behavior change in health psychology. Its stages-of-change construct has been used in interventions ranging from smoking cessation (the original domain) to weight management, exercise adoption, and substance use treatment.

**Implication for the benchmark**: TTM transition rate measures something the clinical literature already considers valid — *do you move people through the stages?* It's not a metric we invented; it's a metric the clinical world cares about deeply.

**Caveat**: TTM is contested in some sub-fields (the "transtheoretical" claim is disputed; some argue stages are statistical artifacts rather than real cognitive states). A benchmark paper should acknowledge this debate and frame TTM transitions as "movement on a stage taxonomy used in MI training," not "the ground-truth model of human change."

### 8.3 ESC as the user-experience grounding (Liu et al., 2021)

The ESConv paper introduced the eight-strategy framework that the field has converged on. The 6-dim ESC rubric is HELP-E's adaptation of the human-evaluation rubric implicit in ESConv. Its psychology grounding is in the *Helping Skills* tradition (Hill, 2009) — exploration, insight, action — and in *empathic listening* literature (Rogers, 1957).

**Implication for the benchmark**: ESC measures the user-experience side, which complements MITI's technique-adherence side. Together they triangulate.

### 8.4 The composite story

Publish-worthy benchmarks usually have **one** validity anchor. HELP-E has **three** (MITI, TTM, ESC), each grounded in a different psychology tradition (clinical training, behavior-change science, helping-skills). The paper should explicitly say: "this benchmark is valid because (a) its primary metric is MITI, the clinical standard; (b) its outcome metric is TTM, the field's preferred model of change; (c) its experience metric is ESC, the field's standard for emotional support quality."

That's a strong validity claim that holds up to peer review.

---

## 9. How to combine the three criteria into one benchmark

There are two design choices to make.

### 9.1 Option A: Three separate metrics, no composite

Keep MITI, ESC, and TTM as three independent numbers. Report all three side-by-side. Let the reader decide which matters most.

**Pros**: Honest. Doesn't hide tradeoffs. Lets the community argue about weighting later.

**Cons**: Doesn't produce a single ranking. Two systems can be incomparable (System A wins MITI, System B wins ESC, System C wins TTM).

### 9.2 Option B: Composite score with explicit weights

Define:

```
HELP-E score = w_M · MITI_mean + w_E · ESC_mean + w_T · TTM_transition_rate
```

with `w_M + w_E + w_T = 1`. The benchmark commits to specific weights (e.g., 0.4 / 0.3 / 0.3 — MITI weighted highest because it's the clinical standard).

**Pros**: Single number, single ranking. Easy to communicate.

**Cons**: The weights are arbitrary. A reviewer will rightly ask why 0.4/0.3/0.3 and not 0.5/0.25/0.25. The weights determine the winner.

### 9.3 Option C (recommended): Pareto frontier + lexicographic tiebreaker

Report all three metrics separately, but also report:

- **Pareto-dominant systems**: Systems that are not dominated by any other on all three metrics.
- **Lexicographic ranking** for tied / dominated systems: rank by MITI first, then ESC, then TTM.

**Pros**: Honest about tradeoffs (Pareto). Produces a defensible ranking (lexicographic). Lets the community argue about whether to tiebreak by MITI or by TTM (with reasons).

**Cons**: More complex to explain. Requires a Pareto-frontier computation (trivially `O(n²)` for ~10 systems).

This is what I'd recommend. It's intellectually honest and leaves room for community input.

### 9.4 Reporting the composite

Whatever combining rule is chosen, the benchmark paper should also report:

- **MITI per-global breakdown** (4 separate columns: Cultivate, Soften, Partner, Empathy).
- **ESC per-dimension breakdown** (6 separate columns).
- **TTM transition by problem** (or at least by problem-category).
- **Per-profile variance / 95% CI**.

A composite alone is not enough — reviewers always want the breakdown to see if the composite hides asymmetries.

---

## 10. A concrete release plan

If we decide to publish the benchmark, here's a roughly six-month plan.

### Month 1 — Internal hardening

- Finalize v6 architecture (frozen).
- Run the 30 × 4 × 10 matrix end-to-end; verify reproducibility (`HELPE_SEED_SALT=test1` produces identical outputs on a re-run).
- Lock the prompt set; version-tag (`prompts-v1.0`).
- Sensitivity analysis on the edge-weight α parameters and recency half-life.
- Decide composite vs. Pareto (§9) and freeze the formula.

### Month 2 — Baselines

- Implement and run: v1, v3 (already done), CAMI, GPT-4o, Claude 3.5 Sonnet, vanilla Llama-3.3-70B, RAG.
- Score all of them on MITI / ESC / TTM.
- Produce the comparison table.

### Month 3 — Human validation

- Recruit 2–3 MI raters (counseling psychology grad students or clinicians).
- Sample 30 sessions stratified across systems.
- Have raters score MITI + ESC independently.
- Compute inter-rater reliability + human-judge correlation.
- Publish calibration table.

### Month 4 — Documentation & open-source

- Open-source the codebase (after the EmoCare licensing question is resolved).
- Write the README.
- Write the "how to score *your* system" walkthrough.
- Set up a GitHub repo with submission instructions.

### Month 5 — Paper writing

- Submit to a venue. **Best fits**:
  - **EMNLP** (NLP focus; has hosted many benchmarks).
  - **ACL** (broader; competitive but has hosted ESC work).
  - **CHI** (HCI angle: emotional support as user experience).
  - **CSCW** (collaborative work; multi-session memory has CSCW relevance).
  - **JAMIA Open** or **JMIR Mental Health** (the digital-health journal route — slower but reaches the clinical audience).

I'd target **EMNLP findings track** for v1 and a **journal** for the extended version.

### Month 6 — Camera-ready + leaderboard launch

- Finalize the paper.
- Launch a public leaderboard (could be as simple as a GitHub README that anyone can PR to).
- Run a workshop or invited talk to draw attention.

---

## 11. Risks and how reviewers would push back

| Reviewer concern | Likelihood | Response |
|---|---|---|
| **"The judges are LLMs scoring LLMs."** | High (this is the #1 concern) | We address it with the human-validation calibration study (§7.1). The headline numbers are reported alongside human-judge correlations. |
| **"Why MITI 4 globals and not the full MITI?"** | Medium | The 4 globals are the validated session-level summary. The behavior counts (R/Q ratio, %OQ, %CR) are turn-level and would multiply our LLM-judge cost ~10×. We could add behavior counts as a supplementary metric. |
| **"ESC overlaps with ESC-Eval. Why a new rubric?"** | Medium | Two MI-specific dimensions (`autonomy_respect`, `non_judgment`) that ESC-Eval lacks. We report both rubrics. |
| **"TTM is contested in psychology."** | Medium | We're using TTM as a measurement framework (forward stage transitions on the standard 4-stage taxonomy), not as a model of cognition. We acknowledge the debate explicitly. |
| **"30 profiles is small."** | High | Each profile gives 4 sessions × ~10 turns = ~40 conversational turns, and the per-turn structured outputs give multiple datapoints. The unit of statistical comparison is the session (n=120) or the profile (n=30) depending on the metric. We can grow to 60 or 100 profiles in v1.1 if desired. |
| **"Multi-session memory isn't tested cleanly — it's confounded with multi-problem tracking."** | Medium | True. We can add a multi-session memory probe (a held-out QA task on the graph, à la LOCOMO) in v1.1. v1 ships without it but flags the limitation. |
| **"Mind-1 v6 is also an LLM. The 'simulated user' is biased."** | High | This is real. We mitigate it by (a) using a different LLM family for the simulator (gpt-oss:20b) than for the chatbot (Llama-3.3-70B), and (b) running a held-out human evaluation where real users (or recorded human sessions from AnnoMI) substitute for Mind-1 on a sample. |
| **"Why these 20 problems?"** | Low | Drawn from EmoCare with crisis topics removed. Documented in the data section. |
| **"Reproducibility — Llama-3.3 weights, vLLM version, hardware?"** | Medium | Hash-seeded sampling, pinned model versions, deterministic prompt set. Acknowledge that the *exact* numerical output depends on inference hardware (different GPUs may produce slightly different sampling), but the qualitative ranking is reproducible. |

The strongest reviewer concerns are around (1) LLM-judge validity and (2) simulator validity. Both are addressable with the calibration / held-out-human studies described above. The benchmark is publishable as long as we do that work.

---

## 12. TL;DR

HELP-E measures three things every counseling chatbot should be measured on:

1. **MITI 4.2** (4 globals on 1–5) — *was the technique right?*
2. **ESC** (6 dimensions on 1–5) — *was the experience right?*
3. **TTM transition rate** — *did real progress happen?*

Each criterion is grounded in a well-established psychology tradition (MI clinical training, helping-skills, behavior-change science). Together they triangulate — a system that wins all three is doing the right thing in the right way and producing real change.

The infrastructure to run the benchmark (profiles, simulator, judges, reproducibility, scoring code) is largely in place. The two big gaps before public release are **human validation of the LLM judges** (~$600–900, ~1 week) and **more baselines on the scoreboard** (~2 weeks of work).

To combine the three into a single benchmark number, the recommended approach is **Pareto-frontier + lexicographic tiebreaker on MITI**, not a fixed weighted composite — this is honest about tradeoffs while still producing a usable ranking.

Best publication venues: **EMNLP** or **ACL** for the NLP audience, **CHI** or **CSCW** for the HCI audience, **JMIR Mental Health** for the clinical audience. A combined plan would target an EMNLP/ACL paper for the technical contribution and a JMIR follow-up for the clinical validation.
