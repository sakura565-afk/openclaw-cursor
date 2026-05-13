# Code review request

Use this template when asking an OpenClaw agent to review code changes. Replace every `[PLACEHOLDER]` before sending.

---

## Goal

Perform a structured code review of the following scope and report findings in order of severity.

## Scope

- **Repository / project:** `[REPO_OR_PROJECT_NAME]`
- **Branch or commit range:** `[BRANCH_OR_COMMITS]`
- **Paths or modules in scope:** `[PATHS_OR_MODULES]`
- **Out of scope (explicit):** `[OUT_OF_SCOPE]`

## Context for the reviewer

- **What this code is supposed to do:** `[INTENDED_BEHAVIOR]`
- **Risk areas you care about most:** `[SECURITY_PERFORMANCE_API_COMPAT_ETC]`
- **Related issues or PRs:** `[LINKS_OR_IDS]`

## Review criteria

Check for:

1. Correctness and edge cases
2. Security and data handling
3. Performance and resource use
4. Maintainability (naming, structure, duplication)
5. Tests and observability (logging, metrics, errors)
6. Documentation and API contracts

## Deliverables

Ask the agent to produce:

- **Summary:** one paragraph overall verdict
- **Blocking issues:** list with file:line references where possible
- **Non-blocking suggestions:** grouped by theme
- **Test gaps:** what should be added or run

## Constraints

- **Time budget / depth:** `[SHALLOW_VS_DEEP]`
- **Style or standards to follow:** `[LIN_CONFIG_STYLE_GUIDE]`
- **Do not change:** `[FILES_OR_AREAS_OFF_LIMITS]`

## Raw inputs (optional)

Paste diffs, snippets, or links the agent should read:

```
[PASTE_DIFF_OR_SNIPPETS]
```
