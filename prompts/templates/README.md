# Prompt templates

Markdown prompts under this directory share placeholder conventions documented in [`_placeholders.md`](_placeholders.md). Templates use **Mustache-style** `{{name}}` fields: replace them before use, or substitute `none` / `not provided` when a value is unknown.

## Self-improvement templates

| File | Description |
|------|-------------|
| [`error_analysis_template.md`](error_analysis_template.md) | Post-mortem style capture: error description, root cause, fix applied, lessons learned, tags. |
| [`reflection_template.md`](reflection_template.md) | Session or sprint retrospective: what went well, what went wrong, insights, next actions. |
| [`tool_discovery_template.md`](tool_discovery_template.md) | Record a discovered tool: identity, when to use, invocation, I/O, limits, examples, related tools, tags. |
| [`agent_briefing_template.md`](agent_briefing_template.md) | Brief a sub-agent: context, task, constraints, expected output. |
| [`improvement_proposal_template.md`](improvement_proposal_template.md) | Propose changes: problem, proposed solution, implementation plan, success metrics. |

## General-purpose agent templates

| File | Description |
|------|-------------|
| [`general_agent.md`](general_agent.md) | Default autonomous behavior with explicit assumptions and scoped output. |
| [`focused_task.md`](focused_task.md) | Single deliverable with minimal scope creep. |
| [`multi_step_task.md`](multi_step_task.md) | Planning, ordering, checkpoints, and verification across several steps. |
| [`complex_reasoning.md`](complex_reasoning.md) | Decomposition, trade-offs, and explicit uncertainty for hard decisions. |
| [`code_change.md`](code_change.md) | Implement or fix code with review-friendly structure and test awareness. |
| [`explore_readonly.md`](explore_readonly.md) | Investigate a codebase or system without modifying it. |
| [`review_critique.md`](review_critique.md) | Structured feedback on a design, patch, or document. |
| [`error_recovery.md`](error_recovery.md) | Diagnose failures and converge on a fix without repeating dead ends. |
| [`tool_use_disciplined.md`](tool_use_disciplined.md) | Safe command/API/tool usage with guardrails and audit-friendly summaries. |

## Conventions

| File | Description |
|------|-------------|
| [`_placeholders.md`](_placeholders.md) | Shared placeholder names, meanings, and rendering rules for all templates in this folder. |
