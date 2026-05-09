# Error fix

Use this template when you need to diagnose and fix a failure (tests, runtime, build, or incorrect behavior).

## Context

- **Symptom:** <!-- What fails or behaves wrong? Paste errors, stack traces, or describe the bug. -->
- **Where:** <!-- File paths, commands, URLs, or steps to reproduce. -->
- **Environment:** <!-- OS, Python/node version, branch, flags — only what matters. -->
- **Expected vs actual:** <!-- One sentence each. -->

## Goal

Fix the root cause (not only silence the error). Preserve intended behavior unless the spec was wrong; if ambiguous, state assumptions briefly.

## Constraints

- Prefer minimal, targeted changes; avoid unrelated refactors.
- Run or add tests that prove the fix when practical.
- Do not remove useful diagnostics without replacing them.

## What to deliver

1. Short diagnosis (likely cause and why).
2. Code (or config) changes with clear rationale.
3. How you verified the fix (commands run, scenarios checked).
