# Self-Improvement Template

## Purpose

Use this template to identify improvement opportunities in agent behavior, workflows, decision quality, and execution habits.

## Recommended placeholders

- `{{context}}`: Recent session context, responsibilities, and environment details
- `{{task}}`: The work the agent attempted or completed
- `{{goal}}`: The desired standard for performance or outcome quality
- `{{observations}}`: Concrete examples of what went well or poorly
- `{{metrics}}`: Any quantitative signals such as success rate, latency, retries, or defects
- `{{constraints}}`: Limits on tools, time, permissions, or available context

## Template

```md
You are evaluating an OpenClaw agent to identify the highest-leverage improvement areas.

Context:
{{context}}

Task:
{{task}}

Goal:
{{goal}}

Observations:
{{observations}}

Metrics:
{{metrics}}

Constraints:
{{constraints}}

Instructions:
1. Assess performance against the stated goal using concrete evidence.
2. Separate strengths from recurring weaknesses.
3. Identify process issues, judgment errors, tooling gaps, and communication gaps.
4. Focus on improvements that are specific, actionable, and likely to produce measurable benefit.
5. Prioritize recommendations by expected impact and implementation effort.
6. Include a short experiment or habit change for each major improvement area.

Expected output format:
- Performance snapshot:
- What went well:
  - ...
- Improvement areas (ranked):
  1. Area:
     - Evidence:
     - Why it matters:
     - Recommended change:
     - Small experiment:
     - Success signal:
- Process changes to adopt:
  - ...
- Skills or knowledge to strengthen:
  - ...
- Follow-up review checkpoint:
```
