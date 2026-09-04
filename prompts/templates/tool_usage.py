"""
Tool usage prompt — plans safe tool calls, argument construction, and result synthesis.

Usage notes
-----------
- ``available_tools`` should describe each tool: name, purpose, args schema, and failure modes.
- ``task`` is the user objective the tools must serve (not the tool call itself).
- Use ``safety_policy`` for authz, rate limits, and destructive-operation rules.
- ``prior_tool_results`` supports multi-step tool chains without re-explaining tools.

Example::

    from prompts.templates import render_named
    print(render_named("tool_usage.v1", **EXAMPLE_CONTEXT))
"""

from __future__ import annotations

from typing import Any, Final

from prompts.templates._env import render_template

TEMPLATE_ID: Final = "tool_usage.v1"

VARIABLES: Final[dict[str, str]] = {
    "task": "End goal the tools should accomplish (required).",
    "available_tools": "Markdown or JSON-like description of callable tools and schemas.",
    "safety_policy": "What must never be done; approval gates; data minimization.",
    "output_schema": "Expected shape of final answer (JSON schema, bullet list, etc.).",
    "prior_tool_results": "Results from earlier steps in the same session (optional).",
    "max_tool_calls": "Suggested upper bound on round-trips for this task.",
    "locale_or_tone": "User-facing language or formality for explanations.",
}

EXAMPLE_CONTEXT: Final[dict[str, Any]] = {
    "task": "Find the latest open bug labeled 'p1' in the billing repo and summarize impact.",
    "available_tools": """- search_issues(repo, query, label) -> list[{id, title, state, url}]
- get_issue(repo, id) -> {body, comments, labels, assignees}
- rate_limit: 60 req/min per token""",
    "safety_policy": "Read-only GitHub token; never post comments or close issues; redact internal URLs.",
    "output_schema": "Markdown: title, one-paragraph impact, link list, open questions.",
    "prior_tool_results": "",
    "max_tool_calls": 5,
    "locale_or_tone": "Professional English, concise.",
}

PROMPT: Final[str] = """You are a tool-using assistant. Plan and reason about tool calls without \
hallucinating capabilities beyond those listed.

## Task
{{ task }}

## Available tools
{{ available_tools }}

{% if safety_policy %}
## Safety / policy
{{ safety_policy }}
{% endif %}
{% if output_schema %}
## Final answer shape
{{ output_schema }}
{% endif %}
{% if prior_tool_results %}
## Prior tool results (this session)
{{ prior_tool_results }}
{% endif %}

## Instructions
1. Restate the task in one sentence and list assumptions (if any).
2. Propose an ordered **tool plan** with at most {{ max_tool_calls | default(10) }} calls. \
For each step: tool name, arguments (valid JSON-like), expected signal from success, and fallback \
if the tool errors or returns empty.
3. If a required capability is missing from **Available tools**, say so and give the best \
no-tool alternative within policy.
4. After tools would complete, draft the **final user-facing answer** in tone: \
{{ locale_or_tone | default('neutral, concise') }}.
5. Never fabricate tool outputs; if simulating, label the section clearly as hypothetical.

Use clear headings and bullet lists.
"""


def render(**variables: Any) -> str:
    return render_template(PROMPT, **variables)
