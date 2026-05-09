#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path


_STDLIB_TOP_LEVEL: frozenset[str] | None = None


def _stdlib_top_level_names() -> frozenset[str]:
    global _STDLIB_TOP_LEVEL
    if _STDLIB_TOP_LEVEL is not None:
        return _STDLIB_TOP_LEVEL
    names = getattr(sys, "stdlib_module_names", None)
    if names is not None:
        _STDLIB_TOP_LEVEL = frozenset(names)
    else:
        _STDLIB_TOP_LEVEL = frozenset()
    return _STDLIB_TOP_LEVEL


def _is_linkable_import(name: str) -> bool:
    """Stdlib-only imports are too common to infer cross-tool dependencies."""
    return name not in _stdlib_top_level_names()


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


@dataclass
class ToolProfile:
    name: str
    path: Path
    kind: str
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
            "kind": self.kind,
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


def _discover_script_files(scripts_dir: Path) -> list[Path]:
    if not scripts_dir.is_dir():
        return []
    return sorted(p for p in scripts_dir.glob("*.py") if p.name != "__init__.py")


def _discover_src_modules(src_dir: Path) -> list[Path]:
    if not src_dir.is_dir():
        return []
    return sorted(
        p
        for p in src_dir.rglob("*.py")
        if p.name != "__init__.py" and "__pycache__" not in p.parts
    )


def discover_python_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    paths.extend(_discover_script_files(root / "scripts"))
    paths.extend(_discover_src_modules(root / "src"))
    return sorted(paths, key=lambda p: (p.parts, str(p)))


