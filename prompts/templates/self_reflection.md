# Self-Reflection Prompt Template

Use this template at the end of a coding, debugging, or research session to capture what happened, what worked, and what to improve next time.

## Usage Instructions

1. Fill this out immediately after the session while details are fresh.
2. Be specific: reference concrete decisions, commands, or outcomes.
3. Include both successful and failed approaches.
4. End with clear next-session actions.

## Template

```md
# Session Self-Reflection

## Session Overview
- Session goal: <what_you_wanted_to_achieve>
- Session type: <implementation|debugging|review|research>
- Scope worked on: <files_components_areas>
- Final status: <completed|partial|blocked>

## What I Accomplished
- <accomplishment_1>
- <accomplishment_2>
- <accomplishment_3>

## What Went Well
- <effective_strategy_or_tool_usage>
- <good_decision_or_process_choice>
- <strong_communication_or_execution_point>

## What Did Not Go Well
- <mistake_or_inefficiency_1>
- <mistake_or_inefficiency_2>
- <risk_or_gap_left_open>

## Key Decisions and Rationale
1. Decision: <decision_1>
   - Why: <reasoning>
   - Consequence: <impact>
2. Decision: <decision_2>
   - Why: <reasoning>
   - Consequence: <impact>

## Unknowns and Assumptions
- Unknowns:
  - <unknown_1>
  - <unknown_2>
- Assumptions made:
  - <assumption_1>
  - <assumption_2>

## Evidence and Validation
- Tests run: <unit|integration|manual|none>
- Results: <pass_fail_and_notes>
- Artifacts/logs: <links_or_paths>

## Improvement Opportunities
- Process improvement: <what_to_change_next_time>
- Technical improvement: <refactor_or_tooling_follow_up>
- Communication improvement: <clarity_or_alignment_change>

## Next Session Plan
1. <high_priority_next_step>
2. <second_step>
3. <validation_or_checkpoint_step>
```

## Example

```md
# Session Self-Reflection

## Session Overview
- Session goal: add structured retry logic for transient API failures
- Session type: implementation + debugging
- Scope worked on: `src/http/client.py`, `tests/test_client_retry.py`
- Final status: partial

## What I Accomplished
- Added exponential backoff with jitter for 429/503 responses.
- Added configurable max retries and timeout budget.
- Wrote unit tests for retry count and abort behavior.

## What Went Well
- Started with failing tests, which reduced regressions.
- Logging request IDs made debugging much faster.
- Keeping retry policy isolated avoided touching unrelated code.

## What Did Not Go Well
- Spent too long investigating a flaky test caused by shared fixtures.
- Initially forgot to cap total retry budget.
- Did not yet validate behavior under real network latency.

## Key Decisions and Rationale
1. Decision: retry only idempotent methods by default.
   - Why: reduces risk of duplicate side effects.
   - Consequence: safer defaults, but may require opt-in for POST.
2. Decision: use decorrelated jitter backoff.
   - Why: avoids synchronized retry spikes.
   - Consequence: slightly harder to test deterministic timings.

## Unknowns and Assumptions
- Unknowns:
  - Whether partner API rate limits per key or per IP.
  - Expected p95 latency during peak hours.
- Assumptions made:
  - 429 indicates safe-to-retry requests.
  - Existing timeout values reflect product SLA.

## Evidence and Validation
- Tests run: unit
- Results: 18 passed, 0 failed
- Artifacts/logs: CI run #8421 and local debug log excerpt

## Improvement Opportunities
- Process improvement: add a short pre-debug checklist.
- Technical improvement: add integration test with mock latency proxy.
- Communication improvement: post design rationale earlier for review.

## Next Session Plan
1. Add integration coverage for timeout + retry interaction.
2. Validate metrics in staging with synthetic traffic.
3. Document retry policy in `docs/http-client.md`.
```
