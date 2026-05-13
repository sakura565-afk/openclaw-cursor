#!/usr/bin/env python3
"""Scan the OpenClaw-style repo, skill markdown, SKILL.md docs, and runtime paths.

Emits a Markdown inventory under ``.learnings/tools/`` (default) for agents and humans.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

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

CAPABILITY_SIGNALS: tuple[tuple[str, str], ...] = (
    ("filesystem", "Filesystem and local file access"),
    ("file system", "Filesystem and local file access"),
    ("network", "Network and remote API access"),
    ("http", "Network and remote API access"),
    ("api", "Network and remote API access"),
    ("subprocess", "Process and shell execution"),
    ("shell", "Process and shell execution"),
    ("database", "Database and structured storage"),
    ("sqlite", "Database and structured storage"),
    ("queue", "Queues and asynchronous workloads"),
    ("telegram", "Messaging and notifications"),
    ("email", "Messaging and notifications"),
    ("monitor", "Monitoring and observability"),
    ("analytics", "Analytics and reporting"),
    ("model", "Model lifecycle and inference"),
    ("embedding", "Embeddings and semantic search"),
    ("memory", "Memory and context retention"),
    ("orchestration", "Task orchestration"),
    ("skill", "Agent skill orchestration"),
    ("mcp", "MCP server integration"),
    ("tool", "Tool invocation and automation"),
)

LIMITATION_SECTION_TITLES: frozenset[str] = frozenset(
    {
        "limitations",
        "constraints",
        "caveats",
        "known limitations",
        "risks",
        "safety",
    }
)

ACTION_SECTION_TITLES: frozenset[str] = frozenset(
    {
        "actions",
        "action",
        "what this skill does",
        "capabilities (explicit)",
    }
)

HIGH_RISK_MARKERS = {"subprocess", "os", "shutil", "requests", "socket"}
IO_MARKERS = {"pathlib", "open", "json", "csv", "sqlite3"}
NETWORK_MARKERS = {"requests", "urllib", "http", "socket", "telegram"}

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
        ".learnings",
    }
)


def _parse_simple_yaml_map(block: str) -> dict[str, Any]:
    """Parse a small YAML subset (scalars and simple lists) without external deps."""

    result: dict[str, Any] = {}
    lines = block.splitlines()
    key_re = re.compile(r"^([A-Za-z0-9_+-]+):\s*(.*)$")
    i = 0
    while i < len(lines):
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        match = key_re.match(raw.rstrip())
        if not match:
            i += 1
            continue
        key, rest = match.group(1), match.group(2).strip()
        if rest in {"", "|", ">"}:
            items: list[str] = []
            j = i + 1
            while j < len(lines):
                candidate = lines[j]
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
                result[key] = items
                i = j
                continue
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1]
            result[key] = [p.strip().strip("'\"") for p in inner.split(",") if p.strip()]
        else:
            result[key] = rest.strip('"').strip("'")
        i += 1
    return result


def _load_skill_markdown(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    text = raw.lstrip("\ufeff")
    if not text.startswith("---"):
        return {}, text
    split_lines = text.splitlines()
    if not split_lines or split_lines[0].strip() != "---":
        return {}, text
    fm_lines: list[str] = []
    idx = 1
    while idx < len(split_lines):
        if split_lines[idx].strip() == "---":
            break
        fm_lines.append(split_lines[idx])
        idx += 1
    if idx >= len(split_lines):
        return {}, text
    body = "\n".join(split_lines[idx + 1 :]).lstrip("\n")
    return _parse_simple_yaml_map("\n".join(fm_lines)), body


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return cleaned.strip("-") or "skill"


def _normalize_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [line.strip(" -*\t") for line in stripped.splitlines() if line.strip()]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, str)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _iter_skill_md_paths(
    roots: Iterable[Path],
    skip_dir_names: frozenset[str],
) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("SKILL.md"):
            if any(part in skip_dir_names for part in path.parts):
                continue
            found.append(path.resolve())
    return sorted(set(found))


def _iter_markdown_under_skill_trees(
    repo: Path,
    extra_roots: Sequence[Path],
    skip_dir_names: frozenset[str],
) -> list[Path]:
    """All ``*.md`` under ``skills/`` directories (repo + optional OpenClaw homes)."""

    bases: list[Path] = []
    for candidate in (repo / "skills", repo / "src" / "skills", *extra_roots):
        if candidate.is_dir():
            bases.append(candidate.resolve())
    found: set[Path] = set()
    for base in bases:
        for path in base.rglob("*.md"):
            if any(part in skip_dir_names for part in path.parts):
                continue
            found.add(path.resolve())
    return sorted(found)


def _extract_section_bullets(content: str, section_keywords: frozenset[str]) -> list[str]:
    lines = content.splitlines()
    in_section = False
    current_title = ""
    collected: list[str] = []

    heading_pattern = re.compile(r"^(#{1,6})\s+(.*)$")

    for line in lines:
        match = heading_pattern.match(line)
        if match:
            title = match.group(2).strip().lower()
            current_title = title
            in_section = any(key in title for key in section_keywords)
            continue
        if not in_section:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            in_section = False
            continue
        if stripped.startswith(("-", "*")):
            collected.append(stripped.lstrip("-*").strip())
        elif re.match(r"^\d+\.", stripped):
            collected.append(re.sub(r"^\d+\.\s*", "", stripped).strip())
        elif stripped.lower().startswith("action:"):
            collected.append(stripped.split(":", 1)[1].strip())
        elif current_title and "action" in current_title:
            collected.append(stripped)
    return [item for item in collected if item]


def _infer_capabilities_skill(corpus: str) -> list[str]:
    lower = corpus.lower()
    hits: list[str] = []
    for needle, label in CAPABILITY_SIGNALS:
        if needle in lower and label not in hits:
            hits.append(label)
    if not hits:
        hits.append("General automation and documentation")
    return hits


def _infer_limitations_from_text(body: str) -> list[str]:
    limitations: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lower = line.lower()
        if any(
            lower.startswith(prefix)
            for prefix in (
                "limitation:",
                "caveat:",
                "constraint:",
                "cannot ",
                "can't ",
                "does not ",
                "doesn't ",
                "must not ",
                "requires ",
                "read-only",
                "read only",
                "not supported",
                "no access to",
            )
        ):
            limitations.append(line.lstrip("-* ").strip())
    deduped: list[str] = []
    for item in limitations:
        if item not in deduped:
            deduped.append(item)
    return deduped[:25]


def _stable_json_fragment(payload: Mapping[str, Any]) -> str:
    try:
        return json.dumps(payload, sort_keys=True, default=str)
    except TypeError:
        return repr(payload)


def _fingerprint_for_paths(paths: Sequence[Path]) -> str:
    hasher = hashlib.sha256()
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            hasher.update(f"{path.as_posix()}:missing\n".encode("utf-8"))
            continue
        hasher.update(
            f"{path.as_posix()}:{int(stat.st_mtime_ns)}:{stat.st_size}\n".encode("utf-8")
        )
    return hasher.hexdigest()


@dataclass
class SkillToolInfo:
    """Structured representation of a discovered SKILL.md-backed tool."""

    skill_id: str
    path: Path
    name: str
    description: str
    actions: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    parse_notes: tuple[str, ...] = ()
    extra_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "path": str(self.path),
            "name": self.name,
            "description": self.description,
            "actions": list(self.actions),
            "capabilities": list(self.capabilities),
            "limitations": list(self.limitations),
            "parse_notes": list(self.parse_notes),
            "extra_metadata": self.extra_metadata,
        }


class ToolDiscovery:
    """Scan OpenClaw-style trees for ``SKILL.md`` definitions and expose a catalog API."""

    def __init__(
        self,
        roots: Sequence[Path | str] | None = None,
        *,
        report_path: Path | str | None = None,
        skip_dir_names: frozenset[str] | None = None,
    ) -> None:
        module_dir = Path(__file__).resolve().parent.parent
        default_roots = [module_dir]
        self._roots = [Path(r).resolve() for r in (roots or default_roots)]
        self._report_path = (
            Path(report_path).resolve() if report_path else module_dir / "tool_discovery_report.md"
        )
        self._skip_dir_names = skip_dir_names or DEFAULT_SKIP_DIR_NAMES
        self._cached_fingerprint: str | None = None
        self._cached_catalog: list[SkillToolInfo] | None = None

    @property
    def roots(self) -> tuple[Path, ...]:
        return tuple(self._roots)

    @property
    def report_path(self) -> Path:
        return self._report_path

    def _parse_skill_file(self, absolute_path: Path) -> SkillToolInfo:
        parse_notes: list[str] = []
        try:
            metadata, body = _load_skill_markdown(absolute_path)
        except Exception as exc:  # noqa: BLE001 — best-effort inventory
            parse_notes.append(f"Frontmatter parse error: {exc}")
            text = absolute_path.read_text(encoding="utf-8", errors="replace")
            metadata = {}
            body = text

        name_candidate = metadata.get("name") or metadata.get("title")
        name = str(name_candidate).strip() if name_candidate else absolute_path.parent.name or absolute_path.stem

        description = str(metadata.get("description", "")).strip()
        if not description:
            paragraphs = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
            description = paragraphs[0].replace("\n", " ") if paragraphs else "No description provided."

        actions = _normalize_str_list(metadata.get("actions"))
        if not actions:
            actions = _extract_section_bullets(body, ACTION_SECTION_TITLES)
        actions_tuple = tuple(dict.fromkeys(actions))

        limitations = _normalize_str_list(
            metadata.get("limitations")
            or metadata.get("constraints")
            or metadata.get("caveats")
        )
        if not limitations:
            limitations = _extract_section_bullets(body, LIMITATION_SECTION_TITLES)
        if not limitations:
            limitations = _infer_limitations_from_text(body)
        limitations_tuple = tuple(dict.fromkeys(limitations))

        meta_capabilities = _normalize_str_list(metadata.get("capabilities"))
        corpus = " ".join([name, description, body])
        inferred = _infer_capabilities_skill(corpus)
        merged_caps = list(dict.fromkeys([*meta_capabilities, *inferred]))
        capabilities_tuple = tuple(merged_caps)

        reserved_keys = {
            "name",
            "title",
            "description",
            "actions",
            "limitations",
            "constraints",
            "caveats",
            "capabilities",
        }
        extra = {k: v for k, v in metadata.items() if k not in reserved_keys}

        rel_path = self._relative_path(absolute_path)
        path_slug = _slugify(rel_path.as_posix().replace("/", "-"))
        skill_id = str(metadata.get("id") or "").strip() or path_slug or _slugify(name)

        return SkillToolInfo(
            skill_id=skill_id,
            path=rel_path,
            name=name,
            description=description,
            actions=actions_tuple,
            capabilities=capabilities_tuple,
            limitations=limitations_tuple,
            parse_notes=tuple(parse_notes),
            extra_metadata={k: v for k, v in extra.items() if isinstance(v, (str, int, float, bool, list, dict))},
        )

    def _relative_path(self, absolute_path: Path) -> Path:
        for root in self._roots:
            try:
                return absolute_path.relative_to(root)
            except ValueError:
                continue
        return absolute_path

    def _collect_skill_paths(self) -> list[Path]:
        found: list[Path] = []
        for root in self._roots:
            found.extend(_iter_skill_md_paths([root], self._skip_dir_names))
        return sorted(set(found))

    def discover_all(self, *, force: bool = False) -> list[SkillToolInfo]:
        paths = self._collect_skill_paths()
        fingerprint = _fingerprint_for_paths(paths)
        if (
            not force
            and self._cached_catalog is not None
            and fingerprint == self._cached_fingerprint
        ):
            return list(self._cached_catalog)

        catalog = [self._parse_skill_file(path) for path in paths]
        self._cached_catalog = catalog
        self._cached_fingerprint = fingerprint
        return list(catalog)

    def get_tool_info(self, identifier: str) -> SkillToolInfo | None:
        if not identifier.strip():
            return None
        catalog = self.discover_all()
        needle = identifier.strip().lower()
        for tool in catalog:
            if needle == tool.skill_id.lower():
                return tool
            if needle == tool.name.lower():
                return tool
            if needle == str(tool.path).lower():
                return tool
            if needle == tool.path.as_posix().lower():
                return tool
            if tool.path.as_posix().lower().endswith(needle):
                return tool
        return None

    def search_tools(self, query: str, *, limit: int = 20) -> list[SkillToolInfo]:
        if not query.strip():
            return []
        tokens = [tok for tok in re.split(r"\s+", query.lower()) if tok]
        catalog = self.discover_all()
        scored: list[tuple[int, SkillToolInfo]] = []

        for tool in catalog:
            haystack = " ".join(
                [
                    tool.name.lower(),
                    tool.description.lower(),
                    tool.skill_id.lower(),
                    str(tool.path).lower(),
                    " ".join(tool.actions).lower(),
                    " ".join(tool.capabilities).lower(),
                    " ".join(tool.limitations).lower(),
                ]
            )
            score = 0
            for token in tokens:
                if token in haystack:
                    score += 3
                if token in tool.skill_id.lower():
                    score += 2
                if token in tool.name.lower():
                    score += 2
            if score:
                scored.append((score, tool))

        scored.sort(key=lambda row: (-row[0], row[1].name.lower()))
        return [tool for _, tool in scored[: max(1, limit)]]

    def build_report_markdown(self, catalog: Sequence[SkillToolInfo] | None = None) -> str:
        items = list(catalog) if catalog is not None else self.discover_all()
        generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines = [
            "# OpenClaw SKILL.md tool inventory",
            "",
            f"_Generated {generated} from SKILL.md under: {', '.join(r.as_posix() for r in self._roots)}_",
            "",
            "## Summary",
            "",
            f"- Skills discovered: **{len(items)}**",
            "",
        ]

        if not items:
            lines.extend(
                [
                    "No `SKILL.md` files were found under the scanned roots.",
                    "",
                ]
            )

        for tool in sorted(items, key=lambda t: t.name.lower()):
            lines.extend(
                [
                    f"## {tool.name}",
                    "",
                    f"- **Skill ID:** `{tool.skill_id}`",
                    f"- **Path:** `{tool.path.as_posix()}`",
                    f"- **Description:** {tool.description}",
                    "",
                    "### Actions",
                    "",
                ]
            )
            if tool.actions:
                lines.extend(f"- {action}" for action in tool.actions)
            else:
                lines.append("- _No explicit actions listed; infer from narrative body._")
            lines.extend(["", "### Capabilities", ""])
            lines.extend(f"- {cap}" for cap in tool.capabilities)
            lines.extend(["", "### Limitations and safeguards", ""])
            if tool.limitations:
                lines.extend(f"- {lim}" for lim in tool.limitations)
            else:
                lines.append("- _No explicit limitations captured; review the source SKILL.md for edge cases._")
            if tool.parse_notes:
                lines.extend(["", "### Parse notes", ""])
                lines.extend(f"- {note}" for note in tool.parse_notes)
            if tool.extra_metadata:
                lines.extend(["", "### Additional metadata", ""])
                lines.append(f"```json\n{_stable_json_fragment(tool.extra_metadata)}\n```")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def write_report(self, catalog: Sequence[SkillToolInfo] | None = None) -> Path:
        markdown = self.build_report_markdown(catalog)
        self._report_path.parent.mkdir(parents=True, exist_ok=True)
        self._report_path.write_text(markdown, encoding="utf-8")
        return self._report_path


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


@dataclass(frozen=True)
class SkillMarkdownBrief:
    """Any ``*.md`` under a ``skills/`` tree (including SKILL.md)."""

    path: Path
    title: str
    excerpt: str
    is_skill_file: bool


def openclaw_home() -> Path:
    override = os.environ.get("OPENCLAW_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".openclaw").resolve()


def openclaw_runtime_skill_roots() -> list[Path]:
    """Typical OpenClaw skill directories on the host (may be absent)."""

    home = openclaw_home()
    return [
        (home / "skills").resolve(),
        (home / "workspace" / "skills").resolve(),
    ]


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
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            print(f"tool_discovery: skip {path.name} (syntax error: {exc})", file=sys.stderr)
            profiles.append(
                ToolProfile(
                    name=path.stem,
                    path=path.relative_to(root),
                    description=f"(unparsed: syntax error line {exc.lineno})",
                    imports=set(),
                    functions=[],
                    commands=[],
                    capabilities=["General utility automation"],
                    risk_level="low",
                    io_profile=["unknown"],
                    dependencies=[],
                    examples=[f"# Fix syntax then: python -m scripts.{path.stem} --help"],
                )
            )
            continue
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
        "# Scripts tool inventory",
        "",
        "Auto-generated capability and dependency analysis for ``scripts/*.py`` tools.",
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


def _first_markdown_heading(text: str) -> str:
    for line in text.splitlines():
        m = re.match(r"^#{1,6}\s+(.+)$", line.strip())
        if m:
            return m.group(1).strip()
    return ""


def _first_paragraph_excerpt(text: str, max_len: int = 240) -> str:
    body = re.sub(r"^---[\s\S]*?^---\s*", "", text, count=1, flags=re.MULTILINE)
    body = re.sub(r"^#{1,6}\s+.*$", "", body, flags=re.MULTILINE)
    paras = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip() and not p.strip().startswith("#")]
    if not paras:
        return ""
    one = paras[0].replace("\n", " ").strip()
    return one if len(one) <= max_len else one[: max_len - 1] + "…"


def collect_skill_markdown_briefs(
    repo: Path,
    *,
    extra_skill_roots: Sequence[Path] | None = None,
    skip: frozenset[str] = DEFAULT_SKIP_DIR_NAMES,
) -> list[SkillMarkdownBrief]:
    extra = list(extra_skill_roots or openclaw_runtime_skill_roots())
    paths = _iter_markdown_under_skill_trees(repo, extra, skip)
    briefs: list[SkillMarkdownBrief] = []
    for path in paths:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        title = _first_markdown_heading(raw) or path.stem
        excerpt = _first_paragraph_excerpt(raw)
        briefs.append(
            SkillMarkdownBrief(
                path=path,
                title=title,
                excerpt=excerpt,
                is_skill_file=path.name == "SKILL.md",
            )
        )
    briefs.sort(key=lambda b: b.path.as_posix().lower())
    return briefs


def build_skill_markdown_index_md(briefs: Sequence[SkillMarkdownBrief], repo: Path) -> str:
    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "# Skill directory markdown inventory",
        "",
        f"_Generated {when}. All ``*.md`` files under ``skills/`` trees (repo + OpenClaw runtime roots)._",
        "",
        f"- Files: **{len(briefs)}**",
        "",
    ]
    for b in briefs:
        try:
            rel = b.path.relative_to(repo)
            disp = rel.as_posix()
        except ValueError:
            disp = b.path.as_posix()
        kind = "SKILL.md" if b.is_skill_file else "doc"
        lines.append(f"## [{kind}] {b.title}")
        lines.append("")
        lines.append(f"- Path: `{disp}`")
        if b.excerpt:
            lines.append(f"- Excerpt: {b.excerpt}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def collect_runtime_markdown() -> str:
    """Environment and typical OpenClaw paths (existence checks only)."""

    home = openclaw_home()
    workspace = Path(os.environ.get("OPENCLAW_WORKSPACE", "")).expanduser().resolve() if os.environ.get(
        "OPENCLAW_WORKSPACE"
    ) else (home / "workspace").resolve()
    lines = [
        "# OpenClaw runtime context",
        "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}_",
        "",
        "## Environment",
        "",
    ]
    for key in (
        "OPENCLAW_HOME",
        "OPENCLAW_WORKSPACE",
        "OPENCLAW_REPORT_DIR",
        "NOUZ_DATA_DIR",
    ):
        val = os.environ.get(key)
        lines.append(f"- `{key}`: {val if val else '_unset_'}")
    lines.extend(["", "## Resolved paths", ""])
    checks = [
        ("OpenClaw home", home),
        ("Workspace", workspace),
        ("Skills (home)", home / "skills"),
        ("Skills (workspace)", workspace / "skills"),
        ("Logs (home)", home / "logs"),
        ("Logs (workspace)", workspace / "logs"),
        ("Shared state default", Path.home() / ".openclaw" / "shared"),
    ]
    for label, p in checks:
        exists = p.exists()
        lines.append(f"- **{label}:** `{p}` — {'exists' if exists else '_missing_'}")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Paths mirror ``resolve_openclaw_workspace`` and related helpers under ``src/coordination``.",
            "- Install or configure OpenClaw separately; this file only records what is visible on this machine.",
            "",
        ]
    )
    return "\n".join(lines)


def summarize_openclaw_modules(repo: Path) -> str:
    """Lightweight index of OpenClaw-related Python packages in ``src/``."""

    targets: list[tuple[str, Path]] = []
    for rel, sub in (
        ("openclaw_orchestration", repo / "src" / "openclaw_orchestration"),
        ("skills", repo / "src" / "skills"),
        ("coordination", repo / "src" / "coordination"),
    ):
        if sub.is_dir():
            targets.append((rel, sub))
    lines = [
        "# OpenClaw-related Python modules",
        "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}_",
        "",
    ]
    if not targets:
        lines.append("_No matching ``src/`` packages found._")
        return "\n".join(lines) + "\n"

    for label, base in targets:
        lines.append(f"## ``src/{label}/``")
        lines.append("")
        py_files = sorted(base.glob("*.py"))
        for py in py_files:
            if py.name == "__init__.py":
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            except SyntaxError:
                lines.append(f"- `{py.name}` — _syntax error in source_")
                continue
            desc = extract_description(tree)
            lines.append(f"- **`{py.name}`** — {desc}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_best_practices_md(profiles: list[ToolProfile], skill_count: int) -> str:
    high = [p.name for p in profiles if p.risk_level == "high"]
    lines = [
        "# Tool discovery — best practices",
        "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}_",
        "",
        "## Using this inventory",
        "",
        "- Regenerate after adding scripts or skills: `python -m scripts.tool_discovery --root <repo> inventory`.",
        "- Prefer `python -m scripts.<name> --help` before passing real data.",
        "- Cross-check **SKILL.md** actions with script subcommands when both exist.",
        "",
        "## Safety",
        "",
    ]
    if high:
        lines.append("High heuristics-risk scripts (subprocess / many CLI verbs):")
        lines.extend(f"- `{n}`" for n in sorted(high))
    else:
        lines.append("_No scripts scored as high-risk by current heuristics._")
    lines.extend(
        [
            "",
            "## Skills",
            "",
            f"- Parsed **SKILL.md** records: **{skill_count}** across scanned roots.",
            "- Keep YAML frontmatter (`name`, `description`, optional `actions`, `limitations`) accurate.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def build_inventory_index_md(written: Sequence[tuple[str, str]]) -> str:
    """``written`` is list of (filename, one-line description)."""

    when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "# Tool discovery inventory",
        "",
        f"_Generated {when}_",
        "",
        "Markdown files in this directory are produced by ``scripts/tool_discovery.py``.",
        "",
        "## Files",
        "",
    ]
    for name, blurb in written:
        lines.append(f"- [`{name}`](./{name}) — {blurb}")
    lines.append("")
    return "\n".join(lines)


def write_learning_inventory(repo: Path, output_dir: Path | None = None) -> list[Path]:
    """Write the full Markdown inventory to ``.learnings/tools/`` (or ``output_dir``)."""

    out = (output_dir or (repo / ".learnings" / "tools")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    profiles = analyze_scripts(repo)
    skill_roots = [repo, *openclaw_runtime_skill_roots()]
    discovery = ToolDiscovery(roots=skill_roots, report_path=out / "skill_tools_report.md")
    skill_catalog = discovery.discover_all(force=True)
    briefs = collect_skill_markdown_briefs(repo, extra_skill_roots=openclaw_runtime_skill_roots())

    files: list[tuple[str, str, str]] = [
        (
            "01-scripts.md",
            generate_markdown(profiles),
            "CLI scripts capability scan",
        ),
        (
            "02-skill-md-tools.md",
            discovery.build_report_markdown(skill_catalog),
            "SKILL.md structured tools",
        ),
        (
            "03-skill-markdown-files.md",
            build_skill_markdown_index_md(briefs, repo),
            "all markdown under skills/ trees",
        ),
        ("04-runtime.md", collect_runtime_markdown(), "environment and OpenClaw paths"),
        (
            "05-openclaw-src.md",
            summarize_openclaw_modules(repo),
            "OpenClaw-related ``src/`` modules",
        ),
        (
            "06-best-practices.md",
            build_best_practices_md(profiles, len(skill_catalog)),
            "operational guidance",
        ),
    ]

    paths_out: list[Path] = []
    readme_body = build_inventory_index_md(
        [
            ("01-scripts.md", "CLI scripts capability scan"),
            ("02-skill-md-tools.md", "SKILL.md structured tools"),
            ("03-skill-markdown-files.md", "all markdown under skills/ trees"),
            ("04-runtime.md", "environment and OpenClaw paths"),
            ("05-openclaw-src.md", "OpenClaw-related src/ modules"),
            ("06-best-practices.md", "operational guidance"),
        ]
    )
    (out / "README.md").write_text(readme_body, encoding="utf-8")
    paths_out.append(out / "README.md")

    for name, body, _desc in files:
        dest = out / name
        dest.write_text(body, encoding="utf-8")
        paths_out.append(dest)

    return paths_out


def generate_tool_discovery_report(
    roots: Sequence[Path | str] | None = None,
    *,
    report_path: Path | str | None = None,
) -> Path:
    """Scan SKILL.md under ``roots`` and write a single Markdown report."""

    discovery = ToolDiscovery(roots=roots, report_path=report_path)
    discovery.write_report()
    return discovery.report_path


def _attach_inventory_parser(subparsers: Any) -> None:
    inv = subparsers.add_parser("inventory", help="Emit full Markdown inventory under .learnings/tools/")
    inv.add_argument(
        "--output-dir",
        default=None,
        help="Override output directory (default: <root>/.learnings/tools)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover OpenClaw tools: scripts, SKILL.md, skills markdown, and runtime context.",
    )
    parser.add_argument("--root", default=".", help="Repository root path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    _attach_inventory_parser(subparsers)

    analyze = subparsers.add_parser("analyze", help="Run deep tool capability analysis on scripts/")
    analyze.add_argument("--format", choices=("json", "text"), default="json")

    docs = subparsers.add_parser("docs", help="Generate Markdown tool documentation for scripts/")
    docs.add_argument("--output", help="Write docs to this path instead of stdout")

    suggest = subparsers.add_parser("suggest", help="Suggest tools with contextual reasoning")
    suggest.add_argument("goal", help="User goal, e.g. 'monitor queue latency'")
    suggest.add_argument("--context", default="", help="Additional operational context")
    suggest.add_argument("--top", type=int, default=5, help="Number of tools to suggest")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()

    if args.command == "inventory":
        out_dir = Path(args.output_dir).resolve() if args.output_dir else root / ".learnings" / "tools"
        paths = write_learning_inventory(root, output_dir=out_dir)
        print(f"Wrote {len(paths)} files under {out_dir}", file=sys.stderr)
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
