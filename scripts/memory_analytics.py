#!/usr/bin/env python3
"""Memory health analytics for MEMORY.md files."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path


ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_RED = "\033[31m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_CYAN = "\033[36m"

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
ENTRY_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+\.\s+)(.*\S)\s*$")
DATED_ENTRY_RE = re.compile(
    r"^\s*((?:\d{4}[-/.]\d{2}[-/.]\d{2})|(?:[A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4}))\s*[:-]\s+(.*\S)\s*$"
)
LINK_RE = re.compile(r"\[[^\]]+\]\(#([^)]+)\)")
DATE_TOKEN_RE = re.compile(
    r"\b("
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{4}/\d{2}/\d{2}"
    r"|\d{4}\.\d{2}\.\d{2}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s+\d{4}"
    r")\b"
)

DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%b %d, %Y",
    "%B %d, %Y",
)


def slugify_heading(title: str) -> str:
    """Approximate GitHub heading anchor generation."""
    slug = title.strip().lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = slug.replace("_", "-")
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    return slug.strip("-")


def parse_date_token(token: str) -> str | None:
    """Convert a supported date token to ISO format."""
    normalized = token.replace("Sept ", "Sep ")
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(normalized, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def extract_dates(text: str) -> list[str]:
    """Extract unique ISO date strings from freeform text."""
    seen = set()
    dates = []
    for match in DATE_TOKEN_RE.finditer(text):
        parsed = parse_date_token(match.group(1))
        if parsed and parsed not in seen:
            seen.add(parsed)
            dates.append(parsed)
    return sorted(dates)


def normalize_entry_text(text: str) -> str:
    """Normalize an entry for similarity comparisons."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _new_section(title: str, level: int, line_number: int) -> dict[str, object]:
    return {
        "title": title,
        "level": level,
        "line": line_number,
        "anchor": slugify_heading(title),
        "entries": [],
    }


def _build_entry(entry_id: int, section_title: str, text: str, line_number: int) -> dict[str, object]:
    dates = extract_dates(text)
    return {
        "id": entry_id,
        "section": section_title,
        "line": line_number,
        "text": text.strip(),
        "dates": dates,
        "last_mention": max(dates) if dates else None,
    }


def parse_memory_content(content: str) -> dict[str, object]:
    """Parse MEMORY.md content into sections, entries, and references."""
    lines = content.splitlines()
    sections: list[dict[str, object]] = []
    entries: list[dict[str, object]] = []
    links: list[dict[str, object]] = []
    current_section = _new_section("Document", 0, 1)
    sections.append(current_section)
    current_entry: dict[str, object] | None = None
    entry_id = 1

    for line_number, raw_line in enumerate(lines, start=1):
        for match in LINK_RE.finditer(raw_line):
            links.append(
                {
                    "anchor": match.group(1).strip().lower(),
                    "line": line_number,
                    "text": match.group(0),
                }
            )

        heading_match = HEADING_RE.match(raw_line)
        if heading_match:
            current_entry = None
            current_section = _new_section(
                heading_match.group(2).strip(),
                len(heading_match.group(1)),
                line_number,
            )
            sections.append(current_section)
            continue

        entry_match = ENTRY_RE.match(raw_line)
        dated_match = DATED_ENTRY_RE.match(raw_line)
        if entry_match or dated_match:
            text = entry_match.group(1) if entry_match else raw_line.strip()
            current_entry = _build_entry(entry_id, str(current_section["title"]), text, line_number)
            entries.append(current_entry)
            current_section["entries"].append(entry_id)
            entry_id += 1
            continue

        stripped = raw_line.strip()
        if (
            current_entry is not None
            and stripped
            and not HEADING_RE.match(raw_line)
            and not ENTRY_RE.match(raw_line)
            and not DATED_ENTRY_RE.match(raw_line)
        ):
            merged_text = f'{current_entry["text"]} {stripped}'.strip()
            current_entry["text"] = merged_text
            updated_dates = extract_dates(merged_text)
            current_entry["dates"] = updated_dates
            current_entry["last_mention"] = max(updated_dates) if updated_dates else None
            continue

        if not stripped:
            current_entry = None

    anchors = sorted(
        {
            str(section["anchor"])
            for section in sections
            if section["anchor"]
        }
    )
    parsed = {
        "sections": sections,
        "entries": entries,
        "anchors": anchors,
        "links": links,
        "line_count": len(lines),
    }
    return parsed


def detect_missing_cross_references(parsed: dict[str, object]) -> list[dict[str, object]]:
    """Return markdown anchor references that do not resolve."""
    anchors = set(parsed["anchors"])
    missing = []
    for link in parsed["links"]:
        if link["anchor"] not in anchors:
            missing.append(link)
    return missing


