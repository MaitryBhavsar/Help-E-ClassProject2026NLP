# P18 — primary: `work_stress`

**Seed situation:** My partner lost their job three months ago, and I have become the sole breadwinner while supporting us both. My father was diagnosed with Alzheimer’s last year, and I am his primary caregiver, which demands much of my time and energy. Last week, my best friend moved abroad for a new job opportunity, leaving me feeling isolated

## Hidden session context *(simulator-only, NOT seen by chatbot)*

- **Current life events:** The user has been juggling a demanding part‑time job to support their partner who lost employment, while also caring for their father with Alzheimer’s. The past week saw their best friend move abroad, leaving the user feeling increasingly isolated. The user’s workload has intensified with additional responsibilities at work, and they’ve been feeling pulled in multiple directions.
- **Mental state:** The user feels stretched thin, constantly balancing financial obligations, caregiving duties, and personal loneliness. They are trying to keep everything afloat but are beginning to question their capacity to sustain this pace.
- **Mood:** overwhelmed and exhausted
- **Emotions:** anxious, stressed, lonely
- **Resistance / cooperation:** `low`
- **Active problems (hidden):** work_stress, caregiver_stress, loneliness
- **Why now:** The combination of increased work demands, ongoing caregiving responsibilities, and recent social isolation has made the user feel particularly strained, prompting them to seek support today.

## Session transcript

### Turn 1 — intent `vent`, main `work_stress`

**User:**
> I feel like I'm drowning in this mess. Work is piling up, my dad needs me, and I barely have anyone to talk to. I keep wondering if I can keep doing this forever.

**Assistant:**
> It sounds like you're feeling completely overwhelmed, like everything is piling up on you at once. You're questioning whether you can keep going, and that's a really tough place to be. What is it about your current situation that's making you feel like you can't keep doing this forever?

**Techniques committed:** `T1 T8`
**Current problems:** work_stress, conflicts_with_parents, loneliness

<details>
<summary>Inference (what the system inferred from this turn)</summary>

**Attribute entries:**
  - `work_stress.perceived_severity` (level, conf=`high`): user feels overwhelmed by work
  - `loneliness.perceived_severity` (level, conf=`high`): user feels isolated
  - `work_stress.motivation` (level, conf=`high`): user is questioning their ability to continue

**Attribute-connections:**
  *(none)*

**Co-occurrence connections:** 3

</details>

<details>
<summary>Recompute (level + TTM updates after this turn)</summary>

**Level updates:**
  - `work_stress.perceived_severity`: unknown → **high** — User feels overwhelmed by work, indicating a high level of perceived severity.
  - `work_stress.motivation`: unknown → **low** — User is questioning their ability to continue, suggesting a low level of motivation.
  - `loneliness.perceived_severity`: unknown → **high** — User feels isolated, indicating a high level of perceived severity.

**TTM updates:**
  - `work_stress`: precontemplation → **contemplation** — User is questioning their ability to continue and feels overwhelmed, indicating a shift from precontemplation to contemplation.
  - `loneliness`: precontemplation → **contemplation** — User feels isolated, which may indicate a growing awareness of the problem, but not enough evidence to confirm a stage change beyond contemplation.
  - `conflicts_with_parents`: precontemplation → **precontemplation** — No new evidence was provided for this problem, so the stage remains the same.

</details>

<details>
<summary>System intent + reasoning (LLM plan for this turn)</summary>

**system_intent:**

> The user's main problem appears to be work_stress. Their current TTM stage appears to be contemplation. Because of this, the response should use T1 Reflection and T8 Change Talk. This technique is appropriate because the user is questioning their ability to continue, indicating a need to explore their motivations and reasons for change. It should be applied by reflecting the user's feelings of overwhelm and isolation, using evidence such as their perceived_severity of work_stress and loneliness. The user also shows low motivation, which suggests that T8 Change Talk could help them identify their own reasons for wanting to make a change. The user intent in this turn is to vent, which indicates that the response should prioritize empathy and understanding over solution-focused talk. Relevant connected problems suggest a shared sense of overwhelm between work_stress and conflicts_with_parents, as well as a potential link between loneliness and work_stress. This evidence should be used by acknowledging the user's feelings of isolation and exploring how these might be related to their work stress. Overall, the response should try to improve the user's motivation and awareness of their own strengths and capabilities, while avoiding giving unsolicited advice or trying to fix the problem immediately.

