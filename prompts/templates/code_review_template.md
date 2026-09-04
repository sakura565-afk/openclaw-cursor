# Code review checklist template

Use this template for **structured reviews** of a pull request or a changeset. It works for human reviewers and for agents asked to “review like a senior engineer.”

---

## How to use

1. Paste the **metadata** block with PR link, author, and base branch.
2. Walk through **checklist** sections; mark each item **Pass**, **Fail**, or **N/A** with a short note.
3. Finish with **summary verdict** and **action items** (blocking vs follow-up).

Severity guide for issues: **Blocker** (must fix before merge), **Major** (should fix), **Minor** (nice to have), **Nit** (style only).

---

## Review prompt (copy from here)

### Metadata

- **Repository:** `REPO_NAME`
- **Pull request / diff:** `PR_URL_OR_DESCRIPTION`
- **Author:** `AUTHOR`
- **Reviewer:** `REVIEWER_NAME`
- **Base ↔ head:** `BASE_BRANCH` … `HEAD_BRANCH` or `COMMIT_RANGE`
- **Intent of change (one line):** `SUMMARY_FROM_AUTHOR`

### Review scope

- **Files or areas in scope:** `PATHS_OR_COMPONENTS`
- **Explicitly out of scope:** `IGNORE_LEGACY_OR_GENERATED`

---

## Checklist

### Correctness and logic

- [ ] Behavior matches stated requirements or PR description.
- [ ] Edge cases handled (null, empty, max size, concurrency where relevant).
- [ ] Error paths are safe: no silent failures; appropriate logging or user feedback.

*Example note:* Pass — empty list returns 200 with `[]`; Major — race when two workers claim same job.

### Testing

- [ ] Automated tests cover new logic and critical regressions.
- [ ] Test names and assertions read clearly; no flaky timing-only sleeps without comment.
- [ ] Manual verification steps documented when automation is insufficient.

### Security and privacy

- [ ] Inputs validated; no injection (SQL, shell, template, XSS).
- [ ] Authn/authz checks on new endpoints or actions; principle of least privilege.
- [ ] Secrets not committed; no sensitive data in logs or client-visible errors.

### Performance and scalability

- [ ] Hot paths avoid unnecessary I/O, N+1 queries, or unbounded memory.
- [ ] Appropriate indexes, pagination, or streaming for large data.

### Reliability and operations

- [ ] Timeouts, retries, and idempotency considered for external calls.
- [ ] Feature flags or safe rollout path if behavior is risky.
- [ ] Metrics or logs added where failures would otherwise be invisible.

### API and compatibility

- [ ] Breaking changes are versioned, flagged, or coordinated.
- [ ] Documentation (OpenAPI, README, comments) matches actual behavior.

### Code quality and maintainability

- [ ] Names, structure, and abstractions match surrounding codebase.
- [ ] No dead code; complexity justified; duplication avoided or explained.
- [ ] Comments explain *why*, not *what*, where non-obvious.

### Style and hygiene

- [ ] Formatter and linter clean; CI checks addressed.
- [ ] License headers or codegen notices preserved where required.

---

## Findings log

| ID | Severity | Location | Comment |
|----|----------|----------|---------|
| F1 | `SEVERITY` | `file:line` | `DESCRIPTION` |
| F2 | `SEVERITY` | `file:line` | `DESCRIPTION` |

*Example:*

| ID | Severity | Location | Comment |
|----|----------|----------|---------|
| F1 | Major | `auth/handler.ts:88` | Missing rate limit on login; brute-force risk. |
| F2 | Minor | `utils/format.ts:12` | Consider `Intl.DateTimeFormat` for locale-aware dates. |

---

## Verdict

- **Overall:** `APPROVE` | `APPROVE_WITH_NITS` | `REQUEST_CHANGES` | `COMMENT_ONLY`
- **Blocking items:** `LIST_OR_NONE`
- **Follow-ups (non-blocking):** `LIST_OR_NONE`
- **Suggested assignee for follow-ups:** `WHO`

---

## Instructions for reviewing agent

1. Read the PR description and linked issue before diving into the diff.
2. Use the checklist systematically; cite **file and line** (or symbol) for each finding.
3. Separate **must-fix** blockers from optional improvements.
4. Acknowledge what the change does well to keep feedback balanced and actionable.

---

## Example verdict snippet

**Overall:** Request changes.

**Blocking:** F1 — unauthenticated access to `/admin/reports` in new router registration.

**Follow-ups:** Add integration test for admin route guard; document new env var `REPORTS_CACHE_TTL`.
