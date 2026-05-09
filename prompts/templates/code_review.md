# Code review

Use this template when you want a structured review of a change set or specific files before merge.

## Scope

- **Branch / PR / commit:** <!-- Reference as appropriate. -->
- **Files or areas:** <!-- Paths or feature boundaries. -->
- **Reviewer focus:** <!-- e.g. security, API design, performance, tests. -->

## Intent

- **What this change is supposed to achieve:** <!-- User-visible or internal goal. -->
- **Non-goals:** <!-- What is explicitly out of scope. -->

## Review checklist

Answer concisely for each item: OK, issue, or N/A.

- Correctness and edge cases
- Tests and coverage of critical paths
- Error handling and logging
- Security (inputs, secrets, permissions)
- Performance and resource use
- API or contract compatibility (breaking changes?)
- Readability and consistency with the codebase
- Documentation updates where users or operators need them

## Output format

1. **Summary** — overall recommendation (approve / approve with nits / request changes).
2. **Findings** — ordered by severity (blocker, major, minor, suggestion).
3. **Questions** — only those that affect the review outcome.
