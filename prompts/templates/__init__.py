"""
Jinja2 prompt templates for agent and orchestration workflows.

Usage
-----
Render by template id (recommended for production callers that map ids to runs)::

    from prompts.templates import render_named
    text = render_named("task_decomposition.v1", goal="Ship v2 API", domain="backend")

Render arbitrary Jinja2 source (escape user content before embedding)::

    from prompts.templates import render_string
    text = render_string("Hello, {{ name }}!", name="Ada")

All template strings use ``StrictUndefined``: missing variables raise at render time.
"""

from __future__ import annotations

from typing import Any

from prompts.templates import conversation_analysis
from prompts.templates import error_handling
from prompts.templates import self_improvement
from prompts.templates import task_decomposition
from prompts.templates import tool_usage
from prompts.templates._env import render_template as render_string


def render_named(template_id: str, **variables: Any) -> str:
    """Render a registered template by its ``TEMPLATE_ID``."""
    mod = TEMPLATE_REGISTRY.get(template_id)
    if mod is None:
        known = ", ".join(sorted(TEMPLATE_REGISTRY))
        raise KeyError(f"Unknown template_id={template_id!r}. Known: {known}")
    return mod.render(**variables)


TEMPLATE_REGISTRY: dict[str, Any] = {
    task_decomposition.TEMPLATE_ID: task_decomposition,
    error_handling.TEMPLATE_ID: error_handling,
    self_improvement.TEMPLATE_ID: self_improvement,
    tool_usage.TEMPLATE_ID: tool_usage,
    conversation_analysis.TEMPLATE_ID: conversation_analysis,
}

__all__ = [
    "TEMPLATE_REGISTRY",
    "render_named",
    "render_string",
    "conversation_analysis",
    "error_handling",
    "self_improvement",
    "task_decomposition",
    "tool_usage",
]
