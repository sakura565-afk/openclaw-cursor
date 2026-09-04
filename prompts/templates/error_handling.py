"""
Error handling prompt — classifies failures, proposes mitigations, and defines guardrails.

Usage notes
-----------
- Pass ``error_message`` (required). Include ``stack_trace`` when debugging code paths.
- ``severity`` steers tone: use ``critical`` for user-facing outages, ``low`` for noise triage.
- ``attempted_fixes`` prevents circular suggestions; ``policy`` injects org-specific rules.
- For LLM self-correction loops, set ``retry_budget`` to the remaining attempts allowed.

Example::

    from prompts.templates import render_named
    print(render_named("error_handling.v1", **EXAMPLE_CONTEXT))
"""

from __future__ import annotations

from typing import Any, Final

from prompts.templates._env import render_template

TEMPLATE_ID: Final = "error_handling.v1"

VARIABLES: Final[dict[str, str]] = {
    "error_message": "Human-readable error or exception message (required).",
    "error_code": "Stable code or HTTP status if applicable.",
    "stack_trace": "Stack trace or structured traceback (optional).",
    "context": "What the system was doing: inputs, ids, versions, feature flags.",
    "service_name": "Component or microservice name.",
    "severity": "One of: critical, high, medium, low.",
    "attempted_fixes": "Bulleted list of remediation steps already tried.",
    "policy": "Operational policy: PII handling, allowed restarts, escalation matrix.",
    "retry_budget": "How many automated retries remain (number).",
    "desired_output": "e.g. 'rca_and_next_steps' or 'user_safe_summary_only'.",
}

EXAMPLE_CONTEXT: Final[dict[str, Any]] = {
    "error_message": "Connection refused connecting to redis://cache:6379/0",
    "error_code": "ECONNREFUSED",
    "stack_trace": "  File \"worker.py\", line 412, in dequeue\\n    r = redis.Redis.from_url(url)\\n...",
    "context": "Batch job id=8821; deploy v1.4.2; cache cluster 'cache' in namespace prod.",
    "service_name": "ingestion-worker",
    "severity": "high",
    "attempted_fixes": "- Pod restart on worker-7\\n- Verified DNS resolves for 'cache'",
    "policy": "No destructive Redis commands; page SRE if outage >15m; redact customer ids.",
    "retry_budget": 1,
    "desired_output": "rca_and_next_steps",
}

PROMPT: Final[str] = """You are an incident analyst and reliability engineer.

## Error
**Message:** {{ error_message }}
{% if error_code %}**Code:** {{ error_code }}{% endif %}
**Severity:** {{ severity | default('medium') }}
{% if service_name %}**Service:** {{ service_name }}{% endif %}

{% if context %}
## Context
{{ context }}
{% endif %}
{% if stack_trace %}
## Stack trace / details
```
{{ stack_trace }}
```
{% endif %}
{% if attempted_fixes %}
## Already attempted
{{ attempted_fixes }}
{% endif %}
{% if policy %}
## Policy / constraints
{{ policy }}
{% endif %}

## Instructions
Produce a response tailored to: **{{ desired_output | default('rca_and_next_steps') }}**.

1. **Classify** the failure mode (transient, config, dependency, logic bug, resource, security, unknown).
2. **Likely root cause** with confidence (high/medium/low) and why.
3. **Immediate mitigation** safe within policy and retry budget ({{ retry_budget | default(0) }} retries left).
4. **Longer-term fix** (code, infra, or process) and monitoring/alerting to add.
5. **User or stakeholder messaging** if severity is critical or high (non-technical, honest, no PII leakage).

If information is insufficient, list the smallest set of questions to unblock diagnosis.
"""


def render(**variables: Any) -> str:
    return render_template(PROMPT, **variables)
