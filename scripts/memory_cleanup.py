from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Sequence


RESET = "\033[0m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"

DATE_METADATA_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:last\s+updated|updated|created|date)\s*[:\-]\s*(\d{4}-\d{2}-\d{2})\s*$",
    re.IGNORECASE,
)
ANY_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
COMPACT_DATE_RE = re.compile(r"\b(\d{4})(\d{2})(\d{2})\b")
SECTION_HEADING_RE = re.compile(r"^\s{0,3}##+\s+")


@dataclass
class Entry:
    source_path: Path
    heading_line: str
    raw_text: str
    body_text: str
    last_updated: date
    semantic_text: str
    synthetic: bool = False
    archived: bool = False
    duplicate_of: str | None = None
    merged_into: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def entry_id(self) -> str:
        label = self.heading_line.strip() or "entry"
        return f"{self.source_path.as_posix()}::{label}"

    def render(self) -> str:
        if not self.synthetic:
            return self.raw_text.rstrip() + "\n"

        blocks: list[str] = []
        if self.heading_line.strip():
            blocks.append(self.heading_line.rstrip())
        blocks.append(f"Updated: {self.last_updated.isoformat()}")
        if self.notes:
            blocks.extend(self.notes)
        if self.body_text.strip():
            blocks.append("")
            blocks.append(self.body_text.strip())
        return "\n".join(blocks).rstrip() + "\n"


@dataclass
class ParsedFile:
    path: Path
    preamble: str
    entries: list[Entry]
    original_text: str


def colorize(color: str, text: str) -> str:
    return f"{color}{text}{RESET}"


def human_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{int(size)} B"


def normalize_lines(text: str) -> list[str]:
    cleaned: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if DATE_METADATA_RE.match(stripped):
            continue
        cleaned.append(re.sub(r"\s+", " ", stripped))
    return cleaned


def semantic_text(text: str) -> str:
    return "\n".join(normalize_lines(text)).strip()


def merge_bodies(primary: str, secondary: str) -> str:
    merged: list[str] = []
    seen: set[str] = set()
    for line in normalize_lines(primary) + normalize_lines(secondary):
        if line not in seen:
            seen.add(line)
            merged.append(line)
    return "\n".join(merged).strip()


