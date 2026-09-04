# Pull request review

**PR:** [link or number]  
**Author:**  
**Reviewer:**  
**Area:** [e.g. backend, scripts, infra]

## Intent

- What problem does this change solve?
- Is the scope appropriate (not mixing unrelated refactors)?

## Correctness

- [ ] Logic matches the stated requirements and edge cases are handled.
- [ ] Error paths behave safely; failures do not leak sensitive data.
- [ ] Concurrency, ordering, and idempotency considered where relevant.

## Tests

- [ ] Automated tests cover new behavior and critical regressions.
- [ ] Test names and assertions are clear; no flaky patterns introduced.

## Security & privacy

- [ ] No hardcoded secrets; inputs validated where needed.
- [ ] AuthZ boundaries respected; least privilege for new capabilities.

## Performance & reliability

- [ ] Hot paths avoid unnecessary work (I/O, allocations, N+1 queries).
- [ ] Timeouts, retries, and backoff are sensible if network or external services are involved.

## Maintainability

- [ ] Naming, structure, and comments match project conventions.
- [ ] Duplication avoided or justified; public APIs documented if applicable.

## Observability

- [ ] Logging/metrics/traces appropriate level; no noisy or PII-heavy logs.

## Reviewer notes

- Blocking issues:
- Suggestions (non-blocking):
- Approval: [ ] Approve [ ] Request changes [ ] Comment only
