#!/usr/bin/env python3
"""Discover, index, and report on OpenClaw-related tools and workspace Python scripts."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

# ---------------------------------------------------------------------------
# Legacy capability heuristics (analyze / suggest / docs commands)
# ---------------------------------------------------------------------------

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

SKIP_DIR_NAMES = frozenset(
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
        ".cursor",
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


def _load_repo_workflow_tool_discovery() -> Any:
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
# Scan / index: paths, AST metadata, documentation, caching
# ---------------------------------------------------------------------------

ToolKind = Literal["script", "skill", "src_cli", "root_module", "package_module"]


def _utc_mtime_iso(path: Path) -> str:
    ts = path.stat().st_mtime
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iter_py_files(base: Path) -> Iterator[Path]:
    if not base.is_dir():
        return
    for path in sorted(base.rglob("*.py")):
        if path.name == "__pycache__":
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        yield path


def _module_id_for_file(repo: Path, py_file: Path) -> str | None:
    try:
        rel = py_file.resolve().relative_to(repo.resolve())
    except ValueError:
        return None
    parts = rel.parts
    if not parts:
        return None
    if rel.suffix != ".py":
        return None
    return ".".join(parts[:-1] + (rel.stem,))


def _file_has_main_guard(py_file: Path) -> bool:
    try:
        text = py_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "if __name__" in text and "__main__" in text


def discover_scan_paths(repo_root: Path) -> list[Path]:
    """Collect Python files under scripts/, skills/, src/skills/, and other workspace code dirs."""

    root = repo_root.resolve()
    found: dict[str, Path] = {}

    def add(p: Path) -> None:
        key = str(p.resolve())
        found.setdefault(key, p)

    scripts = root / "scripts"
    for p in _iter_py_files(scripts):
        if p.name == "__init__.py":
            continue
        add(p)

    skills = root / "skills"
    for p in _iter_py_files(skills):
        if p.name == "__init__.py":
            continue
        add(p)

    src_skills = root / "src" / "skills"
    for p in _iter_py_files(src_skills):
        if p.name == "__init__.py":
            continue
        add(p)

    src = root / "src"
    if src.is_dir():
        for p in sorted(src.rglob("*.py")):
            if any(part in SKIP_DIR_NAMES for part in p.parts):
                continue
            if p.name == "__init__.py":
                continue
            if _file_has_main_guard(p):
                add(p)

    for p in sorted(root.glob("*.py")):
        if p.is_file():
            add(p)

    for sub in sorted(root.iterdir()):
        if not sub.is_dir() or sub.name.startswith(".") or sub.name in SKIP_DIR_NAMES:
            continue
        if sub.name in {"scripts", "src", "tests", "docs", "examples", "blog", "memory", "logs", "seo"}:
            continue
        for p in _iter_py_files(sub):
            if p.name == "__init__.py":
                continue
            add(p)

    return sorted(found.values(), key=lambda x: str(x).lower())


def _classify_kind(repo: Path, path: Path) -> ToolKind:
    rel = path.resolve().relative_to(repo.resolve())
    parts = rel.parts
    if not parts:
        return "package_module"
    if parts[0] == "scripts":
        return "script"
    if parts[0] == "skills" or (parts[0] == "src" and len(parts) > 1 and parts[1] == "skills"):
        return "skill"
    if parts[0] == "src" and _file_has_main_guard(path):
        return "src_cli"
    if len(parts) == 1:
        return "root_module"
    return "package_module"


def _unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    if hasattr(ast, "unparse"):
        return ast.unparse(node)
    return "..."


def _function_signature_line(node: ast.FunctionDef) -> str:
    """Single-line signature for display and stable fingerprinting."""

    if hasattr(ast, "unparse"):
        line = ast.unparse(node).split("\n", 1)[0].strip()
        if len(line) > 400:
            return line[:397] + "…"
        return line
    ret = _unparse(node.returns) if node.returns else ""
    return f"def {node.name}(…){' -> ' + ret if ret else ''}"


def _public_module_functions(tree: ast.Module) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        sig = _function_signature_line(node)
        doc = ast.get_docstring(node)
        first_doc = (doc or "").strip().splitlines()[0] if doc else ""
        out.append({"name": node.name, "signature": sig, "doc": first_doc})
    return sorted(out, key=lambda x: x["name"])


def _module_purpose(tree: ast.Module, source: str) -> str:
    doc = ast.get_docstring(tree)
    if doc:
        lines = [ln.strip() for ln in doc.strip().splitlines() if ln.strip()]
        return " ".join(lines[:3])[:500]
    for ln in source.splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith("#!"):
            continue
        if s.startswith("from __future__"):
            continue
        if re.match(r"^(?:import|from)\s", s):
            continue
        if s in {'"""', "'''"} or s.startswith(('"""', "'''")):
            continue
        return (s[:240] + "…") if len(s) > 240 else s
    return "No description available."


