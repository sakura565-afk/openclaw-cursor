# Task scoping and execution (agent)

You are assisting with software work in a real repository. Your goal is to deliver a correct, minimal change that matches the request.

## Inputs (fill before sending)

- **Goal**: [ONE_SENTENCE_GOAL]
- **Constraints**: [PERFORMANCE_SECURITY_STYLE_TESTING_DEPLOYMENT]
- **Scope**: [FILES_OR_AREAS_IN_SCOPE] — explicitly **out of scope**: [WHAT_NOT_TO_TOUCH]
- **Definition of done**: [TESTS_PASS_BEHAVIOR_USER_ACCEPTANCE]

## Instructions

1. **Restate** the goal and constraints in your own words (one short paragraph).
2. **Discover** what already exists: search or read only what you need; prefer reusing utilities and patterns already in the codebase.
3. **Plan** the smallest sequence of edits that satisfies the definition of done. Call out risks (API breaks, migrations, concurrency).
4. **Implement** with consistent naming, types, and error handling matching surrounding code.
5. **Verify** with the project’s test or lint commands when available; if none apply, describe manual checks you performed.
6. **Summarize** for a human reviewer: what changed, why, and how to validate.

## Output format

- Use clear headings: Summary, Changes, Validation, Follow-ups (only if truly necessary).
- When citing existing code, use file paths and line ranges so reviewers can navigate quickly.
