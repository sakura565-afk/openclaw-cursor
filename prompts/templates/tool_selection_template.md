# Tool Selection Template

## Purpose

Use this template to choose the right tool, service, or workflow step for a task based on constraints, evidence, and trade-offs.

## Recommended placeholders

- `{{context}}`: Environment, available systems, or workflow stage
- `{{task}}`: The work that needs to be completed
- `{{goal}}`: Desired outcome and success conditions
- `{{available_tools}}`: Candidate tools, APIs, or actions
- `{{constraints}}`: Access, latency, reliability, cost, or policy limits
- `{{decision_criteria}}`: How the tool choice should be evaluated

## Template

```md
You are selecting the most appropriate tool or workflow step for an OpenClaw task.

Context:
{{context}}

Task:
{{task}}

Goal:
{{goal}}

Available tools:
{{available_tools}}

Constraints:
{{constraints}}

Decision criteria:
{{decision_criteria}}

Instructions:
1. Restate the task and decision point.
2. Compare the realistic tool options only.
3. Evaluate each option against the stated criteria and constraints.
4. Identify the best default choice and any acceptable fallback.
5. Explain why the selected tool reduces risk or improves efficiency.
6. Note any preconditions or data needed before using it.

Expected output format:
- Decision point:
- Recommended tool:
- Why this tool fits best:
- Alternatives considered:
  1. Tool:
     - Advantages:
     - Drawbacks:
  2. Tool:
     - Advantages:
     - Drawbacks:
- Preconditions:
  - ...
- Fallback option:
- Next action:
```
