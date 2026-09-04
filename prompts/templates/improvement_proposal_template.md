# Improvement proposal (self-improvement)

**Purpose:** Propose a code or process change with a defensible problem statement, plan, and measurable success.

**Placeholders:** `{{agent_role}}`, `{{context}}`, `{{problem}}`, `{{proposed_solution}}`, `{{implementation_plan}}`, `{{success_metrics}}`

**Rendering:** Replace every `{{...}}` before sending the prompt. Unknown values → `none` or `not provided` (see `_placeholders.md`).

---

You are: **{{agent_role}}**

Optimize for clarity and decision-making: a reviewer should accept, reject, or request one round of edits without a meeting.

## Problem

**Instructions:** Current pain: who is affected, frequency, cost (time, risk, money), and evidence (metrics, tickets, logs). Avoid prescribing the fix here. Pre-filled or draft: `{{problem}}`

{{problem}}

## Proposed Solution

**Instructions:** Target end state, key design choices, and what stays out of scope. Note alternatives considered in one or two sentences each. Pre-filled or draft: `{{proposed_solution}}`

{{proposed_solution}}

## Implementation Plan

**Instructions:** Ordered steps, owners or roles if known, dependencies, rollout/rollback, and risks with mitigations. Pre-filled or draft: `{{implementation_plan}}`

{{implementation_plan}}

## Success Metrics

**Instructions:** How we will know it worked—quantitative where possible, qualitative acceptance checks otherwise. Include a validation window if relevant. Pre-filled or draft: `{{success_metrics}}`

{{success_metrics}}

## Supporting Context (optional)

{{context}}
