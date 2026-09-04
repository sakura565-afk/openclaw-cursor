# Structured debugging

Use this prompt when you need help diagnosing a bug or unexpected behavior. Replace every `[PLACEHOLDER]` with your own content before sending.

---

## Error or symptom

[Describe the error message, stack trace, or what is going wrong in one or two sentences.]

## Context

- **Environment:** [OS, runtime version, framework, browser, etc.]
- **What you were doing:** [Steps or workflow that led to the issue]
- **Scope:** [Which repo, service, file, or feature is involved]
- **Relevant links or IDs:** [Issue numbers, PRs, docs—if any]

## What you have already tried

1. [Attempt 1 and outcome]
2. [Attempt 2 and outcome]
3. [Add more as needed, or write "None yet."]

## Expected behavior

[What should happen instead?]

## Actual behavior

[What actually happens? Include logs or screenshots if you paste them below.]

## Code or configuration (optional)

```text
[Paste minimal code, config, or commands needed to reproduce the issue.]
```

## Constraints

[Any limits on solutions—e.g. cannot upgrade dependencies, must stay backward compatible, deadline, performance budget.]

---

**Instructions for the assistant:** Work through this systematically: restate the problem, form hypotheses, suggest the smallest next experiment or fix, and call out anything unclear so we can narrow it down.
