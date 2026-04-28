# P09 — primary: `social_anxiety`

**Seed situation:** In high school, I faced relentless bullying because of my hearing impairment. Students would mock the way I pronounced words and deliberately talk behind my back, knowing I couldn't hear them. They also stole my hearing aids multiple times, leaving me unable to follow lessons or participate in class discussions

## Hidden session context *(simulator-only, NOT seen by chatbot)*

- **Current life events:** Today, the user has a school assembly where they will be asked to speak in front of the whole class, and their hearing aid was stolen again last night, leaving them worried about missing important instructions. They also have a group art project meeting later that day and are anxious about presenting their work to classmates.
- **Mental state:** They feel a mix of nervousness and determination, but also a lingering fear of being judged and mocked again.
- **Mood:** nervous but determined
- **Emotions:** anxious, frustrated, hopeful
- **Resistance / cooperation:** `medium`
- **Active problems (hidden):** social_anxiety, loneliness
- **Why now:** The upcoming public speaking event and the theft of their hearing aid have heightened the user's social anxiety and feelings of isolation, prompting them to seek support today.

## Session transcript

### Turn 1 — intent `vent`, main `academic_pressure`

**User:**
> My hearing aid was taken again last night, so I’m terrified I’ll miss the instructions for tomorrow’s assembly. I’m supposed to speak in front of the whole class, and I keep thinking they’ll just laugh at me again. I hate feeling so exposed, especially when I have to present my group art project later.

**Assistant:**
> It sounds like you're feeling really overwhelmed and exposed with the upcoming presentation and assembly, and the loss of your hearing aid is adding to your anxiety. You're worried that you'll miss important instructions and be laughed at in front of your class. Would it be helpful to talk more about what's been going through your mind, or would you rather explore some ways to cope with this situation?

**Techniques committed:** `T1 T5`
**Current problems:** academic_pressure, social_anxiety, general_anxiety

<details>
<summary>Inference (what the system inferred from this turn)</summary>

**Attribute entries:**
  - `academic_pressure.perceived_severity` (level, conf=`high`): user sees the upcoming assembly as highly stressful
  - `social_anxiety.triggers` (non_level, conf=`high`): past experiences of being laughed at trigger current anxiety
  - `general_anxiety.perceived_barriers` (level, conf=`medium`): user feels their hearing aid issues and social anxiety are barriers to success

**Attribute-connections:**
  - `shared_trigger` academic_pressure.triggers ↔ social_anxiety.triggers (conf=`high`): the upcoming presentation triggers both academic pressure and social anxiety
  - `shared_barrier` academic_pressure.perceived_barriers ↔ general_anxiety.perceived_barriers (conf=`medium`): the user's hearing aid issues and social anxiety are shared barriers to success in the academic context

**Co-occurrence connections:** 3

</details>

<details>
<summary>Recompute (level + TTM updates after this turn)</summary>

**Level updates:**
  - `academic_pressure.perceived_severity`: unknown → **high** — User describes the upcoming assembly as 'highly stressful', indicating a high level of perceived severity.
  - `general_anxiety.perceived_barriers`: unknown → **high** — User identifies their hearing aid issues and social anxiety as barriers to success, suggesting a high level of perceived barriers.

**TTM updates:**
  - `academic_pressure`: precontemplation → **contemplation** — User acknowledges the stress of the upcoming assembly, indicating some awareness of the problem and potential consideration of change.
  - `general_anxiety`: precontemplation → **contemplation** — User identifies barriers to success, suggesting some recognition of the problem and potential consideration of change.
  - `social_anxiety`: precontemplation → **precontemplation** — No new evidence suggests a change in the user's awareness or consideration of change for social anxiety.

</details>

<details>
<summary>System intent + reasoning (LLM plan for this turn)</summary>

**system_intent:**

