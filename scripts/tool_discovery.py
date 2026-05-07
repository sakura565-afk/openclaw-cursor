#!/usr/bin/env python3
"""Tool discovery system for the OpenClaw workspace.

Scans an OpenClaw workspace (skills, scripts, MCP servers), extracts
metadata about each available tool, and exposes a searchable registry
plus agent-friendly context exports.

The discovery layer is intentionally stdlib-only and resilient: a single
malformed tool descriptor must never abort the scan. Each parser tags the
tool with ``parse_warnings`` instead of raising, so registries always
expose every tool that *exists*, even if its metadata is incomplete.

CLI overview::

    python -m scripts.tool_discovery scan
    python -m scripts.tool_discovery scan --output registry.json
    python -m scripts.tool_discovery search "image upscale"
    python -m scripts.tool_discovery show skill:image-toolbox
    python -m scripts.tool_discovery summary --format markdown
    python -m scripts.tool_discovery export --format json --output reg.json

Default scan locations match the OpenClaw layout described in the task::

    %USERPROFILE%/.openclaw/workspace/skills
    %USERPROFILE%/.openclaw/workspace/scripts

On non-Windows hosts the equivalent ``~/.openclaw/workspace/...`` paths
are used. Locations can be overridden through CLI flags or environment
variables (``OPENCLAW_SKILLS_DIR``, ``OPENCLAW_SCRIPTS_DIR``,
``OPENCLAW_MCP_DIR``, ``OPENCLAW_WORKSPACE``).
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

__all__ = [
    "Parameter",
    "Tool",
    "Registry",
    "discover",
    "scan_skills",
    "scan_scripts",
    "scan_mcp_servers",
    "search_registry",
    "export_summary",
    "export_markdown",
    "export_json",
    "default_workspace_roots",
    "main",
]

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Parameter:
    """A single tool parameter / CLI argument."""

    name: str
    type: str = ""
    description: str = ""
    required: bool = False
    default: Any = None
    choices: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = dataclasses.asdict(self)
        if not payload["choices"]:
            payload.pop("choices")
        if not payload["type"]:
            payload.pop("type")
        if not payload["description"]:
            payload.pop("description")
        if payload["default"] is None:
            payload.pop("default")
        return payload


@dataclass
class Tool:
    """A normalized record describing a discovered tool."""

    id: str
    kind: str  # "skill" | "script" | "mcp_server"
    name: str
    source_path: str
    description: str = ""
    summary: str = ""
    language: str = ""
    parameters: list[Parameter] = field(default_factory=list)
    use_cases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    parse_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "source_path": self.source_path,
            "description": self.description,
            "summary": self.summary,
            "language": self.language,
            "parameters": [p.to_dict() for p in self.parameters],
            "use_cases": list(self.use_cases),
            "tags": list(self.tags),
            "examples": list(self.examples),
            "metadata": dict(self.metadata),
            "parse_warnings": list(self.parse_warnings),
            "search_text": self.search_text(),
        }
        return payload

    def search_text(self) -> str:
        chunks: list[str] = [self.id, self.kind, self.name, self.description, self.summary, self.language]
        chunks.extend(self.use_cases)
        chunks.extend(self.tags)
        chunks.extend(self.examples)
        for parameter in self.parameters:
            chunks.append(parameter.name)
            chunks.append(parameter.description)
        for value in self.metadata.values():
            if isinstance(value, str):
                chunks.append(value)
        return _normalize_text(" \n ".join(chunk for chunk in chunks if chunk))


@dataclass
class Registry:
    """Aggregate registry of all discovered tools."""

    tools: list[Tool] = field(default_factory=list)
    workspace_roots: dict[str, str | None] = field(default_factory=dict)
    generated_at: str = ""
    errors: list[str] = field(default_factory=list)

    def by_id(self) -> dict[str, Tool]:
        return {tool.id: tool for tool in self.tools}

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {"total": len(self.tools)}
        for tool in self.tools:
            counts[tool.kind] = counts.get(tool.kind, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "workspace_roots": dict(self.workspace_roots),
            "counts": self.counts(),
            "errors": list(self.errors),
            "tools": [tool.to_dict() for tool in self.tools],
        }


# ---------------------------------------------------------------------------
# Path defaults and helpers
# ---------------------------------------------------------------------------


def _user_home() -> Path:
    """Return the user home directory, honoring ``USERPROFILE`` on Windows."""
    profile = os.environ.get("USERPROFILE")
    if profile:
        return Path(profile)
    return Path.home()


def _workspace_base() -> Path:
    override = os.environ.get("OPENCLAW_WORKSPACE")
    if override:
        return Path(override).expanduser()
    return _user_home() / ".openclaw" / "workspace"


def default_workspace_roots() -> dict[str, Path]:
    """Return the default skills/scripts/mcp roots for the current host.

    On Windows hosts the canonical OpenClaw layout
    (``C:\\Users\\<user>\\.openclaw\\workspace\\...``) is reproduced via
    ``USERPROFILE``. On other hosts ``~/.openclaw/workspace/...`` is used.
    """
    base = _workspace_base()
    skills = Path(os.environ.get("OPENCLAW_SKILLS_DIR") or (base / "skills"))
    scripts = Path(os.environ.get("OPENCLAW_SCRIPTS_DIR") or (base / "scripts"))
    mcp = Path(os.environ.get("OPENCLAW_MCP_DIR") or (base / "mcp_servers"))
    return {"skills": skills, "scripts": scripts, "mcp_servers": mcp}


_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text or "").strip().lower()


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-_.")
    return cleaned.lower() or "tool"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _first_sentence(text: str, limit: int = 240) -> str:
    if not text:
        return ""
    cleaned = _WHITESPACE_RE.sub(" ", text).strip()
    if not cleaned:
        return ""
    match = re.search(r"(.+?[.!?])(\s|$)", cleaned)
    summary = match.group(1) if match else cleaned
    if len(summary) > limit:
        summary = summary[: limit - 1].rstrip() + "\u2026"
    return summary


def _ensure_list_of_str(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [item.strip() for item in re.split(r"[\n,]", value) if item.strip()]
        return items
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


# ---------------------------------------------------------------------------
# Minimal frontmatter / YAML-subset parser
# ---------------------------------------------------------------------------
#
# SKILL.md files conventionally use a YAML frontmatter block. To remain
# stdlib-only we ship a small parser that handles the constructs OpenClaw
# skill files use in practice:
#
#     name: image-toolbox
#     description: |
#       Multi-line description.
#     tags: [image, render]
#     parameters:
#       - name: prompt
#         type: string
#         required: true
#         description: The prompt to render.
#
# Any unrecognized line is preserved verbatim under ``raw`` so callers can
# still surface it. Parsing never raises; on failure an empty dict is
# returned and a warning is appended by the caller.


_FRONTMATTER_RE = re.compile(r"^---\s*\n(?P<body>.*?)\n---\s*(?:\n|$)", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str, list[str]]:
    """Return ``(metadata, body, warnings)`` for a SKILL.md style document."""

    warnings: list[str] = []
    if not text:
        return {}, "", warnings

    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text, warnings

    raw_yaml = match.group("body")
    body = text[match.end():]
    try:
        metadata = _parse_yaml_subset(raw_yaml)
    except Exception as exc:  # pragma: no cover - defensive
        warnings.append(f"frontmatter parse failed: {exc}")
        metadata = {}
    return metadata, body, warnings


def _parse_yaml_subset(raw: str) -> dict[str, Any]:
    """Tiny indentation-aware YAML subset parser.

    Supports nested mappings, lists of scalars, lists of mappings, block
    scalars (``|`` and ``>``), inline ``[a, b]`` / ``{k: v}`` collections,
    and quoted strings. Anything more exotic falls through as a string.
    """

    lines = [line.rstrip() for line in raw.splitlines()]
    pos = 0

    def parse_block(indent: int) -> Any:
        nonlocal pos
        # Decide whether the block is a list or a mapping by looking at
        # the first non-blank, non-comment line at this indent.
        while pos < len(lines):
            line = lines[pos]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                pos += 1
                continue
            current_indent = len(line) - len(line.lstrip(" "))
            if current_indent < indent:
                return None
            if stripped.startswith("- "):
                return parse_list(current_indent)
            return parse_mapping(current_indent)
        return None

    def parse_mapping(indent: int) -> dict[str, Any]:
        nonlocal pos
        mapping: dict[str, Any] = {}
        while pos < len(lines):
            line = lines[pos]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                pos += 1
                continue
            current_indent = len(line) - len(line.lstrip(" "))
            if current_indent < indent:
                break
            if current_indent > indent:
                # Over-indented stray line: skip to avoid infinite loop.
                pos += 1
                continue
            if stripped.startswith("- "):
                break
            if ":" not in stripped:
                pos += 1
                continue
            key, _, raw_value = stripped.partition(":")
            key = key.strip()
            raw_value = raw_value.strip()
            pos += 1
            if raw_value in ("|", ">"):
                mapping[key] = _consume_block_scalar(indent + 2, fold=(raw_value == ">"))
            elif raw_value == "":
                child = parse_block(indent + 2)
                mapping[key] = child if child is not None else ""
            else:
                mapping[key] = _coerce_scalar(raw_value)
        return mapping

    def parse_list(indent: int) -> list[Any]:
        nonlocal pos
        result: list[Any] = []
        while pos < len(lines):
            line = lines[pos]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                pos += 1
                continue
            current_indent = len(line) - len(line.lstrip(" "))
            if current_indent < indent or not stripped.startswith("- "):
                break
            if current_indent > indent:
                pos += 1
                continue
            item_text = stripped[2:].strip()
            pos += 1
            if not item_text:
                child = parse_block(indent + 2)
                result.append(child if child is not None else "")
                continue
            if ":" in item_text and not item_text.startswith(("'", '"', "[", "{")):
                # Inline mapping item, may continue with deeper keys.
                key, _, raw_value = item_text.partition(":")
                key = key.strip()
                raw_value = raw_value.strip()
                item: dict[str, Any] = {}
                if raw_value in ("|", ">"):
                    item[key] = _consume_block_scalar(indent + 2, fold=(raw_value == ">"))
                elif raw_value == "":
                    child = parse_block(indent + 2)
                    item[key] = child if child is not None else ""
                else:
                    item[key] = _coerce_scalar(raw_value)
                # Continue collecting sibling keys at indent+2.
                rest = parse_block(indent + 2)
                if isinstance(rest, dict):
                    for k, v in rest.items():
                        item.setdefault(k, v)
                result.append(item)
            else:
                result.append(_coerce_scalar(item_text))
        return result

    def _consume_block_scalar(indent: int, fold: bool) -> str:
        nonlocal pos
        collected: list[str] = []
        while pos < len(lines):
            line = lines[pos]
            if not line.strip():
                collected.append("")
                pos += 1
                continue
            current_indent = len(line) - len(line.lstrip(" "))
            if current_indent < indent:
                break
            collected.append(line[indent:])
            pos += 1
        joined = "\n".join(collected).strip("\n")
        if fold:
            joined = re.sub(r"(?<!\n)\n(?!\n)", " ", joined)
        return joined

    parsed = parse_block(0)
    if isinstance(parsed, dict):
        return parsed
    if parsed is None:
        return {}
    return {"items": parsed}


def _coerce_scalar(raw: str) -> Any:
    text = raw.strip()
    if not text:
        return ""
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_coerce_scalar(part) for part in _split_inline(inner, ",")]
    if text.startswith("{") and text.endswith("}"):
        inner = text[1:-1].strip()
        result: dict[str, Any] = {}
        if not inner:
            return result
        for part in _split_inline(inner, ","):
            if ":" in part:
                key, _, value = part.partition(":")
                result[key.strip()] = _coerce_scalar(value.strip())
        return result
    lowered = text.lower()
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if lowered in {"null", "none", "~"}:
        return None
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


def _split_inline(raw: str, sep: str) -> list[str]:
    """Split ``raw`` by ``sep`` while honoring quotes/brackets."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    for char in raw:
        if quote:
            buf.append(char)
            if char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
            buf.append(char)
            continue
        if char in "[{":
            depth += 1
            buf.append(char)
            continue
        if char in "]}":
            depth = max(0, depth - 1)
            buf.append(char)
            continue
        if char == sep and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
            continue
        buf.append(char)
    if buf:
        parts.append("".join(buf).strip())
    return [part for part in parts if part]


