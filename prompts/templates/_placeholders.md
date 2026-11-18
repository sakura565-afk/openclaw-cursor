# Placeholder conventions (read once)

All templates under `prompts/templates/` use **Mustache-style** placeholders: `{{name}}`.

| Placeholder | Meaning |
|-------------|---------|
| `{{agent_role}}` | Short role line (e.g. “senior backend engineer”). |
| `{{objective}}` | What success looks like in one or two sentences. |
| `{{context}}` | Facts, links, file paths, logs, or constraints the agent must honor. |
| `{{constraints}}` | Hard limits (no network, language, style, time). |
| `{{output_format}}` | Desired structure (bullets, JSON schema, diff-only, etc.). |
| `{{failure_evidence}}` | Errors, stack traces, exit codes, flaky symptoms. |
| `{{attempted_fixes}}` | What was already tried (omit or write “none”). |
| `{{reasoning_task}}` | The question or decision to analyze. |
| `{{known_premises}}` | Given facts; distinguish from assumptions. |
| `{{task_plan}}` | High-level steps or backlog (may be empty for the agent to author). |
| `{{verification_criteria}}` | How to confirm each step or the final result. |

**Rendering:** Replace every `{{...}}` before sending the prompt. If a value is unknown, use a literal like `none` or `not provided` so the model does not invent hidden context.
