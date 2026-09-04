# Migration planning

**Purpose:** Plan a safe move between states (schema, platform, dependency major, data store) with phases, rollback, and validation gates.

**Placeholders:** `{{agent_role}}`, `{{repository_or_project}}`, `{{migration_type}}`, `{{current_state}}`, `{{target_state}}`, `{{downtime_or_sla}}`, `{{dependencies}}`, `{{risks_and_unknowns}}`, `{{context}}`, `{{constraints}}`, `{{output_format}}`

---

You are: **{{agent_role}}**

## Migration overview

**Project / repo:** {{repository_or_project}}

**Migration type (database, cloud, language/runtime, framework major, data format, traffic cutover):**

{{migration_type}}

**Current state (versions, topology, data shape):**

{{current_state}}

**Target state (desired end topology, versions, compatibility guarantees):**

{{target_state}}

**Downtime / SLA / traffic expectations:**

{{downtime_or_sla}}

**Dependencies (other teams, services, feature flags, external vendors):**

{{dependencies}}

**Known risks and unknowns:**

{{risks_and_unknowns}}

## Context

{{context}}

## Constraints

{{constraints}}

## Instructions

1. State **compatibility strategy**: big bang vs strangler vs dual-write/dual-read; justify for this context.
2. Break work into **phases** with entry/exit criteria and **rollback** for each phase.
3. For each phase: **data movement** (if any), **code changes**, **config**, **observability** (metrics/logs/alerts), **who approves**.
4. Define **validation gates**: automated checks, canaries, sampling, reconciliation jobs.
5. Produce a **communications checklist** (stakeholders, status page, runbook link)—brief.
6. Call out **freeze windows** or sequencing constraints if applicable; if unknown, list assumptions.

## Output

{{output_format}}

---

## Example (filled)

**agent_role:** Principal engineer with experience in zero-downtime DB migrations.

**repository_or_project:** `orders-db` + `orders-service`

**migration_type:** PostgreSQL 14 → 16 with logical replication; app connection string change.

**current_state:** Single primary, one read replica; app pins `sslmode=require`.

**target_state:** PG 16 primary, same replica count, connection pooler in front (PgBouncer).

**downtime_or_sla:** RPO 1 min, RTO 15 min; no planned full outage > 2 minutes.

**dependencies:** DBA team for replication slot; SRE for pooler TLS certs.

**risks_and_unknowns:** Extension `pg_trgm` version on 16; large JSONB columns replication lag.

**context:** Peak traffic 10:00–14:00 UTC; migration preferred on Sunday.

**constraints:** No application deploy during cutover window except rollback build.

**output_format:** Markdown: Strategy, Phases (table), Rollback summary, Validation gates, Open questions.

*(The agent would produce a phased plan with explicit gates and rollback.)*
