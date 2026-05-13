# Systematic debugging session guide

Use this template when asking an OpenClaw agent to run a focused debugging session. Replace every `[PLACEHOLDER]` before sending.

---

## Session objective

`[ONE_SENTENCE_WHAT_FIXED_OR_UNDERSTOOD]`

## System under debug

- **Name:** `[SERVICE_APP_OR_MODULE]`
- **Runtime:** `[LANGUAGE_VERSION_PROCESS_MODEL]`
- **Where it runs:** `[LOCAL_STAGING_PROD_OR_CI]`
- **Entry points:** `[CLI_URL_CRON_JOB_ETC]`

## Symptom checklist

- **When it started:** `[DATE_OR_AFTER_CHANGE]`
- **Symptoms:** `[LIST]`
- **What still works:** `[PARTIAL_SUCCESS]`

## Reproduction protocol

Minimal steps:

1. `[STEP]`
2. `[STEP]`
3. `[STEP]`

**Environment variables / secrets pattern (names only, no values):** `[ENV_VAR_NAMES]`

## Evidence collected so far

```
[PASTE_RECENT_LOGS_TRACES_METRICS]
```

## Already ruled out

`[HYPOTHESES_TRIED_AND_FAILED]`

## Debug plan for the agent

Execute in order; stop early if root cause is proven.

1. **Restate** the failure mode and success criteria.
2. **Trace** the code path from entry to failure.
3. **Instrument** or inspect state at `[KEY_CHECKPOINTS]`.
4. **Isolate** variables (config, data, timing, concurrency).
5. **Verify** with a minimal repro or automated test.
6. **Document** root cause, fix, and prevention.

## Tools and access

- **Commands allowed:** `[E_G_TESTS_SHELL_DB_CLIENT]`
- **Read-only vs can mutate:** `[READ_ONLY_OR_CAN_EDIT]`
- **Network / external services:** `[ALLOWED_OR_OFFLINE_ONLY]`

## Stop conditions

- **Success:** `[DEFINE_DONE_E_G_ROOT_CAUSE_AND_PATCH]`
- **Escalate if:** `[DEFINE_STOP_E_G_NO_REPRO_AFTER_N_HOURS]`

## Session log (fill as you go)

| Time | Action | Result |
|------|--------|--------|
| `[T]` | `[WHAT_YOU_DID]` | `[OUTCOME]` |
