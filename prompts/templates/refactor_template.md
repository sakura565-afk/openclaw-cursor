# Refactoring task template

Use this template when the goal is to **improve internal structure** without changing externally observable behavior—or when behavior changes are tightly controlled and explicitly listed.

---

## How to use

1. Capture **current state** (before): responsibilities, pain points, metrics.
2. Define **target state** (after): desired module boundaries, naming, patterns.
3. List **invariants**: what must stay identical from the caller’s perspective.
4. Plan **verification**: tests, snapshots, or diff of outputs that prove equivalence.
5. If behavior *does* change, treat it as a separate **explicit delta** section with migration notes.

---

## Task prompt (copy from here)

### Context

- **Codebase / service:** `SERVICE_OR_PACKAGE`
- **Primary entry points affected:** `PUBLIC_APIS_OR_CLI`
- **Drivers for refactor:** `WHY_NOW` *(example: file >800 lines; duplicate parsing in three packages)*

### Before (current state)

**Summary:** `SHORT_DESCRIPTION_OF_CURRENT_DESIGN`

**Pain points:**

1. `PAIN_1` *(example: `OrderService` mixes persistence, pricing, and notifications.)*
2. `PAIN_2`
3. `PAIN_3`

**Key symbols / files:**

| File or module | Role today |
|----------------|------------|
| `PATH_1` | `RESPONSIBILITY` |
| `PATH_2` | `RESPONSIBILITY` |

*Example:*

| File | Role today |
|------|------------|
| `src/orders/service.ts` | HTTP + DB + domain rules intertwined |

### After (target state)

**Summary:** `SHORT_DESCRIPTION_OF_TARGET_DESIGN`

**Target structure:**

| File or module | Role after refactor |
|----------------|---------------------|
| `PATH_A` | `RESPONSIBILITY` |
| `PATH_B` | `RESPONSIBILITY` |

**Patterns to apply:** `E_G_REPOSITORY_LAYER_FACTORY_DI`

### Behavior invariants (must not change unless listed below)

- [ ] `INVARIANT_1` *(example: Public function `calculateTotal` signature and return type unchanged.)*
- [ ] `INVARIANT_2` *(example: Serialized JSON field names in v1 API responses unchanged.)*
- [ ] `INVARIANT_3`

### Explicit behavior deltas (only if intentional)

| Area | Before | After | Migration |
|------|--------|-------|-----------|
| `AREA` | `OLD_BEHAVIOR` | `NEW_BEHAVIOR` | `HOW_CONSUMERS_ADAPT` |

*If none, write “None — pure internal refactor.”*

### Constraints

- **Time / scope budget:** `LIMITS`
- **Forbidden moves:** `E_G_NO_RENAME_OF_PUBLIC_TYPES`
- **Dependencies:** `BLOCKING_UPSTREAM_OR_DOWNSTREAM`

### Verification plan

1. `STEP_OR_TEST_1` *(example: Full unit suite; `npm test`.)*
2. `STEP_OR_TEST_2` *(example: Golden-file diff for `exportReport()` output on fixture set F1.)*
3. `STEP_OR_TEST_3` *(example: Load test baseline ±5% on p99 latency.)*

### Rollback plan

`HOW_TO_REVERT_OR_FEATURE_FLAG`

---

## Instructions for the refactoring agent

1. **Map dependencies** from the “before” diagram or file list; avoid drive-by edits outside the stated scope.
2. **Refactor in small steps** when possible: extract function → move module → rename, with green tests between steps.
3. **Preserve invariants**; if you discover a necessary behavior change, document it under “Explicit behavior deltas” and get confirmation if policy requires.
4. **Update tests** to match new seams (not weaker assertions) unless the old test encoded a bug.
5. **Summarize** with a before/after diagram or bullet list of moved responsibilities.

---

## Before / after narrative example

**Before:** `NotificationDispatcher` constructs email bodies, sends SMTP, and writes audit rows.

**After:** `EmailTemplateBuilder` builds bodies; `SmtpSender` sends; `AuditWriter` records. `NotificationDispatcher` orchestrates only.

**Invariants:** Same recipients, subject lines, and idempotency keys for a given input event.

**Verification:** Contract tests on template output; integration test with fake SMTP; audit table row count unchanged for replayed events.
