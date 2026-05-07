# Self-Reflection Template

> A reusable prompt template for an agent (or human) to perform structured
> reflection on a completed work session: what was attempted, what worked,
> what failed, and what to change next time.

---

## Metadata

| Field            | Value                                                  |
| ---------------- | ------------------------------------------------------ |
| Template ID      | `self-reflection`                                      |
| Version          | `1.0.0`                                                |
| Category         | Meta-cognition / Continuous improvement                |
| Recommended Use  | End-of-session retros, post-mortems, agent self-audit  |
| Required Inputs  | `session_goal`, `actions_taken`, `outcomes`            |
| Optional Inputs  | `tools_used`, `time_spent`, `artifacts`, `user_feedback`, `prior_reflections` |

---

## Variables

| Variable                | Type   | Required | Description                                      |
| ----------------------- | ------ | -------- | ------------------------------------------------ |
| `{{session_goal}}`      | string | yes      | The objective the session was supposed to achieve |
| `{{actions_taken}}`     | list   | yes      | Ordered list of significant actions or steps     |
| `{{outcomes}}`          | text   | yes      | Final state vs. intended state                   |
| `{{?tools_used}}`       | list   | no       | Tools, commands, or models invoked               |
| `{{?time_spent}}`       | text   | no       | Wall-clock or token budget consumed              |
| `{{?artifacts}}`        | list   | no       | Files created/modified, PRs opened, etc.         |
| `{{?user_feedback}}`    | text   | no       | Direct comments from the user during the session |
| `{{?prior_reflections}}`| text   | no       | Lessons from previous sessions to compare against |

---

## Prompt

```
# Role
You are a thoughtful practitioner conducting a structured retrospective on a
work session that just ended. Your job is to extract durable lessons—not to
defend choices or assign blame. Be candid, specific, and actionable.

# Context
## Session goal
{{session_goal}}

## Actions taken (in order)
{{actions_taken}}

## Outcomes
{{outcomes}}

## Tools used
{{?tools_used}}

## Time / budget spent
{{?time_spent}}

## Artifacts produced
{{?artifacts}}

## User feedback
{{?user_feedback}}

## Prior reflections to compare against
{{?prior_reflections}}

# Task
1. Judge whether the session goal was met (Fully / Partially / Not met) and
   justify the verdict in one sentence.
2. Identify what worked well and why—tie each item to a specific action,
   tool choice, or decision.
3. Identify what did not work and why—again grounded in specific actions.
4. Surface one or more *root* causes behind the friction, not just symptoms.
5. Produce concrete, testable changes for the next similar session. Each
   change must be phrased as an action, not a wish.
6. If prior reflections were provided, note which previous lessons were
   honored and which were repeated as mistakes.

# Output Format
Respond in Markdown using exactly these sections:

## Verdict
`Fully met` | `Partially met` | `Not met` — followed by a one-sentence
justification.

## What Worked
Bulleted list. Each bullet: `**<short label>** — <why it worked, with
reference to the specific action>`.

## What Didn't Work
Bulleted list with the same format as What Worked.

## Root Causes
Numbered list. For each, separate the *symptom* from the *underlying cause*:
- **Symptom:** ...
- **Underlying cause:** ...

## Lessons Learned
Bulleted list of generalizable principles distilled from this session.

## Next-Session Action Items
A table with columns: `#`, `Action`, `Trigger` (when to apply it),
`Success criterion` (how you'll know it worked).

## Repeat Offenses
Only include this section if `prior_reflections` was supplied. List any
mistake from a prior session that recurred here, and explain why the prior
fix did not stick.

# Constraints
- Be specific: prefer "rewrote the regex three times instead of writing a
  unit test first" over "spent too much time debugging".
- Limit Lessons Learned to at most 5 bullets—force prioritization.
- Action items must be falsifiable: a future reviewer should be able to tell
  whether each was followed.
- Do not include praise or blame directed at any person.
```

---

## Examples

### Example 1 — Filled-in input (abridged)

```
## Session goal
Add pagination to the /users API endpoint and update the OpenAPI spec.

## Actions taken (in order)
1. Read existing handler in src/api/users.ts
2. Implemented `?page` and `?pageSize` query parameters
3. Wrote two unit tests; both passed
4. Forgot to update openapi.yaml until user pointed it out
5. Updated openapi.yaml and regenerated the client SDK

## Outcomes
Endpoint paginates correctly; SDK regenerated; CI green. User noted that the
OpenAPI update should have been part of the original change set.
```

### Example 1 — Expected response shape (abridged)

```markdown
## Verdict
Partially met — the endpoint works, but the spec/SDK update was reactive
rather than planned.

## What Worked
- **Test-first on edge cases** — writing tests for `pageSize=0` and
  `page < 1` caught an off-by-one before review (action 3).

## What Didn't Work
- **Spec drift** — openapi.yaml was treated as documentation rather than
  contract, so it was forgotten until the user flagged it (action 4).

## Root Causes
1. - **Symptom:** OpenAPI spec was updated late.
   - **Underlying cause:** No checklist or pre-commit hook ties API code
     changes to spec changes.

## Lessons Learned
- API changes have at least three artifacts (handler, spec, SDK); plan all
  three at the start.
- Reactive changes from user feedback are a signal that planning was thin.

## Next-Session Action Items
| # | Action                                          | Trigger                       | Success criterion                     |
|---|-------------------------------------------------|-------------------------------|---------------------------------------|
| 1 | List all artifacts before writing code          | Any API change                | Written list exists before first edit |
| 2 | Add a pre-commit hook that fails when handlers change without spec changes | Repo setup | Hook runs in CI                       |
```

### Example 2 — Comparing against prior reflections

If `prior_reflections` mentioned "always update OpenAPI alongside handler
code" and the same mistake recurred, the model adds a `## Repeat Offenses`
section explaining why the prior lesson was not operationalized (e.g., it
remained advice rather than tooling) and proposes a stronger mechanism.

---

## Usage Notes

- This template is **agent-friendly**: an autonomous agent can invoke it on
  itself at the end of a task by feeding its own action log into
  `actions_taken`.
- Keep `actions_taken` chronological and granular; vague inputs produce
  vague reflections.
- Store completed reflections as Markdown files (e.g.
  `memory/reflections/YYYY-MM-DD-<slug>.md`) so they can be fed back as
  `prior_reflections` in future sessions.
- This template pairs well with `error-analysis-template.md` when the
  session ended in failure: run error analysis first, then reflection.
