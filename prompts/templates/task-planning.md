# Task planning and breakdown

Use this to turn a goal into an ordered plan with rough time estimates. Replace bracketed fields, then ask the assistant to refine or challenge the plan.

---

## Goal

[State the outcome you want in one clear sentence.]

## Background and constraints

- **Deadline or window:** [Date, sprint end, or "none"]
- **People / skills available:** [Solo, pair, team roles]
- **Technical constraints:** [Stack, environments, must-use libraries, "no new deps," etc.]
- **Dependencies:** [Blocked by or blocking other work, external approvals, data access]

## Definition of done

[Checklist-style criteria: e.g. tests pass, docs updated, feature flag off, metrics dashboard live.]

## Rough sizing preference

[Prefer many small steps vs. fewer large chunks; or "your recommendation."]

---

**Instructions for the assistant:**

1. Break the work into **ordered steps** from first to last. Each step should be a concrete, verifiable unit of work.
2. For **each step**, provide:
   - A short title
   - What will be produced or verified when it is complete
   - A **rough time estimate** in hours or half-days (e.g. `2h`, `0.5d`) suitable for planning—not a promise of calendar duration
3. Call out **risks, unknowns, and spikes** (e.g. "may need 0.5d spike to validate API limits").
4. Suggest **parallelizable** work if any steps can run concurrently.
5. End with a **total rough range** (sum of estimates plus buffer for unknowns) and the **single best next step** to start today.

If information is missing, state assumptions explicitly in `[assumption: ...]` form.
