# HELP-E v6 — MI strategy mapping (theory-grounded reference)

This document is the canonical reference for HELP-E v6's MI strategy
selection. It maps the system's three signals (HBM attributes, TTM stage,
user_intent) to the MI moves (OARS baseline + selectable MISC strategies)
the chatbot can use, with citations to primary literature for every
element.

## Design rationale

HELP-E v6 separates concerns cleanly:

- **HBM attributes** provide the *content* of every response — what the
  user believes/feels about each problem. They never pick the strategy
  for a turn. (Rosenstock 1974 — Health Belief Model.)
- **TTM stage** is the *sole* source of MI strategy candidates per turn.
  Strategy choice depends on where the user is on the change ladder —
  not on what beliefs they hold, and not on what they're asking from
  this particular message. (Prochaska & DiClemente 1983; SAMHSA TIP 35,
  2019, ch. 4.)
- **user_intent** is a lightweight modifier that decides the *entry
  style* of the response — how it begins. It does not add new strategy
  candidates. (M&R 2013 ch. 4–5 on matching response to client need;
  ESConv seeker-side analysis, Liu et al. 2021.)

This separation removes the redundancy of having two sources (intent +
TTM) compete for the same technique slot. It is consistent with TIP 35's
stage-tailored MI strategy chapters and with CAMI's TTM-state-inference +
MISC-strategy-selection architecture (Shi et al., adapted).

## TTM stages (4 values)

`maintenance` is dropped because HELP-E does not track sustained behavior
change across weeks/months. The 4 stages used:

| stage | meaning |
|---|---|
| `precontemplation` | User does not see a problem or does not want to change |
| `contemplation` | Acknowledges the problem; ambivalent about change |
| `preparation` | Intending to act soon; planning, small steps |
| `action` | Actively making changes; concrete recent steps |

(Source: Prochaska & DiClemente 1983; M&R 2013 ch. 18.)

## user_intent taxonomy (8 values)

Constructed HELP-E taxonomy. No off-the-shelf seeker-intent taxonomy
exists in MI/ESC literature; each value here is anchored to a primary
citation, and the set as a whole is HELP-E's contribution informed by:
M&R 2013, MISC client codes (Moyers et al. 2014), Cutrona & Suhr 1992,
and ESConv (Liu et al. 2021).

| value | definition | citation anchor | entry style |
|---|---|---|---|
| `express_emotion` | User expresses affect/distress without (yet) asking for help | MI Engaging phase, M&R 2013 ch. 4 | Reflect first; defer planning/info |
| `seek_validation` | User wants their feeling/reaction confirmed as reasonable | Cutrona & Suhr 1992 esteem support; MI Affirm | Normalize; affirm specifically |
| `seek_information` | User wants facts, context, perspective | MI EPE, M&R 2013 ch. 11 | Elicit what they know → provide one piece → elicit reaction |
| `deliberate_decision` | User is weighing options or sitting with ambivalence | MI decisional balance, M&R 2013 ch. 7 | Reflect both sides before any nudge |
| `request_plan` | User asks what to do / next step | MI Planning phase, M&R 2013 ch. 19 | Propose ONE small step matched to TTM stage |
| `report_action` | User reports an attempt, slip, or progress | MI Evoking + reinforcement, M&R 2013 ch. 12 | Affirm the specific effort; respond to the content of the report |
| `resistance` | User pushes back, disagrees, expresses reluctance | MISC "Sustain Talk" client code (Moyers et al. 2014); MI "rolling with resistance" M&R 2013 ch. 14 | Roll with it — reflect resistance without arguing; emphasize autonomy |
| `small_talk` | Session opening, closing, or pleasantries | MI Engaging phase, M&R 2013 ch. 4; ESC engagement | Stay light; warm acknowledgment |

## MI vocabulary

The chatbot's MI vocabulary has three distinct layers:

### Layer 1 — OARS basic skills (always-on baseline; in SYSTEM prompt; not selectable per turn)

The four basic MI skills, used as the conversational fabric every turn.
(M&R 2013, ch. 6.)

