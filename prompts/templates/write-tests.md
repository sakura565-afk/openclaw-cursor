# Template: Write tests

## When to use

You need unit, integration, or contract tests for new logic or to lock behavior before a refactor.

## Inputs

| Placeholder | Required | Description |
| ----------- | -------- | ----------- |
| `{{TARGET}}` | Yes | Code under test (paths, functions, or pasted implementation). |
| `{{FRAMEWORK}}` | Yes | Test runner and style (e.g. pytest, Jest, Go test). |
| `{{BEHAVIOR}}` | Yes | Behaviors and edge cases that must be covered. |
| `{{FIXTURES}}` | Optional | Existing helpers, factories, or mocking libraries to use. |

## Prompt body

```text
Write tests for the code below. Prefer clarity and stable assertions over cleverness.

## Code under test
{{TARGET}}

## Test framework and conventions
{{FRAMEWORK}}

## Behaviors and edge cases to cover
{{BEHAVIOR}}

## Existing fixtures / patterns
{{FIXTURES}}

## Rules
1. One logical behavior per test; use descriptive names that read like specs.
2. Cover happy path, representative edge cases, and at least one failure mode if applicable.
3. Avoid testing implementation details unless they are part of the contract.
4. If the production code is hard to test, suggest a minimal refactor to improve testability—and keep that refactor optional in a separate short list.
5. Show the full test file(s) or diff-ready blocks, ready to paste into the repo.
```
