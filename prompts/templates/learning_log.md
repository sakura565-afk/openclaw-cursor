---
title: Learning Log Entry
purpose: Durable, searchable lessons that tighten prompts, skills, and tool usage over time
version: "1.0"
workflow: self-improving-agent
tags: [learnings, playbooks, anti-patterns, reinforcement]
when_to_use: After any non-trivial insight, mistake prevented, or pattern worth repeating
outputs_to: "[[downstream_artifact]]"
outputs_hint: "e.g. skill_diff, checklist, team wiki"
---

# Learning Log Entry

One entry = **one atomic lesson**. Link upstream evidence so the lesson can be audited later.

## Meta

- **Entry id:** [[entry_id]]
- **Date:** [[date]]
- **Author / agent:** [[author]]
- **Domain:** [[domain]] <!-- e.g. security, UX, data, infra -->
- **Confidence:** [[confidence]] <!-- low / medium / high + why -->
- **Related sessions / errors:** [[related_links]]

## Lesson (single sentence)

> [[lesson_one_liner]]

## Context (why this matters)

[[context_paragraph]]

## Pattern

- **Do (positive pattern):** [[positive_pattern]]
- **Don’t (anti-pattern):** [[anti_pattern]]
- **Trigger cues (when to apply):** [[trigger_cues]]

## Evidence

- **Supporting data:** [[supporting_evidence]]
- **Counter-evidence / exceptions:** [[counter_evidence]]

## Operationalization

- **Skill / prompt change:** [[skill_or_prompt_change]]
- **Tooling change:** [[tooling_change]]
- **Automation / test:** [[automation_change]]
- **Rollout / comms:** [[rollout_notes]]

## Verification plan

- **How we’ll know this lesson stuck:** [[verification_metric]]
- **Review by:** [[review_by_date_or_owner]]

## Tags

`[[tag_1]]` `[[tag_2]]` `[[tag_3]]`

---

## Usage example

```markdown
## Meta
- **Entry id:** `LL-2026-0513-01`
- **Domain:** security
- **Confidence:** high — reproduced in staging and in unit test.

## Lesson
> Always compare webhook signatures in constant time and reject missing headers before body parse.

## Pattern
- **Do:** `hmac.Equal(expected, actual)` after length check.
- **Don’t:** Short-circuit on string prefix compare of digests.

## Operationalization
- **Skill change:** Add “webhook verify” checklist item to deploy skill.
- **Test:** Add case for tampered signature + timing assertion (coarse).

## Tags
`webhooks` `hmac` `timing`
```
