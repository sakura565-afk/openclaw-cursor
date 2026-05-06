## Code Review Task Template

### Goal
Perform a high-signal code review focused on correctness, regressions, security, and maintainability.

### System/Persona Inputs
- Primary persona prompt: `{{persona_prompt_path}}`
- Persona references:
  - `AGENTS.md` (if available)
  - `SOUL.md` (if available)

### Task Inputs
- PR title: `{{pr_title}}`
- PR summary: `{{pr_summary}}`
- Changed files: `{{changed_files}}`
- Diff snippet(s): `{{diff_or_patch}}`
- Relevant tests: `{{test_context}}`
- Constraints/policies: `{{constraints}}`

### Instructions
1. Prioritize findings by severity: `critical`, `high`, `medium`, `low`.
2. Focus on:
   - Functional correctness and edge cases.
   - Backward compatibility and behavioral regressions.
   - Security and data handling issues.
   - Performance and scalability concerns.
   - Test coverage quality.
3. For each finding, include:
   - **Why it is a problem**
   - **Evidence** (file/path/line or diff hunk reference)
   - **Concrete fix recommendation**
4. If no major issues are found, explicitly say so and list residual risks/test gaps.
5. Keep comments actionable and specific. Avoid style-only nitpicks unless they create risk.

### Output Format
```markdown
## Findings
### [severity] <short title>
- Evidence: <file/path:line or diff context>
- Impact: <what breaks and how>
- Recommendation: <specific fix>

## Open Questions
- <question or `None`>

## Risk Summary
- <brief risk assessment>
```