# ---------------------------------------------------------------------------
# Skill scanner
# ---------------------------------------------------------------------------


_SKILL_HEADING_RE = re.compile(r"(?im)^\s{0,3}#{1,6}\s+(?P<title>.+?)\s*$")
_USE_CASE_HEADERS = ("use case", "use cases", "when to use", "examples", "scenarios")
_PARAMETER_HEADERS = ("parameters", "params", "inputs", "arguments")


def scan_skills(root: Path | None) -> list[Tool]:
    """Discover OpenClaw skills under ``root``.

    A skill is any directory containing ``SKILL.md`` (case-insensitive).
    Optional ``skill.json`` / ``skill.yaml`` sibling files are merged when
    present. Missing roots are tolerated and yield an empty list so this
    function is safe to call against a fresh workspace.
    """

    if root is None:
        return []
    skills: list[Tool] = []
    if not root.exists() or not root.is_dir():
        return skills

    for skill_md in sorted(root.rglob("*")):
        if not skill_md.is_file():
            continue
        if skill_md.name.lower() != "skill.md":
            continue
        try:
            tool = _parse_skill_directory(skill_md.parent, skill_md, root)
        except Exception as exc:  # pragma: no cover - defensive
            tool = Tool(
                id=f"skill:{_slugify(skill_md.parent.name)}",
                kind="skill",
                name=skill_md.parent.name,
                source_path=str(skill_md),
                parse_warnings=[f"unhandled error: {type(exc).__name__}: {exc}"],
            )
        skills.append(tool)
    return skills


