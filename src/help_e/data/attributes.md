# HELP-E behavioral attribute inventory (§8.3)

Flat `attr_type` list. No subtypes, no canonical `value` vocabulary —
`(attr_type, value)` forms AttributeNode identity. Evidence lives on the
AttributeNode ↔ ProblemNode edge in an append-only `information_stack` with a
rolling `current_level ∈ {low, moderate, high}`.

| attr_type | meaning |
|---|---|
| perceived_severity | how serious the user thinks the problem is |
| perceived_susceptibility | how likely the user thinks the problem is to keep affecting them |
| perceived_benefits | what good the user thinks change would bring |
| perceived_barriers | what the user thinks is in the way of change |
| self_efficacy | how capable the user feels of making the change |
| cues_to_action | events / reminders pushing them to act |
| motivation | desire / energy to work on this (change-talk intensity) |
| coping_strategies | what they're currently trying |
| past_attempts | what they've tried before and how it went |
| triggers | what sets the problem off or makes it worse |
| goal | what "solved" looks like for the user (main problem only → system_intent) |

# Persona fields (§8.4)

Stable. Session-end update only.

| field | meaning |
|---|---|
| demographics | age_range, gender, occupation, education, life_stage |
| personality_traits | ~3 dominant traits |
| core_values | what matters to the user |
| core_beliefs | stable self/world beliefs |
| support_system | who's around (family, friends, partner, community, none) |
| hobbies_interests | activities for meaning or fun |
| communication_style | brief, emotional, intellectualizing, guarded, etc. |
| relevant_history | past major events or prior therapy that shape approach |

# User intent enum (§6.2)

`vent | seek_validation | seek_advice | explore_option | report_progress | check_in | other`

# TTM stages

`precontemplation | contemplation | preparation | action | maintenance`
