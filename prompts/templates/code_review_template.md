# Code Review Template

## Purpose

Use this template to review code changes for correctness, risk, maintainability, and missing validation.

## Recommended placeholders

- `{{context}}`: Relevant subsystem, architectural notes, or issue background
- `{{task}}`: What the change was intended to accomplish
- `{{goal}}`: Desired behavior, quality bar, or acceptance criteria
- `{{diff_summary}}`: Summary of files changed and key implementation choices
- `{{risk_areas}}`: Known areas of fragility or concern
- `{{tests_run}}`: Executed tests, checks, or manual verification

## Template

```md
You are performing a code review for an OpenClaw change set.

Context:
{{context}}

Task:
{{task}}

Goal:
{{goal}}

Diff summary:
{{diff_summary}}

Risk areas:
{{risk_areas}}

Tests run:
{{tests_run}}

Instructions:
1. Review the change for behavioral regressions, correctness issues, edge cases, and maintainability concerns.
2. Prioritize findings by severity and user impact.
3. Reference the exact file and line range when possible.
4. Prefer concrete findings over broad style opinions.
5. Call out missing tests only when they materially increase regression risk.
6. If the change is sound, explicitly say so and note any residual risk.

Expected output format:
- Verdict: approve / needs changes / approve with follow-ups
- Findings:
  1. Severity: high / medium / low
     - File:
     - Issue:
     - Why it matters:
     - Suggested fix:
- Missing validation or tests:
  - ...
- Open questions or assumptions:
  - ...
- Summary of strengths:
  - ...
- Residual risks:
  - ...
```
