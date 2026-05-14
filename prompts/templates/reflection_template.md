# Reflection session (self-improvement)

**Purpose:** Structured retrospective after a task, sprint, or incident so patterns surface and next actions are concrete.

**Placeholders:** `{{agent_role}}`, `{{context}}`, `{{what_went_well}}`, `{{what_went_wrong}}`, `{{insights}}`, `{{next_actions}}`

**Rendering:** Replace every `{{...}}` before sending the prompt, or complete each section in prose. Unknown values → `none` or `not provided` (see `_placeholders.md`).

---

You are: **{{agent_role}}**

Reflect honestly. Balance positives and negatives; favor specific examples over generic praise.

## What Went Well

**Instructions:** List concrete wins: decisions, tools, collaborations, or metrics. Why did they work? Pre-filled or draft: `{{what_went_well}}`

{{what_went_well}}

## What Went Wrong

**Instructions:** Problems, misses, or surprises—technical and process. Separate facts from interpretation. Pre-filled or draft: `{{what_went_wrong}}`

{{what_went_wrong}}

## Insights

**Instructions:** Synthesize patterns, trade-offs, or hypotheses worth testing. Mark each as observation vs validated learning. Pre-filled or draft: `{{insights}}`

{{insights}}

## Next Actions

**Instructions:** Owner, outcome, and a crisp “done” definition per item. Prefer a small number of high-leverage actions. Pre-filled or draft: `{{next_actions}}`

{{next_actions}}

## Session Context (optional)

{{context}}
