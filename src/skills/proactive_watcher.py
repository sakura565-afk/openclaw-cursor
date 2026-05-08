from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.error_learning.log_signals import ERROR_SIGNAL_RE as ERROR_PATTERN, normalize_error_line
DOC_NAMES = {"readme.md", "readme.rst", "docs.md", "docs.txt"}


def _default_openclaw_home() -> Path:
    override = os.environ.get("OPENCLAW_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".openclaw"


def _default_skill_roots() -> list[Path]:
    home = _default_openclaw_home()
    return [home / "skills", home / "workspace" / "skills"]


def _default_log_roots() -> list[Path]:
    home = _default_openclaw_home()
    return [home / "logs", home / "workspace" / "logs"]


def _default_report_dir() -> Path:
    override = os.environ.get("OPENCLAW_REPORT_DIR")
    if override:
        return Path(override).expanduser()
    return Path.cwd() / "skills"


def _to_datetime(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


@dataclass
class SkillRecord:
    name: str
    locations: list[Path] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    last_modified: datetime | None = None
    usage_count: int = 0
    usage_label: str = "unknown"
    error_patterns: dict[str, int] = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)
    syntax_failures: list[str] = field(default_factory=list)

    def add_location(self, path: Path) -> None:
        if path not in self.locations:
            self.locations.append(path)

    def add_file(self, path: Path) -> None:
        if path not in self.files:
            self.files.append(path)
        try:
            modified = _to_datetime(path.stat().st_mtime)
        except OSError:
            return
        if self.last_modified is None or modified > self.last_modified:
            self.last_modified = modified

    @property
    def has_docs(self) -> bool:
        return any(
            path.name.lower() in DOC_NAMES or "docs" in {part.lower() for part in path.parts}
            for path in self.files
        )

    @property
    def has_tests(self) -> bool:
        for path in self.files:
            lowered = path.name.lower()
            if lowered.startswith("test_") and lowered.endswith(".py"):
                return True
            if "tests" in {part.lower() for part in path.parts}:
                return True
        return False

    @property
    def python_files(self) -> list[Path]:
        return [path for path in self.files if path.suffix == ".py"]


class ProactiveSkillWatcher:
    def __init__(
        self,
        skill_roots: list[Path] | None = None,
        log_roots: list[Path] | None = None,
        report_dir: Path | None = None,
        now: datetime | None = None,
    ) -> None:
        self.skill_roots = skill_roots or _default_skill_roots()
        self.log_roots = log_roots or _default_log_roots()
        self.report_dir = report_dir or _default_report_dir()
        self.now = now or datetime.now(timezone.utc)
        self.skills: dict[str, SkillRecord] = {}

    def scan_skills(self) -> dict[str, SkillRecord]:
        skills: dict[str, SkillRecord] = {}
        for root in self.skill_roots:
            if not root.exists():
                continue
            for entry in sorted(root.iterdir()):
                if entry.name.startswith("."):
                    continue
                if entry.is_file() and entry.suffix != ".py":
                    continue
                name = entry.stem if entry.is_file() else entry.name
                record = skills.setdefault(name, SkillRecord(name=name))
                record.add_location(entry)
                for file_path in self._collect_skill_files(entry):
                    record.add_file(file_path)
        self.skills = skills
        self._check_syntax()
        return self.skills

    def _collect_skill_files(self, entry: Path) -> list[Path]:
        if entry.is_file():
            return [entry]
        files = []
        for child in entry.rglob("*"):
            if child.is_file():
                files.append(child)
        return files

    def _check_syntax(self) -> None:
        for skill in self.skills.values():
            skill.syntax_failures.clear()
            for python_file in skill.python_files:
                result = subprocess.run(
                    [sys.executable, "-m", "py_compile", str(python_file)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    detail = result.stderr.strip() or result.stdout.strip() or f"Syntax check failed: {python_file}"
                    skill.syntax_failures.append(detail)

    def analyze_usage(self) -> dict[str, SkillRecord]:
        if not self.skills:
            self.scan_skills()
        patterns = {
            name: re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
            for name in self.skills
        }
        for skill in self.skills.values():
            skill.usage_count = 0
        for line in self._iter_log_lines():
            for name, pattern in patterns.items():
                if pattern.search(line):
                    self.skills[name].usage_count += 1
        for skill in self.skills.values():
            age = self._days_since_modified(skill)
            if skill.usage_count >= 3:
                skill.usage_label = "frequent"
            elif age >= 30 and skill.usage_count <= 2:
                skill.usage_label = "rare"
            elif age >= 14 and skill.usage_count <= 1:
                skill.usage_label = "rare"
            else:
                skill.usage_label = "active"
        return self.skills

    def analyze_errors(self) -> dict[str, SkillRecord]:
        if not self.skills:
            self.scan_skills()
        patterns = {
            name: re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
            for name in self.skills
        }
        for skill in self.skills.values():
            skill.error_patterns = {}
        error_buckets: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for line in self._iter_log_lines():
            if not ERROR_PATTERN.search(line):
                continue
            normalized = normalize_error_line(line)
            for name, pattern in patterns.items():
                if pattern.search(line):
                    error_buckets[name][normalized] += 1
        for name, bucket in error_buckets.items():
            self.skills[name].error_patterns = dict(sorted(bucket.items(), key=lambda item: (-item[1], item[0])))
        return self.skills

    def build_suggestions(self) -> dict[str, SkillRecord]:
        if not self.skills:
            self.scan_skills()
        self.analyze_usage()
        self.analyze_errors()
        for skill in self.skills.values():
            suggestions = []
            if not skill.has_docs:
                suggestions.append("Update docs: add a README or usage notes for this skill.")
            if skill.syntax_failures or skill.error_patterns:
                suggestions.append("Fix broken scripts: review failing Python files and recurring log errors.")
            if not skill.has_tests:
                suggestions.append("Add missing tests: cover the primary workflow and failure modes.")
            skill.suggestions = suggestions
        return self.skills

    def _iter_log_lines(self) -> list[str]:
        lines: list[str] = []
        for root in self.log_roots:
            if not root.exists():
                continue
            for log_file in sorted(root.rglob("*")):
                if not log_file.is_file():
                    continue
                text = _read_text(log_file)
                if not text:
                    continue
                if log_file.suffix == ".json":
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError:
                        payload = None
                    if isinstance(payload, list):
                        lines.extend(json.dumps(item, sort_keys=True) for item in payload)
                        continue
                    if isinstance(payload, dict):
                        lines.append(json.dumps(payload, sort_keys=True))
                        continue
                lines.extend(text.splitlines())
        return lines

    def _days_since_modified(self, skill: SkillRecord) -> int:
        if skill.last_modified is None:
            return 10_000
        delta = self.now - skill.last_modified
        return max(0, delta.days)

    def write_report(self) -> Path:
        self.build_suggestions()
        self.report_dir.mkdir(parents=True, exist_ok=True)
        report_path = self.report_dir / f"health_report_{self.now:%Y%m%d}.md"
        report_path.write_text(self.render_report(), encoding="utf-8")
        return report_path

    def render_report(self) -> str:
        scanned_roots = ", ".join(str(path) for path in self.skill_roots)
        lines = [
            f"# OpenClaw Skill Health Report ({self.now:%Y-%m-%d})",
            "",
            "## Scan Summary",
            f"- Scanned skill roots: {scanned_roots}",
            f"- Skills discovered: {len(self.skills)}",
            "",
            "## Usage Patterns",
        ]
        if not self.skills:
            lines.append("- No skills found.")
        else:
            for skill in sorted(self.skills.values(), key=lambda item: item.name.lower()):
                touched = skill.last_modified.strftime("%Y-%m-%d") if skill.last_modified else "unknown"
                lines.append(
                    f"- **{skill.name}**: {skill.usage_label}, usage mentions={skill.usage_count}, "
                    f"last modified={touched}"
                )
        lines.extend(["", "## Error Patterns"])
        if not any(skill.error_patterns for skill in self.skills.values()):
            lines.append("- No skill-specific errors found in logs.")
        else:
            for skill in sorted(self.skills.values(), key=lambda item: item.name.lower()):
                if not skill.error_patterns:
                    continue
                lines.append(f"- **{skill.name}**")
                for pattern, count in skill.error_patterns.items():
                    lines.append(f"  - ({count}x) {pattern}")
        lines.extend(["", "## Improvement Suggestions"])
        if not any(skill.suggestions for skill in self.skills.values()):
            lines.append("- No suggestions at this time.")
        else:
            for skill in sorted(self.skills.values(), key=lambda item: item.name.lower()):
                if not skill.suggestions:
                    continue
                lines.append(f"- **{skill.name}**")
                for suggestion in skill.suggestions:
                    lines.append(f"  - {suggestion}")
        return "\n".join(lines) + "\n"

    def format_scan_output(self) -> str:
        if not self.skills:
            return "No skills found."
        lines = [f"Discovered {len(self.skills)} skills:"]
        for skill in sorted(self.skills.values(), key=lambda item: item.name.lower()):
            locations = ", ".join(str(path) for path in skill.locations)
            lines.append(f"- {skill.name}: {locations}")
        return "\n".join(lines)

    def format_usage_output(self) -> str:
        if not self.skills:
            return "No skills found."
        lines = ["Usage summary:"]
        for skill in sorted(self.skills.values(), key=lambda item: item.name.lower()):
            lines.append(f"- {skill.name}: {skill.usage_label} ({skill.usage_count} mentions)")
        return "\n".join(lines)

    def format_error_output(self) -> str:
        if not self.skills:
            return "No skills found."
        lines = ["Error summary:"]
        found_error = False
        for skill in sorted(self.skills.values(), key=lambda item: item.name.lower()):
            if not skill.error_patterns:
                continue
            found_error = True
            lines.append(f"- {skill.name}:")
            for pattern, count in skill.error_patterns.items():
                lines.append(f"  - ({count}x) {pattern}")
        if not found_error:
            lines.append("- No skill-specific errors found.")
        return "\n".join(lines)

    def format_suggestion_output(self) -> str:
        if not self.skills:
            return "No skills found."
        lines = ["Improvement suggestions:"]
        found_suggestion = False
        for skill in sorted(self.skills.values(), key=lambda item: item.name.lower()):
            if not skill.suggestions:
                continue
            found_suggestion = True
            lines.append(f"- {skill.name}:")
            for suggestion in skill.suggestions:
                lines.append(f"  - {suggestion}")
        if not found_suggestion:
            lines.append("- No suggestions at this time.")
        return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Proactive skill watcher for OpenClaw.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("scan", "usage", "errors", "suggest"):
        subparsers.add_parser(command)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    watcher = ProactiveSkillWatcher()
    watcher.scan_skills()
    if args.command == "scan":
        output = watcher.format_scan_output()
    elif args.command == "usage":
        watcher.analyze_usage()
        output = watcher.format_usage_output()
    elif args.command == "errors":
        watcher.analyze_errors()
        output = watcher.format_error_output()
    else:
        watcher.build_suggestions()
        output = watcher.format_suggestion_output()
    report_path = watcher.write_report()
    print(output)
    print(f"\nReport written to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