def parse_date_candidate(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def dates_from_text(text: str) -> list[date]:
    values: list[date] = []
    for match in DATE_METADATA_RE.finditer(text):
        parsed = parse_date_candidate(match.group(1))
        if parsed is not None:
            values.append(parsed)
    for match in ANY_DATE_RE.finditer(text):
        parsed = parse_date_candidate(match.group(1))
        if parsed is not None:
            values.append(parsed)
    return values


def date_from_stem(path: Path) -> date | None:
    stem = path.stem
    for candidate in (stem, stem.replace("_", "-")):
        parsed = parse_date_candidate(candidate)
        if parsed is not None:
            return parsed
    compact_match = COMPACT_DATE_RE.fullmatch(stem)
    if compact_match:
        compact = "-".join(compact_match.groups())
        return parse_date_candidate(compact)
    return None


def infer_last_updated(section_text: str, path: Path) -> date:
    matches = dates_from_text(section_text)
    if matches:
        return max(matches)
    stem_date = date_from_stem(path)
    if stem_date is not None:
        return stem_date
    return datetime.fromtimestamp(path.stat().st_mtime).date()


def split_sections(text: str) -> tuple[str, list[str]]:
    if not text.strip():
        return "", []

    lines = text.splitlines(keepends=True)
    preamble: list[str] = []
    sections: list[str] = []
    current: list[str] = []
    in_section = False

    for line in lines:
        if SECTION_HEADING_RE.match(line):
            if in_section and current:
                sections.append("".join(current))
            elif not in_section and preamble:
                pass
            current = [line]
            in_section = True
            continue

        if in_section:
            current.append(line)
        else:
            preamble.append(line)

    if in_section and current:
        sections.append("".join(current))
    elif not sections and not in_section and text.strip():
        sections.append(text)
        preamble = []

    return "".join(preamble), sections


def split_heading_and_body(section: str) -> tuple[str, str]:
    lines = section.splitlines()
    if not lines:
        return "", ""

    first = lines[0]
    if SECTION_HEADING_RE.match(first):
        body_lines = [line for line in lines[1:] if not DATE_METADATA_RE.match(line.strip())]
        body = "\n".join(body_lines).strip()
        return first.rstrip(), body

    body_lines = [line for line in lines if not DATE_METADATA_RE.match(line.strip())]
    body = "\n".join(body_lines).strip()
    return "", body


def parse_file(path: Path) -> ParsedFile:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    preamble, sections = split_sections(text)
    entries: list[Entry] = []
    for section in sections:
        heading_line, body_text = split_heading_and_body(section)
        entries.append(
            Entry(
                source_path=path,
                heading_line=heading_line,
                raw_text=section,
                body_text=body_text,
                last_updated=infer_last_updated(section, path),
                semantic_text=semantic_text(section),
            )
        )
    return ParsedFile(path=path, preamble=preamble, entries=entries, original_text=text)


def discover_memory_files(root: Path) -> tuple[Path | None, list[Path]]:
    root_memory = root / "MEMORY.md"
    nested_memory = root / "memory" / "MEMORY.md"
    main_memory = root_memory if root_memory.exists() else nested_memory if nested_memory.exists() else None

    daily_dir = root / "memory"
    daily_files: list[Path] = []
    if daily_dir.exists():
        for path in sorted(daily_dir.glob("*.md")):
            if path.name == "MEMORY.md":
                continue
            if ".backup_" in path.name:
                continue
            daily_files.append(path)

    return main_memory, daily_files


def rebuild_file(parsed: ParsedFile, active_entries: Iterable[Entry]) -> str:
    pieces: list[str] = []
    if parsed.preamble.strip():
        pieces.append(parsed.preamble.rstrip())
    rendered = [entry.render().rstrip() for entry in active_entries if entry.render().strip()]
    if rendered:
        pieces.extend(rendered)
    if not pieces:
        return ""
    return "\n\n".join(pieces).rstrip() + "\n"


def archive_path_for(root: Path, source: Path, stamp: str) -> Path:
    archive_dir = root / "memory" / "archive"
    safe_name = source.stem.replace("/", "_")
    return archive_dir / f"{safe_name}_archive_{stamp}.md"


def ensure_unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def backup_path_for(root: Path, path: Path, stamp: str, main_memory: Path | None) -> Path:
    backup_root = root / "memory"
    if main_memory is not None and path == main_memory:
        return backup_root / f"MEMORY.md.backup_{stamp}"

    relative_name = path.relative_to(root).as_posix().replace("/", "__")
    return backup_root / "backups" / f"{relative_name}.backup_{stamp}"


def backup_file(source: Path, destination: Path) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    original_bytes = source.read_bytes()
    destination.write_bytes(original_bytes)
    return destination.exists() and destination.read_bytes() == original_bytes


def build_weekly_summary(report: dict[str, object]) -> str:
    return (
        f"- Run: {report['timestamp']}\n"
        f"- Dry run: {report['dry_run']}\n"
        f"- Threshold: {report['days_threshold']} days\n"
        f"- Files processed: {report['files_processed']}\n"
        f"- Archived entries: {len(report['archived_entries'])}\n"
        f"- Duplicates removed: {len(report['removed_duplicates'])}\n"
        f"- Similar entries merged: {len(report['merged_entries'])}\n"
        f"- Active size: {report['size_before_bytes']} -> {report['size_after_bytes']} bytes\n"
    )


def run_cleanup(
    root: Path,
    days: int = 90,
    dry_run: bool = False,
    backup: bool = False,
    today: date | None = None,
) -> dict[str, object]:
    current_day = today or date.today()
    timestamp = datetime.combine(current_day, datetime.min.time()).isoformat()
    stamp = current_day.strftime("%Y%m%d")
    cutoff = current_day - timedelta(days=days)

    main_memory, daily_files = discover_memory_files(root)
    target_files = [path for path in [main_memory, *daily_files] if path is not None]

    parsed_files = [parse_file(path) for path in target_files]
    all_entries: list[Entry] = [entry for parsed in parsed_files for entry in parsed.entries]

    archived_entries: list[dict[str, object]] = []
    for entry in all_entries:
        if entry.last_updated < cutoff:
            entry.archived = True
            archived_entries.append(
                {
                    "entry_id": entry.entry_id,
                    "source": entry.source_path.as_posix(),
                    "updated": entry.last_updated.isoformat(),
                }
            )

    active_entries = [entry for entry in all_entries if not entry.archived]
    active_entries.sort(key=lambda item: (item.last_updated, item.entry_id), reverse=True)

    duplicate_map: dict[str, Entry] = {}
    removed_duplicates: list[dict[str, object]] = []
    deduped_entries: list[Entry] = []
    for entry in active_entries:
        if not entry.semantic_text:
            deduped_entries.append(entry)
            continue
        existing = duplicate_map.get(entry.semantic_text)
        if existing is None:
            duplicate_map[entry.semantic_text] = entry
            deduped_entries.append(entry)
            continue
        entry.duplicate_of = existing.entry_id
        removed_duplicates.append(
            {
                "entry_id": entry.entry_id,
                "kept": existing.entry_id,
                "source": entry.source_path.as_posix(),
            }
        )

    merged_entries: list[dict[str, object]] = []
    compacted_entries: list[Entry] = []
    for entry in deduped_entries:
        target: Entry | None = None
        for candidate in compacted_entries:
            if not entry.semantic_text or not candidate.semantic_text:
                continue
            overlap = SequenceMatcher(None, candidate.semantic_text, entry.semantic_text).ratio()
            if overlap >= 0.90:
                target = candidate
                break
        if target is None:
            compacted_entries.append(entry)
            continue

        target.body_text = merge_bodies(target.body_text, entry.body_text)
        target.last_updated = max(target.last_updated, entry.last_updated)
        target.semantic_text = semantic_text(target.body_text)
        target.synthetic = True
        if entry.heading_line and not target.heading_line:
            target.heading_line = entry.heading_line
        note = f"- Merged duplicate context from {entry.source_path.as_posix()}"
        if note not in target.notes:
            target.notes.append(note)
        entry.merged_into = target.entry_id
        merged_entries.append(
            {
                "entry_id": entry.entry_id,
                "merged_into": target.entry_id,
                "source": entry.source_path.as_posix(),
            }
        )

    final_entries_by_file: dict[Path, list[Entry]] = {parsed.path: [] for parsed in parsed_files}
    for entry in compacted_entries:
        final_entries_by_file.setdefault(entry.source_path, []).append(entry)

    size_before = sum(len(parsed.original_text.encode("utf-8")) for parsed in parsed_files)
    rendered_by_file: dict[Path, str] = {}
    changed_files: list[Path] = []
    for parsed in parsed_files:
        rendered = rebuild_file(parsed, final_entries_by_file.get(parsed.path, []))
        rendered_by_file[parsed.path] = rendered
        if rendered != parsed.original_text:
            changed_files.append(parsed.path)
    size_after = sum(len(rendered.encode("utf-8")) for rendered in rendered_by_file.values())

    backups: list[str] = []
    backup_failures: list[str] = []
    if backup:
        for path in target_files:
            destination = backup_path_for(root, path, stamp, main_memory)
            if backup_file(path, destination):
                backups.append(destination.as_posix())
            else:
                backup_failures.append(path.as_posix())

    archive_writes: list[dict[str, object]] = []
    archive_manifest: list[dict[str, object]] = []
    grouped_archives: dict[Path, list[Entry]] = {}
    for entry in all_entries:
        if entry.archived:
            grouped_archives.setdefault(entry.source_path, []).append(entry)

    for source_path, entries in grouped_archives.items():
        archive_target = ensure_unique_path(archive_path_for(root, source_path, stamp))
        archive_body: list[str] = [
            f"# Archived from {source_path.relative_to(root).as_posix()}",
            f"Generated: {timestamp}",
            "",
        ]
        for entry in entries:
            archive_body.append(entry.render().rstrip())
            archive_body.append("")
        archive_text = "\n".join(archive_body).rstrip() + "\n"
        archive_manifest.append(
            {
                "archive_file": archive_target.as_posix(),
                "source": source_path.as_posix(),
                "entries": [entry.entry_id for entry in entries],
            }
        )
        archive_writes.append({"path": archive_target, "content": archive_text})

    if backup and backup_failures:
        raise RuntimeError(f"Backup verification failed for: {', '.join(backup_failures)}")

    if not dry_run:
        for write in archive_writes:
            write["path"].parent.mkdir(parents=True, exist_ok=True)
            write["path"].write_text(write["content"], encoding="utf-8")

        for path, rendered in rendered_by_file.items():
            path.write_text(rendered, encoding="utf-8")

    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "timestamp": timestamp,
        "dry_run": dry_run,
        "days_threshold": days,
        "files_processed": len(target_files),
        "size_before_bytes": size_before,
        "size_after_bytes": size_after,
        "backups": backups,
        "changed_files": [path.as_posix() for path in changed_files],
        "archived_entries": archived_entries,
        "removed_duplicates": removed_duplicates,
        "merged_entries": merged_entries,
        "archive_files": [item["archive_file"] for item in archive_manifest],
    }

    report_path = logs_dir / f"memory_cleanup_report_{stamp}.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    archive_log_path = logs_dir / f"memory_cleanup_archives_{stamp}.json"
    archive_log_path.write_text(json.dumps(archive_manifest, indent=2, sort_keys=True), encoding="utf-8")

    weekly_summary_path = logs_dir / f"memory_cleanup_weekly_summary_{current_day.isocalendar().year}-W{current_day.isocalendar().week:02d}.md"
    weekly_summary = build_weekly_summary(report)
    prefix = f"# Memory cleanup summary for {current_day.isocalendar().year}-W{current_day.isocalendar().week:02d}\n\n"
    if weekly_summary_path.exists():
        existing = weekly_summary_path.read_text(encoding="utf-8")
    else:
        existing = prefix
    if not existing.endswith("\n"):
        existing += "\n"
    weekly_summary_path.write_text(existing + weekly_summary + "\n", encoding="utf-8")

    report["report_path"] = report_path.as_posix()
    report["archive_log_path"] = archive_log_path.as_posix()
    report["weekly_summary_path"] = weekly_summary_path.as_posix()
    return report


