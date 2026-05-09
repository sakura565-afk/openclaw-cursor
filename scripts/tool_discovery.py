#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


STOPWORDS = frozenset(
    """
    a an the and or for of to in on at by from with without into over under is are was were
    be been being as if then else when while do does did done not no yes so such this that these those
    it its we you they he she them their our your my any some all each every both few more most other
    such than too very can could should would may might must will shall also just only same own about
    into through during before after above below between own same few lot next same how what which who
    """.split()
)

INDEX_VERSION = 1

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
    kind: str = "script"
    imports: set[str] = field(default_factory=set)
    functions: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    risk_level: str = "low"
    io_profile: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "path": str(self.path),
            "kind": self.kind,
            "description": self.description,
            "imports": sorted(self.imports),
            "functions": self.functions,
            "commands": self.commands,
            "capabilities": self.capabilities,
            "risk_level": self.risk_level,
            "io_profile": self.io_profile,
            "dependencies": self.dependencies,
            "examples": self.examples,
            "keywords": self.keywords,
        }


def discover_python_files(root: Path, *relative_parts: str) -> list[Path]:
    """List top-level *.py modules under root/relative (excludes __init__.py)."""
    target = root.joinpath(*relative_parts)
    if not target.is_dir():
        return []
    return sorted(p for p in target.glob("*.py") if p.name != "__init__.py")


def _split_camel_and_snake(token: str) -> list[str]:
    if not token:
        return []
    step1 = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", token)
    parts = re.split(r"[_\s]+", step1)
    out: list[str] = []
    for p in parts:
        sub = re.findall(r"[A-Za-z][a-z]+|[A-Z]+(?=[A-Z][a-z]|\b)|\d+", p)
        if sub:
            out.extend(s.lower() for s in sub if len(s) > 1 or s.isdigit())
        elif len(p) > 1:
            out.append(p.lower())
    return out


def _tokenize_free_text(text: str) -> list[str]:
    return [m.group(0).lower() for m in re.finditer(r"[A-Za-z][A-Za-z0-9_-]{1,}", text)]


def extract_keywords(profile: ToolProfile) -> list[str]:
    bag: set[str] = set()
    bag.update(_split_camel_and_snake(profile.name))
    bag.update(_tokenize_free_text(profile.description))
    for cap in profile.capabilities:
        bag.update(w for w in _tokenize_free_text(cap) if w not in STOPWORDS)
    for cmd in profile.commands:
        bag.update(_split_camel_and_snake(cmd))
    for fn in profile.functions:
        bag.update(_split_camel_and_snake(fn))
    parts = Path(profile.path).parts
    for part in parts:
        stem = Path(part).stem if "." in part else part
        bag.update(_split_camel_and_snake(stem))
    bag.discard("")
    return sorted(bag)


def entry_id(profile: ToolProfile) -> str:
    return profile.path.as_posix()


def build_keyword_index(profiles: list[ToolProfile]) -> dict[str, list[str]]:
    """Inverted index: keyword -> sorted unique entry ids (posix relative paths)."""
    inverted: dict[str, set[str]] = defaultdict(set)
    for profile in profiles:
        eid = entry_id(profile)
        for kw in profile.keywords:
            inverted[kw].add(eid)
        inverted[profile.kind].add(eid)
        inverted[profile.name.lower()].add(eid)
    return {kw: sorted(ids) for kw, ids in sorted(inverted.items())}


def build_json_index(root: Path, profiles: list[ToolProfile]) -> dict[str, object]:
    root_s = root.resolve().as_posix()
    entries: list[dict[str, object]] = []
    for profile in profiles:
        d = profile.to_dict()
        d["id"] = entry_id(profile)
        entries.append(d)
    return {
        "version": INDEX_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": root_s,
        "entry_count": len(entries),
        "entries": entries,
        "keyword_index": build_keyword_index(profiles),
    }


