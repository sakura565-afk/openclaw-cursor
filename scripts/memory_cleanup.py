#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path


HEADING_RE = re.compile(r"^#{2,6}\s+")
DATE_LINE_RE = re.compile(
    r"(?im)^\s*(?:last\s*updated|updated|created|date)\s*[:=]\s*(\d{4}-\d{2}-\d{2})\s*$"
)
JSON_DATE_RE = re.compile(
    r'(?i)"(?:last_updated|updated|created|date)"\s*:\s*"(\d{4}-\d{2}-\d{2})"'
)
COMMENT_DATE_RE = re.compile(
    r"(?i)<!--\s*(?:last\s*updated|updated|created|date)\s*:\s*(\d{4}-\d{2}-\d{2})\s*-->"
)
FILENAME_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
ANSI_COLORS = {
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}


@dataclass
class ParsedFile:
    path: Path
    preamble: str
    entries: list[str]


@dataclass
class MemoryEntry:
    source: Path
    index: int
    text: str
    updated_at: date
    entry_id: str
    active: bool = True
    action: str | None = None
    action_target: str | None = None
    normalized: str = field(init=False)

    def __post_init__(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self.normalized = normalize_entry(self.text)


def colorize(text: str, color: str) -> str:
    prefix = ANSI_COLORS.get(color, "")
    if not prefix:
        return text
    return f"{prefix}{text}{ANSI_COLORS['reset']}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean up OpenClaw memory notes.")
    parser.add_argument("--days", type=int, default=90, help="Archive threshold in days.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview cleanup without rewriting memory or archive files.",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Create backups before applying cleanup changes.",
    )
    return parser.parse_args(argv)


def discover_memory_files(root: Path) -> tuple[Path | None, list[Path]]:
    primary = root / "MEMORY.md"
    memory_dir = root / "memory"
    fallback_primary = memory_dir / "MEMORY.md"
    if not primary.exists() and fallback_primary.exists():
        primary = fallback_primary
    if not primary.exists():
        primary = None

    files: list[Path] = []
    if primary is not None:
        files.append(primary)

    if memory_dir.exists():
        for path in sorted(memory_dir.rglob("*.md")):
            if path == primary:
                continue
            if "archive" in path.parts:
                continue
            if ".backup_" in path.name:
                continue
            files.append(path)

    return primary, files


def load_parsed_file(path: Path) -> ParsedFile:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    normalized = text.replace("\r\n", "\n")
    if not normalized.strip():
        return ParsedFile(path=path, preamble="", entries=[])

    lines = normalized.split("\n")
    heading_indexes = [index for index, line in enumerate(lines) if HEADING_RE.match(line)]
    if heading_indexes:
        preamble = "\n".join(lines[: heading_indexes[0]]).strip()
        entries: list[str] = []
        for position, start in enumerate(heading_indexes):
            end = heading_indexes[position + 1] if position + 1 < len(heading_indexes) else len(lines)
            block = "\n".join(lines[start:end]).strip()
            if block:
                entries.append(block)
        return ParsedFile(path=path, preamble=preamble, entries=entries)

    blocks = [block.strip() for block in re.split(r"\n\s*\n+", normalized.strip()) if block.strip()]
    return ParsedFile(path=path, preamble="", entries=blocks)


def render_file(parsed_file: ParsedFile, entries: list[str]) -> str:
    parts: list[str] = []
    if parsed_file.preamble:
        parts.append(parsed_file.preamble.strip())
    parts.extend(entry.strip() for entry in entries if entry.strip())
    if not parts:
        return ""
    return "\n\n".join(parts).strip() + "\n"


def parse_entry_date(path: Path, text: str) -> date:
    matches: list[date] = []
    for pattern in (DATE_LINE_RE, JSON_DATE_RE, COMMENT_DATE_RE):
        for raw_date in pattern.findall(text):
            try:
                matches.append(date.fromisoformat(raw_date))
            except ValueError:
                continue

    if matches:
        return max(matches)

    stem_match = FILENAME_DATE_RE.search(path.stem)
    if stem_match:
        try:
            return date.fromisoformat(stem_match.group(1))
        except ValueError:
            pass

    return datetime.fromtimestamp(path.stat().st_mtime).date()


def is_metadata_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if DATE_LINE_RE.match(stripped):
        return True
    if JSON_DATE_RE.search(stripped):
        return True
    if COMMENT_DATE_RE.search(stripped):
        return True
    return False


def is_heading_line(line: str) -> bool:
    return bool(HEADING_RE.match(line.strip()))


