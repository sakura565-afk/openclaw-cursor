# Placeholder conventions (read once)

All templates under `prompts/templates/` use **Mustache-style** placeholders: `{{name}}`.

| Placeholder | Meaning |
|-------------|---------|
| `{{agent_role}}` | Short role line (e.g. “senior backend engineer”). |
| `{{objective}}` | What success looks like in one or two sentences. |
| `{{context}}` | Facts, links, file paths, logs, or constraints the agent must honor. |
| `{{constraints}}` | Hard limits (no network, language, style, time). |
| `{{output_format}}` | Desired structure (bullets, JSON schema, diff-only, etc.). |
| `{{failure_evidence}}` | Errors, stack traces, exit codes, flaky symptoms. |
| `{{attempted_fixes}}` | What was already tried (omit or write “none”). |
| `{{reasoning_task}}` | The question or decision to analyze. |
| `{{known_premises}}` | Given facts; distinguish from assumptions. |
| `{{task_plan}}` | High-level steps or backlog (may be empty for the agent to author). |
| `{{verification_criteria}}` | How to confirm each step or the final result. |
| `{{error_description}}` | Symptom, expected vs actual, and primary evidence for a failure. |
| `{{root_cause}}` | Underlying reason for a failure (not only the trigger). |
| `{{fix_applied}}` | Patch, workaround, or process change applied (or `none yet`). |
| `{{lessons_learned}}` | Actionable takeaways for the next similar task. |
| `{{tags}}` | Short searchable labels (comma-separated). |
| `{{session_scope}}` | Time range, task, or transcript covered by a review. |
| `{{what_went_well}}` | Concrete wins from a session or sprint slice. |
| `{{what_went_wrong}}` | Problems, misses, or surprises (facts vs interpretation). |
| `{{insights}}` | Synthesized patterns; mark observation vs validated learning. |
| `{{next_actions}}` | Verifiable follow-ups with clear done definitions. |
| `{{repository_or_project}}` | Repo or service name for a code review. |
| `{{change_description}}` | Author intent or summary of the change under review. |
| `{{diff_or_files}}` | Diff, file list, or excerpt to review. |
| `{{review_focus}}` | What reviewers should prioritize (security, API, perf, etc.). |
| `{{known_constraints}}` | Hard limits for planning (time, stack, scope). |
| `{{existing_plan}}` | Draft plan to refine, or empty for the agent to author. |
| `{{risks_and_dependencies}}` | Blockers, external deps, or ordering constraints. |

**Rendering:** Replace every `{{...}}` before sending the prompt. If a value is unknown, use a literal like `none` or `not provided` so the model does not invent hidden context.

**Self-improvement CLI:** `python -m src.self_improvement.prompts list|show|placeholders|render <id>` loads templates from this directory (`error_analysis`, `session_review`, `code_review`, `task_planning`).
