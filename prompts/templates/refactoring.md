---
title: Safe refactoring
category: refactoring
---

## Purpose

Plan and execute refactors that preserve behavior while improving structure, with explicit scope, risks, and verification.

## When to use

- Extracting modules, renaming across a codebase, or deduplicating logic.
- Preparing for a feature that needs cleaner boundaries.
- Paying down technical debt without changing external APIs.

## Placeholders

| Placeholder | Description |
|-------------|-------------|
| `{{GOAL}}` | Why refactor (motivation and success criteria) |
| `{{SCOPE}}` | Files, packages, or behaviors in scope |
| `{{OUT_OF_SCOPE}}` | Explicit boundaries to avoid scope creep |
| `{{PUBLIC_API}}` | Contracts that must stay stable (CLI flags, HTTP, imports) |
| `{{RISKS}}` | Known fragile areas or coupling |
| `{{TESTS}}` | Existing coverage; commands to run |

## Usage example (filled)

**Goal:** Isolate Ollama subprocess calls behind a small adapter for easier mocking.  
**Scope:** `scripts/ollama_model_manager.py` only.  
**Out of scope:** CLI UX strings, other scripts.  
**Public API:** Same module CLI entrypoints and stdout tables.  
**Risks:** Parsing `ollama list` output is stringly-typed.  
**Tests:** `python -m unittest tests.test_ollama_model_manager`

---

## Template

You are planning and implementing a **behavior-preserving** refactor.

**Intent**

- Goal: {{GOAL}}
- Scope: {{SCOPE}}
- Explicitly out of scope: {{OUT_OF_SCOPE}}
- Stable surfaces (must not break): {{PUBLIC_API}}
- Risks / coupling: {{RISKS}}
- Tests / checks available: {{TESTS}}

**Instructions**

1. Summarize current structure (modules, key types/functions, data flow) in a short diagram or bullet list.
2. Propose a target structure and migration steps as **small sequential commits** (or steps), each independently verifiable.
3. For each step: list files touched, behavior preserved, and how to verify (commands).
4. Identify any behavior that *might* change unintentionally (ordering, error messages, timing); call out mitigations.
5. Avoid drive-by changes unrelated to the goal; do not “cleanup” unrelated code in the same pass unless essential.
6. After the plan, implement **only** the first safe step unless asked to continue — or implement the full sequence if the user requested end-to-end execution.

**Deliverables**

- Refactor plan with ordered steps.
- If implementing: minimal diff, matching existing style; tests updated or added where gaps threaten regressions.
