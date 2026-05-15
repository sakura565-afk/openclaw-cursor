# Error analysis

**Purpose:** Capture a single failure end-to-end so future runs avoid the same trap and fixes stay traceable.

**Placeholders:** `{{agent_role}}`, `{{error_description}}`, `{{root_cause}}`, `{{fix_applied}}`, `{{lessons_learned}}`, `{{tags}}`, `{{context}}`, `{{output_format}}`

**Rendering:** Replace every `{{...}}` before sending the prompt. If a value is unknown, use `none` or `not provided` (see `_placeholders.md`).

---

You are: **{{agent_role}}**

Use the sections below in order. Prefer facts (logs, commands, commits) over guesses; label inference explicitly.

## Error description

What broke: symptom, expected vs actual, and where it surfaced (service, job, file, step). Include primary evidence.

{{error_description}}

## Root cause

The underlying reason—not only the trigger. If multiple causes exist, list them and mark the dominant one. Tie claims to evidence.

{{root_cause}}

## Fix applied

The change, workaround, or process adjustment (patch summary, config diff, rollback, or `none yet`). Note blast radius and whether the fix is verified.

{{fix_applied}}

## Lessons learned

What to do differently next time (checklists, tests, monitoring, docs). One bullet per lesson; avoid blame.

{{lessons_learned}}

## Tags

Short labels for search (e.g. `flaky-test`, `permissions`, `dependency-pin`). Comma-separated is fine.

{{tags}}

## Additional context

{{context}}

## Instructions

1. Separate **symptom** from **root cause**; do not conflate them.
2. If root cause is uncertain, rank hypotheses and list the smallest test that would confirm each.
3. Lessons must be actionable for the next similar task, not generic advice.
4. Keep tags consistent with your team's taxonomy when one exists.

## Output

{{output_format}}

---

## Example (filled)

**agent_role:** Backend engineer reviewing a failed nightly job.

**error_description:** `scripts/nightly_pipeline.py` exited 1 at 03:12 UTC. Expected: all three Ollama summarization steps complete. Actual: step 2 timed out after 120s with `ReadTimeout` calling local Ollama on `:11434`. Log excerpt: `requests.exceptions.ReadTimeout: HTTPConnectionPool(host='127.0.0.1', port=11434)`.

**root_cause:** Ollama service was stopped after a host reboot; systemd user unit was not enabled, so nothing restarted it before the cron run.

**fix_applied:** `systemctl --user enable --now ollama`; re-ran pipeline manually—green. Added `scripts/healthcheck.sh` gate at pipeline start.

**lessons_learned:** Enable and verify long-running dependencies after reboot; fail fast in cron with a clear "Ollama unreachable" message instead of timing out inside step 2.

**tags:** `ollama`, `cron`, `infra`, `timeout`

**context:** Pipeline runs on a single-user VPS; no orchestrator restarts services.

**output_format:** Markdown with the five sections above plus a one-line **status** (resolved / mitigated / open).

*(The agent would produce a structured post-mortem following those sections.)*
