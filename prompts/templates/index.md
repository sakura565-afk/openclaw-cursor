# Prompt templates

Reusable prompts for common OpenClaw Cursor task types. Substitute every `{{PLACEHOLDER}}` in the chosen file before use.

| ID | Task type | File | Summary |
|----|-----------|------|---------|
| `code_review` | Code review | [`code_review.md`](code_review.md) | Structured review of diffs or snippets for correctness, security, and maintainability. |
| `image_generation` | Image generation | [`image_generation.md`](image_generation.md) | Brief for image models: subject, style, composition, and negative prompts. |
| `session_summary` | Session summary | [`session_summary.md`](session_summary.md) | Condense a work session into decisions, outcomes, and follow-ups. |
| `error_diagnosis` | Error diagnosis | [`error_diagnosis.md`](error_diagnosis.md) | Root-cause analysis from logs, stack traces, and reproduction steps. |
| `task_planning` | Task planning | [`task_planning.md`](task_planning.md) | Break a goal into steps, risks, and verification criteria. |

## Placeholders (shared vocabulary)

Templates use Mustache-style tokens. Common names:

| Placeholder | Typical use |
|-------------|-------------|
| `{{INPUT}}` | Primary user request, code, or text to operate on. |
| `{{CONTEXT}}` | Repo, environment, prior messages, or constraints not in `{{INPUT}}`. |
| `{{OUTPUT_FORMAT}}` | How the answer should be shaped (bullets, JSON, severity levels, etc.). |
| `{{CONSTRAINTS}}` | Hard limits: length, tone, forbidden actions, PII rules. |
| `{{REFERENCES}}` | Links, ticket IDs, file paths, or citation targets. |

Each template file lists the placeholders it expects in a short table at the top.

## Validation

- **Schema:** [`templates.schema.json`](templates.schema.json) — JSON Schema (Draft-07-style `definitions`; no external `$schema` fetch required) for a template registry document.
- **Registry:** [`templates.manifest.json`](templates.manifest.json) — describes the templates in this folder for tooling and CI checks.
