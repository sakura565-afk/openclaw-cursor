# Code Review Template

> A reusable prompt template for performing a thorough, prioritized code
> review of a diff or changeset. Designed to produce reviews that are
> specific, actionable, and consistent across reviewers (human or model).

---

## Metadata

| Field            | Value                                                     |
| ---------------- | --------------------------------------------------------- |
| Template ID      | `code-review`                                             |
| Version          | `1.0.0`                                                   |
| Category         | Quality assurance / Peer review                           |
| Recommended Use  | Pull-request review, agent self-review before commit      |
| Required Inputs  | `change_summary`, `diff`                                  |
| Optional Inputs  | `context_files`, `coding_standards`, `test_results`, `risk_level`, `target_branch` |

---

## Variables

| Variable               | Type      | Required | Description                                          |
| ---------------------- | --------- | -------- | ---------------------------------------------------- |
| `{{change_summary}}`   | text      | yes      | Author's description of *why* the change exists      |
| `{{diff}}`             | diff      | yes      | Unified diff of the change                           |
| `{{?context_files}}`   | code      | no       | Adjacent code the diff depends on or affects         |
| `{{?coding_standards}}`| text      | no       | Project conventions (lint rules, style guide)        |
| `{{?test_results}}`    | text      | no       | CI output, coverage delta, perf benchmarks           |
| `{{?risk_level}}`      | enum      | no       | `low` / `medium` / `high` — tunes review depth       |
| `{{?target_branch}}`   | string    | no       | Branch the change merges into (e.g. `main`)          |

---

## Prompt

```
# Role
You are an experienced staff engineer reviewing a pull request. You optimize
for *correctness*, then *readability*, then *consistency with the existing
codebase*, in that order. You give feedback that is specific (file + line),
prioritized, and respectful. You do not nitpick when correctness issues
exist.

# Context
## Why this change exists
{{change_summary}}

## Diff under review
{{diff}}

## Surrounding code (read-only context)
{{?context_files}}

## Project coding standards
{{?coding_standards}}

## Test / CI results
{{?test_results}}

## Risk level
{{?risk_level}}

## Target branch
{{?target_branch}}

# Task
1. Identify the *intent* of the change in one sentence and verify the diff
   actually implements that intent.
2. Review for the following review axes, in this order. For each finding,
   record file, line(s), severity, and a concrete suggested change.
   - **Correctness**: bugs, off-by-one, race conditions, null handling,
     incorrect error handling, mismatched contracts.
   - **Security**: injection, authn/authz, secret handling, unsafe
     deserialization, SSRF, dependency risk.
   - **Tests**: coverage of new behavior, meaningful assertions, missing
     edge cases, flaky patterns.
   - **API & contract stability**: backward compatibility, breaking
     changes, undocumented behavior changes.
   - **Performance**: obvious O(n^2) hot paths, unnecessary I/O, allocation
     in tight loops.
   - **Readability & naming**: unclear names, dead code, comments that
     mislead, function length.
   - **Consistency**: style/convention deviations from `coding_standards`
     or surrounding code.
3. Decide an overall verdict: `approve`, `approve with comments`,
   `request changes`, or `block`.
4. If the verdict is `request changes` or `block`, list the *minimum* set
   of changes required to flip to approval.

# Output Format
Respond in Markdown using exactly these sections:

## Intent
One sentence stating what the diff is trying to do, followed by a one-line
judgement of whether the diff achieves it.

## Verdict
`approve` | `approve with comments` | `request changes` | `block`
— followed by a one-sentence rationale.

## Findings
A table sorted by Severity descending, then by file, then by line:

| # | Severity | Axis | File:Line | Finding | Suggested change |
|---|----------|------|-----------|---------|------------------|

Severity values: `Blocker`, `Major`, `Minor`, `Nit`.
- `Blocker`: must fix before merge (correctness/security).
- `Major`: should fix before merge (significant quality issue).
- `Minor`: nice to fix; reviewer would not block on it.
- `Nit`: stylistic; ignore if author disagrees.

## Required Changes
Numbered list of the *minimum* fixes needed to reach approval. If verdict
is `approve`, write `None`.

## Suggested Tests
Bulleted list of additional test cases (with brief assertions) that would
strengthen the change. Omit if existing coverage is already adequate.

## Compliments
Up to three short bullets recognizing genuinely good aspects of the diff.
Skip this section if nothing meaningful applies—do not invent praise.

# Constraints
- Anchor every finding to a specific file and line range from the diff.
- Do not invent code that is not in the diff or context_files.
- Cap the Findings table at 25 rows; if there are more, group lower-severity
  items under a single "Multiple Nits" row with a short summary.
- Adjust depth based on `risk_level`:
  - `low`: focus on Correctness, Tests, and API & contract stability only.
  - `medium`: full review.
  - `high`: full review plus an explicit Security and Performance section
    even if no findings (state "No issues identified").
- Do not approve if any Blocker exists.
- Be direct and brief. Avoid hedging like "you might want to consider".
```

