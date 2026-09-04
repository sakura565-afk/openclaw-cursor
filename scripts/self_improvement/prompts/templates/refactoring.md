# Refactoring

**Purpose:** Plan or execute a behavior-preserving restructuring: clarify goals, risks, incremental steps, and how to prove nothing broke.

**Placeholders:** `{{agent_role}}`, `{{repository_or_project}}`, `{{refactor_goal}}`, `{{scope_modules_or_paths}}`, `{{current_pain_points}}`, `{{non_goals}}`, `{{context}}`, `{{constraints}}`, `{{verification_criteria}}`, `{{output_format}}`

---

You are: **{{agent_role}}**

## Refactoring request

**Project / repo:** {{repository_or_project}}

**Goal (why refactor, what should improve):**

{{refactor_goal}}

**In scope (files, packages, services, or boundaries):**

{{scope_modules_or_paths}}

**Current pain (duplication, coupling, types, performance smells):**

{{current_pain_points}}

**Explicit non-goals (what must not change in this pass):**

{{non_goals}}

## Context

{{context}}

## Constraints

{{constraints}}

## Verification (tests, benchmarks, manual checks)

{{verification_criteria}}

## Instructions

1. Summarize the **target end state** in one paragraph (architecture or API shape after refactor).
2. List **risks** (behavior drift, concurrency, public API) and how to mitigate each.
3. Propose **incremental steps** that keep the codebase shippable between steps; prefer small PRs.
4. For each step: **rationale**, **touch points** (symbols/paths), and **verification** for that step.
5. Flag any **behavior change** that is unavoidable; treat it as a separate explicit decision.
6. If the user asked for code: implement the **first safe step** only unless scope says otherwise.

## Output

{{output_format}}

---

## Example (filled)

**agent_role:** Staff engineer focused on module boundaries and testability.

**repository_or_project:** `billing-service`

**refactor_goal:** Extract pricing rules from HTTP handlers into a pure domain module for unit testing.

**scope_modules_or_paths:** `handlers/quote.go`, `pricing/*`, no DB schema changes.

**current_pain_points:** Handlers mix JSON parsing, DB calls, and `if region ==` pricing branches.

**non_goals:** No API response shape changes; no new endpoints.

**context:** Quote endpoint is latency-sensitive (< 50ms p99).

**constraints:** Go 1.22; no new dependencies without justification.

**verification_criteria:** Existing integration tests for `/quote`; add table-driven tests for `pricing.Engine`.

**output_format:** Markdown: End state, Risks, Step plan (numbered), Rollback notes.

*(The agent would produce a staged plan and optionally sketch interfaces.)*
