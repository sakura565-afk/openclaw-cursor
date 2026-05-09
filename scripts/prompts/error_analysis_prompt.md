# Error Analysis Prompt

Structured template for diagnosing failures, tracing root causes, and proposing fixes. Use after tests fail, commands error, or runtime exceptions occur.

---

## Variables (fill before sending)

| Placeholder | Description |
|-------------|-------------|
| `{{AGENT_NAME}}` | Name or identifier of the acting agent (optional). |
| `{{TASK_CONTEXT}}` | What you were trying to accomplish when the error appeared. |
| `{{ENVIRONMENT}}` | OS, runtime versions, branch, config flags (as relevant). |
| `{{ERROR_ARTIFACT}}` | Full error message, stack trace, log excerpt, or CI output. |
| `{{REPRO_STEPS}}` | Minimal steps to reproduce (or “not reproducible yet”). |
| `{{RECENT_CHANGES}}` | Recent edits, deps, or infra changes that might relate. |
| `{{CONSTRAINTS}}` | Hard limits (e.g. backward compatibility, latency, security). |

---

## Instructions for the responding agent

1. **Restate the failure** in one neutral sentence (symptom vs. underlying cause).
2. **Classify** the error: logic bug, integration/API, environment/config, race/timing, data/schema, security, or unknown.
3. **Trace causality**: list plausible hypotheses ordered by likelihood; cite evidence from `{{ERROR_ARTIFACT}}` and `{{RECENT_CHANGES}}`.
4. **Isolate**: identify the smallest subsystem or function likely responsible; note what would falsify each hypothesis.
5. **Propose fixes**: for the top 1–3 hypotheses, give a concrete change (code path, config, or process), risk level, and how to verify.
6. **Prevention**: one bullet on tests, guards, or monitoring that would catch this class earlier.
7. If information is missing, **list exactly what to collect next** (commands, logs, bisect steps)—do not invent facts.

**Tone:** precise, non-blaming, actionable.

---

## Output format

Use these sections (headings optional but recommended):

1. Summary  
2. Classification  
3. Hypotheses & evidence  
4. Recommended fix(es)  
5. Verification plan  
6. Prevention / follow-ups  
7. Open questions  

---

## Example (filled placeholders)

**`{{TASK_CONTEXT}}`:** Running `pytest tests/test_task_runner.py` after refactoring task cancellation.

**`{{ENVIRONMENT}}`:** Linux, Python 3.11, branch `feature/cancel`, local venv.

**`{{ERROR_ARTIFACT}}`:**
```
AssertionError: expected CancelledError, got None
  File "task_runner.py", line 142, in run_batch
    ...
```

**`{{REPRO_STEPS}}`:** `pytest tests/test_task_runner.py::test_cancel_mid_batch -q`

**`{{RECENT_CHANGES}}`:** Replaced `gather` with manual task bookkeeping.

**`{{CONSTRAINTS}}`:** Must not block event loop longer than 50ms per batch.

**Example response excerpt:**

- **Classification:** logic bug / async lifecycle (task marked done before exception surfaced).
- **Top hypothesis:** cancellation replaces future result with `None` in cleanup path; evidence: stack at `run_batch:142` and test expects `CancelledError`.
- **Fix:** await pending tasks with `return_exceptions=True` and map cancellation to `CancelledError` before clearing handles; add regression test with forced cancel mid-batch.

---

## Empty template (copy-paste)

```
You are an error analyst for {{AGENT_NAME}}.

Context: {{TASK_CONTEXT}}
Environment: {{ENVIRONMENT}}

Error / logs:
{{ERROR_ARTIFACT}}

Reproduction: {{REPRO_STEPS}}
Recent changes: {{RECENT_CHANGES}}
Constraints: {{CONSTRAINTS}}

Follow the Instructions and Output format in scripts/prompts/error_analysis_prompt.md.
```
