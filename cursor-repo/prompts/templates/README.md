# Prompt templates

This directory holds reusable Markdown templates for agent prompts and structured reports. Each file is valid Markdown so it renders cleanly in editors and docs; placeholders mark where callers inject dynamic text.

## Files

| File | Purpose |
|------|---------|
| `agent_prompt.md` | System or task prompts: role, objective, context, tools, procedure, output contract. |
| `error_report.md` | Incidents and defects: summary, reproduction, diagnostics, impact, follow-up. |
| `reflection_report.md` | Retrospectives: outcomes, learnings, decision quality, forward look. |

## Placeholder convention

- Placeholders use double curly braces: `{{placeholder_name}}`.
- Names are lower snake case for easy mapping from code or config keys.
- Replace the entire token including braces. Do not leave empty `{{}}` in final documents.
- Some placeholders accept multi-line content; paste as-is under the heading or inside fenced code blocks as indicated.

## Optional sections

Sections may be omitted from the rendered prompt if they do not apply. Prefer removing whole headings rather than leaving generic placeholder text in production prompts.

## Composition workflow

1. Copy the template that matches your use case (or load it from your orchestration layer).
2. Substitute each placeholder from your runtime context (user message, ticket fields, env vars, etc.).
3. Trim unused sections to reduce noise and token use.
4. Version or hash the filled prompt if you need reproducibility (`Metadata` tables include slots for this).

## Extending the system

- Add new templates alongside these files; document them in the table above.
- Keep one primary objective per template so prompts stay composable.
- When templates depend on each other, note the dependency in this README and in the template `Metadata` section where relevant.

## Rendering notes

- Tables are optional metadata; plain bullet lists are equivalent if your renderer strips tables.
- Code fences use generic `text` unless you need syntax highlighting for logs or JSON.
