---
# Code review — structured review for quality, safety, and maintainability.

title: Code Review
purpose: >
  Review changes or code for correctness, edge cases, security, performance,
  readability, and alignment with stated intent.

parameters:
  - name: change_summary
    required: false
    description: What the change is supposed to do (author intent or PR title body).
  - name: scope_description
    required: true
    description: What is under review (diff, files, feature area, or pasted code).
  - name: code_or_diff
    required: true
    description: The code, diff, or reference to artifacts to review.
  - name: review_focus
    required: false
    description: Emphasis areas (e.g. "thread safety", "API compatibility").
  - name: risk_level
    required: false
    description: Contextual risk hint ("low" | "medium" | "high") for prioritization.
    default: medium
  - name: language_or_stack
    required: false
    description: Primary language/framework for idioms and tooling.
  - name: testing_context
    required: false
    description: Existing tests, coverage gaps, or how to run tests.

schema_version: 1
---

You are performing a code review. Be constructive and specific.

## Intent (from author)

{{change_summary}}

## Scope

{{scope_description}}

## Risk context

**Review focus:** {{review_focus}}

**Assessed risk level:** {{risk_level}}

**Stack:** {{language_or_stack}}

**Testing context:** {{testing_context}}

## Code / diff

```
{{code_or_diff}}
```

## Review instructions

1. **Summary** — two or three sentences on overall quality and readiness.
2. **Strengths** — what works well.
3. **Issues** — grouped by severity:
   - **Blockers** — correctness, security, data loss, broken contracts.
   - **Important** — bugs, races, performance pitfalls, maintainability.
   - **Nits** — style, naming, small simplifications (optional to fix).
4. For each issue: **location** (file/line or excerpt), **problem**, **suggestion**, and **rationale**.
5. **Questions** — only those that unblock a better review or fix.
6. **Test recommendations** — cases or assertions to add.

Assume {{risk_level}} risk means: prioritize blockers and important issues accordingly; skip nits if risk is high until blockers are resolved.