def _parse_skill_directory(skill_dir: Path, skill_md: Path, root: Path) -> Tool:
    text = _read_text(skill_md)
    metadata, body, warnings = _parse_frontmatter(text)

    sidecar = _read_skill_sidecar(skill_dir)
    if sidecar:
        # Frontmatter wins; sidecar fills gaps.
        merged = dict(sidecar)
        merged.update({k: v for k, v in metadata.items() if v not in (None, "", [], {})})
        metadata = merged

    rel = skill_dir.relative_to(root) if root in skill_dir.parents or skill_dir == root else skill_dir
    name = str(metadata.get("name") or skill_dir.name)
    description = str(metadata.get("description") or "").strip()
    if not description:
        description = _first_meaningful_paragraph(body)
    summary = _first_sentence(metadata.get("summary") or description or _first_meaningful_paragraph(body))

    parameters = _parameters_from_metadata(metadata.get("parameters"))
    if not parameters:
        parameters = _parameters_from_markdown(body)

    use_cases = _ensure_list_of_str(metadata.get("use_cases") or metadata.get("examples"))
    if not use_cases:
        use_cases = _use_cases_from_markdown(body)

    tags = _ensure_list_of_str(metadata.get("tags") or metadata.get("keywords"))
    examples = _ensure_list_of_str(metadata.get("examples"))

    extra_meta: dict[str, Any] = {}
    for key in ("version", "author", "homepage", "license", "category", "requires"):
        if key in metadata and metadata[key] not in (None, "", [], {}):
            extra_meta[key] = metadata[key]
    extra_meta["relative_path"] = str(rel)

    tool_id = f"skill:{_slugify(name)}"
    return Tool(
        id=tool_id,
        kind="skill",
        name=name,
        source_path=str(skill_md),
        description=description,
        summary=summary or _first_sentence(description),
        language="markdown",
        parameters=parameters,
        use_cases=use_cases,
        tags=tags,
        examples=examples,
        metadata=extra_meta,
        parse_warnings=warnings,
    )


