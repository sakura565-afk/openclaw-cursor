# Template: Review code

## When to use

Before merge or when learning a foreign module, you want structured feedback on correctness, security, and maintainability—not vague praise.

## Inputs

| Placeholder | Required | Description |
| ----------- | -------- | ----------- |
| `{{ARTIFACT}}` | Yes | Patch, files, PR description, or pasted snippets to review. |
| `{{THREAT_MODEL}}` | Optional | Trust boundaries (user input, auth, network, filesystem). |
| `{{FOCUS}}` | Optional | Areas to emphasize: performance, API design, a11y, etc. |

## Prompt body

```text
Perform a code review on the following. Be direct and specific: reference symbols, functions, or line-level logic when possible.

## Artifact
{{ARTIFACT}}

## Threat model / trust boundaries
{{THREAT_MODEL}}

## Focus areas
{{FOCUS}}

## Output format
1. **Summary** — 2–3 sentences on overall quality and merge readiness.
2. **Blockers** — issues that must be fixed (correctness, security, data loss).
3. **Should-fix** — important but not ship-stopping; suggest patches or patterns.
4. **Nice-to-have** — style, micro-optimizations, docs.
5. **Questions** — assumptions you need confirmed.

If something is good, say why briefly (what pattern or invariant it upholds). Avoid generic compliments.
```
