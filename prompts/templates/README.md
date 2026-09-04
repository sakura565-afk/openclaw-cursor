# Prompt templates

Reusable Markdown templates for common **agent and human** workflows: bugs, features, reviews, refactors, and documentation. Each file includes placeholders, worked examples, and step-by-step instructions.

## How to use this directory

1. Open the template that matches your task.
2. Copy the **“Task prompt”** (or **“Review prompt”**) section from that file.
3. Replace placeholders such as `` `PRODUCT_OR_REPO` `` or `ROLE` with your project-specific values.
4. Delete sections that do not apply so the prompt stays concise.
5. Paste the result into your agent chat, ticket body, or review comment.

## Templates

| File | Purpose |
|------|---------|
| [bug_fix_template.md](./bug_fix_template.md) | Structured bug report: context, reproduction, expected vs actual, acceptance criteria, and agent instructions for diagnosis and fix. |
| [feature_request_template.md](./feature_request_template.md) | Feature handoff: problem statement, user stories, functional and non-functional requirements, rollout, and implementation guidance. |
| [code_review_template.md](./code_review_template.md) | PR review checklist: correctness, security, performance, API compatibility, and a findings table with severity. |
| [refactor_template.md](./refactor_template.md) | Refactor planning: before/after design, invariants, verification plan, and explicit behavior deltas when the public contract changes. |
| [documentation_template.md](./documentation_template.md) | Documentation requests: audience, goal, outline, style, assets, and acceptance criteria aligned with the repo. |

## Conventions

- **Placeholders** appear as `ALL_CAPS_SNAKE_CASE` or short labels in tables; replace them entirely.
- **Examples** use *italics* or quoted blocks labeled *Example*; remove them from prompts you send if they would confuse the model.
- **Instructions for agents** sections tell automated assistants how to execute the task consistently.

## Contributing

When adding a new template, keep the same structure: short intro, **How to use**, copyable prompt body with placeholders and at least one example, and **Instructions for the agent** (or equivalent). Update the table in this README.
