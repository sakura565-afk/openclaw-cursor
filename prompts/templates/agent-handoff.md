# Handoff and continuation (long-running agent)

Continue work from a prior state without losing context. Use when a session was interrupted or split across steps.

## State dump (fill before sending)

- **Branch**: [BRANCH_NAME]
- **Last known good**: [COMMIT_SHA_OR_DESCRIPTION]
- **Completed**: [DONE_ITEMS]
- **In progress**: [PARTIAL_WORK_WITH_FILE_REFS]
- **Next concrete step**: [SINGLE_ACTIONABLE_STEP]
- **Open risks / TODOs**: [LIST]
- **Commands already run**: [TEST_LINT_BUILD_WITH_RESULTS]

## Instructions for the continuing agent

1. Read the listed files before editing; do not duplicate completed work.
2. Run the smallest verification command that proves the next step is sound.
3. Prefer finishing **in-progress** items before starting new scope.
4. If blocked (missing creds, flaky infra), document the blocker and the minimal human action needed.

## Output format

- **Current status**: one paragraph.
- **Next patch goal**: single sentence.
- **Validation plan**: commands to run after the next edits.
