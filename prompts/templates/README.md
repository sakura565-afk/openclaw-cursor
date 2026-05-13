# Prompt templates

Reusable prompt shells under `prompts/templates/`. Copy a file into your chat (or open it and fill the `[BRACKETS]`), then send. Each template uses clear section headers and bracket placeholders so you can stay consistent across sessions.

## Templates

| File | Use case |
|------|----------|
| [debugging.md](./debugging.md) | Walk through a bug with error text, context, what you tried, expected vs actual, and optional code—good for systematic triage. |
| [code-review.md](./code-review.md) | Request structured review covering style, logic, security, and performance on a diff or snippet with explicit severity labels. |
| [task-planning.md](./task-planning.md) | Turn a goal into ordered steps with per-step rough time estimates (hours/half-days), risks, and a suggested first step. |
| [error-analysis.md](./error-analysis.md) | Deep-dive a single error: parse the message, rank root causes, propose fixes and verification, flag gaps in evidence. |
| [summary.md](./summary.md) | Compress long conversations, docs, or notes for a specific audience and length—decisions, open questions, and next actions. |

## How to use

1. Open the template that matches your intent.
2. Replace every `[PLACEHOLDER]` with real content (or delete optional sections you do not need).
3. Keep the **Instructions for the assistant** block at the bottom so the model knows the desired shape of the answer.

Add new templates in this directory following the same pattern: title, short intro, `---`-delimited sections with `[BRACKETS]`, then explicit assistant instructions.
