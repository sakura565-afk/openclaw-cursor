# Refactoring

Use this template when you want behavior-preserving cleanup, restructuring, or tech-debt reduction.

## Current state

<!-- Describe the code as it exists: pain points, smells, coupling. -->

## Goal

<!-- What should the code look like after? Same external behavior, clearer structure, etc. -->

## Constraints

- **Behavior:** <!-- e.g. No intentional behavior change; flag any ambiguity -->
- **Scope:** <!-- files / packages to touch or avoid -->
- **Compatibility:** <!-- breaking API allowed? migration path? -->
- **Performance:** <!-- must not regress; or explicit tradeoffs OK -->

## Tests / verification

- **Existing tests:** <!-- which suites must stay green -->
- **Additional checks:** <!-- manual steps, benchmarks, profiling -->

## Done when

- <!-- e.g. Duplication removed; modules have single responsibility -->
- <!-- e.g. All tests pass; diff is reviewable size -->
