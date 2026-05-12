"""Load and render markdown prompt templates with YAML front matter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import frontmatter

_TEMPLATES_DIR = Path(__file__).resolve().parent

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


@dataclass(frozen=True)
class PromptTemplate:
    """A prompt template parsed from a markdown file with front matter."""

    id: str
    title: str
    description: str
    usage: str
    variables: tuple[dict[str, Any], ...]
    body: str
    source_path: Path

    def render(self, **kwargs: Any) -> str:
        """Fill ``{{ name }}`` placeholders in the body. Values are stringified."""
        missing_req = self.required_variable_names() - frozenset(kwargs.keys())
        if missing_req:
            raise KeyError(f"Missing required variables: {sorted(missing_req)}")
        return render_body(self.body, kwargs, strict=True)

    def required_variable_names(self) -> frozenset[str]:
        names: set[str] = set()
        for item in self.variables:
            if item.get("required", False):
                name = item.get("name")
                if isinstance(name, str):
                    names.add(name)
        return frozenset(names)


def render_body(
    body: str,
    values: Mapping[str, Any],
    *,
    strict: bool = True,
) -> str:
    """
    Replace ``{{ key }}`` in *body* with string values from *values*.

    If *strict* is True, unknown placeholders or missing keys raise ``KeyError``.
    """

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            if strict:
                raise KeyError(f"Missing template value for placeholder: {key!r}")
            return match.group(0)
        return str(values[key])

    out = _PLACEHOLDER.sub(repl, body)
    if strict:
        remaining = _PLACEHOLDER.findall(out)
        if remaining:
            raise KeyError(f"Missing template values for placeholders: {remaining}")
    return out


def _normalize_variables(raw: Any) -> tuple[dict[str, Any], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        return ()
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(dict(item))
    return tuple(out)


def load_template(template_id: str) -> PromptTemplate:
    """
    Load a template by ``id`` (YAML ``id`` field), matching ``*.md`` in this package.

    Raises:
        FileNotFoundError: No template file declares this id.
        ValueError: Front matter is missing required fields.
    """
    for path in sorted(_TEMPLATES_DIR.glob("*.md")):
        if path.name.startswith("_"):
            continue
        post = frontmatter.load(path)
        meta = post.metadata or {}
        tid = meta.get("id")
        if tid != template_id:
            continue
        title = meta.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"Template {path.name} must set a non-empty string 'title' in front matter")
        desc = meta.get("description", "")
        if not isinstance(desc, str):
            desc = str(desc)
        usage = meta.get("usage", "")
        if not isinstance(usage, str):
            usage = str(usage)
        variables = _normalize_variables(meta.get("variables"))
        body = post.content
        if not isinstance(body, str):
            body = str(body)
        return PromptTemplate(
            id=str(tid),
            title=title.strip(),
            description=desc.strip(),
            usage=usage.strip(),
            variables=variables,
            body=body.lstrip("\n"),
            source_path=path,
        )
    raise FileNotFoundError(f"No prompt template with id={template_id!r}")


def list_templates() -> list[dict[str, Any]]:
    """Return summary metadata for every ``*.md`` template in this directory."""
    summaries: list[dict[str, Any]] = []
    for path in sorted(_TEMPLATES_DIR.glob("*.md")):
        if path.name.startswith("_"):
            continue
        post = frontmatter.load(path)
        meta = post.metadata or {}
        tid = meta.get("id")
        if not isinstance(tid, str):
            continue
        title = meta.get("title", tid)
        if not isinstance(title, str):
            title = str(title)
        desc = meta.get("description", "")
        if not isinstance(desc, str):
            desc = str(desc)
        summaries.append(
            {
                "id": tid,
                "title": title,
                "description": desc.strip(),
                "path": str(path),
            }
        )
    summaries.sort(key=lambda item: item["id"])
    return summaries


def template_ids() -> tuple[str, ...]:
    """Ordered tuple of template ids discovered on disk."""
    return tuple(item["id"] for item in list_templates())
