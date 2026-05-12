#!/usr/bin/env python3
"""Discover OpenClaw-related tools from code, docs, and session logs.

Scans script CLIs (via doc_generator AST), documentation tables, optional JSON
schemas, and skill modules; builds a searchable registry; infers usage patterns
from session-style logs; tracks evolution snapshots under ``.learnings/tools/``.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

LOGGER = logging.getLogger(__name__)

LEARNINGS_DIR = ".learnings"
TOOLS_LEARNINGS_SUBDIR = "tools"
REGISTRY_FILENAME = "tool_registry.json"
PATTERNS_FILENAME = "usage_patterns.json"
EVOLUTION_STATE = "tool_discovery_state.json"
EVOLUTION_LOG = "tool_evolution.log"
CURATED_INDEX = "tool_discovery.md"

DEFAULT_SESSION_GLOBS = (
    "logs/**/*.log",
    "logs/**/*.json",
    "memory/**/*_log.md",
    "memory/**/*.md",
    ".learnings/insights/**/*.json",
    ".learnings/insights/**/*.md",
)

DOC_TABLE_ROW = re.compile(
    r"^\|\s*\[`([^`]+\.py)`\]\([^)]+\)\s*\|\s*([^|]+)\|",
    re.MULTILINE,
)

# Heuristic: agent / MCP style tool names in transcripts
CURSOR_STYLE_TOOLS = frozenset(
    {
        "Shell",
        "Grep",
        "Glob",
        "Read",
        "Write",
        "StrReplace",
        "Delete",
        "Task",
        "WebSearch",
        "WebFetch",
        "EditNotebook",
        "TodoWrite",
        "ManagePullRequest",
        "ReadLints",
        "SwitchMode",
    }
)

TOOL_JSON_NAME = re.compile(r'"name"\s*:\s*"([A-Za-z_][A-Za-z0-9_.-]*)"')
TOOL_ASSIGN = re.compile(
    r"(?i)(?:tool|function|mcp\s+tool|calling)\s*[:=]\s*[`\"']?([A-Za-z_][A-Za-z0-9_.-]*)[`\"']?"
)
FLAG_TOKEN = re.compile(r"(--[a-z][a-z0-9-]*\b|-[a-z]\b)")

FAILURE_NEAR = re.compile(
    r"(?i)(\b(traceback|exception|error|failed|failure|timed?\s*out|exit\s*code\s*[1-9]\d*)\b|"
    r"\b(Fatal|Critical)\b|^Error:|\[\s*ERROR\s*\])"
)

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
    imports: set[str] = field(default_factory=set)
    functions: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    risk_level: str = "low"
    io_profile: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    parameters: list[dict[str, Any]] = field(default_factory=list)
    use_cases: list[str] = field(default_factory=list)
    success_rate: float | None = None

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
            "parameters": self.parameters,
            "use_cases": self.use_cases,
            "success_rate": self.success_rate,
        }


@dataclass
class UsageStats:
    """Aggregated usage for one logical tool name."""

    name: str
    call_count: int = 0
    error_near_count: int = 0
    success_near_count: int = 0
    parameter_tokens: Counter[str] = field(default_factory=Counter)

    @property
    def error_rate(self) -> float | None:
        total = self.error_near_count + self.success_near_count
        if total == 0:
            return None
        return self.error_near_count / total

    @property
    def inferred_success_rate(self) -> float | None:
        total = self.error_near_count + self.success_near_count
        if total == 0:
            return None
        return self.success_near_count / total


@dataclass
class UsagePatternReport:
    """Summary of tool-like mentions in session histories."""

    files_scanned: int
    tools: dict[str, UsageStats]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_scanned": self.files_scanned,
            "tools": {
                name: {
                    "call_count": s.call_count,
                    "error_near_count": s.error_near_count,
                    "success_near_count": s.success_near_count,
                    "error_rate": s.error_rate,
                    "inferred_success_rate": s.inferred_success_rate,
                    "top_parameter_tokens": s.parameter_tokens.most_common(12),
                }
                for name, s in sorted(self.tools.items(), key=lambda kv: (-kv[1].call_count, kv[0]))
            },
            "notes": self.notes,
        }


@dataclass
class RegisteredTool:
    """Unified searchable registry row (scripts, docs, skills)."""

    name: str
    kind: str
    description: str
    path: str | None
    parameters: list[dict[str, Any]]
    use_cases: list[str]
    examples: list[str]
    capabilities: list[str]
    risk_level: str | None
    io_profile: list[str]
    dependencies: list[str]
    success_rate: float | None
    call_count: int
    error_rate: float | None
    source_detail: str

    def search_blob(self) -> str:
        parts = [
            self.name,
            self.description,
            self.kind,
            self.source_detail,
            " ".join(self.use_cases),
            " ".join(self.capabilities),
            json.dumps(self.parameters, sort_keys=True),
            " ".join(self.examples),
        ]
        return " ".join(parts).lower()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def discover_script_paths(root: Path) -> list[Path]:
    scripts_dir = root / "scripts"
    if not scripts_dir.exists():
        return []
    return sorted(path for path in scripts_dir.glob("*.py") if path.name != "__init__.py")


def discover_skill_paths(root: Path) -> list[Path]:
    skills_dir = root / "src" / "skills"
    if not skills_dir.exists():
        return []
    return sorted(p for p in skills_dir.glob("*.py") if p.name != "__init__.py")


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


def infer_use_cases(
    name: str,
    description: str,
    capabilities: list[str],
    commands: list[str],
    parameters: list[dict[str, Any]],
) -> list[str]:
    cases: list[str] = []
    for cap in capabilities:
        cases.append(f"When you need {cap.lower()}, consider ``{name}``.")
    if commands:
        cases.append(f"Typical entry points: subcommands {', '.join(f'`{c}`' for c in commands[:6])}.")
    if any(p.get("required") for p in parameters):
        cases.append("Some CLI arguments are required; run with ``--help`` for the full contract.")
    if not cases:
        cases.append(f"General automation helper: {description}")
    return cases[:8]


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


def _try_parse_cli_parameters(script_path: Path, root: Path) -> list[dict[str, Any]]:
    try:
        from scripts.doc_generator import parse_script  # noqa: WPS433 (runtime import)
    except ImportError:
        LOGGER.debug("doc_generator unavailable; skipping CLI parameter extraction")
        return []

    scripts_dir = root / "scripts"
    docs_dir = root / "docs"
    if not script_path.is_relative_to(scripts_dir.parent):
        return []
    try:
        doc = parse_script(script_path, scripts_dir, docs_dir)
    except (OSError, SyntaxError, ValueError) as exc:
        LOGGER.debug("parse_script failed for %s: %s", script_path, exc)
        return []

    out: list[dict[str, Any]] = []
    for arg in doc.arguments:
        out.append(
            {
                "display": arg.display_name,
                "positional": arg.positional,
                "help": (arg.help_text or "").strip(),
                "required": arg.required,
                "default": arg.default,
                "type": arg.value_type,
                "choices": list(arg.choices) if arg.choices else [],
            }
        )
    return out


def _try_build_doc_examples(script_path: Path, root: Path) -> list[str]:
    try:
        from scripts.doc_generator import build_usage_examples, parse_script  # noqa: WPS433
    except ImportError:
        return []

    scripts_dir = root / "scripts"
    docs_dir = root / "docs"
    try:
        doc = parse_script(script_path, scripts_dir, docs_dir)
        return build_usage_examples(doc)[:5]
    except (OSError, SyntaxError, ValueError):
        return []


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


def build_examples(profile: ToolProfile, root: Path) -> list[str]:
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
    for ex in _try_build_doc_examples(root / profile.path, root):
        if ex not in examples:
            examples.append(ex)
    return examples[:8]


def analyze_scripts(root: Path) -> list[ToolProfile]:
    profiles: list[ToolProfile] = []
    for path in discover_script_paths(root):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError, ValueError) as exc:
            LOGGER.debug("Skipping unreadable script %s: %s", path, exc)
            continue
        name = path.stem
        imports = extract_imports(tree)
        functions = extract_functions(tree)
        commands = extract_cli_commands(tree)
        description = extract_description(tree)
        capabilities = infer_capabilities(name, description, commands, functions)
        io_profile = infer_io_profile(imports, source)
        risk_level = infer_risk_level(imports, commands)
        parameters = _try_parse_cli_parameters(path, root)
        use_cases = infer_use_cases(name, description, capabilities, commands, parameters)
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
                parameters=parameters,
                use_cases=use_cases,
            )
        )
    enrich_dependency_graph(profiles)
    for profile in profiles:
        profile.examples = build_examples(profile, root)
    return profiles


def parse_docs_script_index(root: Path) -> list[tuple[str, str, Path]]:
    """Return (stem, summary, doc_path) rows from docs/README.md script table."""
    readme = root / "docs" / "README.md"
    if not readme.exists():
        return []
    text = readme.read_text(encoding="utf-8", errors="replace")
    rows: list[tuple[str, str, Path]] = []
    for match in DOC_TABLE_ROW.finditer(text):
        script_file, summary = match.group(1), match.group(2).strip()
        stem = Path(script_file).stem
        rows.append((stem, summary, readme))
    return rows


def discover_json_tool_shapes(root: Path) -> list[dict[str, Any]]:
    """Light scan for OpenAI-style function/tool JSON blobs in the repo."""
    hits: list[dict[str, Any]] = []
    root = root.resolve()
    for path in (root / "docs").rglob("*.json") if (root / "docs").exists() else []:
        _collect_schema_hits(path, root, hits)
    for name in ("openclaw_tools.json", "tool_schemas.json", "mcp_tools.json"):
        candidate = root / name
        if candidate.exists():
            _collect_schema_hits(candidate, root, hits)
    return hits[:200]


def _collect_schema_hits(path: Path, root: Path, hits: list[dict[str, Any]]) -> None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        if len(raw) > 400_000:
            return
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return

    try:
        rel_source = path.relative_to(root).as_posix()
    except ValueError:
        rel_source = path.as_posix()

    def handle_obj(obj: Any, trail: str, depth: int) -> None:
        if depth > 14 or len(hits) >= 200:
            return
        if isinstance(obj, dict):
            if obj.get("type") == "function" and "function" in obj and isinstance(obj["function"], dict):
                fn = obj["function"]
                name = fn.get("name")
                if isinstance(name, str):
                    hits.append(
                        {
                            "name": name,
                            "description": fn.get("description") or "",
                            "parameters": fn.get("parameters") or {},
                            "source": rel_source,
                        }
                    )
            for k, v in obj.items():
                if k in {"tools", "functions", "items"} and isinstance(v, list):
                    for i, item in enumerate(v):
                        handle_obj(item, f"{trail}.{k}[{i}]", depth + 1)
                elif isinstance(v, (dict, list)):
                    handle_obj(v, f"{trail}.{k}", depth + 1)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                handle_obj(item, f"{trail}[{i}]", depth + 1)

    handle_obj(data, path.name, 0)


def analyze_skills(root: Path) -> list[ToolProfile]:
    profiles: list[ToolProfile] = []
    for path in discover_skill_paths(root):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError, ValueError) as exc:
            LOGGER.debug("Skipping unreadable skill %s: %s", path, exc)
            continue
        name = f"skill:{path.stem}"
        imports = extract_imports(tree)
        functions = extract_functions(tree)
        description = extract_description(tree)
        commands: list[str] = []
        capabilities = sorted(set([*infer_capabilities(path.stem, description, commands, functions), "OpenClaw skill module"]))
        io_profile = infer_io_profile(imports, source)
        risk_level = infer_risk_level(imports, commands)
        parameters: list[dict[str, Any]] = []
        use_cases = infer_use_cases(name, description, capabilities, commands, parameters)
        rel = path.relative_to(root)
        profiles.append(
            ToolProfile(
                name=name,
                path=rel,
                description=description,
                imports=imports,
                functions=functions,
                commands=commands,
                capabilities=capabilities,
                risk_level=risk_level,
                io_profile=io_profile,
                parameters=parameters,
                use_cases=use_cases,
                examples=[f"python -c \"import importlib; importlib.import_module('src.skills.{path.stem}')\""],
            )
        )
    return profiles


def _iter_session_files(root: Path, globs: Sequence[str], max_bytes: int = 2 * 1024 * 1024) -> Iterator[Path]:
    seen: set[Path] = set()
    for pattern in globs:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            try:
                if path.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            rp = path.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            yield path


def _lines_have_failure(window: Sequence[str]) -> bool:
    return any(FAILURE_NEAR.search(line) for line in window)


def extract_tool_mentions_from_text(text: str) -> list[tuple[str, int]]:
    """Return (tool_name, line_index) for heuristic tool references."""
    lines = text.splitlines()
    found: list[tuple[str, int]] = []
    for i, line in enumerate(lines):
        for match in TOOL_ASSIGN.finditer(line):
            name = match.group(1)
            if name in CURSOR_STYLE_TOOLS or name.isidentifier():
                found.append((name, i))
        if '"tool_calls"' in line or "tool_calls" in line:
            for m in TOOL_JSON_NAME.finditer(line):
                found.append((m.group(1), i))
    return found


def _parameter_flags_near(lines: list[str], center: int, radius: int = 4) -> Counter[str]:
    lo = max(0, center - radius)
    hi = min(len(lines), center + radius + 1)
    c: Counter[str] = Counter()
    segment = "\n".join(lines[lo:hi])
    for m in FLAG_TOKEN.finditer(segment):
        c[m.group(1)] += 1
    return c


def analyze_usage_patterns(
    root: Path,
    globs: Sequence[str] | None = None,
) -> UsagePatternReport:
    globs = globs or DEFAULT_SESSION_GLOBS
    tools: dict[str, UsageStats] = {}
    notes: list[str] = []
    files_scanned = 0

    def get_stats(name: str) -> UsageStats:
        if name not in tools:
            tools[name] = UsageStats(name=name)
        return tools[name]

    for path in _iter_session_files(root, globs):
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        files_scanned += 1
        lines = raw.splitlines()
        mentions = extract_tool_mentions_from_text(raw)
        for tool_name, line_idx in mentions:
            st = get_stats(tool_name)
            st.call_count += 1
            window = lines[line_idx : line_idx + 18]
            if _lines_have_failure(window[1:]):
                st.error_near_count += 1
            else:
                st.success_near_count += 1
            st.parameter_tokens.update(_parameter_flags_near(lines, line_idx))

    if files_scanned == 0:
        notes.append("No session files matched the configured globs; usage stats are empty.")

    return UsagePatternReport(files_scanned=files_scanned, tools=tools, notes=notes)


def attach_usage_to_profiles(profiles: Iterable[ToolProfile], usage: UsagePatternReport) -> None:
    for profile in profiles:
        st = usage.tools.get(profile.name)
        if st and st.inferred_success_rate is not None:
            profile.success_rate = round(st.inferred_success_rate, 4)


def build_registered_tools(
    root: Path,
    profiles: list[ToolProfile],
    usage: UsagePatternReport,
    doc_rows: list[tuple[str, str, Path]],
    json_schemas: list[dict[str, Any]],
) -> list[RegisteredTool]:
    by_name = {p.name: p for p in profiles}
    registered: list[RegisteredTool] = []

    for profile in profiles:
        st = usage.tools.get(profile.name)
        call_count = st.call_count if st else 0
        err_rate = st.error_rate if st else None
        success = profile.success_rate
        if success is None and st:
            success = st.inferred_success_rate
            if success is not None:
                success = round(success, 4)
        registered.append(
            RegisteredTool(
                name=profile.name,
                kind="script" if not profile.name.startswith("skill:") else "skill",
                description=profile.description,
                path=str(profile.path),
                parameters=profile.parameters,
                use_cases=profile.use_cases,
                examples=profile.examples,
                capabilities=profile.capabilities,
                risk_level=profile.risk_level,
                io_profile=profile.io_profile,
                dependencies=profile.dependencies,
                success_rate=success,
                call_count=call_count,
                error_rate=round(err_rate, 4) if isinstance(err_rate, float) else err_rate,
                source_detail="scripts/ AST + doc_generator CLI schema",
            )
        )

    for stem, summary, doc_path in doc_rows:
        if stem in by_name:
            continue
        st = usage.tools.get(stem)
        registered.append(
            RegisteredTool(
                name=stem,
                kind="doc_index",
                description=summary,
                path=str(doc_path.relative_to(root)),
                parameters=[],
                use_cases=[f"Listed in documentation index; see ``{doc_path.name}`` for context."],
                examples=[f"python scripts/{stem}.py --help"],
                capabilities=["Documentation index entry"],
                risk_level=None,
                io_profile=[],
                dependencies=[],
                success_rate=st.inferred_success_rate if st else None,
                call_count=st.call_count if st else 0,
                error_rate=round(st.error_rate, 4) if st and st.error_rate is not None else None,
                source_detail="docs/README.md generated index",
            )
        )

    for schema in json_schemas:
        name = schema.get("name")
        if not isinstance(name, str) or name in {r.name for r in registered}:
            continue
        registered.append(
            RegisteredTool(
                name=name,
                kind="json_schema",
                description=str(schema.get("description") or "Tool schema snippet"),
                path=schema.get("source"),
                parameters=[{"schema": schema.get("parameters")}],
                use_cases=["Declared in JSON tool/function schema."],
                examples=[],
                capabilities=["Schema-defined tool"],
                risk_level=None,
                io_profile=[],
                dependencies=[],
                success_rate=None,
                call_count=0,
                error_rate=None,
                source_detail="JSON tool shape",
            )
        )

    registered.sort(key=lambda r: (r.kind != "script", r.name))
    return registered


def _fingerprint_registry(entries: Sequence[RegisteredTool]) -> dict[str, str]:
    fp: dict[str, str] = {}
    for entry in entries:
        if entry.kind not in {"script", "skill"}:
            continue
        payload = f"{entry.description}|{entry.parameters}|{entry.capabilities}"
        fp[entry.name] = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return fp


def record_tool_evolution(root: Path, entries: Sequence[RegisteredTool]) -> list[str]:
    """Compare against last snapshot; append human-readable lines to evolution log."""
    learnings = root / LEARNINGS_DIR / TOOLS_LEARNINGS_SUBDIR
    learnings.mkdir(parents=True, exist_ok=True)
    state_path = learnings / EVOLUTION_STATE
    log_path = learnings / EVOLUTION_LOG

    new_fp = _fingerprint_registry(entries)
    prev: dict[str, Any] = {}
    if state_path.exists():
        try:
            prev = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            prev = {}

    old_fp: dict[str, str] = prev.get("fingerprints") or {}
    lines: list[str] = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    added = sorted(name for name in new_fp if name not in old_fp)
    removed = sorted(name for name in old_fp if name not in new_fp)
    changed = sorted(name for name in new_fp if name in old_fp and old_fp[name] != new_fp[name])

    if added:
        lines.append(f"{ts} ADDED: {', '.join(added)}")
    if removed:
        lines.append(f"{ts} REMOVED: {', '.join(removed)}")
    if changed:
        lines.append(f"{ts} UPDATED: {', '.join(changed)}")
    if not lines and not old_fp:
        lines.append(f"{ts} INITIAL_SNAPSHOT tools={len(new_fp)}")

    if lines:
        prev_text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        log_path.write_text(prev_text + "\n".join(lines) + "\n", encoding="utf-8")

    prev["fingerprints"] = new_fp
    prev["updated_at_utc"] = ts
    state_path.write_text(json.dumps(prev, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return lines


def tokenize_query(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9_./:-]+", text.lower()) if len(t) >= 2]


def search_tools_natural_language(
    entries: Sequence[RegisteredTool],
    query: str,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    tokens = tokenize_query(query)
    scored: list[tuple[float, RegisteredTool, list[str]]] = []
    for entry in entries:
        blob = entry.search_blob()
        reasons: list[str] = []
        score = 0.0
        for tok in tokens:
            if tok in blob:
                score += 3.0
                reasons.append(f"token:{tok}")
            if tok in entry.name.lower():
                score += 5.0
                reasons.append(f"name:{tok}")
        for cap in entry.capabilities:
            cl = cap.lower()
            if any(t in cl for t in tokens):
                score += 2.0
                reasons.append(f"capability:{cap}")
        scored.append((score, entry, reasons))
    scored.sort(key=lambda row: (row[0], row[1].name), reverse=True)

    results: list[dict[str, Any]] = []
    for score, entry, reasons in scored[:top_n]:
        if score <= 0:
            continue
        results.append(
            {
                "name": entry.name,
                "kind": entry.kind,
                "score": round(score, 3),
                "reasons": reasons[:12],
                "description": entry.description,
                "examples": entry.examples[:3],
                "success_rate": entry.success_rate,
                "call_count": entry.call_count,
            }
        )
    if not results:
        for _, entry, _ in scored[:top_n]:
            results.append(
                {
                    "name": entry.name,
                    "kind": entry.kind,
                    "score": 0.0,
                    "reasons": ["fallback:alphabetical"],
                    "description": entry.description,
                    "examples": entry.examples[:2],
                    "success_rate": entry.success_rate,
                    "call_count": entry.call_count,
                }
            )
            if len(results) >= top_n:
                break
    return results


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
        if profile.parameters:
            lines.extend(["", "### CLI parameters", ""])
            for p in profile.parameters[:20]:
                req = "required" if p.get("required") else "optional"
                lines.append(f"- `{p.get('display')}` ({req}): {p.get('help', '')}")
        lines.extend(["", "### Example usage", ""])
        lines.extend(f"```bash\n{example}\n```" for example in profile.examples)
        lines.append("")
    return "\n".join(lines)


def generate_curated_learnings_markdown(
    entries: Sequence[RegisteredTool],
    usage: UsagePatternReport,
    evolution_lines: Sequence[str],
) -> str:
    lines = [
        "# OpenClaw tool discovery index",
        "",
        "Curated, searchable view of repository tools (scripts, skills, doc index) plus",
        "how to interpret session-derived usage metrics.",
        "",
        "## How to refresh",
        "",
        "```bash",
        "python -m scripts.tool_discovery scan --root .",
        "python -m scripts.tool_discovery search \"sync obsidian notes\" --top 5 --root .",
        "```",
        "",
        "## Usage tips",
        "",
        "- Prefer ``python -m scripts.<name> --help`` to confirm CLI flags; parameters below come from static analysis.",
        "- ``success_rate`` / ``error_rate`` in JSON registries are **heuristic** signals from log adjacency, not ground truth.",
        "- When logs are empty, run ``scan`` after collecting agent transcripts under ``logs/`` or ``memory/``.",
        "",
        "## Session usage snapshot",
        "",
        f"- Files scanned: **{usage.files_scanned}**",
        f"- Distinct tool-like symbols: **{len(usage.tools)}**",
        "",
    ]
    if usage.notes:
        lines.append("Notes:")
        for n in usage.notes:
            lines.append(f"- {n}")
        lines.append("")

    top_calls = sorted(usage.tools.items(), key=lambda kv: -kv[1].call_count)[:15]
    if top_calls:
        lines.extend(["### Most-mentioned tools (from histories)", ""])
        for name, st in top_calls:
            rate = st.inferred_success_rate
            rate_s = f"{rate:.0%}" if rate is not None else "n/a"
            lines.append(f"- `{name}` — calls≈{st.call_count}, inferred success≈{rate_s}")
        lines.append("")

    if evolution_lines:
        lines.extend(["## Recent evolution", ""])
        lines.extend(f"- {ln}" for ln in evolution_lines[-12:])
        lines.append("")

    lines.extend(["## Tool capabilities", ""])
    for entry in entries:
        if entry.kind == "json_schema":
            continue
        lines.append(f"### `{entry.name}` ({entry.kind})")
        lines.append("")
        lines.append(entry.description)
        lines.append("")
        if entry.use_cases:
            lines.append("**Use cases**")
            for uc in entry.use_cases[:5]:
                lines.append(f"- {uc}")
            lines.append("")
        if entry.examples:
            lines.append("**Examples**")
            for ex in entry.examples[:4]:
                lines.append(f"```bash\n{ex}\n```")
            lines.append("")
        if entry.capabilities:
            lines.append(f"**Tags:** {', '.join(entry.capabilities)}")
            lines.append("")
        lines.append("---")
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


def run_discovery_scan(root: Path, session_globs: Sequence[str] | None = None) -> dict[str, Any]:
    """Full scan: scripts + skills + docs + JSON hints + usage patterns + registry objects."""
    root = root.resolve()
    session_globs = session_globs or DEFAULT_SESSION_GLOBS

    script_profiles = analyze_scripts(root)
    skill_profiles = analyze_skills(root)
    combined_profiles = script_profiles + skill_profiles

    usage = analyze_usage_patterns(root, session_globs)
    attach_usage_to_profiles(combined_profiles, usage)

    doc_rows = parse_docs_script_index(root)
    json_schemas = discover_json_tool_shapes(root)

    registered = build_registered_tools(root, combined_profiles, usage, doc_rows, json_schemas)
    evolution_lines = record_tool_evolution(root, registered)

    return {
        "root": str(root),
        "profiles": combined_profiles,
        "usage": usage,
        "registered": registered,
        "evolution_lines": evolution_lines,
    }


def write_scan_artifacts(root: Path, payload: dict[str, Any]) -> dict[str, str]:
    learnings = root / LEARNINGS_DIR / TOOLS_LEARNINGS_SUBDIR
    learnings.mkdir(parents=True, exist_ok=True)

    reg_path = learnings / REGISTRY_FILENAME
    pat_path = learnings / PATTERNS_FILENAME
    idx_path = learnings / CURATED_INDEX

    registered: list[RegisteredTool] = payload["registered"]
    usage: UsagePatternReport = payload["usage"]

    reg_path.write_text(
        json.dumps([r.to_dict() for r in registered], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    pat_path.write_text(json.dumps(usage.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    md = generate_curated_learnings_markdown(registered, usage, payload.get("evolution_lines") or [])
    idx_path.write_text(md, encoding="utf-8")

    return {
        "registry": str(reg_path.relative_to(root)),
        "patterns": str(pat_path.relative_to(root)),
        "index": str(idx_path.relative_to(root)),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".", help="Repository root path")
    common.add_argument(
        "--session-glob",
        action="append",
        dest="session_globs",
        help="Extra glob (repeatable) for session logs; defaults to built-in OpenClaw-style paths.",
    )
    common.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")

    parser = argparse.ArgumentParser(description="Discover and suggest utility scripts by capability.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("scan", parents=[common], help="Run full discovery, write .learnings/tools artifacts")

    search = subparsers.add_parser("search", parents=[common], help="Natural-language search over the tool registry")
    search.add_argument("query", help="Plain-language query")
    search.add_argument("--top", type=int, default=10)

    subparsers.add_parser("patterns", parents=[common], help="Print usage pattern JSON from session histories")

    subparsers.add_parser("registry", parents=[common], help="Print combined registry JSON (scan in-memory)")

    analyze = subparsers.add_parser("analyze", parents=[common], help="Run deep tool capability analysis")
    analyze.add_argument("--format", choices=("json", "text"), default="json")

    docs = subparsers.add_parser("docs", parents=[common], help="Generate Markdown tool documentation")
    docs.add_argument("--output", help="Write docs to this path instead of stdout")

    suggest = subparsers.add_parser("suggest", parents=[common], help="Suggest tools with contextual reasoning")
    suggest.add_argument("goal", help="User goal, e.g. 'monitor queue latency'")
    suggest.add_argument("--context", default="", help="Additional operational context")
    suggest.add_argument("--top", type=int, default=5, help="Number of tools to suggest")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)

    root = Path(args.root).resolve()

    if args.command == "scan":
        payload = run_discovery_scan(root, session_globs=args.session_globs)
        paths = write_scan_artifacts(root, payload)
        print(json.dumps({"status": "ok", "written": paths, "tools": len(payload["registered"])}, indent=2))
        return 0

    if args.command == "search":
        payload = run_discovery_scan(root, session_globs=args.session_globs)
        hits = search_tools_natural_language(payload["registered"], args.query, top_n=max(1, args.top))
        print(json.dumps({"query": args.query, "results": hits}, indent=2))
        return 0

    if args.command == "patterns":
        usage = analyze_usage_patterns(root, args.session_globs or DEFAULT_SESSION_GLOBS)
        print(json.dumps(usage.to_dict(), indent=2))
        return 0

    if args.command == "registry":
        payload = run_discovery_scan(root, session_globs=args.session_globs)
        print(json.dumps([r.to_dict() for r in payload["registered"]], indent=2))
        return 0

    script_profiles = analyze_scripts(root)

    if args.command == "analyze":
        if args.format == "json":
            print(json.dumps([profile.to_dict() for profile in script_profiles], indent=2))
        else:
            for profile in script_profiles:
                print(f"{profile.name}: {', '.join(profile.capabilities)} | deps={profile.dependencies}")
        return 0

    if args.command == "docs":
        markdown = generate_markdown(script_profiles)
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
            "suggestions": suggest_tools(script_profiles, goal=args.goal, context=args.context, top_n=max(1, args.top)),
        }
        print(json.dumps(payload, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