def search_by_keywords(
    profiles: list[ToolProfile], query: str, top_n: int = 10
) -> list[dict[str, object]]:
    """Score profiles by overlap between query tokens and indexed keywords + names."""
    raw_tokens = _tokenize_free_text(query)
    query_tokens = [t.lower() for t in raw_tokens if t.lower() not in STOPWORDS]
    query_tokens.extend(_split_camel_and_snake(query.replace(" ", "_")))
    tokens = sorted(set(t for t in query_tokens if len(t) > 1))

    scored: list[tuple[int, float, ToolProfile, list[str]]] = []
    for profile in profiles:
        kw_set = set(profile.keywords)
        name_lower = profile.name.lower()
        reasons: list[str] = []
        score_i = 0
        partial = 0.0
        for t in tokens:
            if t in kw_set:
                score_i += 4
                reasons.append(f"keyword:{t}")
            elif t in name_lower:
                score_i += 6
                reasons.append(f"name:{t}")
            else:
                for kw in kw_set:
                    if t in kw or kw in t:
                        partial += 1.5
                        reasons.append(f"partial:{t}~{kw}")
                        break
                path_text = profile.path.as_posix().lower()
                if t in path_text:
                    partial += 1.0
                    reasons.append(f"path:{t}")
        total = score_i + partial
        if tokens and total == 0:
            continue
        if not tokens:
            total = 1
            reasons.append("empty query; full catalog")
        scored.append((score_i, total, profile, reasons))

    scored.sort(key=lambda row: (row[1], row[0], row[2].name), reverse=True)
    results: list[dict[str, object]] = []
    for score_i, total, profile, reasons in scored[:top_n]:
        results.append(
            {
                "id": entry_id(profile),
                "name": profile.name,
                "kind": profile.kind,
                "path": str(profile.path),
                "score": round(total, 3),
                "exact_keyword_hits": score_i,
                "matched": sorted(set(reasons))[:20],
            }
        )
    return results


def analyze_python_file(root: Path, path: Path, kind: str) -> ToolProfile | None:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        print(f"tool_discovery: skipping {path}: {exc}", file=sys.stderr)
        return None
    rel = path.relative_to(root)
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
        path=rel,
        description=description,
        kind=kind,
        imports=imports,
        functions=functions,
        commands=commands,
        capabilities=capabilities,
        risk_level=risk_level,
        io_profile=io_profile,
    )


def analyze_python_directory(root: Path, relative_dir: str, kind: str) -> list[ToolProfile]:
    profiles: list[ToolProfile] = []
    for path in discover_python_files(root, *relative_dir.split("/")):
        profile = analyze_python_file(root, path, kind)
        if profile:
            profiles.append(profile)
    return profiles


def finalize_profiles(profiles: list[ToolProfile]) -> None:
    enrich_dependency_graph(profiles)
    for profile in profiles:
        profile.keywords = extract_keywords(profile)
        profile.examples = build_examples(profile)


def collect_profiles(root: Path) -> list[ToolProfile]:
    """Scan scripts/, tools/, skills/, and src/skills/ for Python modules."""
    root = root.resolve()
    seen: set[Path] = set()
    merged: list[ToolProfile] = []
    for relative, kind in (
        ("scripts", "script"),
        ("tools", "tool"),
        ("skills", "skill"),
        ("src/skills", "skill"),
    ):
        for profile in analyze_python_directory(root, relative, kind):
            abs_path = root / profile.path
            try:
                key = abs_path.resolve()
            except OSError:
                key = abs_path
            if key in seen:
                continue
            seen.add(key)
            merged.append(profile)
    finalize_profiles(merged)
    return merged


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


def analyze_scripts(root: Path) -> list[ToolProfile]:
    """Analyze only ``scripts/*.py`` (used by tests and narrow tooling)."""
    root = root.resolve()
    profiles = analyze_python_directory(root, "scripts", "script")
    finalize_profiles(profiles)
    return profiles


