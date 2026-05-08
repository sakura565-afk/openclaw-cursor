#!/usr/bin/env python3
"""Discover Python tools across the workspace: capabilities, I/O, safety, and suggestions."""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


KEYWORD_CAPABILITIES: tuple[tuple[str, str], ...] = (
    ("monitor", "Monitoring and observability"),
    ("analytics", "Analytics and reporting"),
    ("cleanup", "Cleanup and maintenance"),
    ("sync", "Data synchronization"),
    ("queue", "Queue orchestration"),
    ("benchmark", "Performance benchmarking"),
    ("model", "Model lifecycle management"),
    ("telegram", "Messaging and notifications"),
    ("media", "Media processing"),
    ("context", "Context shaping and prompt preparation"),
    ("dream", "Memory and ideation workflows"),
    ("orchestration", "Task orchestration"),
    ("dedup", "Deduplication and similarity"),
    ("thumbnail", "Media processing"),
    ("video", "Media processing"),
    ("image", "Media processing"),
    ("ollama", "Model lifecycle management"),
    ("obsidian", "Data synchronization"),
    ("health", "Monitoring and observability"),
    ("dashboard", "Analytics and reporting"),
    ("memory", "Memory and ideation workflows"),
    ("pipeline", "Task orchestration"),
    ("batch", "Task orchestration"),
    ("extract", "General utility automation"),
    ("photo", "Media processing"),
    ("face", "Media processing"),
    ("error", "General utility automation"),
    ("skill", "Task orchestration"),
    ("coordination", "Data synchronization"),
    ("session", "Monitoring and observability"),
    ("idea", "Memory and ideation workflows"),
    ("self_improvement", "General utility automation"),
)

HIGH_RISK_MARKERS = frozenset({"subprocess", "os", "shutil", "requests", "socket"})
IO_MARKERS = frozenset({"pathlib", "open", "json", "csv", "sqlite3", "tempfile", "gzip", "zipfile"})
NETWORK_MARKERS = frozenset({"requests", "urllib", "http", "socket", "aiohttp", "httpx", "websocket"})
DB_MARKERS = frozenset({"sqlite3", "sqlalchemy", "psycopg2"})
CODE_EXEC_MARKERS = frozenset({"eval", "exec", "compile"})

SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        "node_modules",
        ".tox",
        "dist",
        "build",
        ".eggs",
        ".ruff_cache",
    }
)

def _display_name(relative: Path) -> str:
    parts = relative.parts
    if len(parts) >= 2 and parts[0] == "scripts":
        if len(parts) == 2:
            return relative.stem
        return relative.with_suffix("").as_posix().replace("/", ".")
    return relative.with_suffix("").as_posix().replace("/", ".")


def _tool_id(relative: Path) -> str:
    return relative.as_posix()


