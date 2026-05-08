#!/usr/bin/env python3
"""Static discovery for CLI scripts and library modules under ``scripts/`` and ``src/``.

Parses Python sources with the ``ast`` module (no imports of discovered modules) to build:

- ``docs/tool_discovery/manifest.json`` — machine-readable catalog
- ``docs/tool_discovery/by_module/*.md`` — one Markdown page per scanned file

Run after changing tools::

    python src/skills/tool_discovery.py --write

CI / local guard::

    python src/skills/tool_discovery.py --check
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


MANIFEST_VERSION = 1
DEFAULT_OUTPUT_SUBDIR = Path("docs/tool_discovery")


@dataclass
class ArgSpec:
    flags: list[str]
    dest: str | None
    help: str | None
    action: str | None
    required: bool | None = None


@dataclass
class SubcommandSpec:
    name: str
    help: str | None = None


@dataclass
class CliProfile:
    parser_descriptions: list[str] = field(default_factory=list)
    arguments: list[ArgSpec] = field(default_factory=list)
    subcommands: list[SubcommandSpec] = field(default_factory=list)


@dataclass
class SymbolDoc:
    kind: str  # "class" | "function"
    name: str
    doc: str | None
    bases: list[str] | None = None


@dataclass
class ToolRecord:
    id: str
    relative_path: str
    purpose: str
    purpose_source: str  # "module_docstring" | "argparse" | "inferred"
    has_cli_guard: bool
    entry_functions: list[str]
    cli: CliProfile
    symbols: list[SymbolDoc]
    notes: list[str] = field(default_factory=list)
    parse_error: str | None = None


def _repo_root() -> Path:
    env = __import__("os").environ.get("OPENCLAW_REPO_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def _iter_tool_paths(repo_root: Path) -> list[Path]:
    scripts = sorted((repo_root / "scripts").glob("*.py"))
    src_py = sorted((repo_root / "src").rglob("*.py"))
    paths = [p for p in scripts + src_py if p.is_file()]
    # Stable unique order
    return sorted(set(paths), key=lambda p: str(p))


def _relative(repo_root: Path, path: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def _clean_doc(text: str | None, limit: int = 1200) -> str | None:
    if text is None:
        return None
    t = text.strip()
    if not t:
        return None
    t = re.sub(r"\s+", " ", t)
    return t[:limit] + ("…" if len(t) > limit else "")


def _first_line(text: str | None) -> str | None:
    if not text:
        return None
    line = text.strip().splitlines()[0].strip()
    return line or None


def _literal_str(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _get_kw(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def _flags_from_add_argument(call: ast.Call) -> list[str]:
    flags: list[str] = []
    for arg in call.args:
        s = _literal_str(arg)
        if s:
            flags.append(s)
    return flags


def _bool_from_node(node: ast.expr | None) -> bool | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return None


def _extract_cli_profile(tree: ast.Module) -> CliProfile:
    profile = CliProfile()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # argparse.ArgumentParser(...)
        if isinstance(func, ast.Attribute) and func.attr == "ArgumentParser":
            desc = _literal_str(_get_kw(node, "description"))
            if desc:
                profile.parser_descriptions.append(desc)
            epilog = _literal_str(_get_kw(node, "epilog"))
            if epilog:
                profile.parser_descriptions.append(f"Epilog: {epilog}")

        # parser.add_argument(...)
        elif isinstance(func, ast.Attribute) and func.attr == "add_argument":
            flags = _flags_from_add_argument(node)
            dest_kw = _get_kw(node, "dest")
            dest = _literal_str(dest_kw) if dest_kw is not None else None
            help_kw = _get_kw(node, "help")
            help_text = _literal_str(help_kw) if help_kw is not None else None
            action_kw = _get_kw(node, "action")
            action = _literal_str(action_kw) if action_kw is not None else None
            if action is None and isinstance(action_kw, ast.Attribute):
                action = action_kw.attr
            if isinstance(action_kw, ast.Name):
                action = action_kw.id
            req = _bool_from_node(_get_kw(node, "required"))
            if flags or dest or help_text or action:
                profile.arguments.append(
                    ArgSpec(
                        flags=flags,
                        dest=dest,
                        help=_clean_doc(help_text, 500),
                        action=action,
                        required=req,
                    )
                )

        # subparsers.add_parser("name", help="...")
        elif isinstance(func, ast.Attribute) and func.attr == "add_parser":
            name = _literal_str(node.args[0]) if node.args else None
            if not name:
                continue
            help_text = _literal_str(_get_kw(node, "help"))
            profile.subcommands.append(SubcommandSpec(name=name, help=_clean_doc(help_text, 500)))

    return profile


def _has_main_guard(tree: ast.Module) -> bool:
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.Compare):
            continue
        left = test.left
        if not isinstance(left, ast.Name) or left.id != "__name__":
            continue
        # __name__ == '__main__' or "__main__"
        for op, comp in zip(test.ops, test.comparators):
            if not isinstance(op, ast.Eq):
                continue
            val = _literal_str(comp)
            if val == "__main__":
                return True
    return False


def _module_docstring(tree: ast.Module) -> str | None:
    return ast.get_docstring(tree)


def _extract_symbols(tree: ast.Module) -> list[SymbolDoc]:
    symbols: list[SymbolDoc] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            bases: list[str] = []
            for b in node.bases:
                if isinstance(b, ast.Name):
                    bases.append(b.id)
                elif isinstance(b, ast.Attribute):
                    bases.append(b.attr)
                else:
                    bases.append(ast.unparse(b))
            symbols.append(
                SymbolDoc(
                    kind="class",
                    name=node.name,
                    doc=_clean_doc(ast.get_docstring(node)),
                    bases=bases or None,
                )
            )
        elif isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            symbols.append(
                SymbolDoc(
                    kind="function",
                    name=node.name,
                    doc=_clean_doc(ast.get_docstring(node)),
                )
            )
    return symbols


def _entry_functions(tree: ast.Module) -> list[str]:
    preferred = ("main", "cli", "run", "parse_args", "parse_arguments")
    found: list[str] = []
    for name in preferred:
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == name:
                found.append(name)
                break
    return sorted(set(found), key=lambda n: preferred.index(n) if n in preferred else len(preferred))


def _infer_purpose(module_doc: str | None, cli: CliProfile) -> tuple[str, str]:
    if module_doc and module_doc.strip():
        return _first_line(module_doc) or module_doc.strip()[:240], "module_docstring"
    if cli.parser_descriptions:
        return _first_line(cli.parser_descriptions[0]) or cli.parser_descriptions[0][:240], "argparse"
    return "Library or helper module (no module docstring or ArgumentParser description found).", "inferred"


def _record_notes(record: ToolRecord) -> None:
    if record.parse_error:
        return
    if not record.symbols and not record.cli.arguments and not record.entry_functions:
        record.notes.append("No public classes/functions and no argparse usage detected; may be a thin shim.")
    if record.has_cli_guard and not record.cli.arguments and "parse_args" not in record.entry_functions:
        record.notes.append("CLI entry guard present but no ``add_argument`` calls matched (dynamic argparse possible).")
    pub_classes = [s for s in record.symbols if s.kind == "class"]
    if pub_classes and record.entry_functions:
        record.notes.append("Mix of class-based implementation and CLI-style entry functions.")


def analyze_file(repo_root: Path, path: Path) -> ToolRecord:
    rel = _relative(repo_root, path)
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        return ToolRecord(
            id=rel,
            relative_path=rel,
            purpose=f"Syntax error in source: {exc}",
            purpose_source="inferred",
            has_cli_guard=False,
            entry_functions=[],
            cli=CliProfile(),
            symbols=[],
            parse_error=f"{type(exc).__name__}: {exc}",
        )

    cli = _extract_cli_profile(tree)
    symbols = _extract_symbols(tree)
    module_doc = _module_docstring(tree)
    purpose, purpose_src = _infer_purpose(module_doc, cli)
    record = ToolRecord(
        id=rel,
        relative_path=rel,
        purpose=purpose,
        purpose_source=purpose_src,
        has_cli_guard=_has_main_guard(tree),
        entry_functions=_entry_functions(tree),
        cli=cli,
        symbols=symbols,
    )
    _record_notes(record)
    return record


def _sanitize_doc_filename(rel_id: str) -> str:
    return rel_id.replace("/", "__").replace("\\", "__")


def _render_markdown(repo_root: Path, record: ToolRecord) -> str:
    lines: list[str] = []
    title = record.id
    lines.append(f"# Tool: `{title}`")
    lines.append("")
    lines.append("## Purpose")
    lines.append("")
    lines.append(record.purpose)
    lines.append("")
    lines.append(f"*Purpose source:* `{record.purpose_source}`")
    lines.append("")

    lines.append("## Location")
    lines.append("")
    lines.append(f"- Repository path: `{record.relative_path}`")
    lines.append(f"- Run from repo root: `python {record.relative_path}`")
    lines.append("")

    if record.parse_error:
        lines.append("## Parse status")
        lines.append("")
        lines.append(f"```\n{record.parse_error}\n```")
        lines.append("")
        return "\n".join(lines)

    lines.append("## CLI")
    lines.append("")
    lines.append(f"- **Has** ``if __name__ == '__main__'`` guard: **{'yes' if record.has_cli_guard else 'no'}**")
    if record.entry_functions:
        lines.append(f"- Notable entry-related functions: {', '.join(f'`{n}`' for n in record.entry_functions)}")
    if record.cli.parser_descriptions:
        lines.append("")
        lines.append("### Parser text")
        for block in record.cli.parser_descriptions:
            lines.append(f"- {block}")
    if record.cli.subcommands:
        lines.append("")
        lines.append("### Subcommands")
        for sub in record.cli.subcommands:
            h = f" — {sub.help}" if sub.help else ""
            lines.append(f"- `{sub.name}`{h}")
    if record.cli.arguments:
        lines.append("")
        lines.append("### Arguments (statically extracted)")
        for arg in record.cli.arguments:
            flags = " ".join(f"`{f}`" for f in arg.flags) if arg.flags else "(positional)"
            dest = f", dest=`{arg.dest}`" if arg.dest else ""
            act = f", action=`{arg.action}`" if arg.action else ""
            req = ", required" if arg.required else ""
            helpt = f": {arg.help}" if arg.help else ""
            lines.append(f"- {flags}{dest}{act}{req}{helpt}")
    if record.has_cli_guard and not record.cli.arguments:
        lines.append("")
        lines.append(
            "*No* ``add_argument`` calls were found via static analysis. "
            "The script may build parsers dynamically or delegate to another module."
        )
    lines.append("")

    lines.append("## Symbols (public)")
    lines.append("")
    if not record.symbols:
        lines.append("*No* public top-level classes or functions (names not starting with `_`), "
                     "or none with extractable docstrings beyond listing here.")
    else:
        for sym in record.symbols:
            if sym.kind == "class":
                bases = f" extends {', '.join(sym.bases)}" if sym.bases else ""
                lines.append(f"- **class** `{sym.name}`{bases}")
            else:
                lines.append(f"- **def** `{sym.name}`")
            if sym.doc:
                lines.append(f"  - {sym.doc}")
    lines.append("")

    if record.notes:
        lines.append("## Notes")
        lines.append("")
        for n in record.notes:
            lines.append(f"- {n}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"*Generated by `src/skills/tool_discovery.py` from `{record.relative_path}`.*")
    return "\n".join(lines)


def _tool_record_to_json(record: ToolRecord) -> dict[str, Any]:
    payload = asdict(record)
    return payload


def discover(repo_root: Path | None = None) -> list[ToolRecord]:
    root = repo_root or _repo_root()
    return [analyze_file(root, p) for p in _iter_tool_paths(root)]


def write_outputs(
    repo_root: Path,
    records: list[ToolRecord],
    out_dir: Path | None = None,
) -> tuple[Path, Path]:
    base = (repo_root / (out_dir or DEFAULT_OUTPUT_SUBDIR)).resolve()
    by_mod = base / "by_module"
    by_mod.mkdir(parents=True, exist_ok=True)

    manifest_path = base / "manifest.json"
    manifest = {
        "version": MANIFEST_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo_root),
        "scan_globs": ["scripts/*.py", "src/**/*.py"],
        "tools": [_tool_record_to_json(r) for r in records],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8")

    for path in by_mod.glob("*.md"):
        path.unlink()

    for record in records:
        name = _sanitize_doc_filename(record.id) + ".md"
        (by_mod / name).write_text(_render_markdown(repo_root, record), encoding="utf-8")

    index_lines = [
        "# Tool discovery index",
        "",
        "Timestamps and metadata live in [`manifest.json`](manifest.json) (`generated_at`).",
        "Regenerate after edits: `python3 src/skills/tool_discovery.py --write`.",
        "",
        "## Modules",
        "",
    ]
    for record in sorted(records, key=lambda r: r.id):
        doc_name = _sanitize_doc_filename(record.id) + ".md"
        blurb = record.purpose.replace("\n", " ")
        status = " (parse error)" if record.parse_error else ""
        index_lines.append(f"- [{record.id}](by_module/{doc_name}) — {blurb}{status}")
    index_lines.append("")
    (base / "INDEX.md").write_text("\n".join(index_lines), encoding="utf-8")

    return manifest_path, base


def _read_manifest(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def manifests_equivalent(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Compare ignoring generated_at timestamp."""

    def strip(meta: dict[str, Any]) -> dict[str, Any]:
        m = dict(meta)
        m.pop("generated_at", None)
        return m

    return strip(a) == strip(b)


