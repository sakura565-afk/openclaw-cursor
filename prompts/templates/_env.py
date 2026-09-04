"""Shared Jinja2 environment for prompt modules (keeps imports acyclic)."""

from __future__ import annotations

from typing import Any, Final

from jinja2 import Environment, StrictUndefined

_ENV: Final = Environment(
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    autoescape=False,
)


def render_template(source: str, **variables: Any) -> str:
    return _ENV.from_string(source).render(**variables)