def content_lines(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if is_metadata_line(stripped):
            continue
        if is_heading_line(stripped):
            continue
        lines.append(" ".join(stripped.split()).lower())
    return lines


def normalize_entry(text: str) -> str:
    normalized_lines = content_lines(text)
    normalized = "\n".join(line for line in normalized_lines if line).strip()
    if normalized:
        return normalized
    return " ".join(text.split()).lower()


def merge_entry_texts(keeper: str, other: str) -> str:
    keeper_lines = keeper.rstrip().splitlines()
    existing = {
        " ".join(line.strip().split()).lower()
        for line in keeper_lines
        if line.strip() and not is_metadata_line(line) and not is_heading_line(line)
    }

    extras: list[str] = []
    for line in other.splitlines():
        stripped = line.strip()
        if not stripped or is_metadata_line(line) or is_heading_line(line):
            continue
        canonical = " ".join(stripped.split()).lower()
        if canonical in existing:
            continue
        extras.append(line.rstrip())
        existing.add(canonical)

    if not extras:
        return keeper.strip()

    merged = keeper.rstrip()
    if merged:
        merged += "\n"
    merged += "\n".join(extras)
    return merged.strip()


def build_archive_filename(root: Path, source: Path, now: datetime) -> str:
    relative_parts = list(source.relative_to(root).parts)
    safe_parts = [part.replace(".", "_") for part in relative_parts]
    return f"{'_'.join(safe_parts)}.archive_{now.strftime('%Y%m%d_%H%M%S')}.md"


def build_archive_content(root: Path, source: Path, entries: list[MemoryEntry], today: date) -> str:
    lines = [
        f"# Archived entries from {source.name}",
        "",
        f"- Cleanup date: {today.isoformat()}",
        f"- Original file: {source.relative_to(root).as_posix()}",
        "",
    ]
    for entry in sorted(entries, key=lambda item: (item.updated_at, item.index)):
        lines.append("## Archived entry")
        lines.append("")
        lines.append(f"- Last updated: {entry.updated_at.isoformat()}")
        lines.append("")
        lines.append(entry.text.strip())
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def create_backup(source: Path, backup_path: Path) -> None:
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, backup_path)
    if not backup_path.exists():
        raise RuntimeError(f"Backup was not created for {source}")
    if source.read_bytes() != backup_path.read_bytes():
        raise RuntimeError(f"Backup content mismatch for {source}")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def update_weekly_summary(summary_path: Path, run_summary: dict) -> None:
    runs: list[dict] = []
    if summary_path.exists():
        try:
            existing = json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and isinstance(existing.get("runs"), list):
                runs = existing["runs"]
        except json.JSONDecodeError:
            runs = []

    runs.append(run_summary)
    aggregate = {
        "runs": len(runs),
        "archived_entries": sum(item.get("archived_entries", 0) for item in runs),
        "duplicates_removed": sum(item.get("duplicates_removed", 0) for item in runs),
        "similar_entries_compacted": sum(item.get("similar_entries_compacted", 0) for item in runs),
        "size_before": sum(item.get("size_before", 0) for item in runs),
        "size_after": sum(item.get("size_after", 0) for item in runs),
        "bytes_saved": sum(item.get("bytes_saved", 0) for item in runs),
    }
    payload = {
        "week": run_summary["week"],
        "updated_at": run_summary["run_at"],
        "aggregate": aggregate,
        "runs": runs,
    }
    write_json(summary_path, payload)


