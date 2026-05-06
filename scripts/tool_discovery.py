#!/usr/bin/env python3
"""Scan OpenClaw workspace(s) for CLI tools, Python modules, APIs, and skills."""

from __future__ import annotations

import argparse
import ast
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
URL_RE = re.compile(r'https?://[^\s\'\"`\)\]\>]+')


def _default_openclaw_home() -> Path | None:
    raw = os.environ.get("OPENCLAW_HOME")
    if raw:
        return Path(raw).expanduser()
    home = Path.home() / ".openclaw"
    return home if home.is_dir() else None


def _resolve_workspace_roots(paths: list[Path]) -> list[Path]:
    resolved: list[Path] = []
    seen: set[Path] = set()
    for p in paths:
        r = p.expanduser().resolve()
        if r.is_dir() and r not in seen:
            resolved.append(r)
            seen.add(r)
    return resolved


def collect_scan_roots(workspace_paths: list[Path], *, include_openclaw_home: bool) -> list[Path]:
    roots: list[Path] = list(workspace_paths)
    if include_openclaw_home:
        oh = _default_openclaw_home()
        if oh:
            ws = oh / "workspace"
            if ws.is_dir():
                roots.append(ws)
    return _resolve_workspace_roots(roots)


def iter_python_files(roots: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    skip_parts = {"__pycache__", ".git", "venv", ".venv", "node_modules"}
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if any(part in skip_parts for part in path.parts):
                continue
            out.append(path)
    out.sort()
    return out


def _mod_stem_for_path(path: Path, root: Path) -> str:
    rel = path.relative_to(root)
    return ".".join(rel.with_suffix("").parts)


def _collect_string_constant(node: ast.expr | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _argparse_description(
    call: ast.Call,
    module_doc: str | None,
) -> str | None:
    for kw in call.keywords:
        if kw.arg != "description":
            continue
        if isinstance(kw.value, ast.Name) and kw.value.id == "__doc__":
            return (module_doc or "").strip() or None
        return _collect_string_constant(kw.value)
    return None


def _is_argument_parser_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name) and func.id == "ArgumentParser":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "ArgumentParser":
        return isinstance(func.value, ast.Name)
    return False


def _extract_subcommands(tree: ast.AST) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "add_parser"
                and node.args
            ):
                name = _collect_string_constant(node.args[0])
                helpt: str | None = None
                for kw in node.keywords:
                    if kw.arg in ("help", "description"):
                        helpt = _collect_string_constant(kw.value)
                if name:
                    found.append((name, (helpt or "").strip()))
            self.generic_visit(node)

    Visitor().visit(tree)
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, h in found:
        if name not in seen:
            ordered.append((name, h))
            seen.add(name)
    return ordered


@dataclass
class ParsedCLI:
    path: Path
    relative_repo: str
    import_target: str
    module_doc: str | None
    descriptions: list[str]
    subcommands: list[tuple[str, str]]
    has_main_guard: bool


def parse_cli_module(path: Path, base_root: Path) -> ParsedCLI | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    try:
        rel = path.relative_to(base_root).as_posix()
    except ValueError:
        rel = str(path)
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return None

    module_doc = ast.get_docstring(tree, clean=True)
    descriptions: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_argument_parser_call(node):
            desc = _argparse_description(node, module_doc)
            if desc and desc.strip() and desc not in descriptions:
                descriptions.append(desc.strip())
    subcommands = _extract_subcommands(tree)
    has_main = bool(re.search(r'if\s+__name__\s*==\s*["\']__main__["\']', text))

    try:
        import_target = _mod_stem_for_path(path, base_root)
    except ValueError:
        import_target = path.with_suffix("").name
    return ParsedCLI(
        path=path,
        relative_repo=rel,
        import_target=import_target,
        module_doc=module_doc,
        descriptions=descriptions,
        subcommands=subcommands,
        has_main_guard=has_main,
    )


def extract_urls(text: str) -> list[str]:
    raw = URL_RE.findall(text)
    cleaned: list[str] = []
    seen: set[str] = set()
    for u in raw:
        u = u.rstrip(".,);\"'")
        if u not in seen and len(u) < 500:
            seen.add(u)
            cleaned.append(u)
    return sorted(cleaned)


@dataclass
class SkillDirInfo:
    name: str
    path: Path
    has_readme: bool
    py_files: list[str]


