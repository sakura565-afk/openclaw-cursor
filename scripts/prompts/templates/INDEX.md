# Prompt templates index

Canonical templates live in `scripts/prompts/templates/`. A frozen copy for offline reference is kept under `.learnings/prompts/`.

| Template | One-line description | When to use |
|----------|----------------------|-------------|
| `code_review.md` | Structured review for correctness, security, and maintainability. | PRs, risky diffs, or pre-merge quality gates. |
| `bug_hunt.md` | Hypothesis-driven investigation before writing a fix. | Flaky failures, unknown root cause, or broad symptoms. |
| `refactor_plan.md` | Phased migration plan with risks, tests, and non-goals. | Tech debt paydown without accidental behavior drift. |
| `research_synthesis.md` | Evidence-weighted brief from multiple sources. | Tooling choices, literature scans, competitive notes. |
| `step_by_step.md` | Checkable runbook with prerequisites and verification. | Onboarding, ops procedures, teaching a workflow. |
| `error_debug.md` | Classify and localize a concrete error signature quickly. | Stack traces, CI failures, or log-line triage. |

## CLI

From the repository root:

```bash
python scripts/prompts/cli.py list
python scripts/prompts/cli.py render bug_hunt --var 'bug_description=NULL pointer crash' --var 'severity=high'
```

Use `--strict` to fail when any `{{placeholder}}` lacks a `--var` value. Use `-o path` to write output to a file.
