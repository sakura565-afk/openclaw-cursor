#!/usr/bin/env python3
"""Discover, index, and search tools, skills, and scripts in the OpenClaw workspace.

This module scans the repository for executable helpers (scripts/), agent skills
(src/skills/), and library-style tools (remaining Python under src/). It writes a
searchable JSON index with usage metadata, supports lightweight semantic ranking
(token overlap + fuzzy similarity), and tracks invocation counts when callers
report usage via ``--record``.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable


# --- Constants -----------------------------------------------------------------

INDEX_VERSION = 2
DEFAULT_INDEX_REL = Path("scripts/tool_index.json")

# Capability markers reused when enriching script profiles (legacy / suggestions).
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

TOKEN_RE = re.compile(r"[a-z0-9]+(?:[/._-][a-z0-9]+)*", re.IGNORECASE)


# --- Data classes --------------------------------------------------------------

@dataclass
class ToolProfile:
    """Structured analysis for a single script under ``scripts/`` (legacy API)."""

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
class DiscoveredItem:
    """One indexed artifact (script, skill, or src tool module)."""

    kind: str  # "script" | "skill" | "tool"
    name: str
    path: Path  # relative to workspace root
    description: str
    frequency: int = 0
    last_used: str | None = None  # ISO 8601 UTC
    keywords: list[str] = field(default_factory=list)

    def search_blob(self) -> str:
        parts = [
            self.kind,
            self.name,
            str(self.path).replace("\\", "/"),
            self.description,
            " ".join(self.keywords),
        ]
        return " ".join(parts)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _tokenize(text: str) -> list[str]:
    return [m.group(0).lower() for m in TOKEN_RE.finditer(text)]


def _keyword_from_path(path: Path) -> list[str]:
    stem = path.stem.replace("_", " ").replace("-", " ").split()
    parent = path.parent.name if path.parent != Path(".") else ""
    out = [w.lower() for w in stem if len(w) > 1]
    if parent and parent not in ("scripts", "skills", "src"):
        out.extend(_tokenize(parent))
    return sorted(set(out))


# --- Python / shell description extraction ------------------------------------

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


def _shell_header_description(path: Path, max_lines: int = 40) -> str:
    lines: list[str] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "Shell script (could not read file)."
    for line in raw.splitlines()[:max_lines]:
        stripped = line.strip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            comment = stripped.lstrip("#").strip()
            if comment:
                lines.append(comment)
        elif stripped and not stripped.startswith("#"):
            break
    if lines:
        return lines[0][:500]
    return "Shell script."


def _python_first_line_description(path: Path) -> str:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, OSError):
        return "Python module (parse failed)."
    return extract_description(tree)


# --- Discovery -----------------------------------------------------------------

def discover_script_paths(root: Path) -> list[Path]:
    scripts_dir = root / "scripts"
    if not scripts_dir.exists():
        return []
    paths: list[Path] = []
    for pattern in ("*.py", "*.sh"):
        paths.extend(scripts_dir.glob(pattern))
    return sorted(p for p in paths if p.name != "__init__.py")


def discover_skill_paths(root: Path) -> list[Path]:
    skills_dir = root / "src" / "skills"
    if not skills_dir.exists():
        return []
    return sorted(p for p in skills_dir.glob("*.py") if p.name != "__init__.py")


def discover_src_tool_paths(root: Path) -> list[Path]:
    """Python modules under ``src/`` excluding the ``skills`` package."""
    src = root / "src"
    if not src.exists():
        return []
    out: list[Path] = []
    for path in src.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        try:
            rel = path.relative_to(src)
        except ValueError:
            continue
        parts = rel.parts
        if parts and parts[0] == "skills":
            continue
        out.append(path)
    return sorted(out)


def discover_root_python_tools(root: Path) -> list[Path]:
    """Top-level ``*.py`` helpers at repo root (e.g. dashboards)."""
    return sorted(p for p in root.glob("*.py") if p.is_file())


def _merge_usage(
    previous_by_path: dict[str, dict[str, Any]], path_str: str
) -> tuple[int, str | None]:
    prev = previous_by_path.get(path_str)
    if not prev:
        return 0, None
    freq = int(prev.get("frequency") or 0)
    last = prev.get("last_used")
    if isinstance(last, str):
        return freq, last
    return freq, None


def scan_workspace(root: Path, previous_index: dict[str, Any] | None) -> list[DiscoveredItem]:
    """Walk the workspace and build fresh metadata, optionally preserving usage."""
    prev_items = (previous_index or {}).get("items") or []
    prev_map = {
        str(it.get("path", "")).replace("\\", "/"): it
        for it in prev_items
        if isinstance(it, dict) and it.get("path")
    }

    discovered: list[DiscoveredItem] = []

    # Scripts (project automation entrypoints).
    for path in discover_script_paths(root):
        rel = path.relative_to(root)
        ps = str(rel).replace("\\", "/")
        freq, last_used = _merge_usage(prev_map, ps)
        if path.suffix == ".py":
            desc = _python_first_line_description(path)
        else:
            desc = _shell_header_description(path)
        keywords = sorted(set(_keyword_from_path(path) + _tokenize(desc)[:12]))
        discovered.append(
            DiscoveredItem(
                kind="script",
                name=path.stem,
                path=rel,
                description=desc,
                frequency=freq,
                last_used=last_used,
                keywords=keywords,
            )
        )

    # Agent skills.
    for path in discover_skill_paths(root):
        rel = path.relative_to(root)
        ps = str(rel).replace("\\", "/")
        freq, last_used = _merge_usage(prev_map, ps)
        desc = _python_first_line_description(path)
        keywords = sorted(set(_keyword_from_path(path) + _tokenize(desc)[:12] + ["skill"]))
        discovered.append(
            DiscoveredItem(
                kind="skill",
                name=path.stem,
                path=rel,
                description=desc,
                frequency=freq,
                last_used=last_used,
                keywords=keywords,
            )
        )

    # Library / orchestration modules under src/ (excluding skills/).
    for path in discover_src_tool_paths(root):
        rel = path.relative_to(root)
        ps = str(rel).replace("\\", "/")
        freq, last_used = _merge_usage(prev_map, ps)
        desc = _python_first_line_description(path)
        keywords = sorted(set(_keyword_from_path(path) + _tokenize(desc)[:12]))
        discovered.append(
            DiscoveredItem(
                kind="tool",
                name=path.stem,
                path=rel,
                description=desc,
                frequency=freq,
                last_used=last_used,
                keywords=keywords,
            )
        )

    # Repo-root Python utilities.
    for path in discover_root_python_tools(root):
        rel = path.relative_to(root)
        ps = str(rel).replace("\\", "/")
        freq, last_used = _merge_usage(prev_map, ps)
        desc = _python_first_line_description(path)
        keywords = sorted(set(_keyword_from_path(path) + _tokenize(desc)[:12] + ["root"]))
        discovered.append(
            DiscoveredItem(
                kind="tool",
                name=path.stem,
                path=rel,
                description=desc,
                frequency=freq,
                last_used=last_used,
                keywords=keywords,
            )
        )

    # Stable ordering for deterministic JSON diffs.
    discovered.sort(key=lambda x: (x.kind, str(x.path)))
    return discovered


def item_to_record(item: DiscoveredItem) -> dict[str, Any]:
    """Serialize one ``DiscoveredItem`` for ``tool_index.json``."""
    search_text = item.search_blob()
    return {
        "kind": item.kind,
        "name": item.name,
        "path": str(item.path).replace("\\", "/"),
        "description": item.description,
        "last_used": item.last_used,
        "frequency": item.frequency,
        "keywords": item.keywords,
        "search_text": search_text,
    }


def build_index_payload(root: Path, items: Iterable[DiscoveredItem]) -> dict[str, Any]:
    root_resolved = root.resolve()
    return {
        "version": INDEX_VERSION,
        "generated_at": _utc_now_iso(),
        "root": str(root_resolved),
        "items": [item_to_record(it) for it in items],
    }


def load_index(index_path: Path) -> dict[str, Any]:
    if not index_path.is_file():
        return {}
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_index(index_path: Path, payload: dict[str, Any]) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


# --- Search scoring ------------------------------------------------------------

def _document_frequency(items: list[dict[str, Any]]) -> Counter[str]:
    df: Counter[str] = Counter()
    for item in items:
        toks = set(_tokenize(item.get("search_text", "")))
        for t in toks:
            df[t] += 1
    return df


def idf_weight(term: str, df: Counter[str], n_docs: int) -> float:
    """Classic inverse document frequency with smoothing."""
    count = df.get(term, 0)
    # Smooth: treat absent terms as if they appeared in one doc.
    return math.log((n_docs + 1) / (count + 1)) + 1.0


def keyword_match_score(query: str, item: dict[str, Any]) -> float:
    """Lexical match: token overlap with emphasis on name/path hits."""
    q_tokens = _tokenize(query)
    if not q_tokens:
        return 0.0
    text = (item.get("search_text") or "").lower()
    name = (item.get("name") or "").lower()
    path_s = (item.get("path") or "").lower()
    score = 0.0
    for t in q_tokens:
        if t in name:
            score += 4.0
        elif t in path_s:
            score += 2.5
        elif t in text:
            score += 1.0
    # Phrase bonus.
    ql = query.lower().strip()
    if len(ql) > 2 and ql in text:
        score += 3.0
    return score


def semantic_match_score(query: str, item: dict[str, Any], df: Counter[str], n_docs: int) -> float:
    """Soft 'semantic' ranking without embeddings: TF-IDF weighted overlap + fuzzy ratio."""
    q_tokens = _tokenize(query)
    doc_tokens = _tokenize(item.get("search_text", ""))
    if not q_tokens or not doc_tokens:
        return 0.0

    doc_counter = Counter(doc_tokens)
    dot = 0.0
    qw = 0.0
    for qt in q_tokens:
        wq = idf_weight(qt, df, n_docs)
        qw += wq * wq
        dot += wq * wq * doc_counter.get(qt, 0) / max(len(doc_tokens), 1)

    sim_ratio = SequenceMatcher(None, query.lower(), (item.get("search_text") or "").lower()).ratio()
    norm = math.sqrt(max(qw, 1e-9))
    tfidf_component = dot / norm
    return 0.65 * tfidf_component + 0.35 * (sim_ratio * 5.0)


def rank_search_results(
    query: str,
    items: list[dict[str, Any]],
    *,
    keyword_only: bool,
    limit: int = 25,
) -> list[tuple[float, dict[str, Any]]]:
    if not items:
        return []
    df = _document_frequency(items)
    n_docs = max(len(items), 1)
    ranked: list[tuple[float, dict[str, Any]]] = []
    for item in items:
        if keyword_only:
            score = keyword_match_score(query, item)
        else:
            score = 0.45 * keyword_match_score(query, item) + 0.55 * semantic_match_score(
                query, item, df, n_docs
            )
        ranked.append((score, item))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked[:limit]


# --- Usage statistics ----------------------------------------------------------

def record_usage(index_path: Path, path_or_name: str, root: Path | None) -> dict[str, Any]:
    """Increment ``frequency`` and set ``last_used`` for a matching index row."""
    data = load_index(index_path)
    items = list(data.get("items") or [])
    if not items:
        return {"ok": False, "error": "index missing or empty; run --refresh first"}

    target = path_or_name.strip().replace("\\", "/")
    matched: dict[str, Any] | None = None
    for it in items:
        p = str(it.get("path", "")).replace("\\", "/")
        if p == target or p.endswith(target) or it.get("name") == target:
            matched = it
            break

    if matched is None and root is not None:
        # Try resolving as relative to workspace.
        candidate = (root / target).resolve()
        for it in items:
            try:
                ip = (root / str(it.get("path", ""))).resolve()
                if ip == candidate:
                    matched = it
                    break
            except OSError:
                continue

    if matched is None:
        return {"ok": False, "error": f"no index entry for {path_or_name!r}"}

    matched["frequency"] = int(matched.get("frequency") or 0) + 1
    matched["last_used"] = _utc_now_iso()
    data["items"] = items
    data["generated_at"] = _utc_now_iso()
    save_index(index_path, data)
    return {"ok": True, "path": matched.get("path"), "frequency": matched["frequency"]}


def format_stats_report(data: dict[str, Any]) -> str:
    items = [it for it in (data.get("items") or []) if isinstance(it, dict)]
    lines = [
        "Tool discovery — usage statistics",
        f"Index version: {data.get('version', '?')}",
        f"Generated at: {data.get('generated_at', '?')}",
        f"Workspace root: {data.get('root', '?')}",
        "",
        f"Total entries: {len(items)}",
    ]
    by_kind: Counter[str] = Counter()
    total_freq = 0
    for it in items:
        by_kind[str(it.get("kind", "unknown"))] += 1
        total_freq += int(it.get("frequency") or 0)
    lines.append(f"Total invocations recorded: {total_freq}")
    lines.append("")
    lines.append("By kind:")
    for kind, n in sorted(by_kind.items()):
        lines.append(f"  {kind}: {n}")
    lines.append("")

    # Top by frequency.
    by_freq = sorted(items, key=lambda x: int(x.get("frequency") or 0), reverse=True)[:15]
    lines.append("Most used (frequency):")
    for it in by_freq:
        freq = int(it.get("frequency") or 0)
        if freq == 0:
            break
        lines.append(f"  {freq:5d}  [{it.get('kind')}] {it.get('path')}")
    if not any(int(it.get("frequency") or 0) for it in by_freq):
        lines.append("  (no usage recorded yet — use --record after runs)")

    lines.append("")
    # Recently used.
    with_dates = [it for it in items if it.get("last_used")]
    with_dates.sort(key=lambda x: str(x.get("last_used")), reverse=True)
    lines.append("Recently used (last_used):")
    for it in with_dates[:15]:
        lines.append(f"  {it.get('last_used')}  [{it.get('kind')}] {it.get('path')}")
    if not with_dates:
        lines.append("  (none)")

    return "\n".join(lines) + "\n"


# --- Legacy script analysis (tests & downstream tooling) -----------------------

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
    profiles: list[ToolProfile] = []
    for path in discover_script_paths(root):
        if path.suffix != ".py":
            continue
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
    if "filesystem" in profile.io_profile and any(
        word in combined_text for word in ("file", "context", "split", "log")
    ):
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


# --- CLI -----------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Discover tools, skills, and scripts; maintain scripts/tool_index.json; "
            "search and report usage."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --refresh\n"
            "  %(prog)s --search \"obsidian sync\"\n"
            "  %(prog)s --search queue --keyword-only\n"
            "  %(prog)s --stats\n"
            "  %(prog)s --record scripts/memory_analytics.py\n"
        ),
    )
    parser.add_argument("--root", default=".", help="Repository root (OpenClaw workspace)")
    parser.add_argument(
        "--index",
        default=None,
        help=f"Path to JSON index (default: <root>/{DEFAULT_INDEX_REL.as_posix()})",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Scan workspace and rewrite the JSON index (preserves usage fields)",
    )
    parser.add_argument("--search", metavar="QUERY", help="Query the index (semantic + keyword ranking)")
    parser.add_argument(
        "--keyword-only",
        action="store_true",
        help="Restrict --search to lexical / keyword matching",
    )
    parser.add_argument("--stats", action="store_true", help="Print usage statistics from the index")
    parser.add_argument(
        "--record",
        metavar="PATH_OR_NAME",
        help="Record one invocation for an indexed path or tool name",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Max rows for --search (default: 25)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON for --search / --stats / --record",
    )
    # Legacy subcommands (optional) for backward compatibility.
    subparsers = parser.add_subparsers(dest="legacy_command", required=False)
    legacy_analyze = subparsers.add_parser("analyze", help="Deep capability analysis (scripts/*.py only)")
    legacy_analyze.add_argument("--format", choices=("json", "text"), default="json")
    legacy_docs = subparsers.add_parser("docs", help="Generate Markdown documentation")
    legacy_docs.add_argument("--output", help="Write docs to this path instead of stdout")
    legacy_suggest = subparsers.add_parser("suggest", help="Suggest tools with contextual reasoning")
    legacy_suggest.add_argument("goal", help="User goal")
    legacy_suggest.add_argument("--context", default="", help="Additional context")
    legacy_suggest.add_argument("--top", type=int, default=5)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    index_path = Path(args.index) if args.index else (root / DEFAULT_INDEX_REL)

    # Legacy command paths (keep tests passing).
    if getattr(args, "legacy_command", None):
        profiles = analyze_scripts(root)
        if args.legacy_command == "analyze":
            if args.format == "json":
                print(json.dumps([profile.to_dict() for profile in profiles], indent=2))
            else:
                for profile in profiles:
                    print(f"{profile.name}: {', '.join(profile.capabilities)} | deps={profile.dependencies}")
            return 0
        if args.legacy_command == "docs":
            markdown = generate_markdown(profiles)
            if args.output:
                output = Path(args.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(markdown, encoding="utf-8")
            else:
                print(markdown)
            return 0
        if args.legacy_command == "suggest":
            payload = {
                "goal": args.goal,
                "context": args.context,
                "suggestions": suggest_tools(
                    profiles, goal=args.goal, context=args.context, top_n=max(1, args.top)
                ),
            }
            print(json.dumps(payload, indent=2))
            return 0

    # Primary workflow.
    if args.record:
        result = record_usage(index_path, args.record, root)
        if args.json:
            print(json.dumps(result, indent=2))
        elif result.get("ok"):
            print(f"Recorded usage for {result.get('path')} (frequency={result.get('frequency')})")
        else:
            print(result.get("error", "error"), file=sys.stderr)
            return 1
        return 0 if result.get("ok") else 1

    if args.refresh:
        previous = load_index(index_path)
        items = scan_workspace(root, previous)
        payload = build_index_payload(root, items)
        save_index(index_path, payload)
        if args.json:
            print(json.dumps({"ok": True, "written": str(index_path), "count": len(items)}, indent=2))
        else:
            print(f"Wrote {len(items)} entries to {index_path}")
        return 0

    if args.search is not None:
        data = load_index(index_path)
        items_raw = data.get("items") or []
        items = [it for it in items_raw if isinstance(it, dict)]
        if not items:
            print("Index empty or missing; run with --refresh first.", file=sys.stderr)
            return 1
        ranked = rank_search_results(
            args.search,
            items,
            keyword_only=args.keyword_only,
            limit=max(1, args.limit),
        )
        if args.json:
            out = [
                {"score": round(score, 4), **{k: it[k] for k in it if k != "search_text"}}
                for score, it in ranked
            ]
            print(json.dumps({"query": args.search, "results": out}, indent=2))
        else:
            print(f'Search: "{args.search}" ({len(ranked)} hits)\n')
            for score, it in ranked:
                freq = int(it.get("frequency") or 0)
                lu = it.get("last_used") or "never"
                print(f"  [{it.get('kind')}] {it.get('path')}")
                print(f"    score={score:.3f}  uses={freq}  last_used={lu}")
                desc = (it.get("description") or "")[:120]
                if desc:
                    print(f"    {desc}")
        return 0

    if args.stats:
        data = load_index(index_path)
        if not data.get("items"):
            print("Index empty or missing; run with --refresh first.", file=sys.stderr)
            return 1
        if args.json:
            items = [it for it in data["items"] if isinstance(it, dict)]
            total_freq = sum(int(it.get("frequency") or 0) for it in items)
            by_kind: Counter[str] = Counter(str(it.get("kind", "?")) for it in items)
            print(
                json.dumps(
                    {
                        "total_entries": len(items),
                        "total_frequency": total_freq,
                        "by_kind": dict(by_kind),
                        "items": items,
                    },
                    indent=2,
                )
            )
        else:
            sys.stdout.write(format_stats_report(data))
        return 0

    # Default: refresh when no explicit action (convenient for automation).
    previous = load_index(index_path)
    items = scan_workspace(root, previous)
    payload = build_index_payload(root, items)
    save_index(index_path, payload)
    print(f"Wrote {len(items)} entries to {index_path} (default refresh)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
