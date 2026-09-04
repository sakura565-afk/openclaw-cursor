# Prompt templates

Reusable instruction patterns for coding assistants (Cursor, CLI agents, or chat). Each file is a standalone template: copy the **Prompt body** section, replace the placeholders, and send it to the model.

## How to use

1. Open the template that matches your task (see the table below).
2. Read **When to use** and **Inputs** so you include the right context.
3. Copy everything under **Prompt body** into your assistant.
4. Replace each `{{PLACEHOLDER}}` with real values. Remove optional blocks you do not need.
5. If the model’s reply is too shallow, tighten **Constraints** (add stack trace lines, file paths, or acceptance criteria).

## Conventions

- **Placeholders** use `{{LIKE_THIS}}`. Use the exact names so you can search a template for `{{` when filling it in.
- **Optional sections** are marked in the prompt body; delete them if they do not apply.
- Prefer pasting **errors verbatim**, **minimal reproducible snippets**, and **expected vs actual** behavior—the templates assume you attach enough signal for the model to reason.

## Template index

| File | Purpose |
| ---- | ------- |
| [debug-error.md](debug-error.md) | Reproduce, localize, and fix a bug from symptoms, logs, or stack traces. |
| [refactor-code.md](refactor-code.md) | Restructure code safely with tests and scoped diffs. |
| [explain-concept.md](explain-concept.md) | Learn a term, API, or design with depth matched to your level. |
| [implement-feature.md](implement-feature.md) | Specify and implement a feature with clear acceptance criteria. |
| [review-code.md](review-code.md) | Structured review for correctness, security, and maintainability. |
| [write-tests.md](write-tests.md) | Add or extend tests from behavior or existing code paths. |

## Audit summary (this directory)

**Prior state:** `prompts/templates/` did not exist; there were no template files to migrate.

**Design choices:** Each template is Markdown with explicit **When to use**, **Inputs**, and **Prompt body** sections so humans and agents can parse intent without a separate schema. Placeholders are consistent and grep-friendly (`{{`).

**Gaps filled:** Debug, refactor, and explain workflows are first-class. Implement, review, and test authoring complement the most common agent-assisted development loops.

## Adding a new template

1. Copy an existing file as a starting point.
2. Keep **When to use** to one short paragraph.
3. List every placeholder in **Inputs** with required vs optional.
4. Put the copy-paste block only under **Prompt body** so the rest of the file stays documentation.
