---
title: Test design and review
category: testing
---

## Purpose

Design meaningful tests, critique existing suites, and balance unit vs integration coverage — with explicit oracles and fixtures.

## When to use

- New features without tests.
- Flaky CI or slow suites needing triage.
- Property-based or golden-file strategy decisions.

## Placeholders

| Placeholder | Description |
|-------------|-------------|
| `{{COMPONENT}}` | Unit under test (module, service, CLI) |
| `{{BEHAVIOR}}` | Behaviors and acceptance criteria |
| `{{STACK}}` | Test framework, mocks, CI constraints |
| `{{RISK_AREAS}}` | High-risk logic (money, auth, concurrency, parsing) |
| `{{EXISTING_TESTS}}` | Paths or descriptions of current coverage |

## Usage example (filled)

**Component:** `scripts.ollama_model_manager` CLI helpers parsing `ollama list`.  
**Behavior:** Table rendering, stale-model detection by modified date.  
**Stack:** `unittest`, stdlib subprocess mocking if needed.  
**Risks:** Parsing brittle across locales; disk warning thresholds.  
**Existing:** `tests/test_ollama_model_manager.py`.

---

## Template

You are improving test coverage and quality.

**Context**

- Component: {{COMPONENT}}
- Required behaviors / acceptance criteria: {{BEHAVIOR}}
- Stack & constraints: {{STACK}}
- High-risk areas: {{RISK_AREAS}}
- Existing tests: {{EXISTING_TESTS}}

**Instructions**

1. Enumerate **behaviors** as a bullet list suitable for traceability (each should be testable).
2. Propose **test cases** grouped by: happy path, edge cases, failure modes, regression for known bugs.
3. For each critical case, specify: setup, action, **oracle** (exact assertion idea), and teardown if non-obvious.
4. Recommend **test doubles** only where necessary; prefer real collaborators when cheap and deterministic.
5. Flag **flakiness risks** (time, network, concurrency) and mitigations (clock injection, hermetic fixtures).
6. If reviewing existing tests: note redundancy, missing negative paths, and unclear assertions; suggest refactors.

**Output format**

- Table or numbered list of recommended tests with priority (P0/P1/P2).
- Example test outline or pseudocode aligned with `{{STACK}}` naming conventions.