def _decorator_label(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _decorator_label(node.value)
        return f"{inner}.{node.attr}" if inner else node.attr
    if isinstance(node, ast.Call):
        return _decorator_label(node.func)
    if isinstance(node, ast.Subscript):
        return _decorator_label(node.value)
    return ""


def _annotation_str(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except AttributeError:
        return None


@dataclass
class FunctionToolInfo:
    """Structured view of a public function (potential tool entrypoint)."""

    name: str
    lineno: int
    is_async: bool
    parameters: list[str]
    vararg: str | None
    kwarg: str | None
    returns: str | None
    decorators: list[str]
    docstring_summary: str | None
    has_docstring: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "lineno": self.lineno,
            "is_async": self.is_async,
            "parameters": self.parameters,
            "vararg": self.vararg,
            "kwarg": self.kwarg,
            "returns": self.returns,
            "decorators": self.decorators,
            "docstring_summary": self.docstring_summary,
            "has_docstring": self.has_docstring,
        }


@dataclass
class ToolProfile:
    """Registry entry for one Python module."""

    name: str
    path: Path
    description: str
    tool_id: str = ""
    imports: set[str] = field(default_factory=set)
    functions: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    risk_level: str = "low"
    io_profile: list[str] = field(default_factory=list)
    safety_constraints: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    function_details: list[FunctionToolInfo] = field(default_factory=list)
    decorator_signals: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.tool_id:
            self.tool_id = _tool_id(self.path) if isinstance(self.path, Path) else str(self.path)

    def to_dict(self) -> dict[str, object]:
        return {
            "tool_id": self.tool_id,
            "name": self.name,
            "path": str(self.path),
            "description": self.description,
            "imports": sorted(self.imports),
            "functions": self.functions,
            "function_details": [f.to_dict() for f in self.function_details],
            "commands": self.commands,
            "capabilities": self.capabilities,
            "risk_level": self.risk_level,
            "io_profile": self.io_profile,
            "safety_constraints": self.safety_constraints,
            "decorator_signals": self.decorator_signals,
            "dependencies": self.dependencies,
            "examples": self.examples,
        }


@dataclass
class ToolRegistry:
    """Collection of discovered tools with lookup helpers."""

    root: Path
    tools: list[ToolProfile] = field(default_factory=list)

    def by_name(self) -> dict[str, ToolProfile]:
        return {t.name: t for t in self.tools}

    def by_id(self) -> dict[str, ToolProfile]:
        return {t.tool_id: t for t in self.tools}

    def to_dict(self) -> dict[str, object]:
        return {
            "root": str(self.root),
            "tool_count": len(self.tools),
            "tools": [t.to_dict() for t in self.tools],
        }


def discover_python_files(root: Path) -> list[Path]:
    """All ``.py`` files under root, excluding common cache/vendor trees."""
    root = root.resolve()
    found: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if rel.name == "__init__.py":
            continue
        found.append(rel)
    return [root / rel for rel in found]


def _literal_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def extract_cli_commands(tree: ast.AST) -> list[str]:
    commands: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr == "add_parser" and node.args:
            maybe_command = _literal_string(node.args[0])
            if maybe_command:
                commands.add(maybe_command)
    return sorted(commands)


def extract_imports(tree: ast.AST) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    return imports


def extract_decorator_signals(tree: ast.AST) -> list[str]:
    signals: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                label = _decorator_label(dec)
                if not label:
                    continue
                low = label.lower()
                if "click" in low or low.endswith(".command") or low.endswith(".group"):
                    signals.add("click_cli")
                if "typer" in low:
                    signals.add("typer_cli")
                if "pytest.fixture" in low or low == "fixture":
                    signals.add("pytest")
                if "app.route" in low or low.endswith(".route"):
                    signals.add("http_route")
                if "task" in low and "celery" in low:
                    signals.add("celery_task")
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in ("command", "option", "argument"):
                if isinstance(func.value, ast.Name) and func.value.id in ("click", "typer"):
                    signals.add("click_typer_api")
    return sorted(signals)


def extract_public_functions(tree: ast.AST) -> list[FunctionToolInfo]:
    details: list[FunctionToolInfo] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_"):
            continue
        decorators = [_decorator_label(d) for d in node.decorator_list]
        decorators = [d for d in decorators if d]
        args = node.args
        params: list[str] = []
        for arg in list(args.posonlyargs) + list(args.args):
            if arg.arg in ("self", "cls"):
                continue
            ann = _annotation_str(arg.annotation)
            params.append(f"{arg.arg}: {ann}" if ann else arg.arg)
        vararg = args.vararg.arg if args.vararg else None
        kwarg = args.kwarg.arg if args.kwarg else None
        raw_doc = ast.get_docstring(node)
        summary = None
        has_doc = bool(raw_doc)
        if raw_doc:
            summary = raw_doc.strip().splitlines()[0].strip()
        returns = _annotation_str(node.returns)
        details.append(
            FunctionToolInfo(
                name=node.name,
                lineno=getattr(node, "lineno", 0) or 0,
                is_async=isinstance(node, ast.AsyncFunctionDef),
                parameters=params,
                vararg=vararg,
                kwarg=kwarg,
                returns=returns,
                decorators=decorators,
                docstring_summary=summary,
                has_docstring=has_doc,
            )
        )
    details.sort(key=lambda f: (f.lineno, f.name))
    return details


def extract_functions(tree: ast.AST) -> list[str]:
    return sorted({f.name for f in extract_public_functions(tree)})


def extract_description(tree: ast.AST) -> str:
    doc = ast.get_docstring(tree)
    if doc:
        return doc.strip().splitlines()[0]
    return "No module docstring available."


def _capability_from_signature(fn: FunctionToolInfo) -> list[str]:
    extra: list[str] = []
    blob = " ".join(
        [
            fn.name,
            *(fn.parameters or []),
            fn.returns or "",
            *(fn.decorators or []),
            fn.docstring_summary or "",
        ]
    ).lower()
    if fn.is_async:
        extra.append("Async I/O and concurrency")
    if any("path" in p.lower() or "Path" in p for p in fn.parameters):
        extra.append("Filesystem-oriented API surface")
    if any(n in blob for n in ("request", "http", "url", "socket")):
        extra.append("Network-oriented API surface")
    if "callback" in blob or "handler" in blob:
        extra.append("Event or callback handling")
    return extra


def infer_capabilities(
    name: str,
    description: str,
    commands: list[str],
    functions: list[str],
    function_details: list[FunctionToolInfo],
    decorator_signals: list[str],
) -> list[str]:
    corpus = " ".join([name, description, *commands, *functions]).lower()
    capabilities = [label for marker, label in KEYWORD_CAPABILITIES if marker in corpus]
    for fn in function_details:
        capabilities.extend(_capability_from_signature(fn))
    if "click_cli" in decorator_signals or "typer_cli" in decorator_signals:
        capabilities.append("CLI workflow automation")
    if "http_route" in decorator_signals:
        capabilities.append("HTTP service surface")
    if "pytest" in decorator_signals:
        capabilities.append("Test and verification harness")
    if "argparse" in corpus and "CLI workflow automation" not in capabilities:
        capabilities.append("CLI workflow automation")
    if not capabilities:
        capabilities.append("General utility automation")
    return sorted(set(capabilities))


def infer_safety_constraints(imports: set[str], source: str, tree: ast.AST) -> list[str]:
    constraints: set[str] = set()
    if imports.intersection(NETWORK_MARKERS) or re.search(r"\b(https?://|urllib\.request)\b", source):
        constraints.add("network_egress")
    if "subprocess" in imports or "subprocess." in source or "os.system" in source:
        constraints.add("subprocess_execution")
    if imports.intersection({"shutil"}) and re.search(r"\b(remove|unlink|rmtree|move)\b", source):
        constraints.add("filesystem_destructive")
    if re.search(r"\bopen\s*\([^)]*[\"']w", source) or re.search(r"\bopen\s*\([^)]*[\"']a", source):
        constraints.add("filesystem_write")
    elif imports.intersection(IO_MARKERS) and ("Path(" in source or "open(" in source):
        constraints.add("filesystem_read")
    if imports.intersection(DB_MARKERS):
        constraints.add("database_access")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in CODE_EXEC_MARKERS:
                constraints.add("dynamic_code_execution")
    if not constraints:
        constraints.add("pure_or_unknown_side_effects")
    return sorted(constraints)


def infer_risk_level(imports: set[str], commands: list[str], safety: list[str]) -> str:
    import_hits = len(imports.intersection(HIGH_RISK_MARKERS))
    command_hits = len(commands)
    elevated_safety = bool(
        {"subprocess_execution", "network_egress", "dynamic_code_execution"}.intersection(safety)
    )
    if import_hits >= 2 or command_hits >= 7:
        return "high"
    if import_hits >= 1 or command_hits >= 4 or elevated_safety:
        return "medium"
    return "low"


def infer_io_profile(imports: set[str], text: str, safety: list[str]) -> list[str]:
    profile: list[str] = []
    if "filesystem_read" in safety or "filesystem_write" in safety or "filesystem_destructive" in safety:
        profile.append("filesystem")
    elif imports.intersection(IO_MARKERS) or "Path(" in text or "open(" in text:
        profile.append("filesystem")
    if "network_egress" in safety or imports.intersection(NETWORK_MARKERS):
        profile.append("network")
    if "subprocess_execution" in safety or "subprocess" in imports or "subprocess." in text:
        profile.append("process")
    if "json" in imports:
        profile.append("structured-data")
    if "database_access" in safety:
        profile.append("database")
    return profile or ["in-memory"]


def analyze_file(root: Path, path: Path) -> ToolProfile | None:
    rel = path.relative_to(root)
    try:
        source = path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return ToolProfile(
            name=_display_name(rel),
            path=rel,
            tool_id=_tool_id(rel),
            description="Syntax error: module could not be parsed.",
            risk_level="unknown",
            io_profile=["unknown"],
            safety_constraints=["unparsed_module"],
        )

    name = _display_name(rel)
    tool_id = _tool_id(rel)
    imports = extract_imports(tree)
    function_details = extract_public_functions(tree)
    functions = sorted({f.name for f in function_details})
    commands = extract_cli_commands(tree)
    description = extract_description(tree)
    decorator_signals = extract_decorator_signals(tree)
    safety = infer_safety_constraints(imports, source, tree)
    capabilities = infer_capabilities(
        name, description, commands, functions, function_details, decorator_signals
    )
    io_profile = infer_io_profile(imports, source, safety)
    risk_level = infer_risk_level(imports, commands, safety)
    profile = ToolProfile(
        name=name,
        path=rel,
        tool_id=tool_id,
        description=description,
        imports=imports,
        functions=functions,
        commands=commands,
        capabilities=capabilities,
        risk_level=risk_level,
        io_profile=io_profile,
        safety_constraints=safety,
        function_details=function_details,
        decorator_signals=decorator_signals,
    )
    return profile


def analyze_scripts(root: Path) -> list[ToolProfile]:
    """Discover tools under ``root`` and return a flat list of profiles (backward compatible)."""
    return build_registry(root).tools


def build_registry(root: Path) -> ToolRegistry:
    root = root.resolve()
    tools: list[ToolProfile] = []
    for path in discover_python_files(root):
        profile = analyze_file(root, path)
        if profile:
            tools.append(profile)
    tools.sort(key=lambda p: str(p.path))
    enrich_dependency_graph(tools)
    for profile in tools:
        profile.examples = build_examples(root, profile)
    return ToolRegistry(root=root, tools=tools)


def enrich_dependency_graph(profiles: list[ToolProfile]) -> None:
    by_name = {profile.name: profile for profile in profiles}
    for profile in profiles:
        deps: set[str] = set()
        for imported in profile.imports:
            if imported in by_name and imported != profile.name:
                deps.add(imported)
        for peer in profiles:
            if peer.name == profile.name:
                continue
            shared_imports = profile.imports.intersection(peer.imports)
            shared_caps = set(profile.capabilities).intersection(peer.capabilities)
            if len(shared_imports) >= 2 or len(shared_caps) >= 2:
                deps.add(peer.name)
        profile.dependencies = sorted(deps)


def build_examples(root: Path, profile: ToolProfile) -> list[str]:
    rel = profile.path
    examples: list[str] = []
    if isinstance(rel, Path) and len(rel.parts) >= 1 and rel.parts[0] == "scripts" and len(rel.parts) == 2:
        base = f"python -m scripts.{rel.stem}"
    else:
        mod = rel.with_suffix("").as_posix().replace("/", ".")
        base = f"python -m {mod}"
    if profile.commands:
        for command in profile.commands[:3]:
            examples.append(f"{base} {command}")
    else:
        examples.append(base)
    if "network" in profile.io_profile:
        examples.append(f"# Network-aware run\n{base} --help")
    if "filesystem" in profile.io_profile:
        examples.append(f"# Filesystem workflow\n{base} --help")
    return examples


def generate_markdown(profiles: list[ToolProfile], *, root: Path | None = None) -> str:
    root_note = f"Repository root: `{root}`" if root else ""
    lines = [
        "# Tool Discovery Report",
        "",
        "Auto-generated capability, I/O, safety, and dependency analysis for Python modules in the workspace.",
        "",
        root_note,
        "",
        "## Summary",
        "",
        f"- Total tools discovered: **{len(profiles)}**",
        f"- High-risk tools: **{sum(1 for p in profiles if p.risk_level == 'high')}**",
        f"- Medium-risk tools: **{sum(1 for p in profiles if p.risk_level == 'medium')}**",
        "",
        "## Registry overview",
        "",
        "| Name | Path | Risk | I/O | Safety constraints |",
        "| --- | --- | --- | --- | --- |",
    ]
    for p in profiles:
        io = ", ".join(p.io_profile)
        safety = ", ".join(p.safety_constraints) if p.safety_constraints else "—"
        lines.append(f"| `{p.name}` | `{p.path}` | {p.risk_level} | {io} | {safety} |")
    lines.extend(["", "---", ""])

    for profile in profiles:
        lines.extend(
            [
                f"## `{profile.name}`",
                "",
                f"- **tool_id**: `{profile.tool_id}`",
                f"- **Path**: `{profile.path}`",
                f"- **Description**: {profile.description}",
                f"- **Risk level**: **{profile.risk_level}**",
                f"- **Capabilities**: {', '.join(profile.capabilities)}",
                f"- **I/O profile**: {', '.join(profile.io_profile)}",
                f"- **Safety constraints**: {', '.join(profile.safety_constraints)}",
                f"- **Decorator signals**: {', '.join(profile.decorator_signals) or '—'}",
                f"- **Dependencies**: {', '.join(profile.dependencies) if profile.dependencies else 'none'}",
                "",
            ]
        )
        if profile.function_details:
            lines.extend(
                [
                    "### Public functions (signatures & docstrings)",
                    "",
                    "| Function | Parameters | Returns | Decorators | Docstring |",
                    "| --- | --- | --- | --- | --- |",
                ]
            )
            for fn in profile.function_details[:12]:
                params = ", ".join(fn.parameters[:6])
                if len(fn.parameters) > 6:
                    params += ", …"
                doc = (fn.docstring_summary or "—").replace("|", "\\|")
                ret = fn.returns or "—"
                async_badge = "async " if fn.is_async else ""
                dec = ", ".join(f"`{d}`" for d in fn.decorators[:3]) if fn.decorators else "—"
                lines.append(f"| `{async_badge}{fn.name}` | {params} | `{ret}` | {dec} | {doc} |")
            if len(profile.function_details) > 12:
                lines.append(f"| … | _{len(profile.function_details) - 12} more_ | | | |")
            lines.append("")
        lines.extend(
            [
                "### CLI subcommands",
                "",
            ]
        )
        if profile.commands:
            lines.extend(f"- `{cmd}`" for cmd in profile.commands)
        else:
            lines.append("- _No argparse subcommands discovered_")
        lines.extend(["", "### Example usage", ""])
        lines.extend(f"```bash\n{example}\n```" for example in profile.examples)
        lines.append("")
    return "\n".join(lines)


def _tokenize(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9_]+", text.lower()) if len(w) > 2]


def suggest(
    goal: str,
    profiles: Iterable[ToolProfile],
    *,
    context: str = "",
    top_n: int = 5,
) -> list[dict[str, object]]:
    """
    Score each tool for a natural-language goal using capabilities, signatures,
    docstrings, I/O profile, safety constraints, and CLI commands.
    """
    profiles = list(profiles)
    goal_toks = set(_tokenize(goal))
    ctx_toks = set(_tokenize(context))
    combined = goal_toks | ctx_toks
    ranked: list[tuple[int, ToolProfile, list[str]]] = []

    for profile in profiles:
        score = 0
        reasons: list[str] = []

        for capability in profile.capabilities:
            cap_words = [w for w in _tokenize(capability) if len(w) > 3]
            hits = goal_toks.intersection(cap_words)
            if hits:
                score += 3 + len(hits)
                reasons.append(f"Capability match ({capability}): tokens {sorted(hits)}")

        for command in profile.commands:
            if command in goal.lower() or command in context.lower():
                score += 4
                reasons.append(f"CLI subcommand match: `{command}`")

        for fn in profile.function_details:
            fn_hits = goal_toks.intersection(_tokenize(fn.name))
            if fn_hits:
                score += 2
                reasons.append(f"Function name overlap: `{fn.name}`")
            if fn.docstring_summary:
                doc_hits = goal_toks.intersection(_tokenize(fn.docstring_summary))
                if doc_hits:
                    score += 2
                    reasons.append(f"Docstring signal on `{fn.name}`: {sorted(doc_hits)}")

        for dec in profile.decorator_signals:
            if any(t in dec for t in combined):
                score += 1
                reasons.append(f"Decorator / wiring signal: {dec}")

        if "network" in profile.io_profile and combined.intersection(
            {"send", "sync", "notify", "api", "http", "webhook", "request", "post", "get"}
        ):
            score += 2
            reasons.append("I/O fit: network workflow expected from goal/context")

        if "filesystem" in profile.io_profile and combined.intersection(
            {"file", "context", "split", "log", "path", "read", "write", "dir", "folder"}
        ):
            score += 2
            reasons.append("I/O fit: filesystem workflow expected from goal/context")

        if profile.dependencies:
            score += 1
            reasons.append(f"Orchestration: integrates with {len(profile.dependencies)} related tool(s)")

        if profile.risk_level == "high" and ("safe" in context.lower() or "local only" in context.lower()):
            score -= 3
            reasons.append("Safety: high-risk tool penalized under safe/local context")

        if "network_egress" in profile.safety_constraints and (
            "offline" in context.lower() or "airgap" in context.lower()
        ):
            score -= 3
            reasons.append("Safety: network egress discouraged by context")

        if profile.function_details:
            documented = sum(1 for f in profile.function_details if f.has_docstring)
            if documented:
                score += min(2, documented // 2)
                reasons.append(f"Documentation density: {documented} public function(s) with docstrings")

        ranked.append((score, profile, reasons))

    ranked.sort(key=lambda row: (row[0], len(row[2]), row[1].name), reverse=True)

    suggestions: list[dict[str, object]] = []
    for score, profile, reasons in ranked[: max(1, top_n)]:
        suggestions.append(
            {
                "tool": profile.name,
                "tool_id": profile.tool_id,
                "score": score,
                "reasoning": reasons or ["General fallback candidate (weak lexical overlap)"],
                "commands": profile.commands[:4],
                "dependencies": profile.dependencies,
                "safety_constraints": profile.safety_constraints,
                "io_profile": profile.io_profile,
                "suggested_chain": [profile.name, *profile.dependencies[:2]],
            }
        )
    return suggestions


def suggest_tools(
    profiles: list[ToolProfile],
    goal: str,
    context: str = "",
    top_n: int = 5,
) -> list[dict[str, object]]:
    """Backward-compatible wrapper: same argument order as historical API."""
    return suggest(goal, profiles, context=context, top_n=top_n)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover Python tools across the workspace: registry, docs, and ranked suggestions."
    )
    parser.add_argument("--root", default=".", help="Repository root path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("registry", help="Emit full tool registry as JSON")

    analyze = subparsers.add_parser("analyze", help="Run capability analysis (same data as registry)")
    analyze.add_argument("--format", choices=("json", "text"), default="json")

    docs = subparsers.add_parser("docs", help="Generate Markdown tool documentation")
    docs.add_argument(
        "--output",
        default=None,
        help="Markdown output path (default: <root>/docs/tool_discovery.md)",
    )

    suggest_p = subparsers.add_parser("suggest", help="Suggest tools with contextual reasoning")
    suggest_p.add_argument("goal", help="User goal, e.g. 'monitor queue latency'")
    suggest_p.add_argument("--context", default="", help="Additional operational context")
    suggest_p.add_argument("--top", type=int, default=5, help="Number of tools to suggest")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    registry = build_registry(root)

    if args.command == "registry":
        print(json.dumps(registry.to_dict(), indent=2))
        return 0

    if args.command == "analyze":
        if args.format == "json":
            print(json.dumps([profile.to_dict() for profile in registry.tools], indent=2))
        else:
            for profile in registry.tools:
                print(
                    f"{profile.name}: {', '.join(profile.capabilities)} | "
                    f"deps={profile.dependencies} | safety={profile.safety_constraints}"
                )
        return 0

    if args.command == "docs":
        markdown = generate_markdown(registry.tools, root=root)
        out_path = Path(args.output) if args.output else (root / "docs" / "tool_discovery.md")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8")
        return 0

    if args.command == "suggest":
        payload = {
            "goal": args.goal,
            "context": args.context,
            "suggestions": suggest(
                args.goal,
                registry.tools,
                context=args.context,
                top_n=max(1, args.top),
            ),
        }
        print(json.dumps(payload, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
