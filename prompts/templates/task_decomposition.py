"""
Task decomposition prompt — breaks a goal into ordered, checkable subtasks.

Usage notes
-----------
- Set ``goal`` (required). Optional fields refine scope and output shape.
- ``constraints`` and ``non_goals`` reduce scope creep in the decomposition.
- ``max_subtasks`` caps breadth; increase when the goal is inherently parallel.
- Output is structured markdown; parse downstream if you need machine-readable JSON.
- For RAG or tool-augmented planners, pass ``context_snippets`` as a short bullet list.

Example::

    from prompts.templates import render_named
    print(render_named("task_decomposition.v1", **EXAMPLE_CONTEXT))
"""

from __future__ import annotations

from typing import Any, Final

from prompts.templates._env import render_template

TEMPLATE_ID: Final = "task_decomposition.v1"

VARIABLES: Final[dict[str, str]] = {
    "goal": "Primary outcome or deliverable (required).",
    "domain": "Problem domain or product area (e.g. 'payments', 'data pipeline').",
    "constraints": "Hard limits: time, budget, tech stack, compliance, SLAs.",
    "non_goals": "Explicitly out-of-scope items to avoid wasted branches.",
    "stakeholders": "Who cares about the outcome; informs prioritization and acceptance checks.",
    "existing_plan": "Prior draft plan or partial work to extend or correct (optional).",
    "context_snippets": "Short retrieved facts or policy excerpts to ground steps (optional).",
    "max_subtasks": "Upper bound on leaf tasks (integer as string or int in Jinja context).",
    "output_style": "e.g. 'numbered_markdown' or 'outline_with_checkboxes'.",
}

EXAMPLE_CONTEXT: Final[dict[str, Any]] = {
    "goal": "Roll out read replicas for the checkout service with zero-downtime cutover.",
    "domain": "backend infrastructure / PostgreSQL",
    "constraints": "No more than 2h total write outage; EU data residency; change window Fri 02:00 UTC.",
    "non_goals": "Application query rewrite for sharding; ORM major version bump.",
    "stakeholders": "SRE (owns runbooks), Checkout team (owns app), DBA (owns DB).",
    "existing_plan": "",
    "context_snippets": "- Current primary in eu-west-1; p95 read latency 180ms under peak.\n- Replica lag alert at >30s.",
    "max_subtasks": 12,
    "output_style": "numbered_markdown",
}

PROMPT: Final[str] = """You are a senior technical planner. Decompose the following goal into \
an executable task breakdown.

## Goal
{{ goal }}

{% if domain %}
## Domain
{{ domain }}
{% endif %}
{% if stakeholders %}
## Stakeholders
{{ stakeholders }}
{% endif %}
{% if constraints %}
## Constraints
{{ constraints }}
{% endif %}
{% if non_goals %}
## Non-goals
{{ non_goals }}
{% endif %}
{% if existing_plan %}
## Existing plan / prior work
{{ existing_plan }}
{% endif %}
{% if context_snippets %}
## Grounding context
{{ context_snippets }}
{% endif %}

## Instructions
1. Produce at most {{ max_subtasks | default(15) }} concrete subtasks.
2. Order tasks by dependencies (topological order). Note explicit dependencies inline.
3. Each subtask must have: a clear verb-led title, expected outcome, owner/role if inferable, \
and a simple verification or acceptance check.
4. Flag risks, unknowns, and where human approval is required before execution.
5. Use style: {{ output_style | default('numbered_markdown') }}.

Begin with a one-line summary of the decomposition strategy, then the task list.
"""


def render(**variables: Any) -> str:
    """Render ``PROMPT`` with caller-supplied Jinja variables."""
    return render_template(PROMPT, **variables)
