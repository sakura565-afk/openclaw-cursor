# Code Review Prompt

Review diffs or proposed changes for correctness, security, maintainability, and test gaps—prioritized and actionable.

---

## Variables (fill before sending)

| Placeholder | Description |
|-------------|-------------|
| `{{AGENT_NAME}}` | Reviewer persona label (optional). |
| `{{CHANGE_SUMMARY}}` | Author’s intent in 2–4 sentences. |
| `{{DIFF_OR_CODE}}` | Patch, PR diff, or relevant files/snippets. |
| `{{CONTEXT}}` | Architecture notes, callers, data contracts, rollout plan. |
| `{{RISK_PROFILE}}` | e.g. user-facing, auth, payments, PII, low-risk utility. |
| `{{TEST_COMMANDS}}` | How to run tests/lint relevant to this change. |

---

## Instructions for the responding agent

1. **Intent check:** Does `{{DIFF_OR_CODE}}` implement `{{CHANGE_SUMMARY}}`? Note any mismatch first.
2. **Severity-tagged findings:** For each issue use **Blocker / Major / Minor / Nit** with file:line (or hunk reference if line numbers absent).
3. **Categories to cover** (skip only if clearly N/A): correctness & edge cases; concurrency; errors & resource cleanup; security & secrets; performance; API/back-compat; readability & naming; tests & observability.
4. **Positive observations:** Brief list of what was done well (specific).
5. **Suggested changes:** Ordered by severity; prefer minimal diffs; avoid rewrite-the-world unless Blocker.
6. Do not shame the author; **assume good intent**. Cite uncertainty explicitly (“possible race—needs runtime confirmation”).
7. If `{{DIFF_OR_CODE}}` is too large, propose **incremental review slices** instead of skimming everything superficially.

---

## Output format

1. Intent alignment  
2. Summary verdict (ship / ship with fixes / hold) — **verdict must reference risk profile**  
3. Findings (table or bulleted list with severity)  
4. Strengths  
5. Recommended follow-ups (tests, docs, tickets)  
6. Optional: suggested patch sketch (only if small)  

---

## Example (filled placeholders)

**`{{CHANGE_SUMMARY}}`:** Add timeout to HTTP client wrapper defaulting to 30s.

**`{{RISK_PROFILE}}`:** Internal batch jobs; no user PII in this path.

**`{{DIFF_OR_CODE}}`:** (illustrative) `timeout=30` added to `session.get`; callers unchanged.

**Example finding:**

- **Major:** Several callers relied on blocking indefinitely for long polls; default timeout may break `poll_until_ready`. Mitigation: opt-in parameter or per-call override documented in `CONTEXT`.

---

## Empty template (copy-paste)

```
You are {{AGENT_NAME}} conducting a code review.

Author intent:
{{CHANGE_SUMMARY}}

Risk profile:
{{RISK_PROFILE}}

Diff / code:
{{DIFF_OR_CODE}}

Context:
{{CONTEXT}}

Tests / lint:
{{TEST_COMMANDS}}

Follow the Instructions and Output format in scripts/prompts/code_review_prompt.md.
```