| code | what it is |
|---|---|
| `open_question` | Open Question — invite elaboration, don't interrogate |
| `affirm` | Affirmation — spotlight a SPECIFIC effort, never generic praise |
| `simple_reflection` | Simple Reflection — name what's UNDERNEATH the user's words |
| `summary` | Summary — gather threads at natural breakpoints |

### Layer 2 — MI principle (always-on; in SYSTEM prompt; not selectable)

(M&R 2013, ch. 2–3, MI Spirit.)

| code | what it is |
|---|---|
| `emphasize_control` | Autonomy support — the user owns their choices |

### Layer 3 — Selectable MISC strategy codes (10) — picked per turn from a candidate list

Each strategy carries a TTM transition function (the change-ladder move
it's designed to support). Citations: TIP 35 ch. 4 (stage-tailored
strategies); M&R 2013 ch. 18 (MI–TTM integration); Moyers et al. 2014
(MISC code definitions).

| code | what it is | TTM transition function |
|---|---|---|
| `support` | Sympathetic / understanding statement | Sustains engagement at any stage; especially during distress / resistance |
| `facilitate` | Brief encouragement to continue ("mm-hmm", "go on") | Sustains conversation flow at any stage |
| `complex_reflection` | Reflect with added inferred meaning, depth, shift in emphasis | Develops discrepancy → drives **pre → contempl** |
| `reframe` | Offer alternative interpretation that widens or shifts meaning | Widens stuck frame → drives **pre → contempl** |
| `inform_with_permission` | Provide information after asking permission | Builds awareness (**pre → contempl**) or supports informed decision (**contempl → prep**) |
| `raise_concern_with_permission` | Voice concern about a plan or pattern after asking permission | Develops discrepancy gently (**pre → contempl**) or surfaces risks to refine plan (**prep → action**) |
| `evoke` | Invite client's own reasons for change in their words (counselor-side mirror of MISC client "Change Talk") | Core MI move for resolving ambivalence → drives **contempl → prep** |
| `closed_question` | Question that limits answer to yes/no/short — used sparingly | Confirms commitment or specific plan elements (**prep → action**) |
| `advise_with_permission` | Suggest a course of action after asking permission | Drives **prep → action**; supports change planning |
| `structure` | Explain what will happen next in the session or turn | Drives **prep → action** by structuring the change plan |

### Anti-patterns — MI-inconsistent MISC codes (in SYSTEM prompt as warnings; NOT selectable)

These exist in the MISC inventory because MISC is observational — it
catalogues all counselor behavior, including bad behavior. HELP-E
deliberately excludes them from the selectable pool and lists them in the
SYSTEM prompt as anti-patterns.

| code | what it is | why excluded |
|---|---|---|
| `direct` | Giving an order / command | Violates MI autonomy support |
| `confront` | Arguing, criticizing, or disagreeing with the client | Known to increase resistance; MI-inconsistent |
| `warn` | Threatening consequences | MI-inconsistent |
| `advise_without_permission` | Suggestion not gated by permission | MI-inconsistent variant of advise |
| `inform_without_permission` | Information not gated by permission | MI-inconsistent variant of inform |
| `raise_concern_without_permission` | Concern not gated by permission | MI-inconsistent variant of raise-concern |

## Candidate-selection rule (TTM stage → MISC strategies)

Pure Python rule, no LLM scan. The candidate list per turn is the union
of `COMMON` (always available) and `STAGE_SPECIFIC[ttm_stage]`:

```python
COMMON = ("support", "facilitate")

STAGE_SPECIFIC = {
    "precontemplation": ("complex_reflection", "reframe",
                         "inform_with_permission",
                         "raise_concern_with_permission"),
    "contemplation":    ("evoke", "complex_reflection",
                         "inform_with_permission"),
    "preparation":      ("advise_with_permission", "closed_question",
                         "structure"),
    "action":           ("structure", "advise_with_permission",
                         "raise_concern_with_permission"),
}

def candidates_for_turn(ttm_stage):
    return COMMON + STAGE_SPECIFIC[ttm_stage]
```