def discover_skill_directories(openclaw_home: Path | None) -> list[SkillDirInfo]:
    if not openclaw_home:
        return []
    roots = [openclaw_home / "skills", openclaw_home / "workspace" / "skills"]
    out: list[SkillDirInfo] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            key = child.resolve()
            if key in seen:
                continue
            seen.add(key)
            py_files = sorted(p.name for p in child.glob("*.py") if p.is_file())
            has_readme = any(
                p.is_file() for p in child.iterdir() if p.name.lower() in {"readme.md", "readme.rst"}
            )
            out.append(
                SkillDirInfo(
                    name=child.name,
                    path=child,
                    has_readme=has_readme,
                    py_files=py_files,
                )
            )
    out.sort(key=lambda s: s.name.lower())
    return out


def discover_src_skills(repo_root: Path) -> list[Path]:
    pkg = repo_root / "src" / "skills"
    if not pkg.is_dir():
        return []
    return sorted(
        p for p in pkg.glob("*.py") if p.is_file() and p.name != "__init__.py"
    )


def discover_example_docs(repo_root: Path) -> list[Path]:
    ex = repo_root / "examples"
    if not ex.is_dir():
        return []
    return sorted(p for p in ex.iterdir() if p.suffix.lower() in {".yaml", ".yml", ".json"})


def _under_repo(path: Path, repo_root: Path) -> bool:
    try:
        path.resolve().relative_to(repo_root.resolve())
        return True
    except ValueError:
        return False


def _longest_scan_root(path: Path, scan_roots: list[Path]) -> Path:
    resolved = path.resolve()
    best: Path | None = None
    best_len = -1
    for root in scan_roots:
        r = root.resolve()
        try:
            resolved.relative_to(r)
        except ValueError:
            continue
        n = len(r.parts)
        if n > best_len:
            best_len = n
            best = r
    return best if best is not None else scan_roots[0]


def _invocation_line(parsed: ParsedCLI, repo_root: Path) -> str:
    try:
        rel = parsed.path.relative_to(repo_root.resolve())
    except ValueError:
        return f'python "{parsed.path}"'
    if rel.parts and rel.parts[0] == "scripts":
        return f"python -m {parsed.import_target}"
    if rel.parts and rel.parts[0] == "src":
        return f"python -m {parsed.import_target}"
    return f"python {rel.as_posix()}"


