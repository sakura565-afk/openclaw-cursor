# Self-improvement agent task

**Purpose:** Guide an agent to reflect on its own behavior, workflow, or tooling usage and produce concrete, prioritized improvements (prompts, skills, checks, or automation) without scope creep.

**When to use:** After a session, on a recurring schedule, or when quality or repeatability of agent work needs tightening. Suitable for cloud agents that can read repo history, skills, or runbooks.

---

## Variables to fill

| Variable | Description |
|----------|-------------|
| `{{agent_role}}` | Role framing the reflection (e.g. “coding agent for service Y”). |
| `{{repository_context}}` | Where improvements apply (repo, branch, team). |
| `{{observation_scope}}` | What to analyze: last N tasks, specific PR, transcript excerpt, metrics. |
| `{{current_pain_points}}` | Known issues: retries, wrong assumptions, flaky tests, doc gaps. |
| `{{improvement_goals}}` | What “better” means: speed, accuracy, safety, UX of outputs. |
| `{{constraints}}` | What must not change (APIs, compliance, no new deps, etc.). |
| `{{deliverable_format}}` | How to return findings (ranked backlog, patch proposals, ADR outline). |

---

## Prompt body (render after filling variables)

You are **{{agent_role}}** working in **{{repository_context}}**.

### Scope

Analyze **{{observation_scope}}**. Honor **{{constraints}}** at all times.

### Known pain (may be incomplete)

{{current_pain_points}}

### Improvement goals

{{improvement_goals}}

### Instructions

1. Summarize what actually happened versus what should have happened (factual, no blame).
2. Identify root causes: missing context, unclear success criteria, tool misuse, skill gaps, or process gaps.
3. Propose improvements as a **ranked** list. Each item must include: **problem**, **proposed change** (specific file/skill/prompt/check), **expected effect**, **risk**, **verification** (how to know it worked).
4. Prefer changes that are small, testable, and reversible. Avoid generic advice (“communicate better”).
5. If you lack evidence, state gaps and suggest **one** minimal instrumentation step (log, checklist, or test) instead of guessing.

### Output

Return the result in **{{deliverable_format}}**.

---

## Example (filled)

**agent_role:** Senior autonomous agent maintaining the payments API service.

**repository_context:** `acme/payments-api`, branch `main`, Cloud Agent workspace.

**observation_scope:** Last three tasks: refund idempotency fix, flaky integration test, and doc update for webhooks.

**current_pain_points:** Repeated full `mvn test` runs; occasional wrong file targeted when multiple modules match grep.

**improvement_goals:** Faster feedback loop and fewer mistaken edits outside `payments-core`.

**constraints:** No new third-party dependencies; do not change public REST contracts.

**deliverable_format:** Markdown with sections: Executive summary, Ranked improvements (max 7), Next 48h actions (max 3 bullets).

*(Rendered prompt = body above with variables substituted.)*
