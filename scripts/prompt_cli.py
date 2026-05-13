#!/usr/bin/env python3
"""List, view, render, and create reusable LLM prompt templates (YAML or JSON).

Templates live under ``prompts/templates/`` by default. Each file may define:

* ``metadata`` — optional mapping with ``description``, ``author``, ``version``, ``tags``.
* ``template`` — required prompt body (multi-line string). Alias key: ``body``.

Placeholders use Python-style ``{variable}`` braces. Literal braces in text must be
escaped as ``{{`` and ``}}``.

Configuration defaults are read from ``prompts/config.yaml`` (resolved relative to
the repository root or the current working directory).

Examples:

    python -m scripts.prompt_cli list
    python -m scripts.prompt_cli view code_review
    python -m scripts.prompt_cli render code_review \\
        --set repository_name=myapp \\
        --set change_summary="Add retry logic" \\
        --set diff_or_files="src/net/client.py" \\
        --set focus_areas="correctness, error handling" \\
        --set reviewer_context="Python 3.11 service"
    python -m scripts.prompt_cli create my_template --description "One-liner"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

import yaml

CONFIG_NAME = "config.yaml"
TEMPLATES_SUBDIR_DEFAULT = "templates"
METADATA_KEYS = ("description", "author", "version", "tags")


def _repo_root_candidates(start: Path) -> list[Path]:
    roots: list[Path] = []
    here = start.resolve()
    for p in [here, *here.parents]:
        if (p / "prompts" / CONFIG_NAME).is_file():
            roots.append(p)
    script_dir = Path(__file__).resolve().parent
    for p in [script_dir, *script_dir.parents]:
        if (p / "prompts" / CONFIG_NAME).is_file() and p not in roots:
            roots.append(p)
    return roots


def default_config_path() -> Path:
    roots = _repo_root_candidates(Path.cwd())
    if roots:
        return roots[0] / "prompts" / CONFIG_NAME
    return Path.cwd() / "prompts" / CONFIG_NAME


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping at root of {path}, got {type(data).__name__}")
    return data


def default_config() -> dict[str, Any]:
    return {
        "templates_directory": TEMPLATES_SUBDIR_DEFAULT,
        "strict_substitution": True,
        "default_author": "",
        "default_version": "1.0.0",
    }


def load_config(path: Path) -> dict[str, Any]:
    cfg = default_config()
    if not path.is_file():
        return cfg
    loaded = load_yaml_mapping(path)
    cfg.update({k: v for k, v in loaded.items() if v is not None})
    return cfg


def prompts_home(config_path: Path) -> Path:
    return config_path.resolve().parent


def resolve_templates_dir(config_path: Path, config: Mapping[str, Any]) -> Path:
    home = prompts_home(config_path)
    raw = str(config.get("templates_directory") or TEMPLATES_SUBDIR_DEFAULT).strip()
    p = Path(raw)
    if p.is_absolute():
        return p
    return (home / p).resolve()


def list_template_files(templates_dir: Path) -> list[Path]:
    if not templates_dir.is_dir():
        return []
    paths: list[Path] = []
    for pattern in ("*.yaml", "*.yml", "*.json"):
        paths.extend(sorted(templates_dir.glob(pattern)))
    # de-dupe case-insensitive same file
    seen: set[str] = set()
    unique: list[Path] = []
    for p in sorted(paths, key=lambda x: x.name.lower()):
        key = str(p.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def load_template_document(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        doc = load_yaml_mapping(path)
    elif suffix == ".json":
        text = path.read_text(encoding="utf-8")
        doc = json.loads(text) if text.strip() else {}
        if not isinstance(doc, dict):
            raise ValueError(f"Expected JSON object in {path}, got {type(doc).__name__}")
    else:
        raise ValueError(f"Unsupported template format: {path}")

    meta_raw = doc.get("metadata")
    metadata: dict[str, Any] = {}
    if isinstance(meta_raw, dict):
        metadata = dict(meta_raw)
    elif meta_raw is not None:
        raise ValueError("`metadata` must be a mapping when present.")

    body = doc.get("template")
    if body is None:
        body = doc.get("body")
    if body is None:
        raise ValueError("Template must include a `template` or `body` string field.")
    if not isinstance(body, str):
        raise ValueError("`template` / `body` must be a string.")

    return {"metadata": metadata, "template": body, "source_path": path}


def normalize_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in METADATA_KEYS:
        if key not in metadata:
            continue
        val = metadata[key]
        if key == "tags":
            if val is None:
                out[key] = []
            elif isinstance(val, (list, tuple)):
                out[key] = [str(x) for x in val]
            elif isinstance(val, str):
                out[key] = [t.strip() for t in re.split(r"[,\n;]+", val) if t.strip()]
            else:
                raise ValueError("`tags` must be a list or comma-separated string.")
        else:
            out[key] = "" if val is None else str(val)
    return out


def parse_assignments(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in pairs:
        if "=" not in raw:
            raise ValueError(f"Expected KEY=value, got: {raw!r}")
        key, val = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Empty key in assignment: {raw!r}")
        out[key] = val
    return out


def load_vars_file(path: Path) -> dict[str, str]:
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        data = load_yaml_mapping(path)
    elif suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        raise ValueError("Variables file must be .yaml, .yml, or .json")
    if not isinstance(data, dict):
        raise ValueError("Variables file must contain a JSON/YAML object at the root.")
    return {str(k): "" if v is None else str(v) for k, v in data.items()}


def substitute(template: str, variables: Mapping[str, str], *, strict: bool) -> str:
    if strict:
        return template.format_map(dict(variables))

    class _Loose(dict):
        def __missing__(self, key: str) -> str:  # type: ignore[override]
            return "{" + key + "}"

    return template.format_map(_Loose(variables))


def template_stem(path: Path) -> str:
    return path.stem


def cmd_list(templates_dir: Path, *, verbose: bool) -> int:
    files = list_template_files(templates_dir)
    if not files:
        print(f"No templates found in {templates_dir}", file=sys.stderr)
        return 1
    if not verbose:
        for p in files:
            print(template_stem(p))
        return 0
    for p in files:
        try:
            doc = load_template_document(p)
        except Exception as exc:  # noqa: BLE001 — surface parse errors per file
            print(f"{template_stem(p)}\t(parse error: {exc})", file=sys.stderr)
            continue
        meta = normalize_metadata(doc["metadata"])
        desc = (meta.get("description") or "").replace("\n", " ").strip()
        tags = ",".join(meta.get("tags") or [])
        ver = meta.get("version") or ""
        print(f"{template_stem(p)}\t{ver}\t{tags}\t{desc}")
    return 0


def cmd_view(name: str, templates_dir: Path) -> int:
    path = find_template_path(name, templates_dir)
    if path is None:
        print(f"Unknown template: {name}", file=sys.stderr)
        return 2
    doc = load_template_document(path)
    meta = normalize_metadata(doc["metadata"])
    print(f"# {path.name}\n")
    print("metadata:")
    print(yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).rstrip())
    print("\ntemplate: |")
    for line in doc["template"].splitlines():
        print("  " + line)
    return 0


def cmd_render(
    name: str,
    templates_dir: Path,
    *,
    assignments: list[str],
    vars_file: Path | None,
    strict: bool,
) -> int:
    path = find_template_path(name, templates_dir)
    if path is None:
        print(f"Unknown template: {name}", file=sys.stderr)
        return 2
    doc = load_template_document(path)
    variables = {}
    if vars_file is not None:
        variables.update(load_vars_file(vars_file))
    variables.update(parse_assignments(assignments))
    try:
        rendered = substitute(doc["template"], variables, strict=strict)
    except KeyError as exc:
        missing = exc.args[0]
        print(f"Missing template variable: {missing}", file=sys.stderr)
        return 3
    sys.stdout.write(rendered)
    if not rendered.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def find_template_path(name: str, templates_dir: Path) -> Path | None:
    stem = Path(name).stem
    for p in list_template_files(templates_dir):
        if p.stem == stem:
            return p
    return None


def cmd_create(
    name: str,
    templates_dir: Path,
    config: Mapping[str, Any],
    *,
    description: str,
    author: str | None,
    version: str | None,
    tags: list[str],
    force: bool,
) -> int:
    templates_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9_\-]+", "-", name).strip("-") or "template"
    out = templates_dir / f"{safe}.yaml"
    if out.exists() and not force:
        print(f"Refusing to overwrite existing file: {out} (use --force)", file=sys.stderr)
        return 4

    meta = {
        "description": description or "New prompt template.",
        "author": author if author is not None else str(config.get("default_author") or ""),
        "version": version if version is not None else str(config.get("default_version") or "1.0.0"),
        "tags": tags,
    }
    ph = "{placeholders}"
    body = (
        f"Replace this section with your prompt. Use {ph} for values you will\n"
        "pass when rendering, for example:\n\n"
        f"  python -m scripts.prompt_cli render {safe} --set placeholder=example\n"
    )

    document = {"metadata": meta, "template": body}
    out.write_text(yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(str(out))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"Path to prompts/{CONFIG_NAME} (default: discover from cwd or repo).",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List available template names.")
    p_list.add_argument("-v", "--verbose", action="store_true", help="Include metadata columns.")

    p_view = sub.add_parser("view", help="Show metadata and template body for one template.")
    p_view.add_argument("name", help="Template name (stem, with or without extension).")

    p_render = sub.add_parser("render", help="Render a template with variable values.")
    p_render.add_argument("name", help="Template name (stem).")
    p_render.add_argument(
        "--set",
        dest="assignments",
        metavar="KEY=value",
        action="append",
        default=[],
        help="Variable assignment (repeatable).",
    )
    p_render.add_argument(
        "--vars-file",
        type=Path,
        default=None,
        help="YAML or JSON file of key/value pairs merged before --set.",
    )
    p_render.add_argument(
        "--no-strict",
        action="store_true",
        help="Leave unknown {placeholders} untouched instead of erroring.",
    )

    p_create = sub.add_parser("create", help="Create a new YAML template scaffold.")
    p_create.add_argument("name", help="Template stem (letters, digits, dash, underscore).")
    p_create.add_argument("--description", default="", help="Short description for metadata.")
    p_create.add_argument("--author", default=None, help="Override config default_author.")
    p_create.add_argument("--version", default=None, help="Override config default_version.")
    p_create.add_argument(
        "--tags",
        default="",
        help="Comma-separated tags for metadata.",
    )
    p_create.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing file with the same stem.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config_path = args.config if args.config is not None else default_config_path()
    config = load_config(config_path)
    templates_dir = resolve_templates_dir(config_path, config)
    strict_default = bool(config.get("strict_substitution", True))

    cmd = args.command
    if cmd == "list":
        return cmd_list(templates_dir, verbose=args.verbose)
    if cmd == "view":
        return cmd_view(args.name, templates_dir)
    if cmd == "render":
        strict = strict_default and not args.no_strict
        return cmd_render(
            args.name,
            templates_dir,
            assignments=args.assignments,
            vars_file=args.vars_file,
            strict=strict,
        )
    if cmd == "create":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
        return cmd_create(
            args.name,
            templates_dir,
            config,
            description=args.description,
            author=args.author,
            version=args.version,
            tags=tags,
            force=args.force,
        )

    parser.error(f"Unknown command: {cmd}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