def build_text_report(report: dict) -> str:
    archived = ", ".join(report["archived_files"]) if report["archived_files"] else "none"
    backups = ", ".join(report["backup_files"]) if report["backup_files"] else "none"
    lines = [
        f"Run at: {report['run_at']}",
        f"Dry run: {report['dry_run']}",
        f"Days threshold: {report['days_threshold']}",
        f"Entries before: {report['entries_before']}",
        f"Entries after: {report['entries_after']}",
        f"Archived entries: {report['archived_entries']}",
        f"Duplicates removed: {report['duplicates_removed']}",
        f"Similar entries compacted: {report['similar_entries_compacted']}",
        f"Source size before: {report['size_before']}",
        f"Source size after: {report['size_after']}",
        f"Bytes saved: {report['bytes_saved']}",
        f"Backups: {backups}",
        f"Archive files: {archived}",
        "",
        "Cleaned items:",
    ]
    if report["cleaned_items"]:
        lines.extend(f"- {item}" for item in report["cleaned_items"])
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def cleanup_memory(
    root: Path,
    days: int = 90,
    dry_run: bool = False,
    backup: bool = False,
    today: date | None = None,
    now: datetime | None = None,
) -> dict:
    today = today or date.today()
    now = now or datetime.now()
    primary_file, memory_files = discover_memory_files(root)
    parsed_files = {path: load_parsed_file(path) for path in memory_files}
    original_contents = {
        path: path.read_text(encoding="utf-8") if path.exists() else "" for path in memory_files
    }

    entries: list[MemoryEntry] = []
    for path in memory_files:
        parsed_file = parsed_files[path]
        for index, entry_text in enumerate(parsed_file.entries):
            entries.append(
                MemoryEntry(
                    source=path,
                    index=index,
                    text=entry_text.strip(),
                    updated_at=parse_entry_date(path, entry_text),
                    entry_id=f"{path.name}:{index}",
                )
            )

    cleaned_items: list[str] = []
    duplicates_removed = 0
    similar_entries_compacted = 0
    archived_entries = 0

    groups: dict[str, list[MemoryEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.normalized, []).append(entry)

    def sort_key(item: MemoryEntry) -> tuple[date, int, str, int]:
        return (item.updated_at, len(item.normalized), item.source.as_posix(), -item.index)

    for group_entries in groups.values():
        if len(group_entries) < 2:
            continue
        ranked = sorted(group_entries, key=sort_key, reverse=True)
        keeper = ranked[0]
        for duplicate in ranked[1:]:
            duplicate.active = False
            duplicate.action = "duplicate_removed"
            duplicate.action_target = keeper.entry_id
            duplicates_removed += 1
            cleaned_items.append(
                f"Removed duplicate {duplicate.entry_id} from {duplicate.source.name}; kept {keeper.entry_id}"
            )

    active_entries = sorted((entry for entry in entries if entry.active), key=sort_key, reverse=True)
    for index, keeper in enumerate(active_entries):
        if not keeper.active:
            continue
        for other in active_entries[index + 1 :]:
            if not other.active:
                continue
            ratio = SequenceMatcher(
                None,
                "\n".join(content_lines(keeper.text)),
                "\n".join(content_lines(other.text)),
            ).ratio()
            if ratio < 0.90 or keeper.normalized == other.normalized:
                continue
            keeper.text = merge_entry_texts(keeper.text, other.text)
            keeper.updated_at = max(keeper.updated_at, other.updated_at)
            keeper.refresh()
            other.active = False
            other.action = "similarity_compacted"
            other.action_target = keeper.entry_id
            similar_entries_compacted += 1
            cleaned_items.append(
                f"Compacted similar entry {other.entry_id} into {keeper.entry_id} ({ratio:.2%} overlap)"
            )

    archive_dir = root / "memory" / "archive"
    archive_map: dict[Path, list[MemoryEntry]] = {}
    threshold = timedelta(days=days)
    for entry in entries:
        if not entry.active:
            continue
        if today - entry.updated_at <= threshold:
            continue
        archive_map.setdefault(entry.source, []).append(entry)
        entry.active = False
        entry.action = "archived"
        archived_entries += 1
        cleaned_items.append(
            f"Archived stale entry {entry.entry_id} from {entry.source.name} last updated {entry.updated_at.isoformat()}"
        )

    final_entries_by_path: dict[Path, list[str]] = {path: [] for path in memory_files}
    for path in memory_files:
        file_entries = sorted(
            (entry for entry in entries if entry.active and entry.source == path),
            key=lambda item: item.index,
        )
        final_entries_by_path[path] = [entry.text for entry in file_entries]

    rendered_files = {
        path: render_file(parsed_files[path], final_entries_by_path[path]) for path in memory_files
    }

    archive_payloads: list[tuple[Path, str]] = []
    for source, source_entries in archive_map.items():
        archive_path = archive_dir / build_archive_filename(root, source, now)
        archive_content = build_archive_content(root, source, source_entries, today)
        archive_payloads.append((archive_path, archive_content))

    changed_sources = {
        path for path, rendered in rendered_files.items() if rendered != original_contents.get(path, "")
    }

    backup_paths: list[Path] = []
    if backup and primary_file is not None and primary_file.exists():
        backup_paths.append(root / "memory" / f"MEMORY.md.backup_{today.strftime('%Y%m%d')}")
    for source in sorted(changed_sources, key=lambda path: path.as_posix()):
        if primary_file is not None and source == primary_file:
            continue
        backup_paths.append(source.with_name(f"{source.name}.backup_{today.strftime('%Y%m%d')}"))

    if backup:
        backup_sources: list[tuple[Path, Path]] = []
        seen_targets: set[Path] = set()
        if primary_file is not None and primary_file.exists():
            primary_backup = root / "memory" / f"MEMORY.md.backup_{today.strftime('%Y%m%d')}"
            backup_sources.append((primary_file, primary_backup))
            seen_targets.add(primary_backup)
        for source in sorted(changed_sources, key=lambda path: path.as_posix()):
            if not source.exists():
                continue
            backup_path = source.with_name(f"{source.name}.backup_{today.strftime('%Y%m%d')}")
            if backup_path in seen_targets:
                continue
            backup_sources.append((source, backup_path))
            seen_targets.add(backup_path)
        for source, backup_path in backup_sources:
            create_backup(source, backup_path)

    if not dry_run:
        for archive_path, archive_content in archive_payloads:
            write_text(archive_path, archive_content)
        for path, rendered in rendered_files.items():
            if path not in changed_sources:
                continue
            write_text(path, rendered)

    size_before = sum(len(content.encode("utf-8")) for content in original_contents.values())
    size_after = sum(len(rendered.encode("utf-8")) for rendered in rendered_files.values())
    run_at = now.isoformat(timespec="seconds")
    week = today.isocalendar()
    week_key = f"{week.year}-W{week.week:02d}"

    logs_dir = root / "logs"
    report_slug = now.strftime("%Y%m%d_%H%M%S")
    report_path = logs_dir / f"cleanup_report_{report_slug}.json"
    text_report_path = logs_dir / f"cleanup_report_{report_slug}.txt"
    archive_manifest_path = logs_dir / f"archive_manifest_{report_slug}.json"
    weekly_summary_path = logs_dir / f"weekly_summary_{week_key}.json"

    report = {
        "run_at": run_at,
        "days_threshold": days,
        "dry_run": dry_run,
        "backup_requested": backup,
        "memory_files": [path.relative_to(root).as_posix() for path in memory_files],
        "entries_before": len(entries),
        "entries_after": sum(1 for entry in entries if entry.active),
        "archived_entries": archived_entries,
        "duplicates_removed": duplicates_removed,
        "similar_entries_compacted": similar_entries_compacted,
        "size_before": size_before,
        "size_after": size_after,
        "bytes_saved": size_before - size_after,
        "backup_files": [path.relative_to(root).as_posix() for path in backup_paths if path.exists()],
        "archived_files": [path.relative_to(root).as_posix() for path, _ in archive_payloads],
        "cleaned_items": cleaned_items,
        "report_path": report_path.relative_to(root).as_posix(),
        "text_report_path": text_report_path.relative_to(root).as_posix(),
        "archive_manifest_path": archive_manifest_path.relative_to(root).as_posix(),
        "weekly_summary_path": weekly_summary_path.relative_to(root).as_posix(),
    }

    write_json(report_path, report)
    write_text(text_report_path, build_text_report(report))
    write_json(
        archive_manifest_path,
        {
            "generated_at": run_at,
            "dry_run": dry_run,
            "archived_files": report["archived_files"],
        },
    )
    update_weekly_summary(
        weekly_summary_path,
        {
            "run_at": run_at,
            "week": week_key,
            "dry_run": dry_run,
            "archived_entries": archived_entries,
            "duplicates_removed": duplicates_removed,
            "similar_entries_compacted": similar_entries_compacted,
            "size_before": size_before,
            "size_after": size_after,
            "bytes_saved": size_before - size_after,
            "cleaned_items": cleaned_items,
        },
    )

    return report


def print_console_report(report: dict) -> None:
    title = "OpenClaw memory cleanup"
    print(colorize(title, "bold"))
    print(colorize(f"Days threshold: {report['days_threshold']}", "cyan"))
    run_mode = "DRY RUN" if report["dry_run"] else "APPLIED"
    mode_color = "yellow" if report["dry_run"] else "green"
    print(colorize(f"Mode: {run_mode}", mode_color))
    print(colorize(f"Archived entries: {report['archived_entries']}", "blue"))
    print(colorize(f"Duplicates removed: {report['duplicates_removed']}", "blue"))
    print(colorize(f"Similar entries compacted: {report['similar_entries_compacted']}", "blue"))
    print(colorize(f"Size before: {report['size_before']} bytes", "cyan"))
    print(colorize(f"Size after: {report['size_after']} bytes", "cyan"))
    print(colorize(f"Bytes saved: {report['bytes_saved']} bytes", "green"))
    print(colorize(f"Cleanup report: {report['report_path']}", "green"))
    print(colorize(f"Weekly summary: {report['weekly_summary_path']}", "green"))
    if report["backup_files"]:
        print(colorize("Backups:", "yellow"))
        for backup_path in report["backup_files"]:
            print(colorize(f"  - {backup_path}", "yellow"))
    if report["archived_files"]:
        print(colorize("Archive files:", "green"))
        for archive_path in report["archived_files"]:
            print(colorize(f"  - {archive_path}", "green"))
    if report["cleaned_items"]:
        print(colorize("Cleaned items:", "bold"))
        for item in report["cleaned_items"]:
            print(f"  - {item}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = cleanup_memory(
        root=Path.cwd(),
        days=args.days,
        dry_run=args.dry_run,
        backup=args.backup,
    )
    print_console_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