---

## Examples

### Example 1 — Filled-in input (abridged)

```
## Why this change exists
Add server-side validation that rejects user-supplied URLs pointing at
internal IP ranges to prevent SSRF in the webhook subscription endpoint.

## Diff under review
diff --git a/src/webhooks/validate.ts b/src/webhooks/validate.ts
@@
+export function isPublicUrl(raw: string): boolean {
+  const u = new URL(raw);
+  if (u.protocol !== "https:") return false;
+  // TODO: block internal ranges
+  return true;
+}

## Risk level
high
```

### Example 1 — Expected response shape (abridged)

```markdown
## Intent
Reject webhook URLs that target internal addresses to mitigate SSRF — the
diff does *not* fully achieve this; the internal-range check is a TODO.

## Verdict
`block` — the security control the PR claims to add is not implemented.

## Findings
| # | Severity | Axis        | File:Line                          | Finding | Suggested change |
|---|----------|-------------|------------------------------------|---------|------------------|
| 1 | Blocker  | Security    | src/webhooks/validate.ts:4         | TODO leaves SSRF protection unimplemented; function returns `true` for any HTTPS URL including `https://169.254.169.254`. | Resolve hostname, reject RFC1918, loopback, link-local, and CGNAT ranges before returning `true`. |
| 2 | Major    | Correctness | src/webhooks/validate.ts:2         | `new URL(raw)` throws on malformed input; callers will surface a 500. | Wrap in try/catch and return `false` on parse failure. |
| 3 | Minor    | Tests       | (no test file in diff)             | No unit tests for the new function. | Add tests covering loopback, RFC1918, IPv6 ULA, and a happy path. |

## Required Changes
1. Implement the internal-range block list and add coverage for it.
2. Handle URL parse errors without throwing.
3. Add unit tests as listed above.

## Suggested Tests
- `isPublicUrl("http://example.com")` → `false` (non-HTTPS).
- `isPublicUrl("https://10.0.0.1")` → `false`.
- `isPublicUrl("https://[::1]")` → `false`.

## Compliments
- Function is small and single-purpose, which will make it easy to test
  once the TODO is filled in.
```

### Example 2 — Low-risk pure refactor

For a diff that only renames a private helper across one file with passing
tests and `risk_level: low`, the reviewer should:
- Skip Performance, Security, and Readability axes unless something jumps
  out.
- Likely return `approve` with no Findings, an empty Required Changes
  section (`None`), and one short compliment about the rename.

---

## Usage Notes

- Feed the **smallest possible diff** plus the **smallest necessary
  context**. Reviews degrade rapidly when the model has to guess at the
  surrounding code.
- For agent self-review before committing, run this template against your
  own staged diff (`git diff --staged`) and require the verdict to be
  `approve` or `approve with comments` before pushing.
- The Severity scale (`Blocker`/`Major`/`Minor`/`Nit`) is intentionally the
  same wording reviewers use in PR comments so findings can be copy-pasted
  directly.
- Pairs naturally with `error-analysis-template.md` (when the change fixes
  a bug) and with `tool-discovery-template.md` (when the change introduces
  a new dependency).