> The user's main problem appears to be academic_pressure, with a current TTM stage of contemplation. Because of this, the response should use T1 Reflection and T5 Ask Permission. This technique is appropriate because the user is acknowledging the problem and is ambivalent about change, and T1 Reflection can help them explore their feelings and concerns. It should be applied by reflecting the user's content and feeling, using evidence such as their perceived_severity of academic_pressure and their triggers, specifically the upcoming presentation and past experiences of being laughed at. The user also shows high perceived_severity and shared triggers with social_anxiety, which should be acknowledged in the reflection. The user intent in this turn is to vent, so the response should first acknowledge their feelings and concerns, then ask permission to explore possible solutions. Relevant connected problems suggest a shared_trigger between academic_pressure and social_anxiety, which should be surfaced in the reflection to help the user see the connection between their problems. Overall, the response should try to improve the user's awareness and emotional regulation, while avoiding giving unsolicited advice or pivoting to the secondary problem too quickly.

**response_reasoning:**

> I'll open with T1 Reflection of the user's feelings and concerns, specifically their fear of missing instructions and being laughed at, then gate options with T5 Ask Permission. I'll name the shared trigger between academic_pressure and social_anxiety to help the user see the connection between their problems, but will not delve deeply into the secondary problem. I'll prioritize the user's venting intent and avoid giving advice or solutions unless they explicitly ask for it.

</details>

### Turn 2 — intent `vent`, main `social_anxiety`

**User:**
> I just feel trapped in this loop. Every time I think about speaking, I hear the laughter in my head. I know I have to do it, but I don't know if talking about it or some coping plan will change anything.

**Assistant:**
> It sounds like the thought of speaking in front of the class is really triggering your anxiety, and you're feeling stuck. You've had past experiences that make you worry about being laughed at, and that's making it hard to see a way forward. What do you think would need to happen for you to feel more confident about presenting?

**Techniques committed:** `T1 T8 T5`
**Current problems:** social_anxiety, academic_pressure, general_anxiety

<details>
<summary>Inference (what the system inferred from this turn)</summary>

**Attribute entries:**
  - `social_anxiety.triggers` (non_level, conf=`high`): public speaking triggers social anxiety
  - `social_anxiety.perceived_severity` (level, conf=`high`): user sees social anxiety as a significant concern
  - `academic_pressure.perceived_benefits` (level, conf=`medium`): user recognizes the importance of presenting

**Attribute-connections:**
  - `shared_trigger` social_anxiety.triggers ↔ academic_pressure.triggers (conf=`high`): public speaking is a trigger for both social anxiety and academic pressure

**Co-occurrence connections:** 3

</details>

<details>
<summary>Recompute (level + TTM updates after this turn)</summary>

**Level updates:**
  - `social_anxiety.perceived_severity`: unknown → **high** — User now describes social anxiety as a 'significant concern', indicating a high level of perceived severity.
  - `academic_pressure.perceived_benefits`: unknown → **medium** — User recognizes the importance of presenting, suggesting a moderate level of perceived benefits.

**TTM updates:**
  - `social_anxiety`: precontemplation → **contemplation** — User acknowledges social anxiety as a significant concern and identifies triggers, indicating a shift from precontemplation to contemplation.
  - `academic_pressure`: contemplation → **contemplation** — User continues to weigh the importance of presenting and acknowledges the stress of the upcoming assembly, indicating no change in stage.
  - `general_anxiety`: contemplation → **contemplation** — No new evidence suggests a change in stage, and previous entries indicate the user is still considering their barriers to success.

</details>

<details>
<summary>System intent + reasoning (LLM plan for this turn)</summary>

**system_intent:**

> The user's main problem appears to be social_anxiety. Their current TTM stage appears to be contemplation. Because of this, the response should use T1 Reflection and T8 Change Talk. This technique is appropriate because the user is acknowledging their problem and is ambivalent about change. It should be applied by reflecting the user's feelings and concerns, using evidence such as their perceived_severity of social anxiety and their triggers, including past experiences of being laughed at and public speaking. The user also shows high perceived_severity and shared triggers with academic_pressure, including public speaking. Because of this, the response should use T5 Ask Permission to explore ways to cope with these triggers. The user intent in this turn is to vent, so the response should acknowledge their feelings and concerns before guiding the turn. Relevant connected problems suggest a shared_trigger between social_anxiety and academic_pressure, which should be used to highlight the user's concerns about public speaking. Overall, the response should try to improve the user's motivation and awareness of their concerns, while avoiding giving unsolicited advice or over-questioning.