def print_report(report: dict[str, object]) -> None:
    archived = len(report["archived_entries"])
    duplicates = len(report["removed_duplicates"])
    merged = len(report["merged_entries"])
    backups = len(report["backups"])

    print(colorize(CYAN, "OpenClaw memory cleanup"))
    print(colorize(BLUE, f"  Threshold: {report['days_threshold']} days"))
    print(colorize(BLUE, f"  Files processed: {report['files_processed']}"))
    print(colorize(BLUE, f"  Active size: {human_bytes(int(report['size_before_bytes']))} -> {human_bytes(int(report['size_after_bytes']))}"))
    print(colorize(YELLOW, f"  Archived stale entries: {archived}"))
    print(colorize(YELLOW, f"  Removed exact duplicates: {duplicates}"))
    print(colorize(YELLOW, f"  Compacted similar entries: {merged}"))
    if backups:
        print(colorize(GREEN, f"  Backups created: {backups}"))
    status = "dry run complete" if report["dry_run"] else "cleanup complete"
    print(colorize(GREEN, f"  Status: {status}"))
    print(colorize(CYAN, f"  Report: {report['report_path']}"))
    print(colorize(CYAN, f"  Archive log: {report['archive_log_path']}"))
    print(colorize(CYAN, f"  Weekly summary: {report['weekly_summary_path']}"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Automated memory cleanup for OpenClaw.")
    parser.add_argument("--days", type=int, default=90, help="Archive entries older than this many days.")
    parser.add_argument("--dry-run", action="store_true", help="Preview cleanup without changing memory files.")
    parser.add_argument("--backup", action="store_true", help="Create verified backups before cleanup.")
    return parser


def main(
    argv: Sequence[str] | None = None,
    root: Path | None = None,
    today: date | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project_root = root or Path.cwd()
    try:
        report = run_cleanup(
            root=project_root,
            days=args.days,
            dry_run=args.dry_run,
            backup=args.backup,
            today=today,
        )
    except Exception as exc:  # pragma: no cover - exercised in CLI use
        print(colorize(RED, f"Cleanup failed: {exc}"), file=sys.stderr)
        return 1

    print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
