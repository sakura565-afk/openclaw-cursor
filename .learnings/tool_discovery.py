#!/usr/bin/env python3
"""Discover, catalog, and document OpenClaw skills defined by SKILL.md files.

Scans skill directories (repository root and optional ``~/.openclaw`` skill trees),
parses YAML frontmatter and body sections, infers capabilities, and emits a JSON
tool registry. Use ``--search`` for keyword lookup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
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


def _openclaw_home() -> Path:
    override = os.environ.get("OPENCLAW_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".openclaw"


def _default_discovery_roots() -> list[Path]:
    """Repository workspace (parent of ``.learnings``) plus OpenClaw skill dirs if present."""

    module_dir = Path(__file__).resolve().parent
    roots: list[Path] = [module_dir.parent.resolve()]
    oc = _openclaw_home()
    for candidate in (oc / "skills", oc / "workspace" / "skills"):
        try:
            if candidate.is_dir():
                roots.append(candidate.resolve())
        except OSError:
            continue
    seen: set[Path] = set()
    unique: list[Path] = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique


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
    """Scan OpenClaw-style repositories for SKILL.md definitions and expose a catalog API."""

    def __init__(
        self,
        roots: Sequence[Path | str] | None = None,
        *,
        report_path: Path | str | None = None,
        skip_dir_names: frozenset[str] | None = None,
    ) -> None:
        module_dir = Path(__file__).resolve().parent
        default_roots = _default_discovery_roots()
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

    def build_tools_registry(
        self, catalog: Sequence[SkillToolInfo] | None = None
    ) -> dict[str, Any]:
        """Full JSON-serializable registry including scan roots and metadata."""

        items = list(catalog) if catalog is not None else self.discover_all()
        return {
            "registry_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "roots": [r.as_posix() for r in self._roots],
            "tool_count": len(items),
            "tools": [tool.to_dict() for tool in items],
        }

    def build_report_markdown(self, catalog: Sequence[SkillToolInfo] | None = None) -> str:
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


def generate_tool_discovery_report(
    roots: Sequence[Path | str] | None = None,
    *,
    report_path: Path | str | None = None,
) -> Path:
    """Convenience helper that scans roots and writes `tool_discovery_report.md`."""

    discovery = ToolDiscovery(roots=roots, report_path=report_path)
    discovery.write_report()
    return discovery.report_path


def _parse_cli_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover OpenClaw SKILL.md tools, emit a JSON registry, and search by keyword.",
    )
    parser.add_argument(
        "--root",
        action="append",
        dest="roots",
        help="Root directory to scan (repeatable). Defaults to workspace root and ~/.openclaw skill dirs.",
    )
    parser.add_argument(
        "--report",
        help="Output path for Markdown report when --write-report is set "
        "(default: .learnings/tool_discovery_report.md).",
    )
    parser.add_argument(
        "--registry-out",
        metavar="PATH",
        help="Write the full tools registry JSON to this file.",
    )
    parser.add_argument(
        "--write-report",
        action="store_true",
        help="Write a Markdown report in addition to registry output.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print registry JSON to stdout (use with --registry-out and/or --write-report).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass cached scan results.",
    )
    parser.add_argument(
        "--search",
        metavar="QUERY",
        help="Search tools by keyword(s); prints a JSON array of matches to stdout.",
    )
    parser.add_argument(
        "--keyword",
        metavar="QUERY",
        help="Alias for --search.",
    )
    parser.add_argument(
        "--info",
        metavar="IDENTIFIER",
        help="Print a single skill record as JSON (name, id, or path).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_cli_args(argv)
    default_roots = _default_discovery_roots()
    roots = [Path(r).resolve() for r in args.roots] if args.roots else default_roots
    module_dir = Path(__file__).resolve().parent
    report_path = Path(args.report).resolve() if args.report else None
    discovery = ToolDiscovery(roots=roots, report_path=report_path or (module_dir / "tool_discovery_report.md"))

    if args.info:
        tool = discovery.get_tool_info(args.info)
        if tool is None:
            print(json.dumps({"error": "not_found", "identifier": args.info}, indent=2))
            return 2
        print(json.dumps(tool.to_dict(), indent=2))
        return 0

    search_q = args.search or args.keyword
    if search_q:
        matches = discovery.search_tools(search_q)
        payload = {
            "query": search_q,
            "match_count": len(matches),
            "tools": [tool.to_dict() for tool in matches],
        }
        print(json.dumps(payload, indent=2))
        return 0

    catalog = discovery.discover_all(force=args.force)
    registry = discovery.build_tools_registry(catalog=catalog)

    if args.registry_out:
        out = Path(args.registry_out).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(registry, indent=2), encoding="utf-8")
        print(f"Wrote registry ({registry['tool_count']} tools) to {out}", file=sys.stderr)

    if args.write_report:
        written = discovery.write_report(catalog)
        print(f"Wrote Markdown report to {written}", file=sys.stderr)

    if not args.quiet:
        print(json.dumps(registry, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
