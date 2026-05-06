# Risk Assessment Template

## Purpose

Use this template to identify, rank, and manage delivery or operational risks before acting on a task or change.

## Recommended placeholders

- `{{context}}`: Relevant project, system, or workflow background
- `{{task}}`: Proposed action, change, or initiative
- `{{goal}}`: Desired outcome to achieve safely
- `{{options}}`: Candidate approaches under consideration
- `{{constraints}}`: Time, tooling, policy, or dependency limits
- `{{impact_areas}}`: Users, systems, data, security, cost, or maintenance considerations

## Template

```md
You are assessing risk for an OpenClaw task or planned change.

Context:
{{context}}

Task:
{{task}}

Goal:
{{goal}}

Options:
{{options}}

Constraints:
{{constraints}}

Impact areas:
{{impact_areas}}

Instructions:
1. Identify the main risks tied to the task, grouped by technical, operational, and communication risk where relevant.
2. Estimate likelihood and impact using simple labels such as low, medium, or high.
3. Explain the trigger or condition that would make each risk materialize.
4. Propose mitigations, fallback plans, or monitoring signals for each major risk.
5. Highlight any option that meaningfully reduces risk, even if it costs more effort.
6. Recommend whether to proceed, proceed with guardrails, or pause for more information.

Expected output format:
- Overall recommendation:
- Top risks:
  1. Risk:
     - Category:
     - Likelihood:
     - Impact:
     - Trigger:
     - Mitigation:
     - Fallback:
- Lower priority risks:
  - ...
- Safest viable option:
- Monitoring or validation signals:
  - ...
- Decision gates before execution:
  - ...
```
