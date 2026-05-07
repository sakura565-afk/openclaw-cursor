# Prompt Templates

This directory contains reusable prompt templates for common engineering and AI-assistant workflows.

## Index

| Template | Path | When to Use |
| --- | --- | --- |
| Error Analysis | `prompts/templates/error_analysis.md` | When debugging failures, exceptions, regressions, flaky tests, or production incidents and you need structured root-cause analysis plus fix options. |
| Self Reflection | `prompts/templates/self_reflection.md` | At the end of a session to capture what worked, what did not, key decisions, and concrete next steps for improvement. |
| Tool Discovery | `prompts/templates/tool_discovery.md` | When evaluating libraries, CLIs, or platforms and you need a requirement-driven comparison and recommendation. |
| Code Review | `prompts/templates/code_review.md` | When requesting targeted review feedback on a PR or patch, especially for correctness, regression risk, and test quality. |

## How to Use These Templates

1. Open the relevant template file.
2. Copy its `Template` section into your prompt or issue.
3. Replace all `<placeholders>` with project-specific details.
4. Keep the `Requested Output` section to get consistent, high-quality responses.
5. Use the `Example` section as a reference for depth and structure.

## Contribution Guidelines

- Keep templates generic and reusable across projects.
- Use clear placeholder names in angle brackets (e.g., `<component_name>`).
- Include:
  - concise usage instructions,
  - a copyable template block,
  - and one realistic example.
- Prefer practical, action-oriented requested outputs.
