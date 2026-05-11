#!/usr/bin/env python3
"""Discover and catalog OpenClaw tools (scripts/modules) and skills (SKILL.md).

Scans the workspace for SKILL.md files and Python tool entry points, builds a
searchable index (JSON), suggests tools for natural-language tasks, and tracks
usage counts for ranking hints. Default catalog location::

    <OPENCLAW_WORKSPACE>/catalog/

or ``./.openclaw/catalog`` when no OpenClaw workspace is set. Override with
``--catalog-dir`` or ``OPENCLAW_TOOL_CATALOG_DIR``.
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


INDEX_VERSION = 1
USAGE_VERSION = 1

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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_openclaw_workspace() -> Path | None:
    raw = os_getenv("OPENCLAW_WORKSPACE", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if p.exists():
            return p.resolve()
    home_ws = Path.home() / ".openclaw" / "workspace"
    if home_ws.exists():
        return home_ws.resolve()
    return None


def os_getenv(key: str, default: str = "") -> str:
    import os

    return os.environ.get(key, default) or default


def default_catalog_dir(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    env_dir = os_getenv("OPENCLAW_TOOL_CATALOG_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    ws = default_openclaw_workspace()
    if ws is not None:
        return (ws / "catalog").resolve()
    return (Path.cwd() / ".openclaw" / "catalog").resolve()


@dataclass
class SkillProfile:
    """Parsed metadata from a SKILL.md file."""

    name: str
    path: Path
    title: str
    description: str
    use_cases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "skill",
            "name": self.name,
            "path": str(self.path),
            "title": self.title,
            "description": self.description,
            "use_cases": self.use_cases,
            "tags": self.tags,
        }


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
    kind: str = "script"
    use_cases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "tool",
            "tool_kind": self.kind,
            "name": self.name,
            "path": str(self.path),
            "description": self.description,
            "imports": sorted(self.imports),
            "functions": self.functions,
            "commands": self.commands,
            "capabilities": self.capabilities,
            "use_cases": self.use_cases,
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


def discover_module_tool_paths(root: Path) -> list[Path]:
    """Python modules under src/ that define argparse and a __main__ guard (runnable tools)."""
    src = root / "src"
    if not src.exists():
        return []
    out: list[Path] = []
    for path in sorted(src.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "ArgumentParser" not in text and "argparse" not in text:
            continue
        if '__name__ == "__main__"' not in text and "__name__ == '__main__'" not in text:
            continue
        out.append(path)
    return out


def _slug_from_skill_path(skill_path: Path, roots: Iterable[Path]) -> str:
    for root in roots:
        try:
            rel = skill_path.relative_to(root)
            parts = rel.parts[:-1]
            if parts:
                return "_".join(parts).replace(" ", "_").replace("/", "_")
        except ValueError:
            continue
    parent = skill_path.parent.name or skill_path.stem
    return parent.replace(" ", "_")


def discover_skill_paths(roots: list[Path], max_files: int = 500) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        root = root.resolve()
        if not root.exists():
            continue
        for path in root.rglob("SKILL.md"):
            if path.resolve() in seen:
                continue
            # Skip huge dependency trees
            if any(p in path.parts for p in ("node_modules", ".git", "__pycache__", ".venv", "venv")):
                continue
            seen.add(path.resolve())
            found.append(path)
            if len(found) >= max_files:
                return sorted(found, key=lambda p: str(p))
    return sorted(found, key=lambda p: str(p))


def parse_skill_markdown(path: Path, roots: list[Path]) -> SkillProfile:
    text = path.read_text(encoding="utf-8", errors="replace")
    title = path.stem
    description = ""
    use_cases: list[str] = []
    tags: list[str] = []

    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            body = text[end + 4 :].lstrip("\n")
        else:
            body = text
    else:
        body = text

    lines = body.splitlines()
    for line in lines:
        s = line.strip()
        if s.startswith("# ") and not s.startswith("##"):
            title = s[2:].strip() or title
            break

    for i, line in enumerate(lines):
        s = line.strip()
        if re.match(r"(?i)^##\s+use\s+cases", s) or re.match(r"(?i)^##\s+when\s+to\s+use", s):
            for ul in lines[i + 1 :]:
                t = ul.strip()
                if t.startswith("##"):
                    break
                m = re.match(r"^[-*]\s+(.+)", t)
                if m:
                    use_cases.append(m.group(1).strip())
            break

    # Description: first substantial paragraph after optional frontmatter / title
    desc_lines: list[str] = []
    in_para = False
    for line in lines:
        st = line.strip()
        if not st or st.startswith("#"):
            if in_para and desc_lines:
                break
            continue
        if st.startswith("---") or st.startswith("```"):
            continue
        if st.startswith(("- ", "* ", "1.")):
            if desc_lines:
                break
            continue
        desc_lines.append(st)
        in_para = True
        if len(desc_lines) >= 4:
            break
    description = " ".join(desc_lines).strip() or f"Skill defined in {path.name}."

    # YAML-ish frontmatter tags (optional)
    if text.startswith("---"):
        fm_end = text.find("\n---", 3)
        if fm_end != -1:
            fm = text[3:fm_end]
            for raw in fm.splitlines():
                if raw.lower().startswith("tags:"):
                    rest = raw.split(":", 1)[1].strip()
                    if rest.startswith("["):
                        tags = [t.strip() for t in rest.strip("[]").split(",") if t.strip()]
                    else:
                        tags = [rest] if rest else []

    name = _slug_from_skill_path(path, roots)
    if not use_cases and description:
        use_cases = [f"Apply when: {description[:160]}{'…' if len(description) > 160 else ''}"]

    return SkillProfile(
        name=name,
        path=path,
        title=title,
        description=description,
        use_cases=use_cases,
        tags=tags,
    )


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


def infer_use_cases(description: str, capabilities: list[str], commands: list[str]) -> list[str]:
    uc: list[str] = []
    if description and description != "No module docstring available.":
        uc.append(description)
    for cap in capabilities[:5]:
        uc.append(f"Use for {cap.lower()}.")
    for cmd in commands[:5]:
        uc.append(f"CLI subcommand `{cmd}`.")
    return uc[:8] or ["General automation entry point."]


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


def _analyze_py_tool(path: Path, root: Path, kind: str) -> ToolProfile:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    if kind == "module":
        name = "_".join(rel.with_suffix("").parts).replace("\\", "_")
    else:
        name = path.stem
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        return ToolProfile(
            name=name,
            path=rel,
            description=f"(unparsed) {type(exc).__name__}: {exc}",
            capabilities=["General utility automation"],
            risk_level="low",
            io_profile=["in-memory"],
            kind=kind,
            use_cases=["Fix syntax or encoding issues before using static analysis on this file."],
            examples=[f"# Inspect {rel}"],
        )
    imports = extract_imports(tree)
    functions = extract_functions(tree)
    commands = extract_cli_commands(tree)
    description = extract_description(tree)
    capabilities = infer_capabilities(name, description, commands, functions)
    io_profile = infer_io_profile(imports, source)
    risk_level = infer_risk_level(imports, commands)
    use_cases = infer_use_cases(description, capabilities, commands)
    mod_base = ".".join(rel.with_suffix("").parts)
    examples: list[str] = []
    if kind == "script":
        examples.append(f"python -m scripts.{name}")
    else:
        examples.append(f"python -m {mod_base}")
    if commands:
        examples = [f"{examples[0]} {cmd}" for cmd in commands[:3]]
    return ToolProfile(
        name=name,
        path=rel,
        description=description,
        imports=imports,
        functions=functions,
        commands=commands,
        capabilities=capabilities,
        risk_level=risk_level,
        io_profile=io_profile,
        kind=kind,
        use_cases=use_cases,
        examples=examples,
    )


def analyze_scripts(root: Path) -> list[ToolProfile]:
    paths = discover_script_paths(root)
    raw: list[ToolProfile] = []
    for path in paths:
        raw.append(_analyze_py_tool(path, root, "script"))
    enrich_dependency_graph(raw)
    for profile in raw:
        profile.examples = build_examples(profile)
    return raw


def analyze_module_tools(root: Path) -> list[ToolProfile]:
    paths = discover_module_tool_paths(root)
    raw: list[ToolProfile] = []
    for path in paths:
        raw.append(_analyze_py_tool(path, root, "module"))
    for profile in raw:
        profile.examples = build_examples(profile)
    return raw


def discover_all_tools(root: Path) -> list[ToolProfile]:
    scripts = analyze_scripts(root)
    modules = analyze_module_tools(root)
    combined = scripts + modules
    # Re-run dependency enrichment across script names only for script-script edges
    enrich_dependency_graph(scripts)
    return combined


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
    if profile.kind == "module" and profile.examples:
        return profile.examples
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


def score_tool_for_goal(profile: ToolProfile, goal: str, context: str, usage_count: int = 0) -> tuple[int, list[str]]:
    goal_lower = goal.lower()
    context_lower = context.lower()
    reasons: list[str] = []
    score = 0

    for capability in profile.capabilities:
        cap_words = capability.lower().split()
        if any(word in goal_lower for word in cap_words if len(word) > 2):
            score += 3
            reasons.append(f"Capability match: {capability}")
    for uc in profile.use_cases:
        for word in re.findall(r"[a-zA-Z]{4,}", uc.lower()):
            if len(word) > 3 and word in goal_lower:
                score += 1
                reasons.append(f"Use-case keyword: {word}")
                break
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
    if usage_count:
        bonus = min(5, 1 + int(math.log1p(usage_count)))
        score += bonus
        reasons.append(f"Usage history bonus (+{bonus})")
    return score, reasons


def score_skill_for_goal(skill: SkillProfile, goal: str, context: str) -> tuple[int, list[str]]:
    goal_lower = goal.lower()
    context_lower = context.lower()
    reasons: list[str] = []
    score = 0
    blob = f"{skill.title} {skill.description} {' '.join(skill.use_cases)} {' '.join(skill.tags)}".lower()
    for word in re.findall(r"[a-zA-Z]{4,}", goal_lower + " " + context_lower):
        if len(word) > 3 and word in blob:
            score += 2
            reasons.append(f"Topic match: {word}")
    for tag in skill.tags:
        if tag.lower() in goal_lower or tag.lower() in context_lower:
            score += 3
            reasons.append(f"Tag match: {tag}")
    return score, reasons


def suggest_tools(
    profiles: list[ToolProfile],
    goal: str,
    context: str,
    top_n: int = 5,
    usage_counts: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    usage_counts = usage_counts or {}
    ranked: list[tuple[int, ToolProfile, list[str]]] = []
    for profile in profiles:
        score, reasons = score_tool_for_goal(profile, goal, context, usage_counts.get(profile.name, 0))
        ranked.append((score, profile, reasons))
    ranked.sort(key=lambda row: (row[0], len(row[2]), row[1].name), reverse=True)

    suggestions: list[dict[str, Any]] = []
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


def suggest_relevant_tools(
    task: str,
    *,
    context: str = "",
    root: Path | None = None,
    skills: list[SkillProfile] | None = None,
    tools: list[ToolProfile] | None = None,
    top_n: int = 5,
    catalog_dir: Path | None = None,
) -> dict[str, Any]:
    """Suggest skills and tools for a natural-language *task* description."""
    root = (root or Path.cwd()).resolve()
    if tools is None:
        tools = discover_all_tools(root)
    if skills is None:
        roots = scan_roots(root)
        skills = [parse_skill_markdown(p, roots) for p in discover_skill_paths(roots)]
    usage = load_usage_stats(default_catalog_dir(catalog_dir))

    ranked_tools: list[tuple[int, ToolProfile, list[str]]] = []
    for profile in tools:
        raw_tools: dict[str, Any] = usage.get("tools") or {}
        s, r = score_tool_for_goal(profile, task, context, int(raw_tools.get(profile.name, 0)))
        ranked_tools.append((s, profile, r))
    ranked_tools.sort(key=lambda row: (row[0], row[1].name), reverse=True)

    ranked_skills: list[tuple[int, SkillProfile, list[str]]] = []
    for sk in skills:
        s, r = score_skill_for_goal(sk, task, context)
        ranked_skills.append((s, sk, r))
    ranked_skills.sort(key=lambda row: (row[0], row[1].name), reverse=True)

    return {
        "task": task,
        "context": context,
        "tools": [
            {
                "name": p.name,
                "score": s,
                "path": str(p.path),
                "description": p.description,
                "reasoning": r or ["Heuristic match"],
            }
            for s, p, r in ranked_tools[:top_n]
        ],
        "skills": [
            {
                "name": sk.name,
                "score": s,
                "path": str(sk.path),
                "title": sk.title,
                "description": sk.description,
                "reasoning": r or ["Heuristic match"],
            }
            for s, sk, r in ranked_skills[:top_n]
        ],
    }


def scan_roots(repo_root: Path) -> list[Path]:
    roots = [repo_root.resolve()]
    ws = default_openclaw_workspace()
    if ws and ws.resolve() not in roots:
        roots.append(ws.resolve())
    skills_dir = Path.home() / ".openclaw" / "skills"
    if skills_dir.exists() and skills_dir.resolve() not in roots:
        roots.append(skills_dir.resolve())
    return roots


def build_index_payload(
    root: Path,
    *,
    skills: list[SkillProfile] | None = None,
    tools: list[ToolProfile] | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    roots = scan_roots(root)
    if skills is None:
        skills = [parse_skill_markdown(p, roots) for p in discover_skill_paths(roots)]
    if tools is None:
        tools = discover_all_tools(root)
    return {
        "version": INDEX_VERSION,
        "generated_at": _utc_now_iso(),
        "scan_roots": [str(r) for r in roots],
        "repo_root": str(root),
        "skills": [s.to_dict() for s in skills],
        "tools": [t.to_dict() for t in tools],
    }


def index_paths(catalog_dir: Path) -> tuple[Path, Path]:
    catalog_dir.mkdir(parents=True, exist_ok=True)
    return catalog_dir / "tool_skill_index.json", catalog_dir / "tool_usage.json"


def write_index(root: Path, catalog_dir: Path | None = None) -> Path:
    cat = default_catalog_dir(catalog_dir)
    index_path, _ = index_paths(cat)
    payload = build_index_payload(root)
    index_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    readme = cat / "README.txt"
    readme.write_text(
        "OpenClaw tool/skill catalog\n"
        f"- tool_skill_index.json — machine-readable index (version {INDEX_VERSION})\n"
        "- tool_usage.json — usage counters for ranking (see tool_discovery.py)\n",
        encoding="utf-8",
    )
    return index_path


def load_usage_stats(catalog_dir: Path | None = None) -> dict[str, Any]:
    _, usage_path = index_paths(default_catalog_dir(catalog_dir))
    if not usage_path.exists():
        return {"version": USAGE_VERSION, "tools": {}, "skills": {}, "updated_at": None}
    try:
        data = json.loads(usage_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": USAGE_VERSION, "tools": {}, "skills": {}, "updated_at": None}
    data.setdefault("tools", {})
    data.setdefault("skills", {})
    return data


def save_usage_stats(data: dict[str, Any], catalog_dir: Path | None = None) -> Path:
    _, usage_path = index_paths(default_catalog_dir(catalog_dir))
    usage_path.parent.mkdir(parents=True, exist_ok=True)
    data["version"] = USAGE_VERSION
    data["updated_at"] = _utc_now_iso()
    usage_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return usage_path


def record_tool_usage(
    name: str,
    *,
    kind: str = "tool",
    delta: int = 1,
    catalog_dir: Path | None = None,
) -> dict[str, Any]:
    """Increment usage counter for a tool or skill *name* (optimization signal)."""
    data = load_usage_stats(catalog_dir)
    bucket = "skills" if kind == "skill" else "tools"
    data.setdefault(bucket, {})
    cur = int(data[bucket].get(name, 0))
    data[bucket][name] = cur + max(1, delta)
    save_usage_stats(data, catalog_dir)
    return data


def format_list_text(skills: list[SkillProfile], tools: list[ToolProfile]) -> str:
    lines = ["# Discovered skills (SKILL.md)", ""]
    if not skills:
        lines.append("_No SKILL.md files found._\n")
    for s in skills:
        lines.append(f"## skill:{s.name}")
        lines.append(f"- Path: {s.path}")
        lines.append(f"- Title: {s.title}")
        lines.append(f"- Description: {s.description}")
        if s.use_cases:
            lines.append("- Use cases:")
            lines.extend(f"  - {u}" for u in s.use_cases[:6])
        lines.append("")
    lines.extend(["# Discovered tools", ""])
    for t in tools:
        lines.append(f"## tool:{t.name} ({t.kind})")
        lines.append(f"- Path: {t.path}")
        lines.append(f"- Description: {t.description}")
        lines.append(f"- Capabilities: {', '.join(t.capabilities)}")
        if t.use_cases[:3]:
            lines.append("- Use cases:")
            lines.extend(f"  - {u}" for u in t.use_cases[:3])
        lines.append("")
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover and catalog OpenClaw tools and SKILL.md skills.")
    parser.add_argument("--root", default=".", help="Repository / workspace root to scan")
    parser.add_argument(
        "--catalog-dir",
        default="",
        help="Directory for tool_skill_index.json and tool_usage.json "
        "(default: OPENCLAW_WORKSPACE/catalog, OPENCLAW_TOOL_CATALOG_DIR, or ./.openclaw/catalog)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("index", help="Write tool_skill_index.json (+ README) to the catalog directory")

    list_p = subparsers.add_parser("list", help="List all discovered skills and tools with descriptions")
    list_p.add_argument("--format", choices=("text", "json"), default="text")

    analyze = subparsers.add_parser("analyze", help="Emit tool analysis as JSON or compact text")
    analyze.add_argument("--format", choices=("json", "text"), default="json")
    analyze.add_argument(
        "--scripts-only",
        action="store_true",
        help="Only analyze scripts/*.py (legacy analyze_scripts behavior for tooling)",
    )

    docs = subparsers.add_parser("docs", help="Generate Markdown tool documentation for scripts/")
    docs.add_argument("--output", help="Write docs to this path instead of stdout")

    suggest = subparsers.add_parser("suggest", help="Suggest tools with contextual reasoning")
    suggest.add_argument("goal", help="User goal, e.g. 'monitor queue latency'")
    suggest.add_argument("--context", default="", help="Additional operational context")
    suggest.add_argument("--top", type=int, default=5, help="Number of tools to suggest")
    suggest.add_argument(
        "--track",
        action="store_true",
        help="Record +1 usage for each tool name returned in the suggestion list",
    )

    rel = subparsers.add_parser("suggest-all", help="Suggest both tools and skills for a task description")
    rel.add_argument("task", help="Natural language task description")
    rel.add_argument("--context", default="", help="Extra context")
    rel.add_argument("--top", type=int, default=5, help="Max items per category")

    rec = subparsers.add_parser("record-usage", help="Increment usage counter for a tool or skill")
    rec.add_argument("name", help="Tool stem (e.g. ollama_monitor) or skill slug")
    rec.add_argument("--kind", choices=("tool", "skill"), default="tool")
    rec.add_argument("--delta", type=int, default=1, help="Increment amount (default 1)")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()
    catalog_dir = Path(args.catalog_dir).resolve() if getattr(args, "catalog_dir", "") and args.catalog_dir else None

    if args.command == "index":
        path = write_index(root, catalog_dir)
        print(path)
        return 0

    if args.command == "list":
        roots = scan_roots(root)
        skills = [parse_skill_markdown(p, roots) for p in discover_skill_paths(roots)]
        tools = discover_all_tools(root)
        if args.format == "json":
            print(
                json.dumps(
                    {"skills": [s.to_dict() for s in skills], "tools": [t.to_dict() for t in tools]},
                    indent=2,
                )
            )
        else:
            print(format_list_text(skills, tools))
        return 0

    if args.command == "analyze":
        if args.scripts_only:
            profiles = analyze_scripts(root)
        else:
            profiles = discover_all_tools(root)
        if args.format == "json":
            print(json.dumps([profile.to_dict() for profile in profiles], indent=2))
        else:
            for profile in profiles:
                print(f"{profile.name}: {', '.join(profile.capabilities)} | deps={profile.dependencies}")
        return 0

    if args.command == "docs":
        profiles = analyze_scripts(root)
        markdown = generate_markdown(profiles)
        output_arg = args.output
        if output_arg:
            output = Path(output_arg)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(markdown, encoding="utf-8")
        else:
            print(markdown)
        return 0

    if args.command == "suggest":
        profiles = discover_all_tools(root)
        usage = load_usage_stats(catalog_dir)
        raw_tools: dict[str, Any] = usage.get("tools") or {}
        counts = {str(k): int(v) for k, v in raw_tools.items()}
        payload = {
            "goal": args.goal,
            "context": args.context,
            "suggestions": suggest_tools(
                profiles, goal=args.goal, context=args.context, top_n=max(1, args.top), usage_counts=counts
            ),
        }
        print(json.dumps(payload, indent=2))
        if args.track:
            for row in payload["suggestions"]:
                record_tool_usage(str(row["tool"]), kind="tool", delta=1, catalog_dir=catalog_dir)
        return 0

    if args.command == "suggest-all":
        out = suggest_relevant_tools(args.task, context=args.context, root=root, top_n=max(1, args.top), catalog_dir=catalog_dir)
        print(json.dumps(out, indent=2))
        return 0

    if args.command == "record-usage":
        record_tool_usage(args.name, kind=args.kind, delta=max(1, args.delta), catalog_dir=catalog_dir)
        print(json.dumps(load_usage_stats(catalog_dir), indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
