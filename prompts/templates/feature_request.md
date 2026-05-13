# Feature development brief

Use this template when asking an OpenClaw agent to design or implement a feature. Replace every `[PLACEHOLDER]` before sending.

---

## Feature name

`[FEATURE_NAME]`

## Problem statement

Who is affected and what pain exists today?

`[PROBLEM_DESCRIPTION]`

## Goals

- **Primary goal:** `[MEASURABLE_OR_CLEAR_OUTCOME]`
- **Secondary goals:** `[OPTIONAL_LIST]`

## Non-goals

Explicitly exclude:

`[WHAT_THIS_FEATURE_WILL_NOT_DO]`

## User stories

1. As `[ROLE]`, I want `[CAPABILITY]` so that `[BENEFIT]`.
2. `[ADD_MORE_IF_NEEDED]`

## Functional requirements

| ID | Requirement | Priority (P0–P3) |
|----|-------------|------------------|
| FR-1 | `[REQUIREMENT_TEXT]` | `[P0]` |
| FR-2 | `[REQUIREMENT_TEXT]` | `[P1]` |

## Technical context

- **Repository / services:** `[REPOS_OR_SERVICES]`
- **Existing patterns to follow:** `[LINKS_OR_DESCRIPTION]`
- **Dependencies or integrations:** `[APIS_DB_QUEUES_ETC]`

## UX / API sketch (optional)

```
[PASTE_WIREFRAME_OPENAPI_OR_BEHAVIOR_NOTES]
```

## Acceptance criteria

- [ ] `[TESTABLE_CRITERION_ONE]`
- [ ] `[TESTABLE_CRITERION_TWO]`
- [ ] `[TESTABLE_CRITERION_THREE]`

## Rollout

- **Feature flags:** `[FLAG_NAME_OR_NONE]`
- **Migration or data backfill:** `[YES_NO_DETAILS]`
- **Observability:** `[METRICS_LOGS_ALERTS]`

## Open questions

`[LIST_UNKNOWNS_OR_NONE]`

## Requested agent output

Choose one or more:

- [ ] Technical design / RFC outline
- [ ] Implementation plan with milestones
- [ ] Code changes in repo
- [ ] Test plan
