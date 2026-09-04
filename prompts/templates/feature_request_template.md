# Feature implementation request template

Use this template to hand off a **new capability** to an agent or developer with enough clarity to estimate work, implement, and verify without guesswork.

---

## How to use

1. State the **problem or opportunity** before the solution so intent is clear.
2. Define **users and outcomes** (who benefits and how you will know it worked).
3. Specify **functional requirements** as testable bullets.
4. Call out **non-functional** needs: performance, security, accessibility, i18n.
5. List **dependencies** and **open questions** explicitly.
6. Attach **design references** (wireframes, API sketches) when UI or contracts change.

Replace all `PLACEHOLDER` values. Remove unused sections rather than leaving them empty.

---

## Task prompt (copy from here)

### Context

- **Product / area:** `PRODUCT_OR_AREA`
- **Stakeholder / requester:** `NAME_OR_TEAM`
- **Tracking link:** `TICKET_OR_SPEC_URL`

### Problem statement

`WHAT_IS_WRONG_OR_MISSING_TODAY`

*Example: Operators cannot bulk-retry failed jobs; they retry one by one in the UI, which is slow and error-prone.*

### Proposed solution (high level)

`SUMMARY_OF_THE_FEATURE`

*Example: Add a “Retry selected” action on the job queue page that enqueues retries for all selected rows.*

### Users and personas

- **Primary users:** `WHO_USES_IT`
- **Secondary users:** `ADMINS_OR_INTEGRATORS_IF_ANY`

### User stories

- As `ROLE`, I want `CAPABILITY` so that `BENEFIT`.
- As `ROLE`, I want `CAPABILITY` so that `BENEFIT`.

*Example:*

- As an operator, I want to select up to 100 failed jobs and retry them in one click so that recovery after an outage takes minutes instead of hours.

### Functional requirements

1. `REQUIREMENT_1` *(must be verifiable)*
2. `REQUIREMENT_2`
3. `REQUIREMENT_3`

### Non-functional requirements

- **Performance:** `LATENCY_THROUGHPUT_OR_LIMITS`
- **Security / privacy:** `AUTHZ_DATA_HANDLING`
- **Accessibility:** `A11Y_EXPECTATIONS_IF_UI`
- **Observability:** `LOGS_METRICS_TRACES`

### User experience (if applicable)

- **Entry points:** `WHERE_IN_THE_PRODUCT`
- **Happy path:** `STEP_BY_STEP_FLOW`
- **Empty / error / loading states:** `UX_FOR_EDGE_CASES`

### API and data (if applicable)

- **New or changed endpoints:** `METHOD_PATH_AND_PAYLOAD`
- **Schema / migrations:** `TABLES_OR_EVENTS`
- **Backward compatibility:** `VERSIONING_OR_DEPRECATION`

### Dependencies and risks

- **Depends on:** `SERVICES_LIBRARIES_OR_TEAMS`
- **Risks:** `WHAT_COULD_GO_WRONG_AND_MITIGATION`

### Out of scope

`EXPLICIT_NON_GOALS`

*Example: No mobile app changes in this iteration; bulk retry capped at 100 jobs.*

### Acceptance criteria

- [ ] `MEASURABLE_CRITERION_1`
- [ ] `MEASURABLE_CRITERION_2`
- [ ] `MEASURABLE_CRITERION_3`

### Rollout

- **Feature flag / config:** `FLAG_NAME_OR_NONE`
- **Migration steps:** `ORDER_OF_DEPLOY`

### Open questions

1. `QUESTION_1` — **Proposed default:** `ASSUMPTION_UNLESS_ANSWERED`

---

## Instructions for the implementing agent

1. **Confirm understanding** of acceptance criteria and out-of-scope items; flag ambiguities early with a recommended default.
2. **Design** minimal interfaces (API, components, modules) before coding large surfaces.
3. **Implement** behind a flag if rollout requires it; keep commits focused.
4. **Test** at unit, integration, and (if UI) critical e2e paths aligned with acceptance criteria.
5. **Document** user-facing changes in the project’s usual location (changelog, README section, or internal wiki link).

---

## Example (abbreviated)

**Problem:** No audit trail when admin changes role assignments.

**Solution:** Append-only audit log table and UI “History” tab on team settings.

**Requirements:** Every role change records actor, target, old/new roles, timestamp; exportable CSV for admins.

**Acceptance:** History shows last 90 days; CSV matches DB; unauthorized users receive 403.