**response_reasoning:**

> I'll open with T1 Reflection of the user's feelings of being trapped and their concerns about public speaking, then use T8 Change Talk to invite the user to explore their own reasons for change. I'll skip the connection to general_anxiety this turn and focus on the shared trigger with academic_pressure. I'll name the user's goal of presenting without anxiety once to ground the turn.

</details>

### Turn 3 — intent `explore_option`, main `social_anxiety`

**User:**
> I don’t know how to stop the laughter in my head. Maybe if I just stayed silent, but that feels like giving up on everything I’ve worked for.

**Assistant:**
> It sounds like the laughter in your head is really haunting you, and you're feeling torn between speaking up and staying silent. Would it be helpful to explore some ways to manage that anxiety, or would you rather talk more about what's holding you back from feeling confident about presenting?

**Techniques committed:** `T1 T8 T5`
**Current problems:** social_anxiety, academic_pressure, general_anxiety

<details>
<summary>Inference (what the system inferred from this turn)</summary>

**Attribute entries:**
  - `social_anxiety.triggers` (non_level, conf=`high`): past experiences of being laughed at trigger anxiety
  - `social_anxiety.perceived_severity` (level, conf=`medium`): user sees their social anxiety as a significant obstacle
  - `academic_pressure.perceived_benefits` (level, conf=`medium`): user sees presenting as a valuable experience

**Attribute-connections:**
  - `reinforcing` social_anxiety.triggers ↔ academic_pressure.perceived_severity (conf=`medium`): the user's social anxiety triggers (laughter) exacerbate their academic pressure (fear of failure)

**Co-occurrence connections:** 2

</details>

<details>
<summary>Recompute (level + TTM updates after this turn)</summary>

**Level updates:**
  - `social_anxiety.perceived_severity`: high → **high** — User continues to describe their social anxiety as a significant concern and obstacle, with no indication of a decrease in perceived severity.
  - `academic_pressure.perceived_benefits`: medium → **medium** — User still recognizes the importance and value of presenting, with no new evidence suggesting a change in perceived benefits.

**TTM updates:**
  - `social_anxiety`: contemplation → **contemplation** — User acknowledges their social anxiety and its triggers but does not express a clear intention to act or make changes, remaining in the contemplation stage.
  - `academic_pressure`: contemplation → **contemplation** — User recognizes the benefits of presenting but does not express a clear plan or intention to address the academic pressure, staying in the contemplation stage.
  - `general_anxiety`: contemplation → **contemplation** — There is no new evidence this turn to suggest a change in the user's stage regarding general anxiety.

</details>

<details>
<summary>System intent + reasoning (LLM plan for this turn)</summary>

**system_intent:**

> The user's main problem appears to be social_anxiety. Their current TTM stage appears to be contemplation. Because of this, the response should use T1 Reflection and T8 Change Talk. This technique is appropriate because the user is acknowledging their anxiety and is ambivalent about change. It should be applied by reflecting the user's feelings and concerns, using evidence such as their perceived_severity and triggers. The user also shows high perceived_severity and low self_efficacy. Because of this, the response should use T5 Ask Permission to explore options for coping with their anxiety. The user intent in this turn is to explore options, so the response should acknowledge this intent and guide the user towards exploring ways to manage their anxiety. Relevant connected problems suggest a shared_trigger between social_anxiety and academic_pressure, which should be used to highlight the interconnectedness of the user's concerns. Overall, the response should try to improve the user's motivation and self_efficacy, while avoiding giving unsolicited advice or pivoting to the secondary problem.

**response_reasoning:**

> I'll open with T1 Reflection of the user's feelings and concerns, then use T8 Change Talk to explore their reasons for wanting to change. I'll also use T5 Ask Permission to explore options for coping with their anxiety, and mention the shared_trigger between social_anxiety and academic_pressure to highlight the interconnectedness of their concerns. I'll drop the general_anxiety problem for now to focus on the main issue.

</details>

### Turn 4 — intent `explore_option`, main `social_anxiety`

