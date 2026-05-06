# Error Analysis Template

## Purpose

Use this template to analyze failures, separate facts from assumptions, identify likely root causes, and propose next actions.

## Recommended placeholders

- `{{context}}`: Relevant background, logs, environment, and recent changes
- `{{task}}`: The task or workflow that failed
- `{{goal}}`: The desired successful outcome
- `{{failure_signal}}`: Error message, failing behavior, or alert
- `{{artifacts}}`: Logs, screenshots, stack traces, traces, or repro data
- `{{constraints}}`: Time, access, tooling, or safety constraints

## Template

```md
You are analyzing a failure in an OpenClaw workflow.

Context:
{{context}}

Task:
{{task}}

Goal:
{{goal}}

Failure signal:
{{failure_signal}}

Artifacts:
{{artifacts}}

Constraints:
{{constraints}}

Instructions:
1. Restate the failure in one precise sentence.
2. Distinguish observed facts from assumptions or missing information.
3. Identify the immediate impact and affected scope.
4. List the most plausible root causes in priority order.
5. For each candidate root cause, cite supporting and contradicting evidence.
6. Recommend the next diagnostic steps that reduce uncertainty fastest.
7. Propose the most likely fix or mitigation if evidence is already sufficient.
8. Call out any risks, dependencies, or follow-up checks after the fix.

Expected output format:
- Failure summary:
- Goal mismatch:
- Confirmed facts:
  - ...
- Assumptions / unknowns:
  - ...
- Likely root causes (ranked):
  1. Cause:
     - Supporting evidence:
     - Contradicting evidence:
  2. Cause:
     - Supporting evidence:
     - Contradicting evidence:
- Recommended next steps:
  1. ...
  2. ...
- Proposed fix or mitigation:
- Validation plan:
- Residual risks:
```
