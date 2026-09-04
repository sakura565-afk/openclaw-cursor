# Session summary

| Placeholder | Required | Role |
|-------------|----------|------|
| `{{INPUT}}` | yes | Raw notes, chat transcript, or timeline. |
| `{{CONTEXT}}` | no | Project, participants, session goal. |
| `{{OUTPUT_FORMAT}}` | no | Length, sections, tone (e.g. internal vs. stakeholder). |
| `{{REFERENCES}}` | no | PRs, tickets, commits, or docs to mention. |

---

You are summarizing a **work session** for people who may not have attended.

## Source material

{{INPUT}}

## Session context

{{CONTEXT}}

## Desired output shape

{{OUTPUT_FORMAT}}

## References to preserve or cite

{{REFERENCES}}

## Instructions

1. **Outcome:** What was decided or accomplished? Bullet list, max six items.
2. **Open questions:** What remains unresolved?
3. **Next actions:** Table with columns: Action | Owner (if known) | Priority (H/M/L).
4. **Risks or blockers:** Only if present in the source; otherwise state "None noted."
5. Do not invent decisions or owners; if unknown, write "TBD" and flag the gap.
