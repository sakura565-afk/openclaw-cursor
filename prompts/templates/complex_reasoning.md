# Complex reasoning

**Purpose:** Structured analysis for hard problems: decomposition, trade-offs, and explicit uncertainty.

**Placeholders:** `{{agent_role}}`, `{{reasoning_task}}`, `{{known_premises}}`, `{{constraints}}`, `{{output_format}}`

---

You are: **{{agent_role}}**

## Question or decision

{{reasoning_task}}

## Premises (facts you must treat as given)

{{known_premises}}

## Constraints

{{constraints}}

## Instructions

1. **Frame** the problem: what is being optimized or decided, and what would count as wrong?
2. **Decompose** into sub-questions or components; tackle dependencies first.
3. For each important claim, tag it as **fact** (from premises), **inference**, or **assumption** (if you must assume, keep assumptions few and testable).
4. Compare **options** if applicable: pros, cons, risks, and sensitivity to wrong assumptions.
5. Give a **conclusion** with confidence (high/medium/low) and what evidence would change your mind.

Avoid long unstructured prose; use short sections and lists.

## Output

{{output_format}}
