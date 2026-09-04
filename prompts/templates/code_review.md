# Code review task

**Purpose:** Produce a structured, actionable code review that balances correctness, security, maintainability, and project conventions—suitable for pre-merge or agent-generated PR feedback.

**When to use:** On pull requests, draft patches, or agent-proposed diffs when you need consistent review depth and explicit severity levels.

---

## Variables to fill

| Variable | Description |
|----------|-------------|
| `{{agent_role}}` | Reviewer stance (e.g. “staff engineer familiar with this codebase”). |
| `{{repository_context}}` | Repo, module, or service under review. |
| `{{change_summary}}` | Author’s summary or high-level intent of the change. |
| `{{diff_or_files}}` | Diff text, file list, or instructions to locate the change set. |
| `{{review_focus}}` | Extra emphasis: performance, API design, tests, accessibility, etc. |
| `{{project_standards}}` | Linters, style guides, ADRs, or links to internal standards. |
| `{{risk_profile}}` | Data sensitivity, prod impact, migration risk (`low` / `medium` / `high`). |
| `{{output_contract}}` | Required review format (see example for a default). |

---

## Prompt body (render after filling variables)

You are **{{agent_role}}** reviewing a change in **{{repository_context}}**.

### Change context

**Intent:** {{change_summary}}

**Artifacts to review:** {{diff_or_files}}

### Focus and standards

- **Review focus:** {{review_focus}}
- **Standards / conventions:** {{project_standards}}
- **Risk profile:** {{risk_profile}}

### Instructions

1. **Understand first:** Briefly restate what the change does in one paragraph (no praise padding).
2. **Findings:** List issues grouped by severity: **Blocker**, **Major**, **Minor**, **Nit** (use only categories that apply).
3. For each finding: **location** (file:region or symbol), **problem**, **why it matters** (tie to correctness, security, regressions, or maintainability), **concrete fix** (patch-level suggestion or pseudocode when helpful).
4. Call out **what went well** in at most three bullets (specific, not generic).
5. If information is missing to decide, ask **targeted** questions—do not speculate on unseen code.
6. Align suggestions with **{{project_standards}}**; if a standard conflicts with common practice, note the tradeoff.

### Output

{{output_contract}}

---

## Example (filled)

**agent_role:** Backend reviewer with Go and PostgreSQL production experience.

**repository_context:** `acme/billing`, PR touching `internal/refund/processor.go` and tests.

**change_summary:** Add idempotency key to refund processor to prevent double refunds.

**diff_or_files:** Use the attached unified diff in the task payload (omitted here for brevity).

**review_focus:** Correctness under concurrency and transaction boundaries.

**project_standards:** `golangci-lint` config in repo; errors must use `fmt.Errorf` with `%w`; no `panic` in handlers.

**risk_profile:** high (money movement).

**output_contract:** Markdown: Summary (5 lines max), Blockers, Majors, Minors, Nits, Test gaps, Quick verdict (approve / approve with nits / request changes) with one-sentence rationale.

*(Rendered prompt = body above with variables substituted.)*
