#!/usr/bin/env python3
"""Discover OpenClaw tools and generate local markdown documentation."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

RESET = "\033[0m"
COLORS = {
    "bold": "\033[1m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
    "green": "\033[32m",
    "magenta": "\033[35m",
    "red": "\033[31m",
    "yellow": "\033[33m",
}

TEXT_FILE_SUFFIXES = {
    ".json",
    ".md",
    ".py",
    ".txt",
    ".yaml",
    ".yml",
}
SCAN_ROOTS = ("scripts", "src")
SKIP_DIRECTORIES = {
    ".git",
    ".learnings",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    ".venv",
    "venv",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def use_color(stream: Any = None) -> bool:
    stream = stream or sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)())


def colorize(text: str, color: str, *, enabled: bool | None = None) -> str:
    enabled = use_color() if enabled is None else enabled
    if not enabled:
        return text
    prefix = COLORS.get(color)
    if not prefix:
        return text
    return f"{prefix}{text}{RESET}"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def data_root(root: Path | None = None) -> Path:
    return (root or repo_root()) / ".learnings" / "tool_discovery"


def _ensure_storage(root: Path | None = None) -> Path:
    storage = data_root(root)
    (storage / "docs").mkdir(parents=True, exist_ok=True)
    return storage


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: max(limit - 3, 0)]}..."


def _read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


@dataclass
class CommandInfo:
    name: str
    help: str | None = None


@dataclass
class ToolInfo:
    name: str
    module: str
    path: str
    category: str
    summary: str
    docstring: str | None
    cli_description: str | None
    has_cli: bool
    commands: list[CommandInfo] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    reference_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["commands"] = [asdict(command) for command in self.commands]
        return data

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ToolInfo":
        return cls(
            name=str(payload["name"]),
            module=str(payload["module"]),
            path=str(payload["path"]),
            category=str(payload["category"]),
            summary=str(payload["summary"]),
            docstring=payload.get("docstring"),
            cli_description=payload.get("cli_description"),
            has_cli=bool(payload.get("has_cli")),
            commands=[CommandInfo(**command) for command in payload.get("commands", [])],
            functions=list(payload.get("functions", [])),
            classes=list(payload.get("classes", [])),
            reference_count=int(payload.get("reference_count", 0)),
        )


@dataclass
class ScanResult:
    scanned_at: str
    repo_root: str
    tools: list[ToolInfo]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned_at": self.scanned_at,
            "repo_root": self.repo_root,
            "tools": [tool.to_dict() for tool in self.tools],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ScanResult":
        return cls(
            scanned_at=str(payload["scanned_at"]),
            repo_root=str(payload["repo_root"]),
            tools=[ToolInfo.from_dict(item) for item in payload.get("tools", [])],
        )


class ArgparseMetadata:
    def __init__(self, description: str | None, commands: list[CommandInfo], has_cli: bool) -> None:
        self.description = description
        self.commands = commands
        self.has_cli = has_cli


class _ArgparseVisitor(ast.NodeVisitor):
    def __init__(self, module_docstring: str | None) -> None:
        self.module_docstring = module_docstring
        self.parser_description: str | None = None
        self.subparser_names: set[str] = set()
        self.commands: dict[str, CommandInfo] = {}
        self._loop_values: dict[str, list[str]] = {}
        self.has_cli = False

    def visit_Assign(self, node: ast.Assign) -> None:
        self._capture_assignment(node.value, node.targets)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._capture_assignment(node.value, [node.target])
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        if isinstance(node.target, ast.Name):
            names = self._resolve_string_sequence(node.iter)
            if names:
                previous = self._loop_values.get(node.target.id)
                self._loop_values[node.target.id] = names
                for statement in node.body:
                    self.visit(statement)
                if previous is None:
                    self._loop_values.pop(node.target.id, None)
                else:
                    self._loop_values[node.target.id] = previous
                for statement in node.orelse:
                    self.visit(statement)
                return
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self._is_argument_parser(node):
            self.has_cli = True
            if self.parser_description is None:
                description = self._resolve_keyword_string(node, "description")
                self.parser_description = description or self.module_docstring
        elif self._is_add_parser(node):
            self.has_cli = True
            receiver = node.func.value
            if isinstance(receiver, ast.Name) and receiver.id in self.subparser_names and node.args:
                help_text = self._resolve_keyword_string(node, "help")
                for command_name in self._resolve_command_names(node.args[0]):
                    self.commands.setdefault(command_name, CommandInfo(name=command_name, help=help_text))
        self.generic_visit(node)

    def _capture_assignment(self, value: ast.AST, targets: list[ast.expr]) -> None:
        if isinstance(value, ast.Call) and self._is_add_subparsers(value):
            self.has_cli = True
            for target in targets:
                if isinstance(target, ast.Name):
                    self.subparser_names.add(target.id)

    def _is_argument_parser(self, node: ast.Call) -> bool:
        func = node.func
        return isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "argparse" and func.attr == "ArgumentParser"

    def _is_add_subparsers(self, node: ast.Call) -> bool:
        func = node.func
        return isinstance(func, ast.Attribute) and func.attr == "add_subparsers"

    def _is_add_parser(self, node: ast.Call) -> bool:
        func = node.func
        return isinstance(func, ast.Attribute) and func.attr == "add_parser"

    def _resolve_command_names(self, node: ast.AST) -> list[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node.value]
        if isinstance(node, ast.Name):
            return list(self._loop_values.get(node.id, []))
        return []

    def _resolve_keyword_string(self, node: ast.Call, name: str) -> str | None:
        for keyword in node.keywords:
            if keyword.arg == name:
                return self._resolve_string(keyword.value)
        return None

    def _resolve_string(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and node.id == "__doc__":
            return self.module_docstring
        return None

    def _resolve_string_sequence(self, node: ast.AST) -> list[str]:
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            values: list[str] = []
            for element in node.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    values.append(element.value)
                else:
                    return []
            return values
        return []


def _discover_argparse_metadata(module_ast: ast.Module) -> ArgparseMetadata:
    module_docstring = ast.get_docstring(module_ast)
    visitor = _ArgparseVisitor(module_docstring)
    visitor.visit(module_ast)
    commands = sorted(visitor.commands.values(), key=lambda item: item.name)
    return ArgparseMetadata(visitor.parser_description, commands, visitor.has_cli)


def _module_name_from_path(path: Path, root: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _iter_python_files(root: Path) -> Iterable[Path]:
    for scan_root in SCAN_ROOTS:
        base = root / scan_root
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if any(part in SKIP_DIRECTORIES for part in path.parts):
                continue
            if path.name == "__init__.py":
                continue
            yield path


def _summarize(docstring: str | None, cli_description: str | None) -> str:
    summary = cli_description or docstring or "No summary available."
    first_line = next((line.strip() for line in summary.splitlines() if line.strip()), "No summary available.")
    return first_line


def _read_text_files(root: Path) -> Iterable[tuple[Path, str]]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRECTORIES for part in path.parts):
            continue
        if path.suffix.lower() not in TEXT_FILE_SUFFIXES:
            continue
        try:
            yield path, path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue


def _count_references(root: Path, tools: list[ToolInfo]) -> None:
    patterns: dict[str, tuple[str, str, str]] = {}
    for tool in tools:
        patterns[tool.name] = (
            tool.module,
            f"python -m {tool.module}",
            Path(tool.path).stem,
        )

    counts = {tool.name: 0 for tool in tools}
    for path, contents in _read_text_files(root):
        relative = path.relative_to(root).as_posix()
        for tool in tools:
            if relative == tool.path:
                continue
            module_pattern, cli_pattern, short_name = patterns[tool.name]
            if module_pattern in contents or cli_pattern in contents or short_name in contents:
                counts[tool.name] += 1

    for tool in tools:
        tool.reference_count = counts[tool.name]


def _parse_tool_file(path: Path, root: Path) -> ToolInfo | None:
    source = path.read_text(encoding="utf-8")
    try:
        module_ast = ast.parse(source, filename=str(path))
    except SyntaxError:
        return None

    module = _module_name_from_path(path, root)
    functions = sorted(
        node.name
        for node in module_ast.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_")
    )
    classes = sorted(
        node.name
        for node in module_ast.body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    )
    argparse_metadata = _discover_argparse_metadata(module_ast)
    if not argparse_metadata.has_cli and not functions and not classes:
        return None

    docstring = ast.get_docstring(module_ast)
    category = path.relative_to(root).parts[0]
    tool_name = module
    return ToolInfo(
        name=tool_name,
        module=module,
        path=path.relative_to(root).as_posix(),
        category=category,
        summary=_summarize(docstring, argparse_metadata.description),
        docstring=docstring,
        cli_description=argparse_metadata.description,
        has_cli=argparse_metadata.has_cli,
        commands=argparse_metadata.commands,
        functions=functions,
        classes=classes,
    )


def _build_alias_map(tools: list[ToolInfo]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    short_counts = Counter(tool.module.split(".")[-1] for tool in tools)
    for tool in tools:
        aliases[tool.name] = tool.name
        aliases[tool.module] = tool.name
        short_name = tool.module.split(".")[-1]
        if short_counts[short_name] == 1:
            aliases[short_name] = tool.name
    return aliases


def load_scan_cache(root: Path | None = None) -> ScanResult | None:
    payload = _read_json(data_root(root) / "tool_index.json", default=None)
    if not payload:
        return None
    return ScanResult.from_dict(payload)


def save_scan_cache(scan_result: ScanResult, root: Path | None = None) -> None:
    _ensure_storage(root)
    _write_json(data_root(root) / "tool_index.json", scan_result.to_dict())


def scan_openclaw_tools(root: Path | None = None) -> list[ToolInfo]:
    """Discover OpenClaw tools and persist the latest inventory."""

    root = (root or repo_root()).resolve()
    tools = [tool for tool in (_parse_tool_file(path, root) for path in _iter_python_files(root)) if tool]
    discovered = sorted(tools, key=lambda item: item.name)
    _count_references(root, discovered)
    scan_result = ScanResult(scanned_at=_utc_now(), repo_root=str(root), tools=discovered)
    save_scan_cache(scan_result, root)
    return discovered


def _tool_doc_path(tool: ToolInfo, root: Path) -> Path:
    return data_root(root) / "docs" / f"{tool.module}.md"


def _build_markdown(tool: ToolInfo) -> str:
    lines = [
        f"# {tool.name}",
        "",
        f"- Path: `{tool.path}`",
        f"- Category: `{tool.category}`",
        f"- Module: `{tool.module}`",
        f"- Summary: {tool.summary}",
        f"- Has CLI: {'yes' if tool.has_cli else 'no'}",
        f"- Static reference count: {tool.reference_count}",
    ]
    if tool.has_cli:
        lines.append(f"- Invocation: `python -m {tool.module}`")
    lines.append("")

    if tool.docstring:
        lines.extend(["## Overview", "", tool.docstring.strip(), ""])

    if tool.cli_description:
        lines.extend(["## CLI description", "", tool.cli_description.strip(), ""])

    if tool.commands:
        lines.extend(["## Commands", ""])
        for command in tool.commands:
            detail = f" - {command.help}" if command.help else ""
            lines.append(f"- `{command.name}`{detail}")
        lines.append("")
    elif tool.has_cli:
        lines.extend(["## Commands", "", "- This CLI does not define subcommands.", ""])

    if tool.functions:
        lines.extend(["## Public functions", ""])
        lines.extend(f"- `{function_name}`" for function_name in tool.functions)
        lines.append("")

    if tool.classes:
        lines.extend(["## Public classes", ""])
        lines.extend(f"- `{class_name}`" for class_name in tool.classes)
        lines.append("")

    lines.extend(
        [
            "## Discovery notes",
            "",
            "- Generated via static AST inspection.",
            "- Command detection is based on argparse `add_parser(...)` calls.",
            "- Reference counts are heuristic matches across repository text files.",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def generate_tool_docs(tools: Sequence[ToolInfo] | None = None, *, root: Path | None = None) -> dict[str, Path]:
    """Generate markdown documentation for each discovered tool."""

    root = (root or repo_root()).resolve()
    tools = list(tools) if tools is not None else scan_openclaw_tools(root)
    storage = _ensure_storage(root)
    docs_root = storage / "docs"
    written: dict[str, Path] = {}

    for tool in tools:
        doc_path = _tool_doc_path(tool, root)
        doc_path.write_text(_build_markdown(tool), encoding="utf-8")
        written[tool.name] = doc_path

    index_lines = [
        "# OpenClaw Tool Discovery Index",
        "",
        f"Generated at: `{_utc_now()}`",
        "",
    ]
    for tool in tools:
        index_lines.append(f"- [`{tool.name}`](docs/{tool.module}.md) - {tool.summary}")
    index_lines.append("")
    (storage / "index.md").write_text("\n".join(index_lines), encoding="utf-8")
    return written


class ToolUsageTracker:
    """Persist discovery usage and generate underused-tool suggestions."""

    def __init__(self, *, root: Path | None = None) -> None:
        self.root = (root or repo_root()).resolve()
        self.storage = _ensure_storage(self.root)
        self.path = self.storage / "usage.json"

    def _load(self) -> dict[str, Any]:
        return _read_json(
            self.path,
            default={
                "updated_at": None,
                "commands": {},
                "tools": {},
                "history": [],
            },
        )

    def _save(self, payload: dict[str, Any]) -> None:
        payload["updated_at"] = _utc_now()
        history = payload.get("history", [])
        payload["history"] = history[-100:]
        _write_json(self.path, payload)

    def record_command(self, command: str, *, tool_name: str | None = None) -> None:
        payload = self._load()
        payload["commands"][command] = int(payload["commands"].get(command, 0)) + 1
        event: dict[str, Any] = {"command": command, "timestamp": _utc_now()}
        if tool_name:
            tool_state = payload["tools"].setdefault(tool_name, {"views": 0, "last_viewed_at": None})
            tool_state["views"] = int(tool_state.get("views", 0)) + 1
            tool_state["last_viewed_at"] = event["timestamp"]
            event["tool_name"] = tool_name
        payload.setdefault("history", []).append(event)
        self._save(payload)

    def snapshot(self) -> dict[str, Any]:
        return self._load()

    def most_used_tools(self, *, limit: int = 5) -> list[dict[str, Any]]:
        payload = self._load()
        tool_state = payload.get("tools", {})
        ranked = sorted(
            (
                {
                    "tool_name": tool_name,
                    "views": int(details.get("views", 0)),
                    "last_viewed_at": details.get("last_viewed_at"),
                }
                for tool_name, details in tool_state.items()
            ),
            key=lambda item: (-item["views"], item["tool_name"]),
        )
        return ranked[:limit]

    def recommend_underused_tools(self, tools: Sequence[ToolInfo], *, limit: int = 5) -> list[dict[str, Any]]:
        payload = self._load()
        tool_state = payload.get("tools", {})
        scored: list[dict[str, Any]] = []
        for tool in tools:
            views = int(tool_state.get(tool.name, {}).get("views", 0))
            command_weight = max(len(tool.commands), 1) if tool.has_cli else 0
            function_weight = min(len(tool.functions), 8)
            capability = command_weight * 2 + function_weight + min(tool.reference_count, 5)
            if capability <= 0:
                continue
            underuse_score = capability / (views + 1)
            scored.append(
                {
                    "tool_name": tool.name,
                    "views": views,
                    "underuse_score": round(underuse_score, 2),
                    "reason": (
                        f"{views} tracked views, {len(tool.commands)} commands, "
                        f"{len(tool.functions)} public functions, {tool.reference_count} static references"
                    ),
                }
            )
        scored.sort(key=lambda item: (-item["underuse_score"], item["tool_name"]))
        return scored[:limit]


tool_usage_tracker = ToolUsageTracker()


def _resolve_tool_name(name: str, tools: Sequence[ToolInfo]) -> ToolInfo:
    aliases = _build_alias_map(list(tools))
    canonical_name = aliases.get(name)
    if canonical_name is None:
        raise KeyError(name)
    for tool in tools:
        if tool.name == canonical_name:
            return tool
    raise KeyError(name)


def _render_table(headers: Sequence[str], rows: Sequence[Sequence[str]], *, color: bool) -> str:
    widths = [len(header) for header in headers]
    normalized_rows = [[str(cell) for cell in row] for row in rows]
    for row in normalized_rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def divider() -> str:
        return "+" + "+".join("-" * (width + 2) for width in widths) + "+"

    def render_row(cells: Sequence[str], *, header: bool = False) -> str:
        pieces: list[str] = []
        for index, cell in enumerate(cells):
            value = colorize(cell, "bold", enabled=color) if header else cell
            pieces.append(f" {value}{' ' * (widths[index] - len(cell))} ")
        return "|" + "|".join(pieces) + "|"

    lines = [divider(), render_row(headers, header=True), divider()]
    lines.extend(render_row(row) for row in normalized_rows)
    lines.append(divider())
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover OpenClaw tools and generate documentation.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="List all discovered tools.")
    scan_parser.add_argument("--no-docs", action="store_true", help="Skip markdown generation during the scan.")

    docs_parser = subparsers.add_parser("docs", help="Show generated docs for a tool.")
    docs_parser.add_argument("tool_name", help="Canonical module name or unique short name.")

    suggest_parser = subparsers.add_parser("suggest", help="Recommend underused tools.")
    suggest_parser.add_argument("--limit", type=int, default=5, help="Maximum number of suggestions to show.")

    stats_parser = subparsers.add_parser("stats", help="Show discovery usage statistics.")
    stats_parser.add_argument("--limit", type=int, default=5, help="Maximum number of tools to display.")

    return parser


def _scan_command(*, root: Path, no_docs: bool, color: bool) -> int:
    tools = scan_openclaw_tools(root)
    if not no_docs:
        generate_tool_docs(tools, root=root)
    tracker = ToolUsageTracker(root=root)
    tracker.record_command("scan")

    print(colorize("OpenClaw tool discovery", "cyan", enabled=color))
    print(colorize(f"Discovered {len(tools)} tools", "green", enabled=color))
    print(colorize(f"Artifacts: {data_root(root)}", "blue", enabled=color))
    print()

    rows = [
        [
            tool.name,
            "cli" if tool.has_cli else "module",
            str(len(tool.commands)),
            str(len(tool.functions)),
            str(tool.reference_count),
            _truncate(tool.summary, 46),
        ]
        for tool in tools
    ]
    print(_render_table(("Tool", "Type", "Cmds", "Fns", "Refs", "Summary"), rows, color=color))
    return 0


def _docs_command(*, root: Path, tool_name: str, color: bool) -> int:
    tools = scan_openclaw_tools(root)
    generate_tool_docs(tools, root=root)
    try:
        tool = _resolve_tool_name(tool_name, tools)
    except KeyError:
        print(colorize(f"Unknown tool '{tool_name}'.", "red", enabled=color), file=sys.stderr)
        return 1

    tracker = ToolUsageTracker(root=root)
    tracker.record_command("docs", tool_name=tool.name)

    doc_path = _tool_doc_path(tool, root)
    print(colorize(f"Documentation for {tool.name}", "cyan", enabled=color))
    print(colorize(str(doc_path), "blue", enabled=color))
    print()
    print(doc_path.read_text(encoding="utf-8").rstrip())
    return 0


def _suggest_command(*, root: Path, limit: int, color: bool) -> int:
    tools = scan_openclaw_tools(root)
    generate_tool_docs(tools, root=root)
    tracker = ToolUsageTracker(root=root)
    tracker.record_command("suggest")
    suggestions = tracker.recommend_underused_tools(tools, limit=limit)

    print(colorize("Recommended underused tools", "cyan", enabled=color))
    if not suggestions:
        print(colorize("No suggestions available yet.", "yellow", enabled=color))
        return 0

    rows = [
        [
            suggestion["tool_name"],
            str(suggestion["views"]),
            f"{suggestion['underuse_score']:.2f}",
            _truncate(suggestion["reason"], 60),
        ]
        for suggestion in suggestions
    ]
    print(_render_table(("Tool", "Views", "Score", "Reason"), rows, color=color))
    return 0


def _stats_command(*, root: Path, limit: int, color: bool) -> int:
    tools = scan_openclaw_tools(root)
    tracker = ToolUsageTracker(root=root)
    tracker.record_command("stats")
    snapshot = tracker.snapshot()
    most_used = tracker.most_used_tools(limit=limit)

    print(colorize("Tool discovery usage statistics", "cyan", enabled=color))
    print(colorize(f"Tracked tools: {len(tools)}", "green", enabled=color))
    print(colorize(f"Updated: {snapshot.get('updated_at') or 'never'}", "blue", enabled=color))
    print()

    command_rows = [
        [name, str(count)]
        for name, count in sorted(snapshot.get("commands", {}).items(), key=lambda item: (-int(item[1]), item[0]))
    ]
    if command_rows:
        print(colorize("CLI command usage", "magenta", enabled=color))
        print(_render_table(("Command", "Count"), command_rows, color=color))
        print()

    if most_used:
        print(colorize("Most viewed tool docs", "magenta", enabled=color))
        tool_rows = [
            [item["tool_name"], str(item["views"]), item["last_viewed_at"] or "-"]
            for item in most_used
        ]
        print(_render_table(("Tool", "Views", "Last viewed"), tool_rows, color=color))
    else:
        print(colorize("No tool-specific usage recorded yet.", "yellow", enabled=color))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    root = repo_root()
    color = use_color()

    if args.command == "scan":
        return _scan_command(root=root, no_docs=args.no_docs, color=color)
    if args.command == "docs":
        return _docs_command(root=root, tool_name=args.tool_name, color=color)
    if args.command == "suggest":
        return _suggest_command(root=root, limit=args.limit, color=color)
    if args.command == "stats":
        return _stats_command(root=root, limit=args.limit, color=color)
    parser.error(f"Unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
