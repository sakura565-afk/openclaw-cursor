# Task planning

**Purpose:** Turn an ambiguous goal into an ordered, verifiable plan before deep implementation work.

**Placeholders:** `{{agent_role}}`, `{{objective}}`, `{{known_constraints}}`, `{{context}}`, `{{existing_plan}}`, `{{verification_criteria}}`, `{{risks_and_dependencies}}`, `{{output_format}}`

**Rendering:** Replace every `{{...}}` before sending the prompt. If a value is unknown, use `none` or `not provided` (see `_placeholders.md`).

---

You are: **{{agent_role}}**

## Objective

What success looks like in one or two sentences:

{{objective}}

## Known constraints

Hard limits (time, stack, no network, style, scope boundaries):

{{known_constraints}}

## Context

Facts, links, file paths, prior art, or tickets the plan must honor:

{{context}}

## Existing plan (optional)

If the user already drafted steps, refine them; if empty, author a full plan from scratch.

{{existing_plan}}

## Verification

How the user or system will confirm the work is done (tests, commands, acceptance checks):

{{verification_criteria}}

## Risks and dependencies

Known blockers, external teams, migrations, feature flags, or ordering constraints:

{{risks_and_dependencies}}

## Instructions

1. Emit a **numbered checklist** of steps. Each step needs a verifiable completion signal.
2. Mark steps that require **human approval** or **external access** before execution.
3. Call out **parallelizable** vs **sequential** work when it saves time.
4. Prefer the smallest plan that fully satisfies the objective; defer nice-to-haves explicitly.
5. End with a **critical path** summary (longest dependency chain) in at most five bullets.

## Output

{{output_format}}

---

## Example (filled)

**agent_role:** Tech lead planning a repo hygiene task.

**objective:** Add reusable prompt templates under `prompts/templates/` and a CLI to list and render them via `python -m src.self_improvement.prompts`.

**known_constraints:** Python 3.12 only; no new dependencies; match existing Mustache-style placeholder fields; include unit tests.

**context:** `prompts/templates/_placeholders.md` documents shared names; other templates use Purpose / Placeholders / Instructions / Output sections.

**existing_plan:** (empty)

**verification_criteria:** `python3 -m unittest tests.test_self_improvement_prompts`; manual `python3 -m src.self_improvement.prompts list` shows four templates.

**risks_and_dependencies:** Must not break existing templates in the same directory; module must resolve repo root when cwd is not the repository root.

**output_format:** Markdown: Objective restatement, Plan table (step | owner | done when | depends on), Risks, Out of scope, First executable command.

*(The agent would produce an actionable plan before writing code.)*