**User:**
> I don’t know if talking about it will help, but I feel stuck. The laughter in my head keeps coming back even when I try to picture myself speaking, and I’m worried I’ll just give up when it’s time to present the art project. I want to find a way to feel more confident, but I’m not sure where to start.

**Assistant:**
> It sounds like you're feeling really stuck and unsure about how to overcome your anxiety, especially with the upcoming presentation. You've been trying to picture yourself speaking, but the laughter in your head keeps coming back. Would it be helpful to explore some small, manageable steps you could take to build your confidence, or would you rather talk more about what's holding you back from feeling confident about presenting?

**Techniques committed:** `T1 T8 T5 T4`
**Current problems:** social_anxiety, academic_pressure, general_anxiety

<details>
<summary>Inference (what the system inferred from this turn)</summary>

**Attribute entries:**
  - `social_anxiety.triggers` (non_level, conf=`high`): the thought of speaking in front of the class triggers the user's anxiety
  - `social_anxiety.perceived_severity` (level, conf=`medium`): the user perceives their social anxiety as severe, given the significant impact it has on their daily life
  - `academic_pressure.perceived_benefits` (level, conf=`medium`): the user sees presenting the art project as a valuable experience, despite their anxiety

**Attribute-connections:**
  - `conflicting` social_anxiety.triggers ↔ academic_pressure.perceived_benefits (conf=`medium`): the user's social anxiety triggers (fear of being laughed at) conflict with their perceived benefits of presenting the art project (valuable experience)

**Co-occurrence connections:** 3

</details>

<details>
<summary>Recompute (level + TTM updates after this turn)</summary>

**Level updates:**
  - `social_anxiety.perceived_severity`: high → **high** — The user continues to describe their social anxiety as severe, citing its significant impact on daily life, which aligns with previous assessments.
  - `academic_pressure.perceived_benefits`: medium → **medium** — The user consistently views presenting as a valuable experience, despite acknowledging anxiety, which maintains the medium level of perceived benefits.

**TTM updates:**
  - `social_anxiety`: contemplation → **contemplation** — The user acknowledges their social anxiety and its impact but does not express immediate intentions to act or change, which keeps them in the contemplation stage.
  - `academic_pressure`: contemplation → **contemplation** — The user recognizes the benefits of presenting despite their anxiety, indicating awareness of the issue but not yet a clear plan to address it, keeping them in contemplation.
  - `general_anxiety`: contemplation → **contemplation** — There is no new evidence this turn to suggest a change in stage for general anxiety, and previous entries indicate the user feels barriers to success, which keeps them in contemplation.

</details>

<details>
<summary>System intent + reasoning (LLM plan for this turn)</summary>

**system_intent:**

> The user's main problem appears to be social_anxiety. Their current TTM stage appears to be contemplation. Because of this, the response should use T1 Reflection and T8 Change Talk. This technique is appropriate because the user is acknowledging their anxiety and expressing a desire to feel more confident, but is unsure where to start. It should be applied by reflecting the user's feelings of being stuck and their desire to overcome their anxiety, using evidence such as their perceived_severity of social anxiety and their triggers, including public speaking and past experiences of being laughed at. The user also shows high perceived_severity and low self_efficacy. Because of this, the response should use T5 Ask Permission to explore ways to manage their anxiety. This technique should be applied by asking permission to explore small, manageable steps to build confidence, using evidence such as the user's desire to feel more confident and their worry about giving up when it's time to present. The user intent in this turn is to explore options. Because of this, the response should use T4 Open Question to invite the user to consider what small steps they could take to build confidence. Apply it by FIRST acknowledging the user's feelings of being stuck, THEN guiding them to think about small, achievable steps. Relevant connected problems suggest a shared_trigger between social_anxiety and academic_pressure, where public speaking is a trigger for both. This evidence should be used by surfacing the connection between the user's social anxiety and their academic pressure, and exploring how addressing one may impact the other. Overall, the response should try to improve the user's self_efficacy and motivation, while avoiding giving unsolicited advice or overwhelming the user with too many options.

**response_reasoning:**

