# Base patterns for Cursor Cloud Agent prompts

**Purpose:** Shared variables, conventions, and reusable preamble blocks so agent prompts stay consistent, auditable, and easy to render from templates.

**When to use:** Before composing any task-specific prompt from `prompts/templates/`. Copy the **Variable header** block into your orchestration layer, fill values, then append a scenario template (for example `code_review.md`).

---

## Variable header (copy and fill)

Use this block at the top of rendered prompts so the model knows scope and ground rules. Replace every `{{...}}`; use `none` or `not provided` when unknown.

| Variable | Description |
|----------|-------------|
| `{{agent_role}}` | One-line role (e.g. “autonomous cloud agent on repo X”). |
| `{{repository_context}}` | Repo name, branch, PR/issue link, or workspace path. |
| `{{task_objective}}` | Single clear outcome the agent must achieve. |
| `{{hard_constraints}}` | Non-negotiables: no force-push, readonly, language, PII rules, time limits. |
| `{{soft_preferences}}` | Style, verbosity, test commands, formatting preferences. |
| `{{available_tools}}` | What the agent may call (shell, grep, MCP names) or `default toolset`. |
| `{{ground_truth}}` | Files, logs, specs, or links the agent must treat as authoritative. |
| `{{success_criteria}}` | Observable checks that mean the task is done. |
| `{{output_contract}}` | Required structure: sections, JSON schema, diff-only, etc. |

---

## Common patterns

### Operating principles

1. Prefer the smallest change that satisfies `{{success_criteria}}`.
2. If `{{ground_truth}}` conflicts with instructions, flag the conflict and follow the stricter safety constraint.
3. Label assumptions explicitly when information is missing.
4. Use tools for facts; do not invent file contents, command output, or URLs.

### Safety and repo hygiene (adjust per policy)

- Do not exfiltrate secrets; redact tokens and keys in logs or examples.
- Match branch and remotes to `{{repository_context}}`; avoid pushing to unintended refs unless explicitly allowed.
- When running commands, prefer read-only inspection until a write phase is authorized.

### Handoff footer (optional)

Add when another agent or human continues the work:

```text
## Handoff
- Completed: {{completed_summary}}
- Blocked on: {{blockers_or_none}}
- Next step: {{recommended_next_action}}
```

---

## Relationship to other files

- `_placeholders.md` — quick glossary of frequently reused names across legacy templates.
- Scenario templates in this folder — append after this base block for specialized tasks.
