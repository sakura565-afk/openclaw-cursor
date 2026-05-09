#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


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
    description: str
    category: str = "scripts"
    imports: set[str] = field(default_factory=set)
    functions: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    risk_level: str = "low"
    io_profile: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)

    @property
    def entry_id(self) -> str:
        return f"{self.category}:{self.name}"

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.entry_id,
            "category": self.category,
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


def discover_script_paths(root: Path) -> list[Path]:
    scripts_dir = root / "scripts"
    if not scripts_dir.exists():
        return []
    return sorted(path for path in scripts_dir.glob("*.py") if path.name != "__init__.py")


def discover_nested_py_roots(root: Path) -> list[tuple[Path, str]]:
    """Return existing (directory, category) pairs for tools and skills trees."""
    pairs: list[tuple[Path, str]] = []
    mapping = (
        (root / "tools", "tools"),
        (root / "skills", "skills"),
        (root / "src" / "skills", "skills"),
    )
    for directory, category in mapping:
        if directory.is_dir():
            pairs.append((directory, category))
    return pairs


def discover_nested_python_files(root: Path) -> list[tuple[Path, str]]:
    discovered: list[tuple[Path, str]] = []
    for base_dir, category in discover_nested_py_roots(root):
        for path in sorted(base_dir.rglob("*.py")):
            if path.name == "__init__.py":
                continue
            discovered.append((path, category))
    return discovered


def discover_markdown_skill_files(root: Path) -> list[tuple[Path, str]]:
    """Optional non-Python artifacts under tools/ or skills/ (e.g. SKILL.md)."""
    found: list[tuple[Path, str]] = []
    for base_dir, category in discover_nested_py_roots(root):
        for path in sorted(base_dir.rglob("*.md")):
            found.append((path, category))
    return found


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
    if "argparse" in corpus and "orchestration" not in corpus:
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


TOKEN_PATTERN = re.compile(r"[a-z][a-z0-9_-]{2,}", re.IGNORECASE)


def stable_nested_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root)
    return rel.with_suffix("").as_posix().replace("/", "_")


def extract_markdown_title(text: str) -> str:
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def tokenize_keywords(text: str) -> set[str]:
    return {m.group(0).lower() for m in TOKEN_PATTERN.finditer(text)}


def collect_entry_keywords(profile: ToolProfile) -> set[str]:
    parts = [
        profile.name,
        profile.description,
        profile.category,
        *profile.capabilities,
        *profile.functions,
        *profile.commands,
        *profile.io_profile,
        str(profile.path),
    ]
    blob = " ".join(parts)
    return tokenize_keywords(blob)


def build_keyword_index(profiles: list[ToolProfile]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for profile in profiles:
        entry_id = profile.entry_id
        for kw in collect_entry_keywords(profile):
            bucket = index.setdefault(kw, [])
            if entry_id not in bucket:
                bucket.append(entry_id)
    for kw in index:
        index[kw] = sorted(index[kw])
    return dict(sorted(index.items()))


def build_search_index_payload(root: Path, profiles: list[ToolProfile]) -> dict[str, object]:
    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "entries": [profile.to_dict() for profile in profiles],
        "keyword_index": build_keyword_index(profiles),
    }


def search_by_keywords(profiles: list[ToolProfile], query: str, limit: int = 20) -> list[dict[str, object]]:
    tokens = tokenize_keywords(query)
    if not tokens:
        stripped = query.strip().lower()
        if stripped:
            tokens = {stripped}

    by_id = {profile.entry_id: profile for profile in profiles}
    keyword_index = build_keyword_index(profiles)
    scores: dict[str, int] = {}

    for tok in tokens:
        for entry_id in keyword_index.get(tok, []):
            scores[entry_id] = scores.get(entry_id, 0) + 3
        for profile in profiles:
            hay = f"{profile.name} {profile.description}".lower()
            if tok in hay:
                scores[profile.entry_id] = scores.get(profile.entry_id, 0) + 2

    ranked_ids = sorted(scores.keys(), key=lambda eid: (-scores[eid], eid))
    results: list[dict[str, object]] = []
    for entry_id in ranked_ids[: max(1, limit)]:
        profile = by_id[entry_id]
        results.append(
            {
                "id": entry_id,
                "score": scores[entry_id],
                "matched_keywords": sorted(tokens) if tokens else [],
                "entry": profile.to_dict(),
            }
        )
    return results


def analyze_python_file(path: Path, root: Path, category: str) -> ToolProfile:
    source = path.read_text(encoding="utf-8")
    name = path.stem if category == "scripts" else stable_nested_name(path, root)
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        description = f"Syntax error while scanning ({exc.msg or 'invalid Python'})"
        return ToolProfile(
            name=name,
            path=path.relative_to(root),
            description=description,
            category=category,
            imports=set(),
            functions=[],
            commands=[],
            capabilities=["General utility automation"],
            risk_level="medium",
            io_profile=infer_io_profile(set(), source),
        )

    imports = extract_imports(tree)
    functions = extract_functions(tree)
    commands = extract_cli_commands(tree)
    description = extract_description(tree)
    capabilities = infer_capabilities(name, description, commands, functions)
    io_profile = infer_io_profile(imports, source)
    risk_level = infer_risk_level(imports, commands)
    return ToolProfile(
        name=name,
        path=path.relative_to(root),
        description=description,
        category=category,
        imports=imports,
        functions=functions,
        commands=commands,
        capabilities=capabilities,
        risk_level=risk_level,
        io_profile=io_profile,
    )