def _read_skill_sidecar(skill_dir: Path) -> dict[str, Any]:
    json_sidecar = next((p for p in (skill_dir / "skill.json", skill_dir / "manifest.json") if p.exists()), None)
    if json_sidecar is not None:
        try:
            payload = json.loads(_read_text(json_sidecar))
        except json.JSONDecodeError:
            return {}
        if isinstance(payload, dict):
            return payload
    yaml_sidecar = next(
        (p for p in (skill_dir / "skill.yaml", skill_dir / "skill.yml", skill_dir / "manifest.yaml") if p.exists()),
        None,
    )
    if yaml_sidecar is not None:
        try:
            return _parse_yaml_subset(_read_text(yaml_sidecar))
        except Exception:  # pragma: no cover - defensive
            return {}
    return {}


def _first_meaningful_paragraph(body: str) -> str:
    if not body:
        return ""
    paragraphs = [chunk.strip() for chunk in re.split(r"\n\s*\n", body) if chunk.strip()]
    for paragraph in paragraphs:
        if paragraph.startswith("#"):
            continue
        cleaned = re.sub(r"^\s*[-*+]\s+", "", paragraph, flags=re.MULTILINE).strip()
        if cleaned:
            return cleaned
    return paragraphs[0] if paragraphs else ""


def _parameters_from_metadata(value: Any) -> list[Parameter]:
    if not value:
        return []
    parameters: list[Parameter] = []
    if isinstance(value, dict):
        for name, raw in value.items():
            parameters.append(_parameter_from_value(str(name), raw))
        return parameters
    if isinstance(value, list):
        for raw in value:
            if isinstance(raw, dict):
                name = str(raw.get("name") or raw.get("id") or "").strip()
                if not name:
                    continue
                parameters.append(_parameter_from_value(name, raw))
            elif isinstance(raw, str) and raw.strip():
                parameters.append(Parameter(name=raw.strip()))
    return parameters


def _parameter_from_value(name: str, raw: Any) -> Parameter:
    if isinstance(raw, dict):
        return Parameter(
            name=name,
            type=str(raw.get("type") or "").strip(),
            description=str(raw.get("description") or raw.get("desc") or "").strip(),
            required=bool(raw.get("required") or raw.get("mandatory") or False),
            default=raw.get("default"),
            choices=_ensure_list_of_str(raw.get("choices") or raw.get("enum")),
        )
    if isinstance(raw, str):
        return Parameter(name=name, description=raw.strip())
    return Parameter(name=name)


def _parameters_from_markdown(body: str) -> list[Parameter]:
    if not body:
        return []
    section = _section_under_header(body, _PARAMETER_HEADERS)
    if not section:
        return []
    parameters: list[Parameter] = []
    for line in section.splitlines():
        match = re.match(r"\s*[-*+]\s+`?(?P<name>[A-Za-z_][A-Za-z0-9_.-]*)`?\s*[:\-]\s*(?P<desc>.+)", line)
        if not match:
            continue
        parameters.append(Parameter(name=match.group("name"), description=match.group("desc").strip()))
    return parameters


def _use_cases_from_markdown(body: str) -> list[str]:
    if not body:
        return []
    section = _section_under_header(body, _USE_CASE_HEADERS)
    if not section:
        return []
    items: list[str] = []
    for line in section.splitlines():
        match = re.match(r"\s*[-*+]\s+(?P<text>.+)", line)
        if match:
            items.append(match.group("text").strip())
    return items


def _section_under_header(body: str, headers: Sequence[str]) -> str:
    lower_headers = {header.lower() for header in headers}
    lines = body.splitlines()
    start: int | None = None
    start_level = 0
    for idx, line in enumerate(lines):
        match = _SKILL_HEADING_RE.match(line)
        if not match:
            continue
        title = match.group("title").strip().lower()
        if title in lower_headers or any(title.startswith(header) for header in lower_headers):
            start = idx + 1
            start_level = len(line) - len(line.lstrip("#"))
            break
    if start is None:
        return ""
    end = len(lines)
    for idx in range(start, len(lines)):
        match = _SKILL_HEADING_RE.match(lines[idx])
        if match:
            level = len(lines[idx]) - len(lines[idx].lstrip("#"))
            if level <= start_level:
                end = idx
                break
    return "\n".join(lines[start:end]).strip()


# ---------------------------------------------------------------------------
# Script scanner
# ---------------------------------------------------------------------------


_SCRIPT_EXTENSIONS = {
    ".py": "python",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".js": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".ps1": "powershell",
    ".rb": "ruby",
}


def scan_scripts(root: Path | None) -> list[Tool]:
    """Discover executable scripts under ``root``.

    Python scripts are parsed with :mod:`ast` to extract their module
    docstring and any ``argparse.add_argument`` calls. Other languages
    fall back to leading-comment / docstring heuristics. Files starting
    with ``_`` (other than ``__init__``) and ``__init__.py`` are skipped.
    """

    if root is None:
        return []
    scripts: list[Tool] = []
    if not root.exists() or not root.is_dir():
        return scripts

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        language = _SCRIPT_EXTENSIONS.get(ext)
        if not language:
            continue
        if path.name == "__init__.py":
            continue
        if path.name.startswith("."):
            continue
        try:
            tool = _parse_script(path, root, language)
        except Exception as exc:  # pragma: no cover - defensive
            tool = Tool(
                id=f"script:{_slugify(path.stem)}",
                kind="script",
                name=path.stem,
                source_path=str(path),
                language=language,
                parse_warnings=[f"unhandled error: {type(exc).__name__}: {exc}"],
            )
        scripts.append(tool)
    return scripts


