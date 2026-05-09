# Reflection Prompt

Periodic self-review template: assess recent behavior, strengths, gaps, and concrete improvements—without generic platitudes.

---

## Variables (fill before sending)

| Placeholder | Description |
|-------------|-------------|
| `{{AGENT_NAME}}` | Identifier of the reflecting agent. |
| `{{PERIOD}}` | Time window (e.g. last session, last week). |
| `{{GOALS}}` | Stated goals or success criteria for that period. |
| `{{WORK_SUMMARY}}` | Bullets of what was attempted and delivered. |
| `{{FAILURES_OR_FRICTION}}` | Mistakes, retries, confusion, or user corrections. |
| `{{USER_FEEDBACK}}` | Direct quotes or paraphrased feedback (or “none”). |
| `{{METRICS}}` | Optional: tasks completed, error rate, latency, test pass rate. |

---

## Instructions for the responding agent

1. **Outcome vs goals:** Compare `{{WORK_SUMMARY}}` to `{{GOALS}}` honestly (partial success is fine).
2. **Patterns:** Name 2–3 recurring strengths and 2–3 recurring failure modes (specific behaviors, not traits).
3. **Root causes:** For each failure mode, hypothesize *why* it happened (missing verification step, wrong abstraction, unclear requirements, etc.).
4. **Behavioral commitments:** Propose 3–5 **specific** behavior changes for the next `{{PERIOD}}` (e.g. “always run linter before commit,” “ask one clarifying question when requirements conflict”).
5. **Knowledge gaps:** List topics or tools to study briefly; prioritize by impact.
6. **Anti-patterns to drop:** Explicitly list what to stop doing.
7. Avoid vague self-praise or blame; every bullet should be **observable** or **testable**.

---

## Output format

1. Snapshot (goals vs outcomes)  
2. What worked  
3. What didn’t / friction  
4. Root-cause hypotheses  
5. Commitments (next period)  
6. Learning backlog (ordered)  
7. Stop-doing list  

---

## Example (filled placeholders)

**`{{PERIOD}}`:** Last development session.

**`{{GOALS}}`:** Add prompt library under `scripts/prompts/`, commit and push once.

**`{{WORK_SUMMARY}}`:** Created five markdown templates; validated paths against repo layout.

**`{{FAILURES_OR_FRICTION}}`:** Initially considered `prompts/templates/` until user path clarified.

**`{{USER_FEEDBACK}}`:** “Execute directly, don’t only propose.”

**Example response excerpt:**

- **Failure mode:** Planning verbosity before acting when instructions already specify execution.
- **Commitment:** When the user says “implement and push,” skip roadmap prose and begin file edits in the first turn.
- **Stop-doing:** Long feasibility preamble when scope is explicit and small.

---

## Empty template (copy-paste)

```
You are {{AGENT_NAME}} performing structured reflection.

Period: {{PERIOD}}
Goals: {{GOALS}}

Work summary:
{{WORK_SUMMARY}}

Failures / friction:
{{FAILURES_OR_FRICTION}}

User feedback:
{{USER_FEEDBACK}}

Metrics (optional):
{{METRICS}}

Follow the Instructions and Output format in scripts/prompts/reflection_prompt.md.
```
