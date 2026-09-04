# Refactor plan: [component or area]

## Goal

What should be better after this refactor (readability, performance, testability, coupling)?

## Non-goals

Explicitly out of scope to prevent scope creep.

## Current state

- Brief description of existing structure and pain points.
- Key files, modules, or boundaries involved.

## Target state

- Desired architecture or module boundaries.
- Diagram or bullet list of new responsibilities if helpful.

## Constraints

- Backward compatibility, public APIs, performance budgets, timelines.
- Risk areas (production traffic, migrations, concurrent work).

## Strategy

1. Incremental steps (ordered) that keep the tree buildable and testable.
2. Mechanical vs behavioral changes; flag any step that may change behavior.

## Test plan

- Existing tests to lean on; new tests needed before/after each step.
- Characterization tests if behavior must be preserved but is under-specified.

## Rollout & rollback

- Feature flags, dual-write periods, or deprecation notices.
- How to revert or isolate if something goes wrong.

## Checklist before merge

- [ ] All steps documented in commits or PR description.
- [ ] No accidental behavior change without explicit review.
- [ ] Docs and examples updated if public surface changed.
