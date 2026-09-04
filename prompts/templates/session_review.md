# Session review

**Purpose:** Structured retrospective after an agent session, sprint slice, or incident so patterns surface and next actions are concrete.

**Placeholders:** `{{agent_role}}`, `{{session_scope}}`, `{{what_went_well}}`, `{{what_went_wrong}}`, `{{insights}}`, `{{next_actions}}`, `{{context}}`, `{{output_format}}`

**Rendering:** Replace every `{{...}}` before sending the prompt. If a value is unknown, use `none` or `not provided` (see `_placeholders.md`).

---

You are: **{{agent_role}}**

## Session scope

What time range, task, or transcript this review covers:

{{session_scope}}

## What went well

Concrete wins: decisions, tools, collaborations, or metrics. Explain why they worked.

{{what_went_well}}

## What went wrong

Problems, misses, or surprises—technical and process. Separate facts from interpretation.

{{what_went_wrong}}

## Insights

Synthesize patterns, trade-offs, or hypotheses worth testing. Mark each as **observation** or **validated learning**.

{{insights}}

## Next actions

Per item: owner (if known), outcome, and a crisp "done" definition. Prefer a small number of high-leverage actions.

{{next_actions}}

## Additional context

Logs, links, metrics, or constraints the reviewer should honor:

{{context}}

## Instructions

1. Balance positives and negatives; avoid generic praise without specifics.
2. Tie each insight to at least one concrete example from the session.
3. Next actions must be verifiable; avoid vague "improve documentation" without scope.
4. If the session is incomplete, say what is still open and what blocked progress.

## Output

{{output_format}}

---

## Example (filled)

**agent_role:** Staff engineer reviewing an autonomous coding session.

**session_scope:** 2026-05-14, branch `feature/batch-retry` — implement retry wrapper for `ollama_batch.py` and add tests.

**what_went_well:** Agent read existing tests first and matched `PromptResult` dataclass style; added three focused unit tests without flaky network calls.

**what_went_wrong:** First implementation retried on all exceptions including `KeyboardInterrupt`; CI caught it. Two tool calls repeated the same `git status` without new information.

**insights:** Observation — retries need an allowlist of exception types. Validated learning — mocking `subprocess.run` at the module boundary keeps tests fast.

**next_actions:** (1) Merge after fixing interrupt handling — done when PR green. (2) Add one line to `scripts/README.md` documenting retry env vars — done when doc PR merged.

**context:** Repo uses `unittest` + `mock`; no pytest in CI for this package.

**output_format:** Markdown: Executive summary (3 bullets), Wins, Misses, Insights table (observation vs validated), Next actions checklist.

*(The agent would produce a structured session retrospective.)*
