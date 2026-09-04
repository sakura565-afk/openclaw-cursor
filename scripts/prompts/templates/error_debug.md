# Error debugging ({{error_signature}})

## Description

Triage a specific error message, stack trace, or failing output: classify, localize, and propose the smallest fix or next diagnostic.

## Variables

Substitutions use mustache-style placeholders in the **task block** below (e.g. `{{` `error_signature` `}}`).

| Variable | Role |
|----------|------|
| `{{` `error_signature` `}}` | Error type, code, or first distinctive line. |
| `{{` `command_or_context` `}}` | Command, job name, request path, or UI flow that triggers it. |
| `{{` `logs_excerpt` `}}` | Short relevant log or traceback (redact secrets). |
| `{{` `expected_behavior` `}}` | What should happen instead. |
| `{{` `recent_changes` `}}` | Deploys, config edits, dependency bumps, or “unknown”. |

## Instruction structure

1. **Classify**: Error family (syntax, dependency, resource, logic, infra) with rationale.
2. **Localize**: Most likely file/function/line or subsystem; secondary suspects.
3. **Minimal repro**: Shortest path to trigger; note if non-deterministic.
4. **Root cause chain**: From symptom to underlying mistake or broken assumption.
5. **Fix**: Patch-level guidance or config change; include validation command.
6. **Prevention**: Test, assertion, lint rule, or monitor to catch this earlier next time.

## Examples

**Illustrative:** error_signature = “`ModuleNotFoundError: No module named 'yaml'`”, command_or_context = “`python scripts/nightly_pipeline.py` in CI”, logs_excerpt = “traceback top 15 lines”, expected_behavior = “pipeline runs”, recent_changes = “switched base image to slim”.

## Tips for best results

- Redact tokens in the logs excerpt but keep structure (URLs, SQL shapes) when useful.
- If recent changes are unknown, list the top three change classes to verify first.
- Pair this template with `bug_hunt.md` when the error is only a surface symptom of a deeper bug.

---

You are an on-call engineer debugging an error.

**Error:** {{error_signature}}

**Context / command:** {{command_or_context}}

**Logs / traceback:** {{logs_excerpt}}

**Expected behavior:** {{expected_behavior}}

**Recent changes:** {{recent_changes}}

Follow the instruction structure above. Prefer the smallest verifiable next step. If multiple causes remain plausible, present them as a ranked list with disambiguating checks.