def _parse_script(path: Path, root: Path, language: str) -> Tool:
    rel = path.relative_to(root) if root in path.parents or path == root else path
    text = _read_text(path)
    description = ""
    parameters: list[Parameter] = []
    warnings: list[str] = []

    if language == "python":
        description, parameters, warnings = _analyze_python_script(text)
    else:
        description = _leading_comment_block(text, language)

    summary = _first_sentence(description)
    use_cases = _use_cases_from_markdown(description) if description else []

    metadata: dict[str, Any] = {"relative_path": str(rel)}
    if language == "python":
        module_name = ".".join(rel.with_suffix("").parts)
        metadata["module"] = module_name
        metadata["invocation"] = f"python -m scripts.{path.stem}"

    return Tool(
        id=f"script:{_slugify(path.stem)}",
        kind="script",
        name=path.stem,
        source_path=str(path),
        description=description,
        summary=summary,
        language=language,
        parameters=parameters,
        use_cases=use_cases,
        tags=[language],
        metadata=metadata,
        parse_warnings=warnings,
    )


def _analyze_python_script(text: str) -> tuple[str, list[Parameter], list[str]]:
    warnings: list[str] = []
    if not text:
        return "", [], warnings
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        warnings.append(f"python parse error: {exc.msg} (line {exc.lineno})")
        return "", [], warnings

    description = ast.get_docstring(tree) or ""
    parameters = _argparse_arguments_from_ast(tree)
    return description, parameters, warnings


def _argparse_arguments_from_ast(tree: ast.AST) -> list[Parameter]:
    parameters: list[Parameter] = []
    seen: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        attr_name = ""
        if isinstance(func, ast.Attribute):
            attr_name = func.attr
        elif isinstance(func, ast.Name):
            attr_name = func.id
        if attr_name != "add_argument":
            continue
        param = _parameter_from_call(node)
        if param is None or param.name in seen:
            continue
        seen.add(param.name)
        parameters.append(param)
    return parameters


def _parameter_from_call(node: ast.Call) -> Parameter | None:
    if not node.args:
        return None
    raw_names: list[str] = []
    for arg in node.args:
        value = _literal_value(arg)
        if isinstance(value, str):
            raw_names.append(value)
    if not raw_names:
        return None
    name = max(raw_names, key=len).lstrip("-")
    if not name:
        return None

    param = Parameter(name=name)
    required = any(not raw_name.startswith("-") for raw_name in raw_names)
    for keyword in node.keywords:
        if keyword.arg is None:
            continue
        value = _literal_value(keyword.value)
        if keyword.arg == "help" and isinstance(value, str):
            param.description = value.strip()
        elif keyword.arg == "type" and isinstance(keyword.value, ast.Name):
            param.type = keyword.value.id
        elif keyword.arg == "default":
            if value is not None or isinstance(keyword.value, ast.Constant):
                param.default = value
        elif keyword.arg == "required" and isinstance(value, bool):
            required = value
        elif keyword.arg == "choices" and isinstance(value, (list, tuple)):
            param.choices = [str(item) for item in value]
        elif keyword.arg == "action" and isinstance(value, str) and value in {"store_true", "store_false"}:
            param.type = "bool"
            if param.default is None:
                param.default = value == "store_false"
    param.required = required
    return param


def _literal_value(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def _leading_comment_block(text: str, language: str) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    collected: list[str] = []
    started = False
    for line in lines:
        stripped = line.strip()
        if not started and stripped.startswith("#!"):
            continue
        if not stripped:
            if started:
                break
            continue
        if stripped.startswith("#"):
            collected.append(stripped.lstrip("#").strip())
            started = True
            continue
        if stripped.startswith("//"):
            collected.append(stripped.lstrip("/").strip())
            started = True
            continue
        if collected:
            break
        if not started:
            break
    return "\n".join(collected).strip()


# ---------------------------------------------------------------------------
# MCP server scanner
# ---------------------------------------------------------------------------


_MCP_FILE_NAMES = {"mcp.json", "mcp_servers.json", "mcpservers.json", "mcp_config.json"}


def scan_mcp_servers(root: Path | None) -> list[Tool]:
    """Discover MCP server definitions reachable from ``root``.

    Looks for the standard configuration file shapes:

    * A directory of per-server folders, each holding ``mcp.json`` /
      ``manifest.json`` / ``package.json`` describing one server.
    * A single ``mcp_servers.json`` (or similar) with an ``mcpServers``
      mapping or ``servers`` list.

    Missing roots and malformed JSON are tolerated.
    """

    if root is None:
        return []
    servers: list[Tool] = []
    if not root.exists():
        return servers

    if root.is_file():
        servers.extend(_load_mcp_config_file(root))
        return servers

    config_files: list[Path] = []
    for path in sorted(root.rglob("*.json")):
        if path.name.lower() in _MCP_FILE_NAMES:
            config_files.append(path)

    for config in config_files:
        servers.extend(_load_mcp_config_file(config))

    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            for candidate_name in ("mcp.json", "manifest.json", "package.json"):
                candidate = child / candidate_name
                if candidate.exists():
                    tool = _mcp_tool_from_directory(child, candidate)
                    if tool is not None:
                        servers.append(tool)
                    break

    return _deduplicate_by_id(servers)


def _load_mcp_config_file(path: Path) -> list[Tool]:
    raw = _read_text(path)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [
            Tool(
                id=f"mcp:{_slugify(path.stem)}",
                kind="mcp_server",
                name=path.stem,
                source_path=str(path),
                parse_warnings=[f"invalid JSON: {exc.msg} (line {exc.lineno})"],
            )
        ]

    servers: list[Tool] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("mcpServers"), dict):
            for name, definition in payload["mcpServers"].items():
                servers.append(_mcp_tool_from_definition(str(name), definition or {}, path))
        if isinstance(payload.get("servers"), list):
            for definition in payload["servers"]:
                if not isinstance(definition, dict):
                    continue
                name = str(definition.get("name") or definition.get("id") or "mcp-server").strip()
                servers.append(_mcp_tool_from_definition(name, definition, path))
        if not servers and ("command" in payload or "name" in payload):
            name = str(payload.get("name") or path.stem).strip()
            servers.append(_mcp_tool_from_definition(name, payload, path))
    return servers


