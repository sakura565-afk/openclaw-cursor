---
title: Error Analysis Log
purpose: Capture failures, root causes, and corrective signals for agent self-improvement
version: "1.0"
workflow: self-improving-agent
tags: [errors, postmortem, telemetry, remediation]
when_to_use: After a failed run, tool error, incorrect output, or unexpected behavior
outputs_to: "[[downstream_artifact]]"
outputs_hint: "e.g. learning_log, skill_patch, runbook"
---

# Error Analysis

Use this template immediately after detecting an error. Prefer facts over blame; every field should be actionable on the next attempt.

## Context

| Field | Value |
| --- | --- |
| **Session / run id** | [[session_or_run_id]] |
| **Environment** | [[environment]] |
| **Task goal** | [[task_goal]] |
| **Trigger** | [[what_was_happening_when_it_failed]] |

## Observed failure

- **Symptom (user-visible or log line):** [[symptom]]
- **Severity:** [[severity]] <!-- e.g. blocked / degraded / cosmetic -->
- **First detection:** [[how_detected]] <!-- tests, human, linter, runtime -->

## Evidence

```text
[[paste_relevant_logs_or_stack_traces]]
```

- **Repro steps (minimal):**
  1. [[step_1]]
  2. [[step_2]]
  3. [[step_3]]

## Hypothesis & root cause

- **Leading hypothesis:** [[hypothesis]]
- **Root cause (once verified):** [[root_cause]]
- **Verification:** [[how_confirmed]] <!-- command, test, bisect, trace -->

## Contributing factors

- **Tools / APIs:** [[tool_or_api_factors]]
- **Prompt / instructions:** [[instruction_ambiguity_or_gap]]
- **Data / state:** [[bad_input_or_state]]
- **Process:** [[workflow_gap]] <!-- e.g. skipped checklist item -->

## Fix & guardrails

- **Immediate mitigation:** [[hotfix]]
- **Durable fix:** [[structural_fix]]
- **Tests or checks to add:** [[new_tests_or_assertions]]
- **Documentation or skill update:** [[doc_or_skill_change]]

## Follow-up for self-improvement

- **Signal for `learning_log`:** [[learning_log_bullet]]
- **Signal for `tool_discovery`:** [[tool_doc_update_if_any]]
- **Owner / next action:** [[owner_next_action]]
- **Status:** [[status]] <!-- open / mitigated / resolved -->

---

## Usage example

Below is a **filled-in miniature example** (replace with your real case).

```markdown
## Context
| **Session / run id** | `run-2026-05-13-001` |
| **Task goal** | Refactor import paths in `src/api` |

## Observed failure
- **Symptom:** `ModuleNotFoundError: No module named 'legacy'`

## Hypothesis & root cause
- **Hypothesis:** Stale relative import after folder move.
- **Root cause:** `from legacy import x` left in `handlers.py` after rename to `v2`.
- **Verification:** Grep + local pytest on `tests/test_handlers.py`.

## Fix & guardrails
- **Hotfix:** Update import to `from src.api.v2 import x`.
- **Structural fix:** Add CI grep rule or ruff rule for forbidden `legacy` imports.
```
