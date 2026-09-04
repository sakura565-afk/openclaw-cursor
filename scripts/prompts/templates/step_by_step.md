# Step-by-step guide ({{task_title}})

## Description

Break a goal into ordered, checkable steps with prerequisites, verification, and common failure modes. Use for runbooks, onboarding, or teaching a procedure.

## Variables

Substitutions use mustache-style placeholders in the **task block** below (e.g. `{{` `task_title` `}}`).

| Variable | Role |
|----------|------|
| `{{` `task_title` `}}` | Name of the procedure or outcome. |
| `{{` `starting_point` `}}` | What is already true (access, repo state, tools installed). |
| `{{` `tools` `}}` | Required CLIs, accounts, permissions, or versions. |
| `{{` `success_criteria` `}}` | How to know the task is done (commands, URLs, metrics). |
| `{{` `safety_notes` `}}` | Data loss risks, production cautions, backup expectations. |

## Instruction structure

1. **Overview**: 2–3 sentences on what the procedure achieves and when to use it.
2. **Prerequisites**: Bullet checklist from the starting point and tools fields.
3. **Steps**: Numbered steps; each ends with a micro-verification (“you should see …”).
4. **Troubleshooting**: Symptom → likely cause → fix, for the top predictable failures.
5. **Completion**: Map final state to the success criteria you were given.
6. **Rollback / cleanup**: How to undo partial progress safely using the safety notes.

## Examples

**Illustrative:** task_title = “Rotate API keys for staging”, starting_point = “AWS admin role, repo cloned”, tools = “awscli v2, jq”, success_criteria = “new key active, old key disabled, CI green”, safety_notes = “do not delete prod keys; keep a 24h overlap window”.

## Tips for best results

- Keep each step single-action; split if it mixes “edit config” and “deploy”.
- Put exact command names and flags in the tools field when versions matter.
- If the audience is junior, add “expected time” hints inside the starting point field.

---

You are writing a precise operational guide.

**Task:** {{task_title}}

**Starting point:** {{starting_point}}

**Tools / access:** {{tools}}

**Success criteria:** {{success_criteria}}

**Safety:** {{safety_notes}}

Follow the instruction structure above. Use imperative mood and second person (“you”). Avoid vague verbs like “configure appropriately”—name files, keys, and UI paths when inferable.
