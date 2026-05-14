# Error analysis (self-improvement)

**Purpose:** Capture a single failure end-to-end so future runs avoid the same trap and fixes stay traceable.

**Placeholders:** `{{agent_role}}`, `{{context}}`, `{{error_description}}`, `{{root_cause}}`, `{{fix_applied}}`, `{{lessons_learned}}`, `{{tags}}`

**Rendering:** Replace every `{{...}}` before sending the prompt, or fill each section in prose. If a value is unknown, use `none` or `not provided` (see `_placeholders.md`).

---

You are: **{{agent_role}}**

Use the sections below in order. Prefer facts (logs, commands, commits) over guesses; label inference explicitly.

## Error Description

**Instructions:** State what broke in plain language: symptom, expected vs actual, and where it surfaced (service, job, file, step). Paste or summarize primary evidence here. Pre-filled body (optional): `{{error_description}}`

{{error_description}}

## Root Cause

**Instructions:** Explain the underlying reason—not only the trigger. If multiple causes exist, list them and mark the dominant one. Tie each claim to evidence from the error description. Pre-filled or draft: `{{root_cause}}`

{{root_cause}}

## Fix Applied

**Instructions:** Describe the change, workaround, or process adjustment (patch summary, config diff, rollback, or “none yet”). Note blast radius and whether the fix is verified. Pre-filled or draft: `{{fix_applied}}`

{{fix_applied}}

## Lessons Learned

**Instructions:** What should we do differently next time (checklists, tests, monitoring, docs)? One bullet per lesson; avoid blame. Pre-filled or draft: `{{lessons_learned}}`

{{lessons_learned}}

## Tags

**Instructions:** Short labels for search (e.g. `flaky-test`, `permissions`, `dependency-pin`, `prod-incident`). Comma-separated or JSON array—pick one convention and stick to it. Pre-filled: `{{tags}}`

{{tags}}

## Additional Context (optional)

{{context}}
