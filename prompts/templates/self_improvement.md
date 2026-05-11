# Agent Self-Improvement

Use this template for **meta** tasks: improving how the agent (or assistant) plans, verifies, communicates, or uses tools—based on concrete incidents or goals.

---

## Role

You are reflecting on **process and capability**. Ground recommendations in evidence (logs, transcripts, failures). Avoid vague advice; prefer checklists, prompts, or concrete habit changes.

---

## Improvement charter

| Field | Value |
|-------|-------|
| **Domain** | `{{domain}}` (e.g. coding, research, ops, writing) |
| **Trigger** | `{{trigger}}` (recurring mistake, user feedback, benchmark) |
| **Time horizon** | `{{horizon}}` (single session vs ongoing practice) |
| **Success metric** | `{{success_metric}}` |

### Evidence (optional)

Paste a redacted transcript, failure case, or benchmark result:

```
{{evidence_snippet}}
```

### Current behavior (honest)

`{{current_behavior}}`

### Desired behavior

`{{desired_behavior}}`

---

## Self-improvement dimensions

Address only what applies:

1. **Planning** — Task breakdown, assumptions, ordering of steps.
2. **Tool use** — When to read files, run commands, search vs guess.
3. **Verification** — Tests, lints, sanity checks before declaring done.
4. **Communication** — Clarity, structure, proportionality to task size.
5. **Safety** — Destructive ops, secrets, scope creep.
6. **Knowledge gaps** — What to document or learn for next time.

---

## Example (filled)

**Domain:** Shell-heavy coding tasks  
**Trigger:** Prematurely marked task complete before tests passed  
**Success metric:** Every coding task ends with explicit test command + result  

**Current behavior:** Stated "done" after editing files without running tests.  
**Desired behavior:** Always run specified test command and report pass/fail.  

**Improvement:** Add a mandatory closing checklist: (1) run `{{test_command}}`, (2) paste summary, (3) only then summarize completion.

---

## Output format

1. **Diagnosis** — What went wrong or what is suboptimal (root habits, not blame).
2. **Principles** — 2–5 durable rules to adopt (imperative, short).
3. **Concrete changes** — Prompt fragments, checklists, or workflow edits.
4. **Experiment** — One small change to try on the next similar task.
5. **Review cadence** — When to revisit (e.g. after next 5 tasks).

---

## Placeholders reference

| Placeholder | Description |
|-------------|-------------|
| `{{domain}}` | Area of work to improve |
| `{{trigger}}` | Why this improvement session exists |
| `{{horizon}}` | One-off vs ongoing |
| `{{success_metric}}` | How you will know it worked |
| `{{evidence_snippet}}` | Ground truth for analysis |
| `{{current_behavior}}` | What happens today |
| `{{desired_behavior}}` | Target state |
| `{{test_command}}` | Example from template text (replace in your checklist) |
