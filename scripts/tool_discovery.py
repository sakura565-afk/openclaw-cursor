#!/usr/bin/env python3
"""Scan scripts, tools/, skills (Python + SKILL.md), build a searchable JSON index, and suggest tools."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


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

INDEX_VERSION = 1

DEFAULT_SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".venv",
        "venv",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        "node_modules",
        "dist",
        "build",
        ".eggs",
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


def discover_script_paths(root: Path) -> list[Path]:
    scripts_dir = root / "scripts"
    if not scripts_dir.exists():
        return []
    return sorted(path for path in scripts_dir.glob("*.py") if path.name != "__init__.py")


def discover_tools_py_paths(root: Path) -> list[Path]:
    tools_dir = root / "tools"
    if not tools_dir.exists():
        return []
    return sorted(
        path
        for path in tools_dir.rglob("*.py")
        if path.name != "__init__.py" and not any(part in DEFAULT_SKIP_DIR_NAMES for part in path.parts)
    )


def discover_skill_module_paths(root: Path) -> list[Path]:
    skills_dir = root / "src" / "skills"
    if not skills_dir.exists():
        return []
    return sorted(
        path
        for path in skills_dir.glob("*.py")
        if path.name != "__init__.py"
    )


def discover_all_python_tool_paths(root: Path) -> list[Path]:
    return sorted({*discover_script_paths(root), *discover_tools_py_paths(root), *discover_skill_module_paths(root)})


def iter_skill_markdown_paths(
    root: Path,
    skip_dir_names: frozenset[str] = DEFAULT_SKIP_DIR_NAMES,
) -> list[Path]:
    found: list[Path] = []
    if not root.exists():
        return found
    for path in root.rglob("SKILL.md"):
        if any(part in skip_dir_names for part in path.parts):
            continue
        found.append(path.resolve())
    return sorted(set(found))


def _parse_simple_yaml_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Parse leading --- ... --- YAML subset (scalars and simple lists) without deps."""

    text = raw.lstrip("\ufeff")
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if len(lines) < 2 or lines[0].strip() != "---":
        return {}, text
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end = idx
            break
    if end is None:
        return {}, text
    block = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    meta: dict[str, Any] = {}
    key_re = re.compile(r"^([A-Za-z0-9_+-]+):\s*(.*)$")
    i = 0
    blines = block.splitlines()
    while i < len(blines):
        raw_line = blines[i]
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            i += 1
            continue
        match = key_re.match(raw_line.rstrip())
        if not match:
            i += 1
            continue
        key, rest = match.group(1), match.group(2).strip()
        if rest in {"", "|", ">"}:
            items: list[str] = []
            j = i + 1
            while j < len(blines):
                candidate = blines[j]
                stripped = candidate.strip()
                if stripped.startswith("- "):
                    items.append(stripped[2:].strip().strip('"').strip("'"))
                    j += 1
                    continue
                if not stripped:
                    j += 1
                    continue
                if candidate[0] in " \t" and not stripped.startswith("- "):
                    j += 1
                    continue
                break
            if items:
                meta[key] = items
                i = j
                continue
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1]
            meta[key] = [p.strip().strip("'\"") for p in inner.split(",") if p.strip()]
        else:
            meta[key] = rest.strip('"').strip("'")
        i += 1
    return meta, body


def _skill_slug(rel: Path) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", rel.as_posix().lower())
    return cleaned.strip("-") or "skill"


