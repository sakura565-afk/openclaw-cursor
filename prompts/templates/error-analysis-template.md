# Error Analysis Template

> A reusable prompt template for performing rigorous, evidence-based root-cause
> analysis of an error, exception, stack trace, or unexpected behavior.

---

## Metadata

| Field            | Value                                    |
| ---------------- | ---------------------------------------- |
| Template ID      | `error-analysis`                         |
| Version          | `1.0.0`                                  |
| Category         | Debugging / Diagnostics                  |
| Recommended Use  | Triage incidents, debug failing tests, investigate production errors |
| Required Inputs  | `error_message`, `code_context`          |
| Optional Inputs  | `stack_trace`, `environment`, `recent_changes`, `repro_steps`, `logs` |

---

## Variables

Replace each `{{placeholder}}` with the corresponding value before sending the
prompt to the model. Variables wrapped in `{{?optional_field}}` may be omitted
if the information is not available.

| Variable               | Type      | Required | Description                                    |
| ---------------------- | --------- | -------- | ---------------------------------------------- |
| `{{error_message}}`    | string    | yes      | The raw error message or exception text        |
| `{{code_context}}`     | code      | yes      | Surrounding source code (with file/line refs)  |
| `{{?stack_trace}}`     | text      | no       | Full stack/backtrace if available              |
| `{{?environment}}`     | text      | no       | Runtime, OS, language version, dependencies    |
| `{{?recent_changes}}`  | diff/text | no       | Recent commits, diffs, or config changes       |
| `{{?repro_steps}}`     | list      | no       | Steps to reproduce the issue                   |
| `{{?logs}}`            | text      | no       | Relevant log excerpts (timestamps preserved)   |

---

## Prompt

```
# Role
You are a senior software engineer specializing in debugging and root-cause
analysis. You reason about failures using the scientific method: form
hypotheses, weigh evidence, and rank likely causes by probability. You never
guess when evidence is missing—you ask for it.

# Context
An error has occurred in the system described below. Your goal is to identify
the most likely root cause(s), explain the failure mechanism, and propose a
verified fix.

## Error message
{{error_message}}

## Code context
{{code_context}}

## Stack trace
{{?stack_trace}}

## Environment
{{?environment}}

## Recent changes
{{?recent_changes}}

## Reproduction steps
{{?repro_steps}}

## Relevant logs
{{?logs}}

# Task
1. Summarize the failure in one sentence (what broke, where, when).
2. Enumerate every plausible root cause. For each, cite the specific evidence
   (line numbers, log lines, stack frames) that supports or refutes it.
3. Rank the candidates by likelihood (High / Medium / Low) and justify the
   ranking.
4. Recommend the minimum-risk fix for the top candidate. Include the exact
   code change as a unified diff if possible.
5. Propose a regression test that would have caught this bug.
6. List any missing information that, if provided, would meaningfully change
   your analysis.

# Output Format
Respond in Markdown using exactly the following sections, in this order:

## Summary
One sentence.

## Hypotheses
A numbered list. For each hypothesis use this sub-structure:
- **Hypothesis N — <short title>** (Likelihood: High|Medium|Low)
  - Evidence for: ...
  - Evidence against: ...
  - Failure mechanism: ...

## Recommended Fix
Describe the fix in prose, then provide a unified diff in a fenced ```diff
block. If the fix touches multiple files, include one diff per file.

## Regression Test
Provide the test as a fenced code block in the appropriate language.

## Open Questions
Bulleted list of information that would sharpen the diagnosis.

# Constraints
- Cite line numbers from the provided code context whenever you reference it.
- Do not invent functions, modules, or APIs that are not present in the
  context.
- If the evidence is insufficient to identify a single root cause, say so
  explicitly in the Summary and explain what is missing under Open Questions.
- Prefer the smallest correct fix over a sweeping refactor.
```

---

## Examples

### Example 1 — Filled-in input

```
## Error message
TypeError: Cannot read properties of undefined (reading 'id')

## Code context
// src/services/user.ts
42  export async function getUserName(userId: string) {
43    const user = await db.users.findOne({ id: userId });
44    return user.profile.name; // <— failure here
45  }

## Stack trace
TypeError: Cannot read properties of undefined (reading 'name')
    at getUserName (src/services/user.ts:44:23)
    at handler (src/api/users.ts:18:12)

## Environment
Node.js 20.11.1, TypeScript 5.4, MongoDB 6.0

## Recent changes
Commit a1b2c3d removed the `profile` sub-document from new user records.
```

### Example 1 — Expected response shape (abridged)

```markdown
## Summary
`getUserName` dereferences `user.profile` without verifying that the user or
their profile exists, causing a TypeError when either is missing.

## Hypotheses
1. **Hypothesis 1 — Missing `profile` sub-document on new users**
   (Likelihood: High)
   - Evidence for: commit `a1b2c3d` removed `profile` from new records;
     stack trace points at `user.profile.name` (src/services/user.ts:44).
   - Evidence against: none observed.
   - Failure mechanism: `user.profile` is `undefined`, so reading `.name`
     throws.
2. **Hypothesis 2 — User not found**
   (Likelihood: Medium)
   - ...

## Recommended Fix
Guard against missing user and profile before dereferencing.

```diff
--- a/src/services/user.ts
+++ b/src/services/user.ts
@@
 export async function getUserName(userId: string) {
   const user = await db.users.findOne({ id: userId });
-  return user.profile.name;
+  if (!user) throw new NotFoundError(`user ${userId}`);
+  return user.profile?.name ?? null;
 }
```

## Regression Test
```ts
test("returns null when profile is missing", async () => {
  await db.users.insertOne({ id: "u1" });
  expect(await getUserName("u1")).toBeNull();
});
```

## Open Questions
- Should missing profiles return `null` or trigger a backfill job?
```

### Example 2 — When evidence is insufficient

If the user supplies only an error message with no code context, the model
should respond with a Summary that says the cause cannot be determined and
list the missing inputs (code, stack trace, recent changes) under Open
Questions instead of speculating.

---

## Usage Notes

- This template is **language-agnostic**. The model adapts the diff and test
  syntax to whatever language appears in `code_context`.
- For production incidents, always include `logs` and `environment` so the
  model can correlate timestamps and rule out environmental causes.
- When chaining templates, the output of `error-analysis` pairs naturally
  with the `code-review-template.md` to validate the proposed fix.