> I'll open with T1 Reflection of the user's feelings of being stuck and their desire to overcome their anxiety, then use T8 Change Talk to invite the user to consider their own reasons for wanting to feel more confident. I'll also use T5 Ask Permission to explore small, manageable steps to build confidence. I'll skip the connection to general_anxiety this turn, and instead focus on the shared_trigger between social_anxiety and academic_pressure. I'll name the user's goal ('feeling more confident') once to ground the turn.

</details>

### Turn 5 — intent `seek_advice`, main `social_anxiety`

**User:**
> I’m not sure if just talking about it will help. Maybe I need a concrete plan—something I can do right now to feel a bit calmer before I speak, like a quick art exercise or a breathing trick. I’d love to try something practical.

**Assistant:**
> It sounds like you're looking for something practical to help you feel calmer before speaking, and you've mentioned a quick art exercise or a breathing trick as possibilities. Would it be helpful to explore one of these options in more detail, such as a simple breathing exercise you can do right before you present?

**Techniques committed:** `T1 T5 T10`
**Current problems:** social_anxiety, academic_pressure, general_anxiety

<details>
<summary>Inference (what the system inferred from this turn)</summary>

**Attribute entries:**
  - `social_anxiety.perceived_severity` (level, conf=`high`): user sees their social anxiety as a significant obstacle to presenting
  - `social_anxiety.coping_strategies` (non_level, conf=`high`): user is considering a quick art exercise or a breathing trick to calm down
  - `academic_pressure.perceived_benefits` (level, conf=`medium`): user sees presenting as an important part of their academic work

**Attribute-connections:**
  - `shared_trigger` social_anxiety.triggers ↔ academic_pressure.triggers (conf=`high`): the upcoming presentation is triggering both social anxiety and academic pressure

**Co-occurrence connections:** 2

</details>

<details>
<summary>Recompute (level + TTM updates after this turn)</summary>

**Level updates:**
  - `social_anxiety.perceived_severity`: high → **high** — User continues to describe their social anxiety as a significant obstacle, consistent with previous assessments.
  - `academic_pressure.perceived_benefits`: medium → **medium** — User consistently views presenting as a valuable experience, despite their anxiety, with no change in perceived benefits.

**TTM updates:**
  - `social_anxiety`: contemplation → **contemplation** — User acknowledges their social anxiety and is considering coping strategies, indicating continued contemplation.
  - `academic_pressure`: contemplation → **contemplation** — User continues to weigh the importance of presenting despite their anxiety, indicating ongoing contemplation.
  - `general_anxiety`: contemplation → **contemplation** — No new evidence suggests a change in the user's stage regarding general anxiety.

</details>

<details>
<summary>System intent + reasoning (LLM plan for this turn)</summary>

**system_intent:**

> The user's main problem appears to be social_anxiety. Their current TTM stage appears to be contemplation. Because of this, the response should use T1 Reflection and T5 Ask Permission. This technique is appropriate because the user is seeking advice and is unsure about how to overcome their anxiety. It should be applied by reflecting the user's feelings and concerns, using evidence such as their perceived_severity of social anxiety and their triggers, and then asking permission to explore a concrete plan. The user also shows high perceived_severity and low self_efficacy. Because of this, the response should use T10 Action Planning. This technique should be applied by suggesting a small, manageable step the user can take to build their confidence, such as a quick art exercise or a breathing trick, and then asking permission to explore this option further. The user intent in this turn is to seek advice and find a concrete plan to feel calmer before speaking. Because of this, the response should use T5 Ask Permission. Apply it by FIRST acknowledging the user's desire for a practical solution, THEN guiding the user to explore a specific option, such as a breathing exercise. Relevant connected problems suggest a reinforcing relationship between social_anxiety and academic_pressure, with shared triggers. This evidence should be used by surfacing the connection between the user's social anxiety and their academic pressure, and then exploring how a concrete plan can help alleviate both concerns. Overall, the response should try to improve the user's self_efficacy and motivation, while avoiding giving unsolicited advice or overwhelming the user with too many options.

**response_reasoning:**

> I'll open with T1 Reflection of the user's desire for a concrete plan, then use T5 Ask Permission to explore a specific option, such as a breathing exercise. I'll surface the user's perceived_severity of social anxiety and their triggers, and then guide the user to explore a small, manageable step to build their confidence. I'll drop the connection to general_anxiety for now, as it may overwhelm the user.