def _fingerprint_for_tool(funcs: list[dict[str, str]], commands: list[str]) -> str:
    payload = json.dumps({"functions": funcs, "commands": commands}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _extract_cli_commands_from_source(tree: ast.AST) -> list[str]:
    return extract_cli_commands(tree)


def parse_doc_generator_script_names(readme: Path) -> set[str]:
    """Parse ``docs/README.md`` script index table for documented ``*.py`` stems."""

    if not readme.is_file():
        return set()
    text = readme.read_text(encoding="utf-8", errors="replace")
    start = "<!-- doc-generator:start -->"
    end = "<!-- doc-generator:end -->"
    if start in text and end in text:
        text = text.split(start, 1)[1].split(end, 1)[0]
    names: set[str] = set()
    for m in re.finditer(r"\[([a-zA-Z0-9_]+\.py)\]", text):
        names.add(m.group(1))
    for m in re.finditer(r"`(scripts/[a-zA-Z0-9_/]+\.py)`", text):
        names.add(Path(m.group(1)).name)
    return names


@dataclass
class IndexedTool:
    tool: str
    path: str
    purpose: str
    last_updated: str
    kind: ToolKind
    module_id: str | None
    has_main: bool
    public_functions: list[dict[str, str]]
    cli_commands: list[str]
    fingerprint: str
    documented_on_surfaces: list[str]
    in_doc_generator_table: bool
    missing_from_operator_docs: bool

    def as_row(self) -> dict[str, str | bool | list[str]]:
        return {
            "tool": self.tool,
            "path": self.path,
            "purpose": self.purpose,
            "last_updated": self.last_updated,
            "kind": self.kind,
            "module_id": self.module_id or "",
            "has_main": self.has_main,
            "public_functions": self.public_functions,
            "cli_commands": self.cli_commands,
            "fingerprint": self.fingerprint,
            "documented_on_surfaces": self.documented_on_surfaces,
            "in_doc_generator_table": self.in_doc_generator_table,
            "missing_from_operator_docs": self.missing_from_operator_docs,
        }


@dataclass
class CapabilityChange:
    path: str
    tool: str
    change: Literal["new", "signature_or_cli_changed"]
    detail: str


@dataclass
class ToolsIndexPayload:
    version: int
    generated_at_utc: str
    repo_root: str
    tools: list[IndexedTool]
    new_tools_since_last_scan: list[str]
    capability_changes: list[CapabilityChange]
    undocumented_tools: list[str]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "generated_at_utc": self.generated_at_utc,
            "repo_root": self.repo_root,
            "tools": [t.as_row() for t in self.tools],
            "new_tools_since_last_scan": self.new_tools_since_last_scan,
            "capability_changes": [asdict(c) for c in self.capability_changes],
            "undocumented_tools": self.undocumented_tools,
        }


