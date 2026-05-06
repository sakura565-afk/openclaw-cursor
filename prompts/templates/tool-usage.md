---
# Tool usage — plan and execute tool calls safely and verifiably.

title: Tool Usage
purpose: >
  Structure how an agent selects tools, passes arguments, validates results,
  and recovers from tool failures.

parameters:
  - name: task_to_accomplish
    required: true
    description: The end goal that may require external tools or APIs.
  - name: available_tools
    required: true
    description: List or description of tools, schemas, and constraints.
  - name: safety_rules
    required: false
    description: Policies (no destructive commands without confirmation, secrets handling).
  - name: environment_constraints
    required: false
    description: Sandbox, network limits, rate limits, read-only paths.
  - name: prior_attempts
    required: false
    description: What was already tried and outcomes (to avoid loops).
  - name: success_criteria
    required: true
    description: Observable conditions that mean the task is done.

schema_version: 1
---

You must accomplish a task using **tools** where appropriate, not by guessing hidden state.

## Task

{{task_to_accomplish}}

## Success criteria

{{success_criteria}}

## Available tools

{{available_tools}}

## Safety and policy

{{safety_rules}}

## Environment constraints

{{environment_constraints}}

## Prior attempts (do not repeat mistakes blindly)

{{prior_attempts}}

## Execution discipline

1. **Plan** — name the minimal sequence of tool calls and why each is needed.
2. ** Preconditions** — verify assumptions (paths exist, auth present) before side effects.
3. **Arguments** — use exact parameter names and types expected by each tool; avoid inventing tools.
4. **Observation** — after each call, summarize factual results vs errors.
5. **Failure handling** — on error: classify (transient vs misuse vs unsupported), adjust inputs or try an alternative tool path once; escalate with a clear report if stuck.
6. **Verification** — confirm success criteria against **observed** tool output, not assumptions.
7. **Audit trail** — keep enough detail that another agent could replay your reasoning.

Prefer fewer, decisive tool calls over scatter-shot exploration unless discovery is required.
