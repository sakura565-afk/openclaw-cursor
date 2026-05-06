---
# Self-improvement — reflect on runs and propose concrete improvements.

title: Self-Improvement
purpose: >
  After tasks or episodes, extract lessons, update heuristics, and define
  measurable improvements without vague platitudes.

parameters:
  - name: episode_summary
    required: true
    description: What happened in the session or task (outcome, scope, duration if known).
  - name: goals_vs_outcomes
    required: false
    description: Original goals compared to what was actually achieved.
  - name: failures_or_friction
    required: false
    description: Errors, retries, confusion, tool issues, or user corrections.
  - name: what_worked
    required: false
    description: Strategies, tools, or patterns that paid off.
  - name: constraints_and_resources
    required: false
    description: Limits faced (context, permissions, missing docs).
  - name: improvement_horizon
    required: false
    description: Focus on "next task", "next week of usage", or "system design".
    default: "next task"

schema_version: 1
---

You are conducting **structured self-improvement reflection** for an autonomous agent.

## Episode summary

{{episode_summary}}

## Goals vs outcomes

{{goals_vs_outcomes}}

## Failures and friction

{{failures_or_friction}}

## What worked

{{what_worked}}

## Constraints and resources

{{constraints_and_resources}}

## Improvement horizon

{{improvement_horizon}}

## Reflection instructions

1. **Outcome assessment** — grade success on a simple scale (full / partial / failed) with justification.
2. **Root patterns** — recurring mistakes or strengths; separate **one-off noise** from **systematic** issues.
3. **Actionable improvements** — for each issue, specify:
   - **Change** (behavior, prompt, checklists, tool use, tests to add).
   - **Trigger** — when to apply this next time.
   - **Verification** — how to tell the improvement succeeded.
4. **Anti-goals** — what not to optimize (avoid overfitting to a single episode).
5. **Memory / knowledge updates** — bullet list of **factual** items worth storing for future sessions (if your system supports memory).
6. **Single priority** — the **one** change with the best effort-to-impact ratio for {{improvement_horizon}}.

Be honest about uncertainty; prefer concrete edits over generic advice.
