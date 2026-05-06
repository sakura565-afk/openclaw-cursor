# Task Template: Documentation Authoring

## Purpose

Create clear, accurate, and audience-appropriate documentation from source inputs.

## Suggested Model Settings

- Temperature: `0.3`
- Style: precise, concise, and complete

## Prompt

```markdown
You are producing technical documentation.

### Persona & Behavior Sources
- Read and apply AGENTS.md for collaboration and operating constraints.
- Read and apply SOUL.md for voice, values, and communication style.
- If AGENTS.md or SOUL.md is missing, proceed with defaults and explicitly state that assumption.

### Audience
{{audience}}

### Objective
{{doc_objective}}

### Source Material
{{inputs}}

### Required Sections
1. Overview
2. Prerequisites
3. Step-by-step instructions
4. Examples
5. Troubleshooting
6. FAQ (optional, include if source supports it)

### Quality Requirements
- Do not invent APIs, commands, or behavior not shown in sources.
- Call out assumptions and unknowns.
- Prefer short paragraphs and bullet points for scanability.
- Include concrete examples where possible.

### Output Format
Return markdown using heading levels `##` and `###`, plus fenced code blocks for commands.
```

## Notes

- Best used after stabilizing behavior through code review/testing.
- Works well with changelog generation and migration guides.
