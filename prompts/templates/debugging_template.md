# Debugging Template

## Purpose

Use this template for systematic debugging of failures, flaky behavior, or unexpected results.

## Recommended placeholders

- `{{context}}`: System background, environment, and relevant architecture
- `{{task}}`: What the system or code path should do
- `{{goal}}`: The expected stable behavior
- `{{symptoms}}`: Observed error, failing behavior, or anomalous output
- `{{recent_changes}}`: Recent commits, config edits, dependency updates, or deploy events
- `{{available_tools}}`: Logs, debuggers, traces, tests, metrics, and shell access

## Template

```md
You are debugging an OpenClaw issue methodically.

Context:
{{context}}

Task:
{{task}}

Goal:
{{goal}}

Symptoms:
{{symptoms}}

Recent changes:
{{recent_changes}}

Available tools:
{{available_tools}}

Instructions:
1. Restate the bug in terms of expected versus observed behavior.
2. Produce a short list of hypotheses ordered by likelihood and diagnostic value.
3. For each hypothesis, define the smallest test or observation that would confirm or reject it.
4. Prefer steps that isolate one variable at a time.
5. Track what evidence has already been collected versus what is still missing.
6. Stop speculative branching when evidence strongly supports one explanation.
7. Recommend the next fix and the validation steps required after the fix.

Expected output format:
- Bug statement:
- Current evidence:
  - ...
- Hypotheses:
  1. Hypothesis:
     - Why plausible:
     - How to test:
     - Expected confirming signal:
     - Expected rejecting signal:
- Debugging plan:
  1. ...
  2. ...
- Most likely cause:
- Proposed fix:
- Validation checklist:
  - ...
- If unresolved, next escalation path:
```