def analyze_markdown_file(path: Path, root: Path, category: str) -> ToolProfile:
    text = path.read_text(encoding="utf-8", errors="replace")
    title = extract_markdown_title(text)
    description = title or "Markdown document"
    name = stable_nested_name(path, root)
    capabilities = infer_capabilities(name, description, [], [])
    return ToolProfile(
        name=name,
        path=path.relative_to(root),
        description=description,
        category=category,
        imports=set(),
        functions=[],
        commands=[],
        capabilities=capabilities,
        risk_level="low",
        io_profile=infer_io_profile(set(), text),
    )


def analyze_scripts(root: Path) -> list[ToolProfile]:
    profiles: list[ToolProfile] = []
    for path in discover_script_paths(root):
        profiles.append(analyze_python_file(path, root, "scripts"))
    for path, category in discover_nested_python_files(root):
        profiles.append(analyze_python_file(path, root, category))
    for path, category in discover_markdown_skill_files(root):
        profiles.append(analyze_markdown_file(path, root, category))
    enrich_dependency_graph(profiles)
    for profile in profiles:
        profile.examples = build_examples(profile)
    return profiles


def enrich_dependency_graph(profiles: list[ToolProfile]) -> None:
    by_name = {profile.name: profile for profile in profiles}
    stem_index: dict[str, list[ToolProfile]] = {}
    for profile in profiles:
        stem = Path(profile.path).stem
        stem_index.setdefault(stem, []).append(profile)

    def resolve_import(module_root: str) -> ToolProfile | None:
        if module_root in by_name:
            return by_name[module_root]
        candidates = stem_index.get(module_root, [])
        if len(candidates) == 1:
            return candidates[0]
        return None

    for profile in profiles:
        deps: set[str] = set()
        for imported in profile.imports:
            peer = resolve_import(imported)
            if peer and peer.entry_id != profile.entry_id:
                deps.add(peer.name)
        for peer in profiles:
            if peer.entry_id == profile.entry_id:
                continue
            shared_imports = profile.imports.intersection(peer.imports)
            shared_caps = set(profile.capabilities).intersection(peer.capabilities)
            if len(shared_imports) >= 2 or len(shared_caps) >= 2:
                deps.add(peer.name)
        profile.dependencies = sorted(deps)


def build_examples(profile: ToolProfile) -> list[str]:
    rel = profile.path
    if profile.path.suffix.lower() == ".md":
        return [f"# Documentation\nless {rel}"]

    if profile.category == "scripts":
        base = f"python -m scripts.{profile.name}"
    else:
        base = f"python {rel}"

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
        "Auto-generated capability and dependency analysis for `scripts/`, `tools/`, and `skills/`.",
        "",
        "## Summary",
        "",
        f"- Total entries discovered: **{len(profiles)}**",
        f"- High-risk entries: **{sum(1 for p in profiles if p.risk_level == 'high')}**",
        "",
    ]
    for profile in profiles:
        lines.extend(
            [
                f"## `{profile.name}`",
                "",
                f"- Category: `{profile.category}`",
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover and suggest utility scripts by capability.")
    parser.add_argument("--root", default=".", help="Repository root path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Run deep tool capability analysis")
    analyze.add_argument("--format", choices=("json", "text"), default="json")

    docs = subparsers.add_parser("docs", help="Generate Markdown tool documentation")
    docs.add_argument("--output", help="Write docs to this path instead of stdout")

    suggest = subparsers.add_parser("suggest", help="Suggest tools with contextual reasoning")
    suggest.add_argument("goal", help="User goal, e.g. 'monitor queue latency'")
    suggest.add_argument("--context", default="", help="Additional operational context")
    suggest.add_argument("--top", type=int, default=5, help="Number of tools to suggest")

    index_cmd = subparsers.add_parser("index", help="Write JSON searchable index (entries + keyword_index)")
    index_cmd.add_argument(
        "--output",
        default="tool_discovery_index.json",
        help="Output path for the JSON index (default: tool_discovery_index.json)",
    )

    search_cmd = subparsers.add_parser("search", help="Keyword search across discovered tools and skills")
    search_cmd.add_argument("query", nargs="+", help="Keywords or phrase to match")
    search_cmd.add_argument("--limit", type=int, default=20, help="Maximum number of results")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
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

    if args.command == "index":
        payload = build_search_index_payload(root, profiles)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return 0

    if args.command == "search":
        query_text = " ".join(args.query)
        payload = {
            "query": query_text,
            "results": search_by_keywords(profiles, query_text, limit=max(1, args.limit)),
        }
        print(json.dumps(payload, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
