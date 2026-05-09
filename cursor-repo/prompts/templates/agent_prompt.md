# Agent prompt

Use this template as the backbone for system or task prompts. Replace every `{{placeholder}}` with runtime values or delete unused sections.

---

## Metadata

| Field | Value |
|-------|-------|
| Template version | `{{template_version}}` |
| Agent / run ID | `{{agent_id}}` |
| Created at | `{{created_at}}` |
| Session / task ID | `{{session_or_task_id}}` |

---

## Role

**Primary role:** `{{agent_role}}`

**Audience / stakeholders:** `{{audience}}`

**Tone and style:** `{{tone_guidelines}}`

---

## Objective

**Goal:** `{{primary_goal}}`

**Success criteria:** `{{success_criteria}}`

**Non-goals (out of scope):** `{{non_goals}}`

---

## Context

**Background:** `{{background_summary}}`

**Relevant prior decisions:** `{{prior_decisions}}`

**Constraints:** `{{hard_constraints}}`

**Assumptions:** `{{assumptions}}`

---

## Inputs

**User request (verbatim or summarized):**  
`{{user_request}}`

**Attached artifacts:** `{{artifact_list}}`

**Structured input (JSON, IDs, paths):**  
```text
{{structured_input}}
```

---

## Tools and environment

**Available tools / APIs:** `{{tools_available}}`

**Forbidden actions:** `{{forbidden_actions}}`

**Working directory / repo context:** `{{workspace_context}}`

---

## Procedure

1. `{{step_1}}`
2. `{{step_2}}`
3. `{{step_n}}`

**Escalation / stop conditions:** `{{stop_conditions}}`

---

## Output contract

**Required sections in the reply:** `{{required_output_sections}}`

**Format:** `{{output_format}}` (e.g. prose only, markdown with headings, JSON)

**Length guidance:** `{{length_guidance}}`

---

## Safety and compliance

**PII / secrets handling:** `{{pii_rules}}`

**Policy reminders:** `{{policy_notes}}`

---

## Dynamic appendix

`{{freeform_appendix}}`
