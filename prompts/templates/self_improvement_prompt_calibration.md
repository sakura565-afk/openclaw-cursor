---
title: Self-improvement — prompt and instruction calibration
category: self_improvement
---

## Purpose

Turn a completed interaction into **better prompts or system instructions**: sharper constraints, fewer omissions, clearer success criteria.

## When to use

- After an agent misunderstood scope or skipped verification steps.
- When refining reusable prompts for a team library (like this folder).
- When converting one-off success into a repeatable playbook.

## Placeholders

| Placeholder | Description |
|-------------|-------------|
| `{{ORIGINAL_PROMPT}}` | What was asked initially |
| `{{ACTUAL_BEHAVIOR}}` | What the assistant or process did |
| `{{IDEAL_BEHAVIOR}}` | What should have happened |
| `{{ENVIRONMENT}}` | Tools, repo layout, policies |
| `{{EVAL_CRITERIA}}` | How you judge quality (tests pass, diff size, tone) |

## Usage example (filled)

**Original:** “Speed up the model list.”  
**Actual:** Cached subprocess output but broke live refresh after pull.  
**Ideal:** Speed up without stale reads; document cache TTL or invalidate on mutations.  
**Environment:** Python CLI, unittest CI.  
**Criteria:** No behavior change without explicit flags; tests green.

---

## Template

Improve prompts/instructions using concrete before/after artifacts.

**Baseline**

- Original request or instruction set:

```
{{ORIGINAL_PROMPT}}
```

- Actual behavior / output summary: {{ACTUAL_BEHAVIOR}}
- Desired behavior / output summary: {{IDEAL_BEHAVIOR}}
- Environment & constraints: {{ENVIRONMENT}}
- Evaluation criteria: {{EVAL_CRITERIA}}

**Instructions**

1. Diagnose **failure mode** (ambiguity, missing constraints, wrong abstraction level, missing verification hook).
2. Rewrite the prompt into **two variants**:
   - **Minimal patch:** smallest wording change likely to fix the gap.
   - **Robust rewrite:** adds structure (sections), explicit constraints, acceptance checks, and negative instructions (what not to do).
3. List **placeholders** the robust template should keep for reuse.
4. Add a **preflight checklist** the executor should confirm before editing code (e.g. branch, tests, scope).

**Output**

- Side-by-side: Original vs Robust rewrite (full text).
- Short rationale (5 bullets max) tying changes to diagnosed failure mode.