def check_up_to_date(repo_root: Path, out_dir: Path | None = None) -> tuple[bool, str]:
    records = discover(repo_root)
    manifest_path = (repo_root / (out_dir or DEFAULT_OUTPUT_SUBDIR) / "manifest.json").resolve()
    fresh_manifest_path, _ = write_outputs(repo_root, records, Path(".tool_discovery_tmp"))
    fresh = json.loads(fresh_manifest_path.read_text(encoding="utf-8"))
    tmp_root = repo_root / ".tool_discovery_tmp"
    try:
        existing = _read_manifest(manifest_path)
        if existing is None:
            return False, f"Missing manifest at {manifest_path}"
        if not manifests_equivalent(existing, fresh):
            return False, "manifest.json is stale relative to sources (run with --write)."
        by_mod = manifest_path.parent / "by_module"
        if not by_mod.is_dir():
            return False, f"Missing directory {by_mod}"
        index_path = manifest_path.parent / "INDEX.md"
        if not index_path.is_file():
            return False, f"Missing {index_path}"

        fresh_by_mod = tmp_root / "by_module"
        for record in records:
            name = _sanitize_doc_filename(record.id) + ".md"
            left = (by_mod / name).read_text(encoding="utf-8") if (by_mod / name).is_file() else ""
            right = (fresh_by_mod / name).read_text(encoding="utf-8") if (fresh_by_mod / name).is_file() else ""
            if left != right:
                return False, f"Stale documentation: by_module/{name} (run with --write)."

        fresh_index = (tmp_root / "INDEX.md").read_text(encoding="utf-8")
        if index_path.read_text(encoding="utf-8") != fresh_index:
            return False, "Stale INDEX.md (run with --write)."

        expected_md = {_sanitize_doc_filename(r.id) + ".md" for r in records}
        actual_md = {p.name for p in by_mod.glob("*.md")}
        if actual_md != expected_md:
            return (
                False,
                "by_module/*.md set does not match scan "
                f"(expected {sorted(expected_md)}, got {sorted(actual_md)}); run with --write.",
            )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)

    return True, "tool discovery outputs are current."


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Discover tools under scripts/ and src/ and emit manifest + docs.")
    p.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root (default: inferred from this file or OPENCLAW_REPO_ROOT).",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUTPUT_SUBDIR,
        help=f"Output directory under repo root (default: {DEFAULT_OUTPUT_SUBDIR.as_posix()}).",
    )
    p.add_argument("--write", action="store_true", help="Write manifest.json, INDEX.md, and by_module/*.md.")
    p.add_argument("--check", action="store_true", help="Exit with non-zero status if outputs are stale.")
    p.add_argument("--json", action="store_true", help="Print manifest JSON to stdout (no files).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    repo_root = (args.repo_root or _repo_root()).resolve()
    records = discover(repo_root)

    if args.json:
        manifest = {
            "version": MANIFEST_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "repo_root": str(repo_root),
            "scan_globs": ["scripts/*.py", "src/**/*.py"],
            "tools": [_tool_record_to_json(r) for r in records],
        }
        sys.stdout.write(json.dumps(manifest, indent=2, sort_keys=False) + "\n")
        return 0

    if args.check:
        ok, message = check_up_to_date(repo_root, args.out_dir)
        sys.stdout.write(message + "\n")
        return 0 if ok else 2

    if args.write:
        manifest_path, base = write_outputs(repo_root, records, args.out_dir)
        sys.stdout.write(f"Wrote {manifest_path}\nWrote markdown under {base / 'by_module'}\n")
        return 0

    sys.stdout.write(
        "No action specified. Use --write to regenerate artifacts, --check for CI, or --json for stdout.\n"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