def parse_skill_markdown(path: Path, root: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    metadata, body = _parse_simple_yaml_frontmatter(raw)
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        rel = path

    name_raw = metadata.get("name") or metadata.get("title")
    name = str(name_raw).strip() if name_raw else path.parent.name or path.stem

    description = str(metadata.get("description", "")).strip()
    if not description:
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
        description = paragraphs[0].replace("\n", " ") if paragraphs else "No description provided."

    actions = metadata.get("actions")
    action_list: list[str]
    if isinstance(actions, list):
        action_list = [str(a).strip() for a in actions if str(a).strip()]
    elif isinstance(actions, str) and actions.strip():
        action_list = [line.strip(" -*\t") for line in actions.splitlines() if line.strip()]
    else:
        action_list = []

    caps = metadata.get("capabilities")
    cap_list: list[str]
    if isinstance(caps, list):
        cap_list = [str(c).strip() for c in caps if str(c).strip()]
    elif isinstance(caps, str) and caps.strip():
        cap_list = [caps.strip()]
    else:
        cap_list = []

    skill_id = str(metadata.get("id", "")).strip() or _skill_slug(rel)

    return {
        "skill_id": skill_id,
        "path": str(rel),
        "name": name,
        "description": description,
        "actions": action_list,
        "capabilities": cap_list,
        "metadata": {k: v for k, v in metadata.items() if k not in {"name", "title", "description", "actions", "capabilities", "id"}},
    }


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


def _python_tool_kind(rel_path: Path) -> str:
    parts = rel_path.parts
    if parts and parts[0] == "scripts":
        return "script"
    if parts and parts[0] == "tools":
        return "tool"
    if "skills" in parts:
        return "skill_module"
    return "python_module"


def analyze_python_file(path: Path, root: Path) -> ToolProfile:
    rel = path.resolve().relative_to(root.resolve())
    try:
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError) as exc:
        return ToolProfile(
            name=path.stem,
            path=rel,
            description=f"Could not parse Python source: {exc}",
            imports=set(),
            functions=[],
            commands=[],
            capabilities=["General utility automation"],
            risk_level="low",
            io_profile=["in-memory"],
        )
    name = path.stem
    imports = extract_imports(tree)
    functions = extract_functions(tree)
    commands = extract_cli_commands(tree)
    description = extract_description(tree)
    capabilities = infer_capabilities(name, description, commands, functions)
    io_profile = infer_io_profile(imports, source)
    risk_level = infer_risk_level(imports, commands)
    return ToolProfile(
        name=name,
        path=path.resolve().relative_to(root.resolve()),
        description=description,
        imports=imports,
        functions=functions,
        commands=commands,
        capabilities=capabilities,
        risk_level=risk_level,
        io_profile=io_profile,
    )


def analyze_python_tools(root: Path, paths: Iterable[Path]) -> list[ToolProfile]:
    root = root.resolve()
    profiles = [analyze_python_file(Path(p).resolve(), root) for p in paths]
    enrich_dependency_graph(profiles)
    for profile in profiles:
        profile.examples = build_examples(profile)
    return profiles


def analyze_scripts(root: Path) -> list[ToolProfile]:
    return analyze_python_tools(root, discover_script_paths(root))


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
    parts = profile.path.parts
    if parts and parts[0] == "scripts":
        base = f"python -m scripts.{profile.name}"
    else:
        base = f"python {profile.path.as_posix()}"
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