def _load_previous_index(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _previous_fingerprints(prev: dict[str, Any] | None) -> dict[str, str]:
    if not prev or "tools" not in prev:
        return {}
    out: dict[str, str] = {}
    for row in prev["tools"]:
        p = row.get("path")
        fp = row.get("fingerprint")
        if isinstance(p, str) and isinstance(fp, str):
            out[p] = fp
    return out


def _previous_paths(prev: dict[str, Any] | None) -> set[str]:
    if not prev or "tools" not in prev:
        return set()
    return {str(r["path"]) for r in prev["tools"] if "path" in r}


def build_tools_index(
    repo_root: Path,
    *,
    verbose: bool = False,
    log: Callable[[str], None] | None = None,
) -> ToolsIndexPayload:
    root = repo_root.resolve()
    index_path = root / "data" / "tools_index.json"
    previous_raw = _load_previous_index(index_path)
    prev_fp = _previous_fingerprints(previous_raw)
    prev_paths = _previous_paths(previous_raw)

    mod_td = _load_repo_workflow_tool_discovery()
    _deployed_refs, ref_index = mod_td.collect_deployed_references(root)

    readme_scripts = parse_doc_generator_script_names(root / "docs" / "README.md")

    paths = discover_scan_paths(root)
    indexed: list[IndexedTool] = []
    log = log or (print if verbose else lambda _m: None)

    for path in paths:
        rel = path.relative_to(root).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError) as exc:
            log(f"[skip] {rel}: {exc}")
            continue

        if not isinstance(tree, ast.Module):
            continue

        mid = _module_id_for_file(root, path)
        kind = _classify_kind(root, path)
        tool_label = mid or path.stem
        has_main = _file_has_main_guard(path)
        funcs = _public_module_functions(tree)
        commands = sorted(set(_extract_cli_commands_from_source(tree)))
        fp = _fingerprint_for_tool(funcs, commands)
        purpose = _module_purpose(tree, source)

        surfaces: list[str] = []
        if mid and mid in ref_index:
            surfaces.extend(ref_index[mid])
        if rel in ref_index:
            surfaces.extend(ref_index[rel])
        posix_path = rel.replace("\\", "/")
        if posix_path in ref_index:
            surfaces.extend(ref_index[posix_path])
        surfaces = sorted(set(surfaces))

        in_table = path.name in readme_scripts if path.parts[:1] == ("scripts",) else False
        missing_ops = not (bool(surfaces) or in_table)

        indexed.append(
            IndexedTool(
                tool=tool_label,
                path=rel,
                purpose=purpose,
                last_updated=_utc_mtime_iso(path),
                kind=kind,
                module_id=mid,
                has_main=has_main,
                public_functions=funcs,
                cli_commands=commands,
                fingerprint=fp,
                documented_on_surfaces=surfaces,
                in_doc_generator_table=in_table,
                missing_from_operator_docs=bool(missing_ops),
            )
        )
        log(f"[ok] {rel} ({len(funcs)} public funcs, {len(commands)} subcommands)")

    indexed.sort(key=lambda t: t.path.lower())

    new_tools: list[str] = []
    cap_changes: list[CapabilityChange] = []
    for t in indexed:
        if t.path not in prev_paths:
            new_tools.append(t.path)
            cap_changes.append(CapabilityChange(path=t.path, tool=t.tool, change="new", detail="First seen in index."))
        else:
            old_fp = prev_fp.get(t.path)
            if old_fp and old_fp != t.fingerprint:
                cap_changes.append(
                    CapabilityChange(
                        path=t.path,
                        tool=t.tool,
                        change="signature_or_cli_changed",
                        detail="Public function signatures, docs, or CLI subcommands differ from cached index.",
                    )
                )

    undocumented = [t.path for t in indexed if t.missing_from_operator_docs]

    return ToolsIndexPayload(
        version=1,
        generated_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        repo_root=str(root),
        tools=indexed,
        new_tools_since_last_scan=new_tools,
        capability_changes=cap_changes,
        undocumented_tools=undocumented,
    )


def write_tools_index(payload: ToolsIndexPayload, index_path: Path) -> None:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(payload.to_json_dict(), indent=2), encoding="utf-8")


def load_tools_index(index_path: Path) -> ToolsIndexPayload:
    raw = json.loads(index_path.read_text(encoding="utf-8"))
    tools: list[IndexedTool] = []
    for row in raw["tools"]:
        tools.append(
            IndexedTool(
                tool=str(row["tool"]),
                path=str(row["path"]),
                purpose=str(row["purpose"]),
                last_updated=str(row["last_updated"]),
                kind=row.get("kind", "package_module"),  # type: ignore[arg-type]
                module_id=str(row["module_id"]) if row.get("module_id") else None,
                has_main=bool(row.get("has_main", False)),
                public_functions=list(row.get("public_functions") or []),
                cli_commands=list(row.get("cli_commands") or []),
                fingerprint=str(row.get("fingerprint", "")),
                documented_on_surfaces=list(row.get("documented_on_surfaces") or []),
                in_doc_generator_table=bool(row.get("in_doc_generator_table", False)),
                missing_from_operator_docs=bool(row.get("missing_from_operator_docs", False)),
            )
        )
    cap_raw = raw.get("capability_changes") or []
    changes = [
        CapabilityChange(
            path=str(c["path"]),
            tool=str(c["tool"]),
            change=c["change"],  # type: ignore[arg-type]
            detail=str(c.get("detail", "")),
        )
        for c in cap_raw
    ]
    return ToolsIndexPayload(
        version=int(raw.get("version", 1)),
        generated_at_utc=str(raw["generated_at_utc"]),
        repo_root=str(raw["repo_root"]),
        tools=tools,
        new_tools_since_last_scan=list(raw.get("new_tools_since_last_scan") or []),
        capability_changes=changes,
        undocumented_tools=list(raw.get("undocumented_tools") or []),
    )


