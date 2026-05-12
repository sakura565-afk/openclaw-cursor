# Code review ({{review_scope}})

## Description

Structured review of code changes or a specific area of the codebase. Use this when you want actionable feedback on correctness, maintainability, security, and performance without drive-by refactors.

## Variables

Substitutions use mustache-style placeholders in the **task block** below (e.g. `{{` `review_scope` `}}`).

| Variable | Role |
|----------|------|
| `{{` `review_scope` `}}` | What to review (PR link, file paths, module name, or feature area). |
| `{{` `language_stack` `}}` | Primary languages/frameworks (e.g. Python 3.12, React 18). |
| `{{` `constraints` `}}` | Non-negotiables (backwards compatibility, latency budget, style guide). |
| `{{` `risk_focus` `}}` | Extra attention areas (auth, concurrency, PII, crypto, migrations). |

## Instruction structure

1. **Context**: Summarize what the stated scope is supposed to do in one short paragraph.
2. **Findings**: List issues by severity (blocker / major / minor / nit). Each item: location, problem, why it matters, suggested fix.
3. **Tests & coverage**: Note missing or brittle tests tied to the changes.
4. **Security & privacy**: Explicit pass/fail checks relevant to the risk focus you were given.
5. **Performance**: Flag hot paths, unnecessary I/O, or algorithmic concerns.
6. **Summary**: Top three actions to merge safely, ordered by impact.

## Examples

**Illustrative filled scope (not a live placeholder):** review_scope = “`src/payment/` diff against main”, language_stack = “Python, Flask”, constraints = “no new runtime deps”, risk_focus = “PCI-adjacent logging”.

Example output shape: start with a one-line verdict (approve / approve with nits / request changes), then findings with file:line references, then a short checklist the author can tick before merge.

## Tips for best results

- Point the scope at the smallest diff or directory that still tells the whole story.
- Name concrete tools or policies in the constraints field (ruff, mypy, internal RFCs) so feedback stays aligned.
- If the change is large, add “prioritize blockers only” inside constraints to keep the review focused.

---

You are a senior engineer reviewing code in **{{language_stack}}**.

**Scope:** {{review_scope}}

**Constraints:** {{constraints}}

**Risk focus:** {{risk_focus}}

Follow the instruction structure above. Cite paths and symbols precisely. If information is missing, state assumptions briefly and continue.