**response_reasoning:**

> I'll open with T1 Reflection of the user's feelings of overwhelm and isolation, then use T8 Change Talk to gently explore their reasons for wanting to make a change. I'll skip the connection to conflicts_with_parents and loneliness for now, focusing on the user's immediate emotional experience. I'll aim to acknowledge their venting and show empathy before guiding the conversation further.

</details>

### Turn 2 — intent `vent`, main `work_stress`

**User:**
> I don't know how much longer I can keep doing this. The work piles up, my dad needs me constantly, and I feel so alone after my friend left.

**Assistant:**
> It sounds like you're feeling completely overwhelmed, like the work and your dad's needs are piling up on you, and the loss of your friend has left you feeling alone. What are some reasons why you think you can't keep going on like this, and what would need to change for you to feel more in control?

**Techniques committed:** `T1 T8`
**Current problems:** work_stress, conflicts_with_parents, loneliness

<details>
<summary>Inference (what the system inferred from this turn)</summary>

**Attribute entries:**
  - `work_stress.perceived_severity` (level, conf=`high`): user feels overwhelmed by work
  - `conflicts_with_parents.perceived_severity` (level, conf=`high`): user feels burdened by dad's constant needs
  - `loneliness.triggers` (non_level, conf=`high`): friend's departure exacerbates feelings of loneliness

**Attribute-connections:**
  - `reinforcing` work_stress.perceived_severity ↔ conflicts_with_parents.perceived_severity (conf=`medium`): the user's feelings of overwhelm by work and burdened by dad's needs reinforce each other
  - `causal` work_stress.perceived_severity ↔ loneliness.triggers (conf=`low`): the user's feelings of overwhelm by work may contribute to their feelings of loneliness
  - `causal` conflicts_with_parents.perceived_severity ↔ loneliness.triggers (conf=`low`): the user's feelings of burdened by dad's needs may contribute to their feelings of loneliness

**Co-occurrence connections:** 3

</details>

<details>
<summary>Recompute (level + TTM updates after this turn)</summary>

**Level updates:**
  - `work_stress.perceived_severity`: high → **high** — User continues to describe feeling overwhelmed by work, with no change in the level of severity described.
  - `conflicts_with_parents.perceived_severity`: unknown → **high** — User now describes feeling burdened by dad's constant needs, indicating a high level of perceived severity.

**TTM updates:**
  - `work_stress`: contemplation → **contemplation** — User continues to acknowledge the problem and question their ability to continue, but there is no evidence of progression to the next stage.
  - `conflicts_with_parents`: precontemplation → **contemplation** — User now acknowledges the problem, describing feeling burdened by dad's constant needs, indicating a shift from precontemplation to contemplation.
  - `loneliness`: contemplation → **contemplation** — User continues to acknowledge the problem and describe feelings of isolation, with no evidence of progression to the next stage.

</details>

<details>
<summary>System intent + reasoning (LLM plan for this turn)</summary>

**system_intent:**

> The user's main problem appears to be work_stress, and their current TTM stage is contemplation. Because of this, the response should use T1 Reflection and T8 Change Talk. This technique is appropriate because the user is questioning their ability to continue and is feeling overwhelmed by work. It should be applied by reflecting the user's feelings of being overwhelmed and then exploring their reasons for wanting to change. The user also shows high perceived_severity and low motivation, which suggests that the response should use T8 Change Talk to invite the user's own reasons for change. The user intent in this turn is to vent, so the response should acknowledge their feelings first. The related problems, conflicts_with_parents and loneliness, suggest a reinforcing relationship between the user's feelings of overwhelm by work and burdened by dad's needs, as well as a causal relationship between the user's feelings of overwhelm by work and their feelings of loneliness. This evidence should be used by reflecting the user's feelings and then exploring how these problems are connected. Overall, the response should try to improve the user's motivation and awareness of their problems, while avoiding giving unsolicited advice or pivoting to the secondary problems too quickly.

**response_reasoning:**

> I'll open with T1 Reflection of the user's feelings of being overwhelmed, then use T8 Change Talk to invite their reasons for wanting to change. I'll name the user's goal of not wanting to feel overwhelmed by work and their dad's needs, and explore how these problems are connected. I'll drop the explicit mention of the related problems for now, to avoid overwhelming the user.