def _query_tokens(query: str) -> list[str]:
    return [t for t in re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", query.lower()) if len(t) >= 2]


def _indexable_chunks_for_profile(profile: ToolProfile) -> list[str]:
    chunks: list[str] = [
        profile.name,
        profile.description,
        str(profile.path),
        *profile.capabilities,
        *profile.commands,
        *profile.functions,
        *profile.io_profile,
        profile.risk_level,
        *profile.dependencies,
        *sorted(profile.imports),
    ]
    return [c.lower() for c in chunks if c]


def build_full_index(root: Path) -> dict[str, Any]:
    root = root.resolve()
    py_paths = discover_all_python_tool_paths(root)
    profiles = analyze_python_tools(root, py_paths)
    entries: list[dict[str, Any]] = []
    for profile in profiles:
        kind = _python_tool_kind(profile.path)
        entry_id = f"{kind}:{profile.path.as_posix()}"
        profile_dict = profile.to_dict()
        search_text = " ".join(_indexable_chunks_for_profile(profile))
        entries.append(
            {
                "id": entry_id,
                "kind": kind,
                "name": profile.name,
                "path": profile_dict["path"],
                "description": profile.description,
                "search_text": search_text,
                "profile": profile_dict,
            }
        )

    for md_path in iter_skill_markdown_paths(root):
        skill = parse_skill_markdown(md_path, root)
        rel = str(skill["path"])
        entry_id = f"skill_markdown:{rel}"
        parts = [
            str(skill["skill_id"]),
            skill["name"],
            skill["description"],
            rel,
            *skill["actions"],
            *skill["capabilities"],
        ]
        search_text = " ".join(p.lower() for p in parts if p)
        entries.append(
            {
                "id": entry_id,
                "kind": "skill_markdown",
                "name": skill["name"],
                "path": rel,
                "description": skill["description"],
                "search_text": search_text,
                "skill": skill,
            }
        )

    keyword_index: dict[str, list[str]] = {}
    for entry in entries:
        tokens = set(re.findall(r"[a-z0-9]{2,}", entry["search_text"]))
        for tok in tokens:
            keyword_index.setdefault(tok, []).append(entry["id"])
    for tok, ids in keyword_index.items():
        keyword_index[tok] = sorted(set(ids))

    return {
        "version": INDEX_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "entries": entries,
        "keyword_index": keyword_index,
    }


def load_index(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "entries" not in data:
        raise ValueError("Index file must be a JSON object with an 'entries' array.")
    return data


def search_index(data: dict[str, Any], query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    tokens = _query_tokens(query)
    if not tokens:
        return []
    limit = max(1, limit)
    ranked: list[tuple[int, dict[str, Any]]] = []
    for entry in data["entries"]:
        haystack = entry.get("search_text", "")
        score = 0
        for tok in tokens:
            if tok in haystack:
                score += haystack.count(tok) * 2 + 3
        if score:
            ranked.append((score, entry))
    ranked.sort(key=lambda row: (-row[0], str(row[1].get("name", "")).lower()))
    out: list[dict[str, Any]] = []
    for sc, ent in ranked[:limit]:
        slim = {k: v for k, v in ent.items() if k != "search_text"}
        slim["match_score"] = sc
        out.append(slim)
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover scripts, tools/, and skills; build a JSON index; search and suggest by capability.",
    )
    parser.add_argument("--root", default=".", help="Repository root path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Run deep tool capability analysis (scripts/ only)")
    analyze.add_argument("--format", choices=("json", "text"), default="json")

    docs = subparsers.add_parser("docs", help="Generate Markdown tool documentation (scripts/ only)")
    docs.add_argument("--output", help="Write docs to this path instead of stdout")

    suggest = subparsers.add_parser("suggest", help="Suggest tools with contextual reasoning (scripts/ only)")
    suggest.add_argument("goal", help="User goal, e.g. 'monitor queue latency'")
    suggest.add_argument("--context", default="", help="Additional operational context")
    suggest.add_argument("--top", type=int, default=5, help="Number of tools to suggest")

    index_cmd = subparsers.add_parser(
        "index",
        help="Scan scripts/, tools/, src/skills/, and SKILL.md files; emit searchable JSON index",
    )
    index_cmd.add_argument(
        "-o",
        "--output",
        help="Output JSON path (default: <root>/tool_discovery_index.json)",
    )
    index_cmd.add_argument(
        "--stdout",
        action="store_true",
        help="Print JSON to stdout instead of writing a file",
    )

    search_cmd = subparsers.add_parser("search", help="Keyword search the discovery index")
    search_cmd.add_argument("query", help="Keywords or phrase to match against the index")
    search_cmd.add_argument(
        "--index",
        dest="index_path",
        help="Path to index JSON (default: rebuild from --root)",
    )
    search_cmd.add_argument("--limit", type=int, default=20, help="Maximum number of matches")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()

    if args.command == "index":
        payload = build_full_index(root)
        if args.stdout:
            print(json.dumps(payload, indent=2))
        else:
            out = Path(args.output).resolve() if args.output else root / "tool_discovery_index.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(
                f"Wrote index with {len(payload['entries'])} entries to {out}",
                file=sys.stderr,
            )
        return 0

    if args.command == "search":
        if args.index_path:
            data = load_index(Path(args.index_path).resolve())
        else:
            data = build_full_index(root)
        matches = search_index(data, args.query, limit=max(1, args.limit))
        print(json.dumps({"query": args.query, "matches": matches}, indent=2))
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