def tool_name_for_path(path: Path, root: Path) -> tuple[str, str]:
    """Return (display_name, kind) where kind is 'script' or 'src_module'."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    parts = rel.parts
    if parts and parts[0] == "scripts":
        return path.stem, "script"
    if parts and parts[0] == "src":
        inner = path.relative_to(root / "src")
        dotted = ".".join(inner.with_suffix("").parts)
        return dotted, "src_module"
    return path.stem, "script"


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


def import_matches_profile(import_top: str, profile_name: str) -> bool:
    if import_top == profile_name:
        return True
    return profile_name.startswith(import_top + ".")


def analyze_scripts(root: Path) -> list[ToolProfile]:
    profiles: list[ToolProfile] = []
    for path in discover_python_paths(root):
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
        name, kind = tool_name_for_path(path, root)
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
                kind=kind,
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
    stdlib = _stdlib_top_level_names()
    by_name = {profile.name: profile for profile in profiles}
    for profile in profiles:
        deps: set[str] = set()
        for imported in profile.imports:
            if not _is_linkable_import(imported):
                continue
            for peer_name in by_name:
                if peer_name == profile.name:
                    continue
                if import_matches_profile(imported, peer_name):
                    deps.add(peer_name)
        for peer in profiles:
            if peer.name == profile.name:
                continue
            shared_imports = {
                m
                for m in profile.imports.intersection(peer.imports)
                if m not in stdlib
            }
            shared_caps = set(profile.capabilities).intersection(peer.capabilities)
            if len(shared_imports) >= 2 or len(shared_caps) >= 2:
                deps.add(peer.name)
        profile.dependencies = sorted(deps)


def build_examples(profile: ToolProfile) -> list[str]:
    examples: list[str] = []
    if profile.kind == "script":
        base = f"python -m scripts.{profile.name}"
        if profile.commands:
            for command in profile.commands[:3]:
                examples.append(f"{base} {command}")
        else:
            examples.append(base)
    else:
        base = f"PYTHONPATH=src python -m {profile.name}"
        if profile.commands:
            for command in profile.commands[:3]:
                examples.append(f"{base} {command}")
        else:
            examples.append(base)
    if "network" in profile.io_profile:
        examples.append(f"# Network-aware run\n{examples[0] if examples else f'PYTHONPATH=src python -m {profile.name}'} --help")
    if "filesystem" in profile.io_profile:
        examples.append(f"# Filesystem workflow\n{examples[0] if examples else f'python -m scripts.{profile.name}'} --help")
    return examples


def generate_markdown(profiles: list[ToolProfile]) -> str:
    scripts_n = sum(1 for p in profiles if p.kind == "script")
    src_n = sum(1 for p in profiles if p.kind == "src_module")
    lines = [
        "# Tool Discovery Report",
        "",
        "Auto-generated capability and dependency analysis for `scripts/` entrypoints and `src/` modules.",
        "",
        "## Summary",
        "",
        f"- Total tools discovered: **{len(profiles)}** (`scripts/`: {scripts_n}, `src/`: {src_n})",
        f"- High-risk tools: **{sum(1 for p in profiles if p.risk_level == 'high')}**",
        "",
    ]
    for profile in profiles:
        kind_label = "Script" if profile.kind == "script" else "Source module"
        lines.extend(
            [
                f"## `{profile.name}`",
                "",
                f"- Kind: {kind_label}",
                f"- Path: `{profile.path}`",
                f"- Description: {profile.description}",
                f"- Risk profile: **{profile.risk_level}**",
                f"- I/O behavior: {', '.join(profile.io_profile)}",
                f"- Capabilities: {', '.join(profile.capabilities)}",
                f"- Dependencies: {', '.join(profile.dependencies) if profile.dependencies else 'none'}",
                "",
                "### Functions",
                "",
            ]
        )
        if profile.functions:
            lines.extend(f"- `{fn}()`" for fn in profile.functions[:40])
            if len(profile.functions) > 40:
                lines.append(f"- _…and {len(profile.functions) - 40} more_")
        else:
            lines.append("- _No public top-level functions found_")
        lines.extend(["", "### Commands", ""])
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
    for part in goal_lower.replace(".", " ").split():
        if len(part) > 2 and part in profile.name.lower():
            score += 2
            reasons.append(f"Name/module match: {part}")
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
                "kind": profile.kind,
                "score": score,
                "reasoning": reasons or ["General fallback candidate"],
                "commands": profile.commands[:4],
                "dependencies": profile.dependencies,
                "suggested_chain": [profile.name, *profile.dependencies[:2]],
            }
        )
    return suggestions


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover capabilities of scripts/ and src/, generate docs, and suggest tools for a goal."
    )
    parser.add_argument("--root", default=".", help="Repository root path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Scan scripts/ and src/ and print capability analysis")
    analyze.add_argument("--format", choices=("json", "text"), default="json")

    docs = subparsers.add_parser("docs", help="Generate Markdown tool documentation (default: docs/tool_discovery.md)")
    docs.add_argument(
        "--output",
        default=None,
        help="Output path (default: <root>/docs/tool_discovery.md; use '-' for stdout)",
    )

    suggest = subparsers.add_parser("suggest", help="Suggest tools with contextual reasoning for a goal")
    suggest.add_argument("goal", help="User goal, e.g. 'monitor queue latency'")
    suggest.add_argument("--context", default="", help="Additional operational context")
    suggest.add_argument("--top", type=int, default=5, help="Number of tools to suggest")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _build_arg_parser().parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    profiles = analyze_scripts(root)

    if args.command == "analyze":
        if args.format == "json":
            print(json.dumps([profile.to_dict() for profile in profiles], indent=2))
        else:
            for profile in profiles:
                print(
                    f"{profile.name} ({profile.kind}): {', '.join(profile.capabilities)} | "
                    f"io={','.join(profile.io_profile)} | risk={profile.risk_level} | deps={profile.dependencies}"
                )
        return 0

    if args.command == "docs":
        markdown = generate_markdown(profiles)
        out = args.output
        if out is None or out == "":
            output = root / "docs" / "tool_discovery.md"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(markdown, encoding="utf-8")
        elif out.strip() == "-":
            print(markdown)
        else:
            output = Path(out)
            if not output.is_absolute():
                output = (root / output).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(markdown, encoding="utf-8")
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
