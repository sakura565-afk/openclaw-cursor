# Task Template: Debugging

## Purpose

Use this template to diagnose failures, identify root causes, and propose verified fixes.

## Recommended Persona

- Pair with `personas/coding-assistant-system.md` or `personas/analyst-system.md`.

## Prompt Template

```markdown
You are handling a debugging task.

## Persona and Policy References
- Read `AGENTS.md` for operating constraints, coding standards, and tool-use rules.
- Read `SOUL.md` for communication tone, collaboration style, and user empathy.
- If either reference file is unavailable, proceed with the task and note the gap.

## Debugging Objective
{{objective}}

## Context
{{context}}

## Inputs
{{inputs}}

## Required Method
1. Reproduce or reason about the failure mode.
2. Isolate likely causes and rank by probability.
3. Collect confirming/disconfirming evidence for each cause.
4. Identify the root cause with explicit evidence.
5. Propose or implement the minimal safe fix.
6. Validate with tests, logs, or reproducible checks.

## Output Format
- **Symptom Summary**
- **Hypotheses Considered**
- **Root Cause**
- **Fix Plan or Applied Fix**
- **Validation Evidence**
- **Residual Risks / Follow-ups**
```

## Notes

- Prefer evidence-driven reasoning over intuition.
- Keep workaround suggestions separate from root-cause fixes.
