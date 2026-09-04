#!/usr/bin/env python3
"""Discover automation utilities, maintain a tools registry, and cross-link skills.

This module supports two complementary flows:

* **Script capability analysis** (``scripts/*.py``): risk/capability profiles,
  Markdown reports, contextual suggestions, and the ``workflow`` bridge to the
  repository-root gap scanner.
* **Inventory registry** (JSON + ``TOOLS_INVENTORY.md``): AST-guided discovery
  across Python sources, manual registrations, keyword search, documentation
  export, and optional overlap hints against ``SKILL.md`` files when present.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

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
)

HIGH_RISK_MARKERS = {"subprocess", "os", "shutil", "requests", "socket"}
IO_MARKERS = {"pathlib", "open", "json", "csv", "sqlite3"}
NETWORK_MARKERS = {"requests", "urllib", "http", "socket", "telegram"}

INVENTORY_VERSION = 1
DEFAULT_REGISTRY_JSON = "tools_inventory.json"
DEFAULT_REGISTRY_MD = "TOOLS_INVENTORY.md"

INVENTORY_SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        "node_modules",
        ".idea",
        ".vscode",
    }
)

API_DOC_MARKERS: frozenset[str] = frozenset(
    {
        "api",
        "client",
        "http",
        "https",
        "endpoint",
        "request",
        "wrapper",
        "webhook",
        "oauth",
        "rest",
        "graphql",
        "sdk",
        "service",
        "remote",
    }
)

CLI_FUNCTION_NAME_MARKERS: frozenset[str] = frozenset(
    {"main", "cli", "run", "parse_args", "entrypoint", "invoke", "execute", "serve"}
)

STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "into",
        "your",
        "are",
        "not",
        "can",
        "use",
        "using",
        "used",
        "when",
        "where",
        "which",
        "while",
        "will",
        "have",
        "has",
        "had",
        "was",
        "were",
        "been",
        "being",
        "their",
        "they",
        "them",
        "than",
        "then",
        "also",
        "such",
        "each",
        "other",
        "more",
        "most",
        "some",
        "any",
        "all",
        "both",
        "only",
        "very",
        "just",
        "over",
        "under",
        "between",
        "after",
        "before",
        "about",
        "into",
        "through",
        "during",
        "without",
        "within",
        "across",
        "based",
        "using",
        "python",
        "module",
        "class",
        "function",
        "return",
        "optional",
        "default",
        "arguments",
        "argument",
        "example",
        "examples",
    }
)


@dataclass
class ToolProfile:
    name: str
    path: Path
    description: str
    imports: set[str] = field(default_factory=set)
    functions: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    risk_level: str = "low"
    io_profile: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "path": str(self.path),
            "description": self.description,
            "imports": sorted(self.imports),
            "functions": self.functions,
            "commands": self.commands,
            "capabilities": self.capabilities,
            "risk_level": self.risk_level,
            "io_profile": self.io_profile,
            "dependencies": self.dependencies,
            "examples": self.examples,
        }


@dataclass
class InventoryTool:
    """Single row in ``tools_inventory.json``."""

    id: str
    name: str
    description: str
    file_path: str
    kind: str
    source: str
    usage_examples: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    suggested_skills: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "file_path": self.file_path,
            "kind": self.kind,
            "source": self.source,
            "usage_examples": self.usage_examples,
            "patterns": self.patterns,
            "suggested_skills": self.suggested_skills,
        }

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> InventoryTool:
        return InventoryTool(
            id=str(payload["id"]),
            name=str(payload["name"]),
            description=str(payload.get("description", "")),
            file_path=str(payload["file_path"]),
            kind=str(payload.get("kind", "manual")),
            source=str(payload.get("source", "manual")),
            usage_examples=list(payload.get("usage_examples") or []),
            patterns=list(payload.get("patterns") or []),
            suggested_skills=list(payload.get("suggested_skills") or []),
        )


def discover_script_paths(root: Path) -> list[Path]:
    scripts_dir = root / "scripts"
    if not scripts_dir.exists():
        return []
    return sorted(path for path in scripts_dir.glob("*.py") if path.name != "__init__.py")


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


def extract_functions(tree: ast.AST) -> list[str]:
    return sorted(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    )


def extract_description(tree: ast.AST) -> str:
    doc = ast.get_docstring(tree)
    if doc:
        return doc.strip().splitlines()[0]
    return "No module docstring available."


def infer_capabilities(name: str, description: str, commands: list[str], functions: list[str]) -> list[str]:
    corpus = " ".join([name, description, *commands, *functions]).lower()
    capabilities = [label for marker, label in KEYWORD_CAPABILITIES if marker in corpus]
    if "argparse" in corpus and "orchestration" not in capabilities:
        capabilities.append("CLI workflow automation")
    if not capabilities:
        capabilities.append("General utility automation")
    return sorted(set(capabilities))


def infer_risk_level(imports: set[str], commands: list[str]) -> str:
    import_hits = len(imports.intersection(HIGH_RISK_MARKERS))
    command_hits = len(commands)
    if import_hits >= 2 or command_hits >= 7:
        return "high"
    if import_hits >= 1 or command_hits >= 4:
        return "medium"
    return "low"


def infer_io_profile(imports: set[str], text: str) -> list[str]:
    profile: list[str] = []
    if imports.intersection(IO_MARKERS) or "Path(" in text or "open(" in text:
        profile.append("filesystem")
    if imports.intersection(NETWORK_MARKERS) or "http" in text or "https" in text:
        profile.append("network")
    if "subprocess" in imports or "subprocess." in text:
        profile.append("process")
    if "json" in imports:
        profile.append("structured-data")
    return profile or ["in-memory"]


def analyze_scripts(root: Path) -> list[ToolProfile]:
    profiles: list[ToolProfile] = []
    for path in discover_script_paths(root):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        name = path.stem
        imports = extract_imports(tree)
        functions = extract_functions(tree)
        commands = extract_cli_commands(tree)
        description = extract_description(tree)
        capabilities = infer_capabilities(name, description, commands, functions)
        io_profile = infer_io_profile(imports, source)
        risk_level = infer_risk_level(imports, commands)
        profiles.append(
            ToolProfile(
                name=name,
                path=path.relative_to(root),
                description=description,
                imports=imports,
                functions=functions,
                commands=commands,
                capabilities=capabilities,
                risk_level=risk_level,
                io_profile=io_profile,
            )
        )
    enrich_dependency_graph(profiles)
    for profile in profiles:
        profile.examples = build_examples(profile)
    return profiles


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


def build_examples(profile: ToolProfile) -> list[str]:
    base = f"python -m scripts.{profile.name}"
    examples: list[str] = []
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


def generate_markdown(profiles: list[ToolProfile]) -> str:
    lines = [
        "# Tool Discovery Report",
        "",
        "Auto-generated capability and dependency analysis for scripts/ tools.",
        "",
        "## Summary",
        "",
        f"- Total tools discovered: **{len(profiles)}**",
        f"- High-risk tools: **{sum(1 for p in profiles if p.risk_level == 'high')}**",
        "",
    ]
    for profile in profiles:
        lines.extend(
            [
                f"## `{profile.name}`",
                "",
                f"- Path: `{profile.path}`",
                f"- Description: {profile.description}",
                f"- Risk level: **{profile.risk_level}**",
                f"- Capabilities: {', '.join(profile.capabilities)}",
                f"- I/O profile: {', '.join(profile.io_profile)}",
                f"- Dependencies: {', '.join(profile.dependencies) if profile.dependencies else 'none'}",
                "",
                "### Commands",
                "",
            ]
        )
        if profile.commands:
            lines.extend(f"- `{cmd}`" for cmd in profile.commands)
        else:
            lines.append("- _No subcommands discovered_")
        lines.extend(["", "### Example usage", ""])
        lines.extend(f"```bash\n{example}\n```" for example in profile.examples)
        lines.append("")
    return "\n".join(lines)


def score_tool_for_goal(profile: ToolProfile, goal: str, context: str) -> tuple[int, list[str]]:
    goal_lower = goal.lower()
    context_lower = context.lower()
    reasons: list[str] = []
    score = 0

    for capability in profile.capabilities:
        cap_words = capability.lower().split()
        if any(word in goal_lower for word in cap_words):
            score += 3
            reasons.append(f"Capability match: {capability}")
    for command in profile.commands:
        if command in goal_lower or command in context_lower:
            score += 2
            reasons.append(f"Command match: {command}")
    combined_text = f"{goal_lower} {context_lower}"
    if "network" in profile.io_profile and any(word in combined_text for word in ("send", "sync", "notify", "api")):
        score += 2
        reasons.append("I/O fit: network workflow expected")
    if "filesystem" in profile.io_profile and any(word in combined_text for word in ("file", "context", "split", "log")):
        score += 2
        reasons.append("I/O fit: filesystem workflow expected")
    if profile.dependencies:
        score += 1
        reasons.append(f"Integrates with {len(profile.dependencies)} related tools")
    if profile.risk_level == "high" and "safe" in context_lower:
        score -= 2
        reasons.append("Context warns for safety; high-risk tool penalized")
    return score, reasons


def suggest_tools(profiles: list[ToolProfile], goal: str, context: str, top_n: int = 5) -> list[dict[str, object]]:
    ranked: list[tuple[int, ToolProfile, list[str]]] = []
    for profile in profiles:
        score, reasons = score_tool_for_goal(profile, goal, context)
        ranked.append((score, profile, reasons))
    ranked.sort(key=lambda row: (row[0], len(row[2]), row[1].name), reverse=True)

    suggestions: list[dict[str, object]] = []
    for score, profile, reasons in ranked[:top_n]:
        suggestions.append(
            {
                "tool": profile.name,
                "score": score,
                "reasoning": reasons or ["General fallback candidate"],
                "commands": profile.commands[:4],
                "dependencies": profile.dependencies,
                "suggested_chain": [profile.name, *profile.dependencies[:2]],
            }
        )
    return suggestions


def _load_repo_workflow_tool_discovery():
    """Import repository-root ``tool_discovery.py`` (workflow gap scanner) by file path."""

    repo_root = Path(__file__).resolve().parent.parent
    mod_path = repo_root / "tool_discovery.py"
    name = "_openclaw_workflow_tool_discovery"
    spec = importlib.util.spec_from_file_location(name, mod_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load workflow scanner from {mod_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Inventory registry: filesystem walk, AST heuristics, JSON/Markdown, skills
# ---------------------------------------------------------------------------


def _is_under_skipped_dir(path: Path) -> bool:
    return any(part in INVENTORY_SKIP_DIR_NAMES for part in path.parts)


def iter_inventory_python_files(root: Path, *, include_tests: bool = False) -> Iterator[Path]:
    """Yield ``.py`` files suitable for registry scanning."""

    for path in sorted(root.rglob("*.py")):
        if _is_under_skipped_dir(path):
            continue
        rel = path.relative_to(root)
        if not include_tests and rel.parts and rel.parts[0] == "tests":
            continue
        yield path


def _first_line(doc: str | None) -> str:
    if not doc:
        return ""
    return doc.strip().splitlines()[0].strip()


def _module_id_for_path(root: Path, path: Path) -> str | None:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return None
    parts = rel.parts
    if not parts:
        return None
    if parts[0] in {"scripts", "src"}:
        dotted = ".".join(parts[:-1] + (path.stem,))
        return dotted
    return None


def _file_uses_argparse(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "ArgumentParser":
            return True
        if isinstance(func, ast.Name) and func.id == "ArgumentParser":
            return True
    return False


def _module_has_main_guard(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "__name__":
            for comparator in test.comparators:
                if isinstance(comparator, ast.Constant) and comparator.value == "__main__":
                    return True
    return False


def _function_body_uses_argparse(tree: ast.FunctionDef) -> bool:
    for child in ast.walk(tree):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Attribute) and func.attr == "ArgumentParser":
                return True
            if isinstance(func, ast.Name) and func.id == "ArgumentParser":
                return True
    return False


def _class_looks_like_api_wrapper(
    node: ast.ClassDef,
    imports: set[str],
    module_source: str,
) -> bool:
    doc = (ast.get_docstring(node) or "").lower()
    if any(marker in doc for marker in API_DOC_MARKERS):
        return True
    lowered_name = node.name.lower()
    if any(token in lowered_name for token in ("client", "api", "adapter", "wrapper", "session", "transport")):
        return True
    net_hits = imports.intersection(NETWORK_MARKERS.union({"httpx", "aiohttp", "urllib3"}))
    if net_hits:
        for base in node.bases:
            if isinstance(base, ast.Name):
                if base.id in {"Client", "Session", "APIClient"}:
                    return True
        for stmt in node.body:
            if isinstance(stmt, ast.FunctionDef):
                if stmt.name.lower() in {"get", "post", "put", "delete", "request", "fetch", "call"}:
                    return True
    if "requests." in module_source or "httpx." in module_source:
        for stmt in node.body:
            if isinstance(stmt, ast.FunctionDef) and any(
                kw in stmt.name.lower() for kw in ("request", "fetch", "call", "post", "get")
            ):
                return True
    return False


def _function_looks_like_cli_candidate(node: ast.FunctionDef, tree: ast.AST) -> bool:
    doc = ast.get_docstring(node)
    if not doc or len(doc.strip()) < 24:
        return False
    if node.name.startswith("_"):
        return False
    if node.name in CLI_FUNCTION_NAME_MARKERS:
        return True
    if _function_body_uses_argparse(node):
        return True
    doc_l = doc.lower()
    if any(word in doc_l for word in ("cli", "command line", "argparse", "console", "usage:", "flags")):
        return True
    return False


def _usage_for_module(root: Path, path: Path, tree: ast.AST, commands: list[str]) -> list[str]:
    rel = path.relative_to(root).as_posix()
    mod_id = _module_id_for_path(root, path)
    examples: list[str] = []
    if mod_id and mod_id.startswith("scripts."):
        base = f"python -m {mod_id}"
        if commands:
            for cmd in commands[:4]:
                examples.append(f"{base} {cmd}")
        else:
            examples.append(base)
        examples.append(f"{base} --help")
    elif mod_id and mod_id.startswith("src."):
        base = f"python -m {mod_id}"
        if _module_has_main_guard(tree):
            examples.append(base)
        examples.append(f"from {mod_id} import ...  # library entry")
    else:
        examples.append(f"python {rel}")
        examples.append(f"# import from project: see {rel}")
    return examples


def discover_inventory_tools_for_file(root: Path, path: Path) -> list[InventoryTool]:
    """Parse a single Python file and emit zero or more registry rows."""

    rel = path.relative_to(root).as_posix()
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    imports = extract_imports(tree)
    module_doc = ast.get_docstring(tree)
    module_summary = _first_line(module_doc) or f"Python module at `{rel}`."
    commands = extract_cli_commands(tree)
    uses_argparse = _file_uses_argparse(tree)
    has_main = _module_has_main_guard(tree)

    tools: list[InventoryTool] = []
    module_patterns: list[str] = []
    if uses_argparse:
        module_patterns.append("argparse")
    if has_main:
        module_patterns.append("__main__")
    if commands:
        module_patterns.append("subcommands")

    should_emit_module = (
        rel.startswith("scripts/")
        or rel.startswith("src/")
        or has_main
        or uses_argparse
        or bool(commands)
    )
    if should_emit_module:
        mod_name = path.stem
        mod_id = f"{rel}::module::{mod_name}"
        tools.append(
            InventoryTool(
                id=mod_id,
                name=mod_name,
                description=module_summary,
                file_path=rel,
                kind="module",
                source="discovered",
                usage_examples=_usage_for_module(root, path, tree, commands),
                patterns=sorted(set(module_patterns)) or ["python-module"],
            )
        )

    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            if _function_looks_like_cli_candidate(node, tree):
                doc = _first_line(ast.get_docstring(node))
                fn_id = f"{rel}::function::{node.name}"
                patterns = ["public-function", "docstring-cli-candidate"]
                if _function_body_uses_argparse(node):
                    patterns.append("argparse-in-body")
                tools.append(
                    InventoryTool(
                        id=fn_id,
                        name=node.name,
                        description=doc or f"Function `{node.name}` in `{rel}`.",
                        file_path=rel,
                        kind="function",
                        source="discovered",
                        usage_examples=[
                            f"# {rel}: call `{node.name}` from application or notebook code after importing the package",
                            f"# Add a thin CLI wrapper if this function should be invoked from the shell",
                        ],
                        patterns=sorted(set(patterns)),
                    )
                )
        elif isinstance(node, ast.ClassDef):
            if _class_looks_like_api_wrapper(node, imports, source):
                doc = _first_line(ast.get_docstring(node))
                cls_id = f"{rel}::class::{node.name}"
                tools.append(
                    InventoryTool(
                        id=cls_id,
                        name=node.name,
                        description=doc or f"Class `{node.name}` in `{rel}`.",
                        file_path=rel,
                        kind="class",
                        source="discovered",
                        usage_examples=[
                            f"# {rel}: construct `{node.name}` where external APIs are coordinated",
                            (
                                f"from {_module_id_for_path(root, path)} import {node.name}"
                                if _module_id_for_path(root, path)
                                else f"# import `{node.name}` from `{rel}` within the project path"
                            ),
                        ],
                        patterns=["api-wrapper", "external-integration"],
                    )
                )

    return tools


def scan_inventory_tools(root: Path, *, include_tests: bool = False) -> list[InventoryTool]:
    """Scan the repository for Python modules, CLI-like functions, and API-style classes."""

    discovered: list[InventoryTool] = []
    for path in iter_inventory_python_files(root, include_tests=include_tests):
        discovered.extend(discover_inventory_tools_for_file(root, path))
    # Stable ordering for deterministic JSON diffs
    discovered.sort(key=lambda t: (t.file_path, t.kind, t.name))
    return dedupe_inventory_tools(discovered)


def dedupe_inventory_tools(tools: Iterable[InventoryTool]) -> list[InventoryTool]:
    by_id: dict[str, InventoryTool] = {}
    for tool in tools:
        by_id[tool.id] = tool
    return list(by_id.values())


def registry_json_path(root: Path, candidate: str) -> Path:
    path = Path(candidate)
    return path if path.is_absolute() else (root / path)


def load_registry(path: Path) -> tuple[dict[str, Any], list[InventoryTool]]:
    if not path.exists():
        payload: dict[str, Any] = {"version": INVENTORY_VERSION, "generated_at": "", "tools": []}
        return payload, []
    raw = json.loads(path.read_text(encoding="utf-8"))
    tools = [InventoryTool.from_dict(entry) for entry in raw.get("tools", [])]
    return raw, tools


def save_registry(path: Path, tools: list[InventoryTool], *, merge_manual: bool = True) -> None:
    manual: list[InventoryTool] = []
    if merge_manual and path.exists():
        _, existing = load_registry(path)
        manual = [t for t in existing if t.source == "manual"]

    merged = {t.id: t for t in manual}
    for tool in tools:
        merged[tool.id] = tool
    final_tools = sorted(merged.values(), key=lambda t: (t.source != "manual", t.file_path, t.kind, t.name))

    payload = {
        "version": INVENTORY_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "tools": [t.to_dict() for t in final_tools],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def render_tools_inventory_markdown(tools: list[InventoryTool], *, intro: str | None = None) -> str:
    """Human-readable inventory suitable for ``TOOLS_INVENTORY.md``."""

    lines = [
        "# Tools inventory",
        "",
        intro
        or (
            "Registry of discovered and manually registered Python tools. "
            "Regenerate with `python -m scripts.tool_discovery scan`."
        ),
        "",
        "## Summary",
        "",
        f"- Total entries: **{len(tools)}**",
        f"- Manual entries: **{sum(1 for t in tools if t.source == 'manual')}**",
        "",
        "## Index",
        "",
        "| Name | Kind | Source | File | Description |",
        "| --- | --- | --- | --- | --- |",
    ]
    for tool in sorted(tools, key=lambda t: (t.file_path, t.name)):
        desc = tool.description.replace("|", "\\|")
        lines.append(
            f"| `{tool.name}` | {tool.kind} | {tool.source} | `{tool.file_path}` | {desc} |"
        )
    lines.append("")
    for tool in sorted(tools, key=lambda t: (t.file_path, t.kind, t.name)):
        lines.extend(
            [
                f"## `{tool.name}` — `{tool.id}`",
                "",
                f"- **File:** `{tool.file_path}`",
                f"- **Kind:** {tool.kind}",
                f"- **Source:** {tool.source}",
                f"- **Patterns:** {', '.join(tool.patterns) if tool.patterns else '_none_'}",
                "",
                tool.description,
                "",
                "### Usage examples",
                "",
            ]
        )
        if tool.usage_examples:
            lines.extend(f"```bash\n{ex}\n```" for ex in tool.usage_examples)
        else:
            lines.append("_No examples recorded._")
        lines.append("")
        if tool.suggested_skills:
            lines.append("### Suggested skills")
            lines.append("")
            for hint in tool.suggested_skills[:5]:
                title = str(hint.get("skill_title") or hint.get("path", ""))
                score = hint.get("score", 0)
                keywords = hint.get("shared_keywords", [])
                rel = str(hint.get("path", ""))
                kw_txt = ", ".join(f"`{k}`" for k in keywords[:6]) if keywords else "_n/a_"
                lines.append(f"- `{rel}` — **{title}** (score {score}; shared {kw_txt})")
            lines.append("")
    return "\n".join(lines)


def generate_registry_markdown_docs(tools: list[InventoryTool]) -> str:
    """Long-form Markdown documentation for registry entries (distinct from the inventory table)."""

    lines = [
        "# Discovered tools documentation",
        "",
        "Generated from ``tools_inventory.json``. Each section summarizes heuristics, "
        "recorded examples, and optional skill cross-links.",
        "",
    ]
    for tool in sorted(tools, key=lambda t: (t.file_path, t.name)):
        lines.extend(
            [
                f"## {tool.name}",
                "",
                f"**Identifier:** `{tool.id}`  ",
                f"**Path:** `{tool.file_path}`  ",
                f"**Kind:** {tool.kind}  ",
                f"**Origin:** {tool.source}  ",
                "",
                "### Description",
                "",
                tool.description,
                "",
                "### Detected patterns",
                "",
            ]
        )
        if tool.patterns:
            lines.extend(f"- {pattern}" for pattern in tool.patterns)
        else:
            lines.append("- _No pattern tags_")
        lines.extend(["", "### Usage examples", ""])
        if tool.usage_examples:
            lines.extend(f"```bash\n{ex}\n```" for ex in tool.usage_examples)
        else:
            lines.append("_No examples recorded._")
        if tool.suggested_skills:
            lines.extend(["", "### Skills that may benefit", ""])
            for hint in tool.suggested_skills[:5]:
                lines.append(
                    f"- `{hint.get('path')}` — score **{hint.get('score', 0)}** "
                    f"({', '.join(hint.get('shared_keywords', [])[:6]) or 'semantic overlap'})"
                )
        lines.append("")
    return "\n".join(lines)


def _tokenize_for_overlap(text: str) -> set[str]:
    tokens = re.split(r"[^a-zA-Z0-9_]+", text.lower())
    return {t for t in tokens if len(t) > 2 and t not in STOPWORDS}


def discover_skill_files(root: Path) -> list[Path]:
    """Return every ``SKILL.md`` under the repository (excluding skipped dirs)."""

    skills: list[Path] = []
    for path in root.rglob("SKILL.md"):
        if _is_under_skipped_dir(path):
            continue
        skills.append(path)
    return sorted(skills)


def _skill_title(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip() or "Skill"
    return "Skill"


def attach_skill_hints(root: Path, tools: list[InventoryTool]) -> list[InventoryTool]:
    """Annotate tools with lightweight keyword overlap against ``SKILL.md`` bodies."""

    skill_paths = discover_skill_files(root)
    if not skill_paths:
        for tool in tools:
            tool.suggested_skills = []
        return tools

    skill_docs: list[tuple[Path, str, str, set[str]]] = []
    for skill_path in skill_paths:
        try:
            body = skill_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = skill_path.relative_to(root).as_posix()
        title = _skill_title(body)
        tokens = _tokenize_for_overlap(body)
        skill_docs.append((skill_path, rel, title, tokens))

    enriched: list[InventoryTool] = []
    for tool in tools:
        haystack = " ".join([tool.name, tool.description, tool.file_path, *tool.usage_examples, *tool.patterns])
        tool_tokens = _tokenize_for_overlap(haystack)
        hints: list[dict[str, Any]] = []
        for _, rel, title, sk_tokens in skill_docs:
            shared = sorted(tool_tokens.intersection(sk_tokens))
            score = len(shared)
            if tool.name.lower() in _tokenize_for_overlap(title):
                score += 2
            if score <= 0:
                continue
            hints.append(
                {
                    "path": rel,
                    "skill_title": title,
                    "score": score,
                    "shared_keywords": shared[:12],
                }
            )
        hints.sort(key=lambda h: (-int(h["score"]), str(h["path"])))
        tool.suggested_skills = hints[:5]
        enriched.append(tool)
    return enriched


def filter_tools_by_keyword(tools: list[InventoryTool], query: str) -> list[InventoryTool]:
    q = query.lower()
    matches: list[InventoryTool] = []
    for tool in tools:
        blob = " ".join(
            [
                tool.name,
                tool.description,
                tool.file_path,
                tool.kind,
                tool.source,
                *tool.patterns,
                *tool.usage_examples,
            ]
        ).lower()
        if q in blob:
            matches.append(tool)
    return matches


def add_manual_tool(
    root: Path,
    *,
    name: str,
    description: str,
    file_path: str,
    examples: list[str],
    registry_path: Path,
) -> InventoryTool:
    rel = Path(file_path)
    rel_posix = rel.as_posix()
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", name).strip("_").lower() or "tool"
    manual_id = f"manual::{rel_posix}::{slug}"
    tool = InventoryTool(
        id=manual_id,
        name=name,
        description=description,
        file_path=rel_posix,
        kind="manual",
        source="manual",
        usage_examples=list(examples),
        patterns=["manual-entry"],
    )
    _, existing = load_registry(registry_path)
    merged = {t.id: t for t in existing}
    merged[tool.id] = tool
    save_registry(registry_path, list(merged.values()), merge_manual=False)
    return tool


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover and suggest utility scripts by capability.")
    parser.set_defaults(from_registry=False)
    parser.add_argument("--root", default=".", help="Repository root path")
    parser.add_argument(
        "--registry",
        default=DEFAULT_REGISTRY_JSON,
        help="Path to tools_inventory.json (absolute or relative to --root).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Run deep tool capability analysis")
    analyze.add_argument("--format", choices=("json", "text"), default="json")

    docs = subparsers.add_parser("docs", help="Generate Markdown tool documentation")
    docs.add_argument("--output", help="Write docs to this path instead of stdout")
    docs.add_argument(
        "--from-registry",
        action="store_true",
        help="Render long-form Markdown from tools_inventory.json instead of scripts-only analysis.",
    )

    suggest = subparsers.add_parser("suggest", help="Suggest tools with contextual reasoning")
    suggest.add_argument("goal", help="User goal, e.g. 'monitor queue latency'")
    suggest.add_argument("--context", default="", help="Additional operational context")
    suggest.add_argument("--top", type=int, default=5, help="Number of tools to suggest")

    workflow = subparsers.add_parser(
        "workflow",
        help="List src/ and scripts/ tools not referenced on deployment surfaces (README, docs, nightly, …).",
    )
    workflow.add_argument(
        "--report-format",
        choices=("markdown", "json"),
        default="markdown",
        help="Serialization for the gap report.",
    )
    workflow.add_argument(
        "--report-output",
        "-o",
        help="Write the gap report to this path instead of stdout.",
    )
    workflow.add_argument(
        "--extra-surface",
        action="append",
        default=[],
        metavar="PATH",
        help="Extra deployment surface file, relative to --root unless absolute (repeatable).",
    )

    scan = subparsers.add_parser(
        "scan",
        help="Scan Python sources for CLI/API patterns and refresh the tools inventory registry.",
    )
    scan.add_argument(
        "--markdown-output",
        default=DEFAULT_REGISTRY_MD,
        help="Path for TOOLS_INVENTORY.md (absolute or relative to --root).",
    )
    scan.add_argument("--no-markdown", action="store_true", help="Skip Markdown inventory export.")
    scan.add_argument("--include-tests", action="store_true", help="Include files under tests/.")
    scan.add_argument(
        "--match-skills",
        action="store_true",
        help="Annotate entries with SKILL.md files that share vocabulary.",
    )

    add_tool = subparsers.add_parser("add-tool", help="Register a tool manually inside tools_inventory.json.")
    add_tool.add_argument("--name", required=True, help="Display name for the tool.")
    add_tool.add_argument("--description", required=True, help="Short description shown in listings.")
    add_tool.add_argument(
        "--path",
        required=True,
        dest="file_path",
        help="Path to the backing Python file (relative to the repository root).",
    )
    add_tool.add_argument(
        "--example",
        action="append",
        default=[],
        dest="examples",
        metavar="CMD",
        help="Usage example (repeatable).",
    )

    list_tools = subparsers.add_parser("list-tools", help="List everything recorded in tools_inventory.json.")
    list_tools.add_argument("--format", choices=("json", "text"), default="text")

    search_tools = subparsers.add_parser(
        "search-tools",
        help="Search registry entries by substring across names, descriptions, paths, and examples.",
    )
    search_tools.add_argument("query", help="Case-insensitive substring to match.")
    search_tools.add_argument("--format", choices=("json", "text"), default="text")

    match_skills = subparsers.add_parser(
        "match-skills",
        help="Recompute SKILL.md cross references for the current registry snapshot.",
    )
    match_skills.add_argument(
        "--persist",
        action="store_true",
        help="Write suggested skill hints back to tools_inventory.json.",
    )
    match_skills.add_argument(
        "--markdown-output",
        help="Optional path to refresh TOOLS_INVENTORY.md after matching.",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    registry_path = registry_json_path(root, args.registry)

    if args.command == "workflow":
        mod = _load_repo_workflow_tool_discovery()
        wf_argv: list[str] = ["--root", str(root), "--format", args.report_format]
        if args.report_output:
            wf_argv.extend(["--output", args.report_output])
        for surf in args.extra_surface or []:
            wf_argv.extend(["--extra-surface", surf])
        return int(mod.main(wf_argv))

    if args.command == "scan":
        discovered = scan_inventory_tools(root, include_tests=args.include_tests)
        if args.match_skills:
            attach_skill_hints(root, discovered)
        save_registry(registry_path, discovered, merge_manual=True)
        if not args.no_markdown:
            _, tools = load_registry(registry_path)
            md_path = registry_json_path(root, args.markdown_output)
            md_path.write_text(render_tools_inventory_markdown(tools), encoding="utf-8")
        return 0

    if args.command == "add-tool":
        add_manual_tool(
            root,
            name=args.name,
            description=args.description,
            file_path=args.file_path,
            examples=list(args.examples or []),
            registry_path=registry_path,
        )
        return 0

    if args.command == "list-tools":
        _, tools = load_registry(registry_path)
        if args.format == "json":
            print(json.dumps([t.to_dict() for t in tools], indent=2))
        else:
            if not tools:
                print("No tools registered yet. Run `python -m scripts.tool_discovery scan` first.")
                return 0
            for tool in sorted(tools, key=lambda t: t.file_path):
                print(f"{tool.name}\t{tool.kind}\t{tool.source}\t{tool.file_path}\t{tool.description}")
        return 0

    if args.command == "search-tools":
        _, tools = load_registry(registry_path)
        hits = filter_tools_by_keyword(tools, args.query)
        if args.format == "json":
            print(json.dumps([t.to_dict() for t in hits], indent=2))
        else:
            if not hits:
                print("No matches.")
                return 0
            for tool in hits:
                print(f"{tool.name} ({tool.kind}) — {tool.file_path}\n  {tool.description}\n")
        return 0

    if args.command == "match-skills":
        _, tools = load_registry(registry_path)
        if not tools:
            print("Registry is empty; run `scan` first.", file=sys.stderr)
            return 1
        attach_skill_hints(root, tools)
        if args.persist:
            payload, _ = load_registry(registry_path)
            payload["tools"] = [t.to_dict() for t in tools]
            payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            registry_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if args.markdown_output:
            md_path = registry_json_path(root, args.markdown_output)
            md_path.write_text(render_tools_inventory_markdown(tools), encoding="utf-8")
        print(json.dumps([t.to_dict() for t in tools], indent=2))
        return 0

    if args.command == "docs" and args.from_registry:
        _, tools = load_registry(registry_path)
        if not tools:
            print("Registry is empty; run `python -m scripts.tool_discovery scan` first.", file=sys.stderr)
            return 1
        markdown = generate_registry_markdown_docs(tools)
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(markdown, encoding="utf-8")
        else:
            print(markdown)
        return 0

    profiles = analyze_scripts(root)

    if args.command == "analyze":
        if args.format == "json":
            print(json.dumps([profile.to_dict() for profile in profiles], indent=2))
        else:
            for profile in profiles:
                print(f"{profile.name}: {', '.join(profile.capabilities)} | deps={profile.dependencies}")
        return 0

    if args.command == "docs":
        markdown = generate_markdown(profiles)
        if args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(markdown, encoding="utf-8")
        else:
            print(markdown)
        return 0

    if args.command == "suggest":
        payload = {
            "goal": args.goal,
            "context": args.context,
            "suggestions": suggest_tools(profiles, goal=args.goal, context=args.context, top_n=max(1, args.top)),
        }
        print(json.dumps(payload, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
