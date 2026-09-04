---
title: Self-improvement — error postmortem
category: self_improvement
---

## Purpose

Analyze mistakes, outages, or bad merges with focus on **systems and prompts**, not individuals — producing preventive actions.

## When to use

- After production incidents, broken releases, or severe regressions.
- After repeated CI failures from the same category.

## Placeholders

| Placeholder | Description |
|-------------|-------------|
| `{{INCIDENT_SUMMARY}}` | Timeline and customer/system impact |
| `{{DETECTION}}` | How the issue was detected |
| `{{ROOT_CAUSE}}` | Known or suspected root cause |
| `{{REMEDIATION}}` | What was done to mitigate and fix |
| `{{SYSTEM_CONTEXT}}` | Architecture touchpoints (deploy, DB, queues, LLM stack) |

## Usage example (filled)

**Incident:** Model pull appeared to succeed while disk full; corrupt partial download.  
**Detection:** User report + `ollama list` missing tags.  
**Root cause:** No free-space check; subprocess stderr ignored.  
**Remediation:** Added `shutil.disk_usage` guard; surface stderr on non-zero exit.  
**Context:** Local CLI wrapper around Ollama binary.

---

## Template

Conduct a blameless postmortem oriented toward prevention.

**Facts**

- Summary & impact: {{INCIDENT_SUMMARY}}
- Detection: {{DETECTION}}
- Root cause (supported by evidence): {{ROOT_CAUSE}}
- Mitigation & fix: {{REMEDIATION}}
- System context: {{SYSTEM_CONTEXT}}

**Analysis**

1. **Timeline:** Bullet timeline from first fault to full recovery (UTC or explicit TZ).
2. **Five whys:** Drill until systemic factor appears; stop at actionable layer.
3. **Contributing factors:** People/process/tools/prompts — separate correlation from causation.
4. **Detection gap:** Why wasn’t it caught earlier (tests, monitors, code review checklist)?
5. **Human/agent factors:** Were instructions ambiguous? Missing rollback? Missing guardrails?

**Preventive actions**

- List **action items** with owner role (not individual name), due-type (immediate/short/long), and verification method.
- Prefer automated checks (tests, CI gates, linters) over documentation-only fixes when cheap.

**Prompt/skills updates**

- Propose concrete edits to prompts, skills, or runbooks (bullet text ready to paste).

**Lessons**

- One paragraph: what we will **always** do before similar changes going forward.
