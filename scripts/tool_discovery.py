#!/usr/bin/env python3
"""Discover scripts, repo-local tools/skills, and build a searchable JSON index."""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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

SKILL_SCAN_SKIP_DIR_NAMES: frozenset[str] = frozenset(
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

SKILL_CAPABILITY_SIGNALS: tuple[tuple[str, str], ...] = (
    ("filesystem", "Filesystem and local file access"),
    ("network", "Network and remote API access"),
    ("http", "Network and remote API access"),
    ("api", "Network and remote API access"),
    ("subprocess", "Process and shell execution"),
    ("database", "Database and structured storage"),
    ("sqlite", "Database and structured storage"),
    ("queue", "Queues and asynchronous workloads"),
    ("telegram", "Messaging and notifications"),
    ("monitor", "Monitoring and observability"),
    ("analytics", "Analytics and reporting"),
    ("model", "Model lifecycle and inference"),
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

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+", re.IGNORECASE)


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


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return cleaned.strip("-") or "item"


def _parse_simple_yaml_map(block: str) -> dict[str, Any]:
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


def _infer_skill_capabilities(corpus: str) -> list[str]:
    lower = corpus.lower()
    hits: list[str] = []
    for needle, label in SKILL_CAPABILITY_SIGNALS:
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


def extract_keyword_vocab(*parts: str) -> list[str]:
    seen: dict[str, None] = {}
    for part in parts:
        for tok in _TOKEN_SPLIT.split(part.lower()):
            if len(tok) >= 2 and tok not in seen:
                seen[tok] = None
    return list(seen.keys())


def discover_script_paths(root: Path) -> list[Path]:
    scripts_dir = root / "scripts"
    if not scripts_dir.exists():
        return []
    return sorted(path for path in scripts_dir.glob("*.py") if path.name != "__init__.py")


def discover_skill_markdown_paths(root: Path) -> list[Path]:
    found: list[Path] = []
    for rel in ("tools", "skills"):
        base = root / rel
        if not base.is_dir():
            continue
        for path in base.rglob("SKILL.md"):
            if any(part in SKILL_SCAN_SKIP_DIR_NAMES for part in path.parts):
                continue
            found.append(path.resolve())
    skills_src = root / "src" / "skills"
    if skills_src.is_dir():
        for path in skills_src.rglob("SKILL.md"):
            if any(part in SKILL_SCAN_SKIP_DIR_NAMES for part in path.parts):
                continue
            found.append(path.resolve())
    return sorted(set(found))


def discover_skill_python_paths(root: Path) -> list[Path]:
    d = root / "src" / "skills"
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.py") if p.name != "__init__.py")


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
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            print(f"tool_discovery: skipping {path.relative_to(root)} ({exc})", file=sys.stderr)
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


def parse_skill_markdown_entry(root: Path, absolute_path: Path) -> dict[str, Any]:
    root = root.resolve()
    absolute_path = absolute_path.resolve()
    parse_notes: list[str] = []
    try:
        metadata, body = _load_skill_markdown(absolute_path)
    except OSError as exc:
        parse_notes.append(f"Read error: {exc}")
        metadata, body = {}, ""
    except Exception as exc:  # pragma: no cover - defensive
        parse_notes.append(f"Frontmatter parse error: {exc}")
        metadata, body = {}, absolute_path.read_text(encoding="utf-8", errors="replace")

    try:
        rel_path = absolute_path.relative_to(root)
    except ValueError:
        rel_path = absolute_path

    name_candidate = metadata.get("name") or metadata.get("title")
    name = str(name_candidate).strip() if name_candidate else absolute_path.parent.name or absolute_path.stem

    description = str(metadata.get("description", "")).strip()
    if not description:
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
        description = paragraphs[0].replace("\n", " ") if paragraphs else "No description provided."

    actions = _normalize_str_list(metadata.get("actions"))
    if not actions:
        actions = _extract_section_bullets(body, ACTION_SECTION_TITLES)
    actions = list(dict.fromkeys(actions))

    limitations = _normalize_str_list(
        metadata.get("limitations") or metadata.get("constraints") or metadata.get("caveats")
    )
    if not limitations:
        limitations = _extract_section_bullets(body, LIMITATION_SECTION_TITLES)
    if not limitations:
        limitations = _infer_limitations_from_text(body)
    limitations = list(dict.fromkeys(limitations))

    meta_capabilities = _normalize_str_list(metadata.get("capabilities"))
    corpus = " ".join([name, description, body])
    inferred = _infer_skill_capabilities(corpus)
    capabilities = list(dict.fromkeys([*meta_capabilities, *inferred]))

    reserved_keys = {
        "name",
        "title",
        "description",
        "actions",
        "limitations",
        "constraints",
        "caveats",
        "capabilities",
        "id",
    }
    extra_meta = {
        k: v
        for k, v in metadata.items()
        if k not in reserved_keys and isinstance(v, (str, int, float, bool, list, dict))
    }

    path_slug = _slugify(rel_path.as_posix().replace("/", "-"))
    skill_id = str(metadata.get("id") or "").strip() or path_slug or _slugify(name)

    keywords = extract_keyword_vocab(
        name,
        description,
        rel_path.as_posix(),
        skill_id,
        " ".join(actions),
        " ".join(capabilities),
        " ".join(limitations),
        json.dumps(extra_meta, sort_keys=True, default=str),
    )

    return {
        "entry_type": "skill_markdown",
        "id": f"skill_md:{skill_id}",
        "path": rel_path.as_posix(),
        "name": name,
        "description": description,
        "keywords": keywords,
        "skill_id": skill_id,
        "actions": actions,
        "capabilities": capabilities,
        "limitations": limitations,
        "parse_notes": parse_notes,
        "metadata": extra_meta,
    }


def parse_skill_python_entry(root: Path, path: Path) -> dict[str, Any] | None:
    root = root.resolve()
    path = path.resolve()
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        rel = path.relative_to(root)
        print(f"tool_discovery: skipping skill module {rel} ({exc})", file=sys.stderr)
        return None
    name = path.stem
    description = extract_description(tree)
    imports = extract_imports(tree)
    functions = extract_functions(tree)
    commands = extract_cli_commands(tree)
    rel = path.relative_to(root)
    capabilities = infer_capabilities(name, description, commands, functions)
    corpus = " ".join([name, description, *capabilities, *functions, *commands])
    skill_caps = _infer_skill_capabilities(corpus)
    merged_caps = list(dict.fromkeys([*capabilities, *skill_caps]))
    keywords = extract_keyword_vocab(
        name,
        description,
        rel.as_posix(),
        " ".join(merged_caps),
        " ".join(commands),
        " ".join(functions),
        " ".join(sorted(imports)),
    )
    return {
        "entry_type": "skill_python",
        "id": f"skill_py:{name}",
        "path": rel.as_posix(),
        "name": name,
        "description": description,
        "keywords": keywords,
        "capabilities": merged_caps,
        "imports": sorted(imports),
        "functions": functions,
        "commands": commands,
    }


def script_entry_from_profile(profile: ToolProfile) -> dict[str, Any]:
    base = profile.to_dict()
    keywords = extract_keyword_vocab(
        profile.name,
        profile.description,
        str(profile.path),
        " ".join(profile.capabilities),
        " ".join(profile.commands),
        " ".join(profile.functions),
        " ".join(sorted(profile.imports)),
        " ".join(profile.io_profile),
        " ".join(profile.dependencies),
    )
    return {
        "entry_type": "script",
        "id": f"script:{profile.name}",
        "keywords": keywords,
        **base,
    }


def build_searchable_index(root: Path) -> dict[str, Any]:
    root = root.resolve()
    entries: list[dict[str, Any]] = []
    for profile in analyze_scripts(root):
        entries.append(script_entry_from_profile(profile))
    for md_path in discover_skill_markdown_paths(root):
        entries.append(parse_skill_markdown_entry(root, md_path))
    for py_path in discover_skill_python_paths(root):
        row = parse_skill_python_entry(root, py_path)
        if row is not None:
            entries.append(row)
    return {
        "version": INDEX_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(root),
        "entries": entries,
    }


def _json_safe_metadata(obj: Any) -> Any:
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _json_safe_metadata(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_json_safe_metadata(v) for v in obj]
    return str(obj)


def write_json_index(index: Mapping[str, Any], output: Path) -> Path:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = _json_safe_metadata(dict(index))
    output.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
    return output


def load_json_index(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Index file must contain a JSON object")
    return data


def tokenize_search_query(query: str, extra_keywords: Iterable[str] | None) -> list[str]:
    tokens: list[str] = []
    for chunk in [query, *(extra_keywords or ())]:
        for tok in re.split(r"\s+", chunk.lower().strip()):
            if len(tok) >= 2:
                tokens.append(tok)
    seen: dict[str, None] = {}
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen[t] = None
            out.append(t)
    return out


def score_index_entry_keywords(entry: Mapping[str, Any], tokens: Sequence[str]) -> tuple[int, list[str]]:
    keyword_set = {str(k).lower() for k in (entry.get("keywords") or [])}
    haystack_parts: list[str] = [
        str(entry.get("name", "")),
        str(entry.get("description", "")),
        str(entry.get("entry_type", "")),
        str(entry.get("path", "")),
        str(entry.get("skill_id", "")),
    ]
    for key in ("capabilities", "commands", "functions", "imports", "limitations", "actions", "examples"):
        val = entry.get(key)
        if isinstance(val, list):
            haystack_parts.append(" ".join(str(v) for v in val))
        elif val is not None:
            haystack_parts.append(str(val))
    haystack = " ".join(haystack_parts).lower()
    matched: list[str] = []
    score = 0
    for token in tokens:
        if token in keyword_set:
            score += 4
            matched.append(token)
        elif token in haystack:
            score += 2
            matched.append(token)
    return score, matched


def search_index_entries(
    entries: Sequence[Mapping[str, Any]],
    tokens: Sequence[str],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if not tokens:
        return []
    ranked: list[tuple[int, dict[str, Any]]] = []
    for entry in entries:
        score, matched = score_index_entry_keywords(entry, tokens)
        if score <= 0:
            continue
        row = dict(entry)
        row["match_score"] = score
        row["matched_tokens"] = list(dict.fromkeys(matched))
        ranked.append((score, row))
    ranked.sort(key=lambda row: (-row[0], str(row[1].get("name", "")).lower()))
    return [row for _, row in ranked[: max(1, limit)]]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover scripts, tools/skills, build a JSON index, and search by keyword.",
    )
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

    index_cmd = subparsers.add_parser("index", help="Scan scripts, tools/, skills/, src/skills and write JSON index")
    index_cmd.add_argument(
        "--output",
        default=None,
        help="Output JSON path (default: <root>/tool_skill_index.json)",
    )
    index_cmd.add_argument(
        "--stdout",
        action="store_true",
        help="Print the full index JSON to stdout instead of only writing a file",
    )

    search_cmd = subparsers.add_parser("search", help="Keyword search over the unified tool/skill index")
    search_cmd.add_argument("query", nargs="?", default="", help="Keyword query (space-separated)")
    search_cmd.add_argument("--keywords", nargs="*", default=None, help="Additional explicit keywords")
    search_cmd.add_argument(
        "--from-index",
        dest="from_index",
        metavar="PATH",
        help="Load entries from a JSON index file instead of rescanning the tree",
    )
    search_cmd.add_argument("--limit", type=int, default=20, help="Maximum number of hits")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()

    if args.command == "index":
        index = build_searchable_index(root)
        out_path = Path(args.output) if args.output else root / "tool_skill_index.json"
        written = write_json_index(index, out_path)
        if args.stdout:
            print(json.dumps(_json_safe_metadata(index), indent=2))
        else:
            summary = {
                "path": str(written),
                "entry_count": len(index["entries"]),
                "version": index["version"],
                "generated_at": index["generated_at"],
            }
            print(json.dumps(summary, indent=2))
        return 0

    if args.command == "search":
        tokens = tokenize_search_query(args.query, args.keywords)
        if not tokens:
            print(json.dumps({"error": "empty_query", "message": "Provide a query or --keywords"}, indent=2))
            return 2
        if args.from_index:
            data = load_json_index(Path(args.from_index).resolve())
            entries = data.get("entries") or []
            if not isinstance(entries, list):
                print(json.dumps({"error": "invalid_index", "message": "entries must be a list"}, indent=2))
                return 2
        else:
            entries = build_searchable_index(root)["entries"]
        hits = search_index_entries(entries, tokens, limit=max(1, args.limit))
        print(
            json.dumps(
                {
                    "query": args.query,
                    "keywords": list(args.keywords or []),
                    "tokens": tokens,
                    "results": hits,
                },
                indent=2,
            )
        )
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
