#!/usr/bin/env python3
"""Discover, catalog, and document OpenClaw skills defined by SKILL.md files."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    import frontmatter as _frontmatter  # type: ignore[import-untyped]

    _HAS_FRONTMATTER = True
except ImportError:  # pragma: no cover - exercised when optional dep missing
    _frontmatter = None
    _HAS_FRONTMATTER = False


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
    if _HAS_FRONTMATTER:
        post = _frontmatter.load(path)
        return dict(post.metadata or {}), post.content or ""

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

USAGE_SCAN_EXTENSIONS: frozenset[str] = frozenset(
    {".py", ".md", ".mdx", ".yaml", ".yml", ".json", ".toml", ".rs", ".go", ".ts", ".tsx", ".js", ".jsx"}
)
USAGE_SCAN_MAX_BYTES = 512_000

_URL_IN_TEXT_RE = re.compile(r"https?://[^\s\)\"'<>]+", re.I)
_REST_PATH_RE = re.compile(r"\b(get|post|put|patch|delete)\s+[/`\"']", re.I)

_HTTP_CALL_ATTRS: frozenset[str] = frozenset(
    {
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "request",
        "head",
        "options",
        "send",
        "open",
    }
)


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


def _iter_skill_markdown_files(
    roots: Iterable[Path],
    skip_dir_names: frozenset[str] = DEFAULT_SKIP_DIR_NAMES,
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


def _infer_capabilities(corpus: str) -> list[str]:
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


def _detect_api_features_from_text(text: str) -> list[str]:
    """Surface API- and integration-oriented signals from prose or string literals."""

    found: list[str] = []
    low = text.lower()
    narrative_hits: tuple[tuple[str, str], ...] = (
        ("graphql", "GraphQL references"),
        ("websocket", "WebSocket references"),
        ("openapi", "OpenAPI or contract references"),
        ("swagger", "Swagger or OpenAPI tooling"),
        ("oauth", "OAuth or delegated auth"),
        ("bearer ", "Bearer token auth"),
        ("authorization:", "HTTP Authorization header usage"),
        ("api key", "API key handling"),
        ("webhook", "Webhook callbacks"),
        ("grpc", "gRPC-style RPC"),
        ("protobuf", "Protocol buffers"),
        ("json-rpc", "JSON-RPC"),
        ("xml-rpc", "XML-RPC"),
        ("soap", "SOAP-style services"),
        ("mcp", "MCP server or tool protocol"),
    )
    for needle, label in narrative_hits:
        if needle in low and label not in found:
            found.append(label)
    if _REST_PATH_RE.search(text):
        found.append("REST-style verb and path narration")
    if _URL_IN_TEXT_RE.search(text):
        found.append("Literal HTTP(S) URLs")
    for lib, label in (
        ("requests", "requests HTTP client"),
        ("httpx", "httpx HTTP client"),
        ("aiohttp", "aiohttp async client"),
        ("urllib3", "urllib3"),
        ("http.client", "http.client"),
        ("urllib.request", "urllib.request"),
        ("fetch(", "JavaScript fetch API"),
        ("axios", "axios HTTP client"),
    ):
        if lib in low:
            found.append(label)
    return list(dict.fromkeys(found))


def _ast_extract_imports(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _ast_http_api_calls(tree: ast.AST) -> list[str]:
    labels: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _HTTP_CALL_ATTRS:
            if isinstance(func.value, ast.Name):
                base = func.value.id
                labels.append(f"{base}.{func.attr}()")
            elif isinstance(func.value, ast.Attribute) and isinstance(func.value.value, ast.Name):
                labels.append(f"{func.value.value.id}.{func.value.attr}.{func.attr}()")
    return list(dict.fromkeys(labels))


def _scan_companion_python(skill_md: Path) -> tuple[list[str], list[str]]:
    """Inspect sibling ``*.py`` files for imports, HTTP calls, and capability keywords."""

    caps: list[str] = []
    api_labels: list[str] = []
    directory = skill_md.parent
    for py_path in sorted(directory.glob("*.py")):
        if py_path.name.startswith("."):
            continue
        try:
            src = py_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(src) > USAGE_SCAN_MAX_BYTES:
            continue
        try:
            tree = ast.parse(src, filename=str(py_path))
        except SyntaxError:
            continue
        imports = _ast_extract_imports(tree)
        corpus = " ".join(sorted(imports)) + " " + py_path.stem.lower()
        caps.extend(_infer_capabilities(corpus))
        api_labels.extend(_detect_api_features_from_text(src))
        for call in _ast_http_api_calls(tree):
            api_labels.append(f"Call pattern: {call}")
        if imports.intersection({"requests", "httpx", "aiohttp", "urllib"}):
            api_labels.append("HTTP client import alongside skill")
    return list(dict.fromkeys(caps)), list(dict.fromkeys(api_labels))


def _iter_files_for_usage_scan(
    roots: Sequence[Path],
    skip_dir_names: frozenset[str],
) -> list[Path]:
    collected: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in USAGE_SCAN_EXTENSIONS:
                continue
            if any(part in skip_dir_names for part in path.parts):
                continue
            try:
                if path.stat().st_size > USAGE_SCAN_MAX_BYTES:
                    continue
            except OSError:
                continue
            collected.append(path.resolve())
    return sorted(set(collected))


def _module_bucket(path: Path, anchor_roots: Sequence[Path]) -> str:
    """Top-level segment under a scan root (cross-module grouping)."""

    for root in anchor_roots:
        try:
            rel = path.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        parts = rel.parts
        return parts[0] if parts else "."
    return path.parent.name or "."


def _usage_needles_for_tool(tool: SkillToolInfo) -> list[tuple[str, str]]:
    """Return (substring, kind) pairs used to find references in the codebase."""

    needles: list[tuple[str, str]] = []
    path_posix = tool.path.as_posix()

    def _add(value: str, kind: str) -> None:
        stripped = value.strip()
        if len(stripped) < 3:
            return
        needles.append((stripped, kind))

    _add(tool.skill_id, "skill_id")
    _add(tool.name, "name")
    _add(path_posix, "skill_path")
    # Path ending with this skill's relative location helps match imports and docs.
    tail = "/".join(path_posix.split("/")[-2:]) if "/" in path_posix else path_posix
    if tail != path_posix:
        _add(tail, "path_tail")
    parent = tool.path.parent
    if parent.parts:
        _add(parent.as_posix(), "parent_path")
    return needles


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
    api_features: tuple[str, ...] = ()
    automatic_capabilities: tuple[str, ...] = ()

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
            "api_features": list(self.api_features),
            "automatic_capabilities": list(self.automatic_capabilities),
        }


@dataclass(frozen=True)
class UsageReference:
    """Single cross-module mention of a skill or its identifiers."""

    file_path: Path
    line_number: int
    line_text: str
    skill_id: str
    needle: str
    needle_kind: str
    module_bucket: str


@dataclass
class ToolIntelligenceReport:
    """Aggregated usage patterns, API signals, gaps, and improvement hints."""

    usage_by_skill: dict[str, list[UsageReference]]
    usage_patterns: dict[str, Any]
    cross_module_usage: dict[str, dict[str, int]]
    gaps: list[dict[str, Any]]
    suggestions: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "usage_by_skill": {
                sid: [self._usage_ref_dict(r) for r in refs]
                for sid, refs in self.usage_by_skill.items()
            },
            "usage_patterns": self.usage_patterns,
            "cross_module_usage": self.cross_module_usage,
            "gaps": self.gaps,
            "suggestions": self.suggestions,
        }

    @staticmethod
    def _usage_ref_dict(ref: UsageReference) -> dict[str, Any]:
        return {
            "file": str(ref.file_path),
            "line": ref.line_number,
            "text": ref.line_text.strip(),
            "skill_id": ref.skill_id,
            "needle": ref.needle,
            "needle_kind": ref.needle_kind,
            "module_bucket": ref.module_bucket,
        }


class ToolDiscovery:
    """Scan OpenClaw-style repositories for SKILL.md definitions and expose a catalog API."""

    def __init__(
        self,
        roots: Sequence[Path | str] | None = None,
        *,
        report_path: Path | str | None = None,
        skip_dir_names: frozenset[str] | None = None,
        usage_scan_roots: Sequence[Path | str] | None = None,
    ) -> None:
        module_dir = Path(__file__).resolve().parent
        default_roots = [module_dir]
        self._roots = [Path(r).resolve() for r in (roots or default_roots)]
        self._report_path = Path(report_path).resolve() if report_path else module_dir / "tool_discovery_report.md"
        self._skip_dir_names = skip_dir_names or DEFAULT_SKIP_DIR_NAMES
        default_scan = Path(__file__).resolve().parent
        self._usage_scan_roots = tuple(
            Path(p).resolve() for p in (usage_scan_roots or [default_scan])
        )
        self._cached_fingerprint: str | None = None
        self._cached_catalog: list[SkillToolInfo] | None = None

    @property
    def roots(self) -> tuple[Path, ...]:
        return tuple(self._roots)

    @property
    def report_path(self) -> Path:
        return self._report_path

    @property
    def usage_scan_roots(self) -> tuple[Path, ...]:
        return self._usage_scan_roots

    def _resolve_skill_absolute(self, tool: SkillToolInfo) -> Path | None:
        for root in self._roots:
            candidate = (root / tool.path).resolve()
            if candidate.is_file():
                return candidate
        return None

    def collect_usage_references(self, catalog: Sequence[SkillToolInfo]) -> dict[str, list[UsageReference]]:
        """Find mentions of each skill across scanned modules (see ``usage_scan_roots``)."""

        items = list(catalog)
        if not items:
            return {}

        scan_roots = list(self._usage_scan_roots)
        files = _iter_files_for_usage_scan(scan_roots, self._skip_dir_names)
        skill_abs = {tool.skill_id: self._resolve_skill_absolute(tool) for tool in items}
        usage_by_skill: dict[str, list[UsageReference]] = {tool.skill_id: [] for tool in items}

        for tool in items:
            needles = _usage_needles_for_tool(tool)
            own = skill_abs.get(tool.skill_id)
            seen_line_keys: set[tuple[str, int, str]] = set()
            for file_path in files:
                resolved = file_path.resolve()
                if own and resolved == own:
                    continue
                try:
                    text = file_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for lineno, line in enumerate(text.splitlines(), start=1):
                    for needle, kind in needles:
                        if needle not in line:
                            continue
                        dedupe_key = (str(resolved), lineno, needle)
                        if dedupe_key in seen_line_keys:
                            continue
                        seen_line_keys.add(dedupe_key)
                        usage_by_skill[tool.skill_id].append(
                            UsageReference(
                                file_path=file_path,
                                line_number=lineno,
                                line_text=line[:400],
                                skill_id=tool.skill_id,
                                needle=needle,
                                needle_kind=kind,
                                module_bucket=_module_bucket(file_path, scan_roots),
                            )
                        )
        return usage_by_skill

    def _usage_pattern_summary(self, usage_by_skill: Mapping[str, Sequence[UsageReference]]) -> dict[str, Any]:
        all_refs = [r for refs in usage_by_skill.values() for r in refs]
        by_extension = Counter(p.file_path.suffix.lower() or "(none)" for p in all_refs)
        by_kind = Counter(r.needle_kind for r in all_refs)
        by_bucket = Counter(r.module_bucket for r in all_refs)
        files_to_skills: dict[str, set[str]] = defaultdict(set)
        for sid, refs in usage_by_skill.items():
            for r in refs:
                files_to_skills[str(r.file_path.resolve())].add(sid)
        co_located: Counter[tuple[str, str]] = Counter()
        for skills in files_to_skills.values():
            ordered = sorted(skills)
            for i, a in enumerate(ordered):
                for b in ordered[i + 1 :]:
                    co_located[(a, b)] += 1
        return {
            "references_total": len(all_refs),
            "skills_with_references": sum(1 for refs in usage_by_skill.values() if refs),
            "by_extension": dict(by_extension),
            "by_needle_kind": dict(by_kind),
            "by_module_bucket": dict(by_bucket),
            "co_located_skill_pairs": [
                {"skills": [a, b], "shared_files": n} for (a, b), n in co_located.most_common(20)
            ],
        }

    def _cross_module_matrix(
        self, usage_by_skill: Mapping[str, Sequence[UsageReference]]
    ) -> dict[str, dict[str, int]]:
        matrix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for sid, refs in usage_by_skill.items():
            for r in refs:
                matrix[r.module_bucket][sid] += 1
        return {bucket: dict(counts) for bucket, counts in matrix.items()}

    def _identify_gaps(
        self,
        catalog: Sequence[SkillToolInfo],
        usage_by_skill: Mapping[str, Sequence[UsageReference]],
    ) -> list[dict[str, Any]]:
        gaps: list[dict[str, Any]] = []
        names = Counter(t.name.lower() for t in catalog)
        for tool in catalog:
            refs = list(usage_by_skill.get(tool.skill_id, ()))
            if not refs and len(catalog) > 1:
                gaps.append(
                    {
                        "skill_id": tool.skill_id,
                        "kind": "unreferenced_skill",
                        "detail": "No references found under configured scan roots.",
                    }
                )
            if not tool.actions:
                gaps.append(
                    {
                        "skill_id": tool.skill_id,
                        "kind": "missing_actions",
                        "detail": "No explicit actions in frontmatter or action sections.",
                    }
                )
            desc_low = tool.description.lower()
            api_narrative = any(
                token in desc_low for token in ("http", " api", "endpoint", "rest ", "graphql", "webhook")
            ) or any("api" in c.lower() or "network" in c.lower() for c in tool.capabilities)
            if api_narrative and not tool.api_features:
                gaps.append(
                    {
                        "skill_id": tool.skill_id,
                        "kind": "api_narrative_without_signals",
                        "detail": "Description or capabilities imply APIs but no concrete API markers were detected.",
                    }
                )
            if tool.api_features and not tool.limitations:
                gaps.append(
                    {
                        "skill_id": tool.skill_id,
                        "kind": "api_without_limitations",
                        "detail": "API-oriented signals detected; document constraints and failure modes.",
                    }
                )
            if names[tool.name.lower()] > 1:
                gaps.append(
                    {
                        "skill_id": tool.skill_id,
                        "kind": "duplicate_skill_name",
                        "detail": f"Name `{tool.name}` is shared by multiple skills; prefer unique ids in references.",
                    }
                )
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for g in gaps:
            key = (g["skill_id"], g["kind"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(g)
        return deduped

    def _suggestions_for_gaps(self, gaps: Sequence[Mapping[str, Any]]) -> list[str]:
        by_kind = Counter(str(g["kind"]) for g in gaps)
        suggestions: list[str] = []
        if by_kind["unreferenced_skill"]:
            suggestions.append(
                "Link high-value skills from README, agent prompts, or module docstrings so adopters can find them."
            )
        if by_kind["missing_actions"]:
            suggestions.append(
                "Add YAML `actions` or an explicit Actions section so automation can map goals to concrete steps."
            )
        if by_kind["api_narrative_without_signals"]:
            suggestions.append(
                "Where skills call remote services, name libraries, base URLs, or auth env vars so discovery can verify API usage."
            )
        if by_kind["api_without_limitations"]:
            suggestions.append(
                "Pair API capabilities with limitations (rate limits, scopes, destructive operations) for safer orchestration."
            )
        if by_kind["duplicate_skill_name"]:
            suggestions.append(
                "Disambiguate skills with duplicate display names using stable `id` fields and path-prefixed references."
            )
        if not suggestions and gaps:
            suggestions.append("Review gap entries and tighten frontmatter so tooling can reason about each skill.")
        return list(dict.fromkeys(suggestions))

    def compile_intelligence(self, catalog: Sequence[SkillToolInfo] | None = None) -> ToolIntelligenceReport:
        """Cross-module usage, pattern summaries, API gap analysis, and actionable suggestions."""

        items = list(catalog) if catalog is not None else self.discover_all()
        usage_by_skill = self.collect_usage_references(items)
        patterns = self._usage_pattern_summary(usage_by_skill)
        cross = self._cross_module_matrix(usage_by_skill)
        gaps = self._identify_gaps(items, usage_by_skill)
        suggestions = self._suggestions_for_gaps(gaps)
        return ToolIntelligenceReport(
            usage_by_skill=usage_by_skill,
            usage_patterns=patterns,
            cross_module_usage=cross,
            gaps=gaps,
            suggestions=suggestions,
        )

    def _parse_skill_file(self, absolute_path: Path) -> SkillToolInfo:
        parse_notes: list[str] = []
        try:
            metadata, body = _load_skill_markdown(absolute_path)
        except Exception as exc:
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
        inferred = _infer_capabilities(corpus)
        api_from_body = _detect_api_features_from_text(body)
        companion_caps, companion_api = _scan_companion_python(absolute_path)
        base_caps = list(dict.fromkeys([*meta_capabilities, *inferred]))
        auto_only = [cap for cap in companion_caps if cap not in base_caps]
        merged_caps = list(dict.fromkeys([*base_caps, *companion_caps]))
        capabilities_tuple = tuple(merged_caps)
        api_features_tuple = tuple(dict.fromkeys([*api_from_body, *companion_api]))
        automatic_caps_tuple = tuple(dict.fromkeys(auto_only))

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
            api_features=api_features_tuple,
            automatic_capabilities=automatic_caps_tuple,
        )

    def _relative_path(self, absolute_path: Path) -> Path:
        for root in self._roots:
            try:
                return absolute_path.relative_to(root)
            except ValueError:
                continue
        return absolute_path

    def _collect_skill_paths(self) -> list[Path]:
        return _iter_skill_markdown_files(self._roots, self._skip_dir_names)

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
                    " ".join(tool.api_features).lower(),
                    " ".join(tool.automatic_capabilities).lower(),
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

    def build_report_markdown(
        self,
        catalog: Sequence[SkillToolInfo] | None = None,
        *,
        intelligence: ToolIntelligenceReport | None = None,
    ) -> str:
        items = list(catalog) if catalog is not None else self.discover_all()
        generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines = [
            "# OpenClaw tool discovery report",
            "",
            f"_Generated {generated} from SKILL.md sources under: {', '.join(r.as_posix() for r in self._roots)}_",
            "",
            "## Summary",
            "",
            f"- Skills discovered: **{len(items)}**",
            f"- Report destination: `{self._report_path.as_posix()}`",
            f"- Usage scan roots: **{', '.join(r.as_posix() for r in self._usage_scan_roots)}**",
            "",
        ]

        if not items:
            lines.extend(
                [
                    "No `SKILL.md` files were found. Add skills by creating `SKILL.md` files with YAML frontmatter "
                    "(`name`, `description`, optional `actions`, `capabilities`, `limitations`).",
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
            lines.extend(["", "### API and integration signals", ""])
            if tool.api_features:
                lines.extend(f"- {feat}" for feat in tool.api_features)
            else:
                lines.append("- _No API-oriented markers detected in SKILL.md body or sibling Python._")
            lines.extend(["", "### Automatic capabilities (companion code)", ""])
            if tool.automatic_capabilities:
                lines.extend(f"- {cap}" for cap in tool.automatic_capabilities)
            else:
                lines.append("- _No extra capability labels inferred from sibling ``*.py`` files._")
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

        if intelligence is not None:
            lines.extend(self._intelligence_markdown_section(intelligence))

        return "\n".join(lines).rstrip() + "\n"

    def _intelligence_markdown_section(self, intelligence: ToolIntelligenceReport) -> list[str]:
        p = intelligence.usage_patterns
        lines = [
            "",
            "## Intelligence: usage, gaps, and suggestions",
            "",
            "### Usage pattern summary",
            "",
            f"- Total reference hits: **{p.get('references_total', 0)}**",
            f"- Skills referenced elsewhere: **{p.get('skills_with_references', 0)}**",
            "",
            "#### References by file extension",
            "",
        ]
        for ext, n in sorted(p.get("by_extension", {}).items(), key=lambda x: -x[1])[:20]:
            lines.append(f"- `{ext}`: {n}")
        lines.extend(["", "#### References by needle kind", ""])
        for kind, n in sorted(p.get("by_needle_kind", {}).items(), key=lambda x: -x[1]):
            lines.append(f"- `{kind}`: {n}")
        lines.extend(["", "### Cross-module usage (bucket → skill hits)", ""])
        if not intelligence.cross_module_usage:
            lines.append("- _No cross-module references detected._")
        else:
            for bucket in sorted(intelligence.cross_module_usage.keys()):
                inner = intelligence.cross_module_usage[bucket]
                parts = ", ".join(f"`{sid}`×{c}" for sid, c in sorted(inner.items(), key=lambda x: -x[1])[:12])
                lines.append(f"- **{bucket}**: {parts or '—'}")
        lines.extend(["", "### Gaps", ""])
        if not intelligence.gaps:
            lines.append("- _No automated gaps flagged._")
        else:
            for g in intelligence.gaps[:80]:
                lines.append(
                    f"- `{g['skill_id']}` — **{g['kind']}**: {g.get('detail', '')}"
                )
            if len(intelligence.gaps) > 80:
                lines.append(f"- _…and {len(intelligence.gaps) - 80} more._")
        lines.extend(["", "### Suggested improvements", ""])
        if intelligence.suggestions:
            lines.extend(f"- {s}" for s in intelligence.suggestions)
        else:
            lines.append("- _No suggestions; catalog looks consistent with usage heuristics._")
        lines.append("")
        return lines

    def write_report(
        self,
        catalog: Sequence[SkillToolInfo] | None = None,
        *,
        intelligence: ToolIntelligenceReport | None = None,
    ) -> Path:
        markdown = self.build_report_markdown(catalog, intelligence=intelligence)
        self._report_path.parent.mkdir(parents=True, exist_ok=True)
        self._report_path.write_text(markdown, encoding="utf-8")
        return self._report_path


def generate_tool_discovery_report(
    roots: Sequence[Path | str] | None = None,
    *,
    report_path: Path | str | None = None,
    usage_scan_roots: Sequence[Path | str] | None = None,
    with_intelligence: bool = False,
) -> Path:
    """Convenience helper that scans roots and writes `tool_discovery_report.md`."""

    discovery = ToolDiscovery(
        roots=roots,
        report_path=report_path,
        usage_scan_roots=usage_scan_roots,
    )
    catalog = discovery.discover_all()
    intel = discovery.compile_intelligence(catalog) if with_intelligence else None
    discovery.write_report(catalog, intelligence=intel)
    return discovery.report_path


def _parse_cli_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover OpenClaw SKILL.md tools and emit tool_discovery_report.md.",
    )
    parser.add_argument(
        "--root",
        action="append",
        dest="roots",
        help="Root directory to scan (repeatable). Defaults to the repository root containing this file.",
    )
    parser.add_argument(
        "--report",
        help="Output path for Markdown report (default: <repo>/tool_discovery_report.md).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass cached scan results.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print catalog JSON to stdout instead of only writing the report.",
    )
    parser.add_argument(
        "--search",
        metavar="QUERY",
        help="Run a search and print matching skill names as JSON.",
    )
    parser.add_argument(
        "--info",
        metavar="IDENTIFIER",
        help="Print a single skill record as JSON (name, id, or path).",
    )
    parser.add_argument(
        "--usage-scan-root",
        action="append",
        dest="usage_scan_roots",
        metavar="DIR",
        help="Directory roots for cross-module usage scanning (repeatable). Defaults to this file's directory.",
    )
    parser.add_argument(
        "--intelligence",
        action="store_true",
        help="Append usage patterns, cross-module matrix, gaps, and suggestions to the Markdown report.",
    )
    parser.add_argument(
        "--intelligence-json",
        action="store_true",
        help="Print compile_intelligence() JSON to stdout (catalog-aware usage and gap analysis).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_cli_args(argv)
    default_root = Path(__file__).resolve().parent
    roots = [Path(r).resolve() for r in args.roots] if args.roots else [default_root]
    report_path = Path(args.report).resolve() if args.report else None
    usage_scan = [Path(p).resolve() for p in args.usage_scan_roots] if args.usage_scan_roots else None
    discovery = ToolDiscovery(roots=roots, report_path=report_path, usage_scan_roots=usage_scan)

    if args.info:
        tool = discovery.get_tool_info(args.info)
        if tool is None:
            print(json.dumps({"error": "not_found", "identifier": args.info}, indent=2))
            return 2
        print(json.dumps(tool.to_dict(), indent=2))
        return 0

    if args.search:
        matches = discovery.search_tools(args.search)
        print(json.dumps([tool.to_dict() for tool in matches], indent=2))
        return 0

    catalog = discovery.discover_all(force=args.force)
    intel = discovery.compile_intelligence(catalog) if (args.intelligence or args.intelligence_json) else None
    written = discovery.write_report(catalog, intelligence=intel if args.intelligence else None)
    print(f"Wrote report with {len(catalog)} skills to {written}", file=sys.stderr)
    if args.json:
        print(json.dumps([tool.to_dict() for tool in catalog], indent=2))
    if args.intelligence_json and intel is not None:
        print(json.dumps(intel.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
