# Tool Discovery Prompt Template

Use this template to document, evaluate, and standardize tools (libraries, CLIs, internal services, or platforms) for a specific workflow.

## Usage Instructions

1. Define the problem before listing tools.
2. Capture constraints (security, licensing, environment, cost).
3. Request side-by-side comparisons and recommendation criteria.
4. Include adoption steps and validation checkpoints.

## Template

```md
# Tool Discovery Request

## Objective
<what_problem_needs_a_tool_and_why_now>

## Workflow Context
- Team/Role: <who_will_use_it>
- Current process: <manual_or_existing_tooling>
- Pain points:
  - <pain_point_1>
  - <pain_point_2>

## Requirements
### Must-Have
- <requirement_1>
- <requirement_2>

### Nice-to-Have
- <optional_requirement_1>
- <optional_requirement_2>

### Constraints
- Platform/Environment: <linux|mac|windows|cloud|on-prem>
- Security/Compliance: <soc2|hipaa|none>
- Licensing/Cost limits: <budget_or_license_constraints>
- Integration constraints: <ci|ide|api|language_constraints>

## Candidate Tools
- Known options:
  - <tool_a>
  - <tool_b>
  - <tool_c>
- Open to new options: <yes_or_no>

## Evaluation Criteria
Please compare candidates on:
1. Capability fit for must-have requirements.
2. Setup complexity and onboarding effort.
3. Operational reliability and maintenance burden.
4. Ecosystem maturity and community support.
5. Cost, licensing, and lock-in risk.

## Requested Output
Please provide:
1. A comparison table of top options.
2. Recommended tool with rationale.
3. Risks/trade-offs and mitigations.
4. Pilot plan (steps, success criteria, rollback plan).
5. Documentation template for team rollout.
```

## Example

```md
# Tool Discovery Request

## Objective
Identify a test data generation tool to create realistic anonymized datasets for integration testing.

## Workflow Context
- Team/Role: backend platform team
- Current process: handwritten fixtures and ad hoc SQL scripts
- Pain points:
  - Fixtures drift from production schema.
  - Data generation is slow and hard to reproduce.

## Requirements
### Must-Have
- Generates relational datasets with referential integrity.
- Supports deterministic seeds for reproducible CI.

### Nice-to-Have
- Native support for PostgreSQL.
- CLI + Python API support.

### Constraints
- Platform/Environment: Linux CI runners and local macOS dev machines
- Security/Compliance: no production PII allowed
- Licensing/Cost limits: open source preferred
- Integration constraints: must run in GitHub Actions and Docker

## Candidate Tools
- Known options:
  - Faker + custom scripts
  - Mockaroo
  - Synthesized.io
- Open to new options: yes

## Evaluation Criteria
Please compare candidates on:
1. Capability fit for must-have requirements.
2. Setup complexity and onboarding effort.
3. Operational reliability and maintenance burden.
4. Ecosystem maturity and community support.
5. Cost, licensing, and lock-in risk.

## Requested Output
Please provide:
1. A comparison table of top options.
2. Recommended tool with rationale.
3. Risks/trade-offs and mitigations.
4. Pilot plan (steps, success criteria, rollback plan).
5. Documentation template for team rollout.
```
