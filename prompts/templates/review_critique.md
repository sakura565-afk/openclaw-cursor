# Review and critique

**Purpose:** Structured feedback on a design, patch, or document without rewriting it wholesale.

**Placeholders:** `{{agent_role}}`, `{{artifact_description}}`, `{{context}}`, `{{constraints}}`, `{{review_focus}}`, `{{output_format}}`

---

You are: **{{agent_role}}**

## Artifact to review

{{artifact_description}}

## Context for reviewers

{{context}}

## Focus areas

What the reader cares about most (security, API stability, UX, performance, etc.):

{{review_focus}}

## Constraints

{{constraints}}

## Instructions

1. Start with a short **verdict** (approve / approve with nits / request changes) with rationale.
2. List **blocking** issues first (correctness, security, data loss, contract breaks), each with location or repro hint if known.
3. Then **non-blocking** suggestions, ordered by impact.
4. Acknowledge what is already strong; avoid generic praise without pointing to specifics.
5. If information is insufficient, say exactly what patch or file excerpt you need.

## Output

{{output_format}}
