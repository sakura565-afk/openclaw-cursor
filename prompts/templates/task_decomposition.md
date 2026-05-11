# Task Decomposition

Use this template to break a **complex task** into ordered, testable steps with dependencies, owners (human vs agent), and clear definitions of done.

---

## Role

You are a **planner**. Produce a decomposition that another executor (human or agent) can follow without re-deriving the full goal from scratch.

---

## Task header

| Field | Value |
|-------|-------|
| **Title** | `{{task_title}}` |
| **End goal** | `{{end_goal}}` |
| **Hard constraints** | `{{constraints}}` (time, tech stack, no network, etc.) |
| **Out of scope** | `{{out_of_scope}}` |
| **Definition of done** | `{{definition_of_done}}` |

### Background (optional)

```
{{background_context}}
```

### Inputs available

- Repos / paths: `{{paths}}`
- Credentials / access: `{{access_notes}}`
- Prior art / links: `{{references}}`

---

## Decomposition rules

- Each step is **atomic**: one clear verb + object (e.g. "Add migration for X").
- Mark **dependencies** explicitly (step B blocks on step A).
- Each step has a **verifiable outcome** (artifact, command output, review).
- Flag **unknowns** early; add a "spike" or "research" step if needed.

---

## Example (filled)

**Title:** Add rate limiting to public API  
**End goal:** Per-IP limits with 429 + Retry-After; docs updated  
**Constraints:** No new paid services; Redis already available  
**Out of scope:** User-level quotas  
**Definition of done:** Integration tests pass; README documents limits  

| # | Step | Depends on | Verify |
|---|------|------------|--------|
| 1 | Read current middleware chain | — | Can summarize injection point |
| 2 | Spike: choose Redis key schema | 1 | Design doc 3 bullets |
| 3 | Implement limiter middleware | 2 | Unit tests green |
| 4 | Wire + config (env) | 3 | Local curl shows 429 |
| 5 | Docs | 4 | README section merged |

---

## Output format

Provide:

1. **Assumptions** — Bulleted; call out risky ones.
2. **Step table** — Columns: `#`, `Step`, `Depends on`, `Verify`, `Owner (H/A)` if useful.
3. **Parallelism** — Which steps can run concurrently.
4. **Rollback / risks** — What could go wrong per critical step.
5. **Open questions** — Numbered; suggest who answers them.

---

## Step table (template to fill)

| # | Step | Depends on | Verify |
|---|------|------------|--------|
| 1 | `{{step_1}}` | `{{dep_1}}` | `{{verify_1}}` |
| 2 | `{{step_2}}` | `{{dep_2}}` | `{{verify_2}}` |
| 3 | `{{step_3}}` | `{{dep_3}}` | `{{verify_3}}` |
| … | … | … | … |

*(Add rows as needed.)*

---

## Placeholders reference

| Placeholder | Description |
|-------------|-------------|
| `{{task_title}}` | Short name |
| `{{end_goal}}` | User-visible success |
| `{{constraints}}` | Non-negotiables |
| `{{out_of_scope}}` | Explicit boundaries |
| `{{definition_of_done}}` | Acceptance criteria |
| `{{background_context}}` | Why, history, politics |
| `{{paths}}`, `{{access_notes}}`, `{{references}}` | Inputs |
| `{{step_N}}`, `{{dep_N}}`, `{{verify_N}}` | Per-row decomposition |
