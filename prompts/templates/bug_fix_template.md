# Bug fix task template

Use this template when you want an agent (or a human) to **report, diagnose, and fix** a defect in a systematic way. Copy the block below, replace every placeholder, and remove sections that do not apply.

---

## How to use

1. Fill in **Context** so the executor knows the product, version, and environment.
2. Describe **Symptoms** with observable facts, not interpretations.
3. Add **Reproduction** steps that anyone can follow; include data fixtures or credentials only via secure channels.
4. List **Expected vs actual** behavior explicitly.
5. Under **Constraints**, note deadlines, risk tolerance, and what must not change.
6. Send the completed prompt to your coding agent or assignee.

**Placeholder convention:** Text in `ALL_CAPS` or angle brackets like `<optional>` should be replaced. Example values appear in *italics* where helpful.

---

## Task prompt (copy from here)

### Context

- **Product or repository:** `PRODUCT_OR_REPO` *(example: acme-api)*
- **Version / commit / branch:** `VERSION_OR_SHA` *(example: v2.3.1 on branch fix/auth-timeout)*
- **Environment:** `ENVIRONMENT` *(example: staging, Linux, Node 20)*
- **Related links:** `ISSUE_URL_OR_DOCS` *(example: https://example.com/issues/123)*

### Summary

`ONE_LINE_SUMMARY_OF_THE_BUG`

*Example: Login returns 500 when the password contains a Unicode apostrophe.*

### Symptoms

- **What users or systems observe:** `OBSERVABLE_SYMPTOMS`
- **Scope:** `WHO_IS_AFFECTED` *(example: all EU tenants; only Safari 17)*
- **Frequency:** `HOW_OFTEN` *(example: every request; ~1% of jobs)*
- **First seen / regression:** `TIMELINE` *(example: since deploy 2026-05-01)*

### Steps to reproduce

1. `STEP_1`
2. `STEP_2`
3. `STEP_3`

*Example:*

1. Create user with email `test+unicode@example.com` and password `café`.
2. Submit login form on `/login`.
3. Observe HTTP 500 and stack trace in server logs.

### Expected behavior

`DESCRIBE_CORRECT_BEHAVIOR`

*Example: Login succeeds; session cookie is set; user lands on `/dashboard`.*

### Actual behavior

`DESCRIBE_WHAT_HAPPENS_INSTEAD`

*Example: HTTP 500; log shows `TypeError` in password normalization.*

### Evidence

- **Logs / traces / screenshots:** `ATTACH_OR_REFERENCE`
- **Sample payloads or IDs (non-secret):** `SANITIZED_SAMPLES`

### Suspected area (optional)

`FILES_MODULES_OR_SUBSYSTEMS` — mark as hypothesis, not fact.

*Example: Hypothesis — `src/auth/normalizePassword.ts` mishandles Unicode NFC.*

### Acceptance criteria

The fix is done when:

- [ ] `CRITERION_1` *(example: Login works for passwords containing combining characters.)*
- [ ] `CRITERION_2` *(example: New regression test covers the case.)*
- [ ] `CRITERION_3` *(example: No new linter errors; existing test suite passes.)*

### Constraints

- **Must not break:** `COMPATIBILITY_OR_API_GUARANTEES`
- **Out of scope:** `WHAT_NOT_TO_TOUCH`
- **Deadline / priority:** `PRIORITY_AND_DATE`

### Definition of done

`HOW_TO_VERIFY` *(example: Run `npm test`; manual check on staging with given user.)*

---

## Instructions for the fixing agent

1. **Reproduce** the bug using the steps above; if you cannot, ask for missing data and suggest minimal extra logging.
2. **Identify root cause** with references to specific code paths or configuration.
3. **Implement** the smallest change that satisfies acceptance criteria; avoid unrelated refactors.
4. **Add or update tests** that fail without the fix and pass with it.
5. **Summarize** for humans: cause, fix, risk, and how to verify.

---

## Example (filled mini-prompt)

**Summary:** Export CSV truncates rows after 1000 characters.

**Reproduction:** Call `GET /reports/export?format=csv` for report `R-42`.

**Expected:** Full cell text in CSV.

**Actual:** Cells cut at 1000 chars.

**Acceptance:** Full text preserved; unit test with a 5000-char cell passes.

**Constraints:** Streaming response must remain memory-bounded.
