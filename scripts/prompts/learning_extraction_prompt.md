# Learning Extraction Prompt

Turn transcripts, logs, or retrospectives into **actionable** learnings: rules, checklists, and updates—not vague takeaways.

---

## Variables (fill before sending)

| Placeholder | Description |
|-------------|-------------|
| `{{AGENT_NAME}}` | Agent extracting learnings (optional). |
| `{{SOURCE_MATERIAL}}` | Conversation, incident log, reflection notes, or ticket thread (paste or path reference). |
| `{{SCOPE}}` | Domain focus: e.g. coding, ops, UX, security. |
| `{{AUDIENCE}}` | Who will apply these learnings (self, team bots, generic agents). |
| `{{STORAGE_FORMAT}}` | Desired shape: bullets, YAML checklist, ADR snippet, skill file outline. |

---

## Instructions for the responding agent

1. **Facts vs inferences:** Separate direct quotes or outcomes from interpretations; discard unsupported speculation.
2. **Extract patterns:** Recurring problems, successful strategies, and anti-patterns (max 7 items unless source is huge).
3. **Actionability test:** Each learning must be **specific enough that someone else could apply it next week** (verb + object + condition).
4. **Normalize:** Convert each pattern into one of:
   - **Rule:** “When X, do Y (because Z).”
   - **Checklist item:** verifiable before/after an action.
   - **Never/Always:** only when justified by evidence in `{{SOURCE_MATERIAL}}`.
5. **Conflicts:** If two learnings contradict, note context boundaries instead of merging blindly.
6. **Meta:** Suggest where to store (e.g. project skill, runbook section)—aligned with `{{AUDIENCE}}`.
7. Do **not** reproduce entire private source verbatim if summarization suffices; focus on durable knowledge.

---

## Output format

1. Source summary (3–6 bullets)  
2. Actionable learnings (numbered; pass actionability test)  
3. Checklist (5–10 lines if applicable)  
4. Anti-patterns to avoid  
5. Suggested storage / next actions  
6. Items **not** justified by source (explicitly listed as “needs evidence”)  

---

## Example (filled placeholders)

**`{{SOURCE_MATERIAL}}`:** Session where migration script deleted rows when CSV had duplicate keys; restored from backup; added dry-run mode.

**`{{SCOPE}}`:** Data migration safety.

**`{{AUDIENCE}}`:** Future automation agents touching DB migrations.

**`{{STORAGE_FORMAT}}`:** Checklist + short rules.

**Example extractions:**

- **Rule:** When writing a migration that deletes or updates rows, require a dry-run that prints affected counts per key **before** destructive flag is allowed.
- **Checklist:** [ ] Backup verified [ ] Dry-run counts match expectation [ ] Idempotent reruns tested on copy.

---

## Empty template (copy-paste)

```
You are {{AGENT_NAME}} extracting actionable learnings.

Scope: {{SCOPE}}
Audience: {{AUDIENCE}}
Desired format notes: {{STORAGE_FORMAT}}

Source material:
{{SOURCE_MATERIAL}}

Follow the Instructions and Output format in scripts/prompts/learning_extraction_prompt.md.
```
