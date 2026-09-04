# Bug analysis template

Use this template when asking an OpenClaw agent to analyze or fix a bug. Replace every `[PLACEHOLDER]` before sending.

---

## Bug summary

- **Title:** `[SHORT_BUG_TITLE]`
- **Severity:** `[BLOCKER_CRITICAL_MAJOR_MINOR_TRIVIAL]`
- **Environment:** `[OS_RUNTIME_VERSIONS_CONFIG_FLAGS]`

## Observed behavior

`[WHAT_THE_USER_SEES_OR_LOGS_SHOW]`

## Expected behavior

`[WHAT_SHOULD_HAPPEN_INSTEAD]`

## Reproduction

1. `[STEP_ONE]`
2. `[STEP_TWO]`
3. `[STEP_THREE]`

**Repro rate:** `[ALWAYS_INTERMITTENT_ONCE]`

## Affected surface

- **Product / repo:** `[NAME]`
- **Version or commit:** `[VERSION_OR_SHA]`
- **Components / files (if known):** `[PATHS_OR_MODULES]`
- **Users or tenants affected:** `[SCOPE]`

## Evidence

Attach or paste:

- **Logs / stack traces:**

```
[PASTE_LOGS]
```

- **Screenshots or recordings:** `[LINKS_OR_N_A]`

## Hypotheses (optional)

What you already suspect:

`[YOUR_GUESSES_OR_NONE]`

## Constraints for the fix

- **Backward compatibility:** `[REQUIRED_OR_NOT]`
- **Deadline or release:** `[DATE_OR_NONE]`
- **Out of scope:** `[WHAT_NOT_TO_TOUCH]`

## Desired output from the agent

- Root cause analysis (or ranked hypotheses if uncertain)
- Minimal fix proposal or patch outline
- Regression test ideas
- Risk notes for deployment