</details>

### Turn 3 — intent `vent`, main `work_stress`

**User:**
> I don't even know how much longer I can keep doing this. The work piles up, my dad needs me constantly, and I feel so alone after my friend left. I feel like I'm going to snap if I keep going like this.

**Assistant:**
> It sounds like you're feeling completely overwhelmed, like you're at a breaking point. You're questioning whether you can keep going, and that's a really tough place to be. What's making you feel like you're going to snap if you keep going like this?

**Techniques committed:** `T1 T8`
**Current problems:** work_stress, conflicts_with_parents, loneliness

<details>
<summary>Inference (what the system inferred from this turn)</summary>

**Attribute entries:**
  - `work_stress.perceived_severity` (level, conf=`high`): user sees work stress as highly severe
  - `conflicts_with_parents.perceived_severity` (level, conf=`high`): user sees dad's needs as highly demanding
  - `loneliness.perceived_severity` (level, conf=`high`): user sees loneliness as highly distressing

**Attribute-connections:**
  - `reinforcing` work_stress.perceived_severity ↔ conflicts_with_parents.perceived_severity (conf=`medium`): the high severity of work stress and conflicts with parents reinforce each other
  - `reinforcing` work_stress.perceived_severity ↔ loneliness.perceived_severity (conf=`medium`): the high severity of work stress and loneliness reinforce each other
  - `reinforcing` conflicts_with_parents.perceived_severity ↔ loneliness.perceived_severity (conf=`medium`): the high severity of conflicts with parents and loneliness reinforce each other

**Co-occurrence connections:** 3

</details>

<details>
<summary>Recompute (level + TTM updates after this turn)</summary>

**Level updates:**
  - `work_stress.perceived_severity`: high → **high** — User continues to describe work stress as highly severe, with consistent expressions of feeling overwhelmed.
  - `conflicts_with_parents.perceived_severity`: high → **high** — User maintains that dad's needs are highly demanding, indicating no change in perceived severity.
  - `loneliness.perceived_severity`: high → **high** — User still describes loneliness as highly distressing, with no indication of a decrease in perceived severity.

**TTM updates:**
  - `work_stress`: contemplation → **contemplation** — User remains in contemplation, acknowledging the problem but not yet taking concrete steps towards change, as indicated by consistent expressions of feeling overwhelmed and questioning their ability to continue.
  - `conflicts_with_parents`: contemplation → **contemplation** — User stays in contemplation, recognizing the problem of dad's demanding needs but not showing signs of moving towards preparation or action.
  - `loneliness`: contemplation → **contemplation** — User remains in contemplation, acknowledging loneliness as a problem and its distressing nature, but without evidence of progressing towards preparation or action.

</details>

<details>
<summary>System intent + reasoning (LLM plan for this turn)</summary>

**system_intent:**

> The user's main problem appears to be work_stress, and their current TTM stage is contemplation. Because of this, the response should use T1 Reflection and T8 Change Talk. This technique is appropriate because the user is questioning their ability to continue and is feeling overwhelmed, indicating a need to explore their motivations for change. It should be applied by reflecting the user's feelings of overwhelm and hopelessness, using evidence such as their perceived_severity of work stress and their motivation levels. The user also shows high perceived_severity of work stress and low motivation, indicating a need to explore their reasons for feeling overwhelmed. Because of this, the response should use T8 Change Talk, applying it by asking the user to elaborate on their reasons for feeling like they can't keep going. The user intent in this turn is to vent, and because of this, the response should use T1 Reflection, acknowledging the user's immediate intent by reflecting their feelings of overwhelm and frustration. Relevant connected problems suggest a reinforcing relationship between work_stress and conflicts_with_parents, as well as between work_stress and loneliness, indicating that the user's feelings of overwhelm are exacerbated by their relationships and social isolation. This evidence should be used by surfacing it in a reflection, highlighting how the user's feelings of overwhelm are connected to their relationships and social isolation. Overall, the response should try to improve the user's awareness of their motivations for change, while avoiding giving unsolicited advice or pivoting to a different problem.

**response_reasoning:**

> I'll open with T1 Reflection of the user's feelings of overwhelm and hopelessness, then use T8 Change Talk to invite the user to elaborate on their reasons for feeling like they can't keep going. I'll skip the connection to conflicts_with_parents and loneliness this turn, focusing on the user's immediate emotional state. I'll name the user's feelings of overwhelm and frustration to ground the turn.

