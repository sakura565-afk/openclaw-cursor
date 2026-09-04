# Template: Refactor code

## When to use

You want to improve structure, naming, or modularity without changing external behavior, or you want to prepare code for a feature while keeping risk low.

## Inputs

| Placeholder | Required | Description |
| ----------- | -------- | ----------- |
| `{{GOAL}}` | Yes | Why refactor (e.g. reduce duplication, split module, clarify API). |
| `{{SCOPE}}` | Yes | Files, directories, or symbols in scope; what is explicitly out of scope. |
| `{{CURRENT_BEHAVIOR}}` | Yes | Behavior that must be preserved (inputs/outputs, side effects, contracts). |
| `{{TEST_COMMAND}}` | Recommended | How you run tests or linters today. |
| `{{RISKS}}` | Optional | Hot paths, concurrency, serialization, or backward compatibility concerns. |

## Prompt body

```text
Refactor the code according to the goal below. Behavior visible to callers and users must stay the same unless I say otherwise.

## Goal
{{GOAL}}

## Scope
{{SCOPE}}

## Behavior to preserve
{{CURRENT_BEHAVIOR}}

## Verification
Tests or checks to run after the refactor:
{{TEST_COMMAND}}

## Risks and constraints
{{RISKS}}

## Instructions
1. Summarize the current structure in 3–5 bullets before changing anything.
2. Propose a short plan (ordered steps) that minimizes blast radius.
3. Apply the refactor in small, reviewable steps; prefer extracting functions/classes over clever one-liners.
4. Do not change public APIs or data formats unless the goal requires it—if it does, call that out explicitly.
5. After edits, list what you ran or would run to verify, and any follow-up cleanups deferred on purpose.
```
