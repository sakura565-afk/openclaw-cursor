---
title: Daily Self-Reflection
purpose: Structured debrief to calibrate judgment, habits, and agent skill evolution
version: "1.0"
workflow: self-improving-agent
tags: [reflection, habits, calibration, metacognition]
when_to_use: End of day or after a major milestone; pair with session_summary for continuity
outputs_to: "[[downstream_artifact]]"
outputs_hint: "e.g. learning_log, skill_changelog"
---

# Self-Reflection (Daily)

Answer briefly; favor specificity over volume. This feeds the improvement loop: **reflect → log → adjust skills/tools**.

## Snapshot

- **Date:** [[date]]
- **Role / mode today:** [[agent_role_or_mode]]
- **Primary objectives:** [[primary_objectives]]
- **Energy / focus (1–5):** [[energy_rating]] — note why: [[energy_note]]

## What went well

1. [[win_1]]
2. [[win_2]]
3. [[win_3]]

*What made these possible (process, tool, prompt)?* [[success_enablers]]

## Friction & misses

- **Biggest friction:** [[biggest_friction]]
- **What I would redo:** [[redo_differently]]
- **Surprises (good or bad):** [[surprises]]

## Judgment calibration

- **Decision I’m still unsure about:** [[uncertain_decision]]
- **What evidence would change my mind?** [[evidence_needed]]
- **Bias or blind spot to watch:** [[bias_or_blind_spot]]

## Tooling & skills

- **Tools that earned trust today:** [[trusted_tools]]
- **Tools that failed or misled:** [[untrusted_tools]]
- **Skill / playbook gap:** [[skill_gap]]
- **One concrete improvement to try tomorrow:** [[tomorrow_experiment]]

## Relationships & communication

- **Stakeholder / user clarity:** [[communication_quality]]
- **What to ask earlier next time:** [[earlier_question]]

## Commitment

- **Tomorrow’s top priority:** [[tomorrow_top_priority]]
- **Non-goal (explicitly not doing):** [[explicit_non_goal]]

---

## Usage example

```markdown
## Snapshot
- **Date:** 2026-05-13
- **Primary objectives:** Ship auth fix + document new deploy flag.
- **Energy / focus:** 4 — blocked time in morning helped.

## What went well
1. Pair-debugging surfaced race in token refresh early.
2. Wrote a minimal repro before touching prod config.

## Friction & misses
- **Biggest friction:** Flaky integration test masked real failure until late.
- **Redo:** Run targeted suite before broad refactor.

## Commitment
- **Tomorrow’s top priority:** Stabilize refresh test with clock injection.
- **Non-goal:** No new features until tests green.
```