def enrich_dependency_graph(profiles: list[ToolProfile]) -> None:
    by_name: dict[str, list[ToolProfile]] = defaultdict(list)
    for profile in profiles:
        by_name[profile.name].append(profile)

    for profile in profiles:
        deps: set[str] = set()
        for imported in profile.imports:
            for peer in by_name.get(imported, []):
                if peer.path != profile.path:
                    deps.add(peer.name)
        for peer in profiles:
            if peer.path == profile.path:
                continue
            shared_imports = profile.imports.intersection(peer.imports)
            shared_caps = set(profile.capabilities).intersection(peer.capabilities)
            if len(shared_imports) >= 2 or len(shared_caps) >= 2:
                deps.add(peer.name)
        profile.dependencies = sorted(deps)


def build_examples(profile: ToolProfile) -> list[str]:
    rel = profile.path.as_posix()
    if profile.kind == "script":
        base = f"python -m scripts.{profile.name}"
    elif profile.kind == "tool":
        base = f"python {rel}"
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
        "Auto-generated capability and dependency analysis for scripts/, tools/, and skills.",
        "",
        "## Summary",
        "",
        f"- Total entries discovered: **{len(profiles)}**",
        f"- High-risk tools: **{sum(1 for p in profiles if p.risk_level == 'high')}**",
        "",
    ]
    for profile in profiles:
        lines.extend(
            [
                f"## `{profile.name}` ({profile.kind})",
                "",
                f"- Path: `{profile.path}`",
                f"- Description: {profile.description}",
                f"- Risk level: **{profile.risk_level}**",
                f"- Capabilities: {', '.join(profile.capabilities)}",
                f"- I/O profile: {', '.join(profile.io_profile)}",
                f"- Dependencies: {', '.join(profile.dependencies) if profile.dependencies else 'none'}",
                (
                    f"- Keywords: {', '.join(profile.keywords[:24])}{'…' if len(profile.keywords) > 24 else ''}"
                    if profile.keywords
                    else "- Keywords: —"
                ),
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
    analyze.add_argument(
        "--scope",
        choices=("all", "scripts"),
        default="all",
        help="Discover scripts/ only, or scripts/, tools/, skills/, src/skills/",
    )

    index_cmd = subparsers.add_parser("index", help="Write searchable JSON index (entries + keyword_index)")
    index_cmd.add_argument("--output", "-o", help="Write JSON to this path instead of stdout")

    search_cmd = subparsers.add_parser("search", help="Keyword match against extracted metadata")
    search_cmd.add_argument("query", nargs="+", help="Search terms")
    search_cmd.add_argument("--top", type=int, default=15, help="Max results")

    docs = subparsers.add_parser("docs", help="Generate Markdown tool documentation")
    docs.add_argument("--output", help="Write docs to this path instead of stdout")

    suggest = subparsers.add_parser("suggest", help="Suggest tools with contextual reasoning")
    suggest.add_argument("goal", help="User goal, e.g. 'monitor queue latency'")
    suggest.add_argument("--context", default="", help="Additional operational context")
    suggest.add_argument("--top", type=int, default=5, help="Number of tools to suggest")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()

    if args.command == "analyze" and getattr(args, "scope", "all") == "scripts":
        profiles = analyze_scripts(root)
    elif args.command == "analyze":
        profiles = collect_profiles(root)
    else:
        profiles = collect_profiles(root)

    if args.command == "analyze":
        if args.format == "json":
            print(json.dumps([profile.to_dict() for profile in profiles], indent=2))
        else:
            for profile in profiles:
                print(f"{profile.name}: {', '.join(profile.capabilities)} | deps={profile.dependencies}")
        return 0

    if args.command == "index":
        payload = build_json_index(root, profiles)
        blob = json.dumps(payload, indent=2)
        out = getattr(args, "output", None)
        if out:
            dest = Path(out)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(blob, encoding="utf-8")
        else:
            print(blob)
        return 0

    if args.command == "search":
        query = " ".join(args.query)
        ranked = search_by_keywords(profiles, query, top_n=max(1, args.top))
        print(json.dumps({"query": query, "results": ranked}, indent=2))
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
