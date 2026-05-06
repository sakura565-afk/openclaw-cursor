---
# Task decomposition — break a goal into ordered, verifiable steps for an agent.

title: Task Decomposition
purpose: >
  Turn a high-level goal into a checklist of smaller tasks with dependencies,
  acceptance criteria, and clear stopping points for verification.

# Fill every {{placeholder}} when instantiating this template. Omit optional
# sections whose placeholders are empty strings.

parameters:
  - name: agent_role
    required: false
    description: Short description of the agent (e.g. "senior Python engineer").
    default: "software agent"
  - name: primary_goal
    required: true
    description: The outcome the user or system wants achieved.
  - name: background_context
    required: false
    description: Repo, product, or domain facts needed to plan correctly.
  - name: hard_constraints
    required: false
    description: Non-negotiables (deadlines, forbidden edits, stack, style).
  - name: decomposition_depth
    required: false
    description: Desired granularity ("coarse" | "medium" | "fine").
    default: medium
  - name: verification_preferences
    required: false
    description: How to confirm success (tests, manual checks, metrics).

schema_version: 1
---

You are a {{agent_role}}. Decompose the following goal into an executable plan.

## Goal

{{primary_goal}}

## Context

{{background_context}}

## Constraints

{{hard_constraints}}

## Planning instructions

1. **Decomposition depth:** {{decomposition_depth}}.
   - *coarse*: phases and milestones only.
   - *medium*: phases with concrete tasks and owners implied as "agent".
   - *fine*: step-by-step actions with file/module hints where inferable.

2. Produce:
   - A **numbered task list** in execution order.
   - For each task: **inputs**, **actions**, **outputs**, and **done when** (acceptance criteria).
   - **Dependencies** between tasks (which must finish before which).
   - **Risks or unknowns** and how to mitigate or resolve them early.
   - **Verification:** tie completion to {{verification_preferences}} where applicable.

3. If the goal is ambiguous, state **assumptions** explicitly before the plan.

4. End with a **minimal first task** that can start immediately.

Respond in structured sections using clear headings.