def _mcp_tool_from_definition(name: str, definition: dict[str, Any], source: Path) -> Tool:
    description = str(definition.get("description") or "").strip()
    summary = _first_sentence(description)
    command = definition.get("command")
    args = definition.get("args")
    env = definition.get("env")
    transport = definition.get("transport") or definition.get("type") or ""

    parameters: list[Parameter] = []
    raw_params = definition.get("parameters") or definition.get("inputs")
    if raw_params:
        parameters.extend(_parameters_from_metadata(raw_params))
    if isinstance(env, dict):
        for env_name, env_value in env.items():
            parameters.append(
                Parameter(
                    name=str(env_name),
                    type="env",
                    description=str(env_value) if isinstance(env_value, str) else "",
                )
            )

    metadata: dict[str, Any] = {"config_path": str(source)}
    if command is not None:
        metadata["command"] = command
    if args is not None:
        metadata["args"] = args
    if transport:
        metadata["transport"] = transport
    for extra_key in ("url", "endpoint", "version", "homepage", "scopes", "tools", "capabilities"):
        if extra_key in definition and definition[extra_key] not in (None, "", [], {}):
            metadata[extra_key] = definition[extra_key]

    use_cases = _ensure_list_of_str(definition.get("use_cases") or definition.get("examples"))
    tags = _ensure_list_of_str(definition.get("tags") or definition.get("keywords"))
    if "mcp" not in tags:
        tags.append("mcp")

    return Tool(
        id=f"mcp:{_slugify(name)}",
        kind="mcp_server",
        name=name,
        source_path=str(source),
        description=description,
        summary=summary,
        language="json",
        parameters=parameters,
        use_cases=use_cases,
        tags=tags,
        metadata=metadata,
    )


def _mcp_tool_from_directory(directory: Path, manifest: Path) -> Tool | None:
    raw = _read_text(manifest)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return Tool(
            id=f"mcp:{_slugify(directory.name)}",
            kind="mcp_server",
            name=directory.name,
            source_path=str(manifest),
            parse_warnings=[f"invalid JSON: {exc.msg} (line {exc.lineno})"],
        )
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("name") or directory.name)
    return _mcp_tool_from_definition(name, payload, manifest)


def _deduplicate_by_id(tools: Iterable[Tool]) -> list[Tool]:
    seen: dict[str, Tool] = {}
    for tool in tools:
        existing = seen.get(tool.id)
        if existing is None:
            seen[tool.id] = tool
            continue
        # Prefer the entry with the richer description.
        if len(tool.description) > len(existing.description):
            seen[tool.id] = tool
    return list(seen.values())


# ---------------------------------------------------------------------------
# Discovery / registry assembly
# ---------------------------------------------------------------------------