</details>

### Turn 4 — intent `vent`, main `work_stress`

**User:**
> I don't even know how to keep my head above water. The work deadlines keep coming, my dad's needs never stop, and I have no one to lean on after my friend moved away. I feel like I'm about to collapse.

**Assistant:**
> It sounds like you're feeling completely overwhelmed, like you're barely keeping your head above water. The work deadlines and your dad's needs are piling up, and you feel like you have no one to lean on. Would it be okay if we explored some ways to help you cope with this feeling of being overwhelmed, or would you rather just talk about how you're feeling right now?

**Techniques committed:** `T1 T8 T5`
**Current problems:** work_stress, conflicts_with_parents, loneliness

<details>
<summary>Inference (what the system inferred from this turn)</summary>

**Attribute entries:**
  - `work_stress.perceived_severity` (level, conf=`high`): user sees work deadlines as unsustainable
  - `conflicts_with_parents.perceived_severity` (level, conf=`high`): user sees dad's needs as relentless
  - `loneliness.perceived_severity` (level, conf=`high`): user feels isolated after friend moved away

**Attribute-connections:**
  - `reinforcing` work_stress.perceived_severity ↔ conflicts_with_parents.perceived_severity (conf=`high`): work deadlines and dad's needs reinforce each other in contributing to feeling overwhelmed
  - `reinforcing` work_stress.perceived_severity ↔ loneliness.perceived_severity (conf=`high`): work deadlines and lack of social support reinforce each other in contributing to feeling overwhelmed
  - `reinforcing` conflicts_with_parents.perceived_severity ↔ loneliness.perceived_severity (conf=`high`): dad's needs and lack of social support reinforce each other in contributing to feeling overwhelmed

**Co-occurrence connections:** 3

</details>

<details>
<summary>Recompute (level + TTM updates after this turn)</summary>

**Level updates:**
  - `work_stress.perceived_severity`: high → **high** — User continues to describe work stress as overwhelming and deadlines as unsustainable, consistent with previous entries.
  - `conflicts_with_parents.perceived_severity`: high → **high** — User consistently describes dad's needs as highly demanding and relentless, with no indication of change.
  - `loneliness.perceived_severity`: high → **high** — User feels isolated after friend moved away, consistent with previous descriptions of loneliness as highly distressing.

**TTM updates:**
  - `work_stress`: contemplation → **contemplation** — User acknowledges the problem of work stress but there's no new evidence of planning or taking action towards change.
  - `conflicts_with_parents`: contemplation → **contemplation** — User recognizes the burden of dad's needs but there's no indication of moving towards planning or action for change.
  - `loneliness`: contemplation → **contemplation** — User acknowledges loneliness and its distress but doesn't show signs of planning or taking concrete steps towards change.

</details>

<details>
<summary>System intent + reasoning (LLM plan for this turn)</summary>

**system_intent:**

> The user's main problem appears to be work_stress, with a current TTM stage of contemplation. Because of this, the response should use T1 Reflection and T8 Change Talk. This technique is appropriate because the user is questioning their ability to continue and is feeling overwhelmed by work deadlines and their dad's needs. It should be applied by reflecting the user's feelings of being overwhelmed and their concerns about not being able to keep going, using evidence such as their perceived severity of work stress and their motivation levels. The user also shows high perceived severity and low motivation, which suggests that they are struggling to cope with their current situation. Because of this, the response should use T5 Ask Permission to explore potential solutions. The user intent in this turn is to vent, which suggests that they need to express their emotions and concerns before considering any changes. Because of this, the response should use T1 Reflection to acknowledge the user's feelings and concerns. Relevant connected problems suggest that the user's feelings of overwhelm are reinforced by their conflicts with parents and loneliness, which should be acknowledged in the response. Overall, the response should try to improve the user's awareness of their situation and their motivation to make changes, while avoiding giving unsolicited advice or solutions.

**response_reasoning:**

> I'll open with T1 Reflection of the user's feelings of being overwhelmed, then use T8 Change Talk to explore their reasons for wanting to make a change. I'll skip the connection to conflicts with parents and loneliness for now, and instead focus on the user's immediate concerns and emotions. I'll ask permission to explore potential solutions, using T5 Ask Permission.

</details>

