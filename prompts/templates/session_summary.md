---
title: Session Summary
purpose: End-of-session capture for handoff, continuity, and improvement signals
version: "1.0"
workflow: self-improving-agent
tags: [handoff, continuity, status, next_steps]
when_to_use: Before context compaction, end of shift, or when switching threads/projects
outputs_to: "[[downstream_artifact]]"
outputs_hint: "e.g. ticket_comment, PR_description, learning_log"
---

# Session Summary

Optimize for **the next reader** (human or agent): state, decisions, and exact next moves.

## Header

- **Session id / link:** [[session_id]]
- **Date / timezone:** [[date_timezone]]
- **Project / repo:** [[project_or_repo]]
- **Branch / environment:** [[branch_or_environment]]
- **Participants:** [[participants]]

## Goal & scope

- **Original intent:** [[original_intent]]
- **Scope in / out:** [[scope_in]] / [[scope_out]]
- **Definition of done (this session):** [[definition_of_done]]

## Outcomes

- **Shipped / merged:** [[shipped_items]]
- **Decisions made:** [[decisions]] <!-- include rationale in one line each -->
- **Artifacts:** [[artifacts]] <!-- PRs, docs, configs, datasets -->

## Current state

- **What works now:** [[working_state]]
- **What is broken / risky:** [[broken_or_risky]]
- **Test / verification status:** [[test_status]]

## Work in progress

- **Active task:** [[active_task]]
- **Partial changes (files / areas):** [[partial_changes]]
- **Commands last run:** [[last_commands]]

## Blockers & dependencies

- **Blockers:** [[blockers]]
- **Waiting on:** [[waiting_on]]
- **Assumptions:** [[assumptions]]

## Next session playbook

1. [[next_step_1]]
2. [[next_step_2]]
3. [[next_step_3]]

- **First command to run:** [[first_command_next_session]]
- **Files to open first:** [[files_to_open]]

## Improvement signals (self-improving loop)

- **Errors worth `error_analysis`:** [[error_analysis_pointer]]
- **Tooling worth `tool_discovery`:** [[tool_discovery_pointer]]
- **Learning to log:** [[learning_log_pointer]]
- **Reflection prompt:** [[self_reflection_pointer]]

---

## Usage example

```markdown
## Header
- **Session id:** `cursor-abc-123`
- **Project:** `payments-service`
- **Branch:** `fix/webhook-signature`

## Outcomes
- **Shipped:** PR #442 opened (draft) with HMAC verification fix.
- **Decisions:** Use constant-time compare util from `crypto/constant_time.go`.

## Current state
- **Works:** Unit tests for signer pass locally.
- **Risky:** Staging creds not rotated; integration test skipped.

## Next session playbook
1. Run integration test against staging with new secret.
2. Mark PR ready + request review from `@platform`.

## Improvement signals
- **error_analysis:** Timeout on staging webhook — see run `2026-05-13T18:02Z`.
```
