# Error analysis

Paste an error and context; ask for root-cause hypotheses and fixes. Complete the sections below.

---

## Raw error

```text
[Paste the full error message, stack trace, or HTTP response body.]
```

## When it occurs

[Always / intermittently / only in production / after deploy / under load—describe timing and triggers.]

## Surrounding context

- **System:** [Service name, job name, CLI command, URL path]
- **Inputs:** [Request payload shape, file being processed, user action—redact secrets]
- **Recent changes:** [Deploys, config edits, migrations, dependency bumps—if known]

## Impact

[Who is affected, severity, workaround in use—if any.]

## What you need

[Choose one or combine: "most likely root cause," "ordered list of fixes," "how to reproduce locally," "what to log next."]

---

**Instructions for the assistant:**

1. **Parse** the error: identify error type, failing component, and any codes or line references.
2. **Hypothesize** root causes ranked by likelihood; note what evidence supports or contradicts each.
3. For the top hypotheses, suggest **concrete fixes** (code changes, config, infra) and **verification steps** after each fix.
4. If information is insufficient, list **specific questions** or **data to collect** (logs, flags, repro steps) instead of guessing.
5. Call out **security or data-loss** implications if the error might expose those risks.

Keep the answer actionable: prefer "do X, then verify Y" over generic advice.