def detect_duplicate_entries(entries: list[dict[str, object]], threshold: float = 0.8) -> list[dict[str, object]]:
    """Find similar entries using difflib similarity."""
    duplicates = []
    for left_index in range(len(entries)):
        left = entries[left_index]
        left_normalized = normalize_entry_text(str(left["text"]))
        if not left_normalized:
            continue
        for right_index in range(left_index + 1, len(entries)):
            right = entries[right_index]
            right_normalized = normalize_entry_text(str(right["text"]))
            if not right_normalized:
                continue
            if max(len(left_normalized), len(right_normalized)) < 20 and left_normalized != right_normalized:
                continue
            similarity = SequenceMatcher(None, left_normalized, right_normalized).ratio()
            if similarity >= threshold:
                duplicates.append(
                    {
                        "entry_a": {
                            "id": left["id"],
                            "section": left["section"],
                            "line": left["line"],
                            "text": left["text"],
                        },
                        "entry_b": {
                            "id": right["id"],
                            "section": right["section"],
                            "line": right["line"],
                            "text": right["text"],
                        },
                        "similarity": round(similarity, 3),
                    }
                )
    return duplicates


def build_age_distribution(entries: list[dict[str, object]], today: date) -> dict[str, int]:
    """Bucket entries by age in days since last mention."""
    distribution = {
        "0-7 days": 0,
        "8-30 days": 0,
        "31-90 days": 0,
        "91+ days": 0,
        "undated": 0,
    }
    for entry in entries:
        last_mention = entry["last_mention"]
        if not last_mention:
            distribution["undated"] += 1
            continue
        age_days = (today - date.fromisoformat(str(last_mention))).days
        if age_days <= 7:
            distribution["0-7 days"] += 1
        elif age_days <= 30:
            distribution["8-30 days"] += 1
        elif age_days <= 90:
            distribution["31-90 days"] += 1
        else:
            distribution["91+ days"] += 1
    return distribution


def find_stale_entries(entries: list[dict[str, object]], today: date, threshold_days: int) -> list[dict[str, object]]:
    """Return entries whose last mention is older than threshold_days."""
    stale_entries = []
    for entry in entries:
        last_mention = entry["last_mention"]
        if not last_mention:
            continue
        age_days = (today - date.fromisoformat(str(last_mention))).days
        if age_days > threshold_days:
            stale_entries.append(
                {
                    "id": entry["id"],
                    "section": entry["section"],
                    "line": entry["line"],
                    "text": entry["text"],
                    "last_mention": last_mention,
                    "age_days": age_days,
                }
            )
    return sorted(stale_entries, key=lambda item: (-int(item["age_days"]), int(item["id"])))


def build_statistics(
    parsed: dict[str, object],
    file_size: int,
    threshold_days: int,
    today: date,
) -> dict[str, object]:
    """Build top-level statistics for the report."""
    entries = parsed["entries"]
    return {
        "total_entries": len(entries),
        "sections_count": len(
            [section for section in parsed["sections"] if str(section["title"]) != "Document" or section["entries"]]
        ),
        "file_size_bytes": file_size,
        "line_count": parsed["line_count"],
        "stale_threshold_days": threshold_days,
        "age_distribution": build_age_distribution(entries, today),
    }


def analyze_memory_file(input_path: Path, threshold_days: int, today: date | None = None) -> dict[str, object]:
    """Parse and analyze a MEMORY.md file."""
    if today is None:
        today = datetime.utcnow().date()
    content = input_path.read_text(encoding="utf-8")
    parsed = parse_memory_content(content)
    duplicates = detect_duplicate_entries(parsed["entries"])
    missing_cross_references = detect_missing_cross_references(parsed)
    stale_entries = find_stale_entries(parsed["entries"], today=today, threshold_days=threshold_days)
    statistics = build_statistics(
        parsed=parsed,
        file_size=len(content.encode("utf-8")),
        threshold_days=threshold_days,
        today=today,
    )
    return {
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "source_file": str(input_path),
        "statistics": statistics,
        "sections": parsed["sections"],
        "entries": parsed["entries"],
        "stale_entries": stale_entries,
        "missing_cross_references": missing_cross_references,
        "duplicate_entries": duplicates,
    }


