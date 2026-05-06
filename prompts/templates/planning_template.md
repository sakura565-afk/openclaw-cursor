# Planning Template

## Purpose

Use this template to plan work before execution, clarify scope, identify dependencies, and define a practical sequence of actions.

## Recommended placeholders

- `{{context}}`: Background information, project state, and prior decisions
- `{{task}}`: The requested work item or problem to solve
- `{{goal}}`: The intended end state or definition of done
- `{{constraints}}`: Time, safety, access, tooling, or policy limits
- `{{dependencies}}`: Systems, approvals, tools, or inputs required
- `{{unknowns}}`: Open questions or uncertain assumptions

## Template

```md
You are preparing a plan for an OpenClaw task.

Context:
{{context}}

Task:
{{task}}

Goal:
{{goal}}

Constraints:
{{constraints}}

Dependencies:
{{dependencies}}

Unknowns:
{{unknowns}}

Instructions:
1. Restate the objective and define what success looks like.
2. Break the work into logical phases or steps.
3. Identify dependencies, blockers, and sequencing requirements.
4. Highlight the highest-risk assumptions and how to validate them.
5. Include checkpoints where progress or correctness should be verified.
6. Keep the plan actionable and specific enough to execute without reinterpretation.

Expected output format:
- Objective:
- Definition of done:
- Assumptions:
  - ...
- Constraints:
  - ...
- Plan:
  1. Step:
     - Why:
     - Dependencies:
     - Validation:
  2. Step:
     - Why:
     - Dependencies:
     - Validation:
- Risks and mitigations:
  - Risk:
    - Mitigation:
- Open questions:
  - ...
```
