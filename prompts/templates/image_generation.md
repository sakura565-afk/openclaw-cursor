# Image generation brief

| Placeholder | Required | Role |
|-------------|----------|------|
| `{{INPUT}}` | yes | Subject, action, setting, and mood. |
| `{{CONTEXT}}` | no | Style references, palette, aspect ratio, medium. |
| `{{OUTPUT_FORMAT}}` | no | Size, count of variants, transparency, file type. |
| `{{CONSTRAINTS}}` | no | Negative prompt, safety, realism vs. stylization. |

---

You are helping craft a **single, precise image-generation brief** for a model or API.

## Creative goal

{{INPUT}}

## Visual context

{{CONTEXT}}

## Deliverable format

{{OUTPUT_FORMAT}}

## Constraints and exclusions

{{CONSTRAINTS}}

## Instructions

1. Produce a **main prompt** (one tight paragraph) optimized for image models: concrete nouns, lighting, camera/composition, and style.
2. Provide a **negative prompt** line listing things to avoid (artifacts, unwanted elements), respecting `{{CONSTRAINTS}}`.
3. If `{{OUTPUT_FORMAT}}` specifies dimensions or aspect ratio, restate them explicitly in the brief.
4. Keep the brief free of copyrighted character names unless `{{CONTEXT}}` explicitly allows them.
