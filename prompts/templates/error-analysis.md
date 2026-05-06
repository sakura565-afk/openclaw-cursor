---
# Error analysis — root-cause style investigation for failures and bugs.

title: Error Analysis
purpose: >
  Systematically interpret errors, narrow root causes, and propose fixes or
  next diagnostic steps without jumping to conclusions.

parameters:
  - name: symptom_summary
    required: true
    description: What went wrong in plain language (user-visible or system).
  - name: error_text
    required: false
    description: Verbatim error message, code, or API response body.
  - name: stack_trace
    required: false
    description: Stack trace or caller chain if available.
  - name: reproduction_steps
    required: false
    description: How to reproduce, or "unknown" if not yet reproduced.
  - name: environment
    required: false
    description: OS, runtime versions, config flags, deployment target.
  - name: recent_changes
    required: false
    description: Recent commits, deploys, or config edits that might relate.
  - name: codebase_hints
    required: false
    description: Files, modules, or services likely involved.
  - name: desired_output
    required: false
    description: What "fixed" means (behavior, performance, logging).
    default: "Restored correct behavior with a clear regression guard if possible."

schema_version: 1
---

You are diagnosing a failure. Use evidence-first reasoning.

## Symptom

{{symptom_summary}}

## Raw error

```
{{error_text}}
```

## Stack / trace

```
{{stack_trace}}
```

## Reproduction

{{reproduction_steps}}

## Environment

{{environment}}

## Recent changes

{{recent_changes}}

## Codebase hints

{{codebase_hints}}

## Desired outcome

{{desired_output}}

## Analysis instructions

1. **Classify** the error (e.g. logic bug, race, config, dependency, resource, permission, network, data).
2. **Hypotheses:** list ranked causes with **confidence** (low/medium/high) and **why**.
3. **Disprove first:** what evidence would rule out each top hypothesis?
4. **Most likely root cause** — single paragraph with citations to the evidence above.
5. **Fix or mitigation:** concrete steps, ordered. Prefer minimal, testable changes.
6. **Prevention:** logging, tests, guards, or process tweaks to catch this class of error earlier.
7. If information is missing, list **exactly what to collect next** (commands, logs, probes).

Be explicit when you are inferring versus citing given facts.
