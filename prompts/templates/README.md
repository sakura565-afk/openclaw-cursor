# Prompt templates index

**Purpose:** Central index for reusable Markdown prompts optimized for **Cursor Cloud Agents** and similar autonomous runners. Each file combines metadata (purpose, usage, variables) with a copy-ready **prompt body** you can render after substituting `{{placeholders}}`.

**Conventions:**

- Placeholders use Mustache-style **`{{variable_name}}`**.
- Fill every placeholder before sending; use `none` or `not provided` when a value is intentionally empty so the model does not invent context.
- Start from **`base.md`** for shared variables and safety patterns, then append a scenario template.

---

## Core files

| File | Summary |
|------|---------|
| [`base.md`](base.md) | Shared variable header, operating principles, optional handoff footer. |
| [`_placeholders.md`](_placeholders.md) | Glossary of common placeholder names used across older templates. |

---

## Scenario templates (this system)

| File | Summary |
|------|---------|
| [`self_improvement.md`](self_improvement.md) | Reflect on agent or team workflow; output ranked, verifiable improvements. |
| [`code_review.md`](code_review.md) | Structured PR / diff review with severities and actionable fixes. |
| [`error_analysis.md`](error_analysis.md) | Triage failures from evidence; hypotheses, fixes, verification. |
| [`session_summary.md`](session_summary.md) | Condense a session into a handoff: outcomes, decisions, next steps. |
| [`tool_documentation.md`](tool_documentation.md) | Document a new tool: interface, safety, examples, troubleshooting. |

---

## Other templates in this directory

| File | Summary |
|------|---------|
| [`code_change.md`](code_change.md) | Scoped implementation tasks. |
| [`complex_reasoning.md`](complex_reasoning.md) | Multi-step reasoning with explicit verification. |
| [`error_recovery.md`](error_recovery.md) | Recovery from known failure classes. |
| [`explore_readonly.md`](explore_readonly.md) | Read-only codebase exploration. |
| [`focused_task.md`](focused_task.md) | Short, single-objective tasks. |
| [`general_agent.md`](general_agent.md) | Default agent behavior and output shape. |
| [`multi_step_task.md`](multi_step_task.md) | Planned execution across several steps. |
| [`review_critique.md`](review_critique.md) | Critique of plans or designs. |
| [`tool_use_disciplined.md`](tool_use_disciplined.md) | Constrain and structure tool usage. |

---

## How to render a prompt

1. Copy the **Variable header** from [`base.md`](base.md) (or the scenario’s **Variables to fill** table).
2. Build a key/value map for every `{{name}}`.
3. Concatenate: **base header (filled)** → **scenario prompt body (filled)**.
4. Attach artifacts (diffs, logs) outside the template or in dedicated variables such as `{{failure_evidence}}` / `{{diff_or_files}}`.

---

## Contributing

When adding a template, include **Purpose**, **When to Use**, **Variables to fill** (table with `{{names}}`), a **Prompt body** section, and a short **Example** with realistic filled values. Keep instructions imperative and testable.
