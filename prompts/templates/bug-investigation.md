# Bug investigation and fix

You are debugging a reported issue in a codebase. Prefer evidence from code and runtime behavior over speculation.

## Report (fill before sending)

- **Observed behavior**: [WHAT_USERS_SEE_OR_LOGS]
- **Expected behavior**: [WHAT_SHOULD_HAPPEN]
- **Reproduction**: [STEPS_OR_COMMANDS] — **frequency**: [ALWAYS_INTERMITTENT_ONCE]
- **Environment**: [OS_RUNTIME_VERSIONS_CONFIG_FLAGS]
- **Suspected area** (optional): [MODULE_PATH_URL]

## Process

1. **Reproduce** (or explain precisely why you cannot) and capture the narrowest failing case.
2. **Trace** the code path from entry point to failure; note invariants that should hold but do not.
3. **Hypothesize** one primary root cause; list at most one alternative if genuinely plausible.
4. **Fix** at the correct layer: prefer fixing the root cause over masking symptoms, unless a documented compatibility shim is required.
5. **Regress**: add or extend a test, log line, or assertion that would have caught this class of bug.
6. **Verify** that the fix does not break adjacent behavior.

## Output format

- **Root cause**: one paragraph tied to specific functions or conditions.
- **Fix**: bullet list of edits by file (or module).
- **Validation**: commands run and their outcome.
- **Risk notes**: migrations, rollbacks, or feature flags if any.