def build_registry_markdown(
    *,
    scan_roots: list[Path],
    repo_root: Path,
    openclaw_home: Path | None,
    generated_at: datetime,
) -> str:
    lines: list[str] = [
        "# OpenClaw tools registry",
        "",
        "This file is generated for agent reference. Regenerate after adding or changing tools.",
        "",
        f"**Generated:** `{generated_at.isoformat(timespec='seconds')}` (UTC)",
        "",
        "## Scanned locations",
        "",
    ]
    for r in scan_roots:
        lines.append(f"- `{r}`")
    if openclaw_home:
        lines.append(f"- OpenClaw home (skills): `{openclaw_home}`")
    lines.extend(["", "---", ""])

    py_files = iter_python_files(scan_roots)
    repo_resolved = repo_root.resolve()

    cli_by_section: dict[str, list[ParsedCLI]] = {
        "scripts": [],
        "src": [],
        "workspace": [],
        "repo_other": [],
    }

    apis_by_file: list[tuple[str, list[str]]] = []
    for path in py_files:
        base_root = repo_resolved if _under_repo(path, repo_resolved) else _longest_scan_root(path, scan_roots)

        if _under_repo(path, repo_resolved):
            try:
                rel = path.relative_to(repo_resolved)
            except ValueError:
                continue
            if rel.parts and rel.parts[0] == "tests":
                continue
            section = (
                "scripts"
                if rel.parts[0] == "scripts"
                else ("src" if rel.parts[0] == "src" else "repo_other")
            )
        else:
            section = "workspace"

        parsed = parse_cli_module(path, base_root)
        if parsed is None:
            continue

        if parsed.descriptions or parsed.subcommands:
            cli_by_section.setdefault(section, []).append(parsed)

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        urls = extract_urls(text)
        label = path.relative_to(repo_resolved).as_posix() if _under_repo(path, repo_resolved) else str(path)
        if urls:
            apis_by_file.append((label, urls))

    def sort_cli(p: ParsedCLI) -> str:
        return p.import_target.lower()

    lines.append("## CLI tools (argparse)")
    lines.append("")
    lines.append(
        "Typical usage: run from the repository root with `PYTHONPATH` set so `scripts` and `src` "
        "resolve (many environments do this automatically when executing from the project)."
    )
    lines.append("")
    for section_name, heading in (
        ("scripts", "### `scripts/` — automation CLIs"),
        ("src", "### `src/` — package CLIs"),
        ("repo_other", "### Other repository Python (argparse)"),
        ("workspace", "### OpenClaw workspace (outside repo root)"),
    ):
        bucket = cli_by_section.get(section_name, [])
        if not bucket:
            continue
        lines.append(heading)
        lines.append("")
        for p in sorted(bucket, key=sort_cli):
            lines.append(f"#### `{p.import_target}`")
            lines.append("")
            lines.append(f"- **Source:** `{p.relative_repo}`")
            invoke = _invocation_line(p, repo_resolved) if section_name in {"scripts", "src", "repo_other"} else (
                f'python "{p.path}"'
            )
            lines.append(f"- **Invoke:** `{invoke}`")
            if p.module_doc:
                first = p.module_doc.strip().split("\n\n")[0].replace("\n", " ")
                if len(first) > 280:
                    first = first[:277] + "..."
                lines.append(f"- **Module summary:** {first}")
            if p.descriptions:
                for d in p.descriptions[:3]:
                    short = d.replace("\n", " ").strip()
                    if len(short) > 320:
                        short = short[:317] + "..."
                    lines.append(f"- **CLI description:** {short}")
            if p.subcommands:
                sub_summ = ", ".join(f"`{name}`" for name, _ in p.subcommands[:12])
                if len(p.subcommands) > 12:
                    sub_summ += ", ..."
                lines.append(f"- **Subcommands / commands:** {sub_summ}")
                for name, h in p.subcommands[:12]:
                    if h:
                        lines.append(f"  - `{name}` — {h}")
            lines.append("")
        lines.append("")

    scripts_dir = repo_root / "scripts"
    lib_only: list[Path] = []
    if scripts_dir.is_dir():
        for path in sorted(scripts_dir.glob("*.py")):
            if path.name == "__init__.py":
                continue
            p = parse_cli_module(path, repo_resolved)
            if p and not p.descriptions and not p.subcommands:
                lib_only.append(path)

    if lib_only:
        lines.append("## Library-style `scripts/` modules (no argparse CLI)")
        lines.append("")
        for path in lib_only:
            parsed = parse_cli_module(path, repo_resolved)
            if not parsed:
                continue
            blurb = ""
            if parsed.module_doc:
                blurb = parsed.module_doc.strip().split("\n\n")[0].replace("\n", " ")
                if len(blurb) > 240:
                    blurb = blurb[:237] + "..."
            lines.append(f"- **`{parsed.import_target}`** (`{parsed.relative_repo}`){': ' + blurb if blurb else ''}")
        lines.append("")

    lines.append("## In-repo Python packages (`src/`)")
    lines.append("")
    src_root = repo_root / "src"
    if src_root.is_dir():
        for pkg in sorted(p for p in src_root.iterdir() if p.is_dir() and (p / "__init__.py").is_file()):
            doc = ""
            doc_path = pkg / "__init__.py"
            if doc_path.is_file():
                try:
                    txt = doc_path.read_text(encoding="utf-8", errors="ignore")
                    tdoc = ast.get_docstring(ast.parse(txt), clean=True)
                    if tdoc:
                        doc = tdoc.strip().split("\n")[0]
                except (OSError, SyntaxError):
                    pass
            slug = ".".join(("src", pkg.name))
            extra = f" — {doc}" if doc else ""
            lines.append(f"- **`{slug}`**{extra}")
            if pkg.name == "skills":
                for sk in discover_src_skills(repo_root):
                    mdoc = ""
                    try:
                        stxt = sk.read_text(encoding="utf-8", errors="ignore")
                        mdoc = ast.get_docstring(ast.parse(stxt), clean=True)
                    except (OSError, SyntaxError):
                        pass
                    line = f"  - `{sk.stem}`"
                    if mdoc:
                        one = mdoc.strip().split("\n\n")[0].replace("\n", " ")
                        if len(one) > 200:
                            one = one[:197] + "..."
                        line += f": {one}"
                    lines.append(line)
    lines.append("")

    lines.append("## HTTP / URL endpoints referenced in code")
    lines.append("")
    lines.append("Constants and literals observed in scanned Python sources (not a live reachability test).")
    lines.append("")
    if not apis_by_file:
        lines.append("_No URLs found in scanned paths._")
    else:
        for label, urls in sorted(apis_by_file, key=lambda x: x[0].lower()):
            lines.append(f"- **`{label}`**")
            for u in urls[:20]:
                lines.append(f"  - `{u}`")
            if len(urls) > 20:
                lines.append(f"  - _…plus {len(urls) - 20} more_")
            lines.append("")

    lines.append("## OpenClaw skill directories")
    lines.append("")
    skill_dirs = discover_skill_directories(openclaw_home)
    if not skill_dirs:
        lines.append(
            "No filesystem skills found under `$OPENCLAW_HOME/skills` or "
            "`$OPENCLAW_HOME/workspace/skills`. Set `OPENCLAW_HOME` or create skill folders "
            "to populate this section."
        )
    else:
        for s in skill_dirs:
            readme = "yes" if s.has_readme else "no"
            pyhint = ", ".join(f"`{n}`" for n in s.py_files[:6]) if s.py_files else "_no .py at top level_"
            if len(s.py_files) > 6:
                pyhint += ", …"
            lines.append(f"- **`{s.name}`** — `{s.path}` (README: {readme}); Python: {pyhint}")
    lines.append("")

    lines.append("## Example artifacts")
    lines.append("")
    examples = discover_example_docs(repo_root)
    if examples:
        for p in examples:
            hint = ""
            try:
                head = p.read_text(encoding="utf-8", errors="ignore").split("\n")[0].strip()
                if head:
                    hint = f" — _{head}_"
            except OSError:
                pass
            lines.append(f"- `{p.relative_to(repo_root).as_posix()}`{hint}")
    else:
        lines.append("_None under `examples/`._")
    lines.append("")

    lines.append("## Usage pattern for agents")
    lines.append("")
    lines.extend(
        [
            "1. Prefer `python -m <module>` for packaged entry points listed above.",
            "2. Check **subcommands** before inventing flags; many tools expose `run`, `scan`, multi-phase workflows, etc.",
            "3. Use **skill directories** on disk for experiments and user-specific automations;",
            "   packaged logic also lives under `src/skills/`.",
            "4. Re-run discovery after substantive changes:",
            "",
            "   ```bash",
            "   python scripts/tool_discovery.py",
            "   ```",
            "",
        ]
    )
    lines.append("")
    return "\n".join(lines)


