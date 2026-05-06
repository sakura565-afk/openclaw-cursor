from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _tokenize_identifiers(content: str) -> Set[str]:
    return set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", content))


@dataclass
class ToolRecord:
    name: str
    path: str
    kind: str
    language: str
    size_bytes: int
    modified_at: str
    age_hours: float
    is_recent: bool
    is_new: bool
    documented: bool
    used: bool
    usage_signals: int
    doc_signals: int
    tags: List[str] = field(default_factory=list)


@dataclass
class DiscoverySnapshot:
    generated_at: str
    workspace_root: str
    total_tools: int
    recent_tools: List[ToolRecord]
    new_tools: List[ToolRecord]
    undocumented_tools: List[ToolRecord]
    unused_tools: List[ToolRecord]
    skill_usage: Dict[str, Any]
    suggestions: List[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "workspace_root": self.workspace_root,
            "total_tools": self.total_tools,
            "recent_tools": [asdict(item) for item in self.recent_tools],
            "new_tools": [asdict(item) for item in self.new_tools],
            "undocumented_tools": [asdict(item) for item in self.undocumented_tools],
            "unused_tools": [asdict(item) for item in self.unused_tools],
            "skill_usage": self.skill_usage,
            "suggestions": self.suggestions,
        }


class ToolDiscoverySystem:
    TOOL_SUFFIXES = ("_tool.py", "_script.py", "_manager.py", "_runner.py", "_cli.py")
    TOOL_DIR_HINTS = ("scripts", "tools", "bin", "src/skills", "src/monitoring", "src/self_improvement")
    TOOL_EXTENSIONS = {".py", ".sh", ".bash", ".zsh", ".js", ".ts"}
    DOC_FILES = ("README.md", "docs/tools.md", "docs/TOOLS.md")

    def __init__(
        self,
        workspace_root: Path | str = ".",
        state_dir: Path | str | None = None,
        *,
        recent_hours: int = 72,
        new_hours: int = 24,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.state_dir = Path(state_dir or self.workspace_root / "logs").resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.recent_hours = recent_hours
        self.new_hours = new_hours
        self.index_path = self.state_dir / "tool_discovery_index.json"
        self.history_path = self.state_dir / "tool_discovery_history.jsonl"

    def discover(self) -> DiscoverySnapshot:
        now = _now_utc()
        previous_index = self._load_index()
        docs_blob = self._load_docs_blob()
        usage_blob, skill_usage = self._analyze_skill_usage()

        tools = [self._build_tool_record(path, now, previous_index, docs_blob, usage_blob) for path in self._iter_tool_candidates()]
        tools.sort(key=lambda item: item.modified_at, reverse=True)

        recent_tools = [item for item in tools if item.is_recent]
        new_tools = [item for item in tools if item.is_new]
        undocumented_tools = [item for item in tools if not item.documented]
        unused_tools = [item for item in tools if not item.used]
        suggestions = self._suggest_improvements(tools, undocumented_tools, unused_tools, skill_usage)

        snapshot = DiscoverySnapshot(
            generated_at=now.isoformat(),
            workspace_root=str(self.workspace_root),
            total_tools=len(tools),
            recent_tools=recent_tools,
            new_tools=new_tools,
            undocumented_tools=undocumented_tools,
            unused_tools=unused_tools,
            skill_usage=skill_usage,
            suggestions=suggestions,
        )
        self._save_index(tools)
        self._append_history(snapshot)
        return snapshot

    def generate_report(self, snapshot: Optional[DiscoverySnapshot] = None, *, write_files: bool = True) -> Dict[str, str]:
        active_snapshot = snapshot or self.discover()
        json_payload = json.dumps(active_snapshot.as_dict(), indent=2, sort_keys=True)
        markdown_payload = self._render_markdown(active_snapshot)

        output: Dict[str, str] = {
            "json": json_payload,
            "markdown": markdown_payload,
        }
        if write_files:
            stamp = datetime.fromisoformat(active_snapshot.generated_at).strftime("%Y%m%d_%H%M%S")
            json_path = self.state_dir / f"tool_discovery_report_{stamp}.json"
            md_path = self.state_dir / f"tool_discovery_report_{stamp}.md"
            json_path.write_text(json_payload, encoding="utf-8")
            md_path.write_text(markdown_payload, encoding="utf-8")
            output["json_path"] = str(json_path)
            output["markdown_path"] = str(md_path)
        return output

    def tool_evolution(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        history: List[Dict[str, Any]] = []
        if not self.history_path.exists():
            return history
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                history.append(payload)
        return history[-limit:]

    def _iter_tool_candidates(self) -> Iterable[Path]:
        for path in self.workspace_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in self.TOOL_EXTENSIONS:
                continue
            rel = str(path.relative_to(self.workspace_root))
            if "/." in rel or rel.startswith("."):
                continue
            lowered = rel.lower()
            name = path.stem.lower()
            has_hint = any(hint in lowered for hint in self.TOOL_DIR_HINTS) or any(name.endswith(suffix[:-3]) for suffix in self.TOOL_SUFFIXES)
            if not has_hint:
                continue
            yield path

    def _analyze_skill_usage(self) -> Tuple[str, Dict[str, Any]]:
        skill_dir = self.workspace_root / "src" / "skills"
        if not skill_dir.exists():
            return "", {"skills": {}, "top_skills": [], "stale_skills": [], "total_skill_calls": 0}

        usage_counts: Dict[str, int] = {}
        for path in skill_dir.glob("*.py"):
            if path.name.startswith("_"):
                continue
            text = _safe_read(path)
            function_defs = re.findall(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", text, flags=re.MULTILINE)
            usages = len(re.findall(r"\b(run|execute|dispatch|trigger|apply|watch|monitor)\b", text))
            usage_counts[path.stem] = max(usages, len(function_defs))

        ranked = sorted(usage_counts.items(), key=lambda item: item[1], reverse=True)
        top_skills = [{"name": name, "score": score} for name, score in ranked[:5]]
        stale_skills = [name for name, score in usage_counts.items() if score <= 1]
        usage_blob = " ".join(usage_counts.keys())
        for path in self.workspace_root.rglob("*.py"):
            if "venv" in path.parts or ".git" in path.parts:
                continue
            usage_blob += "\n" + _safe_read(path)

        return usage_blob, {
            "skills": usage_counts,
            "top_skills": top_skills,
            "stale_skills": stale_skills,
            "total_skill_calls": sum(usage_counts.values()),
        }

    def _load_docs_blob(self) -> str:
        chunks: List[str] = []
        for rel in self.DOC_FILES:
            path = self.workspace_root / rel
            if path.exists():
                chunks.append(_safe_read(path))
        return "\n".join(chunks).lower()

    def _build_tool_record(
        self,
        path: Path,
        now: datetime,
        previous_index: Dict[str, Any],
        docs_blob: str,
        usage_blob: str,
    ) -> ToolRecord:
        stat = path.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        age_hours = max((now - mtime).total_seconds() / 3600.0, 0.0)
        rel_path = str(path.relative_to(self.workspace_root))
        stem = path.stem
        normalized_name = stem.lower()

        doc_signals = int(normalized_name in docs_blob) + int(rel_path.lower() in docs_blob)
        usage_signals = usage_blob.count(stem) + usage_blob.count(normalized_name)

        first_seen = previous_index.get(rel_path, {}).get("first_seen")
        if isinstance(first_seen, str):
            try:
                first_seen_dt = datetime.fromisoformat(first_seen)
                first_seen_age = (now - first_seen_dt).total_seconds() / 3600.0
                is_new = first_seen_age <= self.new_hours
            except ValueError:
                is_new = age_hours <= self.new_hours
        else:
            is_new = age_hours <= self.new_hours

        tags: List[str] = []
        rel_lower = rel_path.lower()
        if "skill" in rel_lower:
            tags.append("skill")
        if "monitor" in rel_lower:
            tags.append("monitoring")
        if "self_improvement" in rel_lower:
            tags.append("self-improvement")
        if "script" in rel_lower:
            tags.append("script")

        return ToolRecord(
            name=stem,
            path=rel_path,
            kind=self._classify_tool_kind(path),
            language=path.suffix.lstrip("."),
            size_bytes=stat.st_size,
            modified_at=mtime.isoformat(),
            age_hours=round(age_hours, 2),
            is_recent=age_hours <= self.recent_hours,
            is_new=is_new,
            documented=doc_signals > 0,
            used=usage_signals > 1,
            usage_signals=usage_signals,
            doc_signals=doc_signals,
            tags=tags,
        )

    def _classify_tool_kind(self, path: Path) -> str:
        rel = str(path.relative_to(self.workspace_root)).lower()
        if "/skills/" in rel:
            return "skill"
        if "/monitoring/" in rel:
            return "monitoring"
        if "/self_improvement/" in rel:
            return "self_improvement"
        if "/scripts/" in rel:
            return "script"
        return "tool"

    def _load_index(self) -> Dict[str, Any]:
        if not self.index_path.exists():
            return {}
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save_index(self, tools: List[ToolRecord]) -> None:
        now = _now_utc().isoformat()
        prior = self._load_index()
        new_index: Dict[str, Any] = {}
        for item in tools:
            old = prior.get(item.path, {})
            new_index[item.path] = {
                "first_seen": old.get("first_seen", now),
                "last_seen": now,
                "name": item.name,
                "kind": item.kind,
                "language": item.language,
            }
        self.index_path.write_text(json.dumps(new_index, indent=2, sort_keys=True), encoding="utf-8")

    def _append_history(self, snapshot: DiscoverySnapshot) -> None:
        entry = {
            "generated_at": snapshot.generated_at,
            "total_tools": snapshot.total_tools,
            "recent_count": len(snapshot.recent_tools),
            "new_count": len(snapshot.new_tools),
            "undocumented_count": len(snapshot.undocumented_tools),
            "unused_count": len(snapshot.unused_tools),
            "top_skills": snapshot.skill_usage.get("top_skills", []),
        }
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")

    def _suggest_improvements(
        self,
        tools: List[ToolRecord],
        undocumented_tools: List[ToolRecord],
        unused_tools: List[ToolRecord],
        skill_usage: Dict[str, Any],
    ) -> List[str]:
        suggestions: List[str] = []
        if undocumented_tools:
            suggestions.append(
                f"Document {min(5, len(undocumented_tools))} undocumented tools first: "
                + ", ".join(item.name for item in undocumented_tools[:5])
            )
        if unused_tools:
            suggestions.append(
                f"Review or retire {len(unused_tools)} unused tools; add integration calls or remove dead code."
            )
        stale_skills = skill_usage.get("stale_skills", [])
        if stale_skills:
            suggestions.append(
                "Improve low-usage skills with triggers/tests: " + ", ".join(stale_skills[:5])
            )
        if not suggestions and tools:
            suggestions.append("Tool ecosystem looks healthy; continue tracking weekly evolution snapshots.")
        return suggestions

    def _render_markdown(self, snapshot: DiscoverySnapshot) -> str:
        lines = [
            "# Tool Discovery Report",
            "",
            f"- Generated: {snapshot.generated_at}",
            f"- Workspace: `{snapshot.workspace_root}`",
            f"- Total tools discovered: **{snapshot.total_tools}**",
            "",
            "## New/Recent Tools",
            "",
        ]
        if not snapshot.recent_tools:
            lines.append("No recent tools discovered in the configured recent window.")
            lines.append("")
        else:
            for item in snapshot.recent_tools[:20]:
                marker = "NEW" if item.is_new else "RECENT"
                lines.append(
                    f"- `{item.path}` ({marker}, {item.kind}, {item.age_hours}h old)"
                )
            lines.append("")

        lines.extend(["## Undocumented Tools", ""])
        if snapshot.undocumented_tools:
            lines.extend([f"- `{item.path}`" for item in snapshot.undocumented_tools[:30]])
        else:
            lines.append("No undocumented tools detected.")
        lines.append("")

        lines.extend(["## Unused Tools", ""])
        if snapshot.unused_tools:
            lines.extend([f"- `{item.path}` (usage_signals={item.usage_signals})" for item in snapshot.unused_tools[:30]])
        else:
            lines.append("No likely-unused tools detected.")
        lines.append("")

        lines.extend(["## Skill Usage Analysis", ""])
        top_skills = snapshot.skill_usage.get("top_skills", [])
        if top_skills:
            for row in top_skills:
                lines.append(f"- {row['name']}: {row['score']}")
        else:
            lines.append("No skills found.")
        lines.append("")

        lines.extend(["## Suggested Improvements", ""])
        for suggestion in snapshot.suggestions:
            lines.append(f"- {suggestion}")
        lines.append("")
        return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenClaw tool discovery and evolution tracker")
    parser.add_argument("command", choices=["discover", "report", "evolution"], help="Action to run")
    parser.add_argument("--workspace", default=".", help="Workspace root path")
    parser.add_argument("--state-dir", default=None, help="Directory for discovery index/history/reports")
    parser.add_argument("--recent-hours", type=int, default=72, help="Time window for recent tools")
    parser.add_argument("--new-hours", type=int, default=24, help="Time window for new tools")
    parser.add_argument("--history-limit", type=int, default=20, help="Number of evolution entries to display")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    system = ToolDiscoverySystem(
        workspace_root=Path(args.workspace),
        state_dir=Path(args.state_dir) if args.state_dir else None,
        recent_hours=args.recent_hours,
        new_hours=args.new_hours,
    )
    if args.command == "discover":
        snapshot = system.discover()
        print(json.dumps(snapshot.as_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "report":
        payload = system.generate_report(write_files=True)
        print(payload["markdown"])
        return 0
    history = system.tool_evolution(limit=args.history_limit)
    print(json.dumps(history, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
