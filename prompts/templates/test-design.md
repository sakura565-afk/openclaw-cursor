# Test design from requirements

Design tests that validate behavior, not implementation details, unless stability of internals is explicitly required.

## Requirements (fill before sending)

- **Behavior to guarantee**: [BULLET_LIST]
- **Public surface**: [API_CLI_EVENTS_CONFIG]
- **Known edge cases**: [EMPTY_MAX_MIN_UNICODE_TIMEZONES_CONCURRENCY]
- **Existing test stack**: [PYTEST_UNIT_JEST_GO_TEST_ETC]

## Deliverables

1. **Equivalence classes and boundaries** for inputs and state.
2. **Test matrix**: rows = scenarios, columns = expected outcome and rationale.
3. **Minimal test list** to implement first (smoke + regression for recent bugs).
4. **Fixtures / fakes**: what to mock and what must stay real for confidence.

## Output format

- Start with a short **priority-ordered** list of test cases (name + one-line assertion intent).
- Flag **flakiness risks** (time, network, randomness) and how to control them.
