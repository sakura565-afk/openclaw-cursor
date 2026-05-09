# Refactoring

Use this template when restructuring code without changing external behavior (or when behavior changes are explicitly listed and tested).

## Motivation

- **Pain today:** <!-- Duplication, coupling, unclear ownership, hard tests, etc. -->
- **Target quality:** <!-- What “better” looks like after the refactor. -->

## Scope

- **In scope:** <!-- Modules, layers, or patterns to touch. -->
- **Out of scope:** <!-- Avoid scope creep; list what not to change. -->
- **Behavior:** <!-- “Preserve behavior” or list intentional deltas. -->

## Safety constraints

- Prefer small steps; keep the tree buildable and tests passing when possible.
- Do not change public APIs or on-disk formats unless called out below.
- Match existing style, naming, and error-handling patterns unless modernizing is the goal.

## Verification

- **Tests to run:** <!-- Commands or test modules. -->
- **Manual checks:** <!-- If any (CLI, UI, integration). -->

## Deliverables

1. Plan (brief) if the refactor spans multiple commits or PRs.
2. Diff-oriented summary of structural changes.
3. Confirmation that verification steps passed (or what remains).
