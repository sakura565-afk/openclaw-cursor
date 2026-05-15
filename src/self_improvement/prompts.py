"""Load and render self-improvement prompt templates from ``prompts/templates/``.

Run as a module::

    python -m src.self_improvement.prompts list
    python -m src.self_improvement.prompts show error_analysis
    python -m src.self_improvement.prompts placeholders code_review
    python -m src.self_improvement.prompts render task_planning --set objective="Ship feature X"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Mapping, Sequence

_PLACEHOLDER_PATTERN = re.compile(r"\{\{(\w+)\}\}")

SELF_IMPROVEMENT_TEMPLATE_IDS: tuple[str, ...] = (
    "error_analysis",
    "session_review",
    "code_review",
    "task_planning",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATES_DIR = _REPO_ROOT / "prompts" / "templates"


def repo_root() -> Path:
    """Repository root (parent of ``src/``)."""

    return _REPO_ROOT


def templates_dir(root: Path | None = None) -> Path:
    return (root or _REPO_ROOT) / "prompts" / "templates"


def template_path(template_id: str, *, root: Path | None = None) -> Path:
    return templates_dir(root) / f"{template_id}.md"


def extract_placeholders(text: str) -> tuple[str, ...]:
    """Return sorted unique ``{{name}}`` keys found in *text*."""

    return tuple(sorted(set(_PLACEHOLDER_PATTERN.findall(text))))


def load_template(template_id: str, *, root: Path | None = None) -> str:
    path = template_path(template_id, root=root)
    if not path.is_file():
        available = ", ".join(list_templates(root=root)) or "(none)"
        raise FileNotFoundError(f"Unknown template {template_id!r}. Available: {available}")
    return path.read_text(encoding="utf-8")


def list_templates(*, root: Path | None = None, self_improvement_only: bool = True) -> list[str]:
    directory = templates_dir(root)
    if not directory.is_dir():
        return []
    on_disk = {
        path.stem
        for path in directory.glob("*.md")
        if not path.name.startswith("_")
    }
    if self_improvement_only:
        return [name for name in SELF_IMPROVEMENT_TEMPLATE_IDS if name in on_disk]
    return sorted(on_disk)


def render_template(
    text: str,
    values: Mapping[str, str],
    *,
    strict: bool = True,
    missing: str = "not provided",
) -> str:
    """Replace ``{{name}}`` in *text* with *values* (or *missing* when non-strict)."""

    effective = dict(values)

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in effective:
            return str(effective[key])
        if strict:
            raise KeyError(f"Missing placeholder value: {key!r}")
        return missing

    return _PLACEHOLDER_PATTERN.sub(_replace, text)


def render(
    template_id: str,
    values: Mapping[str, str],
    *,
    root: Path | None = None,
    strict: bool = True,
    missing: str = "not provided",
) -> str:
    return render_template(
        load_template(template_id, root=root),
        values,
        strict=strict,
        missing=missing,
    )


def _parse_set_pairs(pairs: Sequence[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"Expected key=value, got {pair!r}")
        key, _, value = pair.partition("=")
        key = key.strip()
        if not key:
            raise ValueError(f"Empty key in {pair!r}")
        out[key] = value
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="List, inspect, and render self-improvement prompt templates.",
    )
    parser.add_argument(
        "--root-dir",
        type=Path,
        default=None,
        help="Repository root (default: auto-detected from package location)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List self-improvement template ids")

    show_parser = sub.add_parser("show", help="Print a template file (raw markdown)")
    show_parser.add_argument("template_id", choices=SELF_IMPROVEMENT_TEMPLATE_IDS)

    placeholders_parser = sub.add_parser("placeholders", help="List {{placeholders}} in a template")
    placeholders_parser.add_argument("template_id", choices=SELF_IMPROVEMENT_TEMPLATE_IDS)

    render_parser = sub.add_parser("render", help="Render a template with placeholder values")
    render_parser.add_argument("template_id", choices=SELF_IMPROVEMENT_TEMPLATE_IDS)
    render_parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Placeholder value (repeatable)",
    )
    render_parser.add_argument(
        "--vars-json",
        default=None,
        help='JSON object of placeholder values, e.g. \'{"objective":"Ship X"}\'',
    )
    render_parser.add_argument(
        "--strict",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require every {{placeholder}} to be supplied (default: true)",
    )
    render_parser.add_argument(
        "--missing",
        default="not provided",
        help="Substitute for unset placeholders when --no-strict",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root: Path | None = args.root_dir

    if args.command == "list":
        for template_id in list_templates(root=root):
            path = template_path(template_id, root=root)
            print(f"{template_id}\t{path}")
        return 0

    if args.command == "show":
        text = load_template(args.template_id, root=root)
        sys.stdout.write(text)
        if text and not text.endswith("\n"):
            sys.stdout.write("\n")
        return 0

    if args.command == "placeholders":
        names = extract_placeholders(load_template(args.template_id, root=root))
        for name in names:
            print(name)
        return 0

    values: dict[str, str] = {}
    if args.vars_json:
        loaded = json.loads(args.vars_json)
        if not isinstance(loaded, dict):
            raise ValueError("--vars-json must be a JSON object")
        values.update({str(k): str(v) for k, v in loaded.items()})
    values.update(_parse_set_pairs(args.set))

    try:
        text = render(
            args.template_id,
            values,
            root=root,
            strict=args.strict,
            missing=args.missing,
        )
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    sys.stdout.write(text)
    if text and not text.endswith("\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