### Turn 5 — intent `vent`, main `work_stress`

**User:**
> I don't even know how to keep my head above water. I feel like I'm about to collapse.

**Assistant:**
> It sounds like you're feeling completely overwhelmed, like you're barely keeping your head above water. You're questioning whether you can keep going, and that's a really tough place to be. What are some things that make you want to make a change in your current situation?

**Techniques committed:** `T1 T8`
**Current problems:** work_stress, conflicts_with_parents, loneliness

<details>
<summary>Inference (what the system inferred from this turn)</summary>

**Attribute entries:**
  - `work_stress.perceived_severity` (level, conf=`high`): user sees work deadlines as unsustainable
  - `conflicts_with_parents.perceived_severity` (level, conf=`high`): user sees dad's needs as constant and overwhelming
  - `loneliness.perceived_severity` (level, conf=`high`): user feels alone and without support

**Attribute-connections:**
  - `reinforcing` work_stress.perceived_severity ↔ conflicts_with_parents.perceived_severity (conf=`high`): work deadlines and dad's needs both contribute to the feeling of being overwhelmed
  - `reinforcing` work_stress.perceived_severity ↔ loneliness.perceived_severity (conf=`high`): work deadlines and feeling alone both contribute to the feeling of being overwhelmed
  - `reinforcing` conflicts_with_parents.perceived_severity ↔ loneliness.perceived_severity (conf=`high`): dad's needs and feeling alone both contribute to the feeling of being overwhelmed

**Co-occurrence connections:** 3

</details>

<details>
<summary>Recompute (level + TTM updates after this turn)</summary>

**Level updates:**
  - `work_stress.perceived_severity`: high → **high** — User continues to describe work deadlines as unsustainable, consistent with previous entries indicating high severity.
  - `conflicts_with_parents.perceived_severity`: high → **high** — User still describes dad's needs as constant and overwhelming, consistent with previous entries indicating high severity.
  - `loneliness.perceived_severity`: high → **high** — User continues to feel alone and without support, consistent with previous entries indicating high severity.

**TTM updates:**
  - `work_stress`: contemplation → **contemplation** — User remains in contemplation stage as they continue to acknowledge the problem of work stress but have not indicated any progression towards planning or taking action.
  - `conflicts_with_parents`: contemplation → **contemplation** — User stays in contemplation stage as they acknowledge the problem of conflicts with parents but have not shown signs of moving towards planning or action.
  - `loneliness`: contemplation → **contemplation** — User remains in contemplation stage as they continue to acknowledge their loneliness but have not indicated any progression towards planning or taking action to address it.

</details>

<details>
<summary>System intent + reasoning (LLM plan for this turn)</summary>

**system_intent:**

> The user's main problem appears to be work_stress, with a current TTM stage of contemplation. Because of this, the response should use T1 Reflection and T8 Change Talk. This technique is appropriate because the user is questioning their ability to continue and is feeling overwhelmed, indicating a need to explore their motivations for change. It should be applied by reflecting the user's feelings of being overwhelmed and their concerns about their ability to continue, using evidence such as their perceived_severity of work stress and their motivation levels. The user also shows high perceived_severity and low motivation, indicating a need to explore their reasons for change. Because of this, the response should use T8 Change Talk, applying it by asking the user to elaborate on their reasons for wanting to change their situation. The user intent in this turn is to vent, indicating a need to acknowledge their emotions before guiding the conversation. The related problems of conflicts_with_parents and loneliness suggest a reinforcing relationship between the user's work stress and their personal life, which should be acknowledged in the response. Overall, the response should try to improve the user's motivation and awareness of their situation, while avoiding giving unsolicited advice or solutions.

**response_reasoning:**

> I'll open with T1 Reflection of the user's feelings of being overwhelmed, then use T8 Change Talk to explore their reasons for wanting to change. I'll skip the connection to conflicts_with_parents and loneliness this turn, focusing on the user's main problem of work_stress. I'll name the user's emotions and concerns to ground the turn, avoiding any solutions or advice.

</details>

## Final graph state (after session 1)

**Problems (3):**

- **work_stress** — stage=`contemplation`
  - level attrs:
    - `perceived_severity=high` (5× evidence): user sees work deadlines as unsustainable
    - `motivation=low` (1× evidence): user is questioning their ability to continue

