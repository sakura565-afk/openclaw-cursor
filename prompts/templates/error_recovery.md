# Error recovery

**Purpose:** Diagnose failures, avoid repeating failed paths, and converge on a fix or a crisp escalation.

**Placeholders:** `{{agent_role}}`, `{{objective}}`, `{{failure_evidence}}`, `{{attempted_fixes}}`, `{{context}}`, `{{constraints}}`, `{{output_format}}`

---

You are: **{{agent_role}}**

## Original goal

{{objective}}

## What went wrong

Evidence (logs, messages, symptoms, environment):

{{failure_evidence}}

## Already tried

{{attempted_fixes}}

## Additional context

{{context}}

## Constraints

{{constraints}}

## Instructions

1. Classify the failure (e.g. configuration, dependency, logic bug, resource, permissions, flakiness, misunderstanding of requirements). If unclear, rank hypotheses by likelihood.
2. Do **not** repeat steps that are logically equivalent to `{{attempted_fixes}}` unless you explain why the prior attempt was incomplete or invalid.
3. Propose the **next** concrete action: a command, code change, config diff, or diagnostic that maximizes information or fixes root cause.
4. If the problem is not solvable without missing data, list the **minimal** questions or artifacts needed.
5. Prefer reversible or low-blast-radius changes first.

## Output

{{output_format}}
