# Error Analysis Prompt Template

Use this template when you need a structured investigation of a bug, failure, exception, regression, or flaky behavior.

## Usage Instructions

1. Copy this template into your prompt.
2. Replace all `<placeholder>` values with concrete details.
3. Include logs, stack traces, and reproduction steps when available.
4. Ask for both root-cause analysis and actionable fixes.
5. If uncertain, request ranked hypotheses with confidence levels.

## Template

```md
# Error Analysis Request

## Context
- Project/Repository: <project_name>
- Area/Component: <component_or_module>
- Environment: <local|staging|production|ci>
- Runtime/Version: <language_runtime_and_version>
- Branch/Commit (optional): <branch_or_sha>

## Problem Summary
<one_paragraph_summary_of_the_failure>

## Observed Behavior
- What happened: <observed_outcome>
- When it happens: <timing_or_trigger_conditions>
- Frequency: <always|intermittent|rare>
- Severity/Impact: <who_or_what_is_affected>

## Expected Behavior
<expected_outcome>

## Reproduction
1. <step_1>
2. <step_2>
3. <step_3>
- Reproducibility rate: <e.g._8/10_runs>

## Evidence
- Error message: <exact_error_message>
- Stack trace/log excerpt:
  ```text
  <paste_relevant_logs_here>
  ```
- Related metrics/screenshots (optional): <links_or_notes>

## Recent Changes
- <code_or_config_change_1>
- <dependency_or_infra_change_2>
- <feature_flag_or_data_change_3>

## Constraints
- Time constraints: <none_or_specific_limit>
- Risk constraints: <no_db_migrations|backward_compatible_only|etc>
- Allowed scope: <diagnosis_only|include_fix|include_tests>

## Requested Output
Please provide:
1. Most likely root cause(s), ranked by confidence.
2. Validation steps for each hypothesis.
3. Minimal fix and robust fix options (with trade-offs).
4. Test plan to prevent regressions.
5. Rollback/mitigation plan if fix fails.
```

## Example

```md
# Error Analysis Request

## Context
- Project/Repository: payments-api
- Area/Component: webhook signature verification middleware
- Environment: production
- Runtime/Version: Python 3.12, FastAPI 0.111
- Branch/Commit (optional): main @ 3a2bf64

## Problem Summary
Stripe webhook events are intermittently rejected with 400 responses due to signature verification failures, causing delayed invoice updates.

## Observed Behavior
- What happened: 2-5% of incoming webhook requests return `InvalidSignature`.
- When it happens: Mostly during peak traffic windows.
- Frequency: intermittent
- Severity/Impact: Billing sync delayed for some customers.

## Expected Behavior
All valid Stripe webhook events should pass signature verification and be processed exactly once.

## Reproduction
1. Replay production webhook payloads in staging.
2. Use high-concurrency load (50 req/s) against `/webhooks/stripe`.
3. Compare verification success rates across workers.
- Reproducibility rate: 3/10 runs

## Evidence
- Error message: `stripe.error.SignatureVerificationError: No signatures found matching the expected signature for payload`
- Stack trace/log excerpt:
  ```text
  [ERROR] request_id=8a91... signature mismatch; header=t=171500...,v1=...
  body_length=18453 parsed_json_length=18210 worker=api-3
  ```
- Related metrics/screenshots (optional): spike in 400s from 14:00-14:20 UTC

## Recent Changes
- Introduced request body logging middleware last week.
- Upgraded `uvicorn` from 0.27 to 0.30.
- Enabled gzip decoding at ingress proxy.

## Constraints
- Time constraints: mitigation needed today
- Risk constraints: no schema changes
- Allowed scope: include fix and tests

## Requested Output
Please provide:
1. Most likely root cause(s), ranked by confidence.
2. Validation steps for each hypothesis.
3. Minimal fix and robust fix options (with trade-offs).
4. Test plan to prevent regressions.
5. Rollback/mitigation plan if fix fails.
```