- **conflicts_with_parents** — stage=`contemplation`
  - level attrs:
    - `perceived_severity=high` (4× evidence): user sees dad's needs as constant and overwhelming

- **loneliness** — stage=`contemplation`
  - level attrs:
    - `perceived_severity=high` (4× evidence): user feels alone and without support
  - non-level attrs:
    - `triggers` (1× evidence): friend's departure exacerbates feelings of loneliness


**Edges (3):**

- **conflicts_with_parents ↔ work_stress** (weight=1.000, 5× cooc, 4× attr-conn)
  - `reinforcing` perceived_severity ↔ perceived_severity (conf=`medium`, s1t3): the high severity of work stress and conflicts with parents reinforce each other
  - `reinforcing` perceived_severity ↔ perceived_severity (conf=`high`, s1t4): work deadlines and dad's needs reinforce each other in contributing to feeling overwhelmed
  - `reinforcing` perceived_severity ↔ perceived_severity (conf=`high`, s1t5): work deadlines and dad's needs both contribute to the feeling of being overwhelmed

- **loneliness ↔ work_stress** (weight=0.978, 5× cooc, 4× attr-conn)
  - `reinforcing` perceived_severity ↔ perceived_severity (conf=`medium`, s1t3): the high severity of work stress and loneliness reinforce each other
  - `reinforcing` perceived_severity ↔ perceived_severity (conf=`high`, s1t4): work deadlines and lack of social support reinforce each other in contributing to feeling overwhelmed
  - `reinforcing` perceived_severity ↔ perceived_severity (conf=`high`, s1t5): work deadlines and feeling alone both contribute to the feeling of being overwhelmed

- **conflicts_with_parents ↔ loneliness** (weight=0.978, 5× cooc, 4× attr-conn)
  - `reinforcing` perceived_severity ↔ perceived_severity (conf=`medium`, s1t3): the high severity of conflicts with parents and loneliness reinforce each other
  - `reinforcing` perceived_severity ↔ perceived_severity (conf=`high`, s1t4): dad's needs and lack of social support reinforce each other in contributing to feeling overwhelmed
  - `reinforcing` perceived_severity ↔ perceived_severity (conf=`high`, s1t5): dad's needs and feeling alone both contribute to the feeling of being overwhelmed

## Session summary (written by summary-agent)

> The user primarily discussed feeling overwhelmed by work stress, conflicts with parents due to caregiving responsibilities, and loneliness after a friend moved away. They moved into the contemplation stage for these issues, acknowledging the need for change. The user expressed emotional and anxious personality traits, with a limited support system, and described themselves as emotionally reactive. An open thread is the user's desire to explore ways to cope with their overwhelming situation, which they would like to revisit in the next session.

## Stage transitions (pre → post session)

- **conflicts_with_parents**: `(absent)` → `contemplation`
- **loneliness**: `(absent)` → `contemplation`
- **work_stress**: `(absent)` → `contemplation`

## Persona inferred at session end

  - **personality_traits**: emotional, overwhelmed, anxious
  - **support_system**: limited, friend moved away, dad needs care
  - **communication_style**: emotional, expressive
  - **relevant_history**: friend moved away, caring for dad
  - **general_behavioral_traits**: overwhelmed, emotionally reactive

## Mind-3 satisfaction ratings

- Session 1:
  - `empathy`: **3**/5
  - `understanding`: **2**/5
  - `helpfulness`: **2**/5
  - `autonomy_respect`: **4**/5
  - `non_judgment`: **4**/5
  - `willingness_to_continue`: **3**/5

## Mind-2 retrospective labels

- **Session 1:** The user repeatedly described feeling overwhelmed by a mounting workload, the constant needs of their father, and a sense of isolation after a friend moved away. They expressed uncertainty about how long they could sustain this situation and a fear of breaking down. Across all turns, the user’s tone remained consistent, with no shift toward planning or action. The conversation highlighted persistent distress without any indication of change in perspective or coping attempts.

### Per-problem trajectories (Mind-2)

- **work_stress**
  - Session 1: `contemplation` → `contemplation` — Acknowledgement of unsustainable workload and uncertainty about continuation.
- **caregiver_stress**
  - Session 1: `contemplation` → `contemplation` — Recognition of constant caregiving demands and feeling overwhelmed.
- **loneliness**
  - Session 1: `contemplation` → `contemplation` — Feeling of isolation after a friend moved away.
