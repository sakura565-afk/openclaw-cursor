# Behavior-preserving refactor

Refactor the indicated code for clarity, performance, or structure **without** changing observable behavior.

## Scope (fill before sending)

- **Target**: [FILES_SYMBOLS_OR_DIRECTORIES]
- **Motivation**: [READABILITY_PERF_DEDUPLICATION_TYPES]
- **Behavior contract**: [INPUTS_OUTPUTS_SIDE_EFFECTS_EXTERNAL_APIS]
- **Tests**: [EXISTING_TEST_SUITE_OR_COMMANDS_THAT_MUST_STAY_GREEN]

## Constraints

- No user-visible or API-visible behavior changes unless explicitly listed as in scope.
- Preserve error types and messages when they are part of a stable contract; if improving messages, call that out explicitly as a behavioral change.
- Prefer mechanical refactors (rename, extract function, reorder) over speculative redesign.

## Process

1. Identify the **behavior contract** (including edge cases) before editing.
2. Ensure tests exist for that contract; add missing characterization tests first if safe and small.
3. Apply refactor in small steps; keep compilation and tests green.
4. After edits, run the full relevant test suite (or explain gaps).

## Output format

- **Contract restatement**: bullet list.
- **Refactor steps**: ordered list tied to commits or logical chunks.
- **Risk assessment**: what could have changed accidentally and how you guarded against it.
