# Prompt Templates Index

This directory contains reusable prompt templates for OpenClaw agents. Each template is designed to be copied, filled with concrete values for placeholders such as `{{context}}`, `{{task}}`, and `{{goal}}`, and then adapted to the current workflow.

## Common conventions

- Replace every placeholder before use.
- Keep `{{context}}` factual and concise.
- Use `{{task}}` to describe the immediate job to be done.
- Use `{{goal}}` to define the outcome or success condition.
- Preserve the "Expected output format" section so downstream agents produce predictable responses.
- Add or remove template-specific placeholders as needed for the situation.

## Templates

### 1. `error_analysis_template.md`
Use when an agent, tool, workflow, or integration fails and you need a structured root-cause analysis with evidence and next steps.

### 2. `code_review_template.md`
Use when reviewing a diff, pull request, or patch for regressions, risks, maintainability issues, and missing validation.

### 3. `self_improvement_template.md`
Use when identifying recurring weaknesses in agent behavior and turning them into concrete improvement actions.

### 4. `session_summary_template.md`
Use at the end of a work session to summarize objectives, actions taken, outcomes, blockers, and recommended follow-ups.

### 5. `tool_selection_template.md`
Use before execution when choosing the best tool or workflow path based on task requirements, constraints, and trade-offs.

### 6. `reflection_template.md`
Use after completing a task to reflect on decision quality, execution quality, blind spots, and better future approaches.

### 7. `learning_capture_template.md`
Use to record durable lessons, reusable heuristics, failure patterns, and references that should inform future work.

### 8. `planning_template.md`
Use when decomposing work into a structured plan with dependencies, validation steps, and execution order.

### 9. `communication_template.md`
Use when preparing user-facing updates, status messages, handoffs, or summaries that must be clear and actionable.

### 10. `debugging_template.md`
Use for systematic debugging with hypotheses, reproduction steps, instrumentation, experiments, and exit criteria.

### 11. `risk_assessment_template.md`
Use when evaluating implementation, rollout, operational, or coordination risks before or during execution.

## Suggested usage patterns

- Failure investigation: start with `debugging_template.md`, then use `error_analysis_template.md` once evidence is collected.
- Code change evaluation: pair `planning_template.md` with `code_review_template.md`.
- Agent development loop: combine `reflection_template.md`, `self_improvement_template.md`, and `learning_capture_template.md`.
- User updates and handoffs: use `communication_template.md` during work and `session_summary_template.md` at the end.
- High-uncertainty tasks: start with `tool_selection_template.md` and `risk_assessment_template.md` before execution.
