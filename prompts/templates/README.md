# Prompt Templates

A small, opinionated library of reusable prompt templates for repeatable
agent and reviewer workflows. Each template follows the same structure so
they can be composed, versioned, and rendered programmatically.

## Catalog

| Template                                                    | When to use it                                                |
| ----------------------------------------------------------- | ------------------------------------------------------------- |
| [`error-analysis-template.md`](./error-analysis-template.md) | Diagnose an error, exception, or failing test with rigor.     |
| [`self-reflection-template.md`](./self-reflection-template.md) | Run a structured retrospective at the end of a work session. |
| [`tool-discovery-template.md`](./tool-discovery-template.md) | Pick the right tool/library/MCP server for a capability.      |
| [`code-review-template.md`](./code-review-template.md)       | Review a diff before merge or before commit.                  |

## Common Structure

Every template in this directory uses the same five sections inside the
`# Prompt` block, in this order:

1. **Role** — who the model is acting as and what biases it should hold.
2. **Context** — the inputs (variables) the prompt operates on.
3. **Task** — the numbered steps the model must perform.
4. **Output Format** — the exact Markdown shape of the response.
5. **Examples** — at least one filled-in input plus the expected response
   shape, and one degenerate/edge case.

In addition, every template file carries:

- A short top-level description.
- A `## Metadata` table (template id, version, category, recommended use,
  required/optional inputs).
- A `## Variables` table describing each `{{placeholder}}`.
- A `## Usage Notes` section with operational guidance.

This uniformity means a renderer (or another agent) can parse any template
in this directory the same way.

## Variable Conventions

- Placeholders use double curly braces: `{{variable_name}}`.
- Optional placeholders are prefixed with `?`: `{{?optional_field}}`. If the
  caller has no value, leave the placeholder blank (or remove the
  surrounding section)—do not insert `null` or `N/A`.
- Variable names are `snake_case`.
- Do not introduce new placeholder syntax in new templates; extend this
  README first if a need arises.

## Versioning

Each template declares its own `Version` in the metadata table and follows
[Semantic Versioning](https://semver.org/):

- **Patch** — clarifications, typo fixes, or example tweaks that do not
  change inputs or outputs.
- **Minor** — new optional inputs, additional output sections, or relaxed
  constraints. Backwards-compatible.
- **Major** — renamed/removed variables, restructured output, or stricter
  constraints. Callers must update.

When you change a template, bump its version in the metadata table in the
same commit.

## Composition

The templates are designed to chain:

- `error-analysis` → `code-review`: validate the proposed fix.
- `tool-discovery` → `code-review`: review the integration of a new
  dependency.
- `self-reflection` consumes the artifacts of any of the others as
  `actions_taken` / `outcomes`.

When chaining, pass the *Output Format* sections of the upstream template
as structured input to the downstream template—do not re-summarize.

## Adding a New Template

1. Copy an existing template as the starting point so the structure stays
   consistent.
2. Fill in the Metadata, Variables, Prompt, Examples, and Usage Notes
   sections.
3. Make sure the Prompt block contains the five canonical sections (Role,
   Context, Task, Output Format, Constraints) and that Examples shows both
   a typical and a degenerate case.
4. Add the new file to the **Catalog** table above.
5. Open a PR; reviewers should use `code-review-template.md` against the
   change.
