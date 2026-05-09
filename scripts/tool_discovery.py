#!/usr/bin/env python3
"""Catalog OpenClaw tools (scripts, skills, source APIs) and correlate usage from session logs."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from scripts.conversation_extractor import parse_session_log


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

FAILURE_LINE = re.compile(
    r"\b(error|exception|traceback|failed|failure|timeout|timed out|syntaxerror|"
    r"valueerror|keyerror|runtimeerror|typeerror|importerror|permission denied|"
    r"enoent|not found|invalid|refused|econnrefused|401|403|404|500)\b",
    re.IGNORECASE,
)

DEFAULT_REPORT_REL = Path("scripts/tool_discovery_report.md")


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


@dataclass
class SkillDocument:
    """Parsed SKILL.md (or skill.md) front matter and summary."""

    path: Path
    title: str
    summary: str
    triggers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "title": self.title,
            "summary": self.summary,
            "triggers": self.triggers,
        }


@dataclass
class SourceSignature:
    """Public function signature discovered under src/."""

    module: str
    name: str
    path: Path
    signature: str
    doc_one_liner: str

    def to_dict(self) -> dict[str, object]:
        return {
            "module": self.module,
            "name": self.name,
            "path": str(self.path),
            "signature": self.signature,
            "doc": self.doc_one_liner,
        }


@dataclass
class ToolUsageStats:
    tool: str
    attempts: int
    successes: int
    failures: int
    failure_samples: list[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if self.attempts <= 0:
            return 0.0
        return self.successes / self.attempts

    def to_dict(self) -> dict[str, object]:
        return {
            "tool": self.tool,
            "attempts": self.attempts,
            "successes": self.successes,
            "failures": self.failures,
            "success_rate": round(self.success_rate, 4),
            "failure_samples": self.failure_samples[:5],
        }


def discover_script_paths(root: Path) -> list[Path]:
    scripts_dir = root / "scripts"
    if not scripts_dir.exists():
        return []
    return sorted(path for path in scripts_dir.glob("*.py") if path.name != "__init__.py")


def discover_skill_md_paths(root: Path) -> list[Path]:
    found: set[Path] = set()
    for pattern in ("**/SKILL.md", "**/skill.md"):
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            parts_lower = {p.lower() for p in path.parts}
            if ".git" in parts_lower or "__pycache__" in parts_lower:
                continue
            found.add(path.resolve())
    return sorted(found)


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


def _format_args(args: ast.arguments) -> str:
    parts: list[str] = []
    for a in args.posonlyargs:
        parts.append(a.arg if a.arg else "")
    if args.posonlyargs:
        parts.append("/")
    for a in args.args:
        parts.append(a.arg)
    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    for a in args.kwonlyargs:
        parts.append(a.arg)
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")
    return ", ".join(p for p in parts if p)


def extract_public_signatures(path: Path, src_root: Path) -> list[SourceSignature]:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError):
        return []

    rel = path.relative_to(src_root.parent)
    try:
        inner = path.relative_to(src_root)
        module = "src." + ".".join(inner.with_suffix("").parts)
    except ValueError:
        module = ".".join(rel.with_suffix("").parts)
    out: list[SourceSignature] = []

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        sig = f"def {node.name}({_format_args(node.args)})"
        doc = ast.get_docstring(node) or ""
        one = doc.strip().splitlines()[0] if doc.strip() else ""
        out.append(
            SourceSignature(
                module=module,
                name=node.name,
                path=rel,
                signature=sig,
                doc_one_liner=one or "No docstring.",
            )
        )
    return out


def scan_src_signatures(root: Path) -> list[SourceSignature]:
    src = root / "src"
    if not src.exists():
        return []
    collected: list[SourceSignature] = []
    for path in sorted(src.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        collected.extend(extract_public_signatures(path, src))
    return collected


def _parse_skill_markdown_fields(path: Path) -> tuple[str, str, list[str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    title = path.parent.name or path.stem
    summary = ""
    triggers: list[str] = []
    if lines and lines[0].strip() == "---":
        end = 1
        meta: dict[str, str] = {}
        while end < len(lines) and lines[end].strip() != "---":
            line = lines[end]
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip()
            end += 1
        title = meta.get("name", meta.get("title", title))
        summary = meta.get("description", meta.get("summary", ""))
        raw_tr = meta.get("triggers", "")
        if raw_tr:
            triggers = [t.strip() for t in re.split(r"[,;|]", raw_tr) if t.strip()]
        body_start = end + 1
    else:
        body_start = 0

    body = "\n".join(lines[body_start:]).strip()
    if not summary and body:
        first_para = body.split("\n\n", 1)[0].strip()
        summary = first_para.replace("\n", " ")[:400]
    return title, summary, triggers


def parse_skill_markdown_rooted(path: Path, root: Path) -> SkillDocument:
    title, summary, triggers = _parse_skill_markdown_fields(path)
    try:
        rel_path = path.resolve().relative_to(root.resolve())
    except ValueError:
        rel_path = path
    return SkillDocument(path=rel_path, title=title, summary=summary, triggers=triggers)


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
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
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
            generic_only = shared_caps <= {"General utility automation"}
            if len(shared_imports) >= 2 or (len(shared_caps) >= 2 and not generic_only):
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


def normalize_tool_name(raw: str) -> str:
    s = raw.strip()
    s = re.sub(r"^\[tool\s*:\s*", "", s, flags=re.I)
    s = s.rstrip("]").strip()
    s = s.split("(", 1)[0].strip()
    return s or "unknown"


def _is_tool_invocation_name(text: str) -> bool:
    t = text.strip()
    if not t or len(t) > 120 or "\n" in t:
        return False
    if FAILURE_LINE.search(t):
        return False
    return True


def analyze_session_segments(segments: list[tuple[int, str | None, str]]) -> dict[str, ToolUsageStats]:
    """Classify tool attempts using the next tool_output (or assistant) blob as outcome text."""

    by_tool: dict[str, ToolUsageStats] = {}
    pending: list[str] = []

    def ensure(name: str) -> ToolUsageStats:
        if name not in by_tool:
            by_tool[name] = ToolUsageStats(tool=name, attempts=0, successes=0, failures=0)
        return by_tool[name]

    for _turn, role, text in segments:
        rl = (role or "").lower()

        if rl == "tool" and _is_tool_invocation_name(text):
            name = normalize_tool_name(text)
            st = ensure(name)
            st.attempts += 1
            pending.append(name)
            continue

        if rl == "tool_output" and pending:
            name = pending.pop(0)
            st = ensure(name)
            if FAILURE_LINE.search(text):
                st.failures += 1
                line = next((ln for ln in text.splitlines() if FAILURE_LINE.search(ln)), text.strip())
                compact = re.sub(r"\s+", " ", line)[:200]
                if compact and len(st.failure_samples) < 8:
                    st.failure_samples.append(compact)
            else:
                st.successes += 1
            continue

        if pending and rl in {"assistant", "agent", "user", "system", ""}:
            # Some exports omit explicit tool_output; use following assistant/user text as weak signal.
            name = pending.pop(0)
            st = ensure(name)
            if FAILURE_LINE.search(text):
                st.failures += 1
                line = next((ln for ln in text.splitlines() if FAILURE_LINE.search(ln)), text.strip())
                compact = re.sub(r"\s+", " ", line)[:200]
                if compact and len(st.failure_samples) < 8:
                    st.failure_samples.append(compact)
            else:
                st.successes += 1

    return by_tool


def iter_session_log_paths(log_roots: Iterable[Path]) -> list[Path]:
    paths: list[Path] = []
    for root in log_roots:
        if not root.exists():
            continue
        for pattern in ("**/*.log", "**/*.json"):
            for p in root.glob(pattern):
                if p.is_file() and "__pycache__" not in p.parts:
                    paths.append(p)
    return sorted(set(paths))


def collect_session_usage(log_roots: list[Path]) -> tuple[dict[str, ToolUsageStats], int, int]:
    merged: dict[str, ToolUsageStats] = {}
    paths = iter_session_log_paths(log_roots)
    total_files = len(paths)
    files_with_tool_rows = 0

    for path in paths:
        segments = parse_session_log(path)
        if not segments:
            continue
        local = analyze_session_segments(segments)
        if not local:
            continue
        files_with_tool_rows += 1
        for name, stats in local.items():
            if name not in merged:
                merged[name] = ToolUsageStats(tool=name, attempts=0, successes=0, failures=0, failure_samples=[])
            m = merged[name]
            m.attempts += stats.attempts
            m.successes += stats.successes
            m.failures += stats.failures
            for s in stats.failure_samples:
                if len(m.failure_samples) < 10:
                    m.failure_samples.append(s)

    return merged, total_files, files_with_tool_rows


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


def alternatives_for_session_tool(
    tool_name: str,
    profiles: list[ToolProfile],
    stats: ToolUsageStats | None,
) -> list[str]:
    """Recommend script tools that overlap in naming or capabilities."""

    tn = tool_name.lower()
    scored: list[tuple[int, str]] = []
    for p in profiles:
        if p.name.lower() == tn:
            continue
        score = 0
        if tn.replace("_", "").replace("-", "") in p.name.lower().replace("_", ""):
            score += 2
        for cap in p.capabilities:
            if any(word in cap.lower() for word in tn.split("_") if len(word) > 3):
                score += 2
        if stats and stats.failures > stats.successes and p.risk_level == "low":
            score += 1
        if score > 0:
            scored.append((score, p.name))
    scored.sort(reverse=True)
    return [name for _, name in scored[:5]]


def build_catalog(root: Path) -> dict[str, Any]:
    profiles = analyze_scripts(root)
    skills = [parse_skill_markdown_rooted(p, root) for p in discover_skill_md_paths(root)]
    signatures = scan_src_signatures(root)
    return {
        "repo_root": str(root.resolve()),
        "scripts": [p.to_dict() for p in profiles],
        "skill_docs": [s.to_dict() for s in skills],
        "src_signatures": [s.to_dict() for s in signatures],
        "counts": {
            "scripts": len(profiles),
            "skill_md": len(skills),
            "src_functions": len(signatures),
        },
    }


def default_log_roots(root: Path) -> list[Path]:
    return [root / "logs", root / ".openclaw" / "logs"]


def generate_usage_report_markdown(
    root: Path,
    profiles: list[ToolProfile],
    usage: dict[str, ToolUsageStats],
    log_files_seen: int,
    log_files_with_tools: int,
) -> str:
    lines: list[str] = [
        "# Tool discovery report",
        "",
        f"_Generated for repository `{root.name}`._",
        "",
        "## Catalog overview",
        "",
        f"- **Script tools** (`scripts/*.py`): {len(profiles)}",
        f"- **SKILL.md / skill.md** files: {len(discover_skill_md_paths(root))}",
        f"- **Public `src/` entrypoints** (module-level functions): {len(scan_src_signatures(root))}",
        "",
        "## Session log usage",
        "",
        f"- Session-like files under log roots: **{log_files_seen}**; files with parsed tool rows: **{log_files_with_tools}**.",
        "",
    ]

    if not usage:
        lines.extend(
            [
                "_No tool invocations parsed from session logs._",
                "Ensure transcripts use `role: tool` lines or JSON exports compatible with `conversation_extractor.parse_session_log`.",
                "",
            ]
        )
    else:
        ranked = sorted(usage.values(), key=lambda s: s.attempts, reverse=True)
        lines.append("| Tool | Attempts | OK | Fail | Success rate |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        for s in ranked[:40]:
            rate = f"{100.0 * s.success_rate:.1f}%" if s.attempts else "n/a"
            lines.append(f"| `{s.tool}` | {s.attempts} | {s.successes} | {s.failures} | {rate} |")
        lines.append("")

    lines.extend(
        [
            "## Best practices",
            "",
            "- Prefer **narrow scripts** with clear `--help` and subcommands; match the task to capability labels in this report.",
            "- For **high-risk** tools (subprocess, raw network), dry-run against sample data and keep working directories under version control.",
            "- When logs show repeated failures for one tool, read the captured error line, fix inputs, then retry with a **smaller scope** (single file or single API call).",
            "- Align long-running work with **dependencies** listed per script: chain tools in the suggested order instead of parallel shell hacks.",
            "",
            "## Common failure patterns (heuristic)",
            "",
            "- **Timeouts / refused connections**: retry with backoff; verify local daemons (`ollama`, bridges) are running.",
            "- **Permission / path errors**: use absolute paths from repo root; check `logs/` is writable.",
            "- **JSON / schema errors**: validate payloads with `python -m json.tool` before pasting into sessions.",
            "",
        ]
    )

    if usage:
        lines.append("## Observed failure snippets")
        lines.append("")
        for s in sorted(usage.values(), key=lambda x: x.failures, reverse=True)[:12]:
            if not s.failure_samples:
                continue
            lines.append(f"### `{s.tool}`")
            lines.append("")
            for sample in s.failure_samples[:3]:
                lines.append(f"- {sample}")
            lines.append("")

    lines.append("## Recommended alternatives (scripts)")
    lines.append("")
    for s in sorted(usage.values(), key=lambda x: (x.failures, -x.attempts), reverse=True)[:15]:
        if s.failures == 0:
            continue
        alts = alternatives_for_session_tool(s.tool, profiles, s)
        if not alts:
            goal = f"replace or debug {s.tool}"
            alts = [row["tool"] for row in suggest_tools(profiles, goal=goal, context=" ".join(s.failure_samples[:2]), top_n=4)]
        lines.append(f"- **`{s.tool}`** ({s.failures} fail / {s.attempts} attempts): consider " + ", ".join(f"`{a}`" for a in alts[:4]) + ".")
    if not any(s.failures for s in usage.values()):
        lines.append("_No failed tool calls detected in scanned logs._")
    lines.append("")

    lines.append("## Script reference (compact)")
    lines.append("")
    for p in sorted(profiles, key=lambda x: x.name):
        lines.append(f"### `{p.name}`")
        lines.append("")
        lines.append(f"- Path: `{p.path}` — {p.description}")
        lines.append(f"- Risk: **{p.risk_level}** | I/O: {', '.join(p.io_profile)}")
        caps = ", ".join(p.capabilities)
        lines.append(f"- Capabilities: {caps}")
        if p.dependencies:
            dep_slice = p.dependencies[:12]
            extra = len(p.dependencies) - len(dep_slice)
            suffix = f" (+{extra} more)" if extra else ""
            lines.append(
                f"- Related scripts: {', '.join(f'`{d}`' for d in dep_slice)}{suffix}"
            )
        if p.commands:
            lines.append(f"- Subcommands: {', '.join(f'`{c}`' for c in p.commands)}")
        lines.append("")

    return "\n".join(lines)


def run_report(root: Path, output: Path) -> Path:
    profiles = analyze_scripts(root)
    log_roots = default_log_roots(root)
    usage, total_logs, logs_with_tools = collect_session_usage(log_roots)
    markdown = generate_usage_report_markdown(root, profiles, usage, total_logs, logs_with_tools)
    if not output.is_absolute():
        output = (root / output).resolve()
    else:
        output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown, encoding="utf-8")
    return output


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover OpenClaw tools, analyze session logs, and emit a usage report.",
    )
    parser.add_argument("--root", default=".", help="Repository root path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover", help="Scan scripts, SKILL.md files, and src/ signatures")
    discover.add_argument("--format", choices=("json", "text"), default="json")

    report = subparsers.add_parser("report", help="Write tool_discovery_report.md (catalog + log analysis)")
    report.add_argument(
        "--output",
        default=str(DEFAULT_REPORT_REL),
        help="Markdown output path (default: scripts/tool_discovery_report.md)",
    )

    health = subparsers.add_parser("health-check", help="Verify catalog + report file")
    health.add_argument(
        "--report-path",
        default=str(DEFAULT_REPORT_REL),
        help="Path to report markdown to check",
    )
    health.add_argument("--min-report-bytes", type=int, default=80, help="Minimum report file size")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root).resolve()

    if args.command == "discover":
        catalog = build_catalog(root)
        if args.format == "json":
            print(json.dumps(catalog, indent=2))
        else:
            print(f"scripts={catalog['counts']['scripts']} skill_md={catalog['counts']['skill_md']} src_functions={catalog['counts']['src_functions']}")
        return 0

    if args.command == "report":
        out = Path(args.output)
        path = run_report(root, out)
        print(str(path))
        return 0

    if args.command == "health-check":
        profiles = analyze_scripts(root)
        if not profiles:
            sys.stderr.write("error: no script tools found under scripts/*.py\n")
            return 1
        rp = Path(args.report_path)
        report_path = (root / rp).resolve() if not rp.is_absolute() else rp.resolve()
        if not report_path.exists():
            sys.stderr.write(f"error: report missing at {report_path}\n")
            return 1
        size = report_path.stat().st_size
        if size < args.min_report_bytes:
            sys.stderr.write(f"error: report too small ({size} bytes): {report_path}\n")
            return 1
        print(f"ok: {len(profiles)} tools cataloged; report {report_path} ({size} bytes)")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