def render_index_markdown_table(payload: ToolsIndexPayload) -> str:
    lines = [
        "# Tools index",
        "",
        f"_Generated {payload.generated_at_utc} · repository `{payload.repo_root}`_",
        "",
        "| Tool | Path | Purpose | Last Updated |",
        "| --- | --- | --- | --- |",
    ]
    for t in payload.tools:
        purpose = t.purpose.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {t.tool} | `{t.path}` | {purpose} | {t.last_updated} |")

    if payload.new_tools_since_last_scan:
        lines.extend(["", "## New since last scan", ""])
        lines.extend(f"- `{p}`" for p in payload.new_tools_since_last_scan)

    if payload.undocumented_tools:
        lines.extend(["", "## Not referenced on operator deployment surfaces", ""])
        lines.extend(
            f"- `{p}` _(add a `python -m …` note to README/docs or wire into automation; "
            "see root `tool_discovery.py` surfaces)_"
            for p in payload.undocumented_tools
        )

    cap_only = [c for c in payload.capability_changes if c.change == "signature_or_cli_changed"]
    if cap_only:
        lines.extend(["", "## Capability drift (vs previous index)", ""])
        for c in cap_only:
            lines.append(f"- **{c.tool}** (`{c.path}`): {c.detail}")

    return "\n".join(lines).rstrip() + "\n"


def render_index_json(payload: ToolsIndexPayload) -> str:
    return json.dumps(payload.to_json_dict(), indent=2)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover and index OpenClaw workspace tools; suggest scripts by capability."
    )
    parser.add_argument("--root", default=".", help="Repository root path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Scan tools, update data/tools_index.json cache")
    scan.add_argument("--verbose", "-v", action="store_true", help="Log each file as it is processed")

    report = subparsers.add_parser("report", help="Print report from cached index (JSON or Markdown table)")
    report.add_argument("--format", choices=("json", "md"), default="md", help="Output format")
    report.add_argument(
        "--index",
        metavar="PATH",
        help="Override path to tools_index.json (default: <root>/data/tools_index.json)",
    )

    analyze = subparsers.add_parser("analyze", help="Run deep tool capability analysis (legacy)")
    analyze.add_argument("--format", choices=("json", "text"), default="json")

    docs = subparsers.add_parser("docs", help="Generate Markdown tool documentation (legacy)")
    docs.add_argument("--output", help="Write docs to this path instead of stdout")

    suggest = subparsers.add_parser("suggest", help="Suggest tools with contextual reasoning (legacy)")
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()

    if args.command == "scan":
        payload = build_tools_index(root, verbose=args.verbose)
        out_path = root / "data" / "tools_index.json"
        write_tools_index(payload, out_path)
        print(f"Wrote {out_path} ({len(payload.tools)} tools).", file=sys.stderr)
        if payload.new_tools_since_last_scan:
            print(f"New paths since previous cache: {len(payload.new_tools_since_last_scan)}", file=sys.stderr)
        drift = [c for c in payload.capability_changes if c.change == "signature_or_cli_changed"]
        if drift:
            print(f"Capability / CLI drift vs cache: {len(drift)}", file=sys.stderr)
        return 0

    if args.command == "report":
        idx_path = Path(args.index).resolve() if args.index else root / "data" / "tools_index.json"
        if not idx_path.is_file():
            print(
                f"Index not found at {idx_path}. Run: python scripts/tool_discovery.py scan",
                file=sys.stderr,
            )
            return 2
        payload = load_tools_index(idx_path)
        if args.format == "json":
            sys.stdout.write(render_index_json(payload))
        else:
            sys.stdout.write(render_index_markdown_table(payload))
        return 0

    if args.command == "workflow":
        mod = _load_repo_workflow_tool_discovery()
        wf_argv: list[str] = ["--root", str(root), "--format", args.report_format]
        if args.report_output:
            wf_argv.extend(["--output", args.report_output])
        for surf in args.extra_surface or []:
            wf_argv.extend(["--extra-surface", surf])
        return int(mod.main(wf_argv))

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
