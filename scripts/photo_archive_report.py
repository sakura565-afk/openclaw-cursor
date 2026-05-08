#!/usr/bin/env python3
"""Photo archive analytics and integrity report generator."""

from __future__ import annotations

import argparse
import json
import logging
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt  # noqa: F401  # Reserved for downstream chart generation.
import seaborn as sns  # noqa: F401  # Reserved for downstream chart generation.
from PIL import Image, UnidentifiedImageError
from tqdm import tqdm

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
    ".heic",
    ".heif",
}
INTEGRITY_MIN_SIZE_BYTES = 1024


@dataclass
class IntegrityIssue:
    path: str
    extension: str
    issue: str
    size_bytes: int



def setup_logger(verbose: bool) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
    return logging.getLogger("photo_archive_report")



def scan_files(scan_path: Path) -> list[Path]:
    return sorted(path for path in scan_path.rglob("*") if path.is_file())



def summarize_sizes(sizes: list[int]) -> dict[str, float | int]:
    if not sizes:
        return {
            "total_bytes": 0,
            "avg_bytes": 0,
            "median_bytes": 0,
            "max_bytes": 0,
        }
    return {
        "total_bytes": sum(sizes),
        "avg_bytes": int(sum(sizes) / len(sizes)),
        "median_bytes": int(statistics.median(sizes)),
        "max_bytes": max(sizes),
    }



def check_integrity(path: Path, extension: str, size_bytes: int) -> IntegrityIssue | None:
    if extension in {".heic", ".jpeg", ".jpg"} and size_bytes < INTEGRITY_MIN_SIZE_BYTES:
        return IntegrityIssue(
            path=str(path),
            extension=extension,
            issue="possible_incomplete_file_size_lt_1kb",
            size_bytes=size_bytes,
        )

    if extension not in IMAGE_EXTENSIONS:
        return None

    try:
        with Image.open(path) as img:
            img.verify()
    except UnidentifiedImageError:
        return IntegrityIssue(
            path=str(path),
            extension=extension,
            issue="broken_or_unsupported_image",
            size_bytes=size_bytes,
        )
    except OSError as exc:
        return IntegrityIssue(
            path=str(path),
            extension=extension,
            issue=f"cannot_open_image:{exc}",
            size_bytes=size_bytes,
        )

    return None



def build_report(scan_path: Path, check_file_integrity: bool, verbose: bool) -> dict[str, Any]:
    logger = logging.getLogger("photo_archive_report")
    files = scan_files(scan_path)
    extension_counter: Counter[str] = Counter()
    year_month_counter: Counter[str] = Counter()
    sizes: list[int] = []
    integrity_issues: list[IntegrityIssue] = []

    iterator = tqdm(files, desc="Scanning", unit="file", disable=not verbose)
    for file_path in iterator:
        extension = file_path.suffix.lower().lstrip(".") or "(no_ext)"
        stat = file_path.stat()
        sizes.append(stat.st_size)
        extension_counter[extension] += 1

        dt = datetime.fromtimestamp(stat.st_mtime)
        year_month_counter[f"{dt.year:04d}-{dt.month:02d}"] += 1

        if check_file_integrity:
            issue = check_integrity(file_path, f".{extension}" if extension != "(no_ext)" else "", stat.st_size)
            if issue:
                integrity_issues.append(issue)
                logger.debug("Integrity issue in %s: %s", file_path, issue.issue)

    return {
        "scan_path": str(scan_path),
        "total_files": len(files),
        "by_extension": dict(sorted(extension_counter.items())),
        "distribution_by_year_month": dict(sorted(year_month_counter.items())),
        "size_stats": summarize_sizes(sizes),
        "integrity": {
            "checked": check_file_integrity,
            "broken_files_count": len(integrity_issues),
            "broken_files": [issue.__dict__ for issue in integrity_issues],
        },
    }



def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)



def render_markdown(report: dict[str, Any]) -> str:
    by_ext = report["by_extension"]
    by_period = report["distribution_by_year_month"]
    size_stats = report["size_stats"]
    integrity = report["integrity"]

    ext_rows = [[ext, str(count)] for ext, count in by_ext.items()]
    period_rows = [[period, str(count)] for period, count in by_period.items()]
    size_rows = [[key, str(value)] for key, value in size_stats.items()]

    md: list[str] = [
        "# Photo Archive Report",
        "",
        f"- Scan path: `{report['scan_path']}`",
        f"- Total files: **{report['total_files']}**",
        f"- Integrity check: **{'enabled' if integrity['checked'] else 'disabled'}**",
        "",
        "## Files by extension",
        _table(["Extension", "Count"], ext_rows or [["(none)", "0"]]),
        "",
        "## Distribution by year/month",
        _table(["Year-Month", "Count"], period_rows or [["(none)", "0"]]),
        "",
        "## File size stats",
        _table(["Metric", "Bytes"], size_rows),
        "",
        "## Integrity issues",
        f"Found broken/suspicious files: **{integrity['broken_files_count']}**",
    ]

    if integrity["broken_files"]:
        issue_rows = [
            [item["path"], item["extension"], item["issue"], str(item["size_bytes"])]
            for item in integrity["broken_files"]
        ]
        md.extend([
            "",
            _table(["Path", "Extension", "Issue", "Size bytes"], issue_rows),
        ])
    else:
        md.append("\nNo integrity issues detected.")

    return "\n".join(md) + "\n"



def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze a photo archive and generate markdown + JSON report.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--scan", type=Path, required=True, help="Path to archive directory.")
    parser.add_argument("--output", type=Path, help="Markdown report file path (e.g. report.md).")
    parser.add_argument("--check-integrity", action="store_true", help="Enable image integrity checks.")
    parser.add_argument("--verbose", action="store_true", help="Enable detailed logs and progress bar.")
    return parser.parse_args(argv)



def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logger = setup_logger(args.verbose)

    if not args.scan.exists() or not args.scan.is_dir():
        logger.error("Scan path must be an existing directory: %s", args.scan)
        return 2

    report = build_report(args.scan, args.check_integrity, args.verbose)
    markdown_report = render_markdown(report)
    json_report = json.dumps(report, ensure_ascii=False, indent=2)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(markdown_report, encoding="utf-8")
        json_path = args.output.with_suffix(".json")
        json_path.write_text(json_report + "\n", encoding="utf-8")
        logger.info("Markdown report written: %s", args.output)
        logger.info("JSON report written: %s", json_path)
    else:
        print(markdown_report)
        print(json_report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
