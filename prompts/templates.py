"""Load and render reusable prompt templates from ``prompts/templates/``."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

_PLACEHOLDER_PATTERN = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True)
class PlaceholderSpec:
    """Declaration of a single ``{{name}}`` slot in a template."""

    name: str
    description: str
    required: bool = True


@dataclass(frozen=True)
class ExampleSpec:
    """One filled-in example for documentation and tests."""

    title: str
    placeholder_values: dict[str, str]
    sample_response: str


@dataclass(frozen=True)
class ExpectedOutputFormat:
    """How the model should structure its answer."""

    description: str
    sections: tuple[str, ...] = ()


@dataclass(frozen=True)
class PromptTemplate:
    """A reusable prompt with metadata, placeholders, and examples."""

    id: str
    name: str
    description: str
    body: str
    placeholders: tuple[PlaceholderSpec, ...]
    expected_output: ExpectedOutputFormat
    examples: tuple[ExampleSpec, ...] = ()
    tags: tuple[str, ...] = ()

    def render(self, values: Mapping[str, str], *, strict: bool = True) -> str:
        """Replace ``{{name}}`` in ``body`` with ``values[name]``.

        Placeholders marked ``required: false`` in the JSON default to an empty
        string when omitted so optional sections can be left blank.
        """

        effective: dict[str, str] = dict(values)
        for spec in self.placeholders:
            if not spec.required:
                effective.setdefault(spec.name, "")
        if strict:
            missing_required = sorted(
                spec.name for spec in self.placeholders if spec.required and spec.name not in values
            )
            if missing_required:
                raise KeyError(f"Missing required placeholder values: {missing_required}")

        missing: list[str] = []

        def _replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in effective:
                missing.append(key)
                return match.group(0)
            return str(effective[key])

        out = _PLACEHOLDER_PATTERN.sub(_replace, self.body)
        if strict and missing:
            raise KeyError(f"Missing placeholder values: {sorted(set(missing))}")
        return out


def _parse_placeholders(raw: list[dict[str, Any]]) -> tuple[PlaceholderSpec, ...]:
    specs: list[PlaceholderSpec] = []
    for item in raw:
        specs.append(
            PlaceholderSpec(
                name=str(item["name"]),
                description=str(item.get("description", "")),
                required=bool(item.get("required", True)),
            )
        )
    return tuple(specs)


def _parse_examples(raw: list[dict[str, Any]]) -> tuple[ExampleSpec, ...]:
    examples: list[ExampleSpec] = []
    for item in raw:
        examples.append(
            ExampleSpec(
                title=str(item["title"]),
                placeholder_values=dict(item.get("placeholder_values", {})),
                sample_response=str(item.get("sample_response", "")),
            )
        )
    return tuple(examples)


def _parse_expected_output(raw: dict[str, Any]) -> ExpectedOutputFormat:
    sections = raw.get("sections") or []
    return ExpectedOutputFormat(
        description=str(raw.get("description", "")),
        sections=tuple(str(s) for s in sections),
    )


def _load_template_file(path: Path) -> PromptTemplate:
    data = json.loads(path.read_text(encoding="utf-8"))
    tags = data.get("tags") or []
    return PromptTemplate(
        id=str(data["id"]),
        name=str(data["name"]),
        description=str(data.get("description", "")),
        body=str(data["body"]),
        placeholders=_parse_placeholders(list(data.get("placeholders", []))),
        expected_output=_parse_expected_output(dict(data.get("expected_output_format", {}))),
        examples=_parse_examples(list(data.get("examples", []))),
        tags=tuple(str(t) for t in tags),
    )


class TemplateLibrary:
    """Index of all JSON templates under a directory (default: package ``templates``)."""

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory if directory is not None else Path(__file__).resolve().parent / "templates"
        self._cache: dict[str, PromptTemplate] | None = None

    @property
    def directory(self) -> Path:
        return self._dir

    def _ensure_loaded(self) -> dict[str, PromptTemplate]:
        if self._cache is not None:
            return self._cache
        templates: dict[str, PromptTemplate] = {}
        if not self._dir.is_dir():
            self._cache = templates
            return templates
        for path in sorted(self._dir.glob("*.json")):
            template = _load_template_file(path)
            if template.id in templates:
                raise ValueError(f"Duplicate template id {template.id!r} in {path}")
            templates[template.id] = template
        self._cache = templates
        return templates

    def reload(self) -> None:
        """Drop cached templates (useful after editing JSON on disk)."""

        self._cache = None

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._ensure_loaded()))

    def get(self, template_id: str) -> PromptTemplate:
        templates = self._ensure_loaded()
        if template_id not in templates:
            available = ", ".join(templates) or "(none)"
            raise KeyError(f"Unknown template {template_id!r}. Available: {available}")
        return templates[template_id]

    def all_templates(self) -> tuple[PromptTemplate, ...]:
        loaded = self._ensure_loaded()
        return tuple(loaded[k] for k in sorted(loaded))

    def render(self, template_id: str, values: Mapping[str, str], *, strict: bool = True) -> str:
        return self.get(template_id).render(values, strict=strict)
