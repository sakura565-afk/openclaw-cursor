#!/usr/bin/env python3
"""CLI for listing and rendering prompt templates under ``scripts/prompts/templates/``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.prompts.renderer import (
    list_template_names,
    load_template,
    parse_var_assignments,
    render_template,
    template_path_for_name,
)


def _build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Render reusable prompt templates with {{variable}} placeholders.",
    )
    parser.add_argument(
        "--templates-root",
        type=Path,
        default=None,
        help="Optional base path for prompts package (parent of templates/). "
        "Defaults to this file's directory.",
    )
    subs = parser.add_subparsers(dest="command", required=True)

    list_p = subs.add_parser("list", help="List available template names.")
    list_p.set_defaults(func=_cmd_list)

    render_p = subs.add_parser("render", help="Render a template to stdout or a file.")
    render_p.add_argument(
        "template",
        help="Template stem (e.g. bug_hunt for bug_hunt.md).",
    )
    render_p.add_argument(
        "--var",
        dest="vars",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Variable assignment (repeatable). First '=' separates key from value.",
    )
    render_p.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Write rendered output to this file instead of stdout.",
    )
    render_p.add_argument(
        "--strict",
        action="store_true",
        help="Exit with error if any {{placeholder}} is missing a value.",
    )
    render_p.add_argument(
        "--empty-missing",
        action="store_true",
        help="Replace missing placeholders with empty string (implies non-strict).",
    )
    render_p.set_defaults(func=_cmd_render)

    # Stash default root for subcommands
    parser.set_defaults(_package_root=root)
    return parser


def _cmd_list(args: argparse.Namespace) -> int:
    root = getattr(args, "_package_root", Path(__file__).resolve().parent)
    names = list_template_names(root)
    if not names:
        print("No templates found.", file=sys.stderr)
        return 1
    for name in names:
        print(name)
    return 0


def _cmd_render(args: argparse.Namespace) -> int:
    root = getattr(args, "_package_root", Path(__file__).resolve().parent)
    try:
        text = load_template(args.template, root)
        variables = parse_var_assignments(args.vars)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    leave_missing = not args.empty_missing
    rendered, missing = render_template(text, variables, leave_missing=leave_missing)

    if args.strict and missing:
        print(
            "Missing values for placeholders: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 3

    if missing and not args.empty_missing:
        print(
            "Warning: missing values for: " + ", ".join(missing),
            file=sys.stderr,
        )

    out = args.output
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
        if not rendered.endswith("\n"):
            sys.stdout.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.templates_root is not None:
        args._package_root = args.templates_root
    func = args.func
    return int(func(args))


if __name__ == "__main__":
    raise SystemExit(main())
