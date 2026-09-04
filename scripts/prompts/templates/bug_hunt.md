# Bug hunt ({{bug_description}})

## Description

Systematic exploration to find root causes, reproduction steps, and regression risks. Use when behavior is wrong but the fault location is unclear, or before proposing a patch.

## Variables

Substitutions use mustache-style placeholders in the **task block** below (e.g. `{{` `bug_description` `}}`).

| Variable | Role |
|----------|------|
| `{{` `bug_description` `}}` | One-line symptom (what users or tests observe). |
| `{{` `environment` `}}` | OS, versions, flags, dataset size, hardware if relevant. |
| `{{` `reproduction` `}}` | Steps, scripts, or failing test names/commands. |
| `{{` `severity` `}}` | Impact (e.g. S1 outage, S3 cosmetic) and urgency. |
| `{{` `code_hints` `}}` | Suspected modules, recent commits, logs, or stack traces. |

## Instruction structure

1. **Reproduce**: Turn the supplied reproduction notes into a minimal, deterministic repro if possible (if unknown, propose a repro plan first).
2. **Hypotheses**: List 3–5 plausible causes ordered by likelihood; say how to falsify each quickly.
3. **Investigation plan**: Concrete checks (grep targets, breakpoints, logging, bisect) with expected signals.
4. **Evidence table**: For each hypothesis, note observed vs expected after checks.
5. **Root cause**: Single narrative; include triggering conditions and why it was missed by tests.
6. **Fix options**: Short-term mitigation vs proper fix; trade-offs and blast radius.
7. **Regression**: Tests or monitors to add so this class of bug does not return.

## Examples

**Illustrative:** bug_description = “Export job hangs at 99%”, environment = “Linux, Python 3.11, Celery 5.3”, reproduction = “Run `pytest tests/test_export.py::test_large_csv`”, severity = “S2 revenue-impacting”, code_hints = “`worker/tasks/export.py` last touched in commit abc123”.

## Tips for best results

- Paste the smallest log excerpt that contains timestamps and error codes into the hints field.
- If reproduction is flaky, say so in the environment field and ask for a stability-first repro plan.
- Use severity to steer depth: S1 warrants exhaustive checks; S4 can stay lighter.

---

You are debugging a software issue.

**Summary:** {{bug_description}}

**Severity / impact:** {{severity}}

**Environment:** {{environment}}

**Reproduction:** {{reproduction}}

**Hints (logs, files, commits):** {{code_hints}}

Follow the instruction structure above. Prefer evidence over speculation. If you must guess, label it clearly as a hypothesis and say what would confirm or deny it.
