# Code review

Ask for a thorough review of a change or snippet. Fill in the bracketed sections, then paste the code (or point to files/lines).

---

## Change summary

[One paragraph: what this change does and why it exists.]

## Review focus (optional)

Prioritize feedback on: [e.g. correctness first, then security; or "balance all areas equally."]

## Code to review

**Language / stack:** [e.g. TypeScript, React, Go]

```text
[Paste the diff, full functions, or file paths with line ranges.]
```

## Context the reviewer should know

- **Invariants / contracts:** [APIs, types, or behavior that must not break]
- **Related code:** [Other modules or PRs that interact with this]
- **Non-goals:** [What this change intentionally does not address]

---

**Instructions for the assistant:** Provide structured feedback in four areas:

1. **Style and readability** — naming, structure, comments, consistency with typical patterns for this stack.
2. **Logic and correctness** — edge cases, error handling, race conditions, off-by-one risks, test gaps.
3. **Security** — injection, authz/authn, secrets, unsafe deserialization, dependency risks, trust boundaries.
4. **Performance** — algorithmic complexity, I/O, memory, hot paths, unnecessary work.

For each finding, label severity (suggestion / concern / must-fix) and, when possible, suggest a concrete fix or alternative. End with a short summary of the highest-impact items.
