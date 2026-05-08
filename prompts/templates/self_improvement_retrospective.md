---
title: Self-improvement — task retrospective
category: self_improvement
---

## Purpose

Extract durable lessons from a completed task: what worked, what didn’t, and what to change next time — without blame.

## When to use

- After shipping a feature, closing an incident, or finishing a research spike.
- When pairing human + agent workflows and tuning collaboration patterns.

## Placeholders

| Placeholder | Description |
|-------------|-------------|
| `{{TASK_SUMMARY}}` | What was delivered |
| `{{CONTEXT}}` | Constraints, deadlines, tools available |
| `{{PROCESS}}` | How work was executed (steps, branches, reviews) |
| `{{OUTCOME}}` | Metrics, user feedback, incident impact |
| `{{SURPRISES}}` | Unexpected issues or discoveries |

## Usage example (filled)

**Task:** Add disk-space warning before Ollama pulls.  
**Context:** Single-agent PR, unittest-only CI.  
**Process:** Implemented check in manager, extended tests, README note.  
**Outcome:** Green CI; no prod deploy yet.  
**Surprises:** `shutil.disk_usage` path semantics on macOS vs Linux.

---

## Template

Run a structured retrospective on the completed work.

**Record**

- Delivered: {{TASK_SUMMARY}}
- Context: {{CONTEXT}}
- Process: {{PROCESS}}
- Outcome: {{OUTCOME}}
- Surprises: {{SURPRISES}}

**Reflect (answer each)**

1. **Goals:** Which goals were fully met, partially met, or missed? Why?
2. **Signals:** What evidence validated direction early? What misleading signal wasted time?
3. **Practices:** Which habits or tools helped most? Which hurt velocity or quality?
4. **Failures:** What failed once — and what guardrail would prevent recurrence?
5. **Knowledge:** What should be documented or encoded (checklist, skill, script)?
6. **Next experiments:** 2–3 concrete changes to try on the next similar task (specific, not vague).

**Output**

- One-paragraph summary.
- Bullet list: **Keep / Stop / Start**.
- Optional: proposed updates to team conventions or agent instructions (wording ready to paste).
