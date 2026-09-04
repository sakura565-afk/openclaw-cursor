# Prompts

This directory holds reusable prompt material for OpenClaw Cursor workflows.

## Template library

Structured task prompts with explicit placeholders live under [`templates/`](templates/index.md). Each file is plain Markdown plus `{{PLACEHOLDER}}` tokens for substitution before sending to a model or agent.

Validate the machine-readable registry (when present) against the JSON Schema:

```bash
npx ajv-cli validate -s prompts/templates/templates.schema.json -d prompts/templates/templates.manifest.json
```

If you do not use `ajv-cli`, any JSON Schema validator that supports Draft-07 (or compatible `$ref` to `definitions`) works the same way.
