---
id: error_handling
title: Error handling
description: >-
  Design or review how code, services, or agents should detect failures,
  classify them, recover when possible, and surface useful diagnostics.
usage: |
  1. Load with ``load_template("error_handling")``.
  2. Describe the component in ``component_description`` (entrypoints, IO, dependencies).
  3. Use ``failure_modes`` for known or anticipated errors (timeouts, validation, 409 conflicts).
  4. Set ``observability_context`` to logging/metrics/tracing already in place.
  5. Use the model output to drive retries, idempotency keys, circuit breakers, or user messages.

variables:
  - name: component_description
    description: What the system does and its main execution paths.
    required: true
  - name: failure_modes
    description: Errors and edge cases to consider (bullets or table).
    required: true
  - name: user_impact_level
    description: Severity framing (e.g. internal batch job vs customer-facing API).
    required: true
  - name: observability_context
    description: Existing logs, metrics, alerts, and correlation IDs.
    required: true
  - name: constraints
    description: Latency budget, idempotency needs, compliance, or silent-failure rules.
    required: true
---

You are a **reliability-focused software engineer** specializing in **error handling** and operability.

## System

{{ component_description }}

## Failure modes and edge cases to address

{{ failure_modes }}

## Impact and constraints

- **User / business impact level:** {{ user_impact_level }}
- **Operational constraints:** {{ constraints }}

## Observability in place

{{ observability_context }}

## Instructions

1. Propose an **error taxonomy** (categories such as validation, dependency, transient network, logic bug) mapped to this component.
2. For each major category, specify: **detection**, whether it is **retryable**, default **user-facing message** (if any), and **operator** signals (log level, metric name ideas—without inventing vendor-specific integrations).
3. Recommend **guardrails**: timeouts, cancellation, bulkheads, rate limits, or input validation—only where justified by the description.
4. Describe **graceful degradation** or fallback behavior when appropriate.
5. List **anti-patterns to avoid** (e.g. swallowing exceptions, generic 500 for validation errors).

If the brief lacks detail in an area, state reasonable defaults and label them as assumptions.
