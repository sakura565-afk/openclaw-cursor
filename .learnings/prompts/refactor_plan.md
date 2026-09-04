# Refactor plan ({{target_area}})

## Description

Produce a safe, incremental plan to improve structure without changing external behavior—unless behavior change is explicitly in scope.

## Variables

Substitutions use mustache-style placeholders in the **task block** below (e.g. `{{` `target_area` `}}`).

| Variable | Role |
|----------|------|
| `{{` `target_area` `}}` | Package, service, or feature to refactor. |
| `{{` `pain_points` `}}` | What hurts today (coupling, duplication, slow change, bugs). |
| `{{` `goals` `}}` | Desired end state (testability, clearer boundaries, smaller modules). |
| `{{` `non_goals` `}}` | What must not change (public API, DB schema, latency). |
| `{{` `constraints` `}}` | Timebox, team size, release cadence, compatibility promises. |

## Instruction structure

1. **Current architecture sketch**: Components and dependencies as understood from the target area you were given.
2. **Problem diagnosis**: Tie the stated pain points to concrete code smells or boundaries.
3. **Target shape**: Bullet-level target design aligned with the stated goals.
4. **Migration phases**: Ordered steps small enough to ship independently; include checkpoints.
5. **Risk register**: For each phase, rollback plan and how to detect regressions early.
6. **Testing strategy**: Unit/integration/e2e coverage to add or extend before each phase.
7. **Definition of done**: Checklist mapping back to goals and non-goals from the task block.

## Examples

**Illustrative:** target_area = “`billing/` service”, pain_points = “God class + untestable I/O”, goals = “ports/adapters + fakeable clock”, non_goals = “no price algorithm changes”, constraints = “two-week slices, weekly releases”.

## Tips for best results

- Be explicit about non-goals to prevent “helpful” behavior changes during refactors.
- If the area is huge, narrow the target area to one bounded context per plan.
- Mention observability (metrics, traces) in constraints if production validation matters.

---

You are a staff engineer planning a refactor.

**Target:** {{target_area}}

**Pain points:** {{pain_points}}

**Goals:** {{goals}}

**Non-goals:** {{non_goals}}

**Constraints:** {{constraints}}

Follow the instruction structure above. Prefer incremental steps over a big-bang rewrite unless the constraints you listed explicitly allow a larger change.
