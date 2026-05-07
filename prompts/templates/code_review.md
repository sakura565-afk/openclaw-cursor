# Code Review Request Prompt Template

Use this template when requesting a focused, high-signal code review for a pull request or patch.

## Usage Instructions

1. Provide enough context so reviewers understand intent and constraints.
2. Highlight risky areas and specific feedback requests.
3. Include testing evidence and known limitations.
4. Ask reviewers to prioritize correctness, regressions, and maintainability.

## Template

```md
# Code Review Request

## Change Summary
- PR/Branch: <pr_link_or_branch_name>
- Scope: <high_level_description_of_changes>
- Motivation: <why_this_change_is_needed>

## What Changed
- <major_change_1>
- <major_change_2>
- <major_change_3>

## Files/Areas to Focus On
1. <file_or_module_1> - <why_it_is_important_or_risky>
2. <file_or_module_2> - <why_it_is_important_or_risky>
3. <file_or_module_3> - <why_it_is_important_or_risky>

## Behavior and Risk
- User-visible behavior change: <yes_no_and_details>
- Backward compatibility impact: <none_or_details>
- Performance implications: <none_or_details>
- Security/privacy considerations: <none_or_details>

## Testing Performed
- Automated tests:
  - <test_suite_and_result>
- Manual validation:
  - <manual_steps_and_result>
- Not tested / remaining gaps:
  - <known_test_gap_1>
  - <known_test_gap_2>

## Known Trade-offs or Follow-ups
- <tradeoff_or_todo_1>
- <tradeoff_or_todo_2>

## Reviewer Request
Please review for:
1. Correctness and edge-case handling.
2. Regressions or hidden side effects.
3. Clarity, maintainability, and API design.
4. Test coverage quality and missing cases.
5. Any blocking concerns before merge.
```

## Example

```md
# Code Review Request

## Change Summary
- PR/Branch: https://github.com/acme/orders/pull/129
- Scope: Introduces idempotency key support for order creation endpoint.
- Motivation: Prevent duplicate orders during client retries.

## What Changed
- Added `Idempotency-Key` header handling in `POST /orders`.
- Persisted idempotency records with 24h TTL.
- Updated service layer to return previous response on key reuse.

## Files/Areas to Focus On
1. `src/api/orders.py` - request parsing and response semantics changed.
2. `src/services/idempotency_service.py` - core dedupe logic and race handling.
3. `src/repositories/idempotency_repo.py` - new DB writes and uniqueness guarantees.

## Behavior and Risk
- User-visible behavior change: yes, duplicate submits now return original result.
- Backward compatibility impact: low, endpoint contract unchanged.
- Performance implications: moderate, adds one read + optional write per request.
- Security/privacy considerations: ensure headers are not logged with sensitive payloads.

## Testing Performed
- Automated tests:
  - `pytest tests/orders/test_idempotency.py` (14 passed)
  - `pytest tests/api/test_orders_post.py` (22 passed)
- Manual validation:
  - Sent repeated requests with same key and confirmed stable response body + status.
- Not tested / remaining gaps:
  - High-concurrency race under 500+ rps.
  - Multi-region replication lag behavior.

## Known Trade-offs or Follow-ups
- TTL is fixed at 24h for now; configurability deferred.
- No background compaction yet for stale idempotency rows.

## Reviewer Request
Please review for:
1. Correctness and edge-case handling.
2. Regressions or hidden side effects.
3. Clarity, maintainability, and API design.
4. Test coverage quality and missing cases.
5. Any blocking concerns before merge.
```