def generate_markdown_report(report: dict[str, object]) -> str:
    """Render a markdown health report."""
    stats = report["statistics"]
    lines = [
        "# Memory Health Report",
        "",
        f"- Source: `{report['source_file']}`",
        f"- Generated: `{report['generated_at']}`",
        f"- Total entries: **{stats['total_entries']}**",
        f"- Sections: **{stats['sections_count']}**",
        f"- File size: **{stats['file_size_bytes']} bytes**",
        f"- Stale threshold: **{stats['stale_threshold_days']} days**",
        "",
        "## Age Distribution",
        "",
        "| Bucket | Entries |",
        "| --- | ---: |",
    ]

    for bucket, count in stats["age_distribution"].items():
        lines.append(f"| {bucket} | {count} |")

    lines.extend(
        [
            "",
            "## Stale Entries",
            "",
        ]
    )
    if report["stale_entries"]:
        for entry in report["stale_entries"]:
            lines.append(
                f"- Line {entry['line']} in **{entry['section']}** "
                f"({entry['age_days']} days old, last mention `{entry['last_mention']}`): {entry['text']}"
            )
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Missing Cross-References",
            "",
        ]
    )
    if report["missing_cross_references"]:
        for missing in report["missing_cross_references"]:
            lines.append(f"- Line {missing['line']}: unresolved anchor `#{missing['anchor']}`")
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Duplicate Entries",
            "",
        ]
    )
    if report["duplicate_entries"]:
        for duplicate in report["duplicate_entries"]:
            left = duplicate["entry_a"]
            right = duplicate["entry_b"]
            lines.append(
                f"- {duplicate['similarity']:.1%} similarity between line {left['line']} "
                f"(**{left['section']}**) and line {right['line']} (**{right['section']}**)"
            )
            lines.append(f"  - A: {left['text']}")
            lines.append(f"  - B: {right['text']}")
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Sections",
            "",
        ]
    )
    for section in report["sections"]:
        if str(section["title"]) == "Document" and not section["entries"]:
            continue
        lines.append(
            f"- `{section['title']}` (anchor: `#{section['anchor']}`, entries: {len(section['entries'])})"
        )

    lines.append("")
    return "\n".join(lines)


def write_json_report(report: dict[str, object], logs_dir: Path, today: date) -> Path:
    """Write a machine-readable JSON report under logs/."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    json_path = logs_dir / f"memory_analytics_{today.strftime('%Y%m%d')}.json"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return json_path


def write_markdown_report(markdown: str, output_path: Path) -> None:
    """Persist the markdown report to disk."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")


def colorize(text: str, color: str) -> str:
    """Wrap text in ANSI color codes."""
    return f"{color}{text}{ANSI_RESET}"


def print_console_summary(report: dict[str, object], markdown_path: Path, json_path: Path) -> None:
    """Emit a concise, colorized summary to stdout."""
    stats = report["statistics"]
    stale_count = len(report["stale_entries"])
    missing_count = len(report["missing_cross_references"])
    duplicate_count = len(report["duplicate_entries"])

    status_color = ANSI_GREEN if stale_count == 0 and missing_count == 0 and duplicate_count == 0 else ANSI_YELLOW
    print(colorize("Memory Health Analytics", ANSI_BOLD + ANSI_CYAN))
    print(f"Source: {report['source_file']}")
    print(
        f"Entries: {stats['total_entries']} | Sections: {stats['sections_count']} | "
        f"File size: {stats['file_size_bytes']} bytes"
    )
    print(
        f"Issues: stale={colorize(str(stale_count), status_color)}, "
        f"missing_links={colorize(str(missing_count), ANSI_RED if missing_count else ANSI_GREEN)}, "
        f"duplicates={colorize(str(duplicate_count), ANSI_RED if duplicate_count else ANSI_GREEN)}"
    )
    print(f"Markdown report: {markdown_path}")
    print(f"JSON report: {json_path}")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze MEMORY.md health and references.")
    parser.add_argument("--input", default="MEMORY.md", help="Path to the MEMORY.md file")
    parser.add_argument("--output", default="report.md", help="Path to write the markdown report")
    parser.add_argument("--days", type=int, default=30, help="Stale threshold in days")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    if args.days < 0:
        parser.error("--days must be zero or greater")

    input_path = Path(args.input)
    if not input_path.exists():
        print(colorize(f"Input file not found: {input_path}", ANSI_RED), file=sys.stderr)
        return 1

    today = datetime.utcnow().date()
    report = analyze_memory_file(input_path=input_path, threshold_days=args.days, today=today)
    markdown = generate_markdown_report(report)
    output_path = Path(args.output)
    write_markdown_report(markdown, output_path)
    json_path = write_json_report(report, logs_dir=Path("logs"), today=today)
    print_console_summary(report, markdown_path=output_path, json_path=json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
