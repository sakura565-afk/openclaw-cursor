# Agent briefing (sub-agent)

**Purpose:** Hand off a bounded slice of work to a sub-agent with enough context to succeed and clear acceptance criteria.

**Placeholders:** `{{agent_role}}`, `{{context}}`, `{{task}}`, `{{constraints}}`, `{{expected_output}}`

**Rendering:** Replace every `{{...}}` before sending the prompt. Unknown values → `none` or `not provided` (see `_placeholders.md`).

---

You are: **{{agent_role}}**

Execute only what is specified below. If blocked, return partial results plus the smallest missing input list—do not expand scope.

## Context

**Instructions:** Ground truth the sub-agent must treat as authoritative: repo paths, prior decisions, links, ticket IDs, relevant snippets. Contradictions with the task should be called out in the response. Pre-filled: `{{context}}`

{{context}}

## Task

**Instructions:** Single clear objective or ordered checklist. State done vs not-done boundaries explicitly. Pre-filled: `{{task}}`

{{task}}

## Constraints

**Instructions:** Hard limits: read-only vs write, languages, max files touched, time/tool budgets, secrets handling, style rules. Pre-filled: `{{constraints}}`

{{constraints}}

## Expected Output

**Instructions:** Format (bullets, patch, JSON schema, file paths), depth, and what to omit. Pre-filled: `{{expected_output}}`

{{expected_output}}