</details>

## Final graph state (after session 1)

**Problems (3):**

- **academic_pressure** — stage=`contemplation`
  - level attrs:
    - `perceived_severity=high` (1× evidence): user sees the upcoming assembly as highly stressful
    - `perceived_benefits=medium` (4× evidence): user sees presenting as an important part of their academic work

- **social_anxiety** — stage=`contemplation`
  - level attrs:
    - `perceived_severity=high` (4× evidence): user sees their social anxiety as a significant obstacle to presenting
  - non-level attrs:
    - `triggers` (4× evidence): the thought of speaking in front of the class triggers the user's anxiety
    - `coping_strategies` (1× evidence): user is considering a quick art exercise or a breathing trick to calm down

- **general_anxiety** — stage=`contemplation`
  - level attrs:
    - `perceived_barriers=high` (1× evidence): user feels their hearing aid issues and social anxiety are barriers to success


**Edges (3):**

- **academic_pressure ↔ social_anxiety** (weight=1.000, 5× cooc, 5× attr-conn)
  - `reinforcing` triggers ↔ perceived_severity (conf=`medium`, s1t3): the user's social anxiety triggers (laughter) exacerbate their academic pressure (fear of failure)
  - `conflicting` triggers ↔ perceived_benefits (conf=`medium`, s1t4): the user's social anxiety triggers (fear of being laughed at) conflict with their perceived benefits of presenting the art project (valuable experience)
  - `shared_trigger` triggers ↔ triggers (conf=`high`, s1t5): the upcoming presentation is triggering both social anxiety and academic pressure

- **academic_pressure ↔ general_anxiety** (weight=0.487, 3× cooc, 1× attr-conn)
  - `shared_barrier` perceived_barriers ↔ perceived_barriers (conf=`medium`, s1t1): the user's hearing aid issues and social anxiety are shared barriers to success in the academic context

- **general_anxiety ↔ social_anxiety** (weight=0.400, 5× cooc, 0× attr-conn)

## Session summary (written by summary-agent)

> The user primarily discussed their anxiety about an upcoming presentation and assembly, where they have to speak in front of the class and present their group art project, fearing they will be laughed at due to past experiences. They moved into the contemplation stage for academic pressure, general anxiety, and social anxiety. The user expressed a desire to find practical ways to manage their anxiety, such as a quick art exercise or a breathing trick, to feel calmer before speaking. An open thread is the user's intent to explore these practical strategies further to build their confidence.

## Stage transitions (pre → post session)

- **academic_pressure**: `(absent)` → `contemplation`
- **general_anxiety**: `(absent)` → `contemplation`
- **social_anxiety**: `(absent)` → `contemplation`

## Persona inferred at session end

  - **demographics**: student
  - **personality_traits**: anxious, self-critical
  - **hobbies_interests**: art
  - **communication_style**: emotional, expressive
  - **relevant_history**: past experiences of being laughed at
  - **general_behavioral_traits**: emotionally reactive, ruminative

## Mind-3 satisfaction ratings

- Session 1:
  - `empathy`: **5**/5
  - `understanding`: **5**/5
  - `helpfulness`: **5**/5
  - `autonomy_respect`: **5**/5
  - `non_judgment`: **5**/5
  - `willingness_to_continue`: **5**/5

## Mind-2 retrospective labels

- **Session 1:** The user described anxiety about speaking in front of the class after losing their hearing aid, fearing ridicule and feeling exposed. They repeatedly expressed a sense of being trapped by intrusive laughter in their head and uncertainty about how to overcome it. The conversation progressed from a state of feeling stuck and uncertain to a desire for a concrete, practical plan to calm themselves before speaking. The user’s focus remained on social anxiety related to public speaking and the impact of past bullying experiences. By the end of the session, the user expressed a willingness to try a specific coping strategy, indicating a shift toward action.

### Per-problem trajectories (Mind-2)

- **social_anxiety**
  - Session 1: `contemplation` → `preparation` — User acknowledges social anxiety and moves from contemplation to preparation by expressing a desire for a concrete plan.
