"""Load and render markdown prompt templates with ``{{variable}}`` placeholders."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Mapping

# Placeholder names: letters, digits, underscore; must not be empty.
_PLACEHOLDER = re.compile(r"\{\{([a-zA-Z_][a-zA-Z0-9_]*)\}\}")


def templates_dir(root: Path | None = None) -> Path:
    """Directory containing ``*.md`` template files (excluding ``INDEX.md``)."""
    base = Path(__file__).resolve().parent if root is None else root
    return base / "templates"


def template_path_for_name(name: str, root: Path | None = None) -> Path:
    """Resolve ``name`` (with or without ``.md``) to a template file path."""
    stem = name[:-3] if name.endswith(".md") else name
    path = templates_dir(root) / f"{stem}.md"
    return path


def list_template_names(root: Path | None = None) -> list[str]:
    """Sorted stem names of all ``*.md`` templates, excluding ``INDEX.md``."""
    directory = templates_dir(root)
    if not directory.is_dir():
        return []
    names: list[str] = []
    for path in sorted(directory.glob("*.md")):
        if path.name.upper() == "INDEX.MD":
            continue
        names.append(path.stem)
    return names


def load_template(name: str, root: Path | None = None) -> str:
    """Read template file contents as UTF-8 text."""
    path = template_path_for_name(name, root)
    if not path.is_file():
        raise FileNotFoundError(f"Template not found: {path}")
    return path.read_text(encoding="utf-8")


def parse_var_assignments(pairs: Iterable[str]) -> dict[str, str]:
    """
    Parse ``KEY=VALUE`` strings. The first ``=`` splits key and value;
    the value may contain additional ``=`` characters.
    """
    result: dict[str, str] = {}
    for raw in pairs:
        item = raw.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"Invalid --var (expected key=value): {raw!r}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid --var (empty key): {raw!r}")
        result[key] = value.strip()
    return result


def render_template(
    text: str,
    variables: Mapping[str, str],
    *,
    leave_missing: bool = True,
) -> tuple[str, list[str]]:
    """
    Replace ``{{name}}`` placeholders with ``variables[name]``.

    Returns rendered text and a sorted list of placeholder names that had no
    value supplied when ``leave_missing`` is True (placeholders left as
    ``{{name}}`` in output). When ``leave_missing`` is False, missing keys are
    replaced with an empty string and the second element is always an empty
    list.
    """
    missing: set[str] = set()

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in variables:
            return variables[key]
        missing.add(key)
        if leave_missing:
            return match.group(0)
        return ""

    out = _PLACEHOLDER.sub(repl, text)
    if leave_missing:
        return out, sorted(missing)
    return out, []