Per turn, the LLM sees 5–6 candidate codes (not the full 10). Each
candidate is rendered with its `transition_fn` so the LLM can reason in
TTM-transition terms.

| TTM stage | Candidate count | Codes |
|---|---|---|
| `precontemplation` | 6 | support, facilitate, complex_reflection, reframe, inform_with_permission, raise_concern_with_permission |
| `contemplation` | 5 | support, facilitate, evoke, complex_reflection, inform_with_permission |
| `preparation` | 5 | support, facilitate, advise_with_permission, closed_question, structure |
| `action` | 5 | support, facilitate, structure, advise_with_permission, raise_concern_with_permission |

## Per-turn response shape

The chatbot's response is a structured object with three fields:

```json
{
  "reasoning": "4 short sentences walking the chain (Where? Which strategies? Evidence? Entry?)",
  "evidence_used": [
    {
      "source": "hbm_attribute.perceived_severity (main_problem=academic_pressure, s2t3)",
      "content": "user feels workload is crushing this week"
    },
    {
      "source": "problem_connection.academic_pressure↔sleep_problems (shared_trigger, s1t1)",
      "content": "late-night cramming drives both"
    }
  ],
  "final_response": "the user-facing reply"
}
```

`evidence_used` makes evidence usage auditable — for each turn we can
count by source type, filter `source` starting with
`problem_connection.*` to see when typed-edge graph evidence was
actually used, etc.

## Evaluation (3 metrics — see plan §1.a/§1.b/§1.c)

1. **MITI adherence** (session-level LLM judge) — MITI 4.2 standard
   4 globals on 1–5 scale: Cultivating Change Talk, Softening Sustain
   Talk, Partnership, Empathy. (Moyers, Manuel, & Ernst 2014, MITI 4.2.1.)
2. **TTM state-transition rate** (pure compute, no judge) — per
   (profile, problem), turns-to-transition between consecutive TTM
   stages, derived from the system's per-turn `recompute` outputs.
3. **ESC adherence** (session-level Mind-3 judge) — 6 ESConv dimensions
   on 1–5 scale: empathy, understanding, helpfulness, autonomy_respect,
   non_judgment, willingness_to_continue. (Liu et al. 2021.)

Dropped: E2 (AnnoMI), E3a/E3b (subsumed by transition rate), E4
(maintenance dropped from TTM), Mind-2 entirely (its silver labels are
no longer needed), evidence-relevance evaluation (out of scope).

## Citations

- **Rosenstock, I. M.** (1974). The Health Belief Model and Preventive
  Health Behavior. *Health Education Monographs*, 2(4), 354–386.
- **Prochaska, J. O., & DiClemente, C. C.** (1983). Stages and processes
  of self-change in smoking. *Journal of Consulting and Clinical
  Psychology*, 51, 390–395.
- **Miller, W. R., & Rollnick, S.** (2013). *Motivational Interviewing:
  Helping People Change* (3rd ed.). Guilford Press.
- **Moyers, T. B., Manuel, J. K., & Ernst, D.** (2014). *Motivational
  Interviewing Treatment Integrity Coding Manual 4.2.1*. Unpublished
  manual.
- **SAMHSA** (2019). *Enhancing Motivation for Change in Substance Use
  Disorder Treatment* (Treatment Improvement Protocol [TIP] Series 35,
  updated). HHS Publication No. (SMA) 19-5062.
- **Cutrona, C. E., & Suhr, J. A.** (1992). Controllability of stressful
  events and satisfaction with spouse support behaviors. *Communication
  Research*, 19, 154–174.
- **Liu, S., Zheng, C., Demasi, O., Sabour, S., Li, Y., Yu, Z., Jiang, Y.,
  & Huang, M.** (2021). Towards Emotional Support Dialog Systems
  (ESConv). *ACL 2021*.
- **Shi, Z., et al.** CAMI: A Counselor Agent Supporting Motivational
  Interviewing through State Inference and Topic Exploration.
  (Reference for TTM-state-inference + MISC-strategy-selection
  architecture.)
