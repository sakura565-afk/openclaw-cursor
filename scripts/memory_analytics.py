#!/usr/bin/env python3
"""Memory health analytics for MEMORY.md files."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable


ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
}


DATE_PATTERNS = (
    re.compile(r"\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b"),
    re.compile(
        r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
        r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t)?(?:ember)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?,\s+\d{4}\b",
        re.IGNORECASE,
    ),
)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(.+?)\s*$")
INTERNAL_LINK_RE = re.compile(r"\[[^\]]+\]\(#([^)#\s]+)\)")
HREF_LINK_RE = re.compile(r'href=["\']#([^"\']+)["\']', re.IGNORECASE)
EXPLICIT_ANCHOR_RE = re.compile(r'<a\s+(?:name|id)=["\']([^"\']+)["\']', re.IGNORECASE)
ANCHOR_ONLY_LINE_RE = re.compile(
    r'^\s*<a\s+(?:name|id)=["\'][^"\']+["\']\s*>\s*</a>\s*$',
    re.IGNORECASE,
)


@dataclass
class Entry:
    """A single MEMORY.md entry."""

    entry_id: int
    section_title: str
    section_anchor: str
    line_start: int
    line_end: int
    content: str
    dates: list[date]

    @property
    def last_mention(self) -> date | None:
        return max(self.dates) if self.dates else None

    def to_dict(self, reference_date: date) -> dict[str, object]:
        last_mention = self.last_mention
        age_days = None
        if last_mention is not None:
            age_days = (reference_date - last_mention).days
        return {
            "entry_id": self.entry_id,
            "section_title": self.section_title,
            "section_anchor": self.section_anchor,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "content": self.content,
            "dates": [item.isoformat() for item in self.dates],
            "last_mention": last_mention.isoformat() if last_mention else None,
            "age_days": age_days,
        }


@dataclass
class Section:
    """A heading section within the memory file."""

    title: str
    level: int
    anchor: str
    line_number: int
    entries: list[Entry]

    def to_dict(self, reference_date: date) -> dict[str, object]:
        return {
            "title": self.title,
            "level": self.level,
            "anchor": self.anchor,
            "line_number": self.line_number,
            "entry_count": len(self.entries),
            "entries": [entry.to_dict(reference_date) for entry in self.entries],
        }


def colorize(text: str, color: str) -> str:
    """Wrap text in ANSI color codes unless disabled."""

    if os.environ.get("NO_COLOR"):
        return text
    return f"{ANSI[color]}{text}{ANSI['reset']}"


def human_size(size_bytes: int) -> str:
    """Format bytes into a compact human-readable string."""

    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{int(size_bytes)} B"


def slugify_heading(text: str) -> str:
    """Create a markdown-friendly anchor from a heading."""

    text = re.sub(r"<[^>]+>", "", text)
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-") or "section"


def normalize_anchor(anchor: str) -> str:
    """Normalize anchor references for comparisons."""

    anchor = anchor.strip().lstrip("#").strip().lower()
    return re.sub(r"\s+", "-", anchor)


def normalize_text(text: str) -> str:
    """Normalize markdown text for duplicate detection."""

    text = re.sub(r"\[[^\]]+\]\(([^)]+)\)", "", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[`*_>#-]", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def parse_date_string(raw_value: str) -> date | None:
    """Parse a single date-like string into a date."""

    cleaned = raw_value.strip().replace("/", "-").replace(".", "-")
    cleaned = re.sub(r"(\d{1,2})(st|nd|rd|th)", r"\1", cleaned, flags=re.IGNORECASE)
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def extract_dates(text: str) -> list[date]:
    """Extract unique dates found within a block of text."""

    found: list[tuple[int, date]] = []
    seen_iso: set[str] = set()
    for pattern in DATE_PATTERNS:
        for match in pattern.finditer(text):
            parsed = parse_date_string(match.group(0))
            if parsed is None:
                continue
            iso_value = parsed.isoformat()
            if iso_value in seen_iso:
                continue
            seen_iso.add(iso_value)
            found.append((match.start(), parsed))
    found.sort(key=lambda item: item[0])
    return [item[1] for item in found]


def extract_internal_links(text: str) -> list[dict[str, object]]:
    """Extract internal anchor references from markdown and HTML links."""

    links: list[dict[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for pattern in (INTERNAL_LINK_RE, HREF_LINK_RE):
            for match in pattern.finditer(line):
                target = normalize_anchor(match.group(1))
                links.append(
                    {
                        "target": target,
                        "line_number": line_number,
                        "context": line.strip(),
                    }
                )
    return links


def _make_unique_anchor(base_anchor: str, seen_anchors: dict[str, int]) -> str:
    count = seen_anchors.get(base_anchor, 0)
    seen_anchors[base_anchor] = count + 1
    if count == 0:
        return base_anchor
    return f"{base_anchor}-{count}"


def parse_memory_file(path: Path) -> dict[str, object]:
    """Parse the memory file into sections, entries, anchors, and links."""

    text = path.read_text(encoding="utf-8")
    size_bytes = path.stat().st_size
    lines = text.splitlines()

    seen_anchors: dict[str, int] = {}
    explicit_anchors: set[str] = set()
    sections: list[Section] = []
    all_entries: list[Entry] = []

    root_section = Section("Document Root", 0, "document-root", 1, [])
    current_section = root_section
    current_entry_lines: list[str] = []
    current_entry_start = 0
    next_entry_id = 1

    def flush_entry(line_end: int) -> None:
        nonlocal current_entry_lines, current_entry_start, next_entry_id
        content = "\n".join(part for part in current_entry_lines if part is not None).strip()
        if not content:
            current_entry_lines = []
            current_entry_start = 0
            return
        entry = Entry(
            entry_id=next_entry_id,
            section_title=current_section.title,
            section_anchor=current_section.anchor,
            line_start=current_entry_start,
            line_end=max(current_entry_start, line_end),
            content=content,
            dates=extract_dates(content),
        )
        current_section.entries.append(entry)
        all_entries.append(entry)
        next_entry_id += 1
        current_entry_lines = []
        current_entry_start = 0

    for line_number, line in enumerate(lines, start=1):
        for match in EXPLICIT_ANCHOR_RE.finditer(line):
            explicit_anchors.add(normalize_anchor(match.group(1)))

        if ANCHOR_ONLY_LINE_RE.match(line):
            flush_entry(line_number - 1)
            continue

        heading_match = HEADING_RE.match(line)
        if heading_match:
            flush_entry(line_number - 1)
            title = re.sub(r"\s+#+\s*$", "", heading_match.group(2)).strip()
            anchor = _make_unique_anchor(slugify_heading(title), seen_anchors)
            current_section = Section(
                title=title,
                level=len(heading_match.group(1)),
                anchor=anchor,
                line_number=line_number,
                entries=[],
            )
            sections.append(current_section)
            continue

        if not line.strip():
            flush_entry(line_number - 1)
            continue

        list_match = LIST_ITEM_RE.match(line)
        if list_match:
            flush_entry(line_number - 1)
            current_entry_start = line_number
            current_entry_lines = [list_match.group(1).strip()]
            continue

        if not current_entry_lines:
            current_entry_start = line_number
            current_entry_lines = [line.strip()]
        else:
            current_entry_lines.append(line.strip())

    flush_entry(len(lines))

    heading_anchors = {section.anchor for section in sections}
    all_anchors = sorted(heading_anchors | explicit_anchors)
    internal_links = extract_internal_links(text)

    return {
        "path": str(path),
        "text": text,
        "size_bytes": size_bytes,
        "sections": sections,
        "entries": all_entries,
        "anchors": all_anchors,
        "internal_links": internal_links,
    }


def build_age_distribution(entries: Iterable[Entry], reference_date: date) -> dict[str, int]:
    """Bucket entries by age based on the most recent date in each entry."""

    buckets = {
        "0-7 days": 0,
        "8-30 days": 0,
        "31-90 days": 0,
        "91-180 days": 0,
        "181+ days": 0,
        "unknown": 0,
    }
    for entry in entries:
        last_mention = entry.last_mention
        if last_mention is None:
            buckets["unknown"] += 1
            continue
        age_days = max((reference_date - last_mention).days, 0)
        if age_days <= 7:
            buckets["0-7 days"] += 1
        elif age_days <= 30:
            buckets["8-30 days"] += 1
        elif age_days <= 90:
            buckets["31-90 days"] += 1
        elif age_days <= 180:
            buckets["91-180 days"] += 1
        else:
            buckets["181+ days"] += 1
    return buckets


def find_stale_entries(entries: Iterable[Entry], reference_date: date, stale_days: int) -> list[dict[str, object]]:
    """Return entries whose last mention is older than the configured threshold."""

    stale_entries: list[dict[str, object]] = []
    for entry in entries:
        last_mention = entry.last_mention
        if last_mention is None:
            continue
        age_days = (reference_date - last_mention).days
        if age_days > stale_days:
            stale_entries.append(
                {
                    "entry_id": entry.entry_id,
                    "section_title": entry.section_title,
                    "line_start": entry.line_start,
                    "line_end": entry.line_end,
                    "last_mention": last_mention.isoformat(),
                    "age_days": age_days,
                    "content": entry.content,
                }
            )
    stale_entries.sort(key=lambda item: int(item["age_days"]), reverse=True)
    return stale_entries


def find_missing_cross_references(parsed: dict[str, object]) -> list[dict[str, object]]:
    """Find internal links that point to anchors not present in the file."""

    available_anchors = {normalize_anchor(anchor) for anchor in parsed["anchors"]}
    missing_refs: list[dict[str, object]] = []
    for link in parsed["internal_links"]:
        if link["target"] not in available_anchors:
            missing_refs.append(
                {
                    "target": link["target"],
                    "line_number": link["line_number"],
                    "context": link["context"],
                }
            )
    return missing_refs


def find_duplicate_entries(entries: list[Entry], threshold: float = 0.80) -> list[dict[str, object]]:
    """Detect similar entry pairs above the configured similarity threshold."""

    duplicates: list[dict[str, object]] = []
    normalized_entries: list[tuple[Entry, str]] = []
    for entry in entries:
        normalized = normalize_text(entry.content)
        normalized_entries.append((entry, normalized))

    for left_index in range(len(normalized_entries)):
        left_entry, left_text = normalized_entries[left_index]
        if len(left_text) < 20:
            continue
        for right_index in range(left_index + 1, len(normalized_entries)):
            right_entry, right_text = normalized_entries[right_index]
            if len(right_text) < 20:
                continue
            ratio = SequenceMatcher(None, left_text, right_text).ratio()
            if ratio < threshold:
                continue
            duplicates.append(
                {
                    "entry_id_a": left_entry.entry_id,
                    "entry_id_b": right_entry.entry_id,
                    "section_a": left_entry.section_title,
                    "section_b": right_entry.section_title,
                    "line_a": left_entry.line_start,
                    "line_b": right_entry.line_start,
                    "similarity": round(ratio, 3),
                    "content_a": left_entry.content,
                    "content_b": right_entry.content,
                }
            )
    duplicates.sort(key=lambda item: float(item["similarity"]), reverse=True)
    return duplicates


def analyze_memory(
    parsed: dict[str, object],
    stale_days: int,
    reference_date: date | None = None,
) -> dict[str, object]:
    """Produce analytics results for a parsed memory file."""

    reference_date = reference_date or date.today()
    sections: list[Section] = parsed["sections"]
    entries: list[Entry] = parsed["entries"]

    age_distribution = build_age_distribution(entries, reference_date)
    stale_entries = find_stale_entries(entries, reference_date, stale_days)
    missing_refs = find_missing_cross_references(parsed)
    duplicates = find_duplicate_entries(entries)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "reference_date": reference_date.isoformat(),
        "input_file": parsed["path"],
        "stale_days": stale_days,
        "summary": {
            "total_entries": len(entries),
            "sections_count": len(sections),
            "file_size_bytes": parsed["size_bytes"],
            "file_size_human": human_size(parsed["size_bytes"]),
            "dated_entries": sum(1 for entry in entries if entry.last_mention is not None),
            "undated_entries": sum(1 for entry in entries if entry.last_mention is None),
            "anchors_count": len(parsed["anchors"]),
            "internal_links_count": len(parsed["internal_links"]),
        },
        "age_distribution": age_distribution,
        "stale_entries": stale_entries,
        "missing_cross_references": missing_refs,
        "duplicate_entries": duplicates,
        "sections": [section.to_dict(reference_date) for section in sections],
        "entries": [entry.to_dict(reference_date) for entry in entries],
        "anchors": parsed["anchors"],
    }


def render_markdown_report(report: dict[str, object]) -> str:
    """Render a markdown report from the analytics data."""

    summary = report["summary"]
    lines = [
        "# Memory Health Report",
        "",
        f"- Generated at: {report['generated_at']}",
        f"- Input file: `{report['input_file']}`",
        f"- Reference date: {report['reference_date']}",
        f"- Stale threshold: {report['stale_days']} days",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Total entries | {summary['total_entries']} |",
        f"| Sections | {summary['sections_count']} |",
        f"| File size | {summary['file_size_human']} ({summary['file_size_bytes']} bytes) |",
        f"| Dated entries | {summary['dated_entries']} |",
        f"| Undated entries | {summary['undated_entries']} |",
        f"| Anchors | {summary['anchors_count']} |",
        f"| Internal links | {summary['internal_links_count']} |",
        "",
        "## Age Distribution",
        "",
        "| Bucket | Entries |",
        "| --- | ---: |",
    ]

    for bucket, count in report["age_distribution"].items():
        lines.append(f"| {bucket} | {count} |")

    lines.extend(
        [
            "",
            f"## Stale Entries ({len(report['stale_entries'])})",
            "",
        ]
    )
    if report["stale_entries"]:
        for stale_entry in report["stale_entries"]:
            snippet = stale_entry["content"].replace("\n", " ")
            snippet = snippet[:120] + ("..." if len(snippet) > 120 else "")
            lines.extend(
                [
                    (
                        f"- Entry {stale_entry['entry_id']} in **{stale_entry['section_title']}** "
                        f"(lines {stale_entry['line_start']}-{stale_entry['line_end']}) "
                        f"- last mention {stale_entry['last_mention']} "
                        f"({stale_entry['age_days']} days old)"
                    ),
                    f"  - {snippet}",
                ]
            )
    else:
        lines.append("- No stale entries detected.")

    lines.extend(
        [
            "",
            f"## Missing Cross References ({len(report['missing_cross_references'])})",
            "",
        ]
    )
    if report["missing_cross_references"]:
        for missing_ref in report["missing_cross_references"]:
            lines.append(
                (
                    f"- `#{missing_ref['target']}` referenced on line "
                    f"{missing_ref['line_number']}: `{missing_ref['context']}`"
                )
            )
    else:
        lines.append("- No missing cross-references detected.")

    lines.extend(
        [
            "",
            f"## Duplicate Entries ({len(report['duplicate_entries'])})",
            "",
        ]
    )
    if report["duplicate_entries"]:
        for duplicate in report["duplicate_entries"]:
            lines.extend(
                [
                    (
                        f"- Entry {duplicate['entry_id_a']} ({duplicate['section_a']}, line {duplicate['line_a']}) "
                        f"and Entry {duplicate['entry_id_b']} ({duplicate['section_b']}, line {duplicate['line_b']}) "
                        f"have {duplicate['similarity']:.1%} similarity"
                    ),
                    f"  - A: {duplicate['content_a'][:100].replace(chr(10), ' ')}",
                    f"  - B: {duplicate['content_b'][:100].replace(chr(10), ' ')}",
                ]
            )
    else:
        lines.append("- No duplicate entries detected.")

    lines.extend(["", "## Sections", ""])
    for section in report["sections"]:
        lines.append(
            f"- `{section['title']}` (level {section['level']}, anchor `#{section['anchor']}`, entries: {section['entry_count']})"
        )

    return "\n".join(lines) + "\n"


def print_console_summary(report: dict[str, object], markdown_path: Path, json_path: Path) -> None:
    """Print a colorized summary to the terminal."""

    summary = report["summary"]
    issue_count = (
        len(report["stale_entries"])
        + len(report["missing_cross_references"])
        + len(report["duplicate_entries"])
    )
    status_color = "green" if issue_count == 0 else "yellow"
    if report["missing_cross_references"]:
        status_color = "red"

    print(colorize("Memory Health Analytics", "bold"))
    print(colorize("=======================", "blue"))
    print(
        f"{colorize('Input:', 'cyan')} {report['input_file']}  "
        f"{colorize('Reference:', 'cyan')} {report['reference_date']}"
    )
    print(
        f"{colorize('Entries:', 'green')} {summary['total_entries']}  "
        f"{colorize('Sections:', 'green')} {summary['sections_count']}  "
        f"{colorize('Size:', 'green')} {summary['file_size_human']}"
    )
    print(
        f"{colorize('Stale:', status_color)} {len(report['stale_entries'])}  "
        f"{colorize('Missing refs:', 'red' if report['missing_cross_references'] else 'green')} "
        f"{len(report['missing_cross_references'])}  "
        f"{colorize('Duplicates:', 'yellow' if report['duplicate_entries'] else 'green')} "
        f"{len(report['duplicate_entries'])}"
    )
    print(
        f"{colorize('Markdown report:', 'cyan')} {markdown_path}  "
        f"{colorize('JSON report:', 'cyan')} {json_path}"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(description="Analyze MEMORY.md health.")
    parser.add_argument("--input", default="MEMORY.md", help="Path to the input MEMORY.md file.")
    parser.add_argument("--output", default="report.md", help="Path to write the markdown report.")
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Stale threshold in days for entry freshness checks.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""

    args = parse_args(argv)
    input_path = Path(args.input)
    if not input_path.exists():
        print(colorize(f"Input file not found: {input_path}", "red"), file=sys.stderr)
        return 1

    parsed = parse_memory_file(input_path)
    report = analyze_memory(parsed, stale_days=args.days)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_report = render_markdown_report(report)
    output_path.write_text(markdown_report, encoding="utf-8")

    logs_path = Path("logs")
    logs_path.mkdir(parents=True, exist_ok=True)
    json_path = logs_path / f"memory_analytics_{date.today().strftime('%Y%m%d')}.json"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print_console_summary(report, output_path, json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
