# TTM + HBM → MI-technique candidate rules (§6.6, §12.1)

Pure rules consumed by `mi_selector.py`. Output is a **candidate list** of
`{technique_id, technique_label}` only — no usage-guidance strings. The merged
instruction+response call (§18.4) composes the technique(s) inside the
instruction.

## 12 techniques (T1–T12)

| id | label |
|---|---|
| T1 | Reflection |
| T2 | Affirmation |
| T3 | Summary |
| T4 | Open Question |
| T5 | Ask Permission |
| T6 | Options Menu |
| T7 | EPE (Elicit–Provide–Elicit) |
| T8 | Change Talk |
| T9 | Confidence Builder |
| T10 | Action Planning |
| T11 | Coping / Relapse Plan |
| T12 | Normalize / Reframe |

## Base set from main problem's TTM stage

| stage | base candidates |
|---|---|
| precontemplation | T1, T3, T12 |
| contemplation | T1, T5, T8 |
| preparation | T5, T6, T10 |
| action | T2, T10, T11 |
| maintenance | T2, T11, T12 |

## HBM modifiers — behavioral attributes detected in last N turns only

For each AttributeNode↔ProblemNode edge with new evidence in the last N turns,
check `attr_type` + `current_level`:

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

Special constraint: if `self_efficacy=low` AND T10 is in candidates, the
instruction layer (§18.4) must scope T10 to a **micro-step** — the selector
just notes both techniques in the candidate list; the instruction writer reads
this modifier note and enforces the micro-step wording.

## Intent gate (main problem only)

If `user_intent ∈ {vent, seek_validation}`, restrict final candidate set to
the reflective/affirming subset:

- T1 Reflection
- T2 Affirmation
- T3 Summary
- T12 Normalize / Reframe

(Other techniques dropped, even if added by TTM or HBM rules.)

## Safety fallback

If the rule pipeline emits an empty candidate list, fall back to `{T1, T4}`.
