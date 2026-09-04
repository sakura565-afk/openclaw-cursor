# Template: Implement a feature

## When to use

You have a concrete feature or change request and want implementation aligned with existing project patterns and verifiable acceptance criteria.

## Inputs

| Placeholder | Required | Description |
| ----------- | -------- | ----------- |
| `{{FEATURE}}` | Yes | What to build or change, in user-visible terms. |
| `{{ACCEPTANCE}}` | Yes | Testable criteria: given/when/then or bullet checklist. |
| `{{CODEBASE_HINTS}}` | Recommended | Relevant modules, patterns, or files to follow. |
| `{{NON_GOALS}}` | Optional | What this change must not include. |

## Prompt body

```text
Implement the feature below. Match the existing codebase’s style, error handling, and testing approach unless a standard is specified.

## Feature
{{FEATURE}}

## Acceptance criteria
{{ACCEPTANCE}}

## Codebase hints (patterns, files, layers)
{{CODEBASE_HINTS}}

## Non-goals
{{NON_GOALS}}

## Process
1. Restate the feature in one line and list acceptance criteria as a checklist.
2. Inspect or infer the right integration points; do not invent new architecture unless necessary.
3. Implement with the smallest diff that satisfies the checklist.
4. Add or update tests where the project already tests similar logic; say explicitly if tests are missing and why.
5. Summarize files touched and how each acceptance item is verified.
```
