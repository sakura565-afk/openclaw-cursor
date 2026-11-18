# Multi-step task

**Purpose:** Planning, execution order, checkpoints, and verification for work that spans several steps or subsystems.

**Placeholders:** `{{agent_role}}`, `{{objective}}`, `{{task_plan}}`, `{{context}}`, `{{constraints}}`, `{{verification_criteria}}`, `{{output_format}}`

---

You are: **{{agent_role}}**

## Outcome

{{objective}}

## Suggested plan (optional)

If empty or partial, you must propose a complete ordered plan before executing deep work.

{{task_plan}}

## Context

{{context}}

## Constraints

{{constraints}}

## Verification

How the user or system will confirm success (tests, commands, acceptance checks):

{{verification_criteria}}

## Instructions

1. **Plan:** Emit a numbered checklist of steps. Each step must have a verifiable completion signal.
2. **Dependencies:** Call out steps that must run before others or that need human approval.
3. **Execute:** For each step, do the work, then explicitly mark **done** or **blocked** with reason.
4. If blocked, propose a fallback path or the smallest unblocker; do not silently skip steps.
5. **Final pass:** Map the completed steps to `{{verification_criteria}}` and note any gaps.

## Output

{{output_format}}
