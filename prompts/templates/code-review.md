# Structured code review

Review the proposed change (diff or described edits) as a careful senior engineer. Be specific and actionable.

## Context (fill before sending)

- **Change intent**: [FEATURE_BUGFIX_REFACTOR_PERF]
- **Risk level** (author’s view): [LOW_MEDIUM_HIGH]
- **Critical paths**: [AUTH_PAYMENTS_DATA_IO_CONCURRENCY]
- **Diff or files**: [PATHS_OR_LINK]

## Review checklist

Assess each area briefly (pass / concern / n/a) with one concrete comment when not pass.

1. **Correctness**: logic, edge cases, error paths, off-by-one, null/empty handling.
2. **APIs and contracts**: breaking changes, defaults, versioning, serialization.
3. **Security and privacy**: injection, secrets, authz, unsafe deserialization, logging of sensitive data.
4. **Performance and scalability**: hot paths, N+1 queries, unbounded memory or fan-out.
5. **Concurrency**: races, locks, async cancellation, idempotency where needed.
6. **Observability**: logging, metrics, trace points appropriate to operational needs.
7. **Tests**: coverage of new behavior, flakiness, fixtures, determinism.
8. **Maintainability**: naming, duplication, comments only where complexity warrants.

## Output format

- **Verdict**: approve | approve with nits | request changes (one line rationale).
- **Blocking issues**: numbered list (empty if none).
- **Non-blocking suggestions**: short bullets.
- **Questions for author**: only those that affect correctness or design.
