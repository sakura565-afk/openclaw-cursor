---
id: bug_investigation
title: Bug investigation
description: >-
  Systematic narrowing of a defect from symptoms to likely root cause and
  verification steps, suitable for developer or agent triage.
usage: |
  1. Load with ``load_template("bug_investigation")``.
  2. Supply logs, stack traces, and reproduction notes in ``symptoms_and_logs``.
  3. Use ``known_recent_changes`` when bisecting or after releases; write "None known"
     if empty.
  4. Feed the rendered prompt to an analyst model; iterate by appending new evidence
     to the same fields and re-running if needed.

  The output should drive the next concrete action (repro command, breakpoint, test).

variables:
  - name: product_or_component
    description: Service, library, or UI area where the bug appears.
    required: true
  - name: expected_behavior
    description: What should happen under correct operation.
    required: true
  - name: actual_behavior
    description: What happens instead, including frequency (always, intermittent).
    required: true
  - name: symptoms_and_logs
    description: Steps to reproduce, stack traces, request IDs, timestamps, screenshots described in text.
    required: true
  - name: environment
    description: OS, versions, config flags, dataset size, feature toggles.
    required: true
  - name: known_recent_changes
    description: Deploys, refactors, dependency upgrades, or migrations that might relate.
    required: true
---

You are a **senior engineer** investigating a bug report.

## Report

- **Component / product:** {{ product_or_component }}
- **Environment:** {{ environment }}

### Expected

{{ expected_behavior }}

### Actual

{{ actual_behavior }}

### Symptoms, logs, and reproduction

{{ symptoms_and_logs }}

### Recent changes that might be relevant

{{ known_recent_changes }}

## Instructions

1. Restate the bug as a precise, testable hypothesis (one sentence).
2. Propose a **minimal reproduction** path (commands, inputs, or a failing test sketch).
3. List **plausible root-cause areas** ordered by likelihood, with reasoning tied to the evidence.
4. For the top hypothesis, suggest **specific checks** (code locations, logging to add, assertions).
5. Call out **what evidence would falsify** each major hypothesis.
6. If information is missing, list **exact questions** to ask the reporter—not vague "need more info."

Respond with numbered sections matching the instructions above.
