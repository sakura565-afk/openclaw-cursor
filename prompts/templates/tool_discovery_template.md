# Tool discovery (self-improvement)

**Purpose:** Record a newly found tool, API, script, or integration so others (or future you) can adopt it without rediscovering edge cases.

**Placeholders:** `{{agent_role}}`, `{{tool_name}}`, `{{tool_summary}}`, `{{discovery_context}}`, `{{when_to_use}}`, `{{how_to_invoke}}`, `{{inputs_outputs}}`, `{{limitations}}`, `{{examples}}`, `{{related_tools}}`, `{{tags}}`

**Rendering:** Replace every `{{...}}` before sending the prompt. Unknown values → `none` or `not provided` (see `_placeholders.md`).

---

You are: **{{agent_role}}**

Document only what you have verified or clearly labeled as hearsay.

## Tool identity

**Instructions:** Official name, version or release date, vendor/repo URL, and runtime environment if relevant. Pre-filled: `{{tool_name}}` — one-line summary: `{{tool_summary}}`

- **Name / version:** {{tool_name}}
- **Summary:** {{tool_summary}}

## Discovery context

**Instructions:** How you found it (search, colleague, doc link), and what problem it solved. Pre-filled: `{{discovery_context}}`

{{discovery_context}}

## When to use

**Instructions:** Fit criteria: problem types, scale, team skills, licensing. Pre-filled: `{{when_to_use}}`

{{when_to_use}}

## How to invoke

**Instructions:** Entry points: CLI command, library import, HTTP endpoint, IDE action. Include install or auth prerequisites. Pre-filled: `{{how_to_invoke}}`

{{how_to_invoke}}

## Inputs and outputs

**Instructions:** Parameters, env vars, config files, and what success/failure looks like (exit codes, schemas). Pre-filled: `{{inputs_outputs}}`

{{inputs_outputs}}

## Limitations and risks

**Instructions:** Quotas, cost, security, platform gaps, deprecation. Pre-filled: `{{limitations}}`

{{limitations}}

## Examples

**Instructions:** Minimal copy-paste example or transcript snippet. Redact secrets. Pre-filled: `{{examples}}`

{{examples}}

## Related tools

**Instructions:** Alternatives, complements, or migrations (“use X instead when …”). Pre-filled: `{{related_tools}}`

{{related_tools}}

## Tags

**Instructions:** Search labels (e.g. `cli`, `observability`, `llm`, `paid-tier`). Pre-filled: `{{tags}}`

{{tags}}
