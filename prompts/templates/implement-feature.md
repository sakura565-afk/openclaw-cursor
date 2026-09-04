# Implement a feature (minimal, test-backed)

Implement the described feature in the repository. Match existing architecture and style.

## Specification (fill before sending)

- **User story**: [WHO_WHAT_WHY]
- **Acceptance criteria**:
  1. [CRITERION_1]
  2. [CRITERION_2]
- **Interfaces**: [CLI_HTTP_EVENTS_CONFIG] — **backwards compatibility**: [YES_NO_DETAILS]
- **Files or subsystems likely involved**: [OPTIONAL_HINT]

## Rules

- Prefer the smallest change that meets every acceptance criterion.
- Reuse existing helpers, types, and configuration patterns; do not introduce a parallel framework without strong justification.
- Add or update tests that fail on the old behavior and pass with the new behavior.
- Do not expand scope (no drive-by refactors or unrelated formatting).

## Deliverables

1. Implementation as a coherent set of commits or a single described batch.
2. Tests or documented manual verification steps if automated tests are not applicable.
3. Short summary: behavior change, public surface change (if any), and how to run tests.

## Output format

- **Summary**: 2–4 sentences.
- **Files touched**: path list with one-line purpose each.
- **How to validate**: exact commands.
