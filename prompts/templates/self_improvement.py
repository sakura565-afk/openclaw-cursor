"""
Self-improvement prompt — reflects on prior outputs and proposes concrete upgrades.

Usage notes
-----------
- Supply ``prior_output`` and ``feedback`` (or ``rubric`` alone for cold critique).
- Use ``iteration_number`` to encourage diminishing, high-leverage changes in later loops.
- ``success_criteria`` should be testable where possible (behaviour, latency, format).
- Keep ``forbidden_changes`` updated when certain APIs or tone must stay fixed.

Example::

    from prompts.templates import render_named
    print(render_named("self_improvement.v1", **EXAMPLE_CONTEXT))
"""

from __future__ import annotations

from typing import Any, Final

from prompts.templates._env import render_template

TEMPLATE_ID: Final = "self_improvement.v1"

VARIABLES: Final[dict[str, str]] = {
    "prior_output": "The model's previous answer, plan, or code to improve (required).",
    "task_description": "Original user or system task that generated the output.",
    "feedback": "Human or automated critique, test failures, or linter messages.",
    "rubric": "Scoring dimensions and weights (quality, safety, brevity, correctness).",
    "success_criteria": "What 'good enough' means for this iteration.",
    "forbidden_changes": "Invariants: must-not-break behaviours or interfaces.",
    "iteration_number": "1-based iteration index in a refinement loop.",
    "max_suggestions": "Cap on improvement bullets to avoid thrash.",
}

EXAMPLE_CONTEXT: Final[dict[str, Any]] = {
    "prior_output": "Used a bare except and returned None on all failures.",
    "task_description": "Refactor fetch_prices() for clearer errors and logging.",
    "feedback": "Reviewer: bare except hides bugs; add structured logging with request id.",
    "rubric": "Correctness 40%, clarity 30%, observability 20%, minimal diff 10%.",
    "success_criteria": "Specific exception types; no silent failures; under 40 lines changed.",
    "forbidden_changes": "Public function signature and return type must stay the same.",
    "iteration_number": 2,
    "max_suggestions": 6,
}

PROMPT: Final[str] = """You are a disciplined self-critique and improvement assistant.

## Original task
{{ task_description | default('(not specified)') }}

## Prior output
```
{{ prior_output }}
```

{% if feedback %}
## Feedback / signals
{{ feedback }}
{% endif %}
{% if rubric %}
## Rubric
{{ rubric }}
{% endif %}
{% if success_criteria %}
## Success criteria
{{ success_criteria }}
{% endif %}
{% if forbidden_changes %}
## Forbidden changes
{{ forbidden_changes }}
{% endif %}

## Instructions
This is iteration **{{ iteration_number | default(1) }}** of improvement.

1. Diagnose the top issues in the prior output (max {{ max_suggestions | default(8) }} bullets).
2. For each issue: why it matters against the rubric/success criteria, and severity.
3. Provide a **revised output** that fully replaces the prior output where appropriate, \
or a **patch-style** edit if the format is code and a minimal diff is clearer—pick one style and state it.
4. List **regression checks** (manual or automated) to validate the revision.
5. If further external info is needed, ask at most three precise questions.

Be concise; prefer actionable edits over generic advice.
"""


def render(**variables: Any) -> str:
    return render_template(PROMPT, **variables)
