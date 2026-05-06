# Prompt Templates

This directory contains reusable, high-quality prompt templates organized by use case.

## Structure

- `personas/`: reusable **system prompts** for different agent personas.
- `tasks/`: task-specific prompt templates (code review, debugging, documentation).
- `variations/`: controlled variations for the same task type (temperature and instruction style).

## Persona Alignment Requirements

Several templates reference `AGENTS.md` and `SOUL.md`:

- If those files exist, treat them as authoritative persona and behavior guidance.
- If either file does not exist, continue with the template defaults and note the missing reference.

## Usage Pattern

1. Choose a persona template from `personas/`.
2. Choose a task template from `tasks/`.
3. Optionally apply a variation from `variations/`.
4. Fill all placeholders in `{{double_braces}}`.

## Placeholder Conventions

- `{{context}}`: relevant background and constraints.
- `{{inputs}}`: source material (code, logs, docs).
- `{{output_format}}`: expected final structure (bullets, JSON, markdown, etc.).
- `{{risk_tolerance}}`: conservative, balanced, or aggressive.

## Included Templates

- `personas/coding-assistant-system.md`
- `personas/writer-system.md`
- `personas/analyst-system.md`
- `tasks/code-review-template.md`
- `tasks/debugging-template.md`
- `tasks/documentation-template.md`
- `variations/code-review-temperature-variations.md`