def write_registry(
    *,
    workspace_paths: list[Path],
    output_path: Path,
    repo_root: Path,
    include_openclaw_home: bool,
) -> Path:
    scan_roots = collect_scan_roots(workspace_paths, include_openclaw_home=include_openclaw_home)
    openclaw_home: Path | None = _default_openclaw_home() if include_openclaw_home else None
    md = build_registry_markdown(
        scan_roots=scan_roots,
        repo_root=repo_root,
        openclaw_home=openclaw_home,
        generated_at=datetime.now(timezone.utc),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(md + "\n", encoding="utf-8")
    return output_path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        dest="workspace",
        action="append",
        type=Path,
        default=None,
        help=(
            "Directory to scan (repeatable). Defaults to the repository root containing this "
            "script."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "prompts" / "tools_registry.md",
        help="Markdown output path for the registry (default: prompts/tools_registry.md under repo root).",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root used for relative paths and imports (defaults to parent of scripts/).",
    )
    parser.add_argument(
        "--no-openclaw-home",
        action="store_true",
        help="Do not merge ~/.openclaw/workspace into scan roots or list home-based skills.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    ws = args.workspace if args.workspace else [REPO_ROOT]
    paths = _resolve_workspace_roots(ws)
    if not paths:
        print("tool_discovery: no valid workspace directories", flush=True)
        return 2
    out = write_registry(
        workspace_paths=paths,
        output_path=args.output.expanduser().resolve(),
        repo_root=args.repo_root.expanduser().resolve(),
        include_openclaw_home=not args.no_openclaw_home,
    )
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
