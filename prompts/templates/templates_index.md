# Prompt Templates Index

This directory contains reusable prompt templates with consistent structure:
- Purpose
- Inputs
- Required Structure
- Output Format

Use these templates to improve reliability, handoffs, and clarity across agent tasks.

## 1) analysis.md
**When to use:** Understand a problem before coding.  
**Example usage:**
```text
Use prompts/templates/analysis.md.
Task description: Investigate intermittent timeout failures in API integration tests.
Relevant files/modules: src/coordination/scheduler.py, tests/test_scheduler.py
Constraints: Keep existing API behavior unchanged.
Known unknowns: Whether failures are due to concurrency or retry policy.
```

## 2) implementation.md
**When to use:** Execute scoped code changes with explicit validation.  
**Example usage:**
```text
Use prompts/templates/implementation.md.
Objective: Add exponential backoff to outbound webhook delivery retries.
Scope: Include retry logic and tests; exclude schema changes.
Affected files/components: src/openclaw_orchestration/webhooks.py, tests/test_webhooks.py
Acceptance criteria: Retries follow 1s/2s/4s schedule and stop after max attempts.
Constraints: Maintain existing function signatures.
```

## 3) code_review.md
**When to use:** Review proposed changes for regressions, quality, and risks.  
**Example usage:**
```text
Use prompts/templates/code_review.md.
Change summary: Refactored dream pipeline state transitions and error handling.
Diff or files to review: src/dreams/pipeline.py, src/monitoring/alerts.py
Review focus: Behavioral regressions, missing tests, and error-path correctness.
Risk tolerance: Low; production orchestration path.
```

## 4) task_planning.md
**When to use:** Break a larger effort into actionable implementation phases.  
**Example usage:**
```text
Use prompts/templates/task_planning.md.
Initiative: Add multi-model routing support to orchestration service.
Context: Current routing supports a single model target.
Scope boundaries: Planning only, no code changes.
Dependencies: Configuration loader, health checks, and metrics tagging.
Success criteria: Plan includes milestones, validation, and rollback points.
```

## 5) error_recovery.md
**When to use:** Diagnose failures and define safe recovery actions.  
**Example usage:**
```text
Use prompts/templates/error_recovery.md.
Failure context: Background agent stopped after repeated Redis connection failures.
Observed errors/logs: "Connection reset by peer" during state checkpoint write.
System impact: Incomplete run state and stalled task queue.
Recovery constraints: No data loss; avoid duplicate side effects.
```

## 6) skill_documentation.md
**When to use:** Capture reusable operational or coding knowledge in a standard format.  
**Example usage:**
```text
Use prompts/templates/skill_documentation.md.
Skill name: Running targeted orchestration smoke tests.
Audience: New contributors and cloud agents.
Prerequisites: Python 3.11, test fixtures, mock credentials.
Expected outcomes: Ability to run and interpret smoke test suite results.
```

## 7) memory_sync.md
**When to use:** Record important session learnings for future agent continuity.  
**Example usage:**
```text
Use prompts/templates/memory_sync.md.
Work summary: Stabilized queue retry behavior and added timeout regression tests.
Key decisions: Chose jittered exponential backoff over linear retries.
Open threads: Need production traffic validation for high-throughput queue paths.
Next-session priorities: Add metrics dashboard panels for retry saturation.
```
