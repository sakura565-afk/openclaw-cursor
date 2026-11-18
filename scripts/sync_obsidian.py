#!/usr/bin/env python3
"""Bidirectional sync between MEMORY.md and an Obsidian vault."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import unified_diff
from pathlib import Path
from typing import Iterable


IGNORED_PARTS = {".obsidian", "__pycache__"}
VAULT_DIRS = ("01_Projects", "02_Knowledge", "memory")
GENERATED_REFERENCE_NOTE = Path("memory/MEMORY_sync_index.md")
DAILY_NOTES_TITLE = "Daily Notes"
STALE_PREFIX = "> [!warning] Sync stale:"
DEFAULT_MEMORY_ENV = "SYNC_OBSIDIAN_MEMORY_PATH"
DEFAULT_VAULT_ENV = "SYNC_OBSIDIAN_VAULT_PATH"


class Color:
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    RESET = "\033[0m"


@dataclass(frozen=True)
class MemorySection:
    title: str
    level: int
    start_line: int
    end_line: int

    @property
    def slug(self) -> str:
        return slugify(self.title)


@dataclass(frozen=True)
class VaultNote:
    path: Path
    rel_path: str
    mtime: float

    @property
    def stem_slug(self) -> str:
        return slugify(Path(self.rel_path).stem)


def colorize(text: str, color: str, enabled: bool = True) -> str:
    if not enabled:
        return text
    return f"{color}{text}{Color.RESET}"


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower())
    return normalized.strip("-")


def isoformat_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def parse_memory_sections(text: str) -> list[MemorySection]:
    lines = text.splitlines()
    headings: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            headings.append((index, len(match.group(1)), match.group(2).strip()))

    sections: list[MemorySection] = []
    for offset, (line_index, level, title) in enumerate(headings):
        if level < 2:
            continue

        end_line = len(lines)
        for next_line_index, next_level, _ in headings[offset + 1 :]:
            if next_level <= level:
                end_line = next_line_index
                break

        sections.append(
            MemorySection(
                title=title,
                level=level,
                start_line=line_index,
                end_line=end_line,
            )
        )

    return sections


def iter_vault_notes(vault_path: Path) -> list[VaultNote]:
    notes: list[VaultNote] = []
    for folder_name in VAULT_DIRS:
        folder = vault_path / folder_name
        if not folder.exists():
            continue

        for path in folder.rglob("*.md"):
            if any(part in IGNORED_PARTS for part in path.parts):
                continue

            rel_path = path.relative_to(vault_path).as_posix()
            if rel_path == GENERATED_REFERENCE_NOTE.as_posix():
                continue

            notes.append(
                VaultNote(
                    path=path,
                    rel_path=rel_path,
                    mtime=path.stat().st_mtime,
                )
            )

    notes.sort(key=lambda note: note.rel_path)
    return notes


def build_reference_note(memory_path: Path, sections: Iterable[MemorySection]) -> str:
    lines = [
        "# MEMORY Sync Index",
        "",
        f"Source: {memory_path}",
        "",
        "This note is managed by scripts/sync_obsidian.py.",
        "",
        "## MEMORY Sections",
    ]

    indexable_sections = [
        section for section in sections if section.title.lower() != DAILY_NOTES_TITLE.lower()
    ]
    if not indexable_sections:
        lines.append("- No sections found in MEMORY.md")
    else:
        for section in indexable_sections:
            lines.append(f"- {section.title}")

    lines.append("")
    return "\n".join(lines)


def build_wikilink(rel_path: str) -> str:
    rel = Path(rel_path)
    without_suffix = rel.with_suffix("")
    return f"[[{without_suffix.as_posix()}]]"


def build_daily_note_entry(rel_path: str, now: datetime) -> str:
    return f"- {now.date().isoformat()}: Added vault note {build_wikilink(rel_path)}"


def add_daily_notes_entries(text: str, entries: list[str]) -> tuple[str, list[str]]:
    if not entries:
        return text, []

    lines = text.splitlines()
    sections = parse_memory_sections(text)
    daily_section = next(
        (section for section in sections if section.title.lower() == DAILY_NOTES_TITLE.lower()),
        None,
    )

    added_entries: list[str] = []
    if daily_section is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend([f"## {DAILY_NOTES_TITLE}", ""])
        for entry in entries:
            if entry not in lines:
                lines.append(entry)
                added_entries.append(entry)
        return "\n".join(lines) + "\n", added_entries

    section_lines = lines[daily_section.start_line : daily_section.end_line]
    for entry in entries:
        if entry in section_lines:
            continue
        insert_at = daily_section.end_line
        lines.insert(insert_at, entry)
        added_entries.append(entry)
        daily_section = MemorySection(
            title=daily_section.title,
            level=daily_section.level,
            start_line=daily_section.start_line,
            end_line=daily_section.end_line + 1,
        )

    return "\n".join(lines) + "\n", added_entries


def apply_stale_marker(
    text: str,
    section_title: str,
    note_rel_path: str,
    note_timestamp: str,
    should_mark: bool,
) -> tuple[str, bool]:
    marker = f"{STALE_PREFIX} `{note_rel_path}` changed at {note_timestamp}"
    lines = text.splitlines()
    sections = parse_memory_sections(text)
    section = next((item for item in sections if item.title == section_title), None)
    if section is None:
        return text, False

    marker_indexes = [
        index
        for index in range(section.start_line + 1, min(section.end_line, section.start_line + 5))
        if lines[index].startswith(STALE_PREFIX)
    ]

    changed = False
    if should_mark:
        if marker_indexes:
            first_index = marker_indexes[0]
            if lines[first_index] != marker:
                lines[first_index] = marker
                changed = True
            for index in reversed(marker_indexes[1:]):
                del lines[index]
                changed = True
        else:
            lines.insert(section.start_line + 1, marker)
            changed = True
    elif marker_indexes:
        for index in reversed(marker_indexes):
            del lines[index]
        changed = True

    if not changed:
        return text, False

    return "\n".join(lines) + "\n", True


def build_diff(before: str, after: str, from_name: str, to_name: str) -> list[str]:
    return list(
        unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=from_name,
            tofile=to_name,
            lineterm="",
        )
    )


def sync_memory_and_vault(
    memory_path: Path,
    vault_path: Path,
    *,
    dry_run: bool = False,
    log_dir: Path | None = None,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    memory_path = memory_path.resolve()
    vault_path = vault_path.resolve()
    log_dir = (log_dir or (Path(__file__).resolve().parents[1] / "logs")).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)

    original_memory = read_text(memory_path)
    if not original_memory:
        original_memory = "# MEMORY\n"
    memory_text = original_memory if original_memory.endswith("\n") else original_memory + "\n"
    memory_sections = parse_memory_sections(memory_text)
    vault_notes = iter_vault_notes(vault_path)
    memory_mtime = memory_path.stat().st_mtime if memory_path.exists() else 0.0

    actions: list[dict] = []
    conflicts: list[dict] = []

    generated_note_path = vault_path / GENERATED_REFERENCE_NOTE
    generated_before = read_text(generated_note_path)
    generated_after = build_reference_note(memory_path, memory_sections)
    if generated_before != generated_after:
        actions.append(
            {
                "type": "update_vault_reference",
                "path": str(generated_note_path),
                "status": "planned" if dry_run else "applied",
                "diff": build_diff(
                    generated_before,
                    generated_after,
                    "before_reference",
                    "after_reference",
                ),
            }
        )
        if not dry_run:
            write_text(generated_note_path, generated_after)

    section_by_slug = {section.slug: section for section in memory_sections}

    new_daily_entries: list[str] = []
    for note in vault_notes:
        if not note.rel_path.startswith("memory/"):
            continue
        entry = build_daily_note_entry(note.rel_path, now)
        if build_wikilink(note.rel_path) in memory_text:
            continue
        new_daily_entries.append(entry)

    updated_memory, added_entries = add_daily_notes_entries(memory_text, new_daily_entries)
    if added_entries:
        actions.append(
            {
                "type": "update_memory_daily_notes",
                "path": str(memory_path),
                "status": "planned" if dry_run else "applied",
                "entries": added_entries,
                "diff": build_diff(memory_text, updated_memory, "before_memory", "after_memory"),
            }
        )
        memory_text = updated_memory

    for note in vault_notes:
        section = section_by_slug.get(note.stem_slug)
        if section is None:
            continue

        note_timestamp = isoformat_timestamp(note.mtime)
        prefer_vault = note.mtime > memory_mtime
        stale_updated_memory, changed = apply_stale_marker(
            memory_text,
            section.title,
            note.rel_path,
            note_timestamp,
            should_mark=prefer_vault,
        )

        if prefer_vault or memory_mtime > note.mtime:
            conflicts.append(
                {
                    "section": section.title,
                    "note": note.rel_path,
                    "memory_timestamp": isoformat_timestamp(memory_mtime),
                    "vault_timestamp": note_timestamp,
                    "winner": "vault" if prefer_vault else "memory",
                }
            )

        if changed:
            actions.append(
                {
                    "type": "mark_memory_section_stale" if prefer_vault else "clear_memory_stale_marker",
                    "path": str(memory_path),
                    "status": "planned" if dry_run else "applied",
                    "section": section.title,
                    "note": note.rel_path,
                    "diff": build_diff(
                        memory_text,
                        stale_updated_memory,
                        "before_memory",
                        "after_memory",
                    ),
                }
            )
            memory_text = stale_updated_memory

    if memory_text != original_memory and not dry_run:
        write_text(memory_path, memory_text)

    report_path = log_dir / f"sync_obsidian_{now.strftime('%Y%m%d')}.json"
    report = {
        "timestamp": now.isoformat(),
        "dry_run": dry_run,
        "memory_path": str(memory_path),
        "vault_path": str(vault_path),
        "report_path": str(report_path),
        "actions": actions,
        "conflicts": conflicts,
        "stats": {
            "memory_sections": len(memory_sections),
            "vault_notes": len(vault_notes),
            "daily_note_entries_added": len(added_entries),
            "changes_planned_or_applied": len(actions),
            "conflicts_logged": len(conflicts),
        },
    }

    write_text(report_path, json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def print_report(report: dict) -> None:
    dry_run = report["dry_run"]
    color_enabled = sys.stdout.isatty()
    mode_color = Color.YELLOW if dry_run else Color.GREEN
    print(
        colorize(
            f"{'DRY-RUN' if dry_run else 'SYNC'} completed: "
            f"{report['stats']['changes_planned_or_applied']} change(s), "
            f"{report['stats']['conflicts_logged']} conflict(s)",
            mode_color,
            color_enabled,
        )
    )
    print(colorize(f"Report: {report['report_path']}", Color.BLUE, color_enabled))

    for action in report["actions"]:
        label = "[plan]" if dry_run else "[apply]"
        print(
            colorize(
                f"{label} {action['type']} -> {action['path']}",
                Color.CYAN,
                color_enabled,
            )
        )

    for conflict in report["conflicts"]:
        print(
            colorize(
                "conflict: "
                f"{conflict['section']} <-> {conflict['note']} "
                f"(winner: {conflict['winner']})",
                Color.RED,
                color_enabled,
            )
        )


def resolve_path(cli_value: str | None, env_var: str, default: Path) -> Path:
    if cli_value:
        return Path(cli_value).expanduser()
    env_value = os.environ.get(env_var)
    if env_value:
        return Path(env_value).expanduser()
    return default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing files.")
    parser.add_argument(
        "--memory",
        help=f"Path to MEMORY.md (defaults to {DEFAULT_MEMORY_ENV} or ./MEMORY.md).",
    )
    parser.add_argument(
        "--vault",
        help=f"Path to the Obsidian vault (defaults to {DEFAULT_VAULT_ENV} or current directory).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cwd = Path.cwd()
    memory_path = resolve_path(args.memory, DEFAULT_MEMORY_ENV, cwd / "MEMORY.md")
    vault_path = resolve_path(args.vault, DEFAULT_VAULT_ENV, cwd)

    report = sync_memory_and_vault(
        memory_path=memory_path,
        vault_path=vault_path,
        dry_run=args.dry_run,
    )
    print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