def discover(
    skills_root: Path | None = None,
    scripts_root: Path | None = None,
    mcp_root: Path | None = None,
) -> Registry:
    """Run all scanners and assemble a :class:`Registry`."""
    defaults = default_workspace_roots()
    skills_root = skills_root if skills_root is not None else defaults["skills"]
    scripts_root = scripts_root if scripts_root is not None else defaults["scripts"]
    mcp_root = mcp_root if mcp_root is not None else defaults["mcp_servers"]

    registry = Registry(generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    registry.workspace_roots = {
        "skills": str(skills_root) if skills_root else None,
        "scripts": str(scripts_root) if scripts_root else None,
        "mcp_servers": str(mcp_root) if mcp_root else None,
    }

    for kind, scanner, path in (
        ("skills", scan_skills, skills_root),
        ("scripts", scan_scripts, scripts_root),
        ("mcp_servers", scan_mcp_servers, mcp_root),
    ):
        try:
            registry.tools.extend(scanner(path))
        except Exception as exc:  # pragma: no cover - defensive
            registry.errors.append(f"{kind}: {type(exc).__name__}: {exc}")

    registry.tools.sort(key=lambda tool: (tool.kind, tool.id))
    return registry


# ---------------------------------------------------------------------------
# Search and exports
# ---------------------------------------------------------------------------


def search_registry(registry: Registry, query: str, limit: int = 10) -> list[tuple[Tool, float]]:
    """Return the top matching tools ranked by simple TF / field weight.

    Ranking weights, in decreasing order:
        * Exact match on tool id or name (huge boost).
        * Term hits in name / tags / use_cases (strong boost).
        * Term hits in description / parameters (moderate boost).
        * Substring hits anywhere in ``search_text`` (baseline).
    """

    terms = [term for term in re.split(r"\s+", query.strip().lower()) if term]
    if not terms:
        return []

    results: list[tuple[Tool, float]] = []
    for tool in registry.tools:
        score = _score_tool(tool, terms)
        if score > 0:
            results.append((tool, score))
    results.sort(key=lambda item: (-item[1], item[0].id))
    return results[:limit]


def _score_tool(tool: Tool, terms: Sequence[str]) -> float:
    score = 0.0
    name = tool.name.lower()
    tool_id = tool.id.lower()
    description = tool.description.lower()
    summary = tool.summary.lower()
    tags_blob = " ".join(tool.tags).lower()
    use_cases_blob = " ".join(tool.use_cases).lower()
    params_blob = " ".join(p.name + " " + p.description for p in tool.parameters).lower()
    search_text = tool.search_text()

    for term in terms:
        if not term:
            continue
        if term == name or term == tool_id:
            score += 25.0
        if term in name:
            score += 8.0
        if term in tool_id:
            score += 6.0
        if term in summary:
            score += 4.0
        if term in tags_blob:
            score += 5.0
        if term in use_cases_blob:
            score += 4.0
        if term in params_blob:
            score += 2.0
        if term in description:
            score += 2.0
        elif term in search_text:
            score += 1.0
    return score


def export_json(registry: Registry, indent: int = 2) -> str:
    return json.dumps(registry.to_dict(), indent=indent, sort_keys=True, default=_json_default)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def export_markdown(registry: Registry) -> str:
    counts = registry.counts()
    lines: list[str] = [
        "# OpenClaw Tool Registry",
        "",
        f"Generated at: {registry.generated_at or 'n/a'}",
        "",
        "## Summary",
        "",
        f"- Total tools: **{counts.get('total', 0)}**",
        f"- Skills: **{counts.get('skill', 0)}**",
        f"- Scripts: **{counts.get('script', 0)}**",
        f"- MCP servers: **{counts.get('mcp_server', 0)}**",
        "",
        "## Workspace Roots",
        "",
    ]
    for kind, path in registry.workspace_roots.items():
        lines.append(f"- `{kind}`: `{path or 'n/a'}`")
    lines.append("")

    for kind_label, kind in (("Skills", "skill"), ("Scripts", "script"), ("MCP Servers", "mcp_server")):
        section_tools = [tool for tool in registry.tools if tool.kind == kind]
        if not section_tools:
            continue
        lines.append(f"## {kind_label}")
        lines.append("")
        for tool in section_tools:
            lines.append(f"### `{tool.id}` — {tool.name}")
            lines.append("")
            if tool.summary:
                lines.append(tool.summary)
                lines.append("")
            if tool.description and tool.description != tool.summary:
                lines.append(tool.description.strip())
                lines.append("")
            if tool.parameters:
                lines.append("**Parameters:**")
                lines.append("")
                for param in tool.parameters:
                    type_part = f" ({param.type})" if param.type else ""
                    required = " *required*" if param.required else ""
                    desc = f" — {param.description}" if param.description else ""
                    lines.append(f"- `{param.name}`{type_part}{required}{desc}")
                lines.append("")
            if tool.use_cases:
                lines.append("**Use cases:**")
                lines.append("")
                for case in tool.use_cases:
                    lines.append(f"- {case}")
                lines.append("")
            if tool.tags:
                lines.append("**Tags:** " + ", ".join(f"`{tag}`" for tag in tool.tags))
                lines.append("")
            lines.append(f"_Source:_ `{tool.source_path}`")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def export_summary(registry: Registry, max_tools: int | None = None) -> str:
    """Compact text summary suitable for stuffing into agent context."""
    counts = registry.counts()
    header = (
        f"OpenClaw tool registry ({counts.get('total', 0)} tools: "
        f"{counts.get('skill', 0)} skills, {counts.get('script', 0)} scripts, "
        f"{counts.get('mcp_server', 0)} MCP servers)"
    )
    lines: list[str] = [header, ""]
    tools = registry.tools if max_tools is None else registry.tools[:max_tools]
    for tool in tools:
        bits = [f"[{tool.kind}] {tool.id}"]
        summary = tool.summary or _first_sentence(tool.description)
        if summary:
            bits.append(f"— {summary}")
        line = " ".join(bits)
        params = [param.name for param in tool.parameters[:6]]
        if params:
            line += f" | params: {', '.join(params)}"
        if tool.tags:
            line += f" | tags: {', '.join(tool.tags[:5])}"
        lines.append(line)
    if max_tools is not None and len(registry.tools) > max_tools:
        lines.append(f"... ({len(registry.tools) - max_tools} more tools omitted)")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print(payload: str, output: Path | None) -> None:
    if output is None:
        sys.stdout.write(payload)
        if not payload.endswith("\n"):
            sys.stdout.write("\n")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(payload if payload.endswith("\n") else payload + "\n", encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tool_discovery",
        description="Discover OpenClaw skills, scripts, and MCP servers.",
    )
    parser.add_argument("--skills-dir", type=Path, default=None, help="Override the skills directory.")
    parser.add_argument("--scripts-dir", type=Path, default=None, help="Override the scripts directory.")
    parser.add_argument("--mcp-dir", type=Path, default=None, help="Override the MCP servers directory.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Scan workspace and emit the registry as JSON.")
    scan.add_argument("--output", "-o", type=Path, default=None, help="Write to file instead of stdout.")
    scan.add_argument("--indent", type=int, default=2, help="JSON indent (default 2).")

    search = subparsers.add_parser("search", help="Search the registry by free text.")
    search.add_argument("query", help="Free-text query (terms are AND-scored).")
    search.add_argument("--limit", type=int, default=10, help="Max number of results.")
    search.add_argument("--output", "-o", type=Path, default=None, help="Write to file instead of stdout.")
    search.add_argument("--json", action="store_true", help="Emit results as JSON instead of text.")

    show = subparsers.add_parser("show", help="Show one tool by id (e.g. skill:image-toolbox).")
    show.add_argument("tool_id", help="Tool id reported by `scan`.")
    show.add_argument("--output", "-o", type=Path, default=None, help="Write to file instead of stdout.")
    show.add_argument("--json", action="store_true", help="Emit as JSON instead of text.")

    summary = subparsers.add_parser("summary", help="Emit a compact agent-context summary.")
    summary.add_argument("--format", choices=["text", "markdown", "json"], default="text")
    summary.add_argument("--max-tools", type=int, default=None, help="Limit text summary to N entries.")
    summary.add_argument("--output", "-o", type=Path, default=None, help="Write to file instead of stdout.")

    export = subparsers.add_parser("export", help="Export registry in the requested format.")
    export.add_argument("--format", choices=["json", "markdown", "text"], default="json")
    export.add_argument("--output", "-o", type=Path, default=None, help="Write to file instead of stdout.")
    export.add_argument("--indent", type=int, default=2, help="JSON indent (default 2).")

    return parser


def _format_search_text(results: list[tuple[Tool, float]]) -> str:
    if not results:
        return "No matching tools.\n"
    lines: list[str] = []
    for tool, score in results:
        line = f"{score:6.2f}  {tool.id:<40s}  {tool.summary or tool.description[:80]}"
        lines.append(line.rstrip())
    return "\n".join(lines) + "\n"


def _format_search_json(results: list[tuple[Tool, float]]) -> str:
    payload = [
        {
            "score": round(score, 4),
            "tool": tool.to_dict(),
        }
        for tool, score in results
    ]
    return json.dumps(payload, indent=2, sort_keys=True)


def _format_tool_text(tool: Tool) -> str:
    lines = [
        f"{tool.id} ({tool.kind})",
        f"  name        : {tool.name}",
        f"  source      : {tool.source_path}",
        f"  language    : {tool.language or '-'}",
        f"  summary     : {tool.summary or '-'}",
    ]
    if tool.description:
        lines.append("  description :")
        for desc_line in tool.description.splitlines():
            lines.append(f"    {desc_line}")
    if tool.parameters:
        lines.append("  parameters  :")
        for param in tool.parameters:
            req = " (required)" if param.required else ""
            type_part = f" [{param.type}]" if param.type else ""
            desc = f" — {param.description}" if param.description else ""
            lines.append(f"    - {param.name}{type_part}{req}{desc}")
    if tool.use_cases:
        lines.append("  use cases   :")
        for case in tool.use_cases:
            lines.append(f"    - {case}")
    if tool.tags:
        lines.append(f"  tags        : {', '.join(tool.tags)}")
    if tool.metadata:
        lines.append("  metadata    :")
        for key, value in sorted(tool.metadata.items()):
            lines.append(f"    {key}: {value}")
    if tool.parse_warnings:
        lines.append("  warnings    :")
        for warning in tool.parse_warnings:
            lines.append(f"    - {warning}")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    registry = discover(
        skills_root=args.skills_dir,
        scripts_root=args.scripts_dir,
        mcp_root=args.mcp_dir,
    )

    if args.command == "scan":
        _print(export_json(registry, indent=args.indent), args.output)
        return 0

    if args.command == "search":
        results = search_registry(registry, args.query, limit=args.limit)
        payload = _format_search_json(results) if args.json else _format_search_text(results)
        _print(payload, args.output)
        return 0

    if args.command == "show":
        tool = registry.by_id().get(args.tool_id)
        if tool is None:
            sys.stderr.write(f"Unknown tool id: {args.tool_id}\n")
            return 2
        payload = json.dumps(tool.to_dict(), indent=2, sort_keys=True) if args.json else _format_tool_text(tool)
        _print(payload, args.output)
        return 0

    if args.command == "summary":
        if args.format == "markdown":
            payload = export_markdown(registry)
        elif args.format == "json":
            payload = export_json(registry, indent=2)
        else:
            payload = export_summary(registry, max_tools=args.max_tools)
        _print(payload, args.output)
        return 0

    if args.command == "export":
        if args.format == "markdown":
            payload = export_markdown(registry)
        elif args.format == "text":
            payload = export_summary(registry)
        else:
            payload = export_json(registry, indent=args.indent)
        _print(payload, args.output)
        return 0

    return 1  # pragma: no cover - argparse already enforces required subcommand


if __name__ == "__main__":
    raise SystemExit(main())
