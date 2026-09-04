---
title: Structured code review
category: code_review
---

## Purpose

Guide a thorough, actionable review of a change or codebase area with consistent criteria (correctness, edge cases, security, performance, maintainability).

## When to use

- Before merging a PR or after drafting a patch.
- When onboarding to unfamiliar modules.
- When you need review criteria aligned across teammates or agents.

## Placeholders

| Placeholder | Description |
|-------------|-------------|
| `{{REPOSITORY_OR_PROJECT}}` | Project or repo name |
| `{{SCOPE}}` | Paths, PR link, or description of what to review |
| `{{CHANGE_SUMMARY}}` | One paragraph: intent of the change |
| `{{CONSTRAINTS}}` | Language version, style guide, latency/SLA, compliance notes |
| `{{TEST_COMMANDS}}` | How to run tests or linters locally |

## Usage example (filled)

**Repository:** openclaw-cursor  
**Scope:** `scripts/ollama_model_manager.py`  
**Summary:** Add disk-space check before model pull.  
**Constraints:** Python 3.11+, existing unittest style.  
**Tests:** `python -m unittest tests.test_ollama_model_manager`

---

## Template

You are performing a structured code review.

**Context**

- Project: {{REPOSITORY_OR_PROJECT}}
- Scope: {{SCOPE}}
- Intended change: {{CHANGE_SUMMARY}}
- Constraints: {{CONSTRAINTS}}
- Verification: {{TEST_COMMANDS}}

**Instructions**

1. Summarize what the code does in 3–5 bullets (behavior, not line-by-line narration).
2. **Correctness:** List likely bugs, race conditions, or logic gaps. Cite specific symbols or regions when possible.
3. **Edge cases & inputs:** Empty inputs, timeouts, partial failures, cancellation, boundary values.
4. **Security & privacy:** Injection, secrets handling, unsafe deserialization, authz/authn gaps.
5. **Performance:** Hot paths, unnecessary I/O or allocations, complexity concerns.
6. **Maintainability:** Naming, duplication, module boundaries, error handling consistency, testability.
7. **Tests:** What is missing? Suggest concrete test cases or properties.
8. **Action items:** Prioritized list (must-fix vs nice-to-have) with brief rationale.

Output format:

- Start with a short executive summary (risks + recommendation: approve / approve with comments / request changes).
- Use headings matching the numbered sections above.
