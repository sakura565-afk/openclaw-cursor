# Test generation

**Purpose:** Design or write tests (unit, integration, contract) that lock intended behavior, cover edge cases, and stay maintainable.

**Placeholders:** `{{agent_role}}`, `{{repository_or_project}}`, `{{target_code}}`, `{{behavior_spec}}`, `{{test_framework}}`, `{{coverage_goals}}`, `{{context}}`, `{{constraints}}`, `{{output_format}}`

---

You are: **{{agent_role}}**

## Testing goal

**Project / repo:** {{repository_or_project}}

**Code under test (paths, symbols, or pasted signatures):**

{{target_code}}

**Behavior to lock (acceptance criteria, invariants, edge cases):**

{{behavior_spec}}

**Test stack (framework, runner, mocks policy):**

{{test_framework}}

**Coverage or quality goals (lines, branches, property tests, snapshot policy):**

{{coverage_goals}}

## Context

{{context}}

## Constraints

{{constraints}}

## Instructions

1. Enumerate **test cases** as a matrix: happy path, boundaries, errors, concurrency/time if relevant.
2. Identify **fixtures and test data**; prefer factories over huge static blobs.
3. For each case: **name**, **arrange/act/assert** sketch, and **what regression** it prevents.
4. Avoid testing **implementation details** unless necessary; prefer public API or observable outcomes.
5. Call out **flaky risks** (timing, network, global state) and how to stabilize.
6. If writing code: output **complete, runnable tests** that follow project conventions (paths, naming, imports).

## Output

{{output_format}}

---

## Example (filled)

**agent_role:** Engineer who prefers pytest and explicit assertions.

**repository_or_project:** `auth-lib`

**target_code:** `authlib/tokens.py` — function `issue_token(sub, scopes, ttl_seconds)`.

**behavior_spec:** Token must include `sub` and `scopes`; reject empty scopes; TTL clamped to max 3600; clock skew not handled (document).

**test_framework:** pytest 8.x; `freezegun` allowed for expiry tests.

**coverage_goals:** Branch coverage on TTL clamp and empty-scope error path.

**context:** Tokens are JWT-shaped strings but tests should not depend on private claim order.

**constraints:** No network; no real clock sleep; tests under `tests/`.

**output_format:** First a case matrix table, then a single `test_tokens.py` code block.

*(The agent would produce the matrix and pytest module.)*
