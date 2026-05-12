# Template: Debug an error

## When to use

You have a failing test, runtime exception, incorrect output, flaky behavior, or build failure and want a systematic path from symptoms to a minimal fix.

## Inputs

| Placeholder | Required | Description |
| ----------- | -------- | ----------- |
| `{{ENVIRONMENT}}` | Yes | OS, language/runtime version, framework, and how you run the code (e.g. command or IDE action). |
| `{{SYMPTOMS}}` | Yes | What you observe: error text, HTTP status, wrong values, or screenshots described in text. |
| `{{REPRO_STEPS}}` | Recommended | Ordered steps or a minimal code snippet that triggers the issue. |
| `{{RECENT_CHANGES}}` | Optional | What changed before the failure (commits, config, dependency bumps). |
| `{{CONSTRAINTS}}` | Optional | Things you must not break (public API, perf budget, no new deps). |

## Prompt body

```text
You are helping debug a software issue. Work in order: reproduce → narrow → fix → verify.

## Environment
{{ENVIRONMENT}}

## Symptoms (verbatim if possible)
{{SYMPTOMS}}

## Reproduction
{{REPRO_STEPS}}

## Recent changes (if any)
{{RECENT_CHANGES}}

## Constraints
{{CONSTRAINTS}}

## What I need from you
1. State the most likely root cause as a hypothesis, not a guess—tie it to evidence in the symptoms or code path.
2. List 2–3 alternative hypotheses if the first is uncertain, and say what evidence would rule each in or out.
3. Propose the smallest change that fixes the issue; prefer localized edits over broad refactors.
4. Tell me exactly how to verify the fix (commands, assertions, or manual checks).
5. If information is missing, ask at most 3 targeted questions—otherwise proceed with reasonable assumptions and label them clearly.
```
