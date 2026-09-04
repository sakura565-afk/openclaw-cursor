# Documentation update

**Purpose:** Align docs (README, ADRs, API reference, runbooks) with the current system and audience—clarity first, minimal churn.

**Placeholders:** `{{agent_role}}`, `{{repository_or_project}}`, `{{doc_audience}}`, `{{doc_types}}`, `{{change_summary}}`, `{{source_of_truth}}`, `{{context}}`, `{{constraints}}`, `{{output_format}}`

---

You are: **{{agent_role}}**

## Documentation task

**Project / repo:** {{repository_or_project}}

**Audience (new hire, operator, library consumer, partner integrator, etc.):**

{{doc_audience}}

**Document types to create or update (README, OpenAPI, internal wiki export, runbook, ADR):**

{{doc_types}}

**What changed in the product or code that docs must reflect:**

{{change_summary}}

**Source of truth (links, paths to code, design docs the text must match):**

{{source_of_truth}}

## Additional context

{{context}}

## Constraints

{{constraints}}

## Instructions

1. List **stale or missing** sections relative to the source of truth—be specific (heading or path).
2. Propose **revised outline** only if structure is the problem; otherwise keep structure and patch content.
3. For each updated section: **intent**, **key facts**, **procedures** (numbered), and **examples** where helpful.
4. Use **consistent terminology**; define acronyms on first use in each doc.
5. Add **troubleshooting** or **FAQ** only if operators/integrators are in the audience.
6. If facts are uncertain, mark **TBD** with what evidence would resolve them—do not invent behavior.

## Output

{{output_format}}

---

## Example (filled)

**agent_role:** Technical writer–engineer hybrid.

**repository_or_project:** `data-pipeline`

**doc_audience:** On-call engineers rotating into the service.

**doc_types:** `docs/runbook.md`, `README.md` “Deploy” section.

**change_summary:** Deployments now use blue/green; old “restart workers” procedure is wrong.

**source_of_truth:** `deploy/bluegreen.tf`, `scripts/rollout.sh`, Slack #infra announcement 2026-05-01.

**context:** Single region; rollback is automatic on failed health checks.

**constraints:** Keep runbook under 400 lines; no screenshots (text only).

**output_format:** Markdown: Gap list, then full revised `runbook.md` sections as fenced copies labeled by file.

*(The agent would list gaps and supply updated prose.)*
